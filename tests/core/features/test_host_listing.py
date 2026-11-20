"""The host bundle's project lists, read in two queries for every host, and a
project's sessions moved with it in one statement."""
import pytest

from app import chats, db, hosts
from tests.core.helpers import quick_user

from ._support import make_host, unlock_vault


@pytest.fixture
def uid(fresh_db):
    user = quick_user()
    unlock_vault(user)
    return user


def session(uid, host_id, project):
    return chats.create_thread(uid, model="offline", kind="project", host_id=host_id, project_id=project["id"])


def test_each_host_lists_only_its_own_projects_in_name_order_with_their_counts(uid):
    first, second = make_host(uid, "alpha"), make_host(uid, "beta")
    zed = hosts.add_project(uid, first, {"name": "zed", "remote_path": "/srv/zed"})
    hosts.add_project(uid, first, {"name": "api", "remote_path": "/srv/api"})
    web = hosts.add_project(uid, second, {"name": "web", "remote_path": "/srv/web"})
    session(uid, first, zed)
    session(uid, second, web)
    newest = session(uid, second, web)
    chats.create_thread(uid, model="offline")  # a plain chat counts for no project
    other = quick_user("other@core.test")
    theirs = make_host(other, "alpha")
    session(other, theirs, hosts.add_project(other, theirs, {"name": "web", "remote_path": "/srv/web"}))

    listed = {h["alias"]: h for h in hosts.list_bundle(uid)["hosts"]}

    assert [(p["name"], p["sessions"]) for p in listed["alpha"]["projects"]] == [("api", 0), ("zed", 1)]
    assert [(p["name"], p["sessions"]) for p in listed["beta"]["projects"]] == [("web", 2)]
    assert listed["beta"]["projects"][0]["last_active_at"] == chats.get_thread(uid, newest["id"])["updated_at"]
    for host_id, alias in ((first, "alpha"), (second, "beta")):
        assert hosts.get_host(uid, host_id) == listed[alias]


def test_moving_a_project_moves_every_session_and_announces_each(uid):
    first, second = make_host(uid, "alpha"), make_host(uid, "beta")
    project = hosts.add_project(uid, first, {"name": "api", "remote_path": "/srv/api"})
    threads = [session(uid, first, project) for _ in range(3)]
    loose = session(uid, first, hosts.add_project(uid, first, {"name": "other", "remote_path": "/srv/other"}))
    before = {t["id"]: chats.get_thread(uid, t["id"])["revision"] for t in threads}
    last_event = db.fetchone("SELECT COALESCE(MAX(id), 0) AS n FROM chat_events WHERE user_id=?", (uid,))["n"]

    hosts.patch_project(uid, first, project["id"], {"host_id": second})

    for t in threads:
        after = chats.get_thread(uid, t["id"])
        assert (after["host_id"], after["revision"]) == (second, before[t["id"]] + 1)
    assert chats.get_thread(uid, loose["id"])["host_id"] == first
    announced = {r["thread_id"] for r in db.fetchall(
        "SELECT thread_id FROM chat_events WHERE user_id=? AND id>? AND type='thread.updated'", (uid, last_event))}
    pks = {chats.resolve_thread_pk(uid, t["id"]) for t in threads}
    assert pks <= announced
