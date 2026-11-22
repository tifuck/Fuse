"""ChatGPT (Codex Responses): request translation, stream translation into
OpenAI chunks, encrypted reasoning replayed only to its own account, retries
that drop rejected parameters, error mapping, token refresh, Codex turn
state, and the paid/free catalog read."""
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.sources import chatgpt, chatgpt_catalog, pool_pick, pool_policy, pool_session
from tests.core.subscriptions._support import (
    account, drain, events, extra_of, finish_of, http, iso, reasoning_of, run, set_extra, sse, text_of,
    tool_calls_of, usage_of, user,
)

COMPLETED = {"type": "response.completed", "response": {"id": "r1", "usage": {
    "input_tokens": 1200, "output_tokens": 40, "total_tokens": 1240,
    "input_tokens_details": {"cached_tokens": 1024}, "output_tokens_details": {"reasoning_tokens": 12}}}}


def ok_stream(*objs, headers=None):
    body = list(objs) or [{"type": "response.output_text.delta", "delta": "ok"}, COMPLETED]
    return httpx.Response(200, content=sse([events(body)]), headers={"content-type": "text/event-stream", **(headers or {})})


def linked(plan="plus", **more):
    now = chatgpt.db.now()
    return {"access_token": "at-1", "refresh_token": "rt-1", "expires_at": iso(3600), "account_id": "acct-1",
            "billing": {"plan_type": plan, "fetched_at": now, "checked_at": now, "windows": []}, **more}


@pytest.fixture
def pool(fresh_db, monkeypatch):
    owner = user()
    source = chatgpt.ChatGPTSource({"id": pool_policy.source_id("chatgpt"), "name": "ChatGPT"}, {}, [])
    chatgpt_catalog.parse_catalog_payload({"models": [
        {"slug": "gpt-6.1-sol", "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}, {"effort": "high"}],
         "default_reasoning_level": "medium", "service_tiers": [{"id": "priority"}]},
    ]})
    with patch.object(chatgpt, "refresh_due", new_callable=AsyncMock), \
            patch.object(chatgpt, "refresh_pool_usage", new_callable=AsyncMock):
        yield owner, source


def chat(pool, body, **kwargs):
    owner, source = pool
    return run(source.chat({"model": "gpt-6.1-sol", "stream": True, **body}, user_id=owner, **kwargs))


def chat_frames(pool, body, **kwargs):
    async def go():
        owner, source = pool
        result = await source.chat({"model": "gpt-6.1-sol", "stream": True, **body}, user_id=owner, **kwargs)
        return result, (await drain(result) if result.stream else [])
    return run(go())


HISTORY = [
    {"role": "system", "content": "be terse"},
    {"role": "user", "content": [{"type": "text", "text": "look"},
                                 {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA", "detail": "low"}}]},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "grep", "arguments": {"pattern": "x"}}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": "hit"},
]
TOOLS = [{"type": "function", "function": {"name": "grep", "description": "search",
                                           "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}}}}]


def test_a_chat_body_becomes_a_codex_responses_request(pool, monkeypatch):
    owner, _source = pool
    account("chatgpt", owner, linked())
    with http(lambda request: ok_stream(), monkeypatch) as calls:
        result, _frames = chat_frames(pool, {"model": "gpt-6.1-sol-fast", "messages": HISTORY, "tools": TOOLS,
                                             "tool_choice": "required", "temperature": 0.2, "max_tokens": 50,
                                             "prompt_cache_key": "raw-client-key", "reasoning_effort": "xhigh"},
                                      session_id="scoped-session")

    assert result.status == 200
    request = calls.requests[0]
    assert str(request.url) == "https://chatgpt.com/backend-api/codex/responses"
    assert (request.headers["ChatGPT-Account-Id"], request.headers["session-id"]) == ("acct-1", "scoped-session")
    payload = json.loads(request.content)
    assert (payload["model"], payload["service_tier"], payload["store"]) == ("gpt-6.1-sol", "priority", False)
    assert payload["prompt_cache_key"] == "scoped-session"
    assert payload["instructions"] == "be terse"
    assert payload["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "look"},
                                     {"type": "input_image", "image_url": "data:image/png;base64,AAA", "detail": "low"}]},
        {"type": "function_call", "call_id": "call_1", "name": "grep", "arguments": '{"pattern": "x"}'},
        {"type": "function_call_output", "call_id": "call_1", "output": "hit"},
    ]
    assert payload["tools"][0]["strict"] is False and payload["tool_choice"] == "required"
    # Codex rejects Chat Completions sampling fields; the effort maps to the nearest listed level.
    assert "temperature" not in payload and "max_output_tokens" not in payload and "max_tokens" not in payload
    assert payload["reasoning"] == {"effort": "high", "summary": "auto"}


def test_effort_off_sends_no_effort_where_the_model_lists_no_none_level(pool, monkeypatch):
    owner, _source = pool
    account("chatgpt", owner, linked())
    with http(lambda request: ok_stream(), monkeypatch) as calls:
        chat_frames(pool, {"messages": HISTORY[:2], "reasoning_effort": "none"})
    assert "reasoning" not in json.loads(calls.requests[0].content)


@pytest.mark.parametrize("body,reasoning", [
    ({"reasoning": {"effort": "none"}}, None),
    ({"reasoning": {"effort": "none"}, "reasoning_effort": "none"}, None),
    ({"reasoning": {"effort": "xhigh", "summary": "detailed"}}, {"effort": "high", "summary": "detailed"}),
])
def test_a_client_reasoning_effort_maps_to_a_listed_level(pool, monkeypatch, body, reasoning):
    owner, _source = pool
    account("chatgpt", owner, linked())
    with http(lambda request: ok_stream(), monkeypatch) as calls:
        chat_frames(pool, {"messages": HISTORY[:2], **body})
    assert json.loads(calls.requests[0].content).get("reasoning") == reasoning


def test_images_in_tool_results_follow_the_outputs_in_a_user_item_of_the_same_turn(pool, monkeypatch):
    owner, _source = pool
    account("chatgpt", owner, linked())
    turn = [{"role": "user", "content": "look"}]
    shots = turn + [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "shot", "arguments": "{}"}},
            {"id": "c2", "type": "function", "function": {"name": "shot", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": [
            {"type": "text", "text": "one"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}]},
        {"role": "tool", "tool_call_id": "c2", "content": [{"type": "image_url", "image_url": "data:image/png;base64,BBB"}]},
    ]
    with http(lambda request: ok_stream(headers={"x-codex-turn-state": "ts-1"}), monkeypatch) as calls:
        chat_frames(pool, {"messages": turn}, session_id="sess")
        chat_frames(pool, {"messages": shots}, session_id="sess")
    items = json.loads(calls.requests[1].content)["input"]
    assert items[-3:] == [
        {"type": "function_call_output", "call_id": "c1", "output": "one"},
        {"type": "function_call_output", "call_id": "c2", "output": ""},
        {"role": "user", "content": [
            {"type": "input_text", "text": "[Fuse: images from tool result c1]"},
            {"type": "input_image", "image_url": "data:image/png;base64,AAA"},
            {"type": "input_text", "text": "[Fuse: images from tool result c2]"},
            {"type": "input_image", "image_url": "data:image/png;base64,BBB"}]},
    ]
    # The image item is part of the turn: the turn state is echoed.
    assert calls.requests[1].headers.get("x-codex-turn-state") == "ts-1"


def test_a_client_disconnect_is_cancelled_and_neither_cools_nor_pins_the_account(pool, monkeypatch):
    owner, source = pool
    cred = account("chatgpt", owner, linked())

    async def go():
        result = await source.chat({"model": "gpt-6.1-sol", "stream": True, "messages": HISTORY[:2]},
                                   user_id=owner, session_id="sess")
        await result.stream.__anext__()
        await result.stream.aclose()
        return result

    with http(lambda request: ok_stream(), monkeypatch):
        result = run(go())
    assert result.stream.usage["stream_status"] == "cancelled"
    assert not pool_pick.cooling_down(cred) and pool_session.lookup("chatgpt", "sess") is None


def test_the_stream_translates_text_reasoning_tool_calls_and_cached_usage(pool, monkeypatch):
    owner, _source = pool
    cred = account("chatgpt", owner, linked())
    upstream = [
        {"type": "response.created", "response": {"id": "r1"}},
        {"type": "response.output_item.added", "output_index": 0, "item": {"type": "reasoning", "id": "rs1"}},
        {"type": "response.reasoning_summary_text.delta", "delta": "plan"},
        {"type": "response.reasoning_summary_part.added", "summary_index": 1},
        {"type": "response.reasoning_summary_text.delta", "delta": "more"},
        {"type": "response.output_item.done", "output_index": 0,
         "item": {"type": "reasoning", "encrypted_content": "ENC", "summary": [{"text": "plan"}]}},
        {"type": "response.output_item.added", "output_index": 1, "item": {"type": "message", "phase": "commentary"}},
        {"type": "response.output_text.delta", "delta": "Looking"},
        # Two calls reusing one item id, one whose arguments arrive only when done,
        # and a zero-argument call.
        {"type": "response.output_item.added", "output_index": 2, "item": {"type": "function_call", "id": "fc", "call_id": "c1", "name": "grep"}},
        {"type": "response.function_call_arguments.delta", "output_index": 2, "item_id": "fc", "delta": '{"pattern":'},
        {"type": "response.output_item.added", "output_index": 3, "item": {"type": "function_call", "id": "fc", "call_id": "c2", "name": "read"}},
        {"type": "response.function_call_arguments.delta", "output_index": 2, "item_id": "fc", "delta": '"x"}'},
        {"type": "response.output_item.done", "output_index": 3,
         "item": {"type": "function_call", "id": "fc", "call_id": "c2", "name": "read", "arguments": '{"path":"a"}'}},
        {"type": "response.output_item.added", "output_index": 4, "item": {"type": "function_call", "id": "fz", "call_id": "c3", "name": "now"}},
        {"type": "response.function_call_arguments.done", "output_index": 4, "item_id": "fz", "arguments": ""},
        COMPLETED,
    ]
    with http(lambda request: ok_stream(*upstream), monkeypatch):
        result, frames = chat_frames(pool, {"messages": HISTORY[:2]})

    assert (text_of(frames), reasoning_of(frames)) == ("Looking", "plan\n\nmore")
    assert tool_calls_of(frames) == [{"id": "c1", "name": "grep", "arguments": '{"pattern":"x"}'},
                                     {"id": "c2", "name": "read", "arguments": '{"path":"a"}'},
                                     {"id": "c3", "name": "now", "arguments": "{}"}]
    assert finish_of(frames) == ["tool_calls"]
    thinking = [b for f in frames for c in f.get("choices") or [] for b in c["delta"].get("claude_thinking") or []]
    assert thinking == [{"openai_reasoning": {"encrypted_content": "ENC", "summary": [{"type": "summary_text", "text": "plan"}],
                                              "account": cred, "phase": "commentary"}}]
    assert usage_of(frames) == {"prompt_tokens": 1200, "completion_tokens": 40, "total_tokens": 1240,
                                "prompt_tokens_details": {"cached_tokens": 1024, "cache_write_tokens": 0},
                                "completion_tokens_details": {"reasoning_tokens": 12}}
    assert result.stream.usage["stream_status"] == "complete"


def test_an_output_limit_cut_ends_with_length_and_a_cut_stream_fails(pool, monkeypatch):
    owner, _source = pool
    account("chatgpt", owner, linked())
    cut = {"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"},
                                                        "usage": {"input_tokens": 5, "output_tokens": 9}}}
    with http(lambda request: ok_stream({"type": "response.output_text.delta", "delta": "part"}, cut), monkeypatch):
        _result, frames = chat_frames(pool, {"messages": HISTORY[:2]})
    assert (text_of(frames), finish_of(frames)) == ("part", ["length"])

    with http(lambda request: ok_stream({"type": "response.output_text.delta", "delta": "part"}), monkeypatch):
        result, frames = chat_frames(pool, {"messages": HISTORY[:2]})
    assert frames[-1]["error"]["message"] == "upstream stream ended before a terminal response"
    assert result.stream.usage["stream_status"] == "failed"


def _with_reasoning(account_id):
    return HISTORY[:2] + [{"role": "assistant", "content": "ok", "claude_thinking": [
        {"openai_reasoning": {"encrypted_content": "ENC", "summary": [], "account": account_id}}]},
        {"role": "user", "content": "go on"}]


def test_encrypted_reasoning_goes_only_to_its_account_and_an_undecryptable_one_is_dropped(pool, monkeypatch):
    owner, _source = pool
    cred = account("chatgpt", owner, linked())
    with http(lambda request: ok_stream(), monkeypatch) as calls:
        chat_frames(pool, {"messages": _with_reasoning(cred)})
        chat_frames(pool, {"messages": _with_reasoning(cred + 999)})
    first, other = (json.loads(r.content)["input"] for r in calls.requests)
    assert {"type": "reasoning", "summary": [], "encrypted_content": "ENC"} in first
    assert not any(item.get("type") == "reasoning" for item in other)

    answers = [httpx.Response(400, json={"error": {"message": "The encrypted content could not be verified."}}), ok_stream()]
    with http(answers, monkeypatch) as calls:
        result, _frames = chat_frames(pool, {"messages": _with_reasoning(cred)})
    assert result.status == 200 and result.credential_id == cred
    assert [any(i.get("type") == "reasoning" for i in json.loads(r.content)["input"]) for r in calls.requests] == [True, False]


@pytest.mark.parametrize("detail", [
    "Unsupported parameter: parallel_tool_calls",
    "Error: Unsupported parameter: parallel_tool_calls",
])
def test_a_rejected_parameter_is_dropped_and_the_request_retried(pool, monkeypatch, detail):
    owner, _source = pool
    cred = account("chatgpt", owner, linked())
    answers = [httpx.Response(400, json={"detail": detail}), ok_stream()]
    with http(answers, monkeypatch) as calls:
        result, _frames = chat_frames(pool, {"messages": HISTORY[:2], "parallel_tool_calls": False})
    assert (result.status, result.credential_id) == (200, cred)
    assert ["parallel_tool_calls" in json.loads(r.content) for r in calls.requests] == [True, False]


def test_a_spent_plan_fails_over_but_a_bad_request_never_cools_the_account(pool, monkeypatch):
    owner, _source = pool
    spent = account("chatgpt", owner, linked())
    other = account("chatgpt", owner, linked(access_token="at-2"))
    holds = []
    limit = {"error": {"type": "usage_limit_reached", "message": "limit", "resets_at": 4102444800}}
    with patch.object(chatgpt, "note_failure", side_effect=lambda cid, **kw: holds.append((cid, kw))):
        with http([httpx.Response(429, json=limit), ok_stream()], monkeypatch):
            result, _frames = chat_frames(pool, {"messages": HISTORY[:2]})
        assert result.credential_id in (spent, other) and result.status == 200
        assert len(holds) == 1 and holds[0][1]["limit"] is True and holds[0][1]["cooldown_seconds"] >= 1800

        holds.clear()
        with http(lambda request: httpx.Response(400, json={"error": {"message": "context_length_exceeded"}}), monkeypatch) as calls:
            result = chat(pool, {"messages": HISTORY[:2]})
    assert (result.status, len(calls.requests), holds) == (400, 1, [])


def test_a_non_stream_error_event_keeps_its_top_level_code_and_message(pool, monkeypatch):
    owner, source = pool
    account("chatgpt", owner, linked())
    error = {"type": "error", "code": "context_length_exceeded", "message": "Your input exceeds the context window"}
    with http(lambda request: ok_stream(error), monkeypatch) as calls:
        result = run(source.chat({"model": "gpt-6.1-sol", "stream": False, "messages": HISTORY[:2]}, user_id=owner))
    assert (result.status, len(calls.requests)) == (400, 1)
    assert result.body["error"]["message"] == "Your input exceeds the context window"


def test_a_401_refreshes_once_and_retries_with_the_new_token(pool, monkeypatch):
    owner, _source = pool
    cred = account("chatgpt", owner, linked())

    async def rotated(c):
        set_extra(c["id"], {**extra_of(c["id"]), "access_token": "at-new"})
        return True

    with patch.object(chatgpt, "refresh_one", side_effect=rotated) as refresh, \
            http([httpx.Response(401, json={"error": "expired"}), ok_stream()], monkeypatch) as calls:
        result, _frames = chat_frames(pool, {"messages": HISTORY[:2]})
    assert (result.status, result.credential_id, refresh.await_count) == (200, cred, 1)
    assert [r.headers["Authorization"] for r in calls.requests] == ["Bearer at-1", "Bearer at-new"]


def test_codex_turn_state_is_echoed_within_a_turn_and_not_into_the_next(pool, monkeypatch):
    owner, _source = pool
    account("chatgpt", owner, linked())
    turn = [{"role": "user", "content": "do it"}]
    later = turn + [{"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "grep", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "r"}]
    nxt = later + [{"role": "assistant", "content": "done"}, {"role": "user", "content": "again"}]
    with http(lambda request: ok_stream(headers={"x-codex-turn-state": "ts-1"}), monkeypatch) as calls:
        for messages in (turn, later, nxt):
            chat_frames(pool, {"messages": messages}, session_id="sess")
    assert [r.headers.get("x-codex-turn-state") for r in calls.requests] == [None, "ts-1", None]


def test_the_catalog_reads_a_paid_and_a_free_account_and_a_complete_read_drops_retired_ids(pool, monkeypatch):
    owner, source = pool
    account("chatgpt", owner, linked("plus"))
    account("chatgpt", owner, linked("free", access_token="at-free"))
    source.creds = chatgpt.db.fetchall("SELECT * FROM credentials")

    def catalog(paid, free):
        def answer(request):
            rows = paid if request.headers["Authorization"] == "Bearer at-1" else free
            return httpx.Response(200, json={"models": [{"slug": s} for s in rows]}) if rows is not None else httpx.Response(500)
        return answer

    with http(catalog(["gpt-7", "gpt-6.1-sol"], ["gpt-6-luna"]), monkeypatch):
        names = run(source.models())
    assert names == ["gpt-7", "gpt-6.1-sol", "gpt-6-luna"]  # seeds Codex no longer lists are gone
    assert chatgpt.listable_models(names, owner) == names
    assert pool_pick.chatgpt_free_model("gpt-6-luna")

    monkeypatch.setattr(chatgpt_catalog, "_models_cache", (0.0, names))
    with http(catalog(["gpt-7"], None), monkeypatch):
        # One tier failed: a partial read only adds, so nothing drops.
        assert run(source.models()) == names


@pytest.mark.parametrize("terminal,code,prompt", [
    ({"type": "response.failed", "response": {"error": {"code": "server_is_overloaded", "message": "busy"},
                                              "usage": {"input_tokens": 50, "output_tokens": 2}}},
     "server_is_overloaded", 50),
    ({"type": "error", "error": {"code": "context_length_exceeded", "message": "too long"}}, "context_length_exceeded", None),
])
def test_a_mid_stream_failure_keeps_its_code_and_the_billed_prompt(pool, monkeypatch, terminal, code, prompt):
    owner, _source = pool
    account("chatgpt", owner, linked())
    with http(lambda request: ok_stream({"type": "response.output_text.delta", "delta": "part"}, terminal), monkeypatch):
        result, frames = chat_frames(pool, {"messages": HISTORY[:2]})
    assert text_of(frames) == "part" and finish_of(frames) == []
    assert [f["error"]["code"] for f in frames if f.get("error")] == [code]
    collected = result.stream.usage
    assert collected["stream_status"] == "failed" and collected.get("prompt_tokens") == prompt
    if prompt:
        assert collected["usage_status"] == "partial"
