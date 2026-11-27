"""run_terminal_command, get_command_output and kill_command.

The host is a recording fake (tests.core.tools._support.FakeShell): it is
asked to run the command, records it, and answers with the scripted output
of a real host. Nothing is executed. What is real: command_jobs (the job
row a reload or restart would see), the foreground/background handoff, the
output caps, the denylist and cwd confinement in front of the host.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app import command_jobs, db, run_inbox, workspace
from app.workspace import WorkspaceError
from tests.core.helpers import quick_user, run_async, wait_until_async
from tests.core.tools._support import FakeConnector, FakeFS, FakeShell, make_host, new_thread


async def tool(h, name, *, run_id="", thread_id=None, **args):
    text, rec = await workspace.execute(
        name, json.dumps(args), user_id=h.uid, bound_host_id=h.host_id, bound_cwd=h.root,
        run_id=run_id, thread_id=thread_id)
    return workspace.unlabel_host(h.alias, text), rec


def job_row(job_id):
    return db.fetchone("SELECT * FROM command_jobs WHERE id=?", (job_id,))


async def arrive_until_returned(call, run_id):
    """Signal an inbox arrival until the blocked call returns (a signal sent
    before its wait starts listening is not queued, so keep sending)."""
    def signalled():
        run_inbox.arrived(run_id, "steer-1")
        return call.done()

    await wait_until_async(signalled)
    return await call


def test_a_failing_command_completes_with_its_exit_code_and_both_streams(ssh_host):
    ssh_host.shell.on("make check", out="out\n", err="err\n", code=3)

    body, rec = run_async(tool(ssh_host, "run_terminal_command", command="make check"))

    assert ssh_host.shell.reached == ["make check"]
    assert rec["state"] == "completed" and rec["exit_code"] == 3
    assert rec["status"] == "done"  # a non-zero exit is still a finished command
    assert "exit_code: 3" in body
    assert "out" in rec["output"] and "err" in rec["output"]
    row = job_row(rec["id"])
    assert (row["status"], row["exit_code"], row["background"]) == ("completed", 3, 0)
    assert (row["command"], row["cwd"]) == ("make check", ssh_host.root)


def test_commands_run_in_the_working_root_and_cwd_stays_inside_it(ssh_host):
    ssh_host.fs.makedirs(f"{ssh_host.root}/sub")
    ssh_host.shell.on("ls", out="a\n")

    run_async(tool(ssh_host, "run_terminal_command", command="ls"))
    run_async(tool(ssh_host, "run_terminal_command", command="ls", cwd="sub"))
    first, second = ssh_host.shell.raw
    assert f"cd {ssh_host.root} || exit 1" in first
    assert f"cd {ssh_host.root}/sub || exit 1" in second

    for cwd in ("../", "/etc", f"{ssh_host.root}-other"):
        text, rec = run_async(tool(ssh_host, "run_terminal_command", command="ls", cwd=cwd))
        assert rec["status"] == "error" and "outside the project root" in text, cwd
    assert len(ssh_host.shell.raw) == 2


def test_a_missing_cwd_stops_the_command_instead_of_running_it_elsewhere(ssh_host):
    _, rec = run_async(tool(ssh_host, "run_terminal_command", command="make", cwd="nosuch"))

    assert rec["state"] == "completed" and rec["exit_code"] == 1
    assert ssh_host.shell.asked == ["make"] and ssh_host.shell.reached == []
    # The wrapper stops on a failed cd instead of running the rest in $HOME.
    assert f"cd {ssh_host.root}/nosuch || exit 1" in ssh_host.shell.raw[0]


# Destructive commands appear only here: as pure strings for the matcher, and
# once through the recording fake to prove nothing is even asked of a host.
DESTRUCTIVE = ("rm -rf /", "rm -rf /*", "rm --no-preserve-root -rf /", "mkfs.ext4 /dev/sda",
               "chmod -R 777 /", "x; reboot", "nohup poweroff", "echo x; sudo true", "bash -c 'shutdown now'",
               "ssh other true", "scp f other:")
WINDOWS_DESTRUCTIVE = ("Stop-Computer -Force", "Restart-Computer", "Format-Volume -DriveLetter D",
                       "Clear-Disk 1", "dir; shutdown /s")


def test_the_denylist_matches_catastrophic_commands():
    for command in DESTRUCTIVE:
        assert workspace.denied(command), command
    for command in WINDOWS_DESTRUCTIVE:
        assert workspace.denied(command, windows=True), command
    for command in ("rm -rf build", "ls /", "git status", "make test", "echo rebooted", "chmod 755 bin/run"):
        assert workspace.denied(command) is None, command


# Pure strings for the matcher only: quoted or escaped words, a command named
# by its path, power verbs, every-process signals and other privilege tools.
DENY_BYPASSES = ("\\sudo ls", '"sudo" ls', "s\\udo ls", "'reboot'", "/sbin/reboot", "systemctl reboot",
                 "systemctl --no-wall poweroff", "systemctl halt", "systemctl kexec", "systemctl suspend",
                 "systemctl hibernate", "init 0", "x && init 6", "telinit 1", "kill -9 -1", "kill -s KILL -1",
                 "doas ls", "pkexec ls",
                 # Leading blanks, assignments, ! and wrappers with their own options.
                 " sudo ls", "\tsudo ls", "cd x\n  sudo ls", "FOO=1 sudo ls",
                 "DEBIAN_FRONTEND=noninteractive sudo apt-get install -y jq", "env -i sudo ls", "env -u PATH sudo ls",
                 "timeout 5 sudo ls", "timeout -s KILL 5 sudo ls", "nice -n 5 sudo ls", "ionice -c 3 sudo ls",
                 "stdbuf -oL sudo ls", "command -p sudo ls", "! sudo ls", 'bash --norc -c "sudo ls"', "sh -e -c 'reboot'",
                 "FOO=1 ssh other true", " rm -rf /", "x; LANG=C reboot",
                 "loginctl reboot", "loginctl poweroff", "systemctl start reboot.target", "systemctl isolate poweroff.target",
                 "systemctl --no-block start halt.target")
WINDOWS_DENY_BYPASSES = (" shutdown -r -t 0", "dir;  shutdown /s", "& ('Stop-'+'Computer')", "& (\"Restart-\" + \"Computer\")")


def test_the_denylist_sees_through_quoting_and_covers_power_and_privilege_commands():
    for command in DENY_BYPASSES:
        assert workspace.denied(command), command
    for command in ("systemctl status nginx", "kill -9 1234", "echo init 0", "git commit -m 'docs: sudo usage'",
                    "grep -r reboot docs", " make test", "FOO=1 make", "env -i make", "timeout 5 make test",
                    "nice -n 5 make", "systemctl start nginx", "loginctl list-sessions", "FOO=sudo make", "echo sudo"):
        assert workspace.denied(command) is None, command
    for command in WINDOWS_DENY_BYPASSES:
        assert workspace.denied(command, windows=True), command
    for command in ("Get-Date", " Get-ChildItem", "Write-Output ('Get-'+'Date')"):
        assert workspace.denied(command, windows=True) is None, command


def test_denylisted_commands_are_never_sent_to_a_host(ssh_host):
    for command in DESTRUCTIVE:
        text, rec = run_async(tool(ssh_host, "run_terminal_command", command=command))
        assert rec["status"] == "error" and "denylist" in text, command
        text, rec = run_async(tool(ssh_host, "monitor", command=command, description="d"))
        assert rec["status"] == "error" and "denylist" in text, command
    assert ssh_host.shell.raw == []
    assert not db.fetchone("SELECT 1 FROM command_jobs")


def test_destructive_windows_commands_are_never_sent_to_a_windows_host(fresh_db, monkeypatch):
    from app import agent_hub

    uid = quick_user()
    host = make_host(uid, "win", default_cwd="C:\\proj", auth_type="agent", agent_os="windows", agent_version="1.9.0")
    fs = FakeFS()
    connector = FakeConnector(fs, FakeShell(fs))
    monkeypatch.setattr(agent_hub, "call", connector)

    for command in WINDOWS_DESTRUCTIVE:
        text, rec = run_async(workspace.execute("run_terminal_command", json.dumps({"command": command}),
                                                user_id=uid, bound_host_id=host, bound_cwd="C:\\proj"))
        assert rec["status"] == "error" and "denylist" in text, command
    assert connector.calls == []


def test_the_per_host_exec_budget_is_180_a_minute(ssh_host):
    # The budget's window has no public reader: spend 179 of it directly
    # (179 commands through command_jobs cost seconds of database writes).
    for _ in range(179):
        workspace._rate_ok(ssh_host.uid, ssh_host.host_id)
    ssh_host.shell.on("make", out="ok\n")

    _, last = run_async(tool(ssh_host, "run_terminal_command", command="make"))
    assert last["state"] == "completed"
    text, refused = run_async(tool(ssh_host, "run_terminal_command", command="make"))
    assert refused["status"] != "done" and "exec rate limit" in (text + str(refused.get("error")))
    assert ssh_host.shell.reached == ["make"]


def test_a_host_list_is_one_shell_family_of_known_hosts(ssh_host):
    make_host(ssh_host.uid, "box2", default_cwd="/srv")
    make_host(ssh_host.uid, "pc", default_cwd="C:\\p", auth_type="agent", agent_os="windows")

    assert workspace.host_list(ssh_host.uid, ["box", "BOX2", "box"]) == ["box", "box2"]
    for raw, why in ((["box", "pc"], "mixes Windows"), (["box", "nowhere"], "nowhere: unknown host alias"),
                     ([], "1-32"), (["h"] * 33, "1-32"), ("box", "1-32"), (["box", ""], "1-32")):
        with pytest.raises(WorkspaceError, match=why):
            workspace.host_list(ssh_host.uid, raw)


def test_a_valid_host_list_inside_run_code_is_told_to_make_one_call_per_host(ssh_host):
    # The runner splits a direct call's list; only run_code's calls arrive whole.
    make_host(ssh_host.uid, "box2", default_cwd="/srv")
    text, rec = run_async(tool(ssh_host, "run_terminal_command", command="uptime", host=["box", "box2"]))
    assert rec["kind"] == "invalid_arguments" and text.startswith("inside run_code, host takes one alias")
    assert "hosts.map((host) => run_terminal_command({command, host}))" in text

    text, _ = run_async(tool(ssh_host, "run_terminal_command", command="uptime", host=["box", "nowhere"]))
    assert text.startswith("nowhere: unknown host alias")
    assert ssh_host.shell.reached == []


def test_empty_and_oversized_commands_are_refused(ssh_host):
    text, rec = run_async(tool(ssh_host, "run_terminal_command", command="   "))
    assert rec["status"] == "error" and "command is required" in text
    text, rec = run_async(tool(ssh_host, "run_terminal_command", command="echo " + "x" * 8000))
    assert rec["status"] == "error" and "8000" in text
    assert ssh_host.shell.raw == []


def test_long_output_keeps_its_head_and_tail_and_counts_every_character(ssh_host):
    output = "".join(f"{i}\n" for i in range(1, 20001))
    ssh_host.shell.on("seq 1 20000", out=output)

    _, rec = run_async(tool(ssh_host, "run_terminal_command", command="seq 1 20000"))

    assert rec["output_chars"] == len(output) and rec["output_chars_exact"] is True
    assert rec["truncated"] is True
    row = job_row(rec["id"])
    assert row["out_chars"] == len(output)
    assert len(row["out"]) == command_jobs.OUT_TAIL_CHARS and row["out"].endswith("19999\n20000\n")
    body, _ = run_async(tool(ssh_host, "get_command_output", id=rec["id"]))
    assert "\n1\n2\n3\n" in body and body.rstrip().endswith("20000")
    assert "characters of output omitted" in body


def test_background_command_returns_an_id_at_once_and_joins_later(ssh_host):
    build = ssh_host.shell.on("make build", out="finished\n", hold=True)

    async def body():
        text, rec = await tool(ssh_host, "run_terminal_command", background=True, command="make build")
        assert rec["status"] == "running" and rec["background"] is True
        row = job_row(rec["id"])
        assert (row["status"], row["background"], row["notify"]) == ("running", 1, 1)
        build.release()
        joined, result = await tool(ssh_host, "get_command_output", id=rec["id"], timeout_ms=5000)
        return rec["id"], joined, result

    job_id, joined, result = run_async(body())

    assert result["state"] == "completed" and result["exit_code"] == 0
    assert "finished" in joined
    row = job_row(job_id)
    assert (row["status"], row["delivered"]) == ("completed", 1)  # the join was the delivery


def test_a_foreground_command_moves_to_the_background_when_something_arrives(ssh_host):
    build = ssh_host.shell.on("make build", out="released\n", hold=True)

    async def body():
        call = asyncio.create_task(tool(ssh_host, "run_terminal_command", run_id="run-x", command="make build"))
        text, rec = await arrive_until_returned(call, "run-x")
        moved = job_row(rec["id"])
        build.release()
        _, result = await tool(ssh_host, "get_command_output", id=rec["id"], timeout_ms=5000)
        return text, rec, moved, result

    text, rec, moved, result = run_async(body())

    assert text.startswith("Background results arrived for you")
    assert rec["status"] == "running" and rec["background"] is True
    assert (moved["background"], moved["notify"]) == (1, 1)
    assert result["state"] == "completed" and "released" in result["output"]


def test_a_foreground_command_past_its_timeout_moves_to_the_background(ssh_host):
    # The shortest foreground timeout is 1 s, so this test waits that long.
    build = ssh_host.shell.on("make build", out="late\n", hold=True)

    async def body():
        text, rec = await tool(ssh_host, "run_terminal_command", timeout=1, command="make build")
        build.release()
        _, result = await tool(ssh_host, "get_command_output", id=rec["id"], timeout_ms=5000)
        return text, rec, result

    text, rec, result = run_async(body())

    assert text.startswith("Command did not complete within its 1s timeout and was moved to the background.")
    assert rec["status"] == "running" and result["state"] == "completed"  # moved, not killed
    row = job_row(rec["id"])
    assert (row["status"], row["background"], row["notify"]) == ("completed", 1, 1)
    assert row["out"] == "late\n"


def test_a_bare_sleep_stops_instead_of_moving_to_the_background(ssh_host):
    ssh_host.shell.on("sleep 2", hold=True)

    async def body():
        call = asyncio.create_task(tool(ssh_host, "run_terminal_command", run_id="run-y", command="sleep 2"))
        text, rec = await arrive_until_returned(call, "run-y")
        await wait_until_async(lambda: job_row(rec["id"])["status"] == "cancelled")
        return text, rec

    text, rec = run_async(body())

    assert text.startswith("Sleep stopped early.")
    assert job_row(rec["id"])["status"] == "cancelled"
    assert ssh_host.conn.processes[0].killed


def test_kill_command_cancels_a_running_job_and_a_second_kill_is_harmless(ssh_host):
    ssh_host.shell.on("make serve", hold=True)
    _, tid = new_thread(ssh_host.uid)

    async def body():
        _, rec = await tool(ssh_host, "run_terminal_command", background=True, thread_id=tid, command="make serve")
        _, killed = await tool(ssh_host, "kill_command", id=rec["id"], thread_id=tid)
        await wait_until_async(lambda: job_row(rec["id"])["status"] == "cancelled" and job_row(rec["id"])["delivered"])
        _, again = await tool(ssh_host, "kill_command", id=rec["id"], thread_id=tid)
        return rec["id"], killed, again

    job_id, killed, again = run_async(body())

    assert killed["state"] == "cancelling"
    assert again["state"] == "cancelled"
    assert ssh_host.conn.processes[0].killed
    row = job_row(job_id)
    assert row["status"] == "cancelled" and row["delivered"] == 1
    # The model asked for the kill: its own receipt is not delivered again.
    assert command_jobs.pending_envelopes(ssh_host.uid, tid) == []


# -- connector ----------------------------------------------------------------

def test_a_connector_command_streams_stdout_and_labels_stderr(agent_host):
    agent_host.shell.on("make check", out="out\n", err="err\n", code=4)

    _, rec = run_async(tool(agent_host, "run_terminal_command", command="make check"))

    assert rec["state"] == "completed" and rec["exit_code"] == 4
    assert rec["output"] == "out\n[stderr]\nerr\n"
    assert [op for op, _ in agent_host.connector.calls if op.startswith("exec")][-1] == "exec_stream"


def test_a_running_connector_command_shows_its_output_before_it_exits(agent_host):
    finish = asyncio.Event()

    async def exec_stream(args, on_chunk):
        on_chunk("out", "collected 3 items\n")
        on_chunk("err", "warning: slow\n")
        await finish.wait()
        on_chunk("out", "3 passed\n")
        return {"ok": True, "exit": 0, "out": "", "err": "", "error": "", "truncated": False}

    agent_host.connector.op_exec_stream = exec_stream
    shown = []

    async def body():
        call = asyncio.create_task(workspace.execute(
            "run_terminal_command", json.dumps({"command": "pytest"}), user_id=agent_host.uid,
            bound_host_id=agent_host.host_id, bound_cwd=agent_host.root, on_output=shown.append))
        await wait_until_async(lambda: shown)
        finish.set()
        return await call

    _, rec = run_async(body())

    assert shown[0] == {"output": "collected 3 items\n[stderr]\nwarning: slow\n"}
    assert rec["output"] == "collected 3 items\n3 passed\n[stderr]\nwarning: slow\n"


def test_a_connector_command_defaults_python_to_unbuffered_output(agent_host):
    agent_host.shell.on("pytest", out="ok\n")

    run_async(tool(agent_host, "run_terminal_command", command="pytest"))

    sent = [args["cmd"] for op, args in agent_host.connector.calls if op == "exec_stream"][-1]
    assert sent == 'export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"; pytest'


def test_an_ssh_command_defaults_python_to_unbuffered_output(ssh_host):
    ssh_host.shell.on("pytest", out="ok\n")

    run_async(tool(ssh_host, "run_terminal_command", command="pytest"))

    assert 'export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"' in ssh_host.shell.raw[-1]


def test_a_connector_deadline_kill_is_a_timeout_not_an_exit(agent_host):
    agent_host.connector.overrides["exec_stream"] = {
        "ok": False, "exit": 137, "out": "partial\n", "err": "", "error": "timed out", "timeout": True,
        "termination_confirmed": True}

    _, rec = run_async(tool(agent_host, "run_terminal_command", command="make slow"))

    assert rec["state"] == "timed_out" and rec["exit_code"] is None
    assert "partial" in rec["output"] and rec["termination_confirmed"] is True


def test_a_command_the_connector_could_not_start_is_an_error_not_an_exit(agent_host):
    agent_host.shell.on("make quiet", code=2)

    _, rec = run_async(tool(agent_host, "run_terminal_command", command="make quiet"))
    assert rec["state"] == "completed" and rec["exit_code"] == 2  # a silent failing command still ran

    for error, code in (("invalid cwd", 127), ("invalid env", 1)):
        agent_host.connector.overrides["exec_stream"] = {"ok": False, "exit": code, "out": "", "err": "", "error": error}
        text, rec = run_async(tool(agent_host, "run_terminal_command", command="make"))
        assert rec["state"] != "completed" and rec.get("exit_code") is None, error
        assert f"connector could not execute command: {error}" in text


def test_a_temp_dir_another_account_owns_is_refused(ssh_host):
    cwd = "/tmp/fuse/c-1/s-1"
    h = SimpleNamespace(**{**vars(ssh_host), "root": cwd})  # a chat working in its temp dir
    h.shell.on("ls", out="a\n")

    _, rec = run_async(tool(h, "run_terminal_command", command="ls"))
    assert rec["state"] == "completed" and h.shell.reached == ["ls"]
    assert h.fs.mode("/tmp/fuse") == 0o700 and h.fs.mode(cwd) == 0o700

    h.fs.nodes["/tmp/fuse"].uid = 1001  # made first by another local user
    text, rec = run_async(tool(h, "run_terminal_command", command="ls"))
    assert rec["status"] == "error" and "refusing to use the Fuse temp dir: /tmp/fuse" in text
    text, rec = run_async(tool(h, "write_file", path=cwd + "/new/run.sh", content="echo hi\n"))
    assert rec["status"] == "error" and "refusing to use the Fuse temp dir" in text
    assert h.shell.reached == ["ls"] and not h.fs.exists(cwd + "/new/run.sh")


def test_the_windows_temp_dir_script_makes_private_folders_and_checks_each_one():
    # Text only: the script is never run here.
    script = workspace._fuse_temp_mkdir_cmd("%TEMP%\\fuse\\c-1\\it's", True)
    home = "(Join-Path $env:USERPROFILE 'AppData\\Local\\Temp\\fuse"
    assert f"$dirs = @({home}'), {home}\\c-1'), {home}\\c-1\\it''s'))" in script
    assert "SetAccessRuleProtection($true, $false)" in script and "CreateDirectory($d, $sec)" in script
    assert "$ok = @($me, 'S-1-5-18', 'S-1-5-32-544')" in script
    for check in ("ReparsePoint", "GetOwner(", "-band 0x500d0046", "InheritOnly", "'fuse-temp-unsafe:' + $d"):
        assert check in script, check
    # SYSTEM's C:\Windows\Temp is temp too, and any user may create folders there.
    script = workspace._fuse_temp_mkdir_cmd("C:/Windows/Temp/fuse/skills/demo-1", True)
    assert "$dirs = @(('C:\\Windows\\Temp\\fuse'), ('C:\\Windows\\Temp\\fuse\\skills'), " \
           "('C:\\Windows\\Temp\\fuse\\skills\\demo-1'))" in script


def test_an_older_connector_without_streaming_still_runs_the_command(agent_host):
    agent_host.connector.overrides["exec_stream"] = {"ok": False, "exit": None, "out": "", "err": "",
                                                     "error": "unknown op"}
    agent_host.shell.on("make legacy", out="legacy\n")

    _, rec = run_async(tool(agent_host, "run_terminal_command", command="make legacy"))

    assert rec["state"] == "completed" and rec["exit_code"] == 0 and rec["output"] == "legacy\n"
    assert [op for op, _ in agent_host.connector.calls][-2:] == ["exec_stream", "exec"]
