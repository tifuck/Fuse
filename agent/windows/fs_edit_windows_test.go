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
	inDir, release, err := pinDir(dir)
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
	if err := inDir(inside); err != nil {
		t.Fatalf("file in the folder rejected: %v", err)
	}
	outside, err := os.Create(filepath.Join(root, "out.txt"))
	if err != nil {
		t.Fatal(err)
	}
	defer outside.Close()
	if err := inDir(outside); err == nil {
		t.Fatal("file outside the folder accepted")
	}
	release()
	inside.Close()
	if err := os.Rename(dir, filepath.Join(root, "moved")); err != nil {
		t.Fatalf("released folder still pinned: %v", err)
	}
}
