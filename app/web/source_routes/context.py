"""Shared helpers for the /sources route modules: config parsing,
pending sign-in lookups, OAuth token validation and the pool account card."""

from __future__ import annotations

import json
import time
from ... import accounts, stats
from ...sources import grok as grok_mod
from ...sources import chatgpt as chatgpt_mod
from ...sources import devin as devin_mod
from ...sources import kimi as kimi_mod
from ...sources import claude as claude_mod
from ...sources import cursor as cursor_mod
from ...providers.auth_flows import oauth_tokens as _oauth_tokens, terminal_error as _terminal_oauth_error  # noqa: F401
from ..deps import _cursor_pending, _nanogpt_pending, _PENDING_TTL

def _source_cfg(row) -> dict:
    try:
        data = json.loads((row["config"] if row else None) or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _cursor_pending_for(uid: int):
    data = _cursor_pending.get(uid)
    if not data:
        return None
    if time.time() - float(data.get("_started") or 0) > _PENDING_TTL:
        _cursor_pending.pop(uid, None)
        return None
    _clamp_interval(data)
    return data


def _nanogpt_pending_for(uid: int):
    data = _nanogpt_pending.get(uid)
    if not data:
        return None
    if time.time() - float(data.get("_started") or 0) > _PENDING_TTL:
        _nanogpt_pending.pop(uid, None)
        return None
    _clamp_interval(data)
    return data


def _clamp_interval(data: dict) -> None:
    """The auth server controls `interval`; keep poll backoffs inside 3-30s
    before the value reaches a client `retry_after` or the poll page."""
    try:
        data["interval"] = max(3, min(int(data.get("interval") or 5), 30))
    except (TypeError, ValueError, OverflowError):
        data["interval"] = 5


def _pool_card(user, row, *, kind: str, index: int, extra: dict | None = None) -> dict:
    mine = row["donated_by"] == user["id"]
    admin = bool(user["is_admin"])
    extra = extra if isinstance(extra, dict) else {}
    label = row["label"] or f"{kind} account"
    if not mine and not admin:
        label = f"{kind} plan {index}"
    email = ""
    if mine or admin:
        if kind == "ChatGPT":
            email = chatgpt_mod.account_email(extra)
        elif kind == "Grok":
            email = grok_mod.account_email(extra)
        elif kind == "Devin":
            email = devin_mod.account_email(extra)
        elif kind == "Kimi":
            email = kimi_mod.account_email(extra)
        elif kind == "Claude":
            email = claude_mod.account_email(extra)
        elif kind == "Cursor":
            email = cursor_mod.account_email(extra)
        if not email and admin and row["donated_by"] and row["donated_by"] != user["id"]:
            donor = accounts.get_user(row["donated_by"])
            try:
                email = str((donor["email"] if donor is not None else "") or "").strip()
            except (KeyError, IndexError, TypeError):
                email = ""
    return {
        "id": row["id"],
        "label": label,
        "email": email,
        "mine": mine,
        "can_revoke": mine or admin,
        "show_billing": mine or admin,
        "fuse": stats.fuse_view(row),
    }
