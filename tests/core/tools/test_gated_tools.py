"""The permission gate in front of real tool calls on an SSH host.

workspace.execute with a Gate: an approved path outside the working root is
really read (confinement follows the approval), a refused one is not, and a
real symlink is judged by where the host resolves it.
"""
import asyncio
import json
import os

from app import permissions, ssh_pool, workspace
from tests.core.tools._support import CardUser, new_thread


def gated(h, name, *, answer=None, unattended=False, **args):
    _, tid = new_thread(h.uid)
    user = CardUser(h.uid, tid, answer)
    gate = permissions.Gate(user_id=h.uid, thread_id=tid, run_id="run-g", requester=None,
                            unattended=unattended, notify=user, cancel=None)
    text, rec = asyncio.run(workspace.execute(
        name, json.dumps(args), user_id=h.uid, bound_host_id=h.host_id, bound_cwd=h.root, gate=gate))
    return workspace.unlabel_host(h.alias, text), rec, user


def outside_file(h, name="notes.txt", body="outside words\n"):
    path = os.path.join(h.base, name)
    with open(path, "w") as f:
        f.write(body)
    return path


def test_an_approved_outside_read_happens_and_a_refused_one_does_not(ssh_host):
    path = outside_file(ssh_host)

    body, rec, user = gated(ssh_host, "read_file", path=path, answer="allow")
    assert rec["status"] == "done" and "1: outside words" in body and len(user.asks) == 1

    text, rec, _ = gated(ssh_host, "read_file", path=path, answer="deny")
    assert rec["status"] == "error" and rec["kind"] == "permission_denied"
    assert "outside words" not in text


def test_a_secret_file_in_the_project_reads_without_a_card(ssh_host):
    with open(os.path.join(ssh_host.root, ".env"), "w") as f:
        f.write("API_KEY=dev\n")

    body, rec, user = gated(ssh_host, "read_file", path=".env", unattended=True)

    assert rec["status"] == "done" and "API_KEY=dev" in body and user.asks == []


def test_a_symlink_inside_the_root_is_judged_by_its_real_target(ssh_host):
    target = outside_file(ssh_host, "private.txt", "private words\n")
    os.symlink(target, os.path.join(ssh_host.root, "link.txt"))

    text, rec, _ = gated(ssh_host, "read_file", path="link.txt", unattended=True)
    assert rec["status"] == "error" and "private words" not in text
    text, rec, _ = gated(ssh_host, "run_terminal_command", command="cat link.txt", unattended=True)
    assert rec["status"] == "error" and "private words" not in text

    body, rec, _ = gated(ssh_host, "read_file", path="link.txt", answer="allow")
    assert rec["status"] == "done" and "private words" in body


def test_a_denylisted_command_runs_only_on_the_users_own_allow(ssh_host):
    marker = os.path.join(ssh_host.root, "ran")
    command = f"echo x; sudo -n true 2>/dev/null; touch {marker}"

    _, rec, user = gated(ssh_host, "run_terminal_command", command=command, answer="deny")
    assert rec["status"] == "error" and not os.path.exists(marker)
    assert user.asks[0]["grant_kind"] == ""

    _, rec, _ = gated(ssh_host, "run_terminal_command", command=command, answer="allow")
    assert rec["state"] == "completed" and os.path.exists(marker)


def test_an_unreachable_host_is_reported_as_a_host_failure(ssh_host, monkeypatch):
    async def down(user_id, host_id):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(ssh_pool, "connection", down)
    text, rec = asyncio.run(workspace.execute(
        "read_file", json.dumps({"path": "a.txt"}), user_id=ssh_host.uid,
        bound_host_id=ssh_host.host_id, bound_cwd=ssh_host.root))

    assert rec["status"] == "error" and rec["kind"] == "upstream_error"
    assert text.startswith("SSH connection failed")
