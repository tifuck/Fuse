"""run_code in the tool runner: checkpoints, failures, secrets, rows and tips.

Round cases run one tool round through tool_runner.run_tools with run_code
answered by a scripted stand-in for code_mode.run (no worker process): it
makes its calls through the runner as the real one does, then returns what
the case gives. The calls reach a recording executor; nothing reaches a host.
"""
import asyncio
import json

import pytest

from app import chat_tools, chats, code_mode, secret_guard, tool_failures
from app.chat_generation import tool_jobs, tool_runner
from app.chat_generation.tool_jobs import ToolJob
from tests.core.secrets._values import OPENAI

CODE = "await write_file({path: 'a.txt', content: 'x'});"


class _Connected:
    async def is_disconnected(self):
        return False


async def done(name, arguments, **kw):
    return f"ran {name}", {"name": name, "status": "done"}


def scripted(monkeypatch, calls=(), text="ok\n[Fuse: 1 call: write_file×1]", **trace):
    """run_code makes calls [(name, args)] through the runner, then returns text with a trace of status done
    updated by trace."""
    async def run(arguments, *, call, paused=None, owner=()):
        for name, args in calls:
            await call(name, args)
        return text, {"name": "run_code", "status": "done", "output": text, "code": json.loads(arguments)["code"], **trace}

    monkeypatch.setattr(code_mode, "available", lambda: True)
    monkeypatch.setattr(code_mode, "run", run)


def one_round(h, code=CODE, *, persist=None, execute_tool=done, request=None):
    """One round holding a single run_code call: (jobs, emitted events)."""
    call = {"id": "call-1", "type": "function",
            "function": {"name": "run_code", "arguments": json.dumps({"code": code, "description": "Write a"})}}

    async def go():
        jobs, events = None, []
        async for item in tool_runner.run_tools(
                [call], user_id=h.uid, allowed_urls=set(), request=request or _Connected(), completion_i=0, budgets={},
                persist=persist, execute_tool=execute_tool,
                session={"coding": True, "bound_host_id": h.host_id, "bound_cwd": h.root}):
            if isinstance(item, list):
                jobs = item
            else:
                events.append(item)
        return jobs, events

    return asyncio.run(go())


def test_an_edit_the_code_makes_is_checkpointed_as_it_starts(ssh_host, monkeypatch):
    scripted(monkeypatch, [("read_file", {"path": "b.txt"}), ("write_file", {"path": "a.txt", "content": "x"})])
    resumes = []

    def persist(jobs):
        if any(j.parent for j in jobs):
            resumes.append(tool_jobs._tool_messages(jobs)[1]["content"])

    one_round(ssh_host, persist=persist)

    # A restart while the write runs resumes knowing its outcome is unknown; a read is not saved as it starts.
    assert any("- write_file a.txt: outcome unknown" in text for text in resumes)
    assert "b.txt" not in resumes[0]


def test_a_checkpoint_that_fails_inside_run_code_ends_the_turn_instead_of_failing_the_tool(ssh_host, monkeypatch):
    scripted(monkeypatch, [("write_file", {"path": "a.txt", "content": "x"})])
    failed = []

    def persist(jobs):
        if any(j.parent for j in jobs) and not failed:
            failed.append(True)
            raise tool_runner.CheckpointFailed("Generation stopped: Fuse could not save its progress.")

    with pytest.raises(tool_runner.CheckpointFailed):
        one_round(ssh_host, persist=persist)


def test_a_failed_call_the_code_made_is_logged_as_one(ssh_host, monkeypatch):
    logged = []
    monkeypatch.setattr(tool_failures, "record", lambda **kw: logged.append((kw["tool_name"], kw["phase"])))
    scripted(monkeypatch, [("write_file", {"path": "a.txt", "content": "x"})])

    async def refused(name, arguments, **kw):
        return "no", {"name": name, "status": "error", "error": "no"}

    one_round(ssh_host, execute_tool=refused)

    assert logged == [("write_file", "code")]


def test_the_stored_row_reads_real_values_like_the_result(ssh_host, monkeypatch):
    secret_guard.add_manual(ssh_host.uid, "OPENAI_API_KEY", OPENAI)
    fake = next(it.fake for it in secret_guard.map_for(ssh_host.uid).items if it.real == OPENAI)
    scripted(monkeypatch, text=f"Error: key {fake} was refused", status="error", kind="code_error",
             error=f"key {fake} was refused")

    jobs, _ = one_round(ssh_host)

    head = jobs[0]
    assert OPENAI in head.text and OPENAI in head.trace["output"] and OPENAI in head.trace["error"]
    assert fake not in head.trace["output"] + head.trace["error"]


def test_a_long_result_keeps_its_closing_calls_lines_on_the_row(ssh_host, monkeypatch):
    tail = "\n[Fuse: 2 calls: read_file×2]"
    scripted(monkeypatch, text="x" * (chats.TOOL_OUTPUT_MAX + 500) + tail)

    jobs, _ = one_round(ssh_host)

    row = jobs[0].trace
    assert len(row["output"]) == chats.TOOL_OUTPUT_MAX and row["output"].endswith(tail) and row["truncated"]


def test_the_code_shows_on_the_row_while_it_runs_and_after_a_stop(ssh_host, monkeypatch):
    scripted(monkeypatch, [("run_terminal_command", {"command": "make"})])
    stopped = []

    class Stop:
        async def is_disconnected(self):
            return bool(stopped)

    async def hangs(name, arguments, **kw):
        stopped.append(True)
        await asyncio.Event().wait()

    jobs, events = one_round(ssh_host, execute_tool=hangs, request=Stop())

    running = next(e for e in events if e.get("id") == jobs[0].event_id and e.get("status") == "running")
    assert running["code"] == CODE
    assert jobs[0].trace["status"] == "interrupted" and jobs[0].trace["code"] == CODE


def test_a_read_only_subagent_is_told_it_cannot_write_not_to_load_the_tool(ssh_host):
    # run_code reaches the workspace tools whether or not they were loaded.
    for kind, name, said in (("plan", "run_terminal_command", "plan subagents cannot run shell or write files."),
                             ("explore", "write_file", "explore subagents cannot write files.")):
        text, trace = asyncio.run(chat_tools.execute_tool(
            name, json.dumps({"path": "a.txt", "content": "x", "command": "ls"}), user_id=ssh_host.uid,
            allowed_urls=set(), coding=True, loaded=["read_file"], session={"subagent_type": kind}))
        assert text == said and trace["kind"] == "denied"


def step(code, tool="run_terminal_command"):
    head = ToolJob(0, "c0", "run_code", "{}", "ts_0", trace={"status": "done", "code": code})
    return [head, ToolJob(0, "c0", tool, "{}", "ts_0_c1", parent="ts_0", trace={"status": "done"})]


def test_a_run_code_that_works_on_its_one_result_gets_no_wrap_tip():
    for code in ('return (await sh("npm test 2>&1")).slice(-2000);',
                 'const r = await run_terminal_command("make");\nreturn r.output.split("\\n").filter((l) => l.includes("x"));',
                 'return (await read_file("a.json")).length;',
                 'return JSON.parse(await read_file("a.json"));',
                 'const r = await run_terminal_command("make");\nreturn r.exit_code === 0;'):
        assert tool_jobs.wrap_tip(step(code)) == ((0, 0), ""), code
    # What sits in a string is the command, not work on its result.
    state, tip = tool_jobs.wrap_tip(step('return await sh("npm test 2>&1 | tee a.log");'))
    assert state == (1, 1) and "wrapped one run_terminal_command call" in tip


def test_the_wrap_tip_says_the_exit_code_is_hidden_only_when_the_code_returned_the_output():
    _, tip = tool_jobs.wrap_tip(step('const r = await run_terminal_command("ls");\nreturn r.output;'))
    assert "hides the command's exit code" in tip
    _, tip = tool_jobs.wrap_tip(step('return await run_terminal_command("ls");'))
    assert tip and "exit code" not in tip


def test_the_cut_note_says_how_long_the_rest_is_kept():
    assert f"(kept {code_mode.KEEP_S // 60} minutes)" in tool_jobs._code_clip("x" * 30_000, "r_1")
