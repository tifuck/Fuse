"""run_code worker: one model-written script in a disposable V8 isolate.

argv[1] is the isolate heap limit in MB. stdin/stdout carry JSON lines: the
parent (app/code_mode.py) sends {"code": ...}; each helper call goes out as
{"call": n, "name": ..., "args": "<json>"} and waits for {"reply": n,
"body": "<json>"}; the run ends with {"done": <outcome>}. The worker has no
tools of its own: the parent runs every call as a real tool job. It is
started with an empty environment and gives up root, if it has it, once V8
is loaded, before it reads the code.
"""
from __future__ import annotations

import asyncio
import json
import os
import resource
import sys

HELPERS = ("read_file", "list_dir", "grep", "glob", "write_file", "search_replace", "run_terminal_command")
# The argument a bare string stands for: read_file("a.py"), grep("TODO").
_STRING_ARG = {"read_file": "path", "list_dir": "path", "grep": "pattern", "glob": "pattern",
               "run_terminal_command": "command"}

PRELUDE = r"""
const __call = globalThis.__fuse_call;
delete globalThis.__fuse_call;
for (const name of ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Atomics", "SharedArrayBuffer", "WebAssembly"]) {
  try { delete globalThis[name]; } catch (_) {}
  globalThis[name] = undefined;
}
globalThis.require = (name) => {
  throw new Error(`require(${JSON.stringify(String(name))}) is not available: run_code is not Node.js. ` +
    "Use the async helpers instead: await read_file({path}), list_dir, glob, grep, write_file, search_replace, run_terminal_command.");
};
const __logs = [];
const console = {};
console.log = console.info = console.warn = console.error = console.debug = (...parts) => {
  __logs.push(parts.map((p) => typeof p === "string" ? p : JSON.stringify(p)).join(" "));
};
const __tool = (name, key) => async (args) => {
  if (typeof args === "string" && key) args = {[key]: args};
  const reply = JSON.parse(await __call(name, JSON.stringify(args === undefined ? {} : args)));
  if (!reply.ok) throw new Error(reply.error);
  return reply.value;
};
""" + "".join(f"const {name} = __tool({json.dumps(name)}, {json.dumps(_STRING_ARG.get(name, ''))});\n" for name in HELPERS)

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
LINE_LIMIT = 64 * 1024 * 1024


def _write(obj) -> None:
    sys.stdout.buffer.write(json.dumps(obj).encode() + b"\n")
    sys.stdout.buffer.flush()


def _drop_root() -> None:
    if os.getuid() == 0:
        os.setgroups([])
        os.setgid(65534)
        os.setuid(65534)


async def _main() -> None:
    heap = int(sys.argv[1]) * 1024 * 1024
    # V8 reserves a large address space, so RLIMIT_AS would stop it; data
    # (what it commits) and CPU are the backstops behind the parent's kill.
    resource.setrlimit(resource.RLIMIT_DATA, (heap * 8, heap * 8))
    resource.setrlimit(resource.RLIMIT_CPU, (660, 660))
    from py_mini_racer import JSEvalException, init_mini_racer, mini_racer

    init_mini_racer(flags=("--single-threaded", "--jitless"))
    _drop_root()
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=LINE_LIMIT)
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
        count += 1
        fut = loop.create_future()
        waiting[count] = fut
        _write({"call": count, "name": str(name), "args": str(args_json)})
        return await fut

    pump = asyncio.ensure_future(replies())
    outcome = None
    try:
        with mini_racer() as ctx:
            ctx.set_hard_memory_limit(heap)
            async with ctx.wrap_py_function(bridge) as js_bridge:
                ctx.eval("this")["__fuse_call"] = js_bridge
                outcome = json.loads(await ctx.eval(PRELUDE + HEAD + code + TAIL))
    except JSEvalException as exc:
        # Also raised while closing an isolate that hit its memory limit.
        outcome = outcome or {"ok": False, "error": str(exc)}
    finally:
        pump.cancel()
    _write({"done": outcome})


if __name__ == "__main__":
    asyncio.run(_main())
