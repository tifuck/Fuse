"""Cheaper read paths that must answer exactly as before: thread writes that
publish after building their view, search snippets read from a transcript
head, the list's running flags, chunked event pruning with cached replay
floors, the batched title sweep and legacy history recovery."""
from unittest.mock import patch
from uuid import uuid4

from app import chat_events, chat_store, chat_threads, chat_titles, chat_tx, chats, db
from tests.core.helpers import make_user, quick_user

from ._support import events, pk
from .test_history import RESULT, _next_turn, _tool_rows, _tool_turn


def _publish(uid, kind="test.ping"):
    with chat_tx.transaction() as conn:
        return chat_tx.event(conn, uid, kind, None, {})


def test_a_thread_write_answers_with_the_cursor_of_its_own_event(fresh_db):
    uid = quick_user()
    created = chats.create_thread(uid, model="offline", title="First")
    assert created["event_cursor"] == chat_store.cursor(uid)

    saved = chats.update_thread(uid, created["id"], messages=[{"role": "user", "content": "hello"}],
                                expected_revision=created["revision"])
    forked = chats.fork_thread(uid, created["id"])

    last = events(uid)[-2:]
    assert [(e["type"], e["thread_id"]) for e in last] == [("thread.updated", saved["id"]), ("thread.updated", forked["id"])]
    assert saved["event_cursor"] == last[0]["id"] and saved["thread_cursor"] <= saved["event_cursor"]
    assert forked["event_cursor"] == last[1]["id"] == chat_store.cursor(uid)


def test_a_snippet_past_the_transcript_head_reads_the_whole_transcript(fresh_db):
    uid = quick_user()
    filler = [{"role": "user", "content": "filler words " * 3000}] * 3
    late = chats.create_thread(uid, model="offline", title="Late",
                               messages=[*filler, {"role": "user", "content": "the late needle is here"}])
    early = chats.create_thread(uid, model="offline", title="Early",
                                messages=[{"role": "user", "content": "an early needle first"}, *filler])
    stored = {row["id"]: row["messages"] for row in db.fetchall("SELECT id, messages FROM chat_threads WHERE user_id=?", (uid,))}
    assert all(len(raw) > chat_threads._SNIPPET_HEAD for raw in stored.values())

    hits = {t["title"]: t["snippet"] for t in chats.search_threads(uid, "needle")}

    assert hits == {"Late": chat_threads._snippet(stored[pk(uid, late["id"])], "needle"),
                    "Early": chat_threads._snippet(stored[pk(uid, early["id"])], "needle")}
    assert hits == {"Late": "the late needle is here", "Early": "an early needle first"}


def test_search_answers_both_lists_in_one_pass_within_a_byte_window(fresh_db):
    uid = quick_user()
    text = [{"role": "user", "content": "a needle in here " + "x" * 300}]
    chats.create_thread(uid, model="offline", title="Titled needle")
    chats.create_thread(uid, model="offline", title="Old", messages=text)
    chats.create_thread(uid, model="offline", title="New", messages=text)
    shelved = chats.create_thread(uid, model="offline", title="Shelved", messages=text)
    chats.update_thread(uid, shelved["id"], archived=True)

    def titles(lists):
        return [[t["title"] for t in hits] for hits in lists]

    assert titles(chat_threads.search_thread_lists(uid, "needle")) == [["New", "Old", "Titled needle"], ["Shelved"]]
    assert [t["title"] for t in chats.search_threads(uid, "needle", archived=True)] == ["Shelved"]
    # Past the byte window only titles match; the newest transcript is always in.
    with patch.object(chat_threads, "_SEARCH_MESSAGE_BYTES", 100):
        assert titles(chat_threads.search_thread_lists(uid, "needle")) == [["New", "Titled needle"], ["Shelved"]]


def test_the_list_marks_a_chat_with_a_live_background_job_running(fresh_db):
    uid = quick_user()
    busy = chats.create_thread(uid, model="offline", title="Job")
    chats.create_thread(uid, model="offline", title="Idle")
    db.execute(
        "INSERT INTO command_jobs(id,user_id,host_id,thread_id,command,background,timeout_ms,status,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (uuid4().hex[:12], uid, 0, pk(uid, busy["id"]), "make", 1, 1000, "running", db.now()),
    )

    assert {t["title"]: t["running"] for t in chats.list_threads(uid)} == {"Job": True, "Idle": False}
    assert {t["title"]: t["running"] for t in chats.list_threads(uid, archived=None)} == {"Job": True, "Idle": False}


def test_pruning_in_chunks_moves_the_floor_past_a_cached_one(fresh_db):
    uid = make_user()
    old = [_publish(uid) for _ in range(5)]
    db.execute(f"UPDATE chat_events SET created_at='2020-01-01T00:00:00Z' WHERE id IN ({','.join('?' * len(old))})", old)
    fresh = _publish(uid)
    # Caches the floor (0) and the committed cursor.
    assert chat_events.replay_valid(uid, old[0]) is True
    assert chat_store.committed_floor(uid) == 0

    with patch.object(chat_tx, "PRUNE_CHUNK", 2):
        chat_store.prune_events()

    assert [e["id"] for e in events(uid)] == [fresh]
    assert chat_events.replay_valid(uid, old[3]) is False
    assert chat_events.replay_valid(uid, old[4]) is True
    assert chat_events.replay_valid(uid, fresh) is True
    assert chat_events.replay_valid(uid, fresh + 1) is False


def test_a_floor_read_that_overlaps_a_prune_is_not_cached(fresh_db):
    uid = make_user()
    _publish(uid)
    epoch = chat_store.prune_epoch()
    real = chat_tx.transaction

    def overlapping(*args, **kwargs):
        # A socket read its floor before this prune started; it finishes after.
        chat_store.remember_floor(uid, 0, epoch)
        return real(*args, **kwargs)

    with patch.object(chat_tx, "transaction", new=overlapping):
        chat_store.prune_events()

    assert chat_store.committed_floor(uid) is None
    chat_store.remember_floor(uid, 0, epoch)
    assert chat_store.committed_floor(uid) is None


def test_the_title_sweep_marks_every_unqualified_chat_attempted(fresh_db):
    uid = make_user()
    ids = []
    for text in ("hi", "/compact now please"):
        thread = chats.create_thread(uid, model="offline")
        chat_store.start_turn(uid, thread["id"], text, thread["revision"], None)
        ids.append(pk(uid, thread["id"]))

    assert chat_titles.pending_title_threads() == []

    rows = db.fetchall(f"SELECT title_attempted_at FROM chat_threads WHERE id IN ({','.join('?' * len(ids))})", ids)
    assert len(rows) == 2 and all(row["title_attempted_at"] for row in rows)


def test_history_without_its_retained_cache_recovers_tool_rounds_from_the_runs(fresh_db):
    uid = make_user()
    thread = _tool_turn(uid)
    db.execute("UPDATE chat_threads SET retained_history='{}' WHERE id=?", (pk(uid, thread["id"]),))

    sent = _next_turn(uid, thread)

    assert [m["content"] for m in _tool_rows(sent)] == [RESULT]
