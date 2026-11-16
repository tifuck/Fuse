"""Background work finishing: a report that lands while the root runs is
delivered at the next tool boundary; one that lands after the turn ended
starts one automated wake turn; a stopped thread holds it for the user's next
message. Each report reaches the model exactly once."""
import asyncio
from unittest.mock import patch

from app import chat_runs, chat_store, chats
from tests.core.helpers import fake_provider, quick_user, run_async, run_turn, text_frames, tool_call_frames

from ._support import fake_tools, root_runs, settle, start_run, thread_pk, tool_thread


def _background_child(uid, thread_id, root_id, report):
    """A background subagent of root_id that finished with ``report``."""
    child = chat_store.start_child_run(user_id=uid, thread_id=thread_id, parent_run_id=root_id,
                                       prompt="dig", description="digger", background=True)
    chat_store.finish_run(child["id"], {"content": report}, "completed")
    return child


def _wake_sent(uid, child_id):
    return bool(chat_store.get_run(uid, child_id)["request"]["agent"].get("wake_sent"))


def _mentions(body, text):
    return sum(str(m.get("content")).count(text) for m in body["messages"])


def _idle_root_with_report(uid, report="REPORT-7781"):
    thread = chats.create_thread(uid, model="offline", tools=[])
    root = chat_store.start_turn(uid, thread["id"], "start the work", thread["revision"], None)["run"]
    child = _background_child(uid, thread["id"], root["id"], report)
    chat_store.finish_run(root["id"], {"content": "started it"}, "completed")
    return thread, root, child


def test_report_landing_mid_run_is_delivered_at_the_next_tool_boundary_once(fresh_db):
    uid = quick_user()

    async def body():
        tools.hold()
        thread, run, task = await start_run(uid, "go", thread=tool_thread(uid))
        await asyncio.wait_for(tools.started.wait(), 5)
        child = await asyncio.to_thread(_background_child, uid, thread["id"], run["id"], "REPORT-4410")
        tools.gate.set()
        await asyncio.wait_for(task, 5)
        await settle()
        return thread, run, child

    with fake_tools() as tools, fake_provider(
        tool_call_frames([("c1", "web_search", {"query": "x"})]), text_frames("used the report"),
    ) as provider:
        thread, run, child = run_async(body())

    assert len(provider.bodies) == 2
    assert _mentions(provider.bodies[1], "REPORT-4410") == 1
    assert _wake_sent(uid, child["id"])
    # Delivered in the turn: no automated wake turn follows.
    assert [r["id"] for r in root_runs(uid, thread["id"])] == [run["id"]]


def test_report_after_the_turn_ended_starts_one_wake_turn(fresh_db):
    uid = quick_user()
    thread, root, child = _idle_root_with_report(uid)

    async def body():
        chat_runs.schedule_wake(uid, thread_pk(uid, thread["id"]))
        await settle()
        # A second trigger (a sweep, another finish) finds nothing to deliver.
        chat_runs.schedule_wake(uid, thread_pk(uid, thread["id"]))
        await settle()

    with fake_provider(text_frames("Noted the report.")) as provider:
        run_async(body())

    roots = root_runs(uid, thread["id"])
    assert len(roots) == 2 and roots[1]["status"] == "completed"
    assert len(provider.bodies) == 1
    assert _mentions(provider.bodies[0], "REPORT-7781") == 1
    assert _wake_sent(uid, child["id"])
    messages = chats.get_thread(uid, thread["id"])["messages"]
    assert messages[-2]["role"] == "user" and messages[-2].get("auto") is True
    assert messages[-1]["content"] == "Noted the report."


def test_a_stopped_thread_holds_the_report_for_the_users_next_message(fresh_db):
    uid = quick_user()
    thread, root, child = _idle_root_with_report(uid, "REPORT-9902")
    chat_store.request_cancel(uid, root["id"])

    async def body():
        chat_runs.schedule_wake(uid, thread_pk(uid, thread["id"]))
        await settle()

    run_async(body())
    assert len(root_runs(uid, thread["id"])) == 1, "a Stop is final: no automated turn"
    assert not _wake_sent(uid, child["id"])

    with fake_provider(text_frames("ok")) as provider:
        run_async(run_turn(uid, "what did it find?", thread=chats.get_thread(uid, thread["id"])))
    assert _mentions(provider.bodies[0], "REPORT-9902") == 1
    assert _wake_sent(uid, child["id"])


def test_stop_during_the_final_commit_cannot_wake_the_thread(fresh_db):
    uid = quick_user()
    real_finish = chat_store.finish_run
    children = []

    def stop_at_commit(ident, message, status):
        row = chat_store.get_run(uid, ident)
        if row and not row.get("parent_run_id") and not children:
            # A background report lands and the user hits Stop while the
            # root's terminal row is being written.
            children.append(_background_child(uid, row["thread_id"], ident, "REPORT-5150"))
            chat_store.request_cancel(uid, ident)
        return real_finish(ident, message, status)

    async def body():
        thread_id, run = await run_turn(uid, "go")
        await settle()
        return thread_id, run

    with fake_provider(text_frames("Done")), patch.object(chat_store, "finish_run", side_effect=stop_at_commit):
        thread_id, run = run_async(body())

    assert run["status"] == "cancelled"
    assert [r["id"] for r in root_runs(uid, thread_id)] == [run["id"]]
    assert not _wake_sent(uid, children[0]["id"])
