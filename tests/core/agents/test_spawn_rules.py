"""Who may spawn what: depth, admission caps, agent types, model and folder.

Admission (chat_store.start_child_run) is checked on the database; the tool
surface (agents.spawn) with the worker start stubbed; and what a child may
actually call through real runs (the provider faked per run).
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app import agents, chat_children, chat_runs, chat_store, chats, db
from app.chat_store import SpawnCapError
from tests.core.agents._support import (
    agent_host, bind_parent, child, coding_user, parent_and_thread, routed_provider, tool_names, tool_result,
)
from tests.core.helpers import quick_user, run_async, run_turn, text_frames, tool_call_frames


def _spawn(uid, thread, parent, **args):
    args.setdefault("prompt", "inspect")
    args.setdefault("description", "Inspect")
    with patch.object(chat_runs, "start", new=AsyncMock(return_value=None)):
        return run_async(agents.spawn(user={"id": uid}, request=None, parent_run_id=parent["id"],
                                      thread_id=thread["id"], arguments=json.dumps(args)))


def _child_count(uid, thread_pk):
    return db.fetchone("SELECT COUNT(*) AS c FROM chat_runs WHERE user_id=? AND thread_id=? AND parent_run_id IS NOT NULL",
                       (uid, thread_pk))["c"]


# -- depth ------------------------------------------------------------------

def test_with_nested_spawn_off_only_the_main_agent_spawns(fresh_db):
    uid = coding_user()
    thread, root = parent_and_thread(uid)
    first = child(uid, thread, root)
    assert first["agent_depth"] == 1
    with pytest.raises(SpawnCapError, match="max depth|nested spawn is off"):
        child(uid, thread, first, prompt="grand")


def test_nested_spawn_stops_at_depth_three(fresh_db):
    uid = coding_user(nested=True)
    thread, parent = parent_and_thread(uid)
    for depth in (1, 2, 3):
        parent = child(uid, thread, parent, allow_nested_spawn=True, description=f"d{depth}")
        assert parent["agent_depth"] == depth
    with pytest.raises(SpawnCapError, match="max depth"):
        child(uid, thread, parent, allow_nested_spawn=True)


def test_a_finished_or_stopping_parent_cannot_spawn(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    chat_store.request_cancel(uid, parent["id"])
    with pytest.raises(SpawnCapError, match="not active"):
        child(uid, thread, parent)
    chat_store.finish_run(parent["id"], {"content": "ok"}, "cancelled")
    with pytest.raises(SpawnCapError, match="not active"):
        child(uid, thread, parent)
    assert _child_count(uid, parent["thread_id"]) == 0


# -- admission caps --------------------------------------------------------

def test_live_tree_cap_counts_only_running_children(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    with patch.object(chat_children, "LIVE_TREE_CHILDREN", 2):
        first = child(uid, thread, parent, prompt="a")
        child(uid, thread, parent, prompt="b")
        with pytest.raises(SpawnCapError, match="live children"):
            child(uid, thread, parent, prompt="c")
        chat_store.finish_run(first["id"], {"content": "x"}, "failed")
        # Finished history never blocks a new spawn.
        assert child(uid, thread, parent, prompt="c")["status"] == "queued"


def test_per_agent_spawn_cap_is_not_spent_by_resumes(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    with patch.object(chat_children, "PER_AGENT_PARENT", 2):
        made = [child(uid, thread, parent, prompt=f"c{i}") for i in range(2)]
        for row in made:
            chat_store.finish_run(row["id"], {"content": "done"}, "completed")
        with pytest.raises(SpawnCapError, match="already spawned 2"):
            child(uid, thread, parent, prompt="overflow")
        resumed = child(uid, thread, parent, prompt="continue", resume_from_id=made[0]["id"])
    assert resumed["id"] == made[0]["id"] and resumed["status"] == "queued"


def test_user_and_process_live_caps(fresh_db):
    uid = coding_user()
    other = coding_user("other@core.test")
    thread, parent = parent_and_thread(uid)
    other_thread, other_parent = parent_and_thread(other)
    # Live runs count roots too: each user has one queued root here.
    with patch.object(chat_children, "LIVE_USER_RUNS", 2):
        child(uid, thread, parent)
        with pytest.raises(SpawnCapError, match="live agents for this user"):
            child(uid, thread, parent, prompt="again")
        assert child(other, other_thread, other_parent)["status"] == "queued"
    with patch.object(chat_children, "LIVE_PROCESS_RUNS", 4):
        with pytest.raises(SpawnCapError, match="capacity"):
            child(other, other_thread, other_parent, prompt="third")


def test_a_failed_worker_start_fails_the_child_and_refunds_the_spawn(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    with patch.object(chat_runs, "start", new=AsyncMock(side_effect=RuntimeError("worker boom"))):
        text, trace = run_async(agents.spawn(user={"id": uid}, request=None, parent_run_id=parent["id"],
                                             thread_id=thread["id"], arguments='{"prompt":"p","description":"d"}'))
    assert trace["status"] == "error" and "worker boom" in text
    assert chat_store.get_run(uid, trace["child_id"])["status"] == "failed"
    assert int(chat_store.get_run(uid, parent["id"])["request"]["agent"].get("spawns_used") or 0) == 0


# -- tool surface ------------------------------------------------------------

def test_a_new_spawn_needs_a_description_and_a_prompt(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    text, trace = _spawn(uid, thread, parent, description="")
    assert trace["status"] == "error" and "description is required" in text
    _, trace = _spawn(uid, thread, parent, prompt="  ")
    assert trace["status"] == "error"
    assert _child_count(uid, parent["thread_id"]) == 0


def test_a_scratch_slot_is_the_prompt_and_survives_a_failed_spawn(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    text, trace = _spawn(uid, thread, parent, prompt="", scratch="brief")
    assert trace["status"] == "error" and "not found" in text
    chat_store.scratch_apply(parent["id"], [{"op": "put", "name": "brief", "text": "Inspect the auth module."}])
    _, trace = _spawn(uid, thread, parent, prompt="", scratch="brief", background=True)
    assert trace["status"] == "running"
    seed = chat_store.get_run(uid, trace["child_id"])["request"]["messages"]
    assert seed[0]["content"].startswith("Inspect the auth module.")
    assert chat_store.scratch_read(parent["id"], "brief") == "Inspect the auth module."


def test_background_spawn_returns_running_at_once(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    started = []
    with patch.object(chat_runs, "start", new=AsyncMock(return_value=None)):
        text, trace = run_async(agents.spawn(user={"id": uid}, request=None, parent_run_id=parent["id"], thread_id=thread["id"],
                                             on_started=started.append,
                                             arguments=json.dumps({"prompt": "p", "description": "d", "background": True})))
    assert (trace["status"], trace["background"]) == ("running", True)
    assert started[0]["background"] is True and 'state="running"' in text
    assert chat_store.get_run(uid, trace["child_id"])["request"]["agent"]["background"] is True


def test_explore_and_plan_children_preload_only_read_tools(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    writes = {"write_file", "search_replace"}
    general = set(child(uid, thread, parent)["request"]["agent"]["loaded_tools"])
    explore = set(child(uid, thread, parent, subagent_type="explore")["request"]["agent"]["loaded_tools"])
    plan = set(child(uid, thread, parent, subagent_type="plan")["request"]["agent"]["loaded_tools"])
    assert writes <= general
    assert "read_file" in explore and not explore & writes and "monitor" not in explore
    assert "read_file" in plan and not plan & (writes | {"run_terminal_command", "monitor"})


def test_a_restricted_parent_only_spawns_its_own_type_with_nesting_off(fresh_db):
    uid = coding_user(nested=True)
    thread, root = parent_and_thread(uid)
    explorer = child(uid, thread, root, subagent_type="explore", allow_nested_spawn=True)
    text, trace = _spawn(uid, thread, explorer, subagent_type="general-purpose", background=True)
    # agents.spawn forces the child's type and turns nesting off; admission
    # then refuses a grandchild of a non-nesting depth-1 agent.
    assert trace["status"] == "error" and ("max depth" in text or "nested spawn is off" in text)
    assert _child_count(uid, root["thread_id"]) == 1


def test_explore_child_cannot_load_or_call_write_tools(fresh_db):
    uid = coding_user(nested=True)
    write = tool_call_frames([("w1", "write_file", {"path": "/tmp/x", "content": "pwned"})])
    load = tool_call_frames([("s1", "search_tools", {"query": "files"})])
    spawn = tool_call_frames([("c1", "spawn_subagent", {"prompt": "audit it", "description": "Audit",
                                                        "subagent_type": "explore"})])
    with patch("app.workspace.execute", new_callable=AsyncMock) as workspace, \
            routed_provider(go=[spawn, text_frames("done")],
                            **{"audit it": [load, write, text_frames("read-only report")]}) as provider:
        _, run = run_async(run_turn(uid, "go"))

    assert run["status"] == "completed"
    first, after_load, after_write = provider.bodies["audit it"]
    for body in provider.bodies["audit it"]:
        names = tool_names(body)
        assert not names & {"write_file", "search_replace", "monitor", "spawn_subagent"}, names
    assert "read_file" in tool_names(first)
    assert "read-only" in tool_result(after_load, "s1")
    assert "write_file" not in tool_names(after_load)
    refused = tool_result(after_write, "w1")
    assert "pwned" not in refused
    workspace.assert_not_called()


def test_spawn_tools_are_offered_to_a_child_only_when_nesting_is_on(fresh_db):
    for nested in (False, True):
        uid = coding_user(f"n{nested}@core.test", nested=nested)
        spawn = tool_call_frames([("c1", "spawn_subagent", {"prompt": "sub task", "description": "Sub"})])
        with routed_provider(go=[spawn, text_frames("done")], **{"sub task": [text_frames("ok")]}) as provider:
            run_async(run_turn(uid, "go"))
        assert "spawn_subagent" in tool_names(provider.bodies["go"][0])
        assert ("spawn_subagent" in tool_names(provider.bodies["sub task"][0])) is nested


# -- model -----------------------------------------------------------------

CATALOG = [
    {"id": "grok-4.6", "name": "Grok 4.6", "type": "grok"},
    {"id": "openrouter/x-ai/grok-4.6", "name": "Grok 4.6", "type": "openrouter"},
    {"id": "glm-5.3", "name": "GLM 5.3", "type": "zai"},
    {"id": "nanogpt/glm-5.3", "name": "GLM 5.3", "type": "nanogpt"},
]


def test_child_model_is_the_parents_unless_a_catalog_model_is_named(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    with patch("app.opencode.model_options", new=AsyncMock(return_value=CATALOG)):
        _, cloned = _spawn(uid, thread, parent, background=True)
        _, exact = _spawn(uid, thread, parent, model="openrouter/x-ai/grok-4.6", background=True)
        _, family = _spawn(uid, thread, parent, model="glm", background=True)
        unknown_text, unknown = _spawn(uid, thread, parent, model="no-such-model", background=True)
        ambiguous_text, ambiguous = _spawn(uid, thread, parent, model="grok", background=True)

    model = lambda trace: chat_store.get_run(uid, trace["child_id"])["request"]["model"]
    assert model(cloned) == parent["request"]["model"]
    assert model(exact) == "openrouter/x-ai/grok-4.6"
    # A bare family prefers the plan provider over a relisting reseller.
    assert model(family) == "glm-5.3"
    assert unknown["kind"] == "unknown_model" and "Unknown model" in unknown_text
    assert ambiguous["status"] == "error" and "Ambiguous" in ambiguous_text
    assert _child_count(uid, parent["thread_id"]) == 3


def test_resume_keeps_the_childs_model_and_a_live_follow_up_cannot_change_it(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    worker = child(uid, thread, parent, model="grok-4.6", background=True)
    text, trace = _spawn(uid, thread, parent, task_id=worker["id"], model="glm-5.3", prompt="more")
    assert trace["status"] == "error" and "Cannot change model" in text

    chat_store.save_turn_progress(worker["id"], [{"role": "user", "content": "inspect"}, {"role": "assistant", "content": "a"}])
    chat_store.finish_run(worker["id"], {"content": "done"}, "completed")
    _, trace = _spawn(uid, thread, parent, task_id=worker["id"], prompt="continue", background=True)
    assert trace["child_id"] == worker["id"]
    assert chat_store.get_run(uid, worker["id"])["request"]["model"] == "grok-4.6"


def test_a_child_on_the_parents_model_thinks_as_hard_as_the_parent(fresh_db):
    uid = coding_user()
    thread = chats.create_thread(uid, model="offline")
    parent = chat_store.start_turn(uid, thread["id"], "Hello", thread["revision"], None, reasoning_effort="xhigh")["run"]
    same = child(uid, thread, parent)
    other = child(uid, thread, parent, model="other-model")
    assert same["request"]["reasoning_effort"] == "xhigh"
    assert "reasoning_effort" not in other["request"]


# -- host and folder ---------------------------------------------------------

def test_child_cwd_is_reviewed_against_the_parents_folder(fresh_db):
    uid = coding_user()
    host, _ = agent_host(uid)
    thread, parent = parent_and_thread(uid)
    bind_parent(uid, parent, host["id"], "/srv/app")
    for cwd in ("/etc", "/", "../.."):
        _, trace = _spawn(uid, thread, parent, cwd=cwd, background=True)
        assert (trace["status"], trace.get("kind")) == ("error", "permission_denied"), cwd
    _, trace = _spawn(uid, thread, parent, cwd="lib", background=True)
    assert chat_store.get_run(uid, trace["child_id"])["request"]["agent"]["bound_cwd"] == "/srv/app/lib"
    _, inherited = _spawn(uid, thread, parent, background=True)
    agent = chat_store.get_run(uid, inherited["child_id"])["request"]["agent"]
    assert (agent["bound_host_id"], agent["bound_cwd"]) == (host["id"], "/srv/app")


def test_a_child_on_another_host_does_not_inherit_the_parents_folder(fresh_db):
    uid = coding_user()
    home, _ = agent_host(uid, "home")
    other, _ = agent_host(uid, "other")
    thread, parent = parent_and_thread(uid)
    bind_parent(uid, parent, home["id"], "/srv/app")
    _, trace = _spawn(uid, thread, parent, host=other["alias"], background=True)
    agent = chat_store.get_run(uid, trace["child_id"])["request"]["agent"]
    assert (agent["bound_host_id"], agent["bound_cwd"]) == (other["id"], "")
    text, trace = _spawn(uid, thread, parent, host="localhost", background=True)
    assert trace["status"] == "error"
    _, trace = _spawn(uid, thread, parent, host="nope", background=True)
    assert trace["status"] == "error"


def test_one_users_spawn_cannot_target_another_users_run_or_host(fresh_db):
    uid = coding_user()
    intruder = quick_user("intruder@core.test")
    thread, parent = parent_and_thread(uid)
    intruder_host, _ = agent_host(intruder, "theirs")
    text, trace = _spawn(intruder, thread, parent)
    assert trace["status"] == "error"
    _, trace = _spawn(uid, thread, parent, host=intruder_host["alias"], background=True)
    assert trace["status"] == "error"
    assert _child_count(uid, parent["thread_id"]) == 0
