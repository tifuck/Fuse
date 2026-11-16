"""run_terminal_command, get_command_output and kill_command over SSH.

Commands really run (local bash via LocalConn), so exit codes, stderr,
output caps and cwd handling are the host's, and the job row in command_jobs
is what a reload or restart would see. Waits are on files the test creates,
never on a sleep.
"""
import asyncio
import json
import os

from app import command_jobs, db, run_inbox, workspace
from tests.core.helpers import run_async, wait_until_async


async def tool(h, name, *, run_id="", thread_id=None, **args):
    text, rec = await workspace.execute(
        name, json.dumps(args), user_id=h.uid, bound_host_id=h.host_id, bound_cwd=h.root,
        run_id=run_id, thread_id=thread_id)
    return workspace.unlabel_host(h.alias, text), rec


def job_row(job_id):
    return db.fetchone("SELECT * FROM command_jobs WHERE id=?", (job_id,))


def test_a_failing_command_completes_with_its_exit_code_and_both_streams(ssh_host):
    body, rec = run_async(tool(ssh_host, "run_terminal_command", command="echo out; echo err >&2; exit 3"))

    assert rec["state"] == "completed" and rec["exit_code"] == 3
    assert rec["status"] == "done"  # a non-zero exit is still a finished command
    assert "exit_code: 3" in body
    assert "out" in rec["output"] and "err" in rec["output"]
    row = job_row(rec["id"])
    assert (row["status"], row["exit_code"], row["background"]) == ("completed", 3, 0)
    assert row["command"] == "echo out; echo err >&2; exit 3"
    assert row["cwd"] == ssh_host.root


def test_commands_run_in_the_working_root_and_cwd_stays_inside_it(ssh_host):
    os.makedirs(os.path.join(ssh_host.root, "sub"))

    _, rec = run_async(tool(ssh_host, "run_terminal_command", command="pwd"))
    assert rec["output"].strip() == ssh_host.root
    _, rec = run_async(tool(ssh_host, "run_terminal_command", command="pwd", cwd="sub"))
    assert rec["output"].strip() == os.path.join(ssh_host.root, "sub")

    text, rec = run_async(tool(ssh_host, "run_terminal_command", command="pwd", cwd="../"))
    assert rec["status"] == "error" and "outside the project root" in text


def test_a_missing_cwd_stops_the_command_instead_of_running_it_elsewhere(ssh_host):
    _, rec = run_async(tool(ssh_host, "run_terminal_command", command="touch marker", cwd="nosuch"))

    assert rec["state"] == "completed" and rec["exit_code"] == 1
    assert not os.path.exists(os.path.join(ssh_host.home, "marker"))
    assert not os.path.exists(os.path.join(ssh_host.root, "marker"))


def test_denylisted_commands_never_run_without_a_gate(ssh_host):
    for command in ("rm -rf /", "echo x; sudo touch planted", "ssh other touch planted",
                    "bash -c 'shutdown now'"):
        text, rec = run_async(tool(ssh_host, "run_terminal_command", command=command))
        assert rec["status"] == "error" and "denylist" in text, command
    assert not db.fetchone("SELECT 1 FROM command_jobs")
    assert not os.path.exists(os.path.join(ssh_host.root, "planted"))


def test_empty_and_oversized_commands_are_refused(ssh_host):
    text, rec = run_async(tool(ssh_host, "run_terminal_command", command="   "))
    assert rec["status"] == "error" and "command is required" in text
    text, rec = run_async(tool(ssh_host, "run_terminal_command", command="echo " + "x" * 8000))
    assert rec["status"] == "error" and "8000" in text


def test_long_output_keeps_its_head_and_tail_and_counts_every_character(ssh_host):
    _, rec = run_async(tool(ssh_host, "run_terminal_command", command="seq 1 20000"))

    total = len("".join(f"{i}\n" for i in range(1, 20001)))
    assert rec["output_chars"] == total and rec["output_chars_exact"] is True
    assert rec["truncated"] is True
    row = job_row(rec["id"])
    assert row["out_chars"] == total
    assert len(row["out"]) == command_jobs.OUT_TAIL_CHARS and row["out"].endswith("19999\n20000\n")
    body, _ = command_jobs.render(command_jobs._live[rec["id"]])
    assert "\n1\n2\n3\n" in body and body.rstrip().endswith("20000")
    assert "characters of output omitted" in body


def test_background_command_returns_an_id_at_once_and_joins_later(ssh_host):
    release = os.path.join(ssh_host.root, "release")

    async def body():
        text, rec = await tool(ssh_host, "run_terminal_command", background=True,
                               command=f"until [ -e {release} ]; do sleep 0.02; done; echo finished")
        assert rec["status"] == "running" and rec["background"] is True
        row = job_row(rec["id"])
        assert (row["status"], row["background"], row["notify"]) == ("running", 1, 1)
        open(release, "w").close()
        joined, result = await tool(ssh_host, "get_command_output", id=rec["id"], timeout_ms=5000)
        return rec["id"], joined, result

    job_id, joined, result = run_async(body())

    assert result["state"] == "completed" and result["exit_code"] == 0
    assert "finished" in joined
    row = job_row(job_id)
    assert (row["status"], row["delivered"]) == ("completed", 1)  # the join was the delivery


def test_a_foreground_command_moves_to_the_background_when_something_arrives(ssh_host):
    release = os.path.join(ssh_host.root, "release")

    async def body():
        call = asyncio.create_task(tool(
            ssh_host, "run_terminal_command", run_id="run-x",
            command=f"until [ -e {release} ]; do sleep 0.02; done; echo released"))
        await wait_until_async(lambda: run_inbox._waiters.get("run-x"))  # the foreground wait is listening
        run_inbox.arrived("run-x", "steer-1")
        text, rec = await call
        moved = job_row(rec["id"])
        open(release, "w").close()
        _, result = await tool(ssh_host, "get_command_output", id=rec["id"], timeout_ms=5000)
        return text, rec, moved, result

    text, rec, moved, result = run_async(body())

    assert text.startswith("Background results arrived for you")
    assert rec["status"] == "running" and rec["background"] is True
    assert (moved["background"], moved["notify"]) == (1, 1)
    assert result["state"] == "completed" and "released" in result["output"]


def test_a_foreground_command_past_its_timeout_moves_to_the_background(ssh_host):
    # The shortest foreground timeout is 1 s, so this test waits that long.
    release = os.path.join(ssh_host.root, "release")

    async def body():
        text, rec = await tool(ssh_host, "run_terminal_command", timeout=1,
                               command=f"until [ -e {release} ]; do sleep 0.02; done; echo late")
        live = command_jobs._live[rec["id"]]
        still = live["status"]
        open(release, "w").close()
        await asyncio.wait_for(asyncio.shield(live["future"]), 5)
        return text, rec, still

    text, rec, still = run_async(body())

    assert text.startswith("Command did not complete within its 1s timeout and was moved to the background.")
    assert still == "running"  # moved, not killed
    row = job_row(rec["id"])
    assert (row["status"], row["background"], row["notify"]) == ("completed", 1, 1)
    assert row["out"] == "late\n"


def test_a_bare_sleep_stops_instead_of_moving_to_the_background(ssh_host):
    async def body():
        call = asyncio.create_task(tool(ssh_host, "run_terminal_command", run_id="run-y", command="sleep 2"))
        await wait_until_async(lambda: run_inbox._waiters.get("run-y"))
        run_inbox.arrived("run-y", "report-1")
        text, rec = await call
        await asyncio.wait_for(asyncio.shield(command_jobs._live[rec["id"]]["future"]), 5)
        return text, rec

    text, rec = run_async(body())

    assert text.startswith("Sleep stopped early.")
    assert job_row(rec["id"])["status"] == "cancelled"


def test_a_connector_command_streams_stdout_and_labels_stderr(agent_host):
    _, rec = run_async(tool(agent_host, "run_terminal_command", command="echo out; echo err >&2; exit 4"))

    assert rec["state"] == "completed" and rec["exit_code"] == 4
    assert rec["output"] == "out\n[stderr]\nerr\n"
    assert [op for op, _ in agent_host.connector.calls if op.startswith("exec")][-1] == "exec_stream"


def test_a_connector_deadline_kill_is_a_timeout_not_an_exit(agent_host):
    agent_host.connector.overrides["exec_stream"] = {
        "ok": False, "exit": 137, "out": "partial\n", "err": "", "error": "timed out", "timeout": True,
        "termination_confirmed": True}

    _, rec = run_async(tool(agent_host, "run_terminal_command", command="make slow"))

    assert rec["state"] == "timed_out" and rec["exit_code"] is None
    assert "partial" in rec["output"] and rec["termination_confirmed"] is True


def test_an_older_connector_without_streaming_still_runs_the_command(agent_host):
    agent_host.connector.overrides["exec_stream"] = {"ok": False, "exit": None, "out": "", "err": "",
                                                     "error": "unknown op"}

    _, rec = run_async(tool(agent_host, "run_terminal_command", command="echo legacy"))

    assert rec["state"] == "completed" and rec["exit_code"] == 0 and rec["output"] == "legacy\n"
    assert [op for op, _ in agent_host.connector.calls][-2:] == ["exec_stream", "exec"]


def test_kill_command_cancels_a_running_job_and_a_second_kill_is_harmless(ssh_host):
    release = os.path.join(ssh_host.root, "release")

    async def body():
        _, rec = await tool(ssh_host, "run_terminal_command", background=True,
                            command=f"until [ -e {release} ]; do sleep 0.02; done")
        _, killed = await tool(ssh_host, "kill_command", id=rec["id"])
        await asyncio.wait_for(asyncio.shield(command_jobs._live[rec["id"]]["future"]), 5)
        _, again = await tool(ssh_host, "kill_command", id=rec["id"])
        open(release, "w").close()
        return rec["id"], killed, again

    job_id, killed, again = run_async(body())

    assert killed["state"] == "cancelling"
    assert again["state"] == "cancelled"
    row = job_row(job_id)
    assert row["status"] == "cancelled" and row["delivered"] == 1
    # The model asked for the kill: its own receipt is not delivered again.
    assert command_jobs.pending_envelopes(ssh_host.uid, 0) == []
