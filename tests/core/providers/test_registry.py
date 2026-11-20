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
from app.sources import nanogpt, openrouter, pool_policy, refuseless, usage_walk
from app.web.source_routes import admin_actions
from tests.core.helpers import quick_user

from ._support import key, route, source

POOLS = ("grok", "chatgpt", "zai", "devin", "kimi", "mimo", "claude", "cursor", "nanogpt", "refuseless")


def test_the_derived_lists_match_what_they_listed_before():
    assert sources.TYPES == {
        "llama": "Local llama.cpp", "openai": "OpenAI-compatible", "ollama": "Ollama Cloud", "grok": "Grok pool",
        "chatgpt": "ChatGPT pool", "zai": "Z.AI pool", "devin": "Devin pool", "kimi": "Kimi pool",
        "mimo": "Xiaomi pool", "claude": "Claude pool", "cursor": "Cursor pool", "nanogpt": "NanoGPT pool",
        "refuseless": "Refuseless pool", "openrouter": "OpenRouter", "explabs": "Experiential Labs",
    }
    assert sources.PROVIDER_LABELS == {
        "grok": "x.ai", "llama": "llama.cpp", "ollama": "Ollama Cloud", "openai": "OpenAI-compatible",
        "chatgpt": "ChatGPT", "zai": "Z.AI", "devin": "Devin", "kimi": "Kimi", "mimo": "Xiaomi",
        "claude": "Anthropic", "cursor": "Cursor", "nanogpt": "NanoGPT", "refuseless": "Refuseless",
        "openrouter": "OpenRouter", "explabs": "Experiential Labs", "unknown": "Unknown",
    }
    assert sources.POOL_TYPES == frozenset(POOLS)
    assert set(sources._SEEDED_MODELS) == {"grok", "chatgpt", "zai", "devin", "kimi", "mimo", "claude", "cursor"}
    assert set(sources._BUILDERS) == set(sources.TYPES)
    assert sources._POOL_CONNECT["zai"] == "Connect a Z.AI key to use GLM models"
    assert set(sources._POOL_CONNECT) == set(POOLS)
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
    assert provider_errors._LABELS == {
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
        else:
            assert not (spec.guard or spec.access or spec.claim == "hard"), spec.id


NANO = {"data": [{"id": "z-ai/glm-5.3", "subscription": {"included": True}}]}
REFUSE = {"data": [{"id": "glm-5.3-abliterated"}]}
OR_CATALOG = {"data": [{"id": "anthropic/claude-x", "pricing": {"prompt": "0.000003", "completion": "0.000015"}}]}
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
    route("routed-alias", ids["ollama"], "ollama-upstream")
    route("routed-pool", ids["kimi"], "kimi-upstream")
    return uid


def _pick(model, uid, allowed):
    allow = {f"allow_{kind}": kind in allowed for kind in POOLS}
    picked = asyncio.run(sources.pick(model, user_id=uid, **allow))
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
]


def test_golden_routing_table(world):
    got = {(model, len(allowed)): _pick(model, world, allowed) for model, allowed, _ in GOLDEN}
    assert got == {(model, len(allowed)): expected for model, allowed, expected in GOLDEN}


def test_a_denied_pool_names_itself_in_claim_order(world):
    denied = {kind: False for kind in POOLS}
    assert sources.pool_denial("devin/claude-opus-5", world, denied).startswith("Devin: access denied.")
    assert sources.pool_denial("grok-4", world, denied).startswith("Grok: access denied.")
    assert sources.pool_denial("gpt-5.5", world, {**denied, "chatgpt": True}) is None
    assert sources.pool_denial("local-model", world, denied) is None
