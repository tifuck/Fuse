"""Interactive terminals: a real login shell on one of the user's own
machines, streamed to the browser over /terminal/ws (app/web/terminal.py).

A terminal is not an agent tool. Nothing here goes through
app/permissions.py, the command denylist, read-only modes or the command
rate limit; the only checks are that the signed-in user owns the host and
the host is not disabled. No terminal byte is logged, stored or shown to a
model, and a shell never runs on the Fuse server itself.

Sessions live in memory (one uvicorn worker by design) and outlive their
browser socket: detached, the shell keeps running into a RING_BYTES
scrollback ring for DETACH_SECONDS, then closes. Backends: "agent" (a
connector's pty_open job, app/agent_hub.py) and "ssh" (a PTY channel on the
pooled asyncssh connection, app/ssh_pool.py). Flow control: once more than
HIGH_WATER bytes sent to the browser are unacked the backend stops being
read, and it resumes below LOW_WATER. The terminal_sessions table holds
metadata only, never input or output.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import secrets
import shlex

from . import agent_hub, db, hosts, ssh_pool, vault
from .hosts import HostError

logger = logging.getLogger(__name__)

RING_BYTES = 256 * 1024
FRAME_BYTES = 32 * 1024
HIGH_WATER = 512 * 1024
LOW_WATER = 128 * 1024
# A socket whose queue still grows past this (a connector ignoring its
# window) is dropped; the browser reconnects and replays the ring.
QUEUE_LIMIT = 4 * 1024 * 1024
DETACH_SECONDS = 600.0
ENDED_SECONDS = 60.0
MAX_PER_USER = 8
MAX_PER_HOST = 4
LINUX_SHELLS = (("login", "Login shell"),)
WINDOWS_SHELLS = (("powershell", "PowerShell"), ("cmd", "Command Prompt"), ("pwsh", "PowerShell 7"))
_SELF_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

_sessions: dict[str, "Session"] = {}
_background: set[asyncio.Task] = set()


class TerminalError(HostError):
    pass


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


def _os(row) -> str:
    name = str(row["agent_os"] or "").strip().lower()
    return "windows" if name.startswith("win") else "linux" if name.startswith("linux") else ""


def _shells(user_id: int, row, os_name: str) -> list[dict]:
    if os_name != "windows":
        return [{"id": sid, "label": label} for sid, label in LINUX_SHELLS]
    live = agent_hub.shells(user_id, row["id"]) if row["auth_type"] == "agent" else None
    names = set(live) if live is not None else {"powershell", "cmd"}
    return [{"id": sid, "label": label} for sid, label in WINDOWS_SHELLS if sid in names]


def _on_this_server(row) -> bool:
    if str(row["alias"] or "").strip().lower() in hosts.RESERVED_ALIASES:
        return True
    return row["auth_type"] != "agent" and str(row["hostname"] or "").strip().lower() in _SELF_HOSTNAMES


def host_list(user_id: int) -> list[dict]:
    """GET /terminal/hosts: the user's enabled machines a terminal can open on."""
    rows = db.fetchall("SELECT * FROM user_hosts WHERE user_id=? AND disabled=0 ORDER BY alias", (user_id,))
    unlocked = vault.unlocked(user_id)
    out = []
    for row in rows:
        if _on_this_server(row):
            continue
        agent = row["auth_type"] == "agent"
        os_name = _os(row)
        shells = _shells(user_id, row, os_name)
        out.append({
            "id": int(row["id"]),
            "alias": row["alias"],
            "os": os_name,
            "transport": "agent" if agent else "ssh",
            "online": agent_hub.is_online(user_id, int(row["id"])) if agent else unlocked,
            "shells": shells,
            "default_shell": shells[0]["id"],
        })
    return out


def _clamp(value, low: int, high: int, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(low, min(high, value))


class Client:
    """One attached browser socket's outbound side. Output and control frames
    queue here without awaiting; the socket's sender drains them."""

    def __init__(self):
        self.items: collections.deque = collections.deque()
        self.queued = 0
        self.wake = asyncio.Event()
        self.close_code: int | None = None
        self.drain = True

    def push(self, item) -> None:
        if self.close_code is not None:
            return
        self.items.append(item)
        if isinstance(item, bytes):
            self.queued += len(item)
        self.wake.set()

    def end(self, code: int, *, drain: bool = False) -> None:
        """Close the socket with code: after what is queued (drain), or now."""
        if self.close_code is None:
            self.close_code, self.drain = code, drain
        self.wake.set()


class Session:
    def __init__(self, *, uid: int, login: str, row, transport: str, os_name: str, shell: str, cwd: str,
                 thread_id, project_id, client_ip: str, user_agent: str):
        self.id = secrets.token_urlsafe(18)
        self.uid = int(uid)
        self.login = login  # the sign-in (session cookie sid) that opened it: logout closes it
        self.host_id = int(row["id"])
        self.alias = row["alias"]
        self.transport = transport
        self.os = os_name
        self.shell = shell
        self.cwd = cwd
        self.user = str(row["username"] or "")
        self.root = self.user == "root" or self.user.lower() in ("system", "nt authority\\system")
        self.thread_id = thread_id
        self.project_id = project_id
        self.client_ip = client_ip[:64]
        self.user_agent = user_agent[:255]
        self.created = db.now()
        self.title = f"{self.user}@{self.alias}" if self.user else self.alias
        self.cols, self.rows = 80, 24
        self.ring = bytearray()
        self.client: Client | None = None
        self.unacked = 0      # sent to the browser, not acked (replay included)
        self.replay_left = 0  # of those, replay bytes
        self.owed = 0         # read from the backend, not yet acked to it
        self.paused = False
        self.alive = True
        self.exit_code: int | None = None
        self.reason = ""
        self.bytes_in = 0
        self.bytes_out = 0
        self.backend = None
        self._detach_timer: asyncio.TimerHandle | None = None

    def view(self) -> dict:
        return {"id": self.id, "host_id": self.host_id, "alias": self.alias, "os": self.os,
                "transport": self.transport, "user": self.user, "root": self.root, "cwd": self.cwd or "~",
                "shell": self.shell, "title": self.title}

    def listing(self) -> dict:
        return {**self.view(), "created": self.created, "attached": self.client is not None, "alive": self.alive}

    # -- output (inline from the backend: never awaits) --------------------
    def feed(self, data: bytes) -> None:
        if not data or not self.alive:
            return
        self.bytes_out += len(data)
        self.ring += data
        if len(self.ring) > RING_BYTES:
            del self.ring[:len(self.ring) - RING_BYTES]
        client = self.client
        if client is None:
            self.backend.ack(len(data))  # detached: never stall the shell
            return
        self.owed += len(data)
        self._send(client, data)

    def _send(self, client: Client, data: bytes) -> None:
        for start in range(0, len(data), FRAME_BYTES):
            client.push(bytes(data[start:start + FRAME_BYTES]))
        self.unacked += len(data)
        if client.queued > QUEUE_LIMIT:
            client.end(1013)
            self.detach(client)
        elif self.unacked > HIGH_WATER and not self.paused:
            self.paused = True
            self.backend.pause()

    def ack(self, n) -> None:
        """The browser processed n more bytes."""
        if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
            return
        n = min(n, self.unacked)
        self.unacked -= n
        live = min(max(0, n - self.replay_left), self.owed)
        self.replay_left = max(0, self.replay_left - n)
        if live:
            self.owed -= live
            self.backend.ack(live)
        if self.paused and self.unacked < LOW_WATER:
            self.paused = False
            self.backend.resume()

    def _settle(self) -> None:
        """Forget the browser window: credit everything owed, read again."""
        if self.owed:
            self.backend.ack(self.owed)
        self.owed = self.unacked = self.replay_left = 0
        if self.paused:
            self.paused = False
            self.backend.resume()

    # -- sockets --------------------------------------------------------------
    def attach(self, client: Client) -> None:
        """A socket takes the session over (a second attach replaces the
        first, 4409): ready, the ring's replay, then live output."""
        old, self.client = self.client, client
        if old is not None:
            old.end(4409)
        if self._detach_timer is not None:
            self._detach_timer.cancel()
            self._detach_timer = None
        self._settle()
        client.push({"t": "ready", **self.view()})
        replay = bytes(self.ring)
        self.replay_left = len(replay)
        if replay:
            self._send(client, replay)

    def detach(self, client: Client) -> None:
        if self.client is not client:
            return
        self.client = None
        self._settle()
        if self.alive:
            self._arm_detach()

    def _arm_detach(self) -> None:
        loop = asyncio.get_running_loop()
        self._detach_timer = loop.call_later(DETACH_SECONDS, lambda: _spawn(self.close("detached")))

    async def write(self, data: bytes) -> None:
        if not self.alive or not data:
            return
        self.bytes_in += len(data)
        await self.backend.write(data)

    async def resize(self, cols, rows) -> None:
        cols, rows = _clamp(cols, 2, 1000, self.cols), _clamp(rows, 1, 500, self.rows)
        if (cols, rows) == (self.cols, self.rows) or not self.alive:
            return
        self.cols, self.rows = cols, rows
        await self.backend.resize(cols, rows)

    # -- ending ---------------------------------------------------------------
    async def close(self, reason: str) -> None:
        """Kill the shell (user close, teardown, detach timeout)."""
        if not self.alive:
            return
        self.finished(None, reason)
        try:
            await self.backend.close()
        except Exception:
            logger.exception("terminal backend close failed")

    def finished(self, code: int | None, reason: str) -> None:
        """The session is over: the shell exited, was killed, or its machine
        went away. Tells the attached socket (4410) and records the close."""
        if not self.alive:
            return
        self.alive = False
        self.exit_code, self.reason = code, reason
        if self._detach_timer is not None:
            self._detach_timer.cancel()
            self._detach_timer = None
        client = self.client
        if client is not None:
            client.push({"t": "exit", "code": code, "reason": reason})
            client.end(4410, drain=True)
        self.ring.clear()
        _spawn(_record_close(self))
        asyncio.get_running_loop().call_later(ENDED_SECONDS, lambda: _sessions.pop(self.id, None))


class _AgentPty:
    """A connector's pty_open job. Credit (pty_ack) goes back only for bytes
    the browser processed, or at once while detached, so the connector's own
    window is the flow control: pause and resume have nothing to do."""

    def __init__(self, session: Session):
        self.s = session
        self.job_id = ""
        self._credit = 0
        self._crediting = False

    async def open(self) -> None:
        s = self.s
        info = await agent_hub.open_pty(s.uid, s.host_id, cols=s.cols, rows=s.rows, cwd=s.cwd,
                                        shell="" if s.shell == "login" else s.shell, on_output=s.feed)
        self.job_id = info["id"]
        _spawn(self._wait(info["done"]))
        if self._credit:
            _spawn(self._flush())

    async def _wait(self, done) -> None:
        try:
            result = await done
        except agent_hub.AgentError:
            self.s.finished(None, "offline")
            return
        self.s.finished(result.get("exit"), "exit")

    def ack(self, n: int) -> None:
        self._credit += n
        if self.job_id and not self._crediting:
            _spawn(self._flush())

    async def _flush(self) -> None:
        if self._crediting:
            return
        self._crediting = True
        try:
            while self._credit and self.s.alive:
                n, self._credit = self._credit, 0
                await agent_hub.pty_ack(self.s.uid, self.s.host_id, self.job_id, n)
        finally:
            self._crediting = False

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    async def write(self, data: bytes) -> None:
        await agent_hub.pty_input(self.s.uid, self.s.host_id, self.job_id, data)

    async def resize(self, cols: int, rows: int) -> None:
        await agent_hub.pty_resize(self.s.uid, self.s.host_id, self.job_id, cols, rows)

    async def close(self) -> None:
        if self.job_id:
            await agent_hub.close_pty(self.s.uid, self.s.host_id, self.job_id)


def _ssh_command(os_name: str, shell: str, cwd: str) -> str | None:
    """What the PTY channel runs: None is the account's login shell."""
    if os_name == "windows":
        if shell == "cmd":
            return f'cmd.exe /K cd /d "{cwd}"' if cwd and not set(cwd) & set('"%^&|<>') else "cmd.exe"
        if cwd:
            return "powershell.exe -NoLogo -NoExit -Command \"Set-Location -LiteralPath '{}'\"".format(
                cwd.replace("'", "''").replace('"', ""))
        return "powershell.exe -NoLogo"
    if not cwd or cwd == "~":
        return None
    target = '"$HOME"/' + shlex.quote(cwd[2:]) if cwd.startswith("~/") else shlex.quote(cwd)
    return f'cd -- {target} 2>/dev/null; exec "${{SHELL:-/bin/sh}}" -l'


class _SshPty:
    """A PTY channel on the pooled SSH connection. Pausing stops reading the
    channel; asyncssh's window then holds the remote side."""

    def __init__(self, session: Session):
        self.s = session
        self.proc = None
        self.flowing = asyncio.Event()
        self.flowing.set()

    async def open(self) -> None:
        s = self.s
        self.proc = await ssh_pool.open_pty(s.uid, s.host_id, cols=s.cols, rows=s.rows,
                                            command=_ssh_command(s.os, s.shell, s.cwd))
        _spawn(self._read())

    async def _read(self) -> None:
        reason = "exit"
        try:
            while True:
                await self.flowing.wait()
                data = await self.proc.stdout.read(FRAME_BYTES)
                if not data:
                    break
                self.s.feed(data)
            await asyncio.wait_for(self.proc.wait_closed(), 5)
        except Exception:
            reason = "offline"
        finally:
            # An open channel keeps ssh_pool counting the connection busy, so
            # it would never be reaped. Closing again is a no-op.
            try:
                self.proc.close()
            except Exception:
                logger.debug("terminal ssh close failed", exc_info=True)
        self.s.finished(self.proc.exit_status if reason == "exit" else None, reason)

    def ack(self, n: int) -> None:
        pass

    def pause(self) -> None:
        self.flowing.clear()

    def resume(self) -> None:
        self.flowing.set()

    async def write(self, data: bytes) -> None:
        try:
            self.proc.stdin.write(data)
            await asyncio.wait_for(self.proc.stdin.drain(), 10)
        except Exception:
            logger.debug("terminal ssh write failed", exc_info=True)

    async def resize(self, cols: int, rows: int) -> None:
        try:
            self.proc.change_terminal_size(cols, rows)
        except Exception:
            logger.debug("terminal ssh resize failed", exc_info=True)

    async def close(self) -> None:
        self.flowing.set()
        if self.proc is not None:
            self.proc.close()


# -- registry -------------------------------------------------------------------

def _binding(user_id: int, thread_id) -> tuple[int, int | None, int | None, str] | None:
    """(thread pk, host_id, project_id, cwd) of a chat; host_id None for a
    chat with no project. The cwd comes from the server-side binding only."""
    from . import chats

    pk = chats.resolve_thread_pk(user_id, thread_id)
    row = db.fetchone("SELECT kind, host_id, project_id FROM chat_threads WHERE id=? AND user_id=?",
                      (pk, user_id)) if pk is not None else None
    if row is None:
        raise TerminalError("chat not found", 404)
    if (row["kind"] or "chat") != "project" or row["host_id"] is None:
        return int(pk), None, None, ""
    host_id = int(row["host_id"])
    project_id = int(row["project_id"]) if row["project_id"] is not None else None
    cwd = ""
    if project_id is not None:
        cwd = (hosts.resolve_project_id(user_id, project_id) or {}).get("remote_path") or ""
    if not cwd:
        host = hosts.host_row(user_id, host_id)
        cwd = (host["default_cwd"] if host is not None else "") or ""
    return int(pk), host_id, project_id, cwd


def _prepare(user_id: int, host_id, thread_id, shell) -> tuple:
    thread_pk = project_id = None
    cwd = ""
    if thread_id is not None:
        thread_pk, bound_host, project_id, cwd = _binding(user_id, thread_id)
        if bound_host is not None:
            host_id = bound_host
    if isinstance(host_id, bool) or not isinstance(host_id, int):
        raise TerminalError("host_id is required", 400)
    row = hosts.host_row(user_id, host_id)
    if row is None or row["disabled"]:
        raise TerminalError("host not found", 404)
    if _on_this_server(row):
        raise TerminalError("a terminal never runs on the Fuse server itself", 403)
    os_name = _os(row)
    shells = [item["id"] for item in _shells(user_id, row, os_name)]
    shell = shell or shells[0]
    if shell not in shells:
        raise TerminalError("unknown shell for this machine", 400)
    return row, os_name, shell, cwd, thread_pk, project_id


async def open_session(user_id: int, login: str, *, host_id=None, thread_id=None, shell=None, cols=80, rows=24,
                       client_ip: str = "", user_agent: str = "") -> Session:
    """POST /terminal/sessions. Raises TerminalError with the HTTP status."""
    uid = int(user_id)
    row, os_name, shell, cwd, thread_pk, project_id = await asyncio.to_thread(_prepare, uid, host_id, thread_id, shell)
    transport = "agent" if row["auth_type"] == "agent" else "ssh"
    if transport == "agent":
        if not agent_hub.is_online(uid, int(row["id"])):
            raise TerminalError("machine is offline", 409)
        if not agent_hub.has_cap(uid, int(row["id"]), "pty"):
            raise TerminalError(agent_hub.PTY_UPDATE, 409)
    live = [s for s in _sessions.values() if s.uid == uid and s.alive]
    if len(live) >= MAX_PER_USER:
        raise TerminalError(f"terminal limit reached ({MAX_PER_USER} open terminals)", 429)
    if sum(1 for s in live if s.host_id == int(row["id"])) >= MAX_PER_HOST:
        raise TerminalError(f"terminal limit reached ({MAX_PER_HOST} open terminals on this machine)", 429)
    s = Session(uid=uid, login=login, row=row, transport=transport, os_name=os_name, shell=shell, cwd=cwd,
                thread_id=thread_pk, project_id=project_id, client_ip=client_ip, user_agent=user_agent)
    s.cols, s.rows = _clamp(cols, 2, 1000, 80), _clamp(rows, 1, 500, 24)
    s.backend = _AgentPty(s) if transport == "agent" else _SshPty(s)
    _sessions[s.id] = s  # holds its slot against concurrent opens
    await asyncio.to_thread(_audit_open, s)
    try:
        await s.backend.open()
    except BaseException as exc:
        s.alive = False
        s.reason = "error"
        _sessions.pop(s.id, None)
        _spawn(_record_close(s))
        if isinstance(exc, HostError):
            raise TerminalError(exc.detail, exc.status_code) from exc
        if isinstance(exc, Exception):
            logger.exception("terminal open failed")
            raise TerminalError("could not open a terminal", 502) from exc
        raise
    if not s.alive:  # closed (teardown) while it was opening
        await s.backend.close()
        raise TerminalError("terminal closed", 409)
    if s.client is None:
        s._arm_detach()
    return s


def get(session_id) -> Session | None:
    return _sessions.get(session_id) if isinstance(session_id, str) else None


def list_for(user_id: int) -> list[dict]:
    return [s.listing() for s in _sessions.values() if s.uid == int(user_id)]


async def close_where(user_id: int, reason: str, *, session_id: str | None = None, login: str | None = None,
                      host_id: int | None = None, transport: str | None = None) -> int:
    """Close a user's live sessions matching every filter given. Teardown:
    logout (login), host disabled or removed (host_id), vault locked
    (transport="ssh"), password change (all)."""
    found = [s for s in list(_sessions.values())
             if s.uid == int(user_id) and s.alive
             and (session_id is None or s.id == session_id)
             and (login is None or s.login == login)
             and (host_id is None or s.host_id == int(host_id))
             and (transport is None or s.transport == transport)]
    await asyncio.gather(*(s.close(reason) for s in found), return_exceptions=True)
    return len(found)


async def drain() -> None:
    """Shutdown: every shell ends with the process that streams it."""
    await asyncio.gather(*(s.close("shutdown") for s in list(_sessions.values()) if s.alive),
                         return_exceptions=True)
    if _background:
        await asyncio.wait(list(_background), timeout=5)


# -- audit (metadata only: never input or output) -------------------------------

def _audit_open(s: Session) -> None:
    try:
        db.execute(
            """INSERT INTO terminal_sessions (id, user_id, host_id, alias, transport, remote_user, root, thread_id,
               project_id, cwd, shell, opened_at, client_ip, user_agent) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (s.id, s.uid, s.host_id, s.alias, s.transport, s.user, int(s.root), s.thread_id, s.project_id,
             s.cwd, s.shell, s.created, s.client_ip, s.user_agent),
        )
    except Exception:
        logger.exception("terminal audit insert failed")


def _audit_close(s: Session) -> None:
    try:
        db.execute(
            """UPDATE terminal_sessions SET closed_at=?, close_reason=?, exit_code=?, bytes_in=?, bytes_out=?
               WHERE id=? AND closed_at IS NULL""",
            (db.now(), s.reason[:32], s.exit_code, s.bytes_in, s.bytes_out, s.id),
        )
    except Exception:
        logger.exception("terminal audit close failed")


async def _record_close(s: Session) -> None:
    await asyncio.to_thread(_audit_close, s)


def recover() -> None:
    """Startup: sessions the last process left open ended with it."""
    try:
        db.execute("UPDATE terminal_sessions SET closed_at=?, close_reason='restart' WHERE closed_at IS NULL",
                   (db.now(),))
    except Exception:
        logger.exception("terminal audit recovery failed")
