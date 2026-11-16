"""Steers and the run inbox: a message sent while the run works reaches the
model once at the next tool boundary, is acknowledged in the same save, and
is never stranded by a race with the end of the turn."""
import asyncio
import json

import pytest

from app import chat_run_state, chat_store, chats, db, run_inbox
from app.chats import ChatValidationError
from tests.core.helpers import fake_provider, quick_user, run_async, run_turn, text_frames, tool_call_frames

from ._support import fake_tools, run_row, settle, start_run, tool_thread


def _pending_steers(uid, run_id):
    return chat_store.get_run(uid, run_id)["request"].get("agent", {}).get("pending_steers") or []


def _count(body, text):
    return sum(str(m.get("content")).count(text) for m in body["messages"])


def test_steer_during_a_tool_reaches_the_model_once_and_is_acknowledged(fresh_db):
    uid = quick_user()

    async def body():
        tools.hold()
        thread, run, task = await start_run(uid, "research", thread=tool_thread(uid))
        await asyncio.wait_for(tools.started.wait(), 5)
        await asyncio.to_thread(chat_store.steer_turn, uid, thread["id"], "also check pricing", "steer-1")
        # A retried POST of the same steer changes nothing.
        again = await asyncio.to_thread(chat_store.steer_turn, uid, thread["id"], "also check pricing", "steer-1")
        assert again["idempotent"] is True
        tools.gate.set()
        await asyncio.wait_for(task, 5)
        return thread, run

    with fake_tools() as tools, fake_provider(
        tool_call_frames([("c1", "web_search", {"query": "x"})]), text_frames("pricing checked"),
    ) as provider:
        thread, run = run_async(body())

    assert run_row(run["id"])["status"] == "completed"
    assert _count(provider.bodies[0], "also check pricing") == 0
    assert _count(provider.bodies[1], "also check pricing") == 1
    assert _pending_steers(uid, run["id"]) == []
    messages = chats.get_thread(uid, thread["id"])["messages"]
    users = [m["content"] for m in messages if m["role"] == "user"]
    assert users == ["research", "also check pricing"]
    assert messages[-1]["content"] == "pricing checked"


def test_steer_accepted_during_the_final_text_reply_gets_its_own_round(fresh_db):
    uid = quick_user()
    sent = {}

    def first_reply(_body):
        # The user's message is accepted while the last reply streams.
        sent["steer"] = chat_store.steer_turn(uid, sent["thread"], "one more thing", "late-steer")
        return text_frames("first reply")

    async def body():
        thread, run, task = await start_run(uid, "hello")
        sent["thread"] = thread["id"]
        await asyncio.wait_for(task, 5)
        await settle()
        return thread, run

    with fake_provider(first_reply, text_frames("about that one more thing")) as provider:
        thread, run = run_async(body())

    assert len(provider.bodies) == 2
    assert _count(provider.bodies[1], "one more thing") == 1
    assert run_row(run["id"])["status"] == "completed"
    messages = chats.get_thread(uid, thread["id"])["messages"]
    assert messages[-1]["content"] == "about that one more thing"


def test_a_steer_racing_the_terminal_write_requeues_the_run(fresh_db):
    uid = quick_user()
    thread = chats.create_thread(uid, model="offline", tools=[])
    run = chat_store.start_turn(uid, thread["id"], "work", thread["revision"], None)["run"]
    chat_store.steer_turn(uid, thread["id"], "wait, also this", "steer-race")

    # The provider failed right after the steer was accepted.
    chat_store.finish_run(run["id"], {"content": "", "error": "upstream error"}, "failed")

    assert run_row(run["id"])["status"] == "queued", "an accepted steer owns a continuation"
    agent = chat_store.get_run(uid, run["id"])["request"]["agent"]
    assert agent["followup_continuation"] is True and agent["steer_continuations"] == 1


def test_steer_continuations_are_bounded_and_the_user_is_told(fresh_db):
    uid = quick_user()
    thread = chats.create_thread(uid, model="offline", tools=[])
    run = chat_store.start_turn(uid, thread["id"], "work", thread["revision"], None)["run"]
    chat_store.steer_turn(uid, thread["id"], "again", "steer-x")
    req = json.loads(db.fetchone("SELECT request FROM chat_runs WHERE id=?", (run["id"],))["request"])
    req["agent"]["steer_continuations"] = 3
    db.execute("UPDATE chat_runs SET request=? WHERE id=?", (json.dumps(req), run["id"]))

    chat_store.finish_run(run["id"], {"content": "done"}, "completed")

    assert run_row(run["id"])["status"] == "completed"
    assert "not delivered this turn" in chats.get_thread(uid, thread["id"])["messages"][-1]["notice"]


def test_steer_needs_a_live_run_that_is_not_stopping(fresh_db):
    uid = quick_user()
    with fake_provider(text_frames("done")):
        thread_id, run = run_async(run_turn(uid, "hi"))
    with pytest.raises(ChatValidationError) as idle:
        chat_store.steer_turn(uid, thread_id, "late", "steer-idle")
    assert idle.value.status_code == 409

    thread = chats.create_thread(uid, model="offline", tools=[])
    live = chat_store.start_turn(uid, thread["id"], "work", thread["revision"], None)["run"]
    chat_store.request_cancel(uid, live["id"])
    with pytest.raises(ChatValidationError) as stopping:
        chat_store.steer_turn(uid, thread["id"], "late", "steer-stopping")
    assert stopping.value.status_code == 409
    assert _pending_steers(uid, live["id"]) == []


def test_pending_steers_are_capped_not_silently_dropped(fresh_db):
    uid = quick_user()
    thread = chats.create_thread(uid, model="offline", tools=[])
    run = chat_store.start_turn(uid, thread["id"], "work", thread["revision"], None)["run"]
    for i in range(8):
        chat_store.steer_turn(uid, thread["id"], f"note {i}", f"s{i}")

    with pytest.raises(ChatValidationError) as full:
        chat_store.steer_turn(uid, thread["id"], "note 8", "s8")

    assert full.value.status_code == 429
    assert [item["id"] for item in _pending_steers(uid, run["id"])] == [f"s{i}" for i in range(8)]


def test_an_arrival_cuts_a_blocked_wait_short_only_for_its_own_run(fresh_db):
    async def body():
        work = asyncio.get_running_loop().create_future()
        other = asyncio.create_task(run_inbox.wait_or_arrival(work, 5, "run-b"))
        mine = asyncio.create_task(run_inbox.wait_or_arrival(work, 5, "run-a", ignore=("job-1",)))
        await asyncio.sleep(0)
        run_inbox.arrived("run-a", "job-1")  # the thing it already waits on
        run_inbox.arrived("run-a", "steer:s1")
        finished, arrival = await asyncio.wait_for(mine, 5)
        assert not other.done()
        work.set_result("done")  # the awaited work is left running, never cancelled
        return finished, arrival, await asyncio.wait_for(other, 5)

    finished, arrival, other = run_async(body())
    assert (finished, arrival) == (False, "steer:s1")
    assert other == (True, "")


def test_steer_acceptance_signals_the_live_run(fresh_db):
    uid = quick_user()
    thread = chats.create_thread(uid, model="offline", tools=[])
    run = chat_store.start_turn(uid, thread["id"], "work", thread["revision"], None)["run"]

    async def body():
        with run_inbox.listen(run["id"]) as arrival:
            await asyncio.to_thread(chat_run_state.steer_turn, uid, thread["id"], "hey", "steer-sig")
            return await asyncio.wait_for(arrival, 5)

    assert run_async(body()) == "steer:steer-sig"
