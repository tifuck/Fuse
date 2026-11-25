"""What the chat page knows about the pool account that served a chat.

Accounts are named as the Sources page names them: the caller's own accounts
(every account, for an admin) by their provider email or label, anyone
else's as "<Pool> plan <n>" by its place among the accounts the caller can
reach. The ids are the ones the Sources sharing dialog already lists.

The thread keeps the raw record (compaction state "pool"); public() turns it
into the page's view: the serving account's plan limits, its model and
product limits, every account of the pool this user can reach, and the
user's own sharing settings for the pool, which the card can change. Money
left on an account (a prepaid balance) shows only to its owner and admins,
as on Sources.

A model outside a pool gets the same view for the caller's own key when its
provider reports what is left on it (OpenRouter, keyed presets with a
balance route): one account, no sharing settings (prefs None).
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
        nxt = int(raw.get("next") or 0) or None
    except (TypeError, ValueError):
        return None
    if cred <= 0:
        return None
    reason = raw.get("reason") if raw.get("reason") in REASONS else ""
    return {"cred": cred, "source": raw["source"], "model": str(raw.get("model") or "")[:200],
            "prev": prev, "reason": reason, "next": nxt if nxt != cred else None}


def state(prior, cred_id: int, kind: str, model: str) -> dict:
    """The thread's record after a round that `cred_id` served. A round
    still on the record's account keeps a pending pick (next): that request
    was in flight when the user picked."""
    prior = parse(prior)
    cred = int(cred_id)
    if prior and prior["source"] == kind and prior["cred"] == cred:
        return {**prior, "model": model}
    switched = prior["cred"] if prior and prior["source"] == kind else None
    return {"cred": cred, "source": kind, "model": model, "prev": switched,
            "reason": _reason(switched) if switched else "", "next": None}


def chose(prior, cred_id: int, kind: str, model: str, running: bool) -> dict:
    """The thread's record after the user picks `cred_id`: current at once in
    an idle chat; while a turn runs, its request in flight stays on the
    account it started on, so the pick waits as next until a request uses it
    (state). Picking the current account again cancels a pending pick."""
    st = parse(prior)
    cred = int(cred_id)
    if not running or not st or st["source"] != kind:
        return {**state(prior, cred, kind, model), "next": None}
    return {**st, "next": None if cred == st["cred"] else cred}


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
    admin = _is_admin(user_id)
    own = row is not None and pool_policy.donated_by(row) == int(user_id)
    windows = _windows(billing)
    return {
        "model": st["model"],
        "provider": kind,
        "pool": label,
        "id": st["cred"],
        "name": names.get(st["cred"], f"{label} account"),
        "own": own,
        "plan": _plan(billing),
        "windows": windows,
        "limits": _limits(billing),
        "balance": _balance(billing, bool(windows)) if own or admin else None,
        "accounts": [_account(r, names, st, settings, user_id, admin) for r in reachable[:MAX_ACCOUNTS]],
        "ready": sum(1 for i in used if not pool_pick.cooling_down(i)),
        "size": len(used),
        "prefs": settings,
        "hop": {"from": names.get(st["prev"], f"another {label} account"), "reason": st["reason"]} if st["prev"] else None,
        # The user's pick while a turn runs: its next request goes there.
        "next": {"id": st["next"], "name": names.get(st["next"], f"another {label} account")} if st["next"] else None,
    }


def _account(row, names: dict[int, str], st: dict, settings: dict, user_id: int, admin: bool = False) -> dict:
    """One line of the card's account list."""
    cid = int(row["id"])
    billing = _billing(row)
    windows = _windows(billing)
    peak = max((w["percent"] for w in windows), default=None)
    own = pool_policy.donated_by(row) == int(user_id)
    until = None
    if cid == st["cred"]:
        status = "current"
    elif cid == st["next"]:
        status = "next"
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
    return {"id": cid, "name": names.get(cid, ""), "own": own,
            "plan": _plan(billing), "percent": peak, "status": status, "until": until,
            "balance": _balance(billing, bool(windows)) if own or admin else None,
            "primary": settings["primary"] == cid}


def _is_admin(user_id: int) -> bool:
    user = accounts.get_user(user_id)
    return bool(user and user["is_admin"])


def _money(amount, currency: str = "USD") -> dict | None:
    value = _num(amount)
    return {"amount": round(value, 2), "currency": str(currency or "USD")[:8]} if value is not None else None


def _balance(billing: dict, has_windows: bool) -> dict | None:
    """Money left on the account (prepaid_cents): shown when there is some,
    or when it is all the account reports."""
    cents = _num(billing.get("prepaid_cents"))
    if cents is None or (cents <= 0 and has_windows):
        return None
    return _money(cents / 100)


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
    # Some providers list each plan window again as a product (ChatGPT, Z.AI, NanoGPT).
    planned = {str(w.get("name") or w.get("label") or "") for w in billing.get("windows") or [] if isinstance(w, dict)}
    for product in billing.get("products") or []:
        if not isinstance(product, dict):
            continue
        pct = _num(product.get("usage_percent"))
        name = str(product.get("product") or product.get("name") or "")
        if pct is None or not name or name in planned:
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
    if not picked:
        return None
    if picked[0].type_id == "openrouter":
        return await _openrouter_view(picked[0], user_id, model)
    if getattr(getattr(picked[0], "preset", None), "balance", ""):
        return await _keyed_view(picked[0], user_id, model)
    if picked[0].type_id not in POOL_TYPES or not hasattr(picked[0], "_pick"):
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



async def switch(user_id: int, model: str, thread_id: str | None, cred_id: int, *, running: bool = False) -> dict | None:
    """Pin the chat to another account of its pool: its next request goes to
    that account and starts a cold prompt cache. running: a turn is under way,
    so the badge shows the account in use and this pick as next (chose).
    Without a thread (a chat not sent yet) nothing is pinned: the badge
    previews the account, and the page pins it once the chat exists.
    Returns the badge, or None when the model is not a pool's or the account
    is not one the caller's requests may use."""
    from . import POOL_TYPES, pick
    from .chatgpt_catalog import base_slug

    picked = await pick(model, user_id=user_id)
    if not picked or picked[0].type_id not in POOL_TYPES or not hasattr(picked[0], "_pick"):
        return None
    src, upstream = picked
    kind = src.type_id
    rows = await asyncio.to_thread(db.fetchall, "SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id",
                                   (src.source_id,))
    usable = await asyncio.to_thread(pool_policy.filter_credentials, rows, kind, user_id)
    if int(cred_id) not in {int(r["id"]) for r in usable}:
        return None
    if not thread_id:
        view = await asyncio.to_thread(public, user_id, {"cred": int(cred_id), "source": kind, "model": model})
        if view is not None:
            view["preview"] = True
        return view
    scope_model = base_slug(upstream) if kind == "chatgpt" else upstream
    # The chat runtime's cache key for a conversation (chat_runtime: "thread:<id>").
    pool_session.bind(kind, pool_session.related_session_id(f"thread:{thread_id}", user_id=user_id, model=scope_model),
                      int(cred_id), chosen=True)
    # The thread's record moves too, so the chat shows the new account before its next reply.
    from .. import chat_checkpoint

    record = await asyncio.to_thread(chat_checkpoint.save_pool, user_id, thread_id,
                                     lambda prior: chose(prior, cred_id, kind, model, running))
    return await asyncio.to_thread(public, user_id, record) if record else None


def _solo(kind: str, label: str, row, model: str, user_id: int, windows: list[dict], balance: dict | None) -> dict:
    """The badge for one key outside a pool: no other accounts, no sharing."""
    return {"model": model, "provider": kind, "pool": label, "id": int(row["id"]),
            "name": str(row["label"] or f"{label} key")[:120], "own": pool_policy.donated_by(row) == int(user_id),
            "plan": "", "windows": windows, "limits": [], "balance": balance, "accounts": [], "ready": 0, "size": 0,
            "prefs": None, "hop": None}


async def _openrouter_view(src, user_id: int, model: str) -> dict | None:
    """The caller's OpenRouter key: its spend limit as a window and the money
    left (the account's credits when the key may read them, else the limit's
    remainder). None when OpenRouter reports neither."""
    from . import openrouter

    owned = await asyncio.to_thread(openrouter.pick_key_for, user_id, src.source_id)
    if not owned:
        return None
    cred = owned[0]
    info = await openrouter.refresh_key_info(cred) or {}
    budget = info.get("effective_budget") if isinstance(info.get("effective_budget"), dict) else {}
    limit = _num(budget.get("limit_usd")) if budget else _num(info.get("limit"))
    left = _num(budget.get("remaining_usd")) if budget else _num(info.get("limit_remaining"))
    windows = []
    if limit and left is not None:
        interval = str((budget.get("reset_interval") if budget else info.get("limit_reset")) or "")
        win = _window({"percent": (1 - left / limit) * 100, "label": f"{interval.capitalize()} limit" if interval else "Key limit",
                       "resets_at": budget.get("resets_at")}, time.time(), named_first=True)
        windows = [win] if win else []
    credits = _num(info.get("credits_remaining"))
    balance = _money(credits if credits is not None else left)
    if not windows and balance is None:
        return None
    return _solo("openrouter", "OpenRouter", cred, model, user_id, windows, balance)


async def _keyed_view(src, user_id: int, model: str) -> dict | None:
    """The key the caller's next request uses, for presets with a balance
    route. A site key's balance is the admin's business."""
    from . import keyed

    probe = copy.copy(src)
    rows = await asyncio.to_thread(probe.usable, user_id)
    if not rows:
        return None
    row = rows[0]
    if pool_policy.donated_by(row) != int(user_id) and not await asyncio.to_thread(_is_admin, user_id):
        return None
    stored = await keyed.refresh_balance(src.preset, row) or {}
    balance = _money(stored.get("amount"), stored.get("currency") or "USD")
    if balance is None:
        return None
    return _solo(src.type_id, src.preset.label, row, model, user_id, [], balance)
