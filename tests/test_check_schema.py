"""scripts/check_schema.py: promote-dev.sh refuses code whose columns fuse_live lacks.

On 2026-10-15 a promote shipped code reading user_projects.default_permissions
before its migration reached fuse_live, and every /chat/sync failed.
"""
from app.config import settings
from scripts import check_schema

SCHEMA = check_schema.expected_columns(check_schema.SCHEMA_SQL.read_text(encoding="utf-8"))
FULL = check_schema.expected_schema(check_schema.SCHEMA_SQL.read_text(encoding="utf-8"))
KEYS = {table: set(spec["keys"]) for table, spec in FULL.items()}


def _live(expected, *drop):
    actual = {table: set(columns) for table, columns in expected.items()}
    for name in drop:
        table, _, column = name.partition(".")
        if column:
            actual[table].discard(column)
        else:
            del actual[table]
    return actual


def test_reads_every_table_and_its_columns_from_schema_sql():
    text = check_schema.SCHEMA_SQL.read_text(encoding="utf-8")
    assert len(SCHEMA) == text.count("\nCREATE TABLE ")
    assert SCHEMA["user_projects"][0] == "id"
    assert "default_permissions" in SCHEMA["user_projects"]
    # Keys and constraints are not columns.
    assert not {"PRIMARY", "KEY", "UNIQUE", "CONSTRAINT"} & {c for cols in SCHEMA.values() for c in cols}


def test_the_2026_10_15_promote_would_have_been_refused():
    actual = _live(SCHEMA, "user_projects.default_permissions",
                   "user_agent_settings.default_chat_permissions",
                   "user_agent_settings.default_project_permissions")
    assert check_schema.missing(SCHEMA, actual) == [
        "user_agent_settings.default_chat_permissions",
        "user_agent_settings.default_project_permissions",
        "user_projects.default_permissions",
    ]


def test_missing_table_is_named_once_and_extras_are_fine():
    actual = _live(SCHEMA, "artifacts")
    actual["bug_reports"] = {"id"}
    actual["user_projects"].add("dropped_later")
    assert check_schema.missing(SCHEMA, actual) == ["artifacts"]
    assert check_schema.missing(SCHEMA, _live(SCHEMA)) == []


def test_reads_indexes_and_whole_tables():
    assert FULL["command_jobs"]["keys"]["command_jobs_thread"] == "KEY `command_jobs_thread` (`thread_id`)"
    assert FULL["api_keys"]["keys"]["sqlite_autoindex_api_keys_1"].startswith("UNIQUE KEY ")
    assert "PRIMARY" not in FULL["api_keys"]["keys"]
    assert FULL["user_projects"]["columns"]["id"].startswith("`id` bigint")
    create = FULL["artifacts"]["create"]
    assert create.startswith("CREATE TABLE `artifacts` (") and create.endswith("ROW_FORMAT=DYNAMIC")


def test_additions_only_add_what_is_missing():
    columns = _live(SCHEMA, "artifacts", "user_projects.default_permissions")
    columns["bug_reports"] = {"id"}
    keys = {table: set(names) for table, names in KEYS.items()}
    keys["command_jobs"].discard("command_jobs_thread")
    sql = check_schema.additions(FULL, columns, keys)
    assert sql == [
        FULL["artifacts"]["create"],
        "ALTER TABLE `command_jobs` ADD KEY `command_jobs_thread` (`thread_id`)",
        "ALTER TABLE `user_projects` ADD COLUMN " + FULL["user_projects"]["columns"]["default_permissions"],
    ]
    assert not any(word in s for s in sql for word in ("DROP", "MODIFY", "CHANGE", "bug_reports"))
    assert check_schema.additions(FULL, _live(SCHEMA), KEYS) == []
    assert check_schema.missing_keys(FULL, columns, keys) == ["command_jobs.command_jobs_thread"]


def test_main_apply_adds_then_passes(monkeypatch, capsys):
    monkeypatch.setattr(settings, "database_url", "mysql://u:p@db/fuse_example")
    state = {"keys": {table: set(names) for table, names in KEYS.items()}}
    state["keys"]["command_jobs"].discard("command_jobs_thread")
    applied = []

    def fake_apply(url, statements):
        applied.extend(statements)
        state["keys"]["command_jobs"].add("command_jobs_thread")

    monkeypatch.setattr(check_schema, "live_columns", lambda url: _live(SCHEMA))
    monkeypatch.setattr(check_schema, "live_keys", lambda url: state["keys"])
    monkeypatch.setattr(check_schema, "apply", fake_apply)
    assert check_schema.main([]) == 1
    assert "  command_jobs.command_jobs_thread\n" in capsys.readouterr().err
    assert applied == []
    assert check_schema.main(["--apply"]) == 0
    assert applied == ["ALTER TABLE `command_jobs` ADD KEY `command_jobs_thread` (`thread_id`)"]
    assert "has all" in capsys.readouterr().out


def test_main_exits_1_and_names_what_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(settings, "database_url", "mysql://u:p@db/fuse_example")
    monkeypatch.setattr(check_schema, "live_keys", lambda url: KEYS)
    monkeypatch.setattr(check_schema, "live_columns", lambda url: _live(SCHEMA, "user_projects.default_permissions"))
    assert check_schema.main([]) == 1
    err = capsys.readouterr().err
    assert "'fuse_example' is missing 1" in err
    assert "  user_projects.default_permissions\n" in err

    monkeypatch.setattr(check_schema, "live_columns", lambda url: _live(SCHEMA))
    assert check_schema.main([str(check_schema.SCHEMA_SQL)]) == 0
    assert f"has all {len(SCHEMA)} tables" in capsys.readouterr().out
