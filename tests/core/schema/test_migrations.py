"""scripts/sql/2026-*.sql: every migration can run again on a schema it was
already applied to, or that stopped halfway, and leaves exactly schema.sql.

Rerunning 2026-10-20-provider-tail once failed on a migrated schema, and a
run that stopped between its two statements left chat_runs without
provider_messages and no way to finish it."""
import re

from scripts import check_schema, init_db
from tests.core.schema._support import admin, before_provider_tail, before_run_sweeps, run, scratch_schema, show_create

EXPECTED = check_schema.expected_schema(check_schema.SCHEMA_SQL.read_text(encoding="utf-8"))
MIGRATIONS = sorted(check_schema.MIGRATIONS.glob("2026-*.sql"))


def _touched(*paths) -> list:
    """Tables the migrations name, plus the tables their foreign keys reference."""
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths or MIGRATIONS)
    tables = {table for table in EXPECTED if re.search(rf"\b{table}\b", text)}
    refs = re.compile(r"REFERENCES `([^`]+)`")
    while more := {ref for table in tables for ref in refs.findall(EXPECTED[table]["create"])} - tables:
        tables |= more
    return sorted(tables)


def _apply(url, *paths):
    for path in paths:
        run(url, *init_db.statements(path.read_text(encoding="utf-8")))


def test_every_migration_reruns_from_the_old_shape_to_schema_sql():
    # ~6 s: each ALTER and the ~20 CREATE TABLEs are slow on this host's ZFS HDDs.
    tables = _touched()
    creates = {table: EXPECTED[table]["create"] for table in tables}
    old = dict(creates, chat_runs=before_provider_tail(creates["chat_runs"]))
    grants = [line for line in creates["chat_permission_grants"].splitlines() if "`thread_key` bigint" not in line]
    old["chat_permission_grants"] = "\n".join(grants).replace(
        "UNIQUE KEY `chat_perm_grants_scope` (`thread_key`,",
        "UNIQUE KEY `sqlite_autoindex_chat_permission_grants_1` (`thread_id`,")
    with scratch_schema(*old.values()) as (url, name), admin() as conn:
        for _ in range(2):
            _apply(url, *MIGRATIONS)
            assert show_create(conn, name) == creates


def test_provider_tail_finishes_after_stopping_between_its_statements():
    paths = [check_schema.MIGRATIONS / f"{name}.sql"
             for name in ("2026-10-20-provider-tail", "2026-10-20-run-activity", "2026-11-20-run-sweeps")]
    creates = {table: EXPECTED[table]["create"] for table in _touched(*paths)}
    creates["chat_runs"] = "\n".join(line for line in before_run_sweeps(creates["chat_runs"]).splitlines()
                                      if not line.strip().startswith(("`provider_messages`", "`activity`")))
    with scratch_schema(*creates.values()) as (url, name), admin() as conn:
        _apply(url, *paths)
        assert show_create(conn, name)["chat_runs"] == EXPECTED["chat_runs"]["create"]
