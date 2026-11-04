"""Starred chat models, per user, so every device lists them first.

Kept in users.pool_prefs["favorite_models"], beside the other per-user model
choices; a change publishes favorites.updated to the user's other tabs.
"""
from __future__ import annotations

import json

from . import chat_tx, db
from .chat_schema import ChatValidationError

KEY = "favorite_models"
MAX = 200
ID_MAX = 200


def _prefs(raw) -> dict:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _ids(prefs: dict) -> list[str]:
    items = prefs.get(KEY)
    return [m for m in items if isinstance(m, str)] if isinstance(items, list) else []


def from_prefs(raw) -> list[str]:
    """The favorites in a users.pool_prefs value."""
    return _ids(_prefs(raw))


def list_favorites(user_id: int) -> list[str]:
    row = db.fetchone("SELECT pool_prefs FROM users WHERE id=?", (int(user_id),))
    return from_prefs(row["pool_prefs"] if row else None)


def set_favorite(user_id: int, model, on) -> list[str]:
    if not isinstance(model, str) or not model or len(model) > ID_MAX or any(ord(ch) < 32 for ch in model):
        raise ChatValidationError("invalid model id")
    if not isinstance(on, bool):
        raise ChatValidationError("on must be true or false")
    uid = int(user_id)
    with chat_tx.transaction() as conn:
        row = conn.execute("SELECT pool_prefs FROM users WHERE id=?" + db.for_update(conn), (uid,)).fetchone()
        prefs = _prefs(row["pool_prefs"] if row else None)
        ids = [m for m in _ids(prefs) if m != model]
        if on:
            if len(ids) >= MAX:
                raise ChatValidationError("at most %d favorite models" % MAX)
            ids.append(model)
        prefs[KEY] = ids
        conn.execute("UPDATE users SET pool_prefs=? WHERE id=?", (json.dumps(prefs), uid))
        chat_tx.event(conn, uid, "favorites.updated", payload={"models": ids})
    return ids
