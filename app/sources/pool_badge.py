"""What the chat page may know about the pool account that served a chat.

The badge beside the model names the account by a slot letter, never by its
label, email, owner or credential id: shared accounts belong to other users.
Letters follow credential id over the accounts this user can reach in the
pool, so an account keeps its letter while that set stays the same.

The thread keeps the raw record (compaction state "pool"); public() turns it
into the page's view with the account's latest usage windows.
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
    ids = [int(r["id"]) for r in pool_policy.filter_credentials(rows, kind, user_id, chosen=False)]
    skip = pool_policy.account_choice(user_id, kind)["skip"]
    used = [i for i in ids if i not in skip]
    row = next((r for r in rows if int(r["id"]) == st["cred"]), None)
    return {
        "model": st["model"],
        "provider": kind,
        "pool": pool_policy.POOL_LABELS.get(kind, kind),
        "slot": _slot(ids, st["cred"]),
        "own": row is not None and pool_policy.donated_by(row) == int(user_id),
        "windows": _windows(row),
        "ready": sum(1 for i in used if not pool_pick.cooling_down(i)),
        "size": len(used),
        "hop": {"from": _slot(ids, st["prev"]), "reason": st["reason"]} if st["prev"] else None,
    }


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


def _windows(row) -> list[dict]:
    """The account's usage windows from its last billing snapshot."""
    if row is None:
        return []
    try:
        billing = accounts.cred_extra(row).get("billing")
    except (DecryptionError, accounts.CredentialDataError):
        return []
    if not isinstance(billing, dict):
        return []
    now = time.time()
    out = []
    for win in billing.get("windows") or []:
        if not isinstance(win, dict):
            continue
        pct = _num(win.get("percent", win.get("usage_percent")))
        if pct is None:
            continue
        reset = _epoch(win.get("resets_at") or win.get("reset_at"))
        if reset and reset <= now:
            # A window past its reset has started over.
            pct, reset = 0.0, None
        span = _span(win.get("seconds"))
        label = span[0] if span else str(win.get("label") or win.get("name") or "Usage")[:40]
        out.append({"label": label, "short": span[1] if span else _short(label),
                    "percent": round(max(0.0, min(100.0, pct))), "reset_at": int(reset) if reset else None})
    if not out:
        pct = _num(billing.get("credit_usage_percent"))
        if pct is not None:
            out.append({"label": "Usage", "short": "use", "percent": round(max(0.0, min(100.0, pct))), "reset_at": None})
    return out[:MAX_WINDOWS]
