"""Spawn traces settle from one batched read: a finished child's message and
request.agent, and for children that left no report their transcripts."""
from app import chat_store, db

from ._support import child, coding_user, parent_and_thread


def test_children_without_a_report_settle_from_their_transcripts(fresh_db):
    uid = coding_user()
    thread, parent = parent_and_thread(uid)
    quiet = [child(uid, thread, parent, description=f"worker {n}") for n in range(2)]
    for n, worker in enumerate(quiet):
        chat_store.save_turn_progress(worker["id"], [{"role": "user", "content": "inspect"},
                                                     {"role": "assistant", "content": f"REPORT {n}"}])
        chat_store.finish_run(worker["id"], {"content": "", "elapsedMs": 1_000}, "completed")
    failed = child(uid, thread, parent, description="broken")
    chat_store.finish_run(failed["id"], {"content": "", "error": "boom"}, "failed")
    live = child(uid, thread, parent, description="busy")
    messages = [{"tools": [{"name": "spawn_subagent", "child_id": run["id"], "status": "running", "summary": "stale"}
                           for run in (*quiet, failed, live)]}]

    with db.tx() as conn:
        chat_store.settle_spawn_traces(conn, uid, messages)

    first, second, broken, busy = messages[0]["tools"]
    assert (first["status"], first["summary"], first["agent_work_ms"]) == ("done", "REPORT 0", 1_000)
    assert (second["status"], second["summary"]) == ("done", "REPORT 1")
    assert broken["status"] == "error" and broken["error"] == "boom" and "boom" in broken["summary"]
    assert busy["status"] == "running" and "summary" not in busy
