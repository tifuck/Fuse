"""Requests on another user's pooled account carry only plain Chat Completions
fields: nothing that multiplies or extends what they bill the donor."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app import db
from app.sources import grok, kimi, mimo, pool_policy, pool_session, zai
from app.sources.pool_pick import reset_health

FN = {"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}
IMAGE = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}


def test_strip_keeps_generation_tools_reasoning_and_images():
    body = {
        "model": "m", "stream": True, "messages": [{"role": "user", "content": [IMAGE]}], "max_tokens": 9,
        "tools": [FN], "tool_choice": "auto", "parallel_tool_calls": True, "reasoning_effort": "high",
        "thinking": {"type": "enabled"}, "prompt_cache_key": "fx:1", "stream_options": {"include_usage": True},
        "n": 16, "best_of": 4, "service_tier": "priority", "search_parameters": {"mode": "on"},
        "web_search_options": {}, "provider": {"only": ["x"]},
    }
    out = pool_policy.strip_borrowed(body)
    assert set(out) == {
        "model", "stream", "messages", "max_tokens", "tools", "tool_choice", "parallel_tool_calls",
        "reasoning_effort", "thinking", "prompt_cache_key", "stream_options",
    }
    assert out["messages"] == body["messages"] and out["tools"] == [FN]
    assert body["n"] == 16  # the caller's copy is untouched


def test_strip_drops_provider_side_tools():
    builtin = {"type": "builtin_function", "function": {"name": "$web_search"}}
    out = pool_policy.strip_borrowed({"tools": [FN, builtin, {"type": "web_search"}], "tool_choice": "auto"})
    assert out == {"tools": [FN], "tool_choice": "auto"}
    out = pool_policy.strip_borrowed({"tools": [builtin], "tool_choice": "required", "parallel_tool_calls": True})
    assert out == {}
    out = pool_policy.strip_borrowed({"tools": [FN], "tool_choice": {"type": "web_search"}})
    assert out == {"tools": [FN]}


def test_borrowed_is_another_users_account():
    assert pool_policy.borrowed({"donated_by": 2}, 1)
    assert pool_policy.borrowed({"donated_by": 2}, None)
    assert not pool_policy.borrowed({"donated_by": 1}, 1)
    assert not pool_policy.borrowed({"donated_by": None}, 1)
    body = {"model": "m", "n": 3}
    assert pool_policy.lent_payload(body, {"donated_by": 1}, 1) is body
    assert pool_policy.lent_payload(body, {"donated_by": 2}, 1) == {"model": "m"}


POOLS = {
    "grok": (grok, grok.GrokSource, "grok-4.7", ("refresh_pool_billing",), {"access_token": "tok"}),
    "kimi": (kimi, kimi.KimiSource, "k3", ("refresh_due", "refresh_pool_quota"), {"access_token": "tok"}),
    "zai": (zai, zai.ZaiSource, "glm-5.3", ("refresh_pool_quota",), {"api_key": "key"}),
    "mimo": (mimo, mimo.MimoSource, "mimo-v2.6-pro", ("refresh_pool_quota",), {"api_key": "key"}),
}


def _sent(kind: str, donor: int | None, caller: int) -> dict:
    mod, cls, model, refreshers, extra = POOLS[kind]
    cred = {"id": 7, "disabled": 0, "last_usage_percent": 1.0, "donated_by": donor,
            "week_tokens": 0, "week_start": db.iso_week_start(), "_extra": extra}
    ok = (200, {"id": "c"}, None, {"prompt_tokens": 1, "completion_tokens": 1}, {})
    body = {"model": model, "messages": [{"role": "user", "content": [IMAGE]}], "stream": False, "n": 8,
            "service_tier": "priority", "tools": [FN, {"type": "web_search"}], "tool_choice": "auto",
            "max_tokens": 50}
    patches = [
        patch.object(mod, "note_pool_invoke"),
        patch("app.accounts.cred_extra", side_effect=lambda c: c["_extra"]),
        patch("app.db.fetchall", return_value=[cred]),
        patch.object(pool_policy, "filter_credentials", side_effect=lambda rows, *a, **k: list(rows)),
        patch.object(pool_policy, "primary_account", return_value=None),
        *(patch.object(mod, name, new_callable=AsyncMock) for name in refreshers),
    ]
    chat = patch.object(mod, "openai_chat", new_callable=AsyncMock, return_value=ok)
    for p in patches:
        p.start()
    try:
        with chat as sent:
            src = cls({"id": 1, "name": kind}, {}, [cred])
            result = asyncio.run(src.chat(body, user_id=caller, session_id="s"))
    finally:
        for p in reversed(patches):
            p.stop()
    assert result.status == 200
    return sent.call_args.args[3]


@pytest.fixture(autouse=True)
def _clean():
    reset_health()
    pool_session.persist = False
    pool_session.reset_store()
    yield
    pool_session.reset_store()
    pool_session.persist = True


@pytest.mark.parametrize("kind", sorted(POOLS))
def test_borrower_cannot_multiply_or_extend_the_donors_bill(kind):
    sent = _sent(kind, donor=1, caller=2)
    assert "n" not in sent and "service_tier" not in sent
    assert sent["tools"] == [FN] and sent["messages"][0]["content"][0]["type"] == "image_url"


@pytest.mark.parametrize("kind", sorted(POOLS))
def test_owner_request_is_forwarded_as_sent(kind):
    sent = _sent(kind, donor=1, caller=1)
    assert sent["n"] == 8 and sent["service_tier"] == "priority" and len(sent["tools"]) == 2
