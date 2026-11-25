//go:build !windows

package main

import "os"

// pinDir is a no-op off Windows (this build only serves the tests).
func pinDir(dir string) (inDir func(f *os.File) error, release func(), err error) {
	return func(*os.File) error { return nil }, func() {}, nil
}

// copyFileSecurity is a no-op off Windows: the mode bits copied by the
// atomic write already carry the original file's access.
func copyFileSecurity(src, dst string) error { return nil }
