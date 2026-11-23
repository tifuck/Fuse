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
calls and their mean calls; failed runs; the longest row of one-call runs in one
reply. Tracked on dev with the QA harness, never released.
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
        for t in tools:
            name = t.get("name")
            if t.get("parent"):
                continue
            if name in WORK:
                c["direct"] += 1
                continue
            if name != "run_code":
                continue
            if t.get("status") != "done":
                c["failed"] += 1
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
    head = ("model", "direct", "runs", "1call", "%1", "shell", "pass", "multi", "avgN", "fail", "row")
    print("%-40s %7s %6s %6s %4s %6s %5s %6s %5s %5s %4s" % head)
    total = collections.Counter()
    for model, c in sorted(stats.items(), key=lambda kv: -kv[1]["runs"]):
        total.update(c)
        if c["runs"] + c["direct"] < args.min:
            continue
        print("%-40s %7d %6d %6d %3d%% %6d %5d %6d %5.1f %5d %4d" % (
            model, c["direct"], c["runs"], c["one"], 100 * c["one"] // max(c["runs"], 1), c["one_shell"], c["pass"],
            c["multi"], c["multi_calls"] / max(c["multi"], 1), c["failed"], streaks[model]))
    print("%-40s %7d %6d %6d %3d%% %6d %5d %6d %5.1f %5d" % (
        "all models", total["direct"], total["runs"], total["one"], 100 * total["one"] // max(total["runs"], 1),
        total["one_shell"], total["pass"], total["multi"], total["multi_calls"] / max(total["multi"], 1), total["failed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
