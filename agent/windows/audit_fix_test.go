package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestBraceExpansionIsBoundedAcrossTheWholeRecursion(t *testing.T) {
	pattern := strings.Repeat("{a,b}", 30)
	start := time.Now()
	alts := expandBraces(pattern)
	if time.Since(start) > time.Second {
		t.Fatalf("expansion took %s", time.Since(start))
	}
	if len(alts) == 0 || len(alts) > braceMax {
		t.Fatalf("got %d alternatives", len(alts))
	}
	if got := expandBraces("*.{ts,tsx}"); len(got) != 2 || got[0] != "*.ts" || got[1] != "*.tsx" {
		t.Fatalf("simple expansion broke: %v", got)
	}
}

func TestUpdateFetchRefusesHTTPRedirect(t *testing.T) {
	plain := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"version":"99.0.0"}`))
	}))
	defer plain.Close()
	secure := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, plain.URL+"/manifest", http.StatusFound)
	}))
	defer secure.Close()
	req, _ := http.NewRequest(http.MethodGet, plain.URL, nil)
	if err := httpsOnlyRedirect(req, nil); err == nil {
		t.Fatal("http redirect target accepted")
	}
	client := secure.Client()
	client.CheckRedirect = httpsOnlyRedirect
	if _, err := client.Get(secure.URL); err == nil || !strings.Contains(err.Error(), "non-https") {
		t.Fatalf("expected non-https refusal, got %v", err)
	}
}

func TestSaveConfigRefusesLineBreaksInValues(t *testing.T) {
	path := t.TempDir() + "/agent.conf"
	cfg := &agentConfig{Path: path, URL: "https://fuse.example", Token: "tok\nurl=https://evil.example"}
	if err := saveConfig(cfg); err == nil {
		t.Fatal("saved a token carrying a newline")
	}
	cfg.Token = "tok"
	if err := saveConfig(cfg); err != nil {
		t.Fatalf("plain token: %v", err)
	}
}

// The tray of an elevated install never connects while the service exists,
// whatever state the service is in; a user install's tray still does.
func TestTraySessionStandsDownForTheService(t *testing.T) {
	t.Setenv("PROGRAMDATA", t.TempDir())
	system := defaultSystemConfigPath()
	if traySessionWanted(system, true) {
		t.Fatal("tray connected next to the service")
	}
	if traySessionWanted(strings.ToUpper(system), true) {
		t.Fatal("path case let the tray connect next to the service")
	}
	if !traySessionWanted(system, false) {
		t.Fatal("tray without a service stayed offline")
	}
	if !traySessionWanted(filepath.Join(t.TempDir(), "agent.conf"), true) {
		t.Fatal("user install's tray stayed offline")
	}
}

// A refusal that meets a full queue and no link is held, then queued once
// there is room, instead of dropped.
func TestRefusalSurvivesAFullQueueWithoutALink(t *testing.T) {
	s := &session{}
	for i := 0; i < maxJobs; i++ {
		s.results = append(s.results, jobRes{ID: "busy"})
	}
	s.sendRefusal("job", "busy")
	if len(s.refusals) != 1 || len(s.results) != maxJobs {
		t.Fatalf("refusal not held: refusals=%d results=%d", len(s.refusals), len(s.results))
	}
	full := s.results
	s.results, s.jobs = nil, newJobHub()
	if s.idleForUpdate() {
		t.Fatal("updating would lose a held refusal")
	}
	s.mu.Lock()
	s.results = full[1:]
	s.queueRefusalsLocked()
	s.mu.Unlock()
	if len(s.refusals) != 0 || s.results[len(s.results)-1].ID != "job" {
		t.Fatalf("refusal not queued: refusals=%d", len(s.refusals))
	}
}

// The binary download has no total deadline, only a stall timeout.
func TestBinaryDownloadFailsOnlyWhenItStalls(t *testing.T) {
	slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		for i := 0; i < 6; i++ {
			w.Write([]byte("x"))
			w.(http.Flusher).Flush()
			time.Sleep(50 * time.Millisecond)
		}
	}))
	defer slow.Close()
	data, err := httpFetch(slow.Client(), slow.URL, 200*time.Millisecond)
	if err != nil || string(data) != "xxxxxx" {
		t.Fatalf("steady download outlasting the stall timeout failed: %q %v", data, err)
	}
	stalled := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("x"))
		w.(http.Flusher).Flush()
		select {
		case <-r.Context().Done():
		case <-time.After(5 * time.Second):
		}
	}))
	defer stalled.Close()
	if _, err := httpFetch(stalled.Client(), stalled.URL, 200*time.Millisecond); err == nil || !strings.Contains(err.Error(), "no data") {
		t.Fatalf("stalled download: %v", err)
	}
}

// One huge matching line is clipped like rg --max-columns, not copied whole.
func TestGrepClipsHugeLines(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "a.txt"), []byte("needle"+strings.Repeat("é", 300000)+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	out, err := grepDirOptions(dir, "needle", "", 50)
	if err != nil || len(out) > 2*searchMaxColumns || !strings.Contains(out, "[line truncated]") {
		t.Fatalf("len=%d err=%v", len(out), err)
	}
	if !strings.Contains(out, "needle") || strings.Contains(out, "\ufffd") {
		t.Fatalf("clipped badly: %q", out[:80])
	}
}
