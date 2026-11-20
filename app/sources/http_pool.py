"""Long-lived httpx clients for completion requests.

A new client per request paid DNS, TCP and TLS on every model call. Callers
close each response (proxy.openai_chat does) and never the client, which
returns the connection to the pool. Clients are kept per running event loop,
so a loop that ends (tests run one per case) never hands its client to the
next one.
"""

from __future__ import annotations

import asyncio
import weakref

import httpx

# Completion streams stay open for minutes: a connection cap would queue new
# requests behind them (the per-request clients this replaces had none).
_LIMITS = httpx.Limits(max_connections=None, max_keepalive_connections=20)
_clients: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def client(timeout, *, verify=True) -> httpx.AsyncClient:
    """The shared client for this loop and these settings. Never close it."""
    t = httpx.Timeout(timeout)
    key = (t.connect, t.read, t.write, t.pool, verify)
    per_loop = _clients.setdefault(asyncio.get_running_loop(), {})
    found = per_loop.get(key)
    if found is None or found.is_closed:
        # Looked up at creation: tests patch httpx.AsyncClient per case.
        found = per_loop[key] = httpx.AsyncClient(timeout=t, verify=verify, limits=_LIMITS)
    return found


async def aclose_all() -> None:
    """Close the running loop's clients (app shutdown)."""
    for found in _clients.pop(asyncio.get_running_loop(), {}).values():
        await found.aclose()
