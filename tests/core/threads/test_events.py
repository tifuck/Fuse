"""Transactional event publication: commit order, replay from a cursor, no
event for rolled-back writes, after-commit observers, retention floors and the
group-commit publisher."""
import asyncio
import threading
from unittest.mock import patch

import pymysql
import pytest

from app import chat_events, chat_pub, chat_store, chat_tx, chats, db, mysql_compat
from tests.core.helpers import make_user, run_async, wait_until_async

from ._support import events, pk


def _publish(uid, kind, payload=None, thread_id=None):
    with chat_tx.transaction() as conn:
        return chat_tx.event(conn, uid, kind, thread_id, payload or {})


def test_events_replay_in_commit_order_from_any_cursor(fresh_db):
    uid = make_user()
    ids = [_publish(uid, "test.ping", {"n": n}) for n in range(5)]

    assert ids == sorted(ids)
    assert chat_store.cursor(uid) == ids[-1]
    assert chat_store.committed_cursor(uid) == ids[-1]
    assert [e["payload"]["n"] for e in events(uid)] == [0, 1, 2, 3, 4]
    assert [e["payload"]["n"] for e in events(uid, ids[2])] == [3, 4]
    assert events(uid, ids[-1]) == []
    assert [e["payload"]["n"] for e in chat_store.events_after(uid, 0, limit=2)] == [0, 1]


def test_a_rolled_back_write_publishes_nothing_and_runs_no_observer(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline", title="Before")
    cursor = chat_store.cursor(uid)
    observed = []

    with pytest.raises(RuntimeError):
        with chat_tx.transaction() as conn:
            conn.execute("UPDATE chat_threads SET title='Lost' WHERE id=?", (pk(uid, thread["id"]),))
            chat_tx.thread_event(conn, uid, pk(uid, thread["id"]))
            chat_tx.after_commit(lambda: observed.append("ran"))
            raise RuntimeError("abort")

    assert events(uid, cursor) == []
    assert chat_store.cursor(uid) == cursor
    assert chat_store.committed_cursor(uid) == cursor
    assert observed == []
    assert chats.get_thread(uid, thread["id"])["title"] == "Before"


def test_after_commit_observers_run_once_the_write_is_durable(fresh_db):
    uid = make_user()
    seen = []

    def observer():
        # Runs after COMMIT: a fresh connection already sees the event.
        seen.append(chat_store.cursor(uid))
        raise ValueError("observer failed")

    with pytest.raises(ValueError):
        with chat_tx.transaction() as conn:
            eid = chat_tx.event(conn, uid, "test.ping", None, {})
            chat_tx.after_commit(observer)

    assert seen == [eid]
    # A failing observer does not undo the commit.
    assert [e["id"] for e in events(uid)] == [eid]


def test_after_commit_needs_a_chat_transaction(fresh_db):
    with pytest.raises(RuntimeError):
        chat_tx.after_commit(lambda: None)


def test_each_account_has_its_own_stream_and_cursor(fresh_db):
    alice, bob = make_user("alice@core.test"), make_user("bob@core.test")
    _publish(alice, "test.ping", {"who": "alice"})
    bob_id = _publish(bob, "test.ping", {"who": "bob"})

    assert [e["payload"]["who"] for e in events(alice)] == ["alice"]
    assert [e["payload"]["who"] for e in events(bob)] == ["bob"]
    assert chat_store.cursor(bob) == bob_id
    # Another account's cursor is past this account's stream: not replayable.
    assert chat_events.replay_valid(alice, bob_id) is False


def test_pruned_history_moves_the_floor_and_forces_a_resync(fresh_db):
    uid = make_user()
    old = [_publish(uid, "test.ping", {"n": n}) for n in range(3)]
    db.execute("UPDATE chat_events SET created_at='2020-01-01T00:00:00Z' WHERE id IN (?,?)", (old[0], old[1]))
    fresh = _publish(uid, "test.ping", {"n": 3})

    chat_store.prune_events()

    assert [e["id"] for e in events(uid)] == [old[2], fresh]
    # A client behind the floor missed deleted events: it must resync.
    assert chat_events.replay_valid(uid, old[0]) is False
    assert chat_events.replay_valid(uid, old[1]) is True
    assert chat_events.replay_valid(uid, fresh) is True
    assert chat_events.replay_valid(uid, fresh + 1) is False


def test_an_unchanged_thread_short_circuits_and_a_change_does_not(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    mark = chat_store.thread_event_watermark(uid, pk(uid, thread["id"]))
    assert mark is not None

    assert chats.get_thread(uid, thread["id"], observed_thread_cursor=mark)["unchanged"] is True
    chats.update_thread(uid, thread["id"], title="Renamed")
    view = chats.get_thread(uid, thread["id"], observed_thread_cursor=mark)
    assert "unchanged" not in view and view["title"] == "Renamed"
    # The short-circuit still checks ownership.
    other = make_user("other@core.test")
    assert chats.get_thread(other, thread["id"], observed_thread_cursor=mark) is None


def test_direct_publish_drops_progress_for_a_finished_run(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    run = chat_store.start_turn(uid, thread["id"], "go", thread["revision"], None)["run"]
    numeric = pk(uid, thread["id"])
    chat_pub.publish(uid, "run.progress", numeric, {"n": 1}, guard_run=run["id"])
    chat_store.finish_run(run["id"], {"content": "done"}, "completed")
    cursor = chat_store.cursor(uid)

    chat_pub.publish(uid, "run.progress", numeric, {"n": 2}, guard_run=run["id"])

    assert events(uid, cursor) == []
    progress = [e["payload"]["n"] for e in events(uid) if e["type"] == "run.progress"]
    assert progress == [1]


def test_group_commit_keeps_fifo_order_and_isolates_a_bad_payload(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    run = chat_store.start_turn(uid, thread["id"], "go", thread["revision"], None)["run"]
    chat_store.finish_run(run["id"], {"content": "done"}, "completed")
    cursor = chat_store.cursor(uid)

    async def body():
        chat_pub.start_publisher()
        loop = asyncio.get_running_loop()
        futures = [loop.create_future() for _ in range(4)]
        chat_pub.publish(uid, "test.a", None, {"n": 0}, future=futures[0])
        chat_pub.publish(uid, "test.bad", None, {"n": float("inf")}, future=futures[1])
        chat_pub.publish(uid, "test.stale", None, {"n": 2}, guard_run=run["id"], future=futures[2])
        chat_pub.publish(uid, "test.b", None, {"n": 3}, future=futures[3])
        results = await asyncio.gather(*futures, return_exceptions=True)
        await chat_pub.stop_publisher()
        return results

    first, bad, stale, last = run_async(body())

    assert isinstance(bad, RuntimeError)
    assert stale is None  # its run finalized first: dropped, not failed
    assert first < last
    assert [e["type"] for e in events(uid, cursor)] == ["test.a", "test.b"]


def test_group_commit_guards_each_item_by_its_own_run(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    done = chat_store.start_turn(uid, thread["id"], "go", thread["revision"], None)["run"]
    chat_store.finish_run(done["id"], {"content": "done"}, "completed")
    live = chat_store.start_turn(uid, thread["id"], "again", None, None)["run"]
    cursor = chat_store.cursor(uid)

    async def body():
        chat_pub.start_publisher()
        loop = asyncio.get_running_loop()
        futures = [loop.create_future() for _ in range(4)]
        chat_pub.publish(uid, "test.live", None, {"n": 0}, guard_run=live["id"], future=futures[0])
        chat_pub.publish(uid, "test.stale", None, {"n": 1}, guard_run=done["id"], future=futures[1])
        chat_pub.publish(uid, "test.gone", None, {"n": 2}, guard_run="no-such-run", future=futures[2])
        chat_pub.publish(uid, "test.live", None, {"n": 3}, guard_run=live["id"], future=futures[3])
        results = await asyncio.gather(*futures)
        await chat_pub.stop_publisher()
        return results

    first, stale, gone, last = run_async(body())

    assert stale is None and gone is None
    assert first < last
    assert [e["payload"]["n"] for e in events(uid, cursor)] == [0, 3]


def test_a_batch_whose_commit_may_have_landed_is_not_published_again(fresh_db):
    """The connection drops during COMMIT after the server applied it: the
    publisher cannot know, and committing the items again would publish them
    twice under new ids."""
    uid = make_user()
    cursor = chat_store.cursor(uid)
    real = mysql_compat.Conn.commit
    lost = []

    def commit_then_lose_the_connection(conn):
        real(conn)
        if not lost:
            lost.append(True)
            raise pymysql.err.OperationalError(2013, "Lost connection to server during query")

    async def body():
        chat_pub.start_publisher()
        loop = asyncio.get_running_loop()
        futures = [loop.create_future() for _ in range(2)]
        with patch.object(mysql_compat.Conn, "commit", new=commit_then_lose_the_connection):
            chat_pub.publish(uid, "test.a", None, {"n": 0}, future=futures[0])
            chat_pub.publish(uid, "test.b", None, {"n": 1}, future=futures[1])
            results = await asyncio.gather(*futures, return_exceptions=True)
        await chat_pub.stop_publisher()
        return results

    results = run_async(body())

    assert lost and all(isinstance(result, RuntimeError) for result in results)
    assert [e["type"] for e in events(uid, cursor)] == ["test.a", "test.b"]


def test_sockets_of_one_user_share_an_event_read_until_the_next_wake():
    uid = 987654
    calls, gate = [], threading.Event()

    def read(user_id, cursor, limit):
        calls.append((user_id, cursor, limit))
        n = len(calls)
        gate.wait(5)
        return [{"id": cursor + 1, "n": n}]

    async def body():
        chat_events.start()
        chat_events._subscribers[uid][object()] = asyncio.Event()
        try:
            first = [asyncio.create_task(chat_events._events_after(uid, 7)) for _ in range(3)]
            await wait_until_async(lambda: len(calls) == 1)
            before = chat_events._wakes[uid]
            # A commit woke the user: a socket reading now must not join the
            # read that started before it.
            chat_events.notify([uid])
            await wait_until_async(lambda: chat_events._wakes[uid] != before)
            late = asyncio.create_task(chat_events._events_after(uid, 7))
            await wait_until_async(lambda: len(calls) == 2)
            gate.set()
            return await asyncio.gather(*first), await late
        finally:
            chat_events._subscribers.pop(uid, None)
            chat_events._wakes.pop(uid, None)

    with patch.object(chat_store, "events_after", side_effect=read):
        shared, late = run_async(body())

    assert calls == [(uid, 7, 64), (uid, 7, 64)]
    assert shared[0] is shared[1] is shared[2] and shared[0] == [{"id": 8, "n": 1}]
    assert late == [{"id": 8, "n": 2}]
    assert chat_events._reads == {}


def test_the_page_that_applied_a_turn_end_reconciles_unchanged_and_still_reads_the_badge(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    run = chat_store.start_turn(uid, thread["id"], "go", thread["revision"], None)["run"]
    cursor = chat_store.cursor(uid)

    chat_store.finish_run(run["id"], {"content": "done"}, "completed")

    published = events(uid, cursor)
    assert [e["type"] for e in published] == ["run.completed", "thread.updated"]
    # The page applies the run event and reconciles at its id: the thread row
    # after it moved nothing that read would return.
    end = published[0]["id"]
    assert chats.get_thread(uid, thread["id"], observed_cursor=end, observed_thread_cursor=end, mark_read=True)["unchanged"] is True
    assert chats.get_thread(uid, thread["id"])["unread"] is False
    # Reading the badge moves no watermark: another tab at the same event is
    # answered unchanged too.
    assert chats.get_thread(uid, thread["id"], observed_thread_cursor=end, mark_read=True)["unchanged"] is True


def test_a_steered_turn_end_is_read_again(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    run = chat_store.start_turn(uid, thread["id"], "go", thread["revision"], None)["run"]
    # Before any output: the part the steer froze is hidden and the finish drops it.
    chat_store.steer_turn(uid, thread["id"], "and this", "steer-1")
    cursor = chat_store.cursor(uid)

    chat_store.finish_run(run["id"], {"content": "stopped"}, "cancelled")

    published = events(uid, cursor)
    assert [e["type"] for e in published] == ["run.cancelled", "thread.updated"]
    # The run event does not carry what the finish inserted or dropped: a
    # reconcile at it reads the thread.
    end = published[0]["id"]
    assert "unchanged" not in chats.get_thread(uid, thread["id"], observed_cursor=end, observed_thread_cursor=end)


def test_opening_an_unread_chat_reads_it_before_the_snapshot(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    db.execute("UPDATE chat_threads SET unread=1 WHERE public_id=?", (thread["id"],))
    cursor = chat_store.cursor(uid)

    view = chats.get_thread(uid, thread["id"], mark_read=True)

    read = events(uid, cursor)
    assert view["unread"] is False and [(e["type"], e["payload"]["unread"]) for e in read] == [("thread.updated", False)]
    # The snapshot already covers the read's event: the page has nothing to fetch for it.
    assert view["event_cursor"] >= read[-1]["id"]
    assert chats.get_thread(uid, thread["id"], observed_thread_cursor=view["thread_cursor"])["unchanged"] is True


def test_label_writes_say_so_and_settings_writes_do_not(fresh_db):
    from app import chat_titles

    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    cursor = chat_store.cursor(uid)

    chats.update_thread(uid, thread["id"], title="Renamed")
    chats.update_thread(uid, thread["id"], pinned=True)
    chats.update_thread(uid, thread["id"], memory_off=True)
    chat_titles.apply_regenerated_title(uid, pk(uid, thread["id"]), "Regenerated")
    chats.update_thread(uid, thread["id"], model="other")
    chats.update_thread(uid, thread["id"], context="be brief")

    flags = [e["payload"].get("labels_only", False) for e in events(uid, cursor) if e["type"] == "thread.updated"]
    assert flags == [True, True, True, True, False, False]


def test_a_socket_sends_full_run_progress_only_for_the_thread_it_views(fresh_db, monkeypatch):
    from tests.core.auth._support import sign_in, web_client

    uid = make_user()
    rows = [
        {"id": 1, "type": "run.progress", "thread_id": "viewed", "payload": {"run_id": "r1", "events": [{"n": 1}]}},
        {"id": 2, "type": "run.progress", "thread_id": "other", "payload": {"run_id": "r2", "events": [{"n": 2}]}},
        {"id": 3, "type": "run.completed", "thread_id": "other", "payload": {"run_id": "r2"}},
    ]
    monkeypatch.setattr(chat_events, "replay_valid", lambda uid, cursor: True)
    monkeypatch.setattr(chat_store, "events_after", lambda uid, cursor, limit: [row for row in rows if row["id"] > cursor])

    def first_batch(query):
        with web_client() as web:
            sign_in(web, uid)
            with web.websocket_connect("wss://testserver/chat/events?" + query, headers={"origin": "https://testserver"}) as ws:
                return [(e["type"], e["payload"]) for e in ws.receive_json()["events"]]

    assert first_batch("cursor=0&thread=viewed") == [
        ("run.progress", rows[0]["payload"]), ("run.tick", {"run_id": "r2"}), ("run.completed", {"run_id": "r2"})]
    # A page that names no thread gets every patch.
    assert first_batch("cursor=0") == [(row["type"], row["payload"]) for row in rows]
