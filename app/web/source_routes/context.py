"""Shared helpers for the /sources route modules: config parsing, OAuth
token validation (from app/providers/auth_flows.py) and the pool account card."""

from __future__ import annotations

import json
from ... import accounts, stats
from ...sources import grok as grok_mod
from ...sources import chatgpt as chatgpt_mod
from ...sources import devin as devin_mod
from ...sources import kimi as kimi_mod
from ...sources import claude as claude_mod
from ...sources import cursor as cursor_mod
from ...providers.auth_flows import oauth_tokens as _oauth_tokens, terminal_error as _terminal_oauth_error  # noqa: F401

def _source_cfg(row) -> dict:
    try:
        data = json.loads((row["config"] if row else None) or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _pool_batch(user, rows) -> dict:
    """What _pool_card reads per card (cost coverage, donor emails for an
    admin), for a whole board at once."""
    donors = {row["donated_by"] for row in rows if row["donated_by"]} if user["is_admin"] else set()
    return {"coverage": stats.fuse_coverage(rows), "donors": stats.user_emails(donors)}


def _pool_card(user, row, *, kind: str, index: int, extra: dict | None = None, batch: dict | None = None) -> dict:
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
            if batch is not None:
                email = batch["donors"].get(int(row["donated_by"]), "")
            else:
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
        "fuse": stats.fuse_view(row, coverage=batch["coverage"] if batch is not None else None),
    }
