"""Every route refuses an anonymous caller, and every /admin route a signed-in
non-admin, unless it is listed in PUBLIC below.

Routes are discovered from app.main, not listed by hand: a new route is
covered the day it is added, and either refuses or is made public on purpose
by adding it here. Path parameters get dummy values; a form or query field
FastAPI requires before the handler runs gets a dummy too, so the refusal
seen is the handler's, not the validator's. Anonymous requests carry a valid
CSRF token, so a 403 is the route's own refusal, not csrf_guard's.
"""
import os
import re

import pytest
from fastapi import FastAPI
from fastapi.routing import APIWebSocketRoute, iter_route_contexts
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Mount, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from app import accounts
from app.config import settings
from app.security import limiter
from tests.mariadb_fixture import isolated_database

pytestmark = pytest.mark.skipif(
    os.environ.get("FUSE_CHAT_TEST_MODE") != "1", reason="requires disposable MariaDB fixture"
)

_PASSWORD = "password12"

# Reachable without signing in, by design.
PUBLIC = {
    ("GET", "/login"),
    ("POST", "/login"),
    ("GET", "/register"),
    ("POST", "/register"),
    ("GET", "/"),  # redirects to /chat
    ("GET", "/hosts/scheduled"),  # legacy redirects to /tasks
    ("GET", "/projects/scheduled"),
    ("GET", "/docs"),
    ("GET", "/install-opencode.sh"),
    ("GET", "/sw.js"),
    ("GET", "/build"),
    ("GET", "/favicon.ico"),
    # Artifact share links; owner-only operations are pinned in test_authz_matrix.
    ("GET", "/a/{public_id}"),
    ("GET", "/a/{public_id}/content"),
    ("GET", "/a/{public_id}/meta"),
    # Connector install and downloads; the connector itself authenticates
    # with its own token on /agent/v1/connect.
    ("GET", "/agent/v1/manifest"),
    ("GET", "/agent/dist/{name}"),
    ("GET", "/install.sh"),
    ("GET", "/install.ps1"),
    ("GET", "/i/{code}"),
    ("GET", "/v1/health"),
}
PUBLIC_MOUNTS = {"/static", "/desktop"}
AGENT_SOCKET = "/agent/v1/connect"


@pytest.fixture()
def client():
    from app import main

    with isolated_database():
        accounts.create_user("root@routes.invalid", _PASSWORD, admin=True)
        accounts.create_user("bob@routes.invalid", _PASSWORD, admin=False)
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key=settings.app_secret, https_only=True, same_site="lax")
        app.router.routes.extend(main.app.routes)
        app.add_exception_handler(StarletteHTTPException, main.http_errors)
        with TestClient(app, base_url="https://testserver") as http:
            yield http


def _routes():
    from app import main

    routes, mounts, sockets = [], [], []
    for route in iter_route_contexts(main.app.routes):
        original = route.original_route
        if isinstance(original, Mount):
            mounts.append(original.path)
        elif isinstance(original, (WebSocketRoute, APIWebSocketRoute)):
            sockets.append(original.path)
        else:
            routes.extend((method, route.path) for method in sorted(route.methods - {"HEAD"}))
    return routes, mounts, sockets


def _dummy(path):
    return re.sub(r"\{[^}]+\}", lambda m: "x" if m.group(0).endswith(":path}") else "1", path)


def _anonymous(client):
    limiter._hits.clear()
    client.cookies.clear()
    page = client.get("/login").text
    client.headers["x-csrf-token"] = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


def _login(client, email):
    limiter._hits.clear()
    client.cookies.clear()
    client.headers.pop("x-csrf-token", None)
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text).group(1)
    posted = client.post(
        "/login", data={"email": email, "password": _PASSWORD, "csrf_token": token}, follow_redirects=False
    )
    assert posted.status_code == 303, posted.text
    meta = re.search(r'name="csrf-token" content="([^"]+)"', client.get("/chat").text)
    client.headers["x-csrf-token"] = meta.group(1)


def _call(client, method, path):
    """The route's answer; required fields FastAPI checks first get dummies."""
    params, data, files = {}, {}, {}
    for _ in range(4):
        res = client.request(
            method, _dummy(path), params=params, data=data or None, files=files or None, follow_redirects=False
        )
        if res.status_code != 422:
            return res
        detail = res.json().get("detail")
        fields = [e["loc"] for e in detail if len(e.get("loc") or ()) == 2] if isinstance(detail, list) else []
        if not fields:
            return res
        for where, name in fields:
            if where == "query":
                params[name] = "1"
            elif where == "body" and name in data:
                # The dummy string was refused: the field is an upload.
                files[name] = ("x.txt", b"x")
                data.pop(name)
            elif where == "body":
                data[name] = "1"
    return res


def _refused(res, home):
    if res.status_code in (401, 403):
        return True
    return res.status_code == 303 and res.headers.get("location", "").split("?")[0] == home


def test_every_non_public_route_refuses_an_anonymous_caller(client):
    routes, mounts, _sockets = _routes()
    assert set(mounts) == PUBLIC_MOUNTS
    # A stale entry would hide nothing today but could whitelist a route
    # that later reuses the path.
    assert PUBLIC <= set(routes), sorted(PUBLIC - set(routes))
    open_routes = []
    for method, path in routes:
        if (method, path) in PUBLIC:
            continue
        _anonymous(client)
        res = _call(client, method, path)
        if not _refused(res, "/login"):
            open_routes.append((method, path, res.status_code, res.text[:120]))
    assert not open_routes, open_routes


def test_every_websocket_refuses_an_anonymous_caller(client):
    _routes_, _mounts, sockets = _routes()
    assert AGENT_SOCKET in sockets
    for path in sockets:
        _anonymous(client)
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(
                "wss://testserver" + _dummy(path), headers={"origin": "https://testserver"}
            ) as ws:
                if path == AGENT_SOCKET:
                    # The connector authenticates in its first frame.
                    ws.send_json({"v": 1, "t": "hello", "token": "not-a-device-token"})
                    assert ws.receive_json()["code"] == "auth"
                ws.receive_json()
        # 4401 is the auth refusal; a wrong origin closes with another code.
        assert closed.value.code == 4401, path


def test_every_admin_route_refuses_a_signed_in_non_admin(client):
    routes, _mounts, _sockets = _routes()
    admin = [(method, path) for method, path in routes if path.startswith("/admin")]
    assert admin
    _login(client, "bob@routes.invalid")
    allowed = []
    for method, path in admin:
        limiter._hits.clear()
        res = _call(client, method, path)
        if not _refused(res, "/"):
            allowed.append((method, path, res.status_code, res.text[:120]))
    assert not allowed, allowed
    # Positive control: the session is still bob's, signed in, not an admin.
    assert client.get("/chat", follow_redirects=False).status_code == 200
