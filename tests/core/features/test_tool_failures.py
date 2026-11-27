"""Failed tool-call log: root-cause classification, clipping that keeps the
useful end of a traceback, fingerprint grouping, retention, and a real turn
whose hallucinated tool call lands in the log."""
from unittest.mock import patch

import pytest

from app import db, tool_failures
from tests.core.helpers import fake_provider, quick_user, run_async, run_turn, text_frames, tool_call_frames


@pytest.mark.parametrize("text, kind", [
    ("unknown tool", "unknown_tool"),
    ("Tool is not loaded. Call search_tools", "tool_not_loaded"),
    ("arguments were invalid JSON", "invalid_arguments"),
    ("rg: regex parse error:\n    print(\nunclosed group", "invalid_arguments"),
    ("Tool error: no valid query.", "no_valid_query"),
    ("Search budget for this turn is exhausted.", "budget_exhausted"),
    ("Search rate limit reached. Try again shortly.", "rate_limited"),
    ("That URL is not an allowed public HTTPS address.", "not_allowed"),
    ("Refused to fetch a non-public address.", "not_allowed"),
    ("Page returned HTTP 403.", "upstream_error"),
    ("agent timed out", "upstream_error"),
    # A timed-out permission ask is a denial, not an outage.
    ("Permission request timed out (no user response).", "permission_denied"),
    ("Permission denied by Jev (Auto mode automation), not by the user.", "permission_denied"),
    ("subagent not found", "not_found"),
    ("unknown host alias", "not_found"),
    ("vault locked", "vault_locked"),
    ("agent is offline", "unavailable"),
    ("unknown skill", "unknown_skill"),
    ("mystery", "tool_error"),
    ("", "tool_error"),
])
def test_errors_are_bucketed_by_root_cause(text, kind):
    assert tool_failures.classify(text) == kind


def test_an_explicit_kind_wins_over_the_text():
    assert tool_failures.classify("unknown tool", kind="internal_exception") == "internal_exception"
    assert tool_failures.classify("unknown tool", kind="not-a-kind") == "unknown_tool"


def test_run_code_failures_keep_their_kind_whatever_the_output_says():
    # A failed command's output can read like any bucket ("not found", "timed out", "permission denied").
    error = "sh npm test: exited 1\n--- output ---\nError: module not found; request timed out"
    assert tool_failures.classify(error, kind="command_failed") == "command_failed"
    assert tool_failures.classify("read_file a.txt: file not found", kind="code_error") == "code_error"


def test_a_record_round_trips_with_its_fields_clipped(fresh_db):
    uid = quick_user()
    trace = "Traceback (most recent call last):\n" + "  frame\n" * 1000 + "TypeError: cannot unpack coroutine"
    new_id = tool_failures.record(
        tool_name="web_search", error="Tool error: no valid query.", kind="no_valid_query",
        arguments='{"query":"' + "x" * 9000, call_id="call_0", user_id=uid, thread_id=7, run_id="run-1",
        model="glm-5.3-flash", source_type="zai", round_index=2, phase="search", latency_ms=123,
        trace={"name": "web_search", "status": "error", "blob": "y" * 9000}, exception=trace,
        reasoning="r" * 5000, user_text="who won the game",
    )
    assert new_id
    [row] = tool_failures.recent()
    assert (row["error_kind"], row["model"], row["round_index"], row["user_id"]) == ("no_valid_query", "glm-5.3-flash", 2, uid)
    assert len(row["arguments"]) == tool_failures.ARGUMENTS_MAX
    assert len(row["reasoning"]) == tool_failures.REASONING_MAX
    # The traceback keeps its last lines, where the exception is named.
    assert len(row["exception"]) == tool_failures.EXCEPTION_MAX
    assert row["exception"].endswith("TypeError: cannot unpack coroutine")
    # An oversized trace stays parseable JSON with its key fields.
    assert row["trace"]["truncated"] is True and row["trace"]["name"] == "web_search"


def test_filters_summary_and_fingerprints_group_one_failure_once(fresh_db):
    common = "Page returned HTTP 403. " + "x" * 150
    for n in range(4):
        tool_failures.record(tool_name="read_url", error=common + f" request {n}", model="a" if n < 3 else "b")
    tool_failures.record(tool_name="read_url", error="Page returned HTTP 500.", model="b")
    tool_failures.record(tool_name="web_search", error="unknown tool", model="b", user_id=42)
    assert len(tool_failures.recent(kind="upstream_error")) == 5
    assert len(tool_failures.recent(tool="web_search")) == 1
    assert len(tool_failures.recent(model="b")) == 3
    assert len(tool_failures.recent(user_id=42)) == 1
    summary = tool_failures.summary(hours=24)
    assert summary["total"] == 6
    assert summary["by_kind"] == {"upstream_error": 5, "unknown_tool": 1}
    assert summary["by_tool"] == {"read_url": 5, "web_search": 1}
    prints = tool_failures.fingerprints(hours=24)
    # Errors that differ only after the first 160 characters are one cluster.
    assert [(p["tool_name"], p["n"], p["models"]) for p in prints][0] == ("read_url", 4, 2)
    assert len(prints) == 3
    pack = tool_failures.analysis_pack(limit=10, hours=24)
    assert set(pack) >= {"guide", "summary", "fingerprints", "recent"}


def test_old_rows_are_pruned_to_the_retention_window(fresh_db):
    with patch.object(tool_failures, "PRUNE_EVERY", 1), patch.object(tool_failures, "RETENTION_ROWS", 3):
        ids = [tool_failures.record(tool_name="t", error=f"e{n}") for n in range(6)]
    assert all(ids)
    assert [r["error"] for r in tool_failures.recent()] == ["e5", "e4", "e3"]


def test_recording_never_raises(fresh_db):
    with patch.object(db, "tx", side_effect=RuntimeError("boom")):
        assert tool_failures.record(tool_name="x", error="y") is None
    assert tool_failures.record(tool_name="x", error="y", trace={"bad": object()}) is not None


def test_a_hallucinated_tool_call_in_a_real_turn_is_logged(fresh_db):
    uid = quick_user()
    with fake_provider(tool_call_frames([("c1", "summon_dragon", {"color": "red"})]), text_frames("Sorry.")):
        _, run = run_async(run_turn(uid, "summon a dragon"))
    assert run["status"] == "completed"
    [row] = tool_failures.recent(user_id=uid)
    assert (row["tool_name"], row["error_kind"], row["call_id"]) == ("summon_dragon", "unknown_tool", "c1")
    assert row["run_id"] == run["id"] and '"red"' in row["arguments"]
    assert row["user_text"].startswith("summon a dragon")
