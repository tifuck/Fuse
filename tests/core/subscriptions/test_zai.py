"""Z.AI GLM Coding Plan: effort mapping per model generation, the coding
endpoint and region, the plan-key probe (raw auth first, then Bearer, global
then China), quota windows, 429 holds until the named reset, and the catalog."""
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app import db
from app.sources import pool_policy, zai
from tests.core.subscriptions._support import account, extra_of, fresh_billing, http, run, user

USAGE = {"prompt_tokens": 1, "completion_tokens": 1, "cached_tokens": 0}


def reply(status=200, body=None, headers=None):
    return (status, body if body is not None else {"id": "c", "usage": USAGE}, None, dict(USAGE), headers or {})


@pytest.fixture
def pool(fresh_db):
    owner = user()
    cred = account("zai", owner, {"api_key": "zk-plan-1", "region": "global",
                                  "billing": fresh_billing(plan_type="pro")})
    source = zai.ZaiSource({"id": pool_policy.source_id("zai"), "name": "Z.AI"}, {}, [])
    with patch.object(zai, "refresh_pool_quota", new_callable=AsyncMock), \
            patch.object(zai, "openai_chat", new_callable=AsyncMock, return_value=reply()) as upstream:
        yield owner, cred, source, upstream


def sent(upstream, body, owner, source):
    upstream.reset_mock()
    run(source.chat({"messages": [{"role": "user", "content": "hi"}], **body}, user_id=owner))
    _client, url, headers, payload = upstream.await_args.args[:4]
    return url, headers, payload


@pytest.mark.parametrize("extra,effort", [
    ({"reasoning_effort": "medium"}, "high"),
    ({"reasoning_effort": "xhigh"}, "max"),
    ({"reasoning_effort": "minimal"}, "low"),
    ({"reasoning": {"effort": "max"}}, "max"),
    # GLM-5.3 always thinks: "off" is the lightest level, never a disabled request.
    ({"reasoning_effort": "none"}, "low"),
    ({"thinking": {"type": "disabled"}}, "low"),
    # An explicit effort wins over the thinking toggle.
    ({"thinking": {"type": "disabled"}, "reasoning_effort": "high"}, "high"),
    # An unknown level is left to the model default.
    ({"reasoning_effort": "turbo"}, None),
])
def test_glm_5_3_sends_mapped_reasoning_effort_and_never_disables_thinking(pool, extra, effort):
    owner, _cred, source, upstream = pool
    url, headers, payload = sent(upstream, {"model": "glm-5.3", "prompt_cache_key": "raw-client-key", **extra},
                                 owner, source)

    assert url == "https://api.z.ai/api/coding/paas/v4/chat/completions"
    assert headers["Authorization"] == "Bearer zk-plan-1"
    assert payload.get("reasoning_effort") == effort
    assert "reasoning" not in payload and "prompt_cache_key" not in payload
    assert payload.get("thinking", {}).get("type") != "disabled"


def test_older_glm_keeps_its_thinking_switch_and_takes_no_effort_levels(pool):
    owner, _cred, source, upstream = pool
    _url, _headers, off = sent(upstream, {"model": "glm-4.7", "reasoning_effort": "none"}, owner, source)
    _url, _headers, on = sent(upstream, {"model": "glm-4.7", "reasoning_effort": "high"}, owner, source)
    _url, _headers, native = sent(upstream, {"model": "glm-4.7", "reasoning_effort": "none",
                                             "thinking": {"type": "enabled"}}, owner, source)

    assert (off["thinking"], "reasoning_effort" in off) == ({"type": "disabled"}, False)
    assert (on["thinking"], "reasoning_effort" in on) == ({"type": "enabled"}, False)
    assert native["thinking"] == {"type": "enabled"}
    assert zai.efforts_for("glm-5.3") == ("low", "high", "max") == zai.efforts_for("glm-5.3-flash")
    assert zai.efforts_for("glm-4.7") == ("none", "high")


def test_a_china_key_chats_on_the_china_coding_endpoint(fresh_db):
    owner = user()
    account("zai", owner, {"api_key": "zk-cn", "region": "cn", "billing": fresh_billing(plan_type="pro")})
    source = zai.ZaiSource({"id": pool_policy.source_id("zai"), "name": "Z.AI"}, {}, [])
    with patch.object(zai, "refresh_pool_quota", new_callable=AsyncMock), \
            patch.object(zai, "openai_chat", new_callable=AsyncMock, return_value=reply()) as upstream:
        run(source.chat({"model": "glm-5.3", "messages": [{"role": "user", "content": "hi"}]}, user_id=owner))

    assert upstream.await_args.args[1] == "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"


QUOTA = {"code": 200, "success": True, "data": {"level": "PRO", "limits": [
    {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 42, "nextResetTime": 1_900_000_000_000},
    {"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 71.5, "nextResetTime": 1_900_500_000_000},
    {"type": "TIME_LIMIT", "percentage": 3},
]}}


def test_a_plan_key_is_probed_raw_then_bearer_and_falls_back_to_the_china_region(fresh_db, monkeypatch):
    owner = user()

    def upstream(request):
        china = request.url.host == "open.bigmodel.cn"
        bearer = request.headers["Authorization"].startswith("Bearer ")
        assert request.url.path == "/api/monitor/usage/quota/limit"
        return httpx.Response(200, json=QUOTA) if china and bearer else httpx.Response(401, json={"msg": "no"})

    with http(upstream, monkeypatch) as calls:
        cred_id, err = run(zai.add_plan_key(pool_policy.source_id("zai"), "  zk-plan-key-1  ", owner))

    assert err == "" and cred_id
    assert [(r.url.host, r.headers["Authorization"].startswith("Bearer")) for r in calls.requests] == [
        ("api.z.ai", False), ("api.z.ai", True), ("open.bigmodel.cn", False), ("open.bigmodel.cn", True)]
    extra = extra_of(cred_id)
    assert (extra["api_key"], extra["region"], extra["quota_auth"]) == ("zk-plan-key-1", "cn", "bearer")
    billing = extra["billing"]
    assert billing["plan_type"] == "pro" and billing["credit_usage_percent"] == 71.5
    assert [(w["name"], w["percent"]) for w in billing["windows"]] == [("5-hour", 42), ("Weekly", 71.5)]
    assert billing["five_hour_percent"] == 42 and billing["mcp_percent"] == 3
    assert billing["windows"][0]["reset_at"] == "2030-03-17T17:46:40Z"


def test_a_key_without_a_coding_plan_is_refused_and_never_stored(fresh_db, monkeypatch):
    owner = user()
    empty = {"code": 200, "success": True, "data": {"limits": []}}
    with http(lambda request: httpx.Response(200, json=empty), monkeypatch):
        cred_id, err = run(zai.add_plan_key(pool_policy.source_id("zai"), "zk-payg-key-1", owner))

    assert cred_id is None and "not a GLM Coding Plan key" in err
    assert db.fetchall("SELECT id FROM credentials") == []


def test_a_spent_window_holds_the_key_until_the_reset_zai_names(pool):
    owner, cred, source, upstream = pool
    spent = {"error": {"code": "1308", "message": "Usage limit reached for 5 hour. Your limit will reset at 2099-01-01 08:00:00"}}
    upstream.return_value = reply(429, spent)
    holds = []
    real = zai.note_failure
    with patch.object(zai, "refresh_quota", new_callable=AsyncMock), \
            patch.object(zai, "note_failure", side_effect=lambda cid, **kw: (holds.append((cid, kw)), real(cid, **kw))):
        result = run(source.chat({"model": "glm-5.3", "messages": [{"role": "user", "content": "hi"}]}, user_id=owner))

    assert result.status == 429
    assert [(cid, kw["limit"]) for cid, kw in holds] == [(cred, True)]
    # Capped at 6 hours, far past the 60 s a bare 429 would get.
    assert holds[0][1]["cooldown_seconds"] == 6 * 3600


def test_catalog_lists_upstream_order_then_seeds_and_survives_a_failed_fetch(fresh_db, monkeypatch):
    listing = {"data": [{"id": "glm-6"}, {"id": "glm-5.3"}]}
    with http(lambda request: httpx.Response(200, json=listing), monkeypatch) as calls:
        names = run(zai.fetch_catalog("zk", "global"))
    assert names == ["glm-6", "glm-5.3", "glm-5.3-flash", "glm-5-turbo"]
    assert calls.requests[0].url == "https://api.z.ai/api/coding/paas/v4/models"

    monkeypatch.setattr(zai, "_models_cache", (0.0, names))
    with http(lambda request: httpx.Response(500, json={}), monkeypatch):
        assert run(zai.fetch_catalog("zk", "global")) == names
    # The plan reroutes retired models: a listing that drops one drops it.
    with http(lambda request: httpx.Response(200, json={"data": [{"id": "glm-5.3"}]}), monkeypatch):
        assert run(zai.fetch_catalog("zk", "global")) == ["glm-5.3", "glm-5.3-flash", "glm-5-turbo"]


def test_quota_parse_falls_back_to_legacy_fields(fresh_db):
    parsed = zai.parse_quota_payload({"fiveHourPercent": 12, "weeklyPercent": 30, "monthlyMCPUsage": 5})
    assert [(w["name"], w["percent"]) for w in parsed["windows"]] == [("5-hour", 12.0), ("Weekly", 30.0)]
    assert parsed["credit_usage_percent"] == 30.0
