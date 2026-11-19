"""Price rows: who may overwrite whom (manual > built-in/synced > auto), the
built-in defaults, auto-pricing from another spelling or the OpenRouter
catalog, and the OpenRouter sync. Every save reprices the model's history.
"""
import asyncio
import json

import httpx
import pytest

from app import db, pricing
from tests.core.usage._support import rollup_drift, source, user


@pytest.fixture
def no_listed_models(monkeypatch):
    # The pool catalogs auto_price also targets are process caches filled by
    # live fetches; tests name their targets through recorded usage instead.
    monkeypatch.setattr(pricing, "_listed_pool_models", set)


def _price(provider, model):
    return db.fetchone("SELECT * FROM model_prices WHERE provider=? AND model=?", (provider, model))


def _use(uid, provider, model, **usage):
    sid = db.fetchone("SELECT id FROM sources WHERE type=?", (provider,))
    sid = sid["id"] if sid else source(provider)
    db.record_usage(uid, sid, None, model, {"usage_status": "complete", **usage}, provider=provider)


def _cost(provider, model):
    row = db.fetchone("SELECT estimated_cost_micros AS c, pricing_known AS k FROM usage_events "
                      "WHERE source_type=? AND model=? ORDER BY id DESC LIMIT 1", (provider, model))
    return int(row["c"]), int(row["k"])


def test_a_manual_price_is_never_overwritten_by_a_sync_auto_or_builtin_row(fresh_db):
    uid = user()
    _use(uid, "grok", "grok-m", prompt_tokens=1000)
    db.save_model_price("grok", "grok-m", 2_000_000, None, 6_000_000, None, origin="manual")
    for origin in ("openrouter", pricing.AUTO_ORIGIN, "default"):
        db.save_model_price("grok", "grok-m", 9_000_000, None, 9_000_000, None, origin=origin)

    row = _price("grok", "grok-m")
    assert (row["origin"], row["input_micros_per_million"]) == ("manual", 2_000_000)
    assert _cost("grok", "grok-m") == (2000, 1)  # history stayed at the manual rate

    # A manual save does replace a synced row.
    db.save_model_price("grok", "grok-s", 1_000_000, None, 2_000_000, None, origin="openrouter")
    db.save_model_price("grok", "grok-s", 3_000_000, None, 4_000_000, None)
    row = _price("grok", "grok-s")
    assert (row["origin"], row["input_micros_per_million"]) == ("manual", 3_000_000)


def test_builtin_defaults_seed_once_refresh_stale_rows_and_keep_manual_edits(fresh_db):
    uid = user()
    # Recorded before any price exists; seeding reprices it at the inclusive
    # 200K long-context tier of grok-4.7 ($4 in, $1 cached, $12 out).
    _use(uid, "grok", "grok-4.7", prompt_tokens=200_000, cached_tokens=100_000, completion_tokens=1000)
    db.save_model_price("chatgpt", "gpt-5.5", 1, None, 1, None)
    db.save_model_price("kimi", "k3", 1, None, 1, None, origin=pricing.AUTO_ORIGIN)

    seeded = pricing.ensure_default_prices()

    assert "grok/grok-4.7" in seeded and "chatgpt/gpt-5.5" not in seeded
    assert _price("chatgpt", "gpt-5.5")["input_micros_per_million"] == 1
    assert (_price("kimi", "k3")["origin"], _price("kimi", "k3")["input_micros_per_million"]) == ("default", 3_000_000)
    assert _cost("grok", "grok-4.7") == (100_000 * 4 + 100_000 * 1 + 1000 * 12, 1)
    assert pricing.ensure_default_prices() == []

    db.execute("UPDATE model_prices SET cached_input_micros_per_million=1 WHERE provider='grok' AND model='grok-4.7'")
    assert pricing.ensure_default_prices() == ["grok/grok-4.7"]
    assert _price("grok", "grok-4.7")["cached_input_micros_per_million"] == 500_000
    assert rollup_drift() == {}


def test_a_manual_rate_edit_keeps_long_context_tiers_and_a_new_row_starts_with_the_builtin_ones(fresh_db):
    uid = user()
    tier = [{"min_prompt_tokens": 100_000, "input_rate": 3_000_000, "cached_rate": None,
             "cache_write_rate": None, "output_rate": 6_000_000}]
    db.save_model_price("grok", "grok-x", 1_000_000, None, 2_000_000, None, tiers=tier, origin="openrouter")
    _use(uid, "grok", "grok-x", prompt_tokens=100_000, completion_tokens=1000)
    _use(uid, "chatgpt", "gpt-5.5", prompt_tokens=272_000, completion_tokens=1000)

    # What the pricing page saves: base rates only.
    for provider, model in (("grok", "grok-x"), ("chatgpt", "gpt-5.5")):
        db.save_model_price(provider, model, 2_000_000, None, 4_000_000, None,
                            tiers=pricing.default_tiers(provider, model), keep_long_context=True)

    edited = _price("grok", "grok-x")
    assert (edited["origin"], edited["input_micros_per_million"]) == ("manual", 2_000_000)
    assert [t["input_rate"] for t in json.loads(edited["tiers_json"])] == [3_000_000]
    assert _cost("grok", "grok-x") == (100_000 * 3 + 1000 * 6, 1)
    # gpt-5.5's built-in tier from 272K: $10 in, $45 out.
    assert _cost("chatgpt", "gpt-5.5") == (272_000 * 10 + 1000 * 45, 1)
    assert rollup_drift() == {}


def test_auto_price_copies_another_spelling_of_the_model_and_reprices_history(fresh_db, no_listed_models):
    uid = user()
    db.save_model_price("chatgpt", "gpt-5.6-sol", 4_000_000, 400_000, 20_000_000, None, cache_write_rate=5_000_000)
    _use(uid, "devin", "gpt-5-6-sol-low", prompt_tokens=1000, completion_tokens=100)

    assert pricing.auto_price() == ["devin/gpt-5-6-sol-low"]

    row = _price("devin", "gpt-5-6-sol-low")
    assert row["origin"] == pricing.AUTO_ORIGIN
    assert (row["input_micros_per_million"], row["cached_input_micros_per_million"],
            row["cache_write_micros_per_million"], row["output_micros_per_million"]) == (4_000_000, 400_000, 5_000_000, 20_000_000)
    assert _cost("devin", "gpt-5-6-sol-low") == (1000 * 4 + 100 * 20, 1)
    assert pricing.auto_price() == []  # unchanged reference: nothing to do


def test_auto_price_prefers_the_same_provider_and_strips_tails_only_for_service_tiers(fresh_db, no_listed_models):
    uid = user()
    db.save_model_price("chatgpt", "gpt-5.6-sol", 4_000_000, None, 20_000_000, None)
    db.save_model_price("devin", "gpt-5.6-sol", 1_000_000, None, 2_000_000, None)
    db.save_model_price("grok", "grok-4", 2_000_000, None, 6_000_000, None)
    _use(uid, "devin", "gpt-5-6-sol-high", prompt_tokens=1000)
    _use(uid, "chatgpt", "gpt-5.6-sol-fast", prompt_tokens=1000)
    _use(uid, "grok", "grok-4-fast", prompt_tokens=1000)

    assert sorted(pricing.auto_price()) == ["chatgpt/gpt-5.6-sol-fast", "devin/gpt-5-6-sol-high"]

    # Devin's own row beats ChatGPT's for a Devin wire SKU.
    assert _price("devin", "gpt-5-6-sol-high")["input_micros_per_million"] == 1_000_000
    assert _price("chatgpt", "gpt-5.6-sol-fast")["input_micros_per_million"] == 4_000_000
    # On Grok "-fast" is a different model, never priced from grok-4.
    assert _price("grok", "grok-4-fast") is None and _cost("grok", "grok-4-fast") == (0, 0)


def test_auto_rows_follow_their_reference_and_yield_to_manual_and_builtin(fresh_db, no_listed_models):
    uid = user()
    db.save_model_price("chatgpt", "gpt-5.6-sol", 4_000_000, None, 20_000_000, None)
    _use(uid, "devin", "gpt-5-6-sol-low", prompt_tokens=1000)
    _use(uid, "cursor", "gpt-5.6-sol-max", prompt_tokens=1000)
    pricing.auto_price()

    db.save_model_price("chatgpt", "gpt-5.6-sol", 8_000_000, None, 20_000_000, None)
    db.save_model_price("cursor", "gpt-5.6-sol-max", 5_000_000, None, 5_000_000, None)
    assert pricing.auto_price() == ["devin/gpt-5-6-sol-low"]

    assert _cost("devin", "gpt-5-6-sol-low") == (8000, 1)
    cursor = _price("cursor", "gpt-5.6-sol-max")
    assert (cursor["origin"], cursor["input_micros_per_million"]) == ("manual", 5_000_000)
    assert rollup_drift() == {}


def test_an_auto_row_is_never_a_reference_for_another_model(fresh_db, no_listed_models):
    uid = user()
    db.save_model_price("zai", "glm-ref", 1_000_000, None, 1_000_000, None, origin=pricing.AUTO_ORIGIN)
    _use(uid, "kimi", "glm-ref", prompt_tokens=1000)

    assert pricing.auto_price() == []
    assert _price("kimi", "glm-ref") is None


def test_auto_price_falls_back_to_the_openrouter_catalog_but_not_its_free_records(fresh_db, no_listed_models):
    uid = user()
    _use(uid, "zai", "glm-9", prompt_tokens=1000, completion_tokens=1000)
    _use(uid, "zai", "glm-gift", prompt_tokens=1000)
    catalog = {"by_id": {
        "z-ai/glm-9": {"id": "z-ai/glm-9", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
        "z-ai/glm-gift": {"id": "z-ai/glm-gift", "free": True, "pricing": {"prompt": "0.000001", "completion": "0.000001"}},
    }}

    assert pricing.auto_price(catalog) == ["zai/glm-9"]

    row = _price("zai", "glm-9")
    assert (row["input_micros_per_million"], row["output_micros_per_million"], row["openrouter_id"]) == (
        1_000_000, 2_000_000, "z-ai/glm-9")
    assert _cost("zai", "glm-9") == (3000, 1)
    assert _price("zai", "glm-gift") is None


def test_openrouter_sync_imports_rates_reprices_and_skips_manual_rows(fresh_db, monkeypatch):
    uid = user()
    _use(uid, "grok", "grok-9", prompt_tokens=1000, cached_tokens=400)
    db.save_model_price("grok", "grok-8", 7_000_000, None, 7_000_000, None)
    catalogs = {"x-ai": [
        {"id": "x-ai/grok-9", "pricing": {"prompt": "0.000002", "completion": "0.000006", "input_cache_read": "0.0000005"}},
        {"id": "x-ai/grok-8", "pricing": {"prompt": "0.000001", "completion": "0.000001"}},
        {"id": "x-ai/grok-7:free", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "x-ai/grok-6", "pricing": {"prompt": "0.000001"}},  # no completion rate: skipped
    ]}

    def handler(request):
        return httpx.Response(200, json={"data": catalogs.get(request.url.params["model_authors"], [])})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real(transport=httpx.MockTransport(handler)))

    result = asyncio.run(pricing.sync_openrouter_prices())

    assert result["models"] == ["x-ai/grok-7:free", "x-ai/grok-8", "x-ai/grok-9"]
    synced = _price("grok", "grok-9")
    assert (synced["origin"], synced["input_micros_per_million"], synced["cached_input_micros_per_million"]) == (
        "openrouter", 2_000_000, 500_000)
    assert _cost("grok", "grok-9") == (600 * 2 + 400 // 2, 1)
    assert (_price("grok", "grok-8")["origin"], _price("grok", "grok-8")["input_micros_per_million"]) == ("manual", 7_000_000)
    assert _price("grok", "grok-7") is None and _price("openrouter", "x-ai/grok-7:free") is not None
    assert _price("grok", "grok-6") is None


def _costs(provider, model):
    return [int(row["c"]) for row in db.fetchall(
        "SELECT estimated_cost_micros AS c FROM usage_events WHERE source_type=? AND model=? ORDER BY id",
        (provider, model))]


def test_nanogpt_prices_a_paid_claude_cache_write_at_twice_input(fresh_db, no_listed_models, monkeypatch):
    from app.sources import nanogpt

    rates = {"prompt": 3, "completion": 15, "cacheReadInputPer1kTokens": 0.0003, "cacheWriteInputPer1kTokens": 0.00375}
    catalog = {"anthropic/claude-z": {"pricing": rates}, "openai/gpt-z": {"pricing": rates}}
    monkeypatch.setattr(nanogpt, "catalog_record", lambda model: catalog.get(nanogpt.upstream_id(model)))
    uid = user()
    claude, other = nanogpt.public_id("anthropic/claude-z"), nanogpt.public_id("openai/gpt-z")
    for model in (claude, other):
        _use(uid, "nanogpt", model, prompt_tokens=1000, cache_write_tokens=1000)

    assert sorted(pricing.auto_price()) == [f"nanogpt/{claude}", f"nanogpt/{other}"]

    # Fuse's one-hour cache helper: 2x input, not the catalog's 5-minute rate.
    assert _price("nanogpt", claude)["cache_write_micros_per_million"] == 6_000_000
    assert _cost("nanogpt", claude) == (6000, 1)
    assert _price("nanogpt", other)["cache_write_micros_per_million"] == 3_750_000
    assert _cost("nanogpt", other) == (3750, 1)


def test_refuseless_long_context_starts_one_token_past_its_threshold(fresh_db, no_listed_models, monkeypatch):
    from app.sources import refuseless

    rec = {"pricing": {"input_per_m": 1, "output_per_m": 2,
                       "long_context": {"threshold": 128_000, "input_per_m": 2, "output_per_m": 4}}}
    monkeypatch.setattr(refuseless, "catalog_record", lambda model: rec)
    uid = user()
    for prompt in (128_000, 128_001):
        _use(uid, "refuseless", "rl-model", prompt_tokens=prompt)

    assert pricing.auto_price() == ["refuseless/rl-model"]

    assert _costs("refuseless", "rl-model") == [128_000, 128_001 * 2]


def test_claude_long_context_starts_over_100k_prompt_tokens(fresh_db):
    pricing.ensure_default_prices()
    uid = user()
    for prompt in (100_000, 100_001):
        _use(uid, "claude", "claude-haiku-5-5", prompt_tokens=prompt)

    # $0.10/M up to 100,000 tokens, $0.50/M for the whole prompt above.
    assert _costs("claude", "claude-haiku-5-5") == [10_000, 50_001]


def test_openrouter_sync_keeps_a_manual_openrouter_row_and_prices_only_listed_pool_models(fresh_db, monkeypatch):
    from app.sources import chatgpt, chatgpt_catalog

    monkeypatch.setattr(chatgpt_catalog, "_models_cache", None)
    listed = sorted(chatgpt.CHATGPT_MODELS)[0]
    db.save_model_price("openrouter", "x-ai/grok-9", 5_000_000, None, 5_000_000, None)
    catalogs = {
        "x-ai": [{"id": "x-ai/grok-9", "pricing": {"prompt": "0.000002", "completion": "0.000006"}}],
        "openai": [{"id": f"openai/{listed}", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
                   {"id": "openai/gpt-unlisted", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}],
    }

    def handler(request):
        return httpx.Response(200, json={"data": catalogs.get(request.url.params["model_authors"], [])})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real(transport=httpx.MockTransport(handler)))

    result = asyncio.run(pricing.sync_openrouter_prices())

    assert "x-ai/grok-9" not in result["models"]
    kept = _price("openrouter", "x-ai/grok-9")
    assert (kept["origin"], kept["input_micros_per_million"]) == ("manual", 5_000_000)
    assert _price("chatgpt", listed)["origin"] == "openrouter"
    # Off the ChatGPT pool's list: priced for OpenAI and OpenRouter only.
    assert _price("chatgpt", "gpt-unlisted") is None
    assert _price("openai", "gpt-unlisted") is not None and "openai/gpt-unlisted" in result["models"]
