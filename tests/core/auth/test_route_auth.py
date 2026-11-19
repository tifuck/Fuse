"""Every route refuses an anonymous caller, and every /admin route a signed-in
non-admin, unless it is listed in PUBLIC below.

Routes are discovered from app.main, not listed by hand: a route added
tomorrow is covered the day it lands, and either refuses or is made public on
purpose by adding it here. Anonymous callers carry a valid CSRF token, so a
403 is the route's own refusal and not csrf_guard's.
"""
import pytest
from starlette.websockets import WebSocketDisconnect

from app import db
from tests.core.auth._support import (
    anonymous, call_route, dummy_path, http_routes, refused, sign_in,
)
from tests.core.helpers import quick_user

# Reachable without signing in, by design.
PUBLIC = {
    ("GET", "/login"),
    ("POST", "/login"),
    ("GET", "/register"),
    ("POST", "/register"),
    ("GET", "/"),  # redirects to /chat
    ("GET", "/hosts/scheduled"),  # old links, redirect to /tasks
    ("GET", "/projects/scheduled"),
    ("GET", "/docs"),
    ("GET", "/install-opencode.sh"),
    ("GET", "/sw.js"),  # the service worker must load signed out
    ("GET", "/build"),
    ("GET", "/favicon.ico"),
    # Artifact share links: a public artifact is viewable by anyone; owner-only
    # operations are pinned in test_isolation.
    ("GET", "/a/{public_id}"),
    ("GET", "/a/{public_id}/content"),
    ("GET", "/a/{public_id}/meta"),
    # Connector install and downloads; the connector authenticates with its
    # own device token on /agent/v1/connect.
    ("GET", "/agent/v1/manifest"),
    ("GET", "/agent/dist/{name}"),
    ("GET", "/install.sh"),
    ("GET", "/install.ps1"),
    ("GET", "/i/{code}"),
    ("GET", "/v1/health"),
}
PUBLIC_MOUNTS = {"/static", "/desktop"}
AGENT_SOCKET = "/agent/v1/connect"


def test_every_non_public_route_refuses_an_anonymous_caller(web):
    # ~250 routes in one test (~0.5 s): one request each is the point.
    routes, mounts, _sockets = http_routes()
    assert set(mounts) == PUBLIC_MOUNTS
    # A stale entry hides nothing today but would whitelist a route that later
    # reuses the path.
    assert PUBLIC <= set(routes), sorted(PUBLIC - set(routes))
    users_before = db.fetchone("SELECT COUNT(*) AS n FROM users")["n"]
    open_routes = []
    for method, path in routes:
        if (method, path) in PUBLIC:
            continue
        anonymous(web)
        res = call_route(web, method, path)
        if not refused(res, "/login"):
            open_routes.append((method, path, res.status_code, res.text[:120]))
    assert not open_routes, open_routes
    assert db.fetchone("SELECT COUNT(*) AS n FROM users")["n"] == users_before


def test_a_write_without_the_session_csrf_token_is_refused(web):
    uid = quick_user("alice@core.test")
    sign_in(web, uid)
    assert web.post("/chat/threads", json={"model": "offline", "command_id": "c0"}).status_code == 201
    for n, token in enumerate(("", "not-the-token")):
        web.headers["x-csrf-token"] = token
        refused_write = web.post("/chat/threads", json={"model": "offline", "command_id": f"c{n + 1}"})
        assert refused_write.status_code == 403, token
    assert db.fetchone("SELECT COUNT(*) AS n FROM chat_threads WHERE user_id=?", (uid,))["n"] == 1


# Writes authenticated by something a browser does not send on its own (an
# API key), not by the session cookie.
CSRF_EXEMPT = {("POST", "/v1/chat/completions")}


def test_every_write_refuses_a_signed_in_caller_without_the_csrf_token(web):
    routes, _mounts, _sockets = http_routes()
    writes = [(method, path) for method, path in routes if method not in ("GET", "HEAD", "OPTIONS")]
    assert CSRF_EXEMPT <= set(writes), sorted(CSRF_EXEMPT - set(writes))
    admin = quick_user("root@core.test", admin=True)
    unguarded = []
    for method, path in writes:
        if (method, path) in CSRF_EXEMPT:
            continue
        sign_in(web, admin)
        web.headers.pop("x-csrf-token")
        res = call_route(web, method, path)
        if path in ("/login", "/register"):
            # A signed-in repeat submit goes to the app; nothing is processed.
            ok = res.status_code == 303 and res.headers["location"] == "/chat"
        else:
            ok = res.status_code == 403 and "CSRF" in res.text
        if not ok:
            unguarded.append((method, path, res.status_code, res.text[:120]))
    assert not unguarded, unguarded


def test_every_websocket_refuses_an_anonymous_caller(web):
    _routes, _mounts, sockets = http_routes()
    assert AGENT_SOCKET in sockets
    for path in sockets:
        anonymous(web)
        with pytest.raises(WebSocketDisconnect) as closed:
            with web.websocket_connect("wss://testserver" + dummy_path(path),
                                       headers={"origin": "https://testserver"}) as ws:
                if path == AGENT_SOCKET:
                    # The connector authenticates in its first frame.
                    ws.send_json({"v": 1, "t": "hello", "token": "not-a-device-token"})
                    assert ws.receive_json()["code"] == "auth"
                ws.receive_json()
        # 4401 is the auth refusal; a wrong origin closes with another code.
        assert closed.value.code == 4401, path


def test_every_admin_route_refuses_a_signed_in_non_admin(web):
    routes, _mounts, _sockets = http_routes()
    admin_routes = [(method, path) for method, path in routes if path.startswith("/admin")]
    assert len(admin_routes) > 20
    member = quick_user("member@core.test")
    admin = quick_user("root@core.test", admin=True)
    sign_in(web, member)
    allowed = []
    for method, path in admin_routes:
        res = call_route(web, method, path)
        if not refused(res, "/"):
            allowed.append((method, path, res.status_code, res.text[:120]))
    assert not allowed, allowed
    # Positive controls: the session is still a signed-in member, and the same
    # pages open for an admin.
    assert web.get("/chat/threads").status_code == 200
    sign_in(web, admin)
    assert web.get("/admin/users", follow_redirects=False).status_code == 200
