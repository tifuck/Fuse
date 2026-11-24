//go:build windows

package main

import (
	"errors"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

const (
	ptyKillMS = 2000 // cancel: console closed and job terminated; TerminateProcess this long after
	// After the shell exits, output this quiet means done. Longer than the
	// Linux 100 ms: the console host paints its output on its own cadence.
	ptyQuietMS = 250
)

// ptySupported: ConPTY arrived in Windows 10 1809 (build 17763). The proc
// check also keeps a call into a missing export from panicking.
var ptySupported = sync.OnceValue(func() bool {
	major, _, build := windows.RtlGetNtVersionNumbers()
	if major < 10 || (major == 10 && build < 17763) {
		return false
	}
	return windows.NewLazySystemDLL("kernel32.dll").NewProc("CreatePseudoConsole").Find() == nil
})

// pwshPath is PowerShell 7 from PATH, or "". LookPath never returns a
// current-directory match, and the service's PATH is the machine's.
func pwshPath() string {
	p, err := exec.LookPath("pwsh.exe")
	if err != nil || !filepath.IsAbs(p) {
		return ""
	}
	return p
}

func ptyHello(hello map[string]any) {
	if !ptySupported() {
		return
	}
	shells := []string{"powershell", "cmd"}
	if pwshPath() != "" {
		shells = append(shells, "pwsh")
	}
	hello["caps"] = []string{"pty"}
	hello["shells"] = shells
}

func ptyShell(name string) (string, []string, error) {
	switch name {
	case "", "powershell":
		return powershellPath(), []string{"-NoLogo"}, nil
	case "pwsh":
		if p := pwshPath(); p != "" {
			return p, []string{"-NoLogo"}, nil
		}
		return "", nil, errors.New("PowerShell 7 (pwsh.exe) is not installed")
	case "cmd":
		return systemTool("cmd.exe"), nil, nil
	}
	return "", nil, errors.New("unknown shell")
}

// ptyEnvBlock is the agent's environment plus the terminal's, as a
// CreateProcess UTF-16 block.
func ptyEnvBlock() []uint16 {
	var env []string
	for _, kv := range os.Environ() {
		if k, _, _ := strings.Cut(kv, "="); !strings.EqualFold(k, "TERM") && !strings.EqualFold(k, "COLORTERM") {
			env = append(env, kv)
		}
	}
	var b []uint16
	for _, kv := range append(env, "TERM=xterm-256color", "COLORTERM=truecolor") {
		if u, err := windows.UTF16FromString(kv); err == nil {
			b = append(b, u...)
		}
	}
	return append(b, 0)
}

func ptyCoord(cols, rows int) windows.Coord {
	return windows.Coord{X: int16(cols), Y: int16(rows)}
}

// conPTY is a shell on a pseudo console. inW and outR are our ends of the
// console's pipes; the shell runs in its own Job Object when one could be
// assigned (job 0 otherwise).
type conPTY struct {
	hpc, inW, outR, proc, job windows.Handle
	pid                       uint32
}

// release frees what a failed start made. Our pipe ends close first, so
// the console cannot block on a write nobody reads while it closes.
func (c *conPTY) release() {
	for _, h := range []windows.Handle{c.outR, c.inW, c.proc, c.job} {
		if h != 0 {
			windows.CloseHandle(h)
		}
	}
	if c.hpc != 0 {
		windows.ClosePseudoConsole(c.hpc)
	}
}

// kill ends the shell's whole tree, or the shell alone without a job.
func (c *conPTY) kill() {
	if c.job == 0 || windows.TerminateJobObject(c.job, 1) != nil {
		windows.TerminateProcess(c.proc, 1)
	}
}

func startConPTY(path string, args []string, dir string, cols, rows int) (*conPTY, error) {
	var inR, inW, outR, outW windows.Handle
	if err := windows.CreatePipe(&inR, &inW, nil, 0); err != nil {
		return nil, err
	}
	if err := windows.CreatePipe(&outR, &outW, nil, 0); err != nil {
		windows.CloseHandle(inR)
		windows.CloseHandle(inW)
		return nil, err
	}
	// The console holds its own copies: ours close once the shell started,
	// so the reader sees the pipe break when the console goes.
	defer windows.CloseHandle(inR)
	defer windows.CloseHandle(outW)
	c := &conPTY{inW: inW, outR: outR}
	fail := func(err error) (*conPTY, error) {
		c.release()
		return nil, err
	}
	if err := windows.CreatePseudoConsole(ptyCoord(cols, rows), inR, outW, 0, &c.hpc); err != nil {
		return fail(err)
	}
	attrs, err := windows.NewProcThreadAttributeList(1)
	if err != nil {
		return fail(err)
	}
	defer attrs.Delete()
	// The attribute's value is the HPCON itself, not a pointer to it.
	if err := attrs.Update(windows.PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, *(*unsafe.Pointer)(unsafe.Pointer(&c.hpc)), unsafe.Sizeof(c.hpc)); err != nil {
		return fail(err)
	}
	si := windows.StartupInfoEx{ProcThreadAttributeList: attrs.List()}
	si.Cb = uint32(unsafe.Sizeof(si))
	// Null std handles, as Windows Terminal does: without the flag the shell
	// takes the agent's own (a redirected console run) over the console's.
	si.Flags = windows.STARTF_USESTDHANDLES
	app, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return fail(err)
	}
	cmdline, err := windows.UTF16PtrFromString(windows.ComposeCommandLine(append([]string{path}, args...)))
	if err != nil {
		return fail(err)
	}
	wd, err := windows.UTF16PtrFromString(dir)
	if err != nil {
		return fail(err)
	}
	env := ptyEnvBlock()
	// Suspended until it is in the job object, so no descendant escapes it.
	// No KILL_ON_JOB_CLOSE, as exec: the job only scopes the kill on cancel.
	var pi windows.ProcessInformation
	flags := uint32(windows.EXTENDED_STARTUPINFO_PRESENT | windows.CREATE_UNICODE_ENVIRONMENT | windows.CREATE_SUSPENDED)
	if err := windows.CreateProcess(app, cmdline, nil, nil, false, flags, &env[0], wd, &si.StartupInfo, &pi); err != nil {
		return fail(err)
	}
	c.proc, c.pid = pi.Process, pi.ProcessId
	if job, err := windows.CreateJobObject(nil, nil); err == nil {
		if windows.AssignProcessToJobObject(job, pi.Process) == nil {
			c.job = job
		} else {
			windows.CloseHandle(job)
		}
	}
	_, err = windows.ResumeThread(pi.Thread)
	windows.CloseHandle(pi.Thread)
	if err != nil {
		windows.TerminateProcess(pi.Process, 1)
		return fail(err)
	}
	return c, nil
}

func isDir(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.IsDir()
}

// ptyOpen runs the pty_open op from spawn to its res: pty_ok once the shell
// runs, then the res with its exit code when it exits or is cancelled.
func (s *session) ptyOpen(id string, msg map[string]any, cancel <-chan struct{}) {
	if !ptyIDOK(id) {
		s.sendRes(id, false, 1, "", "", "invalid terminal id")
		return
	}
	if !ptySupported() {
		s.sendRes(id, false, 1, "", "", "terminals need Windows 10 version 1809 or later")
		return
	}
	path, args, err := ptyShell(jsonStr(msg, "shell", ""))
	if err != nil {
		s.sendRes(id, false, 1, "", "", err.Error())
		return
	}
	cols := ptyClampDim(jsonInt(msg, "cols", 80), 80)
	rows := ptyClampDim(jsonInt(msg, "rows", 24), 24)
	dir := userHome()
	if want := jsonStr(msg, "cwd", ""); want != "" && isDir(expandHome(want)) {
		dir = expandHome(want)
	}
	p, why := s.ptys.open(id, cols, rows)
	if p == nil {
		s.sendRes(id, false, 1, "", "", why)
		return
	}
	c, err := startConPTY(path, args, dir, cols, rows)
	if err != nil {
		s.ptys.close(p)
		s.sendRes(id, false, 1, "", "", "could not start "+filepath.Base(path)+": "+err.Error())
		return
	}
	s.ptys.setOK(p, map[string]any{"v": 1, "t": "pty_ok", "id": id, "shell": path, "pid": c.pid})
	code, reaped := s.ptyPump(p, c, cancel)
	// The res follows the last output byte: give the writer (bounded) time
	// to send what is queued.
	until := time.Now().Add(10 * time.Second)
	for !s.ptys.flushed(p) && time.Now().Before(until) {
		select {
		case <-p.wake:
		case <-time.After(250 * time.Millisecond):
		}
	}
	s.ptys.close(p)
	s.sendResult(jobRes{V: 1, T: "res", ID: id, OK: true, Exit: int(code), TerminationConfirmed: reaped, nullExit: !reaped})
}

// ptyPump moves bytes until the shell has exited and its output is taken,
// then tears the console down. reaped is false when a killed shell never
// died; its handles are then left to the goroutine still waiting on it.
func (s *session) ptyPump(p *ptySlot, c *conPTY, cancel <-chan struct{}) (code uint32, reaped bool) {
	h := s.ptys
	readDone := make(chan struct{})
	go func() {
		defer close(readDone)
		defer windows.CloseHandle(c.outR)
		buf := make([]byte, ptyFrameMax)
		for {
			// Closing: read on and discard until the pipe breaks, or
			// ClosePseudoConsole blocks on its final frame.
			want := h.waitRoom(p)
			if want < 0 {
				want = len(buf)
			}
			var n uint32
			err := windows.ReadFile(c.outR, buf[:want], &n, nil)
			if n > 0 {
				h.putOut(p, buf[:n])
			}
			if err != nil {
				return
			}
		}
	}()
	go func() {
		defer windows.CloseHandle(c.inW)
		for {
			b, ok := h.takeInput(p)
			if !ok {
				return
			}
			var n uint32
			if windows.WriteFile(c.inW, b, &n, nil) != nil {
				return
			}
		}
	}()
	exited := make(chan struct{})
	go func() {
		windows.WaitForSingleObject(c.proc, windows.INFINITE)
		close(exited)
	}()
	var exitAt, cancelAt time.Time
	done, cancelled, killed, tornDown := false, false, false, false
	// The reader is draining (setClosing) while ClosePseudoConsole runs; its
	// end of the pipe breaks once the console is gone.
	teardown := func() {
		if !tornDown {
			tornDown = true
			h.setClosing(p)
			go windows.ClosePseudoConsole(c.hpc)
		}
	}
	for !(done && tornDown) {
		var timer <-chan time.Time
		if done {
			timer = time.After(50 * time.Millisecond)
		} else if cancelled {
			timer = time.After(time.Second)
		}
		select {
		case <-cancel:
			cancel = nil
			cancelled, cancelAt = true, time.Now()
			teardown()
			c.kill()
		case <-exited:
			exited = nil
			done, exitAt = true, time.Now()
		case <-p.wake:
			if cols, rows, ok := h.takeResize(p); ok && !tornDown {
				windows.ResizePseudoConsole(c.hpc, ptyCoord(cols, rows))
			}
		case <-timer:
		}
		now := time.Now()
		// Exited: read on until the console is quiet (the last bytes may
		// still be on their way), but never wait long on a background
		// process that still holds it.
		if done && !tornDown && (h.quiet(p, exitAt, ptyQuietMS*time.Millisecond) || now.Sub(exitAt) >= 10*time.Second) {
			teardown()
		}
		if cancelled && !done && !killed && now.Sub(cancelAt) >= ptyKillMS*time.Millisecond {
			windows.TerminateProcess(c.proc, 1)
			killed = true
		}
		if cancelled && !done && now.Sub(cancelAt) >= (ptyKillMS+10000)*time.Millisecond {
			log.Printf("fuse-agent: terminal shell %d unkillable; abandoning", c.pid)
			if c.job != 0 {
				windows.CloseHandle(c.job)
			}
			return 0, false
		}
	}
	select {
	case <-readDone:
	case <-time.After(10 * time.Second):
		log.Printf("fuse-agent: terminal %s console did not close", p.id)
	}
	windows.GetExitCodeProcess(c.proc, &code)
	windows.CloseHandle(c.proc)
	if c.job != 0 {
		windows.CloseHandle(c.job)
	}
	return code, true
}
