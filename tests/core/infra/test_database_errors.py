"""is_lock_conflict decides whole-transaction retries: a deadlock victim
(1213) or a lock wait timeout (1205), by the driver's errno, also behind the
TransactionAborted the adapter raises on the next statement. A message that
merely quotes those words never makes a write replayable."""
import pymysql
import pytest

from app import mysql_compat
from app.database_errors import IntegrityError, TransactionAborted, is_lock_conflict


class _Cursor:
    def __init__(self, error):
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args):
        raise self.error


class _Raw:
    """Stands in for a pymysql connection whose statements fail; nothing connects."""

    def __init__(self, error):
        self.encoders = {}
        self.error = error

    def cursor(self):
        return _Cursor(self.error)


def aborted_after(error):
    """The TransactionAborted the adapter raises on the statement after error."""
    conn = mysql_compat.Conn(_Raw(error))
    with pytest.raises(type(error)):
        conn.native("UPDATE t SET a=1")
    with pytest.raises(TransactionAborted) as later:
        conn.native("UPDATE t SET b=2")
    return later.value


@pytest.mark.parametrize("errno", [1213, 1205])
def test_deadlocks_and_lock_wait_timeouts_are_lock_conflicts(errno):
    error = pymysql.err.OperationalError(errno, "rolled back")
    assert is_lock_conflict(error)
    assert is_lock_conflict(aborted_after(error))


def test_other_errors_are_not_lock_conflicts_whatever_their_message_says():
    lost = pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query")
    assert not is_lock_conflict(lost) and not is_lock_conflict(aborted_after(lost))
    try:
        try:
            raise pymysql.err.IntegrityError(1062, "Duplicate entry 'Deadlock found' for key 'name'")
        except pymysql.err.IntegrityError as exc:
            raise IntegrityError(str(exc), errno=1062) from exc
    except IntegrityError as wrapped:
        duplicate = wrapped
    assert "Deadlock found" in str(duplicate) and not is_lock_conflict(duplicate)
    assert not is_lock_conflict(RuntimeError("Lock wait timeout exceeded; try restarting transaction"))
    assert not is_lock_conflict(None)
