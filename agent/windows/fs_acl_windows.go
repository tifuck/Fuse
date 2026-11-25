//go:build windows

package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"golang.org/x/sys/windows"
)

// pinDir opens dir for the length of an atomic write. A directory handle
// without FILE_SHARE_DELETE keeps that folder, and every folder above it,
// from being renamed or swapped for a junction meanwhile, so the temp file
// and the rename land in the folder opened here (a privileged connector
// writing under a folder another user controls). inDir checks that a file
// created by path really sits in it.
func pinDir(dir string) (inDir func(f *os.File) error, release func(), err error) {
	name, err := windows.UTF16PtrFromString(dir)
	if err != nil {
		return nil, nil, err
	}
	h, err := windows.CreateFile(name, windows.FILE_LIST_DIRECTORY,
		windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE, nil,
		windows.OPEN_EXISTING, windows.FILE_FLAG_BACKUP_SEMANTICS, 0)
	if err != nil {
		return nil, nil, err
	}
	want, err := finalPath(h)
	if err != nil {
		windows.CloseHandle(h)
		return nil, nil, err
	}
	inDir = func(f *os.File) error {
		got, err := finalPath(windows.Handle(f.Fd()))
		if err != nil {
			return err
		}
		if !strings.EqualFold(strings.TrimRight(filepath.Dir(got), `\`), strings.TrimRight(want, `\`)) {
			return fmt.Errorf("%s changed during the write", dir)
		}
		return nil
	}
	return inDir, func() { windows.CloseHandle(h) }, nil
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
func copyFileSecurity(src, dst string) error {
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
	return windows.SetNamedSecurityInfo(dst, windows.SE_FILE_OBJECT, info, nil, nil, dacl, nil)
}
