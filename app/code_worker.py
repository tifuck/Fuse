"""run_code worker: one model-written script in a disposable V8 isolate.

argv[1] is the isolate heap limit in MB. stdin/stdout carry JSON lines: the
parent (app/code_mode.py) sends {"code": ...}; each helper call goes out as
{"call": n, "name": ..., "args": "<json>"} and waits for {"reply": n,
"body": "<json>"}; the run ends with {"done": <outcome>}. The worker has no
tools of its own: the parent runs every call as a real tool job. It starts
with an empty environment; once V8 is loaded it leaves the network (a new
network namespace), sets no_new_privs and gives up root, and once its
isolate exists it may not start a process or thread, before it runs the code.
Without root the namespace and the uid change are skipped: the server then
shares its own uid with the worker.
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import resource
import sys

HELPERS = ("read_file", "list_dir", "grep", "glob", "write_file", "search_replace", "run_terminal_command")
# The argument a bare string stands for: read_file("a.py"), grep("TODO").
_STRING_ARG = {"read_file": "path", "list_dir": "path", "grep": "pattern", "glob": "pattern",
               "run_terminal_command": "command"}
# One call's arguments, as for a direct call (chat_generation.parsing._ARG_LIMIT_FILE).
ARG_CHARS = 400_000
# A line the worker sends: a call's arguments (JSON-escaped) or the outcome.
MESSAGE_LIMIT = 4 * 1024 * 1024
# A line the parent sends: a helper's result (read_file returns up to 2 MB).
REPLY_LIMIT = 64 * 1024 * 1024
# What the outcome carries back; the model is shown far less of either (the
# rest of the value stays readable with read_result). Even escaped as \u
# pairs, a value this long still fits one MESSAGE_LIMIT line.
RESULT_CHARS = 300_000
LOG_CHARS = 10_000
# CPU seconds; time spent awaiting helpers costs none.
CPU_SECONDS = 120
_FIX = "Fix the code and call run_code again."

PRELUDE = r"""
const __call = globalThis.__fuse_call;
delete globalThis.__fuse_call;
for (const name of ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Atomics", "SharedArrayBuffer", "WebAssembly"]) {
  try { delete globalThis[name]; } catch (_) {}
  globalThis[name] = undefined;
}
globalThis.require = (name) => {
  throw new Error(`require(${JSON.stringify(String(name))}) is not available: run_code is not Node.js. ` +
    "Read files with `const text = await read_file(\"src/app.py\")` (and list_dir, glob, grep, write_file, search_replace, " +
    "run_terminal_command the same way). %FIX%");
};
const __logs = [];
const console = {};
console.log = console.info = console.warn = console.error = console.debug = (...parts) => {
  __logs.push(parts.map((p) => typeof p === "string" ? p : JSON.stringify(p)).join(" "));
};
const __tool = (name, key) => async (args, content) => {
  if (typeof args === "string" && key) args = {[key]: args};
  // write_file(path, content), the habit of every file API.
  else if (name === "write_file" && typeof args === "string" && typeof content === "string") args = {path: args, content};
  const reply = JSON.parse(await __call(name, JSON.stringify(args === undefined ? {} : args)));
  if (!reply.ok) throw new Error(reply.error);
  return reply.value;
};
// The rest of an earlier run's cut result (the parent keeps it a while).
const read_result = (id, opts) => __tool("read_result", "")({...(opts || {}), id});
""".replace("%FIX%", _FIX) + "".join(
    f"const {name} = __tool({json.dumps(name)}, {json.dumps(_STRING_ARG.get(name, ''))});\n" for name in HELPERS)

# `return` and `await` work at the code's top level. One script, so stack
# lines up to OFFSET are the prelude's and the code's line n is OFFSET + n.
HEAD = "(async () => {\n"
TAIL = """
})().then(
  (v) => { try { return JSON.stringify({ok: true, undef: v === undefined,
             value: typeof v === "string" ? v : JSON.stringify(v, null, 2), logs: __logs}); }
           catch (e) { return JSON.stringify({ok: false, error: "the return value is not JSON-serializable: " + e, logs: __logs}); } },
  (e) => JSON.stringify({ok: false, error: String((e && e.stack) || e), logs: __logs}))
"""
OFFSET = (PRELUDE + HEAD).count("\n")


def _write(obj) -> None:
    sys.stdout.buffer.write(json.dumps(obj).encode() + b"\n")
    sys.stdout.buffer.flush()


def _confine() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if os.getuid() == 0:
        libc.unshare(0x40000000)  # CLONE_NEWNET; refused without CAP_SYS_ADMIN (some containers)
    libc.prctl(38, 1, 0, 0, 0)  # PR_SET_NO_NEW_PRIVS
    if os.getuid() == 0:
        os.setgroups([])
        os.setgid(65534)
        os.setuid(65534)


def _clip(outcome: dict) -> dict:
    value = outcome.get("value")
    if isinstance(value, str) and len(value) > RESULT_CHARS:
        outcome["value"] = value[:RESULT_CHARS] + f"\n[Fuse: return value cut at {RESULT_CHARS} characters]"
    logs = "\n".join(str(item) for item in outcome.get("logs") or [])
    if len(logs) > LOG_CHARS:
        logs = logs[:LOG_CHARS] + f"\n[console output cut; {len(logs)} characters total]"
    outcome["logs"] = logs
    if isinstance(outcome.get("error"), str):
        outcome["error"] = outcome["error"][:LOG_CHARS]
    return outcome


async def _main() -> None:
    heap = int(sys.argv[1]) * 1024 * 1024
    # V8 reserves a large address space, so RLIMIT_AS would stop it; data
    # (what it commits) and CPU are the backstops behind the parent's kill.
    resource.setrlimit(resource.RLIMIT_DATA, (heap * 8, heap * 8))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    from py_mini_racer import JSEvalException, init_mini_racer, mini_racer

    init_mini_racer(flags=("--single-threaded", "--jitless"))
    _confine()
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=REPLY_LIMIT)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    first = json.loads(await reader.readline())
    code = str(first.get("code") or "")
    waiting: dict[int, asyncio.Future] = {}
    count = 0

    async def replies():
        while line := await reader.readline():
            msg = json.loads(line)
            fut = waiting.pop(int(msg["reply"]), None)
            if fut is not None and not fut.done():
                fut.set_result(str(msg["body"]))

    async def bridge(name, args_json):
        nonlocal count
        args_json = str(args_json)
        if len(args_json) > ARG_CHARS:
            return json.dumps({"ok": False, "error": f"{name}'s arguments are over {ARG_CHARS} characters; split the "
                                                     "work (for example, write the file in parts)."})
        count += 1
        fut = loop.create_future()
        waiting[count] = fut
        _write({"call": count, "name": str(name), "args": args_json})
        return await fut

    pump = asyncio.ensure_future(replies())
    outcome = None
    try:
        with mini_racer() as ctx:
            ctx.set_hard_memory_limit(heap)
            # The isolate's own thread exists now; nothing may start another.
            resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
            async with ctx.wrap_py_function(bridge) as js_bridge:
                ctx.eval("this")["__fuse_call"] = js_bridge
                outcome = json.loads(await ctx.eval(PRELUDE + HEAD + code + TAIL))
    except JSEvalException as exc:
        # Also raised while closing an isolate that hit its memory limit.
        outcome = outcome or {"ok": False, "error": str(exc)}
    finally:
        pump.cancel()
    _write({"done": _clip(outcome)})


if __name__ == "__main__":
    asyncio.run(_main())
