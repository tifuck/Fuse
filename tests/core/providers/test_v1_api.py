"""The public OpenAI-compatible API: hashed API keys authenticate, /v1/models
lists what the key's owner may use, and every completion (streamed, plain or
failed) is recorded against the key that made it, with OpenAI-shaped errors.
"""
import asyncio
import hashlib
import json
import time

import httpx
import pytest
from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.testclient import TestClient

from app import accounts, api, db
from app.config import settings
from app.proxy import UsageStream
from app.sources import pool_pick, refuseless
from app.sources.base import ChatResult
from tests.core.helpers import quick_user

from ._support import completion, delta, events, frames_of, key, route, source, sse, sse_response

USAGE = {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24}


@pytest.fixture
def v1(fresh_db, upstream):
    app = FastAPI()
    app.include_router(api.router)
    app.add_exception_handler(StarletteHTTPException, api.http_errors)
    uid = quick_user()
    raw = accounts.create_api_key(uid, "Laptop")
    sid = source("openai", {"base_url": "https://up.test"})
    key(sid, "sk-upstream-key")
    route("m1", sid, "up-1")
    upstream.on("GET", "/v1/models", httpx.Response(200, json={"data": [{"id": "up-1"}]}))
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {raw}"
    return client, uid, raw


def _key_row(uid):
    return db.fetchone("SELECT * FROM api_keys WHERE user_id=?", (uid,))


def test_only_a_known_bearer_key_authenticates_and_only_its_hash_is_stored(v1):
    client, uid, raw = v1
    anon = TestClient(client.app)

    for headers, detail in (({}, "Missing API key"), ({"Authorization": f"Basic {raw}"}, "Missing API key"),
                            ({"Authorization": "Bearer sk-not-a-key"}, "Invalid API key")):
        got = anon.get("/v1/models", headers=headers)
        assert got.status_code == 401
        assert got.json()["error"] == {"message": detail, "type": "invalid_request_error", "code": 401}

    assert client.get("/v1/models").status_code == 200
    row = _key_row(uid)
    assert row["key_hash"] == hashlib.sha256(raw.encode()).hexdigest() and row["last_used_at"]
    assert raw not in json.dumps({k: str(v) for k, v in row.items()})

    accounts.delete_api_key(uid, row["id"])
    assert client.get("/v1/models").status_code == 401


def test_models_lists_what_the_keys_owner_may_use(v1):
    client, uid, _raw = v1
    rsid = refuseless.ensure_refuseless_source()
    refuseless._catalog = (time.time(), refuseless.parse_catalog({"data": [{"id": "abl", "input_modalities": ["text"]}]}))

    ids = [m["id"] for m in client.get("/v1/models").json()["data"]]
    assert ids == ["up-1"]

    key(rsid, "rl-own-key-1", owner=uid)
    import app.sources as sources
    sources.invalidate_model_cache()
    data = {m["id"]: m for m in client.get("/v1/models").json()["data"]}
    assert set(data) == {"up-1", "refuseless/abl"}
    assert data["refuseless/abl"]["input_modalities"] == ["text"]


def test_a_streamed_completion_is_relayed_and_recorded_against_its_key(v1, upstream):
    client, uid, _raw = v1
    upstream.on("POST", "/chat/completions", sse_response(delta("hel"), delta("lo"), {"choices": [], "usage": USAGE}))

    got = client.post("/v1/chat/completions", json={"model": "m1", "stream": True,
                                                     "messages": [{"role": "user", "content": "hi"}]})

    assert got.status_code == 200 and got.headers["content-type"].startswith("text/event-stream")
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in frames_of(got.content) if c["choices"])
    assert text == "hello" and got.content.endswith(b"data: [DONE]\n\n")
    assert upstream.sent("/chat/completions")[0]["stream_options"] == {"include_usage": True}
    (row,) = events()
    keyrow = _key_row(uid)
    assert (row["api_key_id"], row["user_id"], row["model"], row["requested_model"]) == (keyrow["id"], uid, "up-1", "m1")
    assert (row["prompt_tokens"], row["usage_status"], row["request_status"]) == (20, "complete", "success")
    assert (keyrow["requests"], keyrow["prompt_tokens"], keyrow["completion_tokens"]) == (1, 20, 4)


def test_a_plain_completion_is_relayed_and_recorded(v1, upstream):
    client, uid, _raw = v1
    upstream.on("POST", "/chat/completions", httpx.Response(200, json=completion("hey", usage=USAGE)))

    got = client.post("/v1/chat/completions", json={"model": "m1", "messages": [{"role": "user", "content": "hi"}]})

    assert got.json()["choices"][0]["message"]["content"] == "hey"
    (row,) = events()
    assert (row["total_tokens"], row["request_status"], row["api_key_id"]) == (24, "success", _key_row(uid)["id"])


def test_an_upstream_failure_is_relayed_with_its_kind_and_counted_as_failed(v1, upstream):
    client, uid, _raw = v1
    upstream.on("POST", "/chat/completions",
                httpx.Response(429, json={"error": {"message": "Rate limit reached. Try again in 9s."}}))

    got = client.post("/v1/chat/completions", json={"model": "m1", "messages": [{"role": "user", "content": "hi"}]})

    assert got.status_code == 429 and got.headers["retry-after"] == "9"
    assert got.json()["error"]["type"] == "rate_limit"
    (row,) = events()
    assert (row["request_status"], row["count_request"], row["api_key_id"]) == ("failed", 1, _key_row(uid)["id"])


def test_bad_bodies_are_client_errors_and_never_reach_an_upstream(v1, upstream):
    client, _uid, _raw = v1
    for content in (b"{not json", b'{"model": "m1", "temperature": NaN}', b"[1, 2]"):
        got = client.post("/v1/chat/completions", content=content, headers={"content-type": "application/json"})
        assert got.status_code == 400 and got.json()["error"]["message"]
    assert client.post("/v1/chat/completions", json={"model": "nope", "messages": [
        {"role": "user", "content": "x"}]}).json()["error"] == {"message": "Unknown model",
                                                              "type": "invalid_request_error", "code": 404}
    assert not upstream.sent("/chat/completions")
    assert not events()


def test_per_user_rate_and_concurrency_limits_release_after_each_request(v1, upstream, monkeypatch):
    client, _uid, _raw = v1
    monkeypatch.setattr(settings, "api_max_in_flight", 1)
    upstream.on("POST", "/chat/completions", sse_response(delta("x"), {"choices": [], "usage": USAGE}))
    body = {"model": "m1", "stream": True, "messages": [{"role": "user", "content": "hi"}]}

    # The single slot comes back after a finished stream and after a rejected body.
    assert client.post("/v1/chat/completions", json=body).status_code == 200
    assert client.post("/v1/chat/completions", content=b"{bad").status_code == 400
    assert client.post("/v1/chat/completions", json=body).status_code == 200
    assert not api._in_flight

    monkeypatch.setattr(settings, "api_rate_per_minute", 4)
    statuses = [client.get("/v1/models").status_code for _ in range(2)]
    assert statuses == [200, 429] and client.get("/v1/models").json()["error"]["message"] == "Too many requests"


def test_a_client_gone_before_the_first_byte_still_releases_its_slots_and_records_a_row(v1, monkeypatch):
    client, uid, raw = v1
    started = []

    async def never_read():
        started.append(True)
        yield sse(delta("x"))

    async def pooled(src, body, user_id, session_id=None, affinity_id=None, mask=True):
        pool_pick.reserve(77)
        stream = UsageStream(never_read(), {}, on_close=lambda: pool_pick.release(77))
        return ChatResult(status=200, stream=stream, credential_id=None)

    monkeypatch.setattr(api, "_run_chat", pooled)
    body = json.dumps({"model": "m1", "stream": True, "messages": [{"role": "user", "content": "hi"}]}).encode()
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions", "query_string": b"",
             "root_path": "", "headers": [(b"authorization", f"Bearer {raw}".encode()), (b"content-type", b"application/json")],
             "client": ("127.0.0.1", 5000), "server": ("test", 80)}
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            raise OSError("client went away")

    async def go():
        with pytest.raises(Exception):
            await client.app(scope, receive, send)

    asyncio.run(go())
    assert not started and not api._in_flight and pool_pick.in_flight(77) == 0
    (row,) = events()
    assert (row["request_status"], row["api_key_id"]) == ("cancelled", _key_row(uid)["id"])


def test_a_failed_over_key_is_a_failed_uncounted_row_beside_the_serving_one(v1, upstream):
    client, uid, _raw = v1
    sid = db.fetchone("SELECT id FROM sources WHERE type='openai'")["id"]
    second = key(sid, "sk-upstream-key-2", label="key2")
    first = db.fetchone("SELECT id FROM credentials WHERE source_id=? AND id<>?", (sid, second))["id"]

    def answer(request):
        if request.headers["authorization"] == "Bearer sk-upstream-key":
            return httpx.Response(401, json={"error": {"message": "bad key"}})
        return httpx.Response(200, json=completion("hey", usage=USAGE))

    upstream.on("POST", "/chat/completions", answer)
    got = client.post("/v1/chat/completions", json={"model": "m1", "messages": [{"role": "user", "content": "hi"}]})

    assert got.status_code == 200
    rows = {r["credential_id"]: r for r in events()}
    assert {cid: (r["request_status"], r["count_request"]) for cid, r in rows.items()} == {
        first: ("failed", 0), second: ("success", 1)}
    assert {r["api_key_id"] for r in rows.values()} == {_key_row(uid)["id"]}

def test_the_key_lookup_runs_off_the_event_loop_and_still_rejects_bad_keys(v1, monkeypatch):
    client, _uid, _raw = v1
    anon = TestClient(client.app)
    on_loop = []
    real = accounts.user_by_api_key

    def lookup(token):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(token)

    monkeypatch.setattr(accounts, "user_by_api_key", lookup)
    assert anon.get("/v1/models").json()["error"]["message"] == "Missing API key"
    bad = {"Authorization": "Bearer sk-not-a-key"}
    assert anon.get("/v1/models", headers=bad).json()["error"]["message"] == "Invalid API key"
    assert anon.post("/v1/chat/completions", headers=bad, json={"model": "m1"}).status_code == 401
    assert client.get("/v1/models").status_code == 200
    assert on_loop == [False, False, False]


def test_a_large_body_is_parsed_off_the_loop_by_the_same_strict_rules(v1, upstream):
    client, _uid, _raw = v1
    upstream.on("POST", "/chat/completions", httpx.Response(200, json=completion("hey", usage=USAGE)))
    big = "x" * (api._LARGE_BODY + 1)

    got = client.post("/v1/chat/completions", json={"model": "m1", "messages": [{"role": "user", "content": big}]})
    assert got.status_code == 200 and upstream.sent("/chat/completions")[0]["messages"][0]["content"] == big

    lone_surrogate = b'{"model": "m1", "messages": [{"role": "user", "content": "' + big.encode() + b'\\ud800"}]}'
    got = client.post("/v1/chat/completions", content=lone_surrogate, headers={"content-type": "application/json"})
    assert got.status_code == 400 and got.json()["error"]["message"] == "Invalid JSON"
    assert len(upstream.sent("/chat/completions")) == 1
