"""Ollama Cloud key pool: which key a request uses, which failures rotate to
another key (never one that may already have generated), and cooldown and
disable states, including what survives a rebuild or a restart.
"""
import asyncio
import hashlib
import json
import os
from unittest.mock import patch

import httpx
import pytest

from app.sources.key_pool import (
    ACTIVE, COOLING, DISABLED, DISABLED_AUTH, DISABLED_UNAVAILABLE, DISABLED_WEEKLY, RESTING, KeyPool,
    NoAvailableKeyError,
)

URL = "https://ollama.com/api/chat"
OK = b'{"message":{"role":"assistant","content":"ok"},"done":true}'
UNAVAILABLE = b'{"error":"Your account is currently unavailable. Please contact support@ollama.com."}'
WEEKLY = b'{"error":"you have reached your weekly usage limit"}'
NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def first_active_key():
    # Active keys are drawn weighted-random; the first listed is drawn here.
    with patch("app.sources.key_pool.random.choices", side_effect=lambda population, weights=None, k=1: population[:k]):
        yield


@pytest.fixture
def make_pool(tmp_path):
    def make(*keys, **kwargs):
        pool = KeyPool(state_file=str(tmp_path / "state.json"), **kwargs)
        pool.load_from_list(list(keys))
        return pool
    return make


def _send(pool, handler, payload=None):
    seen = []

    def record(request):
        seen.append(request.headers["Authorization"].split()[-1])
        return handler(seen[-1])

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as client:
            return await pool.send_request(client, URL, payload or {"model": "m", "messages": []})

    response, state, retried = asyncio.run(go())
    return response, state, retried, seen


def _fail(pool, key, status, body, headers=None):
    pool._handle_error_status(status, httpx.Headers(headers or {}), body, pool.states[key])


def test_selection_order_is_active_then_rested_then_cooling_then_spent_never_auth_disabled(make_pool):
    pool = make_pool("active", "rested-long", "rested-short", "cool-soon", "cool-late", "spent", "revoked")
    s = pool.states
    with patch("app.sources.key_pool.time.time", return_value=NOW):
        s["rested-long"].mark_success(120)
    with patch("app.sources.key_pool.time.time", return_value=NOW + 60):
        s["rested-short"].mark_success(120)
        s["cool-soon"].mark_error("server_500", 30)
        s["cool-late"].mark_error("server_500", 90)
        _fail(pool, "spent", 429, WEEKLY.decode())
        for _ in range(3):
            _fail(pool, "revoked", 401, '{"error":"invalid api key"}')

    with patch("app.sources.key_pool.time.time", return_value=NOW + 61):
        order = []
        while True:
            try:
                order.append(pool.select_key(exclude=set(order)).key)
            except NoAvailableKeyError:
                break

    assert order == ["active", "rested-long", "rested-short", "cool-soon", "cool-late", "spent"]
    assert (s["revoked"].state, s["revoked"].disabled_reason) == (DISABLED, DISABLED_AUTH)


def test_an_unavailable_account_rotates_to_the_next_key_and_stays_out_for_a_day(make_pool):
    pool = make_pool("keyA", "keyB")

    response, state, retried, seen = _send(pool, lambda key: httpx.Response(403 if key == "keyA" else 200,
                                                                            content=UNAVAILABLE if key == "keyA" else OK))

    assert (response.status_code, state.key, retried, seen) == (200, "keyB", True, ["keyA", "keyB"])
    bad = pool.states["keyA"]
    assert (bad.state, bad.disabled_reason) == (DISABLED, DISABLED_UNAVAILABLE)
    assert bad.available_at - bad.last_error_at == pytest.approx(pool.account_unavailable, abs=1)
    assert pool.states["keyB"].state == RESTING
    assert response.extensions["fuse_attempts"] == [{"credential_id": None, "status": 403, "usage": {}}]


def test_a_plan_gated_model_rotates_to_a_paid_key(make_pool):
    pool = make_pool("free", "paid")

    response, state, _retried, seen = _send(pool, lambda key: httpx.Response(
        403, content=b'{"error":"this model requires a subscription"}') if key == "free" else httpx.Response(200, content=OK))

    assert (response.status_code, state.key, seen) == (200, "paid", ["free", "paid"])
    assert pool.states["free"].state == ACTIVE  # a paid model is not a spent key


def test_a_server_error_is_returned_and_never_replayed_on_another_key(make_pool):
    pool = make_pool("keyA", "keyB")

    response, state, retried, seen = _send(pool, lambda key: httpx.Response(500, content=b"boom"))

    # The upstream may already have generated (and billed) it.
    assert (response.status_code, state.key, retried, seen) == (500, "keyA", False, ["keyA"])
    assert pool.states["keyA"].state == COOLING


def test_limited_keys_are_each_asked_once_and_a_reset_quota_serves_again(make_pool):
    pool = make_pool("keyA", "keyB")
    _fail(pool, "keyA", 429, WEEKLY.decode())
    pool.states["keyB"].mark_error("rate_limit", 100)

    response, _state, _retried, seen = _send(pool, lambda key: httpx.Response(429, content=WEEKLY))
    # The caller gets the provider's live answer, not one Fuse remembered.
    assert response.status_code == 429 and sorted(seen) == ["keyA", "keyB"]

    response, state, _retried, seen = _send(pool, lambda key: httpx.Response(200, content=OK) if key == "keyA"
                                            else httpx.Response(429, content=WEEKLY))
    assert (response.status_code, state.key) == (200, "keyA")
    assert (state.state, state.disabled_reason) == (RESTING, None)


def test_every_key_cooling_raises_with_the_soonest_retry_after(make_pool):
    pool = make_pool("keyA", "keyB")
    pool.states["keyA"].mark_error("server_503", 100)
    pool.states["keyB"].mark_error("server_503", 200)

    with pytest.raises(NoAvailableKeyError) as caught:
        _send(pool, lambda key: httpx.Response(200, content=OK))

    assert 90 < caught.value.retry_after <= 100


def test_only_repeated_credential_failures_disable_a_key(make_pool):
    pool = make_pool("keyA", "keyB")
    for _ in range(5):
        _fail(pool, "keyA", 403, '{"error":"forbidden for this plan"}')
    assert pool.states["keyA"].state != DISABLED and pool.states["keyA"].consecutive_auth_errors == 0

    for count in (1, 2):
        _fail(pool, "keyB", 403, '{"error":"Invalid token"}')
        assert (pool.states["keyB"].state, pool.states["keyB"].consecutive_auth_errors) == (ACTIVE, count)
    _fail(pool, "keyB", 403, '{"error":"Invalid token"}')
    assert (pool.states["keyB"].state, pool.states["keyB"].disabled_reason) == (DISABLED, DISABLED_AUTH)


def test_retry_after_extends_a_cooldown_but_is_capped_at_the_longest_tier(make_pool):
    pool = make_pool("long", "short", "bogus")
    with patch("app.sources.key_pool.time.time", return_value=NOW):
        _fail(pool, "long", 403, UNAVAILABLE.decode(), {"retry-after": "172800"})
        _fail(pool, "short", 403, UNAVAILABLE.decode(), {"retry-after": "30"})
        _fail(pool, "bogus", 429, "slow down", {"retry-after": "99999999"})

    assert pool.states["long"].available_at == NOW + 172800
    assert pool.states["short"].available_at == NOW + pool.account_unavailable
    bogus = pool.states["bogus"]
    assert (bogus.disabled_reason, bogus.available_at) == (DISABLED_WEEKLY, NOW + pool.weekly_quota)


def test_state_is_saved_by_key_hash_and_a_rebuild_keeps_live_cooldowns(make_pool, tmp_path):
    pool = make_pool("keyA", "keyB")
    _fail(pool, "keyA", 429, WEEKLY.decode())
    pool.save_state()

    raw = (tmp_path / "state.json").read_text()
    assert "keyA" not in raw and hashlib.sha256(b"keyA").hexdigest() in json.loads(raw)
    assert os.stat(tmp_path / "state.json").st_mode & 0o777 == 0o600
    # A rebuild of the same pool keeps its in-memory state; a fresh process restores it.
    pool.states["keyA"].mark_error("server_500", 10)
    pool.load_from_list(["keyA", "keyB"])
    assert pool.states["keyA"].last_error_type == "server_500"
    restored = make_pool("keyA", "keyB")
    assert (restored.states["keyA"].state, restored.states["keyA"].disabled_reason) == (DISABLED, DISABLED_WEEKLY)


def test_error_counters_decay_once_per_idle_window_not_every_sweep(make_pool):
    state = make_pool("key").states["key"]
    with patch("app.sources.key_pool.time.time", return_value=NOW):
        state.mark_error("server_500", 10)
        state.mark_error("server_500", 10)
        state.consecutive_errors = 8
    with patch("app.sources.key_pool.time.time", return_value=NOW + 101):
        assert state.decay_errors(10.0) is True
    with patch("app.sources.key_pool.time.time", return_value=NOW + 131):
        assert state.decay_errors(10.0) is False  # the next window (10 * 5s) has not passed
    assert (state.consecutive_errors, state.last_backoff) == (4, 5)

    auth = make_pool("auth").states["auth"]
    auth.consecutive_auth_errors, auth.last_error_at = 2, NOW
    with patch("app.sources.key_pool.time.time", return_value=NOW + 601):
        assert auth.decay_errors(10.0) is True
    with patch("app.sources.key_pool.time.time", return_value=NOW + 631):
        assert auth.decay_errors(10.0) is False
    assert auth.consecutive_auth_errors == 1


def test_a_spent_quota_comes_back_when_its_window_ends_but_an_auth_disable_does_not(make_pool):
    pool = make_pool("spent", "revoked")
    with patch("app.sources.key_pool.time.time", return_value=NOW):
        _fail(pool, "spent", 403, UNAVAILABLE.decode())
        for _ in range(3):
            _fail(pool, "revoked", 401, '{"error":"invalid api key"}')
    with patch("app.sources.key_pool.time.time", return_value=NOW + pool.account_unavailable + 1):
        for state in pool.states.values():
            state.refresh_state()

    assert (pool.states["spent"].state, pool.states["spent"].disabled_reason) == (ACTIVE, None)
    assert pool.states["revoked"].state == DISABLED
