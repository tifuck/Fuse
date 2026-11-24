"""scripts/sql/2026-*.sql: every migration can run again on a schema it was
already applied to, or that stopped halfway, and leaves exactly schema.sql;
and the ones that rewrite rows do it right on the shape and rows they met.

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


def _without(create: str, *names: str) -> str:
    """create without its column, key and constraint lines starting with names."""
    kept = [line for line in create.splitlines() if not line.strip().startswith(names)]
    return re.sub(r",\n\)", "\n)", "\n".join(kept))


def _rows(url, sql) -> list:
    return [tuple(row) for row in run(url, sql)]


def _grants_before_scope_key(create: str) -> str:
    """chat_permission_grants as it was before 2026-10-26-grant-scope-unique."""
    grants = [line for line in create.splitlines() if "`thread_key` bigint" not in line]
    return "\n".join(grants).replace(
        "UNIQUE KEY `chat_perm_grants_scope` (`thread_key`,",
        "UNIQUE KEY `sqlite_autoindex_chat_permission_grants_1` (`thread_id`,")


def test_every_migration_reruns_from_the_old_shape_to_schema_sql():
    # ~6 s: each ALTER and the ~20 CREATE TABLEs are slow on this host's ZFS HDDs.
    tables = _touched()
    creates = {table: EXPECTED[table]["create"] for table in tables}
    old = dict(creates, chat_runs=before_provider_tail(creates["chat_runs"]))
    old["chat_permission_grants"] = _grants_before_scope_key(creates["chat_permission_grants"])
    with scratch_schema(*old.values()) as (url, name), admin() as conn:
        for _ in range(2):
            _apply(url, *MIGRATIONS)
            assert show_create(conn, name) == creates


def test_provider_tail_finishes_after_stopping_between_its_statements():
    paths = [check_schema.MIGRATIONS / f"{name}.sql"
             for name in ("2026-10-20-provider-tail", "2026-10-20-run-activity", "2026-11-20-run-sweeps", "2026-11-24-db-perf")]
    creates = {table: EXPECTED[table]["create"] for table in _touched(*paths)}
    creates["chat_runs"] = "\n".join(line for line in before_run_sweeps(creates["chat_runs"]).splitlines()
                                      if not line.strip().startswith(("`provider_messages`", "`activity`")))
    with scratch_schema(*creates.values()) as (url, name), admin() as conn:
        _apply(url, *paths)
        assert show_create(conn, name)["chat_runs"] == EXPECTED["chat_runs"]["create"]


# The tests below build the shape a table had before one migration (foreign
# keys left out: only the rows matter), seed rows, and apply it twice.

def test_grant_scope_keeps_only_the_oldest_of_duplicate_host_wide_grants():
    path = check_schema.MIGRATIONS / "2026-10-26-grant-scope-unique.sql"
    old = _without(_grants_before_scope_key(EXPECTED["chat_permission_grants"]["create"]), "CONSTRAINT")
    # (id, thread_id, host_id, value): 9, 4 and 6 are one host-wide grant
    # stored three times; the others differ in thread, value or host.
    grants = [(9, None, 7, "/srv"), (4, None, 7, "/srv"), (6, None, 7, "/srv"), (5, 3, 7, "/srv"),
              (8, None, 7, "/etc"), (7, None, 2, "/srv")]
    with scratch_schema(old) as (url, _name):
        run(url, *(f"INSERT INTO chat_permission_grants (id, user_id, thread_id, host_id, kind, value, created_at) "
                   f"VALUES ({gid}, 1, {'NULL' if thread is None else thread}, {host}, 'dir', '{value}', '2026-10-01T00:00:00Z')"
                   for gid, thread, host, value in grants))
        for _ in range(2):
            _apply(url, path)
            assert _rows(url, "SELECT id, thread_key FROM chat_permission_grants ORDER BY id") == [(4, 0), (5, 3), (7, 0), (8, 0)]


def test_artifact_sharing_keeps_existing_public_links_under_their_identifier():
    path = check_schema.MIGRATIONS / "2026-10-30-artifacts.sql"
    later = ("`viewed_at`", "`expired_at`", "`display`", "CONSTRAINT")
    old = [_without(EXPECTED["artifacts"]["create"], "`share_token`", "`shared_version`", "`icon`",
                    "UNIQUE KEY `artifacts_share_token`", *later),
           _without(EXPECTED["artifact_versions"]["create"], "`doc_format`", "`note`", "CONSTRAINT"),
           _without(EXPECTED["users"]["create"], "`chat_artifacts`", "CONSTRAINT")]
    with scratch_schema(*old) as (url, _name):
        run(url, "INSERT INTO artifacts (public_id, user_id, title, visibility, created_at, updated_at) VALUES "
                 "('pub-one', 1, 'a', 'public', 't', 't'), ('priv-one', 1, 'b', 'private', 't', 't')")
        _apply(url, path)
        shares = "SELECT public_id, share_token FROM artifacts ORDER BY id"
        assert _rows(url, shares) == [("pub-one", "pub-one"), ("priv-one", None)]
        # A link reset after the first run survives the second.
        run(url, "UPDATE artifacts SET share_token='rotated-link' WHERE public_id='pub-one'")
        _apply(url, path)
        assert _rows(url, shares) == [("pub-one", "rotated-link"), ("priv-one", None)]


def test_artifact_expiry_counts_existing_artifacts_as_viewed_once():
    path = check_schema.MIGRATIONS / "2026-11-18-artifact-expiry.sql"
    old = _without(EXPECTED["artifacts"]["create"], "`viewed_at`", "`expired_at`", "`display`", "CONSTRAINT")
    with scratch_schema(old) as (url, _name):
        run(url, "INSERT INTO artifacts (public_id, user_id, title, created_at, updated_at) VALUES "
                 "('a-one', 1, 'a', 't', 't'), ('a-two', 1, 'b', 't', 't')")
        _apply(url, path)
        stamps = _rows(url, "SELECT viewed_at, expired_at FROM artifacts ORDER BY id")
        assert [expired for _viewed, expired in stamps] == ["", ""]
        assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", viewed) for viewed, _expired in stamps), stamps
        run(url, "UPDATE artifacts SET viewed_at='2026-01-01T00:00:00Z' WHERE public_id='a-one'")
        _apply(url, path)
        assert _rows(url, "SELECT viewed_at FROM artifacts ORDER BY id") == [("2026-01-01T00:00:00Z",), (stamps[1][0],)]


_EVENT_COLUMNS = ("user_id, source_id, api_key_id, source_type, model, created_at, prompt_tokens, completion_tokens, "
                  "output_tokens, reasoning_tokens, cached_tokens, cache_write_tokens, total_tokens, count_request, "
                  "request_status, reported_cost_micros, estimated_cost_micros, pricing_known")
_EVENTS = [
    "1, 5, NULL, '', 'grok-4', '2026-11-20T10:15:00Z', 100, 20, 30, 10, 5, 2, 130, 1, 'success', 700, 500, 0",
    "1, 5, NULL, 'grok', 'grok-4', '2026-11-20T10:45:00Z', 50, 0, 10, 0, 0, 0, 0, 1, 'failed', NULL, 300, 1",
    "1, NULL, NULL, '', '', '2026-11-20T11:05:00Z', 7, 0, 3, 0, 0, 0, 10, 0, 'cancelled', NULL, 0, 0",
    "1, 5, 9, 'grok', 'grok-4', '2026-11-20T10:20:00Z', 1, 0, 1, 0, 0, 0, 2, 1, 'cancelled', NULL, 0, 0",
    "1, NULL, NULL, '', NULL, '2026-11-20T11:30:00Z', 0, 0, 0, 0, 0, 0, 0, 1, 'success', NULL, 0, 0",
]
# Summed by hand from _EVENTS: (user_id, hour, api_key_id, source_type, model,
# tokens, requests, prompt, completion, output, reasoning, cached, cache_write,
# ok_requests, failed, cancelled, cost, estimated_cost, priced, priced_tokens,
# unpriced_events, first_at, last_at).
_ROLLUP = [
    (1, "2026-11-20T10:00:00Z", 0, "grok", "grok-4", 190, 2, 150, 20, 40, 10, 5, 2, 1, 1, 0, 1000, 800, 2, 190, 0,
     "2026-11-20T10:15:00Z", "2026-11-20T10:45:00Z"),
    (1, "2026-11-20T10:00:00Z", 9, "grok", "grok-4", 2, 1, 1, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1,
     "2026-11-20T10:20:00Z", "2026-11-20T10:20:00Z"),
    (1, "2026-11-20T11:00:00Z", 0, "unknown", "Unknown", 10, 1, 7, 0, 3, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1,
     "2026-11-20T11:05:00Z", "2026-11-20T11:30:00Z"),
]


def test_usage_rollups_are_backfilled_from_the_events_and_refilled_on_rerun():
    path = check_schema.MIGRATIONS / "2026-11-20-usage-rollups.sql"
    old = [EXPECTED["usage_events"]["create"], EXPECTED["sources"]["create"],
           "CREATE TABLE usage_hourly (id bigint(20) NOT NULL, PRIMARY KEY (id))"]
    rollup = ("SELECT user_id, hour, api_key_id, source_type, model, tokens, requests, prompt, completion, output, "
              "reasoning, cached, cache_write, ok_requests, failed, cancelled, cost, estimated_cost, priced, "
              "priced_tokens, unpriced_events, first_at, last_at FROM usage_rollup_hourly ORDER BY hour, api_key_id")
    with scratch_schema(*old) as (url, _name):
        run(url, "INSERT INTO sources (id, type, name, created_at) VALUES (5, 'grok', 'Grok', 't')",
             *(f"INSERT INTO usage_events ({_EVENT_COLUMNS}) VALUES ({values})" for values in _EVENTS))
        _apply(url, path)
        assert _rows(url, rollup) == _ROLLUP
        assert _rows(url, "SELECT source_type FROM usage_events ORDER BY id") == [
            ("grok",), ("grok",), ("unknown",), ("grok",), ("unknown",)]
        assert _rows(url, "SHOW TABLES LIKE 'usage_hourly'") == []
        # Usage the old code recorded meanwhile is refilled by the second run:
        # a stale row is rewritten and a missing one comes back.
        run(url, "UPDATE usage_rollup_hourly SET tokens=999, requests=999, last_at='x' WHERE api_key_id=0 AND model='grok-4'",
             "DELETE FROM usage_rollup_hourly WHERE api_key_id=9")
        _apply(url, path)
        assert _rows(url, rollup) == _ROLLUP
