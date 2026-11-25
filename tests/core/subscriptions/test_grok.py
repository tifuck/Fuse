"""Grok (SuperGrok CLI proxy): payload shaping for reasoning models, CLI
headers and conversation id, JPEG/PNG-only images, 401 refresh, 426 client
version refresh, spent-plan holds, billing tiers, the catalog, and the weekly
stable-channel client-version check with its fallback and cache."""
import asyncio
import base64
import io
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from PIL import Image

from app.config import settings
from app.sources import grok, grok_version, pool_pick, pool_policy
from tests.core.subscriptions._support import (
    account, drain, events, extra_of, fresh_billing, http, iso, row, run, set_extra, sse, text_of, user,
)

USAGE = {"prompt_tokens": 1, "completion_tokens": 1, "cached_tokens": 0}


def reply(status=200, body=None):
    return (status, body if body is not None else {"id": "c", "usage": USAGE}, None, dict(USAGE), {})


def signed_in(**more):
    return {"access_token": "at-1", "refresh_token": "rt-1", "expires_at": iso(6 * 3600),
            "billing": fresh_billing(tier="SuperGrok", credit_usage_percent=10), **more}


@pytest.fixture
def pool(fresh_db, monkeypatch):
    monkeypatch.setattr(settings, "grok_client_version", "1.0.46")
    owner = user()
    source = grok.GrokSource({"id": pool_policy.source_id("grok"), "name": "Grok"}, {}, [])
    with patch.object(grok, "refresh_pool_billing", new_callable=AsyncMock), \
            patch.object(grok, "openai_chat", new_callable=AsyncMock, return_value=reply()) as upstream:
        yield owner, source, upstream


def chat(pool, body=None, **kwargs):
    owner, source, _upstream = pool
    return run(source.chat({"model": "grok-4.7", "messages": [{"role": "user", "content": "hi"}], **(body or {})},
                           user_id=owner, **kwargs))


def test_payload_drops_what_reasoning_models_reject_and_maps_effort(pool):
    owner, _source, upstream = pool
    account("grok", owner, signed_in())
    chat(pool, {"stop": ["x"], "presence_penalty": 1, "frequency_penalty": 1, "max_tokens": 900,
                "reasoning_effort": "none", "prompt_cache_key": "raw", "temperature": 0.3},
         session_id="scoped-session")

    url, headers, payload = upstream.await_args.args[1:4]
    assert url == "https://cli-chat-proxy.grok.com/v1/chat/completions"
    assert payload == {"model": "grok-4.7", "messages": [{"role": "user", "content": "hi"}], "temperature": 0.3,
                       "reasoning_effort": "low", "max_completion_tokens": 900}
    assert (headers["Authorization"], headers["X-XAI-Token-Auth"]) == ("Bearer at-1", "xai-grok-cli")
    assert (headers["x-grok-client-version"], headers["x-grok-model-override"], headers["x-grok-conv-id"]) == (
        "1.0.46", "grok-4.7", "scoped-session")
    assert grok.chat_payload({"reasoning_effort": "max"})["reasoning_effort"] == "xhigh"
    assert grok.chat_payload({"reasoning_effort": "high"})["reasoning_effort"] == "high"


def _data_url(fmt):
    out = io.BytesIO()
    Image.new("RGB", (4, 3), "teal").save(out, format=fmt)
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(out.getvalue()).decode()


def test_only_jpeg_and_png_images_reach_grok(pool):
    owner, _source, upstream = pool
    account("grok", owner, signed_in())
    png, webp = _data_url("PNG"), _data_url("WEBP")
    parts = [{"type": "text", "text": "see"}, {"type": "image_url", "image_url": {"url": png}},
             {"type": "image_url", "image_url": {"url": webp, "detail": "high"}},
             {"type": "image_url", "image_url": {"url": "data:image/gif;base64,!!notbase64"}}]
    chat(pool, {"messages": [{"role": "user", "content": parts}]})

    sent = upstream.await_args.args[3]["messages"][0]["content"]
    assert sent[1]["image_url"]["url"] == png
    assert sent[2]["image_url"]["url"].startswith(("data:image/png;base64,", "data:image/jpeg;base64,"))
    assert sent[2]["image_url"]["detail"] == "high"
    assert sent[3] == parts[3]  # undecodable input is left for the API to refuse


def test_a_401_refreshes_the_token_and_retries_on_the_same_account(pool):
    owner, _source, upstream = pool
    cred = account("grok", owner, signed_in())
    upstream.side_effect = [reply(401, {"error": "expired"}), reply(200)]

    async def rotated(c):
        set_extra(c["id"], {**extra_of(c["id"]), "access_token": "at-new"})
        return True

    with patch.object(grok, "refresh_one", side_effect=rotated) as refresh:
        result = chat(pool)

    assert (result.status, result.credential_id, refresh.await_count) == (200, cred, 1)
    assert [c.args[2]["Authorization"] for c in upstream.await_args_list] == ["Bearer at-1", "Bearer at-new"]


def test_426_refreshes_the_client_version_and_retries_once_only_when_it_changed(pool):
    owner, _source, upstream = pool
    account("grok", owner, signed_in())
    upgrade = {"error": "Upgrade Required"}

    upstream.side_effect = [reply(426, upgrade), reply(200)]
    with patch.object(grok, "force_refresh", new_callable=AsyncMock, return_value=True):
        assert chat(pool).status == 200
    assert upstream.await_count == 2

    upstream.reset_mock()
    upstream.side_effect = [reply(426, upgrade)]
    with patch.object(grok, "force_refresh", new_callable=AsyncMock, return_value=False):
        assert chat(pool).status == 426
    assert upstream.await_count == 1


def test_a_spent_plan_is_held_out_and_the_request_moves_on(pool):
    owner, _source, upstream = pool
    spent = account("grok", owner, signed_in())
    other = account("grok", owner, signed_in(access_token="at-2"))
    upstream.side_effect = [reply(402, {"error": {"message": "out of credits"}}), reply(200)]
    holds = []
    with patch.object(grok, "note_failure", side_effect=lambda cid, **kw: holds.append((cid, kw))):
        result = chat(pool)

    assert (result.status, result.credential_id) == (200, other)
    assert holds[0][0] == spent and holds[0][1]["limit"] is True and holds[0][1]["cooldown_seconds"] >= 300


def test_billing_reads_the_cli_tier_and_a_free_tier_is_unsubscribed(fresh_db, monkeypatch):
    cred = account("grok", user(), {"access_token": "at-1", "refresh_token": "rt-1", "expires_at": iso(6 * 3600),
                                    "email": "a@x.test"})
    tier = {"value": "Free"}

    def upstream(request):
        if request.url.path.endswith("/billing"):
            return httpx.Response(200, json={"config": {"creditUsagePercent": 37.5, "currentPeriod": {"type": "WEEK"},
                                                        "productUsage": [{"product": "GrokBuild", "usagePercent": 37.5}]}})
        return httpx.Response(200, json={"subscription_tier_display": tier["value"]})

    with http(upstream, monkeypatch):
        run(grok.refresh_billing(grok.db.fetchone("SELECT * FROM credentials WHERE id=?", (cred,)), force=True))
        assert extra_of(cred)["billing"]["credit_usage_percent"] == 37.5
        assert grok.known_unsubscribed(None, extra_of(cred)) is True
        tier["value"] = "SuperGrok Heavy"
        run(grok.refresh_billing(grok.db.fetchone("SELECT * FROM credentials WHERE id=?", (cred,)), force=True))
    assert grok.known_unsubscribed(None, extra_of(cred)) is False


def test_catalog_only_grows(fresh_db, monkeypatch):
    with http(lambda request: httpx.Response(200, json={"data": [{"id": "grok-5"}, {"id": "grok-4.7"}]}), monkeypatch):
        assert run(grok.fetch_catalog("at")) == ["grok-5", "grok-4.7", "grok-4.6", "grok-4.5"]
    monkeypatch.setattr(grok, "_models_cache", (0.0, grok._models_cache[1]))
    with http(lambda request: httpx.Response(200, json={"data": [{"id": "grok-4.7"}]}), monkeypatch):
        assert run(grok.fetch_catalog("at")) == ["grok-4.7", "grok-5", "grok-4.6", "grok-4.5"]


# -- client version -------------------------------------------------------

@pytest.fixture
def stable(monkeypatch, tmp_path):
    monkeypatch.setattr(grok_version, "CACHE_PATH", tmp_path / "version.json")
    monkeypatch.setattr(grok_version, "_loaded", False)
    monkeypatch.setattr(grok_version, "_version", None)
    monkeypatch.setattr(grok_version, "_next_check", 0.0)
    monkeypatch.setattr(grok_version, "_lock", asyncio.Lock())
    monkeypatch.setattr(settings, "grok_client_version_auto_refresh", True)
    monkeypatch.setattr(settings, "grok_client_version", "1.0.46")
    clock = [2_000_000_000.0]
    monkeypatch.setattr(grok_version.time, "time", lambda: clock[0])
    answers = []

    def upstream(request):
        status, text = answers.pop(0)
        return httpx.Response(status, text=text)

    with http(upstream, monkeypatch) as calls:
        yield clock, answers, calls


def restart(monkeypatch):
    monkeypatch.setattr(grok_version, "_loaded", False)
    monkeypatch.setattr(grok_version, "_version", None)
    monkeypatch.setattr(grok_version, "_next_check", 0.0)


def test_the_client_version_is_checked_weekly_and_survives_a_restart(stable, monkeypatch):
    clock, answers, calls = stable
    answers.append((200, "1.0.47\n"))
    run(grok_version.refresh_due())
    assert [str(r.url) for r in calls.requests] == [grok_version.URLS[0]]
    assert grok._auth_headers("t")["x-grok-client-version"] == "1.0.47"
    assert grok._cli_get_headers("t")["x-grok-client-version"] == "1.0.47"

    restart(monkeypatch)
    clock[0] += grok_version.WEEK - 1
    run(grok_version.refresh_due())
    assert grok_version.current_version() == "1.0.47" and len(calls.requests) == 1
    clock[0] += 1
    answers.append((200, "1.0.48"))
    run(grok_version.refresh_due())
    assert grok_version.current_version() == "1.0.48" and len(calls.requests) == 2


def test_a_bad_primary_answer_uses_the_official_fallback(stable):
    _clock, answers, calls = stable
    answers.extend([(200, "<html>blocked</html>"), (200, "1.0.49")])
    run(grok_version.refresh_due())
    assert [str(r.url) for r in calls.requests] == list(grok_version.URLS)
    assert grok_version.current_version() == "1.0.49"


def test_a_failed_check_keeps_the_cached_version_and_retries_after_an_hour(stable):
    clock, answers, calls = stable
    cached = {"version": "1.0.47", "checked_at": clock[0] - grok_version.WEEK}
    grok_version.CACHE_PATH.write_text(json.dumps(cached))
    answers.extend([(503, "down"), (200, "1.0.50-alpha")])
    run(grok_version.refresh_due())
    assert grok_version.current_version() == "1.0.47"
    assert json.loads(grok_version.CACHE_PATH.read_text()) == cached
    clock[0] += grok_version.RETRY - 1
    run(grok_version.refresh_due())
    assert len(calls.requests) == 2
    clock[0] += 1
    answers.append((200, "1.0.50"))
    run(grok_version.refresh_due())
    assert grok_version.current_version() == "1.0.50"


def test_a_pinned_version_never_checks_and_force_refresh_reports_a_change(stable, monkeypatch):
    _clock, answers, calls = stable
    answers.extend([(200, "1.0.70"), (200, "1.0.70")])
    assert run(grok_version.force_refresh()) is True
    assert run(grok_version.force_refresh()) is False
    assert len(calls.requests) == 2

    monkeypatch.setattr(settings, "grok_client_version_auto_refresh", False)
    monkeypatch.setattr(settings, "grok_client_version", "1.0.13")
    run(grok_version.refresh_due())
    assert run(grok_version.force_refresh()) is False
    assert grok_version.current_version() == "1.0.13" and len(calls.requests) == 2


def test_a_streamed_usage_frame_adds_reasoning_to_completion(fresh_db, monkeypatch):
    monkeypatch.setattr(settings, "grok_client_version", "1.0.46")
    owner = user()
    account("grok", owner, signed_in())
    source = grok.GrokSource({"id": pool_policy.source_id("grok"), "name": "Grok"}, {}, [])
    usage = {"prompt_tokens": 10, "completion_tokens": 30, "completion_tokens_details": {"reasoning_tokens": 20}}
    body = events([{"choices": [{"index": 0, "delta": {"content": "hi"}}]}, {"choices": [], "usage": usage}])

    async def go():
        result = await source.chat({"model": "grok-4.7", "messages": [{"role": "user", "content": "hi"}], "stream": True},
                                   user_id=owner)
        return result, await drain(result)

    with patch.object(grok, "refresh_pool_billing", new_callable=AsyncMock), \
            http(lambda request: httpx.Response(200, content=sse([body + "data: [DONE]\n\n"]),
                                                headers={"content-type": "text/event-stream"}), monkeypatch) as calls:
        result, frames = run(go())
    assert [r.url.path for r in calls.requests] == ["/v1/chat/completions"] and text_of(frames) == "hi"
    got = result.stream.usage
    # xAI reports reasoning disjoint from the visible completion.
    assert (got["output_tokens"], got["reasoning_tokens"], got["total_tokens"]) == (50, 20, 60)


def test_a_refresh_answer_other_than_200_never_replaces_the_tokens(fresh_db, monkeypatch):
    cred = account("grok", user(), signed_in(expires_at=iso(-60)))
    odd = httpx.Response(500, json={"access_token": "at-bad", "refresh_token": "rt-bad"})
    with http(lambda request: odd, monkeypatch):
        assert run(grok.refresh_one(row(cred))) is False
    assert (extra_of(cred)["access_token"], extra_of(cred)["refresh_token"]) == ("at-1", "rt-1")


def test_a_refresh_without_an_email_reads_it_from_userinfo_and_rotates_once(fresh_db, monkeypatch):
    cred = account("grok", user(), signed_in(expires_at=iso(-60)))

    def upstream(request):
        if request.url.path == "/oauth2/userinfo":
            return httpx.Response(200, json={"email": "g@x.test", "name": "G"})
        return httpx.Response(200, json={"access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600})

    with http(upstream, monkeypatch) as calls:
        assert run(grok.refresh_one(row(cred))) is True
    assert [(r.method, r.url.path) for r in calls.requests][1:] == [("GET", "/oauth2/userinfo")]
    assert calls.requests[0].method == "POST"
    assert calls.requests[1].headers["Authorization"] == "Bearer at-2"
    extra = extra_of(cred)
    assert (extra["access_token"], extra["refresh_token"], extra["email"]) == ("at-2", "rt-2", "g@x.test")


def test_usage_from_a_request_in_flight_at_the_last_reading_never_holds_the_account():
    now = datetime.now(timezone.utc)
    cred = {"id": 424242, "pool_invokes": 0, "pool_held_until": None, "last_usage_percent": None}
    extra = {"guard": {"last_percent": 10.0, "period_start": "p1", "seen_invokes": 0,
                       "checked_at": (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")}}

    def reading(pct, at):
        _extra, hold, _pct = grok.apply_usage_guard(cred, extra, {"credit_usage_percent": pct, "period_start": "p1"},
                                                    now + at)
        cred["pool_held_until"] = hold
        return hold

    # Picked: counted and in flight; the refresh it starts reads the old %.
    pool_pick.reserve(424242)
    cred["pool_invokes"] = 1
    assert reading(10.0, timedelta(minutes=-20)) is None
    # It finishes after that reading; the next one sees its usage.
    pool_pick.release(424242)
    assert reading(12.0, timedelta(minutes=1)) is None
    # A rise with nothing of Fuse's running since is still someone else's.
    assert reading(14.0, timedelta(minutes=2))
