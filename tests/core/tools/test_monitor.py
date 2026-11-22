"""Monitor: stdout lines become batched events; the watch stops itself.

LineBatcher is driven directly (close() flushes what is pending, so no batch
timer is waited on). Monitor jobs run through command_jobs with a runner the
test feeds, the batch window patched to zero and, where the firehose window
matters, the monitor clock frozen.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app import chat_runs, chat_store, command_jobs, db, monitor, workspace
from tests.core.helpers import quick_user, run_async, wait_until_async
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
        self.exc = None

    async def __call__(self, sink):
        self.sink = sink
        self.started.set()
        try:
            await self.released.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.exc is not None:
            raise self.exc
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
    uid = quick_user()
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
    uid = quick_user()
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
    uid = quick_user()
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
    uid = quick_user()
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
    uid = quick_user()

    async def body():
        runner = FeedRunner()
        rec = await launch(uid, runner, kind="monitor", max_events=5, description="d", command="tail -f x",
                           timeout_ms=60_000)
        await runner.started.wait()
        runner.sink("a\n")
        await wait_until_async(lambda: rec["event_count"] == 1)
        # The runner reports its deadline kill (as connectors and the SSH runner do).
        runner.exc = command_jobs.CommandTimedOut(termination_confirmed=True)
        runner.released.set()
        await asyncio.wait_for(asyncio.shield(rec["future"]), 5)
        return rec

    rec = run_async(body())
    assert rec["status"] == "timed_out"
    assert rec["error"].endswith("; 1 events")


def test_stopping_the_run_that_started_a_monitor_leaves_it_running(fresh_db, fast_batches, wakes):
    uid = quick_user()

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


def test_an_older_event_receipt_never_rewinds_the_acknowledgement(fresh_db):
    uid = quick_user()
    _, tid = new_thread(uid)
    db.execute(
        "INSERT INTO command_jobs (id,user_id,host_id,host,thread_id,command,cwd,background,timeout_ms,status,"
        "created_at,kind,notify) VALUES ('mon000000001',?,1,'box',?,'tail -f x','',1,60000,'running',?,'monitor',1)",
        (uid, tid, db.now()),
    )
    command_jobs.mark_delivered(["mon000000001:e2"])
    command_jobs.mark_delivered(["mon000000001:e1"])

    row = db.fetchone("SELECT delivered_event_seq, delivered FROM command_jobs WHERE id='mon000000001'")
    assert (row["delivered_event_seq"], row["delivered"]) == (2, 0)


def test_websocket_monitors_reach_only_public_web_ports(monkeypatch):
    answers = {"feed.example.com": "93.184.216.34", "inside.example.com": "10.1.2.3",
               "nat64.example.com": "64:ff9b::7f00:1", "compat.example.com": "::a00:1"}

    def getaddrinfo(host, port, *args, **kwargs):
        if host not in answers:
            raise OSError("no such name")
        return [(2, 1, 6, "", (answers[host], port))]

    monkeypatch.setattr(monitor.socket, "getaddrinfo", getaddrinfo)
    assert monitor.public_ws_url("wss://feed.example.com/live?x=1") == "wss://feed.example.com/live?x=1"
    for url in ("wss://feed.example.com:8443/live", "ws://feed.example.com:22/", "wss://user:pw@feed.example.com/",
                "wss://inside.example.com/", "https://feed.example.com/", "ws://127.0.0.1/", "wss://nowhere.example/",
                "wss://nat64.example.com/", "wss://compat.example.com/"):
        assert monitor.public_ws_url(url) == "", url


# -- the monitor tool --------------------------------------------------------

def test_the_monitor_tool_turns_command_output_into_events(ssh_host, fast_batches, wakes):
    _, tid = new_thread(ssh_host.uid)
    ssh_host.shell.on("tail -f build.log", out="ok\nFAIL one\nok\n", err="oops\n")

    async def body():
        _, rec = await workspace.execute(
            "monitor", json.dumps({"command": "tail -f build.log",
                                   "pattern": "FAIL", "max_events": 5, "description": "Watch for failures"}),
            user_id=ssh_host.uid, bound_host_id=ssh_host.host_id, bound_cwd=ssh_host.root, thread_id=tid)
        await wait_until_async(lambda: job(rec["id"])["status"] == "completed")
        envelopes = [e["envelope"] for e in command_jobs.pending_envelopes(ssh_host.uid, tid)]
        result, _ = await workspace.execute("get_command_output", json.dumps({"id": rec["id"]}),
                                            user_id=ssh_host.uid, thread_id=tid)
        return rec, result, envelopes

    rec, result, envelopes = run_async(body())

    assert rec["name"] == "monitor" and rec["status"] == "running"
    stored = job(rec["id"])
    assert (stored["exit_code"], stored["event_count"], stored["kind"]) == (0, 1, "monitor")
    assert "[stderr]\noops" in result  # stderr is kept, never an event
    assert any("FAIL one" in e for e in envelopes)
    assert ssh_host.shell.reached == ["tail -f build.log"]


def job(job_id):
    return db.fetchone("SELECT * FROM command_jobs WHERE id=?", (job_id,))


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
    assert ssh_host.shell.raw == []
