"""Fork, delete, archive and pin semantics, and per-account isolation of
threads, on real store functions and real runs (provider faked)."""
import pytest

from app import chat_recaps, chat_store, chats, command_jobs, db, hosts
from app.chats import ChatValidationError
from tests.core.features._support import make_host, make_project, make_schedule
from tests.core.helpers import fake_provider, make_user, quick_user, run_async, run_turn, text_frames

from ._support import events, pk, runs_of


def _finished_thread(uid, text="hello", reply="world"):
    with fake_provider(text_frames(reply)):
        thread_id, run = run_async(run_turn(uid, text))
    return chats.get_thread(uid, thread_id), run


def test_fork_copies_the_conversation_and_runs_under_new_ids(fresh_db):
    uid = make_user()
    source, run = _finished_thread(uid)

    fork = chats.fork_thread(uid, source["id"], expected_revision=source["revision"])

    assert fork["id"] != source["id"]
    assert fork["title"].endswith("(fork)")
    assert [(m["role"], m["content"]) for m in fork["messages"]] == [(m["role"], m["content"]) for m in source["messages"]]
    assert not {m["id"] for m in fork["messages"]} & {m["id"] for m in source["messages"]}
    copied = runs_of(uid, fork["id"])
    assert len(copied) == 1 and copied[0]["id"] != run["id"]
    assert copied[0]["message_id"] == fork["messages"][-1]["id"]
    # The source is untouched and both can be continued independently.
    assert chats.get_thread(uid, source["id"])["messages"] == source["messages"]
    assert [r["id"] for r in runs_of(uid, source["id"])] == [run["id"]]


def test_fork_waits_for_running_work(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    chat_store.start_turn(uid, thread["id"], "busy", thread["revision"], None)

    with pytest.raises(ChatValidationError) as refused:
        chats.fork_thread(uid, thread["id"])
    assert refused.value.status_code == 409
    assert len(chats.list_threads(uid)) == 1


def test_delete_cancels_live_runs_and_publishes_the_public_id(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    chat_store.start_turn(uid, thread["id"], "busy", thread["revision"], None)
    numeric = pk(uid, thread["id"])
    cursor = chat_store.cursor(uid)

    assert chats.delete_thread(uid, thread["id"]) is True

    assert chats.get_thread(uid, thread["id"]) is None
    assert db.fetchone("SELECT COUNT(*) AS n FROM chat_runs WHERE thread_id=?", (numeric,))["n"] == 0
    published = events(uid, cursor)
    assert [e["type"] for e in published] == ["run.cancel_requested", "thread.deleted"]
    # The row is gone, yet observers still learn which thread it was.
    assert published[-1]["thread_id"] == thread["id"]


def test_a_failed_cleanup_after_the_delete_neither_skips_the_rest_nor_fails_the_delete(fresh_db, monkeypatch):
    from app import chat_runs

    uid = quick_user()
    thread = chats.create_thread(uid, model="offline")
    chat_store.start_turn(uid, thread["id"], "busy", thread["revision"], None)
    numeric = pk(uid, thread["id"])

    def broken(_run_id):
        raise RuntimeError("worker table busy")

    stopped = []
    monkeypatch.setattr(chat_runs, "cancel", broken)
    monkeypatch.setattr(command_jobs, "cancel_thread", lambda uid, tid: stopped.append((uid, tid)))

    assert chats.delete_thread(uid, thread["id"]) is True
    assert chats.get_thread(uid, thread["id"]) is None
    assert stopped == [(uid, numeric)]


def test_deleting_a_chat_or_project_deletes_its_command_jobs_and_events(fresh_db):
    uid = make_user()
    host = make_host(uid)
    project = make_project(uid, host)
    chat, kept = _finished_thread(uid)[0], _finished_thread(uid)[0]
    session = chats.create_thread(uid, model="offline", kind="project", host_id=host, project_id=project)
    run = chat_store.start_turn(uid, session["id"], "hi", session["revision"], None)["run"]
    chat_store.finish_run(run["id"], {"content": "ok"}, "completed")
    command_jobs.ensure_schema()
    for n, thread in enumerate((chat, kept, session)):
        db.execute(
            "INSERT INTO command_jobs (id, user_id, host_id, thread_id, command, timeout_ms, status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (f"job{n}", uid, host, pk(uid, thread["id"]), "true", 1000, "completed", db.now()),
        )
    gone, stays = {pk(uid, chat["id"]), pk(uid, session["id"])}, pk(uid, kept["id"])
    stored = lambda: db.fetchall("SELECT thread_id, type FROM chat_events WHERE user_id=?", (uid,))  # noqa: E731
    assert gone | {stays} <= {row["thread_id"] for row in stored()}

    assert chats.delete_thread(uid, chat["id"]) is True
    assert hosts.delete_project(uid, host, project) is True

    assert [row["id"] for row in db.fetchall("SELECT id FROM command_jobs WHERE user_id=?", (uid,))] == ["job1"]
    assert db.fetchone("SELECT COUNT(*) AS n FROM chat_runs WHERE user_id=? AND thread_id<>?", (uid, stays))["n"] == 0
    # Only the notice every client drops the chat on outlives it.
    assert {row["type"] for row in stored() if row["thread_id"] in gone} == {"thread.deleted"}
    assert any(row["thread_id"] == stays and row["type"] != "thread.deleted" for row in stored())


def test_retention_style_delete_refuses_while_a_run_is_live(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    current = chat_store.start_turn(uid, thread["id"], "busy", thread["revision"], None, reply="thread")["thread"]

    assert chats.delete_thread(uid, thread["id"], expected_revision=current["revision"], cancel_active=False) is False
    assert chats.get_thread(uid, thread["id"]) is not None
    assert runs_of(uid, thread["id"])[0]["status"] == "queued"


def test_labels_do_not_move_a_chat_but_edits_do(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    db.execute("UPDATE chat_threads SET updated_at='2026-01-01T00:00:00Z', active_at='2026-01-01T00:00:00Z' WHERE id=?",
               (pk(uid, thread["id"]),))

    archived = chats.update_thread(uid, thread["id"], title="Renamed", archived=True, pinned=True)
    assert archived["updated_at"] == "2026-01-01T00:00:00Z"
    assert archived["archived"] and archived["archived_at"]
    assert [t["id"] for t in chats.list_threads(uid)] == []
    assert [t["id"] for t in chats.list_threads(uid, archived=True)] == [thread["id"]]

    # Saving an edited transcript is activity: the chat comes back from the archive.
    edited = chats.update_thread(uid, thread["id"], messages=[{"role": "user", "content": "new words"}])
    assert edited["updated_at"] > "2026-01-01T00:00:00Z"
    assert not edited["archived"] and edited["archived_at"] == ""


def test_a_new_turn_unarchives_the_chat(fresh_db):
    uid = make_user()
    thread = chats.update_thread(uid, chats.create_thread(uid, model="offline")["id"], archived=True)

    chat_store.start_turn(uid, thread["id"], "back again", thread["revision"], None)
    assert not chats.get_thread(uid, thread["id"])["archived"]


def test_a_new_pin_lands_on_top_and_a_saved_order_persists(fresh_db):
    uid = make_user()
    a, b, c = (chats.create_thread(uid, model="offline", title=t) for t in ("A", "B", "C"))
    for thread in (a, b, c):
        chats.update_thread(uid, thread["id"], pinned=True)

    def pinned_order():
        rows = [t for t in chats.list_threads(uid) if t["pinned"]]
        return [t["title"] for t in sorted(rows, key=lambda t: t["pin_order"])]

    assert pinned_order() == ["C", "B", "A"]
    chats.set_pin_order(uid, [a["id"], c["id"], b["id"]])
    assert pinned_order() == ["A", "C", "B"]
    # Unpin then pin again: it goes back on top.
    chats.update_thread(uid, b["id"], pinned=False)
    chats.update_thread(uid, b["id"], pinned=True)
    assert pinned_order() == ["B", "A", "C"]


def test_a_finished_turn_badges_the_chat_until_it_is_viewed(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    root = chat_store.start_turn(uid, thread["id"], "work", thread["revision"], None)["run"]
    child = chat_store.start_child_run(user_id=uid, thread_id=thread["id"], parent_run_id=root["id"],
                                       prompt="look", description="helper")
    chat_store.finish_run(child["id"], {"content": "found it"}, "completed")
    assert chats.get_thread(uid, thread["id"])["unread"] is False  # a subagent finishing is not news yet

    chat_store.finish_run(root["id"], {"content": "all done"}, "completed")
    assert chats.get_thread(uid, thread["id"])["unread"] is True
    revision = chats.get_thread(uid, thread["id"])["revision"]
    cursor = chat_store.cursor(uid)

    assert chats.mark_thread_read(uid, thread["id"]) is True
    assert chats.mark_thread_read(uid, thread["id"]) is False
    view = chats.get_thread(uid, thread["id"])
    assert view["unread"] is False and view["revision"] == revision
    assert [(e["type"], e["payload"]["unread"]) for e in events(uid, cursor)] == [("thread.updated", False)]


def test_an_unreadable_transcript_is_never_overwritten(fresh_db):
    from app.chat_schema import UNREADABLE_TRANSCRIPT

    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    run = chat_store.start_turn(uid, thread["id"], "hello", thread["revision"], None)["run"]
    numeric = pk(uid, thread["id"])
    db.execute("UPDATE chat_threads SET messages='{not json' WHERE id=?", (numeric,))

    # The run's reply stays on the run row instead of replacing the transcript.
    chat_store.finish_run(run["id"], {"content": "reply"}, "completed")
    assert chat_store.get_run(uid, run["id"])["status"] == "completed"
    revision = db.fetchone("SELECT revision FROM chat_threads WHERE id=?", (numeric,))["revision"]
    with pytest.raises(ChatValidationError) as refused:
        chats.update_thread(uid, thread["id"], messages=[{"role": "user", "content": "replacement"}],
                            expected_revision=revision)
    assert (refused.value.status_code, refused.value.detail) == (409, UNREADABLE_TRANSCRIPT)

    assert db.fetchone("SELECT messages FROM chat_threads WHERE id=?", (numeric,))["messages"] == "{not json"


def test_other_accounts_cannot_see_or_change_a_thread(fresh_db):
    owner, other = make_user("owner@core.test"), make_user("other@core.test")
    thread = chats.create_thread(owner, model="offline", title="Private")
    numeric = pk(owner, thread["id"])

    assert chats.get_thread(other, thread["id"]) is None
    assert chats.resolve_thread_pk(other, numeric) is None
    assert chats.resolve_thread_pk(other, str(numeric)) is None
    assert chats.update_thread(other, thread["id"], title="Mine now") is None
    assert chats.delete_thread(other, thread["id"]) is False
    assert chats.fork_thread(other, thread["id"]) is None
    assert chats.list_threads(other) == [] and chats.search_threads(other, "Private") == []
    with pytest.raises(ChatValidationError):
        chat_store.start_turn(other, thread["id"], "hi", None, None)
    assert chats.get_thread(owner, thread["id"])["title"] == "Private"
    assert events(other) == []


def test_other_accounts_cannot_pin_read_move_or_inspect_a_thread(fresh_db):
    owner, other = make_user("owner@core.test"), make_user("other@core.test")
    thread = chats.create_thread(owner, model="offline", title="Private")
    chats.update_thread(owner, thread["id"], pinned=True)
    run = chat_store.start_turn(owner, thread["id"], "secret", chats.get_thread(owner, thread["id"])["revision"], None)["run"]
    numeric = pk(owner, thread["id"])
    db.execute("UPDATE chat_threads SET unread=1 WHERE id=?", (numeric,))
    host = make_host(owner)
    project = make_project(owner, host)
    stored = "SELECT kind, host_id, project_id, pinned, pin_order, unread, revision FROM chat_threads WHERE id=?"
    before = dict(db.fetchone(stored, (numeric,)))
    cursor = chat_store.cursor(owner)

    assert chats.set_pin_order(other, [thread["id"]]) == {"threads": []}
    assert chats.mark_thread_read(other, thread["id"]) is False
    assert chats.move_thread_to_project(other, thread["id"], host, project) is None
    assert chats.promote_working_session(other, thread["id"], host_id=host, cwd="/srv/api", tool="write_file") is None
    assert chat_store.get_run_snapshot(other, run["id"]) is None
    assert chat_store.historical_command_jobs(other, numeric) == []
    with pytest.raises(ChatValidationError) as refused:
        chat_recaps.request_manual(other, numeric)
    assert refused.value.status_code == 404

    assert dict(db.fetchone(stored, (numeric,))) == before
    assert chat_store.get_run_snapshot(owner, run["id"])["id"] == run["id"]
    assert events(owner, cursor) == [] and events(other) == []


def test_a_schedule_deletes_only_its_own_finished_threads(fresh_db):
    uid = make_user()
    schedule = make_schedule(uid)["id"]
    user_chat = chats.create_thread(uid, model="offline")
    busy, done = chats.create_thread(uid, model="offline"), chats.create_thread(uid, model="offline")
    for thread in (busy, done):
        db.execute("UPDATE chat_threads SET schedule_id=? WHERE id=?", (schedule, pk(uid, thread["id"])))
    chat_store.start_turn(uid, busy["id"], "working", busy["revision"], None)
    run = chat_store.start_turn(uid, done["id"], "finished", done["revision"], None)["run"]
    chat_store.finish_run(run["id"], {"content": "done"}, "completed")
    cursor = chat_store.cursor(uid)

    assert chats.delete_schedule_owned_thread(uid, pk(uid, user_chat["id"])) is False
    assert chats.delete_schedule_owned_thread(uid, pk(uid, busy["id"])) is False
    assert chats.delete_schedule_owned_thread(uid, pk(uid, done["id"])) is True

    assert chats.get_thread(uid, done["id"]) is None and chat_store.get_run(uid, run["id"]) is None
    assert chats.get_thread(uid, user_chat["id"]) is not None and runs_of(uid, busy["id"])
    assert [(e["type"], e["thread_id"]) for e in events(uid, cursor)] == [("thread.deleted", done["id"])]


def test_search_ignores_case_and_matches_escaped_and_non_ascii_text(fresh_db):
    uid = quick_user()
    greeting = chats.create_thread(uid, model="offline", title="Greeting")
    chat_store.start_turn(uid, greeting["id"], "Привет мир", greeting["revision"], None)
    chats.create_thread(uid, model="offline", title="Docker setup")
    quoted = chats.create_thread(uid, model="offline", title="Quote")
    chat_store.start_turn(uid, quoted["id"], 'please say "hi" back', quoted["revision"], None)

    # Transcripts are stored with ensure_ascii: Cyrillic as \u escapes, quotes escaped.
    assert {t["title"]: t["snippet"] for t in chats.search_threads(uid, "мир")} == {"Greeting": "Привет мир"}
    assert [t["title"] for t in chats.search_threads(uid, "привет")] == ["Greeting"]
    assert [t["title"] for t in chats.search_threads(uid, "docker")] == ["Docker setup"]
    assert {t["title"]: t["snippet"] for t in chats.search_threads(uid, 'SAY "HI"')} == {"Quote": 'please say "hi" back'}


def test_list_and_search_report_running_and_escape_wildcards(fresh_db):
    uid = make_user()
    busy = chats.create_thread(uid, model="offline", title="100% done")
    chats.create_thread(uid, model="offline", title="Idle chat")
    chat_store.start_turn(uid, busy["id"], "work", busy["revision"], None)

    running = {t["title"]: t["running"] for t in chats.list_threads(uid)}
    assert running == {"100% done": True, "Idle chat": False}
    assert [t["title"] for t in chats.search_threads(uid, "%")] == ["100% done"]
    hits = chats.search_threads(uid, "work")
    assert [t["title"] for t in hits] == ["100% done"] and "work" in hits[0]["snippet"]


def test_search_snippet_keeps_leading_context_without_a_space(fresh_db):
    uid = quick_user()
    thread = chats.create_thread(uid, model="offline", title="Long word")
    text = "abcdefghij" * 10 + "needle"
    chat_store.start_turn(uid, thread["id"], text, thread["revision"], None)

    hits = chats.search_threads(uid, "needle")

    assert [t["title"] for t in hits] == ["Long word"]
    assert hits[0]["snippet"] == "…" + text[-48:]


def _spawned_tree(uid):
    """A finished turn whose reply spawned a child that spawned a grandchild."""
    thread = chats.create_thread(uid, model="offline")
    root = chat_store.start_turn(uid, thread["id"], "go", thread["revision"], None)["run"]
    spawn = dict(user_id=uid, thread_id=thread["id"], description="helper", allow_nested_spawn=True)
    kid = chat_store.start_child_run(parent_run_id=root["id"], prompt="look", **spawn)["id"]
    grandkid = chat_store.start_child_run(parent_run_id=kid, prompt="deeper", **spawn)["id"]
    for run_id in (grandkid, kid):
        chat_store.finish_run(run_id, {"content": "found"}, "completed")
    trace = {"id": "call-1", "name": "spawn_subagent", "status": "done", "child_id": kid, "description": "helper"}
    chat_store.finish_run(root["id"], {"content": "done", "tools": [trace]}, "completed")
    return chats.get_thread(uid, thread["id"]), {kid, grandkid}


def _save_reply_tools(uid, view, tools):
    messages = [dict(m) for m in view["messages"]]
    messages[-1] = {**messages[-1], "tools": tools}
    chats.update_thread(uid, view["id"], messages=messages)
    return {r["id"] for r in runs_of(uid, view["id"]) if r["parent_run_id"]}


def test_removing_a_spawn_trace_deletes_its_finished_child_and_grandchild(fresh_db):
    uid = quick_user()
    view, tree = _spawned_tree(uid)
    assert _save_reply_tools(uid, view, view["messages"][-1]["tools"]) == tree
    assert _save_reply_tools(uid, view, []) == set()


def test_a_saved_trace_without_its_child_id_keeps_the_child(fresh_db):
    uid = quick_user()
    view, tree = _spawned_tree(uid)
    # The page sends its settled view of the trace; the server's copy has the child.
    clipped = [{k: v for k, v in t.items() if k != "child_id"} for t in view["messages"][-1]["tools"]]
    assert _save_reply_tools(uid, view, clipped) == tree
