"""The SQLite-to-MariaDB translator app SQL goes through: the rendered SQL for
each rewrite, and on the fixture the semantics SQLite gives that SQL."""
import pytest

from app import db
from app.mysql_compat import _rewrite
from tests.core.helpers import quick_user


@pytest.mark.parametrize("sql, lock_rows, rendered", [
    ("SELECT id FROM t WHERE a IS ?", False, "SELECT `id` FROM `t` WHERE `a` <=> %s"),
    ("INSERT INTO t (a,b) VALUES (?,?) ON CONFLICT(a) DO UPDATE SET b=excluded.b", False,
     "INSERT INTO `t` (`a`, `b`) VALUES (%s, %s) ON DUPLICATE KEY UPDATE `b` = VALUES(`b`)"),
    ("SELECT id FROM t WHERE name LIKE '50%'", False, "SELECT `id` FROM `t` WHERE `name` LIKE '50%%'"),
    ("SELECT id FROM t WHERE name LIKE ? ESCAPE '\\'", False,
     "SELECT `id` FROM `t` WHERE `name` LIKE %s ESCAPE '\\\\'"),
    ("SELECT id FROM chat_runs WHERE id=?", True, "SELECT `id` FROM `chat_runs` WHERE `id` = %s FOR UPDATE"),
    ("SELECT id FROM chat_runs WHERE id=?", False, "SELECT `id` FROM `chat_runs` WHERE `id` = %s"),
    ("SELECT COUNT(*) AS n FROM chat_runs WHERE id=?", True, "SELECT COUNT(*) AS `n` FROM `chat_runs` WHERE `id` = %s"),
    ("SELECT status FROM chat_runs GROUP BY status", True, "SELECT `status` FROM `chat_runs` GROUP BY `status`"),
    ("SELECT id FROM chat_threads WHERE id=?", True, "SELECT `id` FROM `chat_threads` WHERE `id` = %s"),
])
def test_rewrites(sql, lock_rows, rendered):
    assert _rewrite(sql, lock_rows) == rendered


@pytest.mark.parametrize("sql", [
    "SELECT id FROM t WHERE a GLOB ?",
    "SELECT id FROM t WHERE a = ? COLLATE NOCASE",
    "SELECT id FROM t ORDER BY a COLLATE NOCASE",
    "SELECT typeof(a) FROM t",
    "SELECT datetime('now')",
])
def test_sqlite_only_syntax_is_refused_not_passed_through(sql):
    with pytest.raises(ValueError, match="Unported"):
        _rewrite(sql)


@pytest.mark.parametrize("value, quotient", [(-7, -3), (7, 3), (-8, -4), (1, 0), (-1, 0)])
def test_integer_division_truncates_toward_zero_like_sqlite(fresh_db, value, quotient):
    assert db.fetchone("SELECT CAST(? AS INTEGER)/2 AS q", (value,))["q"] == quotient


UPSERT = """INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at) VALUES (?,?,?,?,?)
            ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth
            WHERE push_subscriptions.p256dh <> excluded.p256dh"""


def _keys(endpoint):
    row = db.fetchone("SELECT p256dh, auth FROM push_subscriptions WHERE endpoint=?", (endpoint,))
    return row["p256dh"], row["auth"]


def test_an_upsert_where_guards_every_assignment_with_the_original_row(fresh_db):
    uid = quick_user("ada@core.test")
    endpoint = "https://push.example/a"
    db.execute(UPSERT, (uid, endpoint, "k1", "a1", db.now()))
    # The first assignment changes p256dh; auth's guard must still see k1.
    db.execute(UPSERT, (uid, endpoint, "k2", "a2", db.now()))
    assert _keys(endpoint) == ("k2", "a2")
    # The guard is false on the original row: neither column moves.
    db.execute(UPSERT, (uid, endpoint, "k2", "a3", db.now()))
    assert _keys(endpoint) == ("k2", "a2")


def test_a_batched_upsert_guards_each_row_on_its_own(fresh_db):
    uid = quick_user("ada@core.test")
    now = db.now()
    with db.tx() as conn:
        conn.executemany(UPSERT, [(uid, "https://push.example/a", "k1", "a1", now),
                                  (uid, "https://push.example/b", "k1", "b1", now)])
    with db.tx() as conn:
        conn.executemany(UPSERT, [(uid, "https://push.example/a", "k1", "a2", now),
                                  (uid, "https://push.example/b", "k2", "b2", now)])
    assert _keys("https://push.example/a") == ("k1", "a1")
    assert _keys("https://push.example/b") == ("k2", "b2")
