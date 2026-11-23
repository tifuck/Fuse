"""What the chat page knows about the pool account that served a chat.

Accounts are named as the Sources page names them: the caller's own accounts
(every account, for an admin) by their provider email or label, anyone
else's as "<Pool> plan <n>" by its place among the accounts the caller can
reach. The ids are the ones the Sources sharing dialog already lists.

The thread keeps the raw record (compaction state "pool"); public() turns it
into the page's view: the serving account's plan limits, its model and
product limits, every account of the pool this user can reach, and the
user's own sharing settings for the pool, which the card can change.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import re
import time
from datetime import datetime

from .. import accounts, db
from ..crypto import DecryptionError
from . import pool_pick, pool_policy, pool_refresh, pool_session

REASONS = ("limit", "error", "")
MAX_WINDOWS = 4
# The card lists this many accounts at most; a pool rarely holds more.
MAX_ACCOUNTS = 40
# Grok and Cursor name their per-product meters by internal ids.
PRODUCT_NAMES = {"GrokBuild": "Build", "GrokChat": "Chat", "GrokVoice": "Voice", "GrokImagine": "Imagine", "GrokAPI": "API"}
# The usage refresh a chat request starts for its pool (pool_refresh), by
# module and function. An idle chat's meter starts it too, so its numbers do
# not wait for a reply; each skips accounts read within their TTL.
POOL_REFRESH = {"chatgpt": "refresh_pool_usage", "claude": "refresh_pool_quota", "cursor": "refresh_pool_quota",
                "devin": "refresh_pool_status", "grok": "refresh_pool_billing", "kimi": "refresh_pool_quota",
                "mimo": "refresh_pool_quota", "nanogpt": "refresh_pool_usage", "refuseless": "refresh_pool_usage",
                "zai": "refresh_pool_quota"}


def parse(raw) -> dict | None:
    """The stored record, or None when it is missing or malformed."""
    if not isinstance(raw, dict) or raw.get("source") not in pool_policy.POOL_KIND_SET:
        return None
    try:
        cred = int(raw.get("cred") or 0)
        prev = int(raw.get("prev") or 0) or None
    except (TypeError, ValueError):
        return None
    if cred <= 0:
        return None
    reason = raw.get("reason") if raw.get("reason") in REASONS else ""
    return {"cred": cred, "source": raw["source"], "model": str(raw.get("model") or "")[:200],
            "prev": prev, "reason": reason}


def state(prior, cred_id: int, kind: str, model: str) -> dict:
    """The thread's record after a round that `cred_id` served."""
    prior = parse(prior)
    cred = int(cred_id)
    if prior and prior["source"] == kind and prior["cred"] == cred:
        return {**prior, "model": model}
    switched = prior["cred"] if prior and prior["source"] == kind else None
    return {"cred": cred, "source": kind, "model": model, "prev": switched,
            "reason": _reason(switched) if switched else ""}


def _reason(cred_id: int) -> str:
    """Why the chat left this account, read from its cooldown right after."""
    if pool_pick._limit_cooldown(cred_id):
        return "limit"
    return "error" if pool_pick.cooling_down(cred_id) else ""


def _emails(kind: str):
    """The provider's account_email(extra), for pools whose accounts have one."""
    from . import chatgpt, claude, cursor, devin, grok, kimi

    mod = {"chatgpt": chatgpt, "claude": claude, "cursor": cursor, "devin": devin, "grok": grok, "kimi": kimi}.get(kind)
    return getattr(mod, "account_email", None)


def _extra(row) -> dict:
    if row is None:
        return {}
    try:
        return accounts.cred_extra(row)
    except (DecryptionError, accounts.CredentialDataError):
        return {}


def _names(reachable, kind: str, user_id: int) -> dict[int, str]:
    """Each reachable account's name, as the Sources page shows it."""
    user = accounts.get_user(user_id)
    admin = bool(user and user["is_admin"])
    label = pool_policy.POOL_LABELS.get(kind, kind)
    email_of = _emails(kind)
    donors = {}
    if admin:
        ids = sorted({d for d in (pool_policy.donated_by(r) for r in reachable) if d and d != int(user_id)})
        if ids:
            rows = db.fetchall(f"SELECT id, email FROM users WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))
            donors = {int(r["id"]): str(r["email"] or "").strip() for r in rows}
    names = {}
    for index, row in enumerate(reachable, 1):
        donor = pool_policy.donated_by(row)
        mine = donor == int(user_id)
        if not (mine or admin):
            names[int(row["id"])] = f"{label} plan {index}"
            continue
        email = str(email_of(_extra(row)) or "") if email_of else ""
        if not email and not mine:
            email = donors.get(donor or 0, "")
        names[int(row["id"])] = (email or row["label"] or f"{label} account")[:120]
    return names


def prefs(user_id: int, kind: str, ids: list[int]) -> dict:
    """The user's sharing settings for this pool, as the Sources dialog edits them."""
    ui = pool_policy.ui_state(user_id, kind)
    choice = pool_policy.account_choice(user_id, kind)
    used = [i for i in ids if i not in choice["skip"]]
    return {
        "can_override": ui["can_override"],
        "open_access": ui["open_access"],
        "share": ui["share"],
        "use": ui["use"],
        "saved_share": ui["saved_share"],
        "saved_use": ui["saved_use"],
        "contributor": pool_policy.contributes(user_id, kind) if ui["pool_shared"] else False,
        "skip": sorted(i for i in choice["skip"] if i in ids),
        "primary": choice["primary"] if choice["primary"] in used else None,
    }


def public(user_id: int, raw) -> dict | None:
    """The badge for this user, or None when the chat has no pool account."""
    st = parse(raw)
    if st is None:
        return None
    kind = st["source"]
    rows = db.fetchall("SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id",
                       (pool_policy.source_id(kind),))
    reachable = pool_policy.filter_credentials(rows, kind, user_id, chosen=False)
    ids = [int(r["id"]) for r in reachable]
    names = _names(reachable, kind, user_id)
    settings = prefs(user_id, kind, ids)
    used = [i for i in ids if i not in settings["skip"]]
    row = next((r for r in rows if int(r["id"]) == st["cred"]), None)
    billing = _billing(row)
    label = pool_policy.POOL_LABELS.get(kind, kind)
    return {
        "model": st["model"],
        "provider": kind,
        "pool": label,
        "id": st["cred"],
        "name": names.get(st["cred"], f"{label} account"),
        "own": row is not None and pool_policy.donated_by(row) == int(user_id),
        "plan": _plan(billing),
        "windows": _windows(billing),
        "limits": _limits(billing),
        "accounts": [_account(r, names, st["cred"], settings, user_id) for r in reachable[:MAX_ACCOUNTS]],
        "ready": sum(1 for i in used if not pool_pick.cooling_down(i)),
        "size": len(used),
        "prefs": settings,
        "hop": {"from": names.get(st["prev"], f"another {label} account"), "reason": st["reason"]} if st["prev"] else None,
    }


def _account(row, names: dict[int, str], current: int, settings: dict, user_id: int) -> dict:
    """One line of the card's account list."""
    cid = int(row["id"])
    billing = _billing(row)
    peak = max((w["percent"] for w in _windows(billing)), default=None)
    until = None
    if cid == current:
        status = "current"
    elif cid in settings["skip"]:
        status = "off"
    elif pool_pick._hard_cooldown(cid):
        status = "paused"
    elif pool_pick._limit_cooldown(cid):
        status, until = "limit", int(pool_pick._cooldown_end(cid))
    elif peak is not None and peak >= pool_pick.USAGE_BUSY:
        status = "full"
    else:
        status = "ready"
    return {"id": cid, "name": names.get(cid, ""), "own": pool_policy.donated_by(row) == int(user_id),
            "plan": _plan(billing), "percent": peak, "status": status, "until": until,
            "primary": settings["primary"] == cid}


def _billing(row) -> dict:
    billing = _extra(row).get("billing")
    return billing if isinstance(billing, dict) else {}


def _plan(billing: dict) -> str:
    raw = str(billing.get("plan_type") or billing.get("tier") or "").strip()[:24]
    return raw[:1].upper() + raw[1:] if raw.islower() else raw


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _epoch(value) -> float | None:
    """A reset time as Unix seconds: epoch seconds or ms, or an ISO string."""
    num = _num(value)
    if num is not None:
        return num / 1000 if num > 1e12 else num if num > 0 else None
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _span(seconds) -> tuple[str, str] | None:
    s = int(_num(seconds) or 0)
    if s <= 0:
        return None
    if s < 86400:
        hours = max(1, round(s / 3600))
        return f"{hours}-hour", f"{hours}h"
    if s < 7 * 86400:
        days = round(s / 86400)
        return ("Daily", "1d") if days == 1 else (f"{days}-day", f"{days}d")
    if s < 28 * 86400:
        return "Weekly", "7d"
    return "Monthly", "30d"


def _short(label: str) -> str:
    low = label.lower()
    hours = re.search(r"(\d+)\s*-?\s*h", low)
    if hours:
        return f"{hours.group(1)}h"
    for word, short in (("week", "7d"), ("month", "30d"), ("day", "1d")):
        if word in low:
            return short
    return low[:4] or "use"


def _window(win, now: float, named_first: bool = False) -> dict | None:
    """A limit for the page. Plan windows are named by their span ("5-hour");
    named_first keeps a model limit's own name ("Fable weekly")."""
    if not isinstance(win, dict):
        return None
    pct = _num(win.get("percent", win.get("usage_percent")))
    if pct is None:
        return None
    reset = _epoch(win.get("resets_at") or win.get("reset_at"))
    if reset and reset <= now:
        # A window past its reset has started over.
        pct, reset = 0.0, None
    span = _span(win.get("seconds"))
    named = str(win.get("label") or win.get("name") or "")[:40]
    label = (named or (span and span[0])) if named_first else ((span and span[0]) or named)
    label = label or "Usage"
    return {"label": label, "short": span[1] if span else _short(label),
            "percent": round(max(0.0, min(100.0, pct))), "reset_at": int(reset) if reset else None}


def _windows(billing: dict) -> list[dict]:
    """The plan's own limits (5-hour, weekly, monthly…): what the badge shows."""
    now = time.time()
    out = [w for w in (_window(win, now) for win in billing.get("windows") or []) if w]
    if not out:
        pct = _num(billing.get("credit_usage_percent"))
        if pct is not None:
            period = str(billing.get("period_type") or "").lower()
            label = "Monthly" if period.startswith("month") else "Plan"
            out.append({"label": label, "short": "30d" if label == "Monthly" else "plan",
                        "percent": round(max(0.0, min(100.0, pct))), "reset_at": _reset(billing.get("period_end"), now)})
    return out[:MAX_WINDOWS]


def _limits(billing: dict) -> list[dict]:
    """Narrower limits: one model's weekly cap (Claude's Fable, ChatGPT's
    Codex models) or one product's share (Cursor's Auto, Grok's Build)."""
    now = time.time()
    out = [w for w in (_window(win, now, named_first=True) for win in billing.get("model_windows") or []) if w]
    for product in billing.get("products") or []:
        if not isinstance(product, dict):
            continue
        pct = _num(product.get("usage_percent"))
        name = str(product.get("product") or product.get("name") or "")
        if pct is None or not name:
            continue
        name = PRODUCT_NAMES.get(name, name)[:40]
        out.append({"label": name, "short": _short(name), "percent": round(max(0.0, min(100.0, pct))), "reset_at": None})
    return out[:8]


def _reset(value, now: float) -> int | None:
    reset = _epoch(value)
    return int(reset) if reset and reset > now else None


async def preview(user_id: int, model: str, thread_id: str | None = None, *, refresh: bool = False) -> dict | None:
    """The badge before a reply: the account the chat's next request goes to.

    A conversation is pinned to the account that served it (pool_session), so
    for a chat with a pin this is exact; otherwise it is the account the
    pool's own picker would choose now (marked preview), which the first
    reply confirms or replaces. Nothing is reserved. refresh (an open chat's
    periodic reread) also starts the pool's usage refresh, as a request would;
    its numbers land for the next read.
    """
    from . import POOL_TYPES, pick

    picked = await pick(model, user_id=user_id)
    if not picked or picked[0].type_id not in POOL_TYPES or not hasattr(picked[0], "_pick"):
        return None
    kind = picked[0].type_id
    if refresh and kind in POOL_REFRESH:
        pool_refresh.start(kind, getattr(importlib.import_module(f"{__package__}.{kind}"), POOL_REFRESH[kind]))
    return await asyncio.to_thread(_preview, picked[0], picked[1], user_id, model, thread_id)


def _preview(src, upstream: str, user_id: int, model: str, thread_id: str | None) -> dict | None:
    from .chatgpt_catalog import base_slug

    kind = src.type_id
    rows = db.fetchall("SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id", (src.source_id,))
    # The picker reads self.creds; a copy keeps the live source untouched.
    probe = copy.copy(src)
    probe.creds = pool_policy.filter_credentials(rows, kind, user_id)
    scope_model = base_slug(upstream) if kind == "chatgpt" else upstream
    pinned = None
    if thread_id:
        # The chat runtime's cache key for a conversation (chat_runtime: "thread:<id>").
        pinned = pool_session.peek(kind, pool_session.related_session_id(f"thread:{thread_id}", user_id=user_id, model=scope_model))
    args = {"prefer_id": pinned, "owner_id": user_id, "primary_id": pool_policy.primary_account(user_id, kind)}
    got = probe._pick(upstream, **args) if kind == "chatgpt" else probe._pick(**args)
    if not got:
        return None
    view = public(user_id, {"cred": got[0]["id"], "source": kind, "model": model})
    if view is not None:
        view["preview"] = int(got[0]["id"]) != pinned
    return view
