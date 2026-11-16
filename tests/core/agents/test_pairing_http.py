"""Connector pairing (install codes and device tokens), the public connector
endpoints, and the Projects page event socket."""
import threading
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import agent_hub, agent_http, db, hosts, host_events
from app.hosts import HostError
from tests.core.helpers import quick_user


# -- pairing -------------------------------------------------------------------

def test_an_install_code_is_shown_once_and_stored_only_as_a_hash(fresh_db):
    uid = quick_user()
    with patch.object(hosts.settings, "public_base_url", "https://fuse.test"):
        pairing = hosts.mint_pairing(uid)
    code = pairing["code"]
    assert code.startswith("fuse_") and code in pairing["install"]
    row = db.fetchone("SELECT code, code_hash FROM host_pairings WHERE id=?", (pairing["id"],))
    assert row["code"] == "" and row["code_hash"] == hosts.token_hash(code)
    pending = hosts.list_pending_pairings(uid)
    assert [(p["id"], p["code"], p["install"]) for p in pending] == [(pairing["id"], "", "")]


def test_a_code_pairs_one_machine_once(fresh_db):
    uid = quick_user()
    code = hosts.mint_pairing(uid)["code"]
    view, token = hosts.redeem_pairing(code, {"hostname": "laptop.local", "username": "ada", "os": "Windows"})
    assert (view["auth_type"], view["username"], view["agent_os"]) == ("agent", "ada", "windows")
    assert token.startswith("fa_")
    stored = db.fetchone("SELECT device_token_hash FROM user_hosts WHERE id=?", (view["id"],))["device_token_hash"]
    assert stored == hosts.token_hash(token) and token not in stored
    with pytest.raises(HostError) as again:
        hosts.redeem_pairing(code, {"hostname": "other"})
    assert again.value.status_code == 401
    assert len(hosts.list_bundle(uid)["hosts"]) == 1 and hosts.list_pending_pairings(uid) == []


def test_concurrent_redeems_of_one_code_pair_one_machine(fresh_db):
    uid = quick_user()
    code = hosts.mint_pairing(uid)["code"]
    barrier = threading.Barrier(2)
    outcomes = []

    def redeem(name):
        barrier.wait()
        try:
            outcomes.append(hosts.redeem_pairing(code, {"hostname": name})[1])
        except HostError:
            outcomes.append(None)

    threads = [threading.Thread(target=redeem, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(o is None for o in outcomes) == [False, True]
    assert len(hosts.list_bundle(uid)["hosts"]) == 1


def test_expired_cancelled_and_malformed_codes_are_refused(fresh_db):
    uid = quick_user()
    expired = hosts.mint_pairing(uid)
    db.execute("UPDATE host_pairings SET expires_at='2000-01-01T00:00:00Z' WHERE id=?", (expired["id"],))
    with pytest.raises(HostError, match="expired"):
        hosts.redeem_pairing(expired["code"], {})
    cancelled = hosts.mint_pairing(uid)
    assert hosts.cancel_pairing(uid, cancelled["id"])
    with pytest.raises(HostError):
        hosts.redeem_pairing(cancelled["code"], {})
    with pytest.raises(HostError):
        hosts.redeem_pairing("fuse_\nnot-a-code", {})
    assert hosts.list_bundle(uid)["hosts"] == []


def test_unused_codes_are_capped_and_another_user_cannot_cancel_them(fresh_db):
    uid, other = quick_user(), quick_user("other@core.test")
    made = [hosts.mint_pairing(uid) for _ in range(hosts.MAX_PENDING_PAIRINGS)]
    with pytest.raises(HostError, match="too many"):
        hosts.mint_pairing(uid)
    assert hosts.cancel_pairing(other, made[0]["id"]) is False
    assert len(hosts.list_pending_pairings(uid)) == hosts.MAX_PENDING_PAIRINGS


def test_an_undone_pairing_reopens_the_code(fresh_db):
    uid = quick_user()
    code = hosts.mint_pairing(uid)["code"]
    view, token = hosts.redeem_pairing(code, {"hostname": "laptop"})
    # hello_ok (the only copy of the token) never reached the connector.
    with patch.object(hosts, "_evict_pool", side_effect=RuntimeError("after commit")):
        with pytest.raises(RuntimeError):
            hosts.undo_pairing(uid, view["id"], code)
    assert hosts.host_by_device_token(token) is None
    again, _ = hosts.redeem_pairing(code, {"hostname": "laptop"})
    assert [h["id"] for h in hosts.list_bundle(uid)["hosts"]] == [again["id"]]


def test_a_connector_host_has_no_ssh_secret_and_is_refused_when_disabled(fresh_db):
    import asyncio
    import json

    from app import workspace

    uid = quick_user()
    view, _ = hosts.redeem_pairing(hosts.mint_pairing(uid)["code"], {"hostname": "box"})
    with pytest.raises(HostError):
        hosts.secret_plain(uid, view["id"])
    hosts.patch_host(uid, view["id"], {"disabled": True})
    text, rec = asyncio.run(workspace.execute("run_terminal_command", json.dumps({"command": "echo hi"}),
                                              user_id=uid, bound_host_id=view["id"], bound_cwd="/tmp"))
    assert rec["status"] == "error" and "disabled" in text


# -- public endpoints ----------------------------------------------------------

@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(agent_http.router)
    app.include_router(host_events.router)
    return TestClient(app)


def test_the_connect_socket_pairs_a_machine_end_to_end(fresh_db, client):
    uid = quick_user()
    code = hosts.mint_pairing(uid)["code"]
    with client.websocket_connect("/agent/v1/connect") as ws:
        ws.send_json({"v": 1, "t": "hello", "code": code, "hostname": "wsbox", "user": "ada", "ver": "1.0.0"})
        hello = ws.receive_json()
        assert hello["t"] == "hello_ok" and hello["token"].startswith("fa_")
        assert agent_hub.is_online(uid, hello["host_id"])
    assert int(hosts.host_by_device_token(hello["token"])["id"]) == hello["host_id"]
    assert hosts.list_pending_pairings(uid) == []


def test_installers_need_an_https_origin_and_bake_only_valid_codes(client):
    with patch.object(hosts.settings, "public_base_url", "http://fuse.test"):
        assert client.get("/install.sh").status_code == 503
    with patch.object(hosts.settings, "public_base_url", "https://fuse.test"):
        good = client.get("/i/fuse_abcdefgh12")
        assert good.status_code == 200 and 'CODE="${1:-fuse_abcdefgh12}"' in good.text
        assert "fuse_abcdefgh12" in client.get("/i/fuse_abcdefgh12.ps1").text
        assert client.get("/i/fuse_bad").status_code == 404
        assert client.get("/i/not_a_code").status_code == 404
    # A trailing newline must not become a multi-line default in the script.
    assert 'CODE="${1:-}"' in agent_http.install_script("fuse_abcdefgh12\n")


def test_dist_serves_only_known_binary_names(client):
    for name in ("../config.py", "..%2Fconfig.py", "fuse-agent-linux-amd64.sh", "VERSION"):
        assert client.get(f"/agent/dist/{name}").status_code == 404, name


def test_host_events_refuse_cross_origin_and_signed_out_sockets(fresh_db):
    from starlette.middleware.sessions import SessionMiddleware

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="core-test-only")
    app.include_router(host_events.router)
    client = TestClient(app)
    for headers, code in (({"origin": "https://evil.test"}, 4403), ({"origin": "http://testserver"}, 4401)):
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect("/hosts/events", headers=headers) as ws:
                ws.receive_json()
        assert closed.value.code == code


def test_host_events_keep_the_newest_updates_when_a_subscriber_lags():
    import asyncio

    async def body():
        host_events.start()
        queue = asyncio.Queue(maxsize=2)
        host_events._subscribers[7]["sock"] = queue
        try:
            for n in range(3):
                host_events.publish(7, {"n": n})
            host_events.publish(8, {"n": "other user"})
            await asyncio.sleep(0)  # publish hops onto the loop with call_soon_threadsafe
            return [queue.get_nowait()["n"] for _ in range(queue.qsize())]
        finally:
            host_events._subscribers.pop(7, None)
            host_events._loop = None

    assert asyncio.run(body()) == [1, 2]
