"""The provider registry: every per-provider list is derived from it and
still says what it said before (snapshots), the specs are complete, and a
golden table pins how pick() routes every kind of model id.
"""
import asyncio
import time

import httpx
from pytest import fixture

import app.sources as sources
from app import pricing, provider_errors
from app.providers import registry
from app.providers.access import Access
from app.sources import kimi, nanogpt, openrouter, pool_policy, refuseless, usage_walk
from app.web.source_routes import admin_actions
from tests.core.helpers import quick_user

from ._support import key, route, source

POOLS = ("grok", "chatgpt", "zai", "devin", "kimi", "mimo", "claude", "cursor", "nanogpt", "refuseless")
KEYED_IDS = {s.id for s in registry.specs() if s.keyed}


def _builtin(mapping):
    """The entries for providers that predate the keyed presets."""
    return {k: v for k, v in mapping.items() if k not in KEYED_IDS}


def test_the_derived_lists_match_what_they_listed_before():
    assert _builtin(sources.TYPES) == {
        "llama": "Local llama.cpp", "openai": "OpenAI-compatible", "ollama": "Ollama Cloud", "grok": "Grok pool",
        "chatgpt": "ChatGPT pool", "zai": "Z.AI pool", "devin": "Devin pool", "kimi": "Kimi pool",
        "mimo": "Xiaomi pool", "claude": "Claude pool", "cursor": "Cursor pool", "nanogpt": "NanoGPT pool",
        "refuseless": "Refuseless pool", "openrouter": "OpenRouter", "explabs": "Experiential Labs",
    }
    assert _builtin(sources.PROVIDER_LABELS) == {
        "grok": "x.ai", "llama": "llama.cpp", "ollama": "Ollama Cloud", "openai": "OpenAI-compatible",
        "chatgpt": "ChatGPT", "zai": "Z.AI", "devin": "Devin", "kimi": "Kimi", "mimo": "Xiaomi",
        "claude": "Anthropic", "cursor": "Cursor", "nanogpt": "NanoGPT", "refuseless": "Refuseless",
        "openrouter": "OpenRouter", "explabs": "Experiential Labs", "unknown": "Unknown",
    }
    assert sources.POOL_TYPES == frozenset(POOLS)
    assert set(sources._SEEDED_MODELS) == {"grok", "chatgpt", "zai", "devin", "kimi", "mimo", "claude", "cursor"}
    assert set(sources._BUILDERS) == set(sources.TYPES)
    assert sources._POOL_CONNECT["zai"] == "Connect a Z.AI key to use GLM models"
    assert set(_builtin(sources._POOL_CONNECT)) == set(POOLS)
    assert pool_policy.POOL_KINDS == POOLS
    assert pool_policy.POOL_LABELS == {
        "grok": "x.ai", "chatgpt": "OpenAI", "zai": "Z.AI", "devin": "Devin", "kimi": "Kimi", "mimo": "Xiaomi",
        "claude": "Claude", "cursor": "Cursor", "nanogpt": "NanoGPT", "refuseless": "Refuseless",
    }
    assert pool_policy.GUARD_KINDS == ("grok", "zai", "kimi", "mimo", "claude", "cursor", "nanogpt")
    assert pool_policy.DEFAULT_SHARE == {"refuseless": "private"}
    assert set(usage_walk.WALK_KINDS) == {"grok", "chatgpt", "zai", "devin", "kimi", "mimo", "cursor", "openrouter",
                                          "nanogpt", "refuseless"}
    assert admin_actions._BUILTINS == set(sources.TYPES) - {"openai"}
    assert set(pricing._AUTO_PROVIDERS) == set(POOLS)
    assert _builtin(provider_errors._LABELS) == {
        "claude": "Claude", "chatgpt": "ChatGPT", "grok": "Grok", "zai": "Z.AI", "devin": "Devin", "kimi": "Kimi",
        "cursor": "Cursor", "ollama": "Ollama Cloud", "openrouter": "OpenRouter", "nanogpt": "NanoGPT",
        "mimo": "MiMo", "llama": "llama.cpp", "explabs": "Experiential Labs", "refuseless": "Refuseless",
    }
    assert sources.reasoning_efforts("grok", "grok-4") == ("low", "medium", "high", "xhigh")
    assert sources.reasoning_efforts("grok", "grok-build-0.1") == ()
    assert sources.reasoning_efforts("mimo") == ("none", "high")
    assert sources.reasoning_efforts("openai") == sources.reasoning_efforts("llama") == sources.GENERIC_EFFORTS
    assert sources.reasoning_efforts("no-such-type") == sources.GENERIC_EFFORTS


def test_every_spec_is_complete():
    ids = [s.id for s in registry.specs()]
    assert len(ids) == len(set(ids))
    for spec in registry.specs():
        assert registry.builder(spec.id), spec.id
        assert spec.call_style in ("plain", "session", "pool", "user"), spec.id
        assert bool(spec.claim) == bool(spec.claims), spec.id
        if spec.pool:
            assert spec.ensure and spec.access and spec.own_access and spec.connect_copy, spec.id
            assert spec.call_style == "pool", spec.id
        elif spec.keyed:
            p = spec.keyed
            assert spec.claim == "soft" and spec.call_style == "user" and spec.access, spec.id
            assert p.base().startswith("https://") and all(r.base_url.startswith("https://") for r in p.regions), spec.id
            assert p.protocol in ("openai_chat", "anthropic_messages") and p.auth in ("bearer", "x-api-key"), spec.id
            assert p.methods and p.public_prefix.endswith("/"), spec.id
        else:
            assert not (spec.guard or spec.access or spec.claim == "hard"), spec.id
    prefixes = [s.keyed.public_prefix for s in registry.specs() if s.keyed]
    assert len(prefixes) == len(set(prefixes))
    # No keyed prefix shadows a namespace an existing provider owns.
    taken = ("devin/", "claude/", "cursor/", "nanogpt/", "refuseless/", "explabs/", "openrouter/")
    assert not [p for p in prefixes if p.startswith(taken)]


NANO = {"data": [{"id": "z-ai/glm-5.3", "subscription": {"included": True}}]}
REFUSE = {"data": [{"id": "glm-5.3-abliterated"}]}
OR_CATALOG = {"data": [
    {"id": slug, "pricing": {"prompt": "0.000003", "completion": "0.000015"}}
    for slug in ("anthropic/claude-x", "openai/gpt-x", "deepseek/deepseek-chat")
]}
CATALOGS = {
    "llama": ["local-model"],
    "openai": ["openai-model", "grok-in-openai", "kimi-for-coding-x"],
    "ollama": ["ollama-model"],
    "grok": ["grok-4", "grok-in-openai"],
    "chatgpt": ["gpt-5.5"],
    "zai": ["glm-5.3"],
    "devin": ["devin/claude-opus-5"],
    "kimi": ["kimi-for-coding"],
    "mimo": ["mimo-v2-pro"],
    "claude": ["claude/claude-opus-5"],
    "cursor": ["cursor/auto"],
}
KEYED = frozenset({"openai_api", "deepseek"})


@fixture
def world(fresh_db, upstream):
    upstream.on("GET", "openrouter.ai/api/v1/models", httpx.Response(200, json=OR_CATALOG))
    nanogpt._catalog = (time.time(), nanogpt.parse_catalog(NANO))
    refuseless._catalog = (time.time(), refuseless.parse_catalog(REFUSE))
    uid = quick_user()
    ids = {kind: source(kind) for kind in CATALOGS}
    ids["nanogpt"] = nanogpt.ensure_nanogpt_source()
    ids["refuseless"] = refuseless.ensure_refuseless_source()
    ids["openrouter"] = openrouter.ensure_openrouter_source()
    key(ids["openrouter"], "sk-or-golden-key", owner=uid, extra={"role": "user", "api_key": "sk-or-golden-key"})
    for kind, names in CATALOGS.items():
        cache_key = (ids[kind], None)
        sources._store_models(cache_key, names)
        sources._live.add(cache_key)
    for kind in KEYED:
        source(kind)
    route("routed-alias", ids["ollama"], "ollama-upstream")
    route("routed-pool", ids["kimi"], "kimi-upstream")
    return uid


def _pick(model, uid, allowed):
    access = Access.fixed(uid, {kind: kind in allowed for kind in POOLS + tuple(KEYED)})
    picked = asyncio.run(sources.pick(model, user_id=uid, access=access))
    return picked and (picked[0].type_id, picked[1])


ALL = frozenset(POOLS)
NONE = frozenset()
GOLDEN = [
    # (model, allowed pools, expected (type, upstream) or None)
    ("devin/claude-opus-5", ALL, ("devin", "claude-opus-5")),
    ("devin/claude-opus-5", NONE, None),
    ("kimi-for-coding", ALL, ("kimi", "kimi-for-coding")),
    ("kimi-for-coding", NONE, None),
    ("mimo-v2-pro", ALL, ("mimo", "mimo-v2-pro")),
    ("claude/claude-opus-5", ALL, ("claude", "claude-opus-5")),
    ("claude/claude-opus-5", NONE, None),
    ("cursor/auto", ALL, ("cursor", "auto")),
    ("nanogpt/z-ai/glm-5.3", ALL, ("nanogpt", "z-ai/glm-5.3")),
    ("nanogpt/z-ai/glm-5.3", NONE, None),
    ("nanogpt/missing", ALL, None),
    ("refuseless/glm-5.3-abliterated", ALL, ("refuseless", "glm-5.3-abliterated")),
    ("refuseless/gone", ALL, None),
    ("grok-4", ALL, ("grok", "grok-4")),
    ("grok-4", NONE, None),
    # Soft claims fall through: a denied grok id can still be served by a catalog that lists it.
    ("grok-in-openai", ALL, ("grok", "grok-in-openai")),
    ("grok-in-openai", NONE, ("openai", "grok-in-openai")),
    # Hard claims never fall through.
    ("kimi-for-coding-x", NONE, None),
    ("gpt-5.5", ALL, ("chatgpt", "gpt-5.5")),
    ("gpt-5.5", NONE, None),
    ("glm-5.3", ALL, ("zai", "glm-5.3")),
    ("glm-5.3", NONE, None),
    ("openrouter/anthropic/claude-x", NONE, ("openrouter", "anthropic/claude-x")),
    ("anthropic/claude-x", NONE, ("openrouter", "anthropic/claude-x")),
    ("explabs/anything", ALL, None),
    ("routed-alias", NONE, ("ollama", "ollama-upstream")),
    ("routed-pool", ALL, ("kimi", "kimi-upstream")),
    ("routed-pool", NONE, None),
    ("local-model", NONE, ("llama", "local-model")),
    ("openai-model", NONE, ("openai", "openai-model")),
    ("ollama-model", NONE, ("ollama", "ollama-model")),
    ("no-such-model", ALL, None),
    # Keyed prefixes are soft: without a key the id keeps routing as before.
    ("openai/gpt-x", NONE, ("openrouter", "openai/gpt-x")),
    ("openai/gpt-x", KEYED, ("openai_api", "gpt-x")),
    ("openrouter/openai/gpt-x", KEYED, ("openrouter", "openai/gpt-x")),
    ("deepseek/deepseek-chat", NONE, ("openrouter", "deepseek/deepseek-chat")),
    ("deepseek/deepseek-chat", KEYED, ("deepseek", "deepseek-chat")),
    ("deepseek/unlisted", NONE, None),
    ("xai/grok-4", ALL, None),
    ("gpt-5.5", KEYED, None),
    ("claude/claude-opus-5", KEYED, None),
]


def test_golden_routing_table(world):
    got = {(model, len(allowed)): _pick(model, world, allowed) for model, allowed, _ in GOLDEN}
    assert got == {(model, len(allowed)): expected for model, allowed, expected in GOLDEN}


def test_a_denied_pool_names_itself_in_claim_order(world):
    denied = Access.fixed(world, {})
    assert sources.pool_denial("devin/claude-opus-5", world, denied).startswith("Devin: access denied.")
    assert sources.pool_denial("grok-4", world, denied).startswith("Grok: access denied.")
    assert sources.pool_denial("gpt-5.5", world, Access.fixed(world, {"chatgpt": True})) is None
    assert sources.pool_denial("local-model", world, denied) is None


def test_access_asks_each_provider_once_and_only_when_needed(monkeypatch):
    asked = []
    monkeypatch.setattr(kimi, "kimi_access_state", lambda uid: asked.append(uid) or {"ok": True, "reason": "ok"})
    access = Access(7)
    assert asked == []
    assert access.allows("kimi") and access.allows("kimi") and access.state("kimi")["reason"] == "ok"
    assert asked == [7]
    assert access.allows("llama") and access.allows("openrouter")  # no access verdict: open
    assert not Access().allows("kimi")  # no user: gated providers are denied
    assert asked == [7]
