"""Auto mode: Jev's verdict stands in for the user's card, and fails closed.

jev.judge is checked against a mocked decisions API (httpx.MockTransport);
the gate's Auto flow is checked with judge itself scripted. The OpenRouter
key lookup is the boundary for "this user may use Auto".
"""
import asyncio
import json

import httpx
import pytest

from app import chats, db, jev, permissions
from app.sources import openrouter
from tests.core.helpers import quick_user
from tests.core.tools._support import CardUser, make_host, new_thread

ROOT = "/srv/app"


@pytest.fixture
def chat(fresh_db, monkeypatch):
    uid = quick_user()
    thread, tid = new_thread(uid)
    monkeypatch.setattr(openrouter, "user_has_openrouter", lambda user_id: True)
    monkeypatch.setattr(openrouter, "pick_key_for", lambda user_id, source_id=None: ({}, {}, "sk-or-test", "user"))
    return type("Chat", (), {"uid": uid, "tid": tid, "thread": thread, "host": make_host(uid, "box", default_cwd=ROOT)})


@pytest.fixture
def decisions(monkeypatch):
    """The decisions endpoint: set `.reply` to (status, json body); `.requests` holds what was sent."""
    state = type("Decisions", (), {"reply": (200, {}), "requests": []})()
    real = httpx.AsyncClient

    def handler(request):
        state.requests.append(json.loads(request.content))
        status, body = state.reply
        return httpx.Response(status, json=body)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return state


def verdict_for(noul):
    return (200, {"model": jev.MODEL, "answers": {"dangerous": {"noul": noul}}})


def judge(chat, command="rm -rf build"):
    return asyncio.run(jev.judge(chat.uid, chat.tid, {"tool": "run_terminal_command", "command": command},
                                 roots=["/tmp", ROOT]))


def test_jev_allows_safe_actions_and_refuses_dangerous_ones(chat, decisions):
    decisions.reply = verdict_for(0.1)
    assert judge(chat) is True
    decisions.reply = verdict_for(0.9)
    assert judge(chat) is False

    sent = decisions.requests[0]
    assert sent["model"] == jev.MODEL
    assert sent["state"]["action"]["command"] == "rm -rf build"
    assert sent["state"]["allowed_roots"] == ["/tmp", ROOT]


def test_the_danger_ceiling_itself_is_dangerous(chat, decisions):
    decisions.reply = verdict_for(jev.DANGEROUS_CEILING)
    assert judge(chat) is False
    decisions.reply = verdict_for(jev.DANGEROUS_CEILING - 0.01)
    assert judge(chat) is True


def test_jev_only_sees_the_callers_own_thread(chat, decisions):
    owner = chats.create_thread(chat.uid, model="offline",
                                messages=[{"role": "user", "content": "deploy the secret plan"}])
    owner_tid = chats.resolve_thread_pk(chat.uid, owner["id"])
    stranger = quick_user("stranger@core.test")
    decisions.reply = verdict_for(0.1)
    action = {"tool": "read_file", "path": "/etc/hosts"}

    asyncio.run(jev.judge(chat.uid, owner_tid, action))
    asyncio.run(jev.judge(stranger, owner_tid, action))

    mine, theirs = decisions.requests
    assert mine["state"]["user_request"] == "deploy the secret plan"
    assert theirs["state"]["user_request"] == "" and theirs["state"]["recent"] == []


@pytest.mark.parametrize("reply", [
    (500, {"error": "upstream"}),
    (500, {"model": jev.MODEL, "answers": {"dangerous": {"noul": 0.0}}}),  # a safe-looking body on an error status
    (200, ["not", "an", "object"]),
    (200, {"answers": {}}),
    (200, {"answers": {"dangerous": {"noul": 1.7}}}),
    (200, {"answers": {"dangerous": {"noul": True}}}),
    (200, {"answers": {"dangerous": {"noul": 0.0}}, "error": "partial"}),
])
def test_any_unclear_answer_is_no_verdict(chat, decisions, reply):
    decisions.reply = reply
    assert judge(chat) is None


def test_no_key_or_an_overlong_command_never_reaches_jev(chat, decisions, monkeypatch):
    assert judge(chat, command="echo " + "x" * jev.ACTION_CLIP) is None
    monkeypatch.setattr(openrouter, "user_has_openrouter", lambda user_id: False)
    assert judge(chat) is None
    assert decisions.requests == []


# -- the gate in Auto mode ----------------------------------------------------

@pytest.fixture
def jev_says(monkeypatch):
    """Script jev.judge: set `.answer` (True / False / None / an exception)."""
    state = type("Judge", (), {"answer": True, "calls": [], "before": None})()

    async def fake(user_id, thread_id, action, *, roots=None):
        state.calls.append(action)
        if state.before:
            state.before()
        if isinstance(state.answer, Exception):
            raise state.answer
        return state.answer

    monkeypatch.setattr(jev, "judge", fake)
    return state


def review(chat, tool, *, answer=None, unattended=False, **args):
    user = CardUser(chat.uid, chat.tid, answer)
    gate = permissions.Gate(user_id=chat.uid, thread_id=chat.tid, run_id="run-1", requester=None,
                            unattended=unattended, notify=user, cancel=None)
    verdict = asyncio.run(permissions.review(tool=tool, args=args, user_id=chat.uid, host_id=chat.host,
                                             host_alias="box", root=ROOT, windows=False, gate=gate))
    return verdict, user


def test_auto_follows_jev_without_raising_a_card(chat, jev_says):
    permissions.set_auto(chat.uid, chat.tid, True)

    jev_says.answer = True
    allowed, user = review(chat, "read_file", path="/etc/hosts")
    assert allowed.allowed and user.asks == []
    assert "outside the allowed roots" in jev_says.calls[0]["reasons"]

    jev_says.answer = False
    denied, user = review(chat, "run_terminal_command", command="rm -rf /var/www")
    assert not denied.allowed and denied.detail.startswith("Permission denied by Jev")
    assert user.asks == []
    assert "matches a destructive command pattern" in jev_says.calls[-1]["reasons"]


def test_no_verdict_falls_back_to_the_users_card(chat, jev_says):
    permissions.set_auto(chat.uid, chat.tid, True)
    for answer in (None, RuntimeError("jev down")):
        jev_says.answer = answer
        verdict, user = review(chat, "write_file", path="/etc/motd", answer="allow")
        assert verdict.allowed and len(user.asks) == 1
        unattended, _ = review(chat, "write_file", path="/etc/motd", unattended=True)
        assert not unattended.allowed and unattended.detail.startswith("Unattended run")


def test_auto_never_judges_in_scope_calls_and_allows_listings_itself(chat, jev_says):
    permissions.set_auto(chat.uid, chat.tid, True)
    assert review(chat, "read_file", path="src/app.py")[0].allowed
    assert review(chat, "list_dir", path="/var")[0].allowed
    assert review(chat, "glob", path="/opt", pattern="*.conf")[0].allowed
    assert jev_says.calls == []


def test_without_a_key_auto_allows_nothing_by_itself(chat, jev_says, monkeypatch):
    monkeypatch.setattr(openrouter, "user_has_openrouter", lambda user_id: False)
    permissions.set_auto(chat.uid, chat.tid, True)

    for tool, args in (("list_dir", {"path": "/var"}), ("read_file", {"path": "/etc/hosts"})):
        verdict, _ = review(chat, tool, unattended=True, **args)
        assert not verdict.allowed, tool
    assert jev_says.calls == []


def test_auto_off_never_asks_jev(chat, jev_says):
    verdict, user = review(chat, "read_file", path="/etc/hosts", answer="deny")
    assert not verdict.allowed and len(user.asks) == 1
    assert jev_says.calls == []


def test_turning_auto_on_sends_a_waiting_card_to_jev_once(chat, jev_says):
    jev_says.answer = True
    flip = lambda event: permissions.set_auto(chat.uid, chat.tid, True)  # noqa: E731

    verdict, user = review(chat, "read_file", path="/etc/hosts", answer=flip)

    assert verdict.allowed and len(jev_says.calls) == 1
    [row] = db.fetchall("SELECT status, decision FROM chat_permission_asks WHERE thread_id=?", (chat.tid,))
    assert (row["status"], row["decision"]) == ("allowed", "allow")


def test_the_users_answer_beats_a_late_jev_verdict(chat, jev_says):
    ask = {}
    jev_says.answer = True
    jev_says.before = lambda: permissions.resolve(chat.uid, chat.tid, ask["id"], "deny")

    def flip(event):
        ask["id"] = event["id"]
        permissions.set_auto(chat.uid, chat.tid, True)

    verdict, _ = review(chat, "read_file", path="/etc/hosts", answer=flip)

    assert not verdict.allowed and verdict.detail.startswith("Permission denied by user")


def test_switching_auto_on_approves_nothing_by_itself(chat):
    db.execute(
        "INSERT INTO chat_permission_asks (id,user_id,thread_id,run_id,status,kind,tool,created_at,expires_at)"
        " VALUES ('pa_wait',?,?,'run-1','pending','path','read_file',?,?)",
        (chat.uid, chat.tid, db.now(), "2999-01-01T00:00:00Z"),
    )
    permissions.set_auto(chat.uid, chat.tid, True)
    assert db.fetchone("SELECT status FROM chat_permission_asks WHERE id='pa_wait'")["status"] == "pending"
