"""The shared OpenAI-compatible request path (proxy.openai_chat) and the /v1
stream tap (api._tap): usage from the final SSE frame, failure phases that
say whether a retry is safe, and an accounting row for every stream however
it ends (complete, truncated, errored inline, broken, or cancelled).
"""
import asyncio
import json

import httpx
import pytest

from app import api, db, stats
from app.proxy import openai_chat
from app.sources import http_pool
from tests.core.helpers import quick_user, wait_until

from ._support import completion, delta, drain, events, frames_of, source, sse, sse_response

URL = "https://up.test/v1/chat/completions"
USAGE = {"prompt_tokens": 100, "completion_tokens": 7, "total_tokens": 107,
         "prompt_tokens_details": {"cached_tokens": 40}}


def _call(body, **kwargs):
    async def go():
        status, obj, stream, usage, headers = await openai_chat(httpx.AsyncClient(), URL, {}, body,
                                                                close_client=True, **kwargs)
        raw = await drain(stream) if stream is not None else None
        return status, obj, raw, usage, headers
    return asyncio.run(go())


def test_a_stream_asks_for_usage_and_keeps_the_final_frame_past_later_deltas(upstream):
    upstream.on("POST", "/chat/completions", sse_response(delta("hi"), {"choices": [], "usage": USAGE}, delta("")))

    status, _obj, raw, usage, _h = _call({"model": "m", "stream": True, "stream_options": {"x": 1}, "messages": []})

    assert status == 200 and b"hi" in raw
    assert upstream.sent("/chat/completions")[0]["stream_options"] == {"x": 1, "include_usage": True}
    assert (usage["prompt_tokens"], usage["completion_tokens"], usage["cached_tokens"]) == (100, 7, 40)
    assert usage["stream_status"] == "complete"


def test_a_stream_without_done_or_with_malformed_json_is_failed(upstream):
    upstream.on("POST", "/chat/completions",
                sse_response(delta("cut"), done=False),
                httpx.Response(200, content=b"data: {not json\n\n"))

    _s, _o, _raw, usage, _h = _call({"model": "m", "stream": True, "messages": []})
    assert usage["stream_status"] == "failed"

    with pytest.raises(ValueError, match="malformed SSE"):
        _call({"model": "m", "stream": True, "messages": []})


def test_a_malformed_success_keeps_the_billed_usage_and_is_not_retryable(upstream):
    upstream.on("POST", "/chat/completions", httpx.Response(200, json={"choices": "nope", "usage": USAGE}))

    status, obj, _raw, usage, _h = _call({"model": "m", "messages": []})

    assert status == 502 and obj["error"]["retryable"] is False
    assert usage["failure_phase"] == "response_received" and usage["prompt_tokens"] == 100


@pytest.mark.parametrize("exc,status,phase", [
    (httpx.ConnectError("refused"), 502, "pre_generation"),
    (httpx.ConnectTimeout("slow"), 504, "pre_generation"),
    (httpx.ReadTimeout("slow"), 504, "request_sent"),
    (httpx.RemoteProtocolError("reset"), 502, "request_sent"),
])
def test_transport_failures_say_whether_the_upstream_may_have_started(upstream, exc, status, phase):
    upstream.on("POST", "/chat/completions", exc)
    for stream in (False, True):
        got, obj, _raw, usage, _h = _call({"model": "m", "stream": stream, "messages": []})
        assert (got, usage["failure_phase"]) == (status, phase)
        # Only a request that never reached the upstream is safe to resend.
        assert obj["error"]["retryable"] is (phase == "pre_generation")


def test_error_bodies_keep_their_status_and_rate_headers_but_never_html(upstream):
    upstream.on("POST", "/chat/completions",
                httpx.Response(413, text="<html><body>Request Entity Too Large</body></html>"),
                httpx.Response(429, json={"error": {"message": "slow down"}},
                               headers={"retry-after": "7", "x-ratelimit-reset-requests": "6m0s",
                                        "authorization": "Bearer leaked-token-123"}))

    status, obj, *_ = _call({"model": "m", "messages": []})
    assert (status, obj["error"]["message"]) == (413, "upstream returned HTTP 413")

    status, obj, _raw, _usage, headers = _call({"model": "m", "stream": True, "messages": []})
    assert status == 429 and headers == {"Retry-After": "7"}
    assert obj["_fuse_headers"] == {"x-ratelimit-reset-requests": "6m0s"}


# -- /v1 stream tap ---------------------------------------------------------

def _chunks(*items):
    async def gen():
        for item in items:
            if isinstance(item, Exception):
                raise item
            yield item
    return gen()


def _tap(stream, uid, sid, key_id=None):
    return asyncio.run(drain(api._tap(stream, uid, sid, None, "m-up", key_id=key_id, provider="openai",
                                      requested_model="m")))


@pytest.fixture
def owner(fresh_db):
    uid = quick_user()
    return uid, source("openai")


def test_the_tap_records_the_final_usage_frame_once_for_the_key(owner):
    uid, sid = owner
    db.execute("INSERT INTO api_keys (user_id, name, prefix, key_hash, created_at) VALUES (?,?,?,?,?)",
               (uid, "k", "sk-x", "h" * 64, db.now()))
    key_id = int(db.fetchone("SELECT id FROM api_keys WHERE user_id=?", (uid,))["id"])
    body = sse(delta("a"), {"choices": [], "usage": USAGE}, delta(""))

    out = _tap(_chunks(body[:17], body[17:]), uid, sid, key_id)

    assert out == body
    (row,) = events()
    assert (row["prompt_tokens"], row["completion_tokens"], row["cached_tokens"]) == (100, 7, 40)
    assert (row["usage_status"], row["request_status"], row["api_key_id"]) == ("complete", "success", key_id)
    assert (row["model"], row["requested_model"]) == ("m-up", "m")


def test_a_stream_without_usage_is_recorded_as_unknown_not_as_free(owner):
    uid, sid = owner
    db.save_model_price("openai", "m-up", 1_000_000, None, 2_000_000, None)
    _tap(_chunks(sse(delta("a"))), uid, sid)
    (row,) = events()
    assert (row["usage_status"], row["total_tokens"], row["request_status"]) == ("unknown", 0, "success")

    # The model has a price, but nothing says what this request used.
    (model,) = stats.usage_analytics(uid, range_id="24h")["models"]
    assert (model["requests"], model["cost_known"]) == (1, False)
    for _ in range(2):  # recorded, then repriced
        hour = db.fetchone("SELECT priced, priced_tokens, unpriced_events, cost FROM usage_rollup_hourly")
        assert (hour["priced"], hour["priced_tokens"], hour["unpriced_events"], hour["cost"]) == (0, 0, 1, 0)
        db.save_model_price("openai", "m-up", 3_000_000, None, 6_000_000, None)


def test_a_stream_cut_before_done_gets_an_error_frame_and_a_failed_row(owner):
    uid, sid = owner
    out = _tap(_chunks(sse(delta("half"), {"choices": [], "usage": USAGE}, done=False)), uid, sid)

    error = frames_of(out)[-1]["error"]
    assert error["message"] == "upstream stream ended before completion" and out.endswith(b"data: [DONE]\n\n")
    (row,) = events()
    # Tokens arrived, so they are known even though the request failed.
    assert (row["request_status"], row["usage_status"], row["prompt_tokens"]) == ("failed", "complete", 100)


def test_a_broken_stream_ends_with_an_error_frame(owner):
    uid, sid = owner
    out = _tap(_chunks(sse(delta("x"), done=False), httpx.ReadError("gone")), uid, sid)
    assert frames_of(out)[-1]["error"]["message"] == "upstream stream failed (ReadError)"
    assert events()[0]["request_status"] == "failed"


def test_inline_error_frames_are_scrubbed_and_an_oversized_one_is_dropped(owner):
    uid, sid = owner
    leak = {"error": {"message": "bad key sk-abcdefghijklmnop for Bearer abcdefghijklmn"}}
    out = _tap(_chunks(sse(leak)), uid, sid)
    assert b"sk-abcdefghijklmnop" not in out and b"abcdefghijklmn\"" not in out
    assert frames_of(out)[0]["error"]["message"] == "bad key sk-*** for Bearer ***"

    huge = b'data: {"error": {"message": "' + b"x" * (api._LINE_CAP + 10)
    out = _tap(_chunks(huge, b'"}}\n\n', sse({"choices": [], "usage": USAGE})), uid, sid)
    assert b"xxxx" not in out
    assert [f.get("error", {}).get("message") for f in frames_of(out)][0] == "upstream error frame too large"
    assert [r["request_status"] for r in events()] == ["failed", "failed"]


def test_a_client_disconnect_still_records_what_was_used_as_cancelled(owner):
    uid, sid = owner
    first_chunk = asyncio.Event()

    async def held():
        yield sse({"choices": [], "usage": USAGE}, done=False)
        await asyncio.Event().wait()

    async def go():
        async def consume():
            async for _chunk in api._tap(held(), uid, sid, None, "m-up", provider="openai"):
                first_chunk.set()
        task = asyncio.create_task(consume())
        await first_chunk.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    row = wait_until(lambda: (events() or [None])[0])
    assert (row["request_status"], row["prompt_tokens"]) == ("cancelled", 100)
    assert json.loads(json.dumps(row["usage_status"])) == "complete"


# -- failover on the OpenAI-compatible source ----------------------------------

def test_a_request_that_may_have_generated_is_never_replayed_on_another_key(fresh_db, upstream):
    from app.sources import openai_compat
    from ._support import key

    sid = source("openai", {"base_url": "https://up.test"})
    for i in range(3):
        key(sid, f"sk-key-{i}")
    creds = db.fetchall("SELECT * FROM credentials WHERE source_id=? ORDER BY id", (sid,))
    src = openai_compat.OpenAICompatSource({"id": sid, "name": "o"}, {"base_url": "https://up.test"}, creds)
    upstream.on("POST", "/chat/completions",
                httpx.Response(200, json={"choices": [], "usage": USAGE}),
                httpx.ReadTimeout("slow"),
                httpx.Response(500, json={"error": {"message": "boom"}}))
    chat = lambda: asyncio.run(src.chat({"model": "m", "messages": []}))

    malformed = chat()
    assert (malformed.status, len(upstream.requests)) == (502, 1)
    assert malformed.usage["prompt_tokens"] == 100  # billed tokens are still accounted
    timed_out = chat()
    assert (timed_out.status, len(upstream.requests)) == (504, 2)
    # A 5xx arrived before any output: it fails over, but to at most two keys.
    failed = chat()
    assert failed.status == 500 and len(upstream.requests) == 4


def test_completions_reuse_one_client_per_event_loop(upstream):
    upstream.on("POST", "/chat/completions", httpx.Response(200, json=completion()))

    async def two_calls():
        clients = []
        for _ in range(2):
            client = http_pool.client(httpx.Timeout(300.0, connect=15.0))
            status, *_rest = await openai_chat(client, URL, {}, {"model": "m", "messages": []})
            assert status == 200
            clients.append(client)
        return clients

    first, second = asyncio.run(two_calls())
    assert first is second and not first.is_closed
    # A new loop (each test, or a restarted app) never inherits another loop's client.
    assert asyncio.run(two_calls())[0] is not first
    assert len(upstream.requests) == 4
