"""Usage analytics (/usage.json and the source cards) on explicit timestamps.

The analytics clock is pinned (stats.datetime) so every bucket boundary is
computed by hand: NOW is Wednesday 2026-11-11 12:30:00 UTC.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app import accounts, db, stats
from tests.core.usage._support import api_key, source, user

NOW = datetime(2026, 11, 11, 12, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(stats, "datetime", Clock)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago(**delta):
    return _iso(NOW - timedelta(**delta))


def _event(uid, at, model="glm-5.3", provider="zai", key=None, status="success", prompt=1000, cached=600,
           output=200, count=True, **extra):
    db.record_usage(uid, None, None, model,
                    {"prompt_tokens": prompt, "cached_tokens": cached, "completion_tokens": output,
                     "usage_status": "complete", **extra},
                    at, key_id=key, provider=provider, request_status=status, count_request=count)


def _priced_zai():
    db.save_model_price("zai", "glm-5.3", 1_000_000, 100_000, 2_000_000, None)


def test_series_buckets_hold_each_event_once_at_its_own_hour(fresh_db):
    uid = user()
    for at in ("2026-11-10T13:00:00Z", "2026-11-11T10:30:00Z", "2026-11-11T11:00:00Z", "2026-11-11T11:59:59Z",
               "2026-11-10T12:00:00Z"):  # the last is before the window: previous period only
        _event(uid, at)

    data = stats.usage_analytics(uid, range_id="24h")

    series = data["series"]
    assert (len(series), series[0]["at"], series[-1]["at"]) == (25, "2026-11-10T12:00:00Z", "2026-11-11T12:00:00Z")
    # 10:30 is half-way into its hour and stays there (truncated, not rounded up).
    assert {p["at"]: p["requests"] for p in series if p["requests"]} == {
        "2026-11-10T13:00:00Z": 1, "2026-11-11T10:00:00Z": 1, "2026-11-11T11:00:00Z": 2}
    assert sum(p["tokens"] for p in series) == data["summary"]["tokens"] == 4 * 1200
    assert (data["summary"]["requests"], data["previous"]["requests"]) == (4, 1)
    assert data["summary"]["token_change"] == 300.0
    assert data["range"]["from"] == "2026-11-10T12:30:01Z"


def test_summary_counts_outcomes_cache_savings_and_sources(fresh_db):
    uid = user()
    key = api_key(uid)
    _priced_zai()
    _event(uid, _ago(hours=1))
    _event(uid, _ago(hours=2), key=key, status="failed")
    _event(uid, _ago(hours=3), key=key, status="cancelled")

    data = stats.usage_analytics(uid, range_id="24h")

    s = data["summary"]
    assert (s["requests"], s["ok_requests"], s["failed"], s["cancelled"]) == (3, 1, 1, 1)
    assert (s["success_rate"], s["per_request"]) == (33.3, 1200)
    # Each event: 400 plain at $1 + 600 cached at $0.10 + 200 out at $2.
    assert s["cost"] == 3 * (400 + 60 + 400) and s["pricing_coverage"] == 100
    # 1800 cached tokens saved (1.00 - 0.10) $/M.
    assert (s["cache_saved"], s["cache_saved_known"]) == (1620, True)
    keys = {row["kind"]: row for row in data["keys"]}
    assert (keys["chat"]["requests"], keys["chat"]["name"]) == (1, "Fuse chat & agents")
    assert (keys["key"]["name"], keys["key"]["requests"], keys["key"]["failed"]) == ("Laptop", 2, 1)
    model = data["models"][0]
    assert (model["failed"], model["error_rate"], model["cache_hit"], model["cost_known"]) == (1, 33.3, 60.0, True)
    assert len(model["spark"]) == len(data["series"])
    assert [r["via"] for r in data["recent"]] == ["Fuse chat", "Laptop", "Laptop"]


def test_short_ranges_still_scan_a_week_for_the_heatmap(fresh_db):
    uid = user()
    _event(uid, _ago(days=5))
    _event(uid, _ago(minutes=5))

    short = stats.usage_analytics(uid, range_id="6h")

    assert short["summary"]["requests"] == 1
    assert short["activity_days"] == 7
    assert sum(requests for _, _, requests in short["activity"]) == 2
    assert all(hour % 3600 == 0 for hour, _, _ in short["activity"])


def test_lifetime_spans_every_event_folds_the_heatmap_and_goes_weekly_past_120_days(fresh_db):
    uid = user()
    key = api_key(uid)
    events = [NOW - timedelta(days=90), NOW - timedelta(days=40), NOW - timedelta(hours=1)]
    _event(uid, _iso(events[0]), key=key)
    _event(uid, _iso(events[1]), model="grok-4.5", provider="grok", status="failed")
    _event(uid, _iso(events[2]))

    data = stats.usage_analytics(uid, range_id="all")

    s = data["summary"]
    assert (s["requests"], s["failed"], s["ok_requests"], s["token_change"]) == (3, 1, 2, None)
    assert data["range"]["bucket"] == "daily" and data["range"]["first_at"] == data["range"]["from"]
    assert len(data["series"]) == 91 and sum(p["requests"] for p in data["series"]) == 3
    assert {p["id"]: p["requests"] for p in data["providers"]} == {"zai": 2, "grok": 1}
    assert not any(m["is_new"] for m in data["models"])
    # Past a month, hourly cells fold into one week: each event keeps its
    # weekday and hour.
    cells = [(datetime.fromtimestamp(hour, timezone.utc), requests) for hour, _, requests in data["activity"]]
    assert sorted((c.weekday(), c.hour, n) for c, n in cells) == sorted((e.weekday(), e.hour, 1) for e in events)

    _event(uid, _ago(days=200))
    weekly = stats.usage_analytics(uid, range_id="all")
    assert weekly["range"]["bucket_seconds"] == 7 * 86400
    assert sum(p["requests"] for p in weekly["series"]) == 4
    assert all(datetime.fromisoformat(p["at"].replace("Z", "+00:00")).weekday() == 0 for p in weekly["series"])


def test_all_users_split_accounts_by_provider_and_keep_rows_of_deleted_users(fresh_db):
    admin = user("admin@core.test", admin=True)
    other = user("other@core.test")
    gone = user("gone@core.test")
    _event(admin, _ago(hours=1), prompt=1000, output=0, cached=0)
    _event(admin, _ago(hours=2), model="grok-4.5", provider="grok", prompt=300, output=0, cached=0)
    _event(other, _ago(hours=1), prompt=500, output=0, cached=0)
    _event(gone, _ago(hours=1), prompt=10, output=0, cached=0)
    db.execute("DELETE FROM users WHERE id=?", (gone,))

    data = stats.usage_analytics(admin, range_id="30d", all_users=True)

    users = {u["email"]: u for u in data["users"]}
    assert [(p["id"], p["tokens"], p["requests"]) for p in users["admin@core.test"]["providers"]] == [
        ("zai", 1000, 1), ("grok", 300, 1)]
    assert [(p["id"], p["tokens"]) for p in users["other@core.test"]["providers"]] == [("zai", 500)]
    assert users[f"user {gone}"]["requests"] == 1
    assert data["summary"]["requests"] == 4
    split = {}
    for point in data["series"]:
        for pid, (tokens, requests, _cost) in point["providers"].items():
            t, r = split.get(pid, (0, 0))
            split[pid] = (t + tokens, r + requests)
    assert split == {"zai": (1510, 3), "grok": (300, 1)}

    mine = stats.usage_analytics(admin, range_id="24h")
    theirs = stats.usage_analytics(admin, range_id="24h", subject_id=other, include_accounts=True)
    assert mine["summary"]["requests"] == 2 and mine["users"] == []
    assert (theirs["summary"]["requests"], theirs["summary"]["tokens"]) == (1, 500)
    assert {"admin@core.test", "other@core.test"} <= {row["email"] for row in theirs["account_options"]}


def test_rates_describe_priced_traffic_and_openrouter_is_neither_priced_nor_missing(fresh_db):
    uid = user()
    db.save_model_price("zai", "glm-5.3", 2_000_000, None, 0, None)
    _event(uid, _ago(hours=1), prompt=1000, cached=0, output=0)
    _event(uid, _ago(hours=1), model="qwen3", provider="llama", prompt=9000, cached=0, output=0)
    _event(uid, _ago(hours=1), model="openrouter/x/y", provider="openrouter", prompt=5000, cached=0, output=0)

    data = stats.usage_analytics(uid, range_id="24h")

    s = data["summary"]
    # 2000 micros over the 1000 priced tokens, not all 15,000.
    assert (s["blended"], s["cost_per_request"]) == (2.0, 2000)
    # OpenRouter bills the user's own key: 1 of the 2 costable requests is priced.
    assert s["pricing_coverage"] == 50
    models = {m["provider"]: m for m in data["models"]}
    assert (models["openrouter"]["cost_tracked"], models["zai"]["cost_tracked"]) == (False, True)
    assert (models["llama"]["cost_known"], models["zai"]["cost_known"]) == (False, True)


def test_an_unpriced_uncounted_attempt_makes_the_model_cost_incomplete(fresh_db):
    uid = user()
    _event(uid, _ago(hours=2), model="unpriced", prompt=9000, cached=0, output=0, status="failed", count=False)
    _event(uid, _ago(hours=1), model="unpriced", prompt=1000, cached=0, output=0, cost_micros=2000)

    data = stats.usage_analytics(uid, range_id="24h")

    model = data["models"][0]
    assert (model["tokens"], model["requests"], model["cost"]) == (10_000, 1, 2000)
    assert (model["cost_known"], model["per_million"]) == (False, None)
    assert data["providers"][0]["cost_known"] is False


def test_a_provider_filter_and_an_empty_range(fresh_db):
    uid = user()
    _event(uid, _ago(hours=1))
    _event(uid, _ago(hours=1), model="grok-4.5", provider="grok")

    grok = stats.usage_analytics(uid, range_id="24h", provider="grok")
    empty = stats.usage_analytics(uid, range_id="30m", provider="kimi")

    assert [m["name"] for m in grok["models"]] == ["grok-4.5"] and grok["summary"]["requests"] == 1
    assert {"grok", "zai"} <= {row["id"] for row in grok["provider_options"]}
    assert empty["summary"]["empty"] and empty["summary"]["success_rate"] is None
    assert (empty["models"], empty["recent"]) == ([], [])


def test_account_totals_partition_tokens_without_double_counting(fresh_db):
    uid = user()
    key = api_key(uid)
    db.record_usage(uid, None, None, "glm-5.3",
                    {"prompt_tokens": 1000, "cached_tokens": 600, "completion_tokens": 300, "reasoning_tokens": 100,
                     "usage_status": "complete"}, key_id=key, provider="zai")
    db.record_usage(uid, None, None, "grok-4.5", {"prompt_tokens": 500, "completion_tokens": 50,
                                                  "usage_status": "complete"}, provider="grok")

    usage = stats.current_usage(uid)

    totals = usage["totals"]
    assert (totals["prompt_tokens"], totals["output_tokens"], totals["requests"]) == (1500, 350, 2)
    mix = {part["name"]: part["tokens"] for part in usage["mix"]}
    assert mix == {"uncached input": 900, "cached read": 600, "visible output": 250, "reasoning": 100}
    assert sum(mix.values()) == totals["prompt_tokens"] + totals["output_tokens"]
    assert {k["id"]: k["total_tokens"] for k in usage["api_keys"]}[key] == 1300


def test_a_source_card_shows_api_equivalent_cost_only_when_it_is_known(fresh_db):
    uid = user()
    sid = source("grok")
    cred = accounts.add_credential(sid, "pool", "secret", donated_by=uid)
    db.save_model_price("grok", "grok-4.5", 2_000_000, None, 6_000_000, None)
    # The provider's reported cost is what the plan billed; the card shows the API equivalent.
    db.record_usage(uid, sid, cred, "grok-4.5", {"prompt_tokens": 1000, "completion_tokens": 100, "cost_micros": 100,
                                                 "usage_status": "complete"}, provider="grok")

    view = stats.fuse_view(db.fetchone("SELECT * FROM credentials WHERE id=?", (cred,)))
    assert (view["cost_micros"], view["cost_label"], view["cost_partial"]) == (2600, "$0.0026", False)

    db.record_usage(uid, sid, cred, "grok-unpriced", {"prompt_tokens": 10, "usage_status": "complete"}, provider="grok")
    view = stats.fuse_view(db.fetchone("SELECT * FROM credentials WHERE id=?", (cred,)))
    assert view["cost_partial"] and not view["cost_unavailable"]

    db.delete_model_price("grok", "grok-4.5")
    view = stats.fuse_view(db.fetchone("SELECT * FROM credentials WHERE id=?", (cred,)))
    assert (view["cost_label"], view["cost_unavailable"]) == ("", True)
