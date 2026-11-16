"""Xiaomi MiMo Token Plan: reasoning_content on tool-call turns while thinking
is on, the regional host and source header, Retry-After cooldowns, the
catalog-only key probe, the browser-login blob, and the live lineup."""
import base64
import hashlib
import json
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app import db
from app.sources import mimo, pool_policy
from tests.core.subscriptions._support import account, extra_of, fresh_billing, http, run, user

USAGE = {"prompt_tokens": 1, "completion_tokens": 1, "cached_tokens": 0}
CALL = {"id": "c1", "type": "function", "function": {"name": "grep", "arguments": "{}"}}
HISTORY = [
    {"role": "user", "content": "find it"},
    {"role": "assistant", "content": "", "tool_calls": [CALL]},  # from another model: no reasoning_content
    {"role": "tool", "tool_call_id": "c1", "content": "found"},
    {"role": "assistant", "content": "", "tool_calls": [CALL], "reasoning_content": "kept"},
    {"role": "tool", "tool_call_id": "c1", "content": "again"},
    {"role": "assistant", "content": "done"},
]


def reply(status=200, body=None, headers=None):
    return (status, body if body is not None else {"id": "c", "usage": USAGE}, None, dict(USAGE), headers or {})


@pytest.fixture
def pool(fresh_db):
    owner = user()
    cred = account("mimo", owner, {"api_key": "tp-plan-key", "region": "sgp",
                                   "base_url": mimo.base_for_region("sgp"), "billing": fresh_billing()})
    source = mimo.MimoSource({"id": pool_policy.source_id("mimo"), "name": "Xiaomi"}, {}, [])
    with patch.object(mimo, "refresh_pool_quota", new_callable=AsyncMock), \
            patch.object(mimo, "openai_chat", new_callable=AsyncMock, return_value=reply()) as upstream:
        yield owner, cred, source, upstream


def sent(pool, **body):
    owner, _cred, source, upstream = pool
    upstream.reset_mock()
    run(source.chat({"model": "mimo-v2.6-pro", "messages": HISTORY, **body}, user_id=owner))
    return upstream.await_args.args[1:4]


def test_thinking_on_fills_reasoning_content_on_every_tool_call_turn(pool):
    url, headers, payload = sent(pool, prompt_cache_key="raw-client-key")

    assert url == "https://token-plan-sgp.xiaomimimo.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer tp-plan-key" and headers["X-Mimo-Source"] == "mimocode-cli"
    assert "prompt_cache_key" not in payload and "thinking" not in payload
    assistants = [m for m in payload["messages"] if m["role"] == "assistant"]
    assert [m.get("reasoning_content") for m in assistants] == ["", "kept", None]
    # The caller's history is not rewritten in place.
    assert "reasoning_content" not in HISTORY[1]


@pytest.mark.parametrize("body,thinking,filled", [
    ({"reasoning_effort": "none"}, {"type": "disabled"}, False),
    ({"reasoning": {"effort": "off"}}, {"type": "disabled"}, False),
    ({"reasoning_effort": "high"}, {"type": "enabled"}, True),
    ({"thinking": {"type": "disabled"}, "reasoning_effort": "high"}, {"type": "disabled"}, False),
])
def test_effort_is_a_thinking_toggle_and_off_skips_the_backfill(pool, body, thinking, filled):
    _url, _headers, payload = sent(pool, **body)

    assert payload["thinking"] == thinking
    assert "reasoning_effort" not in payload and "reasoning" not in payload
    assert ("reasoning_content" in payload["messages"][1]) is filled


@pytest.mark.parametrize("headers,cooldown", [({"Retry-After": "120"}, 120.0), ({"Retry-After": "1"}, 5.0),
                                              ({}, 60.0), ({"Retry-After": "999999"}, 86400.0)])
def test_a_rate_limit_cools_the_key_for_its_retry_after(pool, headers, cooldown):
    owner, cred, source, upstream = pool
    upstream.return_value = reply(429, {"error": {"message": "slow down"}}, headers)
    holds = []
    with patch.object(mimo, "refresh_quota", new_callable=AsyncMock), \
            patch.object(mimo, "note_failure", side_effect=lambda cid, **kw: holds.append((cid, kw))):
        result = run(source.chat({"model": "mimo-v2.6-pro", "messages": HISTORY[:1]}, user_id=owner))

    assert result.status == 429
    assert holds == [(cred, {"cooldown_seconds": cooldown, "limit": True})]


def test_a_pasted_key_is_validated_by_the_catalog_only_and_payg_keys_are_refused(fresh_db, monkeypatch):
    owner = user()
    sid = pool_policy.source_id("mimo")

    def upstream(request):
        assert request.method == "GET" and request.url.path == "/v1/models"
        if request.url.host != "token-plan-ams.xiaomimimo.com":
            return httpx.Response(401, json={})
        return httpx.Response(200, json={"data": [{"id": "mimo-v2.6-pro"}, {"id": "mimo-v2.6-flash"}]})

    with http(upstream, monkeypatch) as calls:
        assert run(mimo.add_plan_key(sid, "sk-payg-key-123", owner)) == (None, "Enter a Xiaomi Token Plan key (tp-… or ttp-…).")
        assert calls.requests == []
        cred_id, err = run(mimo.add_plan_key(sid, "tp-plan-key-123", owner))

    assert err == ""
    assert [r.url.host.split(".")[0] for r in calls.requests] == ["token-plan-cn", "token-plan-sgp", "token-plan-ams"]
    extra = extra_of(cred_id)
    assert (extra["region"], extra["base_url"], extra["auth_kind"]) == ("ams", "https://token-plan-ams.xiaomimimo.com/v1", "api")
    assert extra["models"] == ["mimo-v2.6-pro", "mimo-v2.6-flash"] and extra["billing"]["fetched_at"]
    assert mimo.mimo_own_access(owner)["ok"]


def _seal(public_b64: str, payload: dict) -> str:
    """Encrypt a login result the way MiMo's authorize callback does."""
    spki = base64.urlsafe_b64decode(public_b64 + "=" * (-len(public_b64) % 4))
    server = X25519PrivateKey.generate()
    shared = server.exchange(X25519PublicKey.from_public_bytes(spki[-32:]))
    nonce = os.urandom(12)
    sealed = AESGCM(hashlib.sha256(shared).digest()).encrypt(nonce, json.dumps(payload).encode(), None)
    return base64.urlsafe_b64encode(server.public_key().public_bytes_raw() + nonce + sealed).decode().rstrip("=")


def test_browser_login_decrypts_the_plan_key_and_never_takes_over_another_donors_key(fresh_db):
    alice, bob = user("alice@core.test"), user("bob@core.test")
    sid = pool_policy.source_id("mimo")
    login = mimo.start_login()
    blob = _seal(login["public_key"], {"sk": "tp-browser-key", "url": "https://token-plan-sgp.xiaomimimo.com/v1", "uid": "42"})

    data = mimo.decrypt_payload(login["private_key"], blob)
    cred_id = mimo.store_login(sid, data, alice)
    assert (extra_of(cred_id)["region"], extra_of(cred_id)["account_id"]) == ("sgp", "42")

    with pytest.raises(ValueError):
        mimo.store_login(sid, data, bob)
    assert db.fetchone("SELECT donated_by FROM credentials WHERE id=?", (cred_id,))["donated_by"] == alice
    # A host outside the three Token Plan regions is refused.
    with pytest.raises(ValueError):
        mimo.store_login(sid, {**data, "url": "https://evil.example/v1"}, alice)
    with pytest.raises(Exception):
        mimo.decrypt_payload(mimo.start_login()["private_key"], blob)


def test_a_live_listing_is_the_lineup_and_a_failed_one_keeps_the_last(fresh_db, monkeypatch):
    listing = {"data": [{"id": "mimo-v3-pro"}, {"id": "mimo-v2.6-flash"}, {"id": "mimo-v2.5-tts"}, {"id": "mimo-asr"}]}
    with http(lambda request: httpx.Response(200, json=listing), monkeypatch):
        names = run(mimo.fetch_catalog("tp-k", "cn"))
    # Retired ids the listing omits (mimo-v2.6-pro here) are gone; speech models never list.
    assert names == ["mimo-v3-pro", "mimo-v2.6-flash"]

    monkeypatch.setattr(mimo, "_models_cache", (0.0, names))
    with http(lambda request: httpx.Response(503, json={}), monkeypatch):
        assert run(mimo.fetch_catalog("tp-k", "cn")) == names
