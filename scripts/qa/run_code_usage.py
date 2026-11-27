"""run_code usage per model, from the chats an instance stored: read-only.

How often each model wraps a single call in run_code, beside its direct file
and shell calls, for replies that ended in a date window. Use it to compare a
window before a change to the run_code prompt or tips with one after it
(scripts/qa/tool_choice.py measures the same choice on the dev model).

    cd /root/fuse-dev && ./venv/bin/python -m scripts.qa.run_code_usage --since 2026-11-08 --until 2026-11-22

It only reads (SELECT) and prints counts, never chat text. Releases leave
scripts/qa/ out, so on an instance without it pipe the file to that
instance's python from its own folder:

    cd /root/fuse && ./venv/bin/python - --since 2026-11-23 < run_code_usage.py

Columns: direct file and shell calls; run_code runs that worked; runs that made
one call (and the share of runs), of them shell commands, and pass-throughs (one
call, three lines of code or fewer, what the wrap tip counts); runs of several
calls and their mean calls; failed runs, and of them runs whose sh command
failed (kind command_failed); the longest row of one-call runs in one reply;
the share of steps that carry one call (a step is the calls whose trace ids
share their ts_<round>_ prefix; calls with a parent are run_code's own);
steps that were one call whose result the next call did not need (a
todo_write or scratch, an edit or write that worked, a command that exited 0
with under 120 characters of output: what tool_jobs.ack_tip counts); calls the
step gate held back (kind not_run). Tracked on dev with the QA harness, never
released.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys

WORK = {"read_file", "list_dir", "grep", "glob", "write_file", "search_replace", "run_terminal_command"}
CALLS = re.compile(r"\[Fuse: (\d+) calls?:")
STEP = re.compile(r"ts_(\d+)_(\d+)")


def acknowledged(t: dict) -> bool:
    """A step's one call whose result the next call did not need (tool_jobs._ack_call)."""
    if t.get("status") != "done":
        return False
    if t.get("name") in ("todo_write", "scratch", "search_replace", "write_file"):
        return True
    chars = t.get("output_chars") if type(t.get("output_chars")) is int else len(str(t.get("output") or ""))
    return t.get("name") == "run_terminal_command" and t.get("exit_code") == 0 and chars < 120


def tally(messages: list, since: str, until: str, stats: dict, streaks: dict) -> None:
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "assistant" or not since <= str(m.get("endedAt") or "") < until:
            continue
        tools = [t for t in (m.get("tools") or []) if isinstance(t, dict)]
        model = str(m.get("model") or "?")[:40]
        made = collections.defaultdict(list)
        for t in tools:
            if t.get("parent"):
                made[t["parent"]].append(t.get("name"))
        c, row = stats[model], 0
        steps = collections.defaultdict(dict)
        for t in tools:
            name = t.get("name")
            c["held"] += t.get("kind") == "not_run"
            if t.get("parent"):
                continue
            step = STEP.match(str(t.get("id") or ""))
            if step:
                # A host list split per host keeps one entry: the ids share the call's.
                steps[step.group(1)].setdefault(step.group(2), t)
            if name in WORK:
                c["direct"] += 1
                continue
            if name != "run_code":
                continue
            if t.get("status") != "done":
                c["failed"] += 1
                c["cmd_failed"] += t.get("kind") == "command_failed"
                continue
            # Rows past the first 40 reads are not stored; the result's calls line counts them all.
            calls = made.get(t.get("id")) or []
            found = CALLS.search(str(t.get("output") or ""))
            n = max(len(calls), int(found.group(1)) if found else 0)
            c["runs"] += 1
            if n == 1:
                c["one"] += 1
                c["one_shell"] += calls[:1] == ["run_terminal_command"]
                c["pass"] += len([line for line in str(t.get("code") or "").splitlines() if line.strip()]) <= 3
                row += 1
                streaks[model] = max(streaks[model], row)
            elif n > 1:
                c["multi"] += 1
                c["multi_calls"] += n
                row = 0
        for calls in steps.values():
            c["steps"] += 1
            if len(calls) == 1:
                c["lone"] += 1
                c["ack"] += acknowledged(next(iter(calls.values())))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="run_code usage per model (read-only; see the module docstring).")
    parser.add_argument("--since", required=True, help="first day, YYYY-MM-DD (UTC)")
    parser.add_argument("--until", default="9999", help="day after the last, YYYY-MM-DD (default: now)")
    parser.add_argument("--min", type=int, default=20, help="leave out models with fewer file, shell and run_code calls")
    args = parser.parse_args(argv)
    sys.path.insert(0, os.getcwd())
    from app import db

    stats, streaks = collections.defaultdict(collections.Counter), collections.defaultdict(int)
    ids = [r["id"] for r in db.fetchall("SELECT id FROM chat_threads WHERE updated_at >= ? AND messages LIKE ?",
                                        (args.since, '%"run_code"%'))]
    direct_only = db.fetchall("SELECT id FROM chat_threads WHERE updated_at >= ? AND messages NOT LIKE ?",
                              (args.since, '%"run_code"%'))
    for tid in ids + [r["id"] for r in direct_only]:
        row = db.fetchone("SELECT messages FROM chat_threads WHERE id=?", (tid,))
        try:
            tally(json.loads(row["messages"]), args.since, args.until, stats, streaks)
        except (ValueError, TypeError):
            continue
    print(f"replies ended {args.since} .. {args.until}: {len(ids)} chats with run_code, {len(direct_only)} without")
    head = ("model", "direct", "runs", "1call", "%1", "shell", "pass", "multi", "avgN", "fail", "shF", "row", "1step",
            "ack", "held")
    print("%-28s %7s %6s %6s %4s %6s %5s %6s %5s %5s %4s %4s %5s %5s %4s" % head)
    total = collections.Counter()
    line = "%-28s %7d %6d %6d %3d%% %6d %5d %6d %5.1f %5d %4d %4s %4d%% %5d %4d"
    for model, c in sorted(stats.items(), key=lambda kv: -kv[1]["runs"]):
        total.update(c)
        if c["runs"] + c["direct"] < args.min:
            continue
        print(line % (
            model[:28], c["direct"], c["runs"], c["one"], 100 * c["one"] // max(c["runs"], 1), c["one_shell"], c["pass"],
            c["multi"], c["multi_calls"] / max(c["multi"], 1), c["failed"], c["cmd_failed"], streaks[model],
            100 * c["lone"] // max(c["steps"], 1), c["ack"], c["held"]))
    print(line % (
        "all models", total["direct"], total["runs"], total["one"], 100 * total["one"] // max(total["runs"], 1),
        total["one_shell"], total["pass"], total["multi"], total["multi_calls"] / max(total["multi"], 1), total["failed"],
        total["cmd_failed"], "", 100 * total["lone"] // max(total["steps"], 1), total["ack"], total["held"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
