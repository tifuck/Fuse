"""Kimi Code: the scoped prompt_cache_key, effort mapping (K3.1 thinking-only,
highspeed without efforts), the coding endpoint and client identity headers,
entitlement 401/403 that only cool the account, the client-identity gate,
OAuth refresh on 401, quota parsing and the catalog merge."""
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.sources import kimi, pool_pick, pool_policy
from tests.core.subscriptions._support import account, extra_of, fresh_billing, http, iso, row, run, set_extra, user

USAGE = {"prompt_tokens": 1, "completion_tokens": 1, "cached_tokens": 0}
MSG = [{"role": "user", "content": "hi"}]


def reply(status=200, body=None):
    return (status, body if body is not None else {"id": "c", "usage": USAGE}, None, dict(USAGE), {})


def oauth_extra(**more):
    return {"auth_kind": "oauth", "access_token": "at-1", "refresh_token": "rt-1", "expires_at": iso(6 * 3600),
            "region": "global", "device_id": "dev-123", "billing": fresh_billing(plan_type="Andante"), **more}


@pytest.fixture
def pool(fresh_db):
    owner = user()
    source = kimi.KimiSource({"id": pool_policy.source_id("kimi"), "name": "Kimi"}, {}, [])
    with patch.object(kimi, "refresh_due", new_callable=AsyncMock), \
            patch.object(kimi, "refresh_pool_quota", new_callable=AsyncMock), \
            patch.object(kimi, "refresh_quota", new_callable=AsyncMock) as quota, \
            patch.object(kimi, "openai_chat", new_callable=AsyncMock, return_value=reply()) as upstream:
        yield owner, source, upstream, quota


def chat(pool, body=None, **kwargs):
    owner, source, _upstream, _quota = pool
    return run(source.chat({"model": "k3", "messages": MSG, **(body or {})}, user_id=owner, **kwargs))


def test_chat_sends_the_scoped_session_as_cache_key_with_kimis_identity_headers(pool):
    owner, _source, upstream, _quota = pool
    account("kimi", owner, oauth_extra())
    chat(pool, {"prompt_cache_key": "raw-client-key"}, session_id="scoped-session-id")

    url, headers, payload = upstream.await_args.args[1:4]
    assert url == "https://api.kimi.ai/coding/v1/chat/completions"
    assert payload["prompt_cache_key"] == "scoped-session-id"
    assert headers["Authorization"] == "Bearer at-1"
    assert (headers["X-Msh-Platform"], headers["User-Agent"], headers["X-Msh-Device-Id"]) == ("fuse", "fuse/1.0", "dev-123")


@pytest.mark.parametrize("model,body,effort,thinking", [
    ("k3", {"reasoning_effort": "medium"}, "high", None),
    ("k3", {"reasoning_effort": "xhigh"}, "max", None),
    ("k3", {"reasoning": {"effort": "minimal"}}, "low", None),
    ("k3", {"reasoning_effort": "none"}, None, {"type": "disabled"}),
    # Kimi answers an unknown effort with 400: it is dropped.
    ("k3", {"reasoning_effort": "turbo"}, None, None),
    # K3.1 only runs thinking: off becomes the lightest level.
    ("k3.1-pro", {"reasoning_effort": "none"}, "low", None),
    ("k3.1-pro-highspeed", {"thinking": {"type": "disabled"}}, "low", None),
    ("k3.1-pro-highspeed", {"thinking": {"type": "disabled"}, "reasoning_effort": "high"}, "high", None),
    # Highspeed K2.7 takes no effort parameter at all.
    ("kimi-for-coding-highspeed", {"reasoning_effort": "high"}, None, None),
])
def test_effort_mapping_per_model(pool, model, body, effort, thinking):
    owner, _source, upstream, _quota = pool
    account("kimi", owner, oauth_extra())
    chat(pool, {"model": model, **body})

    payload = upstream.await_args.args[3]
    assert payload.get("reasoning_effort") == effort
    assert payload.get("thinking") == thinking
    assert "reasoning" not in payload


def test_an_entitlement_401_fails_over_without_refresh_or_auth_strike(pool):
    owner, _source, upstream, quota = pool
    first = account("kimi", owner, oauth_extra())
    second = account("kimi", owner, oauth_extra(access_token="at-2", device_id="dev-2"))
    denied = {"error": {"message": "Your current subscription does not have access to k3"}}
    upstream.side_effect = [reply(401, denied), reply(200)]

    with patch.object(kimi, "refresh_one", new_callable=AsyncMock) as refresh, \
            patch.object(kimi, "auth_failure_confirmed", return_value=True) as strike:
        result = chat(pool)
    assert (result.status, result.credential_id) == (200, second)
    assert [a["credential_id"] for a in result.attempts] == [first]
    assert pool_pick.cooling_down(first)
    # Not an auth failure: no strike (two of which disable), no doomed refresh.
    strike.assert_not_called()
    refresh.assert_not_awaited()
    quota.assert_not_awaited()
    assert row(first)["disabled"] == 0 and row(second)["disabled"] == 0


def test_a_plain_401_refreshes_the_oauth_token_once_and_retries(pool):
    owner, _source, upstream, _quota = pool
    cred = account("kimi", owner, oauth_extra())
    upstream.side_effect = [reply(401, {"error": {"message": "invalid token"}}), reply(200)]

    async def rotated(c):
        set_extra(c["id"], {**extra_of(c["id"]), "access_token": "at-new"})
        return True

    with patch.object(kimi, "refresh_one", side_effect=rotated) as refresh:
        result = chat(pool)

    assert (result.status, result.credential_id, refresh.await_count) == (200, cred, 1)
    assert [call.args[2]["Authorization"] for call in upstream.await_args_list] == ["Bearer at-1", "Bearer at-new"]


def test_a_tier_403_fails_over_without_a_quota_refresh_but_a_spent_quota_refreshes(pool):
    owner, _source, upstream, quota = pool
    first = account("kimi", owner, oauth_extra())
    second = account("kimi", owner, oauth_extra(access_token="at-2"))
    upstream.side_effect = [reply(403, {"error": {"message": "1M context is not included in your plan"}}), reply(200)]
    assert chat(pool).credential_id == second
    quota.assert_not_awaited()

    pool_pick.reset_health()
    upstream.side_effect = [reply(403, {"error": {"type": "rate_limit_error", "message": "quota exceeded"}}), reply(200)]
    result = chat(pool)
    assert result.status == 200 and quota.await_count == 1
    assert {first, second} == {result.credential_id, result.attempts[0]["credential_id"]}


def test_access_terminated_stops_at_once_without_failover_or_cooldown(pool):
    owner, _source, upstream, _quota = pool
    first = account("kimi", owner, oauth_extra())
    account("kimi", owner, oauth_extra(access_token="at-2"))
    gate = {"error": {"type": "access_terminated_error", "message": "client not allowed"}}
    upstream.return_value = reply(403, gate)

    with patch.object(kimi, "note_failure") as cool:
        result = chat(pool)

    assert (result.status, upstream.await_count, result.attempts) == (403, 1, [])
    assert result.credential_id == first
    cool.assert_not_called()


def test_refresh_keeps_the_account_on_a_transient_refusal_and_rotates_on_success(fresh_db, monkeypatch):
    cred = account("kimi", user(), oauth_extra(expires_at=iso(-60)))
    with http(lambda request: httpx.Response(401, json={"error": "unauthorized_client"}), monkeypatch):
        assert run(kimi.refresh_one(row(cred))) is False
    assert row(cred)["disabled"] == 0

    grant = {"access_token": "at-rotated", "refresh_token": "rt-rotated", "expires_in": 3600}
    with http(lambda request: httpx.Response(200, json=grant), monkeypatch) as calls:
        assert run(kimi.refresh_one(row(cred))) is True
    sent = calls.form_bodies("/api/oauth/token")[0]
    assert (sent["grant_type"], sent["refresh_token"]) == ("refresh_token", "rt-1")
    assert calls.requests[0].url.host == "auth.kimi.ai" and calls.requests[0].headers["X-Msh-Device-Id"] == "dev-123"
    assert (extra_of(cred)["access_token"], extra_of(cred)["refresh_token"]) == ("at-rotated", "rt-rotated")


def test_usage_windows_become_percentages_and_the_peak_routes(fresh_db):
    parsed = kimi.parse_usage_payload({"usages": {
        "limit_5h": {"used_ratio": 0.25, "reset_time": "2026-11-16T12:00:00Z"},
        "limit_7d": {"used_ratio": 61},
    }, "user_level_name": "Allegretto"})
    assert [(w["kind"], w["percent"]) for w in parsed["windows"]] == [("5h", 25.0), ("7d", 61.0)]
    assert (parsed["credit_usage_percent"], parsed["plan_type"], parsed["period_end"]) == (61.0, "Allegretto", "2026-11-16T12:00:00Z")


def test_catalog_puts_live_ids_first_and_keeps_dropped_ids_with_their_metadata(fresh_db, monkeypatch):
    first = {"data": [
        {"id": "k4", "display_name": "Kimi K4", "context_length": 524288, "supports_reasoning": True},
        {"id": "k3", "supports_thinking_type": True,
         "think_efforts": {"valid_efforts": ["low", "max"], "default_effort": "max"}},
    ]}
    with http(lambda request: httpx.Response(200, json=first), monkeypatch) as calls:
        names = run(kimi.fetch_catalog("at", "global"))
    assert calls.requests[0].url == "https://api.kimi.ai/coding/v1/models"
    assert names[:2] == ["k4", "k3"] and set(kimi.KIMI_MODELS) <= set(names)
    assert (kimi.efforts_for("k3"), kimi.default_effort("k3")) == (("low", "max"), "max")
    assert (kimi.model_label("k4"), kimi.model_window("k4")) == ("Kimi K4", 524288)

    monkeypatch.setattr(kimi, "_models_cache", (0.0, names))
    with http(lambda request: httpx.Response(200, json={"data": [{"id": "k3"}]}), monkeypatch):
        later = run(kimi.fetch_catalog("at", "global"))
    assert later[0] == "k3" and "k4" in later
    assert kimi.model_label("k4") == "Kimi K4"  # a dropped id keeps its metadata
