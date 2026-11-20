"""Per-request pool writes kept off the hot path: a session pin's durable row
is touched at most once per TOUCH_SECONDS and an unchanged pin is not
rewritten; a provider's background refresh restarts at most once per
MIN_INTERVAL.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from app import db
from app.sources import pool_refresh, pool_session
from app.sources.pool_session import TOUCH_SECONDS, bind, lookup

T0 = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)


def _stamp(at):
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def _stored(session_id):
    row = db.fetchone("SELECT credential_id, updated_at FROM pool_sessions WHERE session_id=?", (session_id,))
    return int(row["credential_id"]), row["updated_at"]


def test_a_pin_row_is_touched_once_its_stamp_is_old_and_rewritten_only_on_a_new_pin(fresh_db):
    soon, later = T0 + timedelta(seconds=TOUCH_SECONDS // 2), T0 + timedelta(seconds=TOUCH_SECONDS + 1)
    bind("grok", "s", 7, now=T0)

    assert lookup("grok", "s", now=soon) == 7
    bind("grok", "s", 7, now=soon)
    assert _stored("s") == (7, _stamp(T0))

    assert lookup("grok", "s", now=later) == 7
    assert _stored("s") == (7, _stamp(later))
    bind("grok", "s", 8, now=later + timedelta(seconds=1))
    assert _stored("s") == (8, _stamp(later + timedelta(seconds=1)))

    # After a restart the row's own stamp decides.
    pool_session.reset_store()
    assert lookup("grok", "s", now=later + timedelta(seconds=2)) == 8
    assert _stored("s")[1] == _stamp(later + timedelta(seconds=1))


def test_a_pool_refresh_restarts_only_after_its_minimum_interval(monkeypatch):
    runs = []
    clock = [1000.0]
    monkeypatch.setattr(pool_refresh.time, "monotonic", lambda: clock[0])

    async def refresh():
        runs.append(clock[0])

    async def body():
        await pool_refresh.wait(pool_refresh.start("core-test", refresh))
        await pool_refresh.wait(pool_refresh.start("core-test", refresh))
        clock[0] += pool_refresh.MIN_INTERVAL
        await pool_refresh.wait(pool_refresh.start("core-test", refresh))
        await pool_refresh.wait(pool_refresh.start("core-test", refresh, min_interval=0))

    asyncio.run(body())
    assert runs == [1000.0, 1000.0 + pool_refresh.MIN_INTERVAL, 1000.0 + pool_refresh.MIN_INTERVAL]
