"""A child's report reaches its parent exactly once.

Live parent: inline (foreground spawn, join) or injected at the next tool
boundary (background). Idle parent: one automated wake turn. Stopped parent:
held, then delivered with the user's next message. A receipt is
acknowledged only once the envelope is durable in the parent's transcript.
"""
import asyncio
import json

from app import chat_runs, chat_store, chats
from tests.core.agents._support import (
    agent_of, child, coding_user, parent_and_thread, root_runs, routed_provider, start_root, tool_result,
)
from tests.core.helpers import run_async, run_turn, text_frames, tool_call_frames, wait_run, wait_until_async


def _spawn(prompt, description="Inspect", call_id="c1", **extra):
    return tool_call_frames([(call_id, "spawn_subagent", {"prompt": prompt, "description": description, **extra})])


def _all_text(body) -> str:
    return "\n".join(m["content"] for m in body["messages"] if isinstance(m.get("content"), str))


def test_foreground_report_comes_back_as_the_tool_result_and_is_acknowledged(fresh_db):
    uid = coding_user()
    with routed_provider(go=[_spawn("inspect it"), text_frames("parent done")],
                         **{"inspect it": [text_frames("CHILD REPORT")]}) as provider:
        _, run = run_async(run_turn(uid, "go"))

    assert run["status"] == "completed"
    child_id = run["message"]["tools"][0]["child_id"]
    second = provider.bodies["go"][1]
    result = tool_result(second, "c1")
    assert "CHILD REPORT" in result and 'state="completed"' in result
    assert _all_text(second).count(f'<task id="{child_id}"') == 1
    assert chat_store.get_run(uid, child_id)["status"] == "completed"
    # Delivered inline and durable in the parent transcript: no wake owed.
    assert agent_of(uid, child_id)["wake_sent"] is True
    assert chat_store.wake_candidates(uid, run["thread_id"]) == []
    assert len(root_runs(uid, run["thread_id"])) == 1


def test_background_report_is_injected_into_the_live_parent_once(fresh_db):
    uid = coding_user()
    gate = {}

    async def child_answer(_body):
        await gate["release"].wait()
        return text_frames("BG REPORT")

    async def parent_waits_for_report(_body):
        gate["release"].set()
        await wait_until_async(lambda: chat_store.take_child_results(gate["parent"]))
        return tool_call_frames([("c2", "list_subagents", {})])

    async def body():
        gate["release"] = asyncio.Event()
        thread, run, task = await start_root(uid, "go")
        gate["parent"] = run["id"]
        await wait_run(task, 10)
        # The finished root may schedule a wake; nothing may be left to deliver.
        await chat_runs._wake_parent(uid, run["thread_id"])
        return run

    with routed_provider(go=[_spawn("bg work", background=True), parent_waits_for_report, text_frames("done")],
                         **{"bg work": [child_answer]}) as provider:
        run = run_async(body())

    final = provider.bodies["go"][2]
    child_id = chat_store.get_run(uid, run["id"])["message"]["tools"][0]["child_id"]
    assert "BG REPORT" in _all_text(final)
    assert _all_text(final).count(f'<task id="{child_id}" state="completed"') == 1
    assert agent_of(uid, child_id)["wake_sent"] is True
    assert chat_store.take_child_results(run["id"]) == []
    assert len(root_runs(uid, run["thread_id"])) == 1


def test_background_report_wakes_an_idle_parent_with_one_automated_turn(fresh_db):
    uid = coding_user()
    gate = {}

    async def child_answer(_body):
        await gate["release"].wait()
        return text_frames("BG REPORT")

    async def body():
        gate["release"] = asyncio.Event()
        thread, run, task = await start_root(uid, "go")
        await wait_run(task, 10)
        assert chat_store.get_run(uid, run["id"])["status"] == "completed"
        gate["release"].set()
        pk = run["thread_id"]
        await wait_until_async(lambda: [r["status"] for r in root_runs(uid, pk)] == ["completed", "completed"])
        # Another wake attempt (sweep, second child) finds nothing owed.
        await chat_runs._wake_parent(uid, pk)
        return pk, run["id"]

    with routed_provider(go=[_spawn("bg work", background=True), text_frames("launched"), text_frames("relayed")],
                         **{"bg work": [child_answer]}) as provider:
        pk, first_id = run_async(body())

    wake = provider.bodies["go"][2]["messages"][-1]["content"]
    assert wake.startswith("[Fuse]") or "AUTOMATED EVENT" in wake
    assert wake.count("BG REPORT") == 1
    rows = root_runs(uid, pk)
    assert len(rows) == 2
    child_id = chat_store.list_thread_agents(uid, pk)[0]["id"]
    assert agent_of(uid, child_id)["wake_sent"] is True
    wake_run = next(r["id"] for r in rows if r["id"] != first_id)
    assert agent_of(uid, wake_run)["wake_ids"] == [child_id]


def test_report_held_by_a_user_stop_rides_with_the_next_user_turn_once(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    bg = child(uid, thread, parent, background=True)
    chat_store.finish_run(bg["id"], {"content": "LATE REPORT", "tools": []}, "completed")
    chat_store.finish_run(parent["id"], {"content": "", "tools": []}, "cancelled")
    pk = parent["thread_id"]

    run_async(chat_runs._wake_parent(uid, pk))
    assert len(root_runs(uid, pk)) == 1  # a user Stop is final: no automated turn
    assert chat_store.list_thread_agents(uid, pk)[0]["wake_state"] == "held"

    with routed_provider(Hello=[text_frames("caught up")]) as provider:
        _, run = run_async(run_turn(uid, "what happened?", thread=chats.get_thread(uid, thread["id"])))

    assert run["status"] == "completed"
    assert _all_text(provider.bodies["Hello"][0]).count("LATE REPORT") == 1
    assert agent_of(uid, bg["id"])["wake_sent"] is True
    assert chat_store.wake_candidates(uid, pk) == []


def test_queued_result_is_acknowledged_only_when_durable_in_the_parent_transcript(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    bg = child(uid, thread, parent, background=True)
    chat_store.finish_run(bg["id"], {"content": "report", "tools": []}, "completed")
    envelope = chat_store.render_task_output(child_id=bg["id"], state="completed", text="report", description="worker",
                                             generation=bg["request"]["agent"].get("delivery_generation", ""))
    assert chat_store.queue_child_result(parent["id"], envelope, child_id=bg["id"])
    # Peeking is not delivery: a crash here must still redeliver.
    assert chat_store.take_child_results(parent["id"]) == [envelope]
    assert chat_store.take_child_results(parent["id"]) == [envelope]
    assert [c["id"] for c in chat_store.wake_candidates(uid, parent["thread_id"])] == [bg["id"]]
    # A partial copy in the transcript is not a receipt either.
    chat_store.save_turn_progress(parent["id"], [{"role": "user", "content": envelope[:40]}])
    assert not agent_of(uid, bg["id"]).get("wake_sent")

    chat_store.save_turn_progress(parent["id"], [{"role": "user", "content": envelope}])
    assert agent_of(uid, bg["id"])["wake_sent"] is True
    assert chat_store.take_child_results(parent["id"]) == []
    assert chat_store.wake_candidates(uid, parent["thread_id"]) == []


def test_a_finished_parent_cannot_take_a_queued_result(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    bg = child(uid, thread, parent, background=True)
    chat_store.finish_run(parent["id"], {"content": "ok", "tools": []}, "completed")
    envelope = chat_store.render_task_output(child_id=bg["id"], state="completed", text="r")
    # False tells the finalizer to wake the thread instead.
    assert chat_store.queue_child_result(parent["id"], envelope, child_id=bg["id"]) is False


def test_an_old_generation_receipt_cannot_acknowledge_a_resumed_leg(fresh_db):
    from app import agents

    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    bg = child(uid, thread, parent, background=True)
    chat_store.save_turn_progress(bg["id"], [{"role": "user", "content": "inspect"}, {"role": "assistant", "content": "a"}])
    chat_store.finish_run(bg["id"], {"content": "same report"}, "completed")
    old = agents._envelope(chat_store.get_run(uid, bg["id"]))
    assert chat_store.queue_child_result(parent["id"], old, child_id=bg["id"])

    resumed = child(uid, thread, parent, prompt="again", background=True, resume_from_id=bg["id"])
    assert resumed["request"]["agent"]["delivery_generation"]
    # The parent delivers the stale envelope while the new leg is in flight.
    chat_store.save_turn_progress(parent["id"], [{"role": "user", "content": old}])
    assert not agent_of(uid, bg["id"]).get("wake_sent")
    chat_store.finish_run(bg["id"], {"content": "same report"}, "completed")
    # A superseded execution's envelope is refused; the new one is accepted
    # and only its delivery acknowledges the child.
    assert chat_store.queue_child_result(parent["id"], old, child_id=bg["id"]) is False
    fresh = agents._envelope(chat_store.get_run(uid, bg["id"]))
    assert chat_store.queue_child_result(parent["id"], fresh, child_id=bg["id"])
    chat_store.mark_wake_sent([bg["id"]], pending_guard_run=parent["id"])
    assert not agent_of(uid, bg["id"]).get("wake_sent")
    chat_store.save_turn_progress(parent["id"], [{"role": "user", "content": old}, {"role": "user", "content": fresh}])
    assert agent_of(uid, bg["id"])["wake_sent"] is True


def test_mark_wake_sent_never_acknowledges_a_child_running_a_new_leg(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    bg = child(uid, thread, parent, background=True)
    chat_store.save_turn_progress(bg["id"], [{"role": "user", "content": "inspect"}])
    chat_store.finish_run(bg["id"], {"content": "first report", "tools": []}, "completed")
    child(uid, thread, parent, prompt="fix it", resume_from_id=bg["id"])

    chat_store.mark_wake_sent([bg["id"]])
    chat_store.bump_wake_attempts([bg["id"]])
    agent = agent_of(uid, bg["id"])
    assert not agent.get("wake_sent")
    assert int(agent.get("wake_attempts") or 0) == 0


def test_join_delivers_inline_and_acknowledges_with_the_parent_transcript(fresh_db):
    from app import agents

    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    done = child(uid, thread, parent)
    chat_store.finish_run(done["id"], {"content": "Final report"}, "completed")
    args = json.dumps({"id": done["id"]})

    text, trace = run_async(agents.get_output(user_id=uid, arguments=args, thread_id=thread["id"], parent_run_id=parent["id"]))
    assert trace["status"] == "done" and "Final report" in text
    assert not agent_of(uid, done["id"]).get("wake_sent")
    # A second join neither acknowledges nor duplicates the pending receipt.
    run_async(agents.get_output(user_id=uid, arguments=args, thread_id=thread["id"], parent_run_id=parent["id"]))
    assert chat_store.take_child_results(parent["id"]) == [text]

    chat_store.save_turn_progress(parent["id"], [{"role": "tool", "tool_call_id": "join", "content": text}])
    assert agent_of(uid, done["id"])["wake_sent"] is True
    assert chat_store.take_child_results(parent["id"]) == []


def test_foreground_report_survives_the_parent_dying_before_it_persisted(fresh_db):
    from unittest.mock import patch

    from app import agents

    uid = coding_user()
    thread, parent = parent_and_thread(uid)

    async def finishes_at_once(_request, _user, row):
        chat_store.finish_run(row["id"], {"content": "Final report"}, "completed")
        return None

    with patch.object(chat_runs, "start", side_effect=finishes_at_once):
        text, trace = run_async(agents.spawn(user={"id": uid}, request=None, parent_run_id=parent["id"], thread_id=thread["id"],
                                             arguments='{"prompt":"inspect","description":"Inspect"}'))
    assert "Final report" in text
    chat_store.abort_active(parent["id"], "interrupted")
    candidates = chat_store.wake_candidates(uid, parent["thread_id"])
    assert [(c["id"], c["summary"]) for c in candidates] == [(trace["child_id"], "Final report")]


def test_hand_off_delivers_a_child_that_finished_as_its_wait_expired(fresh_db):
    from app import agents

    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    racer = child(uid, thread, parent, description="racer")
    assert not racer["request"]["agent"].get("background")
    chat_store.finish_run(racer["id"], {"content": "RACE REPORT", "tools": []}, "completed")

    run_async(agents._hand_off(uid, parent["id"], racer["id"], "racer"))
    assert agent_of(uid, racer["id"])["background"] is True
    assert any("RACE REPORT" in t for t in chat_store.take_child_results(parent["id"]))


def test_root_does_not_take_a_report_owed_to_a_live_nested_parent(fresh_db):
    uid = coding_user(nested=True)
    thread, root = parent_and_thread(uid)
    middle = child(uid, thread, root, description="parent", allow_nested_spawn=True, background=True)
    grand = child(uid, thread, middle, description="grandchild", allow_nested_spawn=True, background=True)
    chat_store.finish_run(grand["id"], {"content": "Nested report"}, "completed")

    assert chat_store.wake_candidates(uid, root["thread_id"]) == []
    chat_store.abort_active(middle["id"], "interrupted")
    assert grand["id"] in [c["id"] for c in chat_store.wake_candidates(uid, root["thread_id"])]


def test_terminal_reports_never_leak_a_failed_childs_partial_work(fresh_db):
    from app import agents

    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    cases = [
        ("completed", {}),
        ("cancelled", {"stopped": True}),
        ("interrupted", {"notice": "Generation interrupted by server shutdown."}),
        ("failed", {"error": "Provider unavailable after retries"}),
    ]
    for index, (status, extra) in enumerate(cases):
        row = child(uid, thread, parent, description=f"case {index}", background=True)
        chat_store.save_turn_progress(row["id"], [
            {"role": "assistant", "content": "PRIVATE_PARTIAL_TRANSCRIPT"},
            {"role": "assistant", "tool_calls": [{"id": "r", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "r", "content": "PRIVATE_TOOL_OUTPUT"},
        ])
        content = "FINAL_REPORT" if status == "completed" else "PRIVATE_PARTIAL"
        chat_store.finish_run(row["id"], {"content": content, "tools": [{"name": "read_file", "preview": "PRIVATE_TOOL_OUTPUT"}], **extra}, status)
        stored = chat_store.get_run(uid, row["id"])
        candidate = next(c for c in chat_store.wake_candidates(uid, parent["thread_id"]) if c["id"] == row["id"])
        for text in (agents._envelope(stored), candidate["summary"]):
            if status == "completed":
                assert "FINAL_REPORT" in text
            else:
                assert "PRIVATE_" not in text, (status, text)
                assert "No completed report" in text
                if extra.get("error") or extra.get("notice"):
                    assert (extra.get("error") or extra["notice"]) in text


def test_report_is_the_final_answer_not_the_preamble_between_tools(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    row = child(uid, thread, parent, background=True)
    chat_store.finish_run(row["id"], {
        "content": "Let me look.Now checking tests.FINAL: 2 bugs fixed.",
        "segments": [{"type": "content", "text": "Let me look."}, {"type": "tool", "id": "t1"},
                     {"type": "content", "text": "Now checking tests."}, {"type": "tool", "id": "t2"},
                     {"type": "content", "text": "FINAL: 2 bugs fixed."}],
        "tools": [{"id": "t1", "name": "read_file", "status": "done"}, {"id": "t2", "name": "grep", "status": "done"}],
    }, "completed")
    assert chat_store.child_writeup(chat_store.get_run(uid, row["id"])) == "FINAL: 2 bugs fixed."


def test_a_failed_wake_turn_acknowledges_only_reports_it_delivered(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    a = child(uid, thread, parent, prompt="a", description="a", background=True)
    b = child(uid, thread, parent, prompt="b", description="b", background=True)
    chat_store.finish_run(a["id"], {"content": "alpha"}, "completed")
    chat_store.finish_run(b["id"], {"content": "beta"}, "completed")
    chat_store.finish_run(parent["id"], {"content": "ok"}, "completed")
    env_a = chat_store.render_task_output(child_id=a["id"], state="completed", text="alpha", description="a",
                                          generation=a["request"]["agent"].get("delivery_generation", ""))
    rev = chats.get_thread(uid, thread["id"])["revision"]
    wake = chat_store.start_turn(uid, thread["id"], f"AUTOMATED EVENT\n\n{env_a}", rev, "wake-1", auto=True)["run"]
    chat_store.mark_run_wake(wake["id"], [a["id"], b["id"]], receipts={a["id"]: env_a})
    chat_store.finish_run(wake["id"], {"content": ""}, "cancelled")

    chat_store.settle_wake_children(wake["id"], [a["id"], b["id"]])
    assert agent_of(uid, a["id"])["wake_sent"] is True
    agent_b = agent_of(uid, b["id"])
    assert not agent_b.get("wake_sent") and agent_b["wake_attempts"] == 1


def test_a_wake_turn_that_fails_to_start_spends_an_attempt_unless_it_raced_a_run(fresh_db):
    from unittest.mock import patch

    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    bg = child(uid, thread, parent, background=True)
    chat_store.finish_run(bg["id"], {"content": "report"}, "completed")
    chat_store.finish_run(parent["id"], {"content": "ok"}, "completed")
    pk = parent["thread_id"]

    busy = chats.ChatValidationError("chat already has an active run", 409)
    with patch.object(chat_store, "start_turn", side_effect=busy):
        run_async(chat_runs._wake_parent(uid, pk))
    assert int(agent_of(uid, bg["id"]).get("wake_attempts") or 0) == 0
    with patch.object(chat_store, "start_turn", side_effect=RuntimeError("database gone")):
        run_async(chat_runs._wake_parent(uid, pk))
    assert agent_of(uid, bg["id"])["wake_attempts"] == 1


def test_a_report_stops_waking_after_two_failed_attempts(fresh_db):
    from unittest.mock import patch

    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    bg = child(uid, thread, parent, background=True)
    chat_store.finish_run(bg["id"], {"content": "report"}, "completed")
    chat_store.finish_run(parent["id"], {"content": "ok"}, "completed")
    pk = parent["thread_id"]
    assert chat_store.list_thread_agents(uid, pk)[0]["wake_state"] == "retrying"

    chat_store.bump_wake_attempts([bg["id"]])
    chat_store.bump_wake_attempts([bg["id"]])
    assert chat_store.list_thread_agents(uid, pk)[0]["wake_state"] == "dropped"
    with patch.object(chat_runs, "schedule_wake") as woken:
        chat_runs.sweep_wakes()
    woken.assert_not_called()
    run_async(chat_runs._wake_parent(uid, pk))
    assert len(root_runs(uid, pk)) == 1
