"""Disposable MariaDB schema for offline integration fixtures.

Importing this module installs the pymysql guard (``install_guard``): no test
or harness may open a connection whose default schema is one of the real Fuse
schemas (``fuse_dev``/``fuse_live``), including via ``pymysql.Connection``,
``read_default_file`` option files, ``select_db`` or ``USE``, and no statement
may name one of them except a read (SELECT, SHOW CREATE TABLE), which is all
the fixture needs to clone the template schema.

Tests use their own database account, never the app's or root: 'fuse_test'
(@localhost for the fixture's socket connection, @127.0.0.1 for the app's
DATABASE_URL), with ALL on the ``fuse_fixture_%`` schema pattern and SELECT on
the template schema only. scripts/test_sandbox.sh hands its credentials to
the run (FUSE_FIXTURE_DB_USER/PASSWORD, DATABASE_URL); the run never reads
the real .env or config.env.
"""
from contextlib import contextmanager
from configparser import ConfigParser
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
import os
import re
import sys
import warnings

import pymysql

PROTECTED_SCHEMAS = {"fuse_dev", "fuse_live"}

_GUARD_MESSAGE = (
    "tests must not connect to the real {schema!r} schema; "
    "use tests/mariadb_fixture.isolated_database"
)
_USE = re.compile(r"^\s*USE\s+[`'\"]?([A-Za-z0-9_]+)", re.I)
_SCHEMA_NAME = re.compile(r"[A-Za-z0-9_]+")
_FIXTURE_SCHEMA = re.compile(r"fuse_fixture_[A-Za-z0-9_]+")
# String literals are data (a stored value may mention a schema), not names:
# blanked before looking for a protected schema (double quotes are strings too
# in MariaDB's default sql_mode).
_STRING_LITERAL = re.compile(r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.|\"\")*\"", re.S)
_PROTECTED_NAME = re.compile(r"\b(?:" + "|".join(sorted(PROTECTED_SCHEMAS)) + r")\b", re.I)
_DYNAMIC_SQL = re.compile(r"^\s*(?:PREPARE|EXECUTE)\b", re.I)
_READ_STATEMENT = re.compile(r"^\s*(?:SELECT|SHOW\s+CREATE\s+TABLE)\b", re.I)
# A SELECT that writes (INTO OUTFILE/@var), locks rows, or smuggles a second statement.
_NOT_A_READ = re.compile(r"\bINTO\b|\bFOR\s+UPDATE\b|\bLOCK\s+IN\s+SHARE\s+MODE\b|;", re.I)


def _as_text(value):
    return value.decode("utf-8", "replace") if isinstance(value, (bytes, bytearray)) else value


def _refuse(schema):
    name = _as_text(schema)
    if name in PROTECTED_SCHEMAS:
        raise RuntimeError(_GUARD_MESSAGE.format(schema=name))


def _refuse_statement(sql):
    """Any statement naming a protected schema must be a plain read."""
    text = _as_text(sql) or ""
    # Dynamic SQL carries its statement in a string: judge that string too.
    names = text if _DYNAMIC_SQL.match(text) else _STRING_LITERAL.sub("''", text)
    match = _PROTECTED_NAME.search(names)
    if match and (not _READ_STATEMENT.match(text) or _NOT_A_READ.search(text)):
        raise RuntimeError(
            f"tests may only read the real {match.group(0)!r} schema (SELECT, SHOW CREATE TABLE): "
            + text[:120]
        )


def _option_file_database(kwargs):
    """Database resolved from read_default_file/read_default_group, if any."""
    read_file = kwargs.get("read_default_file")
    group = kwargs.get("read_default_group")
    if not read_file and group:
        read_file = "c:\\my.ini" if sys.platform.startswith("win") else "/etc/my.cnf"
    if not read_file:
        return None
    try:
        cfg = ConfigParser()
        cfg.read(os.path.expanduser(read_file))
        return cfg.get(group or "client", "database")
    except Exception:
        return None


def install_guard():
    """Refuse protected schemas at construction, USE and COM_INIT_DB."""
    conn = pymysql.connections.Connection
    if getattr(conn.__init__, "_fuse_guarded", False):
        return
    original_init = conn.__init__
    original_select_db = conn.select_db
    original_query = conn.query

    def guarded_init(self, *args, **kwargs):
        _refuse(kwargs.get("database") or kwargs.get("db"))
        # Refuse before the socket opens when an option file names the schema.
        _refuse(_option_file_database(kwargs))
        original_init(self, *args, **kwargs)
        try:
            _refuse(self.db)
        except RuntimeError:
            force_close = getattr(self, "_force_close", None)
            if force_close is not None:
                force_close()
            raise

    def guarded_select_db(self, db):
        _refuse(db)
        return original_select_db(self, db)

    def guarded_query(self, sql, *args, **kwargs):
        match = _USE.match(_as_text(sql) or "")
        if match:
            _refuse(match.group(1))
        _refuse_statement(sql)
        return original_query(self, sql, *args, **kwargs)

    for wrapped in (guarded_init, guarded_select_db, guarded_query):
        wrapped._fuse_guarded = True
    conn.__init__ = guarded_init
    conn.select_db = guarded_select_db
    conn.query = guarded_query


install_guard()


def _fixture_account():
    return (
        os.environ.get("FUSE_FIXTURE_DB_USER", "fuse_test"),
        os.environ.get("FUSE_FIXTURE_DB_PASSWORD", ""),
        os.environ.get("FUSE_FIXTURE_DB_SOCKET", "/run/mysqld/mysqld.sock"),
    )


def _provisioning_sql(source):
    user, _, _ = _fixture_account()
    return (
        f"CREATE USER '{user}'@'localhost' IDENTIFIED BY '<password>';\n"
        f"CREATE USER '{user}'@'127.0.0.1' IDENTIFIED BY '<password>';\n"
        f"GRANT ALL ON `fuse\\_fixture\\_%`.* TO '{user}'@'localhost', '{user}'@'127.0.0.1';\n"
        f"GRANT SELECT ON `{source}`.* TO '{user}'@'localhost', '{user}'@'127.0.0.1';\n"
        "and put DATABASE_URL=mysql://<user>:<password>@127.0.0.1:3306/<source>, FUSE_FIXTURE_DB_USER,\n"
        "FUSE_FIXTURE_DB_PASSWORD and a test-only APP_SECRET in the root-only file scripts/test_sandbox.sh reads"
    )


def _admin_connect(source):
    user, password, socket_path = _fixture_account()
    try:
        return pymysql.connect(user=user, password=password, unix_socket=socket_path, autocommit=True)
    except pymysql.MySQLError as exc:
        raise RuntimeError(
            f"fixture database account {user!r} is missing or unusable ({exc}).\n"
            "Tests must not hold root database access; provision the limited account once:\n"
            + _provisioning_sql(source)
        ) from exc


def sweep_stale_schemas(max_age_seconds=3600):
    """Best-effort drop of fuse_fixture_* schemas left behind by killed runs.
    Persistent core schemas (fuse_fixture_core_*) are reused across runs and
    managed by persistent_database instead."""
    try:
        admin = _admin_connect("fuse_dev")
        try:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT table_schema FROM information_schema.tables"
                    " WHERE table_schema LIKE 'fuse\\_fixture\\_%'"
                    " AND table_schema NOT LIKE 'fuse\\_fixture\\_core\\_%'"
                    f" AND create_time < NOW() - INTERVAL {int(max_age_seconds)} SECOND"
                    " GROUP BY table_schema"
                )
                for (name,) in cursor.fetchall():
                    if _FIXTURE_SCHEMA.fullmatch(name):
                        cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
        finally:
            admin.close()
    except Exception as exc:
        warnings.warn(f"stale fuse_fixture_* schema sweep skipped: {exc}", stacklevel=2)


_ACTIVE: list[tuple[str, str]] = []
_FOREIGN_KEY = re.compile(
    r"CONSTRAINT `(\w+)` FOREIGN KEY \(((?:`\w+`(?:, )?)+)\) REFERENCES `(\w+)` \(((?:`\w+`(?:, )?)+)\)"
    r"((?: ON (?:DELETE|UPDATE) (?:CASCADE|SET NULL|RESTRICT|NO ACTION|SET DEFAULT))*)"
)
_COLUMNS = re.compile(r"`(\w+)`")


def copy_foreign_keys(*tables):
    """CREATE TABLE ... LIKE omits foreign keys. Add the source schema's foreign
    keys for these tables to the innermost isolated schema (opt-in: most suites
    insert rows without their parents). Read from SHOW CREATE TABLE, which the
    fixture account's SELECT grant allows; information_schema hides them."""
    schema, source = _ACTIVE[-1]
    admin = _admin_connect(source)
    try:
        with admin.cursor() as cursor:
            _add_foreign_keys(cursor, schema, source, tables)
    finally:
        admin.close()


def _source_schema():
    from app.config import settings

    url = urlsplit(settings.database_url)
    # While a fixture schema is attached, settings.database_url names that
    # clone: the template is still the schema it was cloned from.
    source = os.environ.get("FUSE_FIXTURE_SCHEMA_SOURCE") or (_ACTIVE[0][1] if _ACTIVE else url.path.strip("/"))
    if url.scheme != "mysql" or not _SCHEMA_NAME.fullmatch(source):
        raise RuntimeError("A configured MariaDB schema is required for offline fixtures")
    if source == "fuse_live":
        raise RuntimeError(
            "refusing to build fixtures from the production 'fuse_live' schema; "
            "set FUSE_FIXTURE_SCHEMA_SOURCE to a dev/test schema"
        )
    if not url.username:
        raise RuntimeError("DATABASE_URL must include a database user")
    return url, source


_FOREIGN_KEY_LINE = re.compile(r"\s*CONSTRAINT `\w+` FOREIGN KEY ")
_AUTO_INCREMENT = re.compile(r" AUTO_INCREMENT=\d+")


def _without_foreign_keys(body):
    """SHOW CREATE TABLE text without its FOREIGN KEY constraint lines."""
    lines = [line for line in body.split("\n") if not _FOREIGN_KEY_LINE.match(line)]
    for i, line in enumerate(lines):
        if line.startswith(")") and i and lines[i - 1].endswith(","):
            lines[i - 1] = lines[i - 1][:-1]  # the definition list ends here
    return "\n".join(lines)


def _copy_tables(cursor, schema, source, *, foreign_keys):
    """Create schema with the template's tables, read with SELECT and SHOW
    CREATE TABLE only (the guard refuses anything else naming the template).
    Without foreign_keys the tables match CREATE TABLE ... LIKE: no foreign
    key constraints, counters starting over."""
    from app import chat_tx

    cursor.execute(f"CREATE DATABASE `{schema}`")
    cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'",
                   (source,))
    tables = [row[0] for row in cursor.fetchall()]
    cursor.execute(f"USE `{schema}`")  # unqualified REFERENCES resolve here
    cursor.execute("SET FOREIGN_KEY_CHECKS=0")
    try:
        for table in tables:
            if not _SCHEMA_NAME.fullmatch(table):
                raise RuntimeError("Unexpected schema identifier")
            cursor.execute(f"SHOW CREATE TABLE `{source}`.`{table}`")
            create = cursor.fetchone()[1]
            prefix = f"CREATE TABLE `{table}`"
            if not create.startswith(prefix):
                raise RuntimeError("Unexpected CREATE TABLE text")
            body = _AUTO_INCREMENT.sub("", create[len(prefix):])
            if not foreign_keys:
                body = _without_foreign_keys(body)
            cursor.execute(f"CREATE TABLE `{schema}`.`{table}`" + body)
    finally:
        cursor.execute("SET FOREIGN_KEY_CHECKS=1")
    # Migration markers, not the source's runtime state: its receipt
    # prune mark would make a fresh schema look already pruned.
    cursor.execute(f"SELECT `key`, value FROM `{source}`.schema_meta WHERE `key`<>%s", (chat_tx._RECEIPT_MARK,))
    rows = cursor.fetchall()
    if rows:
        cursor.executemany(f"INSERT INTO `{schema}`.schema_meta (`key`, value) VALUES (%s, %s)", rows)


def _create_schema(cursor, schema, source):
    _copy_tables(cursor, schema, source, foreign_keys=False)


def _structure_digest(cursor, source):
    """Hash of the source schema's tables, columns, indexes and migration markers."""
    from app import chat_tx

    cursor.execute(
        "SELECT MD5(CONCAT_WS('|',"
        f" (SELECT GROUP_CONCAT(`key`, '=', value ORDER BY `key` SEPARATOR ',') FROM `{source}`.schema_meta WHERE `key`<>%s),"
        " (SELECT GROUP_CONCAT(table_name, ':', column_name, ':', column_type, ':', is_nullable, ':', IFNULL(column_default, ''), ':', extra"
        "   ORDER BY table_name, ordinal_position SEPARATOR ',') FROM information_schema.columns WHERE table_schema=%s),"
        " (SELECT GROUP_CONCAT(table_name, ':', index_name, ':', seq_in_index, ':', column_name, ':', non_unique"
        "   ORDER BY table_name, index_name, seq_in_index SEPARATOR ',') FROM information_schema.statistics WHERE table_schema=%s)))",
        (chat_tx._RECEIPT_MARK, source, source),
    )
    return cursor.fetchone()[0]


@contextmanager
def _attached(url, schema, source, pool_size):
    """Point the application database at schema for the block."""
    from app import db
    from app.config import settings
    from app.mysql_compat import connect

    _ACTIVE.append((schema, source))
    pool = None
    old_url = settings.database_url
    try:
        settings.database_url = urlunsplit(url._replace(path="/" + schema))
        pool = connect(settings.database_url, max_connections=pool_size)
        db._conn = pool
        yield schema
    finally:
        _ACTIVE.remove((schema, source))
        db._conn = None
        if pool is not None:
            pool.close()
        settings.database_url = old_url


@contextmanager
def isolated_database(pool_size=8):
    from app import db

    url, source = _source_schema()
    if db._conn is not None:
        raise RuntimeError("Fixture isolation must be established before opening the application database")
    schema = "fuse_fixture_" + uuid4().hex
    admin = _admin_connect(source)
    try:
        with admin.cursor() as cursor:
            _create_schema(cursor, schema, source)
        with _attached(url, schema, source, pool_size):
            yield schema
    finally:
        try:
            with admin.cursor() as cursor:
                cursor.execute(f"DROP DATABASE IF EXISTS `{schema}`")
        finally:
            admin.close()


def _add_foreign_keys(cursor, schema, source, tables):
    for table in tables:
        if not _SCHEMA_NAME.fullmatch(table):
            raise RuntimeError("Unexpected schema identifier")
        cursor.execute(f"SHOW CREATE TABLE `{source}`.`{table}`")
        for name, cols, ref, ref_cols, rules in _FOREIGN_KEY.findall(cursor.fetchone()[1]):
            names = [name, ref, *_COLUMNS.findall(cols), *_COLUMNS.findall(ref_cols)]
            if not all(_SCHEMA_NAME.fullmatch(n) for n in names):
                raise RuntimeError("Unexpected foreign key definition")
            cursor.execute(
                f"ALTER TABLE `{schema}`.`{table}` ADD CONSTRAINT `{name}` FOREIGN KEY ({cols})"
                f" REFERENCES `{schema}`.`{ref}` ({ref_cols}){rules}"
            )


def _create_schema_with_foreign_keys(cursor, schema, source):
    """Like _create_schema, with the foreign keys in each CREATE TABLE
    (adding them afterwards rebuilds every table: minutes)."""
    _copy_tables(cursor, schema, source, foreign_keys=True)


def _build(cursor, schema, source, foreign_keys):
    try:
        if foreign_keys:
            _create_schema_with_foreign_keys(cursor, schema, source)
        else:
            _create_schema(cursor, schema, source)
    except Exception:
        cursor.execute(f"DROP DATABASE IF EXISTS `{schema}`")
        raise


@contextmanager
def persistent_schema(slot="main", foreign_keys=False):
    """Yield (database_url, schema): a schema reused across test runs,
    fuse_fixture_core_<structure>_<slot>, not attached to the app.

    Built once per source-schema structure (CREATE TABLE ... LIKE costs ~10 s
    on this host), then only emptied (reset_tables, a few ms). With
    foreign_keys it also carries every foreign key of the source (cascade
    tests; most tests insert rows without their parents). A named lock keeps
    two concurrent runs off one schema; the loser gets a throwaway schema.
    Core schemas of an older structure are dropped here.
    """
    url, source = _source_schema()
    if not _SCHEMA_NAME.fullmatch(slot):
        raise RuntimeError("Unexpected fixture slot")
    admin = _admin_connect(source)
    try:
        with admin.cursor() as cursor:
            digest = _structure_digest(cursor, source)[:12]
            schema = f"fuse_fixture_core_{digest}_{slot}"
            cursor.execute("SELECT GET_LOCK(%s, 0)", (schema,))
            if cursor.fetchone()[0] != 1:
                schema = "fuse_fixture_" + uuid4().hex
                _build(cursor, schema, source, foreign_keys)
                try:
                    yield urlunsplit(url._replace(path="/" + schema)), schema
                finally:
                    cursor.execute(f"DROP DATABASE IF EXISTS `{schema}`")
                return
            cursor.execute(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'fuse\\_fixture\\_core\\_%'")
            for (name,) in cursor.fetchall():
                if name == schema or not _FIXTURE_SCHEMA.fullmatch(name):
                    continue
                if not name.startswith(f"fuse_fixture_core_{digest}_"):
                    cursor.execute("SELECT IS_USED_LOCK(%s)", (name,))
                    if cursor.fetchone()[0] is None:
                        cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
            cursor.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name=%s", (schema,))
            if cursor.fetchone() is None:
                _build(cursor, schema, source, foreign_keys)
        yield urlunsplit(url._replace(path="/" + schema)), schema
    finally:
        admin.close()  # releases the lock


@contextmanager
def persistent_database(slot="main", pool_size=8):
    """persistent_schema attached as the application database."""
    from app import db

    if db._conn is not None:
        raise RuntimeError("Fixture isolation must be established before opening the application database")
    url, source = _source_schema()
    with persistent_schema(slot) as (_schema_url, schema):
        with _attached(url, schema, source, pool_size):
            yield schema


def reset_tables(keep=("schema_meta",)):
    """Empty every table of the attached schema in one transaction (~2-6 ms;
    TRUNCATE takes seconds). AUTO_INCREMENT counters keep counting: tests use
    the ids the code returns, never assumed ones. schema_meta keeps its
    migration markers but loses the runtime state the app writes there."""
    from app import accounts, chat_tx, db

    runtime_meta = ("first_user_claimed", accounts._CRYPTO_SENTINEL_KEY, chat_tx._RECEIPT_MARK)

    with db.tx() as conn:
        tables = [row[0] for row in conn.native(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE() AND table_type='BASE TABLE'"
        ).fetchall() if row[0] not in keep]
        conn.native("SET FOREIGN_KEY_CHECKS=0")
        try:
            for table in tables:
                conn.native(f"DELETE FROM `{table}`")
            conn.native("DELETE FROM schema_meta WHERE `key` IN (%s, %s, %s)", runtime_meta)
        finally:
            conn.native("SET FOREIGN_KEY_CHECKS=1")
