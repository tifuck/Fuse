"""The Sources directory: one pane per provider in group order, API keys on
their vendor's pane, and the admin overview of every provider."""
from app.providers import directory
from app.sources import keyed


def _entry(pid):
    return {"id": pid, "keys": [], "site": False}


def test_panes_group_providers_and_put_api_keys_beside_their_subscription():
    panes = directory.panes([_entry(pid) for pid in ("xai", "openai_api", "anthropic", "moonshot", "deepseek",
                                                     "opencode_go", "opencode_zen", "ollama")])
    by_id = {pane["id"]: pane for pane in panes}
    # A vendor's API key sits on its subscription pane, and answers to its name.
    assert [e["id"] for e in by_id["grok"]["keys"]] == ["xai"] and "xai" not in by_id
    assert by_id["grok"]["names"] == ["x.ai", "xai api"]
    assert [e["id"] for e in by_id["chatgpt"]["keys"]] == ["openai_api"]
    assert [e["id"] for e in by_id["claude"]["keys"]] == ["anthropic"]
    assert [e["id"] for e in by_id["kimi"]["keys"]] == ["moonshot"]
    # Pools keep their own panel; keyed providers get a generic pane.
    assert by_id["kimi"]["partial"] == "sources/_pool_kimi.html" and by_id["deepseek"]["partial"] is None
    groups = [pane["group"] for pane in panes]
    assert groups == sorted(groups, key=[g for g, _ in directory.GROUPS].index)
    assert (by_id["opencode_go"]["group"], by_id["opencode_zen"]["group"], by_id["ollama"]["group"]) == (
        "subscription", "aggregator", "api_key")
    # Without a personal-key entry (provider off) there is no generic pane.
    assert "mistral" not in by_id


def test_refuseless_rows_carry_their_lineup_prices_per_million():
    import time

    from app.sources import refuseless

    refuseless._catalog = (time.time(), refuseless.parse_catalog({"data": [
        {"id": "glm-x", "pricing": {"input_per_m": 0.4, "output_per_m": 1.6}},
        {"id": "glm-x-free", "pricing": {"input_per_m": 0, "output_per_m": 0}},
        {"id": "unpriced"},
    ]}))
    rows = [{"id": "refuseless/glm-x"}, {"id": "refuseless/glm-x-free"}, {"id": "refuseless/unpriced"}]
    assert directory._refuseless_prices(rows)
    assert [(r["input"], r["output"]) for r in rows] == [(0.4, 1.6), (0.0, 0.0), (None, None)]
    # The documented fallback lineup has no prices: the list draws no price columns.
    refuseless._catalog = None
    assert not directory._refuseless_prices([{"id": "refuseless/glm-5.3-abliterated"}])


def test_the_admin_overview_lists_each_provider_with_what_it_takes(fresh_db):
    keyed.ensure_source("deepseek")
    from app.sources import ensure_ollama_source, kimi

    kimi.ensure_kimi_source()
    ensure_ollama_source()
    rows = {row["id"]: row for row in directory.admin_overview()}
    assert rows["kimi"]["kinds"] == ["Pool"] and rows["kimi"]["href"] == "/admin/pools"
    assert rows["deepseek"]["kinds"] == ["Site keys", "Personal keys"] and rows["deepseek"]["source_id"]
    assert rows["ollama"]["kinds"] == ["Site keys", "Personal keys"]
    assert "grok" not in rows  # no source row yet
