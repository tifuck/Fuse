"""Pending sign-ins live in the database: they survive a restart, expire,
end once (a cancelled or replaced attempt never links an account), keep the
device-code answers of RFC 8628, and match callbacks by state. Removing an
account is one route for every provider: donor or admin only, with the
provider's revoke call where it has one."""
import asyncio
import time
from unittest.mock import AsyncMock, patch

from app import db
from app.providers import auth_flows, logins
from app.sources import kimi
from app.web.deps import _pool_flash
from tests.core.auth._support import sign_in, web_client
from tests.core.subscriptions._support import account, row, user


def test_a_pending_sign_in_survives_a_restart_and_expires(fresh_db):
    uid = user()
    stored = logins.put(uid, "kimi", {"device_code": "plain-device-code", "user_code": "UC", "interval": 99})
    # Nothing in process memory: a fresh read (as after a restart) finds it, encrypted at rest.
    assert "plain-device-code" not in db.fetchone("SELECT data FROM provider_logins WHERE user_id=?", (uid,))["data"]
    again = logins.get(uid, "kimi", clamp=True)
    assert again["device_code"] == "plain-device-code" and again["_login"] == stored["_login"] and again["interval"] == 30
    assert logins.get(uid, "grok") is None  # one row per provider
    db.execute("UPDATE provider_logins SET expires_at=? WHERE user_id=?", (time.time() - 1, uid))
    assert logins.get(uid, "kimi") is None and db.fetchone("SELECT 1 FROM provider_logins") is None


def test_an_attempt_ends_once_and_never_after_it_was_replaced(fresh_db):
    uid = user()
    first = logins.put(uid, "grok", {"device_code": "a"})
    second = logins.put(uid, "grok", {"device_code": "b"})  # the user started again
    assert not logins.claim(uid, "grok", first)
    assert logins.claim(uid, "grok", second) and not logins.claim(uid, "grok", second)


def test_device_code_answers(fresh_db):
    uid = user()
    pending = logins.put(uid, "kimi", {"device_code": "dc", "interval": 5})
    assert auth_flows.device_step(uid, "kimi", "Kimi", pending, {"error": "authorization_pending"}) == (
        200, {"pending": True, "retry_after": 5})
    status, body = auth_flows.device_step(uid, "kimi", "Kimi", pending, {"error": "slow_down"})
    assert (status, body["retry_after"]) == (200, 10) and logins.get(uid, "kimi")["interval"] == 10  # saved
    assert auth_flows.device_step(uid, "kimi", "Kimi", pending, {"error": "nope"})[0] == 502
    assert logins.get(uid, "kimi") is not None  # unknown errors keep polling
    for terminal in ("expired_token", "access_denied"):
        logins.put(uid, "kimi", pending)
        assert auth_flows.device_step(uid, "kimi", "Kimi", pending, {"error": terminal}) == (
            400, {"error": "Kimi sign-in was denied or expired. Start again."})
        assert logins.get(uid, "kimi") is None


def test_a_callback_matches_only_its_own_state(fresh_db):
    uid = user()
    logins.put(uid, "nanogpt", {"state": "s-1234", "verifier": "v"})
    assert logins.by_state("nanogpt", "s-1234")[0] == uid
    assert logins.by_state("nanogpt", "s-9999") is None and logins.by_state("mimo", "s-1234") is None


def test_one_revoke_route_for_every_provider(fresh_db):
    alice, bob = user("a@core.test"), user("b@core.test")
    claude_cred, kimi_cred = account("claude", alice), account("kimi", alice)
    with web_client() as web, patch("app.sources.claude.revoke_remote", new=AsyncMock()) as remote:
        sign_in(web, bob)
        assert web.post(f"/sources/p/claude/{claude_cred}/revoke", follow_redirects=False).status_code == 403
        assert web.post(f"/sources/p/zai/{claude_cred}/revoke", follow_redirects=False).status_code == 404
        assert web.post(f"/sources/p/nope/{claude_cred}/revoke", follow_redirects=False).status_code == 404
        sign_in(web, alice)
        assert web.post(f"/sources/p/claude/{claude_cred}/revoke", follow_redirects=False).status_code == 303
        assert web.post(f"/sources/p/kimi/{kimi_cred}/revoke", follow_redirects=False).status_code == 303
    assert remote.await_count == 1 and remote.await_args.args[0]["id"] == claude_cred  # kimi has no revoke call
    assert row(claude_cred)["disabled"] == 1 and row(kimi_cred)["disabled"] == 1
    assert _pool_flash.pop(alice)[1:] == ("Kimi account removed.", "kimi")


def test_generic_sign_in_urls_reach_the_providers_handlers(fresh_db, monkeypatch):
    uid = user()
    monkeypatch.setattr(kimi, "start_device", AsyncMock(return_value={
        "device_code": "dc", "user_code": "UC", "interval": 5, "_region": "global", "_device_id": "d"}))
    with web_client() as web:
        sign_in(web, uid)
        assert web.post("/sources/p/kimi/connect", data={"region": "global"}, follow_redirects=False).status_code == 303
        assert logins.get(uid, "kimi")["device_code"] == "dc"
        monkeypatch.setattr(kimi, "poll_device", AsyncMock(return_value={"error": "authorization_pending"}))
        assert web.post("/sources/p/kimi/poll").json() == {"pending": True, "retry_after": 5}
        assert web.post("/pool/kimi/poll").json() == {"pending": True, "retry_after": 5}  # older URL still works
        web.post("/sources/p/kimi/cancel", follow_redirects=False)
    assert logins.get(uid, "kimi") is None
    assert asyncio.run(auth_flows.api_key_paste("deepseek", "x", user_id=uid)) == (None, "Enter a DeepSeek API key.")
