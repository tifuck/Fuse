"""Stored model catalogs: one row per source in provider_catalogs.

The in-memory caches in app.sources stay in front; this keeps the last good
list across restarts and says when each source is next due, so the refresh
loop fetches only what is due (sources.refresh_due_catalogs). Times are Unix
seconds.
"""
from __future__ import annotations

import json
import random
import time

from .. import db

BACKOFF_BASE = 60.0  # first retry after a failed fetch, doubling per failure
JITTER = 0.1  # +-10% on every due time, so sources drift apart


def _jitter(seconds: float) -> float:
    return seconds * random.uniform(1 - JITTER, 1 + JITTER)


def _row(row) -> dict:
    try:
        models = json.loads(row["models"] or "[]")
    except (json.JSONDecodeError, TypeError):
        models = []
    return {
        "source_id": int(row["source_id"]), "provider": row["provider"],
        "models": [m for m in models if isinstance(m, str)] if isinstance(models, list) else [],
        "etag": row["etag"] or None, "last_modified": row["last_modified"] or None,
        "fetched_at": float(row["fetched_at"] or 0), "next_due": float(row["next_due"] or 0),
        "failures": int(row["failures"] or 0), "error": row["error"] or "",
    }


def all_rows() -> dict[int, dict]:
    rows = db.fetchall("SELECT source_id, provider, models, etag, last_modified, fetched_at, next_due, failures, error"
                       " FROM provider_catalogs")
    return {int(r["source_id"]): _row(r) for r in rows}


def get(source_id: int) -> dict | None:
    row = db.fetchone("SELECT source_id, provider, models, etag, last_modified, fetched_at, next_due, failures, error"
                      " FROM provider_catalogs WHERE source_id=?", (source_id,))
    return _row(row) if row else None


def saved(source_id: int, provider: str, models: list[str], ttl: float, *, etag: str | None = None,
          last_modified: str | None = None, now: float | None = None) -> None:
    """A successful fetch (or a 304 that kept the list): next due after ttl."""
    now = time.time() if now is None else now
    db.execute(
        "INSERT INTO provider_catalogs (source_id, provider, models, etag, last_modified, fetched_at, next_due,"
        " failures, error) VALUES (?,?,?,?,?,?,?,0,'')"
        " ON CONFLICT(source_id) DO UPDATE SET provider=excluded.provider, models=excluded.models,"
        " etag=excluded.etag, last_modified=excluded.last_modified, fetched_at=excluded.fetched_at,"
        " next_due=excluded.next_due, failures=0, error=''",
        (source_id, provider, json.dumps(list(models)), (etag or "")[:512] or None, (last_modified or "")[:128] or None,
         now, now + _jitter(ttl)),
    )


def failed(source_id: int, provider: str, error: str, ttl: float, *, now: float | None = None) -> float:
    """A failed fetch keeps the stored list; retries back off exponentially
    (60 s, 2 min, 4 min, ...) but never wait longer than the TTL. Returns the
    delay chosen."""
    now = time.time() if now is None else now
    row = get(source_id)
    failures = (row["failures"] if row else 0) + 1
    delay = _jitter(min(ttl, BACKOFF_BASE * 2 ** (failures - 1)))
    if row:
        db.execute("UPDATE provider_catalogs SET failures=?, next_due=?, error=? WHERE source_id=?",
                   (failures, now + delay, str(error)[:300], source_id))
    else:
        db.execute("INSERT INTO provider_catalogs (source_id, provider, models, fetched_at, next_due, failures, error)"
                   " VALUES (?,?,'[]',0,?,?,?)", (source_id, provider, now + delay, failures, str(error)[:300]))
    return delay


def forget(source_ids=None) -> None:
    """Drop stored lists (all, or these sources): the next read fetches."""
    if source_ids is None:
        db.execute("UPDATE provider_catalogs SET models='[]', etag=NULL, last_modified=NULL, next_due=0")
        return
    for sid in source_ids:
        db.execute("UPDATE provider_catalogs SET models='[]', etag=NULL, last_modified=NULL, next_due=0"
                   " WHERE source_id=?", (sid,))


def mark_due(source_id: int) -> None:
    """Fetch this source on the next pass (a first key was just added)."""
    db.execute("UPDATE provider_catalogs SET next_due=0, failures=0 WHERE source_id=?", (source_id,))


def save_detail(source_id: int, provider: str, detail: dict, at: float) -> None:
    """A provider's richer catalog (prices, flags) as fetched at `at`, kept
    beside its list so a restart need not fetch it again."""
    blob = json.dumps({"at": at, "catalog": detail}, separators=(",", ":"))
    db.execute(
        "INSERT INTO provider_catalogs (source_id, provider, models, detail, fetched_at, next_due) VALUES (?,?,'[]',?,0,0)"
        " ON CONFLICT(source_id) DO UPDATE SET detail=excluded.detail",
        (source_id, provider, blob),
    )


def load_detail(source_id: int) -> tuple[float, dict] | None:
    """(fetched at, catalog) as save_detail stored it, or None."""
    row = db.fetchone("SELECT detail FROM provider_catalogs WHERE source_id=? AND detail IS NOT NULL", (source_id,))
    try:
        blob = json.loads(row["detail"]) if row else None
    except (json.JSONDecodeError, TypeError):
        blob = None
    if not isinstance(blob, dict) or not isinstance(blob.get("catalog"), dict):
        return None
    return float(blob.get("at") or 0), blob["catalog"]
