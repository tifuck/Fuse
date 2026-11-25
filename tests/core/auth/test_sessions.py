"""Sign-in, sessions and their end: the login form, session rotation, server-side
logout, password-change revocation, lifetime limits and the auth rate limits."""
import time
from unittest.mock import patch

import pytest

from app import accounts, crypto, db, vault
from app.config import settings
from app.security import RateLimiter, limiter
from tests.core.auth._support import (
    PASSWORD, anonymous, cookie, login, main_client, read_session, session_options, set_session, sign_in, sign_session,
)
from tests.core.helpers import make_user, quick_user

NEW_PASSWORD = "newpass12345"


def _signed_in(client) -> bool:
    return client.get("/chat/threads", follow_redirects=False).status_code == 200


def test_login_starts_a_fresh_session_bound_to_the_account(web):
    uid = make_user("ada@core.test")
    # A session planted before sign-in (fixation) must not survive it.
    planted = set_session(web, {"sid": "planted-sid"})
    web.headers.pop("x-csrf-token", None)
    form = {"email": "Ada@Core.Test", "password": PASSWORD, "csrf_token": planted["csrf"]}
    assert web.post("/login", data=form, follow_redirects=False).status_code == 303
    session = read_session(cookie(web))
    assert session["uid"] == uid and session["ph"]
    assert session["sid"] != "planted-sid" and session["csrf"] != planted["csrf"]
    web.headers["x-csrf-token"] = session["csrf"]
    assert _signed_in(web)
    # The login unlocked the vault and handed out a keep-cookie for it.
    assert vault.unlocked(uid) and cookie(web, vault.KEEP_COOKIE)


@pytest.mark.parametrize("email, password", [
    ("ada@core.test", "wrong-password"),
    ("nobody@core.test", PASSWORD),
    ("ada@core.test", "x" * 300),
])
def test_a_failed_login_signs_nobody_in(web, email, password):
    uid = make_user("ada@core.test")
    res = login(web, email, password)
    assert res.status_code == 401
    assert "uid" not in read_session(cookie(web))
    assert not _signed_in(web)
    assert not vault.unlocked(uid)


def test_a_repeated_login_submit_goes_to_the_app_and_a_bad_token_is_still_refused(web):
    make_user("ada@core.test")
    token = anonymous(web)["csrf"]
    web.headers.pop("x-csrf-token", None)
    form = {"email": "ada@core.test", "password": PASSWORD, "csrf_token": token}
    assert web.post("/login", data=form, follow_redirects=False).status_code == 303
    # The first submit rotated the token this form still carries.
    again = web.post("/login", data=form, follow_redirects=False)
    assert again.status_code == 303 and again.headers["location"] == "/chat"
    anonymous(web)
    web.headers.pop("x-csrf-token", None)
    assert web.post("/login", data=form, follow_redirects=False).status_code == 403


@pytest.mark.parametrize("target, landed", [
    ("/chat?t=abc123", "/chat?t=abc123"),
    ("//evil.example", "/chat"),
    ("/\\evil.example", "/chat"),
    ("https://evil.example/", "/chat"),
])
def test_login_returns_to_a_deep_link_only_on_this_site(web, target, landed):
    make_user("ada@core.test")
    res = login(web, "ada@core.test", next_path=target)
    assert res.status_code == 303 and res.headers["location"] == landed


def test_a_signed_out_deep_link_survives_the_sign_in_redirect(web):
    anonymous(web)
    sent = web.get("/chat?t=abc123", follow_redirects=False)
    assert sent.status_code == 303 and sent.headers["location"] == "/login?next=%2Fchat%3Ft%3Dabc123"


def test_logout_revokes_the_session_everywhere_and_locks_the_vault(web):
    uid = make_user("ada@core.test")
    login(web, "ada@core.test")
    stolen_session, stolen_keep = cookie(web), cookie(web, vault.KEEP_COOKIE)
    csrf = web.headers["x-csrf-token"]
    assert web.post("/logout", follow_redirects=False).status_code == 303
    assert not vault.unlocked(uid)

    # A copy of the cookie taken before logout no longer authenticates, and
    # its keep-cookie cannot reload the DEK logout wiped.
    web.cookies.clear()
    web.cookies.set("session", stolen_session)
    web.cookies.set(vault.KEEP_COOKIE, stolen_keep)
    web.headers["x-csrf-token"] = csrf
    assert not _signed_in(web)
    assert not vault.unlocked(uid)

    # Signing in again is a new, unrevoked session.
    login(web, "ada@core.test")
    assert _signed_in(web)


def test_logout_closes_only_its_own_projects_sockets(web, monkeypatch):
    from app import host_events

    class Socket:
        def __init__(self, session):
            self.session, self.closed = session, None

        async def close(self, code=1000):
            self.closed = code

    uid = quick_user("ada@core.test")
    other = Socket(dict(sign_in(web, uid)))
    mine = Socket(dict(sign_in(web, uid)))
    monkeypatch.setitem(host_events._subscribers, uid, {other: None, mine: None})
    assert web.post("/logout", follow_redirects=False).status_code == 303
    assert mine.closed == 4401 and other.closed is None


@pytest.mark.parametrize("deleted, later, code", [(False, 1801, 1012), (True, 20, 4401)])
def test_a_chat_stream_that_never_runs_dry_still_hits_the_cap_and_the_session_check(web, monkeypatch, deleted, later,
                                                                                     code):
    from types import SimpleNamespace

    from starlette.websockets import WebSocketDisconnect

    from app import chat_events, chat_store

    uid = quick_user("ada@core.test")
    admin = quick_user("root@core.test", admin=True)
    sign_in(web, uid)
    clock = [0]

    def events_after(uid, cursor, limit):
        # Every read has rows; time passes (and the account goes) mid-stream.
        if cursor == 3:
            if deleted:
                accounts.delete_user(uid, actor_id=admin)
            clock[0] = later
        return [{"id": cursor + 1, "type": "noop", "thread_id": None, "payload": {}}]

    monkeypatch.setattr(chat_events, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(chat_events, "replay_valid", lambda uid, cursor: True)
    monkeypatch.setattr(chat_store, "events_after", events_after)
    with pytest.raises(WebSocketDisconnect) as closed:
        with web.websocket_connect("wss://testserver/chat/events?cursor=0",
                                   headers={"origin": "https://testserver"}) as ws:
            for _ in range(200):
                ws.receive_json()
    assert closed.value.code == code


def test_health_shows_failure_detail_only_to_a_live_admin_session(web, monkeypatch):
    from app import main

    monkeypatch.setattr(main.app.state, "refresh_errors", ["RuntimeError in refresh"], raising=False)
    sign_in(web, quick_user("bob@core.test"))
    background = web.get("/v1/health").json()["background"]
    assert "errors" not in background and background["error_count"] == 1
    uid = quick_user("ada@core.test", admin=True)
    session = sign_in(web, uid)
    assert web.get("/v1/health").json()["background"]["errors"] == ["RuntimeError in refresh"]
    accounts.revoke_session(session)
    set_session(web, session)
    background = web.get("/v1/health").json()["background"]
    assert "errors" not in background and background["error_count"] == 1


def test_a_password_change_logs_out_every_other_session_but_not_the_changer(web):
    uid = make_user("ada@core.test")
    bystander = sign_in(web, uid)
    login(web, "ada@core.test")
    changed = web.post("/account/password", json={"old_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert changed.status_code == 200, changed.text
    assert _signed_in(web)
    assert accounts.login("ada@core.test", NEW_PASSWORD)["id"] == uid
    assert accounts.login("ada@core.test", PASSWORD) is None

    set_session(web, bystander)
    kicked = web.get("/chat", follow_redirects=False)
    assert kicked.status_code == 303 and kicked.headers["location"] == "/login"


def test_a_wrong_old_password_or_a_short_new_one_changes_nothing(web):
    uid = make_user("ada@core.test")
    sign_in(web, uid)
    before = accounts.get_user(uid)
    wrong = web.post("/account/password", json={"old_password": "not-it", "new_password": NEW_PASSWORD})
    assert wrong.status_code == 401
    short = web.post("/account/password", json={"old_password": PASSWORD, "new_password": "short"})
    assert short.status_code == 400
    after = accounts.get_user(uid)
    assert (after["password_hash"], after["wrapped_dek"]) == (before["password_hash"], before["wrapped_dek"])
    assert _signed_in(web)


def test_a_password_change_stops_other_devices_alerts(web):
    uid = make_user("ada@core.test")
    for device in ("this-browser", "lost-phone"):
        db.execute(
            "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, user_agent, created_at) VALUES (?,?,?,?,?,?)",
            (uid, f"https://push.example/{device}", "k", "a", "", db.now()),
        )
    sign_in(web, uid)
    changed = web.post("/account/password", json={"old_password": PASSWORD, "new_password": NEW_PASSWORD,
                                                  "keep_endpoint": "https://push.example/this-browser"})
    assert changed.status_code == 200, changed.text
    rows = db.fetchall("SELECT endpoint FROM push_subscriptions WHERE user_id=?", (uid,))
    assert [row["endpoint"] for row in rows] == ["https://push.example/this-browser"]


def test_sign_ins_expire_and_unstamped_sessions_are_refused(web):
    uid = quick_user("ada@core.test")
    session = sign_in(web, uid)
    now = int(time.time())
    max_age = settings.session_max_age_days * 86400
    cases = [
        ({"iat": now - max_age + 600, "seen": now}, True),
        # A recent renewal (seen) does not extend the sign-in itself.
        ({"iat": now - max_age - 1, "seen": now}, False),
        ({"iat": now + 3600}, False),
        ({"iat": None}, False),
        ({"sid": None}, False),
        ({"ph": None}, False),
        ({"ph": "0" * 32}, False),
    ]
    for change, ok in cases:
        set_session(web, {**session, **change})
        assert _signed_in(web) is ok, change


def test_a_session_for_a_deleted_account_is_refused(web):
    uid = quick_user("ada@core.test")
    admin = quick_user("root@core.test", admin=True)
    session = sign_in(web, uid)
    accounts.delete_user(uid, actor_id=admin)
    set_session(web, session)
    assert not _signed_in(web)


def test_active_use_renews_the_session_cookie(web):
    uid = quick_user("ada@core.test")
    session = sign_in(web, uid)
    set_session(web, {**session, "seen": int(time.time())})
    assert "set-cookie" not in web.get("/chat/threads").headers
    set_session(web, {**session, "seen": int(time.time()) - 3601})
    renewed = web.get("/chat/threads")
    assert renewed.status_code == 200 and "Max-Age=" in renewed.headers["set-cookie"]


def test_a_login_through_app_main_sets_the_cookie_lifetime_it_configures(fresh_db):
    make_user("ada@core.test")
    client = main_client()
    res = login(client, "ada@core.test")
    assert res.status_code == 303 and res.headers["location"] == "/chat"
    [session_cookie] = [c for c in res.headers.get_list("set-cookie") if c.startswith("session=")]
    assert f"Max-Age={session_options()['max_age']}" in session_cookie.split("; ")


@pytest.fixture
def tight_limit(monkeypatch):
    # Each guess costs a real scrypt: a small budget and cheap params keep these quick.
    monkeypatch.setattr(settings, "auth_rate_per_minute", 3)
    monkeypatch.setattr(crypto, "PASSWORD_SCRYPT", (2**10, 8, 1))
    monkeypatch.setattr(accounts, "_DUMMY_HASH", None)


def test_login_is_rate_limited_per_address_even_for_the_right_password(web, tight_limit):
    make_user("ada@core.test")
    for _ in range(settings.auth_rate_per_minute):
        assert login(web, "ada@core.test", "wrong-password").status_code == 401
    assert login(web, "ada@core.test").status_code == 429


def test_guesses_from_many_addresses_hit_the_account_budget(web, tight_limit):
    make_user("ada@core.test")
    ip = {"now": ""}
    with patch("app.web.deps.client_ip", lambda request: ip["now"]):
        for n in range(settings.auth_rate_per_minute * 3):
            ip["now"] = f"203.0.113.{n + 1}"
            assert login(web, "ada@core.test", "wrong-password").status_code == 401
        # The account's budget is spent: an address that guessed wrong is
        # refused, for the right password too and for any spelling of the email.
        ip["now"] = "203.0.113.1"
        assert login(web, "ada@core.test").status_code == 429
        ip["now"] = "203.0.113.2"
        assert login(web, " ADA@core.test ").status_code == 429
        # A fresh address gets one guess; a wrong one closes it too.
        ip["now"] = "203.0.113.200"
        assert login(web, "ada@core.test", "wrong-password").status_code == 401
        assert login(web, "ada@core.test").status_code == 429
        # IPv6 counts by /64: rotating inside it is the same address.
        ip["now"] = "2001:db8::1"
        assert login(web, "ada@core.test", "wrong-password").status_code == 401
        ip["now"] = "2001:db8::2"
        assert login(web, "ada@core.test").status_code == 429


def test_only_failures_use_the_account_budget_and_cannot_lock_the_owner_out(web, tight_limit):
    make_user("ada@core.test")
    ip = {"now": ""}
    with patch("app.web.deps.client_ip", lambda request: ip["now"]):
        # More sign-ins than the account budget: successes do not count.
        for n in range(settings.auth_rate_per_minute * 3 + 1):
            ip["now"] = f"198.51.100.{n + 1}"
            assert login(web, "ada@core.test").status_code == 303
        # An attacker spends the account's budget from a few addresses...
        for n in range(settings.auth_rate_per_minute * 3):
            ip["now"] = f"203.0.113.{n // settings.auth_rate_per_minute + 1}"
            assert login(web, "ada@core.test", "wrong-password").status_code == 401
        # ...and the owner, from an address that never failed, still gets in.
        ip["now"] = "198.51.100.1"
        assert login(web, "ada@core.test").status_code == 303


def test_an_over_long_email_is_refused_before_the_limiter(web, tight_limit):
    long_email = "a" * 300 + "@core.test"
    for _ in range(settings.auth_rate_per_minute * 2):
        res = login(web, long_email, "wrong-password")
        assert res.status_code == 401 and "Email or password is wrong." in res.text
    token = anonymous(web)["csrf"]
    web.headers.pop("x-csrf-token", None)
    form = {"email": long_email, "password": PASSWORD, "csrf_token": token}
    assert web.post("/register", data=form, follow_redirects=False).status_code == 400
    assert not limiter._hits
    # A normal failed sign-in keys the account by a hash, never the address itself.
    assert login(web, "ada@core.test", "wrong-password").status_code == 401
    assert limiter._hits and not any("ada@" in key for key in limiter._hits)


def test_the_rate_limiter_stays_bounded(monkeypatch):
    bounded = RateLimiter()
    monkeypatch.setattr(bounded, "MAX_KEYS", 100)
    for n in range(1000):
        assert bounded.allow(f"auth-account:{n}", 3, 900)
        assert len(bounded._hits) <= 100 and len(bounded._expires) <= 100
    # The newest keys survive; the ones closest to expiry went first.
    assert "auth-account:999" in bounded._hits and "auth-account:0" not in bounded._hits
    # A key keeps at most `limit` stamps, whatever limit it was used with before.
    for _ in range(50):
        bounded.allow("api", 100, 60)
    assert not bounded.allow("api", 2, 60) and len(bounded._hits["api"]) == 2


def test_password_change_and_vault_unlock_share_the_sign_in_limit(web, tight_limit):
    uid = make_user("ada@core.test")
    sign_in(web, uid)
    for _ in range(settings.auth_rate_per_minute):
        assert web.post("/account/vault/unlock", json={"password": "wrong-password"}).status_code == 401
    assert web.post("/account/vault/unlock", json={"password": PASSWORD}).status_code == 429
    assert web.post("/account/password", json={"old_password": PASSWORD, "new_password": NEW_PASSWORD}).status_code == 429
    assert accounts.login("ada@core.test", PASSWORD) is not None


def test_registration_attempts_share_the_address_limit(web, monkeypatch, tight_limit):
    monkeypatch.setattr(settings, "allow_register", False)
    quick_user("root@core.test", admin=True)
    for n in range(settings.auth_rate_per_minute):
        token = anonymous(web)["csrf"]
        web.headers.pop("x-csrf-token", None)
        form = {"email": f"x{n}@core.test", "password": PASSWORD, "csrf_token": token}
        assert web.post("/register", data=form, follow_redirects=False).status_code == 403
    token = anonymous(web)["csrf"]
    web.headers.pop("x-csrf-token", None)
    form = {"email": "late@core.test", "password": PASSWORD, "csrf_token": token}
    assert web.post("/register", data=form, follow_redirects=False).status_code == 429


def test_a_session_check_is_one_query_revoked_or_not(fresh_db, monkeypatch):
    uid = quick_user("ada@core.test")
    user = accounts.get_user(uid)
    session = {}
    accounts.start_session(session, user)
    real, calls = db.tx, []

    def counted():
        calls.append(1)
        return real()

    monkeypatch.setattr(db, "tx", counted)
    assert accounts.session_user(session) == user and len(calls) == 1
    accounts.revoke_session(session)
    calls.clear()
    assert accounts.session_user(session) is None and len(calls) == 1


def test_a_forged_cookie_signed_with_another_secret_is_ignored(web):
    import base64
    import json

    import itsdangerous

    uid = quick_user("ada@core.test")
    session = {}
    accounts.start_session(session, accounts.get_user(uid))
    forged = itsdangerous.TimestampSigner("not-the-app-secret").sign(
        base64.b64encode(json.dumps(session).encode())).decode()
    web.cookies.clear()
    web.cookies.set("session", forged)
    assert not _signed_in(web)
    # Positive control: the same payload under the real secret is accepted.
    web.cookies.clear()
    web.cookies.set("session", sign_session(session))
    assert _signed_in(web)
