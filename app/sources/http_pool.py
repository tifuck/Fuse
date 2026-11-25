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
from http.cookiejar import CookieJar, DefaultCookiePolicy

import httpx

# Completion streams stay open for minutes: a connection cap would queue new
# requests behind them (the per-request clients this replaces had none).
_LIMITS = httpx.Limits(max_connections=None, max_keepalive_connections=20)
_clients: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def no_cookies() -> CookieJar:
    """A jar that keeps nothing: a shared client serves every account, so a
    Set-Cookie from one account's upstream must never ride on the next."""
    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))


def client(timeout, *, verify=True) -> httpx.AsyncClient:
    """The shared client for this loop and these settings. Never close it."""
    t = httpx.Timeout(timeout)
    key = (t.connect, t.read, t.write, t.pool, verify)
    per_loop = _clients.setdefault(asyncio.get_running_loop(), {})
    found = per_loop.get(key)
    if found is None or found.is_closed:
        # Looked up at creation: tests patch httpx.AsyncClient per case.
        found = per_loop[key] = httpx.AsyncClient(
            timeout=t, verify=verify, limits=_LIMITS, cookies=no_cookies()
        )
    return found


# An upstream error body is read only this far: a proxy's error page or a
# misbehaving server can send anything, and only its start is ever shown.
ERROR_BODY_LIMIT = 64 * 1024


async def read_error_body(resp: httpx.Response, limit: int = ERROR_BODY_LIMIT) -> bytes:
    """Up to `limit` bytes of a (streamed) error response's body. Reading it
    again returns the same bytes; the caller still closes the response."""
    kept = resp.extensions.get("fuse_error_body")
    if kept is not None:
        return kept
    try:
        data = resp.content[:limit]
    except httpx.ResponseNotRead:
        chunks, size = [], 0
        async for chunk in resp.aiter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= limit:
                break
        data = b"".join(chunks)[:limit]
    resp.extensions["fuse_error_body"] = data
    return data


async def aclose_all() -> None:
    """Close the running loop's clients (app shutdown)."""
    for found in _clients.pop(asyncio.get_running_loop(), {}).values():
        await found.aclose()
