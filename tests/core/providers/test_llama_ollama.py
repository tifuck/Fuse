"""Self-hosted llama.cpp and Ollama Cloud's native /api/chat.

llama: the public catalog hides filesystem names behind stable ids and chat
maps them back; per-model vision and context come from the server. Ollama:
OpenAI requests translate to native ones (messages, images, tools, options,
effort) and native answers and streams translate back, with tool calls that
keep their identity across chunks and usage on the final frame.
"""
import asyncio
import json

import httpx
import pytest

from app import context_meter, model_caps
from app.sources import llama, ollama
from tests.core.helpers import quick_user

from ._support import completion, drain, frames_of, key, source


def _llama(sid, cfg):
    return llama.LlamaSource({"id": sid, "name": "llama"}, cfg, [])


def test_llama_lists_public_ids_and_sends_the_servers_own_name(fresh_db, upstream):
    rows = [{"id": "/models/bartowski-Qwen3-32B-Q4_K_M.gguf",
             "architecture": {"input_modalities": ["text", "image"]}, "memory": {"effective": {"n_ctx": 65536}}},
            {"id": "mistral-small"}]
    upstream.on("GET", "/v1/models", httpx.Response(200, json={"data": rows}))
    upstream.on("GET", "/props", httpx.Response(200, json={"default_generation_settings": {"n_ctx": 8192},
                                                          "modalities": {"vision": False}}))
    upstream.on("POST", "/chat/completions", httpx.Response(200, json=completion()))
    sid = source("llama", {"base_url": "https://llama.test"})
    src = _llama(sid, {"base_url": "https://llama.test"})

    assert asyncio.run(src.models()) == ["qwen3-32b", "mistral-small"]
    assert model_caps.image_input("llama", "qwen3-32b") is True
    assert model_caps.image_input("llama", "mistral-small") is False  # /props fills the gap
    assert context_meter.cached_window("llama", "qwen3-32b") == 65536

    asyncio.run(src.chat({"model": "qwen3-32b", "messages": []}))
    assert upstream.sent("/chat/completions")[0]["model"] == "/models/bartowski-Qwen3-32B-Q4_K_M.gguf"


def test_llama_never_advertises_the_placeholder_name(fresh_db, upstream):
    upstream.on("GET", "/v1/models", httpx.Response(503))
    upstream.on("GET", "/props", httpx.Response(503))
    sid = source("llama")
    assert asyncio.run(_llama(sid, {"base_url": "https://llama.test", "model": "local"}).models()) == []
    llama._models_cache.clear()
    assert asyncio.run(_llama(sid, {"base_url": "https://llama.test", "model": "qwen"}).models()) == ["qwen"]


# -- Ollama ------------------------------------------------------------------------

@pytest.fixture
def olla(fresh_db, upstream):
    sid = source("ollama", {"base_url": "https://ollama.test/api"})
    cid = key(sid, "ollama-key-1")
    from app import db
    creds = db.fetchall("SELECT * FROM credentials WHERE source_id=?", (sid,))
    return ollama.OllamaSource({"id": sid, "name": "Ollama"}, {"base_url": "https://ollama.test/api"}, creds), cid


def _chat(src, body):
    async def go():
        result = await src.chat({"model": "qwen3", **body})
        raw = await drain(result.stream) if result.stream is not None else None
        return result, raw
    return asyncio.run(go())


def test_requests_translate_to_native_chat(olla, upstream):
    src, _cid = olla
    upstream.on("POST", "/api/chat", httpx.Response(200, json={"message": {"role": "assistant", "content": "ok"},
                                                                "done": True}))
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "see"},
                                     {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}]},
        {"role": "assistant", "content": "", "reasoning_content": "hmm",
         "tool_calls": [{"id": "call_1", "function": {"name": "read", "arguments": "{\"path\": \"a\"}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "file body"},
    ]
    _chat(src, {"messages": messages, "max_tokens": 50, "temperature": 0.2, "stop": "END", "seed": 7,
                "response_format": {"type": "json_object"}, "reasoning_effort": "xhigh",
                "tools": [{"type": "function", "function": {"name": "read"}}]})

    (sent,) = upstream.sent("/api/chat")
    assert sent["model"] == "qwen3" and sent["stream"] is False
    assert sent["messages"][0] == {"role": "user", "content": "see", "images": ["QUJD"]}
    assert sent["messages"][1]["thinking"] == "hmm"
    assert sent["messages"][1]["tool_calls"] == [{"function": {"name": "read", "arguments": {"path": "a"}},
                                                  "id": "call_1"}]
    # Ollama pairs a result with its call by tool name, not the OpenAI id.
    assert sent["messages"][2] == {"role": "tool", "content": "file body", "tool_call_id": "call_1",
                                   "tool_name": "read"}
    assert sent["options"] == {"num_predict": 50, "temperature": 0.2, "stop": ["END"], "seed": 7}
    assert (sent["format"], sent["think"]) == ("json", "max")
    assert sent["tools"][0]["function"]["name"] == "read"


@pytest.mark.parametrize("body,think", [
    ({"reasoning_effort": "none"}, False),
    ({"reasoning_effort": "minimal"}, "low"),
    ({"reasoning": {"effort": "high"}}, "high"),
    ({"thinking": {"type": "disabled"}}, False),
    ({"think": True}, True),
])
def test_effort_maps_onto_think(olla, upstream, body, think):
    src, _cid = olla
    upstream.on("POST", "/api/chat", httpx.Response(200, json={"message": {"content": "ok"}, "done": True}))
    _chat(src, {"messages": [{"role": "user", "content": "x"}], **body})
    assert upstream.sent("/api/chat")[0]["think"] == think


def test_an_on_off_model_gets_true_instead_of_a_level(olla, upstream):
    src, _cid = olla
    ollama._think_values["qwen3"] = [True, False]
    upstream.on("POST", "/api/chat", httpx.Response(200, json={"message": {"content": "ok"}, "done": True}))
    _chat(src, {"messages": [{"role": "user", "content": "x"}], "reasoning_effort": "high"})
    assert upstream.sent("/api/chat")[0]["think"] is True


def test_unsupported_attachments_are_refused_without_a_call(olla, upstream):
    src, _cid = olla
    for part in ({"type": "image_url", "image_url": {"url": "https://x.test/a.png"}},
                 {"type": "file", "file": {"file_data": "abc"}}):
        result, _ = _chat(src, {"messages": [{"role": "user", "content": [part]}]})
        assert result.status == 400
    assert not upstream.requests


def test_a_native_answer_becomes_an_openai_completion(olla, upstream):
    src, cid = olla
    upstream.on("POST", "/api/chat", httpx.Response(200, json={
        "message": {"role": "assistant", "content": "", "thinking": "plan",
                    "tool_calls": [{"function": {"name": "read", "arguments": {"path": "a"}}}]},
        "done": True, "done_reason": "length", "prompt_eval_count": 30, "eval_count": 5,
        "prompt_eval_cached_count": 12}))

    result, _ = _chat(src, {"messages": [{"role": "user", "content": "x"}]})

    choice = result.body["choices"][0]
    assert result.status == 200 and result.credential_id == cid
    assert choice["message"]["reasoning_content"] == "plan"
    assert choice["message"]["tool_calls"] == [{"id": "ollama_0", "type": "function",
                                                "function": {"name": "read", "arguments": "{\"path\": \"a\"}"}}]
    # num_predict reached: truncation wins over the tool call.
    assert choice["finish_reason"] == "length"
    assert (result.usage["prompt_tokens"], result.usage["output_tokens"], result.usage["cached_tokens"]) == (30, 5, 12)


def _ndjson(*chunks):
    return httpx.Response(200, content=b"".join(json.dumps(c).encode() + b"\n" for c in chunks))


def test_a_native_stream_becomes_sse_with_stable_tool_indexes_and_final_usage(olla, upstream):
    src, _cid = olla
    call = lambda name, idx, args: {"function": {"name": name, "index": idx, "arguments": args}}
    upstream.on("POST", "/api/chat", _ndjson(
        {"message": {"content": "hi ", "thinking": "t"}},
        {"message": {"tool_calls": [call("a", 0, {"x": 1})]}},
        # Ollama restarts index at 0 in every chunk: a named call is a new call.
        {"message": {"tool_calls": [call("b", 0, {"y": 2})]}},
        {"message": {"content": "", "tool_calls": [call("c", 0, {})]}, "done": True,
         "prompt_eval_count": 9, "eval_count": 4},
    ))

    result, raw = _chat(src, {"messages": [{"role": "user", "content": "x"}], "stream": True})

    frames = frames_of(raw)
    calls = [tc for f in frames for c in f["choices"] for tc in c["delta"].get("tool_calls", [])]
    assert [(c["index"], c["function"]["name"]) for c in calls] == [(0, "a"), (1, "b"), (2, "c")]
    assert len({c["id"] for c in calls}) == 3
    assert frames[0]["choices"][0]["delta"] == {"content": "hi ", "reasoning_content": "t", "role": "assistant"}
    assert [c["finish_reason"] for f in frames for c in f["choices"] if c.get("finish_reason")] == ["tool_calls"]
    assert frames[-1]["usage"] == {"prompt_tokens": 9, "completion_tokens": 4}
    assert raw.endswith(b"data: [DONE]\n\n")
    assert result.stream.usage["stream_status"] == "complete" and result.stream.usage["output_tokens"] == 4


def test_a_stream_error_passes_ollamas_words_on_and_a_cut_stream_is_failed(olla, upstream):
    src, _cid = olla
    upstream.on("POST", "/api/chat",
                _ndjson({"message": {"content": "a"}}, {"error": "model runner has unexpectedly stopped"}),
                _ndjson({"message": {"content": "a"}}))

    _result, raw = _chat(src, {"messages": [{"role": "user", "content": "x"}], "stream": True})
    assert frames_of(raw)[-1]["error"]["message"] == "model runner has unexpectedly stopped"

    result, _raw = _chat(src, {"messages": [{"role": "user", "content": "x"}], "stream": True})
    assert result.stream.usage["stream_status"] == "failed"


def test_a_rejected_key_is_fuses_problem_and_a_paid_model_is_a_refusal(olla, upstream):
    src, _cid = olla
    upstream.on("POST", "/api/chat",
                httpx.Response(401, json={"error": "unauthorized"}),
                httpx.Response(403, json={"error": "this model requires a subscription, upgrade for access"}))
    rejected, _ = _chat(src, {"messages": [{"role": "user", "content": "x"}]})
    assert rejected.status == 502 and rejected.body["error"]["message"] == "Ollama rejected the configured API key"
    ollama._pools.clear()
    gated, _ = _chat(src, {"messages": [{"role": "user", "content": "x"}]})
    assert gated.status == 403


def test_the_catalog_reads_capabilities_without_completions_and_never_caches_a_blank(olla, upstream):
    src, _cid = olla
    upstream.on("GET", "/api/tags", httpx.Response(500),
                httpx.Response(200, json={"models": [{"name": "b", "modified_at": "2026-01-01"},
                                                     {"name": "a", "modified_at": "2026-02-01"}]}))
    upstream.on("POST", "/api/show", lambda r: httpx.Response(200, json={
        "capabilities": ["completion", "vision", "thinking"] if json.loads(r.content)["model"] == "a"
        else ["completion"], "model_info": {"x.context_length": 32768}}))

    with pytest.raises(ValueError):
        asyncio.run(src._tags())
    assert asyncio.run(src._tags()) == ["a", "b"]  # newest first, stable
    assert model_caps.image_input("ollama", "a") is True and model_caps.image_input("ollama", "b") is False
    assert ollama.efforts_for("b") == () and ollama.efforts_for("a") == ollama.EFFORTS
    assert not upstream.calls("/api/chat")
