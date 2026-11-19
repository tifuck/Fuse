"""Scratch MariaDB schemas for the schema tooling: empty fuse_fixture_* schemas
made and dropped by the fixture account, reached by the app's database user
the way init_db and check_schema reach production."""
import re
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from app import db
from app.config import settings
from app.mysql_compat import _raw_connect
from tests.mariadb_fixture import _admin_connect, _source_schema

_AUTO_INCREMENT = re.compile(r" AUTO_INCREMENT=\d+")


def source_schema() -> str:
    """The schema fixtures are cloned from (fuse_dev). While a core fixture
    schema is attached, settings.database_url names that clone instead."""
    from tests.mariadb_fixture import _ACTIVE

    return _ACTIVE[0][1] if _ACTIVE else _source_schema()[1]


def url_for(schema: str) -> str:
    url, _source = _source_schema()
    return urlunsplit(url._replace(path="/" + schema))


@contextmanager
def admin():
    """The fixture account: CREATE/DROP on fuse_fixture_*, SELECT on the source."""
    conn = _admin_connect(source_schema())
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def scratch_schema(*creates):
    """An empty fuse_fixture_* schema holding the given CREATE TABLE statements;
    yields (url, name). Dropped afterwards."""
    name = "fuse_fixture_" + uuid4().hex
    url = url_for(name)
    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{name}`")
        try:
            if creates:
                run(url, "SET FOREIGN_KEY_CHECKS=0", *creates)
            yield url, name
        finally:
            with conn.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{name}`")


def run(url, *statements):
    raw = _raw_connect(urlsplit(url))
    try:
        with raw.cursor() as cur:
            for sql in statements:
                cur.execute(sql)
            rows = cur.fetchall()
        raw.commit()
        return rows
    finally:
        raw.close()


def show_create(conn, schema: str) -> dict:
    """{table: SHOW CREATE TABLE text} with AUTO_INCREMENT counters removed, as
    scripts/dump_schema.sh writes them."""
    out = {}
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'",
                    (schema,))
        for (table,) in sorted(cur.fetchall()):
            cur.execute(f"SHOW CREATE TABLE `{schema}`.`{table}`")
            out[table] = _AUTO_INCREMENT.sub("", cur.fetchone()[1])
    return out


def before_provider_tail(create: str) -> str:
    """chat_runs from schema.sql as it was before 2026-10-20-provider-tail and
    -run-activity: one plain provider_messages where provider_base is now."""
    lines = []
    for line in create.splitlines():
        name = line.strip().split(" ")[0]
        if name in ("`provider_tail`", "`provider_messages`", "`activity`"):
            continue
        lines.append("  `provider_messages` longtext DEFAULT NULL," if name == "`provider_base`" else line)
    return "\n".join(lines)


@contextmanager
def attached(url):
    """Point the application database at url for the block, as a boot would
    (one connection, not a pool), then put the previous one back."""
    saved = db._conn, settings.database_url, settings.database_pool_size
    db._conn = None
    settings.database_url, settings.database_pool_size = url, 1
    try:
        yield
    finally:
        db.close()
        db._conn, settings.database_url, settings.database_pool_size = saved
