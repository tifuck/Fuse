"""Test-run safety: tests never start real processes and never run unsandboxed.

On 2026-11-16 a test fed "rm -rf /*" to a fake host that ran it in a real
local bash as root, a mutant had switched the denylist off, and the machine
was wiped. Independent layers keep that from happening again:

- scripts/test_sandbox.sh runs tests in a throwaway systemd unit as the
  unprivileged user fuse-test, on a read-only file system (the tree under
  test included), writable only in the unit's own run directory.
- refuse_unless_sandboxed(): every entry point (tests/conftest.py,
  tests/core/__init__.py) ends the process before app code is imported unless it runs in that unit.
- install_process_guard(): no test may start a process or signal one it
  did not start. Every way Python starts one raises (libc's own entry
  points through ctypes included), except the exact
  app workers in ALLOWED (and, in the xdist controller only, the exact
  execnet worker bootstrap).

Neither is a test helper: do not patch, relax or mock them in a test.
"""
from __future__ import annotations

import asyncio
import errno
import os
import sys
from pathlib import Path

SANDBOX_ENV = "FUSE_TEST_SANDBOX"
SANDBOX_TOKEN = "systemd-run"
UNIT_PREFIX = "fuse-test-"
# Read-only mounts inside the unit (/tmp and /root are empty read-only
# tmpfs mounts there): creating a file fails with EROFS (EPERM where a
# security module refuses first). An unprivileged user outside the unit gets
# EACCES in /usr, /etc, /var and /root and can create files in /tmp, so none
# of that passes.
READ_ONLY_DIRS = ("/usr", "/etc", "/var", "/tmp", "/root")
_REFUSED = (errno.EROFS, errno.EPERM)

_APP = Path(__file__).resolve().parents[1] / "app"
# The only processes tests may start: app workers that decode untrusted
# input in a disposable interpreter. Exact argv shape, the test run's own
# interpreter, never a shell, never "-c".
#   app/file_images.py:  [python, <app>/file_images.py, frame, max_side, max_bytes]
#   app/search/engine.py: [python, -I, <app>/search/pdf_worker.py, max_bytes]
#   app/code_mode.py:     [python, -I, <app>/code_worker.py, heap_mb] (a V8
#                         isolate with no I/O; its tool calls come back to
#                         the test's own fakes over the pipe)
ALLOWED = (
    ((), str(_APP / "file_images.py"), 3),
    (("-I",), str(_APP / "search" / "pdf_worker.py"), 1),
    (("-I",), str(_APP / "code_worker.py"), 1),
)

REFUSAL = "tests may not start processes"
SIGNAL_REFUSAL = "tests may only signal processes they started"


# -- sandbox detection ------------------------------------------------------

def _probe_refused(directory: str) -> bool:
    path = os.path.join(directory, f".fuse-test-sandbox-probe-{os.getpid()}")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as exc:
        return exc.errno in _REFUSED
    os.close(fd)
    os.unlink(path)  # we created it (O_EXCL): an empty file, ours alone
    return False


def unit_name() -> str:
    """The systemd unit this process runs in (cgroup v2), or ''."""
    try:
        with open("/proc/self/cgroup") as cgroup:
            lines = cgroup.read().splitlines()
    except OSError:
        return ""
    for line in lines:
        if line.startswith("0::"):
            return line[3:].rstrip("/").rsplit("/", 1)[-1]
    return ""


def sandbox_refusal() -> str | None:
    """None inside the test sandbox, else why the run must stop."""
    how = "Run tests only through scripts/test.sh (it starts the systemd sandbox)."
    if os.environ.get(SANDBOX_ENV) != SANDBOX_TOKEN or not os.environ.get("INVOCATION_ID"):
        return f"not inside the test sandbox ({SANDBOX_ENV} / systemd unit missing). {how}"
    if os.geteuid() == 0 or os.getuid() == 0:
        return f"tests must not run as root. {how}"
    unit = unit_name()
    if not (unit.startswith(UNIT_PREFIX) and unit.endswith(".service")):
        return f"not in a {UNIT_PREFIX}* unit (cgroup {unit!r}). {how}"
    for directory in READ_ONLY_DIRS:
        if not _probe_refused(directory):
            return f"{directory} is not read-only, so this is not the test sandbox. {how}"
    return None


def refuse_unless_sandboxed(*, hard: bool = True) -> None:
    """End this process outside the sandbox.

    hard (test packages): os._exit at once. pytest
    turns exceptions raised while importing a test module into collection
    errors and carries on, so nothing else is certain to stop it; the
    message may stay in pytest's capture. tests/conftest.py passes
    hard=False: SystemExit while pytest loads its first conftest ends the
    run before any other conftest or test module is imported, and pytest
    prints the captured message."""
    reason = sandbox_refusal()
    if reason:
        sys.stderr.write(f"\nREFUSED: {reason}\n")
        sys.stderr.flush()
        if hard:
            os._exit(3)
        raise SystemExit(3)


# -- process guard -------------------------------------------------------------

def _argv(args) -> list[str]:
    if isinstance(args, (str, bytes, os.PathLike)):
        args = [args]
    return [os.fsdecode(a) for a in args]


_xdist_controller = False


def allow_xdist_workers() -> None:
    """Called only from the xdist controller (tests/conftest.py), which
    starts the workers and never imports a test module."""
    global _xdist_controller
    _xdist_controller = True


def _xdist_bootstrap(argv: list[str]) -> bool:
    if not _xdist_controller:
        return False
    from execnet.gateway_io import popen_bootstrapline

    return argv in ([sys.executable, "-u", "-c", popen_bootstrapline],
                    [sys.executable, "-u", "-B", "-c", popen_bootstrapline])


def allowed(args) -> bool:
    argv = _argv(args)
    if not argv or argv[0] != sys.executable:
        return False
    for flags, script, extra in ALLOWED:
        head = [sys.executable, *flags, script]
        tail = argv[len(head):]
        if argv[:len(head)] == head and len(tail) == extra and all(a.isdigit() for a in tail):
            return True
    return _xdist_bootstrap(argv)


def check(args) -> None:
    if not allowed(args):
        raise PermissionError(f"{REFUSAL}: {_argv(args)[:4]!r}")


def _refuse(name):
    def refused(*_args, **_kwargs):
        raise PermissionError(f"{REFUSAL} ({name})")

    refused.__name__ = f"refused_{name}"
    return refused


def _own_child(pid) -> bool:
    """pid is a process this one started (its parent is us)."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        with open(f"/proc/{pid}/stat") as stat:
            fields = stat.read().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return False
    return len(fields) > 1 and fields[1] == str(os.getpid())


_installed = False


def install_process_guard() -> None:
    """Replace every process-starting and signalling entry point for this
    interpreter, under both their os and posix names."""
    global _installed
    if _installed:
        return
    import _posixsubprocess
    import posix
    import pty
    import signal
    import subprocess

    # Lowest layer: what Popen and asyncio end up calling, including code
    # that captured `from subprocess import Popen` before this ran.
    real_fork_exec = _posixsubprocess.fork_exec

    def fork_exec(args, executable_list, *rest, **kwargs):
        check(args)
        # Popen(executable=...) runs this list, not args[0].
        if any(os.fsdecode(path) != sys.executable for path in executable_list):
            raise PermissionError(f"{REFUSAL}: executable {list(map(os.fsdecode, executable_list))!r}")
        return real_fork_exec(args, executable_list, *rest, **kwargs)

    _posixsubprocess.fork_exec = fork_exec
    subprocess._fork_exec = fork_exec

    for name in ("posix_spawn", "posix_spawnp"):
        real = getattr(posix, name, None)
        if real is None:
            continue

        def spawn(path, argv, *rest, _real=real, **kwargs):
            check(argv)
            if os.fsdecode(path) != sys.executable:
                raise PermissionError(f"{REFUSAL}: executable {os.fsdecode(path)!r}")
            return _real(path, argv, *rest, **kwargs)

        setattr(posix, name, spawn)
        setattr(os, name, spawn)

    # Everything that runs a shell or replaces/forks this process: no exceptions.
    for name in ("system", "popen", "fork", "forkpty", "execv", "execve", "execl", "execle", "execlp",
                 "execlpe", "execvp", "execvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv",
                 "spawnve", "spawnvp", "spawnvpe"):
        for module in (os, posix):
            if hasattr(module, name):
                setattr(module, name, _refuse(f"os.{name}"))
    pty.spawn = _refuse("pty.spawn")
    pty.fork = _refuse("pty.fork")
    for name in ("run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"):
        setattr(subprocess, name, _refuse(f"subprocess.{name}"))

    real_popen = subprocess.Popen

    class GuardedPopen(real_popen):
        def __init__(self, args, *rest, shell=False, **kwargs):
            if shell:
                raise PermissionError(f"{REFUSAL} (shell=True)")
            check(args)
            super().__init__(args, *rest, shell=False, **kwargs)

    subprocess.Popen = GuardedPopen

    real_exec = asyncio.create_subprocess_exec

    async def create_subprocess_exec(program, *args, **kwargs):
        check([program, *args])
        return await real_exec(program, *args, **kwargs)

    for module in (asyncio, asyncio.subprocess):
        module.create_subprocess_exec = create_subprocess_exec
        module.create_subprocess_shell = _refuse("asyncio.create_subprocess_shell")

    # Signals: only to this process itself (execnet interrupts its own worker
    # that way) and to processes it started; never a process group it does
    # not lead through a child, never by pidfd.
    real_kill, real_killpg = posix.kill, posix.killpg

    def kill(pid, sig):
        if pid != os.getpid() and not _own_child(pid):
            raise PermissionError(f"{SIGNAL_REFUSAL}: pid {pid!r}")
        return real_kill(pid, sig)

    def killpg(pgid, sig):
        if not _own_child(pgid) or os.getpgid(pgid) != pgid:
            raise PermissionError(f"{SIGNAL_REFUSAL}: process group {pgid!r}")
        return real_killpg(pgid, sig)

    for module in (os, posix):
        module.kill = kill
        module.killpg = killpg
    if hasattr(signal, "pidfd_send_signal"):
        signal.pidfd_send_signal = _refuse("signal.pidfd_send_signal")
    _guard_ctypes()
    _installed = True


# libc's own process and signal entry points, reached by name through ctypes
# (CDLL(None).system, a prototype bound to ("execve", lib), _ctypes.dlsym).
_LIBC_REFUSED = frozenset((
    "system", "popen", "fork", "vfork", "clone", "clone3", "forkpty", "daemon", "syscall",
    "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp", "fexecve", "execveat",
    "posix_spawn", "posix_spawnp", "kill", "killpg", "tgkill", "tkill", "sigqueue", "pidfd_send_signal",
))


def _libc_refused(name) -> bool:
    if not isinstance(name, (str, bytes)):
        return False  # an ordinal
    base = os.fsdecode(name).lstrip("_")  # __libc_system, __execve, _Fork
    if base.startswith("libc_"):
        base = base[len("libc_"):].lstrip("_")
    return base.lower() in _LIBC_REFUSED


def _check_symbol(name) -> None:
    if _libc_refused(name):
        raise PermissionError(f"{REFUSAL} (ctypes {os.fsdecode(name)!r})")


def _guard_ctypes() -> None:
    """Refuse the names in _LIBC_REFUSED wherever ctypes resolves a symbol;
    every other symbol (prctl, a library's own API, callbacks) still loads."""
    import _ctypes
    import ctypes

    class GuardedFuncPtr(ctypes._CFuncPtr):
        _flags_ = ctypes._FUNCFLAG_CDECL

        def __new__(cls, *args, **kwargs):
            if args and isinstance(args[0], tuple) and args[0]:
                _check_symbol(args[0][0])
            return super().__new__(cls, *args, **kwargs)

    # CDLL() and CFUNCTYPE() look the base up when called: libraries and
    # prototypes made from now on derive from the guarded one. Prototypes
    # cached before this ran are dropped so they are rebuilt on it.
    ctypes._CFuncPtr = GuardedFuncPtr
    ctypes._c_functype_cache.clear()
    old = ctypes.pythonapi._FuncPtr
    ctypes.pythonapi._FuncPtr = type("_FuncPtr", (GuardedFuncPtr,), {"_flags_": old._flags_, "_restype_": old._restype_})

    real_getitem = ctypes.CDLL.__getitem__

    def getitem(self, name_or_ordinal):
        _check_symbol(name_or_ordinal)
        return real_getitem(self, name_or_ordinal)

    ctypes.CDLL.__getitem__ = getitem  # attribute access goes through it too

    if hasattr(_ctypes, "dlsym"):
        real_dlsym = _ctypes.dlsym

        def dlsym(handle, name):
            _check_symbol(name)
            return real_dlsym(handle, name)

        _ctypes.dlsym = dlsym
