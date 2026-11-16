"""Threads-area helpers: a faked tool executor, image data URLs, and waiting
for the run supervisor to go idle.

The tool executor is faked at ``app.chat_runtime.execute_tool``; tool rounds,
traces, transcript saves and finalization around it stay real.
"""
import asyncio
import base64
import json
from contextlib import contextmanager
from unittest.mock import patch

from app import chats, db


class FakeTools:
    def __init__(self, answer=None):
        self.answer = answer or (lambda name, args: f"result of {name}")
        self.calls = []

    async def __call__(self, name, arguments, **kwargs):
        args = json.loads(arguments or "{}")
        self.calls.append((name, args))
        return self.answer(name, args), {"name": name, "status": "done"}


@contextmanager
def fake_tools(answer=None):
    tools = FakeTools(answer)
    with patch("app.chat_runtime.execute_tool", new=tools):
        yield tools


def image_url(seed: int = 0, size: int = 12_000) -> str:
    """A PNG data URL above chat_images.MIN_CHARS, distinct per seed."""
    raw = bytes([seed % 256]) * size + seed.to_bytes(4, "big")
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def image_attachment(seed: int = 0) -> dict:
    return {"name": f"shot{seed}.png", "mime": "image/png", "kind": "image", "data": image_url(seed)}


def pk(uid, thread_id) -> int:
    return chats.resolve_thread_pk(uid, thread_id)


def events(uid, after=0):
    from app import chat_store

    return chat_store.events_after(uid, after)


async def settle(timeout=10.0):
    """Wait until no run worker or wake is in flight."""
    from app import chat_runs

    async def idle():
        while True:
            busy = [t for t in (*chat_runs._wake_tasks.values(), *chat_runs._tasks.values()) if not t.done()]
            if not busy:
                return
            await asyncio.wait(busy)

    await asyncio.wait_for(idle(), timeout)


def runs_of(uid, thread_id, columns="id,status,parent_run_id,message_id"):
    return db.fetchall(f"SELECT {columns} FROM chat_runs WHERE user_id=? AND thread_id=? ORDER BY created_at,id",
                       (uid, pk(uid, thread_id)))
