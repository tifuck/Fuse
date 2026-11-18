"""run_code: model-written JavaScript that calls the workspace tools.

Each run is one disposable worker process (app/code_worker.py): a V8 isolate
with no I/O of its own, an empty environment and no root. The worker sends
each helper call back here, and `call` (tool_runner.run_tools) runs it as a
real tool job of the same round: the permission gate, confinement, secret
swap, transcript rows and failure log apply exactly as to a direct call, and
the code only sees the swapped text a direct call would return. Only what
the code returns, plus its console output, reaches the model. The worker is
killed at its deadline or when the run stops, so no script can hold the
server's event loop or outlive its run."""
from __future__ import annotations

import asyncio
import collections
import contextlib
import importlib.util
import json
import re
import sys
from pathlib import Path

from .code_worker import HELPERS, OFFSET
from .config import settings

WORKER = Path(__file__).with_name("code_worker.py")
HEAP_MB = 64
MAX_CALLS = 500
CHILD_CONCURRENCY = 16
DEFAULT_TIMEOUT_MS = 300_000
MAX_TIMEOUT_MS = 600_000
LOG_CHARS = 10_000
ERROR_CHARS = 2_000
# What one helper call may hand the code (read_file's whole-file cap).
VALUE_CHARS = 2_000_000
_LINE_LIMIT = 64 * 1024 * 1024
# Each run is a process with its own V8 heap: a burst of runs across chats
# must not start one per call.
_SLOTS = asyncio.Semaphore(8)
_INSTALLED = importlib.util.find_spec("py_mini_racer") is not None


def available() -> bool:
    return _INSTALLED and bool(settings.code_mode)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_code",
        "description": (
            "Run plain JavaScript, not Node.js (no require, fs, child_process or process): the file and shell "
            "tools are async functions, e.g. `await read_file(\"src/app.py\")` or `await grep({pattern: \"TODO\"})`. "
            "Only what the code returns comes back; intermediate results stay out of the conversation. Prefer it "
            "to several direct calls.\n"
            "The code is the body of an async function: use await, Promise.all for independent calls, and end "
            "with return. Functions (same argument object as the tool of that name; each call is "
            "permission-checked and shown to the user like a direct call):\n"
            "- read_file({path, offset?, limit?}) -> string: the text without line-number prefixes; the whole "
            "file (up to 2 MB) unless limit is given.\n"
            "- grep({...}) -> path:line:text lines; glob({...}) -> one path per line; list_dir({...}) -> one "
            "entry per line, directories end with /. Split on \"\\n\"; a line in [brackets] or (parentheses) "
            "is a note, not a match.\n"
            "- write_file({...}), search_replace({...}) -> receipt string.\n"
            "- run_terminal_command({command, cwd?, timeout?, description?}) -> {status, exit_code, output}; "
            "a non-zero exit does not throw; status \"running\" means it moved to the background and its "
            "result arrives later.\n"
            "A string argument is the path, pattern or command: read_file(\"a.py\"). A failed call throws an "
            "Error with the tool's message; catch it where a miss is expected. console.log output is returned "
            "too. No require, import, fs, process, fetch or timers. Limits: 500 calls and 5 minutes per run "
            "(timeout, in ms, up to 10 minutes); a result over 24,000 characters is cut, so return findings, "
            "not file bodies.\n"
            "Example:\n"
            "const files = (await grep({pattern: \"TODO\", include: \"*.py\", output_mode: \"files_with_matches\"}))"
            ".split(\"\\n\").filter((p) => p.endsWith(\".py\"));\n"
            "const rows = await Promise.all(files.map(async (p) => `${p}: ${(await read_file(p)).split(\"\\n\")"
            ".filter((l) => l.includes(\"TODO\")).length}`));\n"
            "const t = await run_terminal_command({command: \"pytest -q -x 2>&1 | tail -n 5\", description: "
            "\"Run the tests\"});\n"
            "return rows.join(\"\\n\") + `\\npytest exit ${t.exit_code}\\n${t.output}`;"
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


def _timeout_ms(raw) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_TIMEOUT_MS
    if 0 < value < 1000:
        value *= 1000
    return max(1000, min(value, MAX_TIMEOUT_MS))


def _subject(args: dict) -> str:
    text = " ".join(str(args.get("path") or args.get("pattern") or args.get("command") or "").split())
    return text[:80] + ("…" if len(text) > 80 else "")


def _unlabel(text: str, trace: dict) -> str:
    host = str(trace.get("host") or "")
    return text.removeprefix(f"From host {host}:\n") if host else text


def _value(name: str, text: str, trace: dict):
    """What a helper returns to the code, built only from the call's own
    (secret-swapped) text; the trace keeps real values for the user's view."""
    body = _unlabel(text or "", trace)
    if len(body) > VALUE_CHARS:
        body = body[:VALUE_CHARS] + f"\n[Fuse: cut at {VALUE_CHARS} characters]"
    if name != "run_terminal_command":
        return body
    head, _, output = body.partition("\n\n")
    fields = dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)
    exit_code = trace.get("exit_code")
    return {
        "status": fields.get("status") or str(trace.get("status") or ""),
        "exit_code": exit_code if isinstance(exit_code, int) else None,
        "output": output,
    }


def _user_lines(stack: str) -> str:
    """V8 positions as the model's own line numbers; frames in the prelude
    (a helper throwing) are dropped."""
    out = []
    for line in str(stack).splitlines():
        m = re.search(r"<anonymous>:(\d+)", line)
        if m and int(m.group(1)) <= OFFSET:
            continue
        out.append(re.sub(r"<anonymous>:(\d+)", lambda m: f"line {int(m.group(1)) - OFFSET}", line))
    text = "\n".join(out)
    if "is not defined" in text:
        text += f"\n(run_code provides only: {', '.join(HELPERS)}.)"
    elif "is not a function" in text or "[object Promise]" in text:
        text += "\n(Each helper is async: write `await read_file(...)`, and `await Promise.all(...)` over a map.)"
    return text


async def run(arguments: str, *, call, cancel: asyncio.Event | None = None) -> tuple[str, dict]:
    """One run_code call. call(name, args) -> (text, trace) runs a nested tool
    call as a job of this round and returns its finished result."""
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
    async with _SLOTS:
        outcome, kind, done, stopped = await _execute(code, _timeout_ms(args.get("timeout")) / 1000, call, cancel)
    return _result(code, rec, outcome, kind, done, stopped)


async def _execute(code: str, timeout_s: float, call, cancel):
    done: list[dict] = []
    tasks: set[asyncio.Task] = set()
    children = asyncio.Semaphore(CHILD_CONCURRENCY)
    started = 0
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", str(WORKER), str(HEAP_MB),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env={}, cwd="/", limit=_LINE_LIMIT,
    )

    async def send(obj) -> None:
        if proc.stdin is None or proc.stdin.is_closing():
            return
        proc.stdin.write(json.dumps(obj).encode() + b"\n")
        with contextlib.suppress(ConnectionError):
            await proc.stdin.drain()

    async def answer(n: int, name: str, call_args: dict) -> None:
        try:
            async with children:
                text, trace = await call(name, call_args)
        except Exception:
            # The tool runner settles its own failures; whatever escapes it
            # must still answer the waiting code.
            text, trace = "Tool failed.", {"status": "error"}
        done.append({"name": name, "subject": _subject(call_args), "status": str(trace.get("status") or "")})
        if trace.get("status") in ("error", "interrupted"):
            body = {"ok": False, "error": _unlabel(text or "", trace)[:ERROR_CHARS] or f"{name} failed."}
        else:
            body = {"ok": True, "value": _value(name, text, trace)}
        await send({"reply": n, "body": json.dumps(body)})

    async def refuse(n: int, error: str) -> None:
        await send({"reply": n, "body": json.dumps({"ok": False, "error": error})})

    async def pump():
        # The worker runs model code: every line from it is checked like input.
        nonlocal started
        await send({"code": code})
        while line := await proc.stdout.readline():
            msg = json.loads(line)
            if not isinstance(msg, dict):
                return None
            if "done" in msg:
                return msg["done"] if isinstance(msg["done"], dict) else None
            n, name, raw = msg.get("call"), msg.get("name"), msg.get("args")
            if not isinstance(n, int) or not isinstance(name, str) or not isinstance(raw, str):
                return None
            if name not in HELPERS:
                await refuse(n, f"{name} is not available in run_code (available: {', '.join(HELPERS)}).")
                continue
            try:
                call_args = json.loads(raw)
            except (ValueError, RecursionError):
                call_args = None
            if not isinstance(call_args, dict):
                await refuse(n, f"{name} takes one argument object.")
            elif name == "run_terminal_command" and call_args.get("background"):
                await refuse(n, "background commands are not available in run_code; call run_terminal_command directly.")
            elif started >= MAX_CALLS:
                await refuse(n, f"run_code allows at most {MAX_CALLS} tool calls per run.")
            else:
                started += 1
                task = asyncio.ensure_future(answer(n, name, call_args))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        return None

    reader = asyncio.ensure_future(pump())
    stop = asyncio.ensure_future(cancel.wait()) if cancel is not None else None
    outcome, kind = None, ""
    try:
        finished, _ = await asyncio.wait({reader, *([stop] if stop else [])}, timeout=timeout_s,
                                         return_when=asyncio.FIRST_COMPLETED)
        if reader in finished:
            try:
                outcome = reader.result()
            except (ValueError, RecursionError, KeyError):
                outcome = None
            if outcome is None:
                kind, outcome = "worker", {"ok": False, "error": "the code runner stopped unexpectedly."}
            elif not outcome.get("ok"):
                kind = "memory" if "memory limit" in str(outcome.get("error") or "").lower() else "code_error"
        elif stop is not None and stop in finished:
            kind, outcome = "interrupted", {"ok": False, "error": "run_code was stopped."}
        else:
            kind, outcome = "timeout", {"ok": False, "error": f"run_code timed out after {int(timeout_s * 1000)}ms."}
    finally:
        if stop is not None:
            stop.cancel()
        reader.cancel()
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        await proc.wait()
        # Calls the code left running (never awaited, or cut off) stop with it.
        stopped = len(tasks)
        for task in list(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    return outcome, kind, done, stopped


def _calls_line(done: list[dict], stopped: int) -> str:
    counts = collections.Counter(c["name"] for c in done)
    line = (f"[Fuse: {len(done)} calls: " + ", ".join(f"{n}×{k}" for n, k in counts.items()) + "]") if done \
        else "[Fuse: no tool calls]"
    if stopped:
        line += f"\n[Fuse: {stopped} calls were still running when the code ended and were stopped; await every call.]"
    return line


def _result(code: str, rec: dict, outcome: dict, kind: str, done: list[dict], stopped: int) -> tuple[str, dict]:
    logs = "\n".join(str(item) for item in (outcome.get("logs") or []))
    if len(logs) > LOG_CHARS:
        logs = logs[:LOG_CHARS] + f"\n[console output truncated; {len(logs)} characters total]"
    if outcome.get("ok"):
        text = "(no return value: end the code with `return …`)" if outcome.get("undef") else str(outcome.get("value"))
        if logs:
            text += "\n[console]\n" + logs
        text += "\n" + _calls_line(done, stopped)
        return text, {**rec, "status": "done", "output": f"{code}\n\n{text}"}
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
    status = "interrupted" if kind == "interrupted" else "error"
    # A failed row shows its output as the error: the message leads, the code follows.
    return text, {**rec, "status": status, "kind": kind or "code_error", "error": error[:400], "output": f"{text}\n\n{code}"}
