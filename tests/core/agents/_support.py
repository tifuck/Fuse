"""Agents area helpers: coding users, a provider routed per run, hub fakes.

Subagent tests run real parent and child runs at once. A single FIFO script
(tests.core.helpers.fake_provider) cannot say which run asks next, so
``routed_provider`` answers each request from the script of the run it
belongs to, keyed by that run's first user message (a child's prompt, the
root's first user turn; a wake turn on the same thread keeps the root's).
"""
import asyncio
import inspect
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.core.helpers import quick_user, stream_result


def coding_user(email="agents@core.test", nested=False):
    from app import hosts

    uid = quick_user(email)
    hosts.set_settings(uid, agent_coding=True, allow_nested_spawn=nested)
    return uid


def first_user_text(body) -> str:
    for message in body.get("messages") or []:
        if message.get("role") == "user":
            content = message.get("content")
            return content if isinstance(content, str) else json.dumps(content)
    return ""


def tool_names(body) -> set[str]:
    return {item["function"]["name"] for item in body.get("tools") or []}


def tool_result(body, call_id) -> str:
    return next(m["content"] for m in body["messages"] if m.get("role") == "tool" and m.get("tool_call_id") == call_id)


class RoutedProvider:
    """Stand-in for app.api.invoke_chat with one script per run.

    routes: {key: [step, ...]}; a request goes to the first key found in its
    first user message. A step is a frames list, a callable(body) returning
    frames (sync or async), or an Exception to raise."""

    def __init__(self, routes):
        self.routes = {key: list(steps) for key, steps in routes.items()}
        self.bodies = {key: [] for key in routes}

    def _key(self, body):
        text = first_user_text(body)
        for key in self.routes:
            if key in text:
                return key
        raise AssertionError(f"no route for a request starting {text[:80]!r}")

    async def __call__(self, request, user, body, **_kwargs):
        key = self._key(body)
        self.bodies[key].append({**body, "messages": [dict(m) for m in (body.get("messages") or [])]})
        if not self.routes[key]:
            raise AssertionError(f"route {key!r} was called more often than scripted")
        step = self.routes[key].pop(0)
        if isinstance(step, Exception):
            raise step
        frames = step(body) if callable(step) else step
        if inspect.isawaitable(frames):
            frames = await frames
        return user, SimpleNamespace(source_id=1, type_id="openai"), "m", "m", stream_result(frames)


@contextmanager
def routed_provider(**routes):
    from app import api, chat_runtime

    provider = RoutedProvider(routes)
    with patch.object(api, "invoke_chat", new=provider), \
            patch.object(api, "_safe_record_usage") as record_usage, \
            patch.object(chat_runtime, "generate_thread_title", new_callable=AsyncMock, return_value=""):
        provider.record_usage = record_usage
        yield provider


async def start_root(user_id, text, *, thread=None):
    """Start a durable root turn without waiting; returns (thread, run, task)."""
    from app import chat_runs, chat_store, chats

    if thread is None:
        thread = chats.create_thread(user_id, model="offline", tools=[])
    run = chat_store.start_turn(user_id, thread["id"], text, thread["revision"], None)["run"]
    task = await chat_runs.start(chat_runs.internal_request(user_id), {"id": user_id}, run)
    return thread, run, task


def root_runs(user_id, thread_pk):
    from app import db

    return db.fetchall(
        "SELECT id,status FROM chat_runs WHERE user_id=? AND thread_id=? AND parent_run_id IS NULL ORDER BY created_at, id",
        (user_id, thread_pk),
    )


def children(user_id, thread_pk):
    from app import db

    return db.fetchall(
        "SELECT id,status FROM chat_runs WHERE user_id=? AND thread_id=? AND parent_run_id IS NOT NULL",
        (user_id, thread_pk),
    )


def agent_of(user_id, run_id) -> dict:
    from app import chat_store

    return chat_store.get_run(user_id, run_id)["request"]["agent"]


def parent_and_thread(user_id, text="Hello"):
    """A queued root turn to hang children off (no worker)."""
    from app import chat_store, chats

    thread = chats.create_thread(user_id, model="offline")
    run = chat_store.start_turn(user_id, thread["id"], text, thread["revision"], None)["run"]
    return thread, run


def child(user_id, thread, parent, **kw):
    from app import chat_store

    kw.setdefault("prompt", "inspect")
    kw.setdefault("description", "worker")
    return chat_store.start_child_run(user_id=user_id, thread_id=thread["id"], parent_run_id=parent["id"], **kw)


def set_running(run_id):
    from app import db

    db.execute("UPDATE chat_runs SET status='running' WHERE id=?", (run_id,))


def bind_parent(user_id, run, host_id, cwd):
    """Bind a parent run to a host and folder, as a project session does."""
    from app import chat_store, db

    req = chat_store.get_run(user_id, run["id"])["request"]
    req.setdefault("agent", {}).update(bound_host_id=host_id, bound_cwd=cwd)
    db.execute("UPDATE chat_runs SET request=? WHERE id=?", (chat_store.dumps(req), run["id"]))


# -- connector hub ---------------------------------------------------------

def agent_host(user_id, hostname="box", **meta):
    """A paired connector machine; returns (host view, raw device token)."""
    from app import hosts

    pairing = hosts.mint_pairing(user_id)
    return hosts.redeem_pairing(pairing["code"], {"hostname": hostname, "username": "u", **meta})


class ConnectorWS:
    """A connector socket for agent_hub.handle_socket: frames the test puts in
    ``inbox`` are received; everything the hub sends is kept in ``sent``.
    ``answer`` (op -> result dict) makes it reply to jobs on its own."""

    def __init__(self, hello, answer=None):
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.binary: list[bytes] = []
        self.closed: list = []
        self.answer = answer
        if hello is not None:
            self.inbox.put_nowait(hello if isinstance(hello, str) else json.dumps(hello))

    async def receive_text(self):
        item = await self.inbox.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def receive(self):
        """The hub's loop after hello: str items are text frames, bytes items
        binary ones."""
        item = await self.receive_text()
        if isinstance(item, bytes):
            return {"type": "websocket.receive", "bytes": item}
        return {"type": "websocket.receive", "text": item}

    async def send_bytes(self, data):
        self.binary.append(bytes(data))

    async def send_json(self, obj):
        self.sent.append(obj)
        if self.answer is not None and obj.get("t") == "job" and obj.get("op") in self.answer:
            self.reply(obj["id"], **self.answer[obj["op"]])

    async def close(self, code=None):
        self.closed.append(code)

    def reply(self, job_id, **fields):
        self.inbox.put_nowait(json.dumps({"v": 1, "t": "res", "id": job_id, **fields}))

    def drop(self):
        from starlette.websockets import WebSocketDisconnect

        self.inbox.put_nowait(WebSocketDisconnect(1006))

    def jobs(self):
        return [m for m in self.sent if m.get("t") == "job"]

    def cancels(self):
        return [m["id"] for m in self.sent if m.get("t") == "cancel"]
