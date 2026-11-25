//go:build !windows

package main

import "fmt"

func runExec(cwd, cmd string, timeoutMS int, cancel <-chan struct{}, setKill func(func()), onOut, onErr func([]byte), env []string) (string, string, int, error, bool, bool, bool) {
	return "", "", 1, fmt.Errorf("windows only"), false, true, false
}
