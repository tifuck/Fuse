"""The public OpenAI-compatible API: hashed API keys authenticate, /v1/models
lists what the key's owner may use, and every completion (streamed, plain or
failed) is recorded against the key that made it, with OpenAI-shaped errors.
"""
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
from app.sources import refuseless
from tests.core.helpers import quick_user

from ._support import completion, delta, events, frames_of, key, route, source, sse_response

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
