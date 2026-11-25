"""Server-held composer queue: a queued message sends once when the thread is
idle (no tab needed), retires once, and a tab's own send of the same item is
deduped by its command id."""
import asyncio

import pytest

from app import chat_queue, chat_runs, chat_store, chats
from app.chats import ChatValidationError
from tests.core.helpers import fake_provider, make_user, run_async, text_frames

from ._support import pk, runs_of, settle


def _busy_thread(uid):
    thread = chats.create_thread(uid, model="offline")
    run = chat_store.start_turn(uid, thread["id"], "first", thread["revision"], None)["run"]
    return thread, run


def _user_texts(uid, thread_id):
    return [m["content"] for m in chats.get_thread(uid, thread_id)["messages"] if m["role"] == "user"]


def test_a_queued_message_sends_once_when_the_turn_ends(fresh_db):
    uid = make_user()
    thread, run = _busy_thread(uid)
    numeric = pk(uid, thread["id"])
    item = {"id": "q-1", "text": "second", "created": 1}

    async def body():
        # Queued while the turn runs: held server-side, nothing starts.
        assert await chat_runs.queue_message(uid, numeric, item) == (False, False)
        await asyncio.to_thread(chat_store.finish_run, run["id"], {"content": "one"}, "completed")
        sent = await chat_runs.start_queued(uid, numeric)
        await settle()
        again = await chat_runs.start_queued(uid, numeric)
        return sent, again

    with fake_provider(text_frames("two")):
        sent, again = run_async(body())

    assert (sent, again) == ("q-1", None)
    assert _user_texts(uid, thread["id"]) == ["first", "second"]
    assert [r["status"] for r in runs_of(uid, thread["id"])] == ["completed", "completed"]
    assert chat_queue.next_item(uid, numeric) is None
    # The tab still had a copy and sends it too: the command ledger dedupes it.
    resent = chat_store.start_turn(uid, thread["id"], "second", None, chat_queue.command_id("q-1"))
    assert resent["idempotent"] is True
    # A late mirror write of the accepted item never queues it again.
    assert chat_queue.put(uid, numeric, item) is None
    assert chat_queue.next_item(uid, numeric) is None
    assert _user_texts(uid, thread["id"]) == ["first", "second"]


def test_a_permanent_conflict_holds_the_item_instead_of_blocking_the_queue(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    numeric = pk(uid, thread["id"])
    chat_queue.put(uid, numeric, {"id": "q-1", "text": "second", "created": 1})
    chat_queue.put(uid, numeric, {"id": "q-2", "text": "third", "created": 2})
    # A tab then sent the item edited: its command id names a different
    # request, and every retry of the queued copy fails the same way.
    used = chat_store.start_turn(uid, thread["id"], "second, edited", None, chat_queue.command_id("q-1"))["run"]
    chat_store.finish_run(used["id"], {"content": "done"}, "completed")

    with pytest.raises(ChatValidationError, match="command_id already used"):
        run_async(chat_runs.start_queued(uid, numeric))
    assert chat_queue.next_item(uid, numeric)["id"] == "q-2"


def test_send_now_goes_first_and_its_priority_is_never_lowered(fresh_db):
    uid = make_user()
    thread, _ = _busy_thread(uid)
    numeric = pk(uid, thread["id"])
    chat_queue.put(uid, numeric, {"id": "a", "text": "older", "created": 1})
    chat_queue.put(uid, numeric, {"id": "b", "text": "newer", "created": 2})
    assert chat_queue.next_item(uid, numeric)["id"] == "a"

    chat_queue.put(uid, numeric, {"id": "b", "text": "newer", "created": 2, "priority": 5})
    # A slower, earlier mirror write without the priority arrives afterwards.
    chat_queue.put(uid, numeric, {"id": "b", "text": "newer", "created": 2, "priority": 0})
    assert chat_queue.next_item(uid, numeric)["id"] == "b"


def test_stop_holds_only_what_was_queued_before_it_until_send_now(fresh_db):
    uid = make_user()
    thread, _ = _busy_thread(uid)
    numeric = pk(uid, thread["id"])
    chat_queue.put(uid, numeric, {"id": "before", "text": "queued before stop", "created": 10})
    chat_queue.put(uid, numeric, {"id": "after", "text": "typed while stopping", "created": 20})

    chat_queue.hold(uid, numeric, before=15)
    assert chat_queue.next_item(uid, numeric)["id"] == "after"
    chat_queue.delete(uid, numeric, "after")
    assert chat_queue.next_item(uid, numeric) is None
    # An ordinary mirror write keeps the hold; Send now releases it.
    assert chat_queue.put(uid, numeric, {"id": "before", "text": "queued before stop", "created": 10}) is True
    assert chat_queue.put(uid, numeric, {"id": "before", "text": "queued before stop", "created": 10}, release=True) is False
    assert chat_queue.next_item(uid, numeric)["id"] == "before"
    assert chat_queue.pending_threads() == [{"user_id": uid, "thread_id": numeric}]


def test_the_queue_is_bounded_and_per_account(fresh_db):
    uid, other = make_user(), make_user("other@core.test")
    thread, _ = _busy_thread(uid)
    numeric = pk(uid, thread["id"])
    for n in range(chat_queue.PER_THREAD):
        chat_queue.put(uid, numeric, {"id": f"i{n}", "text": "x", "created": n})

    with pytest.raises(ChatValidationError) as full:
        chat_queue.put(uid, numeric, {"id": "one-more", "text": "x", "created": 999})
    assert full.value.status_code == 413
    # Another account names this thread's id: it reads, writes and deletes nothing.
    assert chat_queue.next_item(other, numeric) is None
    chat_queue.delete(other, numeric, "i0")
    with pytest.raises(ChatValidationError) as foreign:
        chat_queue.put(other, numeric, {"id": "i0", "text": "hijacked", "created": 0, "priority": 9})
    assert foreign.value.status_code == 404
    item = chat_queue.next_item(uid, numeric)
    assert (item["id"], item["text"]) == ("i0", "x")
