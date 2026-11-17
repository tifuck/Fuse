package main

import (
	"math"
	"sync/atomic"
	"time"
)

// Live machine usage for the chat's stats card. Sampled on the pump only
// while the server has asked ("stats" on): a handful of system calls a
// tick, no goroutine and nothing at all while off. stats_windows.go reads
// the counters; this file turns two samples into a frame.

type statsSample struct {
	at                 time.Time
	cpuBusy, cpuTotal  uint64
	memUsed, memTotal  uint64
	netRx, netTx       uint64
	diskR, diskW       uint64
	diskBusy, diskTime []uint64 // per disk: time busy and time elapsed, same unit
	volUsed, volTotal  uint64
}

type statsFrame struct {
	V    int        `json:"v"`
	T    string     `json:"t"`
	CPU  float64    `json:"cpu"`
	Mem  [2]uint64  `json:"mem"`
	Net  [2]float64 `json:"net"`
	Disk []float64  `json:"disk,omitempty"` // busiest disk's busy %, read B/s, write B/s
	Vol  []uint64   `json:"vol,omitempty"`  // system volume used, total bytes
}

// A counter that went backwards (a device removed, a reset) moved by 0.
func statsDelta(a, b uint64) uint64 {
	if b > a {
		return b - a
	}
	return 0
}

func statsPct(part, whole uint64) float64 {
	if whole == 0 {
		return 0
	}
	return math.Min(100, math.Round(1000*float64(part)/float64(whole))/10)
}

func statsBuild(a, b *statsSample) (statsFrame, bool) {
	secs := b.at.Sub(a.at).Seconds()
	if secs <= 0 {
		return statsFrame{}, false
	}
	rate := func(x, y uint64) float64 { return math.Round(float64(statsDelta(x, y)) / secs) }
	f := statsFrame{
		V: 1, T: "stats",
		CPU: statsPct(statsDelta(a.cpuBusy, b.cpuBusy), statsDelta(a.cpuTotal, b.cpuTotal)),
		Mem: [2]uint64{b.memUsed, b.memTotal},
		Net: [2]float64{rate(a.netRx, b.netRx), rate(a.netTx, b.netTx)},
	}
	if n := len(b.diskBusy); n > 0 && n == len(a.diskBusy) && n == len(b.diskTime) && n == len(a.diskTime) {
		busy := 0.0
		for i := range n {
			busy = math.Max(busy, statsPct(statsDelta(a.diskBusy[i], b.diskBusy[i]), statsDelta(a.diskTime[i], b.diskTime[i])))
		}
		f.Disk = []float64{busy, rate(a.diskR, b.diskR), rate(a.diskW, b.diskW)}
	}
	if b.volTotal > 0 {
		f.Vol = []uint64{b.volUsed, b.volTotal}
	}
	return f, true
}

// statsLoop belongs to one pump: set from its "stats" frames, ticked from
// its select, stopped when it returns.
type statsLoop struct {
	probe   *statsProbe
	prev    statsSample
	every   time.Duration
	timer   *time.Timer
	sending atomic.Bool
}

func (l *statsLoop) c() <-chan time.Time {
	if l.timer == nil {
		return nil
	}
	return l.timer.C
}

func (l *statsLoop) set(on bool, ms int) {
	if !on {
		l.stop()
		return
	}
	l.every = time.Duration(min(max(ms, 1000), 10000)) * time.Millisecond
	if l.timer != nil {
		return
	}
	l.probe = openStatsProbe()
	l.prev = statsSample{}
	l.probe.sample(&l.prev)
	// The first frame comes a second after the baseline, not a whole interval.
	l.timer = time.NewTimer(time.Second)
}

func (l *statsLoop) tick(send func(any) error) {
	if l.timer == nil {
		return
	}
	l.timer.Reset(l.every)
	var cur statsSample
	if !l.probe.sample(&cur) {
		return
	}
	f, ok := statsBuild(&l.prev, &cur)
	l.prev = cur
	// Lossy: a frame still waiting on the socket means this one is skipped,
	// so a slow link never queues stats or holds up the pump.
	if ok && l.sending.CompareAndSwap(false, true) {
		go func() {
			_ = send(f)
			l.sending.Store(false)
		}()
	}
}

func (l *statsLoop) stop() {
	if l.timer != nil {
		l.timer.Stop()
		l.timer = nil
	}
	if l.probe != nil {
		l.probe.close()
		l.probe = nil
	}
}
