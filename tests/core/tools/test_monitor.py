"""Monitor: stdout lines become batched events; the watch stops itself.

LineBatcher is driven directly (close() flushes what is pending, so no batch
timer is waited on). Monitor jobs run through command_jobs with a runner the
test feeds, the batch window patched to zero and, where the firehose window
matters, the monitor clock frozen.
"""
import asyncio
import json
import os
from types import SimpleNamespace

import pytest

from app import chat_runs, chat_store, command_jobs, db, monitor, workspace
from tests.core.helpers import make_user, run_async, wait_until_async
from tests.core.tools._support import launch, new_run, new_thread


def batches_of(text_chunks, pattern=""):
    """Feed chunks into a LineBatcher and close it; returns the emitted batches."""
    out = []

    async def body():
        rec = {"_pattern": monitor.compile_pattern(pattern)}
        batcher = monitor.LineBatcher(rec, lambda lines, overflow=0: out.append(list(lines)))
        for chunk in text_chunks:
            batcher.feed(chunk)
        await batcher.close()

    asyncio.run(body())
    return out


def test_lines_are_joined_across_chunks_and_blank_lines_are_not_events():
    assert batches_of(["par", "tial\r\n\n   \nsecond", " line"]) == [["partial", "second line"]]


def test_the_pattern_selects_lines_and_an_invalid_pattern_is_refused():
    assert batches_of(["ok 1\nERROR disk\nok 2\nERROR net\n"], pattern=r"ERROR \w+") == [["ERROR disk", "ERROR net"]]
    with pytest.raises(ValueError, match="invalid monitor pattern"):
        monitor.compile_pattern("(unclosed")


def test_a_batch_keeps_bounded_lines_and_counts_what_it_drops():
    huge = "x" * (monitor.MAX_LINE_CHARS + 500)
    [batch] = batches_of([huge + "\n" + "".join(f"l{i}\n" for i in range(60))])

    assert batch[0] == "x" * monitor.MAX_LINE_CHARS + "…"
    assert len(batch) == monitor.MAX_BATCH_LINES + 1
    assert batch[-1] == f"… {61 - monitor.MAX_BATCH_LINES} more lines in this batch omitted"


def test_a_catastrophic_pattern_cannot_stall_the_loop():
    lines = batches_of(["a" * 40 + "!\n"], pattern=r"(a+)+$")
    # The match timed out: no event, and feed() returned instead of hanging.
    assert lines == []


# -- monitor jobs ---------------------------------------------------------

class FeedRunner:
    """A watched process the test writes to through sink / sink.err."""

    def __init__(self):
        self.sink = None
        self.started = asyncio.Event()
        self.released = asyncio.Event()
        self.cancelled = False

    async def __call__(self, sink):
        self.sink = sink
        self.started.set()
        try:
            await self.released.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return SimpleNamespace(exit_status=0, output="")


@pytest.fixture
def fast_batches(monkeypatch):
    monkeypatch.setattr(monitor, "BATCH_S", 0)


@pytest.fixture
def wakes(monkeypatch):
    calls = []
    monkeypatch.setattr(chat_runs, "schedule_wake", lambda uid, tid: calls.append((uid, tid)))
    return calls


async def start_monitor(uid, *, thread_id=None, run_id=None, pattern="", max_events=10):
    runner = FeedRunner()
    rec = await launch(uid, runner, thread_id=thread_id, run_id=run_id, kind="monitor", pattern=pattern,
                       max_events=max_events, description="Watch the log", command="tail -f app.log")
    await runner.started.wait()
    return rec, runner


def test_events_wait_for_an_idle_thread_and_reading_them_does_not_consume_them(fresh_db, fast_batches, wakes):
    uid = make_user()
    _, tid = new_thread(uid)

    async def body():
        rec, runner = await start_monitor(uid, thread_id=tid, pattern="ERROR")
        runner.sink("boot ok\nERROR one\n")
        runner.sink.err("warning: noisy stderr\n")
        await wait_until_async(lambda: rec["pending_events"])
        first = command_jobs.pending_envelopes(uid, tid)
        again = command_jobs.pending_envelopes(uid, tid)
        command_jobs.mark_delivered([first[0]["id"]])
        after = command_jobs.pending_envelopes(uid, tid)
        runner.released.set()
        await asyncio.wait_for(asyncio.shield(rec["future"]), 5)
        return rec, first, again, after

    rec, first, again, after = run_async(body())

    assert [(e["id"], e["envelope"]) for e in first] == [(e["id"], e["envelope"]) for e in again]
    assert len(first) == 1
    assert first[0]["id"] == f"{rec['id']}:e1"
    assert "ERROR one" in first[0]["envelope"] and "boot ok" not in first[0]["envelope"]
    assert "noisy stderr" not in first[0]["envelope"]
    assert after == []
    assert wakes and wakes[0] == (uid, tid)
    stored = db.fetchone("SELECT * FROM command_jobs WHERE id=?", (rec["id"],))
    # An event receipt acknowledges that event, not the monitor's final result.
    assert stored["delivered_event_seq"] == 1 and stored["delivered"] == 0
    assert stored["kind"] == "monitor" and stored["event_count"] == 1
    result = command_jobs.notification(rec)
    assert result.startswith(f'<monitor_result id="{rec["id"]}" state="completed">')
    assert "[stderr]" in result and "noisy stderr" in result


def test_an_event_for_a_live_run_goes_into_that_run(fresh_db, fast_batches, wakes):
    uid = make_user()
    _, tid, run_id = new_run(uid)

    async def body():
        rec, runner = await start_monitor(uid, thread_id=tid, run_id=run_id)
        runner.sink("deploy finished\n")
        await wait_until_async(lambda: chat_store.take_child_results(run_id))
        runner.released.set()
        await asyncio.wait_for(asyncio.shield(rec["future"]), 5)
        return rec

    rec = run_async(body())
    events = [e for e in chat_store.take_child_results(run_id) if "<monitor_event" in e]
    assert len(events) == 1 and "deploy finished" in events[0]
    assert rec["pending_events"] == []  # never both queued and waiting for a wake


def test_max_events_ends_the_watch_as_a_normal_stop(fresh_db, fast_batches, wakes):
    uid = make_user()
    _, tid = new_thread(uid)

    async def body():
        rec, runner = await start_monitor(uid, thread_id=tid, max_events=2)
        runner.sink("one\ntwo\nthree\n")
        await asyncio.wait_for(asyncio.shield(rec["future"]), 5)
        return rec, runner

    rec, runner = run_async(body())

    assert rec["status"] == "completed" and runner.cancelled
    assert rec["event_count"] == 2
    events = [item["envelope"] for item in rec["pending_events"]]
    assert len(events) == 1 and "one\ntwo" in events[0]
    assert "three" not in events[0]  # nothing past the cap is delivered
    assert "ended: max_events reached (normal stop)" in command_jobs.notification(rec)


def test_a_firehose_is_stopped(fresh_db, fast_batches, wakes, monkeypatch):
    uid = make_user()
    _, tid = new_thread(uid)
    monkeypatch.setattr(monitor, "time", SimpleNamespace(monotonic=lambda: 1000.0))

    async def body():
        rec, runner = await start_monitor(uid, thread_id=tid, max_events=50)
        for i in range(monitor.FIREHOSE_MAX):
            runner.sink(f"tick {i}\n")
            await wait_until_async(lambda: rec["event_count"] > i or rec["status"] != "running")
        await asyncio.wait_for(asyncio.shield(rec["future"]), 5)
        return rec

    rec = run_async(body())
    assert rec["status"] == "cancelled"
    assert rec["error"] == "monitor auto-stopped: too many events"
    assert rec["event_count"] == monitor.FIREHOSE_MAX


def test_a_monitor_deadline_reports_how_many_events_it_saw(fresh_db, fast_batches, wakes):
    uid = make_user()

    async def body():
        runner = FeedRunner()
        rec = await launch(uid, runner, kind="monitor", max_events=5, description="d", command="tail -f x",
                           timeout_ms=60_000)
        await runner.started.wait()
        runner.sink("a\n")
        await wait_until_async(lambda: rec["event_count"] == 1)
        rec["_deadline"].reschedule(asyncio.get_running_loop().time())
        await asyncio.wait_for(asyncio.shield(rec["future"]), 5)
        return rec

    rec = run_async(body())
    assert rec["status"] == "timed_out"
    assert rec["error"].endswith("; 1 events")


def test_stopping_the_run_that_started_a_monitor_leaves_it_running(fresh_db, fast_batches, wakes):
    uid = make_user()

    async def body():
        rec, runner = await start_monitor(uid, run_id="run-m")
        command_jobs.cancel_run("run-m")
        await asyncio.sleep(0)
        alive = rec["status"]
        command_jobs.kill(rec["id"], uid)
        await asyncio.wait_for(asyncio.shield(rec["future"]), 5)
        return alive, rec

    alive, rec = run_async(body())
    assert alive == "running"
    assert rec["status"] == "cancelled"


# -- the monitor tool --------------------------------------------------------

def test_the_monitor_tool_turns_command_output_into_events(ssh_host, fast_batches, wakes):
    from tests.core.tools._support import new_thread as thread_for

    _, tid = thread_for(ssh_host.uid)

    async def body():
        text, rec = await workspace.execute(
            "monitor", json.dumps({"command": "printf 'ok\\nFAIL one\\nok\\n'; echo oops >&2",
                                   "pattern": "FAIL", "max_events": 5, "description": "Watch for failures"}),
            user_id=ssh_host.uid, bound_host_id=ssh_host.host_id, bound_cwd=ssh_host.root, thread_id=tid)
        live = command_jobs._live[rec["id"]]
        await asyncio.wait_for(asyncio.shield(live["future"]), 5)
        return rec, live

    rec, live = run_async(body())

    assert rec["name"] == "monitor" and rec["status"] == "running"
    assert live["status"] == "completed" and live["exit_code"] == 0
    assert live["event_count"] == 1
    assert "oops" in live["err"]
    events = [e for e in command_jobs.pending_envelopes(ssh_host.uid, tid)]
    assert any("FAIL one" in e["envelope"] for e in events)


def test_the_monitor_tool_validates_its_arguments(ssh_host):
    async def call(**args):
        return await workspace.execute("monitor", json.dumps(args), user_id=ssh_host.uid,
                                       bound_host_id=ssh_host.host_id, bound_cwd=ssh_host.root)

    cases = [
        ({"command": "tail -f x", "url": "wss://example.com/", "description": "d"}, "not both"),
        ({"description": "d"}, "command or url is required"),
        ({"command": "tail -f x"}, "description is required"),
        ({"url": "ws://127.0.0.1/feed", "description": "d"}, "public ws"),
        ({"url": "ws://10.0.0.5/feed", "description": "d"}, "public ws"),
        ({"command": "tail -f x", "pattern": "(", "description": "d"}, "invalid monitor pattern"),
    ]
    for args, why in cases:
        text, rec = run_async(call(**args))
        assert rec["status"] == "error" and why in text, args
    assert not db.fetchone("SELECT 1 FROM command_jobs")
    assert not os.listdir(ssh_host.home)
