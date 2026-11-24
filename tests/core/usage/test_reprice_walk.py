"""A reprice walk (save_model_price / delete_model_price) against what can
happen during it: an account deleted mid-window, a lock conflict in one
window, a restart before the last window."""
import threading

import pymysql
import pytest

from app import accounts, db
from tests.core.helpers import wait_until
from tests.core.usage._support import api_key, cost_sums, rollup_drift, source, user

USAGE = {"prompt_tokens": 1000, "completion_tokens": 400, "usage_status": "complete"}
PER_EVENT = 1000 + 800  # at $1/M in, $2/M out


def _seed():
    admin = user("admin@core.test", admin=True)
    member = user("member@core.test")
    key = api_key(member)
    source_id = source("zai")
    cred = accounts.add_credential(source_id, "pool", "secret", donated_by=admin)
    for uid, key_id, at in ((admin, None, "2026-11-10T09:15:00Z"), (member, key, "2026-11-10T09:45:00Z"),
                            (member, key, "2026-11-10T10:05:00Z")):
        db.record_usage(uid, source_id, cred, "glm-x", dict(USAGE), at, key_id=key_id, provider="zai")
    return admin, member, cred


def _pending():
    return db.fetchall("SELECT value FROM schema_meta WHERE key LIKE 'reprice_pending:%'")


def _waiting_on(pattern):
    with db.tx() as conn:
        return conn.native(
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE db=DATABASE() AND info LIKE %s AND info NOT LIKE %s",
            (pattern, "%processlist%")).fetchone()[0]


def test_an_account_deleted_during_a_reprice_window_waits_for_it(fresh_db, monkeypatch):
    admin, member, cred = _seed()
    batch = db._reprice_batch
    deleters = []

    def read_then_delete(conn, *args):
        # The window has read its events; the account delete starts now.
        deleter = threading.Thread(target=accounts.delete_user, args=(member,), kwargs={"actor_id": admin})
        deleter.start()
        deleters.append(deleter)
        wait_until(lambda: not deleter.is_alive() or _waiting_on("%usage_events_user_created%"),
                   message="delete_user neither finished nor reached the member's events")
        batch(conn, *args)

    monkeypatch.setattr(db, "_reprice_batch", read_then_delete)
    db.save_model_price("zai", "glm-x", 1_000_000, None, 2_000_000, None)
    deleters[0].join(10)

    assert not deleters[0].is_alive() and accounts.get_user(member) is None
    assert rollup_drift() == {}
    assert db.fetchone("SELECT COUNT(*) AS n FROM usage_rollup_hourly WHERE user_id=?", (member,))["n"] == 0
    assert int(db.fetchone("SELECT cost_micros FROM credentials WHERE id=?", (cred,))["cost_micros"]) == PER_EVENT


def test_a_lock_conflict_in_one_window_retries_it_and_finishes_the_walk(fresh_db, monkeypatch):
    monkeypatch.setattr(db, "REPRICE_WINDOW", 1)
    _seed()
    batch = db._reprice_batch
    calls = []

    def deadlocked_once(conn, *args):
        calls.append(1)
        if len(calls) == 2:
            raise pymysql.OperationalError(1213, "Deadlock found when trying to get lock")
        batch(conn, *args)

    monkeypatch.setattr(db, "_reprice_batch", deadlocked_once)
    db.save_model_price("zai", "glm-x", 1_000_000, None, 2_000_000, None)

    assert len(calls) == 4
    assert cost_sums()["events"] == 3 * PER_EVENT and rollup_drift() == {}
    assert _pending() == []


@pytest.mark.parametrize("change", ["save", "delete"])
def test_a_walk_cut_short_by_a_restart_resumes_at_boot(fresh_db, monkeypatch, change):
    monkeypatch.setattr(db, "REPRICE_WINDOW", 1)
    db.execute("DELETE FROM schema_meta WHERE key LIKE 'reprice_pending:%'")
    _seed()
    if change == "delete":
        db.save_model_price("zai", "glm-x", 1_000_000, None, 2_000_000, None)
    batch = db._reprice_batch
    calls = []

    def dies_in_the_second_window(conn, *args):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("process killed")
        batch(conn, *args)

    monkeypatch.setattr(db, "_reprice_batch", dies_in_the_second_window)
    with pytest.raises(RuntimeError):
        if change == "save":
            db.save_model_price("zai", "glm-x", 1_000_000, None, 2_000_000, None)
        else:
            db.delete_model_price("zai", "glm-x")
    monkeypatch.setattr(db, "_reprice_batch", batch)
    assert len(_pending()) == 1

    db.resume_reprices()

    # Only the member's two events went through the API key.
    per_event = PER_EVENT if change == "save" else 0
    assert cost_sums() == {"events": 3 * per_event, "hourly": 3 * per_event, "credentials": 3 * per_event,
                           "api_keys": 2 * per_event, "providers": 3 * per_event}
    assert rollup_drift() == {}
    assert _pending() == []
