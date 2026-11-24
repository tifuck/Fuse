"""Connector hub terminal channel: pty_open as a job that stays open,
binary output routed by job id (malformed frames skipped), input, resize
and ack frames out, and the capability gate for old connectors.

Real agent_hub.handle_socket against a fake connector socket; no network.
"""
import asyncio
import json

import pytest

from app import agent_hub
from app.agent_hub import AgentError
from tests.core.agents._support import agent_host
from tests.core.helpers import quick_user, run_async, wait_until_async
from tests.core.terminal._support import PtyConnector


@pytest.fixture
def box():
    uid = quick_user()
    host, token = agent_host(uid)
    return uid, host["id"], token


async def _connect(uid, hid, ws):
    task = asyncio.create_task(agent_hub.handle_socket(ws))
    await wait_until_async(lambda: (agent_hub._conns.get((uid, hid)) or {}).get("ws") is ws or task.done())
    assert not task.done(), ws.sent
    return task


async def _hang_up(ws, task):
    ws.drop()
    await asyncio.wait_for(task, 5)


def test_output_frames_reach_their_terminal_and_malformed_ones_are_skipped(box):
    uid, hid, token = box
    seen = []

    async def body():
        ws = PtyConnector(token)
        task = await _connect(uid, hid, ws)
        pty = await agent_hub.open_pty(uid, hid, cols=80, rows=24, cwd="/srv", shell="", on_output=seen.append)
        job = pty["id"]
        assert ws.opens()[0] == {"v": 1, "t": "job", "id": job, "op": "pty_open", "cols": 80, "rows": 24,
                                 "cwd": "/srv", "shell": ""}
        assert (pty["shell"], pty["pid"]) == ("/bin/bash", 4242)
        for bad in (b"\x01short", bytes([3]) + job.encode() + b"kind", b"\x01" + b"f" * 12 + b"unknown",
                    b"\x01" + b"\xff" * 12 + b"not ascii", b"\x01" + job.encode(),
                    b"\x01" + job.encode() + b"y" * (agent_hub.PTY_FRAME + 1)):
            ws.inbox.put_nowait(bad)
        ws.output(job, b"\x1b[1mok\r\n")
        await wait_until_async(lambda: seen)
        # Ordinary jobs share the socket and keep working beside it.
        call = asyncio.create_task(agent_hub.call(uid, hid, "exec", {"cmd": "true"}))
        await wait_until_async(lambda: len(ws.jobs()) == 2)
        ws.reply(ws.jobs()[1]["id"], ok=True, exit=0, out="fine")
        assert (await asyncio.wait_for(call, 5))["out"] == "fine"
        ws.reply(job, ok=True, exit=3)
        result = await asyncio.wait_for(pty["done"], 5)
        await _hang_up(ws, task)
        return result

    result = run_async(body())
    assert seen == [b"\x1b[1mok\r\n"]
    assert result["exit"] == 3
    assert agent_hub._ptys == {}


def test_input_resize_and_ack_go_out_as_their_frames(box):
    uid, hid, token = box

    async def body():
        ws = PtyConnector(token)
        task = await _connect(uid, hid, ws)
        pty = await agent_hub.open_pty(uid, hid, cols=80, rows=24, on_output=lambda _data: None)
        job = pty["id"]
        big = b"a" * (agent_hub.PTY_FRAME + 10)
        assert await agent_hub.pty_input(uid, hid, job, big)
        assert await agent_hub.pty_resize(uid, hid, job, 120, 40)
        assert await agent_hub.pty_ack(uid, hid, job, 4096)
        await agent_hub.close_pty(uid, hid, job, wait=0)
        assert job in ws.cancels()
        with pytest.raises(AgentError):
            await pty["done"]
        await _hang_up(ws, task)
        return job, ws

    job, ws = run_async(body())
    head = bytes([2]) + job.encode()
    assert ws.binary == [head + b"a" * agent_hub.PTY_FRAME, head + b"a" * 10]
    assert {"v": 1, "t": "pty_resize", "id": job, "cols": 120, "rows": 40} in ws.sent
    assert {"v": 1, "t": "pty_ack", "id": job, "n": 4096} in ws.sent


def test_an_old_connector_without_the_pty_capability_is_refused(box):
    uid, hid, token = box

    async def body():
        ws = PtyConnector(token, caps=())
        task = await _connect(uid, hid, ws)
        assert not agent_hub.has_cap(uid, hid, "pty")
        with pytest.raises(AgentError) as refused:
            await agent_hub.open_pty(uid, hid, cols=80, rows=24, on_output=lambda _data: None)
        assert ws.jobs() == []
        await _hang_up(ws, task)
        return refused.value

    err = run_async(body())
    assert err.status_code == 409 and "Update the connector" in err.detail


def test_a_shell_that_fails_to_start_raises_with_the_connectors_error(box):
    uid, hid, token = box

    class Refusing(PtyConnector):
        async def send_json(self, obj):
            self.sent.append(obj)
            if obj.get("t") == "job":
                self.reply(obj["id"], ok=False, error="no such shell")

    async def body():
        ws = Refusing(token)
        task = await _connect(uid, hid, ws)
        with pytest.raises(AgentError, match="no such shell"):
            await agent_hub.open_pty(uid, hid, cols=80, rows=24, shell="pwsh", on_output=lambda _data: None)
        assert agent_hub._ptys == {} and agent_hub._conns[(uid, hid)]["pending"] == {}
        await _hang_up(ws, task)

    run_async(body())


def test_hello_shells_are_kept_for_the_host_picker(box):
    uid, hid, token = box

    async def body():
        ws = PtyConnector(token, shells=["powershell", "cmd", "pwsh", 7])
        task = await _connect(uid, hid, ws)
        shells = agent_hub.shells(uid, hid)
        await _hang_up(ws, task)
        return shells

    assert run_async(body()) == ("powershell", "cmd", "pwsh")
    assert json.dumps(agent_hub.shells(uid, hid)) == "null"  # offline: unknown
