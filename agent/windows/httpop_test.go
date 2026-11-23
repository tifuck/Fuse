package main

import (
	"bytes"
	"encoding/base64"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"
)

// httpOpFrames splits a finished job's queue into its decoded chunk bytes
// (in queue order) and the result, which must be the last frame.
func httpOpFrames(t *testing.T, s *session) (data []byte, chunks int, res jobRes) {
	t.Helper()
	if len(s.results) == 0 {
		t.Fatal("no result")
	}
	for i, r := range s.results {
		if i == len(s.results)-1 {
			if r.T != "res" {
				t.Fatalf("last frame is not the result: %+v", r)
			}
			return data, chunks, r
		}
		raw, err := base64.StdEncoding.DecodeString(r.Data)
		if r.T != "chunk" || r.Stream != "b64" || r.ID != "job" || err != nil || len(raw) < 1 || len(raw) > httpChunk {
			t.Fatalf("bad chunk frame %d: t=%q stream=%q id=%q err=%v len=%d", i, r.T, r.Stream, r.ID, err, len(raw))
		}
		data = append(data, raw...)
		chunks++
	}
	return
}

func httpOpTestServer(t *testing.T, h http.HandlerFunc) (srv *httptest.Server, ip string, port int) {
	t.Helper()
	srv = httptest.NewTLSServer(h)
	saved := httpOpRoots
	httpOpRoots = srv.Client().Transport.(*http.Transport).TLSClientConfig.RootCAs
	t.Cleanup(func() { httpOpRoots = saved; srv.Close() })
	addr := srv.Listener.Addr().(*net.TCPAddr)
	return srv, addr.IP.String(), addr.Port
}

// httptest's certificate is valid for example.com.
const httpOpGet = "GET / HTTP/1.1\r\nHost: example.com\r\nConnection: close\r\n\r\n"

func TestHTTPOpRejectsBadArgs(t *testing.T) {
	req := base64.StdEncoding.EncodeToString([]byte(httpOpGet))
	good := func() map[string]any {
		return map[string]any{"id": "job", "op": "http", "host": "example.com", "ip": "127.0.0.1", "port": 1, "req": req}
	}
	bad := map[string]func(m map[string]any){
		"hostname as ip": func(m map[string]any) { m["ip"] = "localhost" },
		"short ip":       func(m map[string]any) { m["ip"] = "127.1" },
		"zoned ip":       func(m map[string]any) { m["ip"] = "fe80::1%eth0" },
		"missing ip":     func(m map[string]any) { delete(m, "ip") },
		"missing host":   func(m map[string]any) { delete(m, "host") },
		"long host":      func(m map[string]any) { m["host"] = strings.Repeat("a", 254) },
		"port 0":         func(m map[string]any) { m["port"] = 0 },
		"port 65536":     func(m map[string]any) { m["port"] = 65536 },
		"missing req":    func(m map[string]any) { delete(m, "req") },
		"empty req":      func(m map[string]any) { m["req"] = "" },
		"req not base64": func(m map[string]any) { m["req"] = "GET / HTTP/1.1" },
		"req unpadded":   func(m map[string]any) { m["req"] = "R0VUIC8" },
		"req over 65536": func(m map[string]any) { m["req"] = base64.StdEncoding.EncodeToString(make([]byte, httpReqMax+1)) },
	}
	for name, change := range bad {
		m := good()
		change(m)
		s := &session{}
		newJobHub().run(s, m) // through the op switch
		if len(s.results) != 1 || s.results[0].OK || s.results[0].Exit != 1 || s.results[0].Error != "invalid args" {
			t.Fatalf("%s: want invalid args, got %+v", name, s.results)
		}
	}
}

func TestHTTPOpRelaysResponseAsOrderedChunks(t *testing.T) {
	body := make([]byte, 100000)
	for i := range body {
		body[i] = byte(i*7 + i>>8) // every byte value, NUL included
	}
	_, ip, port := httpOpTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Length", strconv.Itoa(len(body)))
		w.Write(body)
	})
	msg := func() map[string]any {
		return map[string]any{"host": "example.com", "ip": ip, "port": float64(port),
			"req": base64.StdEncoding.EncodeToString([]byte(httpOpGet)), "timeout_ms": float64(10000)}
	}

	s := &session{}
	s.httpOp("job", msg(), nil)
	data, chunks, res := httpOpFrames(t, s)
	if !res.OK || res.Exit != 0 || res.Out != "eof" || res.Error != "" {
		t.Fatalf("want eof, got %+v", res)
	}
	if chunks < 2 || !bytes.HasPrefix(data, []byte("HTTP/1.1 200 OK\r\n")) || !bytes.HasSuffix(data, body) {
		t.Fatalf("response lost or reordered: %d chunks, %d bytes", chunks, len(data))
	}

	// max_bytes stops the read exactly there.
	s = &session{}
	m := msg()
	m["max_bytes"] = float64(50000)
	s.httpOp("job", m, nil)
	short, _, res := httpOpFrames(t, s)
	if !res.OK || res.Out != "max" || !bytes.Equal(short, data[:50000]) {
		t.Fatalf("want the first 50000 bytes and max, got %d bytes, %+v", len(short), res)
	}

	// The pinned address does not replace the hostname check.
	s = &session{}
	m = msg()
	m["host"] = "other.test"
	s.httpOp("job", m, nil)
	if _, chunks, res := httpOpFrames(t, s); res.OK || res.Error != "tls failed" || chunks != 0 {
		t.Fatalf("certificate for another name accepted: %+v", res)
	}

	// Nor does anything trust an unknown issuer.
	httpOpRoots = nil
	s = &session{}
	s.httpOp("job", msg(), nil)
	if _, chunks, res := httpOpFrames(t, s); res.OK || res.Error != "tls failed" || chunks != 0 {
		t.Fatalf("untrusted certificate accepted: %+v", res)
	}
}

// The server cancels once it has a whole response; a peer that never closes
// must not hold the job to its deadline, and relayed bytes stay relayed.
func TestHTTPOpCancelEndsAHeldRead(t *testing.T) {
	_, ip, port := httpOpTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("partial"))
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	})
	h := newJobHub()
	h.live["job"] = &liveJob{id: "job", cancel: make(chan struct{})}
	s := &session{}
	done := make(chan struct{})
	go func() {
		h.run(s, map[string]any{"id": "job", "op": "http", "host": "example.com", "ip": ip, "port": float64(port),
			"req": base64.StdEncoding.EncodeToString([]byte(httpOpGet)), "timeout_ms": float64(30000)})
		close(done)
	}()
	time.Sleep(300 * time.Millisecond)
	h.cancel("job")
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("cancel did not end the read")
	}
	data, _, res := httpOpFrames(t, s)
	if res.OK || res.Exit != 1 || res.Error != "cancelled" || !bytes.Contains(data, []byte("partial")) {
		t.Fatalf("want cancelled after the relayed bytes, got %+v, %q", res, data)
	}
}
