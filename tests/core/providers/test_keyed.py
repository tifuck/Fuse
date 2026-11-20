"""Keyed (API-key) providers: whose key a request uses, what reaches the wire
(fields stripped, efforts mapped, each auth header style), key validation and
storage, per-user listing, and the Anthropic Messages adapter.
"""
import asyncio
import json

import httpx
from pytest import fixture

import app.sources as sources
from app import db
from app.providers.protocols import anthropic_messages
from app.sources import keyed
from tests.core.helpers import quick_user

from ._support import completion, drain, frames_of, key

MSG = [{"role": "user", "content": "hi"}]


def _site_access(sid, level):
    db.execute("UPDATE sources SET config=? WHERE id=?", (json.dumps({"site_access": level}), sid))


def _chat(sid, uid, body, session_id=None):
    src = sources.get(sid)
    return asyncio.run(src.chat({"messages": MSG, **body}, user_id=uid, session_id=session_id))


def _bearers(upstream, fragment):
    return [h.get("authorization") for h in upstream.headers(fragment)]


@fixture
def deepseek(fresh_db, upstream):
    sid = keyed.ensure_source("deepseek")
    alice, bob = quick_user("alice@core.test"), quick_user("bob@core.test")
    upstream.on("POST", "api.deepseek.com/chat/completions", httpx.Response(200, json=completion()))
    return sid, alice, bob


def test_a_personal_key_serves_only_its_owner_and_comes_before_site_keys(deepseek, upstream):
    sid, alice, bob = deepseek
    key(sid, "sk-alice-personal", owner=alice)
    key(sid, "sk-site-shared-1")

    # Site keys serve nobody until the admin opens them.
    assert _chat(sid, bob, {"model": "deepseek-chat"}).status == 403
    assert _chat(sid, alice, {"model": "deepseek-chat"}).status == 200
    assert _bearers(upstream, "deepseek") == ["Bearer sk-alice-personal"]

    _site_access(sid, "everyone")
    assert _chat(sid, bob, {"model": "deepseek-chat"}).status == 200
    assert _chat(sid, alice, {"model": "deepseek-chat"}).status == 200
    # Bob never gets Alice's key; Alice still starts on her own.
    assert _bearers(upstream, "deepseek")[1:] == ["Bearer sk-site-shared-1", "Bearer sk-alice-personal"]


def test_site_access_admins_opens_site_keys_to_admins_only(deepseek):
    sid, alice, _bob = deepseek
    admin = quick_user("admin@core.test", admin=True)
    key(sid, "sk-site-shared-1")
    _site_access(sid, "admins")
    assert keyed.access_states(admin)["deepseek"] == {"ok": True, "reason": "site"}
    assert keyed.access_states(alice)["deepseek"] == {"ok": False, "reason": "none"}


def test_the_wire_body_drops_fuse_fields_and_maps_efforts(fresh_db, upstream):
    uid = quick_user()
    openai = keyed.ensure_source("openai_api")
    deepseek = keyed.ensure_source("deepseek")
    key(openai, "sk-openai-personal", owner=uid)
    key(deepseek, "sk-deepseek-personal", owner=uid)
    upstream.on("POST", "/chat/completions", httpx.Response(200, json=completion()))
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok", "reasoning_content": "thought", "claude_thinking": [{"signature": "s"}]},
        {"role": "user", "content": "again"},
    ]
    body = {"messages": history, "reasoning_effort": "xhigh", "thinking": {"type": "enabled"}, "reasoning": {"effort": "high"}}

    assert _chat(openai, uid, {**body, "model": "gpt-5.1"}, session_id="s1").status == 200
    assert _chat(openai, uid, {**body, "model": "gpt-4o"}).status == 200
    assert _chat(deepseek, uid, {**body, "model": "deepseek-chat"}, session_id="s1").status == 200
    gpt5, gpt4o = upstream.sent("api.openai.com")
    (ds,) = upstream.sent("api.deepseek.com")
    assert gpt5["reasoning_effort"] == "high"  # nearest level the model takes
    assert "reasoning_effort" not in gpt4o and "reasoning_effort" not in ds  # no known levels: none sent
    for sent in (gpt5, gpt4o, ds):
        assert "thinking" not in sent and "reasoning" not in sent
        assert sent["messages"][1] == {"role": "assistant", "content": "ok"}
    assert "prompt_cache_key" in gpt5 and "prompt_cache_key" not in ds


def test_listing_follows_each_users_own_key_and_hides_non_chat_models(fresh_db, upstream):
    sid = keyed.ensure_source("openai_api")
    alice, bob = quick_user("alice@core.test"), quick_user("bob@core.test")
    key(sid, "sk-openai-personal", owner=alice)
    upstream.on("GET", "api.openai.com/v1/models",
                httpx.Response(200, json={"data": [{"id": "gpt-5.1"}, {"id": "text-embedding-3-large"}]}))

    assert asyncio.run(sources.models_union(user_id=alice)) == ["openai/gpt-5.1"]
    assert asyncio.run(sources.models_union(user_id=bob)) == []
    assert sources.reasoning_efforts("openai_api", "openai/gpt-5.1") == ("low", "medium", "high")
    assert sources.reasoning_efforts("openai_api", "openai/gpt-4o") == ()
    # Cached per key: a second listing asks nobody.
    asyncio.run(sources.models_union(user_id=alice))
    assert len(upstream.calls("api.openai.com/v1/models")) == 1


def test_a_key_is_validated_without_echoing_it_and_never_linked_twice(fresh_db, upstream):
    alice, bob = quick_user("alice@core.test"), quick_user("bob@core.test")
    upstream.on("GET", "api.deepseek.com/models",
                httpx.Response(401, json={"error": {"message": "bad key sk-rejected-12345"}}),
                httpx.Response(200, json={"data": [{"id": "deepseek-chat"}]}))

    cid, err = asyncio.run(keyed.store_key("deepseek", "sk-rejected-12345", user_id=alice))
    assert cid is None and err == "DeepSeek rejected this key." and "sk-" not in err
    assert asyncio.run(keyed.store_key("deepseek", "short", user_id=alice)) == (None, "Enter a DeepSeek API key.")

    cid, err = asyncio.run(keyed.store_key("deepseek", "sk-good-key-12345", user_id=alice, label="mine"))
    assert cid and not err
    assert upstream.headers("api.deepseek.com/models", "GET")[-1]["authorization"] == "Bearer sk-good-key-12345"
    calls = len(upstream.calls("api.deepseek.com/models"))
    assert asyncio.run(keyed.store_key("deepseek", "sk-good-key-12345", user_id=bob)) == (
        None, "That key is already linked to another account.")
    assert len(upstream.calls("api.deepseek.com/models")) == calls  # refused before any request
    # Re-adding (or replacing) your key keeps one row per key type.
    again, err = asyncio.run(keyed.store_key("deepseek", "sk-good-key-67890", user_id=alice))
    assert again == cid and not err
    assert not keyed.remove_key("deepseek", cid, user_id=bob)
    assert keyed.remove_key("deepseek", cid, user_id=alice)
    assert keyed.access_states(alice)["deepseek"]["ok"] is False


def test_an_unreachable_or_failing_check_says_so_without_the_body(fresh_db, upstream):
    upstream.on("GET", "api.groq.com/openai/v1/models", httpx.ConnectError("down"),
                httpx.Response(500, text="internal sk-groq-secret"))
    uid = quick_user()
    assert asyncio.run(keyed.store_key("groq", "gsk-groq-secret", user_id=uid))[1].startswith("Could not reach Groq")
    assert asyncio.run(keyed.store_key("groq", "gsk-groq-secret", user_id=uid)) == (
        None, "Groq could not check the key (HTTP 500).")


def test_regions_pick_the_base_url(fresh_db, upstream):
    uid = quick_user()
    upstream.on("GET", "api.minimaxi.com/v1/models", httpx.Response(200, json={"data": []}))
    upstream.on("POST", "api.minimaxi.com/v1/chat/completions", httpx.Response(200, json=completion()))
    cid, err = asyncio.run(keyed.store_key("minimax", "mm-plan-key-12345", user_id=uid, method="m_plan", region="cn"))
    assert cid and not err
    assert _chat(keyed.ensure_source("minimax"), uid, {"model": "MiniMax-M3"}).status == 200
    assert upstream.calls("api.minimaxi.com/v1/chat/completions")


def _anthropic_sse(*events):
    text = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, content=text.encode(), headers={"content-type": "text/event-stream"})


REPLY = (
    {"type": "message_start", "message": {"id": "msg_1", "usage": {"input_tokens": 12, "output_tokens": 1}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hello"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}},
    {"type": "message_stop"},
)


def test_anthropic_api_keys_use_x_api_key_and_translate_the_stream(fresh_db, upstream):
    uid = quick_user()
    sid = keyed.ensure_source("anthropic")
    key(sid, "sk-ant-personal-1", owner=uid)
    upstream.on("POST", "api.anthropic.com/v1/messages", _anthropic_sse(*REPLY))

    result = _chat(sid, uid, {"model": "claude-x", "stream": True, "reasoning_effort": "high"})
    frames = frames_of(asyncio.run(drain(result.stream)))
    (headers,) = upstream.headers("api.anthropic.com/v1/messages")
    assert headers["x-api-key"] == "sk-ant-personal-1" and "authorization" not in headers
    assert headers["anthropic-version"] == "2023-06-01"
    (sent,) = upstream.sent("api.anthropic.com/v1/messages")
    assert sent["model"] == "claude-x" and "system" not in sent and "thinking" not in sent
    assert {f["model"] for f in frames} == {"anthropic/claude-x"}
    assert "".join(f["choices"][0]["delta"].get("content") or "" for f in frames if f["choices"]) == "hello"
    assert result.stream.usage["prompt_tokens"] == 12


def test_anthropic_errors_keep_the_reason_and_a_rejected_key_fails_over(fresh_db, upstream):
    uid = quick_user()
    sid = keyed.ensure_source("anthropic")
    key(sid, "sk-ant-personal-1", owner=uid)
    key(sid, "sk-ant-site-key-1")
    _site_access(sid, "everyone")
    upstream.on("POST", "api.anthropic.com/v1/messages",
                httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}),
                _anthropic_sse(*REPLY))
    result = _chat(sid, uid, {"model": "claude-x"})
    assert result.status == 200 and result.body["choices"][0]["message"]["content"] == "hello"
    assert result.body["model"] == "anthropic/claude-x"
    assert [a["status"] for a in result.attempts] == [401]


def test_the_anthropic_payload_has_no_subscription_shaping():
    body = {
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "claude_thinking": [{"thinking": "t", "signature": "sig"}],
             "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "grep", "arguments": "{\"q\": 1}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "found"},
        ],
        "tools": [{"type": "function", "function": {"name": "grep", "parameters": {"type": "object"}}}],
        "tool_choice": "required", "temperature": 0.3, "top_p": 0.9, "stop": "END", "max_tokens": 500,
    }
    payload = anthropic_messages.build_payload(body, "claude-x")
    assert payload["system"] == [{"type": "text", "text": "be brief", "cache_control": {"type": "ephemeral"}}]
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant", "user"]
    assert [b["type"] for b in payload["messages"][1]["content"]] == ["tool_use"]  # thinking dropped
    assert payload["messages"][2]["content"][0]["type"] == "tool_result"
    assert payload["tools"] == [{"name": "grep", "description": "", "input_schema": {"type": "object"},
                                 "cache_control": {"type": "ephemeral"}}]
    assert payload["tool_choice"] == {"type": "any"}
    assert (payload["max_tokens"], payload["temperature"], payload["stop_sequences"]) == (500, 0.3, ["END"])
    assert "top_p" not in payload
