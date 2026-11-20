"""Pool usage/quota refreshes started by chat requests, run off the request.

A chat routes on the stored snapshot instead of waiting for the slowest stale
account; the refresh it starts lands for the requests after it. At most one
refresh per provider runs at a time.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# Also the strong reference that keeps each running task from being collected.
_running: dict[str, asyncio.Task] = {}


def _finished(name: str, task: asyncio.Task) -> None:
    if _running.get(name) is task:
        del _running[name]
    if not task.cancelled() and task.exception() is not None:
        logger.warning("%s pool refresh failed", name, exc_info=task.exception())


def start(name: str, refresh) -> asyncio.Task:
    """Run refresh() in the background unless this provider's last one is
    still running; returns the running task either way."""
    task = _running.get(name)
    if task is not None and not task.done() and task.get_loop() is asyncio.get_running_loop():
        return task
    task = asyncio.get_running_loop().create_task(refresh(), name=f"pool-refresh-{name}")
    _running[name] = task
    task.add_done_callback(lambda done: _finished(name, done))
    return task


async def wait(task: asyncio.Task) -> None:
    """Wait for a started refresh; its failure is already logged. Cancelling
    the waiter leaves the refresh running."""
    try:
        await asyncio.shield(task)
    except Exception:
        pass
