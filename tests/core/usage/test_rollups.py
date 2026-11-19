"""Rollup invariant: every rollup (credentials, api_keys, provider_usage,
usage_hourly) equals the sum of its usage_events, whatever happens to prices
and accounts afterwards, including writes that race each other.
"""
import threading

import pytest

from app import accounts, db
from tests.core.helpers import wait_until
from tests.core.usage._support import api_key, cost_sums, rollup_drift, source, user

USAGE = {"prompt_tokens": 1000, "completion_tokens": 400, "usage_status": "complete"}
PER_EVENT = 1000 + 800  # at $1/M in, $2/M out


def _seed():
    """Two users, one shared credential, one API key, three hours, two models."""
    admin = user("admin@core.test", admin=True)
    member = user("member@core.test")
    key = api_key(member)
    source_id = source("zai")
    cred = accounts.add_credential(source_id, "pool", "secret", donated_by=admin)
    for uid, key_id, at in ((admin, None, "2026-11-10T09:15:00Z"), (member, key, "2026-11-10T09:45:00Z"),
                            (member, key, "2026-11-10T10:05:00Z")):
        db.record_usage(uid, source_id, cred, "glm-x", dict(USAGE), at, key_id=key_id, provider="zai")
    return admin, member, source_id, cred, key


def test_reprice_up_down_and_price_delete_move_every_rollup_with_its_events(fresh_db):
    _admin, _member, source_id, cred, _key = _seed()
    db.record_usage(_admin, source_id, cred, "glm-y", dict(USAGE), "2026-11-10T09:20:00Z", provider="zai")
    assert rollup_drift() == {}

    db.save_model_price("zai", "glm-x", 1_000_000, None, 2_000_000, None)
    assert rollup_drift() == {}
    # Only the member's two events went through the API key.
    assert cost_sums() == {"events": 3 * PER_EVENT, "hourly": 3 * PER_EVENT, "credentials": 3 * PER_EVENT,
                           "api_keys": 2 * PER_EVENT, "providers": 3 * PER_EVENT}
    hourly = db.fetchall("SELECT hour, cost_micros FROM usage_hourly WHERE model='glm-x' ORDER BY hour, user_id")
    assert [(row["hour"], row["cost_micros"]) for row in hourly] == [
        ("2026-11-10T09:00:00Z", PER_EVENT), ("2026-11-10T09:00:00Z", PER_EVENT), ("2026-11-10T10:00:00Z", PER_EVENT)]

    db.save_model_price("zai", "glm-x", 3_000_000, None, 6_000_000, None)
    assert rollup_drift() == {}
    assert cost_sums()["credentials"] == 9 * PER_EVENT
    db.save_model_price("zai", "glm-x", 0, None, 0, None)
    assert set(cost_sums().values()) == {0} and rollup_drift() == {}

    db.save_model_price("zai", "glm-x", 1_000_000, None, 2_000_000, None)
    db.delete_model_price("zai", "glm-x")
    assert set(cost_sums().values()) == {0} and rollup_drift() == {}
    assert db.fetchone("SELECT COUNT(*) AS n FROM usage_events WHERE pricing_known=1")["n"] == 0


def test_a_reprice_below_what_a_rollup_holds_is_not_floored_at_zero(fresh_db):
    _seed()
    db.save_model_price("zai", "glm-x", 1_000_000, None, 2_000_000, None)
    db.execute("UPDATE credentials SET cost_micros=0")  # a rollup that drifted low

    db.save_model_price("zai", "glm-x", 0, None, 0, None)

    # The delta lands in full: a clamp would make the drift permanent.
    assert int(db.fetchone("SELECT cost_micros FROM credentials")["cost_micros"]) == -3 * PER_EVENT


def test_reprice_pages_history_by_id_window(fresh_db, monkeypatch):
    # One id per window and another model's event inside the span: each
    # window must move the rollups by exactly what its own events moved.
    monkeypatch.setattr(db, "REPRICE_WINDOW", 1)
    admin, _member, source_id, cred, _key = _seed()
    db.record_usage(admin, source_id, cred, "glm-y", dict(USAGE), "2026-11-10T09:30:00Z", provider="zai")
    db.record_usage(admin, source_id, cred, "glm-x", dict(USAGE), "2026-11-10T09:31:00Z", provider="zai")

    db.save_model_price("zai", "glm-x", 1_000_000, None, 2_000_000, None)

    assert rollup_drift() == {}
    assert db.fetchone("SELECT SUM(cost_micros) AS c FROM usage_hourly WHERE model='glm-x'")["c"] == 4 * PER_EVENT
    assert db.fetchone("SELECT SUM(cost_micros) AS c FROM usage_hourly WHERE model='glm-y'")["c"] == 0
    db.delete_model_price("zai", "glm-x")
    assert rollup_drift() == {} and set(cost_sums().values()) == {0}


def test_a_price_for_one_provider_leaves_the_same_model_name_elsewhere_alone(fresh_db):
    admin, _member, _source_id, _cred, _key = _seed()
    other = source("kimi")
    db.record_usage(admin, other, None, "glm-x", dict(USAGE), "2026-11-10T09:00:00Z", provider="kimi")

    db.save_model_price("zai", "glm-x", 1_000_000, None, 2_000_000, None)

    kimi = db.fetchone("SELECT estimated_cost_micros, pricing_known FROM usage_events WHERE source_type='kimi'")
    assert (kimi["estimated_cost_micros"], kimi["pricing_known"]) == (0, 0)
    assert cost_sums()["events"] == 3 * PER_EVENT and rollup_drift() == {}


def test_deleting_a_user_takes_their_events_out_of_shared_credential_rollups(fresh_db):
    admin, member, _source_id, cred, key = _seed()
    db.save_model_price("zai", "glm-x", 1_000_000, None, 2_000_000, None)

    accounts.delete_user(member, actor_id=admin)

    assert rollup_drift() == {}
    row = db.fetchone("SELECT requests, total_tokens, cost_micros FROM credentials WHERE id=?", (cred,))
    assert (row["requests"], row["total_tokens"], row["cost_micros"]) == (1, 1400, PER_EVENT)
    assert db.fetchone("SELECT COUNT(*) AS n FROM api_keys WHERE id=?", (key,))["n"] == 0
    assert db.fetchone("SELECT COUNT(*) AS n FROM usage_hourly WHERE user_id=?", (member,))["n"] == 0


def test_deleting_a_user_counts_an_event_recorded_after_its_snapshot(fresh_db, monkeypatch):
    admin, member, source_id, cred, key = _seed()
    admins = accounts._admins_locked

    def admins_then_usage(conn):
        # delete_user's transaction is open: an event for the user commits now.
        rows = admins(conn)
        late = threading.Thread(target=db.record_usage, args=(member, source_id, cred, "glm-x", dict(USAGE)),
                                kwargs={"key_id": key, "provider": "zai"})
        late.start()
        late.join(10)
        return rows

    monkeypatch.setattr(accounts, "_admins_locked", admins_then_usage)
    accounts.delete_user(member, actor_id=admin)

    assert rollup_drift() == {}
    assert int(db.fetchone("SELECT requests FROM credentials WHERE id=?", (cred,))["requests"]) == 1


def test_a_price_save_waits_for_a_usage_event_priced_at_the_old_rate(fresh_db, monkeypatch):
    admin, _member, source_id, cred, _key = _seed()
    db.save_model_price("zai", "glm-x", 1_000_000, None, 2_000_000, None)
    priced, release = threading.Event(), threading.Event()
    estimate = db._estimated_cost

    def held_estimate(*args):
        # record_usage has read the old price under its shared lock.
        priced.set()
        release.wait(10)
        return estimate(*args)

    monkeypatch.setattr(db, "_estimated_cost", held_estimate)
    usage = threading.Thread(target=db.record_usage, args=(admin, source_id, cred, "glm-x", dict(USAGE)),
                             kwargs={"provider": "zai"})
    usage.start()
    assert priced.wait(10)
    monkeypatch.setattr(db, "_estimated_cost", estimate)
    save = threading.Thread(target=db.save_model_price, args=("zai", "glm-x", 3_000_000, None, 6_000_000, None))
    save.start()
    try:
        # The save is parked on the price row until the event commits.
        wait_until(_price_save_waiting, message="the price save never blocked on the in-flight usage event")
        assert save.is_alive()
    finally:
        release.set()
        usage.join(10)
        save.join(10)

    assert rollup_drift() == {}
    costs = {row["estimated_cost_micros"] for row in db.fetchall("SELECT estimated_cost_micros FROM usage_events")}
    assert costs == {3 * PER_EVENT}


def _price_save_waiting():
    with db.tx() as conn:
        return conn.native(
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE db=DATABASE() AND info LIKE %s AND info NOT LIKE %s",
            ("%INSERT INTO%model_prices%", "%processlist%")).fetchone()[0]


def test_the_first_price_of_a_model_waits_for_a_usage_event_that_found_none(fresh_db):
    admin, member, source_id, cred, key = _seed()
    holding, release = threading.Event(), threading.Event()

    def hold_key_row():
        # Parks record_usage on its api_keys rollup, after it read "no price".
        with db.tx() as conn:
            conn.execute("SELECT id FROM api_keys WHERE id=? FOR UPDATE", (key,)).fetchone()
            holding.set()
            release.wait(10)

    holder = threading.Thread(target=hold_key_row)
    holder.start()
    assert holding.wait(10)
    usage = threading.Thread(target=db.record_usage, args=(member, source_id, cred, "glm-new", dict(USAGE)),
                             kwargs={"key_id": key, "provider": "zai"})
    save = threading.Thread(target=db.save_model_price, args=("zai", "glm-new", 1_000_000, None, 2_000_000, None))
    try:
        usage.start()
        wait_until(lambda: _waiting_on("%UPDATE `api_keys`%"), message="record_usage never reached its key rollup")
        save.start()
        # The new row's insert waits on the gap record_usage read as empty.
        wait_until(lambda: _price_save_waiting() or not save.is_alive(), message="the price save hung")
        assert save.is_alive()
    finally:
        release.set()
        for thread in (holder, usage, save):
            if thread.ident is not None:
                thread.join(10)

    row = db.fetchone("SELECT estimated_cost_micros, pricing_known FROM usage_events WHERE model='glm-new'")
    assert (int(row["estimated_cost_micros"]), int(row["pricing_known"])) == (PER_EVENT, 1)
    assert rollup_drift() == {}


def _waiting_on(pattern):
    with db.tx() as conn:
        return conn.native(
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE db=DATABASE() AND info LIKE %s AND info NOT LIKE %s",
            (pattern, "%processlist%")).fetchone()[0]


def test_an_absurd_token_count_never_blocks_later_usage_on_the_same_rollups(fresh_db):
    uid = user()
    source_id = source("zai")
    cred = accounts.add_credential(source_id, "pool", "secret", donated_by=uid)
    # A provider reporting garbage: record_usage clamps it to the BIGINT maximum.
    db.record_usage(uid, source_id, cred, "glm-x", {"prompt_tokens": 10**20, "usage_status": "complete"},
                    "2026-11-10T09:00:00Z", provider="zai")

    db.record_usage(uid, source_id, cred, "glm-x", dict(USAGE), "2026-11-10T09:30:00Z", provider="zai")

    assert db.fetchone("SELECT COUNT(*) AS n FROM usage_events")["n"] == 2
    # The rollups saturate at the BIGINT maximum instead of failing the write.
    assert int(db.fetchone("SELECT prompt_tokens FROM credentials WHERE id=?", (cred,))["prompt_tokens"]) == 2**63 - 1
    assert int(db.fetchone("SELECT requests FROM credentials WHERE id=?", (cred,))["requests"]) == 2


def test_credential_week_counters_restart_each_iso_week(fresh_db, monkeypatch):
    uid = user()
    source_id = source("zai")
    cred = accounts.add_credential(source_id, "pool", "secret", donated_by=uid)
    for week in ("2026-11-02", "2026-11-02", "2026-11-09"):
        monkeypatch.setattr(db, "iso_week_start", lambda week=week: week)
        db.record_usage(uid, source_id, cred, "glm-x", dict(USAGE), provider="zai")

    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred,))
    assert (row["week_start"], row["week_tokens"], row["week_requests"]) == ("2026-11-09", 1400, 1)
    assert (row["total_tokens"], row["requests"]) == (3 * 1400, 3)
