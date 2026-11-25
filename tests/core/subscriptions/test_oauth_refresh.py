"""OAuth refresh per backend: gated on OAUTH_REFRESH_ENABLED (schema ownership
turns it off on clones), a single-use refresh token is never posted twice
even by concurrent refreshes (one queued behind another finds the token
already renewed and posts nothing), rotated tokens land, and a revoked grant keeps
serving until its access token expires, is never re-posted, then disables.
Ownership itself is pinned in tests/core/usage/test_credentials.py."""
import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from app.config import settings
from app.sources import chatgpt, claude, cursor, grok, kimi, pool_pick, token_store
from tests.core.helpers import wait_until_async
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
    assert upstream.spent == ["rt-0"]
    extra = extra_of(cred)
    assert (extra["access_token"], extra["refresh_token"]) == ("at-1", "rt-1")
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


@pytest.mark.parametrize("refresh", [None, 123])
@pytest.mark.parametrize("kind", list(BACKENDS))
def test_an_answer_with_a_null_refresh_token_keeps_the_stored_one(fresh_db, monkeypatch, kind, refresh):
    cred = linked(kind)
    answer = httpx.Response(200, json={"access_token": "at-1", "refresh_token": refresh, "expires_in": 3600})
    with http(lambda request: answer, monkeypatch):
        assert run(BACKENDS[kind].refresh_one(row(cred))) is True
    assert (extra_of(cred)["access_token"], extra_of(cred)["refresh_token"]) == ("at-1", "rt-0")


@pytest.mark.parametrize("access", [None, 123, ""])
@pytest.mark.parametrize("kind", list(BACKENDS))
def test_a_200_without_a_usable_access_token_only_cools_the_account(fresh_db, monkeypatch, kind, access):
    cred = linked(kind)
    answer = httpx.Response(200, json={"access_token": access, "refresh_token": "rt-1", "expires_in": 3600})
    with http(lambda request: answer, monkeypatch):
        assert run(BACKENDS[kind].refresh_one(row(cred))) is False
    assert (extra_of(cred)["access_token"], extra_of(cred)["refresh_token"]) == ("at-0", "rt-0")
    assert row(cred)["disabled"] == 0 and pool_pick.cooling_down(cred)


@pytest.mark.parametrize("kind", list(BACKENDS))
def test_a_caller_cancelled_after_the_token_post_still_persists_the_rotated_pair(fresh_db, monkeypatch, kind):
    cred = linked(kind)

    async def go():
        landed, release = asyncio.Event(), asyncio.Event()

        async def token(request):
            landed.set()
            await release.wait()
            return httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600})

        with http(token, monkeypatch):
            task = asyncio.create_task(BACKENDS[kind].refresh_one(row(cred)))
            await landed.wait()
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await wait_until_async(lambda: extra_of(cred)["refresh_token"] == "rt-1")

    run(go())
    assert extra_of(cred)["access_token"] == "at-1"


def test_grok_cancelled_during_its_profile_lookup_still_persists_the_rotated_pair(fresh_db, monkeypatch):
    # No email: the refresh looks up the profile after the token POST. Ending
    # the loop then (a shutdown) cancels every task, the shielded refresh too.
    cred = account("grok", user(), {"access_token": "at-0", "refresh_token": "rt-0", "expires_at": iso(60)})

    async def handler(request):
        if "userinfo" in request.url.path:
            await asyncio.Event().wait()
        return httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600})

    with http(handler, monkeypatch) as calls:
        async def go():
            asyncio.create_task(grok.refresh_one(row(cred)))
            await wait_until_async(lambda: any("userinfo" in r.url.path for r in calls.requests))

        run(go())
    assert (extra_of(cred)["access_token"], extra_of(cred)["refresh_token"]) == ("at-1", "rt-1")


def test_a_failed_save_of_the_rotated_pair_is_retried_until_it_lands(fresh_db, monkeypatch):
    cred = linked("kimi")
    real, failures, sleeps = kimi._cas_update_tokens, [RuntimeError("database went away")], []

    def flaky(*args, **kwargs):
        if failures:
            raise failures.pop()
        return real(*args, **kwargs)

    monkeypatch.setattr(kimi, "_cas_update_tokens", flaky)
    monkeypatch.setattr(token_store, "time", SimpleNamespace(sleep=sleeps.append))
    with http(lambda request: httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-1",
                                                        "expires_in": 3600}), monkeypatch):
        assert run(kimi.refresh_one(row(cred))) is True
    assert sleeps == [token_store.PERSIST_BACKOFF[0]]
    assert (extra_of(cred)["access_token"], extra_of(cred)["refresh_token"]) == ("at-1", "rt-1")


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


def test_a_rejected_cursor_api_key_is_disabled_on_its_second_strike_and_a_403_never_strikes(fresh_db, monkeypatch):
    sid = cursor.ensure_cursor_source()
    expired = jwt({"sub": "u1", "exp": 1})
    forbidden = cursor.store_tokens(sid, expired, "", user("a@core.test"), api_key="key_forbidden_123456")
    rejected = cursor.store_tokens(sid, jwt({"sub": "u2", "exp": 1}), "", user("b@core.test"), api_key="key_rejected_1234567")

    def handler(request):
        bad = "key_rejected" in request.headers.get("authorization", "")
        return httpx.Response(401 if bad else 403, text="no")

    with http(handler, monkeypatch) as calls:
        for _ in range(3):
            assert run(cursor.refresh_one(row(forbidden))) is False
        assert run(cursor.refresh_one(row(rejected))) is False
        assert row(rejected)["disabled"] == 0 and pool_pick.cooling_down(rejected)
        assert run(cursor.refresh_one(row(rejected))) is False
    assert len(calls.requests) == 5
    assert row(rejected)["disabled"] == 1
    assert row(forbidden)["disabled"] == 0 and pool_pick.cooling_down(forbidden)


def test_cursor_keeps_a_refresh_token_it_rotates(fresh_db, monkeypatch):
    cred = cursor.store_tokens(cursor.ensure_cursor_source(), jwt({"sub": "u1"}), "refresh-token-1234", user())
    renewed = jwt({"sub": "u1", "n": 2})
    with http(lambda request: httpx.Response(200, json={"access_token": renewed, "refresh_token": "refresh-token-5678"}),
              monkeypatch) as calls:
        assert run(cursor.refresh_one(row(cred))) is True
        assert run(cursor.refresh_one(row(cred))) is True
    assert [json.loads(r.content)["refresh_token"] for r in calls.requests] == ["refresh-token-1234", "refresh-token-5678"]
    assert extra_of(cred)["refresh_token"] == "refresh-token-5678"
