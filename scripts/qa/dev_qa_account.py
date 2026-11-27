#!/usr/bin/env python3
"""The dev instance's permanent QA login and its artificial chats.

    ./venv/bin/python -m scripts.qa.dev_qa_account

Run from the dev tree. Creates the QA account once and keeps it an admin (so
the QA browser, scripts/qa/shot.mjs, can open the admin pages; it aborts every
write), keeps its password in data/dev-qa-login.json (gitignored, mode 0600;
never printed) and replaces the account's chats, artifacts, skill drafts and
memories with the "QA · " fixtures below, so every run leaves the same data.

Writing a chat: THREADS lists (title, builder). A builder returns messages
made with user(text) and reply(*parts), where a part is reply text,
("think", text, ms), tool(name, **fields), perm(...), marker(kind, text) or
delivery(envelope, ...); wake(envelope, ...) is the automated user turn that
brings background results. The envelope helpers (command_result,
monitor_event, monitor_result, task_report) copy the server's format. The
server validates each message strictly (app/chat_schema.py: parse_messages
and _parse_tool_traces list the tool names and fields it accepts), so a wrong
field stops the seeder instead of drawing wrongly. "QA · Artifacts" and the
pending draft in "QA · Planning and memory" are real rows; the draft expires
after 24 hours (reseed). Titles start with "QA · ".

Refuses any database but fuse_dev. This file is tracked on dev (pushed to
GitHub dev, which is public, and never released to main); the fixture text is
made up and holds nothing real: never copy chats, paths, keys or output from
production or from a real session.
"""
from __future__ import annotations

import json
import math
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_POOL_SIZE", "2")

EMAIL = "qa@dev.fuse.invalid"
CREDS = ROOT / "data" / "dev-qa-login.json"
HOST = "fuse-dev"
PROJECT = "/srv/demo"
MODEL = "qa-fixture"

_ids = count(1)


def tool(name: str, **fields) -> dict:
    row = {"id": f"qa{next(_ids)}", "name": name, "status": "done", "host": HOST, "project_root": PROJECT}
    row.update(fields)
    return row


def numbered(start: int, lines: list[str]) -> str:
    return "\n".join(f"{start + i}: {line}" for i, line in enumerate(lines))


def perm(**fields) -> dict:
    """A permission card in a reply: tool_permission (an ask) or skill_save."""
    return {"_perm": {"id": f"qa-perm-{next(_ids)}", **fields}}


def marker(kind: str, text: str, summary: str = "") -> dict:
    """A run marker in a reply: compaction, prune, resume or notice."""
    return {"_seg": {"type": "marker", "kind": kind, "text": text, **({"summary": summary} if summary else {})}}


def delivery(*envelopes: str) -> dict:
    """Background results handed to the model mid-reply (inbox.display_text)."""
    return {"_seg": {"type": "delivery", "text": "\n".join(envelopes)}}


def reply(*parts, minutes_ago: int = 0, elapsed_s: int = 0, live: bool = False, **extra) -> dict:
    """An assistant message from its parts in order: a str is reply text, a
    ("think", text, ms) tuple a thought, a dict a tool call (or a perm, marker
    or delivery). live: a reply still being worked on, with no end time (see
    live_thread)."""
    tools, segments, content, reasoning, perms = [], [], [], [], []
    for part in parts:
        if isinstance(part, dict) and "_seg" in part:
            segments.append(part["_seg"])
        elif isinstance(part, dict) and "_perm" in part:
            perms.append(part["_perm"])
            segments.append({"type": "permission", "id": part["_perm"]["id"]})
        elif isinstance(part, dict):
            tools.append(part)
            segments.append({"type": "tool", "id": part["id"]})
        elif isinstance(part, tuple):
            segments.append({"type": "reasoning", "text": part[1], "ms": part[2]})
            reasoning.append(part[1])
        else:
            segments.append({"type": "content", "text": part})
            content.append(part)
    ended = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    done = {} if live else {"elapsedMs": elapsed_s * 1000, "endedAt": ended.strftime("%Y-%m-%dT%H:%M:%SZ")}
    return {"role": "assistant", "content": "".join(content), "reasoning": "".join(reasoning), "tools": tools,
            "segments": segments, "model": MODEL, **({"permissions": perms} if perms else {}), **done, **extra}


def user(text: str, **extra) -> dict:
    return {"role": "user", "content": text, **extra}


# Background result envelopes, in the server's own shapes (command_jobs.notification,
# monitor.event_envelope / result_envelope, chat_views task envelope).
def command_result(ident: str, state: str, command: str, output: str, *, description: str = "", exit_code=None,
                   duration_ms: int = 0, error: str = "") -> str:
    lines = [f'<command_result id="{ident}" state="{state}">', f"host: {HOST} command: {command}"]
    lines += [f"description: {description}"] if description else []
    lines += [f"exit_code: {exit_code}"] if state == "completed" else []
    lines += [f"duration_ms: {duration_ms}"] + ([f"error: {error}"] if error else [])
    lines += [f"output_chars: {len(output)}", "output_chars_exact: true", "", output or "(no output)", "</command_result>"]
    return "\n".join(lines)


def monitor_event(ident: str, seq: int, command: str, body: str, *, description: str = "", elapsed_ms: int = 0) -> str:
    return "\n".join([f'<monitor_event id="{ident}" seq="{seq}" count="{seq}">', f"host: {HOST} source: command target: {command}",
                      *([f"description: {description}"] if description else []), f"events: {seq}", f"elapsed_ms: {elapsed_ms}",
                      "", body, "</monitor_event>"])


def monitor_result(ident: str, state: str, command: str, events: int, *, description: str = "", timeout_ms: int = 0,
                   duration_ms: int = 0, out: str = "") -> str:
    return "\n".join([f'<monitor_result id="{ident}" state="{state}">', f"host: {HOST} source: command target: {command}",
                      *([f"description: {description}"] if description else []), f"events: {events}",
                      *([f"timeout_ms: {timeout_ms}"] if timeout_ms else []), f"duration_ms: {duration_ms}",
                      "", out or "(no output)", "</monitor_result>"])


def task_report(ident: str, state: str, summary: str, body: str) -> str:
    tag = "task_error" if state in ("error", "cancelled", "interrupted") else "task_result"
    return "\n".join([f'<task id="{ident}" state="{state}">', f"<summary>{summary}</summary>", f"<{tag}>", body,
                      f"</{tag}>", "</task>", f"task_id: {ident} (pass task_id to continue this subagent)"])


def wake(*envelopes: str) -> dict:
    """The automated user turn that carries background results (inbox.results_message)."""
    from app.chat_generation.inbox import RESULTS_HEAD, RESULTS_TAIL

    return user(RESULTS_HEAD + "\n\n" + "\n\n".join(envelopes) + "\n\n" + RESULTS_TAIL, auto=True)


SESSION_GO = ["func (s *session) reattach(ws *conn) {", "\told := s.swap(ws)", "\tif old != nil {", "\t\told.Close()", "\t}", "}", "",
              "func (s *session) swap(ws *conn) *conn {", "\ts.mu.Lock()", "\tdefer s.mu.Unlock()", "\told := s.ws", "\ts.ws = ws",
              "\treturn old", "}"]
DIFF = ("@@ -212,3 +212,5 @@\n async def _reader(self, ws):\n-    while not ws.closed:\n+    while True:\n"
        "+        if ws is not self._socket:\n+            break\n         frame = await ws.receive_text()")
BIG_DIFF = ("@@ -40,9 +40,12 @@\n def load(path):\n-    data = open(path).read()\n-    return json.loads(data)\n"
            "+    with open(path, encoding=\"utf-8\") as handle:\n+        data = handle.read()\n+    try:\n"
            "+        return json.loads(data)\n+    except json.JSONDecodeError as error:\n"
            "+        raise ConfigError(f\"{path}: {error}\") from None\n \n def save(path, value):\n"
            "-    open(path, \"w\").write(json.dumps(value))\n+    tmp = path + \".tmp\"\n"
            "+    with open(tmp, \"w\", encoding=\"utf-8\") as handle:\n+        json.dump(value, handle, indent=2)\n"
            "+    os.replace(tmp, path)")
PYTEST = "\n".join([f"tests/core/agents/test_hub.py::test_case_{i:02d} PASSED" for i in range(60)] + ["", "198 passed in 61.82s"])
ANSI = "\x1b[32m✓\x1b[0m lint\n\x1b[32m✓\x1b[0m types\n\x1b[31m✗\x1b[0m unit  \x1b[2m(2 failed)\x1b[0m\n\x1b[33m!\x1b[0m coverage 81% (threshold 85%)"
LONG_LINE = "tests/core/agents/test_hub.py:131:async def test_reattach_after_close_drops_old_frames_and_keeps_the_pending_reply_for_the_new_socket(hub, fake_socket, monkeypatch):"


def rows_thread() -> list[dict]:
    """One reply with every kind of call the desktop rows draw."""
    return [
        user("Run the suite twice so we know it isn't flaky."),
        reply(
            "Benign. Checking the two run-store commits that touched `chat_runs.py`.\n\n",
            tool("read_file", path=f"{PROJECT}/app/chat_runs.py", output=numbered(1, ["import asyncio", "", "from . import db"])),
            tool("run_terminal_command", command="git log --oneline -3", cwd=PROJECT, exit_code=0, execution_duration_ms=180,
                 output="be63041 Status line names what is happening\n712b233 Skills library sheet\n471de71 Skills dialog stays centred"),
            "Checking how the reader loop handles a replaced socket before the double run.\n\n",
            tool("read_file", path=f"{PROJECT}/agent/windows/session.go", output=numbered(86, SESSION_GO)),
            tool("grep", pattern="streamWriter", path=f"{PROJECT}/agent/windows",
                 output="agent/windows/exec_windows.go:41:type streamWriter struct {\n"
                        "agent/windows/exec_windows.go:97:func (w *streamWriter) Write(p []byte) (int, error) {\n"
                        "agent/windows/session.go:58:\tout := &streamWriter{id: id}"),
            ("think", "**Checking the socket swap**\nThe hub drops the reply when the socket is replaced mid-flight, so the test has to keep "
                      "the old socket reading after close. The fake already does that; the double run only needs to prove the ordering is stable.", 4200),
            tool("glob", pattern="tests/core/agents/test_*.py", path=PROJECT,
                 output="tests/core/agents/test_delivery.py\ntests/core/agents/test_hub.py\ntests/core/agents/test_lifecycle.py"),
            tool("list_dir", path=f"{PROJECT}/tests/core", output="agents/\nchat/\nrouter/\ntools/\nworkspace/\nconftest.py"),
            tool("read_file", path=f"{PROJECT}/README.md", output=numbered(1, ["# Demo", "", "A made-up project for QA screenshots."])),
            tool("run_terminal_command", command='ls tests/core && grep -rn "reattach" tests/core/agents',
                 description="List core test areas and relevant test files", cwd=PROJECT, exit_code=0, execution_duration_ms=420,
                 output="agents  chat  router  tools  workspace\ntests/core/agents/test_hub.py:88:async def test_reattach_keeps_pending_reply(hub):\n" + LONG_LINE),
            tool("search_replace", path=f"{PROJECT}/app/agent_hub.py", preview=DIFF, lines_added=3, lines_removed=1),
            tool("write_file", path=f"{PROJECT}/tests/core/agents/test_reattach.py", output="created tests/core/agents/test_reattach.py (412 bytes)"),
            tool("run_terminal_command", command="systemctl restart demo", description="Restart the dev instance", cwd=PROJECT,
                 exit_code=5, execution_duration_ms=110, output="Failed to restart demo.service: Unit demo.service not found."),
            tool("read_file", status="error", path=f"{PROJECT}/app/missing.py", error="No such file or directory"),
            tool("run_terminal_command", command="git -C /srv/demo status --short", cwd=PROJECT, exit_code=0, execution_duration_ms=2400,
                 output=" M app/agent_hub.py\n?? tests/core/agents/test_reattach.py"),
            tool("run_terminal_command", command="true", description="Check the lock file is gone", cwd=PROJECT, exit_code=0,
                 execution_duration_ms=30, output=""),
            tool("read_url", url="https://docs.python.org/3/library/asyncio-task.html", bytes=48211, http_status=200,
                 results=[{"title": "Coroutines and Tasks", "url": "https://docs.python.org/3/library/asyncio-task.html",
                           "snippet": "This section outlines high-level asyncio APIs to work with coroutines and Tasks."}]),
            tool("web_search", queries=["asyncio websocket close race"], latency_ms=900,
                 results=[{"title": "Closing a websocket while a read is pending", "url": "https://example.com/ws-close", "snippet": "A read in flight completes after close."},
                          {"title": "asyncio cancellation notes", "url": "https://example.org/cancel", "snippet": "CancelledError is raised at the next await."}]),
            tool("run_terminal_command", command="scripts/test.sh agents -- -q --count 2", description="Run the test suite twice",
                 cwd=PROJECT, exit_code=0, execution_duration_ms=64000, output=PYTEST),
            "Both runs pass: 99 tests each, no failures, same order. It isn't flaky.",
            minutes_ago=15, elapsed_s=372),
    ]


def edits_thread() -> list[dict]:
    """Bigger diffs and outputs: long, coloured, cut short, interrupted."""
    return [
        user("Harden the config loader and run the checks."),
        reply(
            "Rewriting the loader so a bad file names itself.\n\n",
            tool("search_replace", path=f"{PROJECT}/app/config_store.py", preview=BIG_DIFF, lines_added=11, lines_removed=3),
            tool("run_terminal_command", command="make check", description="Run the checks", cwd=PROJECT, exit_code=1,
                 execution_duration_ms=8300, output=ANSI),
            tool("run_terminal_command", command="pytest -q tests/core", description="Run the core tests", cwd=PROJECT, exit_code=0,
                 execution_duration_ms=61820, output=PYTEST, output_chars=180000, truncated=True),
            tool("run_terminal_command", status="interrupted", command="sleep 600", description="Wait for the queue to drain", cwd=PROJECT,
                 error="Interrupted by the user"),
            "The loader is done. Coverage is under the threshold, so the check target fails until the two unit failures are fixed.",
            minutes_ago=42, elapsed_s=95),
    ]


def summary_thread() -> list[dict]:
    """Several short turns: each folded line counts something different."""
    return [
        user("Where is the retry limit set?"),
        reply(tool("grep", pattern="RETRY_LIMIT", path=PROJECT, output="app/net.py:12:RETRY_LIMIT = 5"),
              "In `app/net.py`, line 12: `RETRY_LIMIT = 5`.", minutes_ago=180, elapsed_s=4),
        user("Raise it to 8 and add a test."),
        reply(tool("search_replace", path=f"{PROJECT}/app/net.py", preview="@@ -12 +12 @@\n-RETRY_LIMIT = 5\n+RETRY_LIMIT = 8", lines_added=1, lines_removed=1),
              tool("write_file", path=f"{PROJECT}/tests/test_net.py", output="created tests/test_net.py (188 bytes)"),
              tool("run_terminal_command", command="pytest -q tests/test_net.py", cwd=PROJECT, exit_code=0, execution_duration_ms=900, output="1 passed in 0.31s"),
              "Raised to 8, with a test that pins it.", minutes_ago=170, elapsed_s=21),
        user("Stop, I changed my mind."),
        reply("Reverting the", minutes_ago=165, elapsed_s=3, stopped=True),
        user("What does the README say about setup?"),
        reply(("think", "The README is short; reading it whole is cheaper than searching it.", 1800),
              "It says to copy `config.env.example`, set the database URL and run `start.sh`.", minutes_ago=160, elapsed_s=6),
    ]


def live_thread() -> list[dict]:
    """A reply caught mid-run: its last call, a described command, is still
    running. Nothing is running on the server; the QA browser's --fake running
    tells the page it is (and --fake ask adds a permission prompt)."""
    return [
        user("Run the suite twice so we know it isn't flaky."),
        reply(
            "Checking how the reader loop handles a replaced socket first.\n\n",
            tool("read_file", path=f"{PROJECT}/agent/windows/session.go", output=numbered(86, SESSION_GO)),
            tool("grep", pattern="streamWriter", path=f"{PROJECT}/agent/windows",
                 output="agent/windows/exec_windows.go:41:type streamWriter struct {\nagent/windows/session.go:58:\tout := &streamWriter{id: id}"),
            tool("search_replace", path=f"{PROJECT}/app/agent_hub.py", preview=DIFF, lines_added=3, lines_removed=1),
            "The fix is in. Running the suite twice to rule out a flake.\n\n",
            tool("run_terminal_command", status="running", command="scripts/test.sh agents -- -q --count 2",
                 description="Run the test suite twice", cwd=PROJECT, timeout_ms=600000,
                 output="........................................ [ 40%]\n........................................ [ 80%]"),
            live=True),
    ]


MARKDOWN = """## Why the reply was lost

A command's finished result can be dropped while the connector reconnects. The server then waits out the background limit.

1. A foreground command waits up to its timeout (`app/workspace.py:200`).
2. At the timeout it moves to the background and reports later.
3. A reconnect in that window loses the report.

| Stage | Limit | Owner |
| --- | --- | --- |
| Foreground | 2 min | caller |
| Background | 30 min | connector |

```python
async def finish(job):
    await hub.report(job.id, job.result)
```

> The fix keeps the old socket reading until the new one has taken over.
"""

EXPLORE_REPORT = ("## Reconnect paths\n\n- `agent/linux/session.go:88` swaps the socket under a lock.\n"
                  "- `agent/windows/session.go:86` does the same, but closes the old socket first.\n\n"
                  "Both drop a frame read in flight. No other path reconnects.")


def agents_thread() -> list[dict]:
    """Subagents: foreground, background, joins, a stop, a list, a report delivered mid-reply.
    Background spawns are stored as chat_views.settle_spawn_traces leaves them
    (there are no child runs). Desktop keeps these out of the transcript."""
    spawn = dict(subagent_type="general-purpose", model="qa/fixture-small")
    return [
        user("Audit the three connectors in parallel and fix what they find."),
        reply(
            "Mapping the reconnect paths first, then one agent per connector.\n\n",
            tool("spawn_subagent", description="Map the reconnect paths", preview="Map the reconnect paths", subagent_type="explore",
                 child_id="qa-child-explore", agent_tool_uses=14, agent_tokens=38200, agent_work_ms=96000, summary=EXPLORE_REPORT),
            tool("spawn_subagent", description="Audit the Linux connector", preview="Audit the Linux connector", child_id="qa-child-linux",
                 background=True, summary="Background task started", execution_status="completed", agent_work_ms=148000, **spawn),
            tool("spawn_subagent", description="Audit the Windows connector", preview="Audit the Windows connector", child_id="qa-child-win",
                 background=True, summary="Background task started", execution_status="completed", agent_work_ms=212000, **spawn),
            tool("spawn_subagent", status="error", description="Audit the macOS connector", subagent_type="plan",
                 error="Subagent could not start: no model is available for this account", preview="Subagent could not start"),
            tool("list_subagents", output=json.dumps({"live": [
                {"id": "qa-child-linux", "status": "running", "description": "Audit the Linux connector", "model": "qa/fixture-small"},
                {"id": "qa-child-win", "status": "running", "description": "Audit the Windows connector", "model": "qa/fixture-small"}],
                "finished": [{"id": "qa-child-explore", "status": "completed", "description": "Map the reconnect paths", "model": "qa/fixture-small"}]})),
            tool("get_subagent_output", child_id="qa-child-win", preview="still running", execution_status="running", timeout_ms=60000),
            delivery(task_report("qa-child-linux", "completed", "Audit the Linux connector",
                                 "Found one bug: the reader loop keeps reading the old socket. Fixed in `agent/linux/session.go` and covered by a test.")),
            tool("get_subagent_output", child_id="qa-child-win", agent_work_ms=212000,
                 summary="Same bug as Linux. Fixed in `agent/windows/session.go`; the existing test now passes on both."),
            tool("spawn_subagent", description="Recheck the hub after both fixes", preview="Recheck the hub after both fixes",
                 child_id="qa-child-recheck", background=True, summary="Background task started", status="interrupted",
                 execution_status="cancelled", agent_work_ms=21000, **spawn),
            tool("kill_subagent", child_id="qa-child-recheck", preview="stopped"),
            "Both connectors had the same bug and both are fixed. I stopped the recheck: the tests already cover it.",
            minutes_ago=50, elapsed_s=318),
    ]


def background_thread() -> list[dict]:
    """Background commands and monitors: started, joined, stopped, results delivered mid-reply and in a wake turn."""
    build, tail, soak = "qa-job-build", "qa-job-tail", "qa-job-soak"
    return [
        user("Build the release in the background and tell me if the log shows errors."),
        reply(
            "Starting the build in the background and watching its log.\n\n",
            tool("run_terminal_command", command="make release", description="Build the release", cwd=PROJECT, background=True,
                 command_id=build, child_id=build, job_id=build, state="completed", execution_status="completed", exit_code=0,
                 execution_duration_ms=184000, output="[1/3] compile\n[2/3] link\n[3/3] package\nbuilt dist/demo-2.4.0.tar.gz"),
            tool("monitor", command="tail -n0 -F build.log | grep --line-buffered -E 'ERROR|WARN'", description="Errors in the build log",
                 cwd=PROJECT, background=True, command_id=tail, child_id=tail, job_id=tail, event_count=2, timeout_ms=1800000,
                 state="completed", execution_status="completed", preview="2 events"),
            tool("get_command_output", command_id=build, timeout_ms=120000, preview="[2/3] link", output="[1/3] compile\n[2/3] link",
                 state="running", execution_status="running"),
            delivery(monitor_event(tail, 1, "tail -n0 -F build.log", "WARN deprecated flag -fno-common", description="Errors in the build log",
                                   elapsed_ms=41000),
                     command_result(build, "completed", "make release", "[3/3] package\nbuilt dist/demo-2.4.0.tar.gz",
                                    description="Build the release", exit_code=0, duration_ms=184000)),
            tool("run_terminal_command", command="./soak.sh --hours 6", description="Run the soak test", cwd=PROJECT, background=True,
                 command_id=soak, child_id=soak, job_id=soak, state="cancelled", execution_status="cancelled", status="interrupted"),
            tool("kill_command", command_id=soak, description="Run the soak test", preview="stopped"),
            tool("kill_command", command_id=build, preview="already completed", exit_code=0),
            "The release built in 3 min 4 s. The log had one warning so far; the watch keeps going.",
            minutes_ago=30, elapsed_s=226),
        wake(monitor_event(tail, 2, "tail -n0 -F build.log", "ERROR upload: 503 from the mirror", description="Errors in the build log",
                           elapsed_ms=402000),
             monitor_result(tail, "timed_out", "tail -n0 -F build.log", 2, description="Errors in the build log", timeout_ms=1800000,
                            duration_ms=1800000),
             command_result("qa-job-upload", "failed", "scripts/upload.sh dist/demo-2.4.0.tar.gz", "uploading… 503 Service Unavailable",
                            description="Upload the release", duration_ms=9100, error="exit 22")),
        reply("The upload failed: the mirror returned 503. The build itself is fine, and the log watch has ended.",
              minutes_ago=20, elapsed_s=12),
    ]


SKILL_BODY = "# Release checklist\n\nRun the checks, bump the version, tag, then build.\n\n1. `make check`\n2. Bump `VERSION`\n3. `git tag v$(cat VERSION)`"


def planning_thread(proposal: str) -> list[dict]:
    """Planning and housekeeping calls: todos, scratch, tools, skills, models, transcript, memory.
    The pending skill draft is a real proposal the seed makes; it expires
    after 24 hours (reseed)."""
    return [
        user("Plan the 2.4 release and remember that I want short replies."),
        reply(
            tool("todo_write", todos=[{"id": "1", "content": "Run the checks", "status": "pending"},
                                      {"id": "2", "content": "Bump the version to 2.4.0", "status": "pending"},
                                      {"id": "3", "content": "Tag and build the release", "status": "pending"},
                                      {"id": "4", "content": "Publish the changelog to the old wiki", "status": "pending"}]),
            tool("todo_write", todos=[{"id": "1", "content": "Run the checks", "status": "completed"},
                                      {"id": "2", "content": "Bump the version to 2.4.0", "status": "in_progress"},
                                      {"id": "3", "content": "Tag and build the release", "status": "pending"},
                                      {"id": "4", "content": "Publish the changelog to the old wiki", "status": "cancelled"}]),
            tool("search_tools", query="shell", preview="run_terminal_command, get_command_output, kill_command, monitor",
                 output=json.dumps({"loaded": ["run_terminal_command", "get_command_output", "kill_command", "monitor"]})),
            tool("search_tools", query="hosts", output="fuse-dev  linux  roots: /tmp, /srv/demo"),
            tool("load_skill", preview="release-checklist", summary="Steps to cut a release of the demo project.", output=SKILL_BODY),
            tool("load_skill", preview="release-checklist", path="references/versioning.md", output="Versions are MAJOR.MINOR.PATCH."),
            tool("search_models", query="small", output=json.dumps({"models": [
                {"id": "qa/fixture-small", "name": "Fixture Small", "provider": "qa"},
                {"id": "qa/fixture-large", "name": "Fixture Large", "provider": "qa"}]})),
            tool("scratch", description="Save the release plan", preview="Wrote release-plan (412 chars)"),
            tool("scratch", preview="release-plan (412 chars)"),
            tool("scratch", preview="2 slots", output=json.dumps({"slots": {"release-plan": 412, "wave-2-prompts": 3810}})),
            tool("read_transcript", preview="messages 1-40 of 120"),
            tool("remember", title="Prefers short replies", preview="saved"),
            tool("recall", title="release habits", preview="2 memories",
                 output="- Prefers short replies\n- Releases go out on Tuesdays"),
            tool("forget", title="Old wiki location", preview="forgotten"),
            tool("propose_skill", preview="release-checklist", summary="Steps to cut a release of the demo project."),
            {"_perm": {"id": proposal, "kind": "skill_save", "status": "pending", "slug": "release-checklist",
                       "description": "Steps to cut a release of the demo project.", "files": ["references/versioning.md"]}},
            "The plan is in the task list. I'll keep replies short, and the checklist is drafted as a skill for you to save.",
            minutes_ago=90, elapsed_s=64),
        user("Save it, and draft one for hotfixes too."),
        reply(tool("propose_skill", preview="hotfix-checklist", summary="Steps for a hotfix release."),
              perm(kind="skill_save", status="saved", slug="release-checklist", description="Steps to cut a release of the demo project."),
              perm(kind="skill_save", status="rejected", slug="hotfix-checklist", description="Steps for a hotfix release."),
              perm(kind="skill_save", status="error", slug="old-checklist", description="An older checklist.", overwrite=True,
                   error="a built-in skill already uses this name"),
              "Saved the release checklist. You turned down the hotfix draft.", minutes_ago=85, elapsed_s=9),
    ]


ARTIFACT_V1 = "<!doctype html><html><body><h1>Release 2.4</h1><p>Build: passing.</p></body></html>"
ARTIFACT_V2 = "<!doctype html><html><body><h1>Release 2.4</h1><p>Build: passing. Upload: failed (503).</p></body></html>"


def artifacts_thread(ident: str) -> list[dict]:
    """Artifact calls against a real artifact the seed makes (v1, then v2)."""
    art = dict(identifier=ident, title="Release 2.4 status", format="html", host="", project_root="")
    return [
        user("Make me a status page for the release."),
        reply(tool("create_artifact", version=1, visibility="private", **art),
              tool("read_artifact", version=1, preview="lines 1-1 of 1", **art),
              tool("edit_artifact", version=2, **art),
              tool("update_artifact", status="error", error="artifact is at v2, not v1: it changed since you read it", **art),
              "The status page is up; it now shows the failed upload.", minutes_ago=70, elapsed_s=33),
    ]


# Inline artifacts: charts drawn by the kit (app/artifact_chart.js) in the reply.
INLINE_LOGINS = {
    "type": "line", "x": "day", "y": "logins",
    "headline": {"value": 20934, "label": "logins", "delta": "▲ 12% vs the 30 days before", "good": True},
    "data": [{"day": f"2026-{10 + (d + 22) // 31:02d}-{(d + 22) % 31 + 1:02d}", "logins": round(640 + 110 * math.sin(d / 2.6) + d * 5 - (170 if d % 7 in (1, 2) else 0))}
             for d in range(30)],
}
INLINE_MODELS = {
    "type": "bar", "x": "model", "y": "requests",
    "data": [{"model": m, "requests": n} for m, n in (("Demo model A", 48210), ("Demo model B", 31877), ("Demo model C", 27455),
                                                      ("Demo model D", 14980), ("Demo model E", 6044))],
}
INLINE_WEEK = {
    "type": "stat",
    "data": [{"label": "Requests", "value": "182.4k", "delta": "▲ 8.1%", "good": True, "note": "vs last week"},
             {"label": "Tokens", "value": "41.2M", "delta": "▲ 14.0%", "good": True, "note": "vs last week"},
             {"label": "Error rate", "value": "0.42%", "delta": "▼ 0.10 pt", "good": True, "note": "vs last week"},
             {"label": "p95 latency", "value": "1.8 s", "delta": "▲ 0.2 s", "good": False, "note": "vs last week"}],
}


# Sloppy data as a model might send it: number-like strings, missing values,
# nested values, long labels, negatives, too many rows and columns.
INLINE_EDGES = (
    ("table", "Machines, as reported", {"type": "table", "data": [
        {"host": "demo-a", "cpu": "45%", "mem": 61.237894, "uptime": "12 days, 4:03:11", "status": "ok"},
        {"host": "a-really-long-hostname-that-goes-on-and-on.internal.example.invalid", "cpu": 12, "mem": "n/a",
         "notes": "Kernel updated, rebooted after the security patch on Tuesday night", "status": "degraded"},
        {"host": "demo-c", "cpu": 1234567.891, "mem": -3, "notes": {"nested": [1, 2]}, "status": None},
        {"cpu": 7, "host": "demo-d 🎮", "mem": 99.9, "status": "down", "extra_only_here": "surprise"},
        {"host": "", "cpu": None, "mem": None, "status": "unknown"}]}),
    ("bars", "Change by team", {"type": "bar", "x": "team", "y": "delta", "unit": "%", "data": [
        {"team": t, "delta": v} for t, v in (("Sales and partnerships", 12.5), ("Operations", -8.25), ("Customer support", -31),
                                             ("R&D", "4"), ("Legal", 0), ("Unassigned", None))]}),
    ("weeks", "Tickets per week", {"type": "column", "x": "week", "y": "tickets", "data": [
        {"week": f"2026-W{w:02d}", "tickets": (w * 53) % 400} for w in range(52, 0, -1)]}),
    ("gaps", "Daily signups, with gaps", {"type": "line", "x": "day", "y": "signups",
        "headline": {"value": "1,234", "label": "signups this month, the most since the instance was installed", "delta": "▲ 12% vs October", "good": True},
        "data": [{"day": f"2026-11-{d:02d}", "signups": None if d % 6 == 0 else 30 + (d * 17) % 40} for d in range(1, 22)]}),
)


def inline_edges_thread(idents: dict) -> list[dict]:
    """Inline charts fed sloppy data: each must fit its frame, wide or narrow."""
    out = []
    for key, title, _ in INLINE_EDGES:
        out += [user(f"Show me {title.lower()}."),
                reply(tool("create_artifact", version=1, visibility="private", display="inline", format="chart",
                           identifier=idents[key], title=title, host="", project_root=""), f"{title}.", minutes_ago=20, elapsed_s=5)]
    return out


def inline_thread(idents: dict) -> list[dict]:
    """Inline artifacts (display inline): real chart rows the seed makes."""
    def art(key: str, title: str) -> dict:
        return tool("create_artifact", version=1, visibility="private", display="inline", format="chart",
                    identifier=idents[key], title=title, host="", project_root="")
    return [
        user("Show me logins over the last 30 days."),
        reply(art("logins", "Logins, last 30 days"), "Logins for the last 30 days, up 12% on the 30 days before.",
              minutes_ago=50, elapsed_s=9),
        user("How did this week compare to last week?"),
        reply(art("week", "This week vs last week"), "Requests and tokens are up, errors are down, and p95 latency rose a little.",
              minutes_ago=40, elapsed_s=7),
        user("Which models got the most requests this month?"),
        reply(art("models", "Requests by model, November"), "Model A took 34% of requests; the top three made up 76%.",
              minutes_ago=30, elapsed_s=8),
    ]


def _png(width: int, height: int) -> str:
    """A small made-up gradient as a PNG data URI."""
    import base64
    import struct
    import zlib

    rows = b"".join(b"\0" + bytes(v for x in range(width) for v in (40 + 4 * x % 200, 90, 160 + y * 3 % 90)) for y in range(height))
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


def notices_thread() -> list[dict]:
    """Attachments, permission outcomes, run markers, notices, an unknown tool, a stored provider error.
    By design no view draws a settled tool_permission, and notices show only
    on a stopped, failed or live reply."""
    import base64

    pdf = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4\n% made-up QA file\n%%EOF\n").decode()
    shell = dict(tool="run_terminal_command", host=HOST, cwd=PROJECT, requester="Main agent")
    return [
        user("Here is the failing screenshot and the log. Clean up the build dir and retry.", attachments=[
            {"name": "failure.png", "kind": "image", "mime": "image/png", "data": _png(160, 100)},
            {"name": "build-log.csv", "kind": "text", "mime": "text/csv", "data": "step,status\ncompile,ok\nupload,503\n"},
            {"name": "runbook.pdf", "kind": "file", "mime": "application/pdf", "data": pdf}]),
        reply(
            tool("read_file", path=f"{PROJECT}/docs/failure.png", image={"width": 800, "height": 533, "original_width": 1600,
                                                                          "original_height": 1066, "mime": "image/png"}),
            perm(kind="tool_permission", status="allowed", command="rm -rf build", grant_scope="allow_session", **shell),
            tool("run_terminal_command", command="rm -rf build", description="Clean the build directory", cwd=PROJECT, exit_code=0,
                 execution_duration_ms=300, output=""),
            perm(kind="tool_permission", status="denied", command="git push --force origin main", **shell),
            perm(kind="tool_permission", status="timeout", tool="write_file", host=HOST, path="/etc/hosts", requester="Main agent"),
            marker("prune", "Pruned 14 old tool outputs to save context"),
            marker("compaction", "Context compacted", summary="## Summary\n\nThe build failed at upload (503). The build dir was cleaned; a force-push was denied."),
            marker("resume", "Resumed after the server restarted"),
            tool("browser_navigate", status="error", error="Unknown tool: browser_navigate"),
            "Cleaned the build directory. I did not force-push: you denied it.",
            minutes_ago=12, elapsed_s=140, usage={"prompt_tokens": 18400, "completion_tokens": 912, "total_tokens": 19312}),
        user("Try the upload once more."),
        reply("Retrying the upload", marker("notice", "Provider busy, retrying (3 of 3)"), minutes_ago=10, elapsed_s=61,
              notice="Stopped after 3 failed upload retries",
              error="Upstream returned 502 Bad Gateway after 3 retries (provider: qa/fixture-model)"),
    ]


def live_agents_thread() -> list[dict]:
    """A reply caught mid-run with a subagent working, a watch armed and a
    background command going (use with --fake running)."""
    return [
        user("Audit the connectors and watch the build log meanwhile."),
        reply(
            tool("monitor", status="running", command="tail -n0 -F build.log | grep --line-buffered ERROR", description="Errors in the build log",
                 cwd=PROJECT, background=True, command_id="qa-live-tail", child_id="qa-live-tail", event_count=1, timeout_ms=1800000,
                 state="running", execution_status="running"),
            tool("run_terminal_command", status="running", command="make release", description="Build the release", cwd=PROJECT,
                 background=True, command_id="qa-live-build", child_id="qa-live-build", state="running", execution_status="running",
                 timeout_ms=1800000),
            "Both are running. Starting the audit.\n\n",
            tool("spawn_subagent", status="running", description="Audit the Linux connector", preview="Audit the Linux connector",
                 subagent_type="explore", child_id="qa-live-child", agent_tool_uses=6, agent_tokens=12100),
            live=True),
    ]


LONG_CMD = ("docker run --rm -v \"$PWD\":/work -w /work -e PYTHONDONTWRITEBYTECODE=1 -e PIP_DISABLE_PIP_VERSION_CHECK=1 --network host "
            "ghcr.io/example/toolchain:2024.11.3-python3.12-slim bash -lc 'pip install -q -r requirements-dev.txt && pytest -q -x tests/core'")
HEREDOC = "cat > /tmp/qa-notes.md <<'EOF'\n# Notes\n- reconnect drops the pending reply\nEOF\nwc -l /tmp/qa-notes.md"
DEEP = "/home/someone/projects/clients/northwind/services/api-gateway/internal/handlers/websocket"
LONG_NAME = "test_reattach_after_close_drops_old_frames_and_keeps_the_pending_reply.py"
UNICODE_OUT = "name\tsize\tnote\nrésumé.txt\t4 KB\tdéjà vu\n日本語.md\t12 KB\t翻訳済み\nemoji.txt\t1 KB\t✅ ok 🚀\n\tindented\t\ttabs"
MINIFIED = json.dumps({"items": [{"id": i, "name": f"connector-{i}", "state": "reconnecting" if i % 3 else "ok"} for i in range(12)]})


def edge_thread() -> list[dict]:
    """Inputs and outputs at their edges: very long, very short, multi-line, unicode, root paths."""
    return [
        user("Show me the awkward ones."),
        reply(
            "Long, short and multi-line commands.\n\n",
            tool("run_terminal_command", command=LONG_CMD, cwd=PROJECT, exit_code=0, execution_duration_ms=48210, output="........\n8 passed in 3.02s"),
            tool("run_terminal_command", command="ls", cwd=PROJECT, exit_code=0, execution_duration_ms=4, output="a"),
            tool("run_terminal_command", command=HEREDOC, description="Write the notes file", cwd="/tmp", exit_code=0, execution_duration_ms=12,
                 output="3 /tmp/qa-notes.md"),
            tool("run_terminal_command", command="grep -rn TODO app/", cwd=DEEP, exit_code=1, execution_duration_ms=35, output=""),
            tool("run_terminal_command", command="ls -la && cat config.json | jq '.items[] | select(.state != \"ok\")'",
                 description="Check that every connector reconnects after the hub restarts and that no pending reply is lost on the way",
                 cwd=PROJECT, exit_code=0, execution_duration_ms=1240, output=UNICODE_OUT),
            tool("run_terminal_command", command="curl -s localhost:8080/connectors", cwd=PROJECT, exit_code=0, execution_duration_ms=95, output=MINIFIED),
            "Paths at the edges.\n\n",
            tool("read_file", path="/etc/hosts", output=numbered(1, ["127.0.0.1 localhost", "::1 localhost"])),
            tool("read_file", path=f"{DEEP}/{LONG_NAME}", output=numbered(1, ["import pytest"])),
            tool("list_dir", path=PROJECT, output="app/\ntests/\nREADME.md"),
            tool("grep", pattern=r"^(async\s+)?def\s+test_[a-z_]+\(.*(hub|socket).*\):$", path=f"{PROJECT}/tests",
                 output="tests/core/agents/test_hub.py:88:async def test_reattach_keeps_pending_reply(hub):"),
            tool("glob", pattern="**/*.{ts,tsx}", path=DEEP, output="index.ts\nsocket.tsx"),
            tool("grep", status="error", pattern="(unclosed", path=PROJECT,
                 error="regex parse error:\n    (unclosed\n    ^\nerror: unclosed group"),
            tool("write_file", path=f"{PROJECT}/docs/notes.md", output="created /srv/demo/docs/notes.md (96 bytes)"),
            tool("search_replace", path=f"{PROJECT}/app/net.py",
                 preview="@@ -3 +3 @@\n-TIMEOUTS = {'connect': 5, 'read': 30}\n+TIMEOUTS = {'connect': 5, 'read': 30, 'write': 30, 'pool': 10, 'keepalive': 75, 'handshake': 15, 'close': 5}",
                 lines_added=1, lines_removed=1),
            "Web calls with long inputs.\n\n",
            tool("web_search", queries=["python asyncio websocket reconnect pending reply lost after server restart race condition", "aiohttp close", "websockets keepalive"],
                 latency_ms=1400, results=[{"title": "A very long result title that keeps going well past the width of a phone screen", "url": "https://example.com/a"},
                                           {"title": "Short", "url": "https://example.org/b"}]),
            tool("read_url", url="https://example.com/docs/guide/networking/websockets?version=2024.11&lang=en&section=reconnect#pending-replies",
                 bytes=1200, http_status=200, results=[{"title": "Reconnecting", "url": "https://example.com/docs/guide/networking/websockets",
                                                        "snippet": "A client that reconnects should resend what it did not see acknowledged."}]),
            "Done.",
            minutes_ago=5, elapsed_s=61),
    ]


CODE = """const names = (await glob({pattern: "services/*.json"})).split("\\n").filter(Boolean);
const rows = await Promise.all(names.map(async (p) => ({p, ...JSON.parse(await read_file(p))})));
const slow = rows.filter((r) => r.timeout_ms * (r.retries + 1) > 60000).map((r) => r.p);
for (const p of slow) await search_replace({path: p, old_string: '"retries": 3', new_string: '"retries": 1'});
const t = await run_terminal_command({command: "make check", description: "Run the checks"});
return {slow, exit: t.exit_code};"""
CODE_RESULT = '{\n  "slow": [\n    "services/a.json"\n  ],\n  "exit": 0\n}\n[Fuse: 6 calls: glob×1, read_file×3, search_replace×1, run_terminal_command×1]'
RETRY_DIFF = "@@ -2,3 +2,3 @@\n   \"timeout_ms\": 30000,\n-  \"retries\": 3,\n+  \"retries\": 1,\n   \"backoff\": \"exponential\""


def code_thread() -> list[dict]:
    """run_code rows: a run with each kind of call under it, a bulk run of
    40 reads, a run whose code failed, one saved before description was
    required and one the user stopped mid-way."""
    run = tool("run_code", description="Find slow retry policies and lower them", output=CODE_RESULT, code=CODE, duration_ms=1840)
    calls = [
        tool("glob", parent=run["id"], pattern="services/*.json", path=PROJECT,
             output="services/a.json\nservices/b.json\nservices/c.json"),
        *[tool("read_file", parent=run["id"], path=f"{PROJECT}/services/{n}.json",
               output=f'{{\n  "timeout_ms": {t},\n  "retries": {r},\n  "backoff": "exponential"\n}}')
          for n, t, r in (("a", 30000, 3), ("b", 5000, 0), ("c", 12000, 2))],
        tool("search_replace", parent=run["id"], path=f"{PROJECT}/services/a.json", preview=RETRY_DIFF, lines_added=1, lines_removed=1),
        tool("run_terminal_command", parent=run["id"], command="make check", description="Run the checks", cwd=PROJECT,
             exit_code=0, execution_duration_ms=900, output="lint ok\nunit ok"),
    ]
    bulk = tool("run_code", description="Count TODOs in every module", duration_ms=3100,
                output='17\n[Fuse: 41 calls: glob×1, read_file×40]',
                code='const files = (await glob({pattern: "app/*.py"})).split("\\n").filter(Boolean);\n'
                     'return (await Promise.all(files.map(async (p) => (await read_file(p)).split("TODO").length - 1)))'
                     '.reduce((a, b) => a + b, 0);')
    bulk_calls = [tool("glob", parent=bulk["id"], pattern="app/*.py", path=PROJECT,
                       output="\n".join(f"app/module_{i:02d}.py" for i in range(40)))]
    bulk_calls += [tool("read_file", parent=bulk["id"], path=f"{PROJECT}/app/module_{i:02d}.py",
                        output=f"# module {i}\n# TODO: tidy\nVALUE = {i}") for i in range(40)]
    failed = tool("run_code", status="error", kind="code_error", description="Parse every config",
                  error="line 1: SyntaxError: missing ) after argument list",
                  output="Error: line 1: SyntaxError: missing ) after argument list\nNo tool calls finished before the error.",
                  code="return files.map((p) => JSON.parse(p);")
    bare = tool("run_code", duration_ms=140, output=" M app/net.py\n?? tests/test_net.py\n[Fuse: 1 calls: run_terminal_command×1]",
                code='return (await run_terminal_command("git status --short")).output;')
    bare_calls = [tool("run_terminal_command", parent=bare["id"], command="git status --short", cwd=PROJECT, exit_code=0,
                       execution_duration_ms=60, output=" M app/net.py\n?? tests/test_net.py")]
    stopped = tool("run_code", status="interrupted", description="Bump the version and build", error="Interrupted by the user")
    stopped_calls = [
        tool("search_replace", parent=stopped["id"], path=f"{PROJECT}/package.json", lines_added=1, lines_removed=1,
             preview='@@ -2,3 +2,3 @@\n   "name": "demo",\n-  "version": "2.3.9",\n+  "version": "2.4.0",\n   "private": true'),
        tool("run_terminal_command", parent=stopped["id"], status="interrupted", command="npm run build",
             description="Build the release", cwd=PROJECT, error="Interrupted by the user"),
    ]
    return [
        user("Lower the retry count on any service whose worst-case wait is over a minute, then run the checks."),
        reply("Scanning the service configs in one pass.\n\n", run, *calls,
              "One service was over a minute (`services/a.json`, 30 s × 4 tries); its retries are now 1 and the checks pass.\n\n",
              failed, bulk, *bulk_calls, "There are 17 TODOs across the 40 modules.\n\n", bare, *bare_calls,
              "Two files are uncommitted.", minutes_ago=5, elapsed_s=41),
        user("Bump the version to 2.4.0 and build it."),
        reply("Bumping the version and building in one pass.\n\n", stopped, *stopped_calls, minutes_ago=3, elapsed_s=12,
              stopped=True),
    ]


def live_code_thread() -> list[dict]:
    """A run_code caught mid-run: two of its calls done, its test run still
    going. Nothing runs on the server; the QA browser's --fake running tells
    the page it is."""
    run = tool("run_code", status="running", description="Rename fetchUser to loadUser and run the tests",
               code='const hits = (await grep({pattern: "fetchUser\\\\(", output_mode: "files_with_matches"})).split("\\n").filter(Boolean);\n'
                    'for (const path of hits) await search_replace({path, old_string: "fetchUser(", new_string: "loadUser(", replace_all: true});\n'
                    'return (await run_terminal_command({command: "npm test", description: "Run the tests"})).exit_code;')
    return [
        user("Rename fetchUser to loadUser everywhere, then run the tests."),
        reply(
            "Renaming in one pass, then running the tests.\n\n", run,
            tool("grep", parent=run["id"], pattern="fetchUser\\(", path=PROJECT, output="src/api/users.js\nsrc/ui/profile.js"),
            tool("search_replace", parent=run["id"], path=f"{PROJECT}/src/api/users.js", lines_added=1, lines_removed=1,
                 preview="@@ -1,3 +1,3 @@\n-export async function fetchUser(id) {\n+export async function loadUser(id) {\n   return api.get(id);"),
            tool("run_terminal_command", parent=run["id"], status="running", command="npm test", description="Run the tests",
                 cwd=PROJECT, timeout_ms=120000, output="PASS src/api/users.test.js\nPASS src/ui/profile.test.js"),
            live=True),
    ]


# Choices artifacts (app/artifact_choices.js): options for the user's own
# settings, each with a Use this button. They target "demo", a placeholder
# project on a placeholder machine (_choices_project).
def _logo(body: str) -> str:
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">' + body + "</svg>"


CHOICE_LOGOS = [
    {"name": "Bracket", "svg": _logo('<rect x="4" y="4" width="56" height="56" rx="14" fill="#3E63DD"/>'
                                     '<path d="M26 18 16 32l10 14M38 18l10 14-10 14" fill="none" stroke="#fff" stroke-width="6" '
                                     'stroke-linecap="round" stroke-linejoin="round"/>')},
    {"name": "Spark", "svg": _logo('<circle cx="32" cy="32" r="28" fill="#12A594"/>'
                                   '<path d="M32 12c2 12 8 18 20 20-12 2-18 8-20 20-2-12-8-18-20-20 12-2 18-8 20-20z" fill="#fff"/>')},
    {"name": "Stack", "svg": _logo('<path d="M32 8 58 22 32 36 6 22z" fill="#FFC53D"/>'
                                   '<path d="M6 32l26 14 26-14" fill="none" stroke="#F76B15" stroke-width="5" stroke-linejoin="round"/>'
                                   '<path d="M6 42l26 14 26-14" fill="none" stroke="#E5484D" stroke-width="5" stroke-linejoin="round"/>')},
    {"name": "Monogram", "svg": _logo('<rect x="4" y="4" width="56" height="56" rx="28" fill="#8E4EC6"/>'
                                      '<path d="M23 17h9c9 0 16 7 16 15s-7 15-16 15h-9z" fill="none" stroke="#fff" stroke-width="6" '
                                      'stroke-linejoin="round"/>')},
]
CHOICE_COLORS = [{"name": "Moss", "color": "#46A758"}, {"name": "Ocean", "color": "#0090FF"},
                 {"name": "Amber", "color": "#FFC53D"}, {"name": "Violet", "color": "#8E4EC6"}]


def _backdrop(base: str, top: str, dot: str) -> str:
    """A small seeded backdrop that keeps the house rules (customize-fuse)."""
    return (
        '<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;height:100%;overflow:hidden;background:'
        + base + '}canvas{display:block;width:100%;height:100%}</style></head><body><canvas id="c"></canvas><script>'
        '(function(){var c=document.getElementById("c"),x=c.getContext("2d"),d=Math.min(devicePixelRatio||1,1.5),w=0,h=0,u=0,last=0;'
        'var still=matchMedia("(prefers-reduced-motion: reduce)").matches;'
        'function draw(t){var g=x.createLinearGradient(0,0,0,h);g.addColorStop(0,"' + top + '");g.addColorStop(1,"' + base + '");'
        'x.fillStyle=g;x.fillRect(0,0,w,h);for(var i=0;i<70;i++){var px=((i*0.137)%1)*w,py=(((i*0.071)%1)*h+t*0.0003*u*(1+i%3))%h;'
        'if(Math.abs(px-w/2)<w*0.22)continue;x.globalAlpha=0.12+0.08*(i%3);x.fillStyle="' + dot + '";'
        'x.beginPath();x.arc(px,py,u*(0.15+0.1*(i%3)),0,7);x.fill();}x.globalAlpha=1;}'
        'function size(){w=innerWidth;h=innerHeight;u=Math.max(6,Math.min(w,h)/80);c.width=w*d;c.height=h*d;x.setTransform(d,0,0,d,0,0);draw(last);}'
        'function loop(t){requestAnimationFrame(loop);if(t-last<33)return;last=t;draw(t);}'
        'addEventListener("resize",size);size();if(!still)requestAnimationFrame(loop);})();'
        "</script></body></html>"
    )


CHOICE_BACKDROPS = [
    {"name": "Night drift", "scheme": "dark", "html": _backdrop("#0b0f17", "#1b1233", "#9db4ff")},
    {"name": "Deep teal", "scheme": "dark", "html": _backdrop("#06161a", "#0d2a30", "#6ad4c8")},
    {"name": "Paper haze", "scheme": "light", "html": _backdrop("#f6f0e6", "#e9dfcf", "#b45c2e")},
]
CHOICES = (
    ("look", "A new look for demo", {"project": "demo", "project_logo": CHOICE_LOGOS, "folder_color": CHOICE_COLORS},
     "Give the demo project a logo, and a folder color to go with it.",
     "Here are four logos and four folder colors for demo. Pick the ones you like."),
    ("backdrop", "Backdrop ideas", {"backdrop": CHOICE_BACKDROPS},
     "Make me a calm backdrop for the chat.",
     "Three calm backdrops to try. Preview each one, then use the one you like."),
    ("names", "Names for this chat and project", {"project": "demo", "project_name": ["demo-api", "Demo service", "Harbor"],
                                                 "chat_title": ["Project look", "Logo and colors for demo", "Demo branding"]},
     "Rename this chat and suggest a better name for the project.",
     "A few names for the project and titles for this chat. Pick one of each, or none."),
)


def choices_thread(idents: dict) -> list[dict]:
    out = []
    for key, title, _spec, ask, answer in CHOICES:
        out += [user(ask), reply(tool("create_artifact", version=1, visibility="private", display="inline", format="choices",
                                      identifier=idents[key], title=title, host="", project_root=""), answer,
                                 minutes_ago=15, elapsed_s=9)]
    return out


def _choices_project(uid: int) -> tuple[int, int]:
    """The placeholder machine and project the choices chat changes: disabled,
    an .invalid hostname and no token, so nothing ever connects to it."""
    from app import db

    ts = db.now()
    host = db.fetchone("SELECT id FROM user_hosts WHERE user_id=? AND alias='qa-box'", (uid,))
    if host is None:
        with db.tx() as conn:
            conn.execute("INSERT INTO user_hosts (user_id, alias, hostname, username, auth_type, secret, default_cwd, disabled,"
                         " created_at, updated_at) VALUES (?,?,?,?,?,?,?,1,?,?)",
                         (uid, "qa-box", "qa-box.invalid", "qa", "agent", "", PROJECT, ts, ts))
            host_id = int(db.last_id(conn))
    else:
        host_id = int(host["id"])
    db.execute("UPDATE user_hosts SET disabled=1, device_token_hash='' WHERE id=?", (host_id,))
    project = db.fetchone("SELECT id FROM user_projects WHERE user_id=? AND host_id=? AND remote_path=?", (uid, host_id, PROJECT))
    if project is None:
        with db.tx() as conn:
            conn.execute("INSERT INTO user_projects (user_id, host_id, name, remote_path, notes, created_at, updated_at)"
                         " VALUES (?,?,?,?,'',?,?)", (uid, host_id, "demo", PROJECT, ts, ts))
            return host_id, int(db.last_id(conn))
    # Back to the fixture's look: a reseed undoes what a check applied.
    db.execute("UPDATE user_projects SET name='demo', color='', archived=0, logo_rev=-ABS(logo_rev) WHERE id=?", (project["id"],))
    db.execute("DELETE FROM project_logos WHERE project_id=?", (project["id"],))
    return host_id, int(project["id"])


THREADS = [
    ("QA · Long markdown", lambda: [user("Why did the background command never report back?"), reply(MARKDOWN, minutes_ago=300, elapsed_s=48)]),
    ("QA · Summary lines", summary_thread),
    ("QA · Live command", live_thread),
    ("QA · Live code", live_code_thread),
    ("QA · Live agents", live_agents_thread),
    ("QA · Notices and attachments", notices_thread),
    ("QA · Artifacts", artifacts_thread),
    ("QA · Inline artifacts", inline_thread),
    ("QA · Inline edge cases", inline_edges_thread),
    ("QA · Choices", choices_thread),
    ("QA · Planning and memory", planning_thread),
    ("QA · Background work", background_thread),
    ("QA · Subagents", agents_thread),
    ("QA · File edits and output", edits_thread),
    ("QA · Claude Code rows", rows_thread),
    ("QA · Code mode", code_thread),
    ("QA · Edge cases", edge_thread),
]


# Saved memories for Settings › Memory: global ones and one in the demo project.
QA_MEMORIES = (
    ("prefers-short-answers", "user", "Wants short, direct replies without recaps",
     "Answer first, then the reason. No closing summary.", False),
    ("staging-dashboard", "reference", "Demo staging health checks live at staging.demo.invalid/status",
     "https://staging.demo.invalid/status shows the staging health checks.", False),
    ("release-freeze-fridays", "project", "No releases of the demo project on Fridays",
     "Releases wait until Monday.\nWhy: nobody is on call at weekends.\nApplies: every release of demo.", True),
)


def _seed(uid: int) -> list[str]:
    """Replace the account's chats (and artifacts) with the fixtures; newest last.
    The artifact chat's calls name a real artifact, so its card opens."""
    from app import artifacts, chat_threads, db, memory, skill_proposals

    db.execute("DELETE FROM chat_runs WHERE user_id=?", (uid,))  # no foreign key takes them with their thread
    db.execute("DELETE FROM chat_threads WHERE user_id=?", (uid,))
    db.execute("DELETE FROM artifacts WHERE user_id=?", (uid,))
    db.execute("DELETE FROM skill_proposals WHERE user_id=?", (uid,))
    art = artifacts.create(uid, title="Release 2.4 status", html=ARTIFACT_V1)["identifier"]
    titles, pk = [], None
    for title, build in THREADS:
        if build is artifacts_thread:
            messages = build(art)
        elif build is inline_edges_thread:
            messages = build({key: artifacts.create(uid, title=title, html=json.dumps(spec), fmt="chart", display="inline")["identifier"]
                              for key, title, spec in INLINE_EDGES})
        elif build is inline_thread:
            messages = build({key: artifacts.create(uid, title=title, html=json.dumps(spec), fmt="chart", display="inline")["identifier"]
                              for key, title, spec in (("logins", "Logins, last 30 days", INLINE_LOGINS),
                                                       ("week", "This week vs last week", INLINE_WEEK),
                                                       ("models", "Requests by model, November", INLINE_MODELS))})
        elif build is choices_thread:
            host_id, project_id = _choices_project(uid)
            made = chat_threads.create_thread(uid, model=MODEL, title=title, kind="project", host_id=host_id, project_id=project_id)
            pk = chat_threads.resolve_thread_pk(uid, made["id"])
            chat_threads.update_thread(uid, made["id"], messages=build({
                key: artifacts.create(uid, title=art_title, html=json.dumps(spec), fmt="choices", display="inline", thread_id=pk)["identifier"]
                for key, art_title, spec, _ask, _answer in CHOICES}))
            titles.append(title)
            continue
        elif build is planning_thread:
            messages = build(skill_proposals.create(uid, thread_id=pk, name="release-checklist", body=SKILL_BODY,
                                                    description="Steps to cut a release of the demo project.",
                                                    files={"references/versioning.md": "Versions are MAJOR.MINOR.PATCH."})["id"])
        else:
            messages = build()
        made = chat_threads.create_thread(uid, model=MODEL, title=title, messages=messages)
        pk = chat_threads.resolve_thread_pk(uid, made["id"])
        if build is artifacts_thread:
            artifacts.update(uid, art, html=ARTIFACT_V2, thread_id=pk)
        titles.append(title)
    db.execute("DELETE FROM memories WHERE user_id=?", (uid,))
    _host_id, project_id = _choices_project(uid)
    for name, kind, description, body, in_project in QA_MEMORIES:
        memory.save(uid, name=name, type=kind, description=description, body=body,
                    project_id=project_id if in_project else None, thread_id=pk)
    return titles


# Sources: made-up keys on three providers, so /sources has panes to draw: a
# Z.AI plan (a subscription pool), NanoGPT (plan and pay-as-you-go models)
# and OpenRouter (model list, provider pins, privacy and precision routing).
# Every reading is stamped far in the future, so the usage walk and refresh
# sweeps never send these keys anywhere; sharing is private, so no other dev
# account's request lands on them. A reseed puts readings and choices back.
QA_READ_AT = "2099-01-01T00:00:00Z"
QA_OPENROUTER_ON = ("deepseek/deepseek-chat-v3.1", "z-ai/glm-4.6", "qwen/qwen3-coder", "moonshotai/kimi-k2-0905",
                    "anthropic/claude-sonnet-4.5", "google/gemini-2.5-pro")


def _credential(source_id: int, uid: int, label: str, secret: str, extra: dict) -> int:
    from app import accounts, db
    from app.crypto import encrypt

    row = db.fetchone("SELECT id FROM credentials WHERE source_id=? AND donated_by=? ORDER BY id LIMIT 1", (source_id, uid))
    if row is None:
        return accounts.add_credential(source_id, label, secret, extra=extra, donated_by=uid)
    db.execute("UPDATE credentials SET disabled=0, label=?, secret=?, extra=? WHERE id=?",
               (label, encrypt(secret), encrypt(json.dumps(extra)), row["id"]))
    return int(row["id"])


def _sources(uid: int) -> list[str]:
    from app.providers import model_prefs
    from app.sources import nanogpt, openrouter, pool_policy, zai

    read = {"fetched_at": QA_READ_AT, "checked_at": QA_READ_AT, "last_error": ""}
    day = int((datetime.now(timezone.utc) + timedelta(hours=9)).timestamp() * 1000)
    week = int((datetime.now(timezone.utc) + timedelta(days=4)).timestamp() * 1000)

    _credential(zai.ensure_zai_source(), uid, "QA Z.AI plan", "qa-fixture-zai-key-0000000000", {
        "api_key": "qa-fixture-zai-key-0000000000", "region": "global",
        "billing": {**zai.parse_quota_payload({"level": "pro", "fiveHourPercent": 34, "weeklyPercent": 12}), **read}})
    _credential(nanogpt.ensure_nanogpt_source(), uid, "QA NanoGPT", "sk-nano-qa-fixture-000000000000", {
        "auth_kind": "api", "account_id": "",
        "billing": {**nanogpt.parse_usage({"active": True, "state": "active",
                                            "dailyInputTokens": {"percentUsed": 0.18, "resetAt": day},
                                            "weeklyInputTokens": {"percentUsed": 0.41, "resetAt": week},
                                            "routing": {"paidSpendPolicyAllowsBalance": True}}),
                    "prepaid_cents": 1240, **read}})
    secret = "sk-or-v1-qa-fixture-000000000000"
    _credential(openrouter.ensure_openrouter_source(), uid, "sk-or-v1-qa0...000", secret, {
        "api_key": secret, "role": "user",
        "key_info": {"label": "sk-or-v1-qa0...000", "usage": 4.82, "limit": 25, "limit_remaining": 20.18,
                     "is_free_tier": False, **read}})
    for kind in ("zai", "nanogpt"):
        pool_policy.set_user_pref(uid, kind, share="private", use="mine")

    openrouter.load_saved()
    by_id = (openrouter.cached_catalog() or {}).get("by_id") or {}
    chosen = [mid for mid in QA_OPENROUTER_ON if mid in by_id] or [mid for mid, rec in by_id.items() if not rec.get("free")][:6]
    model_prefs.replace(uid, "openrouter", groups_on={"free": True, "paid": False},
                        models={openrouter.public_id(mid): True for mid in chosen})
    openrouter.set_privacy(uid, data_collection_deny=True, zdr=False)
    openrouter.set_precision(uid, floor="fp8", allow_unknown=True)
    for mid in chosen[:2]:
        openrouter.set_model_providers(uid, mid, ["deepinfra", "novita"])

    nanogpt.load_saved()
    paid = [mid for mid, rec in ((nanogpt.cached_catalog() or {}).get("by_id") or {}).items() if not rec.get("subscription")]
    model_prefs.replace(uid, "nanogpt", groups_on={"subscription": True, "paid": False},
                        models={nanogpt.public_id(mid): True for mid in paid[:3]})
    return ["Z.AI", "NanoGPT", f"OpenRouter ({len(chosen)} paid models on)"]


def _account() -> int:
    """The QA user's id, created on first run and kept an admin, so the QA
    browser can open the admin pages (it aborts every write). A lost password
    file means a new password (the account has no vault worth keeping)."""
    from app import accounts, db

    row = accounts.user_by_email(EMAIL)
    saved = json.loads(CREDS.read_text()) if CREDS.exists() else {}
    if not (row and saved.get("email") == EMAIL and accounts.login(EMAIL, saved.get("password", ""))):
        password = secrets.token_urlsafe(18)
        if row:
            accounts.reset_password(EMAIL, password)
        else:
            row = accounts.create_user(EMAIL, password, admin=True)
        CREDS.parent.mkdir(exist_ok=True)
        CREDS.touch(mode=0o600, exist_ok=True)
        CREDS.chmod(0o600)
        CREDS.write_text(json.dumps({"email": EMAIL, "password": password}))
    # Not accounts.set_admin: it needs another admin to act, by design.
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (int(row["id"]),))
    return int(row["id"])


def main() -> int:
    from app.config import settings

    name = urlsplit((settings.database_url or "").strip()).path.strip("/")
    if name != "fuse_dev":
        print("refused: this only ever writes to the fuse_dev database", file=sys.stderr)
        return 1
    uid = _account()
    titles = _seed(uid)
    linked = _sources(uid)
    print(f"QA account {EMAIL} (user {uid}); login kept in {CREDS.relative_to(ROOT)}")
    print("threads: " + ", ".join(titles))
    print("sources: " + ", ".join(linked))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
