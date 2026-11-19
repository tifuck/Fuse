"""Fake hosts for the tools area. Nothing here touches the real machine.

- FakeFS: an in-memory POSIX file system (files, directories, symlinks,
  hard links, modes, mtimes). It has no route to real files: every path is
  a key in a dict. It also refuses paths a confined caller must never send
  (relative, unexpanded ~, or with ".." left in them).
- FakeShell: records every command it is asked to run and answers with
  scripted output. It NEVER executes anything. The few helper scripts Fuse
  runs on a host for its own bookkeeping (realpath probe, link count, temp
  dir creation, process-group kill, connector stat probe) are answered by
  reading FakeFS, not by running them.
- FakeConn / FakeSFTP: what app.ssh_pool.connection and its SFTP client
  give app.workspace, on top of the two above.
- FakeConnector: app.agent_hub.call for a connector machine, same rules.

tests/safety.py (process guard) and the systemd sandbox stay in force
underneath: a mistake here still cannot start a process or write a host file.
"""
from __future__ import annotations

import asyncio
import errno
import itertools
import json
import posixpath
import re
import shlex
import stat
from types import SimpleNamespace

from app import db
from app.workspace_ops import transport


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
            (uid, host_id, posixpath.basename(path) or "p", path, permission_mode, ts, ts),
        )
        return int(db.last_id(conn))


def tool_json(**args) -> str:
    return json.dumps(args)


# -- in-memory file system ------------------------------------------------

class _Inode:
    _numbers = itertools.count(1000)

    def __init__(self, kind, mode, data=b"", target=""):
        self.kind, self.mode, self.data, self.target = kind, mode, bytearray(data), target
        self.ino = next(self._numbers)
        self.uid = self.gid = 1000
        self.mtime_ns = 0


def _oserror(cls, code, path):
    return cls(code, f"{errno.errorcode.get(code, code)}: {path}", path)


class FakeFS:
    """Paths are absolute POSIX strings; nothing is stored outside this object."""

    _TYPE = {"file": stat.S_IFREG, "dir": stat.S_IFDIR, "link": stat.S_IFLNK}

    def __init__(self, home="/home/dev"):
        self.home = home
        self.nodes: dict[str, _Inode] = {}
        self._clock = itertools.count(1_700_000_000_000_000_000, 1_000_000)
        self.nodes["/"] = self._new("dir", 0o755)
        for path in (home, "/tmp"):
            self.makedirs(path)

    def _new(self, kind, mode, data=b"", target=""):
        node = _Inode(kind, mode, data, target)
        node.mtime_ns = next(self._clock)
        return node

    @staticmethod
    def check(path) -> str:
        """The fake's own confinement: only clean absolute paths get in."""
        path = str(path)
        if not path.startswith("/") or path.startswith("//"):
            raise AssertionError(f"FakeFS refuses a non-absolute path: {path!r}")
        if ".." in path.split("/") or posixpath.normpath(path) != path.rstrip("/") and path != "/":
            raise AssertionError(f"FakeFS refuses an unnormalized path: {path!r}")
        return posixpath.normpath(path)

    # -- resolution
    def resolve(self, path, *, follow_last=True, missing_ok=False, depth=0) -> str:
        """Where path really points (readlink -f/-m semantics)."""
        if depth > 40:
            raise _oserror(OSError, errno.ELOOP, path)
        parts = [p for p in self.check(path).split("/") if p]
        current = "/"
        for i, part in enumerate(parts):
            candidate = posixpath.join(current, part)
            node = self.nodes.get(candidate)
            last = i == len(parts) - 1
            if node is not None and node.kind == "link" and (follow_last or not last):
                target = node.target if node.target.startswith("/") else posixpath.join(current, node.target)
                current = self.resolve(posixpath.normpath(target), missing_ok=True, depth=depth + 1)
            elif node is None and not missing_ok and not last:
                raise _oserror(FileNotFoundError, errno.ENOENT, path)
            else:
                current = candidate
        return current

    def node(self, path, *, follow=True):
        real = self.resolve(path, follow_last=follow)
        node = self.nodes.get(real)
        if node is None:
            raise _oserror(FileNotFoundError, errno.ENOENT, path)
        return real, node

    def _parent_dir(self, path):
        parent = posixpath.dirname(path)
        node = self.nodes.get(self.resolve(parent))
        if node is None:
            raise _oserror(FileNotFoundError, errno.ENOENT, path)
        if node.kind != "dir":
            raise _oserror(NotADirectoryError, errno.ENOTDIR, path)

    # -- test setup helpers
    def makedirs(self, path, mode=0o755):
        current = ""
        for part in [p for p in self.check(path).split("/") if p]:
            current += "/" + part
            node = self.nodes.get(current)
            if node is None:
                self.nodes[current] = self._new("dir", mode)
            elif node.kind != "dir":
                raise _oserror(FileExistsError, errno.EEXIST, current)

    def write(self, path, data, mode=0o644):
        path = self.check(path)
        self.makedirs(posixpath.dirname(path))
        if isinstance(data, str):
            data = data.encode()
        existing = self.nodes.get(path)
        if existing is not None and existing.kind == "file":
            existing.data[:] = data
            existing.mtime_ns = next(self._clock)
        else:
            self.nodes[path] = self._new("file", mode, data)
        return path

    def read(self, path) -> bytes:
        return bytes(self.node(path)[1].data)

    def text(self, path) -> str:
        return self.read(path).decode()

    def exists(self, path) -> bool:
        try:
            self.node(path)
            return True
        except OSError:
            return False

    def isdir(self, path) -> bool:
        try:
            return self.node(path)[1].kind == "dir"
        except OSError:
            return False

    def symlink(self, target, path):
        path = self.check(path)
        self._parent_dir(path)
        self.nodes[path] = self._new("link", 0o777, target=target)

    def link(self, existing, path):
        _, node = self.node(existing)
        self.nodes[self.check(path)] = node

    def nlink(self, path) -> int:
        _, node = self.node(path)
        return sum(1 for n in self.nodes.values() if n is node)

    def mode(self, path) -> int:
        return self.node(path)[1].mode

    def chmod(self, path, mode):
        self.node(path)[1].mode = mode & 0o7777

    def listdir(self, path):
        real, node = self.node(path)
        if node.kind != "dir":
            raise _oserror(NotADirectoryError, errno.ENOTDIR, path)
        prefix = real.rstrip("/") + "/"
        return sorted(p[len(prefix):] for p in self.nodes if p.startswith(prefix) and "/" not in p[len(prefix):])

    def attrs(self, path, *, follow=False):
        _, node = self.node(path, follow=follow)
        return SimpleNamespace(size=len(node.data) if node.kind != "link" else len(node.target),
                               mtime=node.mtime_ns // 1_000_000_000, mtime_ns=node.mtime_ns,
                               permissions=self._TYPE[node.kind] | node.mode, uid=node.uid, gid=node.gid)


class _Handle:
    def __init__(self, fs, node):
        self.fs, self.node, self.pos = fs, node, 0

    async def read(self, n=-1):
        end = len(self.node.data) if n is None or n < 0 else self.pos + n
        chunk = bytes(self.node.data[self.pos:end])
        self.pos += len(chunk)
        return chunk

    async def write(self, data):
        self.node.data[self.pos:self.pos + len(data)] = data
        self.pos += len(data)
        self.node.mtime_ns = next(self.fs._clock)

    async def stat(self):
        return SimpleNamespace(size=len(self.node.data), mtime=self.node.mtime_ns // 1_000_000_000,
                               mtime_ns=self.node.mtime_ns, permissions=stat.S_IFREG | self.node.mode,
                               uid=self.node.uid, gid=self.node.gid)


class _Open:
    def __init__(self, fs, path, mode, attrs):
        self.fs, self.path, self.mode, self.attrs = fs, path, mode, attrs

    async def __aenter__(self):
        fs, path = self.fs, self.path
        if self.mode == "rb":
            _, node = fs.node(path)
            if node.kind == "dir":
                raise _oserror(IsADirectoryError, errno.EISDIR, path)
            return _Handle(fs, node)
        if self.mode not in ("wb", "xb"):
            raise AssertionError(f"FakeSFTP: unsupported open mode {self.mode!r}")
        real = fs.resolve(path, missing_ok=True)
        node = fs.nodes.get(real)
        if node is not None and self.mode == "xb":
            raise _oserror(FileExistsError, errno.EEXIST, path)
        if node is not None and node.kind == "dir":
            raise _oserror(IsADirectoryError, errno.EISDIR, path)
        if node is None:
            fs._parent_dir(real)
            perms = getattr(self.attrs, "permissions", None)
            node = fs.nodes[real] = fs._new("file", 0o666 if perms is None else perms)
        else:
            node.data[:] = b""
            node.mtime_ns = next(fs._clock)
        return _Handle(fs, node)

    async def __aexit__(self, *exc):
        return False


class FakeSFTP:
    """The asyncssh SFTPClient surface app.workspace uses, over FakeFS."""

    def __init__(self, fs):
        self.fs = fs

    async def realpath(self, path):
        return self.fs.home if path == "." else self.fs.resolve(path, missing_ok=True)

    async def lstat(self, path):
        return self.fs.attrs(path, follow=False)

    def open(self, path, mode="rb", attrs=None):
        return _Open(self.fs, self.fs.check(path), mode, attrs)

    async def posix_rename(self, old, new):
        fs = self.fs
        node = fs.nodes.pop(fs.check(old), None)
        if node is None:
            raise _oserror(FileNotFoundError, errno.ENOENT, old)
        fs.nodes[fs.check(new)] = node

    rename = posix_rename

    async def remove(self, path):
        if self.fs.nodes.pop(self.fs.check(path), None) is None:
            raise _oserror(FileNotFoundError, errno.ENOENT, path)

    async def chmod(self, path, mode):
        self.fs.chmod(path, mode)

    async def chown(self, path, uid, gid):
        _, node = self.fs.node(path)
        node.uid, node.gid = uid, gid

    async def listdir(self, path):
        return self.fs.listdir(path)

    async def scandir(self, path):
        real, _ = self.fs.node(path)
        for name in self.fs.listdir(real):
            yield SimpleNamespace(filename=name, attrs=self.fs.attrs(posixpath.join(real, name)))

    async def makedirs(self, path, exist_ok=False):
        if self.fs.exists(path) and not exist_ok:
            raise _oserror(FileExistsError, errno.EEXIST, path)
        self.fs.makedirs(path)

    def exit(self):
        pass


# -- recording shell --------------------------------------------------------

class Reply:
    def __init__(self, out="", err="", code=0, hold=False):
        self.out, self.err, self.code = out, err, code
        self.hold = asyncio.Event() if hold else None

    def release(self, *, out=None, code=None):
        if out is not None:
            self.out = out
        if code is not None:
            self.code = code
        self.hold.set()


_JOB_CD = re.compile(r"^cd (.+) \|\| exit 1$")


class FakeShell:
    """Records commands, answers from scripts. Executes nothing, ever.

    `asked` holds every command a host was asked to run (the user command
    out of Fuse's wrapper script); `reached` only those whose working
    directory existed, i.e. the ones a real host would have started."""

    def __init__(self, fs):
        self.fs = fs
        self.asked: list[str] = []
        self.reached: list[str] = []
        self.raw: list[str] = []
        self._scripts: list[tuple[object, Reply]] = []

    def on(self, command, out="", err="", code=0, hold=False) -> Reply:
        """Script the answer to a command (exact text, or a predicate)."""
        reply = Reply(out, err, code, hold)
        self._scripts.append((command, reply))
        return reply

    def _scripted(self, command) -> Reply:
        for match, reply in self._scripts:
            if (match(command) if callable(match) else match == command):
                return reply
        return Reply("", f"fake shell: no scripted reply for {command!r}\n", 127)

    # Fuse's own helper scripts, answered from FakeFS.
    def _helper(self, script) -> Reply | None:
        if "__fuse_realpath__" in script:
            out = ["__fuse_realpath__"]
            for arg in re.findall(r"readlink -m -- (.+?) 2>/dev/null \|\|", script):
                word = shlex.split(arg.replace("~/", "\x00/", 1).replace("~", "\x00", 1))[0].replace("\x00", self.fs.home)
                try:
                    out.append(self.fs.resolve(posixpath.normpath(word), missing_ok=True))
                except (OSError, AssertionError):
                    out.append("")
            return Reply("\n".join(out) + "\n")
        match = re.fullmatch(r"stat -L -c %h -- (\S+) 2>/dev/null \|\| stat -L -f %l -- \S+ 2>/dev/null", script)
        if match:
            path = shlex.split(match.group(1))[0]
            return Reply(f"{self.fs.nlink(path)}\n" if self.fs.exists(path) else "", code=0 if self.fs.exists(path) else 1)
        match = re.fullmatch(r"mkdir -p -- (\S+)", script.strip())
        if match:
            self.fs.makedirs(shlex.split(match.group(1))[0])
            return Reply()
        if "kill -TERM --" in script and "/.cache/fuse/jobs/" in script:
            return Reply(code=0)  # the process group kill "reached" the group
        return None

    def ssh(self, command) -> tuple[str, Reply]:
        """What a real host would do with `bash -lc <script>`; returns (user command, reply)."""
        self.raw.append(command)
        argv = shlex.split(command)
        if len(argv) != 3 or argv[0] not in ("bash", "sh") or argv[1] not in ("-c", "-lc"):
            raise AssertionError(f"FakeShell: unexpected invocation {command[:120]!r}")
        script = argv[2]
        helper = self._helper(script)
        if helper is not None:
            return script, helper
        lines = script.split("\n")
        cd_at = next((i for i, line in enumerate(lines) if _JOB_CD.match(line)), None)
        if cd_at is None:  # a bounded tool script (grep/glob): cwd on its own line
            cd_at = next((i for i, line in enumerate(lines) if line.startswith("cd ") and line.endswith("|| exit 1")), None)
        user = "\n".join(lines[cd_at + 1:]).strip("\n") if cd_at is not None else script
        if cd_at is not None and lines[cd_at + 1:] and lines[cd_at + 1].startswith("printf '%s\\n' "):
            user = "\n".join(lines[cd_at + 2:]).strip("\n")  # the tool script's begin marker
        helper = self._helper(user)
        if helper is not None:
            return user, helper
        self.asked.append(user)
        if cd_at is not None and not self.fs.isdir(self._cd_target(lines[cd_at])):
            return user, Reply("", f"bash: line 1: cd: {self._cd_target(lines[cd_at])}: No such file or directory\n", 1)
        self.reached.append(user)
        return user, self._scripted(user)

    def _cd_target(self, line) -> str:
        arg = _JOB_CD.match(line).group(1)
        arg = arg.replace('"$HOME"', shlex.quote(self.fs.home))
        target = "".join(shlex.split(arg))
        return posixpath.normpath(target) if target else self.fs.home

    def connector(self, cmd, cwd) -> tuple[str, Reply]:
        cmd = cmd.removeprefix(transport._UNBUFFERED_SH).removeprefix(transport._UNBUFFERED_PS)  # Fuse's env line
        helper = self._helper(cmd)
        if helper is not None:
            return cmd, helper
        if cmd.startswith("p=") and "echo dmiss" in cmd:  # the write probe
            path = shlex.split(cmd.split("\n", 1)[0][2:])[0].replace("~", self.fs.home, 1)
            if self.fs.isdir(path):
                head = "dir\nna"
            elif self.fs.exists(path):
                head = f"{len(self.fs.read(path))}\nw"
            else:
                head = "missing\nna"
            return cmd, Reply(f"{head}\n{'dw' if self.fs.isdir(posixpath.dirname(path)) else 'dmiss'}\n")
        if cmd.startswith("p=") and 'mkdir -p -- "$(dirname -- "$p")"' in cmd:
            path = shlex.split(cmd.split("\n", 1)[0][2:])[0].replace("~", self.fs.home, 1)
            self.fs.makedirs(posixpath.dirname(path))
            return cmd, Reply()
        self.asked.append(cmd)
        target = self.fs.home if cwd in ("~", "") else cwd.replace("~", self.fs.home, 1)
        if not self.fs.isdir(target):
            return cmd, Reply("", f"chdir {cwd}: no such file or directory\n", 1)
        self.reached.append(cmd)
        return cmd, self._scripted(cmd)


class _Reader:
    def __init__(self, proc, stream):
        self.proc, self.stream = proc, stream

    async def read(self, n=-1):
        await self.proc.settled()
        data = self.proc.buffers[self.stream]
        n = len(data) if n is None or n < 0 else n
        chunk, self.proc.buffers[self.stream] = data[:n], data[n:]
        return chunk


class FakeProcess:
    """A process that never runs: its output is the scripted Reply, delivered
    when the reply is released (at once unless it was scripted to hold)."""

    def __init__(self, reply):
        self.reply = reply
        self.killed = False
        self.exit_status = None
        self.buffers = {"out": b"", "err": b""}
        self.stdout, self.stderr = _Reader(self, "out"), _Reader(self, "err")
        self._done = False
        if reply.hold is None:
            self._finish()

    def _finish(self):
        if self._done:
            return
        self._done = True
        if not self.killed:
            self.buffers = {"out": self.reply.out.encode(), "err": self.reply.err.encode()}
            self.exit_status = self.reply.code

    async def settled(self):
        if not self._done:
            await self.reply.hold.wait()
            self._finish()

    async def wait(self):
        await self.settled()
        return self

    def kill(self):
        self.killed = True
        self._finish()
        if self.reply.hold is not None:
            self.reply.hold.set()

    def close(self):
        pass


class FakeConn:
    """app.ssh_pool.connection's result: SFTP over FakeFS, exec through FakeShell."""

    def __init__(self, fs, shell):
        self.fs, self.shell = fs, shell
        self.processes: list[FakeProcess] = []

    async def start_sftp_client(self):
        return FakeSFTP(self.fs)

    async def run(self, command, check=False, timeout=None):
        _, reply = self.shell.ssh(command)
        if reply.hold is not None:
            await asyncio.wait_for(reply.hold.wait(), timeout)
        return SimpleNamespace(stdout=reply.out, stderr=reply.err, exit_status=reply.code)

    async def create_process(self, command):
        _, reply = self.shell.ssh(command)
        proc = FakeProcess(reply)
        self.processes.append(proc)
        return proc


# -- connector machine ------------------------------------------------------

class FakeConnector:
    """agent_hub.call stand-in for a connector machine: file ops on FakeFS,
    exec ops through FakeShell (recorded, never run). `overrides` maps an op
    to a dict or callable(args) -> dict (a legacy connector, an error)."""

    def __init__(self, fs, shell, **overrides):
        self.fs, self.shell, self.overrides = fs, shell, overrides
        self.calls: list[tuple[str, dict]] = []

    def _path(self, path):
        return self.fs.home + path[1:] if path.startswith("~") else path

    async def __call__(self, user_id, host_id, op, args, timeout=30, on_chunk=None):
        self.calls.append((op, dict(args)))
        if op in self.overrides:
            answer = self.overrides[op]
            return answer(args) if callable(answer) else dict(answer)
        return await getattr(self, "op_" + op)(args, on_chunk)

    async def op_read(self, args, _on_chunk):
        try:
            text = self.fs.text(self._path(args["path"]))
        except (OSError, AssertionError) as exc:
            return {"ok": False, "exit": 1, "out": "", "err": "", "error": str(exc)}
        lines = text.splitlines(keepends=True)
        start = int(args.get("offset") or 1) - 1
        page = lines[start:start + int(args.get("limit") or 2000)]
        return {"ok": True, "exit": 0, "out": "".join(page), "err": "", "error": "", "truncated": False}

    async def op_list(self, args, _on_chunk):
        path = self._path(args["path"])
        if not self.fs.isdir(path):
            return {"ok": False, "exit": 1, "out": "", "err": "", "error": "not a directory"}
        names = [n + "/" if self.fs.isdir(posixpath.join(path, n)) else n for n in self.fs.listdir(path)]
        return {"ok": True, "exit": 0, "out": "\n".join(names), "err": "", "error": ""}

    async def op_write(self, args, _on_chunk):
        path = self._path(args["path"])
        if not self.fs.isdir(posixpath.dirname(path)):
            return {"ok": False, "exit": 1, "out": "", "err": "", "error": "write failed"}
        self.fs.write(path, args["content"])
        return {"ok": True, "exit": 0, "out": "", "err": "", "error": ""}

    async def op_replace(self, args, _on_chunk):
        path = self._path(args["path"])
        if not self.fs.exists(path) or self.fs.isdir(path):
            return {"ok": False, "exit": 1, "out": "", "err": "", "error": "file does not exist"}
        text = self.fs.read(path).decode()
        count = text.count(args["old"])
        if count == 0:
            return {"ok": False, "exit": 1, "out": "", "err": "", "error": "old_string not found"}
        if count > 1 and not args.get("replace_all"):
            return {"ok": False, "exit": 1, "out": "", "err": "", "error": f"old_string not unique ({count} matches)"}
        text = text.replace(args["old"], args["new"]) if args.get("replace_all") else text.replace(args["old"], args["new"], 1)
        self.fs.write(path, text)
        return {"ok": True, "exit": 0, "out": f"{count if args.get('replace_all') else 1} replacement", "err": "", "error": ""}

    async def op_exec(self, args, _on_chunk):
        _, reply = self.shell.connector(args["cmd"], args.get("cwd") or "~")
        if reply.hold is not None:
            await reply.hold.wait()
        code = reply.code
        return {"ok": code == 0, "exit": code, "out": reply.out, "err": reply.err,
                "error": "" if code == 0 else f"exit {code}", "truncated": False}

    async def op_exec_stream(self, args, on_chunk):
        result = await self.op_exec(args, None)
        if on_chunk is not None and result["out"]:
            on_chunk("out", result["out"])
            result["out"] = ""
        if on_chunk is not None and result["err"]:
            on_chunk("err", result["err"])
            result["err"] = ""
        return result


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
