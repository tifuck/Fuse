"""The step gate: host-changing calls of one step run in order and stop at the
first failure on their host. Each case runs one tool round through
tool_runner.run_tools with a scripted executor (nothing reaches a host)."""
import asyncio
import json

from app import code_mode, tool_failures
from app.chat_generation import tool_jobs, tool_runner
from tests.core.tools._support import make_host


class _Connected:
    async def is_disconnected(self):
        return False


class Exec:
    """Answers each call by its command or path with a done trace, updated by
    what `answers` gives that subject; records (name, subject, host)."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    async def __call__(self, name, arguments, **kw):
        args = json.loads(arguments)
        key = args.get("command") or args.get("path")
        self.calls.append((name, key, args.get("host", "")))
        return f"ran {key}", {"name": name, "status": "done", **self.answers.get(key, {})}


def call(i, name, **args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def one_round(h, calls, execute, request=None):
    async def go():
        jobs = None
        async for item in tool_runner.run_tools(
                calls, user_id=h.uid, allowed_urls=set(), request=request or _Connected(), completion_i=0, budgets={},
                execute_tool=execute, session={"coding": True, "bound_host_id": h.host_id, "bound_cwd": h.root}):
            if isinstance(item, list):
                jobs = item
        return jobs

    jobs = asyncio.run(go())
    return jobs, [m["content"] for m in tool_jobs._tool_messages(jobs)[1:]]


def ran(execute):
    return [key for _, key, _ in execute.calls]


def test_an_edit_after_a_failed_edit_is_not_run(ssh_host):
    execute = Exec({"a.py": {"status": "error", "error": "no match"}})
    jobs, results = one_round(ssh_host, [call(0, "search_replace", path="a.py"), call(1, "write_file", path="b.py")],
                              execute)

    assert ran(execute) == ["a.py"]
    assert jobs[1].trace["status"] == "error" and jobs[1].trace["kind"] == "not_run"
    assert results[1].startswith("[Fuse] Not run: search_replace a.py failed earlier in this step.")


def test_a_command_after_a_non_zero_exit_is_not_run(ssh_host):
    execute = Exec({"make build": {"exit_code": 2}})
    _, results = one_round(ssh_host, [call(0, "run_terminal_command", command="make build"),
                                      call(1, "run_terminal_command", command="make deploy")], execute)

    assert ran(execute) == ["make build"]
    assert "run_terminal_command make build exited 2" in results[1]


def test_a_command_after_one_moved_to_the_background_is_not_run(ssh_host):
    execute = Exec({"make test": {"status": "running"}})
    _, results = one_round(ssh_host, [call(0, "run_terminal_command", command="make test"),
                                      call(1, "write_file", path="report.txt")], execute)

    assert ran(execute) == ["make test"]
    assert "make test moved to the background and has not finished" in results[1]


def test_background_commands_and_monitors_do_not_stop_later_calls(ssh_host):
    execute = Exec({"serve": {"status": "running"}, "worker": {"status": "running"}, "tail -F log": {"status": "running"}})
    # "yes" starts a background command too (workspace_ops.common._as_bool).
    one_round(ssh_host, [call(0, "run_terminal_command", command="serve", background=True),
                         call(1, "run_terminal_command", command="worker", background="yes"),
                         call(2, "monitor", command="tail -F log"),
                         call(3, "write_file", path="a.txt")], execute)

    assert ran(execute) == ["serve", "worker", "tail -F log", "a.txt"]


def test_a_read_after_a_failed_edit_still_runs(ssh_host):
    execute = Exec({"a.py": {"status": "error"}})
    jobs, _ = one_round(ssh_host, [call(0, "write_file", path="a.py"), call(1, "read_file", path="a.py"),
                                   call(2, "write_file", path="b.py")], execute)

    assert ran(execute) == ["a.py", "a.py"]
    assert [j.trace.get("kind") for j in jobs] == [None, None, "not_run"]


def test_a_failure_on_another_host_stops_only_that_hosts_calls(ssh_host):
    make_host(ssh_host.uid, "other")
    execute = Exec({"make": {"exit_code": 1}})
    one_round(ssh_host, [call(0, "run_terminal_command", command="make", host="other"),
                         call(1, "write_file", path="a.txt"),
                         call(2, "write_file", path="b.txt", host="other")], execute)
    assert ran(execute) == ["make", "a.txt"]

    # The bound host named explicitly is the same host as an omitted one.
    execute = Exec({"make": {"exit_code": 1}})
    one_round(ssh_host, [call(0, "run_terminal_command", command="make"),
                         call(1, "write_file", path="a.txt", host="BOX")], execute)
    assert ran(execute) == ["make"]


def test_every_skipped_call_names_the_first_failure(ssh_host):
    execute = Exec({"scripts/test.sh": {"exit_code": 1}})
    _, results = one_round(ssh_host, [call(0, "run_terminal_command", command="scripts/test.sh"),
                                      call(1, "write_file", path="a.txt"),
                                      call(2, "write_file", path="b.txt")], execute)

    assert results[1] == results[2] == (
        "[Fuse] Not run: run_terminal_command scripts/test.sh exited 1 earlier in this step. Calls that change a host "
        "run in order and stop at the first failure. Send this call again when it is still needed.")


def test_a_skipped_call_is_not_logged_as_a_tool_failure(ssh_host, monkeypatch):
    logged = []
    monkeypatch.setattr(tool_failures, "record", lambda **kw: logged.append((kw["tool_name"], kw["kind"])))
    one_round(ssh_host, [call(0, "write_file", path="a.py"), call(1, "write_file", path="b.py")],
              Exec({"a.py": {"status": "error", "kind": "permission_denied"}}))

    assert logged == [("write_file", "permission_denied")]


def test_a_user_stop_interrupts_the_queued_calls_instead_of_skipping_them(ssh_host):
    stopped = asyncio.Event()

    class Stop:
        async def is_disconnected(self):
            return stopped.is_set()

    async def execute(name, arguments, **kw):
        stopped.set()
        kw["on_output"]({"output": "working"})
        await asyncio.Event().wait()

    jobs, results = one_round(ssh_host, [call(0, "run_terminal_command", command="make"),
                                         call(1, "write_file", path="a.txt")], execute, request=Stop())

    assert [(j.trace["status"], j.text) for j in jobs] == [("interrupted", "Tool interrupted.")] * 2
    assert all(j.trace.get("kind") != "not_run" for j in jobs)
    # The model learns the queued edit never started, and the command's outcome is unknown.
    assert results[0].startswith("Tool interrupted mid-run") and "outcome is unknown" in results[0]
    assert results[1] == "Not run: the turn was interrupted before this call started. Send it again if it is still needed."


def failing_code(monkeypatch, calls=(), kind="code_error"):
    """run_code answered without a worker: it makes calls [(name, args)] through the runner, then fails."""
    async def run(arguments, *, call, paused=None, owner=()):
        for name, args in calls:
            await call(name, args)
        return "Error: boom", {"name": "run_code", "status": "error", "kind": kind, "error": "boom"}

    monkeypatch.setattr(code_mode, "available", lambda: True)
    monkeypatch.setattr(code_mode, "run", run)


def code(i, text="return 1;"):
    return call(i, "run_code", code=text, description="Look around")


def test_a_failed_run_code_that_changed_nothing_stops_nothing(ssh_host, monkeypatch):
    # A read-only snippet that threw.
    failing_code(monkeypatch, [("read_file", {"path": "a.py"})])
    execute = Exec()
    jobs, _ = one_round(ssh_host, [code(0), call(1, "write_file", path="b.py"), code(2)], execute)
    assert ran(execute) == ["a.py", "b.py", "a.py"]
    assert all(j.trace.get("kind") != "not_run" for j in jobs)

    # A run that never ran.
    failing_code(monkeypatch, kind="busy")
    execute = Exec()
    one_round(ssh_host, [code(0), call(1, "write_file", path="b.py")], execute)
    assert ran(execute) == ["b.py"]


def test_a_failed_run_code_that_started_a_change_stops_later_changes_and_runs(ssh_host, monkeypatch):
    failing_code(monkeypatch, [("write_file", {"path": "a.py"})])
    execute = Exec()
    jobs, _ = one_round(ssh_host, [code(0), call(1, "write_file", path="b.py"), code(2, "return 2;")], execute)

    assert ran(execute) == ["a.py"]
    _, write, held = [j for j in jobs if not j.parent]
    assert write.trace["kind"] == held.trace["kind"] == "not_run"
    # The held-back run still shows its code.
    assert held.trace["code"] == "return 2;"


def test_kill_command_runs_after_a_failure_and_keeps_its_order(ssh_host):
    execute = Exec({"make": {"exit_code": 1}})
    one_round(ssh_host, [call(0, "run_terminal_command", command="make"), call(1, "kill_command", id="job-1"),
                         call(2, "write_file", path="a.txt")], execute)

    assert [name for name, _, _ in execute.calls] == ["run_terminal_command", "kill_command"]
