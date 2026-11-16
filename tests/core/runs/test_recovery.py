"""Restart recovery: runs with a saved transcript resume (once-only resume
note, bounded attempts, never re-running a tool); runs without one are
finalized interrupted; a user Continue resumes only the latest stopped turn."""
import asyncio
import json

import pytest

from app import chat_checkpoint, chat_recovery, chat_runs, chat_store, chats, db
from app.chat_run_state import INTERRUPT_NOTICE, MAX_RESUME_ATTEMPTS
from app.chats import ChatValidationError
from tests.core.helpers import fake_provider, make_user, run_async, text_frames, tool_call_frames

from ._support import fake_tools, provider_messages, run_row, settle, start_run, tool_thread

RESUME_HEAD = chat_recovery.RESUME_NOTE_HEAD


def _crashed_run(uid, transcript=None, status="running", text="do the work"):
    """A root run left ACTIVE by a dead process, with ``transcript`` saved."""
    thread = chats.create_thread(uid, model="offline", tools=[])
    run = chat_store.start_turn(uid, thread["id"], text, thread["revision"], None)["run"]
    if transcript is not None:
        chat_checkpoint.save_turn_progress(run["id"], transcript)
    db.execute("UPDATE chat_runs SET status=? WHERE id=?", (status, run["id"]))
    return thread, run


def _transcript(*extra):
    return [{"role": "system", "content": "sys"}, {"role": "user", "content": "do the work"}, *extra]


def _notes(messages, head=RESUME_HEAD):
    return [m for m in messages if m.get("role") == "user" and str(m.get("content")).startswith(head)]


def test_shutdown_mid_tool_resumes_after_restart_without_rerunning_the_tool(fresh_db):
    uid = make_user()

    async def before_restart():
        tools.hold()
        thread, run, task = await start_run(uid, "search", thread=tool_thread(uid))
        await asyncio.wait_for(tools.started.wait(), 5)
        await chat_runs.stop()  # graceful shutdown with a durable transcript
        await asyncio.wait({task}, timeout=5)
        return thread, run

    async def after_restart():
        await asyncio.to_thread(chat_recovery.recover_runs)
        await chat_runs.resume_queued(uid)
        await settle()

    with fake_tools() as tools, fake_provider(
        tool_call_frames([("c1", "web_search", {"query": "x"})]), text_frames("finished after restart"),
    ) as provider:
        thread, run = run_async(before_restart())
        assert run_row(run["id"])["status"] in chat_store.ACTIVE, "left active for the next boot"
        run_async(after_restart())

    assert run_row(run["id"])["status"] == "completed"
    assert len(tools.calls) == 1, "a tool with an unknown outcome is never re-run"
    resumed = provider.bodies[-1]["messages"]
    results = [m for m in resumed if m.get("role") == "tool"]
    assert [r["tool_call_id"] for r in results] == ["c1"]
    assert results[0]["content"].startswith("Tool interrupted")
    assert len(_notes(resumed)) == 1
    reply = chats.get_thread(uid, thread["id"])["messages"][-1]
    assert reply["content"] == "finished after restart"
    assert not reply.get("error")


def test_a_crash_before_any_saved_progress_is_finalized_interrupted(fresh_db):
    uid = make_user()
    thread, run = _crashed_run(uid)

    chat_recovery.recover_runs()

    assert run_row(run["id"])["status"] == "interrupted"
    reply = chats.get_thread(uid, thread["id"])["messages"][-1]
    assert reply["stopped"] is True and INTERRUPT_NOTICE in reply["notice"]


def test_a_never_started_turn_stays_queued_and_then_runs(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline", tools=[])
    run = chat_store.start_turn(uid, thread["id"], "hello", thread["revision"], None)["run"]

    chat_recovery.recover_runs()
    assert dict(run_row(run["id"])) == {"status": "queued", "cancel_requested": 0, "resume_count": 0}

    async def boot():
        await chat_runs.resume_queued(uid)
        await settle()

    with fake_provider(text_frames("hi there")):
        run_async(boot())
    assert run_row(run["id"])["status"] == "completed"


def test_missing_tool_results_are_sealed_and_the_resume_note_is_added_once(fresh_db):
    uid = make_user()
    call = {"id": "c9", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}
    _, run = _crashed_run(uid, _transcript({"role": "assistant", "content": "", "tool_calls": [call]}))

    chat_recovery.recover_runs()
    first = provider_messages(run["id"])
    # The process dies again before the resumed leg made progress.
    chat_recovery.recover_runs()
    second = provider_messages(run["id"])

    assert run_row(run["id"])["status"] == "queued"
    sealed = [m for m in second if m.get("role") == "tool"]
    assert [(m["tool_call_id"], m["content"].startswith("Tool interrupted")) for m in sealed] == [("c9", True)]
    assert len(_notes(first)) == 1 and len(_notes(second)) == 1
    assert run_row(run["id"])["resume_count"] == 2


def test_resume_attempts_are_capped_then_the_run_is_interrupted(fresh_db):
    uid = make_user()
    _, run = _crashed_run(uid, _transcript({"role": "assistant", "content": "partial"}))
    db.execute("UPDATE chat_runs SET resume_count=? WHERE id=?", (MAX_RESUME_ATTEMPTS, run["id"]))

    chat_recovery.recover_runs()

    assert run_row(run["id"])["status"] == "interrupted"


def test_a_stop_pending_at_boot_finishes_cancelled(fresh_db):
    uid = make_user()
    _, run = _crashed_run(uid, _transcript({"role": "assistant", "content": "partial"}))
    db.execute("UPDATE chat_runs SET cancel_requested=1 WHERE id=?", (run["id"],))

    chat_recovery.recover_runs()

    assert run_row(run["id"])["status"] == "cancelled"


def test_boot_requeues_a_bounded_batch_and_the_reaper_drains_the_rest(fresh_db):
    uid = make_user()
    batch = chat_recovery.BOOT_RESUME_BATCH
    runs = [_crashed_run(uid, _transcript({"role": "assistant", "content": f"p{i}"}), text=f"t{i}")[1]
            for i in range(batch + 2)]

    chat_recovery.recover_runs()
    statuses = [run_row(r["id"])["status"] for r in runs]
    assert statuses.count("queued") == batch and statuses.count("running") == 2

    # The reaper only requeues while fewer than a batch are waiting to start.
    chat_runs.reap_orphans()
    assert [run_row(r["id"])["status"] for r in runs].count("queued") == batch
    db.execute("UPDATE chat_runs SET status='completed' WHERE id=?", (runs[0]["id"],))
    chat_runs.reap_orphans()
    assert [run_row(r["id"])["status"] for r in runs[1:]].count("queued") == batch


def test_reaper_leaves_a_requeued_run_for_its_worker(fresh_db):
    uid = make_user()
    _, run = _crashed_run(uid, _transcript({"role": "assistant", "content": "p"}))
    chat_recovery.recover_runs()
    before = provider_messages(run["id"])

    chat_runs.reap_orphans()

    assert dict(run_row(run["id"])) == {"status": "queued", "cancel_requested": 0, "resume_count": 1}
    assert provider_messages(run["id"]) == before


def test_stale_recovery_never_overwrites_a_newer_write(fresh_db):
    uid = make_user()
    _, run = _crashed_run(uid, _transcript({"role": "assistant", "content": "p"}))
    stale = db.fetchone("SELECT * FROM chat_runs WHERE id=?", (run["id"],))
    # A Stop lands between the recovery's read and its requeue.
    db.execute("UPDATE chat_runs SET cancel_requested=1, cancel_requested_at=? WHERE id=?", (db.now(), run["id"]))

    chat_recovery.recover_one(stale)

    row = run_row(run["id"])
    assert row["status"] == "running" and row["resume_count"] == 0 and row["cancel_requested"] == 1


# -- user Continue -------------------------------------------------------

def test_continue_resumes_a_stopped_turn_without_claiming_a_restart(fresh_db):
    uid = make_user()
    thread, run = _crashed_run(uid, _transcript({"role": "assistant", "content": "half done"}))
    chat_store.abort_active(run["id"], "cancelled")

    resumed = chat_recovery.resume_run(uid, run["id"])

    assert resumed["status"] == "queued"
    saved = provider_messages(run["id"])
    assert len(_notes(saved, chat_recovery.USER_RESUME_NOTE_HEAD)) == 1
    assert not _notes(saved)
    assert run_row(run["id"])["cancel_requested"] == 0


@pytest.mark.parametrize("case", ["completed", "no_transcript", "not_latest", "budget_spent"])
def test_continue_is_refused_when_it_cannot_resume_safely(fresh_db, case):
    uid = make_user()
    transcript = None if case == "no_transcript" else _transcript({"role": "assistant", "content": "x"})
    thread, run = _crashed_run(uid, transcript)
    chat_store.abort_active(run["id"], "interrupted")
    if case == "completed":
        db.execute("UPDATE chat_runs SET status='completed' WHERE id=?", (run["id"],))
    elif case == "not_latest":
        current = chats.get_thread(uid, thread["id"])
        chat_store.start_turn(uid, thread["id"], "newer turn", current["revision"], None)
    elif case == "budget_spent":
        db.execute("UPDATE chat_runs SET resume_count=? WHERE id=?", (MAX_RESUME_ATTEMPTS, run["id"]))
    before = json.dumps(dict(run_row(run["id"], "status,resume_count,message")))

    with pytest.raises(ChatValidationError) as refused:
        chat_recovery.resume_run(uid, run["id"])

    assert refused.value.status_code == 409
    assert json.dumps(dict(run_row(run["id"], "status,resume_count,message"))) == before
