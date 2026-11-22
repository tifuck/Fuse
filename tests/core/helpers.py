"""Shared helpers for core tests: condition waits, users, and a fake provider.

The provider boundary is ``app.api.invoke_chat``: faking it there keeps the
real generation loop, tool rounds, persistence and run supervisor under test.
"""
import asyncio
import json
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def wait_until(condition, timeout=5.0, interval=0.01, message="condition never held"):
    """Poll condition() until it returns a truthy value; return that value."""
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(message)
        time.sleep(interval)


async def wait_until_async(condition, timeout=5.0, interval=0.01, message="condition never held"):
    """wait_until for event-loop code: yields to the loop between checks."""
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(message)
        await asyncio.sleep(interval)


def run_async(coro):
    """Run an async test body on a fresh loop, then stop the run supervisor
    and drain whatever background work it left (wakes, sweeps, threads)."""
    from app import chat_runs

    async def main():
        try:
            return await coro
        finally:
            await chat_runs.stop()
            chat_runs._stopping = False
            current = asyncio.current_task()
            pending = [task for task in asyncio.all_tasks() if task is not current]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.get_running_loop().shutdown_default_executor()

    return asyncio.run(main())


def make_user(email="user@core.test", password="password12", admin=False):
    """A real account (password hash, vault keys, API key); returns its id.
    Costs ~80 ms of scrypt: use quick_user unless the test signs in or uses the vault."""
    from app import accounts

    return int(accounts.create_user(email, password, admin=admin, require_invite=False)["id"])


def quick_user(email="user@core.test", admin=False):
    """A bare users row (no password, vault or API key); returns its id."""
    from app import db

    with db.tx() as conn:
        conn.execute("INSERT INTO users (email, password_hash, is_admin, created_at) VALUES (?,'unused',?,?)",
                     (email, 1 if admin else 0, db.now()))
        return int(db.last_id(conn))


# -- fake provider --------------------------------------------------------

def frame(obj):
    """One SSE data frame, as a provider stream sends it."""
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode()


def text_frames(text, usage=None):
    """A complete streamed assistant reply: content, finish, usage, [DONE]."""
    return [
        frame({"choices": [{"index": 0, "delta": {"content": text}}]}),
        frame({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        frame({"choices": [], "usage": usage or {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}),
        b"data: [DONE]\n\n",
    ]


def tool_call_frames(calls):
    """A streamed turn that ends in tool calls: calls = [(id, name, args_dict)]."""
    deltas = [{"index": i, "id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
              for i, (cid, name, args) in enumerate(calls)]
    return [
        frame({"choices": [{"index": 0, "delta": {"tool_calls": deltas}}]}),
        frame({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        b"data: [DONE]\n\n",
    ]


def stream_result(frames, status=200):
    """What invoke_chat returns for a streamed upstream answer."""
    async def stream():
        for item in frames:
            yield item

    return SimpleNamespace(status=status, body={}, stream=stream(), usage={}, credential_id=None, attempts=[])


class FakeProvider:
    """Scripted stand-in for app.api.invoke_chat.

    Each call pops the next script entry: a list of frames (streamed answer),
    or a callable(body) returning frames, or an Exception to raise. Every
    request body the loop sent is kept in ``bodies`` for assertions.
    """

    def __init__(self, *script):
        self.script = list(script)
        self.bodies = []

    async def __call__(self, request, user, body, **_kwargs):
        self.bodies.append({**body, "messages": [dict(m) for m in (body.get("messages") or [])]})
        if not self.script:
            raise AssertionError("the provider was called more often than scripted")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        frames = step(body) if callable(step) else step
        return user, SimpleNamespace(source_id=1, type_id="openai"), "m", "m", stream_result(frames)


@contextmanager
def fake_provider(*script):
    """Patch the provider boundary for the block; yields the FakeProvider.

    Usage recording is stubbed (it needs real source/credential rows; the
    mock is kept as ``provider.record_usage``) and title generation returns
    nothing, so a test sees only its own turn."""
    from app import api, chat_runtime

    provider = FakeProvider(*script)
    with patch.object(api, "invoke_chat", new=provider), \
            patch.object(api, "_safe_record_usage") as record_usage, \
            patch.object(chat_runtime, "generate_thread_title", new_callable=AsyncMock, return_value=""):
        provider.record_usage = record_usage
        yield provider


async def wait_run(task, timeout=10.0):
    """Wait for a chat run task and return its result.

    Not asyncio.wait_for: on timeout it cancels the run, which swallows the
    cancel and settles "interrupted", so the test failed later on a
    misleading status. Here the timeout itself is the failure."""
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
        task.cancel()
        raise TimeoutError(f"run did not settle within {timeout}s")
    return task.result()


async def run_turn(user_id, text, *, thread=None, model="offline", timeout=30.0):
    """Start a real durable root turn and wait for it to settle.

    Returns (thread_public_id, run_row). Call inside ``fake_provider``."""
    from app import chat_runs, chat_store, chats

    if thread is None:
        thread = chats.create_thread(user_id, model=model, tools=[])
    run = chat_store.start_turn(user_id, thread["id"], text, thread["revision"], None)["run"]
    task = await chat_runs.start(chat_runs.internal_request(user_id), {"id": user_id}, run)
    await wait_run(task, timeout)
    return thread["id"], chat_store.get_run(user_id, run["id"])
