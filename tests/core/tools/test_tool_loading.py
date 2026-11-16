"""Which tools a run can call: chat vs coding, deferred loading, subagent types.

Behaviour through chat_tools.execute_tool (the runtime's tool entry point):
the reply kind says whether the call ran, and loading is the `loaded` bucket
the runtime keeps per run. Schema text is not asserted.
"""
import asyncio
import json
import os

from app import agent_catalog, chat_tools, chats, permissions
from tests.core.tools._support import CardUser, new_thread


def run_tool(name, args=None, *, uid=1, coding=True, loaded=None, session=None, raw=None, **kwargs):
    return asyncio.run(chat_tools.execute_tool(
        name, raw if raw is not None else json.dumps(args or {}), user_id=uid, allowed_urls=set(),
        coding=coding, loaded=loaded, session=session, **kwargs))


def test_a_plain_chat_cannot_call_coding_tools(fresh_db):
    for name in ("read_file", "run_terminal_command", "search_tools", "spawn_subagent"):
        text, rec = run_tool(name, {"path": "x"}, coding=False)
        assert rec["status"] == "error" and rec["kind"] == "unknown_tool", name
        assert "not available in this chat" in text


def test_a_workspace_tool_runs_only_after_it_is_loaded(ssh_host):
    with open(os.path.join(ssh_host.root, "hello.txt"), "w") as f:
        f.write("hi\n")
    loaded = list(agent_catalog.ALWAYS_ON_CODING)
    kwargs = dict(uid=ssh_host.uid, loaded=loaded, bound_host_id=ssh_host.host_id, bound_cwd=ssh_host.root)

    text, rec = run_tool("read_file", {"path": "hello.txt"}, **kwargs)
    assert rec["kind"] == "tool_not_loaded" and "search_tools" in text

    _, rec = run_tool("search_tools", {"names": ["read_file"]}, **kwargs)
    assert rec["status"] == "done" and "read_file" in loaded

    text, rec = run_tool("read_file", {"path": "hello.txt"}, **kwargs)
    assert rec["status"] == "done" and "1: hi" in text


def test_loading_packs_is_capped_and_never_evicts(fresh_db):
    loaded = list(agent_catalog.ALWAYS_ON_CODING)

    run_tool("search_tools", {"query": "workspace"}, loaded=loaded)
    assert set(agent_catalog.PACKS["workspace"]) <= set(loaded)
    run_tool("search_tools", {"query": "artifact"}, loaded=loaded)
    extra = [n for n in loaded if n not in agent_catalog.ALWAYS_ON_CODING]
    assert len(extra) <= agent_catalog.MAX_EXTRA

    before = list(loaded)
    loaded.extend(f"filler_{i}" for i in range(agent_catalog.MAX_EXTRA))
    _, rec = run_tool("search_tools", {"names": ["propose_skill"]}, loaded=loaded)
    assert "propose_skill" not in loaded and rec["status"] == "error" and rec["kind"] == "refused"
    assert loaded[:len(before)] == before


def test_read_only_subagents_cannot_load_or_call_mutating_tools(fresh_db):
    explore = {"subagent_type": "explore"}
    loaded = list(agent_catalog.ALWAYS_ON_CODING)
    text, rec = run_tool("search_tools", {"names": ["write_file", "grep"]}, loaded=loaded, session=explore)
    assert "grep" in loaded and "write_file" not in loaded
    assert "write_file cannot be loaded" in text

    _, rec = run_tool("write_file", {"path": "x", "content": "y"}, session=explore)
    assert rec["kind"] == "denied"
    for name in ("run_terminal_command", "monitor", "search_replace"):
        _, rec = run_tool(name, {"command": "ls"}, session={"subagent_type": "plan"})
        assert rec["kind"] == "denied", name


def test_nested_spawn_is_refused_when_the_session_disallows_it(fresh_db):
    text, rec = run_tool("spawn_subagent", {"prompt": "x"}, session={"allow_spawn": False},
                         parent_run_id="run-1", http_request=object(), user={"id": 1})
    assert rec["kind"] == "denied" and "Nested subagents are off" in text


def test_a_hallucinated_tool_name_points_at_the_real_tool_and_its_load_call(fresh_db):
    loaded = list(agent_catalog.ALWAYS_ON_CODING)
    text, rec = run_tool("bash", {"command": "ls"}, loaded=loaded)
    assert rec["kind"] == "unknown_tool" and rec["suggestion"] == "run_terminal_command"
    assert "not loaded yet" in text

    text, rec = run_tool("read_file", raw="{not json")
    assert rec["kind"] == "invalid_arguments" and "not executed" in text


def test_only_an_in_scope_write_binds_a_plain_chat_to_its_folder(ssh_host):
    def write(path, answer=None):
        thread, tid = new_thread(ssh_host.uid)
        gate = permissions.Gate(user_id=ssh_host.uid, thread_id=tid, run_id="", requester=None, unattended=False,
                                notify=CardUser(ssh_host.uid, tid, answer), cancel=None)
        _, rec = run_tool("write_file", {"path": path, "content": "x"}, uid=ssh_host.uid, thread_id=tid, gate=gate,
                          bound_host_id=ssh_host.host_id, bound_cwd=ssh_host.root, session={"run_id": ""})
        assert rec["status"] == "done", rec
        return chats.get_thread(ssh_host.uid, thread["id"])

    assert write("src/new.py")["kind"] == "project"
    scratch = f"/tmp/fuse-core-tools-{os.getpid()}-{ssh_host.host_id}.txt"
    try:
        assert write(scratch)["kind"] != "project"  # temp is a root, never a project
    finally:
        if os.path.exists(scratch):
            os.remove(scratch)
    outside = os.path.join(ssh_host.base, "elsewhere.txt")
    assert write(outside, answer="allow")["kind"] != "project"  # "Allow once" is not a standing root
