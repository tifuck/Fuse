"""Provider error classification and what each kind makes the callers do.

classify() maps every provider's error shape onto one taxonomy; annotate()
rewrites the relayed body; cooldown() benches a pool account; invoke_chat adds
Retry-After only to transient kinds; /v1 relays the kind; and the chat loop
retries transient kinds but fails terminal ones at once (a retry would burn
quota and fail the same way).
"""
import asyncio
from email.utils import format_datetime
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app import api, chat_runs, chat_runtime, chats, provider_errors
from app.sources.base import ChatResult
from tests.core.helpers import quick_user, run_async, run_turn, text_frames, stream_result

from ._support import completion, key, route, source

NOW = 1_800_000_000.0


def _kind(provider, status, body, headers=None):
    info = provider_errors.classify(provider, status, body, headers, now=NOW)
    return info and info["kind"]


@pytest.mark.parametrize("provider,status,body,headers,kind", [
    ("openai", 429, {"error": {"code": "insufficient_quota", "message": "You exceeded your quota"}}, None,
     "insufficient_balance"),
    ("claude", 429, {"error": {"type": "rate_limit_error", "message": "rate limited"}},
     {"anthropic-ratelimit-unified-status": "rejected"}, "usage_exhausted"),
    ("zai", 429, {"error": {"code": "1308", "message": "Usage limit reached"}}, None, "usage_exhausted"),
    ("zai", 429, {"error": {"code": "1113", "message": "x"}}, None, "insufficient_balance"),
    ("openrouter", 402, {"error": {"message": "x", "metadata": {"limit_source": "openrouter_credits"}}}, None,
     "insufficient_balance"),
    ("openrouter", 404, {"error": {"message": "No endpoints found that support tool use"}}, None, "invalid_request"),
    ("explabs", 429, {"error": {"code": "insufficient_quota", "message": "key_daily_cap: spent"}}, None,
     "usage_exhausted"),
    ("nanogpt", 400, {"error": {"code": "content_policy_violation", "message": "x"}}, None, "content_filter"),
    ("refuseless", 429, {"error": {"code": "model_busy", "message": "x"}}, None, "overloaded"),
    ("refuseless", 402, {"error": {"code": "insufficient_credits", "message": "x"}}, None, "insufficient_balance"),
    ("llama", 400, {"error": {"type": "exceed_context_size_error", "message": "x"}}, None, "context_length"),
    ("openai", 400, {"error": {"message": "This model's maximum context length is 128000 tokens"}}, None,
     "context_length"),
    ("kimi", 429, {"error": {"message": "You have reached your concurrent request limit"}}, None, "rate_limit"),
    ("openai", 400, {"error": {"message": "Your request was flagged for violating our content policy"}}, None,
     "content_filter"),
    ("openai", 401, {"error": "Invalid API key provided"}, None, "credentials"),
    ("openai", 404, {"error": {"message": "The model `gpt-x` does not exist"}}, None, "model_not_found"),
    ("openai", 503, {"error": {"message": "busy"}}, None, "overloaded"),
    ("openai", 500, {}, None, "server_error"),
    ("openai", 418, {"error": {"message": "teapot"}}, None, None),
])
def test_each_provider_shape_classifies_to_one_kind(provider, status, body, headers, kind):
    assert _kind(provider, status, body, headers) == kind


def test_fuse_stream_failures_are_stalls_or_interruptions_and_keep_their_words():
    assert _kind("openai", 502, {"error": {"message": "upstream stream failed (ReadTimeout)"}}) == "stalled"
    assert _kind("openai", 502, {"error": {"message": "upstream transport failure (ReadError)"}}) == "interrupted"
    assert _kind("openai", 502, {"error": {"message": "upstream stream ended before completion"}}) == "interrupted"
    # A local adapter failure is a status-only server error whose exact text
    # the resume policy matches: it must reach it unchanged.
    info = provider_errors.classify("openai", 502, {"error": {"message": "upstream request failed (TypeError)"}})
    assert (info["kind"], info["message"]) == ("server_error", "upstream request failed (TypeError)")


@pytest.mark.parametrize("headers,body,wait", [
    ({"retry-after": "30"}, {"error": {"message": "slow"}}, 30.0),
    ({"retry-after": format_datetime(datetime.fromtimestamp(NOW + 90, timezone.utc), usegmt=True)}, {}, 90.0),
    ({"x-ratelimit-reset-tokens": "1m30s", "x-ratelimit-reset-requests": "20ms"}, {}, 90.0),
    ({}, {"error": {"message": "Rate limit reached. Please try again in 20s."}}, 20.0),
    ({}, {"error": {"message": "Rate limit reached. Please try again in 1m30s."}}, 90.0),
    ({}, {"error": {"message": "x", "resets_at": int((NOW + 45) * 1000)}}, 45.0),
])
def test_reset_times_come_from_every_documented_place(headers, body, wait):
    info = provider_errors.classify("openai", 429, body, headers, now=NOW)
    assert info["retry_after"] == pytest.approx(wait)


@pytest.mark.parametrize("headers,wait", [
    ({"retry-after": "inf"}, None),
    ({"retry-after": "nan"}, None),
    ({"retry-after": "1e300"}, provider_errors.MAX_WAIT),
    ({"anthropic-ratelimit-unified-reset": "9" * 400}, None),
    ({"anthropic-ratelimit-unified-reset": "9" * 20}, provider_errors.MAX_WAIT),
])
def test_an_unbounded_reset_is_dropped_or_capped_and_never_breaks_annotate(headers, wait):
    _body, info = provider_errors.annotate("openai", 429, {"error": {"message": "slow"}}, headers, now=NOW)
    assert info["retry_after"] == wait


def test_a_rate_limit_that_clears_in_hours_is_a_spent_allowance():
    info = provider_errors.classify("openai", 429, {"error": {"message": "slow"}}, {"retry-after": "7200"}, now=NOW)
    assert info["kind"] == "usage_exhausted"


def test_zai_reset_sentences_are_beijing_time():
    body = {"error": {"code": "1308", "message": "Your limit will reset at 2027-01-01 08:00:00"}}
    info = provider_errors.classify("zai", 429, body, now=NOW)
    assert info["reset_at"] == datetime(2027, 1, 1, 0, 0, tzinfo=timezone.utc).timestamp()


def test_messages_name_the_provider_window_and_reset_and_hide_secrets():
    info = provider_errors.classify(
        "claude", 429, {"error": {"type": "rate_limit_error", "message": "limit for sk-abcdefghijk1234"}},
        {"anthropic-ratelimit-unified-status": "rejected",
         "anthropic-ratelimit-unified-representative-claim": "five_hour",
         "anthropic-ratelimit-unified-reset": str(int(NOW + 2 * 3600))}, now=NOW)
    assert info["message"].startswith("Claude: 5-hour usage limit reached; resets in 2h (")
    assert "sk-abcdefghijk1234" not in info["message"] and "sk-***" in info["message"]


def test_annotate_rewrites_once_drops_private_headers_and_normalizes_code():
    raw = {"error": {"code": "1308", "message": "Usage limit reached"},
           "_fuse_headers": {"retry-after": "60"}}

    body, info = provider_errors.annotate("zai", 429, raw, now=NOW)

    assert "_fuse_headers" not in body and info["kind"] == "usage_exhausted"
    err = body["error"]
    assert (err["code"], err["provider_code"], err["fuse_kind"], err["type"]) == (429, "1308", "usage_exhausted",
                                                                                   "usage_exhausted")
    assert err["resets_at"] == int(NOW + 60)
    again, info2 = provider_errors.annotate("zai", 429, body, now=NOW)
    assert again == body and info2 is None


def test_cooldowns_bench_spent_plans_until_reset_and_follow_short_rate_limits():
    def info(kind, wait=None):
        return {"kind": kind, "retry_after": wait}

    assert provider_errors.cooldown(None, 60) == 60
    assert provider_errors.cooldown(info("usage_exhausted", 7200), 60) == 7200
    assert provider_errors.cooldown(info("usage_exhausted", 10 * 86400), 60) == 6 * 3600
    assert provider_errors.cooldown(info("usage_exhausted"), 60) == 1800
    assert provider_errors.cooldown(info("insufficient_balance", 5), 60) == 1800
    assert provider_errors.cooldown(info("rate_limit", 120), 60) == 120
    assert provider_errors.cooldown(info("rate_limit", 5), 60) == 60
    assert provider_errors.cooldown(info("context_length"), 60) == 60


# -- callers ------------------------------------------------------------------

@pytest.fixture
def openai_route(fresh_db, upstream):
    uid = quick_user()
    sid = source("openai", {"base_url": "https://up.test"})
    key(sid, "sk-upstream-key")
    route("m1", sid, "up-1")
    return uid


def _invoke(uid, body):
    return asyncio.run(api.invoke_chat(chat_runs.internal_request(uid), {"id": uid}, body))


def test_invoke_chat_annotates_failures_and_adds_retry_after_only_to_transient_kinds(openai_route, upstream):
    uid = openai_route
    upstream.on("POST", "/chat/completions",
                httpx.Response(429, json={"error": {"message": "Rate limit reached. Please try again in 20s."}}),
                httpx.Response(429, json={"error": {"code": "insufficient_quota",
                                                    "message": "Quota gone. Try again in 20s."}}))
    body = {"model": "m1", "messages": [{"role": "user", "content": "hi"}]}

    *_, limited = _invoke(uid, body)
    assert limited.body["error"]["fuse_kind"] == "rate_limit" and limited.response_headers["Retry-After"] == "20"
    *_, broke = _invoke(uid, body)
    assert broke.body["error"]["fuse_kind"] == "insufficient_balance"
    assert "Retry-After" not in broke.response_headers


def test_dispatch_relays_the_kind_and_never_an_upstream_401_as_the_callers_auth(openai_route, upstream):
    uid = openai_route
    upstream.on("POST", "/chat/completions",
                httpx.Response(401, json={"error": {"message": "Incorrect API key provided: sk-upstreamkey1"}}),
                httpx.Response(402, json={"error": {"message": "Insufficient credits"}}))
    body = {"model": "m1", "messages": [{"role": "user", "content": "hi"}]}

    async def dispatch():
        return await api.dispatch_chat(chat_runs.internal_request(uid), {"id": uid}, dict(body))

    auth = asyncio.run(dispatch())
    # The client's own Fuse key is fine; the provider rejected Fuse's key.
    assert auth.status_code == 502 and b'"type":"credentials"' in auth.body and b"sk-upstreamkey1" not in auth.body
    credits = asyncio.run(dispatch())
    assert credits.status_code == 402 and b'"type":"insufficient_balance"' in credits.body


def _failing_provider(results):
    calls = []

    async def invoke(request, user, body, **_kwargs):
        calls.append(body)
        result = results.pop(0)
        if isinstance(result, list):
            result = stream_result(result)
        return user, SimpleNamespace(source_id=None, type_id="openai"), "m", "m", result
    return invoke, calls


def _annotated(status, body):
    return ChatResult(status=status, body=provider_errors.annotate("openai", status, body)[0])


@pytest.mark.parametrize("error,retried", [
    (_annotated(429, {"error": {"message": "slow down"}}), True),
    (_annotated(503, {"error": {"message": "server busy"}}), True),
    (_annotated(429, {"error": {"code": "insufficient_quota", "message": "no money"}}), False),
    (_annotated(400, {"error": {"code": "context_length_exceeded", "message": "too long"}}), False),
    (_annotated(400, {"error": {"code": "content_filter", "message": "blocked"}}), False),
    (_annotated(403, {"error": {"type": "permission_error", "message": "nope"}}), False),
])
def test_the_chat_loop_retries_transient_kinds_and_fails_terminal_ones_at_once(fresh_db, error, retried):
    uid = quick_user()
    invoke, calls = _failing_provider([error, text_frames("recovered")])
    with patch.object(api, "invoke_chat", new=invoke), patch.object(api, "_safe_record_usage"), \
            patch.object(chat_runtime, "generate_thread_title", new_callable=AsyncMock, return_value=""), \
            patch("app.chat_runtime._RESUME_BACKOFF", (0.0, 0.0, 0.0, 0.0)):
        thread_id, run = run_async(run_turn(uid, "hi"))

    assert len(calls) == (2 if retried else 1)
    assert run["status"] == ("completed" if retried else "failed")
    reply = chats.get_thread(uid, thread_id)["messages"][-1]
    assert bool(reply.get("error")) is not retried


def test_an_unclassified_error_body_is_relayed_scrubbed_and_capped():
    body = {"error": {"message": "teapot sk-abcdefgh12345678 " + "x" * 900, "param": "p"}, "detail": "Bearer abcdefgh1234"}
    out, info = provider_errors.annotate("openai", 418, body, now=NOW)
    assert info is None and out["error"]["param"] == "p"
    assert out["error"]["message"].startswith("teapot sk-*** ") and len(out["error"]["message"]) == 500
    assert out["detail"] == "Bearer ***"
