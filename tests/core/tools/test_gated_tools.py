"""The permission gate in front of real tool calls on an SSH host.

workspace.execute with a Gate on the fake host: an approved path outside
the working root is really read (confinement follows the approval), a
refused one is not, and a symlink is judged by where the host resolves it.
"""
import asyncio
import json

from app import permissions, ssh_pool, workspace
from tests.core.tools._support import CardUser, make_project, new_thread


def gated(h, name, *, answer=None, unattended=False, **args):
    _, tid = new_thread(h.uid)
    user = CardUser(h.uid, tid, answer)
    gate = permissions.Gate(user_id=h.uid, thread_id=tid, run_id="run-g", requester=None,
                            unattended=unattended, notify=user, cancel=None)
    text, rec = asyncio.run(workspace.execute(
        name, json.dumps(args), user_id=h.uid, bound_host_id=h.host_id, bound_cwd=h.root, gate=gate))
    return workspace.unlabel_host(h.alias, text), rec, user


def test_an_approved_outside_read_happens_and_a_refused_one_does_not(ssh_host):
    path = ssh_host.fs.write("/srv/notes.txt", "outside words\n")

    body, rec, user = gated(ssh_host, "read_file", path=path, answer="allow")
    assert rec["status"] == "done" and "1: outside words" in body and len(user.asks) == 1

    text, rec, _ = gated(ssh_host, "read_file", path=path, answer="deny")
    assert rec["status"] == "error" and rec["kind"] == "permission_denied"
    assert "outside words" not in text


def test_a_secret_file_in_the_project_reads_without_a_card(ssh_host):
    ssh_host.fs.write(f"{ssh_host.root}/.env", "API_KEY=dev\n")

    body, rec, user = gated(ssh_host, "read_file", path=".env", unattended=True)

    assert rec["status"] == "done" and "API_KEY=dev" in body and user.asks == []


def test_a_symlink_inside_the_root_is_judged_by_its_real_target(ssh_host):
    ssh_host.fs.write("/srv/private.txt", "private words\n")
    ssh_host.fs.symlink("/srv/private.txt", f"{ssh_host.root}/link.txt")

    text, rec, _ = gated(ssh_host, "read_file", path="link.txt", unattended=True)
    assert rec["status"] == "error" and "private words" not in text
    text, rec, _ = gated(ssh_host, "run_terminal_command", command="cat link.txt", unattended=True)
    assert rec["status"] == "error" and "private words" not in text
    assert ssh_host.shell.asked == []

    body, rec, _ = gated(ssh_host, "read_file", path="link.txt", answer="allow")
    assert rec["status"] == "done" and "private words" in body


def test_an_approved_write_into_a_read_only_project_outside_the_root_is_refused(ssh_host):
    ssh_host.fs.makedirs("/srv/locked")
    make_project(ssh_host.uid, ssh_host.host_id, "/srv/locked", permission_mode="read_only")

    text, rec, user = gated(ssh_host, "write_file", path="/srv/locked/x.txt", content="x", answer="allow")

    assert len(user.asks) == 1  # the user approved the location...
    assert rec["status"] == "error" and "read-only" in text  # ...but the project stays read-only
    assert not ssh_host.fs.exists("/srv/locked/x.txt")


def test_a_denylisted_command_reaches_the_host_only_on_the_users_own_allow(ssh_host):
    command = "make deploy; sudo -n true"
    ssh_host.shell.on(command, out="done\n")

    _, rec, user = gated(ssh_host, "run_terminal_command", command=command, answer="deny")
    assert rec["status"] == "error" and ssh_host.shell.asked == []
    assert user.asks[0]["grant_kind"] == ""

    _, rec, _ = gated(ssh_host, "run_terminal_command", command=command, answer="allow")
    assert rec["state"] == "completed" and ssh_host.shell.reached == [command]


def test_an_unreachable_host_is_reported_as_a_host_failure(ssh_host, monkeypatch):
    async def down(user_id, host_id):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(ssh_pool, "connection", down)
    text, rec = asyncio.run(workspace.execute(
        "read_file", json.dumps({"path": "a.txt"}), user_id=ssh_host.uid,
        bound_host_id=ssh_host.host_id, bound_cwd=ssh_host.root))

    assert rec["status"] == "error" and rec["kind"] == "upstream_error"
    assert text.startswith("SSH connection failed")
