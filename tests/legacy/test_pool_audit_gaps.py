"""Pool cooldown kinds and token persistence gaps from the audit."""
import json
from unittest.mock import patch

import pytest

from app.sources import pool_pick
from app.sources.pool_pick import AUTH_RECHECK_SECONDS, explain_empty, note_failure, pick_account


@pytest.fixture(autouse=True)
def _health():
    pool_pick.reset_health()
    yield
    pool_pick.reset_health()


def _cred(cid):
    return {"id": cid, "disabled": 0, "week_tokens": 0, "week_start": None, "last_usage_percent": None,
            "donated_by": None, "extra": "{}"}


def _at(when):
    return patch("app.sources.pool_pick.time.time", return_value=when)


def _pick(creds, **kwargs):
    why = []
    picked = pick_account(creds, kind="zai", why=why, **kwargs)
    return (picked[0]["id"] if picked else None), why


def test_a_short_blip_then_a_limit_leaves_the_account_a_last_resort():
    spent, healthy = _cred(1), _cred(2)
    with _at(1000):
        note_failure(1, cooldown_seconds=5, affinity_safe=True)
    with _at(1001):
        note_failure(1, cooldown_seconds=3600, limit=True)
    with _at(1010):
        assert _pick([spent, healthy])[0] == 2
        # The blip is over: only the limit remains, which never benches.
        assert _pick([spent])[0] == 1


def test_a_first_401_on_a_limited_account_expires_on_its_own():
    spent, healthy = _cred(1), _cred(2)
    with _at(1000):
        note_failure(1, cooldown_seconds=3600, limit=True)
    with _at(1001):
        note_failure(1, cooldown_seconds=AUTH_RECHECK_SECONDS)
    with _at(1050):
        picked, why = _pick([spent])
        assert picked is None and [reason for _cred, reason in why] == ["cooling"]
        empty = explain_empty("zai", why)
        assert empty.status == 503
        assert empty.body["error"]["resets_at"] == 1001 + AUTH_RECHECK_SECONDS
    with _at(1001 + AUTH_RECHECK_SECONDS + 1):
        assert _pick([spent, healthy])[0] == 2
        assert _pick([spent])[0] == 1


def test_single_cooldowns_keep_their_meaning():
    with _at(1000):
        note_failure(1, cooldown_seconds=60)
        note_failure(2, cooldown_seconds=60, affinity_safe=True)
        note_failure(3, cooldown_seconds=60, limit=True)
    with _at(1030):
        assert _pick([_cred(1)])[0] is None
        assert _pick([_cred(1)], prefer_id=1)[0] is None
        assert _pick([_cred(2)])[0] is None
        assert _pick([_cred(2)], prefer_id=2)[0] == 2
        assert _pick([_cred(3)])[0] == 3
        assert _pick([_cred(3), _cred(4)])[0] == 4
        assert pool_pick._cooldown_end(1) == 1060
    with _at(1061):
        assert [_pick([_cred(cid)])[0] for cid in (1, 2, 3)] == [1, 2, 3]
        assert not any(pool_pick.cooling_down(cid) for cid in (1, 2, 3))


@pytest.mark.usefixtures("isolated_db")
def test_rotation_losing_to_a_relink_leaves_the_relinked_row():
    from app import accounts, db
    from app.sources import chatgpt, token_store

    sid = chatgpt.ensure_chatgpt_source()
    user = accounts.create_user("relink-race@test.invalid", "password12")
    cid = accounts.add_credential(sid, "mine", "at-old", extra={"access_token": "at-old", "refresh_token": "rt-old"},
                                  donated_by=user["id"])
    dispatched = db.fetchone("SELECT extra FROM credentials WHERE id=?", (cid,))["extra"]
    # The donor re-links while the refresh POST is in flight.
    db.execute("UPDATE credentials SET secret=?, extra=? WHERE id=?",
               (accounts.encrypt("at-relinked"),
                accounts.encrypt(json.dumps({"access_token": "at-relinked", "refresh_token": "rt-relinked"})), cid))
    before = dict(db.fetchone("SELECT secret, extra, disabled FROM credentials WHERE id=?", (cid,)))
    rotated = {"access_token": "at-rotated", "refresh_token": "rt-rotated"}
    assert token_store.persist_rotated(chatgpt._cas_update_tokens, cid, dispatched, accounts.encrypt("at-rotated"),
                                       {"access_token": "at-old", "refresh_token": "rt-old", **rotated}, rotated) is False
    assert dict(db.fetchone("SELECT secret, extra, disabled FROM credentials WHERE id=?", (cid,))) == before
