"""Sticky pool routing so follow-up turns reuse the same account's prompt cache.

Prompt caches are per-account (OpenAI: not shared across organizations; xAI:
per-server KV; Z.AI: implicit cache on the calling key). Fuse therefore:

1. Derives a stable session id from the documented client cache keys, or from
   the conversation prefix if the client sent none.
2. Pins that session to one pool credential. A healthy pin is durable: it is
   replaced only by failover (bind) or vacuumed after RETENTION_DAYS idle,
   never expired on a timer. OpenAI GPT-5.6 refreshes its cache for ~30
   minutes per use and the other pools publish no TTL, so a 30-minute pin
   would rotate a warm conversation onto a cold account for no reason.
3. Forwards x-grok-conv-id (Grok Chat Completions) and, for ChatGPT
   Responses, prompt_cache_key plus the session-id header that ChatGPT
   actually routes its cache by. Z.AI has no documented cache-routing field.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import threading
from datetime import datetime, timedelta, timezone
from typing import Mapping

from .. import db
from ..config import settings

logger = logging.getLogger(__name__)

# Pin retention: days, not minutes. This is Fuse's pin policy, not a provider
# contract; rows are forgotten only after this long without a lookup or bind.
RETENTION_DAYS = 7
SESSION_ID_MAX = 256

# Cache-routing body fields Grok/Z.AI chat/completions do not take. xAI reads
# prompt_cache_key on its Responses API only; on Chat Completions the session
# id travels as the x-grok-conv-id header instead.
CACHE_BODY_FIELDS = ("prompt_cache_key", "prompt_cache_retention", "prompt_cache_options")

# Tests set this False so they do not write the live database.
persist = True

# A pin's durable row is touched at most this often: updated_at only feeds
# the RETENTION_DAYS vacuum, which an hour-old stamp cannot change.
TOUCH_SECONDS = 3600

_lock = threading.Lock()
# Orders bind()'s durable writes, so the row ends on the pin memory holds,
# without lookups and peeks waiting on _lock through a database write.
_write_lock = threading.Lock()
# (kind, session id) -> (credential id, last use, updated_at last written)
_mem: dict[tuple[str, str], tuple[int, str, str]] = {}


def _ts(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _cutoff(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")


def reset_store() -> None:
    """Drop in-memory pins. Tests only."""
    with _lock:
        _mem.clear()


def drop_cache_routing_fields(body: dict) -> dict:
    """Copy of body without cache-routing fields Grok/Z.AI chat completions reject."""
    payload = dict(body)
    for key in CACHE_BODY_FIELDS:
        payload.pop(key, None)
    return payload


def _header(headers: Mapping | None, name: str) -> str:
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if callable(getter):
        val = getter(name)
        if val:
            return str(val).strip()
    if isinstance(headers, dict):
        want = name.lower()
        for key, val in headers.items():
            if str(key).lower() == want and val:
                return str(val).strip()
    return ""


def _message_text(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        return "\n".join(parts)
    return ""


def fallback_session_id(*, user_id: int | None, model: str, body: dict) -> str:
    """Stable id for OpenAI-compat clients that send no cache key.

    First system/developer message + first user message + tool names stay
    constant across turns (clients append; they must not rewrite earlier
    messages or prefix cache is already busted). Model is included because
    Grok/OpenAI caches are model-isolated.
    """
    # None = not seen yet; an empty or image-only first message still counts,
    # or a later turn's text would replace it and move the session.
    first_system = None
    first_user = None
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role in ("system", "developer") and first_system is None:
            first_system = _message_text(msg)
        elif role == "user" and first_user is None:
            first_user = _message_text(msg)
        if first_system is not None and first_user is not None:
            break
    first_system = first_system or ""
    first_user = first_user or ""
    names: list[str] = []
    tools = body.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
            if isinstance(fn, dict) and fn.get("name"):
                names.append(str(fn["name"]))
    raw = json.dumps(
        {
            "u": int(user_id or 0),
            "m": model or "",
            "s": first_system,
            "q": first_user,
            "t": sorted(names),
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"fuse:{digest}"


def _scope(user_id: int | None, model: str, raw: str) -> str:
    """Tenant-scope an external cache key.

    Client-supplied prompt_cache_key / conv-id / session_id values are attacker
    controllable. Hashing them with APP_SECRET plus user and model means two
    users picking the same string can't share or hijack each other's pool pin
    or upstream routing group, and no raw client identifier is persisted or
    forwarded upstream.
    """
    secret = (settings.app_secret or "").encode()
    if not secret:
        # APP_SECRET is validated at startup; never scope pins with a public
        # constant — that would collapse tenant isolation if config degrades.
        raise RuntimeError("APP_SECRET is required for pool session scoping")
    material = f"{int(user_id or 0)}|{model or ''}|{raw}".encode("utf-8")
    return "fx:" + hmac.new(secret, material, hashlib.sha256).hexdigest()[:40]


def related_session_id(raw: str | None, *, user_id: int | None, model: str) -> str | None:
    """The scoped id a related conversation (a subagent's parent) is pinned
    under, from the cache key that conversation sends as prompt_cache_key.
    Server-side only: a client never names another session to follow."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    return _scope(user_id, model, raw.strip()[:SESSION_ID_MAX])


def session_id_from(
    headers: Mapping | None,
    body: dict,
    *,
    user_id: int | None,
    model: str,
) -> str:
    """Scoped session id used for pool pins and upstream cache keys.

    Priority matches the documented knobs:
    1. body prompt_cache_key (OpenAI Chat Completions / Responses, xAI Responses)
    2. body session_id (OpenRouter sticky routing)
    3. header x-session-id, then x-grok-conv-id (xAI Chat Completions)
    4. hash of the stable conversation prefix

    The returned value is always the tenant-scoped digest, never the raw key.
    """
    raw = ""
    if isinstance(body, dict):
        for key in ("prompt_cache_key", "session_id"):
            val = body.get(key)
            if isinstance(val, str) and val.strip():
                raw = val.strip()[:SESSION_ID_MAX]
                break
    if not raw:
        for header in ("x-session-id", "x-grok-conv-id"):
            val = _header(headers, header)
            if val:
                raw = val[:SESSION_ID_MAX]
                break
    if not raw:
        raw = fallback_session_id(user_id=user_id, model=model, body=body or {})
    return _scope(user_id, model, raw)


def bind(kind: str, session_id: str | None, credential_id: int | None, *, now: datetime | None = None) -> None:
    if not session_id or not credential_id:
        return
    kind = str(kind)
    session_id = str(session_id)[:SESSION_ID_MAX]
    cid = int(credential_id)
    now = now or datetime.now(timezone.utc)
    stamp = _ts(now)
    cutoff = _cutoff(now)
    with _write_lock:
        with _lock:
            prev = _mem.get((kind, session_id))
            # The same pin, written recently: the durable row already says so.
            same = prev is not None and prev[0] == cid and prev[2] >= _ts(now - timedelta(seconds=TOUCH_SECONDS))
            _mem[(kind, session_id)] = (cid, stamp, prev[2] if same else stamp)
            # Bounded: pins idle past retention are dropped so the map can't grow
            # forever. A healthy active pin is refreshed by every lookup.
            for key in [k for k, (_, updated, _saved) in _mem.items() if updated < cutoff]:
                _mem.pop(key, None)
        if persist and not same:
            # Under _write_lock, so the durable row ends on the pin memory holds.
            # Pinning runs after the upstream already served the request: a DB
            # error here must not turn a delivered (and billed) reply into a 502.
            try:
                db.execute(
                    """INSERT INTO pool_sessions (kind, session_id, credential_id, updated_at)
                       VALUES (?,?,?,?)
                       ON CONFLICT(kind, session_id) DO UPDATE SET
                         credential_id=excluded.credential_id,
                         updated_at=excluded.updated_at""",
                    (kind, session_id, cid, stamp),
                )
                # Full-table vacuum per bind scans updated_at on every chat turn; run
                # it rarely — expired rows are harmless and refreshed on lookup anyway.
                if random.random() < 0.02:
                    db.execute("DELETE FROM pool_sessions WHERE updated_at < ?", (cutoff,))
            except Exception:
                logger.warning("Could not persist pool session pin kind=%s credential=%s", kind, cid, exc_info=True)


def peek(kind: str, session_id: str | None) -> int | None:
    """The account a session is pinned to, without refreshing the pin: a
    look from the chat page must not keep a conversation alive."""
    if not session_id:
        return None
    key = (str(kind), str(session_id)[:SESSION_ID_MAX])
    with _lock:
        hit = _mem.get(key)
    if hit:
        return int(hit[0])
    if not persist:
        return None
    row = db.fetchone("SELECT credential_id FROM pool_sessions WHERE kind=? AND session_id=? AND updated_at >= ?",
                      (key[0], key[1], _cutoff()))
    return int(row["credential_id"]) if row else None


def lookup(kind: str, session_id: str | None, *, now: datetime | None = None) -> int | None:
    if not session_id:
        return None
    kind = str(kind)
    session_id = str(session_id)[:SESSION_ID_MAX]
    now = now or datetime.now(timezone.utc)
    stamp = _ts(now)
    stale = _ts(now - timedelta(seconds=TOUCH_SECONDS))
    cid: int | None = None
    touch = False
    with _lock:
        hit = _mem.get((kind, session_id))
        if hit:
            cid = int(hit[0])
            touch = hit[2] < stale
            _mem[(kind, session_id)] = (cid, stamp, stamp if touch else hit[2])
    if persist:
        if cid is not None:
            # Keep the durable row about as fresh as the memory pin, or the
            # retention vacuum would forget an active conversation after a restart.
            if touch:
                db.execute(
                    "UPDATE pool_sessions SET updated_at=? WHERE kind=? AND session_id=?",
                    (stamp, kind, session_id),
                )
            return cid
        row = db.fetchone(
            "SELECT credential_id, updated_at FROM pool_sessions WHERE kind=? AND session_id=? AND updated_at >= ?",
            (kind, session_id, _cutoff(now)),
        )
        if row:
            cid = int(row["credential_id"])
            saved = str(row["updated_at"] or "")
            if saved < stale:
                db.execute(
                    "UPDATE pool_sessions SET updated_at=? WHERE kind=? AND session_id=?",
                    (stamp, kind, session_id),
                )
                saved = stamp
            with _lock:
                _mem[(kind, session_id)] = (cid, stamp, saved)
    return cid
