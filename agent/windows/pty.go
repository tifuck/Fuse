package main

import (
	"log"
	"strings"
	"sync"
	"time"
)

// Interactive terminals: a pty_open job runs a shell on a pseudo console
// until it exits. Its bytes travel as binary frames [kind][12-char job id]
// [payload] from per-terminal buffers, never the shared results queue, so a
// flood cannot delay results, chunks or pongs. The socket reader only
// touches memory here, never a console handle.
const (
	ptyMax      = 16
	ptyIDLen    = 12
	ptyHdr      = 1 + ptyIDLen
	ptyFrameMax = 32768 // payload bytes per binary frame, both ways
	ptyKindOut  = 0x01
	ptyKindIn   = 0x02
	// Output read but not yet acked by the server; the console is left
	// unread (the shell blocks) while this is used up.
	ptyWindow = 512 * 1024
	// Output read but not yet sent: two frames, so one can be read while the
	// writer sends the other.
	ptyOutMax = 2 * ptyFrameMax
	// Input waiting for the console to take it. A frame that does not fit
	// whole is dropped: the socket reader never waits on a shell.
	ptyInMax = 256 * 1024
	// Output bytes one flush sends, after the job results, before the
	// writer checks the results queue again.
	ptyFlushBudget = 2 * ptyFrameMax
)

func ptyIDOK(id string) bool {
	if len(id) != ptyIDLen {
		return false
	}
	for i := 0; i < len(id); i++ {
		if !strings.ContainsRune("0123456789abcdefABCDEF", rune(id[i])) {
			return false
		}
	}
	return true
}

func ptyFrameBuild(kind byte, id string, data []byte) []byte {
	b := make([]byte, ptyHdr+len(data))
	b[0] = kind
	copy(b[1:ptyHdr], id)
	copy(b[ptyHdr:], data)
	return b
}

// ptyFrameParse rejects a short frame, a bad id or an oversized payload.
func ptyFrameParse(b []byte) (kind byte, id string, data []byte, ok bool) {
	if len(b) < ptyHdr || len(b)-ptyHdr > ptyFrameMax {
		return 0, "", nil, false
	}
	id = string(b[1:ptyHdr])
	if !ptyIDOK(id) {
		return 0, "", nil, false
	}
	return b[0], id, b[ptyHdr:], true
}

// ptyWindowLeft is how many output bytes may still be read before the
// window is used up.
func ptyWindowLeft(sent, acked uint64) int {
	if acked >= sent {
		return ptyWindow
	}
	if used := sent - acked; used < ptyWindow {
		return ptyWindow - int(used)
	}
	return 0
}

func ptyClampDim(v, fallback int) int {
	if v < 1 {
		return fallback
	}
	return min(v, 1000)
}

// ptySlot is one open terminal, shared under ptyHub.mu. Only the console
// reader appends to out and only the writer removes from it; only the
// socket reader appends to in and only the input pump removes from it.
type ptySlot struct {
	id         string
	cond       *sync.Cond    // reader and input pump wait here
	wake       chan struct{} // the session goroutine waits here
	sent       uint64        // output bytes read from the console
	acked      uint64        // of those, acked by the server
	cols, rows int
	resize     bool
	closing    bool // reader discards, input pump stops
	inDropped  bool // logged once
	ok         any  // pty_ok frame not yet sent: goes before any output
	lastOut    time.Time
	out, in    []byte
}

// notify wakes everything waiting on p (ptyHub.mu held). Never blocks.
func (p *ptySlot) notify() {
	p.cond.Broadcast()
	select {
	case p.wake <- struct{}{}:
	default:
	}
}

type ptyHub struct {
	mu      sync.Mutex
	flushMu sync.Mutex // one flush at a time, so a frame is never sent twice
	slots   [ptyMax]*ptySlot
	next    int
	epoch   uint64 // bumped per hello_ok
	kick    func() // wakes the writer; called without mu
}

func newPtyHub(kick func()) *ptyHub {
	return &ptyHub{kick: kick}
}

func (h *ptyHub) find(id string) *ptySlot {
	for _, p := range h.slots {
		if p != nil && p.id == id {
			return p
		}
	}
	return nil
}

func (h *ptyHub) open(id string, cols, rows int) (*ptySlot, string) {
	if !ptyIDOK(id) {
		return nil, "invalid terminal id"
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.find(id) != nil {
		return nil, "terminal id already open"
	}
	for i, p := range h.slots {
		if p == nil {
			p = &ptySlot{id: id, wake: make(chan struct{}, 1), cols: cols, rows: rows,
				out: make([]byte, 0, ptyOutMax)}
			p.cond = sync.NewCond(&h.mu)
			h.slots[i] = p
			return p, ""
		}
	}
	return nil, "too many terminals open on this machine (16)"
}

func (h *ptyHub) close(p *ptySlot) {
	h.mu.Lock()
	defer h.mu.Unlock()
	for i, q := range h.slots {
		if q == p {
			h.slots[i] = nil
		}
	}
	p.closing = true
	p.ok, p.out, p.in = nil, nil, nil
	p.notify()
}

func (h *ptyHub) setClosing(p *ptySlot) {
	h.mu.Lock()
	p.closing = true
	p.notify()
	h.mu.Unlock()
}

func (h *ptyHub) setOK(p *ptySlot, frame any) {
	h.mu.Lock()
	p.ok = frame
	h.mu.Unlock()
	h.kick()
}

// room is how much output may be read now (mu held).
func (p *ptySlot) room() int {
	return min(ptyWindowLeft(p.sent, p.acked), ptyOutMax-len(p.out), ptyFrameMax)
}

// waitRoom blocks the console reader until output may be read; -1 once the
// terminal is closing (read on and discard, so the console can tear down).
func (h *ptyHub) waitRoom(p *ptySlot) int {
	h.mu.Lock()
	defer h.mu.Unlock()
	for !p.closing && p.room() == 0 {
		p.cond.Wait()
	}
	if p.closing {
		return -1
	}
	return p.room()
}

// putOut takes output the reader read; it fits (waitRoom sized the read and
// the room only grows meanwhile).
func (h *ptyHub) putOut(p *ptySlot, b []byte) {
	h.mu.Lock()
	if p.closing {
		h.mu.Unlock()
		return
	}
	wasEmpty := len(p.out) == 0
	p.out = append(p.out, b...)
	p.sent += uint64(len(b))
	p.lastOut = time.Now()
	h.mu.Unlock()
	if wasEmpty {
		h.kick()
	}
}

// quiet says the console has had room but sent nothing for d.
func (h *ptyHub) quiet(p *ptySlot, since time.Time, d time.Duration) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	if p.lastOut.After(since) {
		since = p.lastOut
	}
	return p.room() > 0 && time.Since(since) >= d
}

// takeInput blocks the input pump until input waits; false once closing.
func (h *ptyHub) takeInput(p *ptySlot) ([]byte, bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	for !p.closing && len(p.in) == 0 {
		p.cond.Wait()
	}
	if p.closing {
		return nil, false
	}
	b := append([]byte(nil), p.in...)
	p.in = p.in[:0]
	return b, true
}

func (h *ptyHub) takeResize(p *ptySlot) (cols, rows int, ok bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	ok, p.resize = p.resize, false
	return p.cols, p.rows, ok
}

func (h *ptyHub) flushed(p *ptySlot) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	return p.ok == nil && len(p.out) == 0
}

// input handles an inbound binary frame (socket reader; never blocks).
func (h *ptyHub) input(frame []byte) {
	kind, id, data, ok := ptyFrameParse(frame)
	if !ok || kind != ptyKindIn {
		return
	}
	drop := false
	h.mu.Lock()
	if p := h.find(id); p != nil && !p.closing {
		if len(p.in)+len(data) > ptyInMax {
			drop = !p.inDropped
			p.inDropped = true
		} else {
			p.in = append(p.in, data...)
		}
		p.notify()
	}
	h.mu.Unlock()
	if drop {
		log.Printf("fuse-agent: terminal %s is not reading its input; dropping input", id)
	}
}

func (h *ptyHub) resize(id string, cols, rows int) {
	if cols < 1 || rows < 1 {
		return
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if p := h.find(id); p != nil {
		p.cols, p.rows = ptyClampDim(cols, 80), ptyClampDim(rows, 24)
		p.resize = true
		p.notify()
	}
}

func (h *ptyHub) ack(id string, n float64) {
	if !(n > 0) {
		return
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if p := h.find(id); p != nil {
		unacked := p.sent - p.acked
		if n >= float64(unacked) {
			p.acked += unacked
		} else {
			p.acked += uint64(n)
		}
		p.notify()
	}
}

// reconnected runs after each hello_ok: output handed to the old socket is
// treated as acked, since the server never saw what died in flight and
// would not ack it.
func (h *ptyHub) reconnected() {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.epoch++
	for _, p := range h.slots {
		if p == nil {
			continue
		}
		if handed := p.sent - uint64(len(p.out)); p.acked < handed {
			p.acked = handed
		}
		p.notify()
	}
}

// ids lists the open terminals for hello (never nil: the server reads a list).
func (h *ptyHub) ids() []string {
	ids := []string{}
	if h == nil {
		return ids
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	for _, p := range h.slots {
		if p != nil {
			ids = append(ids, p.id)
		}
	}
	return ids
}

func (h *ptyHub) pending() bool {
	if h == nil {
		return false
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	for _, p := range h.slots {
		if p != nil && (p.ok != nil || len(p.out) > 0) {
			return true
		}
	}
	return false
}

// flush sends pending pty_ok frames (send(v, nil)) and up to ptyFlushBudget
// output bytes as binary frames (send(nil, b)), round-robin. A frame whose
// send fails stays queued.
func (h *ptyHub) flush(send func(v any, bin []byte) error) error {
	h.flushMu.Lock()
	defer h.flushMu.Unlock()
	h.mu.Lock()
	start := h.next
	h.next = (h.next + 1) % ptyMax
	h.mu.Unlock()
	budget := ptyFlushBudget
	for k := 0; k < ptyMax && budget > 0; k++ {
		i := (start + k) % ptyMax
		h.mu.Lock()
		p := h.slots[i]
		var ok any
		var frame []byte
		n := 0
		epoch := h.epoch
		if p != nil && p.ok != nil {
			ok = p.ok
		} else if p != nil && len(p.out) > 0 {
			n = min(len(p.out), ptyFrameMax)
			frame = ptyFrameBuild(ptyKindOut, p.id, p.out[:n])
		}
		h.mu.Unlock()
		if ok != nil {
			if err := send(ok, nil); err != nil {
				return err
			}
			h.mu.Lock()
			if h.slots[i] == p {
				p.ok = nil
				p.notify()
			}
			h.mu.Unlock()
			continue
		}
		if frame == nil {
			continue
		}
		if err := send(nil, frame); err != nil {
			return err
		}
		h.mu.Lock()
		if h.slots[i] == p && len(p.out) >= n {
			p.out = append(p.out[:0], p.out[n:]...)
			// Sent on a socket a hello_ok has since replaced: counted as
			// acked like everything else that socket was handed.
			if h.epoch != epoch {
				p.acked = min(p.acked+uint64(n), p.sent)
			}
			p.notify()
		}
		h.mu.Unlock()
		budget -= n
	}
	return nil
}
