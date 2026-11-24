"""Fakes for terminal tests. Nothing here runs a shell or opens a PTY.

- FakePty: what app.ssh_pool.open_pty returns. The test emits output into
  it; input, resizes and the close are recorded.
- PtyConnector: a connector socket (agents._support.ConnectorWS) that
  advertises "pty" and answers pty_open with pty_ok, as connector 1.12 does.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app import db
from tests.core.agents._support import ConnectorWS


def audit(session_id):
    """The session's terminal_sessions row."""
    row = db.fetchone("SELECT * FROM terminal_sessions WHERE id=?", (session_id,))
    return dict(row) if row else None


class FakePty:
    def __init__(self, cols, rows, command):
        self.size, self.command = (cols, rows), command
        self.input = bytearray()
        self.sizes: list[tuple[int, int]] = []
        self.closed = False
        self.exit_status = None
        self.queue: asyncio.Queue = asyncio.Queue()
        self.stdout = SimpleNamespace(read=self._read)
        self.stdin = SimpleNamespace(write=self.input.extend, drain=self._drain)

    async def _read(self, _n):
        return await self.queue.get()

    async def _drain(self):
        return None

    def emit(self, data: bytes) -> None:
        self.queue.put_nowait(data)

    def exit(self, code: int) -> None:
        self.exit_status = code
        self.queue.put_nowait(b"")

    def change_terminal_size(self, cols, rows):
        self.sizes.append((cols, rows))

    def close(self):
        self.closed = True
        self.queue.put_nowait(b"")

    async def wait_closed(self):
        return None


def hello(token, caps=("pty",), **extra):
    return {"v": 1, "t": "hello", "token": token, "ver": "1.12.0", "hostname": "box", "caps": list(caps), **extra}


class PtyConnector(ConnectorWS):
    def __init__(self, token, caps=("pty",), **extra):
        extra.setdefault("ptys", [])
        super().__init__(hello(token, caps, **extra))

    async def send_json(self, obj):
        await super().send_json(obj)
        if obj.get("t") == "job" and obj.get("op") == "pty_open":
            self.inbox.put_nowait(json.dumps({"v": 1, "t": "pty_ok", "id": obj["id"], "shell": "/bin/bash", "pid": 4242}))

    def output(self, job_id: str, data: bytes) -> None:
        self.inbox.put_nowait(bytes([1]) + job_id.encode() + data)

    def opens(self):
        return [m for m in self.jobs() if m["op"] == "pty_open"]

    def acks(self):
        return [m["n"] for m in self.sent if m.get("t") == "pty_ack"]
