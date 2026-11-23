"""Tool-choice eval: does the model write a run_code snippet or call a tool
directly when it should?

The harness evals (evals.py) grade a chat's final reply; the QA login has no
machine, so they cannot show which tool a coding agent reaches for. This
does: it builds the coding system prompt and tool schemas of a checkout
(code mode on, a made-up machine), sends each case to the dev instance's own
model and classifies the tool calls the model answers with. Nothing runs:
every call gets a canned result, and no chat is created. Run it before and
after any change to the run_code prompt, its tool description or its tips,
and compare the rates.

    cd /root/fuse-dev && ./venv/bin/python -m scripts.qa.tool_choice          # every case, 10 samples each
    ./venv/bin/python -m scripts.qa.tool_choice --repeat 20 fan-out wrap-tip  # chosen cases
    ./venv/bin/python -m scripts.qa.tool_choice --app /root/worktrees/<name>  # that checkout's prompt and tips
    ./venv/bin/python -m scripts.qa.tool_choice --show wrap-habit            # each step's class and first call
    ./venv/bin/python -m scripts.qa.tool_choice --list

A step is one model reply, classified as:
  direct   one direct file or shell call
  batch    several direct calls in one step, or one call that covers every
           target (a shell loop or glob, a command naming several targets, a
           grep over a folder): one round trip either way
  wrap     run_code around one call, in three lines or fewer: a slower direct call
  filter   run_code around one call, with code that works on its result
  snippet  run_code with several calls or a loop over calls
Each case names the classes that are good for it; the summary is good samples
over samples that made a file or shell call. A case of several steps counts a
sample once: as a wrap if any step wrapped (wraps come in runs, so steps are
not independent), else as its first step; multi-edit counts its first step
that edits (a search first is fine; then all edits in one step, or one each?).
A sample's calls are printed with --show: check a surprising rate there
before trusting it (a loop the classifier missed once read as one per step).
Seeded cases start from a made-up history; the "-tip" ones add the tips the
checkout's runtime would (tool_jobs.wrap_tip after every step, add_code_tip
once), so each pair shows what the tip changes. The wrap pair runs three steps
and counts a sample by its last one: is the model out of the habit by then?

Real model calls on the user's GPU server, like the harness evals: one agent
at a time. Only the llama source is ever called (the pooled accounts in
fuse_dev belong to another instance). Tracked on dev with the QA harness,
never released.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QA_EMAIL = "qa@dev.fuse.invalid"
WORK = ("read_file", "list_dir", "grep", "glob", "write_file", "search_replace", "run_terminal_command",
        "get_command_output", "kill_command", "monitor")
HELPERS = WORK[:7]
CATALOG = "- qa-box  connector qa@qa-box.invalid  agent  linux bash  roots: /tmp, /srv/app  projects: app (/srv/app)"
CALL_TIMEOUT_S = 240

SITE = re.compile(r"\b(" + "|".join(HELPERS) + r")\s*\(")
LOOP = re.compile(r"\bfor\s*\(|\bwhile\s*\(|\.map\(|\.forEach\(|Promise\.all")
NOT_WRAP = frozenset({"direct", "batch", "filter", "snippet"})
TOGETHER = frozenset({"batch", "snippet"})

BINARY_ASK = ("libclient.so in this project is a stripped x86-64 ELF. Find the code that clamps view pitch to 89 "
              "degrees and tell me the address of the function.")
BINARY_OUT = (
    "libclient.so: ELF 64-bit LSB shared object, x86-64, dynamically linked, stripped",
    "  1b3c0f0 cl_pitchdown\n  1b3c100 cl_pitchup\n  1b3c118 m_pitch",
    "0000000001a2f4c8  0000b242  (89.0f)\nxrefs: 0x1337360 0x1339a10",
    "  1337360: f3 0f 10 0d 60 81 6f 00  movss  xmm1,DWORD PTR [rip+0x6f8160]  # 1a2f4c8\n"
    "  1337368: 0f 2f c1                 comiss xmm0,xmm1\n  133736b: 76 04                    jbe    1337371\n"
    "  133736d: 0f 28 c1                 movaps xmm0,xmm1",
    "  1339a10: f3 0f 10 05 b0 5a 6f 00  movss  xmm0,DWORD PTR [rip+0x6f5ab0]\n  1339a18: c3                       ret",
)
SERVICE_ASK = "The api service on this machine keeps restarting. Find out why."
SERVICE_OUT = (
    "● api.service - API\n   Loaded: loaded (/etc/systemd/system/api.service; enabled)\n   Active: activating "
    "(auto-restart) (Result: exit-code)\n  Process: 4411 ExecStart=/srv/app/bin/api (code=exited, status=1/FAILURE)",
    "Nov 22 10:01:02 qa-box api[4411]: loading config /srv/app/config/api.toml\n"
    "Nov 22 10:01:02 qa-box api[4411]: Error: listen EADDRINUSE: address already in use :::8080",
    'LISTEN 0 511 *:8080 *:* users:(("node",pid=977,fd=21))',
    "977 /usr/bin/node /srv/legacy/server.js",
    "[Unit]\nDescription=Legacy server\n[Service]\nExecStart=/usr/bin/node /srv/legacy/server.js\nRestart=always",
)
CALLERS_ASK = "Which files call fetchUser, and how does each caller handle a failed fetch?"
CALLERS = tuple(f"src/{name}.js" for name in ("account/profile", "account/settings", "admin/users", "billing/invoice",
                                               "billing/plan", "home/feed", "search/people", "support/ticket", "team/members"))
UNITS = ("worker-ingest", "worker-resize", "worker-notify", "worker-billing", "worker-export")
UNITS_ASK = ("The queue's worker units run on this machine. Check each one's recent logs for errors and tell me which "
             "are failing.")
CALLER_FILES = {"targets": CALLERS, "files": CALLERS}
# One shell call that reaches every target: a loop, xargs, a glob, find, rg or a recursive grep.
FOLDER_WIDE = re.compile(r"\bfor\s|\bwhile\s|\bxargs\b|\*|\bfind\b|\brg\b|\bgrep\b[^|;&]*\s-[a-zA-Z]*[rR]")
EDITS = re.compile(r"\bsed\s+-i|\bperl\s+-[a-z]*i")


def _file_text(path: str) -> str:
    name = path.rsplit("/", 1)[-1].removesuffix(".js")
    lines = ["import { fetchUser } from '../api';", "", f"export async function load_{name}(id) {{", "  try {",
             "    const user = await fetchUser(id);", f"    return render_{name}(user);", "  } catch (err) {",
             f"    report('{name}', err);", "    return null;", "  }", "}"]
    return "\n".join(f"{i}: {line}" for i, line in enumerate(lines, 1))


def _result(name: str, body: str, helpers=()) -> str:
    """A canned result in the shape the runtime gives the model; helpers are the calls a run_code made."""
    if name == "run_code":
        used = collections.Counter(helpers)
        n = sum(used.values())
        calls = f"{n} call{'' if n == 1 else 's'}: " + ", ".join(f"{k}×{v}" for k, v in used.items()) if n else "no tool calls"
        return f"{body}\n[Fuse: {calls}]"
    if name == "run_terminal_command":
        return f"From host qa-box:\nstatus: completed\nexit_code: 0\nduration_ms: 41\n\n{body}"
    return f"From host qa-box:\n{body}" if name in WORK else "ok"


def _step(msgs: list[dict], name: str, args: dict, body: str) -> None:
    ident = f"seed_{len(msgs)}"
    msgs.append({"role": "assistant", "content": "", "tool_calls": [
        {"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]})
    msgs.append({"role": "tool", "tool_call_id": ident, "content": _result(name, body, SITE.findall(args.get("code") or ""))})


def _wrapped(msgs: list[dict], call: str, body: str) -> None:
    """One step that wrapped a single call in run_code and returned its result."""
    value = "r.output" if call.startswith("run_terminal_command") else "r"
    _step(msgs, "run_code", {"code": f"const r = await {call};\nreturn {value};", "description": "Look"}, body)


def _seed_grep(msgs: list[dict], files=CALLERS[:6]) -> None:
    _step(msgs, "grep", {"pattern": "fetchUser\\(", "output_mode": "files_with_matches"}, "\n".join(files))


def _seed_wraps(msgs: list[dict]) -> None:
    """Three steps that each wrapped one command in run_code."""
    commands = ("file libclient.so", "strings -t x libclient.so | grep -i pitch",
                "objdump -s -j .rodata libclient.so | grep -n '0000b242'")
    for command, out in zip(commands, BINARY_OUT):
        code = f"const r = await run_terminal_command({{command: {json.dumps(command)}}});\nreturn r.output;"
        _step(msgs, "run_code", {"code": code, "description": "Inspect the binary"}, out)


def _seed_one_wrap(msgs: list[dict]) -> None:
    _wrapped(msgs, 'run_terminal_command({command: "file libclient.so"})', BINARY_OUT[0])


def _seed_wrapped_grep(msgs: list[dict]) -> None:
    _wrapped(msgs, 'grep({pattern: "fetchUser\\\\(", output_mode: "files_with_matches"})', "\n".join(CALLERS[:6]))


def _seed_wrapped_units(msgs: list[dict]) -> None:
    _wrapped(msgs, "run_terminal_command({command: \"systemctl list-units 'worker-*' --no-legend\"})",
             "\n".join(f"{unit}.service loaded active running Queue worker" for unit in UNITS))


def _seed_singles(msgs: list[dict]) -> None:
    """A grep that found nine files, then five of them read one per step."""
    _seed_grep(msgs, CALLERS)
    for path in CALLERS[:5]:
        _step(msgs, "read_file", {"path": path}, _file_text(path))


def _wrap_tip(tool_jobs, state: tuple, calls: list[dict]) -> tuple[tuple, str]:
    """The checkout's wrap tip for one step, from jobs shaped like the runtime's."""
    jobs = []
    for i, call in enumerate(calls):
        name, raw = call["function"]["name"], call["function"].get("arguments") or "{}"
        try:
            code = str(json.loads(raw).get("code") or "") if name == "run_code" else ""
        except (ValueError, AttributeError):
            code = ""
        jobs.append(tool_jobs.ToolJob(i, call["id"], name, raw, f"ts_{i}", done=True, trace={"status": "done", "code": code}))
        jobs += [tool_jobs.ToolJob(i, call["id"], helper, "{}", f"ts_{i}_c{n}", parent=f"ts_{i}", done=True,
                                   trace={"status": "done"}) for n, helper in enumerate(SITE.findall(code))]
    return tool_jobs.wrap_tip(jobs, state)


CASES = {
    "one-command": {"about": "one command whose output answers the question: a direct call",
                    "ask": "What is the current git branch and the last commit here?",
                    "good": frozenset({"direct", "batch"})},
    "investigate-binary": {"about": "each step depends on reading the last: no wrapped lone calls",
                           "ask": BINARY_ASK, "rounds": 4, "out": BINARY_OUT, "good": NOT_WRAP},
    "investigate-service": {"about": "the same, on a failing service",
                            "ask": SERVICE_ASK, "rounds": 4, "out": SERVICE_OUT, "good": NOT_WRAP},
    "fan-out": {"about": "six files to read are known: one step for all of them",
                "ask": CALLERS_ASK, "seed": _seed_grep, "good": TOGETHER, **CALLER_FILES},
    "multi-edit": {"about": "the same edit in every file that uses it: all edits in one step",
                   "ask": "Rename the function fetchUser to loadUser in every file under src/ that uses it.",
                   "rounds": 4, "grade": "edit", "out": ("\n".join(CALLERS[:6]),), "good": TOGETHER, **CALLER_FILES},
    "wrap-one": {"about": "four steps after one wrapped lone call, without the tips: is the last one direct?",
                 "ask": BINARY_ASK, "seed": _seed_one_wrap, "rounds": 4, "last": True, "out": BINARY_OUT[1:],
                 "good": NOT_WRAP},
    "wrap-one-tip": {"about": "the same history, with the runtime's tips",
                     "ask": BINARY_ASK, "seed": _seed_one_wrap, "tip": "wrap", "rounds": 4, "last": True,
                     "out": BINARY_OUT[1:], "good": NOT_WRAP},
    "fan-out-wrapped": {"about": "six files to read, found by a wrapped grep, without the tips: one step for all",
                        "ask": CALLERS_ASK, "seed": _seed_wrapped_grep, "good": TOGETHER, **CALLER_FILES},
    "fan-out-wrapped-tip": {"about": "the same history, with the runtime's tips: does the tip cost batching?",
                            "ask": CALLERS_ASK, "seed": _seed_wrapped_grep, "tip": "wrap", "good": TOGETHER,
                            **CALLER_FILES},
    "units-wrapped": {"about": "five services to check, listed by a wrapped command, without the tips: one step for all",
                      "ask": UNITS_ASK, "seed": _seed_wrapped_units, "out": ("-- No entries --",), "targets": UNITS,
                      "good": TOGETHER},
    "units-wrapped-tip": {"about": "the same history, with the runtime's tips: does the tip cost batching?",
                          "ask": UNITS_ASK, "seed": _seed_wrapped_units, "tip": "wrap", "out": ("-- No entries --",),
                          "targets": UNITS, "good": TOGETHER},
    "wrap-habit": {"about": "three steps after three wrapped lone calls, without the tips: is the last one direct?",
                   "ask": BINARY_ASK, "seed": _seed_wraps, "rounds": 3, "last": True, "out": BINARY_OUT[2:],
                   "good": NOT_WRAP},
    "wrap-tip": {"about": "the same history, with the runtime's tips",
                 "ask": BINARY_ASK, "seed": _seed_wraps, "tip": "wrap", "rounds": 3, "last": True,
                 "out": BINARY_OUT[2:], "good": NOT_WRAP},
    "single-habit": {"about": "after five files read one per step, four known files left, without the tip",
                     "ask": CALLERS_ASK, "seed": _seed_singles, "good": TOGETHER, **CALLER_FILES},
    "single-tip": {"about": "the same history, with the runtime's tip",
                   "ask": CALLERS_ASK, "seed": _seed_singles, "tip": "single", "good": TOGETHER, **CALLER_FILES},
}


def classify_code(code: str) -> tuple[str, list[str]]:
    """A snippet's class, and the helpers it calls."""
    sites = list(SITE.finditer(code))
    loop = LOOP.search(code)
    # A loop counts only when a call sits inside it (not a .map over one result's lines).
    if len(sites) > 1 or (loop and any(site.start() > loop.start() for site in sites)):
        kind = "snippet"
    else:
        kind = "wrap" if len([line for line in code.splitlines() if line.strip()]) <= 3 else "filter"
    return kind, [site.group(1) for site in sites]


def _covers(name: str, args: dict, targets) -> bool:
    """One direct call that reaches every target: as good as several calls in one step."""
    if name == "run_terminal_command":
        command = str(args.get("command") or "")
        return bool(FOLDER_WIDE.search(command)) or sum(target in command for target in targets) >= 2
    if name == "grep":
        # Content lines from a folder (not one file): every match in one call.
        return args.get("output_mode") in (None, "", "content") and not re.search(r"\.\w+$", str(args.get("path") or ""))
    return False


def classify(calls: list[dict], targets=()) -> tuple[str, list[list[str]]]:
    """A step's class, and per call the helpers its code calls (for the canned result)."""
    kinds, helpers = [], []
    for call in calls:
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except ValueError:
            args = None
        used: list[str] = []
        if name == "run_code" and isinstance(args, dict):
            kind, used = classify_code(str(args.get("code") or ""))
        elif name in HELPERS and isinstance(args, dict):
            kind = "batch" if _covers(name, args, targets) else "direct"
        else:
            kind = "other"
        kinds.append(kind)
        helpers.append(used)
    for kind in ("wrap", "snippet", "filter"):
        if kind in kinds:
            return kind, helpers
    direct = kinds.count("direct") + kinds.count("batch")
    return ("batch" if "batch" in kinds or direct > 1 else "direct" if direct else "other" if kinds else "none"), helpers


def _edits(calls: list[dict], helpers: list[list[str]]) -> bool:
    for call, used in zip(calls, helpers):
        name, raw = call["function"]["name"], call["function"].get("arguments") or ""
        if name in ("search_replace", "write_file") or {"search_replace", "write_file"} & set(used) or (
                name == "run_terminal_command" and EDITS.search(raw)):
            return True
    return False


def _canned(name: str, args: dict, case: dict, i: int) -> str:
    out = case.get("out") or ()
    if name == "list_dir":
        return "libclient.so\nnotes.md\nsrc/"
    if name == "glob":
        return "\n".join(case.get("files") or ("libclient.so",))
    if name == "read_file" and str(args.get("path") or "").endswith(".js"):
        return _file_text(str(args["path"]))
    if name in ("search_replace", "write_file"):
        return f"Edited {args.get('path') or 'the file'}."
    return out[max(0, min(i, len(out) - 1))] if out else "ok"


async def run_case(app, case: dict, repeat: int, send, show: bool = False) -> dict | None:
    """Sample one case `repeat` times; None when this checkout has no such tip."""
    base = [{"role": "system", "content": app["system"]}, {"role": "user", "content": case["ask"]}]
    if case.get("seed"):
        case["seed"](base)
    tool_jobs, wrap_tips, seeded = app["tool_jobs"], case.get("tip") == "wrap", (0, 0)
    if wrap_tips:
        if not hasattr(tool_jobs, "wrap_tip"):
            return None
        for at, message in enumerate(base):
            if message.get("tool_calls"):
                seeded, tip = _wrap_tip(tool_jobs, seeded, message["tool_calls"])
                if tip:
                    base[at + 1]["content"] += f"\n\n{tip}"
    elif case.get("tip") == "single":
        tool_jobs.add_code_tip(base, collections.Counter(read_file=5, grep=1))
    counts, tokens, errors = collections.Counter(), [], 0
    for _ in range(repeat):
        msgs, counted, replies, kinds, state, edit = copy.deepcopy(base), 0, 0, [], seeded, ""
        rounds = int(case.get("rounds") or 1)
        # A reply of only todo_write or search_tools is answered and not counted.
        while counted < rounds and replies < rounds + 2 and errors < 3:
            replies += 1
            try:
                message, used = await send(msgs)
            except Exception as exc:
                errors += 1
                print(f"  (call failed: {type(exc).__name__}: {str(exc)[:160]})", file=sys.stderr)
                break
            calls = message.get("tool_calls") or []
            kind, helpers = classify(calls, case.get("targets") or ())
            if kind == "none":
                break
            if kind != "other":
                kinds.append(kind)
                counted += 1
                if show:
                    first = calls[0]["function"]
                    print(f"  step {counted} {kind}: {first['name']} {first.get('arguments') or ''}"[:400], flush=True)
                if used:
                    tokens.append(used)
            msgs.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
            for call, used in zip(calls, helpers):
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except ValueError:
                    args = {}
                body = _canned(name, args if isinstance(args, dict) else {}, case, counted - 1)
                msgs.append({"role": "tool", "tool_call_id": call["id"], "content": _result(name, body, used)})
            if wrap_tips and calls:
                state, tip = _wrap_tip(tool_jobs, state, calls)
                if tip:
                    msgs[-1]["content"] += f"\n\n{tip}"
            if case.get("grade") == "edit" and kind != "other" and _edits(calls, helpers):
                edit = kind
                break
        if case.get("grade") == "edit":
            if kinds:
                counts[edit or "no edit"] += 1
        elif kinds:
            # Wraps come in runs, so a sample is one verdict, not one per step.
            counts[kinds[-1] if case.get("last") else "wrap" if "wrap" in kinds else kinds[0]] += 1
    return {"counts": counts, "tokens": tokens, "errors": errors}


def _load_app(app_root: Path) -> dict:
    """Import `app` from a checkout, on this instance's settings."""
    if app_root != ROOT:
        from dotenv import dotenv_values

        # The checkout has no config of its own: .env wins over config.env, as in app/config.py.
        for name in (".env", "config.env"):
            for key, value in dotenv_values(ROOT / name).items():
                if value is not None:
                    os.environ.setdefault(key, value)
    sys.path.insert(0, str(app_root))
    from app import agent_catalog, api, chat_prompts, db, sources
    from app.chat_generation import tool_jobs

    system = chat_prompts.build_system_prompt(
        instructions="", profile="", thread_context="", tools=[], host_catalog=CATALOG, coding=True,
        project_session=True, bound_label="qa-box cwd=/srv/app", loaded_tools=list(WORK), code_mode=True,
        temp_dir="/tmp/fuse/c-eval")
    if "run_code" not in system:
        raise SystemExit("tool_choice: this checkout's coding prompt has no run_code (is code mode off?)")
    tools = [copy.deepcopy(s) for s in map(agent_catalog.schema, ("run_code", "search_tools", "load_skill", "todo_write", *WORK)) if s]
    return {"system": system, "tools": tools, "tool_jobs": tool_jobs, "api": api, "db": db, "sources": sources}


async def _run(args, app_root: Path) -> int:
    app = _load_app(app_root)
    user = app["db"].fetchone("SELECT id FROM users WHERE email=?", (QA_EMAIL,))
    if not user:
        raise SystemExit("tool_choice: no QA login in this database (scripts/qa/dev_qa_account.py)")
    uid = user["id"]
    model = args.model
    if not model:
        groups = await app["sources"].models_by_source(user_id=uid)
        model = next((m for g in groups if g["type"] == "llama" for m in g["models"]), "")
    picked = await app["sources"].pick(model, user_id=uid) if model else None
    if not picked or picked[0].type_id != "llama":
        raise SystemExit(f"tool_choice: {model or 'no model'} is not on this instance's llama source; only that source is called")
    src, upstream = picked

    async def send(msgs):
        body = {"model": upstream, "messages": msgs, "tools": app["tools"], "stream": False, "max_tokens": 1500}
        result = await asyncio.wait_for(app["api"]._run_chat(src, body, uid), CALL_TIMEOUT_S)
        if result.status != 200:
            raise RuntimeError(f"HTTP {result.status}: {str(result.body)[:160]}")
        return result.body["choices"][0]["message"], (result.body.get("usage") or {}).get("completion_tokens")

    head = subprocess.run(["git", "-C", str(app_root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    print(f"model {model} · app {app_root} ({head}) · {args.repeat} samples · system {len(app['system'])} chars", flush=True)
    good = steps = wraps = failed = 0
    tokens: list[int] = []
    for name in args.cases or list(CASES):
        case = CASES[name]
        got = await run_case(app, case, args.repeat, send, args.show)
        if got is None:
            print(f"== {name}: n/a (this checkout has no such tip)", flush=True)
            continue
        counts = got["counts"]
        n, ok = sum(counts.values()), sum(v for k, v in counts.items() if k in case["good"])
        mix = ", ".join(f"{k} {v}" for k, v in counts.most_common())
        print(f"== {name}: {ok}/{n} good · {mix or 'no calls'}" + (f" · {got['errors']} failed calls" if got["errors"] else ""), flush=True)
        good, steps, wraps, failed = good + ok, steps + n, wraps + counts["wrap"], failed + got["errors"]
        tokens += got["tokens"]
    mean = round(sum(tokens) / len(tokens)) if tokens else 0
    print(f"total: {good}/{steps} good ({100 * good // max(steps, 1)}%) · {wraps} wrapped · {mean} output tokens a step", flush=True)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tool-choice eval: run_code snippet or direct call (see the module docstring).")
    parser.add_argument("cases", nargs="*", help="cases to run (default: all)")
    parser.add_argument("--repeat", type=int, default=10, help="samples per case")
    parser.add_argument("--model", default="", help="model id on the llama source (default: its first)")
    parser.add_argument("--app", default="", help="checkout whose prompt, schemas and tips to use (default: this one)")
    parser.add_argument("--show", action="store_true", help="print each step's class and its first call")
    parser.add_argument("--list", action="store_true", help="list the cases and exit")
    args = parser.parse_args(argv)
    if args.list:
        for name, case in CASES.items():
            print(f"{name:20} {case['about']}")
        return 0
    unknown = [name for name in args.cases if name not in CASES]
    if unknown:
        parser.error(f"unknown case: {', '.join(unknown)} (see --list)")
    return asyncio.run(_run(args, Path(args.app).resolve() if args.app else ROOT))


if __name__ == "__main__":
    sys.exit(main())
