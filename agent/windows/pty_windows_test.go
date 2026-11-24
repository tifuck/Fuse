package main

import (
	"strings"
	"testing"
	"time"
	"unicode/utf16"

	"golang.org/x/sys/windows"
)

func TestPtyShellMapping(t *testing.T) {
	for name, want := range map[string]string{"": "powershell.exe", "powershell": "powershell.exe", "cmd": "cmd.exe"} {
		path, args, err := ptyShell(name)
		if err != nil || !strings.HasSuffix(strings.ToLower(path), want) {
			t.Fatalf("%q: %s %v", name, path, err)
		}
		if want == "powershell.exe" && (len(args) != 1 || args[0] != "-NoLogo") {
			t.Fatalf("%q args %v", name, args)
		}
	}
	if _, _, err := ptyShell("bash"); err == nil {
		t.Fatal("unknown shell accepted")
	}
}

func TestPtyEnvBlockSetsTerm(t *testing.T) {
	t.Setenv("TERM", "dumb")
	block := ptyEnvBlock()
	if len(block) < 2 || block[len(block)-1] != 0 || block[len(block)-2] != 0 {
		t.Fatal("block must end with two NULs")
	}
	vars := strings.Split(string(utf16.Decode(block[:len(block)-2])), "\x00")
	n := 0
	for _, kv := range vars {
		if strings.HasPrefix(strings.ToUpper(kv), "TERM=") {
			n++
			if kv != "TERM=xterm-256color" {
				t.Fatalf("TERM kept as %q", kv)
			}
		}
	}
	if n != 1 || !strings.Contains(strings.Join(vars, "\n"), "COLORTERM=truecolor") {
		t.Fatalf("env %v", vars)
	}
}

// A real shell on a pseudo console: its output arrives on our pipe end and
// its exit code is reported. Needs Windows 10 1809+.
func TestConPTYSpawn(t *testing.T) {
	if !ptySupported() {
		t.Skip("no ConPTY on this Windows")
	}
	c, err := startConPTY(systemTool("cmd.exe"), []string{"/c", "echo fuse-pty-ok & exit 3"}, t.TempDir(), 80, 24)
	if err != nil {
		t.Fatal(err)
	}
	out := make(chan string, 1)
	go func() {
		defer windows.CloseHandle(c.outR)
		var all []byte
		buf := make([]byte, 4096)
		for {
			var n uint32
			err := windows.ReadFile(c.outR, buf, &n, nil)
			all = append(all, buf[:n]...)
			if err != nil || strings.Contains(string(all), "fuse-pty-ok") {
				out <- string(all)
				// Keep draining so ClosePseudoConsole cannot block.
				for windows.ReadFile(c.outR, buf, &n, nil) == nil {
				}
				return
			}
		}
	}()
	select {
	case got := <-out:
		if !strings.Contains(got, "fuse-pty-ok") {
			t.Fatalf("output %q", got)
		}
	case <-time.After(15 * time.Second):
		t.Fatal("no output")
	}
	if ev, _ := windows.WaitForSingleObject(c.proc, 15000); ev != windows.WAIT_OBJECT_0 {
		t.Fatal("shell did not exit")
	}
	var code uint32
	windows.GetExitCodeProcess(c.proc, &code)
	if code != 3 {
		t.Fatalf("exit %d", code)
	}
	if c.job == 0 {
		t.Fatal("shell not in a job object")
	}
	if err := windows.ResizePseudoConsole(c.hpc, ptyCoord(100, 30)); err != nil {
		t.Fatal(err)
	}
	windows.CloseHandle(c.inW)
	windows.ClosePseudoConsole(c.hpc)
	windows.CloseHandle(c.proc)
	windows.CloseHandle(c.job)
}
