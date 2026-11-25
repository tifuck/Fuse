"""Command job lifecycle: the durable row, terminal states, and delivery.

A job's result reaches the model exactly once: queued into the live run
that started it (acknowledged by that run's receipt), or handed to a wake of
the idle thread, or returned inline by an explicit join or kill — and a
subagent's result never surfaces in the main agent's thread. Runners are
scripted (tests.core.tools._support.ScriptedRunner); the rest is real.
"""
import asyncio

import pytest

from app import chat_runs, chat_store, command_jobs, db, run_inbox
from tests.core.helpers import quick_user, run_async, wait_until_async
from tests.core.tools._support import ScriptedRunner, launch, new_run, new_thread


@pytest.fixture
def wakes(monkeypatch):
    """Threads a terminal job asked to wake (the wake turn itself is the runs area's)."""
    calls = []
    monkeypatch.setattr(chat_runs, "schedule_wake", lambda uid, tid: calls.append((uid, tid)))
    return calls


def row(job_id):
    return db.fetchone("SELECT * FROM command_jobs WHERE id=?", (job_id,))


async def settled(rec):
    await asyncio.wait_for(asyncio.shield(rec["future"]), 5)
    return rec


def test_a_job_is_durable_from_launch_to_its_terminal_state(fresh_db):
    uid = quick_user()

    async def body():
        runner = ScriptedRunner(out="compiling\nok\n")
        rec = await launch(uid, runner, background=False, description="Run the tests")
        await runner.started.wait()
        running = row(rec["id"])
        runner.release(code=0)
        await settled(rec)
        return rec["id"], running

    job_id, running = run_async(body())

    assert (running["status"], running["command"], running["cwd"]) == ("running", "make test", "/srv/app")
    assert running["description"] == "Run the tests" and running["started_at"]
    done = row(job_id)
    assert (done["status"], done["exit_code"], done["out"]) == ("completed", 0, "compiling\nok\n")
    assert done["termination_confirmed"] == 1 and done["finished_at"] and done["duration_ms"] is not None
    # Read back from the database alone (as after a restart), the result is intact.
    command_jobs._live.clear()
    text, trace = asyncio.run(command_jobs.join(job_id, uid, 0))
    assert trace["state"] == "completed" and "compiling\nok" in text


def test_terminal_states_say_what_happened_not_just_that_it_ended(fresh_db):
    uid = quick_user()

    async def body():
        outcomes = {}
        cases = {
            "deadline": command_jobs.CommandTimedOut(termination_confirmed=True),
            "no_exit": None,
            "busy": RuntimeError("connector could not execute command: agent is busy"),
        }
        for name, exc in cases.items():
            runner = ScriptedRunner(code=None) if exc is None else ScriptedRunner()
            rec = await launch(uid, runner)
            runner.release(exc=exc) if exc is not None else runner.release()
            await settled(rec)
            outcomes[name] = row(rec["id"])
        return outcomes

    out = run_async(body())

    assert out["deadline"]["status"] == "timed_out"
    assert "execution deadline" in out["deadline"]["error"] and out["deadline"]["termination_confirmed"] == 1
    # No exit status is an unknown outcome, never a successful completion.
    assert out["no_exit"]["status"] == "interrupted" and out["no_exit"]["exit_code"] is None
    assert "outcome is unknown" in out["no_exit"]["error"]
    assert out["busy"]["status"] == "interrupted" and "agent is busy" in out["busy"]["error"]
    assert all(r["output_incomplete"] == 1 for r in (out["no_exit"], out["busy"]))


def test_a_moved_command_stops_at_its_background_limit(fresh_db):
    uid = quick_user()

    async def body():
        runner = ScriptedRunner()
        rec = await launch(uid, runner, background=False, description="Long build")
        await runner.started.wait()
        command_jobs.arm_background(rec, limit_ms=1)
        await settled(rec)
        return rec

    rec = run_async(body())

    assert rec["status"] == "timed_out"
    assert rec["output_incomplete"] is True and rec["termination_confirmed"] is False
    assert 'Background command "Long build" was stopped after reaching its background time limit' in rec["error"]
    assert row(rec["id"])["background"] == 1


def test_a_result_for_a_live_run_is_queued_there_and_acknowledged_once(fresh_db, wakes):
    uid = quick_user()
    _, tid, run_id = new_run(uid)

    async def body():
        runner = ScriptedRunner(out="42\n")
        rec = await launch(uid, runner, thread_id=tid, run_id=run_id)
        command_jobs.mark_notified(rec)
        runner.release(code=0)
        await settled(rec)
        return rec["id"]

    job_id = run_async(body())

    queued = chat_store.take_child_results(run_id)
    assert len(queued) == 1 and f'<command_result id="{job_id}" state="completed">' in queued[0]
    assert "exit_code: 0" in queued[0] and "42" in queued[0]
    # Still undelivered until the run's transcript receipt: if the run dies
    # first, the thread's wake must still deliver it.
    assert [item["id"] for item in command_jobs.pending_envelopes(uid, tid)] == [job_id]
    command_jobs.mark_delivered([job_id], user_id=uid, thread_id=tid)
    assert command_jobs.pending_envelopes(uid, tid) == []
    assert row(job_id)["delivered"] == 1


def test_an_explicit_join_supersedes_the_queued_delivery(fresh_db, wakes):
    uid = quick_user()
    _, tid, run_id = new_run(uid)

    async def body():
        runner = ScriptedRunner(out="done\n")
        rec = await launch(uid, runner, thread_id=tid, run_id=run_id)
        command_jobs.mark_notified(rec)
        runner.release(code=0)
        await settled(rec)
        assert len(chat_store.take_child_results(run_id)) == 1
        return await command_jobs.join(rec["id"], uid, 5, thread_id=tid, run_id=run_id)

    text, trace = run_async(body())

    assert trace["state"] == "completed" and "done" in text
    assert chat_store.take_child_results(run_id) == []
    assert command_jobs.pending_envelopes(uid, tid) == []


def test_an_idle_thread_is_woken_and_its_envelope_is_peek_only_until_receipt(fresh_db, wakes):
    uid = quick_user()
    _, tid = new_thread(uid)

    async def body():
        runner = ScriptedRunner(out="built\n")
        rec = await launch(uid, runner, thread_id=tid)
        command_jobs.mark_notified(rec)
        runner.release(code=2)
        await settled(rec)
        return rec["id"]

    job_id = run_async(body())

    assert wakes == [(uid, tid)]
    first = command_jobs.pending_envelopes(uid, tid)
    assert first == command_jobs.pending_envelopes(uid, tid)  # reading is not delivering
    assert [item["id"] for item in first] == [job_id]
    assert "exit_code: 2" in first[0]["envelope"]
    assert {"user_id": uid, "thread_id": tid, "resumed_at": ""} in command_jobs.pending_wake_threads()
    command_jobs.mark_delivered([job_id], user_id=uid, thread_id=tid)
    assert command_jobs.pending_envelopes(uid, tid) == []
    assert all(t["thread_id"] != tid for t in command_jobs.pending_wake_threads())


def test_a_job_that_finished_inside_the_foreground_wait_is_never_delivered_again(fresh_db, wakes):
    uid = quick_user()
    _, tid = new_thread(uid)

    async def body():
        runner = ScriptedRunner(out="quick\n")
        rec = await launch(uid, runner, thread_id=tid, background=False)
        runner.release(code=0)
        text, trace = await command_jobs.join(rec["id"], uid, 5, thread_id=tid)
        return rec["id"], trace

    job_id, trace = run_async(body())

    assert trace["state"] == "completed"
    assert wakes == [] and command_jobs.pending_envelopes(uid, tid) == []
    assert row(job_id)["delivered"] == 1


def test_a_subagents_job_goes_to_that_subagent_only(fresh_db, wakes):
    uid = quick_user()
    thread, tid, parent_run = new_run(uid)
    _, _, child_run = new_run(uid)
    db.execute("UPDATE chat_runs SET parent_run_id=? WHERE id=?", (parent_run, child_run))

    async def body(run_id):
        runner = ScriptedRunner(out="child output\n")
        rec = await launch(uid, runner, thread_id=tid, run_id=run_id)
        command_jobs.mark_notified(rec)
        runner.release(code=0)
        await settled(rec)
        return rec["id"]

    live_job = run_async(body(child_run))
    assert len(chat_store.take_child_results(child_run)) == 1
    db.execute("UPDATE chat_runs SET status='completed' WHERE id=?", (child_run,))
    orphan_job = run_async(body(child_run))

    assert wakes == []
    assert command_jobs.pending_envelopes(uid, tid) == []
    assert chat_store.take_child_results(parent_run) == []
    # Nobody is left to read the finished subagent's result: settled, not resurfaced.
    assert row(orphan_job)["delivered"] == 1
    assert row(live_job)["delivered"] == 0


def test_kill_by_the_agent_is_its_own_receipt_and_a_user_stop_is_reported(fresh_db, wakes):
    uid = quick_user()
    _, tid = new_thread(uid)

    async def body():
        agent_runner, user_runner = ScriptedRunner(), ScriptedRunner()
        by_agent = await launch(uid, agent_runner, thread_id=tid)
        by_user = await launch(uid, user_runner, thread_id=tid)
        command_jobs.mark_notified(by_agent)
        command_jobs.mark_notified(by_user)
        _, first = command_jobs.kill(by_agent["id"], uid, thread_id=tid)
        assert command_jobs.kill_thread_job(uid, tid + 1000, by_user["id"]) is None
        assert by_user["status"] == "running"
        command_jobs.kill_thread_job(uid, tid, by_user["id"])
        await settled(by_agent)
        await settled(by_user)
        return by_agent, by_user, first, agent_runner

    by_agent, by_user, first, agent_runner = run_async(body())

    assert first["state"] == "cancelling" and agent_runner.cancelled
    assert row(by_agent["id"])["status"] == "cancelled" and row(by_agent["id"])["delivered"] == 1
    envelopes = command_jobs.pending_envelopes(uid, tid)
    assert [item["id"] for item in envelopes] == [by_user["id"]]
    assert "Stopped by the user" in envelopes[0]["envelope"]
    assert wakes == [(uid, tid)]


def test_the_jobs_panel_stops_background_jobs_only(fresh_db, wakes):
    uid = quick_user()
    _, tid = new_thread(uid)

    async def body():
        runner = ScriptedRunner()
        foreground = await launch(uid, runner, thread_id=tid, background=False)
        refused = command_jobs.kill_thread_job(uid, tid, foreground["id"])
        status = foreground["status"]
        runner.release()
        await settled(foreground)
        return refused, status

    refused, status = run_async(body())
    assert refused is None and status == "running"


def test_deleting_a_thread_stops_its_commands_and_monitors_only(fresh_db, wakes):
    uid = quick_user()
    _, tid = new_thread(uid)
    _, other_tid = new_thread(uid)

    async def body():
        runners = [ScriptedRunner() for _ in range(3)]
        command = await launch(uid, runners[0], thread_id=tid)
        watch = await launch(uid, runners[1], thread_id=tid, kind="monitor", description="d", command="tail -f x")
        elsewhere = await launch(uid, runners[2], thread_id=other_tid)
        command_jobs.cancel_thread(uid, tid)
        right_after = (command["status"], watch["status"], elsewhere["status"])
        for runner in runners:
            runner.release()
        for rec in (command, watch, elsewhere):
            await settled(rec)
        return command, watch, right_after

    command, watch, right_after = run_async(body())
    assert right_after == ("cancelling", "cancelling", "running")
    assert command["status"] == "cancelled" and watch["status"] == "cancelled"


def test_jobs_are_scoped_to_their_owner_and_thread(fresh_db):
    owner, other = quick_user("owner@core.test"), quick_user("other@core.test")
    _, tid = new_thread(owner)
    _, other_tid = new_thread(owner)

    async def body():
        runner = ScriptedRunner()
        rec = await launch(owner, runner, thread_id=tid)
        results = [
            await command_jobs.join(rec["id"], other, 0),
            await command_jobs.join(rec["id"], owner, 0, thread_id=other_tid),
            command_jobs.kill(rec["id"], other),
            command_jobs.kill(rec["id"], owner, thread_id=other_tid),
        ]
        still = rec["status"]
        runner.release()
        await settled(rec)
        return rec["id"], results, still

    job_id, results, still = run_async(body())

    assert all(trace["status"] == "error" and trace["error"] == "not found" for _, trace in results)
    assert still == "running"
    assert command_jobs.reconcile_jobs(other, tid, [job_id]) == {"jobs": {}, "missing": [job_id]}


def test_cancelling_a_wait_never_cancels_the_job(fresh_db):
    uid = quick_user()

    async def body():
        runner = ScriptedRunner()
        rec = await launch(uid, runner)
        waiter = asyncio.create_task(command_jobs.join(rec["id"], uid, 30))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert rec["status"] == "running"
        runner.release(code=0)
        await settled(rec)
        return rec

    rec = run_async(body())
    assert rec["status"] == "completed"


def test_a_restart_marks_unfinished_jobs_interrupted_and_reconcile_finds_them(fresh_db):
    uid = quick_user()
    _, tid = new_thread(uid)

    # Rows a dead process left behind: its executors died with it.
    first, second = "deadjob00001", "deadjob00002"
    for job_id, status in ((first, "running"), (second, "cancelling")):
        db.execute(
            "INSERT INTO command_jobs (id,user_id,host_id,host,thread_id,command,cwd,background,timeout_ms,status,"
            "`out`,created_at,started_at) VALUES (?,?,1,'box',?,'make',?,1,60000,?,'half',?,?)",
            (job_id, uid, tid, "/srv", status, db.now(), db.now()),
        )
    command_jobs.recover()

    for job_id in (first, second):
        stored = row(job_id)
        assert (stored["status"], stored["error"], stored["output_incomplete"]) == ("interrupted", "service restarted", 1)
    found = command_jobs.reconcile_jobs(uid, tid, [first, second, "nope"])
    assert set(found["jobs"]) == {first, second} and found["missing"] == ["nope"]
    assert found["jobs"][first]["state"] == "interrupted"
    assert command_jobs.list_thread_jobs(uid, tid) == []


def test_admission_refuses_past_the_running_cap_without_a_row(fresh_db, monkeypatch):
    uid = quick_user()
    monkeypatch.setattr(command_jobs, "JOB_CAP", 2)

    async def body():
        runners = [ScriptedRunner(), ScriptedRunner()]
        recs = [await launch(uid, r) for r in runners]
        with pytest.raises(ValueError, match="too many running commands"):
            await launch(uid, ScriptedRunner())
        for r in runners:
            r.release()
        for rec in recs:
            await settled(rec)

    run_async(body())
    assert db.fetchone("SELECT COUNT(*) AS n FROM command_jobs")["n"] == 2


def test_one_user_cannot_take_every_running_slot(fresh_db, monkeypatch):
    uid, other = quick_user(), quick_user("other@core.test")
    monkeypatch.setattr(command_jobs, "JOB_USER_CAP", 2)

    async def body():
        runners = [ScriptedRunner() for _ in range(3)]
        recs = [await launch(uid, runners[0]), await launch(uid, runners[1])]
        with pytest.raises(ValueError, match="too many running commands for this user"):
            await launch(uid, ScriptedRunner())
        recs.append(await launch(other, runners[2]))
        for r in runners:
            r.release()
        for rec in recs:
            await settled(rec)

    run_async(body())
    assert db.fetchone("SELECT COUNT(*) AS n FROM command_jobs WHERE user_id=?", (uid,))["n"] == 2
    assert db.fetchone("SELECT COUNT(*) AS n FROM command_jobs WHERE user_id=?", (other,))["n"] == 1


def test_one_user_cannot_take_every_websocket_monitor(fresh_db, monkeypatch):
    uid, other = quick_user(), quick_user("other@core.test")
    monkeypatch.setattr(command_jobs, "WS_MONITOR_USER_CAP", 1)
    ws = {"kind": "monitor", "command": "", "url": "wss://feed.example/live"}

    async def body():
        runners = [ScriptedRunner() for _ in range(3)]
        recs = [await launch(uid, runners[0], **ws)]
        with pytest.raises(ValueError, match="too many running websocket monitors for this user"):
            await launch(uid, ScriptedRunner(), **ws)
        recs.append(await launch(uid, runners[1]))  # plain commands still run
        recs.append(await launch(other, runners[2], **ws))
        for r in runners:
            r.release()
        for rec in recs:
            await settled(rec)

    run_async(body())


def test_a_receipt_never_acknowledges_another_users_job(fresh_db):
    uid, other = quick_user(), quick_user("other@core.test")
    _, tid = new_thread(uid)
    _, their_tid = new_thread(other)
    for job_id, owner, thread, kind in (("myjob0000001", uid, tid, "command"), ("theirjob0001", other, their_tid, "command"),
                                        ("theirmon0001", other, their_tid, "monitor")):
        db.execute(
            "INSERT INTO command_jobs (id,user_id,host_id,host,thread_id,command,cwd,background,timeout_ms,status,"
            "created_at,kind,notify) VALUES (?,?,1,'box',?,'make','',1,60000,'completed',?,?,1)",
            (job_id, owner, thread, db.now(), kind),
        )

    command_jobs.mark_delivered(["myjob0000001", "theirjob0001", "theirmon0001:e999999"], user_id=uid, thread_id=tid)

    assert row("myjob0000001")["delivered"] == 1
    assert row("theirjob0001")["delivered"] == 0
    assert row("theirmon0001")["delivered_event_seq"] == 0


def test_stopping_a_run_cancels_its_own_shell_jobs_only(fresh_db):
    uid = quick_user()

    async def body():
        mine, theirs = ScriptedRunner(), ScriptedRunner()
        a = await launch(uid, mine, run_id="run-a")
        b = await launch(uid, theirs, run_id="run-b")
        command_jobs.cancel_run("run-a")
        await settled(a)
        alive = b["status"]
        theirs.release()
        await settled(b)
        return a, alive

    a, alive = run_async(body())
    assert a["status"] == "cancelled" and alive == "running"


def test_list_thread_jobs_shows_running_background_jobs(fresh_db):
    uid = quick_user()
    _, tid = new_thread(uid)

    async def body():
        bg, fg = ScriptedRunner(), ScriptedRunner()
        a = await launch(uid, bg, thread_id=tid, description="Dev server")
        b = await launch(uid, fg, thread_id=tid, background=False)
        listed = command_jobs.list_thread_jobs(uid, tid)
        bg.release()
        fg.release()
        await settled(a)
        await settled(b)
        return a["id"], listed

    job_id, listed = run_async(body())
    assert [(j["id"], j["description"], j["status"]) for j in listed] == [(job_id, "Dev server", "running")]
    assert command_jobs.list_thread_jobs(uid, tid) == []


def test_waiting_ends_early_when_something_else_arrives_in_the_run(fresh_db):
    uid = quick_user()

    async def body():
        runner = ScriptedRunner()
        rec = await launch(uid, runner)
        waiter = asyncio.create_task(command_jobs.join(rec["id"], uid, 30, run_id="run-z"))
        # Keep signalling: an arrival sent before the wait listens is not queued.
        await wait_until_async(lambda: (run_inbox.arrived("run-z", "steer"), waiter.done())[1])
        text, trace = await waiter
        runner.release()
        await settled(rec)
        return trace

    trace = run_async(body())
    assert trace["state"] == "running" and trace["early_return"] is True
