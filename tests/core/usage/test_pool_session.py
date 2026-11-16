"""Session pins: a tenant-scoped session id per conversation, pinned to one
pool account durably (memory plus pool_sessions) until retention vacuums it.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app import db
from app.sources import pool_session
from app.sources.pool_session import RETENTION_DAYS, bind, fallback_session_id, lookup, session_id_from

T0 = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
SYSTEM = {"role": "system", "content": "You are a coding assistant."}


def test_a_client_cache_key_is_scoped_per_user_and_model_and_never_stored_raw():
    body = {"prompt_cache_key": "client-key", "messages": [{"role": "user", "content": "hello"}]}
    sid = session_id_from({"x-grok-conv-id": "header-id"}, body, user_id=1, model="grok-4.5")

    assert sid.startswith("fx:") and "client-key" not in sid
    assert sid == session_id_from(None, body, user_id=1, model="grok-4.5")  # the key beats the header
    assert sid != session_id_from(None, body, user_id=2, model="grok-4.5")
    assert sid != session_id_from(None, body, user_id=1, model="grok-4.6")
    header = session_id_from({"x-grok-conv-id": "conv-abc"}, {"messages": []}, user_id=1, model="m")
    assert header == session_id_from(None, {"session_id": "conv-abc"}, user_id=1, model="m")
    # A subagent finds its parent's pin from the parent's cache key, scoped the same way.
    assert pool_session.related_session_id("client-key", user_id=1, model="grok-4.5") == sid
    assert pool_session.related_session_id("", user_id=1, model="grok-4.5") is None


def test_the_fallback_id_follows_the_stable_conversation_prefix():
    tools = [{"type": "function", "function": {"name": "read_file"}}, {"type": "function", "function": {"name": "edit"}}]
    first = {"messages": [SYSTEM, {"role": "user", "content": "Open app/api.py"}], "tools": tools}
    later = {"messages": first["messages"] + [{"role": "assistant", "content": "ok"}, {"role": "user", "content": "Edit it"}],
             "tools": list(reversed(tools))}

    base = fallback_session_id(user_id=9, model="grok-4.5", body=first)
    assert base.startswith("fuse:")
    assert fallback_session_id(user_id=9, model="grok-4.5", body=later) == base
    assert fallback_session_id(user_id=10, model="grok-4.5", body=first) != base
    assert fallback_session_id(user_id=9, model="grok-4.6", body=first) != base
    other = {"messages": [SYSTEM, {"role": "user", "content": "Open README"}]}
    assert fallback_session_id(user_id=9, model="grok-4.5", body=other) != base
    # An image-only first message still anchors the conversation.
    image = {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:,"}}]}
    turn1 = {"messages": [image]}
    turn2 = {"messages": [image, {"role": "assistant", "content": "a cat"}, {"role": "user", "content": "and?"}]}
    assert fallback_session_id(user_id=9, model="m", body=turn1) == fallback_session_id(user_id=9, model="m", body=turn2)


def test_a_pin_survives_past_any_cache_ttl_and_a_restart_until_retention(fresh_db):
    bind("grok", "sess-1", 105, now=T0)
    assert lookup("grok", "sess-1", now=T0 + timedelta(minutes=45)) == 105
    assert lookup("chatgpt", "sess-1", now=T0) is None

    pool_session.reset_store()
    later = T0 + timedelta(days=RETENTION_DAYS - 1)
    assert lookup("grok", "sess-1", now=later) == 105
    # Each lookup refreshes the durable row, so an active pin is never vacuumed.
    assert db.fetchone("SELECT updated_at FROM pool_sessions WHERE session_id='sess-1'")["updated_at"] == \
        later.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_failover_rebinds_and_idle_pins_are_dropped_after_retention(fresh_db):
    bind("grok", "sess-1", 104, now=T0)
    bind("grok", "sess-1", 105, now=T0)
    assert lookup("grok", "sess-1", now=T0) == 105

    with patch("app.sources.pool_session.random.random", return_value=0.0):  # force the rare vacuum
        bind("grok", "sess-2", 106, now=T0 + timedelta(days=RETENTION_DAYS + 1))
    pool_session.reset_store()

    assert lookup("grok", "sess-1", now=T0 + timedelta(days=RETENTION_DAYS + 2)) is None
    assert lookup("grok", "sess-2", now=T0 + timedelta(days=RETENTION_DAYS + 2)) == 106
    assert lookup("grok", None) is None and lookup("grok", "") is None


def test_a_failed_pin_write_never_fails_a_reply_that_was_already_served(fresh_db):
    with patch.object(db, "execute", side_effect=RuntimeError("database gone")):
        bind("grok", "sess-1", 7, now=T0)
    assert lookup("grok", "sess-1", now=T0) == 7  # still pinned in memory


def test_cache_routing_fields_are_dropped_from_a_copy():
    body = {"model": "glm-5.3", "prompt_cache_key": "x", "prompt_cache_retention": "24h", "n": 1}
    out = pool_session.drop_cache_routing_fields(body)
    assert out == {"model": "glm-5.3", "n": 1} and body["prompt_cache_key"] == "x"
