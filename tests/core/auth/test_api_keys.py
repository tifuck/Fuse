"""/v1 API keys: only a hash is stored, only a live key authenticates, a key
maps to exactly one account, and per-account limits stay per account."""
from unittest.mock import patch

import pytest
from fastapi.responses import JSONResponse, StreamingResponse

from app import accounts, api, db
from app.config import settings
from tests.core.helpers import quick_user

BODY = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture
def served():
    """The provider dispatch behind /v1/chat/completions, answering with the
    account the request was authenticated as."""
    async def dispatch(request, user, payload):
        if payload.get("stream"):
            async def chunks():
                yield b"data: [DONE]\n\n"
            return StreamingResponse(chunks(), media_type="text/event-stream")
        return JSONResponse({"user": user["id"], "key": user["key_id"]})

    with patch.object(api, "dispatch_chat", new=dispatch):
        yield


def _call(web, key=None, scheme="Bearer", body=BODY):
    headers = {"Authorization": f"{scheme} {key}"} if key is not None else {}
    return web.post("/v1/chat/completions", headers=headers, json=body)


def test_a_new_account_gets_one_key_stored_only_as_a_hash(fresh_db):
    created = accounts.create_user("ada@core.test", "password12")
    raw = created["new_key"]
    assert raw.startswith("sk-") and len(raw) > 30
    row = db.fetchone("SELECT * FROM api_keys WHERE user_id=?", (created["id"],))
    assert row["key_hash"] == accounts.key_hash(raw) and row["prefix"] == raw[:9]
    assert raw not in str(dict(row))
    second = accounts.create_api_key(created["id"], "ci")
    assert second != raw and [k["name"] for k in accounts.keys_for(created["id"])] == ["default", "ci"]


@pytest.mark.parametrize("name", ["", "   ", "x" * 81, "bad\nname", "tab\tname"])
def test_key_names_are_validated(fresh_db, name):
    uid = quick_user()
    with pytest.raises(ValueError):
        accounts.create_api_key(uid, name)
    assert accounts.keys_for(uid) == []


def test_only_a_live_bearer_key_authenticates(web, served):
    uid = quick_user()
    raw = accounts.create_api_key(uid, "ci")
    key_id = accounts.keys_for(uid)[0]["id"]
    for key, scheme, detail in [
        (None, "Bearer", "Missing API key"),
        (raw, "Basic", "Missing API key"),
        ("", "Bearer", "Missing API key"),
        ("sk-not-a-key", "Bearer", "Invalid API key"),
        (raw[:-1], "Bearer", "Invalid API key"),
    ]:
        res = _call(web, key, scheme, body=None)
        # Refused before the body is read: no JSON is needed to get the 401.
        assert res.status_code == 401, (key, scheme)
        assert res.json()["error"] == {"message": detail, "type": "invalid_request_error", "code": 401}
    ok = _call(web, raw)
    assert ok.status_code == 200 and ok.json() == {"user": uid, "key": key_id}
    assert accounts.keys_for(uid)[0]["last_used_at"]
    assert _call(web, raw, scheme="bearer").status_code == 200


def test_revoked_keys_and_keys_of_deleted_accounts_stop_working(web, served):
    admin = quick_user("root@core.test", admin=True)
    uid = quick_user("ada@core.test")
    bystander = quick_user("by@core.test")
    first = accounts.create_api_key(uid, "first")
    second = accounts.create_api_key(uid, "second")
    theirs = accounts.create_api_key(bystander, "theirs")
    first_id = accounts.keys_for(uid)[0]["id"]

    assert accounts.delete_api_key(bystander, first_id) is False
    assert _call(web, first).status_code == 200
    assert accounts.delete_api_key(uid, first_id) is True
    assert _call(web, first).status_code == 401
    assert _call(web, second).status_code == 200

    accounts.delete_user(uid, actor_id=admin)
    assert _call(web, second).status_code == 401
    assert _call(web, theirs).json()["user"] == bystander


def test_one_accounts_rate_limit_does_not_touch_another(web, served, monkeypatch):
    monkeypatch.setattr(settings, "api_rate_per_minute", 3)
    alice = accounts.create_api_key(quick_user("alice@core.test"), "a")
    bob = accounts.create_api_key(quick_user("bob@core.test"), "b")
    for _ in range(3):
        assert _call(web, bob).status_code == 200
    assert _call(web, bob).status_code == 429
    assert _call(web, alice).status_code == 200


def test_concurrent_requests_per_account_are_capped_and_given_back(web, served, monkeypatch):
    monkeypatch.setattr(settings, "api_max_in_flight", 2)
    uid = quick_user("ada@core.test")
    key = accounts.create_api_key(uid, "a")
    other = accounts.create_api_key(quick_user("other@core.test"), "o")
    held = [api._admit(uid), api._admit(uid)]  # two streams still open
    try:
        refused = _call(web, key)
        assert refused.status_code == 429 and refused.json()["detail"] == "Too many concurrent requests"
        assert _call(web, other).status_code == 200
    finally:
        for token in held:
            api._release(uid, token)
    # Finished requests, streamed or not, and refused bodies give their slot back.
    for body in (BODY, {**BODY, "stream": True}, BODY):
        assert _call(web, key, body=body).status_code == 200
    bad = web.post("/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, content=b"{not json")
    assert bad.status_code == 400
    assert uid not in api._in_flight


def test_a_key_used_again_within_a_minute_is_not_rewritten(fresh_db):
    uid = quick_user()
    raw = accounts.create_api_key(uid, "ci")
    user = accounts.user_by_api_key(raw)
    assert user["id"] == uid and "key_last_used_at" not in user
    assert accounts.keys_for(uid)[0]["last_used_at"]
    with patch.object(db, "execute", side_effect=AssertionError("last_used_at rewritten")):
        assert accounts.user_by_api_key(raw)["id"] == uid
    stale = "2020-01-01T00:00:00Z"
    db.execute("UPDATE api_keys SET last_used_at=? WHERE user_id=?", (stale, uid))
    accounts.user_by_api_key(raw)
    assert accounts.keys_for(uid)[0]["last_used_at"] > stale
