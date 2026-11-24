"""The usage page reads whole hours from usage_rollup_hourly and only a
window's partial edge hours from usage_events: every total must equal the
same sums over the raw events, through reprices and price deletes.

The analytics clock is pinned: NOW is Wednesday 2026-11-11 12:30:00 UTC, so
the 24h window starts and ends inside an hour.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import accounts, db, pricing, stats
from tests.core.usage._support import api_key, rollup_drift, source, user

NOW = datetime(2026, 11, 11, 12, 30, tzinfo=timezone.utc)

_RAW = """SELECT COALESCE(SUM(CASE WHEN total_tokens>0 THEN total_tokens ELSE prompt_tokens+output_tokens END),0) AS tokens,
                 COALESCE(SUM(count_request),0) AS requests,
                 COALESCE(SUM(prompt_tokens),0) AS prompt,
                 COALESCE(SUM(cached_tokens),0) AS cached,
                 COALESCE(SUM(CASE WHEN count_request=1 AND request_status='failed' THEN 1 ELSE 0 END),0) AS failed,
                 COALESCE(SUM(COALESCE(reported_cost_micros, estimated_cost_micros)),0) AS cost,
                 COALESCE(SUM(estimated_cost_micros),0) AS estimated_cost,
                 COALESCE(SUM(CASE WHEN count_request=1 AND (reported_cost_micros IS NOT NULL OR (pricing_known=1
                                   AND NOT (usage_status='unknown' AND request_status<>'failed'
                                            AND total_tokens<=0 AND prompt_tokens+output_tokens<=0)))
                                   THEN 1 ELSE 0 END),0) AS priced,
                 COALESCE(SUM(CASE WHEN count_request=1 AND source_type='openrouter' THEN 1 ELSE 0 END),0) AS untracked
          FROM usage_events WHERE created_at>=? AND created_at<?"""


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(stats, "datetime", Clock)


def _at(**delta):
    return (NOW - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _raw(start, end, uid=None):
    sql, args = _RAW, [start, end]
    if uid is not None:
        sql, args = sql + " AND user_id=?", args + [uid]
    return {key: int(value) for key, value in db.fetchone(sql, tuple(args)).items()}


def _seed():
    uid, other = user(), user("other@core.test")
    key = api_key(uid)
    sid = source("zai")
    cred = accounts.add_credential(sid, "pool", "secret", donated_by=uid)
    db.save_model_price("zai", "glm-x", 1_000_000, 100_000, 2_000_000, None)
    usage = {"prompt_tokens": 1000, "cached_tokens": 300, "completion_tokens": 200, "usage_status": "complete"}
    # Both edges of the 24h window (12:30:01 yesterday .. 12:30:01 today), whole
    # hours between, the previous window, and a month back.
    for n, at in enumerate((_at(hours=24, minutes=-10), _at(hours=24, minutes=10), _at(hours=23, minutes=55),
                            _at(hours=5), _at(minutes=20), _at(minutes=31), _at(hours=40), _at(days=20))):
        db.record_usage(uid, sid, cred, "glm-x", {**usage, "prompt_tokens": 1000 + n}, at, provider="zai",
                        key_id=key if n % 2 else None)
    db.record_usage(uid, sid, cred, "glm-y", dict(usage), _at(hours=2), provider="zai", request_status="failed")
    db.record_usage(uid, sid, None, "glm-x", dict(usage), _at(hours=3), provider="zai", count_request=False)
    db.record_usage(uid, None, None, "x/y", {**usage, "cost_micros": 5}, _at(minutes=10), provider="openrouter")
    db.record_usage(uid, sid, cred, "glm-x", {**usage, "cost_micros": 777}, _at(hours=6), provider="zai")
    db.record_usage(other, sid, None, "glm-x", dict(usage), _at(hours=4), provider="zai")
    return uid


def _assert_page_matches_events(uid):
    for range_id in ("24h", "7d", "30d", "all"):
        for scope in ({}, {"all_users": True}):
            data = stats.usage_analytics(uid, range_id=range_id, **scope)
            start, end = data["range"]["from"], data["range"]["to"]
            want = _raw(start, end, None if scope else uid)
            got = {key: data["summary"][key] for key in want}
            assert got == want, (range_id, scope)
            assert sum(point["tokens"] for point in data["series"]) == want["tokens"]
            assert sum(model["cost"] for model in data["models"]) == want["cost"]
            if range_id != "all":
                duration = stats._RANGES[range_id][0]
                prev_start = (datetime.fromisoformat(end.replace("Z", "+00:00")) - 2 * duration).strftime(
                    "%Y-%m-%dT%H:%M:%SZ")
                prev = _raw(prev_start, start, None if scope else uid)
                assert data["previous"] == {key: prev[key] for key in ("tokens", "requests", "cost")}


def test_page_totals_from_the_rollup_equal_the_raw_event_totals(fresh_db):
    uid = _seed()
    _assert_page_matches_events(uid)

    db.save_model_price("zai", "glm-y", 3_000_000, None, 1_000_000, None)
    db.save_model_price("zai", "glm-x", 5_000_000, None, 7_000_000, 9_000_000)
    _assert_page_matches_events(uid)
    db.delete_model_price("zai", "glm-x")
    _assert_page_matches_events(uid)
    assert rollup_drift() == {}


def test_the_recent_list_is_the_latest_events_of_the_window(fresh_db):
    uid = user()
    usage = {"prompt_tokens": 10, "usage_status": "complete"}
    for n in range(30):
        db.record_usage(uid, None, None, f"old-{n}", dict(usage), _at(days=2, minutes=n), provider="zai")
    for n in range(3):
        db.record_usage(uid, None, None, f"new-{n}", dict(usage), _at(minutes=n), provider="zai")

    recent = stats.usage_analytics(uid, range_id="30d")["recent"]
    assert [row["model"] for row in recent] == ["new-0", "new-1", "new-2"] + [f"old-{n}" for n in range(22)]

    for n in range(30):
        db.record_usage(uid, None, None, f"hot-{n}", dict(usage), _at(seconds=200 + n), provider="zai")
    recent = stats.usage_analytics(uid, range_id="30d")["recent"]
    assert [row["model"] for row in recent] == ["new-0", "new-1", "new-2"] + [f"hot-{n}" for n in range(22)]


def test_a_reprice_sets_each_event_exactly_in_fixed_size_statements(fresh_db, monkeypatch):
    monkeypatch.setattr(db, "REPRICE_CHUNK", 2)
    uid = user()
    sid = source("zai")
    for prompt in (1000, 2000, 3000, 4000, 5000):
        db.record_usage(uid, sid, None, "glm-x", {"prompt_tokens": prompt, "usage_status": "complete"},
                        "2026-11-10T09:15:00Z", provider="zai")

    db.save_model_price("zai", "glm-x", 1_000_000, None, 0, None)
    costs = [int(r["estimated_cost_micros"]) for r in db.fetchall(
        "SELECT estimated_cost_micros FROM usage_events ORDER BY id")]
    assert costs == [1000, 2000, 3000, 4000, 5000] and rollup_drift() == {}

    db.save_model_price("zai", "glm-x", 3_000_000, None, 0, None)
    assert int(db.fetchone("SELECT SUM(cost) AS c FROM usage_rollup_hourly")["c"]) == 45_000
    db.delete_model_price("zai", "glm-x")
    assert db.fetchone("SELECT COUNT(*) AS n FROM usage_events WHERE pricing_known=1")["n"] == 0
    assert rollup_drift() == {}


def test_an_unchanged_openrouter_price_is_not_saved_again(fresh_db, monkeypatch):
    catalog = [{"id": "x-ai/grok-9", "pricing": {"prompt": "0.000002", "completion": "0.000006"}}]

    def handler(request):
        return httpx.Response(200, json={"data": catalog if request.url.params["model_authors"] == "x-ai" else []})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real(transport=httpx.MockTransport(handler)))
    asyncio.run(pricing.sync_openrouter_prices())
    db.execute("UPDATE model_prices SET synced_at='2026-01-01T00:00:00Z'")
    saves = []
    monkeypatch.setattr(db, "save_model_price", lambda *a, **kw: saves.append(a))

    result = asyncio.run(pricing.sync_openrouter_prices())

    assert saves == [] and result["models"] == ["x-ai/grok-9"]
    rows = db.fetchall("SELECT synced_at FROM model_prices")
    assert len(rows) == 2 and all(row["synced_at"] != "2026-01-01T00:00:00Z" for row in rows)
    catalog[0]["pricing"]["prompt"] = "0.000003"
    asyncio.run(pricing.sync_openrouter_prices())
    assert len(saves) == 2


def test_batched_card_coverage_matches_one_credential_at_a_time(fresh_db):
    uid = user()
    sid = source("grok")
    creds = [accounts.add_credential(sid, "pool", "secret", donated_by=uid) for _ in range(3)]
    db.save_model_price("grok", "grok-4.5", 2_000_000, None, 6_000_000, None)
    db.record_usage(uid, sid, creds[0], "grok-4.5", {"prompt_tokens": 1000, "usage_status": "complete"}, provider="grok")
    db.record_usage(uid, sid, creds[1], "grok-4.5", {"prompt_tokens": 500, "usage_status": "complete"}, provider="grok")
    db.record_usage(uid, sid, creds[1], "unpriced", {"prompt_tokens": 9, "usage_status": "complete"}, provider="grok")
    rows = db.fetchall("SELECT * FROM credentials WHERE source_id=? ORDER BY id", (sid,))

    coverage = stats.fuse_coverage(rows)

    assert [stats.fuse_view(row, coverage=coverage) for row in rows] == [stats.fuse_view(row) for row in rows]
    assert set(coverage) == {creds[0], creds[1]}


def test_a_half_hour_offset_viewer_gets_the_raw_event_page_from_the_rollup(fresh_db, monkeypatch):
    uid, other = user(), user("other@core.test")
    key = api_key(uid)
    db.save_model_price("zai", "glm-x", 1_000_000, None, 2_000_000, None)
    # A +5:30 viewer's midnight is 18:30 UTC: events on both sides of it, in
    # hours it does not split, and at both edges of the windows.
    for n, at in enumerate(("2026-11-10T18:10:00Z", "2026-11-10T18:40:00Z", "2026-11-10T20:15:00Z",
                            "2026-11-09T00:45:00Z", "2026-11-09T06:20:00Z", "2026-11-04T18:29:59Z",
                            "2026-10-13T18:30:00Z", _at(days=30, minutes=-5), _at(days=7, minutes=-5),
                            _at(minutes=5))):
        db.record_usage(uid, None, None, "glm-x" if n % 3 else "glm-y", {"prompt_tokens": 1000 + 37 * n},
                        at, provider="zai", key_id=key if n % 2 else None)
    db.record_usage(other, None, None, "glm-x", {"prompt_tokens": 5}, "2026-11-10T18:50:00Z", provider="zai")

    def pages():
        token = stats.display_zone.set(timezone(timedelta(hours=5, minutes=30)))
        try:
            return [stats.usage_analytics(uid, range_id=range_id, **scope)
                    for range_id in ("24h", "3d", "7d", "30d", "all") for scope in ({}, {"all_users": True})]
        finally:
            stats.display_zone.reset(token)

    raw_spans = []
    real_events = stats._event_rows

    def spy(granule, ranges, *args, **kwargs):
        raw_spans.extend(ranges)
        return real_events(granule, ranges, *args, **kwargs)

    monkeypatch.setattr(stats, "_event_rows", spy)
    got = pages()
    monkeypatch.setattr(stats, "_window_rows", lambda user_id, start, end, provider, all_users, *, subject_id=None,
                        granule=3600, **_: real_events(granule, [(start, end)], user_id, provider, all_users,
                                                       subject_id=subject_id))
    assert got == pages()
    # Day buckets: only the hour a viewer's midnight splits (18:00 UTC) is
    # read from the events; the whole hours around it stay rollup rows.
    assert ("2026-11-10T18:00:00Z", "2026-11-10T19:00:00Z") in raw_spans
    assert ("2026-11-10T20:00:00Z", "2026-11-10T21:00:00Z") not in raw_spans
