"""Machines and projects: host validation and secrets, aliases unique per
account, row-locked edits, project fields, colors and logos, archive and
delete cascades (all or nothing), and a chat promoted into one project when
parallel writes race."""
import threading
from unittest.mock import patch

import pytest

from app import chat_store, chat_threads, chat_tx, chats, db, hosts, vault
from app.hosts import HostError
from tests.core.helpers import quick_user, wait_until
from tests.core.threads.test_lock_order import _finish, _race

from ._support import make_host, unlock_vault, waiting_on

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def ssh(uid, alias="box", **raw):
    body = {"alias": alias, "hostname": "box.example.com", "username": "dev", "auth_type": "key", "secret": "key-1"}
    body.update(raw)
    return hosts.add_host(uid, body)


@pytest.fixture
def uid(fresh_db):
    user = quick_user()
    unlock_vault(user)
    return user


def session(uid, host_id, project):
    return chats.create_thread(uid, model="offline", kind="project", host_id=host_id, project_id=project["id"])


# -- hosts -----------------------------------------------------------------

def test_saving_an_ssh_host_needs_the_unlocked_vault(fresh_db):
    user = quick_user()
    with pytest.raises(HostError) as refused:
        ssh(user)
    assert refused.value.status_code == 401
    assert hosts.list_bundle(user)["hosts"] == []


@pytest.mark.parametrize("raw", [
    {"alias": "localhost"}, {"alias": "-dash"}, {"alias": "a" * 41}, {"alias": "bad alias"},
    {"username": "bad user"}, {"port": 70000}, {"port": "x"}, {"auth_type": "agent"},
    {"secret": "  "}, {"hostname": "bad\x00host"},
])
def test_invalid_hosts_are_refused(uid, raw):
    with pytest.raises(HostError):
        ssh(uid, **raw)
    assert hosts.list_bundle(uid)["hosts"] == []


def test_secrets_are_wrapped_and_never_listed(uid):
    host = ssh(uid, secret="PRIVATE-KEY-TEXT", passphrase="pass-1")
    stored = db.fetchone("SELECT secret, passphrase FROM user_hosts WHERE id=?", (host["id"],))
    assert "PRIVATE-KEY-TEXT" not in stored["secret"] and "pass-1" not in stored["passphrase"]
    listed = str(hosts.list_bundle(uid)) + str(hosts.get_host(uid, host["id"]))
    assert "PRIVATE-KEY-TEXT" not in listed and stored["secret"] not in listed
    assert hosts.secret_plain(uid, host["id"])[:2] == ("PRIVATE-KEY-TEXT", "pass-1")
    vault.lock(uid)
    with pytest.raises(HostError) as locked:
        hosts.secret_plain(uid, host["id"])
    assert locked.value.status_code == 401


def test_a_new_key_or_auth_type_drops_a_passphrase_not_resent(uid):
    host = ssh(uid, passphrase="pass-1")
    hosts.patch_host(uid, host["id"], {"label": "renamed"})
    assert hosts.secret_plain(uid, host["id"])[:2] == ("key-1", "pass-1")
    hosts.patch_host(uid, host["id"], {"secret": "key-2", "passphrase": "pass-2"})
    assert hosts.secret_plain(uid, host["id"])[:2] == ("key-2", "pass-2")
    hosts.patch_host(uid, host["id"], {"secret": "key-3"})
    assert hosts.secret_plain(uid, host["id"])[:2] == ("key-3", "")
    hosts.patch_host(uid, host["id"], {"passphrase": "pass-3"})
    hosts.patch_host(uid, host["id"], {"auth_type": "password"})
    assert hosts.secret_plain(uid, host["id"])[:2] == ("key-3", "")


def test_aliases_are_unique_per_account(uid):
    first = ssh(uid, "box")
    second = ssh(uid, "Rig")
    assert second["alias"] == "rig"
    with pytest.raises(HostError) as taken:
        ssh(uid, "BOX")
    assert taken.value.status_code == 409
    with pytest.raises(HostError) as renamed:
        hosts.patch_host(uid, second["id"], {"alias": "box"})
    assert renamed.value.status_code == 409
    other = quick_user("other@core.test")
    unlock_vault(other)
    assert ssh(other, "box")["alias"] == "box"  # another account may use it
    assert hosts.get_host(other, first["id"]) is None
    with pytest.raises(HostError) as missing:
        hosts.patch_host(other, first["id"], {"label": "mine"})
    assert missing.value.status_code == 404
    assert hosts.delete_host(other, first["id"]) is False


def test_moving_the_endpoint_clears_the_pinned_key(uid):
    host = ssh(uid)
    pinned = hosts.set_pin(uid, host["id"], "ssh-ed25519", ":".join(["AB"] * 32))
    assert pinned["fingerprint"] == "ab" * 32
    assert hosts.patch_host(uid, host["id"], {"label": "x"})["fingerprint"] == "ab" * 32
    assert hosts.patch_host(uid, host["id"], {"port": 2222})["fingerprint"] == ""
    with pytest.raises(HostError):
        hosts.patch_host(uid, host["id"], {"shell": "zsh"})
    agent = make_host(uid, "pc")
    with pytest.raises(HostError):
        hosts.patch_host(uid, agent, {"auth_type": "key"})
    with pytest.raises(HostError):
        hosts.patch_host(uid, agent, {"secret": "k"})


def test_a_concurrent_edit_does_not_revert_a_saved_field(uid):
    host = ssh(uid)
    real_alias = hosts.normalize_alias
    reading, release = threading.Event(), threading.Event()

    def held_alias(value):
        # The alias edit has read (and locked) the row; a credential edit lands now.
        reading.set()
        release.wait(10)
        return real_alias(value)

    with patch.object(hosts, "normalize_alias", held_alias):
        first = threading.Thread(target=hosts.patch_host, args=(uid, host["id"], {"alias": "renamed"}))
        first.start()
        assert reading.wait(10)
        second = threading.Thread(target=hosts.patch_host, args=(uid, host["id"], {"username": "rotated"}))
        second.start()
        wait_until(lambda: waiting_on("user_hosts") or not second.is_alive(), timeout=10)
        release.set()
        first.join(10)
        second.join(10)
    row = db.fetchone("SELECT alias, username FROM user_hosts WHERE id=?", (host["id"],))
    assert (row["alias"], row["username"]) == ("renamed", "rotated")


def test_removing_a_machine_leaves_its_sessions_as_plain_chats(uid):
    host = make_host(uid)
    project = hosts.add_project(uid, host, {"name": "api", "remote_path": "/srv/api"})
    thread = session(uid, host, project)
    assert hosts.delete_host(uid, host) is True
    after = chats.get_thread(uid, thread["id"])
    assert (after["kind"], after["host_id"], after["project_id"]) == ("chat", None, None)


# -- projects --------------------------------------------------------------

def test_listed_projects_carry_their_session_count_and_last_activity(uid):
    host = make_host(uid)
    busy = hosts.add_project(uid, host, {"name": "busy", "remote_path": "/srv/busy"})
    hosts.add_project(uid, host, {"name": "idle", "remote_path": "/srv/idle"})
    session(uid, host, busy)
    newest = session(uid, host, busy)
    listed = {p["name"]: p for p in hosts.list_bundle(uid)["hosts"][0]["projects"]}
    assert listed["busy"]["sessions"] == 2
    assert listed["busy"]["last_active_at"] == chats.get_thread(uid, newest["id"])["updated_at"]
    assert (listed["idle"]["sessions"], listed["idle"]["last_active_at"]) == (0, "")


def test_project_fields_validate_and_belong_to_the_host_owner(uid):
    host = make_host(uid)
    made = hosts.add_project(uid, host, {"name": "api", "remote_path": "/srv/api", "permission_mode": "read_only",
                                         "default_model": "m1", "default_permissions": "auto"})
    assert (made["permission_mode"], made["default_model"], made["default_permissions"]) == ("read_only", "m1", "auto")
    with pytest.raises(HostError) as dup:
        hosts.add_project(uid, host, {"name": "api", "remote_path": "/srv/other"})
    assert dup.value.status_code == 409
    for bad in ({"name": "", "remote_path": "/x"}, {"name": "x", "remote_path": "/x", "permission_mode": "root"},
                {"name": "x", "remote_path": "/x", "default_permissions": "yolo"}, {"name": "x", "remote_path": "/x", "color": "red"}):
        with pytest.raises(HostError):
            hosts.add_project(uid, host, bad)
    other = quick_user("other@core.test")
    with pytest.raises(HostError) as foreign:
        hosts.add_project(other, host, {"name": "x", "remote_path": "/x"})
    assert foreign.value.status_code == 404
    assert hosts.resolve_project_id(other, made["id"]) is None
    assert hosts.resolve_project_id(uid, made["id"])["remote_path"] == "/srv/api"
    hosts.patch_host(uid, host, {"disabled": True})
    assert hosts.resolve_project_id(uid, made["id"]) is None  # a disabled machine resolves nothing


def test_read_only_covers_the_project_folder_and_below(uid):
    host = make_host(uid)
    hosts.add_project(uid, host, {"name": "docs", "remote_path": "/srv/docs", "permission_mode": "read_only"})
    assert hosts.effective_permission(uid, bound_host_id=host, bound_cwd="/srv/docs") == "read_only"
    assert hosts.effective_permission(uid, bound_host_id=host, bound_cwd="/srv/docs/sub") == "read_only"
    assert hosts.effective_permission(uid, bound_host_id=host, bound_cwd="/srv/docs-other") == "project"
    # Other spellings of the same folder.
    for cwd in ("//srv/docs/x", "///srv/docs", "/srv/./docs/x", "/srv/api/../docs/x", "/srv/docs/./"):
        assert hosts.effective_permission(uid, bound_host_id=host, bound_cwd=cwd) == "read_only", cwd
    for cwd in ("//srv/docs-other", "/srv/./docsx", "/srv/docs/../api"):
        assert hosts.effective_permission(uid, bound_host_id=host, bound_cwd=cwd) == "project", cwd
    hosts.add_project(uid, host, {"name": "notes", "remote_path": "~/notes", "permission_mode": "read_only"})
    assert hosts.effective_permission(uid, bound_host_id=host, bound_cwd="~/notes/x") == "read_only"
    assert hosts.effective_permission(uid, bound_host_id=host, bound_cwd="$HOME/./notes") == "read_only"
    assert hosts.effective_permission(uid, bound_host_id=host, bound_cwd="~/notes-x") == "project"


def test_archiving_a_project_does_not_deadlock_with_a_finishing_session(uid):
    """The session's run finishes, paused holding its run and thread rows
    (run, then thread, then the project the thread event shows), while the
    project is archived, which writes the project row and its sessions."""
    host = make_host(uid)
    project = hosts.add_project(uid, host, {"name": "api", "remote_path": "/srv/api"})
    thread = session(uid, host, project)
    root = chat_store.start_turn(uid, thread["id"], "hello", thread["revision"], None)["run"]

    def writes_sessions(info):
        return "chat_threads" in info and (info.startswith("UPDATE") or "FOR UPDATE" in info)

    view = _race(_finish(root["id"]), (chat_tx, "lock_thread"),
                 lambda: hosts.patch_project(uid, host, project["id"], {"archived": True}), after=True,
                 waiting=writes_sessions)

    assert view["archived"]
    assert chat_store.get_run(uid, root["id"])["status"] == "completed"
    assert chats.get_thread(uid, thread["id"])["archived"] is True


def test_projects_get_a_palette_color_and_keep_an_edit(uid):
    host = make_host(uid)
    made = hosts.add_project(uid, host, {"name": "api", "remote_path": "/srv/api"})
    assert made["color"] in hosts.PROJECT_COLORS
    assert hosts.patch_project(uid, host, made["id"], {"color": "#12ab34"})["color"] == "#12AB34"
    with pytest.raises(HostError):
        hosts.patch_project(uid, host, made["id"], {"color": "red"})
    assert hosts.get_project(uid, host, made["id"])["color"] == "#12AB34"
    # Projects from before colors existed get a stable palette pick.
    db.execute("UPDATE user_projects SET color='' WHERE id=?", (made["id"],))
    first = hosts.get_project(uid, host, made["id"])["color"]
    assert first in hosts.PROJECT_COLORS and first == hosts.get_project(uid, host, made["id"])["color"]


def test_logos_are_checked_versioned_and_private(uid):
    host = make_host(uid)
    made = hosts.add_project(uid, host, {"name": "api", "remote_path": "/srv/api"})
    for mime, data in (("image/png", b"GIF89a...."), ("image/svg+xml", b"<svg/>"),
                       ("image/png", PNG + b"\x00" * hosts.LOGO_MAX_BYTES), ("image/webp", b"RIFF0000WAVE")):
        with pytest.raises(HostError):
            hosts.set_project_logo(uid, made["id"], mime, data)
    first = hosts.set_project_logo(uid, made["id"], "image/png", PNG)["logo_url"]
    second = hosts.set_project_logo(uid, made["id"], "image/png", PNG)["logo_url"]
    assert first and second and first != second  # a new revision busts caches
    assert hosts.project_logo(uid, made["id"]) == ("image/png", PNG)
    other = quick_user("other@core.test")
    assert hosts.project_logo(other, made["id"]) is None
    with pytest.raises(HostError):
        hosts.set_project_logo(other, made["id"], "image/png", PNG)
    assert hosts.clear_project_logo(uid, made["id"])["logo_url"] == ""
    assert hosts.project_logo(uid, made["id"]) is None


def test_archiving_a_project_takes_and_returns_only_its_own_sessions(uid):
    host = make_host(uid)
    made = hosts.add_project(uid, host, {"name": "api", "remote_path": "/srv/api"})
    kept = session(uid, host, made)
    earlier = session(uid, host, made)
    chats.update_thread(uid, earlier["id"], archived=True)
    hosts.patch_project(uid, host, made["id"], {"archived": True})
    assert hosts.get_project(uid, host, made["id"])["archived"] is True
    assert chats.get_thread(uid, kept["id"])["archived"] is True
    hosts.patch_project(uid, host, made["id"], {"archived": False})
    assert chats.get_thread(uid, kept["id"])["archived"] is False
    assert chats.get_thread(uid, earlier["id"])["archived"] is True  # archived by hand before
    # A new session restores an archived project with its sessions.
    hosts.patch_project(uid, host, made["id"], {"archived": True})
    session(uid, host, made)
    assert hosts.get_project(uid, host, made["id"])["archived"] is False
    assert chats.get_thread(uid, kept["id"])["archived"] is False


def test_deleting_a_project_deletes_or_detaches_its_sessions(uid):
    host = make_host(uid)
    doomed = hosts.add_project(uid, host, {"name": "doomed", "remote_path": "/srv/doomed"})
    gone = session(uid, host, doomed)
    plain = chats.create_thread(uid, model="offline")
    assert hosts.delete_project(uid, host, doomed["id"]) is True
    assert chats.get_thread(uid, gone["id"]) is None
    assert chats.get_thread(uid, plain["id"]) is not None
    kept = hosts.add_project(uid, host, {"name": "kept", "remote_path": "/srv/kept"})
    stays = session(uid, host, kept)
    assert hosts.delete_project(uid, host, kept["id"], sessions="keep") is True
    after = chats.get_thread(uid, stays["id"])
    assert (after["kind"], after["project_id"]) == ("chat", None)
    assert hosts.delete_project(quick_user("other@core.test"), host, kept["id"]) is False


def test_a_failed_project_delete_changes_nothing(uid):
    host = make_host(uid)
    live = hosts.add_project(uid, host, {"name": "live", "remote_path": "/srv/live"})
    first, second = session(uid, host, live), session(uid, host, live)
    real_event = chat_store.event
    deleted = []

    def failing_event(conn, user_id, kind, *args, **kwargs):
        if kind == "thread.deleted":
            deleted.append(kind)
            if len(deleted) == 2:
                raise RuntimeError("event write failed")
        return real_event(conn, user_id, kind, *args, **kwargs)

    with patch.object(chat_store, "event", failing_event), pytest.raises(RuntimeError):
        hosts.delete_project(uid, host, live["id"])
    assert hosts.get_project(uid, host, live["id"]) is not None
    assert chats.get_thread(uid, first["id"]) is not None
    assert chats.get_thread(uid, second["id"]) is not None


def test_deleting_a_project_cancels_its_running_session_and_releases_the_vault_pin(uid):
    host = make_host(uid)
    live = hosts.add_project(uid, host, {"name": "live", "remote_path": "/srv/live"})
    thread = session(uid, host, live)
    run = chat_store.start_turn(uid, thread["id"], "hello", thread["revision"], None)["run"]
    vault.pin_run(uid, run["id"])
    with pytest.raises(HostError) as busy:
        hosts.delete_project(uid, host, live["id"], cancel_active=False)
    assert busy.value.status_code == 409 and chats.get_thread(uid, thread["id"]) is not None
    with patch("app.chat_runs.cancel") as cancel:
        assert hosts.delete_project(uid, host, live["id"]) is True
    cancel.assert_called_once_with(run["id"])
    assert vault._vault[uid]["pinned_runs"] == set()
    assert chat_store.get_run(uid, run["id"]) is None


def test_parallel_writes_promote_a_chat_into_one_project(uid):
    host = make_host(uid)
    thread = chats.create_thread(uid, model="offline")
    match = chat_threads._match_project_row
    entered, release, overlapped = threading.Event(), threading.Event(), threading.Event()
    results, errors = [], []

    def held_match(*args):
        # The first promotion holds the chat's row lock here.
        if entered.is_set():
            overlapped.set()
        else:
            entered.set()
            release.wait(10)
        return match(*args)

    def promote(root):
        try:
            results.append(chats.promote_working_session(uid, thread["id"], host_id=host, cwd=root, tool="write_file"))
        except Exception as exc:  # surfaced below
            errors.append(exc)

    with patch.object(chat_threads, "_match_project_row", held_match):
        first = threading.Thread(target=promote, args=("/srv/one",))
        first.start()
        assert entered.wait(10)
        second = threading.Thread(target=promote, args=("/srv/two",))
        second.start()
        wait_until(lambda: waiting_on("chat_threads") or overlapped.is_set() or not second.is_alive(), timeout=10)
        release.set()
        first.join(10)
        second.join(10)
    assert errors == []
    assert not overlapped.is_set(), "the second promotion did not wait for the first"
    assert sum(1 for item in results if item) == 1
    assert [p["remote_path"] for p in chats.list_sidebar_projects(uid)] == ["/srv/one"]
    assert chats.get_thread(uid, thread["id"])["kind"] == "project"


def test_only_writes_outside_temp_folders_create_a_project(uid):
    host = make_host(uid)
    api = hosts.add_project(uid, host, {"name": "api", "remote_path": "/srv/api"})
    chat = chats.create_thread(uid, model="offline")
    assert chats.promote_working_session(uid, chat["id"], host_id=host, cwd="/srv/new", tool="read_file") is None
    assert chats.promote_working_session(uid, chat["id"], host_id=host, cwd="/tmp/scratch", tool="write_file") is None
    assert chats.promote_working_session(uid, chat["id"], host_id=host, path="/srv/new/main.py", tool="write_file",
                                         existing_only=True) is None
    joined = chats.promote_working_session(uid, chat["id"], host_id=host, path="/srv/api/app/main.py",
                                           tool="search_replace", existing_only=True)
    assert (joined["kind"], joined["project_id"]) == ("project", api["id"])
    other = quick_user("other@core.test")
    stranger = chats.create_thread(other, model="offline")
    # Another account's machine id binds nothing.
    assert chats.promote_working_session(other, stranger["id"], host_id=host, cwd="/srv/x", tool="write_file") is None
    assert [p["name"] for p in chats.list_sidebar_projects(uid)] == ["api"]


def test_a_logo_set_again_after_removal_gets_a_new_address(uid):
    host = make_host(uid)
    made = hosts.add_project(uid, host, {"name": "api", "remote_path": "/srv/api"})
    first = hosts.set_project_logo(uid, made["id"], "image/png", PNG)["logo_url"]
    assert hosts.clear_project_logo(uid, made["id"])["logo_url"] == ""
    again = hosts.set_project_logo(uid, made["id"], "image/png", PNG)["logo_url"]
    # Logos are cached for a year by address: a reused one shows the old image.
    assert again and again != first


def test_a_hello_without_a_version_keeps_the_known_one(uid):
    host = make_host(uid)
    hosts.touch_agent(uid, host, version="1.7.3")
    hosts.touch_agent(uid, host, version="")
    assert hosts.get_host(uid, host)["agent_version"] == "1.7.3"


def test_two_saves_of_different_agent_settings_keep_both(uid):
    hosts.set_settings(uid, agent_coding=False)
    _race(lambda: hosts.set_settings(uid, agent_coding=True), (hosts, "settings_for"),
          lambda: hosts.set_settings(uid, permission_mode="read_only"),
          waiting=lambda info: "user_agent_settings" in info and not info.startswith("SELECT"))
    saved = hosts.settings_for(uid)
    assert (saved["agent_coding"], saved["permission_mode"]) == (True, "read_only")
