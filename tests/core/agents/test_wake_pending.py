"""Wake bookkeeping read through the generated chat_runs.wake_pending column
(scripts/sql/2026-11-20-run-sweeps.sql): the sweep finds exactly the
finished background reports that still owe a wake, and batched row reads
keep the per-child results of the old one-row-at-a-time loops."""
from unittest.mock import patch

from app import chat_runs, chat_store, db
from tests.core.agents._support import agent_of, child, coding_user, parent_and_thread


def _finished_background_child(uid):
    thread, parent = parent_and_thread(uid)
    bg = child(uid, thread, parent, background=True)
    chat_store.finish_run(bg["id"], {"content": "report"}, "completed")
    chat_store.finish_run(parent["id"], {"content": "ok"}, "completed")
    return parent["thread_id"], bg


def _swept():
    with patch.object(chat_runs, "schedule_wake") as woken:
        chat_runs.sweep_wakes()
    return [call.args for call in woken.call_args_list]


def _events(uid, kind):
    return db.fetchall("SELECT payload FROM chat_events WHERE user_id=? AND type=?", (uid, kind))


def test_the_sweep_wakes_a_finished_background_report_until_it_is_delivered(fresh_db):
    uid = coding_user()
    pk, bg = _finished_background_child(uid)

    assert _swept() == [(uid, pk)]
    assert [item["id"] for item in chat_store.wake_candidates(uid, pk)] == [bg["id"]]

    chat_store.mark_wake_sent([bg["id"]])
    assert _swept() == []
    assert chat_store.wake_candidates(uid, pk) == []


def test_a_report_the_user_stopped_is_never_swept_but_still_waits_as_a_candidate(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    bg = child(uid, thread, parent, background=True)
    chat_store.request_cancel_self(uid, bg["id"], by_user=True)
    chat_store.finish_run(bg["id"], {"content": "partial"}, "cancelled")
    chat_store.finish_run(parent["id"], {"content": "ok"}, "completed")

    assert _swept() == []
    candidates = chat_store.wake_candidates(uid, parent["thread_id"])
    assert [(item["id"], item["user_stopped"]) for item in candidates] == [(bg["id"], True)]


def test_a_repeated_child_id_is_counted_as_one_row_read_per_occurrence(fresh_db):
    uid = coding_user()
    _, bg = _finished_background_child(uid)

    chat_store.bump_wake_attempts([bg["id"], bg["id"]])
    assert agent_of(uid, bg["id"])["wake_attempts"] == 2

    chat_store.mark_wake_sent([bg["id"], bg["id"]])
    assert agent_of(uid, bg["id"])["wake_sent"] is True
    assert len(_events(uid, "agent.delivered")) == 1


def test_stop_all_flags_each_live_subagent_once(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    first = child(uid, thread, parent, background=True)
    second = child(uid, thread, parent, background=True, prompt="other")

    stopped = chat_store.request_cancel_thread_agents(uid, parent["id"])
    again = chat_store.request_cancel_thread_agents(uid, parent["id"])

    assert sorted(stopped) == sorted(again) == sorted([first["id"], second["id"]])
    flags = db.fetchall("SELECT cancel_requested FROM chat_runs WHERE id IN (?,?)", (first["id"], second["id"]))
    assert [row["cancel_requested"] for row in flags] == [chat_store.USER_STOPPED] * 2
    assert len(_events(uid, "agent.cancel_requested")) == 2
