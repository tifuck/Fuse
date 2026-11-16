"""NanoGPT's two lanes. A subscription model may borrow another user's key
only when that key cannot spend its owner's balance per a recent, successful
reading, and only with plain chat fields (anything else can switch the call to
pay-as-you-go on the donor's balance). Paid models run on the caller's own
key only. Failures move to the next safe key; a probe never sends a completion.
"""
import asyncio
import time

import httpx
import pytest

from app import accounts, db
from app.sources import nanogpt, pool_pick, pool_policy, probe
from tests.core.helpers import quick_user

from ._support import completion, cred_row

SUB, PAID = "z-ai/glm-5.3", "anthropic/claude-opus-5.5"
CATALOG = {"data": [
    {"id": SUB, "subscription": {"included": True}, "reasoning_efforts": ["high", "low", "max"],
     "max_output_tokens": 1000, "capabilities": {"vision": False}},
    {"id": PAID, "subscription": {"included": False}, "reasoning_efforts": ["low", "medium", "high"],
     "capabilities": {"vision": True}},
    {"id": "image/only", "architecture": {"output_modalities": ["image"]}},
]}


def _usage(allows_balance, active=True, mode="subscription"):
    return {"active": active, "dailyInputTokens": {"percentUsed": 0.1, "resetAt": 1790000000000},
            "weeklyInputTokens": {"percentUsed": 0.5, "resetAt": 1790500000000},
            "routing": {"recommendedMode": mode, "paidSpendPolicyAllowsBalance": allows_balance}}


@pytest.fixture
def nano(fresh_db, upstream):
    sid = nanogpt.ensure_nanogpt_source()
    nanogpt._catalog = (time.time(), nanogpt.parse_catalog(CATALOG))
    users = [quick_user(f"n{i}@core.test") for i in range(3)]
    src = nanogpt.NanoGPTSource({"id": sid, "name": "NanoGPT"}, {}, [])
    return src, sid, users


def _link(sid, uid, secret, *, allows_balance, mode="subscription"):
    cid = nanogpt.save_key(sid, secret, uid)
    nanogpt._store_billing(cid, nanogpt.parse_usage(_usage(allows_balance, mode=mode)))
    return cid


def _chat(src, uid, model, **extra):
    body = {"model": "nanogpt/" + model, "messages": [{"role": "user", "content": "hi"}], **extra}
    return asyncio.run(src.chat(body, user_id=uid, session_id=f"s-{uid}-{model}"))


def test_the_catalog_keeps_text_models_with_their_lane_and_the_documented_list_wins(fresh_db, upstream):
    upstream.on("GET", "/subscription/v1/models", httpx.Response(200, json={"data": [{"id": PAID}]}))
    upstream.on("GET", "/v1/models", httpx.Response(200, json=CATALOG))

    catalog = asyncio.run(nanogpt.fetch_catalog())

    assert set(catalog["by_id"]) == {SUB, PAID}
    assert (catalog["by_id"][SUB]["subscription"], catalog["by_id"][PAID]["subscription"]) == (False, True)
    assert catalog["by_id"][SUB]["reasoning_efforts"] == ["low", "high", "max"]


def test_a_subscription_model_borrows_only_a_key_that_cannot_spend_balance(nano, upstream):
    src, sid, (a, b, c) = nano
    _link(sid, a, "sk-nano-aaaaaaaaaaaa", allows_balance=True)
    kb = _link(sid, b, "sk-nano-bbbbbbbbbbbb", allows_balance=False)
    upstream.on("POST", "/v1/chat/completions",
                httpx.Response(200, json=completion(), headers={"x-request-id": "req-1"}))

    result = _chat(src, c, SUB, provider={"only": ["x"]}, billing_mode="paygo", prompt_caching={"enabled": True},
                   n=4, messages=[{"role": "user", "content": [{"type": "text", "text": "hi",
                                                                 "cache_control": {"type": "ephemeral"}}]}])

    assert result.status == 200 and result.credential_id == kb
    assert upstream.headers("/chat/completions")[0]["authorization"] == "Bearer sk-nano-bbbbbbbbbbbb"
    sent = upstream.sent("/chat/completions")[0]
    assert not {"provider", "billing_mode", "prompt_caching", "n"} & set(sent)
    assert "cache_control" not in sent["messages"][0]["content"][0]
    assert result.usage["provider_request_id"] == "req-1"
    # A paid model never borrows: a refusal the chat must not retry as an outage.
    assert _chat(src, c, PAID).status == 403


def test_the_owner_runs_paid_models_on_their_own_key_with_their_extras(nano, upstream):
    src, sid, (a, b, _c) = nano
    ka = _link(sid, a, "sk-nano-aaaaaaaaaaaa", allows_balance=True)
    _link(sid, b, "sk-nano-bbbbbbbbbbbb", allows_balance=False)
    nanogpt.set_model_providers(a, PAID, ["anthropic"])
    upstream.on("POST", "/v1/chat/completions", httpx.Response(200, json=completion()))

    assert _chat(src, a, PAID, reasoning_effort="xhigh").credential_id == ka
    assert _chat(src, a, SUB, max_tokens=5000, prompt_caching={"enabled": True}).credential_id == ka

    paid, sub = upstream.sent("/chat/completions")
    assert paid["prompt_caching"] == {"enabled": True, "ttl": "1h"}  # paid Claude caches for an hour
    assert paid["provider"] == {"only": ["anthropic"], "allow_fallbacks": True}
    assert paid["reasoning_effort"] == "high"  # the model's nearest level
    # The plan covers this model: explicit caching would bill it pay-as-you-go.
    assert "prompt_caching" not in sub and sub["max_tokens"] == 1000


def test_a_failed_stale_or_unavailable_reading_stops_lending(nano, upstream):
    src, sid, (a, b, c) = nano
    kb = _link(sid, b, "sk-nano-bbbbbbbbbbbb", allows_balance=False)
    upstream.on("POST", "/v1/chat/completions", httpx.Response(200, json=completion()))

    nanogpt._store_billing(kb, {}, error="http 500")
    assert _chat(src, a, SUB).status == 403
    assert _chat(src, b, SUB).credential_id == kb  # the owner's own use is unaffected

    kc = _link(sid, c, "sk-nano-cccccccccccc", allows_balance=False, mode="unavailable")
    assert _chat(src, a, SUB).status == 403 and kc
    assert not nanogpt.reading_recent({"billing": {"fetched_at": "2020-01-01T00:00:00Z"}})


def test_failures_move_to_the_next_safe_key_and_a_rejected_key_goes_on_the_second_strike(nano, upstream):
    src, sid, (a, b, c) = nano
    kb = _link(sid, b, "sk-nano-bbbbbbbbbbbb", allows_balance=False)
    kc = _link(sid, c, "sk-nano-cccccccccccc", allows_balance=False)

    def answer(request):
        if request.headers["authorization"].endswith("b" * 12):
            return httpx.Response(429, json={"error": {"code": "daily_rpd_limit_exceeded"}},
                                  headers={"retry-after": "3600"})
        return httpx.Response(200, json=completion())

    upstream.on("POST", "/v1/chat/completions", answer)
    result = _chat(src, a, SUB)
    assert (result.status, result.credential_id) == (200, kc)
    assert [x["credential_id"] for x in result.attempts] == [kb] and pool_pick.cooling_down(kb)

    upstream.routes.clear()
    upstream.on("POST", "/v1/chat/completions", httpx.Response(401, json={"error": {"code": "invalid_api_key"}}))
    pool_pick.reset_health()
    first = _chat(src, a, SUB)
    assert first.status == 502 and "sign in again" in first.body["error"]["message"]
    assert not cred_row(kb)["disabled"]
    pool_pick._cooldown_until.clear()  # the first strike's recheck cooldown has passed
    _chat(src, a, SUB)
    assert cred_row(kb)["disabled"] and cred_row(kc)["disabled"]


def test_listing_follows_the_lane_and_the_users_switches(nano):
    _src, sid, (a, _b, c) = nano
    _link(sid, a, "sk-nano-aaaaaaaaaaaa", allows_balance=True)
    names = ["nanogpt/" + SUB, "nanogpt/" + PAID, "nanogpt/gone"]

    assert nanogpt.listable_models(names, a) == ["nanogpt/" + SUB]
    nanogpt.set_paid_model(a, PAID, True)
    assert nanogpt.listable_models(names, a) == ["nanogpt/" + SUB, "nanogpt/" + PAID]
    nanogpt.set_model_switches(c, paid_all=True)
    assert nanogpt.listable_models(names, c) == ["nanogpt/" + SUB]  # no own key: never paid
    nanogpt.set_model_switches(a, subscription=False)
    assert nanogpt.denied_reason("nanogpt/" + SUB, a).startswith("NanoGPT subscription models are turned off")
    # Pool sharing settings survive model-list writes.
    pool_policy.set_user_pref(a, "nanogpt", share="private")
    nanogpt.set_paid_model(a, PAID, False)
    assert pool_policy.user_pref(a, "nanogpt")["share"] == "private"


def test_a_key_stays_with_its_donor(nano):
    _src, sid, (a, b, _c) = nano
    cid = nanogpt.save_key(sid, "sk-nano-shared-key-1", a)
    with pytest.raises(ValueError):
        nanogpt.save_key(sid, "sk-nano-shared-key-1", b)
    nanogpt.disable_credential(cid)
    assert nanogpt.save_key(sid, "sk-nano-shared-key-1", a) == cid
    assert not cred_row(cid)["disabled"] and "sk-nano" not in cred_row(cid)["secret"]


def test_the_probe_and_a_pasted_key_read_usage_never_a_completion(nano, upstream):
    _src, sid, (a, _b, _c) = nano
    upstream.on("GET", "/subscription/v1/usage", httpx.Response(200, json=_usage(False)))
    upstream.on("POST", "/check-balance", httpx.Response(200, json={"usd_balance": "1.5"}))
    upstream.on("GET", "/subscription/v1/models", httpx.Response(200, json={"data": [{"id": SUB}]}))
    upstream.on("GET", "/v1/models", httpx.Response(200, json=CATALOG))

    cid, err = asyncio.run(nanogpt.add_key(sid, "sk-nano-pasted-key-1", a))
    report = asyncio.run(probe.probe_source(sid))

    assert cid and not err and report["ok"]
    assert nanogpt.shareable(accounts.cred_extra(cred_row(cid)))
    assert not upstream.calls("/chat/completions")
    assert db.fetchone("SELECT COUNT(*) AS n FROM usage_events")["n"] == 0
