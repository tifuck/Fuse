"""Terminal sessions: flow control thresholds, credit to the connector,
the detached timeout, the audit row, and the project binding.

The registry runs for real; SSH channels are FakePty and connectors a fake
socket on the real hub. Nothing executes.
"""
import asyncio

from app import agent_hub, chats, terminal
from tests.core.agents._support import agent_host
from tests.core.helpers import quick_user, run_async, wait_until_async
from tests.core.terminal._support import PtyConnector, audit
from tests.core.tools._support import make_host, make_project

FRAME = terminal.FRAME_BYTES


async def _settle():
    for _ in range(20):
        await asyncio.sleep(0)


def test_output_pauses_above_the_high_water_mark_and_resumes_below_the_low(ssh_ptys):
    uid = quick_user()
    hid = make_host(uid)

    async def body():
        s = await terminal.open_session(uid, "login", host_id=hid)
        pty = ssh_ptys[0]
        s.attach(terminal.Client())
        frames = terminal.HIGH_WATER // FRAME + 4
        for _ in range(frames):
            pty.emit(b"x" * FRAME)
        # The frame that crosses HIGH_WATER is the last one read.
        crossed = terminal.HIGH_WATER // FRAME + 1
        await wait_until_async(lambda: s.unacked == crossed * FRAME)
        await _settle()
        assert s.unacked == crossed * FRAME and pty.queue.qsize() == frames - crossed
        s.ack(s.unacked - terminal.LOW_WATER)  # exactly LOW_WATER left: still paused
        await _settle()
        assert pty.queue.qsize() == frames - crossed
        s.ack(1)
        await wait_until_async(lambda: pty.queue.qsize() == 0)
        await s.close("closed")

    run_async(body())


def test_a_detached_session_closes_after_the_grace_and_kills_its_shell(ssh_ptys, monkeypatch):
    uid = quick_user()
    hid = make_host(uid)
    monkeypatch.setattr(terminal, "DETACH_SECONDS", 0.05)

    async def body():
        s = await terminal.open_session(uid, "login", host_id=hid)
        client = terminal.Client()
        s.attach(client)
        s.detach(client)
        await wait_until_async(lambda: not s.alive)
        await asyncio.gather(*terminal._background)
        return s

    s = run_async(body())
    assert s.reason == "detached" and ssh_ptys[0].closed
    assert audit(s.id)["close_reason"] == "detached"


def test_connector_credit_follows_the_browser_and_runs_free_while_detached(fresh_db):
    uid = quick_user()
    host, token = agent_host(uid)
    hid = host["id"]

    async def body():
        ws = PtyConnector(token)
        task = asyncio.create_task(agent_hub.handle_socket(ws))
        await wait_until_async(lambda: agent_hub.has_cap(uid, hid, "pty"))
        s = await terminal.open_session(uid, "login", host_id=hid, cols=100, rows=30)
        job = ws.opens()[0]["id"]
        client = terminal.Client()
        s.attach(client)
        ws.output(job, b"hello")
        await wait_until_async(lambda: b"hello" in client.items)
        await _settle()
        assert ws.acks() == []  # not processed by the browser yet
        s.ack(5)
        await wait_until_async(lambda: ws.acks() == [5])
        await s.write(b"ls\r")
        s.detach(client)
        ws.output(job, b"more")
        await wait_until_async(lambda: ws.acks() == [5, 4])
        ws.reply(job, ok=True, exit=0)
        await wait_until_async(lambda: not s.alive)
        await asyncio.gather(*terminal._background)
        ws.drop()
        await asyncio.wait_for(task, 5)
        return s, ws, client

    s, ws, client = run_async(body())
    assert ws.opens()[0]["cols"] == 100 and ws.opens()[0]["shell"] == ""
    assert ws.binary == [bytes([2]) + ws.opens()[0]["id"].encode() + b"ls\r"]
    assert client.items[0]["t"] == "ready"
    assert (s.reason, s.exit_code) == ("exit", 0)


def test_a_shell_never_reaped_ends_the_terminal_with_no_exit_code(fresh_db):
    uid = quick_user()
    host, token = agent_host(uid)

    async def body():
        ws = PtyConnector(token)
        task = asyncio.create_task(agent_hub.handle_socket(ws))
        await wait_until_async(lambda: agent_hub.has_cap(uid, host["id"], "pty"))
        s = await terminal.open_session(uid, "login", host_id=host["id"])
        client = terminal.Client()
        s.attach(client)
        ws.reply(ws.opens()[0]["id"], ok=True, exit=None)
        await wait_until_async(lambda: not s.alive)
        await asyncio.gather(*terminal._background)
        ws.drop()
        await asyncio.wait_for(task, 5)
        return s, client

    s, client = run_async(body())
    assert (s.reason, s.exit_code) == ("exit", None)
    assert client.items[-1] == {"t": "exit", "code": None, "reason": "exit"} and client.close_code == 4410
    assert audit(s.id)["exit_code"] is None and audit(s.id)["close_reason"] == "exit"


def test_the_audit_row_holds_metadata_and_never_the_bytes(ssh_ptys):
    uid = quick_user()
    hid = make_host(uid, "web")

    async def body():
        s = await terminal.open_session(uid, "sid-1", host_id=hid, client_ip="203.0.113.9", user_agent="UA/1")
        s.attach(terminal.Client())
        await s.write(b"echo hunter2\r")
        ssh_ptys[0].emit(b"hunter2\r\n")
        await wait_until_async(lambda: s.bytes_out)
        ssh_ptys[0].exit(0)
        await wait_until_async(lambda: not s.alive)
        await asyncio.gather(*terminal._background)
        return s

    s = run_async(body())
    row = audit(s.id)
    assert (row["user_id"], row["host_id"], row["alias"], row["transport"], row["remote_user"]) == (uid, hid, "web", "ssh", "dev")
    assert (row["close_reason"], row["exit_code"], row["bytes_in"], row["bytes_out"]) == ("exit", 0, 13, 9)
    assert (row["client_ip"], row["user_agent"], row["shell"]) == ("203.0.113.9", "UA/1", "login")
    assert row["closed_at"] and not any("hunter2" in str(value) for value in row.values())


def test_a_project_chat_opens_in_the_bound_folder_whatever_the_client_sends(ssh_ptys):
    uid = quick_user()
    bound, other = make_host(uid, "bound"), make_host(uid, "other")
    project = make_project(uid, bound, "/srv/app")
    thread = chats.create_thread(uid, model="offline", kind="project", host_id=bound, project_id=project)

    async def body():
        return await terminal.open_session(uid, "login", host_id=other, thread_id=thread["id"])

    s = run_async(body())
    assert (s.host_id, s.cwd, s.project_id) == (bound, "/srv/app", project)
    assert ssh_ptys[0].command == "cd -- /srv/app 2>/dev/null; exec \"${SHELL:-/bin/sh}\" -l"
    assert audit(s.id)["cwd"] == "/srv/app"
