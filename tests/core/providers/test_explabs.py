"""Experiential Labs: every user brings their own key, so catalogs are private
per user and per key revision, explabs/ ids route only to the caller's own
key, and chat sends a minimal payload with Fuse's internal fields removed.
"""
import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException

from app import api, chat_runs, db
from app.sources import explabs, pool_pick
from tests.core.helpers import quick_user

from ._support import completion, cred_row, route, source

MODELS = {"data": [{"id": "alpha"}, {"id": "beta"}]}
META = {"models": [
    {"model": {"slug": "alpha", "display_name": "Alpha", "context_window": 200000,
               "input_modalities": ["text", "image"]},
     "providers": [{"capabilities": {"supported_reasoning_efforts": ["low", "high"]}},
                   {"routable": False, "capabilities": {"supported_reasoning_efforts": ["max"]}}]},
    {"model": {"slug": "beta", "output_modalities": ["image"]}},
    {"model": {"slug": "smuggled"}},
]}


@pytest.fixture
def lab(fresh_db, upstream):
    sid = explabs.ensure_explabs_source()
    return sid, quick_user("a@core.test"), quick_user("b@core.test")


def _add(sid, uid, secret):
    return asyncio.run(explabs.add_user_key(sid, secret, uid))


def test_catalogs_are_private_per_key_and_metadata_cannot_add_models(lab, upstream):
    sid, a, b = lab
    upstream.on("GET", "/v1/models", lambda r: httpx.Response(
        200, json=MODELS if r.headers["authorization"] == "Bearer xl-key-a" else {"data": [{"id": "gamma"}]}))
    upstream.on("GET", "/api/models", httpx.Response(200, json=META))

    _add(sid, a, "xl-key-a")
    _add(sid, b, "xl-key-b")

    a_cat = explabs.cached_catalog(a)
    assert list(a_cat["by_id"]) == ["alpha"]  # beta is image-only, smuggled was never callable
    alpha = a_cat["by_id"]["alpha"]
    assert (alpha["name"], alpha["context_length"], alpha["image"], alpha["reasoning_efforts"]) == (
        "Alpha", 200000, True, ["low", "high"])
    assert list(explabs.cached_catalog(b)["by_id"]) == ["gamma"]
    assert asyncio.run(explabs.resolve("explabs/alpha", b)) is None
    assert asyncio.run(explabs.resolve("explabs/alpha", a)) == "alpha"


def test_a_failed_replacement_keeps_the_working_key_and_a_duplicate_key_is_refused(lab, upstream):
    sid, a, b = lab
    upstream.on("GET", "/v1/models", lambda r: httpx.Response(401) if "bad" in r.headers["authorization"]
                else httpx.Response(200, json=MODELS))
    upstream.on("GET", "/api/models", httpx.Response(404))
    cid, _ = _add(sid, a, "xl-key-a")

    assert _add(sid, a, "xl-key-bad") == (None, "Experiential Labs rejected the API key.")
    assert _add(sid, b, "xl-key-a") == (None, "That key is already linked to another account.")
    from app import accounts
    assert accounts.cred_secret(cred_row(cid)) == "xl-key-a"
    assert list(explabs.cached_catalog(a)["by_id"]) == ["alpha", "beta"]


def test_a_catalog_401_disables_the_key_on_the_second_strike_but_a_quota_error_keeps_it(lab, upstream):
    sid, a, b = lab
    ca = explabs.accounts.add_credential(sid, "k", "xl-key-a", extra={"role": "user"}, donated_by=a)
    cb = explabs.accounts.add_credential(sid, "k", "xl-key-b", extra={"role": "user"}, donated_by=b)
    upstream.on("GET", "/v1/models", lambda r: httpx.Response(401) if r.headers["authorization"].endswith("a")
                else httpx.Response(429, json={"error": {"code": "insufficient_quota"}}))

    assert asyncio.run(explabs.fetch_catalog(a)) == {"by_id": {}}
    assert not cred_row(ca)["disabled"]
    pool_pick._cooldown_until.clear()
    asyncio.run(explabs.fetch_catalog(a, force=True))
    assert cred_row(ca)["disabled"]

    with pytest.raises(explabs.CatalogError):
        asyncio.run(explabs.fetch_catalog(b))
    assert not cred_row(cb)["disabled"]
    # A first fetch that fails degrades to "not routable now", never a 500.
    assert asyncio.run(explabs.resolve("explabs/alpha", b)) is None


def test_a_catalog_fetched_under_a_key_replaced_meanwhile_is_discarded(lab, upstream):
    sid, a, _b = lab
    cid = explabs.accounts.add_credential(sid, "k", "xl-key-a", extra={"role": "user"}, donated_by=a)

    def replaced_meanwhile(request):
        db.execute("UPDATE credentials SET secret=? WHERE id=?", (explabs.encrypt("xl-key-new"), cid))
        return httpx.Response(200, json=MODELS)

    upstream.on("GET", "/v1/models", replaced_meanwhile)
    upstream.on("GET", "/api/models", httpx.Response(404))

    assert asyncio.run(explabs.fetch_catalog(a)) == {"by_id": {}}
    assert explabs.cached_catalog(a) is None


def test_chat_sends_a_minimal_payload_on_the_callers_key(lab, upstream):
    sid, a, _b = lab
    upstream.on("GET", "/v1/models", httpx.Response(200, json=MODELS))
    upstream.on("GET", "/api/models", httpx.Response(404))
    upstream.on("POST", "/chat/completions", httpx.Response(
        200, json=completion(usage={"prompt_tokens": 3, "completion_tokens": 1, "cost": 0}),
        headers={"x-request-id": "rid-1", "x-gateway-provider": "prov", "set-cookie": "s=1"}))
    _add(sid, a, "xl-key-a")
    messages = [{"role": "user", "content": "x", "_fuse_meta": {"secret": 1}}]
    body = {"model": "explabs/alpha", "messages": messages, "_fuse_trace": 1, "session_id": "raw", "models": ["z"]}
    src = explabs.ExperientialLabsSource({"id": sid, "name": "x"}, {}, [])

    result = asyncio.run(src.chat(body, user_id=a, session_id="fx:scoped"))

    (sent,) = upstream.sent("/chat/completions")
    assert sent == {"model": "alpha", "messages": [{"role": "user", "content": "x"}], "prompt_cache_key": "fx:scoped"}
    assert upstream.headers("/chat/completions")[0]["authorization"] == "Bearer xl-key-a"
    assert messages[0]["_fuse_meta"] == {"secret": 1}  # history is not mutated
    assert result.usage["cost_micros"] == 0  # a reported zero is not an unknown cost
    assert result.response_headers == {"x-request-id": "rid-1", "x-gateway-provider": "prov"}


def test_chat_errors_keep_quota_answers_and_hide_a_rejected_key(lab, upstream):
    sid, a, _b = lab
    upstream.on("GET", "/v1/models", httpx.Response(200, json=MODELS))
    upstream.on("GET", "/api/models", httpx.Response(404))
    upstream.on("POST", "/chat/completions",
                httpx.Response(429, json={"error": {"code": "insufficient_quota", "message": "key_daily_cap"}},
                               headers={"retry-after": "30"}),
                httpx.Response(401, json={"error": {"message": "bad key"}}))
    _add(sid, a, "xl-key-a")
    src = explabs.ExperientialLabsSource({"id": sid, "name": "x"}, {}, [])
    chat = lambda: asyncio.run(src.chat({"model": "explabs/alpha", "messages": []}, user_id=a))

    quota = chat()
    assert quota.status == 429 and quota.response_headers["Retry-After"] == "30"
    rejected = chat()
    assert (rejected.status, rejected.body["error"]["code"]) == (502, "invalid_key")


def test_explabs_ids_need_the_callers_own_key_and_never_take_a_generic_route(lab, upstream):
    sid, a, b = lab
    other = source("openai", {"base_url": "https://up.test"})
    route("explabs/alpha", other)
    upstream.on("GET", "/v1/models", httpx.Response(200, json=MODELS))
    upstream.on("GET", "/api/models", httpx.Response(404))
    upstream.on("POST", "experientiallabs.ai/v1/chat", httpx.Response(200, json=completion()))
    _add(sid, a, "xl-key-a")

    def invoke(uid):
        return asyncio.run(api.invoke_chat(chat_runs.internal_request(uid), {"id": uid},
                                           {"model": "explabs/alpha", "messages": [{"role": "user", "content": "x"}]}))

    with pytest.raises(HTTPException) as caught:
        invoke(b)
    assert (caught.value.status_code, caught.value.detail) == (
        403, "Connect an Experiential Labs API key to use its models")
    _user, src, upstream_model, _req, result = invoke(a)
    assert (src.type_id, upstream_model, result.status) == ("explabs", "alpha", 200)
    assert not upstream.calls("up.test")
