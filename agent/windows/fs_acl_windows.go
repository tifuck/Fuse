//go:build windows

package main

import (
	"fmt"
	"math/rand/v2"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"unsafe"

	"golang.org/x/sys/windows"
)

// pinnedDir is a folder held open for the length of an atomic write. A
// directory handle without FILE_SHARE_DELETE keeps that folder, and every
// folder above it, from being renamed or swapped for a junction meanwhile,
// and the temp file is created and renamed relative to that handle, never
// by path, so both land in the folder opened here (a privileged connector
// writing under a folder another user controls).
type pinnedDir struct {
	dir  string
	h    windows.Handle
	want string
}

func pinDir(dir string) (*pinnedDir, error) {
	name, err := windows.UTF16PtrFromString(dir)
	if err != nil {
		return nil, err
	}
	h, err := windows.CreateFile(name, windows.FILE_LIST_DIRECTORY,
		windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE, nil,
		windows.OPEN_EXISTING, windows.FILE_FLAG_BACKUP_SEMANTICS, 0)
	if err != nil {
		return nil, err
	}
	want, err := finalPath(h)
	if err != nil {
		windows.CloseHandle(h)
		return nil, err
	}
	return &pinnedDir{dir: dir, h: h, want: want}, nil
}

func (p *pinnedDir) release() { windows.CloseHandle(p.h) }

// inDir checks that f really sits in the pinned folder.
func (p *pinnedDir) inDir(f *os.File) error {
	got, err := finalPath(windows.Handle(f.Fd()))
	if err != nil {
		return err
	}
	if !strings.EqualFold(strings.TrimRight(filepath.Dir(got), `\`), strings.TrimRight(p.want, `\`)) {
		return fmt.Errorf("%s changed during the write", p.dir)
	}
	return nil
}

// createTemp creates a new .fuse-*.tmp file inside the pinned folder,
// opened by name relative to its handle (with DELETE access, for rename,
// and WRITE_DAC, for copyFileSecurity).
func (p *pinnedDir) createTemp() (*os.File, error) {
	for range 10000 {
		name := ".fuse-" + strconv.FormatUint(uint64(rand.Uint32()), 10) + ".tmp"
		uname, err := windows.NewNTUnicodeString(name)
		if err != nil {
			return nil, err
		}
		oa := windows.OBJECT_ATTRIBUTES{
			RootDirectory: p.h,
			ObjectName:    uname,
			Attributes:    windows.OBJ_CASE_INSENSITIVE,
		}
		oa.Length = uint32(unsafe.Sizeof(oa))
		var h windows.Handle
		var iosb windows.IO_STATUS_BLOCK
		err = windows.NtCreateFile(&h,
			windows.GENERIC_READ|windows.GENERIC_WRITE|windows.DELETE|windows.WRITE_DAC|windows.SYNCHRONIZE,
			&oa, &iosb, nil, windows.FILE_ATTRIBUTE_NORMAL,
			windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE, windows.FILE_CREATE,
			windows.FILE_NON_DIRECTORY_FILE|windows.FILE_SYNCHRONOUS_IO_NONALERT, 0, 0)
		if err == windows.STATUS_OBJECT_NAME_COLLISION {
			continue
		}
		path := filepath.Join(p.dir, name)
		if st, ok := err.(windows.NTStatus); ok {
			return nil, &os.PathError{Op: "createtemp", Path: path, Err: st.Errno()}
		} else if err != nil {
			return nil, &os.PathError{Op: "createtemp", Path: path, Err: err}
		}
		return os.NewFile(uintptr(h), path), nil
	}
	return nil, &os.PathError{Op: "createtemp", Path: filepath.Join(p.dir, ".fuse-*.tmp"), Err: os.ErrExist}
}

// fileRenameInformationEx is FileRenameInformationEx (Windows 10 1709+),
// which x/sys/windows does not name.
const fileRenameInformationEx = 65

// fileRenameInfo is FILE_RENAME_INFORMATION; Flags is the union with the
// older ReplaceIfExists BOOLEAN (FILE_RENAME_REPLACE_IF_EXISTS is 1 in
// either). The native call is used because only it documents RootDirectory
// with a bare FileName as a name inside that folder.
type fileRenameInfo struct {
	Flags          uint32
	RootDirectory  windows.Handle
	FileNameLength uint32
	FileName       [1]uint16
}

// rename moves f, opened by createTemp, to name inside the pinned folder,
// replacing what is there. The target is resolved relative to the folder
// handle, not by path, so no junction swapped in can redirect it.
func (p *pinnedDir) rename(f *os.File, name string) error {
	target := filepath.Join(p.dir, name)
	if !strings.HasPrefix(p.dir, `\\?\`) {
		// A path-based rename drops these too; a native name keeps them.
		name = strings.TrimRight(name, ". ")
	}
	s16, err := windows.UTF16FromString(name)
	if err != nil {
		return &os.LinkError{Op: "rename", Old: f.Name(), New: target, Err: err}
	}
	s16 = s16[:len(s16)-1]
	off := unsafe.Offsetof(fileRenameInfo{}.FileName)
	size := max(off+uintptr(len(s16))*2, unsafe.Sizeof(fileRenameInfo{}))
	buf := make([]uint64, (size+7)/8)
	info := (*fileRenameInfo)(unsafe.Pointer(&buf[0]))
	info.RootDirectory = p.h
	info.FileNameLength = uint32(len(s16) * 2)
	copy(unsafe.Slice(&info.FileName[0], len(s16)), s16)
	h := windows.Handle(f.Fd())
	var iosb windows.IO_STATUS_BLOCK
	info.Flags = windows.FILE_RENAME_REPLACE_IF_EXISTS | windows.FILE_RENAME_POSIX_SEMANTICS
	err = windows.NtSetInformationFile(h, &iosb, (*byte)(unsafe.Pointer(info)), uint32(size), fileRenameInformationEx)
	if err != nil {
		// Before Windows 10 1709, or a file system without POSIX rename.
		info.Flags = windows.FILE_RENAME_REPLACE_IF_EXISTS
		err = windows.NtSetInformationFile(h, &iosb, (*byte)(unsafe.Pointer(info)), uint32(size), windows.FileRenameInformation)
	}
	if st, ok := err.(windows.NTStatus); ok {
		err = st.Errno()
	}
	if err != nil {
		return &os.LinkError{Op: "rename", Old: f.Name(), New: target, Err: err}
	}
	return nil
}

func finalPath(h windows.Handle) (string, error) {
	buf := make([]uint16, 32768)
	n, err := windows.GetFinalPathNameByHandle(h, &buf[0], uint32(len(buf)), 0)
	if err != nil {
		return "", err
	}
	if n >= uint32(len(buf)) {
		return "", windows.ERROR_INSUFFICIENT_BUFFER
	}
	return windows.UTF16ToString(buf[:n]), nil
}

// copyFileSecurity gives dst the DACL of src (protected flag included). The
// atomic write replaces a file through a temp sibling; without this the new
// file would carry the directory's inherited ACL instead of the original's,
// widening access to a file that was deliberately restricted.
func copyFileSecurity(src string, dst *os.File) error {
	sd, err := windows.GetNamedSecurityInfo(src, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION)
	if err != nil {
		return err
	}
	dacl, _, err := sd.DACL()
	if err != nil {
		return err
	}
	ctrl, _, err := sd.Control()
	if err != nil {
		return err
	}
	info := windows.SECURITY_INFORMATION(windows.DACL_SECURITY_INFORMATION)
	if ctrl&windows.SE_DACL_PROTECTED != 0 {
		info |= windows.PROTECTED_DACL_SECURITY_INFORMATION
	} else {
		info |= windows.UNPROTECTED_DACL_SECURITY_INFORMATION
	}
	return windows.SetSecurityInfo(windows.Handle(dst.Fd()), windows.SE_FILE_OBJECT, info, nil, nil, dacl, nil)
}
