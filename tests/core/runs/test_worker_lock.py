"""One chat worker per database: a MariaDB named lock, not a file lock, so a
second process on the same database is refused whatever folder it runs from,
and a process whose lock connection dropped takes the lock back or stops
acting as the worker."""
from app.config import settings
from app.main import WorkerLock
from tests.core.helpers import wait_until


def test_second_worker_on_the_same_database_is_refused_and_a_dropped_lock_moves(fresh_db):
    first, second = WorkerLock(settings.database_url), WorkerLock(settings.database_url)
    try:
        assert first.acquire()
        assert not second.acquire()
        assert first.held()
        first._conn._raw.close()  # the lock connection drops: MariaDB releases the lock
        assert second.acquire(wait=5)  # the server frees it once it sees the disconnect
        assert not first.held()
        second.close()
        wait_until(first.held, message="the lock never came back")
    finally:
        first.close()
        second.close()
