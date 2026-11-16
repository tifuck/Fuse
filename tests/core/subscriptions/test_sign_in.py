"""Sign-in flows: PKCE links, device/poll pending states, code exchanges that
check state before any call, verification links limited to the provider's
hosts, and start failures reported in their own dialog."""
import asyncio
import base64
import hashlib
import json
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.sources import chatgpt, claude, cursor, devin, kimi
from app.web.deps import _pool_flash
from app.web.source_routes import chatgpt as chatgpt_routes, grok as grok_routes, kimi as kimi_routes
from tests.core.subscriptions._support import extra_of, http, run, user


def _challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


@pytest.mark.parametrize("start,host_path", [
    (lambda: cursor.start_login(), "https://cursor.com/loginDeepControl"),
    (lambda: claude.start_login(), None),
    (lambda: devin.start_cli_login(), "https://app.devin.ai/auth/cli/continue"),
])
def test_browser_logins_are_s256_pkce_and_never_leak_the_verifier(start, host_path):
    data = start()
    url = urlparse(data["auth_url"])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    if host_path:
        assert f"{url.scheme}://{url.netloc}{url.path}" == host_path
    assert query.get("challenge", query.get("code_challenge")) == _challenge(data["verifier"])
    assert data["verifier"] not in data["auth_url"]
    if host_path and "devin" in host_path:
        assert "redirect_uri" not in query  # Devin only allows a localhost redirect


def test_claude_finishes_only_with_the_matching_state_and_stores_the_profile(fresh_db, monkeypatch):
    owner = user()
    login = claude.start_login()

    def upstream(request):
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 28800,
                                             "account": {"uuid": "acct-1", "email_address": "c@x.test"}})
        return httpx.Response(200, json={"account": {"uuid": "acct-1", "email": "c@x.test"},
                                         "organization": {"uuid": "org-1", "organization_type": "claude_max"}})

    with http(upstream, monkeypatch) as calls:
        assert run(claude.complete_login("code-without-state", login["verifier"], login["state"], owner))[0] is None
        assert run(claude.complete_login("code#other-state", login["verifier"], login["state"], owner))[0] is None
        assert calls.requests == []
        cred, err = run(claude.complete_login(f"the-code#{login['state']}", login["verifier"], login["state"], owner))
        again, _ = run(claude.complete_login(f"the-code#{login['state']}", login["verifier"], login["state"], owner))

    assert err == "" and again == cred
    sent = json.loads(calls.requests[0].content)
    assert (sent["code"], sent["code_verifier"], sent["state"]) == ("the-code", login["verifier"], login["state"])
    extra = extra_of(cred)
    assert (extra["account_id"], extra["subscription_type"], extra["email"]) == ("acct-1", "max", "c@x.test")


def test_device_and_poll_logins_stay_pending_until_approved(fresh_db, monkeypatch):
    replies = [httpx.Response(403), httpx.Response(200, json={"authorization_code": "ac", "code_verifier": "cv"}),
               httpx.Response(200, json={"access_token": "at", "refresh_token": "rt"})]
    with http(replies, monkeypatch) as calls:
        assert run(chatgpt.poll_device("dev-id", "UC-1")) == {"status": "pending"}
        done = run(chatgpt.poll_device("dev-id", "UC-1"))
    assert done["status"] == "ok" and done["tokens"]["access_token"] == "at"
    exchange = parse_qs(calls.requests[-1].content.decode())
    assert (exchange["code"], exchange["code_verifier"]) == (["ac"], ["cv"])

    with http(lambda request: httpx.Response(404) if request.url.params["verifier"] == "wait"
              else httpx.Response(200, json={"accessToken": "tok", "refreshToken": "r" * 20}), monkeypatch):
        assert run(cursor.poll_login("u", "wait")) == {"pending": True}
        assert run(cursor.poll_login("u", "ok"))["access_token"] == "tok"


def test_kimi_verification_links_stay_on_kimi_hosts():
    assert kimi.device_verify_url({"verification_uri_complete": "https://www.kimi.ai/code/authorize_device?user_code=AB"}) \
        == "https://www.kimi.ai/code/authorize_device?user_code=AB"
    assert kimi.device_verify_url({"verification_uri_complete": "https://evil.example/phish", "user_code": "AB"}) \
        == "https://www.kimi.ai/code/authorize_device?user_code=AB"
    assert kimi.device_verify_url({"user_code": "AB", "_region": "cn"}) == "https://www.kimi.com/code/authorize_device?user_code=AB"


@pytest.mark.parametrize("module,start,provider,section", [
    (chatgpt_routes, "chatgpt_start", "chatgpt_mod", "chatgpt"),
    (grok_routes, "grok_start", "grok_mod", "grok"),
    (kimi_routes, "kimi_start", "kimi_mod", "kimi"),
])
def test_a_failed_device_start_is_an_error_for_its_own_dialog(monkeypatch, module, start, provider, section):
    monkeypatch.setattr(module, "require_user", lambda request: ({"id": 7}, None))
    monkeypatch.setattr(module.limiter, "allow", lambda *args: True)
    monkeypatch.setattr(getattr(module, provider), "start_device", AsyncMock(side_effect=OSError("proxy refused")))
    _pool_flash.clear()
    handler = getattr(module, start)
    response = asyncio.run(handler(None, "global") if section == "kimi" else handler(None))
    assert response.status_code == 303
    kind, message, where = _pool_flash.pop(7)
    assert (kind, where) == ("err", section) and message.startswith("Could not start")
