"""Providers area fixtures: every process-wide catalog, cache, pool health and
rate-limit table starts empty, and the network is a scripted httpx transport.

Sources keep model catalogs, key cursors, session pins and per-user limits in
module globals; a test must never see another test's.
"""
import httpx
import pytest

from app import api, model_caps
from app.security import limiter
from app.sources import (explabs, llama, nanogpt, ollama, ollama_access, openai_compat, openrouter, pool_pick,
                         pool_policy, pool_session, refuseless)
import app.sources as sources

from ._support import Upstream


def _clear():
    for name in ("_cache", "_model_cache", "_refreshing"):
        getattr(sources, name).clear()
    sources._live.clear()
    sources._unavailable_types.clear()
    llama._models_cache.clear()
    llama._model_aliases.clear()
    ollama._tags_cache.clear()
    ollama._own_tags.clear()
    ollama._think_values.clear()
    ollama._pools.clear()
    ollama._plain.clear()
    ollama._access_tasks.clear()
    ollama_access._cache.clear()
    openai_compat._models_cache.clear()
    openai_compat.reset_cursors()
    openrouter._catalog = None
    openrouter._catalog_available = False
    openrouter._endpoints.clear()
    openrouter._providers = None
    nanogpt._catalog = None
    nanogpt._catalog_ok = False
    nanogpt._providers.clear()
    nanogpt._refresh_locks.clear()
    refuseless._catalog = None
    refuseless._catalog_ok = False
    refuseless._catalog_lock = None
    refuseless._refresh_locks.clear()
    explabs._catalogs.clear()
    explabs._errors.clear()
    explabs._refreshing.clear()
    model_caps._seen.clear()
    pool_pick.reset_health()
    pool_session.reset_store()
    pool_policy._share_cache.clear()
    api._in_flight.clear()
    with limiter._lock:
        limiter._hits.clear()
        limiter._expires.clear()


@pytest.fixture(autouse=True)
def _provider_state(tmp_path, monkeypatch):
    # Ollama's key pool and access map save beside the app (ROOT/data).
    monkeypatch.setattr(ollama, "ROOT", tmp_path)
    monkeypatch.setattr(ollama_access, "ROOT", tmp_path)
    monkeypatch.setattr(ollama.KeyPool, "start_sweeper", lambda self: None)
    _clear()
    yield
    _clear()


@pytest.fixture
def upstream(monkeypatch):
    """Every httpx.AsyncClient the app opens talks to this scripted upstream."""
    fake = Upstream()
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs.pop("transport", None)
        return real(*args, transport=httpx.MockTransport(fake.handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    yield fake
    assert not fake.unmatched, f"unexpected upstream requests: {fake.unmatched}"
