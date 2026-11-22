"""Runs-area helpers: a patched tool executor, runs started without waiting,
and the provider-retry backoff turned off.

The tool executor is faked at ``app.chat_runtime.execute_tool`` (the seam the
runtime resolves at call time); everything around it (tool rounds, traces,
transcript checkpoints, the run supervisor) is real.
"""
import asyncio
import json
from contextlib import contextmanager
from unittest.mock import patch

from tests.core.helpers import frame


class FakeTools:
    """Stand-in for chat_tools.execute_tool. ``answer(name, args)`` returns the
    result text (or raises); every call is kept in ``calls``. ``gate`` (an
    asyncio.Event created on the running loop by ``hold()``) makes calls wait
    until it is set, so a test can act while a tool is running."""

    def __init__(self, answer=None):
        self.answer = answer or (lambda name, args: f"result of {name}")
        self.calls = []
        self.started = None
        self.gate = None

    def hold(self):
        self.started = asyncio.Event()
        self.gate = asyncio.Event()

    async def __call__(self, name, arguments, **kwargs):
        args = json.loads(arguments or "{}")
        self.calls.append((name, args))
        if self.started is not None:
            self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        text = self.answer(name, args)
        return text, {"name": name, "status": "done"}


@contextmanager
def fake_tools(answer=None):
    tools = FakeTools(answer)
    with patch("app.chat_runtime.execute_tool", new=tools):
        yield tools


class HeldStream:
    """Provider stand-in whose stream sends ``text`` and then stays open
    until ``release`` is set (created by ``arm()`` on the running loop)."""

    def __init__(self, text):
        self.text = text
        self.sent = None
        self.release = None

    def arm(self):
        self.sent, self.release = asyncio.Event(), asyncio.Event()

    async def __call__(self, request, user, body, **_kwargs):
        from types import SimpleNamespace

        async def stream():
            yield frame({"choices": [{"index": 0, "delta": {"content": self.text}}]})
            self.sent.set()
            await self.release.wait()
            yield b"data: [DONE]\n\n"

        result = SimpleNamespace(status=200, body={}, stream=stream(), usage={}, credential_id=None, attempts=[])
        return user, SimpleNamespace(source_id=1, type_id="openai"), "m", "m", result


@contextmanager
def held_stream(text):
    from unittest.mock import AsyncMock

    held = HeldStream(text)
    with patch("app.api.invoke_chat", new=held), patch("app.api._safe_record_usage") as record_usage, \
            patch("app.chat_runtime.generate_thread_title", new_callable=AsyncMock, return_value=""):
        held.record_usage = record_usage
        yield held


class StatusProvider:
    """Wraps a FakeProvider: a script step ``(status, body)`` is an upstream
    non-200 answer with no stream; anything else goes to the wrapped one."""

    def __init__(self, provider):
        self.provider = provider

    async def __call__(self, request, user, body, **kwargs):
        from types import SimpleNamespace

        script = self.provider.script
        if script and isinstance(script[0], tuple):
            self.provider.bodies.append({**body, "messages": [dict(m) for m in (body.get("messages") or [])]})
            status, payload = script.pop(0)
            result = SimpleNamespace(status=status, body=payload, stream=None, usage={}, credential_id=None, attempts=[])
            return user, SimpleNamespace(source_id=1, type_id="openai"), "m", "m", result
        return await self.provider(request, user, body, **kwargs)


@contextmanager
def status_provider(*script):
    """fake_provider whose script may also hold ``(status, body)`` answers."""
    from tests.core.helpers import fake_provider

    with fake_provider(*script) as provider, patch("app.api.invoke_chat", new=StatusProvider(provider)):
        yield provider


@contextmanager
def no_backoff():
    """Provider-failure resumes retry at once instead of after 2-40 s."""
    with patch("app.chat_runtime._RESUME_BACKOFF", (0.0, 0.0, 0.0, 0.0)):
        yield


def tool_thread(uid):
    """A plain chat with web_search on, so the request carries tool schemas."""
    from app import chats

    return chats.create_thread(uid, model="offline", tools=["web_search"])


async def start_run(uid, text, thread=None, tools=None):
    """Start a real durable root turn without waiting: (thread, run, task)."""
    from app import chat_runs, chat_store, chats

    if thread is None:
        thread = chats.create_thread(uid, model="offline", tools=tools or [])
    run = chat_store.start_turn(uid, thread["id"], text, thread["revision"], None)["run"]
    task = await chat_runs.start(chat_runs.internal_request(uid), {"id": uid}, run)
    return thread, run, task


async def settle(timeout=10.0):
    """Wait until the supervisor is idle: no wake in flight, no live worker
    (a wake may start a run, a finishing run may schedule a wake)."""
    from app import chat_runs

    async def idle():
        while True:
            # A wake scheduled from a worker thread (call_soon_threadsafe)
            # registers only on the loop's next pass.
            await asyncio.sleep(0)
            busy = [t for t in (*chat_runs._wake_tasks.values(), *chat_runs._tasks.values()) if not t.done()]
            if not busy:
                return
            await asyncio.wait(busy)

    await asyncio.wait_for(idle(), timeout)


def thread_pk(uid, thread_id):
    """The numeric thread id run rows and wakes use (threads expose a public id)."""
    from app import chats

    return chats.resolve_thread_pk(uid, thread_id)


def root_runs(uid, thread_id):
    from app import db

    return db.fetchall(
        "SELECT id,status FROM chat_runs WHERE user_id=? AND thread_id=? AND parent_run_id IS NULL ORDER BY created_at,id",
        (uid, thread_pk(uid, thread_id)),
    )


def split_frames(*deltas, finish="stop"):
    """A streamed reply made of the given delta dicts, one frame each."""
    out = [frame({"choices": [{"index": 0, "delta": d}]}) for d in deltas]
    out.append(frame({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}))
    out.append(b"data: [DONE]\n\n")
    return out


def run_row(run_id, columns="status,cancel_requested,resume_count"):
    from app import db

    return db.fetchone(f"SELECT {columns} FROM chat_runs WHERE id=?", (run_id,))


def provider_messages(run_id):
    row = run_row(run_id, "provider_messages")
    return json.loads(row["provider_messages"]) if row and row["provider_messages"] else None
