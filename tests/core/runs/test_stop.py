"""User Stop, Send now and force-stop: the run ends cancelled with a stopped
reply, a Stop racing the final commit still wins, and a stop is durable
thread intent no automated turn may override."""
import asyncio
from unittest.mock import patch

import pytest

from app import chat_runs, chat_store, chats, db
from app.chats import ChatValidationError
from tests.core.helpers import (
    fake_provider, quick_user, run_async, run_turn, text_frames, tool_call_frames, wait_run, wait_until_async,
)

from ._support import fake_tools, held_stream, provider_messages, run_row, settle, start_run, tool_thread


def _reply(uid, thread_id):
    return chats.get_thread(uid, thread_id)["messages"][-1]


def test_stop_while_a_tool_runs_cancels_the_turn_and_interrupts_the_tool(fresh_db):
    uid = quick_user()

    async def body():
        tools.hold()
        thread, run, task = await start_run(uid, "search", thread=tool_thread(uid))
        await asyncio.wait_for(tools.started.wait(), 5)
        await chat_runs.cancel_wait(uid, run["id"])
        await wait_run(task, 5)
        return thread, run

    with fake_tools() as tools, fake_provider(tool_call_frames([("c1", "web_search", {"query": "x"})])) as provider:
        thread, run = run_async(body())

    assert run_row(run["id"])["status"] == "cancelled"
    assert len(provider.bodies) == 1
    reply = _reply(uid, thread["id"])
    assert reply["stopped"] is True
    assert [t["status"] for t in reply["tools"]] == ["interrupted"]


def test_stop_mid_reply_keeps_the_text_already_streamed(fresh_db):
    uid = quick_user()

    async def body():
        provider.arm()
        thread, run, task = await start_run(uid, "write a story")
        await asyncio.wait_for(provider.sent.wait(), 5)
        await chat_runs.cancel_wait(uid, run["id"])
        await wait_run(task, 5)
        return thread, run

    with held_stream("Once upon a time") as provider:
        thread, run = run_async(body())

    assert run_row(run["id"])["status"] == "cancelled"
    reply = _reply(uid, thread["id"])
    assert reply["content"] == "Once upon a time"
    assert reply["stopped"] is True and not reply.get("error")


def test_stop_without_a_live_worker_settles_the_run_at_once(fresh_db):
    uid = quick_user()
    thread = chats.create_thread(uid, model="offline", tools=[])
    run = chat_store.start_turn(uid, thread["id"], "never started", thread["revision"], None)["run"]

    run_async(chat_runs.cancel_wait(uid, run["id"]))

    assert run_row(run["id"])["status"] == "cancelled"
    reply = _reply(uid, thread["id"])
    assert reply["stopped"] is True and "Stopped." in reply["notice"]
    # The thread is free for the next turn.
    current = chats.get_thread(uid, thread["id"])
    assert chat_store.start_turn(uid, thread["id"], "again", current["revision"], None)["run"]


def test_stop_landing_during_the_final_commit_wins(fresh_db):
    uid = quick_user()
    real_finish = chat_store.finish_run

    def stop_at_commit(ident, message, status):
        chat_store.request_cancel(uid, ident)
        return real_finish(ident, message, status)

    with fake_provider(text_frames("Done")), patch.object(chat_store, "finish_run", side_effect=stop_at_commit):
        thread_id, run = run_async(run_turn(uid, "go"))

    assert run["status"] == "cancelled"
    reply = _reply(uid, thread_id)
    assert reply["stopped"] is True
    assert reply["content"] == "Done"


def test_a_root_stop_outlives_the_run_and_blocks_automated_turns(fresh_db):
    uid = quick_user()
    with fake_provider(text_frames("finished")):
        thread_id, run = run_async(run_turn(uid, "go"))
    # Stop clicked just after the turn finished: still durable intent.
    assert chat_store.request_cancel(uid, run["id"]) is True
    assert run_row(run["id"])["status"] == "completed"

    thread = chats.get_thread(uid, thread_id)
    with pytest.raises(ChatValidationError) as refused:
        chat_store.start_turn(uid, thread_id, "AUTOMATED EVENT", thread["revision"], None, auto=True)
    assert refused.value.status_code == 409
    # The user's own next message is still accepted.
    assert chat_store.start_turn(uid, thread_id, "carry on", thread["revision"], None)["run"]


def test_stop_ends_foreground_subagents_but_send_now_keeps_them(fresh_db):
    uid = quick_user()

    async def scenario(action):
        tools.hold()
        thread, run, task = await start_run(uid, "work", thread=tool_thread(uid))
        await asyncio.wait_for(tools.started.wait(), 5)
        child = await asyncio.to_thread(
            chat_store.start_child_run, user_id=uid, thread_id=thread["id"], parent_run_id=run["id"],
            prompt="sub task", description="helper")
        await action(uid, run["id"])
        await wait_run(task, 5)
        return run, child

    for action, child_survives in ((chat_runs.cancel_wait, False), (chat_runs.interrupt_wait, True)):
        with fake_tools() as tools, fake_provider(tool_call_frames([("c1", "web_search", {"query": "x"})])):
            run, child = run_async(scenario(action))
        assert run_row(run["id"])["status"] == "cancelled"
        child_row = run_row(child["id"])
        if child_survives:
            assert child_row["cancel_requested"] == 0, "Send now must leave the subagent running"
            assert child_row["status"] in chat_store.ACTIVE
            assert chat_store.get_run(uid, child["id"])["request"]["agent"]["background"] is True
        else:
            assert child_row["cancel_requested"] and child_row["status"] == "cancelled"


def _wedged_tool(events):
    """A tool that ignores the first cancellation (a stuck transport), so the
    worker outlives the Stop/Send now grace window."""
    async def wedged(name, arguments, **kwargs):
        events["started"].set()
        try:
            await events["release"].wait()
        except asyncio.CancelledError:
            await events["release"].wait()
        return "late", {"name": name, "status": "done"}
    return wedged


def test_send_now_keeps_background_commands_past_the_grace_window_and_the_reaper(fresh_db):
    from app import command_jobs

    uid = quick_user()
    events = {}

    async def scenario(action, reap):
        events.update(started=asyncio.Event(), release=asyncio.Event())
        thread, run, task = await start_run(uid, "go", thread=tool_thread(uid))
        await asyncio.wait_for(events["started"].wait(), 5)
        job = {"id": f"job-{run['id']}", "run_id": run["id"], "user_id": uid, "kind": "command", "status": "running"}
        command_jobs._live[job["id"]] = job
        try:
            if reap:
                # The reaper ticks while Send now still waits on the worker:
                # first a live stopping run, then one past the force deadline.
                stopping = asyncio.create_task(action(uid, run["id"]))
                await wait_until_async(lambda: run_row(run["id"])["cancel_requested"])
                await asyncio.to_thread(chat_runs.reap_orphans)
                with patch.object(chat_runs, "FORCE_CANCEL_SECONDS", 0):
                    await asyncio.to_thread(chat_runs.reap_orphans)
                events["release"].set()
                await asyncio.wait_for(stopping, 5)
            else:
                with patch.object(chat_runs, "CANCEL_GRACE_SECONDS", 0):
                    await action(uid, run["id"])
                events["release"].set()
            await asyncio.wait_for(asyncio.wait({task}), 5)
        finally:
            command_jobs._live.pop(job["id"], None)
        return run, job

    for action, reap, keeps in ((chat_runs.interrupt_wait, False, True), (chat_runs.interrupt_wait, True, True),
                                (chat_runs.cancel_wait, False, False)):
        with patch("app.chat_runtime.execute_tool", new=_wedged_tool(events)), \
                patch.object(command_jobs, "_kill_from_anywhere") as kill, \
                fake_provider(tool_call_frames([("c1", "web_search", {"query": "x"})])):
            run, job = run_async(scenario(action, reap))
        assert run_row(run["id"])["status"] == "cancelled"
        killed = [call.args[0]["id"] for call in kill.call_args_list]
        if keeps:
            assert killed == [], f"Send now killed the run's background command (reaper={reap})"
        else:
            assert job["id"] in killed, "Stop ends the run's background commands"


def test_force_stop_then_continue_survives_the_wedged_old_worker(fresh_db):
    """Force stop settles a worker stuck in a tool at once; Continue starts a
    new leg; the old worker's late tool result and saves never reach it."""
    from app import chat_recovery

    uid = quick_user()
    release = started = None

    async def wedged(name, arguments, **kwargs):
        # A tool that ignores the first cancellation (a stuck transport).
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return "LATE RESULT", {"name": name, "status": "done"}

    async def body():
        nonlocal release, started
        release, started = asyncio.Event(), asyncio.Event()
        thread, run, task = await start_run(uid, "go", thread=tool_thread(uid))
        await asyncio.wait_for(started.wait(), 5)
        await chat_runs.cancel_wait(uid, run["id"], force=True)
        settled = await asyncio.to_thread(run_row, run["id"])
        await asyncio.to_thread(chat_recovery.resume_run, uid, run["id"])
        requeued = await asyncio.to_thread(provider_messages, run["id"])
        # The old worker comes unstuck and finishes its round after the resume.
        release.set()
        await asyncio.wait_for(asyncio.wait({task}), 5)
        after_old = await asyncio.to_thread(provider_messages, run["id"])
        await chat_runs.start(chat_runs.internal_request(uid), {"id": uid}, chat_store.get_run(uid, run["id"]))
        await settle()
        return thread, run, settled, requeued, after_old

    with patch("app.chat_runtime.execute_tool", new=wedged), fake_provider(
        tool_call_frames([("c1", "web_search", {"query": "x"})]), text_frames("RESUMED ANSWER"),
    ) as provider:
        thread, run, settled, requeued, after_old = run_async(body())

    assert settled["status"] == "cancelled"
    assert after_old == requeued, "the old leg's late saves never touch the resumed transcript"
    assert dict(run_row(run["id"])) == {"status": "completed", "cancel_requested": 0, "resume_count": 1}
    assert len(provider.bodies) == 2, "the old worker never calls the model again"
    resumed = provider.bodies[1]["messages"]
    assert not any("LATE RESULT" in str(m.get("content")) for m in resumed)
    tool_rows = [m for m in resumed if m.get("role") == "tool"]
    assert len(tool_rows) == 1 and tool_rows[0]["content"].startswith("Tool interrupted when the user stopped")
    assert resumed[-1]["content"].startswith(chat_recovery.USER_RESUME_NOTE_HEAD)
    assert _reply(uid, thread["id"])["content"] == "RESUMED ANSWER"


def test_continue_after_force_stop_starts_a_new_leg_while_the_old_worker_is_still_stuck(fresh_db):
    """The old worker is still wedged (here in its first save, which ignores
    cancellation) when Continue lands: the resume gets its own worker, and
    the reaper does not take the queued row for a stalled one."""
    import threading

    from app import chat_checkpoint, chat_recovery

    uid = quick_user()
    real_checkpoint = chat_store.checkpoint
    entered, gate = threading.Event(), threading.Event()

    def first_save_hangs(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            gate.wait(10)
        return real_checkpoint(*args, **kwargs)

    thread = chats.create_thread(uid, model="offline", tools=[])
    run = chat_store.start_turn(uid, thread["id"], "go", thread["revision"], None)["run"]
    # A saved transcript, so the stopped run can be continued.
    chat_checkpoint.save_turn_progress(run["id"], [*run["request"]["messages"], {"role": "assistant", "content": "working"}])

    async def body():
        old = await chat_runs.start(chat_runs.internal_request(uid), {"id": uid}, run)
        await asyncio.to_thread(entered.wait, 5)
        await chat_runs.cancel_wait(uid, run["id"], force=True)
        await asyncio.to_thread(chat_recovery.resume_run, uid, run["id"])
        with patch.object(chat_runs, "STALL_IDLE_SECONDS", 0):
            await asyncio.to_thread(chat_runs.reap_orphans)
        reaped = await asyncio.to_thread(run_row, run["id"])
        new = await chat_runs.start(chat_runs.internal_request(uid), {"id": uid}, chat_store.get_run(uid, run["id"]))
        if new is not None and new is not old:
            await asyncio.wait_for(asyncio.wait({new}), 5)
        gate.set()
        await asyncio.wait_for(asyncio.wait({old}), 5)
        await settle()
        return old, new, reaped

    with patch.object(chat_store, "checkpoint", side_effect=first_save_hangs), \
            fake_provider(text_frames("RESUMED ANSWER")) as provider:
        old, new, reaped = run_async(body())

    assert reaped["status"] == "queued", "the stuck old worker is not the queued run's worker"
    assert new is not None and new is not old
    assert len(provider.bodies) == 1
    assert dict(run_row(run["id"])) == {"status": "completed", "cancel_requested": 0, "resume_count": 1}
    assert _reply(uid, thread["id"])["content"] == "RESUMED ANSWER"


def test_stop_between_start_and_its_worker_never_calls_the_model(fresh_db):
    import threading

    uid = quick_user()
    thread = chats.create_thread(uid, model="offline", tools=[])
    run = chat_store.start_turn(uid, thread["id"], "go", thread["revision"], None)["run"]
    real_get_run, real_fetchone = chat_store.get_run, db.fetchone
    stopped, called = [], threading.Event()

    def poll_after_the_model(sql, *args, **kwargs):
        # The worker's background status poll must not be what saves the day.
        if sql.startswith("SELECT status,cancel_requested FROM chat_runs WHERE id=? AND user_id=?"):
            called.wait(5)
        return real_fetchone(sql, *args, **kwargs)

    def model(_body):
        called.set()
        return text_frames("should not run")

    def read_then_stop(user_id, run_id, *args, **kwargs):
        row = real_get_run(user_id, run_id, *args, **kwargs)
        if not stopped:
            # start() has read the queued row; Stop lands before its task exists.
            stopped.append(True)
            chat_store.request_cancel(uid, run["id"])
            chat_runs.cancel(run["id"])
        return row

    async def body():
        task = await chat_runs.start(chat_runs.internal_request(uid), {"id": uid}, run)
        if task is not None:
            await asyncio.wait_for(asyncio.wait({task}), 10)
        called.set()
        await settle()

    with fake_provider(model) as provider, patch.object(chat_store, "get_run", side_effect=read_then_stop), \
            patch.object(db, "fetchone", side_effect=poll_after_the_model):
        run_async(body())

    assert stopped
    assert provider.bodies == []
    assert run_row(run["id"])["status"] == "cancelled"
    assert _reply(uid, thread["id"])["stopped"] is True


def test_reaper_finishes_a_stop_no_worker_owns(fresh_db):
    uid = quick_user()
    thread = chats.create_thread(uid, model="offline", tools=[])
    run = chat_store.start_turn(uid, thread["id"], "x", thread["revision"], None)["run"]
    db.execute("UPDATE chat_runs SET status='running', cancel_requested=1, cancel_requested_at=? WHERE id=?",
               (db.now(), run["id"]))

    chat_runs.reap_orphans()

    assert run_row(run["id"])["status"] == "cancelled"
    assert _reply(uid, thread["id"])["stopped"] is True


def test_reaper_interrupts_a_live_worker_that_stopped_making_progress(fresh_db):
    uid = quick_user()

    async def body():
        tools.hold()
        thread, run, task = await start_run(uid, "go", thread=tool_thread(uid))
        await asyncio.wait_for(tools.started.wait(), 5)
        # Any quiet time now counts as a stall.
        with patch.object(chat_runs, "STALL_IDLE_SECONDS", 0):
            await asyncio.to_thread(chat_runs.reap_orphans)
        await wait_until_async(lambda: task.done())
        return thread, run

    with fake_tools() as tools, fake_provider(tool_call_frames([("c1", "web_search", {"query": "x"})])):
        thread, run = run_async(body())

    assert run_row(run["id"])["status"] == "interrupted"
    assert chat_store.STALL_NOTICE in _reply(uid, thread["id"])["notice"]
