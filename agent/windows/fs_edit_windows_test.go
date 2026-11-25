package main

import (
	"os"
	"path/filepath"
	"testing"
)

// A pinned folder cannot be renamed away (and swapped for a junction) while a
// write runs, and a file outside it is caught.
func TestPinDirHoldsTheFolderForTheWrite(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "d")
	if err := os.Mkdir(dir, 0755); err != nil {
		t.Fatal(err)
	}
	pin, err := pinDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(dir, filepath.Join(root, "moved")); err == nil {
		t.Fatal("pinned folder was renamed")
	}
	if err := writeFileAtomic(filepath.Join(dir, "f.txt"), "x"); err != nil {
		t.Fatalf("write into a pinned folder: %v", err)
	}
	inside, err := os.Create(filepath.Join(dir, "in.txt"))
	if err != nil {
		t.Fatal(err)
	}
	defer inside.Close()
	if err := pin.inDir(inside); err != nil {
		t.Fatalf("file in the folder rejected: %v", err)
	}
	outside, err := os.Create(filepath.Join(root, "out.txt"))
	if err != nil {
		t.Fatal(err)
	}
	defer outside.Close()
	if err := pin.inDir(outside); err == nil {
		t.Fatal("file outside the folder accepted")
	}
	pin.release()
	inside.Close()
	if err := os.Rename(dir, filepath.Join(root, "moved")); err != nil {
		t.Fatalf("released folder still pinned: %v", err)
	}
}

// The temp file is created and renamed over the target through handles
// relative to the pinned folder, replacing the old file and leaving no temp.
func TestPinnedDirCreatesAndRenamesByHandle(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "f.txt")
	if err := os.WriteFile(target, []byte("old"), 0644); err != nil {
		t.Fatal(err)
	}
	pin, err := pinDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer pin.release()
	tmp, err := pin.createTemp()
	if err != nil {
		t.Fatal(err)
	}
	if err := pin.inDir(tmp); err != nil {
		t.Fatalf("temp file outside the pinned folder: %v", err)
	}
	if _, err := tmp.WriteString("new"); err != nil {
		t.Fatal(err)
	}
	if err := pin.rename(tmp, "f.txt"); err != nil {
		t.Fatalf("rename by handle: %v", err)
	}
	if err := tmp.Close(); err != nil {
		t.Fatal(err)
	}
	if b, err := os.ReadFile(target); err != nil || string(b) != "new" {
		t.Fatalf("target = %q, %v; want new", b, err)
	}
	if left, _ := filepath.Glob(filepath.Join(dir, ".fuse-*.tmp")); len(left) != 0 {
		t.Fatalf("temp files left: %v", left)
	}
}
