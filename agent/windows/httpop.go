package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"errors"
	"io"
	"net"
	"strconv"
	"time"
)

const (
	httpReqMax   = 65536
	httpChunk    = 32768
	httpMaxBytes = 32 * 1024 * 1024
)

// nil means the system roots. Overridable so tests can trust httptest's
// certificate; chain and hostname verification stay on either way.
var httpOpRoots *x509.CertPool

// httpOp is a one-shot pinned TLS pipe for the server's web search and page
// fetch requests: connect to exactly ip (the server resolved and vetted it;
// host is never looked up), verify the certificate for host, write the
// server's request bytes and relay the raw response as base64 chunks. The
// server builds and parses the HTTP itself (httpop.c mirrors this).
func (s *session) httpOp(id string, msg map[string]any, cancel <-chan struct{}) {
	host, _ := msg["host"].(string)
	ip, _ := msg["ip"].(string)
	enc, _ := msg["req"].(string)
	port := jsonInt(msg, "port", 443)
	maxBytes := min(max(jsonInt(msg, "max_bytes", 1048576), 1), httpMaxBytes)
	timeout := min(max(jsonInt(msg, "timeout_ms", 15000), 1000), 60000)
	req, err := base64.StdEncoding.DecodeString(enc)
	if len(host) < 1 || len(host) > 253 || net.ParseIP(ip) == nil || port < 1 || port > 65535 ||
		err != nil || len(req) < 1 || len(req) > httpReqMax {
		s.sendRes(id, false, 1, "", "", "invalid args")
		return
	}
	// One deadline spans connect, handshake, write and read, and a cancel
	// ends it early. Either way the socket is closed, which unblocks
	// whatever is in flight at once.
	ctx, stop := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Millisecond)
	defer stop()
	go func() {
		select {
		case <-cancel:
			stop()
		case <-ctx.Done():
		}
	}()
	fail := func(what string) {
		if isStopped(cancel) {
			what = "cancelled"
		} else if ctx.Err() != nil {
			what = "timed out"
		}
		s.sendRes(id, false, 1, "", "", what)
	}
	raw, err := (&net.Dialer{}).DialContext(ctx, "tcp", net.JoinHostPort(ip, strconv.Itoa(port)))
	if err != nil {
		fail("connect failed")
		return
	}
	defer raw.Close()
	defer context.AfterFunc(ctx, func() { raw.Close() })()
	conn := tls.Client(raw, &tls.Config{ServerName: host, MinVersion: tls.VersionTLS12, RootCAs: httpOpRoots})
	if err := conn.HandshakeContext(ctx); err != nil {
		fail("tls failed")
		return
	}
	if _, err := conn.Write(req); err != nil {
		fail("write failed")
		return
	}
	buf := make([]byte, httpChunk)
	for total := 0; ; {
		n, err := conn.Read(buf[:min(httpChunk, maxBytes-total)])
		if n > 0 {
			// sendResult, not sendChunk: these wait for queue space and are
			// never dropped, and stay ahead of this job's result.
			s.sendResult(jobRes{V: 1, T: "chunk", ID: id, Stream: "b64", Data: base64.StdEncoding.EncodeToString(buf[:n])})
			total += n
		}
		if total >= maxBytes {
			s.sendRes(id, true, 0, "max", "", "")
			return
		}
		if err != nil {
			// Many servers close without close_notify; the Fuse server checks
			// the response length itself, so that is an end of stream too.
			if !isStopped(cancel) && ctx.Err() == nil && (err == io.EOF || errors.Is(err, io.ErrUnexpectedEOF)) {
				s.sendRes(id, true, 0, "eof", "", "")
			} else {
				fail("read failed")
			}
			return
		}
	}
}
