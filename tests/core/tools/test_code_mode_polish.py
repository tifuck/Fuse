"""run_code polish: helper call forms, readable values and errors, the gate
inside a run, and how a run that went wrong is reported.

Unit cases drive code_mode.run with a recording fake runner (the real V8
worker runs the code), as tests/core/tools/test_code_mode.py does.
"""
import json
import threading

import pytest

from app import code_mode
from tests.core.tools.test_code_mode import Runner, command_answer, run_code


def value_of(text):
    return text.split("\n[Fuse:")[0]


def test_a_string_first_argument_keeps_the_options_after_it():
    runner = Runner(command_answer(0, "ok"))

    text, trace = run_code("await grep('x', {path: 'src'});\nawait read_file('a.py', {limit: 50});\n"
                           "await run_terminal_command('make', {cwd: 'web', host: 'blk'});\n"
                           "await write_file('a.txt', 'hi');\nreturn 'ok';", runner)

    assert trace["status"] == "done", text
    assert runner.calls == [("grep", {"pattern": "x", "path": "src"}), ("read_file", {"path": "a.py", "limit": 50}),
                            ("run_terminal_command", {"command": "make", "cwd": "web", "host": "blk"}),
                            ("write_file", {"path": "a.txt", "content": "hi"})]


def test_errors_maps_sets_and_bigints_read_as_text_in_the_value_and_the_console():
    text, trace = run_code("console.log(undefined, new Map([['k', new Set([1])]]), new TypeError('bad'), 10n);\n"
                           "return [new Error('boom'), new Map([['a', 1]]), new Set([1, 2]), 10n];", Runner())

    value, logs = value_of(text).split("\n[console]\n")
    assert trace["status"] == "done"
    assert json.loads(value) == ["Error: boom", {"a": 1}, [1, 2], "10"]
    assert logs == 'undefined {"k":[1]} TypeError: bad 10'


def test_an_object_holding_multi_line_text_comes_back_readable():
    text, _ = run_code("return {file: 'a.py', diff: '-x\\n+y', n: 2, tags: ['a\\nb']};", Runner())
    assert value_of(text) == 'file: "a.py"\n--- diff ---\n-x\n+y\nn: 2\ntags: ["a\\nb"]'

    # Arrays, and objects without multi-line text, stay JSON.
    text, _ = run_code("return [{diff: '-x\\n+y'}];", Runner())
    assert json.loads(value_of(text)) == [{"diff": "-x\n+y"}]
    text, _ = run_code("return {a: 'x', b: 1};", Runner())
    assert value_of(text) == '{\n  "a": "x",\n  "b": 1\n}'


def test_a_change_sent_together_with_one_that_failed_on_its_host_does_not_run():
    def answer(name, args):
        if name == "search_replace":
            return "From host box:\nold_string not found", {"name": name, "status": "error", "host": "box"}
        return command_answer(1 if args.get("command") == "make test" else 0, "out")(name, args)

    runner = Runner(answer)
    text, trace = run_code("""
const r = await Promise.allSettled([
  search_replace({path: "a.py", old_string: "x", new_string: "y", host: "a"}), sh("git commit -m x", {host: "a"}),
  read_file("b.py"), sh("ls", {host: "b"})]);
const t = await Promise.allSettled([run_terminal_command("make test"), write_file("c.txt", "x")]);
return [...r, ...t].map((x) => x.status === "fulfilled" ? "ran" : x.reason.message);""", runner)

    got = json.loads(value_of(text))
    assert trace["status"] == "done", text
    assert got[0].startswith("search_replace a.py: old_string not found")
    assert got[1] == ("Not run: search_replace a.py failed earlier in this run. Calls sent together on a host stop at "
                      "the first failure; send it again if it is still needed.")
    assert got[2:5] == ["ran", "ran", "ran"]
    assert got[5].startswith("Not run: run_terminal_command make test exited 1 earlier in this run.")
    assert sorted(args.get("path") or args.get("command") for _, args in runner.calls) == ["a.py", "b.py", "ls", "make test"]


def test_a_change_sent_after_the_code_saw_the_failure_still_runs():
    def answer(name, args):
        if name == "search_replace":
            return "From host box:\nold_string not found", {"name": name, "status": "error", "host": "box"}
        return command_answer(0, "clean")(name, args)

    runner = Runner(answer)
    text, trace = run_code("try { await search_replace({path: 'a.py', old_string: 'x', new_string: 'y'}); } catch (e) {}\n"
                           "return await sh('git status');", runner)

    assert trace["status"] == "done" and text.startswith("clean")
    assert [name for name, _ in runner.calls] == ["search_replace", "run_terminal_command"]


def test_results_are_masked_off_the_event_loop(monkeypatch):
    from app import secret_guard

    threads = []

    def map_for(user_id):
        threads.append(threading.current_thread())
        return "the map"

    def mask_value(m, value):
        threads.append(threading.current_thread())
        return value

    monkeypatch.setattr(secret_guard, "map_for", map_for)
    monkeypatch.setattr(secret_guard, "mask_value", mask_value)
    text, _ = run_code("return await read_file('a');", Runner(), owner=(1, 1))

    assert text.startswith("ok") and len(threads) == 2
    assert threading.main_thread() not in threads


def test_code_that_throws_about_a_memory_limit_is_a_code_error_with_its_own_text():
    text, trace = run_code("throw new Error('the cache hit its memory limit');", Runner())
    assert trace["kind"] == "code_error" and "the cache hit its memory limit" in text


def test_a_worker_the_run_killed_itself_is_not_blamed_on_the_cpu_limit():
    # A call name too long for the pipe: the parent stops reading and kills the worker.
    text, trace = run_code('await __call("x".repeat(5000000), "{}");', Runner())
    assert trace["kind"] == "worker" and "CPU" not in text


def test_a_syntax_error_shows_its_message_once():
    text, trace = run_code("const x = ;\nreturn 1;", Runner())
    assert trace["kind"] == "code_error" and text.count("Unexpected token") == 1 and "line 1" in text


def test_frames_in_the_wrapper_are_dropped():
    text, _ = run_code("const x = 1;\nnull.foo;", Runner())
    assert "line 2:6" in text and "line 3" not in text

    text, _ = run_code("await new Promise((r) => setTimeout(r, 10));", Runner())
    assert "at line 1:26" in text and "line 2" not in text and "__fuse" not in text and "then.JSON" not in text


@pytest.mark.parametrize("code, hint", [
    ("import fs from 'fs';\nreturn 1;", "run_code is not Node.js, and its helpers are already defined"),
    ("['a'].forEach((x) => { await read_file(x); });", "`for (const x of list) { ... }`"),
    ("return await sh(`echo ${HOME:-/root}`);", "In a backtick string `${` starts JavaScript"),
    ("await new Promise((r) => setTimeout(r, 10));", "Timers are not available in run_code"),
    ("setInterval(() => 1, 10);", "Timers are not available in run_code"),
])
def test_a_common_mistake_gets_its_own_fix(code, hint):
    text, _ = run_code(code, Runner())
    assert hint in text and "Each helper is async" not in text


def test_a_grep_regex_error_from_code_says_how_to_write_a_parenthesis():
    def broken(name, args):
        return "From host box:\ngrep: missing closing parenthesis", {"name": name, "status": "error", "host": "box"}

    text, _ = run_code('return await grep("fetchUser\\(");', Runner(broken))
    assert "grep fetchUser(: grep: missing closing parenthesis (in a JavaScript string \\( is just (: write \\\\(" in text


def test_a_call_without_a_path_pattern_or_command_is_named_by_its_description():
    assert code_mode.subject({"code": "return 1;", "description": "Check the build"}) == "Check the build"
    assert code_mode.subject({"path": "a.py", "description": "Edit it"}) == "a.py"


def test_refusals_name_only_what_code_takes_and_background_false_runs():
    runner = Runner(command_answer(0, "ok"))
    text, _ = run_code("""
const out = [];
try { await run_terminal_command({command: "make", dir: "web"}); } catch (e) { out.push(e.message); }
out.push(JSON.parse(await __call("monitor", "{}")).error);
out.push((await run_terminal_command({command: "make", background: "false"})).status);
return out;""", runner)

    bad, missing, status = json.loads(value_of(text))
    assert bad.startswith("run_terminal_command has no argument dir; it takes command") and "background" not in bad
    assert "read_result" in missing and status == "completed"
    assert runner.calls == [("run_terminal_command", {"command": "make", "background": "false"})]


def test_a_command_sh_left_in_the_background_says_its_result_comes_later():
    runner = Runner(command_answer(None, "building", status="running", job="job_7"))
    text, _ = run_code("try { await sh('make build'); } catch (e) { return e.message; }", runner)
    assert "its result arrives later as a command result" in text and "get_command_output" not in text


def test_the_description_states_how_long_results_are_kept_and_when_sh_needs_or_true():
    desc = code_mode.SCHEMA["function"]["description"]
    sh_line = next(line for line in desc.splitlines() if line.startswith("declare function sh("))
    assert f"kept {code_mode.KEEP_S // 60} minutes" in desc
    assert "a command whose non-zero exit is no failure (grep, diff) needs `|| true`" in sh_line
