"""Subscriptions area fixtures: every backend starts with empty process state.

Pool health, session pins, dead refresh grants, catalogs, Claude threads,
Codex turn states, Devin SKU health and Cursor conversations are module
globals; a test must never see another test's. OAuth refresh is on (the dev
config turns it off) so refresh paths run; tests that pin the gate turn it off.
"""
import pytest

from app.config import settings
from app.sources import (
    chatgpt, chatgpt_catalog, claude, claude_catalog, claude_thread, cursor, cursor_agent, devin, devin_catalog,
    grok, grok_version, kimi, mimo, pool_pick, pool_policy, pool_refresh, pool_session, token_store, zai,
)


@pytest.fixture(autouse=True)
def _backend_state(monkeypatch):
    pool_pick.reset_health()
    pool_session.reset_store()
    pool_policy._share_cache.clear()
    claude_thread.reset()
    devin_catalog.reset_sku_health()
    monkeypatch.setattr(token_store, "_dead_grants", {})
    monkeypatch.setattr(pool_refresh, "_refreshing", {})
    monkeypatch.setattr(pool_refresh, "_retry_at", {})
    monkeypatch.setattr(settings, "oauth_refresh_enabled", True)
    for mod in (grok, kimi, zai, mimo, cursor, chatgpt_catalog, claude_catalog, devin_catalog):
        monkeypatch.setattr(mod, "_models_cache", None)
    monkeypatch.setattr(kimi, "_catalog_meta", {})
    monkeypatch.setattr(claude_catalog, "_catalog_meta", {})
    monkeypatch.setattr(chatgpt_catalog, "_levels", {})
    monkeypatch.setattr(chatgpt_catalog, "_all_turns", set())
    monkeypatch.setattr(chatgpt_catalog, "_no_summary", set())
    monkeypatch.setattr(chatgpt, "_turn_states", {})
    monkeypatch.setattr(pool_pick, "_live_free_models", frozenset())
    for name in ("_model_labels", "_model_efforts", "_model_wire_uids", "_model_plain_uids", "_model_promo_until"):
        monkeypatch.setattr(devin_catalog, name, {})
    monkeypatch.setattr(devin, "_jwt_cache", {})
    monkeypatch.setattr(cursor, "_catalog", {})
    monkeypatch.setattr(cursor, "_labels", {})
    monkeypatch.setattr(cursor_agent, "_conversations", {})
    # The Grok client version must never come from data/ or the network.
    monkeypatch.setattr(settings, "grok_client_version_auto_refresh", False)
    monkeypatch.setattr(grok_version, "_loaded", True)
    for mod in (grok, kimi, zai, mimo, chatgpt, claude, cursor):
        monkeypatch.setattr(mod, "_refresh_locks", {})
    yield
    pool_pick.reset_health()
    pool_session.reset_store()
    claude_thread.reset()
    devin_catalog.reset_sku_health()
