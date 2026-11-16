"""Pool usage meters on the Sources page: an unweighted mean of the plans a
user can be routed onto, where a missing reading is never shown as 0%."""
from app import db, stats
from app.sources.pool_pick import USAGE_BUSY
from tests.core.usage._support import grok_extra, pool_account, pool_rows, user


def test_the_pool_meter_is_a_mean_and_an_empty_pool_has_no_reading():
    assert stats.pool_meter([50.0, 50.0])["percent"] == 50.0
    meter = stats.pool_meter([0.0, USAGE_BUSY], plans=["plus", "plus"])
    assert (meter["percent"], meter["busy"], meter["count_label"]) == (49.0, 1, "2 Plus · 1 at cap")
    # A plan name missing for one sample falls back to a plain count.
    assert stats.pool_meter([10.0, 20.0], plans=["pro", ""])["count_label"] == "2 plans"
    empty = stats.pool_meter([])
    assert (empty["available"], empty["percent"], empty["percent_label"]) == (False, None, "—")


def test_the_grok_meter_counts_live_subscribed_plans_and_holds_only_for_others(fresh_db):
    owner, other = user("owner@core.test"), user("other@core.test")
    held = pool_account("grok", owner, grok_extra(80))
    pool_account("grok", other, grok_extra(20))
    pool_account("grok", other, {"access_token": "", "billing": grok_extra(5)["billing"]})  # signed out
    pool_account("grok", other, {"access_token": "t", "billing": {"fetched_at": "x", "tier": "free"}})
    pool_account("grok", other, {"access_token": "t", "billing": {"fetched_at": "x", "tier": "SuperGrok"}})
    db.execute("UPDATE credentials SET pool_held_until='2999-01-01T00:00:00Z' WHERE id=?", (held,))
    rows = pool_rows("grok")

    for_others = stats.grok_pool_meter(rows)
    for_owner = stats.grok_pool_meter(rows, owner_id=owner)

    assert (for_others["percent"], for_others["n"], for_others["count_label"]) == (20.0, 1, "1 plan · 1 no reading")
    assert (for_owner["percent"], for_owner["n"]) == (50.0, 2)


def test_chatgpt_meters_split_free_from_paid_and_read_the_fullest_window(fresh_db):
    owner = user()
    pool_account("chatgpt", owner, {"refresh_token": "r", "billing": {"plan_type": "plus", "windows": [
        {"percent": 30, "seconds": 5 * 3600}, {"percent": 70, "seconds": 7 * 86400}]}})
    pool_account("chatgpt", owner, {"refresh_token": "r", "billing": {"plan_type": "free", "windows": [
        {"percent": 90, "seconds": 5 * 3600}, {"percent": 10, "seconds": 30 * 86400}]}})
    pool_account("chatgpt", owner, {"refresh_token": "r", "billing": {"plan_type": "go"}})

    paid, free = stats.chatgpt_pool_meters(pool_rows("chatgpt"))

    assert (paid["percent"], paid["count_label"]) == (70.0, "1 Plus")
    # Free plans have a monthly window only; a short one is ignored.
    assert (free["percent"], free["count_label"]) == (10.0, "1 Free · 1 no reading")
