"""Sign-ins in progress, one per user and provider, in provider_logins.

What a provider's sign-in must remember between its start and its finish (a
device code and its poll interval, a PKCE verifier and state) is kept here,
encrypted, so a restart in the middle of a sign-in does not lose it. Rows
live 15 minutes from their start; the refresh loop sweeps expired ones.

Each stored sign-in carries a token (`_login`). claim() removes the row only
while it still holds the same attempt: a poll that finishes after the user
cancelled or restarted the sign-in must not link the account.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time

from .. import db
from ..crypto import decrypt, encrypt

TTL = 900


def _state_hash(data: dict) -> str | None:
    state = data.get("state")
    return hashlib.sha256(state.encode()).hexdigest() if isinstance(state, str) and state else None


def clamp_interval(data: dict) -> None:
    """The auth server controls `interval`; keep poll backoffs inside 3-30 s
    before the value reaches a client `retry_after` or the poll page."""
    try:
        data["interval"] = max(3, min(int(data.get("interval") or 5), 30))
    except (TypeError, ValueError, OverflowError):
        data["interval"] = 5


def put(user_id: int, provider: str, data: dict) -> dict:
    """Start (or restart) this user's sign-in with a provider; replaces any
    earlier one. Returns the stored data, its token included."""
    data = dict(data)
    data.setdefault("_started", time.time())
    data["_login"] = secrets.token_hex(16)
    started = float(data["_started"])
    db.execute(
        "INSERT INTO provider_logins (user_id, provider, token, state_hash, data, created_at, expires_at)"
        " VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id, provider) DO UPDATE SET token=excluded.token,"
        " state_hash=excluded.state_hash, data=excluded.data, created_at=excluded.created_at,"
        " expires_at=excluded.expires_at",
        (int(user_id), provider, data["_login"], _state_hash(data), encrypt(json.dumps(data)), started, started + TTL),
    )
    return data


def _decode(row) -> dict | None:
    try:
        data = json.loads(decrypt(row["data"]))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def get(user_id: int, provider: str, *, clamp: bool = False) -> dict | None:
    """This user's live sign-in with a provider, or None (none, expired or
    unreadable). clamp: a device flow, whose interval is kept in bounds."""
    row = db.fetchone("SELECT data, expires_at FROM provider_logins WHERE user_id=? AND provider=?",
                      (int(user_id), provider))
    if not row:
        return None
    data = _decode(row)
    if data is None or float(row["expires_at"]) < time.time():
        drop(user_id, provider)
        return None
    if clamp:
        clamp_interval(data)
    return data


def update(user_id: int, provider: str, data: dict) -> None:
    """Save a change to a live sign-in (a slower poll interval), only if it
    is still the same attempt."""
    db.execute("UPDATE provider_logins SET data=? WHERE user_id=? AND provider=? AND token=?",
               (encrypt(json.dumps(data)), int(user_id), provider, str(data.get("_login") or "")))


def claim(user_id: int, provider: str, data: dict) -> bool:
    """End this attempt to finish it: True only for the caller that removed
    it while it was still current (not cancelled, replaced or claimed)."""
    with db.tx() as conn:
        cursor = conn.execute("DELETE FROM provider_logins WHERE user_id=? AND provider=? AND token=?",
                              (int(user_id), provider, str(data.get("_login") or "")))
        return bool(getattr(cursor, "rowcount", 0))


def drop(user_id: int, provider: str) -> None:
    db.execute("DELETE FROM provider_logins WHERE user_id=? AND provider=?", (int(user_id), provider))


def by_state(provider: str, state: str) -> tuple[int, dict] | None:
    """(user id, data) of the live sign-in an OAuth callback's state names."""
    if not state:
        return None
    digest = hashlib.sha256(state.encode()).hexdigest()
    row = db.fetchone("SELECT user_id, data, expires_at FROM provider_logins WHERE provider=? AND state_hash=?",
                      (provider, digest))
    data = _decode(row) if row and float(row["expires_at"]) >= time.time() else None
    if data is None or not secrets.compare_digest(str(data.get("state") or ""), state):
        return None
    return int(row["user_id"]), data


async def sweep() -> None:
    """Refresh-loop job: drop sign-ins nobody finished."""
    db.execute("DELETE FROM provider_logins WHERE expires_at < ?", (time.time(),))
