//go:build !windows

package main

import (
	"os"
	"path/filepath"
)

// pinnedDir is a plain folder off Windows (this build only serves the tests).
type pinnedDir struct{ dir string }

func pinDir(dir string) (*pinnedDir, error) { return &pinnedDir{dir: dir}, nil }

func (p *pinnedDir) release() {}

func (p *pinnedDir) inDir(f *os.File) error { return nil }

func (p *pinnedDir) createTemp() (*os.File, error) { return os.CreateTemp(p.dir, ".fuse-*.tmp") }

func (p *pinnedDir) rename(f *os.File, name string) error {
	return os.Rename(f.Name(), filepath.Join(p.dir, name))
}

// copyFileSecurity is a no-op off Windows: the mode bits copied by the
// atomic write already carry the original file's access.
func copyFileSecurity(src string, dst *os.File) error { return nil }
