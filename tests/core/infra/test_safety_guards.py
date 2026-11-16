"""Permanent guards: tests run in the sandbox and cannot start processes.

Only harmless probes here: `true` as the command a guard must refuse, and
one empty file per read-only directory that the sandbox must refuse to create.
"""
import asyncio
import errno
import os
import subprocess
import sys

import pytest

from tests import safety


def test_this_run_is_inside_the_sandbox():
    assert safety.sandbox_refusal() is None
    assert os.environ["FUSE_TEST_SANDBOX"] == "systemd-run" and os.environ.get("INVOCATION_ID")


@pytest.mark.parametrize("directory", ["/usr", "/etc", "/root", "/var", "/boot", "/opt"])
def test_the_canary_cannot_write_outside_the_sandbox(directory):
    path = os.path.join(directory, f".fuse-canary-{os.getpid()}")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileNotFoundError:
        pytest.skip(f"{directory} does not exist here")
    except OSError as exc:
        assert exc.errno in (errno.EROFS, errno.EPERM), exc
    else:
        os.close(fd)
        os.unlink(path)  # created by O_EXCL above: ours, empty
        pytest.fail(f"{directory} is writable inside the test sandbox")


def test_the_tree_and_temp_dir_stay_writable_but_git_does_not(tmp_path):
    (tmp_path / "ok").write_text("x")
    with pytest.raises(OSError) as refused:
        os.open(".git", os.O_WRONLY | os.O_APPEND)
    assert refused.value.errno in (errno.EROFS, errno.EPERM, errno.EISDIR)


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
    lambda: asyncio.run(asyncio.create_subprocess_exec("true")),
    lambda: asyncio.run(asyncio.create_subprocess_shell("true")),
], ids=["run", "call", "check_output", "popen", "popen_shell", "system", "os_popen", "exec", "spawn",
        "posix_spawn", "fork", "asyncio_exec", "asyncio_shell"])
def test_tests_may_not_start_processes(start):
    with pytest.raises(PermissionError, match="tests may not start processes"):
        start()


def test_a_reference_taken_before_the_guard_is_refused_too():
    import _posixsubprocess

    # What Popen calls at the bottom, whoever imported Popen first.
    with pytest.raises(PermissionError):
        _posixsubprocess.fork_exec([b"true"], [b"/usr/bin/true"])  # refused before the real call


def test_only_the_app_workers_are_allowed_and_only_in_their_exact_shape():
    from tests.safety import ALLOWED, allowed

    images = ALLOWED[0][1]
    assert allowed([sys.executable, images, "0", "2048", "5242880"])
    for argv in (
        [sys.executable, "-c", "print(1)"],
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
