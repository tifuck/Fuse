"""The MariaDB pool keeps connections back for the event loop thread."""
import asyncio

import pytest

from app import mysql_compat


class _Raw:
    """Stands in for a pymysql connection; nothing connects."""

    open = True

    def __init__(self):
        self.encoders = {}

    def ping(self, reconnect=False):
        pass

    def close(self):
        self.open = False


def test_the_loop_thread_gets_a_connection_while_other_threads_hold_the_rest(monkeypatch):
    monkeypatch.setattr(mysql_compat, "_raw_connect", lambda parsed: _Raw())
    pool = mysql_compat.Pool(None, 6)
    # This test thread runs no event loop: it gets only the general connections.
    held = [pool.checkout(timeout=0.01) for _ in range(6 - mysql_compat.LOOP_RESERVED)]
    with pytest.raises(TimeoutError, match="pool exhausted"):
        pool.checkout(timeout=0.01)

    async def on_loop():
        outer = pool.checkout(timeout=0.01)
        inner = pool.checkout(timeout=0.01)  # a nested db.tx()
        pool.release(inner)
        pool.release(outer)
        return {outer, inner}

    reserved = asyncio.run(on_loop())
    assert len(reserved) == 2 and reserved.isdisjoint(held)
    # Released back to the reserve, still out of reach for other threads.
    with pytest.raises(TimeoutError, match="pool exhausted"):
        pool.checkout(timeout=0.01)

    # The loop thread takes an idle general connection before the reserve.
    pool.release(held[0])

    async def general_first():
        conn = pool.checkout(timeout=0.01)
        pool.release(conn)
        return conn

    assert asyncio.run(general_first()) is held[0]
    pool.close()
