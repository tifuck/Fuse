"""Run-row persistence: checkpoints never resurrect a finished run or drop a
concurrent write; transcript saves keep one copy of the history and send
only what changed (MariaDB rewrites a changed LONGTEXT whole)."""
import json
import re
from unittest.mock import patch

from app import chat_checkpoint, chat_recovery, chat_store, chats, db, mysql_compat
from tests.core.helpers import quick_user

from ._support import provider_messages, run_row


class Statements:
    """The SQL the app sends to MariaDB, to assert which run columns move."""

    def __enter__(self):
        self.sql = []
        real = mysql_compat.Conn.native

        def native(conn, sql, args=()):
            self.sql.append(sql)
            return real(conn, sql, args)

        self._patch = patch.object(mysql_compat.Conn, "native", native)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()

    def run_columns(self):
        """Columns set by each UPDATE of chat_runs."""
        out = []
        for sql in self.sql:
            if sql.startswith("UPDATE `chat_runs`"):
                assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
                out.append(set(re.findall(r"`(\w+)` = ", assignments)))
        return out


def _turn(uid, text="inspect"):
    thread = chats.create_thread(uid, model="offline", tools=[])
    return chat_store.start_turn(uid, thread["id"], text, thread["revision"], None)["run"]


def _stored_request(run_id):
    return json.loads(run_row(run_id, "request")["request"])


def _rows(seed, *extra):
    return [*seed, {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "hits"}, *extra]


def test_progress_saves_keep_one_history_copy_and_send_only_changes(fresh_db):
    uid = quick_user()
    run = _turn(uid)
    seed = run["request"]["messages"]
    assert seed and "messages" in _stored_request(run["id"])
    rows = _rows(seed)

    chat_checkpoint.save_turn_progress(run["id"], rows)
    # provider_messages now holds the leg: the request drops its copy.
    assert "messages" not in _stored_request(run["id"])
    assert provider_messages(run["id"]) == rows

    with Statements() as repeat:
        chat_checkpoint.save_turn_progress(run["id"], rows)
    assert repeat.run_columns() == [{"updated_at"}], "an unchanged save rewrites nothing"

    longer = [*rows, {"role": "assistant", "content": "done"}]
    with Statements() as extend:
        chat_checkpoint.save_turn_progress(run["id"], longer)
    assert extend.run_columns() == [{"provider_tail", "updated_at"}], "an extension writes only the new rows"
    assert provider_messages(run["id"]) == longer


def test_a_rewritten_transcript_replaces_the_base_and_reads_back_whole(fresh_db):
    uid = quick_user()
    run = _turn(uid)
    rows = _rows(run["request"]["messages"])
    chat_checkpoint.save_turn_progress(run["id"], rows)
    chat_checkpoint.save_turn_progress(run["id"], [*rows, {"role": "assistant", "content": "a"}])

    compacted = [{"role": "system", "content": "sys"}, {"role": "user", "content": "summary of earlier work"}]
    chat_checkpoint.save_turn_progress(run["id"], compacted)

    assert provider_messages(run["id"]) == compacted
    assert run_row(run["id"], "provider_tail")["provider_tail"] is None


def test_an_empty_transcript_keeps_the_request_history(fresh_db):
    uid = quick_user()
    run = _turn(uid)
    chat_checkpoint.save_turn_progress(run["id"], [])
    assert _stored_request(run["id"])["messages"] == run["request"]["messages"]


def test_a_requeue_rebuilds_the_request_history_from_the_transcript(fresh_db):
    uid = quick_user()
    run = _turn(uid)
    rows = _rows(run["request"]["messages"])
    chat_checkpoint.save_turn_progress(run["id"], rows)
    db.execute("UPDATE chat_runs SET status='running' WHERE id=?", (run["id"],))

    chat_recovery.recover_runs()

    request = _stored_request(run["id"])
    assert request["messages"] == provider_messages(run["id"])
    assert [m["role"] for m in request["messages"][:len(rows)]] == [m["role"] for m in rows]


def test_writes_after_the_terminal_commit_change_nothing(fresh_db):
    uid = quick_user()
    run = _turn(uid)
    chat_checkpoint.save_turn_progress(run["id"], _rows(run["request"]["messages"]), loaded_tools=["grep"])
    chat_store.finish_run(run["id"], {"content": "final answer"}, "completed")
    before = dict(run_row(run["id"], "status,message,request,provider_messages"))

    # A slow flush and a late round save from the worker arrive afterwards.
    chat_checkpoint.checkpoint(run["id"], {"content": "late stale write"}, [])
    chat_checkpoint.save_turn_progress(run["id"], [{"role": "user", "content": "late"}])

    assert dict(run_row(run["id"], "status,message,request,provider_messages")) == before
    final = chat_store.get_run(uid, run["id"])
    assert final["status"] == "completed"
    assert final["message"]["content"] == "final answer"
    assert final["request"]["agent"]["loaded_tools"] == ["grep"]


def test_a_checkpoint_never_drops_a_steer_written_since_its_last_read(fresh_db):
    uid = quick_user()
    run = _turn(uid)
    chat_checkpoint.checkpoint(run["id"], {"content": "working"}, [])  # caches the request
    thread_id = chat_store.get_run(uid, run["id"])["thread_id"]
    chat_store.steer_turn(uid, thread_id, "also do this", "steer-cp")

    chat_checkpoint.checkpoint(run["id"], {"content": "working harder"}, [{"fuse": {"event": "heartbeat"}}])

    stored = chat_store.get_run(uid, run["id"])
    assert [s["id"] for s in stored["request"]["agent"]["pending_steers"]] == ["steer-cp"]
    assert stored["status"] == "running"


def test_a_steer_committed_between_a_checkpoints_read_and_write_survives(fresh_db):
    """Checkpoints read without row locks; the request digest guard makes a
    write built from the older read skip the state instead of clobbering."""
    from concurrent.futures import ThreadPoolExecutor

    uid = quick_user()
    run = _turn(uid)
    thread_id = chat_store.get_run(uid, run["id"])["thread_id"]
    chat_store.steer_turn(uid, thread_id, "first steer", "steer-a")  # gives the checkpoint a request write
    real = mysql_compat.Conn.native
    raced = []

    def native(conn, sql, args=()):
        if not raced and sql.startswith("UPDATE `chat_runs`") and "`request_digest` = " in sql:
            raced.append(True)
            with ThreadPoolExecutor(1) as other:
                other.submit(chat_store.steer_turn, uid, thread_id, "second steer", "steer-b").result(10)
        return real(conn, sql, args)

    with patch.object(mysql_compat.Conn, "native", native):
        chat_checkpoint.checkpoint(run["id"], {"content": "working"}, [])

    assert raced
    steers = chat_store.get_run(uid, run["id"])["request"]["agent"]["pending_steers"]
    assert [s["id"] for s in steers] == ["steer-a", "steer-b"]


def test_token_flushes_after_a_delivered_steer_are_throttled_and_patch_from_the_last_publish(fresh_db):
    """Only an undelivered steer forces every flush to rewrite the row; after
    delivery the patch base is the projection last published, not a re-read."""
    from app import chat_children, chat_pub

    uid = quick_user()
    run = _turn(uid)
    chat_checkpoint.checkpoint(run["id"], {"content": "working"}, [])
    thread_id = chat_store.get_run(uid, run["id"])["thread_id"]
    chat_store.steer_turn(uid, thread_id, "also do this", "steer-t")
    chat_checkpoint.checkpoint(run["id"], {"content": "working; on it"}, [], durable=False)
    chat_checkpoint.save_turn_progress(run["id"], _rows(run["request"]["messages"],
                                                        {"role": "user", "content": "(steer:steer-t) also do this"}))
    chat_children.seal_steer_display(run["id"], ["steer-t"])
    chat_checkpoint.checkpoint(run["id"], {"content": "working; on it"}, [], durable=False)

    with Statements() as tick, patch.object(chat_pub, "publish", wraps=chat_pub.publish) as published:
        chat_checkpoint.checkpoint(run["id"], {"content": "working; on it. Done"}, [], durable=False)

    assert tick.run_columns() == [], "throttled: no row rewrite"
    assert not [sql for sql in tick.sql if "FROM `chat_runs`" in sql and "`message`" in sql]
    patch_event = published.call_args.args[3]["events"][0]["fuse"]
    assert patch_event["content"] == {"from": 0, "text": ". Done"}


def test_checkpoints_write_only_changed_columns_and_keep_activity_apart(fresh_db):
    uid = quick_user()
    run = _turn(uid)
    chat_checkpoint.checkpoint(run["id"], {"content": "a"}, [])

    with Statements() as tokens:
        chat_checkpoint.checkpoint(run["id"], {"content": "ab"}, [])
    assert all("request" not in cols for cols in tokens.run_columns())

    with Statements() as phase:
        chat_checkpoint.checkpoint(run["id"], {"content": "ab"},
                                   [{"fuse": {"event": "activity", "phase": "tools"}}])
    assert phase.run_columns() == [{"activity", "status", "updated_at"}]
    assert json.loads(run_row(run["id"], "activity")["activity"])["phase"] == "tools"


SAVE_FAILED = "could not save its progress"


def test_a_transcript_save_that_keeps_failing_fails_the_turn_without_rerunning_the_tool(fresh_db):
    from app import chat_runtime
    from tests.core.helpers import fake_provider, run_async, run_turn, tool_call_frames

    from ._support import fake_tools, tool_thread

    uid = quick_user()
    real_save = chat_store.save_turn_progress
    attempts = []

    def save(run_id, *args, **kwargs):
        if tools.calls:
            attempts.append(run_id)
            raise OSError("database went away")
        return real_save(run_id, *args, **kwargs)

    with fake_tools() as tools, fake_provider(tool_call_frames([("c1", "web_search", {"query": "x"})])) as provider, \
            patch.object(chat_runtime, "CHECKPOINT_RETRY_DELAYS", (0.0, 0.0)), \
            patch.object(chat_store, "save_turn_progress", side_effect=save):
        thread_id, run = run_async(run_turn(uid, "go", thread=tool_thread(uid)))

    assert run["status"] == "failed"
    assert SAVE_FAILED in run["message"]["error"]
    assert len(attempts) >= 3, "retried before the turn ends"
    assert len(tools.calls) == 1
    assert len(provider.bodies) == 1


def test_streaming_flushes_that_keep_failing_fail_the_turn(fresh_db):
    """periodic() gives up after three failed flushes and cancels the worker;
    the cancellation is a failure to save, not a Stop or a shutdown."""
    import asyncio

    from app import chat_runs
    from tests.core.helpers import run_async

    from ._support import held_stream, start_run

    uid = quick_user()
    real_checkpoint = chat_store.checkpoint
    calls = []

    def flaky(run_id, message, *args, **kwargs):
        # Inline (durable) saves work; the periodic token flushes fail.
        if kwargs.get("durable", True) is False:
            calls.append(run_id)
            raise OSError("database went away")
        return real_checkpoint(run_id, message, *args, **kwargs)

    async def body():
        provider.arm()
        thread, run, task = await start_run(uid, "write")
        await asyncio.wait_for(provider.sent.wait(), 5)
        await asyncio.wait_for(asyncio.wait({task}), 10)
        return run

    with held_stream("partial text") as provider, patch.object(chat_store, "checkpoint", side_effect=flaky), \
            patch.object(chat_runs, "FLUSH_SECONDS", 0.01):
        run = run_async(body())

    stored = chat_store.get_run(uid, run["id"])
    assert stored["status"] == "failed"
    assert SAVE_FAILED in stored["message"]["error"]
    assert not stored["message"].get("stopped")
    assert len(calls) >= 3
