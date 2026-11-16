"""Core suite fixtures: one MariaDB schema per test process, emptied per test.

Ask for ``fresh_db`` in any test that touches the database, or ``fresh_db_fk``
for one that needs the real foreign keys (cascades, ON DELETE rules). The schema is a
persistent fuse_fixture_core_* (tests/mariadb_fixture.persistent_database), so
a run pays ~10 s only after the dev schema changes; between tests every table
is emptied in a few milliseconds. Core tests never use SQLite.
"""
import os
import sqlite3

import pytest

from tests.mariadb_fixture import persistent_database, persistent_schema, reset_tables

# app.main mounts data/desktop-releases (runtime data, gitignored). A fresh
# worktree has no data/ yet, and modules importing app.main at collection
# time would fail before any fixture runs.
from app.config import ROOT

(ROOT / "data" / "desktop-releases").mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True, scope="session")
def _no_sqlite():
    def refuse(*_args, **_kwargs):
        raise RuntimeError("core tests run on MariaDB: use the fresh_db fixture, not SQLite")

    real = sqlite3.connect
    sqlite3.connect = refuse
    yield
    sqlite3.connect = real


@pytest.fixture(autouse=True, scope="session")
def _private_push_keys(tmp_path_factory):
    """A finishing run's push hook reads (or creates) data/vapid.json: never
    let tests touch the tree's real keys (regenerating them breaks every
    existing subscription)."""
    from app import push

    real = push._KEY_PATH
    push._KEY_PATH = tmp_path_factory.mktemp("push") / "vapid.json"
    yield
    push._KEY_PATH = real


@pytest.fixture(scope="session")
def _core_schema():
    from app import db

    db._conn = None
    with persistent_database(slot=os.environ.get("PYTEST_XDIST_WORKER", "main")) as schema:
        yield schema


@pytest.fixture
def fresh_db(_core_schema):
    """The attached core schema, empty, with process caches tied to it cleared."""
    from app import chat_runs, chat_store, chat_titles

    reset_tables()
    chat_store.invalidate_committed_cursors()
    chat_titles._due.clear()
    chat_titles._inflight.clear()
    chat_runs._stopping = False
    yield _core_schema


@pytest.fixture(scope="session")
def _core_fk_schema(_core_schema):
    """A second persistent schema carrying every foreign key of the source."""
    from app.mysql_compat import connect

    slot = os.environ.get("PYTEST_XDIST_WORKER", "main") + "_fk"
    with persistent_schema(slot, foreign_keys=True) as (url, schema):
        pool = connect(url, max_connections=8)
        try:
            yield url, schema, pool
        finally:
            pool.close()


@pytest.fixture
def fresh_db_fk(_core_fk_schema):
    """Like fresh_db, on a schema with real foreign keys: inserts need their
    parent rows, and deletes cascade as in production."""
    from app import chat_runs, chat_store, chat_titles, db
    from app.config import settings

    url, schema, pool = _core_fk_schema
    saved = db._conn, settings.database_url
    db._conn, settings.database_url = pool, url
    try:
        reset_tables()
        chat_store.invalidate_committed_cursors()
        chat_titles._due.clear()
        chat_titles._inflight.clear()
        chat_runs._stopping = False
        yield schema
    finally:
        chat_store.invalidate_committed_cursors()
        db._conn, settings.database_url = saved
