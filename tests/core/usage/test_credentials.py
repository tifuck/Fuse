"""Pool credentials as rows: one row per account (no duplicates from a repeat
sign-in or a racing add), which database may spend single-use OAuth refresh
tokens, and rotated tokens that must reach the row (or yield to a re-link).
No token endpoint is ever called.
"""
import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app import accounts, db
from app.config import settings
from app.crypto import encrypt
from app.sources import chatgpt, kimi, pool_policy, token_store, zai
from tests.core.usage._support import user


def _jwt(claims):
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"e30.{body}.sig"


def _chatgpt_tokens(account, email, access):
    claims = {"https://api.openai.com/auth": {"chatgpt_account_id": account},
              "https://api.openai.com/profile": {"email": email}}
    return {"access_token": access, "refresh_token": "rt-" + access, "id_token": _jwt(claims), "expires_in": 3600}


def _rows(sid):
    return [(int(r["id"]), int(r["disabled"])) for r in
            db.fetchall("SELECT id, disabled FROM credentials WHERE source_id=? ORDER BY id", (sid,))]


def _extra(cid):
    return accounts.cred_extra(db.fetchone("SELECT * FROM credentials WHERE id=?", (cid,)))


def test_a_repeat_chatgpt_sign_in_reuses_its_row_but_team_seats_stay_separate(fresh_db):
    owner = user()
    sid = chatgpt.ensure_chatgpt_source()
    first = chatgpt.store_tokens(sid, _chatgpt_tokens("acct", "a@example.com", "t1"), donated_by=owner)
    db.execute("UPDATE credentials SET disabled=1 WHERE id=?", (first,))

    again = chatgpt.store_tokens(sid, _chatgpt_tokens("acct", "a@example.com", "t2"), donated_by=owner)
    seat = chatgpt.store_tokens(sid, _chatgpt_tokens("acct", "b@example.com", "t3"), donated_by=owner)

    assert again == first and seat != first
    assert _rows(sid) == [(first, 0), (seat, 0)]
    assert _extra(first)["access_token"] == "t2"


@pytest.mark.parametrize("mod", [zai, kimi], ids=["zai", "kimi"])
def test_a_plan_key_added_while_its_probe_runs_is_not_added_twice(fresh_db, mod):
    owner = user()
    sid = pool_policy.source_id("zai" if mod is zai else "kimi")

    async def racing_probe(raw):
        mod.store_key(sid, raw, owner)  # a concurrent add of the same key commits first
        return {}, "global", ""

    with patch.object(mod, "probe_key", racing_probe), patch.object(mod, "refresh_quota", AsyncMock()):
        cred_id, err = asyncio.run(mod.add_plan_key(sid, "plan-key-12345", owner))

    assert cred_id is None and "already in the pool" in err
    assert len(_rows(sid)) == 1


def test_only_the_database_named_as_owner_may_spend_refresh_tokens(fresh_db, monkeypatch):
    # The fixture schema carries the dev schema's owner row (fuse_live); read, never written.
    owner = db.fetchone("SELECT value FROM schema_meta WHERE key=?", (token_store.OWNER_KEY,))["value"]

    monkeypatch.setattr(settings, "database_url", f"mysql://u:p@localhost/{owner}")
    monkeypatch.setattr(settings, "oauth_refresh_enabled", True)
    assert token_store.claim_refresh_ownership() is True

    # A clone or restore carries the owner row verbatim and must never refresh.
    monkeypatch.setattr(settings, "database_url", f"mysql://u:p@localhost/{owner}_clone")
    assert token_store.claim_refresh_ownership() is False
    assert settings.oauth_refresh_enabled is False
    # Ownership never turns refresh on when the config has it off.
    monkeypatch.setattr(settings, "database_url", f"mysql://u:p@localhost/{owner}")
    assert token_store.claim_refresh_ownership() is False


def _linked(owner):
    sid = chatgpt.ensure_chatgpt_source()
    cid = accounts.add_credential(sid, "mine", "at-old", extra={"access_token": "at-old", "refresh_token": "rt-old"},
                                  donated_by=owner)
    return cid, db.fetchone("SELECT extra FROM credentials WHERE id=?", (cid,))["extra"]


ROTATED = {"access_token": "at-rotated", "refresh_token": "rt-rotated"}


def test_rotated_tokens_land_even_when_metadata_changed_while_the_refresh_was_in_flight(fresh_db):
    cid, dispatched = _linked(user())
    stale = _extra(cid)
    accounts.update_extra(cid, {**stale, "billing": {"plan_type": "plus"}})

    saved = token_store.persist_rotated(chatgpt._cas_update_tokens, cid, dispatched, encrypt("at-rotated"),
                                        {**stale, **ROTATED}, ROTATED)

    assert saved is True
    extra = _extra(cid)
    assert (extra["access_token"], extra["refresh_token"], extra["billing"]) == ("at-rotated", "rt-rotated",
                                                                                  {"plan_type": "plus"})
    # A metadata writer holding the pre-rotation copy cannot roll the tokens back.
    accounts.update_extra(cid, {**stale, "billing": {"plan_type": "pro"}})
    assert (_extra(cid)["refresh_token"], _extra(cid)["billing"]) == ("rt-rotated", {"plan_type": "pro"})


def test_a_relink_during_the_refresh_wins_over_the_rotated_tokens(fresh_db):
    cid, dispatched = _linked(user())
    db.execute("UPDATE credentials SET secret=?, extra=? WHERE id=?",
               (encrypt("at-relinked"), encrypt(json.dumps({"access_token": "at-relinked", "refresh_token": "rt-relinked"})),
                cid))
    before = dict(db.fetchone("SELECT secret, extra, disabled FROM credentials WHERE id=?", (cid,)))

    saved = token_store.persist_rotated(chatgpt._cas_update_tokens, cid, dispatched, encrypt("at-rotated"),
                                        {"access_token": "at-old", "refresh_token": "rt-old", **ROTATED}, ROTATED)

    assert saved is False
    assert dict(db.fetchone("SELECT secret, extra, disabled FROM credentials WHERE id=?", (cid,))) == before


def test_a_revoked_refresh_grant_keeps_the_account_until_its_access_token_expires():
    disabled = []
    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = {"id": 4242, "extra": "raw"}
    live = {"refresh_token": "rt-dead", "expires_at": soon}
    try:
        token_store.revoked_grant(row, live, "chatgpt", lambda cid, extra=None: disabled.append(cid))
        assert disabled == [] and token_store.dead_grant(4242, "rt-dead")
        assert not token_store.dead_grant(4242, "rt-relinked")  # a re-link is a new grant

        token_store.revoked_grant(row, {**live, "expires_at": "2000-01-01T00:00:00Z"}, "chatgpt",
                                  lambda cid, extra=None: disabled.append(cid))
        assert disabled == [4242] and not token_store.dead_grant(4242, "rt-dead")
    finally:
        token_store._dead_grants.pop(4242, None)
