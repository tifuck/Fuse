"""Run-store guards in a disposable MariaDB schema: a disowned worker leg's
saves, the reaper's resume cap, the command ledger's id width, queued-turn
starts, progress after a terminal event and the composer queue cap."""
import asyncio
import json
import os
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app import chat_pub, chat_queue, chat_runs as runs, chat_store as store, chats, db
from app.async_work import durable_call
from tests.mariadb_fixture import isolated_database

pytestmark = pytest.mark.skipif(os.environ.get("FUSE_CHAT_TEST_MODE") != "1", reason="requires disposable MariaDB fixture")

CALL = {"role": "assistant", "content": "", "tool_calls": [
    {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}]}


def _user(uid=1):
    db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,'unused',?)",
               (uid, f"guards{uid}@test.invalid", db.now()))
    chats.set_user_context(uid, instructions="", profile="")


def _turn(uid=1, command=None):
    thread = chats.create_thread(uid, model="offline", tools=[])
    return store.start_turn(uid, thread["id"], "Hello", thread["revision"], command)["run"]


def _transcript(run_id):
    return json.loads(db.fetchone("SELECT provider_messages FROM chat_runs WHERE id=?", (run_id,))["provider_messages"])


@pytest.fixture(autouse=True)
def _fresh():
    store.invalidate_committed_cursors()
    runs._stopping = False
    yield
    store.invalidate_committed_cursors()


def test_disowned_leg_cannot_overwrite_the_resumed_transcript():
    with isolated_database():
        _user()
        run = _turn()
        rid = run["id"]
        store.save_turn_progress(rid, [{"role": "user", "content": "Hello"}, CALL])

        async def scenario():
            go = asyncio.Event()

            async def old_leg():
                runs._leg.set((rid, asyncio.current_task()))
                await go.wait()
                # Its tool finished after the Stop: the round save and a
                # flush were already on their way.
                await durable_call(store.save_turn_progress, rid, [
                    {"role": "user", "content": "Hello"}, CALL,
                    {"role": "tool", "tool_call_id": "c1", "content": "late hits"},
                ])
                await asyncio.to_thread(store.checkpoint, rid, {"content": "stale leg"})

            leg = asyncio.create_task(old_leg())
            runs._tasks[rid] = leg
            try:
                await asyncio.sleep(0)
                await asyncio.to_thread(store.abort_active, rid, "interrupted")
                await asyncio.to_thread(store.resume_run, 1, rid)
                go.set()
                await leg
            finally:
                runs._tasks.pop(rid, None)

        asyncio.run(scenario())
        resumed = store.get_run(1, rid)
        assert resumed["status"] == "queued"
        assert resumed["message"].get("content") != "stale leg"
        transcript = _transcript(rid)
        assert transcript[-1]["role"] == "user"
        assert f"resume-note:{rid}:1" in transcript[-1]["content"]
        sealed = [m for m in transcript if m.get("role") == "tool" and m.get("tool_call_id") == "c1"]
        assert len(sealed) == 1 and sealed[0]["content"] != "late hits"


def test_reaper_resumes_the_boot_overflow_within_the_batch_cap():
    with isolated_database():
        ids = []
        for uid in range(1, 11):
            _user(uid)
            run = _turn(uid)
            store.save_turn_progress(run["id"], [{"role": "user", "content": "Hello"}, CALL,
                                                 {"role": "tool", "tool_call_id": "c1", "content": "hits"}])
            # Crashed mid-turn: its worker had started the run.
            db.execute("UPDATE chat_runs SET status='running' WHERE id=?", (run["id"],))
            ids.append((uid, run["id"]))

        def resumed():
            return [rid for uid, rid in ids if (store.get_run(uid, rid)["request"].get("agent") or {}).get("resumed")]

        store.recover_runs()
        assert len(resumed()) == store.BOOT_RESUME_BATCH
        runs.reap_orphans()
        runs.reap_orphans(ids[-1][0])
        assert len(resumed()) == store.BOOT_RESUME_BATCH
        # Once the batch has run, the reaper drains the rest.
        db.execute("UPDATE chat_runs SET status='completed' WHERE status='queued'")
        runs.reap_orphans()
        assert len(resumed()) == len(ids)


def test_command_id_must_fit_the_ledger_column():
    with isolated_database():
        _user()
        thread = chats.create_thread(1, model="offline", tools=[])
        with pytest.raises(chats.ChatValidationError):
            store.start_turn(1, thread["id"], "Hello", thread["revision"], "x" * 129)
        assert store.start_turn(1, thread["id"], "Hello", thread["revision"], "x" * 128)["run"]
        assert db.fetchone("SELECT command_id FROM chat_commands")["command_id"] == "x" * 128


def test_web_command_id_must_fit_the_ledger_column():
    from app.web.chat import _chat_command

    assert _chat_command({"command_id": "x" * 128}) == "x" * 128
    with pytest.raises(HTTPException) as raised:
        _chat_command({"command_id": "x" * 129})
    assert raised.value.status_code == 422


def test_start_queued_keeps_the_item_on_capacity_and_holds_it_on_refusal():
    with isolated_database():
        _user()
        thread = chats.create_thread(1, model="offline", tools=[])
        pk = chats.resolve_thread_pk(1, thread["id"])
        chat_queue.put(1, pk, {"id": "a", "text": "Next", "created": 1})
        for status in (409, 429):
            with patch.object(store, "start_turn", side_effect=chats.ChatValidationError("busy", status)):
                assert asyncio.run(runs.start_queued(1, pk)) is None
            assert chat_queue.next_item(1, pk)["id"] == "a"
        with patch.object(store, "start_turn", side_effect=chats.ChatValidationError("too large", 413)):
            with pytest.raises(chats.ChatValidationError):
                asyncio.run(runs.start_queued(1, pk))
        assert chat_queue.next_item(1, pk) is None
        assert db.fetchone("SELECT held FROM chat_queue WHERE id='a'")["held"] == 1


def test_no_progress_lands_after_the_terminal_event():
    with isolated_database():
        _user()
        run = _turn()
        rid, thread_id = run["id"], run["thread_id"]
        store.finish_run(rid, {"content": "Done"}, "completed")
        store.checkpoint(rid, {"content": "stale"}, events=[])
        chat_pub.publish(1, "run.progress", thread_id, {"run_id": rid}, guard_run=rid)
        assert chat_pub._commit_publications([(1, "run.progress", thread_id, {"run_id": rid}, rid, None)]) == [None]
        types = [event["type"] for event in store.events_after(1, 0)]
        assert "run.completed" in types
        assert "run.progress" not in types[types.index("run.completed"):]
        assert store.get_run(1, rid)["message"]["content"] == "Done"


def test_queue_refuses_a_new_item_past_the_cap_but_updates_existing_ones():
    with isolated_database():
        _user()
        thread = chats.create_thread(1, model="offline", tools=[])
        pk = chats.resolve_thread_pk(1, thread["id"])
        for index in range(chat_queue.PER_THREAD):
            chat_queue.put(1, pk, {"id": f"q{index}", "text": "queued", "created": index})
        with pytest.raises(chats.ChatValidationError) as raised:
            chat_queue.put(1, pk, {"id": "overflow", "text": "one more", "created": 99})
        assert raised.value.status_code == 413
        assert chat_queue.put(1, pk, {"id": "q0", "text": "edited", "created": 0}) is False
        assert chat_queue.next_item(1, pk)["text"] == "edited"
