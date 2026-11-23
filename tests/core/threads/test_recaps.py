"""Recap scheduling: a recap is claimed once under a lease, waits until the
chat is idle and settled, lands only if the history it summarized is still
current, and new activity or a disabled preference retires it."""
from app import chat_recaps, chat_store, chats, db
from tests.core.helpers import make_user

from ._support import events, pk


def _finished_chat(uid, reply="Here is the finished work."):
    thread = chats.create_thread(uid, model="offline")
    run = chat_store.start_turn(uid, thread["id"], "Please do the work", thread["revision"], None)["run"]
    chat_store.finish_run(run["id"], {"content": reply}, "completed")
    return thread


def _recaps(uid, thread_id):
    return db.fetchall("SELECT id,state,manual,attempts,lease_token,text,quiet_since FROM chat_recaps WHERE thread_id=? ORDER BY epoch",
                       (pk(uid, thread_id),))


def _manual_job(uid, thread):
    chat_recaps.request_manual(uid, pk(uid, thread["id"]))
    return _recaps(uid, thread["id"])[-1]["id"]


def test_a_recap_is_claimed_once_and_published_when_ready(fresh_db):
    uid = make_user()
    thread = _finished_chat(uid)
    job_id = _manual_job(uid, thread)
    cursor = chat_store.cursor(uid)

    job = chat_recaps.claim(job_id)
    assert job is not None and "finished work" in job["source"]
    assert chat_recaps.claim(job_id) is None  # leased: a second worker gets nothing
    chat_recaps.finish({**job, "token": "someone-else"}, "Stolen recap")
    chat_recaps.finish(job, "You asked for the work; it is done.")

    row = _recaps(uid, thread["id"])[-1]
    assert (row["state"], row["text"], row["lease_token"]) == ("ready", "You asked for the work; it is done.", "")
    assert [e["type"] for e in events(uid, cursor)] == ["recap.updated"]
    with db.tx() as conn:
        view = chat_recaps.attach(conn, uid, pk(uid, thread["id"]), chats.get_thread(uid, thread["id"]))
    assert [r["text"] for r in view["recaps"]] == ["You asked for the work; it is done."]


def test_a_recap_of_changed_history_is_dropped(fresh_db):
    uid = make_user()
    thread = _finished_chat(uid)
    job = chat_recaps.claim(_manual_job(uid, thread))
    current = chats.get_thread(uid, thread["id"])
    # The user edits the transcript while the recap is being written.
    edited = [{**m, "content": "edited request"} if m["role"] == "user" else m for m in current["messages"]]
    chats.update_thread(uid, thread["id"], messages=edited, expected_revision=current["revision"])
    cursor = chat_store.cursor(uid)

    chat_recaps.finish(job, "A recap of the old words")

    assert all(r["state"] != "ready" for r in _recaps(uid, thread["id"]))
    assert events(uid, cursor) == []

    # A rewrite that did not go through the activity hook is caught too.
    job = chat_recaps.claim(_manual_job(uid, thread))
    db.execute("UPDATE chat_threads SET messages=? WHERE id=?",
               (chats.dump_messages(edited[:1]), pk(uid, thread["id"])))
    chat_recaps.finish(job, "A recap of rows that are gone")
    assert _recaps(uid, thread["id"])[-1]["state"] == "stale"
    assert events(uid, cursor) == []


def test_new_activity_retires_a_ready_recap(fresh_db):
    uid = make_user()
    thread = _finished_chat(uid)
    job = chat_recaps.claim(_manual_job(uid, thread))
    chat_recaps.finish(job, "Done so far.")

    current = chats.get_thread(uid, thread["id"])
    chat_store.start_turn(uid, thread["id"], "one more thing", current["revision"], None)

    states = [r["state"] for r in _recaps(uid, thread["id"])]
    assert "ready" not in states and states[-1] == "pending"


def test_an_automatic_recap_waits_for_settled_idle_work(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    run = chat_store.start_turn(uid, thread["id"], "Please do the work", thread["revision"], None)["run"]
    job_id = _recaps(uid, thread["id"])[-1]["id"]

    assert chat_recaps.claim(job_id) is None  # the turn is still running
    chat_store.finish_run(run["id"], {"content": "x" * (chat_recaps.MIN_CONTEXT_CHARS + 10)}, "completed")
    assert chat_recaps.claim(job_id) is None  # settled: the idle period starts now
    assert _recaps(uid, thread["id"])[-1]["quiet_since"] != ""
    assert chat_recaps.claim(job_id) is None  # still inside the idle period

    db.execute("UPDATE chat_recaps SET quiet_since='2020-01-01T00:00:00Z', next_at='2020-01-01T00:00:00Z' WHERE id=?", (job_id,))
    job = chat_recaps.claim(job_id)
    assert job is not None and _recaps(uid, thread["id"])[-1]["state"] == "generating"


def test_a_short_quick_exchange_is_not_recapped_automatically(fresh_db):
    uid = make_user()
    thread = _finished_chat(uid, reply="ok")
    job_id = _recaps(uid, thread["id"])[-1]["id"]
    db.execute("UPDATE chat_recaps SET quiet_since='2020-01-01T00:00:00Z', next_at='2020-01-01T00:00:00Z' WHERE id=?", (job_id,))

    assert chat_recaps.claim(job_id) is None
    assert _recaps(uid, thread["id"])[-1]["state"] == "skipped"


def test_turning_recaps_off_disables_pending_work_but_not_manual_requests(fresh_db):
    uid = make_user()
    thread = _finished_chat(uid)
    job_id = _recaps(uid, thread["id"])[-1]["id"]

    chats.set_user_context(uid, recaps=False)

    assert _recaps(uid, thread["id"])[-1]["state"] == "disabled"
    assert chat_recaps.claim(job_id) is None
    assert chat_recaps.claim(_manual_job(uid, thread)) is not None


def test_a_recap_a_little_over_its_word_target_is_kept_and_a_rambling_one_is_not():
    assert chats.parse_generated_recap(" ".join(["word"] * 52)) == " ".join(["word"] * 52)
    assert chats.parse_generated_recap(" ".join(["word"] * 61)) == ""
