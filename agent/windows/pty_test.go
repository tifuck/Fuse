package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

const testPtyID = "0123456789ab"

func TestPtyFrameRoundTrip(t *testing.T) {
	data := []byte("\x1b[31mhi\x00\xff")
	b := ptyFrameBuild(ptyKindOut, testPtyID, data)
	if len(b) != ptyHdr+len(data) || b[0] != ptyKindOut || string(b[1:ptyHdr]) != testPtyID {
		t.Fatalf("frame layout: %q", b)
	}
	kind, id, got, ok := ptyFrameParse(b)
	if !ok || kind != ptyKindOut || id != testPtyID || !bytes.Equal(got, data) {
		t.Fatalf("parse: %v %x %q %q", ok, kind, id, got)
	}
	if _, _, got, ok := ptyFrameParse(ptyFrameBuild(ptyKindIn, "ABCDEF012345", nil)); !ok || len(got) != 0 {
		t.Fatal("empty payload and upper-case hex must parse")
	}
	if _, _, _, ok := ptyFrameParse(ptyFrameBuild(ptyKindIn, testPtyID, make([]byte, ptyFrameMax))); !ok {
		t.Fatal("a full frame must parse")
	}
	for name, bad := range map[string][]byte{
		"short":     b[:ptyHdr-1],
		"oversized": ptyFrameBuild(ptyKindIn, testPtyID, make([]byte, ptyFrameMax+1)),
		"bad id":    ptyFrameBuild(ptyKindIn, "0123456789az", data),
		"empty":     nil,
	} {
		if _, _, _, ok := ptyFrameParse(bad); ok {
			t.Fatalf("%s frame parsed", name)
		}
	}
	for _, id := range []string{"", "0123456789a", "0123456789abc", "0123456789a-"} {
		if ptyIDOK(id) {
			t.Fatalf("id %q accepted", id)
		}
	}
}

func TestPtyWindowLeft(t *testing.T) {
	for _, c := range []struct {
		sent, acked uint64
		want        int
	}{
		{0, 0, ptyWindow},
		{100, 40, ptyWindow - 60},
		{ptyWindow, 0, 0},
		{ptyWindow + 5, 0, 0},
		{10, 20, ptyWindow},
	} {
		if got := ptyWindowLeft(c.sent, c.acked); got != c.want {
			t.Fatalf("ptyWindowLeft(%d, %d) = %d, want %d", c.sent, c.acked, got, c.want)
		}
	}
	if ptyClampDim(0, 80) != 80 || ptyClampDim(-3, 24) != 24 || ptyClampDim(5000, 80) != 1000 || ptyClampDim(1, 80) != 1 {
		t.Fatal("ptyClampDim")
	}
}

func sendNothing(any, []byte) error { return nil }

// The reader stops once 512 KiB are sent but unacked; acks reopen exactly
// what they cover, clamped to what was sent.
func TestPtyWindowAccounting(t *testing.T) {
	h := newPtyHub(func() {})
	p, why := h.open(testPtyID, 80, 24)
	if p == nil {
		t.Fatal(why)
	}
	chunk := make([]byte, ptyFrameMax)
	for p.sent < ptyWindow {
		n := h.waitRoom(p)
		if n <= 0 || n > ptyFrameMax {
			t.Fatalf("room %d at sent %d", n, p.sent)
		}
		h.putOut(p, chunk[:n])
		if err := h.flush(sendNothing); err != nil {
			t.Fatal(err)
		}
	}
	if p.sent != ptyWindow || p.room() != 0 {
		t.Fatalf("sent %d room %d", p.sent, p.room())
	}
	for _, n := range []float64{0, -5, math.NaN()} {
		h.ack(testPtyID, n)
	}
	h.ack("ffffffffffff", 1000)
	if p.acked != 0 {
		t.Fatalf("ignored acks moved acked to %d", p.acked)
	}
	h.ack(testPtyID, 1000.9)
	if p.acked != 1000 || h.waitRoom(p) != 1000 {
		t.Fatalf("acked %d room %d", p.acked, p.room())
	}
	h.ack(testPtyID, 10*ptyWindow)
	if p.acked != p.sent || h.waitRoom(p) != ptyFrameMax {
		t.Fatalf("ack must clamp to sent: acked %d sent %d", p.acked, p.sent)
	}
	h.close(p)
	if h.waitRoom(p) != -1 {
		t.Fatal("a closing terminal reads to discard")
	}
}

// After hello_ok everything handed to the old socket counts as acked: what
// is still buffered stays unacked, a frame in flight across it does not.
func TestPtyReconnectResetsWindow(t *testing.T) {
	h := newPtyHub(func() {})
	p, _ := h.open(testPtyID, 80, 24)
	h.putOut(p, make([]byte, 1000))
	h.putOut(p, make([]byte, 500))
	if err := h.flush(func(any, []byte) error { h.reconnected(); return nil }); err != nil {
		t.Fatal(err)
	}
	if p.acked != 1500 || len(p.out) != 0 {
		t.Fatalf("in-flight frame: acked %d out %d", p.acked, len(p.out))
	}
	h.putOut(p, make([]byte, 700))
	h.putOut(p, make([]byte, 300))
	h.reconnected()
	if p.acked != 1500 || p.sent != 2500 {
		t.Fatalf("buffered bytes must stay unacked: acked %d sent %d", p.acked, p.sent)
	}
	if err := h.flush(func(any, []byte) error { return errors.New("down") }); err == nil || len(p.out) != 1000 {
		t.Fatal("a failed send keeps the frame queued")
	}
}

func TestPtyInputDropsWholeFrames(t *testing.T) {
	h := newPtyHub(func() {})
	p, _ := h.open(testPtyID, 80, 24)
	frame := ptyFrameBuild(ptyKindIn, testPtyID, bytes.Repeat([]byte("x"), ptyFrameMax))
	for i := 0; i < ptyInMax/ptyFrameMax+1; i++ {
		h.input(frame)
	}
	h.input(ptyFrameBuild(ptyKindOut, testPtyID, []byte("wrong kind")))
	h.input(ptyFrameBuild(ptyKindIn, "ffffffffffff", []byte("no such terminal")))
	h.input([]byte{ptyKindIn, '0'})
	if len(p.in) != ptyInMax || !p.inDropped {
		t.Fatalf("in %d dropped %v", len(p.in), p.inDropped)
	}
	got, ok := h.takeInput(p)
	if !ok || len(got) != ptyInMax || len(p.in) != 0 {
		t.Fatalf("takeInput %v %d", ok, len(got))
	}
	h.input(ptyFrameBuild(ptyKindIn, testPtyID, []byte("ls\r")))
	if got, _ := h.takeInput(p); string(got) != "ls\r" {
		t.Fatalf("after a drop input flows again: %q", got)
	}
	h.setClosing(p)
	if _, ok := h.takeInput(p); ok {
		t.Fatal("a closing terminal takes no input")
	}
}

func TestPtyOpenLimitsAndIDs(t *testing.T) {
	h := newPtyHub(func() {})
	if ids := h.ids(); ids == nil || len(ids) != 0 {
		t.Fatalf("ids must be an empty list, got %#v", ids)
	}
	if _, why := h.open("nothex", 80, 24); why != "invalid terminal id" {
		t.Fatal(why)
	}
	var first *ptySlot
	for i := 0; i < ptyMax; i++ {
		p, why := h.open(strings.Repeat("0", 10)+string("0123456789abcdef"[i])+"0", 80, 24)
		if p == nil {
			t.Fatal(why)
		}
		if first == nil {
			first = p
		}
	}
	if _, why := h.open("ffffffffffff", 80, 24); why != "too many terminals open on this machine (16)" {
		t.Fatal(why)
	}
	if _, why := h.open(first.id, 80, 24); why == "" {
		t.Fatal("a duplicate id must be refused")
	}
	if len(h.ids()) != ptyMax {
		t.Fatalf("ids %v", h.ids())
	}
	h.close(first)
	if p, why := h.open("ffffffffffff", 80, 24); p == nil {
		t.Fatal(why)
	}
	h.resize(first.id, 100, 30) // closed: ignored
	q := h.find("ffffffffffff")
	h.resize(q.id, 5000, 0)
	if _, _, ok := h.takeResize(q); ok {
		t.Fatal("a zero size is ignored")
	}
	h.resize(q.id, 5000, 3)
	if cols, rows, ok := h.takeResize(q); !ok || cols != 1000 || rows != 3 {
		t.Fatalf("resize %d %d %v", cols, rows, ok)
	}
}

func TestPtyNullExitWire(t *testing.T) {
	b, _ := json.Marshal(jobRes{V: 1, T: "res", ID: testPtyID, OK: true, nullExit: true}.wire())
	if string(b) != `{"exit":null,"id":"0123456789ab","ok":true,"t":"res","v":1}` {
		t.Fatalf("%s", b)
	}
	b, _ = json.Marshal(jobRes{V: 1, T: "res", ID: testPtyID, OK: true, Exit: 3}.wire())
	if !strings.Contains(string(b), `"exit":3`) {
		t.Fatalf("%s", b)
	}
}

// The writer sends job results first, then pty_ok, then output in frames
// of at most 32 KiB, with no more than the flush budget between checks of
// the results queue.
func TestWriterSendsResultsBeforeTerminalOutput(t *testing.T) {
	type msg struct {
		kind int
		data []byte
	}
	got := make(chan msg, 64)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := (&websocket.Upgrader{}).Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer c.Close()
		for {
			kind, data, err := c.ReadMessage()
			if err != nil {
				return
			}
			got <- msg{kind, data}
		}
	}))
	defer srv.Close()
	conn, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(srv.URL, "http"), nil)
	if err != nil {
		t.Fatal(err)
	}
	s := &session{}
	s.ptys = newPtyHub(s.kickWriter)
	p, _ := s.ptys.open(testPtyID, 80, 24)
	out := bytes.Repeat([]byte("y"), ptyOutMax)
	s.ptys.putOut(p, out)
	s.ptys.setOK(p, map[string]any{"v": 1, "t": "pty_ok", "id": testPtyID})
	s.sendRes("a", true, 0, "", "", "")
	s.mu.Lock()
	s.conn, s.ready = conn, true
	s.mu.Unlock()
	go s.writer(conn)
	next := func() msg {
		t.Helper()
		select {
		case m := <-got:
			return m
		case <-time.After(2 * time.Second):
			t.Fatal("nothing delivered")
		}
		return msg{}
	}
	if m := next(); m.kind != websocket.TextMessage || !strings.Contains(string(m.data), `"id":"a"`) {
		t.Fatalf("first: %d %s", m.kind, m.data)
	}
	if m := next(); m.kind != websocket.TextMessage || !strings.Contains(string(m.data), `"pty_ok"`) {
		t.Fatalf("second: %d %s", m.kind, m.data)
	}
	var recv []byte
	for len(recv) < len(out) {
		m := next()
		kind, id, data, ok := ptyFrameParse(m.data)
		if m.kind != websocket.BinaryMessage || !ok || kind != ptyKindOut || id != testPtyID || len(data) > ptyFrameMax {
			t.Fatalf("output frame: %d %d %q %d", m.kind, kind, id, len(data))
		}
		recv = append(recv, data...)
	}
	if !bytes.Equal(recv, out) {
		t.Fatal("output corrupted")
	}
	s.ptys.putOut(p, []byte("tail"))
	if m := next(); string(m.data[ptyHdr:]) != "tail" {
		t.Fatalf("late output: %q", m.data)
	}
	s.closeConn()
}
