"""Kill and Stop effects on a spawn tree, activity status, work time, and
admission under concurrency."""
import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app import agents, chat_children, chat_runs, chat_store, chats, db
from app.chat_store import SpawnCapError
from tests.core.agents._support import (
    agent_of, child, children, coding_user, parent_and_thread, root_runs, routed_provider, set_running, start_root,
    tool_result,
)
from tests.core.helpers import run_async, text_frames, tool_call_frames, wait_until, wait_until_async


def _ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- kill and stop -------------------------------------------------------------

def test_killing_a_running_child_interrupts_only_that_child(fresh_db):
    uid = coding_user()
    gate = {}

    async def child_hangs(_body):
        gate["started"].set()
        await asyncio.Event().wait()  # until cancelled

    def parent_sees_interrupt(body):
        result = tool_result(body, "c1")
        assert 'state="interrupted"' in result
        return text_frames("carrying on")

    async def body():
        gate["started"] = asyncio.Event()
        _, run, task = await start_root(uid, "go")
        await gate["started"].wait()
        child_id = children(uid, run["thread_id"])[0]["id"]
        text, rec = await agents.kill(user_id=uid, arguments=json.dumps({"id": child_id}), thread_id=run["thread_id"])
        assert rec["status"] == "done" and text == "cancelled"
        await asyncio.wait_for(task, 10)
        return run, child_id

    spawn = tool_call_frames([("c1", "spawn_subagent", {"prompt": "slow work", "description": "Slow"})])
    with routed_provider(go=[spawn, parent_sees_interrupt], **{"slow work": [child_hangs]}):
        run, child_id = run_async(body())

    assert chat_store.get_run(uid, run["id"])["status"] == "completed"
    assert chat_store.get_run(uid, child_id)["status"] == "cancelled"
    assert chat_store.get_run(uid, run["id"])["message"]["tools"][0]["status"] == "interrupted"


def test_kill_cancels_the_child_and_its_descendants_but_not_siblings(fresh_db):
    uid = coding_user(nested=True)
    thread, parent = parent_and_thread(uid)
    a = child(uid, thread, parent, prompt="a", allow_nested_spawn=True)
    b = child(uid, thread, parent, prompt="b")
    grand = child(uid, thread, a, prompt="a1", allow_nested_spawn=True)

    with patch.object(chat_runs, "cancel") as cancelled:
        _, rec = run_async(agents.kill(user_id=uid, arguments=json.dumps({"id": a["id"]}), thread_id=thread["id"]))
    assert rec["status"] == "done"
    assert sorted(call.args[0] for call in cancelled.call_args_list) == sorted([a["id"], grand["id"]])
    flags = {r: chat_store.get_run(uid, r)["cancel_requested"] for r in (a["id"], grand["id"], b["id"], parent["id"])}
    assert flags[a["id"]] and flags[grand["id"]]
    assert not flags[b["id"]] and not flags[parent["id"]]

    chat_store.finish_run(b["id"], {"content": "x"}, "completed")
    text, rec = run_async(agents.kill(user_id=uid, arguments=json.dumps({"id": b["id"]}), thread_id=thread["id"]))
    assert text == "subagent already finished" and rec["preview"] == "already completed"
    _, rec = run_async(agents.kill(user_id=uid + 1, arguments=json.dumps({"id": grand["id"]}), thread_id=thread["id"]))
    assert rec["status"] == "error"


def test_main_stop_ends_the_turn_and_foreground_children_but_not_background_ones(fresh_db):
    uid = coding_user()
    thread, first = parent_and_thread(uid)
    earlier = child(uid, thread, first, prompt="bg", background=True)
    chat_store.finish_run(first["id"], {"content": "ok"}, "completed")
    second = chat_store.start_turn(uid, thread["id"], "next", chats.get_thread(uid, thread["id"])["revision"], None)["run"]
    background = child(uid, thread, second, prompt="bg2", background=True)
    foreground = child(uid, thread, second, prompt="fg")

    with patch.object(chat_runs, "cancel"):
        run_async(chat_runs.cancel_wait(uid, second["id"], force=True, by_user=True))
    for ident in (second["id"], foreground["id"]):
        assert chat_store.get_run(uid, ident)["status"] == "cancelled"
    for ident in (earlier["id"], background["id"]):
        row = chat_store.get_run(uid, ident)
        assert row["status"] in chat_store.ACTIVE and not row["cancel_requested"]
    # A report landing after the Stop waits for the user's next message.
    chat_store.finish_run(background["id"], {"content": "report"}, "completed")
    held = {r["id"]: r["wake_state"] for r in chat_store.list_thread_agents(uid, second["thread_id"])}
    assert held[background["id"]] == "held"

    with patch.object(chat_runs, "cancel"):
        run_async(chat_runs.cancel_wait(uid, second["id"], force=True, by_user=True, agents=True))
    row = db.fetchone("SELECT status, cancel_requested FROM chat_runs WHERE id=?", (earlier["id"],))
    assert (row["status"], row["cancel_requested"]) == ("cancelled", chat_store.USER_STOPPED)


def test_a_subagent_the_user_stopped_never_wakes_an_idle_thread(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    stopped = child(uid, thread, parent, background=True)
    chat_store.finish_run(parent["id"], {"content": "ok"}, "completed")
    with patch.object(chat_runs, "cancel"):
        run_async(chat_runs.cancel_wait(uid, stopped["id"], tree=False, force=True, by_user=True))
    assert chat_store.get_run(uid, stopped["id"])["status"] == "cancelled"

    with patch("app.command_jobs.pending_envelopes", return_value=[]), \
            patch.object(chat_runs, "start", new=AsyncMock(return_value=None)):
        run_async(chat_runs._wake_parent(uid, parent["thread_id"]))
    assert len(root_runs(uid, parent["thread_id"])) == 1
    assert chat_store.list_thread_agents(uid, parent["thread_id"])[0]["wake_state"] == "held"


def test_a_subagent_killed_by_the_model_still_reports_to_an_idle_thread(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    killed = child(uid, thread, parent, background=True)
    chat_store.finish_run(parent["id"], {"content": "ok"}, "completed")
    chat_store.request_cancel_self(uid, killed["id"])
    chat_store.abort_active(killed["id"], "cancelled")

    with patch("app.command_jobs.pending_envelopes", return_value=[]), \
            patch.object(chat_runs, "start", new=AsyncMock(return_value=None)):
        run_async(chat_runs._wake_parent(uid, parent["thread_id"]))
    roots = root_runs(uid, parent["thread_id"])
    assert len(roots) == 2
    wake = next(r["id"] for r in roots if r["id"] != parent["id"])
    assert agent_of(uid, wake)["wake_ids"] == [killed["id"]]


def test_an_abandoned_parent_settles_its_spawn_trace_from_the_child(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    failed = child(uid, thread, parent)
    chat_store.finish_run(failed["id"], {"content": "", "error": "Child execution failed"}, "failed")
    chat_store.checkpoint(parent["id"], {"content": "working", "tools": [
        {"id": "t1", "name": "spawn_subagent", "child_id": failed["id"], "status": "running"}]})
    assert chat_store.abort_active(parent["id"], "cancelled")
    trace = chat_store.get_run(uid, parent["id"])["message"]["tools"][0]
    assert (trace["status"], trace["execution_status"]) == ("error", "failed")


# -- activity --------------------------------------------------------------

def test_a_quiet_running_child_shows_as_waiting_then_stalled(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    worker = child(uid, thread, parent)
    set_running(worker["id"])
    chat_store.checkpoint(worker["id"], {"content": "Now writing", "tools": [],
                                         "segments": [{"type": "content", "text": "Now writing"}]})
    pk = parent["thread_id"]
    assert chat_store.list_thread_agents(uid, pk)[0]["current"] == "writing"

    db.execute("UPDATE chat_runs SET updated_at=? WHERE id=?", (_ago(chat_store.STALL_WAIT_SECONDS + 30), worker["id"]))
    assert chat_store.list_thread_agents(uid, pk)[0]["current"] == "waiting"
    db.execute("UPDATE chat_runs SET updated_at=? WHERE id=?", (_ago(chat_store.STALL_IDLE_SECONDS + 20), worker["id"]))
    row = chat_store.list_thread_agents(uid, pk)[0]
    assert row["current"] == "stalled" and row["quiet_s"] >= chat_store.STALL_IDLE_SECONDS
    # A finished child is never stalled, however old.
    chat_store.finish_run(worker["id"], {"content": "done"}, "completed")
    db.execute("UPDATE chat_runs SET updated_at=? WHERE id=?", (_ago(10_000), worker["id"]))
    assert chat_store.list_thread_agents(uid, pk)[0]["current"] != "stalled"


def test_the_history_limit_never_hides_live_children(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    live = child(uid, thread, parent, prompt="live")
    for i in range(3):
        done = child(uid, thread, parent, prompt=str(i))
        chat_store.finish_run(done["id"], {"content": "done"}, "completed")
    rows = chat_store.list_thread_agents(uid, parent["thread_id"], limit=1)
    assert len(rows) == 2 and live["id"] in [r["id"] for r in rows]


def test_a_child_never_moves_the_threads_revision_or_transcript(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    worker = child(uid, thread, parent)
    rev = chats.get_thread(uid, thread["id"])["revision"]
    chat_store.checkpoint(worker["id"], {"content": "mid", "tools": []})
    chat_store.finish_run(worker["id"], {"content": "done", "tools": []}, "completed")
    after = chats.get_thread(uid, thread["id"])
    assert after["revision"] == rev
    assert worker["message_id"] not in [m["id"] for m in after["messages"]]
    assert after["run"]["id"] == parent["id"]


# -- work time ---------------------------------------------------------------

def _row(uid, pk, run_id):
    return next(r for r in chat_store.list_thread_agents(uid, pk) if r["id"] == run_id)


def test_work_time_sums_active_legs_never_the_gap_between_them(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    worker = child(uid, thread, parent, background=True)
    pk = parent["thread_id"]
    assert _row(uid, pk, worker["id"])["work_ms"] is None  # never timed: unknown, not zero
    chat_store.save_turn_progress(worker["id"], [{"role": "user", "content": "inspect"}, {"role": "assistant", "content": "a"}])
    chat_store.finish_run(worker["id"], {"content": "first", "elapsedMs": 60_000}, "completed")
    old = _ago(2 * 86_400)
    db.execute("UPDATE chat_runs SET created_at=?, updated_at=? WHERE id=?", (old, old, worker["id"]))

    child(uid, thread, parent, prompt="again", background=True, resume_from_id=worker["id"])
    assert _row(uid, pk, worker["id"])["work_ms"] == 60_000
    chat_store.queue_followup(worker["id"], "more")
    chat_store.finish_run(worker["id"], {"content": "second", "elapsedMs": 30_000}, "completed")
    row = _row(uid, pk, worker["id"])
    assert row["work_ms"] == 90_000 and row["work_live"] is False

    # A follow-up continuation banks the finished leg and starts a new one.
    assert chat_store.continue_child_with_followups(worker["id"], chat_store.take_followups(worker["id"]))
    assert agent_of(uid, worker["id"])["work_ms_before"] == 90_000
    chat_store.finish_run(worker["id"], {"content": "third", "elapsedMs": 5_000}, "completed")
    assert _row(uid, pk, worker["id"])["work_ms"] == 95_000


def test_a_user_resume_keeps_counting_on_the_same_message(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    worker = child(uid, thread, parent, background=True)
    chat_store.save_turn_progress(worker["id"], [{"role": "user", "content": "inspect"}, {"role": "assistant", "content": "a"}])
    chat_store.finish_run(worker["id"], {"content": "", "elapsedMs": 20_000, "stopped": True}, "cancelled")
    chat_store.resume_run(uid, worker["id"])
    assert chat_store.get_run(uid, worker["id"])["status"] == "queued"
    assert _row(uid, parent["thread_id"], worker["id"])["work_ms"] == 20_000


def test_a_live_leg_is_counted_until_its_own_token_ends_it(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    worker = child(uid, thread, parent)
    pk = parent["thread_id"]
    first = chat_store.begin_leg(worker["id"], 10_000)
    row = _row(uid, pk, worker["id"])
    assert row["work_live"] is True and row["work_ms"] >= 10_000
    second = chat_store.begin_leg(worker["id"], 12_000)
    chat_store.end_leg(worker["id"], first)  # stale token: the newer leg owns the slot
    assert _row(uid, pk, worker["id"])["work_live"] is True
    chat_store.end_leg(worker["id"], second)
    assert _row(uid, pk, worker["id"])["work_live"] is False


def test_a_settled_spawn_trace_carries_the_childs_work_time(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    worker = child(uid, thread, parent)
    chat_store.finish_run(worker["id"], {"content": "done", "elapsedMs": 7_000}, "completed")
    messages = [{"tools": [{"name": "spawn_subagent", "child_id": worker["id"], "status": "running"}]}]
    with db.tx() as conn:
        chat_store.settle_spawn_traces(conn, uid, messages)
    trace = messages[0]["tools"][0]
    assert (trace["status"], trace["agent_work_ms"]) == ("done", 7_000)


# -- admission under concurrency ---------------------------------------------

class _WatchedLock:
    """The admission lock, reporting when a second admission has to wait."""

    def __init__(self):
        self.inner = threading.Lock()
        self.contended = threading.Event()

    def __enter__(self):
        if not self.inner.acquire(blocking=False):
            self.contended.set()
            self.inner.acquire()
        return self

    def __exit__(self, *exc):
        self.inner.release()


def test_concurrent_admissions_cannot_both_pass_the_live_cap(fresh_db):
    uid = coding_user()
    one = parent_and_thread(uid, "one")
    two = parent_and_thread(uid, "two")
    lock = _WatchedLock()
    second_done = threading.Event()
    first_paused = threading.Event()
    real_stored = chat_children._stored_request
    paused = []

    def pause_first(raw):
        # Inside the first admission, after its counts and before its writes:
        # hold it until the second admission has either finished (no
        # serialization) or is waiting on the lock.
        if not paused:
            paused.append(True)
            first_paused.set()
            wait_until(lambda: second_done.is_set() or lock.contended.is_set(), message="second admission never ran")
        return real_stored(raw)

    results = {}

    def admit(name, thread, parent):
        try:
            results[name] = child(uid, thread, parent, prompt=name)["id"]
        except SpawnCapError as exc:
            results[name] = exc
        finally:
            if name == "second":
                second_done.set()

    # Two queued roots are live; the cap leaves room for exactly one child.
    with patch.object(chat_children, "LIVE_USER_RUNS", 3), patch.object(chat_children, "_child_admission_lock", lock), \
            patch.object(chat_children, "_stored_request", side_effect=pause_first):
        first = threading.Thread(target=admit, args=("first", *one))
        first.start()
        assert first_paused.wait(5)
        second = threading.Thread(target=admit, args=("second", *two))
        second.start()
        first.join(10)
        second.join(10)

    admitted = [name for name, value in results.items() if isinstance(value, str)]
    assert admitted == ["first"], results
    assert isinstance(results["second"], SpawnCapError)
    live = db.fetchone("SELECT COUNT(*) AS c FROM chat_runs WHERE user_id=? AND status IN ('queued','running')", (uid,))["c"]
    assert live == 3
