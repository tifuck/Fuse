"""run_code: model-written JavaScript that calls the workspace tools.

Each run is one disposable worker process (app/code_worker.py): a V8 isolate
with no I/O or network of its own, an empty environment and no root. The
worker sends each helper call back here, and `call` (tool_runner.run_tools)
runs it as a real tool job of the same round: the permission gate,
confinement, secret swap, transcript rows and failure log apply exactly as
to a direct call, and the code only sees the swapped text a direct call
would return. Only what the code returns, plus its console output, reaches
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
import signal
import sys
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
# Text the helpers may hand one run (each result is also secret-swapped and
# piped to the worker): 16 whole 2 MB files, or hundreds of ordinary ones.
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


def available() -> bool:
    return _INSTALLED and bool(settings.code_mode)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_code",
        "description": (
            "Run plain JavaScript (not Node.js: no require, import, fs, child_process, process or timers) whose "
            "file and shell calls are real tool calls. Only what the code returns comes back, so intermediate "
            "results stay out of the conversation.\n"
            "The code is the body of an async function: await each call, use Promise.all for independent ones, "
            "and end with return. These functions take the same argument object as the tool of that name (a "
            "string is the path, pattern or command); each call is permission-checked and shown to the user like "
            "a direct call:\n"
            "declare function read_file(a: {path: string, offset?: number, limit?: number, host?: string} | string): "
            "Promise<string>; // raw text, no line numbers; the whole file (up to 2 MB) unless limit is given\n"
            "declare function grep(a: {pattern: string, path?: string, include?: string, output_mode?: \"content\" | "
            "\"files_with_matches\" | \"count\", case_insensitive?: boolean, max_matches?: number, host?: string} | "
            "string): Promise<string>; // path:line:text lines\n"
            "declare function glob(a: {pattern: string, path?: string, host?: string} | string): Promise<string>; "
            "// one path per line\n"
            "declare function list_dir(a?: {path?: string, depth?: number, host?: string} | string): Promise<string>; "
            "// one entry per line, directories end with /\n"
            "declare function write_file(a: {path: string, content: string, host?: string}): Promise<string>; "
            "// receipt\n"
            "declare function search_replace(a: {path: string, old_string?: string, new_string?: string, "
            "replace_all?: boolean, edits?: {old_string: string, new_string: string, replace_all?: boolean}[], "
            "host?: string}): Promise<string>; // receipt\n"
            "declare function run_terminal_command(a: {command: string, cwd?: string, timeout?: number, "
            "description?: string, host?: string} | string): Promise<{status: string, exit_code: number | null, "
            "output: string, id?: string}>; // a non-zero exit does not throw; status \"running\" means it moved "
            "to the background and its result arrives later\n"
            "A line in (parentheses) in grep, glob or list_dir text is a note, not a result. A failed call throws "
            "an Error with the tool's message; catch it where a miss is expected. console.log output comes back "
            "too. Limits: 500 calls; 5 minutes (timeout, in ms, up to 10 minutes; time spent waiting on a "
            "permission prompt does not count); 400,000 characters of arguments per call; 32 MB of results per "
            "run; a returned value over 24,000 characters is cut, so return findings, not file bodies.\n"
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
            "required": ["code"],
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
    if 0 < value < 1000:
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
    (secret-swapped) text; the trace keeps real values for the user's view."""
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
    if "is not defined" in text:
        text += f"\n(run_code provides only: {', '.join(HELPERS)}, e.g. `await read_file(\"a.py\")`.{fix})"
    elif text.startswith("Not supported") or "Error: Not supported" in text:
        text += f"\n(import() is not available: run_code is not Node.js. Use {', '.join(HELPERS)}.{fix})"
    elif "is not a function" in text or "[object Promise]" in text:
        text += f"\n(Each helper is async: write `await read_file(...)`, and `await Promise.all(...)` over a map.{fix})"
    return text


async def run(arguments: str, *, call, paused=None) -> tuple[str, dict]:
    """One run_code call. call(name, args) -> (text, trace) runs a nested tool
    call as a job of this round and returns its finished result; paused() is
    true while a permission prompt waits, which stops the deadline's clock.
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
    rec = {"name": "run_code", "description": str(args.get("description") or "")[:200]}
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
            code, _timeout_ms(args.get("timeout")) / 1000, call, paused or (lambda: False))
    finally:
        slots.release()
    return _result(code, rec, outcome, kind, done, stopped)


async def _execute(code: str, timeout_s: float, call, paused):
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
            body = {"ok": False, "error": _unlabel(text, trace)[:ERROR_CHARS] or f"{name} failed."}
        else:
            body = {"ok": True, "value": _value(name, text, trace)}
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
                refuse(n, f"{name} takes one argument object.")
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
        elif not finished:
            kind, outcome = "timeout", {"ok": False, "error": f"run_code timed out after {int(timeout_s * 1000)}ms."}
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
        stopped = len(tasks)
        for task in list(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    if crashed:
        raise crashed[0]
    if outcome is None and not kind:
        if proc.returncode in (-signal.SIGXCPU, -signal.SIGKILL):
            kind, error = "cpu", f"the code used its {CPU_SECONDS} s CPU limit."
        else:
            kind, error = "worker", "the code runner stopped unexpectedly."
            logger.warning("run_code worker ended with %s: %s", proc.returncode,
                           stderr_tail.decode("utf-8", "replace").strip()[-500:])
        outcome = {"ok": False, "error": error}
    return outcome, kind, done, stopped


def _calls_line(done: list[dict], stopped: int) -> str:
    counts = collections.Counter(c["name"] for c in done)
    line = (f"[Fuse: {len(done)} calls: " + ", ".join(f"{n}×{k}" for n, k in counts.items()) + "]") if done \
        else "[Fuse: no tool calls]"
    if stopped:
        line += f"\n[Fuse: {stopped} calls were still running when the code ended and were stopped; await every call.]"
    return line


def _result(code: str, rec: dict, outcome: dict, kind: str, done: list[dict], stopped: int) -> tuple[str, dict]:
    logs = str(outcome.get("logs") or "")
    # The row shows its output: the result leads, so long code never pushes it out.
    if outcome.get("ok"):
        if not outcome.get("undef"):
            text = str(outcome.get("value")) + ("\n[console]\n" + logs if logs else "")
        else:
            text = "[console]\n" + logs if logs else "(no return value: end the code with `return …`)"
        text += "\n" + _calls_line(done, stopped)
        return text, {**rec, "status": "done", "output": f"{text}\n\n{code}"}
    error = _user_lines(str(outcome.get("error") or "run_code failed.")).removeprefix("Error: ")
    lines = [f"Error: {error}"]
    if logs:
        lines.append("[console]\n" + logs)
    if done:
        lines.append(f"Calls that finished before the error ({len(done)}):")
        lines.extend(" ".join(f"- {c['name']} {c['subject']}".split()) + f": {c['status']}" for c in done[-40:])
    else:
        lines.append("No tool calls finished before the error.")
    if stopped:
        lines.append(f"{stopped} calls still running were stopped.")
    text = "\n".join(lines)
    return text, {**rec, "status": "error", "kind": kind or "code_error", "error": error[:400], "output": f"{text}\n\n{code}"}
