"""Pool usage/quota refreshes started by chat requests, run off the request.

A chat routes on the stored snapshot instead of waiting for the slowest stale
account; the refresh it starts lands for the requests after it. At most one
refresh per provider runs at a time.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import timedelta

from .. import db
from . import pool_policy
from .pool_session import lookup

logger = logging.getLogger(__name__)

# A provider's refresh starts again at most this long after its last one
# started: each run re-reads every account of the pool, and what it refreshes
# (usage, quota) is kept for minutes anyway.
MIN_INTERVAL = 30.0

# The last task per provider; also the strong reference that keeps a running
# one from being collected.
_running: dict[str, tuple[asyncio.Task, float]] = {}


def _finished(name: str, task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.warning("%s pool refresh failed", name, exc_info=task.exception())


def start(name: str, refresh, *, min_interval: float = MIN_INTERVAL) -> asyncio.Task:
    """Run refresh() in the background unless this provider's last one is
    still running or started under min_interval ago; returns that task then."""
    last = _running.get(name)
    if last is not None and last[0].get_loop() is asyncio.get_running_loop() and (
            not last[0].done() or time.monotonic() - last[1] < min_interval):
        return last[0]
    task = asyncio.get_running_loop().create_task(refresh(), name=f"pool-refresh-{name}")
    _running[name] = (task, time.monotonic())
    task.add_done_callback(lambda done: _finished(name, done))
    return task


async def wait(task: asyncio.Task) -> None:
    """Wait for a started refresh; its failure is already logged. Cancelling
    the waiter leaves the refresh running."""
    try:
        await asyncio.shield(task)
    except Exception:
        pass


# Request-path token refresh (refresh_due(rows)): per provider, the accounts
# last scanned (ids and stored extras) and the earliest time one of their
# tokens comes due. A request rescans only once an account changed (a new
# token, a re-link, an account added or disabled) or that time has passed;
# the background refresh loop still scans every cycle.
_scans: dict[str, tuple[int, float]] = {}


def _accounts_key(rows) -> int:
    return hash(tuple(sorted((int(row["id"]), str(row["extra"] or "")) for row in rows)))


def scan_due(name: str, rows) -> bool:
    seen = _scans.get(name)
    return seen is None or seen[0] != _accounts_key(rows) or time.time() >= seen[1]


def scanned(name: str, rows, due_at) -> None:
    """due_at: when each scanned account's token comes due (Unix seconds)."""
    _scans[name] = (_accounts_key(rows), min(due_at, default=math.inf))


# A request waits only for tokens that expire within URGENT (or already did);
# the rest of what is due refreshes in the background, one refresh per account
# at a time. After a failed one, requests leave that account alone for
# RETRY_AFTER seconds; the background loop still retries it every cycle.
URGENT = timedelta(seconds=60)
RETRY_AFTER = 60.0
_refreshing: dict[tuple[str, int], asyncio.Task] = {}
_retry_at: dict[tuple[str, int], float] = {}


def _refreshed(key: tuple[str, int], what: str, task: asyncio.Task) -> None:
    if _refreshing.get(key) is task:
        del _refreshing[key]
    error = None if task.cancelled() else task.exception()
    if task.cancelled() or error is not None or not task.result():
        _retry_at[key] = time.monotonic() + RETRY_AFTER
    else:
        _retry_at.pop(key, None)
    if error is not None:
        logger.warning("%s failed for credential %s", what, key[1], exc_info=error)


async def refresh_tokens(name: str, due, urgent, refresh_one, what: str) -> bool:
    """A request's due tokens: waits for the urgent ones and starts the rest
    in the background. True when the request waited for a refresh."""
    loop = asyncio.get_running_loop()
    now = time.monotonic()
    urgent_ids = {int(cred["id"]) for cred in urgent}
    waits = []
    for cred in due:
        key = (name, int(cred["id"]))
        task = _refreshing.get(key)
        if task is None or task.done() or task.get_loop() is not loop:
            if now < _retry_at.get(key, 0.0):
                continue
            task = _refreshing[key] = loop.create_task(refresh_one(cred), name=f"token-refresh-{name}-{key[1]}")
            task.add_done_callback(lambda done, key=key: _refreshed(key, what, done))
        if key[1] in urgent_ids:
            waits.append(task)
    if waits:
        await asyncio.wait(waits)
    return bool(waits)


async def pool_rows(source_id: int, refresh_due) -> list:
    """A request's pool (the source's enabled accounts) once any due token in
    it is refreshed; read again when a refresh ran."""
    def read():
        return db.fetchall("SELECT * FROM credentials WHERE source_id=? AND disabled=0", (source_id,))

    rows = await asyncio.to_thread(read)
    if await refresh_due(rows):
        rows = await asyncio.to_thread(read)
    return rows


def pins(kind: str, session_id, affinity_id, user_id, pinned=None) -> tuple:
    """A pool request's (prefer_id, hint_id, primary_id): its session pin
    (`pinned` when the caller already knows it), its affinity pin when it has
    no session pin, and the caller's primary account. Call it in one thread."""
    prefer_id = pinned if pinned is not None else lookup(kind, session_id)
    hint_id = lookup(kind, affinity_id) if prefer_id is None and affinity_id else None
    return prefer_id, hint_id, pool_policy.primary_account(user_id, kind)


_noting: set[asyncio.Task] = set()


def _noted(task: asyncio.Task) -> None:
    _noting.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.warning("Counting a pool invoke failed", exc_info=task.exception())


def note_invoke(note, cred_id) -> None:
    """Run note(cred_id) (the pool_invokes counter) in a thread without
    holding up the upstream request."""
    task = asyncio.get_running_loop().create_task(asyncio.to_thread(note, cred_id))
    _noting.add(task)
    task.add_done_callback(_noted)
