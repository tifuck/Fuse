"""Connector hub: hello/auth, call routing by job id, disconnects, the
reconnect window, held jobs across a drop, and cancel replay.

Real agent_hub.handle_socket against a fake connector socket
(_support.ConnectorWS) and real host rows; no network.
"""
import asyncio
import json
from unittest.mock import patch

import pytest

from app import agent_hub, db, hosts
from app.agent_hub import AgentError
from tests.core.agents._support import ConnectorWS, agent_host
from tests.core.helpers import quick_user, run_async, wait_until_async


@pytest.fixture
def box(fresh_db):
    uid = quick_user()
    host, token = agent_host(uid)
    return uid, host["id"], token


def _hello(token, inst="", **extra):
    return {"v": 1, "t": "hello", "token": token, "ver": "1.7.3", "hostname": "box", **({"inst": inst} if inst else {}), **extra}


async def _connect(uid, hid, token, inst="", answer=None):
    ws = ConnectorWS(_hello(token, inst), answer)
    task = asyncio.create_task(agent_hub.handle_socket(ws))
    await wait_until_async(lambda: (agent_hub._conns.get((uid, hid)) or {}).get("ws") is ws or task.done())
    assert not task.done(), ws.sent
    return ws, task


async def _hang_up(ws, task):
    ws.drop()
    await asyncio.wait_for(task, 5)


def _pending(uid, hid):
    return (agent_hub._conns.get((uid, hid)) or {}).get("pending") or {}


async def _start_call(uid, hid, op="exec", timeout=30, **args):
    task = asyncio.create_task(agent_hub.call(uid, hid, op, args, timeout=timeout))
    await wait_until_async(lambda: task.done() or _pending(uid, hid) or (uid, hid) in agent_hub._online)
    return task


# -- hello ---------------------------------------------------------------------

def test_hello_by_device_token_registers_the_host_and_records_it(box):
    uid, hid, token = box

    async def body():
        ws, task = await _connect(uid, hid, token)
        assert ws.sent[0] == {"v": 1, "t": "hello_ok", "host_id": hid, "alias": hosts.get_host(uid, hid)["alias"]}
        assert agent_hub.is_online(uid, hid) and agent_hub.connector_version(uid, hid) == "1.7.3"
        await asyncio.gather(*agent_hub._background)
        await _hang_up(ws, task)

    run_async(body())
    assert hosts.get_host(uid, hid)["agent_version"] == "1.7.3"
    assert hosts.settings_for(uid)["agent_coding"] is True  # a connecting machine turns coding on
    assert not agent_hub.is_online(uid, hid)
    assert (uid, hid) in agent_hub._dropped_at


@pytest.mark.parametrize("first, close_code, auth", [
    (_hello("fa_" + "x" * 30), 4401, True),           # unknown token
    ({"v": 1, "t": "hello"}, 4401, True),             # no code or token
    ({"v": 1, "t": "job"}, 4400, False),              # not a hello
    ('{"t":"hello","n":' + "9" * 5000 + "}", 4400, False),  # over-long integer
    ("not json", 4400, False),
])
def test_a_bad_hello_is_refused_without_registering(box, first, close_code, auth):
    uid, hid, _ = box
    ws = ConnectorWS(first)
    run_async(agent_hub.handle_socket(ws, hello_timeout=1))
    assert ws.closed == [close_code]
    assert agent_hub._conns == {}
    if auth:
        assert (ws.sent[0]["t"], ws.sent[0].get("code")) == ("hello_err", "auth")


def test_a_disabled_host_is_refused_at_hello(box):
    uid, hid, token = box
    hosts.patch_host(uid, hid, {"disabled": True})
    ws = ConnectorWS(_hello(token))
    run_async(agent_hub.handle_socket(ws, hello_timeout=1))
    assert ws.closed == [4401] and not agent_hub.is_online(uid, hid)


def test_a_host_disabled_after_the_token_check_is_disconnected(box):
    uid, hid, token = box
    row = hosts.host_by_device_token(token)
    hosts.patch_host(uid, hid, {"disabled": True})

    async def body():
        # The token lookup ran before the disable committed; the hello
        # bookkeeping must close the socket that slipped through.
        ws = ConnectorWS(_hello(token))
        with patch.object(hosts, "host_by_device_token", return_value=row):
            task = asyncio.create_task(agent_hub.handle_socket(ws))
            await wait_until_async(lambda: {"v": 1, "t": "bye", "reason": "disabled"} in ws.sent)
        assert ws.sent[0]["t"] == "hello_ok"
        assert not agent_hub.is_online(uid, hid)
        await _hang_up(ws, task)

    run_async(body())


def test_rotating_the_token_locks_out_the_old_one(box):
    uid, hid, token = box
    fresh = hosts.rotate_agent_token(uid, hid)
    assert hosts.host_by_device_token(token) is None
    assert int(hosts.host_by_device_token(fresh)["id"]) == hid
    assert hosts.host_by_device_token(fresh.removeprefix("fa_")) is None


# -- calls ---------------------------------------------------------------------

def test_calls_are_matched_to_results_by_job_id(box):
    uid, hid, token = box

    async def body():
        ws, task = await _connect(uid, hid, token)
        first = asyncio.create_task(agent_hub.call(uid, hid, "exec", {"cmd": "one"}))
        second = asyncio.create_task(agent_hub.call(uid, hid, "exec", {"cmd": "two"}))
        await wait_until_async(lambda: len(ws.jobs()) == 2)
        by_cmd = {job["cmd"]: job["id"] for job in ws.jobs()}
        assert len(set(by_cmd.values())) == 2
        ws.reply(by_cmd["two"], ok=True, exit=0, out="TWO", truncated=False)
        ws.reply(by_cmd["one"], ok=False, exit="1", out="ONE", err="e" * (agent_hub.RESULT_CHARS + 5))
        one, two = await first, await second
        await _hang_up(ws, task)
        return one, two

    one, two = run_async(body())
    assert (two["out"], two["exit"], two["output_complete"]) == ("TWO", 0, True)
    # A malformed exit is unknown, an over-long field is clipped and flagged.
    assert (one["out"], one["exit"], one["ok"]) == ("ONE", None, False)
    assert len(one["err"]) == agent_hub.RESULT_CHARS and one["truncated"] and not one["output_complete"]


def test_a_payload_over_the_frame_limit_is_refused_before_sending(box):
    uid, hid, token = box

    async def body():
        ws, task = await _connect(uid, hid, token)
        with pytest.raises(AgentError, match="payload too large"):
            # UTF-8 bytes, not characters, count against the frame.
            await agent_hub.call(uid, hid, "write", {"path": "/tmp/x", "content": "€" * 310_000})
        assert ws.jobs() == [] and _pending(uid, hid) == {}
        await _hang_up(ws, task)

    run_async(body())


def test_calls_past_the_inflight_cap_are_refused_as_busy(box):
    uid, hid, token = box

    async def body():
        ws, task = await _connect(uid, hid, token)
        with patch.object(agent_hub, "MAX_INFLIGHT", 2):
            calls = [asyncio.create_task(agent_hub.call(uid, hid, "exec", {})) for _ in range(2)]
            await wait_until_async(lambda: len(ws.jobs()) == 2)
            with pytest.raises(AgentError, match="busy"):
                await agent_hub.call(uid, hid, "exec", {})
        for job in ws.jobs():
            ws.reply(job["id"], ok=True)
        assert all(r["ok"] for r in await asyncio.gather(*calls))
        await _hang_up(ws, task)

    run_async(body())


def test_a_timed_out_call_cancels_the_remote_job(box):
    # The hub's minimum call timeout is 1 s; this test pays it once.
    uid, hid, token = box

    async def body():
        ws, task = await _connect(uid, hid, token)
        with pytest.raises(AgentError, match="timed out"):
            await agent_hub.call(uid, hid, "exec", {"cmd": "sleep 99"}, timeout=0.01)
        job = ws.jobs()[0]["id"]
        assert ws.cancels() == [job] and _pending(uid, hid) == {}
        await _hang_up(ws, task)
        return job

    job = run_async(body())
    assert job in agent_hub._cancel_replay[(uid, hid)]


def test_a_cancelled_caller_cancels_the_remote_job_and_replays_it_after_reconnect(box):
    uid, hid, token = box

    async def body():
        ws, task = await _connect(uid, hid, token)
        caller = asyncio.create_task(agent_hub.call(uid, hid, "exec", {"cmd": "long"}))
        await wait_until_async(lambda: ws.jobs())
        job = ws.jobs()[0]["id"]
        await _hang_up(ws, task)
        # The cancel is sent while the socket is gone: it must reach the
        # connector on its next hello.
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        again, task = await _connect(uid, hid, token)
        await wait_until_async(lambda: job in again.cancels())
        # A terminal result acknowledges it: the next session replays nothing.
        again.reply(job, ok=False, error="cancelled")
        await wait_until_async(lambda: (uid, hid) not in agent_hub._cancel_replay)
        await _hang_up(again, task)
        third, task = await _connect(uid, hid, token)
        assert third.cancels() == []
        await _hang_up(third, task)

    run_async(body())


# -- disconnect and reconnect --------------------------------------------------

def test_disconnect_fails_pending_jobs_and_closes_the_socket(box):
    uid, hid, token = box

    async def body():
        ws, task = await _connect(uid, hid, token)
        caller = asyncio.create_task(agent_hub.call(uid, hid, "exec", {}))
        await wait_until_async(lambda: ws.jobs())
        await agent_hub.disconnect(uid, hid, "disabled")
        with pytest.raises(AgentError, match="offline"):
            await caller
        assert ws.cancels() == [ws.jobs()[0]["id"]]
        assert {"v": 1, "t": "bye", "reason": "disabled"} in ws.sent
        assert not agent_hub.is_online(uid, hid) and (uid, hid) not in agent_hub._dropped_at
        await _hang_up(ws, task)
        # Not coming back: a call fails at once instead of waiting.
        with patch.object(agent_hub, "RECONNECT_WAIT", 3600):
            with pytest.raises(AgentError, match="offline"):
                await asyncio.wait_for(agent_hub.call(uid, hid, "exec", {}, timeout=3600), 5)

    run_async(body())


def test_close_user_closes_only_that_users_connectors(fresh_db):
    a, b = quick_user("a@core.test"), quick_user("b@core.test")
    (ha, ta), (hb, tb) = agent_host(a, "ha"), agent_host(b, "hb")

    async def body():
        wa, task_a = await _connect(a, ha["id"], ta)
        wb, task_b = await _connect(b, hb["id"], tb)
        await agent_hub.close_user(a)
        assert not agent_hub.is_online(a, ha["id"]) and agent_hub.is_online(b, hb["id"])
        assert {"v": 1, "t": "bye", "reason": "logout"} in wa.sent
        await _hang_up(wa, task_a)
        await _hang_up(wb, task_b)

    run_async(body())


def test_a_call_during_the_reconnect_window_waits_for_the_connector(box):
    uid, hid, token = box

    async def body():
        ws, task = await _connect(uid, hid, token)
        await _hang_up(ws, task)
        assert (uid, hid) in agent_hub._dropped_at
        caller = await _start_call(uid, hid, cmd="true")
        assert (uid, hid) in agent_hub._online and not caller.done()
        back, task = await _connect(uid, hid, token, answer={"exec": {"ok": True, "out": "back"}})
        result = await asyncio.wait_for(caller, 5)
        await _hang_up(back, task)
        return result

    assert run_async(body())["out"] == "back"
    assert agent_hub._online == {}


def test_outside_the_reconnect_window_a_call_fails_fast(box):
    uid, hid, _ = box

    async def body():
        long_ago = agent_hub.time.monotonic() - agent_hub.RECONNECT_WINDOW - 1
        agent_hub._dropped_at[(uid, hid)] = long_ago
        agent_hub._started_at = long_ago
        with patch.object(agent_hub, "RECONNECT_WAIT", 3600):
            with pytest.raises(AgentError, match="offline"):
                await asyncio.wait_for(agent_hub.call(uid, hid, "exec", {}, timeout=3600), 5)
        # Just booted: every connector is reconnecting, so a call waits (here
        # a patched, tiny wait) before it gives up.
        agent_hub._started_at = agent_hub.time.monotonic()
        with patch.object(agent_hub, "RECONNECT_WAIT", 0.05):
            caller = await _start_call(uid, hid)
            assert (uid, hid) in agent_hub._online
            with pytest.raises(AgentError, match="offline"):
                await caller

    run_async(body())


def test_jobs_survive_a_drop_and_reattach_to_the_same_connector_process(box):
    uid, hid, token = box

    async def body():
        ws, task = await _connect(uid, hid, token, inst="proc-a")
        caller = asyncio.create_task(agent_hub.call(uid, hid, "exec", {}))
        await wait_until_async(lambda: ws.jobs())
        job = ws.jobs()[0]["id"]
        await _hang_up(ws, task)
        assert job in agent_hub._held[(uid, hid)]["pending"] and not caller.done()
        back, task = await _connect(uid, hid, token, inst="proc-a")
        assert job in _pending(uid, hid) and (uid, hid) not in agent_hub._held
        back.reply(job, ok=True, out="finished across the drop")
        result = await asyncio.wait_for(caller, 5)
        await _hang_up(back, task)
        return result

    assert run_async(body())["out"] == "finished across the drop"


def test_a_restarted_connector_fails_the_jobs_it_cannot_answer(box):
    uid, hid, token = box

    async def body():
        ws, task = await _connect(uid, hid, token, inst="proc-a")
        held = asyncio.create_task(agent_hub.call(uid, hid, "exec", {}))
        await wait_until_async(lambda: ws.jobs())
        await _hang_up(ws, task)
        other, task = await _connect(uid, hid, token, inst="proc-b")
        with pytest.raises(AgentError, match="connector restarted"):
            await asyncio.wait_for(held, 5)
        # A live socket replaced by the same process keeps its jobs.
        kept = asyncio.create_task(agent_hub.call(uid, hid, "exec", {}))
        await wait_until_async(lambda: other.jobs())
        same, task2 = await _connect(uid, hid, token, inst="proc-b")
        assert {"v": 1, "t": "bye", "reason": "replaced"} in other.sent
        same.reply(other.jobs()[0]["id"], ok=True, out="kept")
        assert (await asyncio.wait_for(kept, 5))["out"] == "kept"
        await _hang_up(same, task2)
        other.drop()
        await asyncio.wait_for(task, 5)

    run_async(body())


def test_held_jobs_fail_when_the_connector_does_not_return_in_time(box):
    uid, hid, token = box

    async def body():
        ws, task = await _connect(uid, hid, token)
        with patch.object(agent_hub, "HOLD_SECONDS", 0.01):
            caller = asyncio.create_task(agent_hub.call(uid, hid, "exec", {}, timeout=3600))
            await wait_until_async(lambda: ws.jobs())
            await _hang_up(ws, task)
            with pytest.raises(AgentError, match="connection lost"):
                await asyncio.wait_for(caller, 5)
        assert (uid, hid) not in agent_hub._held
        assert ws.jobs()[0]["id"] in agent_hub._cancel_replay[(uid, hid)]

    run_async(body())
