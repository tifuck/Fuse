"""Only the donor or an admin can remove a pool credential.

Every provider route `/{sources,pool}/<kind>/{cred_id}/revoke` is found on
the web router and checked the same way: another signed-in user is refused
and the credential stays enabled; the admin removes it.
"""
import importlib
import os
import re

import pytest
from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app import accounts, db, web
from app.config import settings
from app.security import limiter

pytestmark = [
    pytest.mark.skipif(os.environ.get("FUSE_CHAT_TEST_MODE") != "1", reason="requires disposable MariaDB fixture"),
    pytest.mark.usefixtures("isolated_db"),
]

_PASSWORD = "password12"
_REVOKE = re.compile(r"^/(?:sources|pool)/(\w+)/\{cred_id\}/revoke$")
_REVOKE_PATHS = sorted(
    route.path for route in iter_route_contexts(web.router.routes) if _REVOKE.match(route.path or "")
)
_POOLS = {"grok", "chatgpt", "claude", "cursor", "devin", "kimi", "mimo", "nanogpt", "openrouter", "refuseless", "zai"}


@pytest.fixture(scope="module")
def client(isolated_db):
    accounts.create_user("root@revoke.invalid", _PASSWORD, admin=True)
    accounts.create_user("alice@revoke.invalid", _PASSWORD, admin=False)
    accounts.create_user("bob@revoke.invalid", _PASSWORD, admin=False)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key=settings.app_secret, https_only=True, same_site="strict")
    app.include_router(web.router)
    with TestClient(app, base_url="https://testserver") as http:
        yield http


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


def _disabled(cred_id):
    return db.fetchone("SELECT disabled FROM credentials WHERE id=?", (cred_id,))["disabled"]


def test_every_pool_has_a_revoke_route():
    assert _POOLS <= {_REVOKE.match(path).group(1) for path in _REVOKE_PATHS}


@pytest.mark.parametrize("path", _REVOKE_PATHS)
def test_only_the_donor_or_an_admin_can_revoke_a_pool_credential(client, path):
    kind = _REVOKE.match(path).group(1)
    source_id = getattr(importlib.import_module(f"app.sources.{kind}"), f"ensure_{kind}_source")()
    alice_id = accounts.user_by_email("alice@revoke.invalid")["id"]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO credentials(source_id, label, secret, extra, donated_by, created_at) VALUES (?,?,?,?,?,?)",
            (source_id, "alice", "not-a-real-secret", "{}", alice_id, db.now()),
        )
        cred_id = db.last_id(conn)

    _login(client, "bob@revoke.invalid")
    refused = client.post(path.format(cred_id=cred_id), follow_redirects=False)
    assert refused.status_code == 403, refused.text
    assert _disabled(cred_id) == 0

    _login(client, "root@revoke.invalid")
    removed = client.post(path.format(cred_id=cred_id), follow_redirects=False)
    assert removed.status_code == 303, removed.text
    assert _disabled(cred_id) == 1
