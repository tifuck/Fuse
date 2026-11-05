// Filesystem tools, write side: atomic writes and exact in-place edits.
package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"unicode/utf8"
)

// Whole-file edit ceiling for replace. Only old/new travel over the socket
// (never the file), so this is a RAM guard, not a transport limit. Keep in
// sync with _REPLACE_FILE_MAX in app/workspace.py and EDIT_FILE_MAX in
// agent/src/agent.h.
const editFileMax = 64 << 20

// write op content ceiling (the server pre-checks 400000; transport-bounded).
const writeFileMax = 400000

func writeFileAtomicLimit(path, content string, max int) error {
	if len(content) > max || !utf8.ValidString(content) || strings.ContainsRune(content, 0) {
		return fmt.Errorf("content must be UTF-8 text without NUL, maximum %d bytes", max)
	}
	path = expandHome(path)
	// os.CreateTemp creates 0600: keep an existing file's mode, and give a
	// new file the conventional 0644 instead.
	mode := os.FileMode(0644)
	exists := false
	if st, err := os.Lstat(path); err == nil {
		if !st.Mode().IsRegular() {
			return fmt.Errorf("write target must be a regular file")
		}
		mode = st.Mode().Perm()
		exists = true
	} else if !os.IsNotExist(err) {
		return err
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".fuse-*.tmp")
	if err != nil {
		// Directory not writable but the file is: rewrite the file itself.
		if exists && os.IsPermission(err) {
			return writeInPlace(path, content, max)
		}
		return err
	}
	tmpName := tmp.Name()
	if err := tmp.Chmod(mode); err != nil && runtime.GOOS != "windows" {
		tmp.Close()
		os.Remove(tmpName)
		return err
	}
	if exists {
		// Before any content lands: the temp file starts with the
		// directory's inherited ACL, not the original file's.
		if err := copyFileSecurity(path, tmpName); err != nil {
			tmp.Close()
			os.Remove(tmpName)
			// Cannot carry the original's access over: rewrite the file
			// itself, which keeps its security descriptor.
			return writeInPlace(path, content, max)
		}
	}
	if _, err := tmp.WriteString(content); err != nil {
		tmp.Close()
		os.Remove(tmpName)
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		os.Remove(tmpName)
		return err
	}
	if err := tmp.Close(); err != nil {
		os.Remove(tmpName)
		return err
	}
	if err := os.Rename(tmpName, path); err != nil {
		os.Remove(tmpName)
		return err
	}
	return nil
}

// inPlaceFile is the write handle writeInPlace needs; the var is a seam so
// tests can force a mid-write failure and pin the restore path.
type inPlaceFile interface {
	io.WriterAt
	WriteString(string) (int, error)
	Truncate(int64) error
	Sync() error
	Close() error
}

var openInPlaceFile = func(path string) (inPlaceFile, error) {
	return os.OpenFile(path, os.O_WRONLY, 0)
}

// writeInPlace rewrites an existing regular file without replacing it (same
// file identity, attributes and ACLs). Not atomic; only a fallback. The file
// is never truncated before the new bytes land, and a failed write puts the
// old body back (when it fit the caller's ceiling), so a partial write cannot
// destroy the original.
func writeInPlace(path, content string, max int) error {
	var orig []byte
	restorable := false
	if rf, err := os.Open(path); err == nil {
		var rerr error
		orig, rerr = io.ReadAll(io.LimitReader(rf, int64(max)+1))
		rf.Close()
		// A short read is not the original: "restoring" it on a failed
		// write would truncate the file to that length.
		restorable = rerr == nil && len(orig) <= max
	}
	f, err := openInPlaceFile(path)
	if err != nil {
		return err
	}
	// Any failure after the first write may leave new bytes in the file: put
	// the old body back, and say so when that is not possible.
	fail := func(err error) error {
		restored := restorable
		if restorable {
			if _, werr := f.WriteAt(orig, 0); werr != nil {
				restored = false
			} else if f.Truncate(int64(len(orig))) != nil || f.Sync() != nil {
				restored = false
			}
		}
		f.Close()
		if !restored {
			return fmt.Errorf("%w; the file may now be partially written, read it before retrying", err)
		}
		return err
	}
	if _, err := f.WriteString(content); err != nil {
		return fail(err)
	}
	if err := f.Truncate(int64(len(content))); err != nil {
		return fail(err)
	}
	if err := f.Sync(); err != nil {
		return fail(err)
	}
	return f.Close()
}

func writeFileAtomic(path, content string) error {
	return writeFileAtomicLimit(path, content, writeFileMax)
}

// fileEdit is one exact replacement; replaceEdits applies a list in order to
// the same text and publishes once — all or nothing.
type fileEdit struct {
	Old string
	New string
	All bool
}

func replaceFile(path, old, newv string, all bool) (string, error) {
	return replaceEdits(path, []fileEdit{{old, newv, all}})
}

// eolMatch is the length of old matched at text[i:], each '\n' in old
// taking LF or CRLF; 0 if none.
func eolMatch(text string, i int, old string) int {
	s := i
	for j := 0; j < len(old); j++ {
		if old[j] == '\n' && strings.HasPrefix(text[s:], "\r\n") {
			s += 2
		} else if s < len(text) && text[s] == old[j] {
			s++
		} else {
			return 0
		}
	}
	return s - i
}

// matchCRLF: whether a match ends its lines with CRLF — its own ending when
// it spans lines, else that of the line it sits on (CRLF on a final line).
func matchCRLF(text string, at, n int) bool {
	if hit := text[at : at+n]; strings.ContainsRune(hit, '\n') {
		return strings.Contains(hit, "\r\n")
	}
	nl := strings.IndexByte(text[at+n:], '\n')
	return nl < 0 || text[at+n+nl-1] == '\r'
}

// applyEditEOL is applyEdit for LF-only strings against a text with CRLF (or
// mixed) endings: each newline in old matches either ending, and new takes
// the matched region's.
func applyEditEOL(text string, e fileEdit) (string, int, error) {
	k := strings.IndexByte(e.Old, '\n')
	if k < 0 {
		k = len(e.Old)
	}
	type hit struct{ at, n int }
	var hits []hit
	for i := 0; i < len(text); {
		var j int
		if k > 0 {
			j = strings.Index(text[i:], e.Old[:k])
		} else {
			j = strings.IndexAny(text[i:], "\r\n")
		}
		if j < 0 {
			break
		}
		if n := eolMatch(text, i+j, e.Old); n > 0 {
			hits = append(hits, hit{i + j, n})
			i += j + n
		} else {
			i += j + 1
		}
	}
	if len(hits) == 0 {
		return "", 0, fmt.Errorf("old_string not found")
	}
	if len(hits) > 1 && !e.All {
		return "", 0, fmt.Errorf("old_string is not unique (%d occurrences)", len(hits))
	}
	if !e.All {
		hits = hits[:1]
	}
	crlfNew := strings.ReplaceAll(e.New, "\n", "\r\n")
	size := len(text)
	for _, h := range hits {
		if matchCRLF(text, h.at, h.n) {
			size += len(crlfNew) - h.n
		} else {
			size += len(e.New) - h.n
		}
	}
	if size > editFileMax {
		return "", 0, fmt.Errorf("replacement too large (maximum %d bytes); apply the change with run_terminal_command", editFileMax)
	}
	var b strings.Builder
	b.Grow(size)
	prev := 0
	for _, h := range hits {
		b.WriteString(text[prev:h.at])
		if matchCRLF(text, h.at, h.n) {
			b.WriteString(crlfNew)
		} else {
			b.WriteString(e.New)
		}
		prev = h.at + h.n
	}
	b.WriteString(text[prev:])
	return b.String(), len(hits), nil
}

// applyEdit replaces old with newv in text. LF-only strings against a text
// with CRLF line endings go through applyEditEOL (read_file hides carriage
// returns) — deterministic line-ending repair, never a fuzzy match.
func applyEdit(text string, e fileEdit) (string, int, error) {
	if e.Old == "" {
		return "", 0, fmt.Errorf("old_string required")
	}
	if e.Old == e.New {
		return "", 0, fmt.Errorf("old_string and new_string are identical")
	}
	if strings.Contains(text, "\r\n") && !strings.ContainsRune(e.Old, '\r') && !strings.ContainsRune(e.New, '\r') {
		return applyEditEOL(text, e)
	}
	oldUse, newUse := e.Old, e.New
	count := strings.Count(text, oldUse)
	if count == 0 {
		return "", 0, fmt.Errorf("old_string not found")
	}
	if count > 1 && !e.All {
		return "", 0, fmt.Errorf("old_string is not unique (%d occurrences)", count)
	}
	// Size the result before building it: ReplaceAll allocates the whole
	// output first, so a short old/long new pair could ask for gigabytes.
	replaced := 1
	if e.All {
		replaced = count
	}
	if growth := len(newUse) - len(oldUse); growth > 0 && replaced > (editFileMax-len(text))/growth {
		return "", 0, fmt.Errorf("replacement too large (maximum %d bytes); apply the change with run_terminal_command", editFileMax)
	}
	if e.All {
		return strings.ReplaceAll(text, oldUse, newUse), count, nil
	}
	return strings.Replace(text, oldUse, newUse, 1), 1, nil
}

func replaceEdits(path string, edits []fileEdit) (string, error) {
	if len(edits) < 1 || len(edits) > 100 {
		return "", fmt.Errorf("edits must contain 1 to 100 items")
	}
	path = expandHome(path)
	before, err := os.Lstat(path)
	if err != nil {
		return "", err
	}
	if !before.Mode().IsRegular() {
		return "", fmt.Errorf("edit target must be a regular file")
	}
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	data, err := io.ReadAll(io.LimitReader(f, editFileMax+1))
	// Close before publishing: Go opens files on Windows without
	// FILE_SHARE_DELETE, so renaming the new content over a file this
	// process still holds open fails with "Access denied".
	f.Close()
	if err != nil {
		return "", err
	}
	if len(data) > editFileMax {
		return "", fmt.Errorf("file too large to edit (maximum %d bytes); apply the change with run_terminal_command", editFileMax)
	}
	text := string(data)
	if !utf8.ValidString(text) || strings.ContainsRune(text, 0) {
		return "", fmt.Errorf("file is not UTF-8 text")
	}
	total := 0
	for i, e := range edits {
		next, count, err := applyEdit(text, e)
		if err != nil {
			if len(edits) > 1 {
				return "", fmt.Errorf("edit %d of %d: %v; no edits were applied", i+1, len(edits), err)
			}
			return "", err
		}
		if len(next) > editFileMax {
			return "", fmt.Errorf("replacement too large (maximum %d bytes); apply the change with run_terminal_command", editFileMax)
		}
		text = next
		total += count
	}
	after, err := os.Lstat(path)
	if err != nil {
		return "", err
	}
	if !os.SameFile(before, after) || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
		return "", fmt.Errorf("file changed during edit; read it again")
	}
	if err := writeFileAtomicLimit(path, text, editFileMax); err != nil {
		return "", err
	}
	return fmt.Sprintf("%d replacement(s)", total), nil
}
