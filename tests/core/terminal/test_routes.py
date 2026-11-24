"""/terminal HTTP routes and the /terminal/ws socket: origin and ownership,
attach and replace, the connector capability gate, caps, the host list, and
teardown on host disable, host delete and logout.

Real routes behind a TestClient; SSH channels are FakePty and connectors a
fake socket on the real hub, all on the client's event loop. Nothing runs.
"""
import pytest
from starlette.websockets import WebSocketDisconnect

from app import agent_hub, terminal
from tests.core.agents._support import agent_host
from tests.core.auth._support import sign_in, web_client
from tests.core.helpers import quick_user, wait_until
from tests.core.terminal._support import PtyConnector, audit
from tests.core.tools._support import make_host

ORIGIN = {"origin": "https://testserver"}
SOCKET = "wss://testserver/terminal/ws"


@pytest.fixture
def web():
    with web_client() as client:
        yield client


def _open(web, **body):
    return web.post("/terminal/sessions", json={"cols": 80, "rows": 24, **body})


def _attach(ws, session_id):
    ws.send_json({"t": "attach", "id": session_id, "cols": 80, "rows": 24})
    return ws.receive_json()


def _closed_with(web, session_id, origin=ORIGIN) -> int:
    with pytest.raises(WebSocketDisconnect) as closed:
        with web.websocket_connect(SOCKET, headers=origin) as ws:
            _attach(ws, session_id)
            ws.receive_json()
    return closed.value.code


def test_the_socket_refuses_other_origins_strangers_and_unknown_sessions(web, ssh_ptys):
    owner, stranger = quick_user("ada@core.test"), quick_user("bob@core.test")
    sign_in(web, owner)
    opened = _open(web, host_id=make_host(owner))
    assert opened.status_code == 201, opened.text
    sid = opened.json()["id"]
    for origin in ("https://evil.test", "http://testserver", "null"):
        assert _closed_with(web, sid, {"origin": origin}) == 4403
    assert _closed_with(web, "no-such-session") == 4404
    sign_in(web, stranger)
    assert _closed_with(web, sid) == 4404
    assert web.delete(f"/terminal/sessions/{sid}").status_code == 404
    assert web.get("/terminal/sessions").json() == {"sessions": []}
    web.cookies.clear()
    assert _closed_with(web, sid) == 4401


def test_a_second_attach_replays_the_scrollback_and_replaces_the_first_with_4409(web, ssh_ptys):
    uid = quick_user()
    sign_in(web, uid)
    view = _open(web, host_id=make_host(uid, "web")).json()
    assert (view["alias"], view["transport"], view["user"], view["root"], view["cwd"], view["shell"]) == (
        "web", "ssh", "dev", False, "~", "login")
    web.portal.call(ssh_ptys[0].emit, b"$ ")
    wait_until(lambda: terminal.get(view["id"]).ring)
    with web.websocket_connect(SOCKET, headers=ORIGIN) as first:
        ready = _attach(first, view["id"])
        assert ready == {"t": "ready", **view}
        assert first.receive_bytes() == b"$ "
        first.send_bytes(b"ls\r")
        first.send_json({"t": "ack", "n": 2})
        first.send_json({"t": "ping"})
        assert first.receive_json() == {"t": "pong"}
        assert bytes(ssh_ptys[0].input) == b"ls\r"
        with web.websocket_connect(SOCKET, headers=ORIGIN) as second:
            assert _attach(second, view["id"])["t"] == "ready"
            assert second.receive_bytes() == b"$ "
            with pytest.raises(WebSocketDisconnect) as replaced:
                first.receive_json()
            assert replaced.value.code == 4409
            listed = web.get("/terminal/sessions").json()["sessions"]
            assert [(s["id"], s["attached"], s["alive"]) for s in listed] == [(view["id"], True, True)]
            second.send_json({"t": "close"})
            assert second.receive_json() == {"t": "exit", "code": None, "reason": "closed"}
            with pytest.raises(WebSocketDisconnect) as ended:
                second.receive_json()
            assert ended.value.code == 4410
    assert ssh_ptys[0].closed
    assert _closed_with(web, view["id"]) == 4410


def test_an_old_connector_gets_409_and_an_offline_one_too(web):
    uid = quick_user()
    sign_in(web, uid)
    host, token = agent_host(uid)
    offline = _open(web, host_id=host["id"])
    assert (offline.status_code, offline.json()["detail"]) == (409, "machine is offline")
    ws = PtyConnector(token, caps=())
    web.portal.start_task_soon(agent_hub.handle_socket, ws)
    wait_until(lambda: agent_hub.is_online(uid, host["id"]))
    old = _open(web, host_id=host["id"])
    assert (old.status_code, old.json()["detail"]) == (409, agent_hub.PTY_UPDATE)
    assert ws.jobs() == []
    web.portal.call(ws.drop)
    wait_until(lambda: not agent_hub.is_online(uid, host["id"]))


def test_terminals_are_capped_per_machine_and_per_account(web, ssh_ptys, monkeypatch):
    monkeypatch.setattr(terminal, "MAX_PER_HOST", 1)
    monkeypatch.setattr(terminal, "MAX_PER_USER", 2)
    uid = quick_user()
    sign_in(web, uid)
    one, two, three = make_host(uid, "one"), make_host(uid, "two"), make_host(uid, "three")
    assert _open(web, host_id=one).status_code == 201
    per_host = _open(web, host_id=one)
    assert per_host.status_code == 429 and "on this machine" in per_host.json()["detail"]
    assert _open(web, host_id=two).status_code == 201
    per_user = _open(web, host_id=three)
    assert per_user.status_code == 429 and "2 open terminals" in per_user.json()["detail"]
    assert len(ssh_ptys) == 2


def test_the_host_list_names_shells_and_leaves_out_disabled_hosts_and_this_server(web):
    uid = quick_user()
    sign_in(web, uid)
    ssh = make_host(uid, "web")
    windows = make_host(uid, "win", auth_type="agent", agent_os="windows")
    make_host(uid, "localhost")
    gone = make_host(uid, "gone")
    web.patch(f"/hosts/{gone}", json={"disabled": True})
    hosts = {h["alias"]: h for h in web.get("/terminal/hosts").json()["hosts"]}
    assert sorted(hosts) == ["web", "win"]
    assert hosts["web"] == {"id": ssh, "alias": "web", "os": "", "transport": "ssh", "online": False,
                            "shells": [{"id": "login", "label": "Login shell"}], "default_shell": "login"}
    assert hosts["win"]["id"] == windows and hosts["win"]["online"] is False
    assert [s["id"] for s in hosts["win"]["shells"]] == ["powershell", "cmd"]
    assert hosts["win"]["default_shell"] == "powershell"
    assert _open(web, host_id=ssh, shell="cmd").status_code == 400
    assert _open(web, host_id=gone).status_code == 404


def test_disabling_or_deleting_a_host_closes_its_terminals(web, ssh_ptys):
    uid = quick_user()
    sign_in(web, uid)
    first, second = make_host(uid, "first"), make_host(uid, "second")
    a = _open(web, host_id=first).json()["id"]
    b = _open(web, host_id=second).json()["id"]
    assert web.patch(f"/hosts/{first}", json={"disabled": True}).status_code == 200
    assert not terminal.get(a).alive and terminal.get(b).alive and ssh_ptys[0].closed
    assert web.delete(f"/hosts/{second}").status_code == 200
    assert not terminal.get(b).alive and ssh_ptys[1].closed
    wait_until(lambda: audit(a)["closed_at"] and audit(b)["closed_at"])
    assert (audit(a)["close_reason"], audit(b)["close_reason"]) == ("host_disabled", "host_removed")


def test_logout_closes_that_sign_ins_terminals(web, ssh_ptys):
    uid = quick_user()
    sign_in(web, uid)
    sid = _open(web, host_id=make_host(uid)).json()["id"]
    assert web.post("/logout", follow_redirects=False).status_code == 303
    assert not terminal.get(sid).alive and terminal.get(sid).reason == "logout" and ssh_ptys[0].closed
