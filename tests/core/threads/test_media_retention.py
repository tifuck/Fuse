"""Media retention and transcript pruning: a chat idle past the media window
loses its images and attached files but keeps its history, finished subagent
and replaced root runs lose their stored transcripts, and deleting a chat or
project (by hand or by the cleanup sweep) deletes its media."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import chat_history, chat_images, chat_store, chats, db, hosts, retention
from tests.core.agents._support import child, coding_user, parent_and_thread
from tests.core.features._support import make_host, make_project
from tests.core.helpers import make_user

from ._support import image_attachment, image_url, pk


@pytest.fixture(autouse=True)
def _cold_image_cache():
    chat_images._cache.clear()
    chat_images._cache_bytes = 0
    yield
    chat_images._cache.clear()
    chat_images._cache_bytes = 0


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _turn(uid, thread, seed=1):
    run = chat_store.start_turn(uid, thread["id"], "what is this?", chats.get_thread(uid, thread["id"])["revision"], None,
                                attachments=[image_attachment(seed)])["run"]
    chat_store.finish_run(run["id"], {"content": "a cat"}, "completed")
    return run


def _age(uid, thread, days):
    ts, tid = _iso(days), pk(uid, thread["id"])
    db.execute("UPDATE chat_threads SET created_at=?, updated_at=?, active_at=? WHERE id=?", (ts, ts, ts, tid))
    db.execute("UPDATE chat_runs SET updated_at=? WHERE thread_id=?", (ts, tid))


def _images(uid, thread):
    return db.fetchone("SELECT COUNT(*) AS n FROM chat_images WHERE thread_id=?", (pk(uid, thread["id"]),))["n"]


def _stored(uid, thread):
    """Everything the thread and its runs store, as one string."""
    tid = pk(uid, thread["id"])
    row = db.fetchone("SELECT messages, retained_history FROM chat_threads WHERE id=?", (tid,))
    runs = db.fetchall("SELECT request, provider_messages FROM chat_runs WHERE thread_id=?", (tid,))
    return json.dumps([dict(row), [dict(r) for r in runs]])


def _anchored(uid, thread) -> bool:
    row = db.fetchone("SELECT messages, retained_history FROM chat_threads WHERE id=?", (pk(uid, thread["id"]),))
    anchor = json.loads(row["retained_history"])["anchor"]
    display = chat_history._retained_display(json.loads(row["messages"]))
    return bool(anchor) and chat_history.anchors(display)[:len(anchor)] == anchor


def test_an_idle_chat_loses_its_media_and_keeps_its_history(fresh_db):
    uid = make_user()
    idle, recent = chats.create_thread(uid, model="offline"), chats.create_thread(uid, model="offline")
    _turn(uid, idle, seed=1)
    _turn(uid, recent, seed=2)
    db.execute("UPDATE chat_threads SET pinned=1 WHERE id=?", (pk(uid, idle["id"]),))
    _age(uid, idle, 4)
    _age(uid, recent, 1)
    assert _anchored(uid, idle)
    aged = db.fetchone("SELECT active_at, updated_at FROM chat_threads WHERE id=?", (pk(uid, idle["id"]),))
    chat_images.lookup(uid, [hashlib.sha256(image_url(1).encode()).hexdigest()])  # warm the cache
    assert chat_images._cache
    cursor = chat_store.cursor(uid)

    assert retention.purge_idle_media() == {"threads": 1, "images": 1}

    assert (_images(uid, idle), _images(uid, recent)) == (0, 1)
    stored = _stored(uid, idle)
    assert "data:image/" not in stored and chat_images.PREFIX not in stored
    assert chat_images.ATTACHMENT_REMOVED in stored and chat_images.MISSING in stored
    attachment = chats.get_thread(uid, idle["id"])["messages"][0]["attachments"][0]
    assert (attachment["kind"], attachment["name"]) == ("text", "shot1.png")
    assert not chat_images._cache
    # Replay still reuses the retained history rather than rebuilding it.
    assert _anchored(uid, idle)
    assert any(e["type"] == "thread.updated" for e in chat_store.events_after(uid, cursor))
    # Purging is not activity, and a purged chat is not purged again.
    row = db.fetchone("SELECT active_at, updated_at FROM chat_threads WHERE id=?", (pk(uid, idle["id"]),))
    assert dict(row) == dict(aged)
    assert retention.purge_idle_media() == {"threads": 0, "images": 0}


def test_a_chat_used_after_its_purge_is_purged_again_once_idle(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    _turn(uid, thread, seed=1)
    _age(uid, thread, 4)
    retention.purge_idle_media()
    _turn(uid, thread, seed=2)

    assert retention.purge_idle_media() == {"threads": 0, "images": 0}
    later = datetime.now(timezone.utc) + timedelta(days=4)
    assert retention.purge_idle_media(now=later) == {"threads": 1, "images": 1}
    assert "data:image/" not in _stored(uid, thread)


def test_a_busy_chat_keeps_its_media(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    chat_store.start_turn(uid, thread["id"], "look", thread["revision"], None, attachments=[image_attachment(1)])
    _age(uid, thread, 4)

    assert retention.purge_idle_media() == {"threads": 0, "images": 0}
    assert _images(uid, thread) == 1


def test_an_archived_chat_loses_its_media_however_recent(fresh_db, monkeypatch):
    uid = make_user()
    archived, open_chat = chats.create_thread(uid, model="offline"), chats.create_thread(uid, model="offline")
    _turn(uid, archived, seed=1)
    _turn(uid, open_chat, seed=2)
    chats.update_thread(uid, archived["id"], archived=True)
    for thread in (archived, open_chat):
        _age(uid, thread, 1)
    monkeypatch.setattr(retention.settings, "chat_media_idle_days", 0)

    assert retention.purge_idle_media() == {"threads": 1, "images": 1}
    assert (_images(uid, archived), _images(uid, open_chat)) == (0, 1)
    assert chat_images.ATTACHMENT_REMOVED in _stored(uid, archived)
    assert retention.purge_idle_media() == {"threads": 0, "images": 0}


def test_run_activity_after_a_purge_makes_an_archived_chat_due_again(fresh_db, monkeypatch):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    _turn(uid, thread)
    chats.update_thread(uid, thread["id"], archived=True)
    _age(uid, thread, 1)
    monkeypatch.setattr(retention.settings, "chat_media_idle_days", 0)
    assert retention.purge_idle_media() == {"threads": 1, "images": 1}
    assert retention.purge_idle_media() == {"threads": 0, "images": 0}

    # A run settling after the purge moves no timestamp of the thread itself.
    later = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.execute("UPDATE chat_runs SET updated_at=? WHERE thread_id=?", (later, pk(uid, thread["id"])))
    assert retention.purge_idle_media() == {"threads": 1, "images": 0}


def test_media_retention_off_keeps_everything(fresh_db, monkeypatch):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    _turn(uid, thread)
    _age(uid, thread, 40)
    monkeypatch.setattr(retention.settings, "chat_media_idle_days", 0)

    assert retention.purge_idle_media() == {"threads": 0, "images": 0}
    assert _images(uid, thread) == 1


def _set(run_id, *, status="completed", days=2.0, created=None):
    sets = f"{db.set_provider_sql()}, status=?, updated_at=?" + (", created_at=?" if created else "")
    args = ('[{"role":"user","content":"inspect"}]', status, _iso(days), *((_iso(created),) if created else ()))
    db.execute(f"UPDATE chat_runs SET {sets} WHERE id=?", (*args, run_id))


def _kept(run_id) -> bool:
    return bool(db.fetchone("SELECT provider_messages FROM chat_runs WHERE id=?", (run_id,))["provider_messages"])


def test_finished_subagent_and_replaced_root_transcripts_are_dropped(fresh_db):
    uid = coding_user()
    thread, first = parent_and_thread(uid)
    old_child, new_child = child(uid, thread, first), child(uid, thread, first)
    _set(first["id"], created=3)
    _set(old_child["id"])
    _set(new_child["id"], days=0.2)
    second = chat_store.start_turn(uid, thread["id"], "again", chats.get_thread(uid, thread["id"])["revision"], None)["run"]
    live_child = child(uid, thread, second)
    _set(second["id"], status="running", created=2.5)
    _set(live_child["id"])

    assert retention.prune_transcripts() == {"agent_runs": 1, "replaced_runs": 1}
    assert not _kept(first["id"]) and not _kept(old_child["id"])
    # The latest turn, a recent subagent and one its live root may continue stay.
    assert _kept(second["id"]) and _kept(new_child["id"]) and _kept(live_child["id"])
    assert retention.prune_transcripts() == {"agent_runs": 0, "replaced_runs": 0}


def test_replaced_transcripts_clear_across_batches(fresh_db, monkeypatch):
    monkeypatch.setattr(retention, "_CLEAR_BATCH", 1)
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    runs = [_turn(uid, thread, seed=n) for n in range(4)]
    for days, run in zip((5, 4, 3, 2), runs):
        _set(run["id"], days=days, created=days)

    assert retention.prune_transcripts()["replaced_runs"] == 3
    assert [_kept(run["id"]) for run in runs] == [False, False, False, True]


def test_runs_whose_thread_is_gone_are_deleted(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    run = _turn(uid, thread)
    db.execute("UPDATE chat_runs SET updated_at=? WHERE id=?", (_iso(40), run["id"]))
    db.execute("DELETE FROM chat_threads WHERE id=?", (pk(uid, thread["id"]),))

    assert retention.prune_history()["orphan_runs"] == 1
    assert db.fetchone("SELECT 1 FROM chat_runs WHERE id=?", (run["id"],)) is None


def test_orphan_runs_delete_in_batches_and_young_or_owned_runs_stay(fresh_db, monkeypatch):
    monkeypatch.setattr(retention, "_DELETE_BATCH", 1)
    uid = make_user()
    gone, kept = chats.create_thread(uid, model="offline"), chats.create_thread(uid, model="offline")
    old = [_turn(uid, gone, seed=n) for n in (1, 2)]
    young = _turn(uid, gone, seed=3)
    owned = _turn(uid, kept, seed=4)
    for run in (*old, owned):
        db.execute("UPDATE chat_runs SET updated_at=? WHERE id=?", (_iso(40), run["id"]))
    db.execute("DELETE FROM chat_threads WHERE id=?", (pk(uid, gone["id"]),))

    assert retention.prune_history()["orphan_runs"] == 2
    left = {row["id"] for row in db.fetchall("SELECT id FROM chat_runs WHERE user_id=?", (uid,))}
    assert left == {young["id"], owned["id"]}


def test_command_jobs_whose_account_or_chat_is_gone_are_deleted_in_batches(fresh_db, monkeypatch):
    monkeypatch.setattr(retention, "_DELETE_BATCH", 1)
    uid, gone_user = make_user(), make_user("gone@core.test")
    gone, kept = chats.create_thread(uid, model="offline"), chats.create_thread(uid, model="offline")
    for job_id, owner, thread, status in (("no-chat", uid, gone, "completed"), ("no-user", gone_user, None, "failed"),
                                          ("live", uid, gone, "running"), ("owned", uid, kept, "completed")):
        db.execute("INSERT INTO command_jobs (id, user_id, host_id, thread_id, command, timeout_ms, status, created_at)"
                   " VALUES (?,?,1,?,'true',1000,?,?)",
                   (job_id, owner, pk(uid, thread["id"]) if thread else None, status, db.now()))
    db.execute("DELETE FROM chat_threads WHERE id=?", (pk(uid, gone["id"]),))
    db.execute("DELETE FROM users WHERE id=?", (gone_user,))

    assert retention.prune_history()["orphan_jobs"] == 2
    assert {row["id"] for row in db.fetchall("SELECT id FROM command_jobs")} == {"live", "owned"}


def test_deleting_a_chat_or_project_deletes_its_media(fresh_db_fk):
    uid = make_user()
    chat, swept = chats.create_thread(uid, model="offline"), chats.create_thread(uid, model="offline")
    host = make_host(uid)
    project = make_project(uid, host)
    session = chats.create_thread(uid, model="offline", kind="project", host_id=host, project_id=project)
    for seed, thread in enumerate((chat, swept, session)):
        _turn(uid, thread, seed=seed)
    images = lambda: db.fetchone("SELECT COUNT(*) AS n FROM chat_images")["n"]  # noqa: E731
    assert images() == 3

    assert chats.delete_thread(uid, chat["id"]) is True
    assert images() == 2
    _age(uid, swept, 40)
    prefs = {"archive_hours": 0, "delete_days": 0, "limits": {"archive_days": 0, "delete_days": 0, "idle_days": 30}}
    assert retention.sweep_user(uid, prefs)["deleted_chats"] == 1
    assert images() == 1
    assert hosts.delete_project(uid, host, project) is True
    assert images() == 0
    assert db.fetchone("SELECT COUNT(*) AS n FROM chat_runs WHERE user_id=?", (uid,))["n"] == 0


def test_purging_an_archived_chat_keeps_its_forks_and_other_accounts_images(fresh_db_fk, monkeypatch):
    uid, stranger = make_user("ada@core.test"), make_user("bob@core.test")
    source = chats.create_thread(uid, model="offline")
    _turn(uid, source, seed=1)
    fork = chats.fork_thread(uid, source["id"])
    theirs = chats.create_thread(stranger, model="offline")
    _turn(stranger, theirs, seed=1)
    chats.update_thread(uid, source["id"], archived=True)
    monkeypatch.setattr(retention.settings, "chat_media_idle_days", 0)

    assert retention.purge_idle_media() == {"threads": 1, "images": 1}

    assert (_images(uid, source), _images(uid, fork), _images(stranger, theirs)) == (0, 1, 1)
    sha = hashlib.sha256(image_url(1).encode()).hexdigest()
    ref = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": chat_images.PREFIX + sha}}]}]
    for owner in (uid, stranger):
        assert chat_images.rehydrate(owner, ref)[0]["content"][0]["image_url"]["url"] == image_url(1)


def test_a_purge_cut_off_before_its_runs_were_stripped_runs_again(fresh_db, monkeypatch):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    run = _turn(uid, thread, seed=1)
    sha = hashlib.sha256(image_url(1).encode()).hexdigest()
    ref = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": chat_images.PREFIX + sha}}]}]
    db.execute(f"UPDATE chat_runs SET {db.set_provider_sql()} WHERE id=?", (json.dumps(ref), run["id"]))
    _age(uid, thread, 4)
    real_strip = retention._strip_runs

    def cut_off(user_id, thread_id):
        raise RuntimeError("worker stopped")

    monkeypatch.setattr(retention, "_strip_runs", cut_off)
    assert retention.purge_idle_media() == {"threads": 0, "images": 0}
    assert _images(uid, thread) == 0 and chat_images.PREFIX in _stored(uid, thread)
    monkeypatch.setattr(retention, "_strip_runs", real_strip)
    # The next sweep finishes the purge: no run keeps a reference to a deleted image.
    assert retention.purge_idle_media() == {"threads": 1, "images": 0}
    assert chat_images.PREFIX not in _stored(uid, thread)
    assert retention.purge_idle_media() == {"threads": 0, "images": 0}
