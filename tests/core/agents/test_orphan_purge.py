"""Saving a transcript deletes the subagent runs whose spawn was edited away,
and only those: a kept spawn keeps its child and the child's own children, a
report still owed to the thread is never deleted, and a spawn the message
could not record (past the trace cap) is not taken for an orphan."""
from app import chat_store, chats, db
from app.chat_schema import TOOL_TRACE_MAX

from ._support import child, coding_user, parent_and_thread


def _trace(run):
    return {"id": f"call_{run['id'][:8]}", "name": "spawn_subagent", "status": "done", "child_id": run["id"]}


def _finish(run, **message):
    chat_store.finish_run(run["id"], {"content": "report", **message}, "completed")


def _exists(run):
    return db.fetchone("SELECT 1 FROM chat_runs WHERE id=?", (run["id"],)) is not None


def _save(uid, thread_id, edit):
    view = chats.get_thread(uid, thread_id)
    messages = [dict(m) for m in view["messages"]]
    messages[-1] = edit(messages[-1])
    return chats.update_thread(uid, thread_id, messages=messages, expected_revision=view["revision"])


def _without_trace(run):
    def edit(message):
        tools = [t for t in message["tools"] if t.get("child_id") != run["id"]]
        edited = {**message, "tools": tools}
        if "segments" in message:
            ids = {t["id"] for t in tools}
            edited["segments"] = [s for s in message["segments"] if s.get("type") != "tool" or s.get("id") in ids]
        return edited
    return edit


def test_an_edit_deletes_only_the_child_whose_spawn_it_removed(fresh_db):
    uid = coding_user()
    thread, root = parent_and_thread(uid)
    kept = child(uid, thread, root, prompt="kept work")
    grandchild = child(uid, thread, kept, prompt="nested work", allow_nested_spawn=True)
    orphan = child(uid, thread, root, prompt="orphan work")
    owed = child(uid, thread, root, prompt="owed work", background=True)
    for run in (grandchild, kept, orphan, owed):
        _finish(run)
    _finish(root, tools=[_trace(kept), _trace(orphan), _trace(owed)])

    _save(uid, thread["id"], _without_trace(orphan))
    assert (_exists(kept), _exists(grandchild), _exists(orphan)) == (True, True, False)

    # A background report not yet delivered survives losing its spawn trace.
    _save(uid, thread["id"], _without_trace(owed))
    assert _exists(owed)


def test_a_spawn_past_the_trace_cap_is_not_an_orphan(fresh_db):
    uid = coding_user()
    thread, root = parent_and_thread(uid)
    late = child(uid, thread, root, prompt="spawned after the cap")
    _finish(late)
    # The reply filled its trace list before this spawn, so its trace was never kept.
    full = [{"id": f"call_{i}", "name": "web_search", "status": "done", "query": "q"} for i in range(TOOL_TRACE_MAX)]
    _finish(root, tools=full)
    assert len(chats.get_thread(uid, thread["id"])["messages"][-1]["tools"]) == TOOL_TRACE_MAX

    _save(uid, thread["id"], lambda message: {**message, "content": "report, revised"})
    assert _exists(late)
