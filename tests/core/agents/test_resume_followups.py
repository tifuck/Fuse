"""Continuing subagents: follow-ups to a live child, task_id resume of a
finished one (same session, same transcript), and who may steer whom."""
import asyncio
import json
import re

import pytest

from app import agents, chat_store, chat_tx, chats
from app.chat_generation import inbox
from app.chat_store import SpawnCapError
from tests.core.agents._support import (
    agent_of, bind_parent, child, coding_user, parent_and_thread, root_runs, routed_provider, set_running, start_root,
    tool_result,
)
from tests.core.helpers import run_async, run_turn, text_frames, tool_call_frames, wait_run, wait_until_async
from tests.core.threads.test_lock_order import _race


def _child_id(body, call_id):
    return re.search(r'<task id="([^"]+)"', tool_result(body, call_id)).group(1)


def _spawn(call_id, **args):
    return tool_call_frames([(call_id, "spawn_subagent", args)])


# -- real runs ---------------------------------------------------------------

def test_task_id_resume_continues_the_same_session_with_its_transcript(fresh_db):
    uid = coding_user()

    def resume(body):
        return _spawn("c2", task_id=_child_id(body, "c1"), prompt="second task")

    with routed_provider(go=[_spawn("c1", prompt="first task", description="Worker"), resume, text_frames("done")],
                         **{"first task": [text_frames("R1"), text_frames("R2")]}) as provider:
        _, run = run_async(run_turn(uid, "go"))

    assert run["status"] == "completed"
    tools = run["message"]["tools"]
    assert tools[0]["child_id"] == tools[1]["child_id"]
    leg2 = [(m["role"], m["content"]) for m in provider.bodies["first task"][1]["messages"] if m["role"] != "system"]
    assert [role for role, _ in leg2] == ["user", "assistant", "user"]
    assert leg2[0][1].startswith("first task") and leg2[1][1] == "R1" and leg2[2][1].startswith("second task")
    assert "R2" in tool_result(provider.bodies["go"][2], "c2")
    rows = chat_store.list_thread_agents(uid, run["thread_id"])
    assert [(r["id"], r["status"]) for r in rows] == [(tools[0]["child_id"], "completed")]


def test_a_follow_up_reaches_a_live_child_at_its_next_tool_boundary(fresh_db):
    uid = coding_user()
    gate = {}

    def follow_up(body):
        gate["child"] = _child_id(body, "c1")
        return _spawn("c2", task_id=gate["child"], prompt="ALSO CHECK Y")

    def parent_ends(body):
        assert "Follow-up queued" in tool_result(body, "c2")
        gate["release"].set()
        return text_frames("launched")

    async def child_works(_body):
        await gate["release"].wait()
        return tool_call_frames([("s1", "scratch", {})])

    def child_reads_follow_up(body):
        last = body["messages"][-1]["content"]
        assert last.startswith(inbox.FOLLOWUP_HEAD) and last.count("ALSO CHECK Y") == 1
        return text_frames("Y CHECKED")

    async def body():
        gate["release"] = asyncio.Event()
        _, run, task = await start_root(uid, "go")
        await wait_run(task, 10)
        pk = run["thread_id"]
        await wait_until_async(lambda: [r["status"] for r in root_runs(uid, pk)] == ["completed", "completed"])
        return pk

    with routed_provider(go=[_spawn("c1", prompt="bg work", description="Worker", background=True), follow_up,
                             parent_ends, text_frames("relayed")],
                         **{"bg work": [child_works, child_reads_follow_up]}) as provider:
        pk = run_async(body())

    assert len(provider.bodies["bg work"]) == 2
    assert not agent_of(uid, gate["child"]).get("pending_followups")
    assert chat_store.take_followups(gate["child"]) == []
    assert provider.bodies["go"][3]["messages"][-1]["content"].count("Y CHECKED") == 1


# -- follow-up queue -----------------------------------------------------------

def test_follow_ups_are_acknowledged_by_id_not_by_matching_text(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    worker = child(uid, thread, parent, background=True)
    set_running(worker["id"])
    chat_store.save_turn_progress(worker["id"], [{"role": "user", "content": "inspect"},
                                                 {"role": "assistant", "content": "Continue? yes"}])
    for text in ("yes", "fix bugs", "yes"):
        assert chat_store.queue_followup(worker["id"], text)
    seen = chat_store.take_followups(worker["id"])
    assert [t.split(") ", 1)[1] for t in seen] == ["yes", "fix bugs", "yes"]
    assert chat_store.take_followups(worker["id"]) == seen  # peeking is not delivery

    chat_store.save_turn_progress(worker["id"], [{"role": "user", "content": "\n\n".join(seen[:2])}])
    assert chat_store.take_followups(worker["id"]) == [seen[2]]


def test_follow_ups_are_refused_rather_than_dropped(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    worker = child(uid, thread, parent, background=True)
    set_running(worker["id"])
    for i in range(8):
        assert chat_store.queue_followup(worker["id"], f"note {i}")
    assert chat_store.queue_followup(worker["id"], "one too many") is False
    assert len(chat_store.take_followups(worker["id"])) == 8

    stopping = child(uid, thread, parent, prompt="b", background=True)
    chat_store.request_cancel_self(uid, stopping["id"])
    assert chat_store.queue_followup(stopping["id"], "too late") is False
    done = child(uid, thread, parent, prompt="c")
    chat_store.finish_run(done["id"], {"content": "x"}, "completed")
    assert chat_store.queue_followup(done["id"], "too late") is False


def test_follow_ups_left_at_the_finish_line_continue_the_same_child(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    worker = child(uid, thread, parent)
    set_running(worker["id"])
    chat_store.save_turn_progress(worker["id"], [{"role": "user", "content": "inspect"},
                                                 {"role": "assistant", "content": "looked around"}])
    chat_store.queue_followup(worker["id"], "also add a test")
    chat_store.finish_run(worker["id"], {"content": "REPORT ONE", "tools": []}, "completed")

    assert chat_store.continue_child_with_followups(worker["id"], chat_store.take_followups(worker["id"]))
    row = chat_store.get_run(uid, worker["id"])
    assert row["status"] == "queued" and row["request"]["agent"]["background"] is True
    assert "looked around" in json.dumps(row["request"]["messages"])
    assert "also add a test" in row["request"]["messages"][-1]["content"]
    chat_store.finish_run(worker["id"], {"content": "TEST ADDED", "tools": []}, "completed")
    report = chat_store.child_writeup(chat_store.get_run(uid, worker["id"]))
    # The first leg's report was never delivered: it leads the final one.
    assert report.index("REPORT ONE") < report.index("TEST ADDED")


def test_follow_ups_a_stopped_or_abandoned_child_never_read_are_reported(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    stopped = child(uid, thread, parent, background=True)
    set_running(stopped["id"])
    chat_store.queue_followup(stopped["id"], "check the lexer too")
    chat_store.finish_run(stopped["id"], {"content": ""}, "cancelled")
    assert chat_store.continue_child_with_followups(stopped["id"], ["keep going"]) is False
    chat_store.flag_unconsumed_followups(stopped["id"], chat_store.take_followups(stopped["id"]))
    assert "check the lexer too" in chat_store.child_writeup(chat_store.get_run(uid, stopped["id"]))

    abandoned = child(uid, thread, parent, prompt="b", background=True)
    set_running(abandoned["id"])
    chat_store.queue_followup(abandoned["id"], "and the parser")
    assert chat_store.abort_active(abandoned["id"], "interrupted")
    view = chat_store.get_run(uid, abandoned["id"])
    assert view["request"]["agent"]["pending_followups"] == []
    assert "and the parser" in chat_store.child_writeup(view)
    assert abandoned["id"] in [c["id"] for c in chat_store.wake_candidates(uid, parent["thread_id"])]


def test_a_user_message_to_a_subagent_queues_while_live_and_continues_when_finished(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    worker = child(uid, thread, parent, background=True)
    set_running(worker["id"])
    chat_store.save_turn_progress(worker["id"], [{"role": "user", "content": "inspect"}])
    assert chat_store.message_child(uid, worker["id"], "check the lexer") == "queued"
    note = inbox.followup_message(chat_store.take_followups(worker["id"]))["content"]
    assert note.startswith(inbox.USER_FOLLOWUP_HEAD)  # framed as the user's, not the parent's
    chat_store.save_turn_progress(worker["id"], [{"role": "user", "content": "inspect"}, {"role": "user", "content": note}])

    chat_store.finish_run(worker["id"], {"content": "REPORT"}, "cancelled")
    assert chat_store.message_child(uid, worker["id"], "now fix it") == "continued"
    resumed = chat_store.get_run(uid, worker["id"])
    assert resumed["status"] == "queued" and not resumed["cancel_requested"]
    assert resumed["request"]["agent"]["user_resumed_at"]
    assert "now fix it" in resumed["request"]["messages"][-1]["content"]
    with pytest.raises(chats.ChatValidationError):
        chat_store.message_child(uid, parent["id"], "not a subagent")
    with pytest.raises(chats.ChatValidationError):
        chat_store.message_child(uid + 1, worker["id"], "someone else's")


def test_the_main_agent_follows_up_a_live_child_from_an_earlier_turn(fresh_db):
    from unittest.mock import AsyncMock, patch

    from app import chat_runs

    uid = coding_user()
    thread, first = parent_and_thread(uid)
    worker = child(uid, thread, first, background=True)
    set_running(worker["id"])
    chat_store.finish_run(first["id"], {"content": "launched"}, "completed")
    second = chat_store.start_turn(uid, thread["id"], "tell it", chats.get_thread(uid, thread["id"])["revision"], None)["run"]
    with patch.object(chat_runs, "start", new=AsyncMock(return_value=None)):
        _, rec = run_async(agents.spawn(user={"id": uid}, request=None, parent_run_id=second["id"], thread_id=thread["id"],
                                        arguments=json.dumps({"prompt": "also check Y", "task_id": worker["id"]})))
    assert rec["status"] == "running"
    assert any("also check Y" in t for t in chat_store.take_followups(worker["id"]))


# -- resume rules --------------------------------------------------------------

def _finished(uid, thread, parent, **kw):
    row = child(uid, thread, parent, **kw)
    chat_store.save_turn_progress(row["id"], [{"role": "user", "content": kw.get("prompt", "inspect")},
                                              {"role": "assistant", "content": "found issues"}])
    chat_store.finish_run(row["id"], {"content": "found issues"}, "completed")
    return row


def test_any_finished_child_resumes_with_a_fresh_budget_and_its_host(fresh_db):
    uid = coding_user()
    for status in ("completed", "interrupted", "cancelled", "failed"):
        thread, parent = parent_and_thread(uid)
        worker = child(uid, thread, parent, host_id=9, cwd="/srv/app")
        chat_store.finish_run(worker["id"], {"content": "x"}, status)
        bind_parent(uid, parent, 3, "/elsewhere")
        resumed = child(uid, thread, parent, prompt="continue", resume_from_id=worker["id"])
        agent = resumed["request"]["agent"]
        assert (resumed["id"], resumed["status"]) == (worker["id"], "queued"), status
        assert agent["remaining_seconds"] == chat_store.DEFAULT_REMAINING_SECONDS
        assert (agent["bound_host_id"], agent["bound_cwd"]) == (9, "/srv/app")
        assert resumed["created_at"] == worker["created_at"]


def test_resume_keeps_the_type_unless_the_parent_retypes_it(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    auditor = _finished(uid, thread, parent, subagent_type="explore", prompt="audit")
    kept = child(uid, thread, parent, prompt="dig deeper", resume_from_id=auditor["id"])
    assert kept["request"]["agent"]["subagent_type"] == "explore"
    assert "write_file" not in kept["request"]["agent"]["loaded_tools"]
    chat_store.finish_run(auditor["id"], {"content": "more"}, "completed")
    fixer = child(uid, thread, parent, prompt="fix them", subagent_type="general-purpose", resume_from_id=auditor["id"])
    assert fixer["request"]["agent"]["subagent_type"] == "general-purpose"
    assert "write_file" in fixer["request"]["agent"]["loaded_tools"]


def test_resume_refuses_a_live_child_another_chat_or_a_cycle(fresh_db):
    uid = coding_user(nested=True)
    thread, parent = parent_and_thread(uid)
    live = child(uid, thread, parent, allow_nested_spawn=True)
    with pytest.raises(SpawnCapError, match="not a completed child"):
        child(uid, thread, parent, resume_from_id=live["id"])
    nested = child(uid, thread, live, allow_nested_spawn=True)
    chat_store.finish_run(live["id"], {"content": "x"}, "completed")
    with pytest.raises(SpawnCapError, match="ancestor"):
        child(uid, thread, nested, resume_from_id=live["id"], allow_nested_spawn=True)

    other_thread, other_parent = parent_and_thread(uid, "other chat")
    elsewhere = _finished(uid, other_thread, other_parent)
    with pytest.raises(SpawnCapError):
        child(uid, thread, parent, resume_from_id=elsewhere["id"])


def test_a_restricted_child_cannot_resume_a_general_purpose_one(fresh_db):
    uid = coding_user(nested=True)
    thread, parent = parent_and_thread(uid)
    planner = child(uid, thread, parent, subagent_type="plan", allow_nested_spawn=True)
    # Its own (general-purpose) descendant: only the type rule can refuse it.
    general = _finished(uid, thread, planner, allow_nested_spawn=True)
    with pytest.raises(SpawnCapError, match="Restricted"):
        child(uid, thread, planner, resume_from_id=general["id"], allow_nested_spawn=True)
    sibling = _finished(uid, thread, parent, prompt="sib")
    with pytest.raises(SpawnCapError):
        child(uid, thread, planner, resume_from_id=sibling["id"], allow_nested_spawn=True)


def test_resume_cannot_orphan_live_descendants_into_another_tree(fresh_db):
    uid = coding_user(nested=True)
    thread, first = parent_and_thread(uid)
    lead = child(uid, thread, first, allow_nested_spawn=True)
    child(uid, thread, lead, allow_nested_spawn=True)
    chat_store.save_turn_progress(lead["id"], [{"role": "user", "content": "inspect"}])
    chat_store.finish_run(lead["id"], {"content": "x"}, "completed")
    chat_store.finish_run(first["id"], {"content": "x"}, "completed")
    second = chat_store.start_turn(uid, thread["id"], "next", chats.get_thread(uid, thread["id"])["revision"], None)["run"]
    with pytest.raises(SpawnCapError, match="descendants are active"):
        child(uid, thread, second, resume_from_id=lead["id"], allow_nested_spawn=True)


def test_a_subagent_cannot_join_kill_list_or_steer_a_sibling(fresh_db):
    uid = coding_user(nested=True)
    thread, parent = parent_and_thread(uid)
    a = child(uid, thread, parent, prompt="a", allow_nested_spawn=True, background=True)
    b = child(uid, thread, parent, prompt="b", background=True)
    grand = child(uid, thread, a, prompt="a1", allow_nested_spawn=True, background=True)
    for row in (a, b, grand):
        set_running(row["id"])
    args = json.dumps({"id": b["id"], "timeout_ms": 0})

    _, rec = run_async(agents.get_output(user_id=uid, arguments=args, thread_id=thread["id"],
                                         parent_run_id=a["id"], scope_run_id=a["id"]))
    assert rec["status"] == "error"
    _, rec = run_async(agents.kill(user_id=uid, arguments=args, thread_id=thread["id"], scope_run_id=a["id"]))
    assert rec["status"] == "error"
    assert chat_store.get_run(uid, b["id"])["status"] in chat_store.ACTIVE
    assert not chat_store.get_run(uid, b["id"])["cancel_requested"]
    text, _ = run_async(agents.list_agents(user_id=uid, thread_id=parent["thread_id"], scope_run_id=a["id"]))
    assert [item["id"] for item in json.loads(text)["live"]] == [grand["id"]]
    text, rec = run_async(agents.spawn(user={"id": uid}, request=None, parent_run_id=a["id"], thread_id=thread["id"],
                                       arguments=json.dumps({"prompt": "hijack", "task_id": b["id"]})))
    assert rec["status"] == "error" and "not a child" in text
    assert chat_store.take_followups(b["id"]) == []
    # Its own descendant stays reachable.
    _, rec = run_async(agents.get_output(user_id=uid, arguments=json.dumps({"id": grand["id"], "timeout_ms": 0}),
                                         thread_id=thread["id"], parent_run_id=a["id"], scope_run_id=a["id"]))
    assert rec["status"] == "running"


def test_child_ids_resolve_from_exact_tokens_only(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    api = child(uid, thread, parent, prompt="a", description="explore api")
    child(uid, thread, parent, prompt="b", description="explore db")
    assert chat_store.resolve_child_run(uid, "explore", thread_id=thread["id"]) is None
    assert chat_store.resolve_child_run(uid, "EXPLORE API", thread_id=thread["id"])["id"] == api["id"]
    assert chat_store.resolve_child_run(uid, api["id"][:8], thread_id=thread["id"])["id"] == api["id"]
    assert chat_store.resolve_child_run(uid, api["id"][:3], thread_id=thread["id"]) is None
    assert chat_store.resolve_child_run(uid + 1, api["id"], thread_id=thread["id"]) is None


def _finished_worker(uid):
    thread, parent = parent_and_thread(uid)
    worker = child(uid, thread, parent)
    set_running(worker["id"])
    chat_store.save_turn_progress(worker["id"], [{"role": "user", "content": "inspect"},
                                                 {"role": "assistant", "content": "looked around"}])
    chat_store.finish_run(worker["id"], {"content": "REPORT", "tools": []}, "completed")
    return thread, parent, worker["id"]


@pytest.mark.parametrize("first", ["follow-up", "resume"])
def test_a_follow_up_continuation_and_a_task_id_resume_racing_continue_the_child_once(fresh_db, first):
    uid = coding_user()
    thread, parent, worker = _finished_worker(uid)

    def follow_up():
        return chat_store.continue_child_with_followups(worker, ["ALSO CHECK Y"], by_user=True)

    def resume():
        try:
            return child(uid, thread, parent, resume_from_id=worker, prompt="SECOND TASK")["id"]
        except SpawnCapError:
            return None

    # The first has re-queued the child (its row locked, uncommitted) when
    # the second reads it.
    one, two = (follow_up, resume) if first == "follow-up" else (resume, follow_up)
    assert _race(one, (chat_tx, "event"), two) in (False, None)

    sent = json.dumps(chat_store.get_run(uid, worker)["request"]["messages"])
    assert chat_store.get_run(uid, worker)["status"] == "queued"
    assert ("ALSO CHECK Y" in sent) is (first == "follow-up")
    assert ("SECOND TASK" in sent) is (first == "resume")
