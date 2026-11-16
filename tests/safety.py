"""Test-run safety: tests never start real processes and never run unsandboxed.

On 2026-11-16 a test fed "rm -rf /*" to a fake host that ran it in a real
local bash as root, a mutant had switched the denylist off, and the machine
was wiped. Two independent layers keep that from happening again:

- sandbox_refusal(): tests run only inside the systemd sandbox that
  scripts/test.sh starts (read-only system and home, writable only the tree
  under test and a per-run temp dir). Checked before anything else is
  imported; outside it, the run stops.
- install_process_guard(): no test may start a process. Every way Python
  starts one raises, except two exact app workers that take no shell (see
  ALLOWED below).

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
# Inside the sandbox these are read-only mounts: creating a file fails with
# EROFS (EPERM where a security module refuses first). Outside it, as root,
# the create succeeds and the empty probe file is removed at once.
PROBE_DIRS = ("/usr", "/etc")
_REFUSED = (errno.EROFS, errno.EPERM)

_APP = Path(__file__).resolve().parents[1] / "app"
# The only processes tests may start: app workers that decode untrusted
# input in a disposable interpreter. Exact argv shape, the test run's own
# interpreter, never a shell, never "-c".
#   app/file_images.py:  [python, <app>/file_images.py, frame, max_side, max_bytes]
#   app/search/engine.py: [python, -I, <app>/search/pdf_worker.py, max_bytes]
ALLOWED = (
    ((), str(_APP / "file_images.py"), 3),
    (("-I",), str(_APP / "search" / "pdf_worker.py"), 1),
)

REFUSAL = "tests may not start processes"


def _probe_refused(directory: str) -> bool:
    path = os.path.join(directory, f".fuse-test-sandbox-probe-{os.getpid()}")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as exc:
        return exc.errno in _REFUSED
    os.close(fd)
    os.unlink(path)  # we created it (O_EXCL): an empty file, ours alone
    return False


def sandbox_refusal() -> str | None:
    """None inside the test sandbox, else why the run must stop."""
    how = "Run tests only through scripts/test.sh (it starts the systemd sandbox)."
    if os.environ.get(SANDBOX_ENV) != SANDBOX_TOKEN or not os.environ.get("INVOCATION_ID"):
        return f"not inside the test sandbox ({SANDBOX_ENV} / systemd unit missing). {how}"
    for directory in PROBE_DIRS:
        if not _probe_refused(directory):
            return f"{directory} is writable, so this is not the test sandbox. {how}"
    return None


def _argv(args) -> list[str]:
    if isinstance(args, (str, bytes, os.PathLike)):
        args = [args]
    return [os.fsdecode(a) for a in args]


def allowed(args) -> bool:
    argv = _argv(args)
    if not argv or argv[0] != sys.executable:
        return False
    for flags, script, extra in ALLOWED:
        head = [sys.executable, *flags, script]
        tail = argv[len(head):]
        if argv[:len(head)] == head and len(tail) == extra and all(a.isdigit() for a in tail):
            return True
    return False


def check(args) -> None:
    if not allowed(args):
        raise PermissionError(f"{REFUSAL}: {_argv(args)[:4]!r}")


def _refuse(name):
    def refused(*_args, **_kwargs):
        raise PermissionError(f"{REFUSAL} ({name})")

    refused.__name__ = f"refused_{name}"
    return refused


_installed = False


def install_process_guard() -> None:
    """Replace every process-starting entry point for this interpreter."""
    global _installed
    if _installed:
        return
    import _posixsubprocess
    import pty
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
        if hasattr(os, name):
            real = getattr(os, name)

            def spawn(path, argv, *rest, _real=real, **kwargs):
                check(argv)
                if os.fsdecode(path) != sys.executable:
                    raise PermissionError(f"{REFUSAL}: executable {os.fsdecode(path)!r}")
                return _real(path, argv, *rest, **kwargs)

            setattr(os, name, spawn)

    # Everything that runs a shell or replaces/forks this process: no exceptions.
    for name in ("system", "popen", "fork", "forkpty", "execv", "execve", "execl", "execle", "execlp",
                 "execlpe", "execvp", "execvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv",
                 "spawnve", "spawnvp", "spawnvpe"):
        if hasattr(os, name):
            setattr(os, name, _refuse(f"os.{name}"))
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
    _installed = True
