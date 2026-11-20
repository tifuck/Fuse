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

from .. import db

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


async def pool_rows(source_id: int, refresh_due) -> list:
    """A request's pool (the source's enabled accounts) once any due token in
    it is refreshed; read again when a refresh ran."""
    def read():
        return db.fetchall("SELECT * FROM credentials WHERE source_id=? AND disabled=0", (source_id,))

    rows = await asyncio.to_thread(read)
    if await refresh_due(rows):
        rows = await asyncio.to_thread(read)
    return rows
