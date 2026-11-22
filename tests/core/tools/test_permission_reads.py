"""What the permission gate reads, and when: one read of the grants per
check, one read of the read-only projects per tool call, and a waiting card
that learns of an answer from this process at once and of one written by
another process on its next poll."""
import asyncio
import json
import threading

import pytest

from app import db, hosts, permissions, workspace
from tests.core.helpers import quick_user
from tests.core.tools._support import CardUser, make_host, make_project, new_thread

ROOT = "/srv/app"


@pytest.fixture
def chat(fresh_db):
    uid = quick_user()
    _, tid = new_thread(uid)
    return type("Chat", (), {"uid": uid, "tid": tid, "host": make_host(uid, "box", default_cwd=ROOT)})


def review(chat, tool, *, answer=None, unattended=False, **args):
    user = CardUser(chat.uid, chat.tid, answer)
    gate = permissions.Gate(user_id=chat.uid, thread_id=chat.tid, run_id="run-1", requester=None,
                            unattended=unattended, notify=user, cancel=None)
    verdict = asyncio.run(permissions.review(
        tool=tool, args=args, user_id=chat.uid, host_id=chat.host, host_alias="box", root=ROOT,
        windows=False, gate=gate))
    return verdict, user


def asks(chat, tool, **args):
    verdict, _ = review(chat, tool, unattended=True, **args)
    return not verdict.allowed


def after_first_poll(monkeypatch, then):
    """Run then(ask_id) on another thread once the waiting gate has read its
    ask: from there on only a wakeup or the next poll can deliver it."""
    polled = threading.Event()
    real = permissions._poll_ask

    def poll(*args):
        row = real(*args)
        polled.set()
        return row

    monkeypatch.setattr(permissions, "_poll_ask", poll)

    def answer(event):
        def worker():
            polled.wait(10)
            then(event["id"])
        threading.Thread(target=worker, daemon=True).start()
    return answer


def test_an_answer_from_this_process_wakes_the_waiting_card(chat, monkeypatch):
    monkeypatch.setattr(permissions, "_ASK_POLL_S", 600)  # no poll before the deadline
    monkeypatch.setattr(permissions, "ASK_TIMEOUT_S", 10)
    answer = after_first_poll(monkeypatch, lambda ask_id: permissions.resolve(chat.uid, chat.tid, ask_id, "allow"))

    verdict, user = review(chat, "read_file", path="/etc/hosts", answer=answer)

    assert verdict.allowed and user.events[-1]["status"] == "allowed"
    assert permissions._ask_waiters == {}


def test_turning_bypass_on_wakes_the_waiting_card(chat, monkeypatch):
    monkeypatch.setattr(permissions, "_ASK_POLL_S", 600)
    monkeypatch.setattr(permissions, "ASK_TIMEOUT_S", 10)
    answer = after_first_poll(monkeypatch, lambda ask_id: permissions.set_bypass(chat.uid, chat.tid, True))

    verdict, _ = review(chat, "read_file", path="/etc/hosts", answer=answer)

    assert verdict.allowed


def test_an_answer_written_by_another_process_arrives_by_the_poll(chat, monkeypatch):
    monkeypatch.setattr(permissions, "_ASK_POLL_S", 0.05)
    monkeypatch.setattr(permissions, "ASK_TIMEOUT_S", 10)

    def other_worker(ask_id):
        db.execute("UPDATE chat_permission_asks SET status='denied', decision='deny', resolved_at=? WHERE id=?",
                   (db.now(), ask_id))

    verdict, user = review(chat, "read_file", path="/etc/hosts", answer=after_first_poll(monkeypatch, other_worker))

    assert not verdict.allowed and user.events[-1]["status"] == "denied"


def test_a_command_runs_silently_only_when_grants_cover_every_path(chat):
    permissions.add_grant(chat.uid, chat.tid, chat.host, "dir", "/data/in")
    permissions.add_grant(chat.uid, None, chat.host, "dir", "/data/out")  # a host-wide grant counts too
    permissions.add_grant(chat.uid, chat.tid, chat.host, "file", "/data/keys/id.pem")

    assert not asks(chat, "run_terminal_command", command="diff /data/in/a.txt /data/out/b.txt")
    assert asks(chat, "run_terminal_command", command="diff /data/in/a.txt /data/other/b.txt")
    assert asks(chat, "run_terminal_command", command="diff /data/in/a.txt /data/outside/b.txt")
    assert not asks(chat, "read_file", path="/data/keys/id.pem")  # a file grant covers exactly that file
    assert asks(chat, "read_file", path="/data/keys/other.pem")
    for kind, value, covered in (("dir", "/data/in/deep", True), ("dir", "/data", False),
                                 ("file", "/data/keys/id.pem", True), ("file", "/data/in/x", False), ("dir", "", False)):
        assert permissions.grant_covers(chat.uid, chat.tid, chat.host, kind, value, False) is covered, (kind, value)


def test_home_and_absolute_grants_cover_only_their_own_spelling(chat):
    permissions.add_grant(chat.uid, chat.tid, chat.host, "dir", "~/notes")
    permissions.add_grant(chat.uid, chat.tid, chat.host, "dir", "//data/abs")
    permissions.add_grant(chat.uid, chat.tid, chat.host, "dir_read", "/data/ro")

    assert not asks(chat, "run_terminal_command", command="diff ~/notes/a.txt //data/abs/b.txt")
    assert not asks(chat, "run_terminal_command", command="cat ~/notes/./sub/../b.txt")
    assert not asks(chat, "read_file", path="/data/abs/./x")
    assert asks(chat, "run_terminal_command", command="cat ~/notes-x/a.txt")
    assert asks(chat, "run_terminal_command", command="cat /notes/a.txt")  # not the home's notes
    assert asks(chat, "read_file", path="~/data/abs/x")
    # A read-only grant covers reads, never a write or a command.
    assert not asks(chat, "read_file", path="/data/ro/x")
    assert asks(chat, "write_file", path="/data/ro/x", content="x")
    assert asks(chat, "run_terminal_command", command="cat /data/ro/x")
    for kind, value, reads, covered in (("dir", "~/notes/deep", False, True), ("dir", "/notes", False, False),
                                        ("dir", "/data/abs/x", False, True), ("dir", "/data/ro/x", True, True),
                                        ("dir", "/data/ro/x", False, False)):
        assert permissions.grant_covers(chat.uid, chat.tid, chat.host, kind, value, False, reads) is covered, (kind, value)


def test_the_per_path_read_only_decision_matches_effective_permission(fresh_db):
    uid = quick_user()
    box, win = make_host(uid, "box"), make_host(uid, "win", agent_os="windows")
    make_project(uid, box, "/srv/docs", permission_mode="read_only")
    make_project(uid, box, "/srv/api")
    make_project(uid, win, "C:\\Work\\Locked", permission_mode="read_only")
    paths = ["", "/srv/docs", "/srv/docs/", "/srv/docs/sub/x", "/srv/docs-other", "/srv", "/srv/api/x", "~",
             "C:\\Work\\Locked", "c:\\work\\locked\\a.txt", "C:\\Work\\Lockedx", "C:\\Work"]

    def decisions():
        for host in (box, win, None):
            scope = hosts.permission_scope(uid, host)
            for path in paths:
                assert hosts.permission_in(scope, path) == hosts.effective_permission(
                    uid, bound_host_id=host, bound_cwd=path), (host, path)
        return {(host, p): hosts.effective_permission(uid, bound_host_id=host, bound_cwd=p) for host in (box, win) for p in paths}

    found = decisions()
    assert found[(box, "/srv/docs/sub/x")] == "read_only" and found[(box, "/srv/docs-other")] == "project"
    assert found[(win, "c:\\work\\locked\\a.txt")] == "read_only" and found[(win, "C:\\Work\\Lockedx")] == "project"
    hosts.set_settings(uid, permission_mode="read_only")
    assert set(decisions().values()) == {"read_only"}


def test_a_project_made_read_only_while_the_card_waits_still_blocks_the_write(ssh_host):
    ssh_host.fs.makedirs("/srv/later")
    _, tid = new_thread(ssh_host.uid)

    def lock_then_allow(event):
        make_project(ssh_host.uid, ssh_host.host_id, "/srv/later", permission_mode="read_only")
        permissions.resolve(ssh_host.uid, tid, event["id"], "allow")

    user = CardUser(ssh_host.uid, tid, lock_then_allow)
    gate = permissions.Gate(user_id=ssh_host.uid, thread_id=tid, run_id="run-g", requester=None,
                            unattended=False, notify=user, cancel=None)
    text, rec = asyncio.run(workspace.execute(
        "write_file", json.dumps({"path": "/srv/later/x.txt", "content": "x"}), user_id=ssh_host.uid,
        bound_host_id=ssh_host.host_id, bound_cwd=ssh_host.root, gate=gate))

    assert len(user.asks) == 1 and rec["status"] == "error" and "read-only" in text
    assert not ssh_host.fs.exists("/srv/later/x.txt")
