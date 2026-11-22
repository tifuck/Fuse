"""Usage cost: the recorded event, the credential total and the hourly rollup
agree with the saved price, before and after a reprice.

Sample for the money pattern: assert exact integer micros computed by hand
from the published rates, and the invariant that every rollup equals the sum
of its events. Rates are micros per million tokens: 1_000_000 is $1/M.
"""
from app import accounts, db, stats
from tests.core.usage._support import api_key, cost_sums, rollup_drift, source, user

# $1 per million input tokens, $2 per million output tokens (micros per million).
INPUT, OUTPUT, CACHED = 1_000_000, 2_000_000, 100_000


def _pool_credential(uid, kind="zai"):
    source_id = source(kind)
    return source_id, accounts.add_credential(source_id, "pool", "secret", donated_by=uid)


def _record(uid, source_id, cred, model="glm-x", provider="zai", key_id=None, **usage):
    db.record_usage(uid, source_id, cred, model, {"usage_status": "complete", **usage}, provider=provider, key_id=key_id)
    return db.fetchone("SELECT * FROM usage_events ORDER BY id DESC LIMIT 1")


def _totals():
    event = db.fetchone("SELECT COALESCE(SUM(estimated_cost_micros),0) AS c FROM usage_events")["c"]
    hourly = db.fetchone("SELECT COALESCE(SUM(cost),0) AS c FROM usage_rollup_hourly")["c"]
    credential = db.fetchone("SELECT COALESCE(SUM(cost_micros),0) AS c FROM credentials")["c"]
    return int(event), int(hourly), int(credential)


def test_usage_is_billed_at_the_saved_rates(fresh_db):
    uid = user()
    source_id, cred = _pool_credential(uid)
    db.save_model_price("zai", "glm-x", INPUT, None, OUTPUT, None)
    _record(uid, source_id, cred, prompt_tokens=1000, completion_tokens=400)

    # 1000 * $1/M + 400 * $2/M = 1000 + 800 micros.
    assert _totals() == (1800, 1800, 1800)


def test_cached_prompt_tokens_bill_once_at_the_cached_rate(fresh_db):
    uid = user()
    source_id, cred = _pool_credential(uid)
    db.save_model_price("zai", "glm-x", INPUT, CACHED, OUTPUT, None)
    _record(uid, source_id, cred, prompt_tokens=1000, cached_tokens=600, completion_tokens=0)

    # 400 uncached * $1/M + 600 cached * $0.10/M = 400 + 60 micros.
    assert _totals() == (460, 460, 460)


def test_a_later_price_reprices_history_and_every_rollup(fresh_db):
    uid = user()
    source_id, cred = _pool_credential(uid)
    _record(uid, source_id, cred, prompt_tokens=1000, completion_tokens=400)
    _record(uid, source_id, cred, prompt_tokens=1000, completion_tokens=400)
    assert _totals() == (0, 0, 0)  # no price yet: nothing estimated

    db.save_model_price("zai", "glm-x", INPUT, None, OUTPUT, None)
    assert _totals() == (3600, 3600, 3600)

    db.save_model_price("zai", "glm-x", INPUT // 2, None, OUTPUT // 2, None)
    assert _totals() == (1800, 1800, 1800)


def test_cache_writes_bill_at_the_write_rate_or_as_input_when_none_is_published(fresh_db):
    uid = user()
    source_id, cred = _pool_credential(uid)
    db.save_model_price("zai", "write-known", 4_000_000, 400_000, 20_000_000, None, cache_write_rate=5_000_000)
    db.save_model_price("zai", "write-unknown", 2_000_000, 500_000, 6_000_000, None)

    known = _record(uid, source_id, cred, "write-known", prompt_tokens=1_000_000, cache_write_tokens=600_000)
    unknown = _record(uid, source_id, cred, "write-unknown", prompt_tokens=500_000, cache_write_tokens=100_000)

    # 400k plain at $4 + 600k written at $5; the written tokens are not also plain input.
    assert known["estimated_cost_micros"] == 400_000 * 4 + 600_000 * 5
    # No write rate: written tokens bill as ordinary input.
    assert unknown["estimated_cost_micros"] == 400_000 * 2 + 100_000 * 2


def test_cached_tokens_bill_as_input_unless_a_cached_rate_is_published(fresh_db):
    uid = user()
    source_id, cred = _pool_credential(uid)
    db.save_model_price("zai", "no-cache-rate", 2_000_000, None, 0, None)
    db.save_model_price("zai", "free-cache", 2_000_000, 0, 0, None)

    unknown = _record(uid, source_id, cred, "no-cache-rate", prompt_tokens=1000, cached_tokens=600)
    free = _record(uid, source_id, cred, "free-cache", prompt_tokens=1000, cached_tokens=600)

    assert unknown["estimated_cost_micros"] == 1000 * 2
    assert free["estimated_cost_micros"] == 400 * 2  # a published $0 is a rate, not "unknown"
    assert _totals() == (2800, 2800, 2800)


def test_reasoning_is_part_of_output_and_never_billed_on_top(fresh_db):
    uid = user()
    source_id, cred = _pool_credential(uid)
    db.save_model_price("zai", "thinker", 0, None, 20_000_000, 30_000_000)
    db.save_model_price("zai", "plain", 0, None, 6_000_000, None)

    split = _record(uid, source_id, cred, "thinker", completion_tokens=100, reasoning_tokens=60)
    # A provider reporting more reasoning than completion: output is the larger, once.
    over = _record(uid, source_id, cred, "thinker", completion_tokens=50, reasoning_tokens=80)
    plain = _record(uid, source_id, cred, "plain", completion_tokens=100, reasoning_tokens=60)

    assert (split["output_tokens"], split["visible_tokens"]) == (100, 40)
    assert split["estimated_cost_micros"] == 40 * 20 + 60 * 30
    assert over["output_tokens"] == 80 and over["estimated_cost_micros"] == 80 * 30
    assert plain["estimated_cost_micros"] == 100 * 6


def test_a_long_context_tier_is_inclusive_and_prices_the_whole_request(fresh_db):
    uid = user()
    source_id, cred = _pool_credential(uid)
    db.save_model_price("zai", "tiered", 2_000_000, 500_000, 6_000_000, None, tiers=[
        {"min_prompt_tokens": 200_000, "input_rate": 4_000_000, "cached_rate": 1_000_000, "output_rate": 12_000_000},
    ])

    below = _record(uid, source_id, cred, "tiered", prompt_tokens=199_999, cached_tokens=100_000, completion_tokens=1000)
    at = _record(uid, source_id, cred, "tiered", prompt_tokens=200_000, cached_tokens=100_000, completion_tokens=1000)

    assert below["estimated_cost_micros"] == 99_999 * 2 + 100_000 // 2 + 1000 * 6
    assert at["estimated_cost_micros"] == 100_000 * 4 + 100_000 * 1 + 1000 * 12


def test_a_tier_without_cache_rates_keeps_published_base_rates_else_bills_tier_input(fresh_db):
    uid = user()
    source_id, cred = _pool_credential(uid)
    tier = [{"min_prompt_tokens": 272_000, "input_rate": 4_000_000, "cached_rate": None,
             "cache_write_rate": None, "output_rate": None}]
    db.save_model_price("zai", "base-cache", 2_000_000, 200_000, 6_000_000, None,
                        cache_write_rate=2_500_000, tiers=tier)
    db.save_model_price("zai", "no-cache", 2_000_000, None, 6_000_000, None, tiers=tier)

    costs = [
        _record(uid, source_id, cred, model, prompt_tokens=272_000, **{field: 272_000})["estimated_cost_micros"]
        for model in ("base-cache", "no-cache")
        for field in ("cached_tokens", "cache_write_tokens")
    ]

    # Published base cache rates still apply inside the tier; unpublished
    # ones bill as input, which in the tier is the tier's input rate.
    assert costs == [272_000 * 2 // 10, 272_000 * 25 // 10, 272_000 * 4, 272_000 * 4]


def test_legacy_long_context_columns_apply_from_the_threshold(fresh_db):
    uid = user()
    source_id, cred = _pool_credential(uid)
    db.save_model_price("zai", "long", 1_000_000, 100_000, 2_000_000, None, long_context_threshold=128_000,
                        long_input_rate=3_000_000, long_output_rate=9_000_000)
    db.save_model_price("zai", "long-no-cache", 1_000_000, None, 2_000_000, None, long_context_threshold=128_000,
                        long_input_rate=3_000_000, long_output_rate=9_000_000)

    short = _record(uid, source_id, cred, "long", prompt_tokens=127_999, completion_tokens=1000)
    long = _record(uid, source_id, cred, "long", prompt_tokens=128_000, cached_tokens=28_000, completion_tokens=1000)
    no_cache = _record(uid, source_id, cred, "long-no-cache", prompt_tokens=128_000, cached_tokens=28_000,
                       completion_tokens=1000)

    assert short["estimated_cost_micros"] == 127_999 + 1000 * 2
    # No long-context cached rate: like a tier, the published base cached rate
    # still applies; only an unpublished one bills as long-context input.
    assert long["estimated_cost_micros"] == 100_000 * 3 + 28_000 // 10 + 1000 * 9
    assert no_cache["estimated_cost_micros"] == 128_000 * 3 + 1000 * 9


def test_estimates_round_to_the_nearest_micro_per_event(fresh_db):
    uid = user()
    source_id, cred = _pool_credential(uid)
    db.save_model_price("zai", "half", 500_000, None, 0, None)
    db.save_model_price("zai", "under", 499_999, None, 0, None)

    assert _record(uid, source_id, cred, "half", prompt_tokens=1)["estimated_cost_micros"] == 1
    assert _record(uid, source_id, cred, "half", prompt_tokens=3)["estimated_cost_micros"] == 2
    assert _record(uid, source_id, cred, "under", prompt_tokens=1)["estimated_cost_micros"] == 0


def test_a_reported_cost_wins_over_the_estimate_and_survives_reprices(fresh_db):
    uid = user()
    source_id, cred = _pool_credential(uid)
    key = api_key(uid)
    db.save_model_price("zai", "glm-x", INPUT, None, OUTPUT, None)
    row = _record(uid, source_id, cred, key_id=key, prompt_tokens=1000, completion_tokens=400, cost_micros=12_345)

    assert (row["reported_cost_micros"], row["estimated_cost_micros"], row["pricing_known"]) == (12_345, 1800, 1)
    assert set(cost_sums().values()) == {12_345}

    db.save_model_price("zai", "glm-x", INPUT * 3, None, OUTPUT * 3, None)
    assert db.fetchone("SELECT estimated_cost_micros FROM usage_events")["estimated_cost_micros"] == 5400
    db.delete_model_price("zai", "glm-x")
    assert set(cost_sums().values()) == {12_345}
    assert rollup_drift() == {}


def test_a_request_without_usage_is_unpriced_unless_it_failed(fresh_db):
    uid = user()
    source_id, cred = _pool_credential(uid)
    for model in ("silent", "refused"):
        db.save_model_price("zai", model, INPUT, None, OUTPUT, None)
    db.record_usage(uid, source_id, cred, "silent", {}, provider="zai")
    db.record_usage(uid, source_id, cred, "refused", {}, provider="zai", request_status="failed")

    for rate in (INPUT, INPUT * 2):  # recorded, then repriced
        db.save_model_price("zai", "silent", rate, None, OUTPUT, None)
        db.save_model_price("zai", "refused", rate, None, OUTPUT, None)
        models = {m["name"]: m for m in stats.usage_analytics(uid, range_id="24h")["models"]}
        # Nothing says what the success used; a failed request with no usage cost nothing.
        assert (models["silent"]["cost_known"], models["refused"]["cost_known"]) == (False, True)
        hours = {r["model"]: (r["priced"], r["unpriced_events"]) for r in db.fetchall(
            "SELECT model, priced, unpriced_events FROM usage_rollup_hourly")}
        assert hours == {"silent": (0, 1), "refused": (1, 0)}
        assert rollup_drift() == {}


def test_openrouter_usage_counts_tokens_but_never_cost(fresh_db):
    uid = user()
    source_id, cred = _pool_credential(uid, kind="openrouter")
    model = "openrouter/x-ai/grok-9"
    db.save_model_price("openrouter", model, INPUT, None, OUTPUT, None)
    row = _record(uid, source_id, cred, model, provider="openrouter",
                  prompt_tokens=1000, completion_tokens=400, cost_micros=999)
    db.save_model_price("openrouter", model, INPUT * 2, None, OUTPUT * 2, None)

    # Billed 1:1 on the user's own key: tokens count, cost views stay at zero.
    assert (row["reported_cost_micros"], row["estimated_cost_micros"], row["pricing_known"]) == (None, 0, 0)
    assert db.fetchone("SELECT estimated_cost_micros FROM usage_events")["estimated_cost_micros"] == 0
    assert set(cost_sums().values()) == {0}
    provider = db.fetchone("SELECT * FROM provider_usage WHERE provider='openrouter'")
    assert (provider["total_tokens"], provider["requests"]) == (1400, 1)


def test_token_counts_are_clamped_and_totals_never_undercount(fresh_db):
    uid = user()
    source_id, cred = _pool_credential(uid)
    db.record_usage(uid, source_id, cred, "glm-x",
                    {"prompt_tokens": 50, "cached_tokens": 999, "output_tokens": -5, "cost_micros": -100,
                     "usage_status": "weird"},
                    provider="zai", request_status="bogus", count_request=False)
    clamped = db.fetchone("SELECT * FROM usage_events ORDER BY id DESC LIMIT 1")
    split = _record(uid, source_id, cred, prompt_tokens=100, cached_tokens=60, cache_write_tokens=90,
                    completion_tokens=50, total_tokens=120)

    assert (clamped["cached_tokens"], clamped["output_tokens"], clamped["reported_cost_micros"]) == (50, 0, 0)
    assert (clamped["usage_status"], clamped["request_status"], clamped["count_request"]) == ("unknown", "failed", 0)
    # cached + written can never exceed the prompt; a short total is raised.
    assert (split["cached_tokens"], split["cache_write_tokens"], split["total_tokens"]) == (60, 40, 150)
    # The uncounted attempt adds tokens but no request.
    assert int(db.fetchone("SELECT requests FROM provider_usage")["requests"]) == 1
    assert int(db.fetchone("SELECT requests FROM credentials WHERE id=?", (cred,))["requests"]) == 1
    assert rollup_drift() == {}
