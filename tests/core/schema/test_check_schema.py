"""scripts/check_schema.py: promote refuses code whose tables, columns, indexes
or foreign keys the live schema lacks, and --apply only ever adds them.

On 2026-10-15 a promote shipped code reading user_projects.default_permissions
before its migration reached fuse_live, and every /chat/sync failed."""
import pytest

from app.config import settings
from scripts import check_schema
from tests.core.schema._support import admin, run, scratch_schema, show_create

FULL = check_schema.expected_schema(check_schema.SCHEMA_SQL.read_text(encoding="utf-8"))
COLUMNS = {table: list(spec["columns"]) for table, spec in FULL.items()}
TABLES = ("users", "api_keys", "user_hosts", "user_projects")


def _without(create: str, *needles: str) -> str:
    lines = [line for line in create.splitlines() if not any(n in line for n in needles)]
    # The line before the closing paren loses its trailing comma if it was cut.
    if lines[-2].endswith(","):
        lines[-2] = lines[-2][:-1]
    return "\n".join(lines)


@pytest.fixture
def drifted(tmp_path, monkeypatch):
    """A live schema behind a four-table schema.sql: api_keys missing, and
    user_projects without a column, an index and a foreign key; plus extras a
    pending migration would drop (a table and a column)."""
    spec = tmp_path / "schema.sql"
    spec.write_text("SET FOREIGN_KEY_CHECKS=0;\n" + ";\n".join(FULL[t]["create"] for t in TABLES) + ";\n")
    projects = _without(FULL["user_projects"]["create"], "`default_permissions`", "KEY `user_projects_host`",
                        "CONSTRAINT `fk_user_projects_1`")
    with scratch_schema(FULL["users"]["create"], FULL["user_hosts"]["create"], projects,
                        "CREATE TABLE bug_reports (id INT PRIMARY KEY)",
                        "ALTER TABLE users ADD COLUMN dropped_later INT") as (url, name):
        monkeypatch.setattr(settings, "database_url", url)
        yield url, name, spec


def test_drift_is_named_then_applied_additively(drifted, capsys):
    url, name, spec = drifted
    assert check_schema.main([str(spec)]) == 1
    err = capsys.readouterr().err
    assert f"'{name}' is missing 4" in err
    assert [line.strip() for line in err.splitlines() if line.startswith("  ")] == [
        "api_keys", "user_projects.default_permissions",
        "user_projects.user_projects_host", "user_projects.fk_user_projects_1",
    ]

    assert check_schema.main(["--apply", str(spec)]) == 0
    assert f"'{name}' has all 4 tables" in capsys.readouterr().out
    with admin() as conn:
        live = check_schema.expected_schema(";\n".join(show_create(conn, name).values()) + ";\n")
    for table in TABLES:
        for part in ("columns", "keys", "constraints"):
            # Verbatim from schema.sql, so the definitions match exactly.
            assert FULL[table][part].items() <= live[table][part].items(), (table, part)
    # Additive only: what the code no longer names stays for its own migration.
    assert "bug_reports" in live and "dropped_later" in live["users"]["columns"]
    assert check_schema.main([str(spec)]) == 0
    assert run(url, "SELECT COUNT(*) FROM users")[0][0] == 0


def test_additions_are_only_creates_and_adds():
    actual = {table: set(columns) for table, columns in COLUMNS.items()}
    del actual["artifacts"]
    actual["user_projects"].discard("default_permissions")
    actual["user_projects"].add("dropped_later")
    actual["bug_reports"] = {"id"}
    keys = {table: set(spec["keys"]) for table, spec in FULL.items()}
    keys["command_jobs"].discard("command_jobs_thread")
    fks = {table: set(spec["constraints"]) for table, spec in FULL.items()}
    sql = check_schema.additions(FULL, actual, keys, fks)
    assert sql == [
        FULL["artifacts"]["create"],
        "ALTER TABLE `command_jobs` ADD KEY `command_jobs_thread` (`thread_id`)",
        "ALTER TABLE `user_projects` ADD COLUMN " + FULL["user_projects"]["columns"]["default_permissions"],
    ]
    assert not any(word in s for s in sql for word in ("DROP", "MODIFY", "CHANGE", "RENAME", "bug_reports"))


def test_a_new_column_is_added_before_its_index_and_foreign_key():
    actual = {table: set(columns) for table, columns in COLUMNS.items()}
    actual["chat_schedules"].discard("thread_id")
    keys = {table: set(spec["keys"]) for table, spec in FULL.items()}
    keys["chat_schedules"].discard("fk_chat_schedules_0")
    fks = {table: set(spec["constraints"]) for table, spec in FULL.items()}
    fks["chat_schedules"].discard("fk_chat_schedules_0")
    [statement] = check_schema.additions(FULL, actual, keys, fks)
    column = statement.index("ADD COLUMN `thread_id`")
    assert column < statement.index("ADD KEY `fk_chat_schedules_0`") < statement.index("ADD CONSTRAINT `fk_chat_schedules_0`")


def test_the_2026_10_15_promote_would_have_been_refused():
    actual = {table: set(columns) for table, columns in COLUMNS.items()}
    actual["user_projects"].discard("default_permissions")
    actual["user_agent_settings"] -= {"default_chat_permissions", "default_project_permissions"}
    assert check_schema.missing(COLUMNS, actual) == [
        "user_agent_settings.default_chat_permissions",
        "user_agent_settings.default_project_permissions",
        "user_projects.default_permissions",
    ]
