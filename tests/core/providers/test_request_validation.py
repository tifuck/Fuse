"""invoke_chat's request checks: size limits (text, retained history, image
data), malformed parts and parameters are client errors raised before any
upstream is called, so no pool account is billed or blamed for them.
"""
import asyncio

import httpx
import pytest
from fastapi import HTTPException

from app import api, chat_runs
from tests.core.helpers import quick_user

from ._support import completion, key, route, source

PNG = "data:image/png;base64,iVBORw0KGgo="


@pytest.fixture
def uid(fresh_db, upstream):
    uid = quick_user()
    sid = source("openai", {"base_url": "https://up.test"})
    key(sid, "sk-upstream-key")
    route("m1", sid, "up-1")
    upstream.on("POST", "/chat/completions", httpx.Response(200, json=completion()))
    upstream.on("GET", "/models", httpx.Response(200, json={"data": [{"id": "up-1"}]}))
    return uid


def _invoke(uid, body, retained=False):
    request = chat_runs.internal_request(uid)
    if retained:
        request.scope["fuse_retained_history"] = True
    return asyncio.run(api.invoke_chat(request, {"id": uid}, {"model": "m1", **body}))


def _rejected(uid, body, retained=False):
    with pytest.raises(HTTPException) as caught:
        _invoke(uid, body, retained)
    return caught.value


def _user(content):
    return [{"role": "user", "content": content}]


def test_text_over_the_limit_is_refused_but_retained_history_gets_the_larger_budget(uid, upstream):
    big = "x" * (api._MAX_MESSAGE_CHARS + 1)

    err = _rejected(uid, {"messages": _user(big)})
    assert err.status_code == 400 and "compact the conversation" in err.detail
    assert not upstream.requests

    *_, result = _invoke(uid, {"messages": _user(big)}, retained=True)
    assert result.status == 200


def test_every_text_field_counts_toward_the_limit(uid):
    third = api._MAX_MESSAGE_CHARS // 3 + 1
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "a" * third},
                                     {"type": "file", "file": {"file_data": "b" * third}}]},
        {"role": "assistant", "content": None, "reasoning_content": "r",
         "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "c" * third}}]},
    ]
    assert "messages too large" in _rejected(uid, {"messages": messages}).detail


def test_message_count_limit_and_its_retained_budget(uid):
    many = _user("hi") * (api._MAX_MESSAGES + 1)
    assert "too many messages" in _rejected(uid, {"messages": many}).detail
    *_, result = _invoke(uid, {"messages": many}, retained=True)
    assert result.status == 200


def test_image_data_is_not_counted_as_text(uid, upstream):
    huge = "data:image/png;base64," + "A" * (api._MAX_MESSAGE_CHARS + 4)
    *_, result = _invoke(uid, {"messages": _user([{"type": "image_url", "image_url": {"url": huge}}])})
    assert result.status == 200


@pytest.mark.parametrize("part,detail", [
    ({"type": "image_url", "image_url": {"url": "data:image/png,abc"}}, "base64 data URI"),
    ({"type": "image_url", "image_url": {"url": "data:image/svg+xml;base64,PHN2Zz4="}}, "Unsupported image MIME"),
    ({"type": "image_url", "image_url": {"url": "data:image/png;base64,@@@"}}, "Invalid base64"),
    ({"type": "image_url", "image_url": {"url": "file:///etc/passwd"}}, "HTTP(S) URL"),
    ({"type": "image_url", "image_url": {}}, "image_url.url must be a string"),
    ({"type": "file", "file": {"filename": "a.txt"}}, "file_id or file_data"),
    ({"text": "no type"}, "must be an object with a type"),
])
def test_malformed_parts_are_client_errors(uid, upstream, part, detail):
    err = _rejected(uid, {"messages": _user([part])})
    assert err.status_code == 400 and detail in err.detail
    assert not upstream.requests


@pytest.mark.parametrize("body,detail", [
    ({"messages": []}, "non-empty array"),
    ({"messages": [{"role": "robot", "content": "x"}]}, "unsupported role"),
    ({"messages": _user("x"), "reasoning_effort": "turbo"}, "Invalid reasoning_effort"),
    ({"messages": _user("x"), "reasoning": {"effort": 3}}, "Invalid reasoning.effort"),
    ({"messages": _user("x"), "thinking": {"type": "on"}}, "thinking.type"),
    ({"messages": _user("x"), "stream": "yes"}, "Invalid stream"),
    ({"messages": _user("x"), "max_tokens": 1.5}, "Invalid max_tokens"),
    ({"messages": _user("x"), "max_completion_tokens": True}, "Invalid max_completion_tokens"),
    ({"messages": _user("x"), "max_tokens": float("nan")}, "Invalid max_tokens"),
    ({"messages": _user("x"), "temperature": float("nan")}, "cannot be encoded"),
    ({"messages": _user("x"), "model": "m" * 201}, "Invalid model"),
])
def test_invalid_parameters_are_refused_before_dispatch(uid, upstream, body, detail):
    assert detail in _rejected(uid, body).detail
    assert not upstream.requests


def test_parameters_are_normalized_before_they_reach_the_upstream(uid, upstream):
    _invoke(uid, {"messages": _user("x"), "max_tokens": "12", "max_completion_tokens": 0,
                  "reasoningEffort": "high", "prompt_snapshot": {"secret": 1}, "image_snapshot": {"x": 1}})
    (sent,) = upstream.sent("/chat/completions")
    assert (sent["model"], sent["max_tokens"], sent["max_completion_tokens"]) == ("up-1", 12, 1)
    assert sent["reasoning_effort"] == "high" and "reasoningEffort" not in sent
    assert "prompt_snapshot" not in sent and "image_snapshot" not in sent


def test_unknown_and_unavailable_models_say_which(uid, upstream):
    assert _rejected(uid, {"model": "nope", "messages": _user("x")}).status_code == 404
    import app.sources as sources
    sources._unavailable_types.add("openai")
    err = _rejected(uid, {"model": "nope", "messages": _user("x")})
    assert (err.status_code, err.detail) == (503, "Model catalog is temporarily unavailable")
