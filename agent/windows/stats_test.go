package main

import (
	"encoding/json"
	"testing"
	"time"
)

func TestStatsBuild(t *testing.T) {
	t0 := time.Unix(100, 0)
	a := statsSample{at: t0, cpuBusy: 100, cpuTotal: 1000, netTx: 500,
		diskBusy: []uint64{0, 0}, diskTime: []uint64{0, 0}}
	b := statsSample{at: t0.Add(2 * time.Second), cpuBusy: 200, cpuTotal: 1400,
		memUsed: 6, memTotal: 16, netRx: 4000, netTx: 100, diskR: 1000, diskW: 3000,
		diskBusy: []uint64{250, 900}, diskTime: []uint64{1000, 1000}, volUsed: 7, volTotal: 9}
	f, ok := statsBuild(&a, &b)
	if !ok {
		t.Fatal("no frame")
	}
	got, _ := json.Marshal(f)
	// 25% CPU, rates per second, a counter that went back counts 0, the busiest disk wins.
	want := `{"v":1,"t":"stats","cpu":25,"mem":[6,16],"net":[2000,0],"disk":[90,500,1500],"vol":[7,9]}`
	if string(got) != want {
		t.Fatalf("got %s want %s", got, want)
	}

	b.diskBusy, b.diskTime, b.volTotal = nil, nil, 0
	got, _ = json.Marshal(mustBuild(t, &a, &b))
	if string(got) != `{"v":1,"t":"stats","cpu":25,"mem":[6,16],"net":[2000,0]}` {
		t.Fatalf("nothing seen should send nothing: %s", got)
	}
	b.at = a.at
	if _, ok := statsBuild(&a, &b); ok {
		t.Fatal("zero interval built a frame")
	}
}

func mustBuild(t *testing.T, a, b *statsSample) statsFrame {
	f, ok := statsBuild(a, b)
	if !ok {
		t.Fatal("no frame")
	}
	return f
}

func TestStatsLoopOnOff(t *testing.T) {
	var l statsLoop
	if l.c() != nil {
		t.Fatal("off loop has a timer")
	}
	l.set(true, 50)
	if l.c() == nil || l.every != time.Second {
		t.Fatalf("on: interval %v", l.every)
	}
	l.set(true, 60000)
	if l.every != 10*time.Second {
		t.Fatalf("interval not capped: %v", l.every)
	}
	l.set(false, 0)
	if l.c() != nil || l.probe != nil {
		t.Fatal("off left the timer or probe")
	}
}
