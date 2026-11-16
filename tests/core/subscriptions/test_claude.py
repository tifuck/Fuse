"""Claude subscription: the Claude Code shaped Messages payload (identity line,
cache breakpoints, tool id mapping, images, signed thinking replay, effort
and thinking per model), SSE translation incl. CRLF framing and stop reasons,
message threads (send only the new turn, replay on refusal), and errors."""
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.sources import claude, claude_catalog, claude_payload, pool_policy
from tests.core.subscriptions._support import (
    account, drain, extra_of, finish_of, http, iso, reasoning_of, run, set_extra, sse, text_of, tool_calls_of,
    usage_of, user,
)

IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."


def anthropic(*events, crlf=False):
    text = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return text.replace("\n", "\r\n") if crlf else text


def message(text="ok", msg_id="msg_1", stop="end_turn", usage=None):
    return [
        {"type": "message_start", "message": {"id": msg_id, "usage": usage or {"input_tokens": 10, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 3}},
        {"type": "message_stop"},
    ]


def ok(*events, chunks=None):
    body = anthropic(*(events or message()))
    return httpx.Response(200, content=sse(chunks(body) if chunks else [body]), headers={"content-type": "text/event-stream"})


@pytest.fixture
def pool(fresh_db):
    owner = user()
    cred = account("claude", owner, {"auth_kind": "oauth", "access_token": "at-1", "refresh_token": "rt-1",
                                     "expires_at": iso(8 * 3600)})
    source = claude.ClaudeSource({"id": pool_policy.source_id("claude"), "name": "Claude"}, {}, [])
    with patch.object(claude, "refresh_due", new_callable=AsyncMock), \
            patch.object(claude, "refresh_pool_quota", new_callable=AsyncMock):
        yield owner, cred, source


def chat(pool, body, **kwargs):
    async def go():
        owner, _cred, source = pool
        result = await source.chat({"model": "claude/claude-sonnet-5-5", "stream": True, **body}, user_id=owner, **kwargs)
        return result, (await drain(result) if result.stream else [])
    return run(go())


def build(body, model="claude-sonnet-5-5"):
    return claude_payload.build_messages_payload(body, model)


def test_payload_is_claude_code_shaped_with_tools_images_and_paired_foreign_tool_ids(pool, monkeypatch):
    foreign = "functions.grep:0"
    body = {"messages": [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": [{"type": "text", "text": "look"},
                                     {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
                                     {"type": "image_url", "image_url": {"url": "data:text/html;base64,PGI+"}}]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": foreign, "type": "function", "function": {"name": "grep", "arguments": '{"pattern":"x"}'}}]},
        {"role": "tool", "tool_call_id": foreign, "content": "hit"},
    ], "tools": [{"type": "function", "function": {"name": "grep", "parameters": {"type": "object"}}}],
        "temperature": 0.5, "stop": "END"}
    with http(lambda request: ok(), monkeypatch) as calls:
        result, _frames = chat(pool, body)

    assert result.status == 200
    request = calls.requests[0]
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["Authorization"] == "Bearer at-1"
    assert request.headers["anthropic-beta"].startswith("claude-code-20250219,oauth-2025-04-20")
    payload = json.loads(request.content)
    assert payload["model"] == "claude-sonnet-5-5" and payload["max_tokens"] == 128_000
    assert [b["text"] for b in payload["system"]] == [IDENTITY, "be terse"]
    assert payload["system"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert payload["tools"][0]["eager_input_streaming"] is True and "cache_control" in payload["tools"][-1]
    user_blocks = payload["messages"][0]["content"]
    assert [b["type"] for b in user_blocks] == ["text", "image"]  # a non-image data URL is dropped
    assert user_blocks[1]["source"] == {"type": "base64", "media_type": "image/png", "data": "AAA"}
    use = payload["messages"][1]["content"][0]
    result_block = payload["messages"][2]["content"][0]
    assert use["id"] == result_block["tool_use_id"] and use["id"].startswith("toolu_")
    assert use["input"] == {"pattern": "x"}
    assert result_block.get("cache_control")  # the rolling breakpoint is on the newest block
    # Effort models reject sampling; stop sequences still go.
    assert "temperature" not in payload and payload["stop_sequences"] == ["END"]


def test_signed_thinking_replays_in_received_order_and_unsigned_text_is_dropped(fresh_db):
    msg = {"role": "assistant", "content": "answer", "tool_calls": [
        {"id": "t1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
        {"id": "t2", "type": "function", "function": {"name": "b", "arguments": "{}"}}],
        "claude_thinking": [
            {"thinking": "first", "signature": "s1", "tool_index": 0},
            {"thinking": "", "signature": "s2", "tool_index": 1, "after_text": True},
            {"redacted_thinking": "opaque", "tool_index": 2},
            {"thinking": "unsigned", "tool_index": 2}]}
    blocks = build({"messages": [{"role": "user", "content": "go"}, msg]})["messages"][1]["content"]

    assert [(b["type"], b.get("id") or b.get("signature") or b.get("data") or b.get("text")) for b in blocks] == [
        ("thinking", "s1"), ("text", "answer"), ("tool_use", "t1"), ("thinking", "s2"),
        ("tool_use", "t2"), ("redacted_thinking", "opaque")]


@pytest.mark.parametrize("model,effort,output,thinking", [
    ("claude-sonnet-5-5", "", "medium", {"type": "adaptive", "display": "summarized"}),
    ("claude-sonnet-5-5", "minimal", "low", {"type": "adaptive", "display": "summarized"}),
    ("claude-sonnet-5-5", "none", "low", {"type": "between_tools"}),
    ("claude-sonnet-5", "none", "low", {"type": "disabled"}),
    # Always-thinking models cannot turn thinking off.
    ("claude-opus-5-5", "none", "low", {"type": "adaptive", "display": "summarized"}),
    # Haiku 4.5 rejects the effort parameter entirely.
    ("claude-haiku-4-5-20251001", "high", None, None),
])
def test_effort_and_thinking_per_model(fresh_db, model, effort, output, thinking):
    payload = build({"messages": [{"role": "user", "content": "hi"}], "reasoning_effort": effort or None}, model)
    assert payload.get("output_config", {}).get("effort") == output
    assert payload.get("thinking") == thinking


def test_forced_tool_choice_is_demoted_where_the_model_rejects_it(fresh_db):
    body = {"messages": [{"role": "user", "content": "hi"}], "tool_choice": "required", "parallel_tool_calls": False,
            "tools": [{"type": "function", "function": {"name": "a"}}]}
    assert build(body, "claude-opus-5-5")["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert build(body, "claude-sonnet-5")["tool_choice"] == {"type": "any", "disable_parallel_tool_use": True}


def test_the_stream_translates_thinking_text_tools_and_cache_usage_across_crlf_splits(pool, monkeypatch):
    events = [
        {"type": "message_start", "message": {"id": "msg_9", "usage": {"input_tokens": 40, "cache_read_input_tokens": 900,
                                                                       "cache_creation_input_tokens": 60}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hmm"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "SIG"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "ping"},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Reading"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "read"}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"path":'}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '"a"}'}},
        {"type": "content_block_stop", "index": 2},
        {"type": "content_block_start", "index": 3, "content_block": {"type": "tool_use", "id": "toolu_2", "name": "now"}},
        {"type": "content_block_stop", "index": 3},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 25}},
        {"type": "message_stop"},
    ]

    def every_7_bytes(body):
        return [body[i:i + 7] for i in range(0, len(body), 7)]

    raw = anthropic(*events, crlf=True)
    with http(lambda request: httpx.Response(200, content=sse(every_7_bytes(raw))), monkeypatch):
        result, frames = chat(pool, {"messages": [{"role": "user", "content": "go"}]})

    assert (text_of(frames), reasoning_of(frames), finish_of(frames)) == ("Reading", "hmm", ["tool_calls"])
    assert tool_calls_of(frames) == [{"id": "toolu_1", "name": "read", "arguments": '{"path":"a"}'},
                                     {"id": "toolu_2", "name": "now", "arguments": "{}"}]
    final = [f for f in frames if f.get("choices") and f["choices"][0]["finish_reason"]][0]
    assert final["choices"][0]["delta"]["claude_thinking"] == [{"thinking": "hmm", "signature": "SIG", "tool_index": 0}]
    assert usage_of(frames) == {"prompt_tokens": 1000, "completion_tokens": 25, "total_tokens": 1025,
                                "prompt_tokens_details": {"cached_tokens": 900, "cache_write_tokens": 60},
                                "completion_tokens_details": {"reasoning_tokens": 0}}
    assert result.stream.usage["stream_status"] == "complete"


@pytest.mark.parametrize("stop,finish", [("max_tokens", "length"), ("model_context_window_exceeded", "length"),
                                         ("refusal", "content_filter"), ("end_turn", "stop")])
def test_stop_reasons_map_to_openai_finish_reasons(pool, monkeypatch, stop, finish):
    with http(lambda request: ok(*message(stop=stop)), monkeypatch):
        _result, frames = chat(pool, {"messages": [{"role": "user", "content": "go"}]})
    assert finish_of(frames) == [finish]


def test_a_thread_sends_only_the_new_turn_and_anything_it_does_not_hold_creates_a_new_one(pool, monkeypatch):
    turn1 = [{"role": "user", "content": "hi"}]
    turn2 = turn1 + [{"role": "assistant", "content": "ok"}, {"role": "user", "content": "more"}]
    edited = [{"role": "user", "content": "HI"}, {"role": "assistant", "content": "ok"}, {"role": "user", "content": "more"}]
    replies = iter(["ok", "fine", "ok", "sure"])
    with http(lambda request: ok(*message(next(replies), msg_id=f"msg_{len(calls.requests)}")), monkeypatch) as calls:
        for messages in (turn1, turn2):
            chat(pool, {"messages": messages}, session_id="sess")
        claude.claude_thread.reset()
        chat(pool, {"messages": turn1}, session_id="sess")
        chat(pool, {"messages": edited}, session_id="sess")

    sent = [json.loads(r.content) for r in calls.requests]
    assert sent[0]["thread"] == {"type": "create"} and len(sent[0]["messages"]) == 1
    assert sent[1]["thread"] == {"type": "continue", "previous_message_id": "msg_1"}
    assert [m["content"][0]["text"] for m in sent[1]["messages"]] == ["more"]
    assert "system" not in sent[1] and "tools" not in sent[1]
    assert "message-threads" in calls.requests[1].headers["anthropic-beta"]
    assert sent[3]["thread"] == {"type": "create"} and len(sent[3]["messages"]) == 3


def test_a_lost_thread_replays_in_full_and_an_unsupported_one_falls_back_to_plain(pool, monkeypatch):
    turn1 = [{"role": "user", "content": "hi"}]
    turn2 = turn1 + [{"role": "assistant", "content": "ok"}, {"role": "user", "content": "more"}]
    lost = httpx.Response(404, json={"error": {"type": "not_found_error", "message": "x", "details": {"error_code": "thread_not_found"}}})
    with http([ok(), lost, ok(*message("fine", msg_id="msg_2"))], monkeypatch) as calls:
        chat(pool, {"messages": turn1}, session_id="sess")
        result, _frames = chat(pool, {"messages": turn2}, session_id="sess")
    sent = [json.loads(r.content) for r in calls.requests]
    assert result.status == 200
    assert [s["thread"]["type"] for s in sent] == ["create", "continue", "create"] and len(sent[2]["messages"]) == 3

    claude.claude_thread.reset()
    refused = httpx.Response(400, json={"error": {"message": "x", "details": {"error_code": "thread_unsupported_request"}}})
    with http([refused, ok(), ok()], monkeypatch) as calls:
        chat(pool, {"messages": turn1}, session_id="other")
        chat(pool, {"messages": turn1}, session_id="other2")
    assert ["thread" in json.loads(r.content) for r in calls.requests] == [True, False, False]


def test_a_rejected_signature_is_surfaced_without_rewriting_history_and_a_429_fails_over(pool, monkeypatch):
    owner, cred, _source = pool
    bad = httpx.Response(400, json={"error": {"message": "Invalid `signature` in `thinking` block"}})
    with http([bad], monkeypatch) as calls:
        result, _frames = chat(pool, {"messages": [{"role": "user", "content": "go"}]})
    assert result.status == 400 and "Start a new conversation" in result.body["error"]["message"]
    assert len(calls.requests) == 1

    other = account("claude", owner, {"auth_kind": "oauth", "access_token": "at-2", "refresh_token": "rt-2",
                                      "expires_at": iso(8 * 3600)})
    limited = httpx.Response(429, json={"error": {"type": "rate_limit_error", "message": "slow"}}, headers={"retry-after": "30"})
    holds = []
    with patch.object(claude, "note_failure", side_effect=lambda cid, **kw: holds.append((cid, kw))), \
            http([limited, ok()], monkeypatch):
        result, _frames = chat(pool, {"messages": [{"role": "user", "content": "go"}]})
    assert result.status == 200 and {result.credential_id, holds[0][0]} == {cred, other}
    assert holds[0][1]["limit"] is True


def test_a_401_refreshes_once_and_retries(pool, monkeypatch):
    _owner, cred, _source = pool

    async def rotated(c):
        set_extra(c["id"], {**extra_of(c["id"]), "access_token": "at-new"})
        return True

    with patch.object(claude, "refresh_one", side_effect=rotated) as refresh, \
            http([httpx.Response(401, json={"error": {"message": "expired"}}), ok()], monkeypatch) as calls:
        result, _frames = chat(pool, {"messages": [{"role": "user", "content": "go"}]})
    assert (result.status, result.credential_id, refresh.await_count) == (200, cred, 1)
    assert [r.headers["Authorization"] for r in calls.requests] == ["Bearer at-1", "Bearer at-new"]


def test_catalog_keeps_first_party_models_and_earlier_ids_after_a_partial_fetch(fresh_db, monkeypatch):
    def cat(ids):
        return {"surfaces": {"cc": {"model_selector_config": [{"models": [
            {"id": i, "name": i, "offered_on": ["first_party"], "runtime": {"effort_levels": ["low", "high"],
                                                                         "max_output_tokens": 64000}} for i in ids]
            + [{"id": "claude-bedrock-only", "offered_on": ["bedrock"]}]}]}}}

    with http(lambda request: httpx.Response(200, json=cat(["claude-opus-6", "claude-sonnet-5-5"])), monkeypatch):
        names = run(claude.fetch_catalog())
    assert names[:2] == ["claude/claude-opus-6", "claude/claude-sonnet-5-5"]
    assert "claude/claude-bedrock-only" not in names and set(claude.CLAUDE_MODELS) <= set(names)
    assert claude.efforts_for("claude/claude-opus-6") == ("low", "high")

    monkeypatch.setattr(claude_catalog, "_models_cache", (0.0, names))
    with http(lambda request: httpx.Response(200, json=cat(["claude-sonnet-5-5"])), monkeypatch):
        later = run(claude.fetch_catalog())
    assert later[0] == "claude/claude-sonnet-5-5" and "claude/claude-opus-6" in later
    assert claude.efforts_for("claude/claude-opus-6") == ("low", "high")
