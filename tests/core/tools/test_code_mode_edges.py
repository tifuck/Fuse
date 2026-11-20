"""run_code edge cases: what the model gets back in every outcome.

Contract checked here: the text a run_code call hands the model always fits
TOOL_RESULT_CHARS, says how many calls ran and which, never claims "no tool
calls" when calls ran, and never loses the error. Plus the helpers' argument
checks against the tools' real schemas, ordering and concurrency of the
calls, hostile messages from the code, read_result paging and scoping, the
round's child jobs, and secret swap in every outcome.

Known bugs are kept as strict xfails (reason starts with "bug:").
Unit cases drive code_mode.run with a recording fake runner (the real V8
worker runs the code); round cases go through tool_runner.run_tools on the
fake SSH host, as tests/core/tools/test_code_mode.py does.
"""
import asyncio
import collections
import contextlib
import json
import re

import pytest

from app import agent_catalog, code_mode
from app.chat_generation import tool_jobs
from app.chat_generation.tool_jobs import TOOL_RESULT_CHARS, ToolJob
from app.code_worker import HELPERS
from app.config import settings
from tests.core.helpers import fake_provider, run_async, run_turn, text_frames
from tests.core.tools.test_code_mode import OPENAI, Runner, code_call, one_round, project_thread, run_code, tool_results

OK = ("From host box:\nok", {"status": "done", "host": "box"})


def model_text(text, result_id=""):
    """What the model is handed for a finished run_code call with this text."""
    head = ToolJob(0, "call-1", "run_code", "{}", "ts_0_0", text=text, done=True, trace={"status": "done"},
                   result_id=result_id)
    return tool_jobs._tool_messages([head])[1]["content"]


def child(n, name, args, *, done=True, status="done"):
    return ToolJob(0, "call-1", name, json.dumps(args), f"ts_0_0_c{n}", parent="ts_0_0", done=done,
                   trace={"status": status} if done else {})


class Blocking:
    """A runner whose calls for `blocked` paths never finish; records what it saw."""

    def __init__(self, blocked=(), fail=()):
        self.blocked, self.fail = set(blocked), set(fail)
        self.calls, self.cancelled = [], []

    async def __call__(self, name, args):
        key = args.get("path") or args.get("command")
        self.calls.append((name, key))
        if key in self.fail:
            return "From host box:\nNo such file or directory", {"name": name, "status": "error", "host": "box"}
        if key in self.blocked:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.append(key)
                raise
        return "From host box:\nok", {"name": name, "status": "done", "host": "box"}


@pytest.fixture
def kept(monkeypatch):
    monkeypatch.setattr(code_mode, "_kept", collections.OrderedDict())
    monkeypatch.setattr(code_mode, "_kept_chars", 0)
    return code_mode._kept


# -- result contract -----------------------------------------------------------

def test_every_size_around_the_cut_fits_and_keeps_the_closing_calls_lines():
    plain, _ = run_code("await read_file('a');\nawait grep('x');\nawait read_file('b');\nreturn 'v';", Runner())
    stopped, _ = run_code("write_file('a.txt', 'x');\nawait read_file('b');\nreturn 'v';", Blocking(blocked={"a.txt"}))
    tails = [plain.removeprefix("v"), stopped.removeprefix("v"),
             # A returned read_result page ends with the earlier run's calls line.
             "\n[Fuse: 1 call: read_file×1]\n[Fuse: 1 call: read_result×1]"]
    assert tails[0] == "\n[Fuse: 3 calls: read_file×2, grep×1]"
    assert tails[1].startswith("\n[Fuse: 1 call: read_file×1]\n[Fuse: 1 call was still running")

    for tail in tails:
        last = tail.rsplit("\n", 1)[1]
        for n in [*range(TOOL_RESULT_CHARS - 520, TOOL_RESULT_CHARS + 40, 13), 30_000, 300_000]:
            body = ("row of text\n" * (n // 12 + 1))[:n]
            for rid in ("", "r_0123456789ab"):
                shown = model_text(body + tail, rid)
                assert len(shown) <= TOOL_RESULT_CHARS, (n, len(shown))
                assert shown.endswith(last), (n, shown[-200:])
                if len(body + tail) > TOOL_RESULT_CHARS:
                    assert "result cut at" in shown and (not rid or f'read_result("{rid}"' in shown)
                else:
                    assert shown == body + tail


def test_an_error_result_over_the_limit_still_says_which_calls_ran():
    def answer(name, args):
        return "From host box:\nnot found", {"name": name, "status": "error", "host": "box"}

    text, trace = run_code("""
const deep = "deploy/".repeat(20);
for (let i = 0; i < 40; i++) await run_terminal_command(`ls ${i} ${deep}`).catch(() => null);
console.log("L".repeat(20000));
throw new Error("E".repeat(20000));""", Runner(answer))
    assert trace["kind"] == "code_error" and len(text) > TOOL_RESULT_CHARS

    shown = model_text(text, "r_0123456789ab")
    assert len(shown) <= TOOL_RESULT_CHARS and shown.startswith("Error: EEEE")
    assert "Calls that finished before the error (40)" in shown
    assert "- run_terminal_command ls 39 deploy/" in shown


def test_a_cut_run_pages_back_exactly_from_the_offset_the_note_names(ssh_host, kept):
    ssh_host.fs.write(f"{ssh_host.root}/a.txt", "alpha\n")
    text, jobs, _ = one_round(ssh_host, """
const a = await read_file("a.txt");
console.log("logged " + a.trim());
return Array.from({length: 4000}, (_, i) => `row ${i} ${a.trim()}`).join("\\n");""")
    head = jobs[0]
    assert len(text) <= TOOL_RESULT_CHARS and text.endswith("\n[Fuse: 1 call: read_file×1]")
    offset = int(re.search(rf'read_result\("{head.result_id}", \{{offset: (\d+)\}}\)', text).group(1))
    assert text[:offset] == head.text[:offset]

    def digest(s):
        h = 0
        for ch in s:
            h = (h * 31 + ord(ch)) & 0xFFFFFFFF
        return h

    out, _ = run_code(f"""
let off = {offset}, rest = "";
for (;;) {{ const page = await read_result("{head.result_id}", {{offset: off}}); if (!page) break; rest += page; off += page.length; }}
let h = 0;
for (let i = 0; i < rest.length; i++) h = (h * 31 + rest.charCodeAt(i)) >>> 0;
return JSON.stringify({{n: rest.length, h}});""", Runner(), owner=kept[head.result_id][1])
    got = json.loads(out.split("\n[Fuse:")[0])
    assert got == {"n": len(head.text) - offset, "h": digest(head.text[offset:])}
    assert "logged alpha" in head.text and head.text.endswith("[Fuse: 1 call: read_file×1]")


def test_paging_by_page_length_reads_back_text_with_emoji_whole(kept):
    owner = (1, 1)
    ident = code_mode.keep(owner, "".join(f"\U0001F600 row {i}\n" for i in range(3000)))

    out, _ = run_code(f"""
let off = 0, all = "";
for (;;) {{ const page = await read_result("{ident}", {{offset: off}}); if (!page) break; all += page; off += page.length; }}
const want = Array.from({{length: 3000}}, (_, i) => `\\u{{1F600}} row ${{i}}\\n`).join("");
return all === want ? "same" : `differs: ${{all.length}} of ${{want.length}}`;""", Runner(), owner=owner)
    assert out.startswith("same"), out


def test_the_cut_notes_offset_pages_back_the_rest_whole_after_emoji(kept):
    owner = (1, 1)
    text = "".join(f"\U0001F600 row {i}\n" for i in range(3000))
    ident = code_mode.keep(owner, text)
    offset = int(re.search(r"\{offset: (\d+)\}", model_text(text, ident)).group(1))
    cut = len(model_text(text, ident).split("\n[Fuse: result cut at ")[0])

    out, _ = run_code(f"""
let off = {offset}, rest = "";
for (;;) {{ const page = await read_result("{ident}", {{offset: off}}); if (!page) break; rest += page; off += page.length; }}
return rest;""", Runner(), owner=owner)
    assert out.split("\n[Fuse:")[0] == text[cut:]


def test_outcomes_without_a_value_still_close_with_the_calls_line():
    for code, want in (("await read_file('a');\nconsole.log('a', {b: 1});\nreturn;", "[console]\na {\"b\":1}\n[Fuse: 1 call: read_file×1]"),
                       ("await read_file('a');", "(no return value: end the code with `return …`)\n[Fuse: 1 call: read_file×1]"),
                       ("return '';", "\n[Fuse: no tool calls]")):
        text, trace = run_code(code, Runner())
        assert (text, trace["status"]) == (want, "done")


def test_a_value_json_cannot_encode_is_explained_not_shown_as_python_none():
    text, _ = run_code("return () => 1;", Runner())
    assert not text.startswith("None") and "return" in text.split("\n[Fuse:")[0]


def test_code_that_throws_an_empty_value_is_told_what_it_threw():
    text, _ = run_code("throw '';", Runner())
    assert "run_code failed." not in text


def test_a_timeout_keeps_its_message_and_lists_the_calls_that_finished():
    # The 1 s minimum timeout is the point of the test.
    text, trace = run_code("await read_file('a');\nwhile (true) {}", Runner(), timeout=1)
    assert trace["kind"] == "timeout"
    assert text.startswith("Error: run_code timed out after 1000ms") and "Pass a larger timeout" in text
    assert text.endswith("Calls that finished before the error (1):\n- read_file a: done")


def test_memory_exhaustion_says_memory_not_timeout():
    text, trace = run_code("const a = [];\nwhile (true) a.push(new Array(1e6).fill(1.5));", Runner())
    assert trace["kind"] == "memory" and "64 MB of memory" in text and "No tool calls finished" in text


def test_memory_exhaustion_after_a_call_still_says_memory():
    # The 1 s minimum timeout bounds the hang this bug causes.
    text, trace = run_code("await read_file('a');\nconst a = [];\nwhile (true) a.push(new Array(1e6).fill(1.5));",
                           Runner(), timeout=1)
    assert trace["kind"] == "memory" and "- read_file a: done" in text


def test_a_call_left_running_at_exit_is_named_and_never_reads_as_no_calls():
    runner = Blocking(blocked={"a.txt"})
    text, _ = run_code("write_file('a.txt', 'x');\nreturn 'ok';", runner)
    assert runner.calls == [("write_file", "a.txt")] and runner.cancelled == ["a.txt"]
    assert "no tool calls" not in text and "write_file a.txt" in text


def test_an_interrupted_run_with_many_edits_still_names_the_unfinished_one():
    head = ToolJob(0, "call-1", "run_code", json.dumps({"code": "..."}), "ts_0_0")
    edits = [child(i + 1, "write_file", {"path": f"src/module_{i:03d}/" + "n" * 70 + ".py"}) for i in range(300)]
    running = child(301, "run_terminal_command", {"command": "make deploy"}, done=False)

    content = tool_jobs._tool_messages([head, *edits, running])[1]["content"]

    assert len(content) <= TOOL_RESULT_CHARS and content.startswith("Tool interrupted")
    assert "- run_terminal_command make deploy: outcome unknown" in content


def test_a_run_that_failed_inside_fuse_still_names_its_edits():
    head = ToolJob(0, "call-1", "run_code", "{}", "ts_0_0", text="Tool failed.", done=True,
                   trace={"status": "error", "kind": "internal_exception"})
    content = tool_jobs._tool_messages([head, child(1, "write_file", {"path": "a.txt"})])[1]["content"]
    assert "write_file a.txt" in content


# -- argument checks and error messages ------------------------------------------

def _declared():
    """The parameters run_code's description declares for each helper: {name: {param: (type, optional)}}."""
    desc = code_mode.SCHEMA["function"]["description"]
    out = {}
    for m in re.finditer(r"declare function (\w+)\(a\??: \{", desc):
        depth, j = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(desc[j], 0)
            j += 1
        parts, depth, cur = [], 0, ""
        for ch in desc[m.end():j - 1]:
            depth += {"{": 1, "}": -1}.get(ch, 0)
            if ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
            else:
                cur += ch
        parts.append(cur)
        params = {}
        for part in parts:
            key, kind = part.strip().split(":", 1)
            params[key.rstrip("?")] = (kind.strip(), key.endswith("?"))
        out[m.group(1)] = params
    return out


def test_every_parameter_run_code_declares_is_one_the_real_tool_takes():
    declared = _declared()
    assert set(declared) == set(HELPERS)
    for name, params in declared.items():
        real = agent_catalog.schema(name)["function"]["parameters"]
        assert set(params) <= set(real["properties"]), name
        assert set(real.get("required") or []) <= {k for k, (_, optional) in params.items() if not optional}, name


def test_every_documented_call_form_reaches_the_tool():
    def sample(kind):
        if kind.startswith("{old_string"):
            return [{"old_string": "a", "new_string": "b"}]
        if kind.startswith('"'):
            return kind.split('"')[1]
        return {"string": "x", "number": 1, "boolean": True}[kind.split()[0]]

    full = [(name, {k: sample(kind) for k, (kind, _) in params.items()}) for name, params in _declared().items()]
    forms = [
        ("read_file('a.py')", ("read_file", {"path": "a.py"})),
        ("list_dir('src')", ("list_dir", {"path": "src"})),
        ("list_dir()", ("list_dir", {})),
        ("grep('TODO')", ("grep", {"pattern": "TODO"})),
        ("grep({pattern: 'x', glob: '*.py'})", ("grep", {"pattern": "x", "glob": "*.py"})),
        ("glob('**/*.py')", ("glob", {"pattern": "**/*.py"})),
        ("write_file('a.txt', 'hi')", ("write_file", {"path": "a.txt", "content": "hi"})),
        ("search_replace({path: 'a.py', edits: [{old_string: 'a', new_string: 'b'}]})",
         ("search_replace", {"path": "a.py", "edits": [{"old_string": "a", "new_string": "b"}]})),
        ("run_terminal_command('make')", ("run_terminal_command", {"command": "make"})),
    ]
    runner = Runner()
    code = "".join(f"await {name}({json.dumps(args)});\n" for name, args in full)
    code += "".join(f"await {js};\n" for js, _ in forms) + "return 'all ran';"

    text, trace = run_code(code, runner)

    assert trace["status"] == "done" and text.startswith("all ran"), text
    assert runner.calls == full + [want for _, want in forms]


def test_every_refused_helper_call_names_the_helper_and_the_fix():
    runner = Runner()
    cases = {
        "read_file({file: 'a.py'})": "read_file has no argument file; it takes path",
        "write_file('a.txt')": "write_file takes one argument object, e.g. write_file({path:",
        "write_file({path: 'a.txt'})": "write_file needs content",
        "grep()": "grep needs pattern",
        "list_dir(42)": "list_dir takes one argument object, e.g. list_dir({path:",
        "read_file(['a', 'b'])": "read_file takes one argument object",
        "glob({pattern: '*', host: 'box', sort: 'name', depth: 1})": "glob has no argument depth",
        "run_terminal_command({command: 'make', background: true})": "call run_terminal_command directly",
        "__call('monitor', '{}')": "monitor is not available in run_code (available: read_file",
    }
    code = "const out = [];\n" + "".join(
        f"try {{ const r = await {js}; out.push(typeof r === 'string' && r.startsWith('{{') ? JSON.parse(r).error : 'ran'); }} "
        f"catch (e) {{ out.push(e.message); }}\n" for js in cases) + "return out;"

    text, _ = run_code(code, runner)

    got = json.loads(text.split("\n[Fuse:")[0])
    for (js, want), message in zip(cases.items(), got):
        assert want in message, (js, message)
    assert runner.calls == []


def test_a_flood_of_refused_calls_ends_the_run_with_its_cause():
    runner = Runner()
    text, trace = run_code("for (let i = 0; i < 1001; i++) { try { await read_file({file: 'x'}); } catch (e) {} }\nreturn 'done';", runner)
    assert trace["status"] == "error" and runner.calls == []
    assert text.startswith(f"Error: the code made over {2 * code_mode.MAX_CALLS} tool calls")
    assert "No tool calls finished before the error." in text


def test_malformed_messages_from_the_code_are_refused_not_fatal():
    runner = Runner()
    text, trace = run_code("""
const out = [];
for (const [name, raw] of [["read_file", "{"], ["read_file", "null"], ["read_file", "\\"a\\""], ["nope", "{}"],
                           ["read_result", "[1]"], ["read_result", "{\\"id\\": {}}"]]) {
  out.push(JSON.parse(await __call(name, raw)).ok);
}
return out;""", runner)
    assert trace["status"] == "done", text
    assert json.loads(text.split("\n[Fuse:")[0]) == [False] * 6 and runner.calls == []


def test_read_result_with_an_infinite_offset_is_refused_by_name():
    text, _ = run_code('return await __call("read_result", \'{"id": "r_x", "offset": 1e999}\');', Runner())
    assert "read_result" in text


def test_a_call_too_big_for_the_pipe_is_refused_by_name_not_blamed_on_cpu():
    runner = Runner()
    text, trace = run_code('await write_file({path: "e.txt", content: "\\u{1F600}".repeat(360000)});\nreturn "ok";', runner)
    assert trace.get("kind") != "cpu" and "CPU" not in text
    assert runner.calls or "write_file" in text


def test_a_syntax_error_at_the_end_of_the_code_points_inside_the_code():
    text, _ = run_code("if (true) {\nreturn 1;", Runner())
    assert "SyntaxError" in text and "})().then(" not in text and "line 3" not in text


# -- ordering and concurrency ------------------------------------------------------

def test_reads_in_promise_all_overlap_up_to_the_child_limit():
    inflight, peak = 0, 0
    full = None

    async def call(name, args):
        nonlocal inflight, peak, full
        full = full or asyncio.Event()
        inflight += 1
        peak = max(peak, inflight)
        if peak >= code_mode.CHILD_CONCURRENCY:
            full.set()
        # Guard only: calls that never overlap would otherwise wait forever.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(full.wait(), 10)
        inflight -= 1
        return OK[0], {"name": name, **OK[1]}

    text, _ = run_code("const all = await Promise.all(Array.from({length: 20}, (_, i) => read_file(`f${i}`)));\n"
                       "return all.length;", call)
    assert text.startswith("20\n[Fuse: 20 calls: read_file×20]")
    assert peak == code_mode.CHILD_CONCURRENCY


def test_edits_and_commands_never_overlap_and_keep_their_order():
    inflight, peak, order = 0, 0, []

    async def call(name, args):
        nonlocal inflight, peak
        if name in code_mode.MUTATING:
            inflight += 1
            peak = max(peak, inflight)
            order.append(args.get("path") or args.get("command"))
            for _ in range(200):
                await asyncio.sleep(0)
            inflight -= 1
        return OK[0], {"name": name, **OK[1]}

    text, _ = run_code("""
await Promise.all([write_file("a", "1"), search_replace({path: "b", old_string: "x", new_string: "y"}),
                   run_terminal_command("make c"), read_file("r"), write_file("d", "4")]);
return "done";""", call)
    assert text.startswith("done") and peak == 1
    assert order == ["a", "b", "make c", "d"]


def test_a_rejected_call_in_promise_all_stops_the_rest_and_says_how_many():
    runner = Blocking(blocked={"a", "b", "c"}, fail={"bad"})
    text, trace = run_code("return await Promise.all(['a', 'b', 'c', 'bad'].map((p) => read_file(p)));", runner)
    assert trace["status"] == "error"
    assert text.startswith("Error: read_file bad: No such file or directory")
    assert "- read_file bad: error" in text and "3 calls still running were stopped." in text
    assert sorted(runner.cancelled) == ["a", "b", "c"]


# -- read_result -------------------------------------------------------------------

def test_read_result_bounds_offsets_and_limits_and_names_bad_ones(kept):
    owner = (7, 70)
    ident = code_mode.keep(owner, "abcdef")
    text, trace = run_code(f"""
const id = "{ident}", r = {{}};
r.past = await read_result(id, {{offset: 99}});
r.negative = await read_result(id, {{offset: -3, limit: 2}});
r.huge = await read_result(id, {{offset: 1e300}});
r.zeroLimit = await read_result(id, {{limit: 0}});
r.fraction = await read_result(id, {{offset: 1.9, limit: 2.9}});
try {{ await read_result(id, {{offset: "x"}}); }} catch (e) {{ r.badType = e.message; }}
try {{ await read_result(); }} catch (e) {{ r.noId = e.message; }}
return r;""", Runner(), owner=owner)
    got = json.loads(text.split("\n[Fuse:")[0])
    assert trace["status"] == "done"
    assert {k: got[k] for k in ("past", "negative", "huge", "zeroLimit", "fraction")} == {
        "past": "", "negative": "ab", "huge": "", "zeroLimit": "abcdef", "fraction": "bc"}
    assert got["badType"].startswith("read_result takes an id and optional {offset, limit} numbers")
    assert got["noId"].startswith("no kept result")
    assert text.endswith("[Fuse: 7 calls: read_result×7]")


def test_a_kept_result_is_readable_only_by_its_own_user_and_chat(kept):
    ident = code_mode.keep((5, 50), "secret-ish text")
    missing = code_mode._read_kept((5, 50), json.dumps({"id": "r_000000000000"}))["error"]

    assert code_mode._read_kept((5, 50), json.dumps({"id": ident}))["value"] == "secret-ish text"
    for owner in ((6, 50), (5, 51), (), (5, None), (None, 50)):
        page = code_mode._read_kept(owner, json.dumps({"id": ident}))
        # Refused exactly like an id that never existed: nothing says it is someone else's.
        assert page == {"ok": False, "error": missing.replace("r_000000000000", ident)}, owner


# -- one round through the tool runner ------------------------------------------------

def test_each_call_from_code_is_a_child_job_of_the_one_model_call(ssh_host):
    ssh_host.fs.write(f"{ssh_host.root}/a.txt", "alpha\n")
    text, jobs, events = one_round(ssh_host, """
const a = await read_file("a.txt");
try { await read_file({file: "a.txt"}); } catch (e) {}
await read_result("r_000000000000").catch(() => null);
await write_file("b.txt", a + "beta\\n");
return await read_file("b.txt");""")

    head, *children = jobs
    assert [j.name for j in children] == ["read_file", "write_file", "read_file"]
    assert [j.event_id for j in children] == [f"{head.event_id}_c{n}" for n in (1, 2, 3)]
    assert {j.call_id for j in jobs} == {"call-1"} and {j.parent for j in children} == {head.event_id}
    assistant, *rest = tool_jobs._tool_messages(jobs)
    assert [c["function"]["name"] for c in assistant["tool_calls"]] == ["run_code"] and len(rest) == 1
    assert text == "alpha\nbeta\n\n[Fuse: 4 calls: read_file×2, read_result×1, write_file×1]"
    rows = [e for e in events if e.get("event") == "tool" and e.get("id") != head.event_id]
    assert {e["id"] for e in rows} == {j.event_id for j in children}
    assert all(e.get("parent") == head.event_id for e in rows)


def test_a_large_write_from_code_leaves_no_copy_of_its_content_in_the_round(ssh_host):
    content = "z" * 300_000
    text, jobs, _ = one_round(ssh_host, f'await write_file("big.txt", "z".repeat({len(content)}));\nreturn "wrote";')

    assert text.startswith("wrote") and ssh_host.fs.text(f"{ssh_host.root}/big.txt") == content
    [write] = jobs[1:]
    assert write.text == "" and len(write.arguments) < 200
    assert len(json.dumps(write.trace)) < 20_000


# -- secret swap ---------------------------------------------------------------------

def test_no_outcome_hands_the_model_or_the_code_a_real_secret(ssh_host, kept, monkeypatch):
    monkeypatch.setattr(settings, "secret_swap_enabled", True)
    ssh_host.fs.write(f"{ssh_host.root}/conf.env", f"OPENAI_API_KEY={OPENAI}\n")
    failing = 'const t = await read_file("conf.env");\nconsole.log("read " + t);\nthrow new Error("bad config: " + t);'
    long = ('const t = await read_file("conf.env");\n'
            'return Array.from({length: 400}, (_, i) => `${i} ${t}`).join("");')

    def page_back(body):
        ident = re.search(r'read_result\("(r_[0-9a-f]+)"', tool_results(body)[-1]["content"]).group(1)
        return code_call(f'return (await read_result("{ident}", {{limit: 200}})) + "|" + '
                         f'(await read_result("{ident}", {{offset: 31000}}));', "call-3")

    async def body():
        with fake_provider(code_call(failing, "call-1"), lambda b: code_call(long, "call-2"), page_back,
                           text_frames("checked")) as provider:
            _, run = await run_turn(ssh_host.uid, "check the config", thread=project_thread(ssh_host, swap=True))
        return run, provider.bodies

    run, bodies = run_async(body())

    assert run["status"] == "completed"
    for request in bodies:
        assert OPENAI not in json.dumps(request["messages"])
    error, cut, paged = (m["content"] for m in tool_results(bodies[3]))
    assert error.startswith("Error: bad config: OPENAI_API_KEY=sk-proj-") and "[console]\nread OPENAI_API_KEY=sk-proj-" in error
    assert "result cut at" in cut and "OPENAI_API_KEY=sk-proj-" in cut
    assert paged.count("OPENAI_API_KEY=sk-proj-") >= 2
    assert kept and all(OPENAI not in text for _, _, text in kept.values())
