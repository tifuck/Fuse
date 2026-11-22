"""Environment secrets: a shell call names one in `env`, its owner allows it
on a card, and the command's process gets the value in its environment. The
value is never in the command text, the job row, the card or the script's
argv. The hosts are the recording fakes: nothing runs.
"""
import json

import pytest

from app import db, permissions, secret_guard, workspace
from tests.core.helpers import run_async
from tests.core.secrets._values import GITHUB, OPENAI
from tests.core.tools._support import make_host, new_thread

NAME = "HCLOUD_TOKEN"
COMMAND = "make deploy"


class Owner:
    """A Gate's notify callback that answers every card with one action."""

    def __init__(self, uid, tid, answer):
        self.uid, self.tid, self.answer, self.asks = uid, tid, answer, []

    def __call__(self, event):
        if event.get("status") == "pending":
            self.asks.append(event)
            permissions.resolve(self.uid, self.tid, event["id"], self.answer)


def call(h, *, answer="allow", unattended=False, thread=None, gate=True, host=None, name="run_terminal_command", **args):
    tid = thread or new_thread(h.uid)[1]
    owner = Owner(h.uid, tid, answer)
    made = permissions.Gate(user_id=h.uid, thread_id=tid, run_id="run-1", requester=None, unattended=unattended,
                            notify=owner, cancel=None) if gate else None
    args.setdefault("command", COMMAND)
    args.setdefault("env", [NAME])
    if name == "monitor":
        args.setdefault("description", "deploy output")
    text, rec = run_async(workspace.execute(name, json.dumps({**args, **({"host": host} if host else {})}), user_id=h.uid,
                                            bound_host_id=h.host_id, bound_cwd=h.root, thread_id=tid, gate=made))
    return text, rec, owner


@pytest.fixture
def ssh(ssh_host):
    secret_guard.add_manual(ssh_host.uid, NAME, GITHUB, env=True)
    ssh_host.shell.on(COMMAND, out="deployed\n")
    return ssh_host


def test_an_allowed_command_gets_the_value_on_stdin_and_nowhere_else(ssh):
    text, rec, owner = call(ssh)

    assert rec["status"] == "done" and ssh.shell.reached == [COMMAND]
    script = ssh.shell.raw[-1]
    assert script.index(f"IFS= read -r {NAME}; export {NAME}") < script.index("exec </dev/null")
    assert ssh.conn.processes[-1].fed == [GITHUB + "\n"]
    [ask] = owner.asks
    assert (ask["perm_kind"], ask["path"], ask["command"], ask["grant_kind"], ask["grant_value"]) == (
        "env", NAME, COMMAND, "env", NAME)
    row = db.fetchone("SELECT * FROM command_jobs WHERE id=?", (rec["id"],))
    stored = db.fetchall("SELECT * FROM chat_permission_asks WHERE user_id=?", (ssh.uid,))
    for place in (script, text, json.dumps(rec), json.dumps(dict(row)), json.dumps(owner.asks), json.dumps([dict(r) for r in stored])):
        assert GITHUB not in place


def test_a_call_without_env_gets_nothing(ssh):
    _text, rec, owner = call(ssh, env=[])

    assert rec["status"] == "done" and owner.asks == []
    assert "read -r" not in ssh.shell.raw[-1] and ssh.conn.processes[-1].fed == []


def test_denied_or_unknown_or_ungated_never_reaches_the_machine(ssh):
    denied, rec, _ = call(ssh, answer="deny")
    assert rec["status"] == "error" and "The user denied it" in denied and NAME in denied

    unknown, rec, owner = call(ssh, env=["NOPE"])
    assert rec["status"] == "error" and owner.asks == []
    assert "no environment secret is named NOPE" in unknown and f"Set: {NAME}." in unknown

    ungated, rec, _ = call(ssh, gate=False)
    assert rec["status"] == "error" and "only available in a chat" in ungated
    assert ssh.shell.asked == [] and ssh.conn.processes == []


def test_allow_always_is_per_name_and_machine_and_is_what_an_unattended_run_needs(ssh):
    other = make_host(ssh.uid, "box2", default_cwd=ssh.root)
    refused, rec, owner = call(ssh, unattended=True)
    assert rec["status"] == "error" and refused.startswith("Unattended run") and owner.asks == []

    _, tid = new_thread(ssh.uid)
    call(ssh, answer="allow_session", thread=tid)
    assert call(ssh, thread=tid)[2].asks == []       # this chat: remembered
    assert len(call(ssh)[2].asks) == 1               # another chat: asked again

    call(ssh, answer="allow_host")
    [grant] = [g for g in db.fetchall("SELECT * FROM chat_permission_grants WHERE user_id=?", (ssh.uid,)) if g["thread_id"] is None]
    assert (grant["kind"], grant["value"], grant["host_id"]) == ("env", NAME, ssh.host_id)
    _text, rec, owner = call(ssh, unattended=True)
    assert rec["status"] == "done" and owner.asks == []
    assert call(ssh, unattended=True, host="box2")[1]["status"] == "error"   # that machine was never allowed
    assert other

    # A second secret is its own decision.
    secret_guard.add_manual(ssh.uid, "OTHER_KEY", OPENAI, env=True)
    _text, rec, owner = call(ssh, env=[NAME, "OTHER_KEY"])
    assert [a["path"] for a in owner.asks] == ["OTHER_KEY"] and rec["status"] == "done"
    assert ssh.conn.processes[-1].fed == [GITHUB + "\n" + OPENAI + "\n"]


def test_bypass_skips_the_card(ssh):
    _, tid = new_thread(ssh.uid)
    permissions.set_bypass(ssh.uid, tid, True)

    _text, rec, owner = call(ssh, thread=tid)

    assert rec["status"] == "done" and owner.asks == [] and ssh.conn.processes[-1].fed == [GITHUB + "\n"]


def test_a_connector_gets_env_beside_the_command_and_an_old_one_is_refused(agent_host):
    h = agent_host
    secret_guard.add_manual(h.uid, NAME, GITHUB, env=True)
    h.shell.on(COMMAND, out="deployed\n")

    old, rec, owner = call(h)
    assert rec["status"] == "error" and "need connector 1.10.0" in old and owner.asks == [] and h.shell.asked == []

    with db.tx() as conn:
        conn.execute("UPDATE user_hosts SET agent_version='1.10.0' WHERE id=?", (h.host_id,))
    _text, rec, _owner = call(h)
    assert rec["status"] == "done"
    # A monitor's runner carries it the same way.
    run_async(workspace._make_monitor_agent_runner(h.uid, h.host_id, COMMAND, h.root, 1000, {NAME: GITHUB})(lambda text: None))
    sent = [args for op, args in h.connector.calls if op == "exec_stream" and args["cmd"].endswith(COMMAND)]
    assert len(sent) == 2 and all(args["env"] == {NAME: GITHUB} and GITHUB not in args["cmd"] for args in sent)
