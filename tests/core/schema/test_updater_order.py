"""scripts/sql/2026-*.sql pass the production updater's ordering rule
(docs/development.md, Schema changes): within one file no additive statement
comes after a destructive one (DROP, RENAME, TRUNCATE, ALTER ... DROP|CHANGE|
MODIFY|RENAME). The updater runs a file's additive statements before the code
swap and holds the destructive ones until the new code runs, so it refuses a
file that adds after it drops. 2026-11-24-db-perf.sql re-added an index in a
plain ALTER right after dropping it and stopped a production update twice."""
import re

from scripts import check_schema, init_db

MIGRATIONS = sorted(check_schema.MIGRATIONS.glob("2026-*.sql"))


def destructive(statement: str) -> bool:
    text = statement.lstrip().upper()
    if re.match(r"(DROP|RENAME|TRUNCATE)\b", text):
        return True
    return text.startswith("ALTER") and re.search(r"\b(DROP|CHANGE|MODIFY|RENAME)\b", text) is not None


def additive_after_destructive(text: str) -> str | None:
    """The first additive statement that follows a destructive one, or None."""
    dropped = False
    for statement in init_db.statements(text):
        if destructive(statement):
            dropped = True
        elif dropped:
            return statement
    return None


def test_the_rule_refuses_an_index_re_added_after_its_drop():
    assert additive_after_destructive(
        "ALTER TABLE t DROP INDEX IF EXISTS i;\nALTER TABLE t ADD INDEX i (a);\n") == "ALTER TABLE t ADD INDEX i (a)"
    assert additive_after_destructive(
        "ALTER TABLE t ADD COLUMN c INT;\nALTER TABLE t DROP INDEX IF EXISTS i;\n"
        "ALTER TABLE t DROP INDEX IF EXISTS i, ADD INDEX i (a);\n") is None


def test_no_migration_adds_after_it_drops():
    refused = {path.name: bad for path in MIGRATIONS
               if (bad := additive_after_destructive(path.read_text(encoding="utf-8")))}
    assert refused == {}
