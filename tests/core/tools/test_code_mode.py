"""run_code: model-written JavaScript whose calls are real tool calls.

End to end: a real durable turn (provider faked at app.api.invoke_chat) on
the fake SSH host (in-memory files, scripted shell, nothing executed). The
code runs in the real worker process (a V8 isolate with no I/O); each call it
makes comes back to the turn's tool runner and reaches the fake host like a
direct call. Round cases run one tool round through tool_runner.run_tools
with the real tools on the fake host. Unit cases drive code_mode.run with a
recording fake runner.
"""
import asyncio
import collections
import json
import re

import pytest

from app import agent_catalog, chat_history, chat_output, chat_prompts, chat_tools, chats, code_mode, hosts, workspace
from app.chat_generation import tool_jobs, tool_runner
from app.chat_generation.tool_jobs import ToolJob
from tests.core.helpers import fake_provider, run_async, run_turn, text_frames, tool_call_frames
from tests.core.tools._support import make_project, new_thread

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


def run_code(code, runner, owner=(), **extra):
    return asyncio.run(code_mode.run(json.dumps({"code": code, "description": "Run the test", **extra}), call=runner, owner=owner))


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


def test_the_row_keeps_the_code_apart_from_what_the_model_got():
    text, trace = run_code("return 1 + 1;", Runner(), description="Add")

    assert trace["output"] == text and "return 1 + 1;" not in text
    assert chats._parse_tool_traces([trace], strict=True)[0]["code"] == "return 1 + 1;"


def test_code_without_a_description_does_not_run():
    runner = Runner()

    for extra in ({}, {"description": "  "}):
        text, trace = asyncio.run(code_mode.run(json.dumps({"code": "await read_file('a');", **extra}), call=runner))
        assert trace["kind"] == "invalid_arguments" and "needs a description" in text
    assert runner.calls == []


def test_only_the_workspace_helpers_reach_the_runner():
    runner = Runner()

    text, _ = run_code("await monitor({command: 'tail -f log'});", runner)
    assert "monitor is not defined" in text and "run_code provides only" in text
    text, _ = run_code("await run_terminal_command({command: 'make', background: true});", runner)
    assert "call run_terminal_command directly" in text
    assert runner.calls == []


def test_a_helper_the_code_shadowed_gets_its_own_fix_not_the_await_hint():
    text, _ = run_code("const { grep } = globalThis;\nreturn await grep('TODO');", Runner())

    assert "grep is not a function" in text and "grep is already defined in run_code" in text
    assert "Each helper is async" not in text
    text, _ = run_code("return (await read_file('a')).lines();", Runner())
    assert "Each helper is async" in text


def test_write_file_takes_a_path_and_content_as_well_as_an_object():
    runner = Runner()

    text, trace = run_code("await write_file('out.txt', 'hi');\nawait write_file({path: 'b.txt', content: ''});\nreturn 'ok';", runner)

    assert trace["status"] == "done" and text.startswith("ok")
    assert runner.calls == [("write_file", {"path": "out.txt", "content": "hi"}), ("write_file", {"path": "b.txt", "content": ""})]


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
        text, trace = await code_mode.run(json.dumps({"code": "while (true) {}", "description": "Spin", "timeout": 1}), call=Runner())
        stop.set()
        await other
        return text, trace, turns

    text, trace, turns = asyncio.run(body())
    assert trace["kind"] == "timeout" and "timed out" in text
    assert turns > 100, "the server's event loop stalled while the script spun"


def test_the_coding_prompt_names_run_code_only_when_it_is_available():
    base = dict(instructions="", profile="", thread_context="", tools=[], coding=True, project_session=True,
                bound_label="box", loaded_tools=["read_file"])
    assert "run_code" in chat_prompts.build_system_prompt(**base, code_mode=True)
    assert "run_code" not in chat_prompts.build_system_prompt(**base, code_mode=False)


class _Connected:
    async def is_disconnected(self):
        return False


def one_round(h, code, **session):
    """One tool round holding a single run_code call, through the real runner
    and tools on the fake host: (the model's result, jobs, emitted events)."""
    call = {"id": "call-1", "type": "function", "function": {"name": "run_code", "arguments": json.dumps({"code": code, "description": "Run the test"})}}
    make_project(h.uid, h.host_id, h.root)
    _, thread_id = new_thread(h.uid)

    async def go():
        jobs, events = None, []
        async for item in tool_runner.run_tools(
                [call], user_id=h.uid, allowed_urls=set(), request=_Connected(), completion_i=0, budgets={},
                thread_id=thread_id,
                loaded=list(agent_catalog.WORKSPACE_NAMES), execute_tool=chat_tools.execute_tool,
                session={"coding": True, "bound_host_id": h.host_id, "bound_cwd": h.root, "run_id": "", **session}):
            if isinstance(item, list):
                jobs = item
            else:
                events.append(item)
        return jobs, events

    jobs, events = asyncio.run(go())
    return tool_jobs._tool_messages(jobs)[1]["content"], jobs, events


def test_reads_from_code_are_raw_windows_or_whole_files_up_to_the_cap(ssh_host, monkeypatch):
    monkeypatch.setattr(workspace, "_RAW_READ_MAX", 50_000)
    ssh_host.fs.write(f"{ssh_host.root}/big.txt", "".join(f"line {i}\n" for i in range(1, 3001)))
    ssh_host.fs.write(f"{ssh_host.root}/huge.txt", "x" * 79 + "\n" * 1 + ("y" * 79 + "\n") * 800)
    text, _, _ = one_round(ssh_host, """
const whole = await read_file("big.txt");
const part = await read_file({path: "big.txt", offset: 2, limit: 2});
let huge = "read";
try { await read_file("huge.txt"); } catch (e) { huge = e.message; }
return JSON.stringify({count: whole.trimEnd().split("\\n").length, last: whole.trimEnd().split("\\n").pop(), part, huge});""")

    got = json.loads(text.split("\n[Fuse:")[0])
    assert got["count"] == 3000 and got["last"] == "line 3000"
    assert got["part"] == "line 2\nline 3\n"
    assert "larger than 50000 characters" in got["huge"]


def test_calls_from_code_keep_the_agent_roles_limits(ssh_host):
    text, jobs, _ = one_round(ssh_host, 'try { await write_file({path: "x.txt", content: "x"}); return "wrote"; } '
                                        'catch (e) { return e.message; }', subagent_type="explore")

    assert text.startswith("explore subagents cannot write files")
    assert not ssh_host.fs.exists(f"{ssh_host.root}/x.txt")
    assert [j.name for j in jobs] == ["run_code", "write_file"] and jobs[1].parent == jobs[0].event_id


def test_a_long_run_shows_its_first_reads_and_keeps_no_copy_of_their_text(ssh_host):
    ssh_host.fs.write(f"{ssh_host.root}/a.txt", "alpha\n" * 10)
    text, jobs, events = one_round(ssh_host, """
let n = 0;
for (let i = 0; i < 45; i++) n += (await read_file("a.txt")).length;
await write_file({path: "total.txt", content: String(n)});
return n;""")

    assert text.startswith("2700") and ssh_host.fs.text(f"{ssh_host.root}/total.txt") == "2700"
    rows = {e["id"]: e["name"] for e in events if e.get("event") == "tool" and e.get("parent")}
    assert list(rows.values()).count("read_file") == tool_runner._CHILD_READ_ROWS and "write_file" in rows.values()
    assert len(jobs) == 1 + 45 + 1 and all(j.text == "" for j in jobs[1:])


def test_oversized_arguments_and_calls_past_the_cap_never_reach_the_runner():
    runner = Runner()
    text, _ = run_code('try { await write_file({path: "big", content: "x".repeat(400001)}); } catch (e) { return e.message; }', runner)
    assert "over 400000 characters" in text and runner.calls == []

    text, _ = run_code("let ok = 0;\nfor (let i = 0; i < 501; i++) { try { await read_file(`f${i}`); ok++; } "
                       "catch (e) { return `${ok} then ${e.message}`; } }", runner)
    assert text.startswith("500 then run_code allows at most 500 tool calls") and len(runner.calls) == 500


def test_the_worker_arg_limit_matches_a_direct_calls():
    from app.chat_generation.parsing import _ARG_LIMIT_FILE
    from app.code_worker import ARG_CHARS

    assert ARG_CHARS == _ARG_LIMIT_FILE


def test_a_failure_that_escapes_the_runner_stops_the_code_and_propagates():
    def answer(name, args):
        raise RuntimeError("checkpoint failed")

    runner = Runner(answer)
    with pytest.raises(RuntimeError, match="checkpoint failed"):
        run_code("await write_file({path: 'a', content: 'x'});\nawait write_file({path: 'b', content: 'y'});", runner)
    assert [args["path"] for _, args in runner.calls] == ["a"]


def test_a_run_waits_only_so_long_for_a_free_worker(monkeypatch):
    monkeypatch.setattr(code_mode, "WORKERS", 0)
    monkeypatch.setattr(code_mode, "SLOT_WAIT_S", 0.01)
    monkeypatch.setattr(code_mode, "_slots", None)

    text, trace = run_code("return 1;", Runner())
    assert trace["kind"] == "busy" and "busy" in text


def test_time_spent_on_a_permission_prompt_does_not_count_toward_the_deadline():
    async def body():
        prompt_open = asyncio.Event()

        async def call(name, args):
            prompt_open.set()
            # The user takes longer than the run's whole timeout to answer.
            await asyncio.sleep(1.5)
            prompt_open.clear()
            return "From host box:\napproved", {"name": name, "status": "done", "host": "box"}

        # The 1 s minimum timeout against a 1.5 s prompt is the point of the test.
        return await code_mode.run(json.dumps({"code": "return await read_file('a');", "description": "Read a", "timeout": 1}),
                                   call=call, paused=prompt_open.is_set)

    text, trace = asyncio.run(body())
    assert trace["status"] == "done" and text.startswith("approved")


def test_a_run_cut_off_mid_way_tells_the_model_which_changes_happened():
    head = ToolJob(0, "call-1", "run_code", json.dumps({"code": "..."}), "ts_0_0")
    wrote = ToolJob(0, "call-1", "write_file", json.dumps({"path": "a.txt"}), "ts_0_0_c1", parent="ts_0_0", done=True,
                    trace={"status": "done"})
    running = ToolJob(0, "call-1", "run_terminal_command", json.dumps({"command": "make deploy"}), "ts_0_0_c2", parent="ts_0_0")
    read = ToolJob(0, "call-1", "read_file", json.dumps({"path": "b.txt"}), "ts_0_0_c3", parent="ts_0_0", done=True,
                   trace={"status": "done"})

    assistant, result = tool_jobs._tool_messages([head, wrote, running, read])

    assert [c["function"]["name"] for c in assistant["tool_calls"]] == ["run_code"]
    assert result["content"].startswith("Tool interrupted")
    assert "- write_file a.txt: done" in result["content"] and "- run_terminal_command make deploy: outcome unknown" in result["content"]
    assert "b.txt" not in result["content"]


def test_the_run_code_tip_names_the_direct_calls_once_and_never_after_a_run_code_that_worked():
    def jobs(*names, status="done"):
        return [ToolJob(0, f"c{i}", name, "{}", f"ts_{i}", trace={"status": status}) for i, name in enumerate(names)]

    seen = tool_jobs.code_tip_count(jobs("read_file", "read_file", "grep", "web_search"), collections.Counter())
    assert seen == collections.Counter(read_file=2, grep=1)
    # A model that gives up on code after a failed run still gets the tip.
    assert tool_jobs.code_tip_count(jobs("run_code", status="error"), seen) == seen
    assert tool_jobs.code_tip_count(jobs("run_code", "read_file"), seen) is None
    msgs = [{"role": "assistant"}, {"role": "tool", "content": "ok"}]
    tool_jobs.add_code_tip(msgs, seen)
    assert "3 direct file calls this turn (2 read_file, 1 grep)" in msgs[1]["content"]


def test_recovered_history_replays_run_code_but_not_the_calls_it_made():
    message = {"id": "m1", "tools": [{"id": "ts_0_0", "name": "run_code", "status": "done", "output": "42"},
                                      {"id": "ts_0_0_c1", "name": "read_file", "status": "done", "path": "a", "parent": "ts_0_0"}]}

    rows = chat_history._trace_rows(message)
    assert [r["tool_calls"][0]["function"]["name"] for r in rows if r["role"] == "assistant"] == ["run_code"]


def test_a_call_run_code_made_is_placed_under_it_even_when_another_call_ran_meanwhile():
    out = chat_output.Output({"message": {}})
    for event in ({"id": "ts_0_0", "name": "run_code"}, {"id": "ts_0_0_c1", "name": "read_file", "parent": "ts_0_0"},
                  {"id": "ts_0_1", "name": "web_search"}, {"id": "ts_0_0_c2", "name": "grep", "parent": "ts_0_0"}):
        out.apply({"fuse": {"event": "tool", "status": "running", **event}})

    assert [part["id"] for part in out.message["segments"] if part["type"] == "tool"] == ["ts_0_0", "ts_0_0_c1", "ts_0_0_c2", "ts_0_1"]


def test_a_shell_variable_left_in_a_template_string_gets_the_template_hint_not_the_helpers_hint():
    text, _ = run_code("return await run_terminal_command(`grep -n x ${sel}`);", Runner())
    assert "sel is not defined" in text and "write \\${sel}" in text and "run_code provides only" not in text

    text, _ = run_code("return await monitor({command: 'x'});", Runner())
    assert "run_code provides only" in text and "backtick" not in text


def test_a_cut_run_code_result_says_the_calls_already_ran():
    head = ToolJob(0, "call-1", "run_code", "{}", "ts_0_0", text="x" * 30_000, done=True, trace={"status": "done"})

    _, result = tool_jobs._tool_messages([head])

    assert len(result["content"]) <= tool_jobs.TOOL_RESULT_CHARS
    assert "of 30000 characters" in result["content"] and "do not run its edits or commands again" in result["content"]


def test_a_cut_result_is_kept_for_its_chat_and_a_later_run_reads_the_rest(ssh_host):
    text, jobs, _ = one_round(ssh_host, 'return Array.from({length: 4000}, (_, i) => `row ${i}`).join("\\n");')
    head = jobs[0]
    assert head.result_id and f'read_result("{head.result_id}"' in text and len(text) <= tool_jobs.TOOL_RESULT_CHARS

    owner = code_mode._kept[head.result_id][1]
    assert owner[0] == ssh_host.uid
    rest, _ = run_code(f'return await read_result("{head.result_id}", {{offset: 30000, limit: 9}});', Runner(), owner=owner)
    assert rest.startswith(head.text[30000:30009])

    other, _ = run_code(f'try {{ return await read_result("{head.result_id}"); }} catch (e) {{ return e.message; }}',
                        Runner(), owner=(ssh_host.uid, -1))
    assert other.startswith(f"no kept result {head.result_id}")


def test_kept_results_expire_and_the_oldest_go_first_past_the_cap(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(code_mode, "_clock", lambda: now[0])
    monkeypatch.setattr(code_mode, "_kept", collections.OrderedDict())
    monkeypatch.setattr(code_mode, "_kept_chars", 0)
    monkeypatch.setattr(code_mode, "KEEP_CHARS", 10)
    first = code_mode.keep((1, 1), "aaaaaa")
    second = code_mode.keep((1, 1), "bbbbbb")
    assert first not in code_mode._kept and second in code_mode._kept

    now[0] += code_mode.KEEP_S
    assert code_mode._read_kept((1, 1), json.dumps({"id": second}))["ok"] is False
    assert code_mode._kept_chars == 0


def test_a_cut_result_keeps_its_calls_line():
    text = "x" * 30_000 + "\n[Fuse: 3 calls: read_file×3]"
    head = ToolJob(0, "call-1", "run_code", "{}", "ts_0_0", text=text, done=True, trace={"status": "done"})

    _, result = tool_jobs._tool_messages([head])

    assert len(result["content"]) <= tool_jobs.TOOL_RESULT_CHARS
    assert result["content"].endswith("\n[Fuse: 3 calls: read_file×3]") and "result cut at" in result["content"]


def test_a_run_that_only_reads_a_kept_result_counts_that_read(monkeypatch):
    monkeypatch.setattr(code_mode, "_kept", collections.OrderedDict())
    monkeypatch.setattr(code_mode, "_kept_chars", 0)
    ident = code_mode.keep((1, 1), "abcdef")

    text, _ = run_code(f'return await read_result("{ident}", {{offset: 2, limit: 2}});', Runner(), owner=(1, 1))

    assert text == "cd\n[Fuse: 1 calls: read_result×1]"
