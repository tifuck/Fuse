"""run_code: model-written JavaScript whose calls are real tool calls.

End to end: a real durable turn (provider faked at app.api.invoke_chat) on
the fake SSH host (in-memory files, scripted shell, nothing executed). The
code runs in the real worker process (a V8 isolate with no I/O); each call it
makes comes back to the turn's tool runner and reaches the fake host like a
direct call. Unit cases drive code_mode.run with a recording fake runner.
"""
import asyncio
import json
import re

from app import chat_prompts, chats, code_mode, hosts
from tests.core.helpers import fake_provider, run_async, run_turn, text_frames, tool_call_frames
from tests.core.tools._support import make_project

OPENAI = "sk-proj-" + "Q7v2Lp9X" * 6
GITHUB = "ghp_" + "R4m8Tz2Kc9" * 3 + "W1x5Yb"


def project_thread(h, *, swap=False):
    hosts.set_settings(h.uid, agent_coding=True)
    if swap:
        chats.set_user_context(h.uid, secret_swap=True)
    project = make_project(h.uid, h.host_id, h.root)
    return chats.create_thread(h.uid, model="offline", tools=[], kind="project", host_id=h.host_id, project_id=project)


def tool_results(body):
    return [m for m in body["messages"] if m.get("role") == "tool"]


def code_call(code, call_id="call-1"):
    return tool_call_frames([(call_id, "run_code", {"code": code, "description": "Check the services"})])


def test_the_code_reads_edits_and_runs_through_real_calls_and_only_its_result_reaches_the_model(ssh_host):
    fs = ssh_host.fs
    for name, timeout, retries in (("a", 30000, 3), ("b", 5000, 0), ("c", 45000, 3)):
        fs.write(f"{ssh_host.root}/services/{name}.json",
                 json.dumps({"timeout_ms": timeout, "retries": retries, "notes": "PADDING-MARKER " * 40}, indent=2))
    ssh_host.shell.on("make check", out="checks ok\n")
    code = """
const names = ["services/a.json", "services/b.json", "services/c.json"];
const rows = await Promise.all(names.map(async (p) => ({p, ...JSON.parse(await read_file(p))})));
const slow = rows.filter((r) => r.timeout_ms * (r.retries + 1) > 60000).map((r) => r.p);
for (const p of slow) await search_replace({path: p, old_string: '"retries": 3', new_string: '"retries": 1'});
const t = await run_terminal_command({command: "make check", description: "Run checks"});
return {slow, exit: t.exit_code, out: t.output.trim()};"""

    async def body():
        with fake_provider(code_call(code), text_frames("Lowered two retry counts.")) as provider:
            _, run = await run_turn(ssh_host.uid, "lower the slow retries", thread=project_thread(ssh_host))
        return run, provider.bodies

    run, bodies = run_async(body())

    assert run["status"] == "completed"
    # One call, one result: the code's return value, not the files it read.
    [result] = tool_results(bodies[1])
    assert result["tool_call_id"] == "call-1"
    returned = json.loads(result["content"].split("\n[Fuse:")[0])
    assert returned == {"slow": ["services/a.json", "services/c.json"], "exit": 0, "out": "checks ok"}
    assert "PADDING-MARKER" not in json.dumps(bodies[1]["messages"])
    # Each call really ran on the host.
    assert '"retries": 1' in fs.text(f"{ssh_host.root}/services/a.json")
    assert '"retries": 1' in fs.text(f"{ssh_host.root}/services/c.json")
    assert '"retries": 0' in fs.text(f"{ssh_host.root}/services/b.json")
    assert ssh_host.shell.reached == ["make check"]
    # The user sees the code row and each call under it.
    head, *calls = run["message"]["tools"]
    assert (head["name"], head["status"]) == ("run_code", "done")
    assert sorted(t["name"] for t in calls) == ["read_file"] * 3 + ["run_terminal_command"] + ["search_replace"] * 2
    assert {t.get("parent") for t in calls} == {head["id"]}


def test_code_only_ever_holds_stand_ins_while_the_machine_gets_real_values(ssh_host):
    command = f"./deploy.sh --key {OPENAI}"
    ssh_host.shell.on(command, out=f"deployed; ci token {GITHUB}\n")
    seen = {}

    def first(body):
        seen["key"] = re.search(r"sk-proj-[A-Za-z0-9_-]{48}", json.dumps(body["messages"])).group(0)
        return code_call(f'return (await run_terminal_command({{command: "./deploy.sh --key {seen["key"]}"}})).output;')

    async def body():
        with fake_provider(first, text_frames("deployed")) as provider:
            _, run = await run_turn(ssh_host.uid, f"deploy with OPENAI_API_KEY={OPENAI}", thread=project_thread(ssh_host, swap=True))
        return run, provider.bodies

    run, bodies = run_async(body())

    assert run["status"] == "completed" and seen["key"] != OPENAI
    assert ssh_host.shell.reached == [command]
    for request in bodies:
        assert OPENAI not in json.dumps(request["messages"]) and GITHUB not in json.dumps(request["messages"])
    [result] = tool_results(bodies[1])
    assert "deployed; ci token ghp_" in result["content"]


def test_failed_calls_the_code_catches_do_not_count_toward_the_stall_stop(ssh_host):
    code = """
let missing = 0;
for (let i = 0; i < 25; i++) { try { await read_file(`nope${i}.txt`); } catch (e) { missing++; } }
return `missing ${missing}`;"""

    async def body():
        with fake_provider(code_call(code), text_frames("none of them exist")) as provider:
            _, run = await run_turn(ssh_host.uid, "check the files", thread=project_thread(ssh_host))
        return run, provider.bodies

    run, bodies = run_async(body())

    assert run["status"] == "completed" and run["message"]["content"] == "none of them exist"
    assert tool_results(bodies[1])[0]["content"].startswith("missing 25")
    assert bodies[1].get("tools"), "the stall stop would have dropped the tools"


class Runner:
    """Stand-in for the tool runner's call(name, args): records and answers."""

    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer or (lambda name, args: ("From host box:\nok", {"name": name, "status": "done", "host": "box"}))

    async def __call__(self, name, args):
        self.calls.append((name, args))
        return self.answer(name, args)


def run_code(code, runner, **extra):
    return asyncio.run(code_mode.run(json.dumps({"code": code, **extra}), call=runner))


def test_a_failed_call_throws_into_the_code_and_an_uncaught_one_lists_what_finished():
    def answer(name, args):
        if args.get("path") == "missing.txt":
            return "From host box:\nfile not found", {"name": name, "status": "error", "host": "box"}
        return "From host box:\nsaved", {"name": name, "status": "done", "host": "box"}

    text, trace = run_code("try { await read_file('missing.txt'); } catch (e) { return 'caught: ' + e.message; }", Runner(answer))
    assert trace["status"] == "done" and text.startswith("caught: file not found")

    text, trace = run_code("await write_file({path: 'out.txt', content: 'x'});\nawait read_file('missing.txt');", Runner(answer))
    assert trace["status"] == "error" and trace["kind"] == "code_error"
    assert text.startswith("Error: file not found") and "line 2" in text
    assert "- write_file out.txt: done" in text and "- read_file missing.txt: error" in text


def test_only_the_workspace_helpers_reach_the_runner():
    runner = Runner()

    text, _ = run_code("await monitor({command: 'tail -f log'});", runner)
    assert "monitor is not defined" in text and "run_code provides only" in text
    text, _ = run_code("await run_terminal_command({command: 'make', background: true});", runner)
    assert "call run_terminal_command directly" in text
    assert runner.calls == []


def test_a_script_that_never_yields_is_stopped_without_holding_the_event_loop():
    async def body():
        turns = 0
        stop = asyncio.Event()

        async def other_work():
            nonlocal turns
            while not stop.is_set():
                turns += 1
                await asyncio.sleep(0)

        other = asyncio.ensure_future(other_work())
        # The 1 s minimum timeout is the point of the test.
        text, trace = await code_mode.run(json.dumps({"code": "while (true) {}", "timeout": 1}), call=Runner())
        stop.set()
        await other
        return text, trace, turns

    text, trace, turns = asyncio.run(body())
    assert trace["kind"] == "timeout" and "timed out" in text
    assert turns > 100, "the server's event loop stalled while the script spun"


def test_the_coding_prompt_leads_with_run_code_only_when_it_is_available():
    base = dict(instructions="", profile="", thread_context="", tools=[], coding=True, project_session=True,
                bound_label="box", loaded_tools=["read_file"])
    on = chat_prompts.build_system_prompt(**base, code_mode=True)
    off = chat_prompts.build_system_prompt(**base, code_mode=False)
    assert "Use `run_code` by default" in on and "inside `run_code`" in on
    assert "run_code" not in off
