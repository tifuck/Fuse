//go:build !windows

package main

// No pseudo console here: hello advertises no "pty" capability.
func ptyHello(hello map[string]any) {}

func (s *session) ptyOpen(id string, msg map[string]any, cancel <-chan struct{}) {
	s.sendRes(id, false, 1, "", "", "terminals are not supported on this system")
}
