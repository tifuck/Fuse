"""run_code: model-written JavaScript that calls the workspace tools.

Each run is one disposable worker process (app/code_worker.py): a V8 isolate
with no I/O or network of its own, an empty environment and no root. The
worker sends each helper call back here, and `call` (tool_runner.run_tools)
runs it as a real tool job of the same round: the permission gate,
confinement, transcript rows and failure log apply exactly as to a direct
call, and the code only sees the text a direct call would return. Only what the code returns, plus its console output, reaches
the model. The worker is killed at its deadline or when the run stops, so no
script can hold the server's event loop or outlive its run."""
from __future__ import annotations

import asyncio
import collections
import contextlib
import importlib.util
import json
import logging
import re
import secrets
import signal
import sys
import time
from pathlib import Path

from .code_worker import ARG_CHARS, CPU_SECONDS, HELPERS, MESSAGE_LIMIT, OFFSET
from .config import settings
from .workspace_ops.instructions import unlabel_host

logger = logging.getLogger(__name__)

WORKER = Path(__file__).with_name("code_worker.py")
HEAP_MB = 64
MAX_CALLS = 500
CHILD_CONCURRENCY = 16
DEFAULT_TIMEOUT_MS = 300_000
MAX_TIMEOUT_MS = 600_000
ERROR_CHARS = 2_000
# Text the helpers may hand one run (each result is also piped to the
# worker): 16 whole 2 MB files, or hundreds of ordinary ones.
RESULT_BUDGET = 32_000_000
# Calls that change something run one at a time, like direct calls on a
# host, so a run raises at most one permission card at a time.
MUTATING = ("write_file", "search_replace", "run_terminal_command")
# Each run is a process with its own V8 heap: a burst of runs across chats
# must not start one per call, and a run must not queue for long.
WORKERS = 8
SLOT_WAIT_S = 30
_slots: tuple | None = None
_INSTALLED = importlib.util.find_spec("py_mini_racer") is not None
# A result cut for the model is kept whole for a while, so a later run_code
# can read the rest (read_result) instead of running the code again. Memory
# only: a restart drops them, and the oldest go first past KEEP_CHARS.
KEEP_S = 1800
KEEP_CHARS = 32_000_000
READ_PAGE = 20_000
_kept: collections.OrderedDict[str, tuple[float, tuple, str]] = collections.OrderedDict()
_kept_chars = 0
_clock = time.monotonic


def keep(owner: tuple, text: str) -> str:
    """Keep a whole result for its owner (user, thread); returns its id."""
    global _kept_chars
    _forget(_clock())
    ident = "r_" + secrets.token_hex(6)
    _kept[ident] = (_clock() + KEEP_S, owner, text)
    _kept_chars += len(text)
    while _kept_chars > KEEP_CHARS and len(_kept) > 1:
        _kept_chars -= len(_kept.popitem(last=False)[1][2])
    return ident


def _forget(now: float) -> None:
    global _kept_chars
    # Entries expire in the order they were kept (one TTL for all).
    while _kept and next(iter(_kept.values()))[0] <= now:
        _kept_chars -= len(_kept.popitem(last=False)[1][2])


def _read_kept(owner: tuple, raw: str) -> dict:
    try:
        args = json.loads(raw)
        ident = str(args.get("id") or "")
        offset = max(0, int(args.get("offset") or 0))
        limit = max(1, int(args.get("limit") or READ_PAGE))
    except (ValueError, TypeError, AttributeError, RecursionError, OverflowError):
        return {"ok": False, "error": "read_result takes an id and optional {offset, limit} numbers."}
    _forget(_clock())
    entry = _kept.get(ident)
    if entry is None or entry[1] != owner:
        return {"ok": False, "error": f"no kept result {ident}: results are kept for {KEEP_S // 60} minutes in this "
                                        "chat and lost when the server restarts. Run the code again with a narrower "
                                        "result (only if repeating its calls is safe)."}
    # Offset and limit count UTF-16 units, as the code's page.length does.
    units = entry[2][:offset + limit].encode("utf-16-le", "surrogatepass")[2 * offset:2 * (offset + limit)]
    return {"ok": True, "value": units.decode("utf-16-le", "surrogatepass")}


def available() -> bool:
    return _INSTALLED and bool(settings.code_mode)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_code",
        "description": (
            "Use this for almost all file and shell work: a short JavaScript snippet whose calls below are real "
            "tool calls; only what it returns comes back to you.\n"
            "Plain JavaScript, not Node.js: no require, import, fs, child_process, process or timers. Await each "
            "call, use Promise.all for independent ones, and end with return. Write shell commands and code-heavy "
            "edit text as '…' or \"…\" strings: in a backtick string ${x} is JavaScript (or escape it as \\${x}); for text with literal backslashes use String.raw`...`, which keeps \\n as two "
            "characters. "
            "Each function takes the same arguments as the tool of that name (a string is the path, pattern or "
            "command):\n"
            "declare function read_file(a: {path: string, offset?: number, limit?: number, host?: string} | string): "
            "Promise<string>; // raw text, no line numbers; the whole file (up to 2 MB) unless limit is given\n"
            "declare function grep(a: {pattern: string, path?: string, include?: string, output_mode?: \"content\" | "
            "\"files_with_matches\" | \"count\", case_insensitive?: boolean, max_matches?: number, host?: string} | "
            "string): Promise<string>; // path:line:text lines; path is one file or directory (for several, use "
            "include with braces, e.g. \"*.{py,js}\", or one call each)\n"
            "declare function glob(a: {pattern: string, path?: string, host?: string} | string): Promise<string>; "
            "// one path per line\n"
            "declare function list_dir(a?: {path?: string, depth?: number, host?: string} | string): Promise<string>; "
            "// one entry per line, directories end with /\n"
            "declare function write_file(a: {path: string, content: string, host?: string} | string, content?: "
            "string): Promise<string>; // receipt; write_file(path, content) works too\n"
            "declare function search_replace(a: {path: string, old_string?: string, new_string?: string, "
            "replace_all?: boolean, edits?: {old_string: string, new_string: string, replace_all?: boolean}[], "
            "host?: string}): Promise<string>; // receipt with the edited lines\n"
            "declare function run_terminal_command(a: {command: string, cwd?: string, timeout?: number, "
            "description?: string, host?: string} | string): Promise<{status: string, exit_code: number | null, "
            "output: string, id?: string}>; // a non-zero exit does not throw; status \"running\" means it moved "
            "to the background and its result arrives later\n"
            "declare function read_result(id: string, a?: {offset?: number, limit?: number}): Promise<string>; "
            "// the rest of an earlier result that was cut (the cut note names its id; kept 30 minutes): the text "
            "that run returned, not its variables, so take only the part you need\n"
            "A line in (parentheses) in grep, glob or list_dir text is a note, not a result. A failed call throws "
            "an Error with the tool's message; catch it where a miss is expected. In Promise.all one failure "
            "rejects the whole batch and ends the code, stopping calls still running: add .catch(...) to calls "
            "that may fail, or use Promise.allSettled. console.log output comes back "
            "too. Limits: 500 calls, 5 minutes (timeout, in ms, up to 10). A returned value over 24,000 "
            "characters is cut, so return findings, not file bodies.\n"
            "Example:\n"
            "const files = (await grep({pattern: \"fetchUser\\\\(\", output_mode: \"files_with_matches\"}))"
            ".split(\"\\n\").filter((p) => p && !p.startsWith(\"(\"));\n"
            "for (const path of files) await search_replace({path, old_string: \"fetchUser(\", new_string: "
            "\"loadUser(\", replace_all: true});\n"
            "const t = await run_terminal_command({command: \"npm test 2>&1 | tail -n 20\", description: \"Run the "
            "tests\"});\n"
            "return {edited: files, exit: t.exit_code, tail: t.output};"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "minLength": 1, "description": "JavaScript: an async function body; end with return."},
                "description": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "What this code does, in 3-10 words; shown on the row instead of the code.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Milliseconds; values under 1000 are seconds. Default 300000 (5 min), max 600000.",
                },
            },
            "required": ["code", "description"],
            "additionalProperties": False,
        },
    },
}


def _slot_lock() -> asyncio.Semaphore:
    """The server's worker slots: one semaphore per event loop (tests run several)."""
    global _slots
    loop = asyncio.get_running_loop()
    if _slots is None or _slots[0] is not loop:
        _slots = (loop, asyncio.Semaphore(WORKERS))
    return _slots[1]


def _timeout_ms(raw) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_TIMEOUT_MS
    if value <= 0:
        return DEFAULT_TIMEOUT_MS
    if value < 1000:
        value *= 1000
    return max(1000, min(value, MAX_TIMEOUT_MS))


def subject(args: dict) -> str:
    """A call's path, pattern or command, short, for summaries of what ran."""
    text = " ".join(str(args.get("path") or args.get("pattern") or args.get("command") or "").split())
    return text[:80] + ("…" if len(text) > 80 else "")


def _unlabel(text: str, trace: dict) -> str:
    return unlabel_host(str(trace.get("host") or ""), text or "")


def _value(name: str, text: str, trace: dict):
    """What a helper returns to the code, built only from the call's own
    text."""
    body = _unlabel(text, trace)
    if name != "run_terminal_command":
        return body
    head, _, output = body.partition("\n\n")
    fields = dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)
    exit_code = trace.get("exit_code")
    value = {
        "status": fields.get("status") or str(trace.get("status") or ""),
        "exit_code": exit_code if isinstance(exit_code, int) else None,
        "output": output,
    }
    if fields.get("id"):
        value["id"] = fields["id"]
    return value


# Undefined names that mean "run_code is not Node.js" or "that is a tool, not
# a helper"; any other undefined name is most often a shell or file `${x}`
# left inside a backtick string, which JavaScript filled in itself.
_NOT_HERE = frozenset({
    "require", "module", "exports", "process", "Buffer", "__dirname", "__filename", "fs", "path", "os",
    "child_process", "fetch", "setTimeout", "setInterval", "monitor", "get_command_output", "kill_command",
    "web_search", "read_url", "spawn_subagent", "todo_write", "scratch", "load_skill", "search_tools",
})


_EXAMPLE_ARGS = {
    "read_file": '{path: "src/app.py"}', "list_dir": '{path: "src"}', "grep": '{pattern: "TODO", path: "src"}',
    "glob": '{pattern: "**/*.py"}', "write_file": '{path: "a.txt", content: "..."}',
    "search_replace": '{path: "a.py", old_string: "x", new_string: "y"}',
    "run_terminal_command": '{command: "npm test", description: "Run the tests"}',
}


def _bad_args(name: str, args: dict) -> str:
    """A helper call whose argument names the tool would not take, said with
    the names it does take."""
    from .agent_catalog import schema

    params = ((schema(name) or {}).get("function") or {}).get("parameters") or {}
    known, required = list(params.get("properties") or {}), params.get("required") or []
    unknown = [key for key in args if key not in known]
    missing = [key for key in required if key not in args]
    if not unknown and not missing:
        return ""
    what = f"has no argument {', '.join(unknown)}" if unknown else f"needs {', '.join(missing)}"
    return f"{name} {what}; it takes {', '.join(known)}, e.g. {name}({_EXAMPLE_ARGS[name]})."


def _call_error(name: str, args: dict, message: str) -> str:
    """A failed helper call as the code sees it: which call, what the tool said."""
    message = message.strip()[:ERROR_CHARS] or "failed"
    path = str(args.get("path") or "")
    if "," in path and re.search(r"no such file|not found|does not exist", message, re.I):
        message += " (path is one file or directory: make one call per path, or use include with braces)"
    if name == "search_replace" and "not found" in message:
        message = _edit_miss(args, message)
    return " ".join(f"{name} {subject(args)}".split()) + f": {message}"


def _edit_miss(args: dict, message: str) -> str:
    """Why an edit from code missed, after the tool's first line (any closest
    match the tool found follows it)."""
    edits = args.get("edits") if isinstance(args.get("edits"), list) else [args]
    olds = [str(e.get("old_string") or "") for e in edits if isinstance(e, dict)]
    hints = []
    if any("\\" in old for old in olds):
        hints.append("old_string has backslashes: in a JavaScript string \\n is a newline and \\\\ is one "
                     "backslash, so write file text that has literal backslashes with String.raw`...` or doubled "
                     "backslashes, or make this one edit with a direct search_replace call")
    which = re.search(r"edit (\d+) of", message)
    if which and int(which.group(1)) > 1:
        hints.append("edits apply in order, so each old_string must match the text after the edits before it")
    if not hints:
        return message
    first, _, rest = message.partition("\n")
    return f"{first} ({'; '.join(hints)})" + (f"\n{rest}" if rest else "")


def _user_lines(stack: str) -> str:
    """V8 positions as the model's own line numbers; frames in the prelude
    (a helper throwing) are dropped, and a Node habit gets its fix."""
    out = []
    for line in str(stack).splitlines():
        m = re.search(r"<anonymous>:(\d+)", line)
        if m and int(m.group(1)) <= OFFSET:
            continue
        out.append(re.sub(r"<anonymous>:(\d+)", lambda m: f"line {int(m.group(1)) - OFFSET}", line))
    text = "\n".join(out)
    fix = " Fix the code and call run_code again."
    # A helper the code shadowed (`const { grep } = globalThis`) is undefined, not un-awaited.
    shadowed = re.search(r"\b(\w+) is not a function", text)
    undefined = re.search(r"\b(\w+) is not defined", text)
    if undefined and undefined.group(1) not in _NOT_HERE:
        name = undefined.group(1)
        text += (f"\n(If `${{{name}}}` sits in a backtick string meant for the shell or for file text, JavaScript "
                 f"filled it in: use a '…' or \"…\" string there, or write \\${{{name}}}.{fix})")
    elif undefined:
        text += f"\n(run_code provides only: {', '.join(HELPERS)}, e.g. `await read_file(\"a.py\")`.{fix})"
    elif text.startswith("Not supported") or "Error: Not supported" in text:
        text += f"\n(import() is not available: run_code is not Node.js. Use {', '.join(HELPERS)}.{fix})"
    elif shadowed and shadowed.group(1) in HELPERS:
        name = shadowed.group(1)
        text += (f"\n({name} is already defined in run_code: call it as `await {name}({{...}})` and do not declare, "
                 f"destructure or import it.{fix})")
    elif re.search(r"\.(split|trim|includes|match|replace|slice|startsWith|endsWith|map|filter|forEach|sort|join|"
                   r"length) is not a function|Cannot read properties of undefined \(reading '(split|trim|length)'\)", text):
        text += ("\n(Helpers return text, except run_terminal_command, which returns {status, exit_code, output}: use "
                 "its .output. Split glob, grep and list_dir text into lines with .split(\"\\n\") before array methods, "
                 f"and await every call.{fix})")
    elif "is not a function" in text or "[object Promise]" in text:
        text += f"\n(Each helper is async: write `await read_file(...)`, and `await Promise.all(...)` over a map.{fix})"
    return text


async def run(arguments: str, *, call, paused=None, owner: tuple = ()) -> tuple[str, dict]:
    """One run_code call. call(name, args) -> (text, trace) runs a nested tool
    call as a job of this round and returns its finished result; paused() is
    true while a permission prompt waits, which stops the deadline's clock;
    owner (user, thread) is whose kept results read_result may read.
    An exception the runner lets escape (a checkpoint that keeps failing)
    stops the code and propagates."""
    try:
        args = json.loads(arguments or "{}")
    except (ValueError, RecursionError):
        args = None
    code = args.get("code") if isinstance(args, dict) else None
    if not isinstance(code, str) or not code.strip():
        msg = "run_code needs code: the body of an async JavaScript function."
        return msg, {"name": "run_code", "status": "error", "kind": "invalid_arguments", "error": msg}
    description = args.get("description")
    if not isinstance(description, str) or not description.strip():
        # The row the user sees is named by it; nothing runs without one.
        msg = "run_code needs a description: what the code does, in 3-10 words. Call it again with one."
        return msg, {"name": "run_code", "status": "error", "kind": "invalid_arguments", "error": msg}
    rec = {"name": "run_code", "description": description.strip()[:200]}
    if not available():
        msg = "run_code is not available on this server; use the tools directly."
        return msg, {**rec, "status": "error", "kind": "unavailable", "error": msg}
    slots = _slot_lock()
    try:
        await asyncio.wait_for(slots.acquire(), SLOT_WAIT_S)
    except TimeoutError:
        msg = (f"run_code is busy: {WORKERS} runs are already in progress on this server. "
               "Try again shortly, or use the tools directly.")
        return msg, {**rec, "status": "error", "kind": "busy", "error": msg}
    try:
        outcome, kind, done, stopped = await _execute(
            code, _timeout_ms(args.get("timeout")) / 1000, call, paused or (lambda: False), owner)
    finally:
        slots.release()
    return _result(code, rec, outcome, kind, done, stopped)


async def _execute(code: str, timeout_s: float, call, paused, owner: tuple):
    done: list[dict] = []
    tasks: set[asyncio.Task] = set()
    children = asyncio.Semaphore(CHILD_CONCURRENCY)
    mutate = asyncio.Lock()
    crashed: list[BaseException] = []
    abort = asyncio.Event()
    started = messages = received = 0
    stderr_tail = bytearray()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", str(WORKER), str(HEAP_MB),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={}, cwd="/", limit=MESSAGE_LIMIT,
    )

    def send(obj) -> None:
        if proc.stdin is not None and not proc.stdin.is_closing():
            proc.stdin.write(json.dumps(obj).encode() + b"\n")

    async def read_stderr() -> None:
        while chunk := await proc.stderr.read(4096):
            stderr_tail.extend(chunk)
            del stderr_tail[:-2000]

    async def answer(n: int, name: str, call_args: dict) -> None:
        nonlocal received
        try:
            async with children:
                if name in MUTATING:
                    async with mutate:
                        text, trace = await call(name, call_args)
                else:
                    text, trace = await call(name, call_args)
        except Exception as exc:
            # The tool runner settles each call's own failures; what escapes
            # it stops the code before it changes anything more.
            crashed.append(exc)
            abort.set()
            text, trace = "Tool failed.", {"status": "error"}
        done.append({"name": name, "subject": subject(call_args), "status": str(trace.get("status") or "")})
        received += len(text or "")
        if trace.get("status") in ("error", "interrupted"):
            body = {"ok": False, "error": _call_error(name, call_args, _unlabel(text, trace))}
        else:
            body = {"ok": True, "value": _value(name, text, trace)}
        if owner:
            from . import secret_guard

            # The code only ever holds stand-ins, so it cannot encode or
            # split a real value on its way back to the model.
            secrets = await asyncio.to_thread(secret_guard.map_for, owner[0])
            body = secret_guard.mask_value(secrets, body)
        send({"reply": n, "body": json.dumps(body)})
        # A large reply waits for the pipe here, in this call's own task.
        with contextlib.suppress(ConnectionError):
            await proc.stdin.drain()

    def refuse(n: int, error: str) -> None:
        send({"reply": n, "body": json.dumps({"ok": False, "error": error})})

    async def pump():
        # The worker runs model code: every line from it is checked like input.
        nonlocal started, messages
        send({"code": code})
        while line := await proc.stdout.readline():
            msg = json.loads(line)
            if not isinstance(msg, dict):
                return None
            if "done" in msg:
                return msg["done"] if isinstance(msg["done"], dict) else None
            n, name, raw = msg.get("call"), msg.get("name"), msg.get("args")
            if not isinstance(n, int) or not isinstance(name, str) or not isinstance(raw, str):
                return None
            messages += 1
            if messages > 2 * MAX_CALLS:
                return {"ok": False, "error": f"the code made over {2 * MAX_CALLS} tool calls, most of them refused."}
            if name == "read_result":
                # The model's own earlier output, already swapped: no host, no tool job.
                page = _read_kept(owner, raw)
                done.append({"name": "read_result", "subject": "", "status": "done" if page["ok"] else "error"})
                send({"reply": n, "body": json.dumps(page)})
                continue
            if name not in HELPERS:
                refuse(n, f"{name} is not available in run_code (available: {', '.join(HELPERS)}).")
                continue
            if len(raw) > ARG_CHARS:
                refuse(n, f"{name}'s arguments are over {ARG_CHARS} characters; split the work (for example, "
                          "write the file in parts).")
                continue
            try:
                call_args = json.loads(raw)
            except (ValueError, RecursionError):
                call_args = None
            if not isinstance(call_args, dict):
                refuse(n, f"{name} takes one argument object, e.g. {name}({_EXAMPLE_ARGS[name]}).")
            elif bad := _bad_args(name, call_args):
                refuse(n, bad)
            elif name == "run_terminal_command" and call_args.get("background"):
                refuse(n, "background commands are not available in run_code; call run_terminal_command directly.")
            elif started >= MAX_CALLS:
                refuse(n, f"run_code allows at most {MAX_CALLS} tool calls per run.")
            elif received >= RESULT_BUDGET:
                refuse(n, f"this run has received {RESULT_BUDGET} characters of results, its limit; return what "
                          "you have and continue in another run_code call.")
            else:
                started += 1
                task = asyncio.ensure_future(answer(n, name, call_args))
                task.set_name(" ".join(f"{name} {subject(call_args)}".split()))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        return None

    loop = asyncio.get_running_loop()
    reader = asyncio.ensure_future(pump())
    errors = asyncio.ensure_future(read_stderr())
    aborted = asyncio.ensure_future(abort.wait())
    outcome, kind, finished, left = None, "", set(), timeout_s
    try:
        # The clock stops while a permission prompt waits for the user.
        while left > 0:
            start = loop.time()
            finished, _ = await asyncio.wait({reader, aborted}, timeout=min(left, 1.0),
                                             return_when=asyncio.FIRST_COMPLETED)
            if finished:
                break
            if not paused():
                left -= loop.time() - start
        if reader in finished:
            try:
                outcome = reader.result()
            except (ValueError, RecursionError, KeyError):
                outcome = None
            if outcome is not None and not outcome.get("ok"):
                kind = "memory" if "memory limit" in str(outcome.get("error") or "").lower() else "code_error"
                if kind == "memory":
                    outcome["error"] = (f"the code used its {HEAP_MB} MB of memory. Keep only what you need from each "
                                        "file instead of holding them all, or split the work across runs.")
        elif not finished:
            kind, outcome = "timeout", {"ok": False, "error": (
                f"run_code timed out after {int(timeout_s * 1000)}ms. Pass a larger timeout (up to {MAX_TIMEOUT_MS}) or "
                "do less per run; run a long build or test with run_terminal_command directly.")}
    finally:
        aborted.cancel()
        reader.cancel()
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        await proc.wait()
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(errors, 1)
        # Calls the code left running (never awaited, or cut off) stop with it.
        stopped = [task.get_name() for task in tasks]
        for task in list(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    if crashed:
        raise crashed[0]
    if outcome is None and not kind:
        if proc.returncode in (-signal.SIGXCPU, -signal.SIGKILL):
            kind, error = "cpu", f"the code used its {CPU_SECONDS} s CPU limit, most likely in an endless loop."
        else:
            kind, error = "worker", ("the code runner stopped unexpectedly. Call run_code again; if it stops again, "
                                     "use the tools directly.")
            logger.warning("run_code worker ended with %s: %s", proc.returncode,
                           stderr_tail.decode("utf-8", "replace").strip()[-500:])
        outcome = {"ok": False, "error": error}
    return outcome, kind, done, stopped


def _calls_line(done: list[dict], stopped: list[str]) -> str:
    counts = collections.Counter(c["name"] for c in done)
    plural = lambda n: "call" if n == 1 else "calls"  # noqa: E731
    line = (f"[Fuse: {len(done)} {plural(len(done))}: " + ", ".join(f"{n}×{k}" for n, k in counts.items()) + "]") if done \
        else "[Fuse: 0 calls: none finished]" if stopped else "[Fuse: no tool calls]"
    if stopped:
        names = "; ".join(stopped[:5]) + ("; …" if len(stopped) > 5 else "")
        line += (f"\n[Fuse: {len(stopped)} {plural(len(stopped))} {'was' if len(stopped) == 1 else 'were'} still running when the code ended and {'was' if len(stopped) == 1 else 'were'} stopped ({names}); "
                 "await every call.]")
    return line


def _result(code: str, rec: dict, outcome: dict, kind: str, done: list[dict], stopped: list[str]) -> tuple[str, dict]:
    logs = str(outcome.get("logs") or "")
    if outcome.get("ok"):
        if not outcome.get("undef"):
            text = str(outcome.get("value")) + ("\n[console]\n" + logs if logs else "")
        else:
            text = "[console]\n" + logs if logs else "(no return value: end the code with `return …`)"
        text += "\n" + _calls_line(done, stopped)
        return text, {**rec, "status": "done", "output": text, "code": code}
    error = _user_lines(str(outcome.get("error") or "run_code failed.")).removeprefix("Error: ")
    # The calls come before the console output: a result over the limit is
    # cut from its end, and which calls ran must survive the cut.
    lines = [f"Error: {error}"]
    if done:
        lines.append(f"Calls that finished before the error ({len(done)}):")
        lines.extend(" ".join(f"- {c['name']} {c['subject']}".split()) + f": {c['status']}" for c in done[-40:])
    else:
        lines.append("No tool calls finished before the error.")
    if stopped:
        lines.append(f"{len(stopped)} calls still running were stopped.")
    if logs:
        lines.append("[console]\n" + logs)
    text = "\n".join(lines)
    return text, {**rec, "status": "error", "kind": kind or "code_error", "error": error[:400], "output": text, "code": code}
