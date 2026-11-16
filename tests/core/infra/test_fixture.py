"""The core fixture itself: per-test emptying and the SQLite refusal."""
import sqlite3

import pytest

from app import db


def _insert_user(email):
    db.execute("INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)", (email, "x", db.now()))


@pytest.mark.parametrize("email", ["first@core.test", "second@core.test"])
def test_each_test_starts_empty(fresh_db, email):
    assert db.fetchone("SELECT COUNT(*) AS n FROM users")["n"] == 0
    _insert_user(email)
    assert db.fetchone("SELECT COUNT(*) AS n FROM users")["n"] == 1


def test_migration_markers_survive_the_reset(fresh_db):
    assert db.fetchone("SELECT COUNT(*) AS n FROM schema_meta")["n"] > 0


def test_sqlite_is_refused():
    with pytest.raises(RuntimeError, match="MariaDB"):
        sqlite3.connect(":memory:")


def test_fresh_db_fk_cascades_like_production(fresh_db_fk):
    from tests.core.helpers import quick_user

    uid = quick_user()
    db.execute("INSERT INTO memories(user_id,project_id,scope_key,name,type,description,body,created_at,updated_at)"
               " VALUES(?,NULL,0,'m','user','d','b',?,?)", (uid, db.now(), db.now()))
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    assert db.fetchone("SELECT COUNT(*) AS n FROM memories")["n"] == 0


def test_fresh_db_has_no_foreign_keys(fresh_db):
    # Rows without parents are fine in the default schema.
    db.execute("INSERT INTO memories(user_id,project_id,scope_key,name,type,description,body,created_at,updated_at)"
               " VALUES(?,NULL,0,'m','user','d','b',?,?)", (987654, db.now(), db.now()))
