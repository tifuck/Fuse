"""Credential helpers the pool modules share (app/sources/<pool>.py).

One copy of what each pool used to carry its own copy of. Where pools really
differ (what makes an account authed or subscribed), the pool keeps its own
function and passes it in; nothing here guesses a provider's rules.
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timedelta, timezone

from .. import accounts, db
from ..crypto import DecryptionError


def row_get(row, key, default=None):
    """A column of a row (dict or DB row), default when missing or NULL."""
    try:
        if isinstance(row, dict):
            return row.get(key, default)
        if key in row.keys():
            val = row[key]
            return default if val is None else val
    except Exception:
        return default
    return default


def parse_ts(ts, *, strip_offset: bool = True) -> datetime | None:
    """A stored ISO time (no zone, or Z) as UTC. strip_offset: ignore a
    trailing +hh:mm instead of failing on it."""
    raw = str(ts or "").replace("Z", "")
    if strip_offset:
        raw = raw.split("+")[0]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def usage_fresh(billing: dict, ttl: float) -> bool:
    """The usage reading was taken less than ttl seconds ago."""
    when = parse_ts(billing.get("checked_at") or billing.get("fetched_at"))
    return bool(when) and datetime.now(timezone.utc) - when < timedelta(seconds=ttl)


def as_float(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        value = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def as_int(v) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return None


def positive_int(v) -> int | None:
    """A count from a provider: a finite number above zero, truncated."""
    value = as_float(v)
    return int(value) if value is not None and value > 0 else None


def refresh_lock(locks: dict, cred_id: int) -> asyncio.Lock:
    """The lock that serializes token refreshes of one credential. Never
    evicted: an unlocked lock may still have a waiter about to acquire it, and
    a second lock would let two refreshes spend the same rotating refresh
    token. One per credential is small. Each pool passes its own table."""
    lock = locks.get(cred_id)
    if lock is None:
        lock = locks[cred_id] = asyncio.Lock()
    return lock


def disable_credential(cred_id: int, *, extra: str | None = None) -> None:
    """Take a credential out of routing (accounts.disable_credential: extra
    is the stored value the failing call read)."""
    accounts.disable_credential(cred_id, extra=extra)


def note_pool_invoke(cred_id: int) -> None:
    db.execute("UPDATE credentials SET pool_invokes = COALESCE(pool_invokes, 0) + 1 WHERE id=?", (cred_id,))


def find_key(source_id: int, key: str, *, extra_field: str = ""):
    """The credential row of this source holding `key` (as its secret, or in
    extra[extra_field] when given), or None."""
    for row in db.fetchall("SELECT * FROM credentials WHERE source_id=?", (source_id,)):
        try:
            if extra_field and accounts.cred_extra(row).get(extra_field) == key:
                return row
            if accounts.cred_secret(row) == key:
                return row
        except Exception:
            continue
    return None


def ensure_source(type_id: str, name: str, config: dict | None = None) -> int:
    """The id of this provider's sources row, created on first use."""
    row = db.fetchone_memo("SELECT id FROM sources WHERE type=?", (type_id,))
    if row:
        return row["id"]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,?,?,?)",
            (type_id, name, 1, json.dumps(config or {}), db.now()),
        )
        return db.last_id(conn)


def billing(extra: dict) -> dict:
    return extra.get("billing") if isinstance(extra.get("billing"), dict) else {}


def own_rows(source_id: int, user_id: int) -> list:
    return db.fetchall_memo(
        "SELECT * FROM credentials WHERE source_id=? AND donated_by=? AND disabled=0", (source_id, user_id)
    )


def own_access(source_id: int, user_id: int, authed, eligible=None, *, skip_unreadable: bool = False) -> dict:
    """A user's access from their own accounts alone: "ok" when one is authed
    (authed(row, extra)) and eligible (eligible(row, extra), e.g. on a paid
    plan); else "no_sub" if one was authed, "unauthed" if none, "none" when
    they linked nothing. skip_unreadable: a row whose secret cannot be
    decrypted is skipped instead of failing the whole check."""
    rows = own_rows(source_id, user_id)
    if not rows:
        return {"ok": False, "reason": "none"}
    saw_authed = False
    for row in rows:
        try:
            extra = accounts.cred_extra(row)
            if not authed(row, extra):
                continue
        except (DecryptionError, accounts.CredentialDataError):
            if skip_unreadable:
                continue
            raise
        saw_authed = True
        if eligible is None or eligible(row, extra):
            return {"ok": True, "reason": "ok"}
    return {"ok": False, "reason": "no_sub" if saw_authed else "unauthed"}


def live_row(row, authed, eligible=None) -> bool:
    """One account another user could be served by right now: not held out of
    the pool, authed(row, extra), and eligible(row, extra) when given."""
    from ..sources.grok import is_held

    if is_held(row):
        return False
    extra = accounts.cred_extra(row)
    return bool(authed(row, extra)) and (eligible is None or bool(eligible(row, extra)))
