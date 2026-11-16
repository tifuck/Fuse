"""Which tools a run can call: chat vs coding, deferred loading, subagent types.

Behaviour through chat_tools.execute_tool (the runtime's tool entry point):
the reply kind says whether the call ran, and loading is the `loaded` bucket
the runtime keeps per run. Schema text is not asserted.
"""
import asyncio
import json

from app import agent_catalog, chat_tools, chats, permissions
import pytest

from tests.core.helpers import quick_user
from tests.core.tools._support import CardUser, new_thread


@pytest.fixture
def uid(fresh_db):
    return quick_user()


def run_tool(name, args=None, *, uid, coding=True, loaded=None, session=None, raw=None, **kwargs):
    return asyncio.run(chat_tools.execute_tool(
        name, raw if raw is not None else json.dumps(args or {}), user_id=uid, allowed_urls=set(),
        coding=coding, loaded=loaded, session=session, **kwargs))


def test_a_plain_chat_cannot_call_coding_tools(uid):
    for name in ("read_file", "run_terminal_command", "search_tools", "spawn_subagent"):
        text, rec = run_tool(name, {"path": "x"}, coding=False, uid=uid)
        assert rec["status"] == "error" and rec["kind"] == "unknown_tool", name
        assert "not available in this chat" in text


def test_a_workspace_tool_runs_only_after_it_is_loaded(ssh_host):
    ssh_host.fs.write(f"{ssh_host.root}/hello.txt", "hi\n")
    loaded = list(agent_catalog.ALWAYS_ON_CODING)
    kwargs = dict(uid=ssh_host.uid, loaded=loaded, bound_host_id=ssh_host.host_id, bound_cwd=ssh_host.root)

    text, rec = run_tool("read_file", {"path": "hello.txt"}, **kwargs)
    assert rec["kind"] == "tool_not_loaded" and "search_tools" in text

    _, rec = run_tool("search_tools", {"names": ["read_file"]}, **kwargs)
    assert rec["status"] == "done" and "read_file" in loaded

    text, rec = run_tool("read_file", {"path": "hello.txt"}, **kwargs)
    assert rec["status"] == "done" and "1: hi" in text


def test_loading_packs_is_capped_and_never_evicts(uid):
    loaded = list(agent_catalog.ALWAYS_ON_CODING)

    run_tool("search_tools", {"query": "workspace"}, loaded=loaded, uid=uid)
    assert set(agent_catalog.PACKS["workspace"]) <= set(loaded)
    run_tool("search_tools", {"query": "artifact"}, loaded=loaded, uid=uid)
    extra = [n for n in loaded if n not in agent_catalog.ALWAYS_ON_CODING]
    assert len(extra) <= agent_catalog.MAX_EXTRA

    before = list(loaded)
    loaded.extend(f"filler_{i}" for i in range(agent_catalog.MAX_EXTRA))
    _, rec = run_tool("search_tools", {"names": ["propose_skill"]}, loaded=loaded, uid=uid)
    assert "propose_skill" not in loaded and rec["status"] == "error" and rec["kind"] == "refused"
    assert loaded[:len(before)] == before


def test_read_only_subagents_cannot_load_or_call_mutating_tools(uid):
    explore = {"subagent_type": "explore"}
    loaded = list(agent_catalog.ALWAYS_ON_CODING)
    text, rec = run_tool("search_tools", {"names": ["write_file", "grep"]}, loaded=loaded, session=explore, uid=uid)
    assert "grep" in loaded and "write_file" not in loaded
    assert "write_file cannot be loaded" in text

    _, rec = run_tool("write_file", {"path": "x", "content": "y"}, session=explore, uid=uid)
    assert rec["kind"] == "denied"
    for name in ("run_terminal_command", "monitor", "search_replace"):
        _, rec = run_tool(name, {"command": "ls"}, session={"subagent_type": "plan"}, uid=uid)
        assert rec["kind"] == "denied", name


def test_nested_spawn_is_refused_when_the_session_disallows_it(uid):
    text, rec = run_tool("spawn_subagent", {"prompt": "x"}, session={"allow_spawn": False}, uid=uid,
                         parent_run_id="run-1", http_request=object(), user={"id": uid})
    assert rec["kind"] == "denied" and "Nested subagents are off" in text


def test_a_hallucinated_tool_name_points_at_the_real_tool_and_its_load_call(uid):
    loaded = list(agent_catalog.ALWAYS_ON_CODING)
    text, rec = run_tool("bash", {"command": "ls"}, loaded=loaded, uid=uid)
    assert rec["kind"] == "unknown_tool" and rec["suggestion"] == "run_terminal_command"
    assert "not loaded yet" in text

    text, rec = run_tool("read_file", raw="{not json", uid=uid)
    assert rec["kind"] == "invalid_arguments" and "not executed" in text


def test_only_an_in_scope_write_binds_a_plain_chat_to_its_folder(ssh_host):
    def write(path, answer=None, session=None):
        thread, tid = new_thread(ssh_host.uid)
        gate = permissions.Gate(user_id=ssh_host.uid, thread_id=tid, run_id="", requester=None, unattended=False,
                                notify=CardUser(ssh_host.uid, tid, answer), cancel=None)
        _, rec = run_tool("write_file", {"path": path, "content": "x"}, uid=ssh_host.uid, thread_id=tid, gate=gate,
                          bound_host_id=ssh_host.host_id, bound_cwd=ssh_host.root, session=session or {"run_id": ""})
        assert rec["status"] == "done", rec
        return chats.get_thread(ssh_host.uid, thread["id"])

    # A subagent shares the thread with the main agent: its writes never create a project.
    child = {"run_id": "", "requester": {"role": "subagent", "type": "general"}}
    assert write("src/child.py", session=child)["kind"] != "project"
    assert write("src/new.py")["kind"] == "project"
    assert write("/tmp/scratch.txt")["kind"] != "project"  # temp is a root, never a project
    assert write("/srv/elsewhere.txt", answer="allow")["kind"] != "project"  # "Allow once" is not a standing root
    assert ssh_host.fs.exists("/srv/elsewhere.txt") and ssh_host.fs.exists("/tmp/scratch.txt")
