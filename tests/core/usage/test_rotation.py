"""Key rotation on an OpenAI-compatible source (none / every_request /
round_robin / sticky), through chat() with only the upstream call faked."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app import accounts, db
from app.sources import openai_compat, pool_pick, pool_session
from tests.core.usage._support import source

BODY = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}


def reply(status, body=None):
    return (status, body if body is not None else {"id": "c"}, None, {}, {})


@pytest.fixture
def keys(fresh_db):
    openai_compat.reset_cursors()
    sid = source("openai")
    ids = [accounts.add_credential(sid, f"k{i}", f"sk-{i}") for i in range(3)]
    rows = {int(r["id"]): r for r in db.fetchall("SELECT * FROM credentials WHERE source_id=?", (sid,))}
    with patch.object(openai_compat, "openai_chat", new_callable=AsyncMock, return_value=reply(200)) as upstream:
        yield sid, [rows[i] for i in ids], upstream
    openai_compat.reset_cursors()


def _chat(sid, creds, rotation, **kwargs):
    # Routing builds a fresh source for every request.
    src = openai_compat.OpenAICompatSource({"id": sid, "name": "t"}, {"rotation": rotation}, creds)
    return asyncio.run(src.chat(dict(BODY), **kwargs))


def _ids(creds):
    return [int(c["id"]) for c in creds]


def test_round_robin_moves_on_every_request_even_though_each_request_rebuilds_the_source(keys):
    sid, creds, _upstream = keys
    a, b, c = _ids(creds)

    assert [_chat(sid, creds, "round_robin").credential_id for _ in range(4)] == [a, b, c, a]
    assert [_chat(sid, creds, "none").credential_id for _ in range(2)] == [a, a]


def test_sticky_keeps_a_conversation_on_the_key_that_served_it(keys):
    sid, creds, upstream = keys
    a, b, _c = _ids(creds)
    upstream.side_effect = [reply(401), reply(200)]

    first = _chat(sid, creds, "sticky", session_id="fx:convo")
    assert (first.credential_id, [x["credential_id"] for x in first.attempts]) == (b, [a])

    upstream.side_effect = None
    pool_pick.reset_health()  # key a is healthy again
    assert _chat(sid, creds, "sticky", session_id="fx:convo").credential_id == b
    assert _chat(sid, creds, "sticky", session_id="fx:other").credential_id == a
    # Pins are per source: another source never inherits this one's pin.
    assert pool_session.lookup(f"openai:{sid}", "fx:convo") == b
    assert pool_session.lookup(f"openai:{sid + 1}", "fx:convo") is None


def test_a_key_that_failed_auth_goes_last_and_a_rate_limit_is_never_rotated(keys):
    sid, creds, upstream = keys
    a, b, c = _ids(creds)
    upstream.side_effect = [reply(401), reply(200)]
    _chat(sid, creds, "round_robin")

    upstream.side_effect = None
    order = [_chat(sid, creds, "round_robin").credential_id for _ in range(3)]
    assert sorted(order[:2]) == [b, c] and a not in order[:2]

    upstream.return_value = reply(429)
    upstream.reset_mock()
    result = _chat(sid, creds, "none")
    assert (result.status, upstream.await_count) == (429, 1)


def test_the_upstream_cache_key_is_always_the_scoped_session_id(keys):
    sid, creds, upstream = keys

    _chat(sid, creds, "none", session_id="fx:scoped")
    raw = {**BODY, "prompt_cache_key": "raw-thread"}
    asyncio.run(openai_compat.OpenAICompatSource({"id": sid, "name": "t"}, {}, creds).chat(raw, session_id="fx:2"))
    _chat(sid, creds, "none")

    sent = [call.args[3] for call in upstream.await_args_list]
    assert [body.get("prompt_cache_key") for body in sent] == ["fx:scoped", "fx:2", None]


def test_a_strict_server_that_rejects_the_cache_key_is_retried_once_without_it(keys):
    sid, creds, upstream = keys
    upstream.side_effect = [reply(400, {"error": "unknown field prompt_cache_key"}), reply(200)]

    result = _chat(sid, creds, "none", session_id="fx:s")

    sent = [call.args[3] for call in upstream.await_args_list]
    assert result.status == 200 and "prompt_cache_key" in sent[0] and "prompt_cache_key" not in sent[1]
    assert {call.args[2]["Authorization"] for call in upstream.await_args_list} == {"Bearer sk-0"}
