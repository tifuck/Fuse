"""Fork, delete, archive and pin semantics, and per-account isolation of
threads, on real store functions and real runs (provider faked)."""
import pytest

from app import chat_store, chats, db
from app.chats import ChatValidationError
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


def test_retention_style_delete_refuses_while_a_run_is_live(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    current = chat_store.start_turn(uid, thread["id"], "busy", thread["revision"], None)["thread"]

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
