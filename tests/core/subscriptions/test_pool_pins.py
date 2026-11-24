"""Durable pool pins: a restart honours only pins inside the retention window,
and concurrent binds leave memory and the database on the same account."""
import threading
from datetime import datetime, timedelta, timezone

from app import db
from app.sources import pool_session
from tests.core.helpers import wait_until
from tests.core.subscriptions._support import account, user


def test_an_expired_durable_pin_is_not_honoured_after_a_restart(fresh_db):
    cred = account("claude", user())
    old = datetime.now(timezone.utc) - timedelta(days=pool_session.RETENTION_DAYS + 1)
    pool_session.bind("claude", "sess", cred, now=old)
    pool_session.reset_store()
    assert pool_session.lookup("claude", "sess") is None


class _SeenLock:
    """The module lock, noting which threads had to wait for it."""

    def __init__(self, inner):
        self.inner, self.waited = inner, set()

    def __enter__(self):
        if not self.inner.acquire(blocking=False):
            self.waited.add(threading.current_thread().name)
            self.inner.acquire()

    def __exit__(self, *exc):
        self.inner.release()


def test_concurrent_binds_leave_memory_and_the_database_on_one_account(fresh_db, monkeypatch):
    owner = user()
    first, second = account("claude", owner), account("claude", owner)
    lock = _SeenLock(pool_session._lock)
    monkeypatch.setattr(pool_session, "_lock", lock)
    real_execute = db.execute
    other = threading.Thread(target=pool_session.bind, args=("claude", "sess", second), name="second-bind")

    def execute(sql, args=()):
        if "INSERT INTO pool_sessions" in sql and other.ident is None:
            other.start()  # the second bind runs while the first is about to write its row
            wait_until(lambda: not other.is_alive() or other.name in lock.waited)
        return real_execute(sql, args)

    monkeypatch.setattr(db, "execute", execute)
    pool_session.bind("claude", "sess", first)
    other.join(5)
    monkeypatch.setattr(db, "execute", real_execute)

    in_memory = pool_session.peek("claude", "sess")
    stored = db.fetchone("SELECT credential_id FROM pool_sessions WHERE kind='claude' AND session_id='sess'")
    assert in_memory == int(stored["credential_id"]) == second
