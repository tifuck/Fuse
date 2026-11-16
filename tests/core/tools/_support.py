"""Fake host transports for the tools area.

The SSH boundary is ``app.ssh_pool.connection``: ``LocalConn`` stands in for
the asyncssh connection and runs everything against this machine — SFTP calls
on the real filesystem, shell commands in a local bash (HOME pointed at a
scratch dir). So workspace's own logic (confinement, paging, atomic writes,
search command construction, job supervision) runs unchanged on real files.

The connector boundary is ``app.agent_hub.call``: ``FakeConnector`` answers
the ops Fuse sends to a connector machine from the same local files.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
from types import SimpleNamespace

from app import db


def make_host(uid, alias="box", *, default_cwd="", auth_type="password", agent_os="", agent_version="") -> int:
    """A user_hosts row; the transport itself is faked, so no secret is needed."""
    ts = db.now()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO user_hosts (user_id, alias, hostname, username, auth_type, secret, default_cwd,"
            " agent_os, agent_version, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (uid, alias, "host.invalid", "dev", auth_type, "", default_cwd, agent_os, agent_version, ts, ts),
        )
        return int(db.last_id(conn))


def make_project(uid, host_id, path, *, permission_mode="project") -> int:
    ts = db.now()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO user_projects (user_id, host_id, name, remote_path, permission_mode, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (uid, host_id, os.path.basename(path) or "p", path, permission_mode, ts, ts),
        )
        return int(db.last_id(conn))


# -- SFTP on the local filesystem ---------------------------------------

def _attrs(st) -> SimpleNamespace:
    return SimpleNamespace(size=st.st_size, mtime=int(st.st_mtime), mtime_ns=st.st_mtime_ns,
                           permissions=st.st_mode, uid=st.st_uid, gid=st.st_gid)


class _Handle:
    def __init__(self, f):
        self.f = f

    async def read(self, n=-1):
        return self.f.read(n)

    async def write(self, data):
        self.f.write(data)

    async def stat(self):
        return _attrs(os.fstat(self.f.fileno()))


class _Opener:
    def __init__(self, path, mode, attrs):
        self.path, self.mode, self.attrs = path, mode, attrs
        self.handle = None

    async def __aenter__(self):
        if self.mode == "xb":
            perms = getattr(self.attrs, "permissions", None)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666 if perms is None else perms)
            f = os.fdopen(fd, "wb")
        else:
            f = open(self.path, self.mode)
        self.handle = _Handle(f)
        return self.handle

    async def __aexit__(self, *exc):
        self.handle.f.close()
        return False


class LocalSFTP:
    def __init__(self, home):
        self.home = home

    async def realpath(self, path):
        return self.home if path == "." else os.path.realpath(path)

    async def lstat(self, path):
        return _attrs(os.lstat(path))

    def open(self, path, mode="rb", attrs=None):
        return _Opener(path, mode, attrs)

    async def posix_rename(self, old, new):
        os.replace(old, new)

    async def rename(self, old, new):
        os.rename(old, new)

    async def remove(self, path):
        os.remove(path)

    async def chmod(self, path, mode):
        os.chmod(path, mode)

    async def listdir(self, path):
        return os.listdir(path)

    async def scandir(self, path):
        for name in os.listdir(path):
            yield SimpleNamespace(filename=name, attrs=_attrs(os.lstat(os.path.join(path, name))))

    async def makedirs(self, path, exist_ok=False):
        os.makedirs(path, exist_ok=exist_ok)

    def exit(self):
        pass


# -- shell on the local machine -----------------------------------------

class _Proc:
    def __init__(self, proc):
        self.proc = proc
        self.stdout = proc.stdout
        self.stderr = proc.stderr

    async def wait(self):
        await self.proc.wait()

    @property
    def exit_status(self):
        return self.proc.returncode

    def kill(self):
        if self.proc.returncode is None:
            self.proc.kill()
        # Nobody awaits a killed channel: close its pipes while the loop lives.
        transport = getattr(self.proc, "_transport", None)
        if transport is not None:
            transport.close()

    def close(self):
        pass


class LocalConn:
    """asyncssh.SSHClientConnection stand-in: local bash, local files."""

    def __init__(self, home):
        self.home = home
        self.env = {"HOME": home, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
        self.commands: list[str] = []

    def _argv(self, command):
        argv = shlex.split(command)
        if argv[:2] == ["bash", "-lc"]:
            argv[1] = "-c"  # no login profile of the test machine
        return argv

    async def start_sftp_client(self):
        return LocalSFTP(self.home)

    async def run(self, command, check=False, timeout=None):
        self.commands.append(command)
        proc = await asyncio.create_subprocess_exec(
            *self._argv(command), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=self.env,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        return SimpleNamespace(stdout=out.decode("utf-8", "replace"), stderr=err.decode("utf-8", "replace"),
                               exit_status=proc.returncode)

    async def create_process(self, command):
        self.commands.append(command)
        proc = await asyncio.create_subprocess_exec(
            *self._argv(command), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=self.env,
        )
        return _Proc(proc)


# -- connector machine ----------------------------------------------------

class FakeConnector:
    """agent_hub.call stand-in answering connector ops from local files.

    ``overrides`` maps an op to a dict or callable(args) -> dict that replaces
    the simulated answer (a legacy connector, an error). ``calls`` records
    every (op, args) Fuse sent."""

    def __init__(self, home, **overrides):
        self.home = home
        self.overrides = overrides
        self.calls: list[tuple[str, dict]] = []

    def _path(self, path):
        return self.home + path[1:] if path.startswith("~") else path

    async def __call__(self, user_id, host_id, op, args, timeout=30, on_chunk=None):
        self.calls.append((op, dict(args)))
        if op in self.overrides:
            answer = self.overrides[op]
            return answer(args) if callable(answer) else dict(answer)
        return await getattr(self, "op_" + op)(args, on_chunk)

    async def op_read(self, args, _on_chunk):
        try:
            with open(self._path(args["path"]), encoding="utf-8", newline="") as f:
                lines = f.read().splitlines(keepends=True)
        except OSError as exc:
            return {"ok": False, "exit": 1, "out": "", "err": "", "error": str(exc)}
        start = int(args.get("offset") or 1) - 1
        page = lines[start:start + int(args.get("limit") or 2000)]
        return {"ok": True, "exit": 0, "out": "".join(page), "err": "", "error": "", "truncated": False}

    async def op_list(self, args, _on_chunk):
        path = self._path(args["path"])
        if not os.path.isdir(path):
            return {"ok": False, "exit": 1, "out": "", "err": "", "error": "not a directory"}
        names = sorted(n + "/" if os.path.isdir(os.path.join(path, n)) else n for n in os.listdir(path))
        return {"ok": True, "exit": 0, "out": "\n".join(names), "err": "", "error": ""}

    async def op_write(self, args, _on_chunk):
        path = self._path(args["path"])
        if not os.path.isdir(os.path.dirname(path)):
            return {"ok": False, "exit": 1, "out": "", "err": "", "error": "write failed"}
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(args["content"])
        return {"ok": True, "exit": 0, "out": "", "err": "", "error": ""}

    async def op_replace(self, args, _on_chunk):
        path = self._path(args["path"])
        if not os.path.isfile(path):
            return {"ok": False, "exit": 1, "out": "", "err": "", "error": "file does not exist"}
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        count = text.count(args["old"])
        if count == 0:
            return {"ok": False, "exit": 1, "out": "", "err": "", "error": "old_string not found"}
        if count > 1 and not args.get("replace_all"):
            return {"ok": False, "exit": 1, "out": "", "err": "", "error": f"old_string not unique ({count} matches)"}
        text = text.replace(args["old"], args["new"]) if args.get("replace_all") else text.replace(args["old"], args["new"], 1)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return {"ok": True, "exit": 0, "out": f"{count if args.get('replace_all') else 1} replacement", "err": "", "error": ""}

    async def op_exec(self, args, _on_chunk):
        cwd = self._path(args.get("cwd") or "~")
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", args["cmd"], cwd=cwd, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={"HOME": self.home, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"},
        )
        out, err = await proc.communicate()
        code = proc.returncode
        return {"ok": code == 0, "exit": code, "out": out.decode(), "err": err.decode(),
                "error": "" if code == 0 else f"exit {code}", "truncated": False}

    async def op_exec_stream(self, args, on_chunk):
        result = await self.op_exec(args, None)
        if on_chunk is not None and result["out"]:
            on_chunk("out", result.pop("out"))
            result["out"] = ""
        return result


def tool_json(**args) -> str:
    return json.dumps(args)


# -- scripted command runners and chat rows -------------------------------

class ScriptedRunner:
    """A command_jobs runner the test drives: sinks `out`, then waits for
    release() and ends with exit `code` or raises `exc`."""

    def __init__(self, out="", code=0, exc=None):
        self.out, self.code, self.exc = out, code, exc
        self.started = asyncio.Event()
        self.released = asyncio.Event()
        self.cancelled = False

    def release(self, *, code=None, exc=None):
        if code is not None:
            self.code = code
        if exc is not None:
            self.exc = exc
        self.released.set()

    async def __call__(self, sink):
        if self.out:
            sink(self.out)
        self.started.set()
        try:
            await self.released.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(exit_status=self.code, output="")


async def launch(uid, runner, *, thread_id=None, run_id=None, background=True, command="make test", **kwargs):
    from app import command_jobs

    return await command_jobs.launch_async(
        user_id=uid, host_id=kwargs.pop("host_id", 1), host=kwargs.pop("host", "box"), thread_id=thread_id,
        run_id=run_id, command=command, cwd="/srv/app", background=background,
        timeout_ms=kwargs.pop("timeout_ms", 60_000), runner=runner, **kwargs)


def new_thread(uid):
    """(public thread dict, integer pk) of a fresh chat."""
    from app import chats

    thread = chats.create_thread(uid, model="offline")
    return thread, chats.resolve_thread_pk(uid, thread["id"])


def new_run(uid, thread=None):
    """A queued root run (status counts as live) on a fresh or given thread."""
    from app import chat_store, chats

    thread = thread or chats.create_thread(uid, model="offline")
    current = chats.get_thread(uid, thread["id"])
    run = chat_store.start_turn(uid, thread["id"], "work", current["revision"], None)["run"]
    return thread, chats.resolve_thread_pk(uid, thread["id"]), run["id"]


class CardUser:
    """A permission Gate's notify callback: records events and answers the first
    raised card with `answer` (an action name, or a callable(event)), as the
    HTTP decision route would."""

    def __init__(self, uid, tid, answer=None):
        self.uid, self.tid, self.answer = uid, tid, answer
        self.events = []

    def __call__(self, event):
        self.events.append(event)
        if event.get("status") == "pending" and self.answer is not None:
            answer, self.answer = self.answer, None
            if callable(answer):
                answer(event)
            else:
                from app import permissions

                permissions.resolve(self.uid, self.tid, event["id"], answer)

    @property
    def asks(self):
        return [e for e in self.events if e.get("status") == "pending"]
