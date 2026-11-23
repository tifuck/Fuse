"""What the chat page may know about the pool account that served a chat.

The badge beside the model names the account by a slot letter, never by its
label, email, owner or credential id: shared accounts belong to other users.
Letters follow credential id over the accounts this user can reach in the
pool, so an account keeps its letter while that set stays the same.

The thread keeps the raw record (compaction state "pool"); public() turns it
into the page's view: the serving account's plan limits, its model and
product limits, and every account of the pool this user can reach.
"""

from __future__ import annotations

import re
import time
from datetime import datetime

from .. import accounts, db
from ..crypto import DecryptionError
from . import pool_pick, pool_policy

REASONS = ("limit", "error", "")
MAX_WINDOWS = 4
# The card lists this many accounts at most; a pool rarely holds more.
MAX_ACCOUNTS = 40
# Grok and Cursor name their per-product meters by internal ids.
PRODUCT_NAMES = {"GrokBuild": "Build", "GrokChat": "Chat", "GrokVoice": "Voice", "GrokImagine": "Imagine", "GrokAPI": "API"}


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


def _slot(ids: list[int], cred_id: int) -> str:
    if cred_id not in ids:
        return "?"
    i = ids.index(cred_id)
    return chr(65 + i) if i < 26 else str(i + 1)


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
    skip = pool_policy.account_choice(user_id, kind)["skip"]
    used = [i for i in ids if i not in skip]
    row = next((r for r in rows if int(r["id"]) == st["cred"]), None)
    billing = _billing(row)
    return {
        "model": st["model"],
        "provider": kind,
        "pool": pool_policy.POOL_LABELS.get(kind, kind),
        "slot": _slot(ids, st["cred"]),
        "own": row is not None and pool_policy.donated_by(row) == int(user_id),
        "plan": _plan(billing),
        "windows": _windows(billing),
        "limits": _limits(billing),
        "accounts": [_account(r, ids, st["cred"], skip, user_id) for r in reachable[:MAX_ACCOUNTS]],
        "ready": sum(1 for i in used if not pool_pick.cooling_down(i)),
        "size": len(used),
        "hop": {"from": _slot(ids, st["prev"]), "reason": st["reason"]} if st["prev"] else None,
    }


def _account(row, ids: list[int], current: int, skip, user_id: int) -> dict:
    """One line of the card's account list: no label, email or owner."""
    cid = int(row["id"])
    billing = _billing(row)
    peak = max((w["percent"] for w in _windows(billing)), default=None)
    until = None
    if cid == current:
        status = "current"
    elif cid in skip:
        status = "off"
    elif pool_pick._hard_cooldown(cid):
        status = "paused"
    elif pool_pick._limit_cooldown(cid):
        status, until = "limit", int(pool_pick._cooldown_end(cid))
    elif peak is not None and peak >= pool_pick.USAGE_BUSY:
        status = "full"
    else:
        status = "ready"
    return {"slot": _slot(ids, cid), "own": pool_policy.donated_by(row) == int(user_id),
            "plan": _plan(billing), "percent": peak, "status": status, "until": until}


def _billing(row) -> dict:
    if row is None:
        return {}
    try:
        billing = accounts.cred_extra(row).get("billing")
    except (DecryptionError, accounts.CredentialDataError):
        return {}
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
        return ("Daily", "day") if days == 1 else (f"{days}-day", f"{days}d")
    if s < 28 * 86400:
        return "Weekly", "wk"
    return "Monthly", "mo"


def _short(label: str) -> str:
    low = label.lower()
    hours = re.search(r"(\d+)\s*-?\s*h", low)
    if hours:
        return f"{hours.group(1)}h"
    for word, short in (("week", "wk"), ("month", "mo"), ("day", "day"), ("daily", "day")):
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
            out.append({"label": label, "short": "mo" if label == "Monthly" else "plan",
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
