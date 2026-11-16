"""OAuth refresh per backend: gated on OAUTH_REFRESH_ENABLED (schema ownership
turns it off on clones), a single-use refresh token is never posted twice
even by concurrent refreshes, rotated tokens land, and a revoked grant keeps
serving until its access token expires, is never re-posted, then disables.
Ownership itself is pinned in tests/core/usage/test_credentials.py."""
import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from app.config import settings
from app.sources import chatgpt, claude, cursor, grok, kimi, pool_pick
from tests.core.subscriptions._support import account, extra_of, http, iso, jwt, row, run, set_extra, user

BACKENDS = {"grok": grok, "kimi": kimi, "chatgpt": chatgpt, "claude": claude}


def linked(kind, expires_in=60):
    extra = {"access_token": "at-0", "refresh_token": "rt-0", "expires_at": iso(expires_in), "email": "d@x.test"}
    if kind == "kimi":
        extra.update(auth_kind="oauth", region="global", device_id="dev-1")
    if kind == "claude":
        extra.update(auth_kind="oauth")
    return account(kind, user(), extra)


def posted_token(request):
    if request.headers.get("content-type", "").startswith("application/json"):
        return json.loads(request.content)["refresh_token"]
    return parse_qs(request.content.decode())["refresh_token"][0]


class Rotating:
    """A token endpoint that rotates the pair and refuses a spent token."""

    def __init__(self):
        self.spent = []

    def __call__(self, request):
        token = posted_token(request)
        if token in self.spent:
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "refresh token reused"})
        self.spent.append(token)
        n = len(self.spent)
        return httpx.Response(200, json={"access_token": f"at-{n}", "refresh_token": f"rt-{n}", "expires_in": 3600})


@pytest.mark.parametrize("kind", [*BACKENDS, "cursor"])
def test_refresh_never_runs_where_this_database_does_not_own_the_tokens(fresh_db, monkeypatch, kind):
    monkeypatch.setattr(settings, "oauth_refresh_enabled", False)
    if kind == "cursor":
        cred = cursor.store_tokens(cursor.ensure_cursor_source(), jwt({"sub": "u1"}), "refresh-token-1234", user())
        mod = cursor
    else:
        cred, mod = linked(kind, expires_in=-60), BACKENDS[kind]
    with http(lambda request: pytest.fail(f"refresh posted to {request.url}"), monkeypatch) as calls:
        assert run(mod.refresh_one(row(cred))) is False
        if kind != "cursor":
            run(mod.refresh_due())
    assert calls.requests == []


@pytest.mark.parametrize("kind", list(BACKENDS))
def test_concurrent_refreshes_never_post_the_same_single_use_token_twice(fresh_db, monkeypatch, kind):
    mod = BACKENDS[kind]
    cred = linked(kind)
    upstream = Rotating()

    async def both():
        return await asyncio.gather(mod.refresh_one(row(cred)), mod.refresh_one(row(cred)))

    with http(upstream, monkeypatch):
        results = run(both())

    assert results == [True, True]
    assert upstream.spent == ["rt-0", "rt-1"]
    extra = extra_of(cred)
    assert (extra["access_token"], extra["refresh_token"]) == ("at-2", "rt-2")
    assert row(cred)["disabled"] == 0


@pytest.mark.parametrize("kind", list(BACKENDS))
def test_a_revoked_grant_keeps_serving_until_expiry_and_is_never_posted_again(fresh_db, monkeypatch, kind):
    mod = BACKENDS[kind]
    cred = linked(kind, expires_in=3600)
    revoked = httpx.Response(400, json={"error": "invalid_grant", "error_description": "Refresh token revoked"})
    with http(lambda request: revoked, monkeypatch) as calls:
        assert run(mod.refresh_one(row(cred))) is False
        assert run(mod.refresh_one(row(cred))) is False
    assert len(calls.requests) == 1
    assert row(cred)["disabled"] == 0

    expired = {**extra_of(cred), "expires_at": iso(-60)}
    set_extra(cred, expired)
    with http(lambda request: revoked, monkeypatch) as calls:
        assert run(mod.refresh_one(row(cred))) is False
    assert calls.requests == [] and row(cred)["disabled"] == 1


@pytest.mark.parametrize("kind", list(BACKENDS))
def test_a_transient_refresh_failure_only_cools_the_account(fresh_db, monkeypatch, kind):
    mod = BACKENDS[kind]
    cred = linked(kind, expires_in=-60)
    with http(lambda request: httpx.Response(503, json={"error": "temporarily_unavailable"}), monkeypatch):
        assert run(mod.refresh_one(row(cred))) is False
    assert row(cred)["disabled"] == 0 and extra_of(cred)["refresh_token"] == "rt-0"
    assert pool_pick.cooling_down(cred)


def test_cursor_renews_with_its_non_rotating_refresh_token_and_logs_out_when_told(fresh_db, monkeypatch):
    sid = cursor.ensure_cursor_source()
    cred = cursor.store_tokens(sid, jwt({"sub": "u1"}), "refresh-token-1234", user())
    renewed = jwt({"sub": "u1", "n": 2})
    with http(lambda request: httpx.Response(200, json={"access_token": renewed}), monkeypatch) as calls:
        assert run(cursor.refresh_one(row(cred))) is True
    assert json.loads(calls.requests[0].content)["refresh_token"] == "refresh-token-1234"
    assert str(calls.requests[0].url) == "https://api2.cursor.sh/oauth/token"
    assert (extra_of(cred)["access_token"], extra_of(cred)["refresh_token"]) == (renewed, "refresh-token-1234")

    with http(lambda request: httpx.Response(200, json={"shouldLogout": True}), monkeypatch):
        assert run(cursor.refresh_one(row(cred))) is False
    assert row(cred)["disabled"] == 1
