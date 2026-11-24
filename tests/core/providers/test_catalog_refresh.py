"""Background catalog refresh: only due sources are fetched, a few at a time
and never twice at once, failures back off, a provider's ETag turns an
unchanged catalog into a 304 that keeps the list, disabled or keyless
providers are never asked, and a restart serves the saved list at once."""
import asyncio
import json
import time

import httpx

import app.sources as sources
from app import context_meter, db
from app.providers import catalogs
from app.sources import keyed
from tests.core.helpers import quick_user, wait_until_async

from ._support import completion, key, source

MODELS = {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}


def _types(due):
    return sorted(s.type_id for s in due)


def test_only_due_enabled_providers_with_a_key_are_fetched(fresh_db):
    for pid in ("deepseek", "mistral", "groq"):
        key(keyed.ensure_source(pid), f"sk-{pid}-key-123456")
    keyed.ensure_source("cerebras")  # no key at all
    db.execute("UPDATE sources SET enabled=0 WHERE id=?", (keyed.ensure_source("groq"),))
    now = time.time()
    catalogs.saved(keyed.ensure_source("deepseek"), "deepseek", ["deepseek/deepseek-chat"], 21600, now=now)

    assert _types(sources.due_catalogs(now)) == ["mistral"]
    # Once deepseek's TTL (with jitter) has passed, it is due again.
    assert _types(sources.due_catalogs(now + 21600 * 1.2)) == ["deepseek", "mistral"]


def test_fetches_share_two_slots_and_never_run_twice(fresh_db, monkeypatch):
    ids = [keyed.ensure_source(pid) for pid in ("deepseek", "mistral", "groq", "venice")]
    for sid in ids:
        key(sid, f"sk-key-{sid}-123456")
    gate = asyncio.Event()
    active, peak, calls = [0], [0], []

    async def fake(self, stored=None):
        calls.append(self.type_id)
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await gate.wait()
        active[0] -= 1
        return [self.preset.public_prefix + "m"]

    monkeypatch.setattr(keyed.KeyedSource, "shared_models", fake)

    async def run():
        job = asyncio.create_task(sources.refresh_due_catalogs())
        await wait_until_async(lambda: len(calls) == 2)
        # Running fetches are not due again: a second pass starts nothing.
        assert sources.due_catalogs() == []
        await asyncio.sleep(0)
        assert len(calls) == 2
        gate.set()
        await job

    asyncio.run(run())
    assert peak[0] == sources.MAX_FETCHES == 2 and sorted(calls) == ["deepseek", "groq", "mistral", "venice"]
    assert all(catalogs.get(sid)["models"] for sid in ids)


def test_a_failing_fetch_backs_off_and_keeps_the_saved_list(fresh_db, upstream):
    sid = keyed.ensure_source("deepseek")
    key(sid, "sk-deepseek-key-123")
    catalogs.saved(sid, "deepseek", ["deepseek/deepseek-chat"], 21600, now=0)
    upstream.on("GET", "api.deepseek.com/models", httpx.Response(503))

    asyncio.run(sources.refresh_due_catalogs())
    row = catalogs.get(sid)
    first = row["next_due"] - time.time()
    assert row["failures"] == 1 and 50 < first < 70 and row["models"] == ["deepseek/deepseek-chat"]
    assert 100 < catalogs.failed(sid, "deepseek", "again", 21600) < 140  # doubles
    assert catalogs.failed(sid, "deepseek", "again", 100) <= 110  # never past the TTL


def test_a_failure_that_began_before_a_newer_success_leaves_that_success_alone(fresh_db, monkeypatch):
    sid = keyed.ensure_source("deepseek")
    key(sid, "sk-deepseek-key-123")

    async def fails_after_another_fetch_succeeded(self, stored=None):
        catalogs.saved(sid, "deepseek", ["deepseek/deepseek-chat"], 21600)
        raise httpx.ConnectError("down")

    monkeypatch.setattr(keyed.KeyedSource, "shared_models", fails_after_another_fetch_succeeded)
    asyncio.run(sources.refresh_due_catalogs())
    row = catalogs.get(sid)
    assert (row["failures"], row["error"], row["models"]) == (0, "", ["deepseek/deepseek-chat"])
    assert row["next_due"] > time.time() + 3600


def test_a_key_change_during_a_fetch_drops_the_list_that_fetch_got(fresh_db, monkeypatch):
    sid = keyed.ensure_source("deepseek")
    key(sid, "sk-deepseek-key-123")
    catalogs.save_detail(sid, "deepseek", {"m": {"price": 1}}, 1.0)

    async def key_changes_mid_fetch(self, stored=None):
        sources.invalidate_model_cache("deepseek")
        return ["deepseek/old-key-model"]

    monkeypatch.setattr(keyed.KeyedSource, "shared_models", key_changes_mid_fetch)
    asyncio.run(sources.refresh_due_catalogs())
    assert catalogs.get(sid)["models"] == [] and catalogs.load_detail(sid) is None
    assert not [k for k in sources._model_cache if k[0] == sid]


def test_an_unchanged_catalog_answers_304_and_keeps_its_list(fresh_db, upstream):
    uid = quick_user()
    sid = keyed.ensure_source("deepseek")
    key(sid, "sk-deepseek-key-123", owner=uid)
    upstream.on("GET", "api.deepseek.com/models",
                httpx.Response(200, json=MODELS, headers={"ETag": '"v1"'}), httpx.Response(304))

    asyncio.run(sources.refresh_due_catalogs())
    assert catalogs.get(sid)["etag"] == '"v1"'
    db.execute("UPDATE provider_catalogs SET next_due=0 WHERE source_id=?", (sid,))
    sources._model_cache.clear()
    asyncio.run(sources.refresh_due_catalogs())

    headers = upstream.headers("api.deepseek.com/models", "GET")
    assert "if-none-match" not in headers[0] and headers[1]["if-none-match"] == '"v1"'
    row = catalogs.get(sid)
    assert row["models"] == ["deepseek/deepseek-chat", "deepseek/deepseek-reasoner"] and row["next_due"] > time.time()
    assert asyncio.run(sources.models_union(user_id=uid)) == row["models"]


def test_a_restart_lists_the_saved_catalog_without_fetching(fresh_db, upstream):
    uid = quick_user()
    sid = keyed.ensure_source("deepseek")
    key(sid, "sk-deepseek-key-123", owner=uid)
    catalogs.saved(sid, "deepseek", ["deepseek/deepseek-chat"], 21600)
    sources._seed_pool_catalogs()  # boot
    assert asyncio.run(sources.models_union(user_id=uid)) == ["deepseek/deepseek-chat"]
    assert sources.due_catalogs() == [] and not upstream.requests


def test_one_shared_catalog_serves_every_user_with_access(fresh_db, upstream):
    alice, bob, carol = quick_user("a@core.test"), quick_user("b@core.test"), quick_user("c@core.test")
    sid = keyed.ensure_source("deepseek")
    key(sid, "sk-deepseek-alice-1", owner=alice)
    key(sid, "sk-deepseek-bob-123", owner=bob)
    upstream.on("GET", "api.deepseek.com/models", httpx.Response(200, json=MODELS))
    for uid in (alice, bob):
        assert asyncio.run(sources.models_union(user_id=uid)) == ["deepseek/deepseek-chat", "deepseek/deepseek-reasoner"]
    assert asyncio.run(sources.models_union(user_id=carol)) == []  # no key, no site access
    assert len(upstream.calls("api.deepseek.com/models")) == 1
    assert json.loads(db.fetchone("SELECT models FROM provider_catalogs WHERE source_id=?", (sid,))["models"])


def test_a_restart_relearns_what_a_fetch_teaches_before_serving_the_saved_list(fresh_db, upstream):
    """llama learns each model's window and its filesystem name only while
    listing. Serving the saved list alone showed the default 8K window (and
    sent the public id upstream) until the next due fetch."""
    raw = "/models/bartowski-Qwen3-32B-Q4_K_M.gguf"
    upstream.on("GET", "/v1/models", httpx.Response(200, json={"data": [
        {"id": raw, "memory": {"effective": {"n_ctx": 262144}}, "architecture": {"input_modalities": ["text"]}}]}))
    upstream.on("POST", "/chat/completions", httpx.Response(200, json=completion()))
    sid = source("llama", {"base_url": "https://llama.test"})
    ds = keyed.ensure_source("deepseek")
    key(ds, "sk-deepseek-key-123")
    catalogs.saved(sid, "llama", ["qwen3-32b"], 300)
    catalogs.saved(ds, "deepseek", ["deepseek/deepseek-chat"], 21600)
    sources._seed_pool_catalogs()  # boot

    # Due at boot whatever its due time; a keyed list (names only) is not.
    assert _types(sources.due_catalogs(boot=True)) == ["llama"] and sources.due_catalogs() == []
    assert asyncio.run(sources.models_union()) == ["qwen3-32b"]
    assert context_meter.cached_window("llama", "qwen3-32b") == 262144
    asyncio.run(sources.get(sid).chat({"model": "qwen3-32b", "messages": []}))
    assert upstream.sent("/chat/completions")[0]["model"] == raw
    assert len(upstream.calls("/v1/models")) == 1
