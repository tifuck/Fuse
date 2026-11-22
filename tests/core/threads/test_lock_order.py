"""Lock order between thread writes and run finalization.

Every finalizer locks its own run row, then the thread row, then the per-user
event cursor (a subagent finish used to publish before locking the thread).
A thread writer that locks the thread row and then a run, or a transaction
that takes the cursor and then the thread, closes a cycle with one of them,
and MariaDB kills a side with a deadlock (1213).

Each case pauses a real finish_run between two of its locks, lets the other
transaction run into it, and requires both to commit. Synchronised with
threading.Event and a processlist check of what the other connection is
waiting in; no sleeps.
"""
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app import chat_history, chat_store, chat_tx, chats, db, retention
from tests.core.features._support import make_host, make_project
from tests.core.helpers import make_user, wait_until

def _may_wait(info: str) -> bool:
    """A statement that can wait on a row lock: anything on the runs or the
    event cursor (every chat_runs read locks in a write transaction), thread
    writes and explicit locking reads. Plain thread reads never wait."""
    return ("chat_runs" in info or "chat_event_cursors" in info or "FOR UPDATE" in info
            or (info.startswith("UPDATE") and "chat_threads" in info))


def _other_waiting(match=_may_wait):
    """Another connection of this schema is inside a statement that is
    blocked by the paused transaction's locks."""
    with db.tx() as conn:
        rows = conn.native(
            "SELECT INFO FROM information_schema.PROCESSLIST WHERE DB=DATABASE() "
            "AND ID<>CONNECTION_ID() AND COMMAND='Query' AND INFO IS NOT NULL"
        ).fetchall()
    return any(match(row["INFO"]) for row in rows)


def _in_thread(fn, *args, name=None):
    out = {"done": threading.Event()}

    def body():
        try:
            out["result"] = fn(*args)
        except BaseException as exc:  # noqa: BLE001 - reported by the test
            out["error"] = exc
        finally:
            out["done"].set()

    worker = threading.Thread(target=body, name=name, daemon=True)
    worker.start()
    return worker, out


def _race(first, pause_at, second, *, after=False, waiting=_may_wait):
    """Run first() until it calls pause_at (module, name) (or returns from
    it, with after), holding the locks it took so far; start second() then,
    and let first continue once second waits on one of those locks (or
    finished without needing one). waiting narrows which of second's
    statements counts as waiting."""
    module, name = pause_at
    real = getattr(module, name)
    paused = threading.Event()
    state = {}

    def pause():
        paused.set()
        wait_until(lambda: state.get("second") and (state["second"]["done"].is_set() or _other_waiting(waiting)),
                   message="the second transaction neither finished nor waited on the first's locks")

    def pause_then_continue(*args, **kwargs):
        if threading.current_thread().name != "first" or paused.is_set():
            return real(*args, **kwargs)
        if after:
            result = real(*args, **kwargs)
            pause()
            return result
        pause()
        return real(*args, **kwargs)

    with patch.object(module, name, new=pause_then_continue):
        one, first_out = _in_thread(first, name="first")
        if not paused.wait(10):
            one.join(5)
            raise AssertionError(f"the first transaction never reached its pause point: {first_out!r}")
        two, state["second"] = _in_thread(second)
        one.join(15)
        two.join(15)
    assert not one.is_alive() and not two.is_alive()
    for out in (first_out, state["second"]):
        if "error" in out:
            raise out["error"]
    return state["second"]["result"]


def _finish(run_id):
    return lambda: chat_store.finish_run(run_id, {"content": "done"}, "completed")


def _active_thread(uid):
    thread = chats.create_thread(uid, model="offline")
    root = chat_store.start_turn(uid, thread["id"], "hello", thread["revision"], None)["run"]
    return thread, root


FINALIZERS = ["root", "child", "child-published"]


def _finalizing(kind, uid, thread, root):
    """(run to finish, pause point). root and child pause holding their run
    row, before they lock the thread row; child-published pauses after the
    subagent's agent.<status> event, where it once held the cursor without
    the thread row."""
    if kind == "root":
        return root["id"], (chat_tx, "lock_thread")
    child = chat_store.start_child_run(user_id=uid, thread_id=thread["id"], parent_run_id=root["id"],
                                       prompt="look around", description="helper")["id"]
    return child, (chat_tx, "lock_thread_id") if kind == "child" else (chat_tx, "thread_event")


@pytest.mark.parametrize("finalizer", FINALIZERS)
def test_marking_read_does_not_deadlock_with_finalization(fresh_db, finalizer):
    uid = make_user()
    thread, root = _active_thread(uid)
    db.execute("UPDATE chat_threads SET unread=1 WHERE public_id=?", (thread["id"],))
    run_id, pause_at = _finalizing(finalizer, uid, thread, root)

    assert _race(_finish(run_id), pause_at, lambda: chats.mark_thread_read(uid, thread["id"])) is True
    assert chat_store.get_run(uid, run_id)["status"] == "completed"


@pytest.mark.parametrize("finalizer", FINALIZERS)
def test_reordering_pins_does_not_deadlock_with_finalization(fresh_db, finalizer):
    uid = make_user()
    thread, root = _active_thread(uid)
    chats.update_thread(uid, thread["id"], pinned=True)
    run_id, pause_at = _finalizing(finalizer, uid, thread, root)

    result = _race(_finish(run_id), pause_at, lambda: chats.set_pin_order(uid, [thread["id"]]))
    assert [t["id"] for t in result["threads"]] == [thread["id"]]
    assert chat_store.get_run(uid, run_id)["status"] == "completed"


@pytest.mark.parametrize("finalizer", FINALIZERS)
def test_renaming_does_not_deadlock_with_finalization(fresh_db, finalizer):
    uid = make_user()
    thread, root = _active_thread(uid)
    run_id, pause_at = _finalizing(finalizer, uid, thread, root)

    renamed = _race(_finish(run_id), pause_at, lambda: chats.update_thread(uid, thread["id"], title="Renamed"))
    assert renamed["title"] == "Renamed"
    assert chat_store.get_run(uid, run_id)["status"] == "completed"
    assert chats.get_thread(uid, thread["id"])["title"] == "Renamed"


def test_retention_archive_does_not_deadlock_with_a_finishing_subagent(fresh_db):
    """The sweep never archives a chat with a live run; the busy check is
    bypassed here to pin the archive write's own lock order (thread, then
    cursor) against a subagent finishing on the same thread."""
    uid = make_user()
    thread, root = _active_thread(uid)
    run_id, pause_at = _finalizing("child", uid, thread, root)
    db.execute("UPDATE chat_threads SET active_at='2020-01-01T00:00:00Z', updated_at='2020-01-01T00:00:00Z', "
               "created_at='2020-01-01T00:00:00Z' WHERE public_id=?", (thread["id"],))
    db.execute("UPDATE chat_runs SET updated_at='2020-01-01T00:00:00Z'")
    real = retention._thread_activity
    prefs = {"archive_hours": 24, "archive_chats": True, "archive_projects": False, "delete_days": 0,
             "limits": {"archive_days": 0, "delete_days": 0}}
    now = datetime.now(timezone.utc) + timedelta(days=1)

    with patch.object(retention, "_thread_activity", new=lambda conn, user_id: (real(conn, user_id)[0], set())):
        stats = _race(_finish(run_id), pause_at, lambda: retention.sweep_user(uid, prefs, now=now))

    assert stats["archived_chats"] == 1
    assert chat_store.get_run(uid, run_id)["status"] == "completed"
    assert chats.get_thread(uid, thread["id"])["archived"] is True


@pytest.mark.parametrize("paused", ["sweep", "finish"])
def test_retention_project_archive_does_not_deadlock_with_a_finishing_session(fresh_db, paused):
    """A project sweep locks the project and its session rows
    (_project_unchanged) and then archives them, while the session's run
    finishes: paused either holding the sweep's locks or the run row. As
    above, the busy check is bypassed to pin the lock order itself."""
    uid = make_user()
    host = make_host(uid)
    project = make_project(uid, host)
    thread = chats.create_thread(uid, model="offline", kind="project", host_id=host, project_id=project)
    root = chat_store.start_turn(uid, thread["id"], "hello", thread["revision"], None)["run"]
    old = "2020-01-01T00:00:00Z"
    db.execute("UPDATE chat_threads SET active_at=?, updated_at=?, created_at=? WHERE public_id=?",
               (old, old, old, thread["id"]))
    db.execute("UPDATE chat_runs SET updated_at=?", (old,))
    db.execute("UPDATE user_projects SET updated_at=?, created_at=? WHERE id=?", (old, old, project))
    real = retention._thread_activity
    prefs = {"archive_hours": 24, "archive_chats": False, "archive_projects": True, "delete_days": 0,
             "limits": {"archive_days": 0, "delete_days": 0}}
    now = datetime.now(timezone.utc) + timedelta(days=1)

    def sweep():
        return retention.sweep_user(uid, prefs, now=now)

    with patch.object(retention, "_thread_activity", new=lambda conn, user_id: (real(conn, user_id)[0], set())):
        if paused == "sweep":
            _race(sweep, (retention, "_project_unchanged"), _finish(root["id"]), after=True)
        else:
            _race(_finish(root["id"]), (chat_tx, "lock_thread"), sweep)

    assert chat_store.get_run(uid, root["id"])["status"] == "completed"
    assert db.fetchone("SELECT archived FROM user_projects WHERE id=?", (project,))["archived"] == 1


def test_a_thread_event_after_a_concurrent_delete_publishes_nothing(fresh_db):
    """This transaction's snapshot still holds the thread when it publishes;
    the delete committed in between. The event reads the current row."""
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    numeric = chats.resolve_thread_pk(uid, thread["id"])
    cursor = chat_store.cursor(uid)

    with chat_tx.transaction() as conn:
        assert conn.execute("SELECT id FROM chat_threads WHERE id=?", (numeric,)).fetchone()
        deleter, out = _in_thread(chats.delete_thread, uid, thread["id"])
        deleter.join(10)
        assert out.get("result") is True
        assert chat_tx.thread_event(conn, uid, numeric) is None

    assert [e["type"] for e in chat_store.events_after(uid, cursor)] == ["thread.deleted"]


def test_a_session_event_does_not_lock_its_project(fresh_db):
    """Archiving a project holds the project row and waits for its sessions'
    rows; a finalizer publishing a session's thread.updated holds that row,
    so it must not wait for the project row in turn."""
    uid = make_user()
    host = make_host(uid)
    project = make_project(uid, host)
    thread = chats.create_thread(uid, model="offline", kind="project", host_id=host, project_id=project)
    numeric = chats.resolve_thread_pk(uid, thread["id"])
    cursor = chat_store.cursor(uid)

    with chat_tx.transaction() as conn:
        assert chat_tx.thread_event(conn, uid, numeric) is not None
        renamer, out = _in_thread(db.execute, "UPDATE user_projects SET name='renamed' WHERE id=?", (project,))
        wait_until(lambda: out["done"].is_set(), message="the project update waited on the thread event's locks")
    renamer.join(10)

    assert "error" not in out
    published = chat_store.events_after(uid, cursor)[-1]["payload"]
    assert (published["project_id"], published["project_name"], published["host_alias"]) == (project, "api", "box")


def test_root_and_subagent_finishing_together_both_commit(fresh_db):
    """The root holds its run and the thread row (paused after merging its
    reply) while a subagent of the same thread finishes into it."""
    uid = make_user()
    thread, root = _active_thread(uid)
    child_id, _ = _finalizing("child", uid, thread, root)

    _race(_finish(root["id"]), (chat_history, "finalize"), _finish(child_id))

    assert chat_store.get_run(uid, root["id"])["status"] == "completed"
    assert chat_store.get_run(uid, child_id)["status"] == "completed"
    assert chats.get_thread(uid, thread["id"])["messages"][-1]["content"] == "done"


def test_marking_read_while_the_chat_is_deleted_retries_instead_of_failing(fresh_db, caplog):
    """The delete holds the thread row when mark-read's UPDATE arrives. For a
    user's only chat MariaDB runs both single-row statements (WHERE id=? AND
    user_id=?) as a range on the user's index rather than the primary key, so
    the waiting UPDATE holds that index entry while the DELETE needs it, and
    the small mark-read is picked as the deadlock victim. Its retry runs
    after the delete and changes nothing instead of failing."""
    uid = make_user()
    thread, _ = _active_thread(uid)
    db.execute("UPDATE chat_threads SET unread=1 WHERE public_id=?", (thread["id"],))
    cursor = chat_store.cursor(uid)

    with caplog.at_level("WARNING", logger="app.chat_threads"):
        _race(lambda: chats.delete_thread(uid, thread["id"]), (chat_store, "event"),
              lambda: chats.mark_thread_read(uid, thread["id"]), after=True)

    assert "hit lock contention" in caplog.text
    assert chats.get_thread(uid, thread["id"]) is None
    assert [e["type"] for e in chat_store.events_after(uid, cursor)] == ["run.cancel_requested", "thread.deleted"]


def test_deleting_a_busy_chat_does_not_deadlock_with_a_pin_reorder(fresh_db):
    """Delete publishes its cancel events (cursor) while a pin reorder holds
    the thread row and waits for that cursor."""
    uid = make_user()
    thread, _ = _active_thread(uid)
    chats.update_thread(uid, thread["id"], pinned=True)
    cursor = chat_store.cursor(uid)

    _race(lambda: chats.delete_thread(uid, thread["id"]), (chat_store, "event"),
          lambda: chats.set_pin_order(uid, [thread["id"]]), after=True)

    assert chats.get_thread(uid, thread["id"]) is None
    assert [e["type"] for e in chat_store.events_after(uid, cursor)] == ["run.cancel_requested", "thread.deleted"]


def test_deleting_a_busy_chat_does_not_deadlock_with_a_rename(fresh_db):
    uid = make_user()
    thread, _ = _active_thread(uid)

    renamed = _race(lambda: chats.delete_thread(uid, thread["id"]), (chat_store, "event"),
                    lambda: chats.update_thread(uid, thread["id"], title="Renamed"), after=True)

    assert renamed is None
    assert chats.get_thread(uid, thread["id"]) is None


def test_deleting_a_busy_chat_does_not_deadlock_with_its_run_finishing(fresh_db):
    uid = make_user()
    thread, root = _active_thread(uid)

    _race(lambda: chats.delete_thread(uid, thread["id"]), (chat_store, "event"), _finish(root["id"]), after=True)

    assert chats.get_thread(uid, thread["id"]) is None
    assert chat_store.get_run(uid, root["id"]) is None


def test_reordering_several_pins_does_not_deadlock_with_a_finishing_run(fresh_db):
    """The root turn of the second pinned chat holds its run and thread row
    (paused after merging its reply) while the reorder asks for that second
    row. It used to have published the first chat's event by then (cursor)."""
    uid = make_user()
    first = chats.create_thread(uid, model="offline")
    second, root = _active_thread(uid)
    for thread in (first, second):
        chats.update_thread(uid, thread["id"], pinned=True)
    second_pk = chats.resolve_thread_pk(uid, second["id"])
    cursor = chat_store.cursor(uid)

    def wants_second_row(info):
        return ("`chat_threads`" in info and f"`id` = {second_pk} " in info
                and (info.startswith("UPDATE") or "FOR UPDATE" in info))

    result = _race(_finish(root["id"]), (chat_history, "finalize"),
                   lambda: chats.set_pin_order(uid, [first["id"], second["id"]]), waiting=wants_second_row)

    assert [t["id"] for t in result["threads"]] == [first["id"], second["id"]]
    pins = sorted((t["pin_order"], t["id"]) for t in chats.list_threads(uid) if t["pinned"])
    assert [tid for _, tid in pins] == [first["id"], second["id"]]
    assert chat_store.get_run(uid, root["id"])["status"] == "completed"
    published = [(e["type"], e["thread_id"]) for e in chat_store.events_after(uid, cursor)
                 if e["type"] == "thread.updated"]
    # The reorder's events keep list order, each after its own row's write.
    assert published[-2:] == [("thread.updated", first["id"]), ("thread.updated", second["id"])]
