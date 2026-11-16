"""Refuseless: pay-as-you-go keys, private until their owner shares. The
catalog is public (read without a key), max_tokens is clamped to the model's
output limit, an out-of-credits key is zeroed and the next one tried, while a
429 is the model's (busy / warming), not the key's, so it is never retried on
another key. Probes and pasted keys read credits, never a completion.
"""
import asyncio
import time

import httpx
import pytest

from app import accounts
from app.sources import pool_policy, probe, refuseless
from tests.core.helpers import quick_user

from ._support import completion, cred_row, delta, drain, sse_response

MODEL = "deepseek-v4.1-flash-abliterated"
CATALOG = {"data": [
    {"id": MODEL, "context_length": 1048576, "max_output_tokens": 8000, "reasoning_efforts": ["off", "low", "high"],
     "reasoning_default": "low", "input_modalities": ["text", "image"]},
    {"id": "embed-1", "type": "embedding"},
    {"id": "tts-1", "output_modalities": ["audio"]},
]}


@pytest.fixture
def pool(fresh_db, upstream):
    sid = refuseless.ensure_refuseless_source()
    refuseless._catalog = (time.time(), refuseless.parse_catalog(CATALOG))
    users = [quick_user(f"r{i}@core.test") for i in range(3)]
    return refuseless.RefuselessSource({"id": sid, "name": "Refuseless"}, {}, []), sid, users


def _link(sid, uid, secret, available=5.0):
    cid = refuseless.save_key(sid, secret, uid)
    refuseless.store_billing(cid, refuseless.parse_credits({"balance": available, "available": available}))
    return cid


def _chat(src, uid, **extra):
    async def go():
        result = await src.chat({"model": "refuseless/" + MODEL, "messages": [{"role": "user", "content": "x"}],
                                 **extra}, user_id=uid, session_id=f"s{uid}")
        raw = await drain(result.stream) if result.stream is not None else None
        return result, raw
    return asyncio.run(go())


def test_the_catalog_is_read_without_a_key_and_a_blocked_first_read_lists_the_documented_lineup(fresh_db, upstream):
    upstream.on("GET", "api.refuseless.com/v1/models", httpx.Response(403, text="cf blocked"),
                httpx.Response(200, json=CATALOG))

    fallback = asyncio.run(refuseless.fetch_catalog(force=True))
    assert MODEL in fallback["by_id"] and not refuseless.catalog_available()

    live = asyncio.run(refuseless.fetch_catalog(force=True))
    assert list(live["by_id"]) == [MODEL] and refuseless.catalog_available()
    assert live["by_id"][MODEL]["reasoning_efforts"] == ["none", "low", "high"]
    assert live["by_id"][MODEL]["image"] is True
    assert all("authorization" not in h for h in upstream.headers("/models", "GET"))


def test_the_payload_maps_effort_clamps_output_and_keeps_tools(pool, upstream):
    src, sid, (a, _b, _c) = pool
    _link(sid, a, "rl-key-aaaaaaaa")
    upstream.on("POST", "/chat/completions", httpx.Response(200, json=completion()))
    tools = [{"type": "function", "function": {"name": "f"}}]

    _chat(src, a, reasoning_effort="none", max_tokens=50_000, tools=tools, tool_choice="auto",
          prompt_cache_key="raw", effort="x", _fuse_internal=1)
    _chat(src, a, reasoning={"effort": "medium"}, max_tokens=True, max_completion_tokens=0)

    first, second = upstream.sent("/chat/completions")
    assert (first["model"], first["reasoning_effort"], first["max_tokens"]) == (MODEL, "off", 8000)
    assert first["tools"] == tools and first["tool_choice"] == "auto"
    assert not {"prompt_cache_key", "effort", "_fuse_internal"} & set(first)
    # medium → the nearest level (higher on a tie); no usable budget → unset.
    assert second["reasoning_effort"] == "high" and "max_tokens" not in second
    assert "max_completion_tokens" not in second


def test_keys_are_private_until_their_owner_shares(pool, upstream):
    src, sid, (a, b, _c) = pool
    ka = _link(sid, a, "rl-key-aaaaaaaa")
    upstream.on("POST", "/chat/completions", httpx.Response(200, json=completion()))
    pool_policy.set_user_pref(b, "refuseless", share="everyone")  # b routes through the pool

    result, _ = _chat(src, b)
    assert result.status == 403 and "Link your own key" in result.body["error"]["message"]

    pool_policy.set_user_pref(a, "refuseless", share="everyone")
    result, _ = _chat(src, b, n=3, provider={"x": 1})
    assert result.credential_id == ka
    lent = upstream.sent("/chat/completions")[0]
    assert "n" not in lent and "provider" not in lent  # a borrowed call cannot multiply the owner's bill


def test_an_out_of_credits_key_is_zeroed_and_the_next_key_serves(pool, upstream):
    src, sid, (a, b, _c) = pool
    ka = _link(sid, a, "rl-key-aaaaaaaa")
    kb = _link(sid, b, "rl-key-bbbbbbbb")
    for uid in (a, b):
        pool_policy.set_user_pref(uid, "refuseless", share="everyone")
    upstream.on("POST", "/chat/completions", lambda r: httpx.Response(
        402, json={"error": {"code": "insufficient_credits"}}) if r.headers["authorization"].endswith("a" * 8)
        else httpx.Response(200, json=completion()))

    result, _ = _chat(src, a)

    assert (result.status, result.credential_id) == (200, kb)
    assert [x["credential_id"] for x in result.attempts] == [ka]
    assert refuseless.out_of_credits(accounts.cred_extra(cred_row(ka)))


def test_credits_running_out_mid_stream_zero_the_key(pool, upstream):
    src, sid, (a, _b, _c) = pool
    ka = _link(sid, a, "rl-key-aaaaaaaa")
    upstream.on("POST", "/chat/completions", sse_response(
        delta("partial"), {"error": {"code": "insufficient_credits", "message": "out"}}, done=False))

    result, raw = _chat(src, a, stream=True)

    assert b"partial" in raw and result.stream.usage["stream_status"] == "failed"
    assert refuseless.out_of_credits(accounts.cred_extra(cred_row(ka)))


def test_a_busy_model_is_returned_with_its_retry_after_not_retried_on_another_key(pool, upstream):
    src, sid, (a, b, _c) = pool
    _link(sid, a, "rl-key-aaaaaaaa")
    _link(sid, b, "rl-key-bbbbbbbb")
    for uid in (a, b):
        pool_policy.set_user_pref(uid, "refuseless", share="everyone")
    upstream.on("POST", "/chat/completions",
                httpx.Response(429, json={"error": {"code": "model_busy"}}, headers={"retry-after": "12"}))

    result, _ = _chat(src, a)

    assert (result.status, result.response_headers) == (429, {"Retry-After": "12"})
    assert len(upstream.sent("/chat/completions")) == 1 and not result.attempts


def test_a_rejected_key_is_disabled_on_the_second_strike(pool, upstream):
    src, sid, (a, _b, _c) = pool
    ka = _link(sid, a, "rl-key-aaaaaaaa")
    upstream.on("POST", "/chat/completions", httpx.Response(401, json={"error": {"code": "invalid_key"}}))
    from app.sources import pool_pick

    first, _ = _chat(src, a)
    assert first.status == 502 and first.body["error"]["code"] == "invalid_key"
    assert not cred_row(ka)["disabled"]
    pool_pick._cooldown_until.clear()  # the first strike's recheck cooldown has passed
    _chat(src, a)
    assert cred_row(ka)["disabled"]


def test_a_pasted_key_and_the_probe_read_credits_never_a_completion(pool, upstream):
    _src, sid, (a, b, _c) = pool
    upstream.on("GET", "/credits", httpx.Response(200, json={"balance": 3, "available": 2.5}),
                httpx.Response(200, json={"balance": 3, "available": 2.5}),
                httpx.Response(401, json={"error": "bad"}))
    upstream.on("GET", "/models", httpx.Response(200, json=CATALOG))

    cid, err = asyncio.run(refuseless.add_key(sid, "Bearer rl-pasted-key-1", a))
    report = asyncio.run(probe.probe_source(sid))
    _none, rejected = asyncio.run(refuseless.add_key(sid, "rl-other-key-2", b))

    assert cid and not err and report["ok"]
    assert accounts.cred_secret(cred_row(cid)) == "rl-pasted-key-1"
    assert rejected.startswith("Refuseless rejected that key")
    assert not upstream.calls("/chat/completions")
