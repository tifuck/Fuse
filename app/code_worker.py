"""run_code kernel: one chat's JavaScript context in a disposable V8 isolate.

argv[1] is the isolate heap limit in MB. stdin/stdout carry JSON lines. Once
sandboxed the worker answers {"ready": true}; the parent then sends runs:
{"run": n, "code": ..., "budget": chars, "shells": {host: "bash" |
"powershell"}}. Each helper call goes out as {"call": k, "run": n, "name":
..., "args": "<json>", "pending": [that run's calls still without a reply]}
and waits for {"reply": k, "body": "<json>"}; a run ends with {"done":
<outcome>, "run": n}, whose "kind" (memory) only the worker sets. Runs share
one context, so top-level declarations persist from run to run
(__fuse_transform) and a run moved to the background goes on beside the
next. The worker has no tools of its own: the parent runs every call as a
real tool job. It starts with an empty environment; once V8 is loaded it
leaves the network (a new network namespace), sets no_new_privs and gives up
root, and once its isolate exists it may not start a process or thread,
before it runs any code. If any of that fails, or the server is not root (no
uid to drop to), it runs nothing and answers {"done": ...} with SANDBOX_FAILED
and kind unavailable, unless the parent set FUSE_CODE_UNCONFINED
(app/code_mode.py UNCONFINED).
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import re
import resource
import sys
import time
from pathlib import Path

HELPERS = ("read_file", "list_dir", "grep", "glob", "write_file", "search_replace", "run_terminal_command",
           "web_search", "read_url", "spawn_subagent", "get_subagent_output")
# The argument a bare string stands for: read_file("a.py"), grep("TODO").
_STRING_ARG = {"read_file": "path", "list_dir": "path", "grep": "pattern", "glob": "pattern",
               "run_terminal_command": "command", "web_search": "query", "read_url": "url",
               "spawn_subagent": "prompt", "get_subagent_output": "id"}
# Every name a run's code gets from its helpers. A top-level declaration of
# one of these stays local to its run instead of replacing the helper for good.
LOCAL = (*HELPERS, "sh", "read_result", "$", "edit", "write", "file", "preview", "sleep", "log", "self", "console")
# One call's arguments, as for a direct call (chat_generation.parsing._ARG_LIMIT_FILE).
ARG_CHARS = 400_000
# A line the worker sends: a call's arguments (JSON-escaped) or the outcome.
# Sent as UTF-8, so ARG_CHARS characters take at most 4 bytes each.
MESSAGE_LIMIT = 4 * 1024 * 1024
# A line the parent sends: a helper's result (read_file returns up to 2 MB).
REPLY_LIMIT = 64 * 1024 * 1024
# What the outcome carries back: the shaped value is within the run's budget;
# the uncut one (kept for read_result) and the console output stop here. Even
# escaped as \u pairs, a value this long still fits one MESSAGE_LIMIT line.
RESULT_CHARS = 300_000
LOG_CHARS = 10_000
# The output an sh failure carries: its tail, so its message and stack fit LOG_CHARS.
SH_TAIL = 8_000
# CPU seconds per run (time spent awaiting helpers costs none), and for the
# kernel's whole life: past that it ends and the chat's next run starts fresh.
CPU_SECONDS = 120
KERNEL_CPU_SECONDS = 1800
ACORN = Path(__file__).with_name("vendor") / "acorn.js"
SOURCE = "code.js"
# What __fuse_transform puts before a last expression, on its line.
RETURN = "return ("
_FIX = "Fix the code and call run_code again."
SANDBOX_FAILED = "run_code unavailable: sandbox could not be set up"

PRELUDE = r"""(() => {
const call = globalThis.__fuse_call;
delete globalThis.__fuse_call;
const acorn = globalThis.acorn;
delete globalThis.acorn;
for (const name of ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Atomics", "SharedArrayBuffer", "WebAssembly"]) {
  try { delete globalThis[name]; } catch (_) {}
  globalThis[name] = undefined;
}
globalThis.require = (name) => {
  throw new Error(`require(${JSON.stringify(String(name))}) is not available: run_code is not Node.js. ` +
    "Read files with `const text = await read_file(\"src/app.py\")` (and list_dir, glob, grep, write_file, search_replace, " +
    "run_terminal_command, sh and $ the same way). %FIX%");
};
const hidden = (name, value) => Object.defineProperty(globalThis, name, {value, enumerable: false});
const HELPERS = %HELPERS%;
const LOCAL = new Set(%LOCAL%);
const RETURN = %RETURN%;
const plain = (v) => v !== null && typeof v === "object" && [Object.prototype, null].includes(Object.getPrototypeOf(v));
const isText = (x) => typeof x === "string" && x.includes("\n");

class ShellOutput {
  constructor(r, command, note) {
    this.stdout = String(r.output || "");
    this.exitCode = typeof r.exit_code === "number" ? r.exit_code : null;
    this.exit_code = this.exitCode;
    this.status = String(r.status || "");
    if (r.id) this.id = r.id;
    if (this.status === "running") this.running = {id: r.id};
    if (note) this.note = note;
    this.output = this.stdout + (this.exitCode ? `\n[exit ${this.exitCode}]` : "") + (note ? `\n[${note}]` : "");
  }
  text() { return this.stdout; }
  lines() { const l = this.stdout.split("\n"); if (l.length && l[l.length - 1] === "") l.pop(); return l; }
  json() { return JSON.parse(this.stdout); }
  toJSON() { return this.output; }
}
const flat = (x) => x instanceof ShellOutput ? x.output : x;

// One JSON form for the return value and console.log, for what JSON drops or refuses.
const json = (k, v) => v instanceof Error ? String(v) : v instanceof Map ? Object.fromEntries(v)
  : v instanceof Set ? [...v] : typeof v === "bigint" ? String(v) : v;
// The returned value as text: an object holding multi-line text shows one entry
// per line, that text raw under a --- key --- line; anything else is JSON.
const render = (v) => {
  if (typeof v === "string") return v;
  if (!plain(v) || !Object.values(v).some(isText)) return JSON.stringify(v, json, 2);
  return Object.entries(v).map(([k, x]) => isText(x) ? `--- ${k} ---\n${x}` : `${k}: ${JSON.stringify(x, json)}`).join("\n");
};
// Head and tail of a text over n characters, with a marker where the middle went.
const cut = (s, n) => {
  if (s.length <= n) return s;
  const keep = Math.max(0, n - 60), head = Math.ceil(keep * 0.6), tail = keep - head;
  return s.slice(0, head) + `\n[… ${s.length - head - tail} characters cut …]\n` + (tail ? s.slice(-tail) : "");
};
// The value within budget, cut part by part (each text of an object gets a fair
// share, so one long log does not crowd out the rest), and the uncut text.
const shape = (v, budget) => {
  const full = render(v);
  if (full === undefined || full.length <= budget) return [full, full];
  if (!plain(v) || !Object.values(v).some(isText)) return [cut(full, budget), full];
  const parts = Object.entries(v).map(([k, x]) => isText(x) ? [`--- ${k} ---\n`, x] : [`${k}: `, JSON.stringify(x, json)]);
  const order = parts.map((_, i) => i).sort((a, b) => parts[a][1].length - parts[b][1].length);
  let left = budget - parts.reduce((n, p) => n + p[0].length + 1, 0);
  const share = [];
  order.forEach((i, k) => { share[i] = Math.min(parts[i][1].length, Math.max(200, Math.floor(left / (order.length - k)))); left -= share[i]; });
  return [parts.map((p, i) => p[0] + cut(p[1], share[i])).join("\n"), full];
};

// A command line's last command, for the exit 1 that means "no match" or "they differ".
const NO_MATCH = /^(?:grep|egrep|fgrep|rg|find|diff|test|\[|git\s+(?:diff|grep))(?:\s|$)/;
const noMatch = (command, code) => {
  if (code !== 1) return false;
  const parts = String(command).split(/\|\||&&|[|;\n]/).map((s) => s.trim()).filter(Boolean);
  const last = (parts.length ? parts[parts.length - 1] : "").replace(/^(?:!\s*)?(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*/, "");
  return NO_MATCH.test(last);
};
const QUOTE = {
  bash: (s) => "'" + s.replace(/'/g, "'\\''") + "'",
  powershell: (s) => "'" + s.replace(/'/g, "''") + "'",
};
const quoted = (v, q) => v !== null && typeof v === "object" && !Array.isArray(v) && "raw" in v ? String(v.raw)
  : Array.isArray(v) ? v.map((x) => quoted(x, q)).join(" ") : q(String(v));
const shError = (command, what, out) => {
  let line = String(command).split(/\s+/).join(" ").trim();
  if (line.length > 80) line = line.slice(0, 80) + "…";
  const cutOut = out.length > %SH_TAIL%;
  const e = new Error(`sh ${line}: ${what}\n` + (!out ? "(no output)" : cutOut
    ? `--- output, last %SH_TAIL% of ${out.length} characters ---\n` + out.slice(-%SH_TAIL%) : "--- output ---\n" + out));
  e.__fuse_sh = true;
  return e;
};

const shell = (h, shells) => {
  const quoteFor = (host) => QUOTE[shells[String(host || "").trim().toLowerCase()] || shells[""] || "bash"] || QUOTE.bash;
  class ShellPromise {
    constructor(build, opts) { this.build = build; this.opts = Object.assign({}, opts); this.throwing = true; this.started = null; }
    nothrow() { this.throwing = false; return this; }
    throws(on) { this.throwing = on !== false; return this; }
    quiet() { return this; }
    timeout(ms) { this.opts.timeout = ms; return this; }
    cwd(dir) { this.opts.cwd = dir; return this; }
    host(name) { this.opts.host = name; return this; }
    env(names) { this.opts.env = names; return this; }
    start() {
      if (!this.started) this.started = (async () => {
        const command = this.build(quoteFor(this.opts.host));
        const r = await h.run_terminal_command(Object.assign({}, this.opts, {command}));
        const code = typeof r.exit_code === "number" ? r.exit_code : null;
        const note = r.status === "running"
          ? `moved to the background as ${r.id} and has not finished; do not run it again: its result arrives later as a command result`
          : noMatch(command, code) ? "exit 1 here means no match (or a difference), not an error" : "";
        const out = new ShellOutput(r, command, note);
        if (r.status === "running" || code === 0 || note || !this.throwing) return out;
        const e = shError(command, code === null ? `ended with status ${r.status} and no exit code` : `exited ${code}`, out.stdout);
        Object.assign(e, {stdout: out.stdout, exitCode: code});
        throw e;
      })();
      return this.started;
    }
    then(a, b) { return this.start().then(a, b); }
    catch(b) { return this.start().catch(b); }
    finally(f) { return this.start().finally(f); }
    text() { return this.then((o) => o.text()); }
    lines() { return this.then((o) => o.lines()); }
    json() { return this.then((o) => o.json()); }
  }
  const $ = (first, ...rest) => {
    if (Array.isArray(first) && Array.isArray(first.raw)) {
      return new ShellPromise((q) => first.raw.map((s, i) => s.replace(/\\`/g, "`").replace(/\\\$\{/g, "${")
        + (i < rest.length ? quoted(rest[i], q) : "")).join(""), {});
    }
    const opts = typeof rest[0] === "number" ? {timeout: rest[0]} : Object.assign({}, rest[0] || {});
    return new ShellPromise(() => String(first), opts);
  };
  $.escape = (s) => quoteFor("")(String(s));
  return $;
};

hidden("__fuse_helpers", (run, shells) => {
  const logs = [];
  const say = (...parts) => {
    logs.push(parts.map((p) => p instanceof ShellOutput ? p.output : p !== null && typeof p === "object" && !(p instanceof Error)
      ? JSON.stringify(p, json) : String(p)).join(" "));
  };
  const send = async (name, args) => {
    const reply = JSON.parse(await call(run, name, JSON.stringify(args === undefined ? {} : args)));
    if (!reply.ok) throw new Error(reply.error);
    return reply.value;
  };
  const h = {__logs: logs, log: say, console: {log: say, info: say, warn: say, error: say, debug: say}};
  // The raw bridge, as __h.__call(name, argsJson): the parent checks every call it gets, so this reaches nothing
  // the helpers do not (the tests send malformed calls through it).
  h.__call = (name, raw) => call(run, String(name), String(raw));
  for (const [name, key] of HELPERS) {
    h[name] = async (args, more) => {
      // A string and options, merged as sh merges them: grep("TODO", {path: "src"}).
      if (typeof args === "string" && key) args = Object.assign({[key]: args}, typeof more === "object" ? more : {});
      // write_file(path, content), the habit of every file API.
      else if (name === "write_file" && typeof args === "string" && typeof more === "string") args = {path: args, content: more};
      return send(name, args);
    };
  }
  // The rest of an earlier run's cut result (the parent keeps it a while).
  h.read_result = (id, opts) => send("read_result", Object.assign({}, opts || {}, {id}));
  h.sleep = (ms) => send("sleep", {ms: Number(ms) || 0});
  // sh(command, options?): a step that must succeed. On top of run_terminal_command,
  // so the parent sees (and gates) a plain run_terminal_command call. The error
  // is marked so the parent shows the output as it is.
  h.sh = async (a, more) => {
    const args = more === undefined ? a : Object.assign({}, typeof a === "string" ? {command: a} : a, more);
    const command = typeof args === "string" ? args : args.command;
    const r = await h.run_terminal_command(args);
    if ((r.exit_code === 0 || noMatch(command, r.exit_code)) && r.status !== "running") return r.output;
    throw shError(command, r.status === "running"
      ? `moved to the background as ${r.id} and has not finished; do not run it again: its result arrives later as a command result`
      : typeof r.exit_code === "number" ? `exited ${r.exit_code}` : `ended with status ${r.status} and no exit code`, r.output || "");
  };
  h.$ = shell(h, shells || {});
  h.edit = async (path, a, b, c) => {
    const opts = (Array.isArray(a) ? b : c) || {};
    const args = Array.isArray(a)
      ? {path, edits: a.map(([o, n]) => ({old_string: o, new_string: n, replace_all: !!opts.all}))}
      : {path, old_string: a, new_string: b, replace_all: !!opts.all};
    if (opts.host) args.host = opts.host;
    try {
      const receipt = await h.search_replace(args);
      const m = /\((\d+) replacements?/.exec(receipt);
      return {ok: true, replacements: m ? Number(m[1]) : (args.edits ? args.edits.length : 1)};
    } catch (e) {
      if (opts.soft) return {ok: false, replacements: 0, error: e.message};
      throw e;
    }
  };
  h.write = async (path, content, opts) => {
    await h.write_file(Object.assign({path, content: String(content)}, opts && opts.host ? {host: opts.host} : {}));
    return unescape(encodeURIComponent(String(content))).length;
  };
  h.file = (path, opts) => {
    const host = opts && opts.host ? {host: opts.host} : {};
    const text = () => h.read_file(Object.assign({path}, host));
    return {
      name: path,
      text,
      lines: async () => { const l = (await text()).split("\n"); if (l.length && l[l.length - 1] === "") l.pop(); return l; },
      json: async () => JSON.parse(await text()),
      slice: (offset, limit) => h.read_file(Object.assign({path, offset: offset || 1}, limit ? {limit} : {}, host)),
      exists: async () => {
        try { await h.read_file(Object.assign({path, limit: 1}, host)); return true; }
        catch (e) {
          if (/no such file|not found|does not exist|cannot find/i.test(e.message)) return false;
          if (/directory/i.test(e.message)) return true;
          throw e;
        }
      },
    };
  };
  h.preview = (value, n) => {
    const s = typeof value === "string" ? value : value instanceof ShellOutput ? value.output : String(render(value));
    const most = n === undefined ? 200 : Math.max(0, Number(n) || 0);
    return s.length <= most ? s : s.slice(0, most) + ` … [${s.length} characters]`;
  };
  h.self = Object.assign(Object.create(globalThis), {log: say});
  return h;
});

// The names a binding pattern declares.
const names = (p, out) => {
  if (!p) return out;
  if (p.type === "Identifier") out.push(p.name);
  else if (p.type === "ObjectPattern") for (const q of p.properties) names(q.type === "RestElement" ? q.argument : q.value, out);
  else if (p.type === "ArrayPattern") for (const q of p.elements) names(q, out);
  else if (p.type === "RestElement") names(p.argument, out);
  else if (p.type === "AssignmentPattern") names(p.left, out);
  return out;
};
// REPL semantics for one run's code, keeping its line and column numbers for
// stack lines: top-level const/let/var, function and class declarations become
// globals of the context (so the next run sees them), and a last expression
// statement is the run's value. Names of the run's helpers stay local. What
// cannot go in place goes on the wrapper's line before the code ("head").
hidden("__fuse_transform", (code) => {
  let ast;
  try {
    ast = acorn.parse(code, {ecmaVersion: "latest", sourceType: "script", allowAwaitOutsideFunction: true,
                             allowReturnOutsideFunction: true, locations: true});
  } catch (e) {
    const at = e.loc ? ` (line ${e.loc.line}, column ${e.loc.column + 1})` : "";
    return JSON.stringify({ok: false, error: `SyntaxError: ${String(e.message).replace(/ \(\d+:\d+\)$/, "")}${at}`});
  }
  const edits = [], hoisted = [];
  for (const s of ast.body) {
    if (s.type === "VariableDeclaration") {
      if (s.declarations.some((d) => names(d.id, []).some((n) => LOCAL.has(n)))) continue;
      // The keyword becomes ";" and spaces, newlines kept: nothing after it moves.
      edits.push([s.start, s.declarations[0].start, ";" + code.slice(s.start + 1, s.declarations[0].start).replace(/[^\n]/g, " ")]);
      for (const d of s.declarations) {
        if (d.id.type === "ObjectPattern") edits.push([d.start, d.start, "("], [d.end, d.end, ")"]);
        if (!d.init) edits.push([d.id.end, d.id.end, " = undefined"]);
      }
    } else if (s.type === "FunctionDeclaration" && s.id && !LOCAL.has(s.id.name)) {
      hoisted.push(s.id.name);
    } else if (s.type === "ClassDeclaration" && s.id && !LOCAL.has(s.id.name)) {
      edits.push([s.start, s.start, `;${s.id.name} = `], [s.end, s.end, ";"]);
    }
  }
  const last = ast.body[ast.body.length - 1];
  let shift = null;
  if (last && last.type === "ExpressionStatement") {
    // The statement's own range: an expression's leaves out its parentheses. "return (" goes on the
    // statement's own line (V8 places its errors there); stack columns after it are moved back.
    const end = code[last.end - 1] === ";" ? last.end - 1 : last.end;
    edits.push([last.start, last.start, RETURN], [end, end, ");"]);
    shift = [last.loc.start.line, last.loc.start.column];
  }
  let out = code;
  edits.map((e, i) => [...e, i]).sort((a, b) => b[0] - a[0] || b[3] - a[3])
    .forEach(([start, end, text]) => { out = out.slice(0, start) + text + out.slice(end); });
  const head = hoisted.map((n) => `globalThis[${JSON.stringify(n)}] = ${n};`).join(" ");
  return JSON.stringify({ok: true, code: out, head, shift});
});

hidden("__fuse_settle", async (v, h, budget) => {
  v = flat(v);
  if (plain(v)) {
    // Promises inside a returned object are awaited: ({a: $`git status`}).
    v = Object.fromEntries(await Promise.all(Object.entries(v).map(async ([k, x]) =>
      [k, flat(x !== null && typeof x === "object" && typeof x.then === "function" ? await x : x)])));
  }
  try {
    if (v === undefined) return JSON.stringify({ok: true, undef: true, value: "", logs: h.__logs});
    const [value, full] = shape(v, budget);
    if (value === undefined) throw new TypeError("a " + typeof v + " has no JSON form; return a string, number, array or object");
    return JSON.stringify({ok: true, undef: false, value, full: full === value ? undefined : full, logs: h.__logs});
  } catch (e) {
    return JSON.stringify({ok: false, error: "the return value is not JSON-serializable: " + e, logs: h.__logs});
  }
});

hidden("__fuse_fail", (e, h) => JSON.stringify(e && e.__fuse_sh
  // An sh failure: the message (with the command's output) apart from the stack lines.
  ? {ok: false, error: e.message, frames: String(e.stack).startsWith(String(e)) ? String(e.stack).slice(String(e).length) : "", logs: h.__logs}
  : {ok: false, error: String((e && e.stack) || e) ||
     "the code threw " + JSON.stringify(e) + ", which has no message; throw an Error with one", logs: h.__logs}));
})();
//# sourceURL=fuse-prelude.js
""".replace("%FIX%", _FIX).replace("%SH_TAIL%", str(SH_TAIL)).replace("%RETURN%", json.dumps(RETURN)).replace(
    "%HELPERS%", json.dumps([[name, _STRING_ARG.get(name, "")] for name in HELPERS])).replace("%LOCAL%", json.dumps(LOCAL))

# One script per run, all in one line before the code: the code's line n is
# the script's line OFFSET + n. Its helpers are its own (calls carry its
# number); `let` so code may reassign one for itself.
HEAD = ("(() => { const __h = __fuse_helpers(%RUN%, %SHELLS%); return (async () => { let {"
        + ", ".join(LOCAL) + "} = __h; return await (async () => {\n")
TAIL = ("\n})(); })().then((v) => __fuse_settle(v, __h, %BUDGET%)).catch((e) => __fuse_fail(e, __h)); })()\n"
        f"//# sourceURL={SOURCE}")
OFFSET = HEAD.count("\n")


def _write(obj) -> None:
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode() + b"\n")
    sys.stdout.buffer.flush()


def _confine() -> bool:
    """True once the worker is off the network, cannot gain privileges and no
    longer holds the server's uid. Fails closed."""
    libc = ctypes.CDLL(None, use_errno=True)
    root = os.getuid() == 0
    if root and libc.unshare(0x40000000) != 0:  # CLONE_NEWNET; refused without CAP_SYS_ADMIN (some containers)
        return False
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        return False
    if not root:
        # No uid to drop to: the code would run as the server, on its network.
        return os.environ.get("FUSE_CODE_UNCONFINED") == "1"
    os.setgroups([])
    os.setgid(65534)
    os.setuid(65534)
    return os.getuid() == 65534 and os.geteuid() == 65534


def _clip(outcome: dict) -> dict:
    for key in ("value", "full"):
        value = outcome.get(key)
        if isinstance(value, str) and len(value) > RESULT_CHARS:
            outcome[key] = value[:RESULT_CHARS] + f"\n[Fuse: return value cut at {RESULT_CHARS} characters]"
    logs = "\n".join(str(item) for item in outcome.get("logs") or [])
    if len(logs) > LOG_CHARS:
        logs = logs[:LOG_CHARS] + f"\n[console output cut; {len(logs)} characters total]"
    outcome["logs"] = logs
    for key in ("error", "frames"):
        if isinstance(outcome.get(key), str):
            outcome[key] = outcome[key][:LOG_CHARS]
    return outcome


def _unshift(outcome: dict, shift) -> None:
    """Stack columns on the last expression's line, after the RETURN the
    transform put there, as the code has them."""
    if not shift:
        return
    line, column = OFFSET + int(shift[0]), int(shift[1])

    def back(m):
        # V8 counts columns from 1, acorn from 0; a position inside RETURN is the statement's start.
        at, col = int(m.group(1)), int(m.group(2)) - 1
        if at == line and col >= column:
            col = max(column, col - len(RETURN))
        return f"{SOURCE}:{at}:{col + 1}"

    for key in ("error", "frames"):
        if isinstance(outcome.get(key), str):
            outcome[key] = re.sub(rf"{re.escape(SOURCE)}:(\d+):(\d+)", back, outcome[key])


def _eval_error(exc, code: str) -> str:
    error = str(exc)
    # V8 repeats a parse error's message after the source line and caret.
    first, _, rest = error.partition("\n")
    last = rest.rstrip("\n").rpartition("\n")[2]
    if last and first.endswith(last):
        error = error.rstrip("\n").removesuffix(last).rstrip("\n")
    # A parse error past the code's last line is in the wrapper: the
    # code left a bracket open or closed one it never opened.
    at = re.match(rf"(?:<anonymous>|{re.escape(SOURCE)}):(\d+): SyntaxError", error)
    if at and int(at.group(1)) > OFFSET + code.count("\n") + 1:
        error = ("SyntaxError: the code's brackets do not balance: a {, ( or [ (or a string) is never closed, or "
                 f"a }}, ) or ] closes one that was never opened. {_FIX}")
    return error


async def _main() -> None:
    heap = int(sys.argv[1]) * 1024 * 1024
    # V8 reserves a large address space, so RLIMIT_AS would stop it; data
    # (what it commits) and CPU are the backstops behind the parent's kill.
    resource.setrlimit(resource.RLIMIT_DATA, (heap * 8, heap * 8))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, KERNEL_CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    acorn = ACORN.read_text(encoding="utf-8")
    from py_mini_racer import JSEvalException, JSOOMException, init_mini_racer, mini_racer

    init_mini_racer(flags=("--single-threaded", "--jitless"))
    if not _confine():
        _write({"done": {"ok": False, "error": SANDBOX_FAILED, "logs": "", "kind": "unavailable"}})
        return
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=REPLY_LIMIT)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    waiting: dict[int, tuple[int, asyncio.Future]] = {}
    runs: set[asyncio.Task] = set()
    active: set[int] = set()
    count = 0

    async def bridge(run, name, args_json):
        nonlocal count
        args_json = str(args_json)
        if len(args_json) > ARG_CHARS:
            return json.dumps({"ok": False, "error": f"{name}'s arguments are over {ARG_CHARS} characters; split the "
                                                     "work (for example, write the file in parts)."})
        count += 1
        fut = loop.create_future()
        # This run's calls the code sent before it saw their results.
        pending = sorted(k for k, (r, _) in waiting.items() if r == int(run))
        waiting[count] = (int(run), fut)
        _write({"call": count, "run": int(run), "name": str(name), "args": args_json, "pending": pending})
        return await fut

    with mini_racer() as ctx:
        ctx.set_hard_memory_limit(heap)
        # The isolate's own thread exists now; nothing may start another.
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
        async with ctx.wrap_py_function(bridge) as js_bridge:
            ctx.eval("this")["__fuse_call"] = js_bridge
            ctx.eval(acorn)
            ctx.eval(PRELUDE)
            _write({"ready": True})

            async def start(run: int, code: str, budget: int, shells: dict) -> None:
                # Each run gets its own CPU allowance on top of what the kernel used.
                used = int(time.process_time()) + 1
                resource.setrlimit(resource.RLIMIT_CPU, (min(KERNEL_CPU_SECONDS, used + CPU_SECONDS), KERNEL_CPU_SECONDS))
                active.add(run)
                outcome = None
                try:
                    made = json.loads(ctx.eval(f"__fuse_transform({json.dumps(code)})"))
                    # Code acorn cannot parse goes to V8 as written: its syntax error reads as
                    # V8's own (the hints know those), and code V8 does take still runs, only
                    # without this run's REPL forms.
                    head = HEAD.replace("%RUN%", str(run)).replace("%SHELLS%", json.dumps(shells))
                    if made.get("ok"):
                        head = head.removesuffix("\n") + " " + made["head"] + "\n"
                    script = head + (made["code"] if made.get("ok") else code) + TAIL.replace("%BUDGET%", str(int(budget)))
                    settled = asyncio.ensure_future(ctx.eval(script))
                    # Memory exhausted after an await leaves the promise unsettled.
                    while not settled.done() and not ctx.was_hard_memory_limit_reached():
                        await asyncio.wait({settled}, timeout=0.1)
                    if settled.done():
                        outcome = json.loads(settled.result())
                        # The code may write any outcome, but never the worker's kind.
                        outcome.pop("kind", None)
                        _unshift(outcome, made.get("shift"))
                    else:
                        settled.cancel()
                        outcome = {"ok": False, "error": "JavaScript memory limit reached", "kind": "memory"}
                except JSEvalException as exc:
                    # Also raised while closing an isolate that hit its memory limit.
                    if isinstance(exc, JSOOMException) or ctx.was_hard_memory_limit_reached():
                        outcome = {"ok": False, "error": str(exc), "kind": "memory"}
                    else:
                        outcome = {"ok": False, "error": _eval_error(exc, code)}
                except Exception as exc:  # noqa: BLE001 - the run must end with an outcome, not hang
                    outcome = {"ok": False, "error": f"the code runner failed: {exc}"}
                active.discard(run)
                _write({"done": _clip(outcome), "run": run})
                if outcome.get("kind") == "memory":
                    # The isolate is spent: every run in it ends with this one.
                    for other in sorted(active):
                        _write({"done": _clip({"ok": False, "error": "JavaScript memory limit reached", "kind": "memory"}),
                                "run": other})
                    os._exit(3)

            while line := await reader.readline():
                msg = json.loads(line)
                if "reply" in msg:
                    _, fut = waiting.pop(int(msg["reply"]), (0, None))
                    if fut is not None and not fut.done():
                        fut.set_result(str(msg["body"]))
                elif "run" in msg:
                    task = asyncio.ensure_future(start(int(msg["run"]), str(msg.get("code") or ""),
                                                       int(msg.get("budget") or RESULT_CHARS), dict(msg.get("shells") or {})))
                    runs.add(task)
                    task.add_done_callback(runs.discard)


if __name__ == "__main__":
    asyncio.run(_main())
