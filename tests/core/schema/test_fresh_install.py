"""scripts/sql/schema.sql is what a fresh install gets (scripts/init_db.py):
it must match the schema the code runs against, load into an empty schema,
and boot. A schema change applied to fuse_dev without scripts/dump_schema.sh
fails here: fresh installs would get the old tables."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app import accounts, db, db_schema
from app.mysql_compat import SCHEMA_VERSION, Conn, _raw_connect
from app.sources import token_store
from scripts import check_schema, init_db
from tests.core.schema._support import admin, attached, run, scratch_schema, show_create, source_schema, url_for

SCHEMA_TEXT = init_db.SCHEMA_SQL.read_text(encoding="utf-8")
EXPECTED = check_schema.expected_schema(SCHEMA_TEXT)


def _stale(actual: dict, where: str) -> str:
    missing = sorted(set(EXPECTED) - set(actual))
    extra = sorted(set(actual) - set(EXPECTED))
    changed = sorted(t for t in set(EXPECTED) & set(actual) if EXPECTED[t]["create"] != actual[t])
    return (f"scripts/sql/schema.sql does not match {where} (missing {missing}, extra {extra}, changed {changed}); "
            "run scripts/dump_schema.sh after the schema change and commit scripts/sql/schema.sql")


def test_schema_sql_matches_the_schema_the_code_runs_against():
    with admin() as conn:
        actual = show_create(conn, source_schema())
    assert {table: spec["create"] for table, spec in EXPECTED.items()} == actual, _stale(actual, source_schema())
    # Structure only: no rows, and none of the source's schema_meta values
    # (its credential sentinel belongs to that database's APP_SECRET).
    assert "INSERT INTO" not in SCHEMA_TEXT and "DROP TABLE" not in SCHEMA_TEXT


def test_schema_sql_splits_into_one_statement_per_table():
    creates = [s for s in init_db.statements(SCHEMA_TEXT) if s.startswith("CREATE TABLE")]
    assert len(creates) == len(EXPECTED) == SCHEMA_TEXT.count("\nCREATE TABLE ")
    assert [s.split("`")[1] for s in creates] == list(EXPECTED)


@pytest.fixture(scope="module")
def loaded():
    """schema.sql loaded by init_db into an empty schema.

    The load is ~60 CREATE TABLEs, 8-15 s on this host's ZFS HDDs, and the
    drop as long again. The result is kept under a name derived from
    everything that decides it (schema.sql, init_db.py, the version marker),
    so a run pays only when one of them changed; a run that finds no
    completed load (crashed midway, swept) loads again."""
    digest = hashlib.sha256(SCHEMA_TEXT.encode() + Path(init_db.__file__).read_bytes()
                            + SCHEMA_VERSION.encode()).hexdigest()[:12]
    name = f"fuse_fixture_initdb_{digest}"
    url = url_for(name)
    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 300)", (name,))
            assert cur.fetchone()[0] == 1, f"another run held {name} for 5 minutes"
            cur.execute("SELECT schema_name FROM information_schema.schemata"
                        " WHERE schema_name LIKE 'fuse\\_fixture\\_initdb\\_%%'")
            for (other,) in cur.fetchall():
                # Another worktree's run (a different schema.sql or init_db.py)
                # may be using its own load right now: drop only unlocked ones.
                cur.execute("SELECT IS_USED_LOCK(%s)", (other,))
                if other != name and cur.fetchone()[0] is None:
                    cur.execute(f"DROP DATABASE IF EXISTS `{other}`")
            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name='schema_meta'",
                        (name,))
            done = cur.fetchone() is not None
            if done:
                cur.execute(f"SELECT 1 FROM `{name}`.schema_meta WHERE `key`=%s", (SCHEMA_VERSION,))
                done = cur.fetchone() is not None
            result = "kept"
            if not done:
                cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
                cur.execute(f"CREATE DATABASE `{name}`")
                result = init_db.init(url)
        try:
            yield url, name, result, conn
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK(%s)", (name,))


def test_init_db_loads_schema_sql_into_an_empty_schema(loaded):
    url, name, result, conn = loaded
    assert result in ("created", "kept")
    actual = show_create(conn, name)
    assert {table: spec["create"] for table, spec in EXPECTED.items()} == actual, _stale(actual, f"a load into {name}")
    marker = dict(run(url, "SELECT `key`, value FROM schema_meta WHERE `key` IN ('fuse_mariadb_v1','oauth_token_owner')"))
    assert json.loads(marker[SCHEMA_VERSION]) == {"tables": len(EXPECTED), "fresh": True}
    # The fresh install owns its pooled OAuth refresh tokens.
    assert marker["oauth_token_owner"] == name
    # Idempotent: an initialized schema is left alone.
    assert init_db.init(url) == "exists"


def test_a_fresh_schema_boots_and_owns_its_refresh_tokens(loaded):
    url, _name, _result, _conn = loaded
    with attached(url):
        db.connect()
        assert token_store.claim_refresh_ownership() is True
        assert accounts.needs_setup()


def test_init_db_refuses_a_schema_it_did_not_make():
    with scratch_schema("CREATE TABLE other (id INT PRIMARY KEY)") as (url, _name):
        with pytest.raises(RuntimeError, match="not an initialized Fuse schema"):
            init_db.init(url)
        assert [row[0] for row in run(url, "SHOW TABLES")] == ["other"]


def test_a_half_finished_load_is_refused_by_init_db_and_by_boot():
    # The version marker is written last: tables without it are a load that
    # died midway, which neither init_db nor the app may treat as done.
    with scratch_schema(EXPECTED["schema_meta"]["create"]) as (url, _name):
        with pytest.raises(RuntimeError, match="not an initialized Fuse schema"):
            init_db.init(url)
        with attached(url):
            with pytest.raises(RuntimeError, match="not been migrated"):
                db.connect()
        raw = Conn(_raw_connect(urlsplit(url)))
        try:
            with pytest.raises(RuntimeError, match="migration required"):
                db_schema._init(raw)
        finally:
            raw.close()
