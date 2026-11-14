"""Refuse code whose tables, columns, indexes or foreign keys the connected schema lacks.

Usage: python -m scripts.check_schema [--apply] [schema.sql]   (DATABASE_URL from .env or the environment)

schema.sql (default scripts/sql/schema.sql) is dumped from fuse_dev after every
schema change, so it lists every table, column, index and foreign key the code at that
commit may use. Exits 1 and names each missing one; extra ones in the database
(a table a pending migration drops) are fine. Run the new commit's copy against
production before deploying it: on MariaDB db._init never adds columns, and
boot refuses a missing index.

--apply adds what is missing first, taken verbatim from schema.sql: whole
tables, columns, indexes and foreign keys. It only ever adds, so the code still running
keeps working. It never drops, renames, retypes or backfills; a migration
that does (scripts/sql/) is still its own step.
"""
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA_SQL = Path(__file__).resolve().parent / "sql" / "schema.sql"

_TABLE = re.compile(r"^CREATE TABLE `([^`]+)` \($")
_COLUMN = re.compile(r"^\s+`([^`]+)` ")
_KEY = re.compile(r"^\s+((?:UNIQUE |FULLTEXT |SPATIAL )?KEY `([^`]+)` .*?),?$")
_CONSTRAINT = re.compile(r"^\s+(CONSTRAINT `([^`]+)` FOREIGN KEY .*?),?$")


def expected_schema(text):
    """{table: {"create": sql, "columns": {name: definition}, "keys": {name: definition},
    "constraints": {name: definition}}} (constraints are foreign keys) from a dump_schema.sh
    file, in file order."""
    tables, current, lines = {}, None, []
    for line in text.splitlines():
        match = _TABLE.match(line)
        if match:
            current = tables.setdefault(match.group(1), {"create": "", "columns": {}, "keys": {},
                                                          "constraints": {}})
            lines = [line]
        elif current is not None:
            lines.append(line)
            if line.startswith(")"):
                current["create"] = "\n".join(lines).rstrip(";")
                current = None
            elif match := _COLUMN.match(line):
                current["columns"][match.group(1)] = line.strip().rstrip(",")
            elif match := _KEY.match(line):
                current["keys"][match.group(2)] = match.group(1)
            elif match := _CONSTRAINT.match(line):
                current["constraints"][match.group(2)] = match.group(1)
    return tables


def expected_columns(text):
    """{table: [column, ...]} from a dump_schema.sh file, in file order."""
    return {table: list(spec["columns"]) for table, spec in expected_schema(text).items()}


def missing(expected, actual):
    """Expected tables absent from actual, then table.column for present tables."""
    gone = [table for table in expected if table not in actual]
    gone += [f"{table}.{column}" for table, columns in expected.items()
             if table in actual for column in columns if column not in actual[table]]
    return gone


def missing_keys(expected, actual_columns, actual_keys, actual_constraints):
    """table.key (index) and table.constraint (foreign key) names schema.sql has that a
    present table lacks."""
    return [f"{table}.{name}" for table, spec in expected.items() if table in actual_columns
            for kind, actual in (("keys", actual_keys), ("constraints", actual_constraints))
            for name in spec[kind] if name not in actual.get(table, set())]


def additions(expected, actual_columns, actual_keys, actual_constraints):
    """The statements that add every missing table, column, index and foreign key."""
    sql = []
    for table, spec in expected.items():
        if table not in actual_columns:
            sql.append(spec["create"])
            continue
        parts = [f"ADD COLUMN {definition}" for column, definition in spec["columns"].items()
                 if column not in actual_columns[table]]
        parts += [f"ADD {definition}" for key, definition in spec["keys"].items()
                  if key not in actual_keys.get(table, set())]
        # Foreign keys last, after the columns and indexes they use.
        parts += [f"ADD {definition}" for name, definition in spec["constraints"].items()
                  if name not in actual_constraints.get(table, set())]
        if parts:
            sql.append(f"ALTER TABLE `{table}` " + ", ".join(parts))
    return sql


def _connect(url):
    from app.mysql_compat import _raw_connect

    return _raw_connect(urlsplit(url))


def _query(url, sql):
    conn = _connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()
    finally:
        conn.close()


def live_columns(url):
    actual = {}
    for table, column in _query(url, "SELECT table_name, column_name FROM information_schema.columns "
                                     "WHERE table_schema=DATABASE()"):
        actual.setdefault(table, set()).add(column)
    return actual


def live_keys(url):
    actual = {}
    for table, key in _query(url, "SELECT DISTINCT table_name, index_name FROM information_schema.statistics "
                                  "WHERE table_schema=DATABASE()"):
        actual.setdefault(table, set()).add(key)
    return actual


def live_constraints(url):
    actual = {}
    for table, name in _query(url, "SELECT table_name, constraint_name FROM information_schema.table_constraints "
                                   "WHERE table_schema=DATABASE() AND constraint_type='FOREIGN KEY'"):
        actual.setdefault(table, set()).add(name)
    return actual


def apply(url, statements):
    conn = _connect(url)
    try:
        with conn.cursor() as cur:
            # New tables can reference each other; schema.sql loads the same way.
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for sql in statements:
                cur.execute(sql)
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
        conn.commit()
    finally:
        conn.close()


def main(argv=None):
    from app.config import settings

    args = list(sys.argv[1:] if argv is None else argv)
    do_apply = "--apply" in args
    args = [arg for arg in args if arg != "--apply"]
    path = Path(args[0]) if args else SCHEMA_SQL
    url = settings.database_url.strip()
    if not url.startswith("mysql://"):
        sys.exit("check_schema: DATABASE_URL must be mysql://user:password@host:port/database")
    expected = expected_schema(path.read_text(encoding="utf-8"))
    if not expected:
        sys.exit(f"check_schema: no CREATE TABLE in {path}")
    schema = urlsplit(url).path.strip("/")
    columns, keys, constraints = live_columns(url), live_keys(url), live_constraints(url)
    if do_apply:
        statements = additions(expected, columns, keys, constraints)
        if statements:
            print(f"check_schema: adding to {schema!r}:")
            for sql in statements:
                print("  " + sql.splitlines()[0] + (" ..." if "\n" in sql else ""))
            apply(url, statements)
            columns, keys, constraints = live_columns(url), live_keys(url), live_constraints(url)
    gone = missing({table: spec["columns"] for table, spec in expected.items()}, columns)
    gone += missing_keys(expected, columns, keys, constraints)
    if gone:
        print(f"check_schema: {schema!r} is missing {len(gone)} of what the code expects:", file=sys.stderr)
        for name in gone:
            print(f"  {name}", file=sys.stderr)
        print("Apply the pending migration from scripts/sql/ to it first, or rerun with --apply.", file=sys.stderr)
        return 1
    print(f"check_schema: {schema!r} has all {len(expected)} tables, their columns, indexes and foreign keys")
    return 0


if __name__ == "__main__":
    sys.exit(main())
