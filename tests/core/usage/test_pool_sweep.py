"""A pool's usage sweep: only stale accounts, a few at a time, and a 429
pauses the provider's sweeps and its turn in the usage walk."""
import asyncio
from datetime import datetime, timezone

import pytest

from app.sources import pool_refresh, usage_walk
from tests.core.usage._support import pool_account, pool_rows, user


@pytest.fixture
def owner(fresh_db, monkeypatch):
    monkeypatch.setattr(pool_refresh, "_paused_until", {})
    return user()


def _zai(checked):
    return {"api_key": "zk", "billing": {"checked_at": checked, "plan_type": "pro"}}


def test_a_sweep_reads_only_stale_accounts_a_few_at_a_time(owner):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh = pool_account("zai", owner, _zai(now))
    stale = [pool_account("zai", owner, _zai("2026-01-01T00:00:00Z")) for _ in range(10)]
    seen, live, peak = [], [0], [0]

    async def read(row):
        seen.append(int(row["id"]))
        live[0] += 1
        peak[0] = max(peak[0], live[0])
        await asyncio.sleep(0.01)
        live[0] -= 1
        return {}

    asyncio.run(pool_refresh.sweep("zai", pool_rows("zai"), read, "test", ttl=300))

    assert sorted(seen) == stale and fresh not in seen
    assert peak[0] == pool_refresh.CONCURRENCY


def test_a_429_stops_the_sweep_and_pauses_the_walk_for_that_provider(owner):
    for _ in range(10):
        pool_account("zai", owner, _zai("2026-01-01T00:00:00Z"))
    grok = pool_account("grok", owner, {"access_token": "t"})
    seen = []

    async def read(row):
        seen.append(int(row["id"]))
        return {"last_error": "http 429"}

    asyncio.run(pool_refresh.sweep("zai", pool_rows("zai"), read, "test", ttl=300))
    # Reads already in flight finish; no new one starts.
    first = len(seen)
    assert 1 <= first <= pool_refresh.CONCURRENCY

    asyncio.run(pool_refresh.sweep("zai", pool_rows("zai"), read, "test", ttl=300))
    assert len(seen) == first
    assert [int(row["id"]) for _kind, row in usage_walk.usage_targets()] == [grok]
