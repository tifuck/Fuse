"""Cleanup sweep: idle chats archive after the window, archived ones delete
after theirs, and pinned, scheduled or busy chats are never touched. Time is
the sweep's explicit ``now``; thread ages are set as stored timestamps."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app import chat_store, chats, db, retention
from tests.core.helpers import make_user

from ._support import events, pk

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
PREFS = {"archive_hours": 24, "archive_chats": True, "archive_projects": True, "delete_days": 30,
         "limits": {"archive_days": 0, "delete_days": 0}}


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chat(uid, title, *, idle_days, archived_days=None, **columns):
    thread = chats.create_thread(uid, model="offline", title=title)
    sets = {"created_at": _iso(idle_days), "updated_at": _iso(idle_days), "active_at": _iso(idle_days), **columns}
    if archived_days is not None:
        sets.update(archived=1, archived_at=_iso(archived_days))
    db.execute("UPDATE chat_threads SET " + ",".join(f"{k}=?" for k in sets) + " WHERE id=?",
               (*sets.values(), pk(uid, thread["id"])))
    return thread


def _state(uid):
    return {t["title"]: ("archived" if t["archived"] else "open") for t in chats.list_threads(uid, archived=None)}


def test_idle_chats_archive_and_old_archives_delete(fresh_db):
    uid = make_user()
    _chat(uid, "idle", idle_days=3)
    _chat(uid, "recent", idle_days=0.5)
    _chat(uid, "old archive", idle_days=90, archived_days=40)
    _chat(uid, "new archive", idle_days=90, archived_days=10)
    cursor = chat_store.cursor(uid)

    stats = retention.sweep_user(uid, PREFS, now=NOW)

    assert _state(uid) == {"idle": "archived", "recent": "open", "new archive": "archived"}
    assert (stats["archived_chats"], stats["deleted_chats"]) == (1, 1)
    assert sorted(e["type"] for e in events(uid, cursor)) == ["thread.deleted", "thread.updated"]


def test_pinned_scheduled_and_busy_chats_are_never_touched(fresh_db):
    uid = make_user()
    _chat(uid, "pinned", idle_days=60, pinned=1)
    _chat(uid, "scheduled", idle_days=60, schedule_id=424242)
    busy = _chat(uid, "running", idle_days=60)
    chat_store.start_turn(uid, busy["id"], "still going", chats.get_thread(uid, busy["id"])["revision"], None)
    db.execute("UPDATE chat_threads SET active_at=?, updated_at=? WHERE id=?", (_iso(60), _iso(60), pk(uid, busy["id"])))
    db.execute("UPDATE chat_runs SET updated_at=? WHERE thread_id=?", (_iso(60), pk(uid, busy["id"])))
    _chat(uid, "pinned archive", idle_days=90, archived_days=60, pinned=1)
    _chat(uid, "scheduled archive", idle_days=90, archived_days=60, schedule_id=424243)

    stats = retention.sweep_user(uid, PREFS, now=NOW)

    assert not any(stats.values())
    assert _state(uid) == {"pinned": "open", "scheduled": "open", "running": "open",
                           "pinned archive": "archived", "scheduled archive": "archived"}


def test_a_chat_is_not_deleted_in_the_pass_that_archived_it(fresh_db):
    uid = make_user()
    _chat(uid, "ancient", idle_days=400)

    retention.sweep_user(uid, PREFS, now=NOW)
    assert _state(uid) == {"ancient": "archived"}
    # It ages from its archive time (real now), not from its old activity.
    retention.sweep_user(uid, PREFS, now=datetime.now(timezone.utc) + timedelta(days=29))
    assert _state(uid) == {"ancient": "archived"}
    retention.sweep_user(uid, PREFS, now=datetime.now(timezone.utc) + timedelta(days=31))
    assert _state(uid) == {}


def test_a_chat_used_after_the_sweep_read_it_stays_open(fresh_db):
    uid = make_user()
    thread = _chat(uid, "idle", idle_days=5)
    real = retention._thread_activity

    def snapshot_then_use(conn, user_id):
        rows = real(conn, user_id)
        # The user renames the chat between the sweep's read and its write.
        chats.update_thread(uid, thread["id"], title="in use")
        return rows

    with patch.object(retention, "_thread_activity", new=snapshot_then_use):
        stats = retention.sweep_user(uid, PREFS, now=NOW)

    assert stats["archived_chats"] == 0
    assert _state(uid) == {"in use": "open"}


def test_the_sweep_touches_only_its_user_and_off_means_off(fresh_db):
    uid, other = make_user(), make_user("other@core.test")
    _chat(uid, "mine", idle_days=5)
    _chat(other, "theirs", idle_days=5)

    assert not any(retention.sweep_user(uid, {**PREFS, "archive_hours": 0, "delete_days": 0}, now=NOW).values())
    retention.sweep_user(uid, PREFS, now=NOW)

    assert _state(uid) == {"mine": "archived"}
    assert _state(other) == {"theirs": "open"}
