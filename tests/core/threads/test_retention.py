"""Cleanup sweep: idle chats archive after the window, archived ones delete
after theirs, and pinned, scheduled or busy chats are never touched. Time is
the sweep's explicit ``now``; thread ages are set as stored timestamps."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app import chat_store, chats, db, hosts, retention
from tests.core.features._support import make_host, make_project
from tests.core.helpers import make_user, quick_user

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


def test_sessions_of_a_project_that_did_not_archive_still_archive_as_chats(fresh_db):
    uid = make_user()
    host = make_host(uid)
    project = make_project(uid, host)
    db.execute("UPDATE user_projects SET created_at=?, updated_at=? WHERE id=?", (_iso(5), _iso(5), project))
    session = chats.create_thread(uid, model="offline", title="session", kind="project", host_id=host, project_id=project)
    db.execute("UPDATE chat_threads SET created_at=?, updated_at=?, active_at=? WHERE id=?",
               (_iso(5), _iso(5), _iso(5), pk(uid, session["id"])))

    with patch.object(hosts, "set_project_archived", return_value=None):
        stats = retention.sweep_user(uid, PREFS, now=NOW)

    assert (stats["archived_projects"], stats["archived_chats"]) == (0, 1)
    assert _state(uid) == {"session": "archived"}


def _project(uid, alias, *, idle_days, archived_days=None):
    """A project with one session, both last used idle_days ago (and both
    archived archived_days ago, as a project archive leaves them)."""
    host = make_host(uid, alias)
    project = make_project(uid, host, name=alias)
    sets = {"created_at": _iso(idle_days), "updated_at": _iso(idle_days)}
    if archived_days is not None:
        sets.update(archived=1, archived_at=_iso(archived_days))
    db.execute("UPDATE user_projects SET " + ",".join(f"{k}=?" for k in sets) + " WHERE id=?", (*sets.values(), project))
    columns = {"archived_by_project": 1} if archived_days is not None else {}
    session = _chat(uid, f"{alias} session", idle_days=idle_days, archived_days=archived_days, **columns)
    db.execute("UPDATE chat_threads SET kind='project', host_id=?, project_id=? WHERE id=?",
               (host, project, pk(uid, session["id"])))
    return project


def _project_state(uid):
    return {r["name"]: ("archived" if r["archived"] else "open")
            for r in db.fetchall("SELECT name, archived FROM user_projects WHERE user_id=?", (uid,))}


def test_an_idle_project_archives_with_its_sessions_and_one_used_at_the_cutoff_stays(fresh_db):
    uid = make_user()
    _project(uid, "idle", idle_days=5)
    _project(uid, "edge", idle_days=1)  # last use == the cutoff: not before it

    stats = retention.sweep_user(uid, PREFS, now=NOW)

    assert (stats["archived_projects"], stats["archived_chats"]) == (1, 0)
    assert _project_state(uid) == {"idle": "archived", "edge": "open"}
    assert _state(uid) == {"idle session": "archived", "edge session": "open"}
    assert db.fetchone("SELECT archived_by_project FROM chat_threads WHERE title='idle session'")["archived_by_project"] == 1


def test_an_old_archived_project_deletes_with_its_sessions_and_one_at_the_cutoff_stays(fresh_db):
    uid = make_user()
    _project(uid, "old", idle_days=90, archived_days=31)
    _project(uid, "edge", idle_days=90, archived_days=30)  # archived == the cutoff: not before it

    stats = retention.sweep_user(uid, PREFS, now=NOW)

    assert (stats["deleted_projects"], stats["deleted_chats"]) == (1, 0)
    assert _project_state(uid) == {"edge": "archived"}
    assert _state(uid) == {"edge session": "archived"}


def test_a_chat_with_a_live_background_command_is_never_touched(fresh_db):
    uid = make_user()
    thread = _chat(uid, "watching", idle_days=60, archived_days=60)
    db.execute(
        "INSERT INTO command_jobs (id, user_id, host_id, thread_id, command, timeout_ms, status, background, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        ("job-1", uid, 1, pk(uid, thread["id"]), "true", 1000, "running", 1, _iso(60)),
    )

    stats = retention.sweep_user(uid, PREFS, now=NOW)

    assert not any(stats.values())
    assert _state(uid) == {"watching": "archived"}
    assert db.fetchone("SELECT status FROM command_jobs WHERE id='job-1'")["status"] == "running"


def test_a_recent_run_keeps_an_otherwise_idle_chat_open(fresh_db):
    uid = make_user()
    thread = _chat(uid, "ran today", idle_days=5)
    chat_store.start_turn(uid, thread["id"], "hello", chats.get_thread(uid, thread["id"])["revision"], None)
    db.execute("UPDATE chat_threads SET active_at=?, updated_at=? WHERE id=?", (_iso(5), _iso(5), pk(uid, thread["id"])))
    db.execute("UPDATE chat_runs SET status='completed', updated_at=? WHERE thread_id=?", (_iso(0.5), pk(uid, thread["id"])))

    assert not any(retention.sweep_user(uid, PREFS, now=NOW).values())
    assert _state(uid) == {"ran today": "open"}


def test_chats_idle_past_the_mandatory_limit_delete_whatever_the_user_chose(fresh_db):
    uid = make_user()
    off = {**PREFS, "archive_hours": 0, "delete_days": 0, "limits": {"archive_days": 0, "delete_days": 0, "idle_days": 30}}
    _chat(uid, "idle", idle_days=31)
    _chat(uid, "pinned", idle_days=31, pinned=1)
    _chat(uid, "archived", idle_days=40, archived_days=1)
    _chat(uid, "recent", idle_days=29)
    _chat(uid, "scheduled", idle_days=60, schedule_id=424242)

    stats = retention.sweep_user(uid, off, now=NOW)

    assert stats["deleted_chats"] == 3
    assert _state(uid) == {"recent": "open", "scheduled": "open"}


def test_the_sweep_touches_only_its_user_and_off_means_off(fresh_db):
    uid, other = make_user(), make_user("other@core.test")
    _chat(uid, "mine", idle_days=5)
    _chat(other, "theirs", idle_days=5)

    assert not any(retention.sweep_user(uid, {**PREFS, "archive_hours": 0, "delete_days": 0}, now=NOW).values())
    retention.sweep_user(uid, PREFS, now=NOW)

    assert _state(uid) == {"mine": "archived"}
    assert _state(other) == {"theirs": "open"}


def test_two_saves_of_different_cleanup_settings_keep_both(fresh_db):
    uid = quick_user()
    real_get = retention.get_settings
    raced = []

    def get_settings(user_id):
        current = real_get(user_id)
        if not raced:
            raced.append(True)
            # Another tab saves the delete window while this save is under way.
            retention.set_settings(user_id, {"delete_days": 9})
        return current

    with patch.object(retention, "get_settings", get_settings):
        retention.set_settings(uid, {"archive_hours": 5})
    prefs = real_get(uid)
    assert (prefs["archive_hours"], prefs["delete_days"]) == (5, 9)
