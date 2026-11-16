"""Subscriptions area helpers: pool accounts, a routed httpx, SSE reading.

Every backend opens its own httpx.AsyncClient; `Http` swaps the class for one
whose transport is an httpx.MockTransport, so the backend's real request code
runs and the test sees each request it sent. Nothing reaches the network.
"""
import asyncio
import base64
import json
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import httpx

from app import accounts, db
from app.sources import pool_policy

_RealClient = httpx.AsyncClient


def user(email="user@core.test"):
    """A bare users row; subscriptions never read its password or vault."""
    with db.tx() as conn:
        conn.execute("INSERT INTO users (email, password_hash, is_admin, created_at) VALUES (?,'unused',0,?)",
                     (email, db.now()))
        return int(db.last_id(conn))


def account(kind, owner, extra=None, secret="secret-token"):
    """A donated credential in this pool (the pool source is created on demand)."""
    return int(accounts.add_credential(pool_policy.source_id(kind), kind, secret, extra=extra or {}, donated_by=owner))


def row(cred_id):
    return db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))


def extra_of(cred_id):
    return accounts.cred_extra(row(cred_id))


def set_extra(cred_id, extra):
    """Overwrite a credential's extra as a token refresh would (update_extra
    deliberately never rolls token fields)."""
    from app.crypto import encrypt

    db.execute("UPDATE credentials SET extra=? WHERE id=?", (encrypt(json.dumps(extra)), cred_id))


def iso(seconds_from_now):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds_from_now)).strftime("%Y-%m-%dT%H:%M:%SZ")


def fresh_billing(**fields):
    """A billing block read just now, so the chat path does not refetch it."""
    now = db.now()
    return {"fetched_at": now, "checked_at": now, "windows": [], **fields}


def jwt(claims):
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'exp': int(time.time()) + 86400, **claims})}.sig"


def run(coro):
    return asyncio.run(coro)


class Http:
    """Route every httpx.AsyncClient through handler(request) -> Response.

    `requests` keeps each request the code sent (body already read). A
    handler may also be a list of responses, answered in order.
    """

    def __init__(self, handler):
        self.requests: list[httpx.Request] = []
        if isinstance(handler, list):
            queue = list(handler)
            handler = lambda request: queue.pop(0)  # noqa: E731
        self.handler = handler

    def _handle(self, request):
        self.requests.append(request)
        return self.handler(request)

    def client_class(self):
        transport = httpx.MockTransport(self._handle)

        class Routed(_RealClient):
            def __init__(self, *args, **kwargs):
                kwargs.pop("proxy", None)
                kwargs.pop("mounts", None)
                kwargs["transport"] = transport
                super().__init__(*args, **kwargs)

        return Routed

    def json_bodies(self, path_part=""):
        return [json.loads(r.content) for r in self.requests if path_part in r.url.path and r.content]

    def form_bodies(self, path_part=""):
        from urllib.parse import parse_qs

        return [{k: v[0] for k, v in parse_qs(r.content.decode()).items()}
                for r in self.requests if path_part in r.url.path and r.content]


@contextmanager
def http(handler, monkeypatch):
    """Patch httpx.AsyncClient for the block; yields the Http recorder."""
    routed = Http(handler)
    monkeypatch.setattr(httpx, "AsyncClient", routed.client_class())
    try:
        yield routed
    finally:
        monkeypatch.setattr(httpx, "AsyncClient", _RealClient)


def sse(chunks):
    """An upstream SSE body streamed in the given byte chunks."""
    async def body():
        for chunk in chunks:
            yield chunk if isinstance(chunk, bytes) else chunk.encode()

    return body()


def events(objs):
    """`data: {...}` lines, one SSE event each."""
    return "".join(f"data: {json.dumps(o)}\n\n" for o in objs)


async def drain(result):
    """Every OpenAI-style frame a ChatResult stream yields, decoded."""
    raw = b"".join([chunk async for chunk in result.stream])
    frames = []
    for line in raw.decode().splitlines():
        if line.startswith("data: ") and line[6:].strip() != "[DONE]":
            frames.append(json.loads(line[6:]))
    return frames


def text_of(frames):
    return "".join((f.get("choices") or [{}])[0].get("delta", {}).get("content") or "" for f in frames if f.get("choices"))


def reasoning_of(frames):
    return "".join((f.get("choices") or [{}])[0].get("delta", {}).get("reasoning_content") or ""
                   for f in frames if f.get("choices"))


def finish_of(frames):
    return [c["finish_reason"] for f in frames for c in f.get("choices") or [] if c.get("finish_reason")]


def tool_calls_of(frames):
    """Assemble streamed tool-call deltas into [{id, name, arguments}]."""
    calls: dict[int, dict] = {}
    for f in frames:
        for choice in f.get("choices") or []:
            for tc in choice.get("delta", {}).get("tool_calls") or []:
                rec = calls.setdefault(tc["index"], {"id": "", "name": "", "arguments": ""})
                rec["id"] = tc.get("id") or rec["id"]
                fn = tc.get("function") or {}
                rec["name"] = fn.get("name") or rec["name"]
                rec["arguments"] += fn.get("arguments") or ""
    return [calls[i] for i in sorted(calls)]


def usage_of(frames):
    return next((f["usage"] for f in reversed(frames) if f.get("usage")), None)
