"""run_code as a REPL: a chat's kernel keeps its names, a run's value is its
last expression, the $ / edit / file helpers, web and subagent helpers, and a
run that moves to the background.

Unit cases drive code_mode.run with a recording fake runner (the real V8
worker runs the code, nothing reaches a host); several runs of one chat go
in one event loop, as a chat's turns do in the server.
"""
import asyncio
import json

from app import chat_tools, code_mode, command_jobs
from app.chat_generation import tool_runner
from tests.core.tools.test_code_mode import Runner


def command_answer(code, out="out"):
    def answer(name, args):
        if name == "run_terminal_command":
            return f"status: done\nexit_code: {code}\n\n{out}", {"name": name, "status": "done", "exit_code": code}
        return "From host box:\nok", {"name": name, "status": "done", "host": "box"}
    return answer


def runs(*codes, runner=None, kernel=("u", 1, ""), **extra):
    """Each code as one run_code call of one chat, in order: their texts."""
    runner = runner or Runner()

    async def go():
        out = []
        for code in codes:
            text, _ = await code_mode.run(json.dumps({"code": code, "description": "Test", **extra}), call=runner,
                                          kernel=kernel, shells={"": "bash", "win": "powershell"})
            out.append(text.split("\n[Fuse: ")[0])
        return out

    return asyncio.run(go())


def test_a_chat_keeps_its_top_level_names_from_run_to_run():
    out = runs("const n = 40; let {a, b: [c]} = {a: 1, b: [1]}; function inc(x) { return x + 1 }\n"
               "class K { v() { return 0 } }",
               "inc(n) + a + c + new K().v()")
    assert out[1] == "43"


def test_another_chat_and_a_run_without_a_chat_start_with_no_names():
    async def go():
        made = []
        for kernel in (("u", 1, ""), ("u", 2, ""), None):
            text, _ = await code_mode.run(json.dumps({"code": "globalThis.seen = (globalThis.seen || 0) + 1; seen",
                                                      "description": "Count"}), call=Runner(), kernel=kernel)
            made.append(text.split("\n")[0])
        return made

    assert asyncio.run(go()) == ["1", "1", "1"]


def test_a_restarted_repl_is_announced_once():
    async def go():
        out = []
        for i, code in enumerate(("const x = 1; x", "typeof x", "typeof x")):
            if i == 1:
                code_mode._pool[("u", 1, "")].kill("a test stopped it")
            text, _ = await code_mode.run(json.dumps({"code": code, "description": "T"}), call=Runner(), kernel=("u", 1, ""))
            out.append(text)
        return out

    first, second, third = asyncio.run(go())
    assert "restarted (a test stopped it)" in second and second.split("\n")[1] == "undefined"
    assert "restarted" not in third


def test_the_last_expression_is_the_value_and_promises_in_it_are_awaited():
    out = runs("const t = await read_file('a');\nt.length", "({n: Promise.resolve(2), s: $`echo hi`})",
               runner=Runner(command_answer(0, "hi")))
    assert out[0] == "2"
    assert json.loads(out[1]) == {"n": 2, "s": "hi"}


def test_dollar_quotes_each_value_for_the_shell_it_reaches():
    runner = Runner(command_answer(0))
    runs("const s = \"it's; rm -rf /\";\n"
         "await $`echo ${s} ${['a b', 2]} ${$.raw('| wc -l')} ${{raw: '; id'}}`;\n"
         "await $`echo ${s}`.host('win').cwd('web');\n"
         "await $('ls -la', {timeout: 5000})", runner=runner)
    assert runner.calls == [
        # Only $.raw is spliced as written; an object that has a raw key is just a value.
        ("run_terminal_command", {"command": "echo 'it'\\''s; rm -rf /' 'a b' '2' | wc -l '[object Object]'"}),
        ("run_terminal_command", {"host": "win", "cwd": "web", "command": "echo 'it''s; rm -rf /'"}),
        ("run_terminal_command", {"timeout": 5000, "command": "ls -la"}),
    ]


def test_a_value_inside_quotes_of_the_template_is_escaped_for_those_quotes():
    runner = Runner(command_answer(0))
    runs("const x = 'a\"$(id)`b\\\\';\n"
         "await $`grep \"${x}\" f`;\n"
         "await $`grep '${\"it's\"}' f`;\n"
         "await $`echo \"${x}\"`.host('win');\n"
         "await $`echo ${'a\u2019; calc; \u2018'}`.host('win')", runner=runner)
    assert [args["command"] for _, args in runner.calls] == [
        'grep "a\\"\\$(id)\\`b\\\\" f',
        "grep 'it'\\''s' f",
        'echo "a`"`$(id)``b\\"',
        "echo 'a\u2019\u2019; calc; \u2018\u2018'",
    ]


def test_exit_1_from_a_search_is_no_failure_but_any_other_throws_unless_nothrow():
    runner = Runner(command_answer(1, "nothing"))
    out = runs("const r = await $`grep -rn x src`; [r.exitCode, r.note.includes('no match')]",
               "(await sh('git diff --quiet'))",
               "let a;\ntry { await sh('make && grep -q x f'); a = 'passed' } catch (e) { a = 'threw' }\na",
               "let m;\ntry { await $`make`; m = 'ran' } catch (e) { m = e.message.split('\\n')[0] }\nm",
               "(await $`make`.nothrow()).exitCode",
               runner=runner)
    assert json.loads(out[0]) == [1, True]
    assert out[1] == "nothing"
    # After && the exit 1 may be make's.
    assert out[2] == "threw"
    assert out[3] == "sh make: exited 1"
    assert out[4] == "1"


def test_edit_write_and_file_are_the_file_tools_underneath():
    def answer(name, args):
        if name == "search_replace":
            return "From host box:\nupdated a.py (3 replacements)", {"name": name, "status": "done", "host": "box"}
        if name == "read_file" and args.get("path") == "gone.txt":
            return "no such file: gone.txt", {"name": name, "status": "error", "host": "box"}
        return "From host box:\nl1\nl2\n", {"name": name, "status": "done", "host": "box"}

    runner = Runner(answer)
    out = runs("JSON.stringify({e: await edit('a.py', 'x', 'y', {all: true}), w: await write('b.txt', 'é'), "
               "l: await file('c.txt').lines(), s: await file('c.txt').slice(2, 1), x: await file('gone.txt').exists(), "
               "soft: await edit('a.py', [['p', 'q']], {soft: true})})", runner=runner)
    got = json.loads(out[0])
    assert got["e"] == {"ok": True, "replacements": 3} and got["w"] == 2 and got["l"] == ["l1", "l2"] and not got["x"]
    assert runner.calls[:2] == [("search_replace", {"path": "a.py", "old_string": "x", "new_string": "y", "replace_all": True}),
                                ("write_file", {"path": "b.txt", "content": "é"})]
    assert ("read_file", {"path": "c.txt", "offset": 2, "limit": 1}) in runner.calls
    assert ("search_replace", {"path": "a.py", "edits": [{"old_string": "p", "new_string": "q", "replace_all": False}]}) \
        in runner.calls


def test_web_results_reach_the_code_and_mark_the_value_untrusted():
    runner = Runner(lambda name, args: ("1. A page https://x.test", {"name": name, "status": "done"}))
    text, trace = asyncio.run(code_mode.run(json.dumps({
        "code": "const r = await web_search({queries: ['a']});\nconst p = await read_url('https://x.test');\nr.length > 0",
        "description": "Search"}), call=runner))
    assert trace["status"] == "done" and text.startswith("[Fuse: this value may hold text from the web")
    assert [name for name, _ in runner.calls] == ["web_search", "read_url"]


def test_a_later_run_of_a_repl_that_read_the_web_is_marked_too():
    runner = Runner(lambda name, args: ("1. A page https://x.test", {"name": name, "status": "done"}))
    out = runs("page = await read_url('https://x.test'); 1", "page", runner=runner)
    assert out[1].startswith("[Fuse: this value may hold text from the web")


def test_code_a_run_left_going_cannot_call_tools_once_the_run_ended():
    runner = Runner()
    runs("(async () => { for (let i = 0; i < 50; i++) { try { await sleep(20); await read_file('late'); } catch (e) {} } })(); 'left'",
         "await sleep(400); 'later'", runner=runner)
    # The leftover loop's calls either went with the run still going or failed in the worker: none after it.
    assert runner.calls.count(("read_file", {"path": "late"})) <= 20


def test_an_account_past_its_share_of_kernels_makes_room_among_its_own(monkeypatch):
    monkeypatch.setattr(code_mode, "KERNELS_PER_USER", 1)

    async def go():
        out = []
        for key in (("u", 1, ""), ("u", 2, ""), ("u", 1, "")):
            text, _ = await code_mode.run(json.dumps({"code": "globalThis.n = (globalThis.n || 0) + 1; n",
                                                      "description": "Count"}), call=Runner(), kernel=key)
            out.append(text)
        return out

    first, second, third = asyncio.run(go())
    assert first.startswith("1") and second.startswith("1")
    assert "restarted (other chats needed the server's REPL slots)" in third and "\n1\n" in third


def test_a_turn_stopped_while_the_run_moves_to_the_background_leaves_no_run_behind():
    moves = Moves()
    held = asyncio.Event()

    async def slow(name, args):
        held.set()
        await asyncio.sleep(30)
        return "ok", {"name": name, "status": "done"}

    async def go():
        task = asyncio.ensure_future(code_mode.run(json.dumps({
            "code": "await read_file('a'); 1", "description": "Slow", "timeout": 1000}),
            call=slow, kernel=("u", 1, ""), background=moves.background))
        await held.wait()
        await asyncio.sleep(1.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        kernel = code_mode._pool.get(("u", 1, ""))
        return kernel is None or not kernel.active(), moves.jobs

    idle, jobs = asyncio.run(go())
    assert idle and jobs == {}


class Moves:
    """A stand-in for tool_runner's Background: records calls and jobs."""

    def __init__(self):
        self.calls, self.jobs = [], {}
        self.background = code_mode.Background(call=self.call, launch=self.launch, limit_ms=60_000)

    async def call(self, name, args):
        self.calls.append((name, args))
        return "From host box:\nlater", {"name": name, "status": "done", "host": "box"}

    async def launch(self, description, finish):
        self.jobs["j1"] = asyncio.ensure_future(finish())
        return "j1"


def test_a_run_still_going_at_its_deadline_moves_to_the_background_and_finishes_there():
    moves, runner = Moves(), Runner()

    async def go():
        text, trace = await code_mode.run(json.dumps({
            "code": "await read_file('a');\nawait sleep(1500);\nawait read_file('b');\nawait spawn_subagent('x').catch((e) => e.message)",
            "description": "Slow", "timeout": 1000}), call=runner, kernel=("u", 1, ""), background=moves.background)
        return text, trace, await moves.jobs["j1"]

    text, trace, (result, ok) = asyncio.run(go())
    assert trace["background"] and trace["job_id"] == "j1" and "moved to the background as job j1 after 1 s" in text
    assert runner.calls == [("read_file", {"path": "a"})] and moves.calls == [("read_file", {"path": "b"})]
    assert ok and result.startswith("spawn_subagent is not available once run_code moved to the background")


def test_background_true_starts_there_and_a_run_without_a_chat_stops_at_its_deadline_instead():
    moves = Moves()

    async def go():
        started, _ = await code_mode.run(json.dumps({"code": "await read_file('a'); 'done'", "description": "Bg",
                                                     "background": True}),
                                         call=Runner(), kernel=("u", 1, ""), background=moves.background)
        stopped, trace = await code_mode.run(json.dumps({"code": "await sleep(3000); 1", "description": "Slow",
                                                         "timeout": 1000}),
                                             call=Runner(), background=moves.background)
        return started, await moves.jobs["j1"], stopped, trace

    started, (result, ok), stopped, trace = asyncio.run(go())
    assert "as job j1:" in started and moves.calls == [("read_file", {"path": "a"})] and ok and result.startswith("done")
    assert trace["kind"] == "timeout" and stopped.startswith("Error: run_code timed out")


def test_a_code_job_reports_its_whole_result_and_its_own_name():
    rec = {"id": "j1", "kind": "code", "status": "completed", "host": "", "command": "run_code: Slow", "exit_code": 0,
           "duration_ms": 5, "out": "head line\n" + "x" * 3000 + "\ntail line", "out_chars": 3020}
    note = command_jobs.notification(rec)
    assert "head line" in note and "tail line" in note


def test_a_run_code_helper_called_as_a_tool_is_told_to_call_it_in_code(monkeypatch):
    monkeypatch.setattr(code_mode, "available", lambda: True)
    text, trace = chat_tools.unknown_tool_reply("read_result", '{"id": "r_1"}', coding=True)
    assert "read_result is a function inside run_code's code, not a tool" in text and trace["kind"] == "unknown_tool"


def test_calls_a_run_makes_in_the_background_cannot_ask_for_permission(ssh_host, monkeypatch):
    from tests.core.tools.test_code_mode_runner import _Connected

    monkeypatch.setattr(code_mode, "available", lambda: True)
    seen = []

    async def execute(name, arguments, **kw):
        seen.append((name, kw["gate"].unattended if kw.get("gate") else None))
        return "From host box:\nok", {"name": name, "status": "done", "host": "box"}

    call = {"id": "call-1", "type": "function", "function": {"name": "run_code", "arguments": json.dumps({
        "code": "await read_file('a');\nawait sleep(1500);\nawait read_file('b');\n'fin'", "description": "Slow",
        "timeout": 1000})}}

    async def go():
        events = []
        async for item in tool_runner.run_tools(
                [call], user_id=ssh_host.uid, allowed_urls=set(), request=_Connected(), completion_i=0, budgets={},
                thread_id=1, execute_tool=execute,
                session={"coding": True, "bound_host_id": ssh_host.host_id, "bound_cwd": ssh_host.root}):
            events.append(item)
        jobs = events[-1]
        job = command_jobs._live[jobs[0].trace["job_id"]]
        await job["future"]
        return jobs, job

    jobs, job = asyncio.run(go())
    assert jobs[0].trace["background"] and job["kind"] == "code" and job["exit_code"] == 0 and "fin" in job["out"]
    # The first read ran in the round, gated as usual; the second after the move, unattended.
    assert seen == [("read_file", False), ("read_file", True)]
