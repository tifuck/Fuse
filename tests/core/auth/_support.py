"""HTTP helpers for auth tests: the real route table behind a TestClient, and
real signed session cookies.

A signed-in session is minted the way /login mints it (accounts.start_session)
and signed the way Starlette's SessionMiddleware signs it, so every request
still goes through current_user/session_user. Tests that are about the sign-in
itself post the real /login form instead.
"""
import base64
import json
import re
import secrets
from contextlib import contextmanager

import itsdangerous
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app import accounts
from app.config import settings

PASSWORD = "password12"


def _signer():
    return itsdangerous.TimestampSigner(str(settings.app_secret))


def sign_session(data: dict) -> str:
    return _signer().sign(base64.b64encode(json.dumps(data).encode("utf-8"))).decode("utf-8")


def read_session(cookie: str) -> dict:
    return json.loads(base64.b64decode(_signer().unsign(cookie)))


def build_app():
    """app.main's routes and exception handler under the session middleware it
    uses, without its lifespan (no pools, loops or recovery)."""
    from app import main

    # No docs routes of its own, as in app.main: FastAPI's default /docs would
    # shadow the dashboard's.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=settings.app_secret, https_only=True, same_site="lax")
    app.router.routes.extend(main.app.routes)
    app.add_exception_handler(StarletteHTTPException, main.http_errors)
    return app


@contextmanager
def web_client(app=None):
    with TestClient(app or build_app(), base_url="https://testserver") as client:
        yield client


def cookie(client, name="session"):
    values = [c.value for c in client.cookies.jar if c.name == name]
    return values[-1] if values else None


def set_session(client, data: dict) -> dict:
    """Replace the client's session cookie; a CSRF token goes into both the
    session and the x-csrf-token header so writes reach the route itself."""
    data = dict(data)
    data.setdefault("csrf", secrets.token_urlsafe(16))
    client.cookies.clear()
    client.cookies.set("session", sign_session(data))
    client.headers["x-csrf-token"] = data["csrf"]
    return data


def anonymous(client) -> dict:
    return set_session(client, {})


def sign_in(client, user_id: int) -> dict:
    """A session exactly as a successful /login leaves it."""
    data = {}
    accounts.start_session(data, accounts.get_user(user_id))
    return set_session(client, data)


def login(client, email, password=PASSWORD, next_path=""):
    """Post the real sign-in form; returns the response. On success the
    client's header carries the rotated CSRF token of the new session."""
    token = anonymous(client)["csrf"]
    client.headers.pop("x-csrf-token", None)
    form = {"email": email, "password": password, "csrf_token": token}
    if next_path:
        form["next"] = next_path
    res = client.post("/login", data=form, follow_redirects=False)
    session = read_session(cookie(client))
    if session.get("csrf"):
        client.headers["x-csrf-token"] = session["csrf"]
    return res


def refused(res, home="/login") -> bool:
    """401/403, or the redirect a dashboard page answers instead."""
    if res.status_code in (401, 403):
        return True
    return res.status_code == 303 and res.headers.get("location", "").split("?")[0] == home


_PARAM = re.compile(r"\{[^}]+\}")


def dummy_path(path: str) -> str:
    return _PARAM.sub(lambda m: "x" if m.group(0).endswith(":path}") else "1", path)


def call_route(client, method, path):
    """Request a route the way an attacker would, filling any form or query
    field FastAPI validates before the handler, so the answer seen is the
    handler's own and not a 422."""
    params, data, files = {}, {}, {}
    res = None
    for _ in range(4):
        res = client.request(method, dummy_path(path), params=params, data=data or None, files=files or None,
                             follow_redirects=False)
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
                files[name] = ("x.txt", b"x")
                data.pop(name)
            elif where == "body":
                data[name] = "1"
    return res


def http_routes():
    """[(method, path)] for every HTTP route, plus mount paths and websocket paths."""
    from fastapi.routing import APIWebSocketRoute, iter_route_contexts
    from starlette.routing import Mount, WebSocketRoute

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
