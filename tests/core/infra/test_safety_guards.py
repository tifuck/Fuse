"""Permanent guards: tests run in the sandbox, unprivileged, and cannot start
or signal processes.

Only harmless probes here: `true` as the command a guard must refuse, signal
0 (a liveness check that changes nothing) as the signal it must refuse, and
one empty file per read-only directory that the sandbox must refuse to create.
"""
import asyncio
import errno
import os
import stat
import posix
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from tests import safety

TREE = Path(__file__).resolve().parents[3]
_READ_ONLY = (errno.EROFS, errno.EPERM)


def create(path):
    """errno of creating path (O_CREAT|O_EXCL), or None if it was created."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as exc:
        return exc.errno
    os.close(fd)
    os.unlink(path)  # created by O_EXCL above: ours, empty
    return None


def test_this_run_is_inside_the_sandbox():
    assert safety.sandbox_refusal() is None
    assert os.environ["FUSE_TEST_SANDBOX"] == "systemd-run" and os.environ.get("INVOCATION_ID")
    assert safety.unit_name().startswith("fuse-test-")


def test_tests_do_not_run_as_root():
    assert os.geteuid() != 0 and os.getuid() != 0
    assert os.getegid() != 0


@pytest.mark.parametrize("directory", ["/usr", "/etc", "/var", "/tmp", "/root"])
def test_the_canary_finds_the_system_read_only(directory):
    assert create(os.path.join(directory, f".fuse-canary-{os.getpid()}")) in _READ_ONLY


@pytest.mark.parametrize("directory", ["/boot", "/opt", "/srv", "/run", "/run/lock", "/var/tmp", "/dev/shm"])
def test_the_canary_cannot_write_anywhere_else(directory):
    if not os.path.isdir(directory):
        pytest.skip(f"{directory} does not exist here")
    # Read-only, or not ours to write in (the unprivileged test user).
    assert create(os.path.join(directory, f".fuse-canary-{os.getpid()}")) in (*_READ_ONLY, errno.EACCES)


def test_home_directories_are_empty_and_closed():
    assert os.listdir("/root") == []  # no .ssh, no real tree, no .env behind the symlinks
    with pytest.raises(OSError):
        os.listdir("/home")


def test_the_tree_under_test_is_read_only_and_only_the_run_dir_is_writable(tmp_path):
    assert create(TREE / f".fuse-canary-{os.getpid()}") in _READ_ONLY
    assert create(TREE / "app" / f".fuse-canary-{os.getpid()}") in _READ_ONLY
    assert create(Path(sys.prefix) / f".fuse-canary-{os.getpid()}") in _READ_ONLY  # the venv
    assert str(tmp_path).startswith(os.environ["FUSE_TEST_RUN_DIR"])
    assert create(tmp_path / "ok") is None


def test_git_metadata_cannot_be_written():
    git = TREE / ".git"
    if git.is_dir():
        assert create(git / f".fuse-canary-{os.getpid()}") in _READ_ONLY
    elif git.exists():
        # A worktree: .git is a file naming the real git dir, which must be
        # unreachable (it lives under /root) or read-only.
        with pytest.raises(OSError) as refused:
            os.open(git, os.O_WRONLY | os.O_APPEND)
        assert refused.value.errno in (*_READ_ONLY, errno.EACCES)
        gitdir = Path(git.read_text().split("gitdir:", 1)[1].strip())
        assert create(gitdir / f".fuse-canary-{os.getpid()}") in (*_READ_ONLY, errno.ENOENT, errno.EACCES)


@pytest.mark.parametrize("name", [".env", "config.env"])
def test_the_real_settings_files_are_neither_readable_nor_writable(name):
    # Unreachable (a symlink into /root) or covered with /dev/null (a real
    # file in the tree, which the app's settings loader must still open):
    # either way no secret is readable and nothing can be written.
    path = TREE / name
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return  # unreachable: nothing to read or write
    # Covered: it is the null device, so reads are empty and writes vanish.
    assert stat.S_ISCHR(mode) and os.stat(path).st_rdev == os.stat("/dev/null").st_rdev
    with open(path, "rb") as handle:
        assert handle.read() == b""


def test_the_venv_is_not_writable():
    for path in (Path(sys.prefix) / "pyvenv.cfg", Path(sys.prefix) / "bin" / "python"):
        with pytest.raises(OSError) as refused:
            os.open(path, os.O_WRONLY | os.O_APPEND)
        assert refused.value.errno in (*_READ_ONLY, errno.EACCES)


def test_tests_use_their_own_database_account(fresh_db):
    from app import db

    assert db.fetchone("SELECT CURRENT_USER() AS who")["who"].startswith("fuse_test@")


def test_the_template_schema_can_be_read_but_never_named_in_a_write(fresh_db):
    from app import db

    assert db.fetchone("SELECT COUNT(*) AS n FROM `fuse_dev`.schema_meta")["n"] >= 0
    # A no-op even if it reached the server (which grants this account SELECT only).
    with pytest.raises(RuntimeError, match="may only read the real 'fuse_dev'"):
        db.execute("UPDATE `fuse_dev`.schema_meta SET value=value WHERE 1=0")
    with pytest.raises(RuntimeError, match="may only read"):
        db.fetchone("SELECT value FROM `fuse_dev`.schema_meta WHERE 1=0 FOR UPDATE")


@pytest.mark.parametrize("start", [
    lambda: subprocess.run(["true"]),
    lambda: subprocess.call(["true"]),
    lambda: subprocess.check_output(["true"]),
    lambda: subprocess.Popen(["true"]),
    lambda: subprocess.Popen("true", shell=True),
    lambda: os.system("true"),
    lambda: os.popen("true"),
    lambda: os.execvp("true", ["true"]),
    lambda: os.spawnlp(os.P_WAIT, "true", "true"),
    lambda: os.posix_spawnp("true", ["true"], {}),
    lambda: os.fork(),
    lambda: posix.system("true"),
    lambda: posix.fork(),
    lambda: posix.execv("/usr/bin/true", ["true"]),
    lambda: posix.posix_spawn("/usr/bin/true", ["true"], {}),
    lambda: asyncio.run(asyncio.create_subprocess_exec("true")),
    lambda: asyncio.run(asyncio.create_subprocess_shell("true")),
], ids=["run", "call", "check_output", "popen", "popen_shell", "system", "os_popen", "exec", "spawn",
        "posix_spawn", "fork", "posix_system", "posix_fork", "posix_execv", "posix_posix_spawn",
        "asyncio_exec", "asyncio_shell"])
def test_tests_may_not_start_processes(start):
    with pytest.raises(PermissionError, match="tests may not start processes"):
        start()


def test_a_reference_taken_before_the_guard_is_refused_too():
    import _posixsubprocess

    # What Popen calls at the bottom, whoever imported Popen first.
    with pytest.raises(PermissionError):
        _posixsubprocess.fork_exec([b"true"], [b"/usr/bin/true"])  # refused before the real call


@pytest.mark.parametrize("send", [
    lambda: os.kill(1, 0),
    lambda: posix.kill(1, 0),
    lambda: os.kill(os.getppid(), 0),
    lambda: os.kill(-1, 0),
    lambda: os.killpg(os.getpgrp(), 0),
    lambda: posix.killpg(1, 0),
    lambda: signal.pidfd_send_signal(0, 0),
], ids=["init", "posix_init", "parent", "everyone", "own_group", "posix_group", "pidfd"])
def test_tests_may_not_signal_processes_they_did_not_start(send):
    with pytest.raises(PermissionError):
        send()


def test_a_process_the_test_started_can_still_be_stopped():
    from tests.safety import ALLOWED

    # The image worker, waiting on stdin: an allowed child, stopped unused.
    child = subprocess.Popen([sys.executable, ALLOWED[0][1], "0", "16", "1024"],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    try:
        os.kill(child.pid, 0)
    finally:
        child.kill()
        child.wait()


def test_only_the_app_workers_are_allowed_and_only_in_their_exact_shape():
    from execnet.gateway_io import popen_bootstrapline

    from tests.safety import ALLOWED, allowed

    images = ALLOWED[0][1]
    assert allowed([sys.executable, images, "0", "2048", "5242880"])
    for argv in (
        [sys.executable, "-c", "print(1)"],
        [sys.executable, "-u", "-c", popen_bootstrapline],  # xdist workers: the controller's alone
        [sys.executable, images, "0", "2048", "5242880", "extra"],
        [sys.executable, images, "0; true", "2048", "5242880"],
        ["/bin/sh", images, "0", "2048", "5242880"],
        ["python3", images, "0", "2048", "5242880"],
        ["bash", "-c", "true"],
    ):
        assert not allowed(argv), argv
    # An allowed argv with another program behind it is refused at the bottom.
    with pytest.raises(PermissionError, match="executable"):
        subprocess.Popen([sys.executable, images, "0", "2048", "5242880"], executable="/usr/bin/true")
