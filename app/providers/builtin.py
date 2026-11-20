"""Specs of the providers built into Fuse, in display order.

Adding a provider: an API-key provider is one preset in presets.py; anything
else gets its Source class in app/sources/ and its spec here. Then run
tests/core/providers/test_registry.py. Hard claims are tried in this
order, then soft claims in this order (see sources.pick).
"""
from __future__ import annotations

import importlib

from .presets import PRESETS, KeyedPreset
from .spec import ProviderSpec


def _m(name: str):
    return importlib.import_module(f"app.sources.{name}")


def _fn(mod: str, attr: str):
    """Late-bound app.sources.<mod>.<attr>, so the registry imports nothing
    and a patched function is the one called."""
    return lambda *args: getattr(_m(mod), attr)(*args)


def _starts(*prefixes: str):
    return lambda mid: mid.startswith(prefixes)


def _model_efforts(mod: str, attr: str = "efforts_for"):
    return lambda model, user_id: getattr(_m(mod), attr)(model)


def _devin_efforts(model, user_id):
    devin = _m("devin")
    return devin.efforts_for(model) if model else devin.SEEDED_EFFORTS


def _explabs_efforts(model, user_id):
    explabs = _m("explabs")
    catalog = explabs.cached_catalog(user_id) or {}
    record = catalog.get("by_id", {}).get(explabs.upstream_id(model or "")) or {}
    return tuple(record.get("reasoning_efforts") or [])


def _keyed(p) -> ProviderSpec:
    """An API-key provider from its preset (presets.py)."""
    return ProviderSpec(
        id=p.id, label=p.label, type_label=p.label, builder="keyed:KeyedSource", error_label=p.label,
        ensure=lambda: _m("keyed").ensure_source(p.id), efforts=lambda model, user_id: p.efforts_for(model),
        warm=not p.per_key, claim="soft", claims=lambda model: model.startswith(p.public_prefix), upstream=p.upstream,
        scan=False, personal=True, access=lambda user_id: _m("keyed").access_states(user_id)[p.id],
        connect_copy=f"Add a {p.label} API key to use its models", keyed=p, key=p,
        user_catalog=(lambda user_id: True) if p.per_key else None, public_id=lambda upstream: p.public_prefix + upstream,
        group=p.group, vendor=p.vendor, site=True, admin_anchor="/admin/sources#keyed",
    )


# Personal Ollama Cloud keys (the admin's keys stay on /admin/sources).
OLLAMA_KEY = KeyedPreset(
    "ollama", "Ollama Cloud", base_url="https://ollama.com/api", catalog="/tags",
    key_url="https://ollama.com/settings/keys", validate=lambda key: _m("ollama").validate_key(key),
)


def _pool(id: str, label: str, type_label: str, builder: str, **kw) -> ProviderSpec:
    kw.setdefault("group", "subscription")
    # Unchanged from before the scheduler: one request on one pooled account,
    # and the list follows that account's plan, so it is read every 5 minutes.
    kw.setdefault("catalog_ttl", 300.0)
    kw.setdefault("catalog_state", True)
    return ProviderSpec(
        id=id, label=label, type_label=type_label, builder=builder, pool=True,
        ensure=_fn(id, f"ensure_{id}_source"), access=_fn(id, f"{id}_access_state"),
        own_access=_fn(id, f"{id}_own_access"), panel=True, admin_anchor="/admin/pools", **kw,
    )


BUILTIN: tuple[ProviderSpec, ...] = (
    _pool(
        "grok", "x.ai", "Grok pool", "grok:GrokSource", pool_label="x.ai", error_label="Grok",
        seeds=lambda: _m("grok").GROK_MODELS,
        # Grok Build documents no reasoning_effort.
        efforts=lambda model, user_id: () if model == "grok-build-0.1" else ("low", "medium", "high", "xhigh"),
        claim="soft", claims=lambda model: model.startswith("grok"),
        connect_copy="Connect a Grok account with an active subscription to use Grok models",
        guard=True, usage_walk=True,
        title="Grok", context_window=500_000, guess=_starts("grok"), aliases=("grok", "xai", "x.ai", "x-ai"), probe="_probe_grok",
        lock_copy={
            "none": "You need to link a Grok account.",
            "no_sub": "The linked Grok account needs an active SuperGrok subscription.",
            "unauthed": "The linked Grok sign-in is no longer valid.",
        },
    ),
    ProviderSpec(
        id="llama", label="llama.cpp", type_label="Local llama.cpp", builder="llama:LlamaSource",
        error_label="llama.cpp", site=True,
        # A local server swapped to another model must show it soon; no key needed.
        catalog_ttl=300.0, catalog_keyless=True, catalog_state=True,
        title="Local inference", context_window=8_192, aliases=("llama", "llama.cpp"),
    ),
    ProviderSpec(
        id="openai", label="OpenAI-compatible", type_label="OpenAI-compatible",
        builder="openai_compat:OpenAICompatSource", builtin=False, site=True, catalog_ttl=300.0,
        admin_anchor="/admin/sources#compat",
        context_window=128_000,
    ),
    ProviderSpec(
        id="ollama", label="Ollama Cloud", type_label="Ollama Cloud", builder="ollama:OllamaSource",
        error_label="Ollama Cloud", efforts=_model_efforts("ollama"), personal=True,
        key=OLLAMA_KEY, user_catalog=_fn("ollama", "user_catalog"), group="api_key", site=True,
        # /tags plus one /show per model: hourly, not on every page.
        catalog_ttl=3600.0, catalog_state=True,
        title="Ollama Cloud", context_window=128_000, aliases=("ollama",),
    ),
    _pool(
        "chatgpt", "ChatGPT", "ChatGPT pool", "chatgpt:ChatGPTSource", pool_label="OpenAI", error_label="ChatGPT",
        seeds=lambda: _m("chatgpt").CHATGPT_MODELS, efforts=_model_efforts("chatgpt_catalog"),
        listable=_fn("chatgpt", "listable_models"),
        # Live-listed slugs count too: a new Codex model (gpt-6.1-sol) must
        # stay on the pool, not fall through to the user's OpenRouter key.
        claim="soft", claims=_fn("chatgpt", "is_chatgpt_model"),
        connect_copy="Connect a ChatGPT account to use ChatGPT models", usage_walk=True,
        title="ChatGPT", context_window=1_050_000, guess=_starts("gpt-"), aliases=("chatgpt", "openai", "chat-gpt"), probe="_probe_chatgpt",
        lock_copy={
            "none": "You need to link a ChatGPT account.",
            "unauthed": "The linked ChatGPT sign-in is no longer valid.",
        },
    ),
    _pool(
        "zai", "Z.AI", "Z.AI pool", "zai:ZaiSource", error_label="Z.AI",
        seeds=lambda: _m("zai").ZAI_MODELS, efforts=_model_efforts("zai"),
        claim="soft", claims=lambda model: model in _m("zai").ZAI_MODELS or model.startswith("glm-"),
        connect_copy="Connect a Z.AI key to use GLM models", guard=True, usage_walk=True,
        title="Z.AI", context_window=1_000_000, guess=_starts("glm-"), aliases=("zai", "z.ai", "z-ai", "glm"), probe="_probe_zai",
        lock_copy={
            "none": "You need to link a Z.AI key, or share a live x.ai or OpenAI account.",
            "no_sub": "The linked Z.AI key needs an active GLM Coding Plan.",
            "unauthed": "The linked Z.AI key is no longer valid.",
        },
    ),
    _pool(
        "devin", "Devin", "Devin pool", "devin:DevinSource", error_label="Devin",
        seeds=lambda: _m("devin").DEVIN_MODELS, efforts=_devin_efforts,
        # Devin-prefixed ids never leave this pool, including GPT/Astra/Claude
        # slugs that would otherwise match ChatGPT or look like OpenRouter.
        claim="hard", claims=_fn("devin", "is_devin_model"), upstream=_fn("devin", "listed_upstream"),
        connect_copy="Connect a Devin account to use Devin models", usage_walk=True,
        removed_copy="Devin account removed.",
        title="Devin", context_window=200_000, guess=_starts("devin/"), aliases=("devin", "cascade", "windsurf"), probe="_probe_devin", reseller=True,
        lock_copy={
            "none": "You need to link a Devin account.",
            "no_sub": "The linked Devin account needs an active Pro plan.",
            "unauthed": "The linked Devin sign-in is no longer valid.",
        },
    ),
    _pool(
        "kimi", "Kimi", "Kimi pool", "kimi:KimiSource", error_label="Kimi",
        seeds=lambda: _m("kimi").KIMI_MODELS, efforts=_model_efforts("kimi"),
        claim="hard", claims=_fn("kimi", "is_kimi_model"),
        connect_copy="Connect a Kimi membership to use Kimi models", guard=True, usage_walk=True,
        removed_copy="Kimi account removed.",
        title="Kimi", context_window=262_144, guess=lambda mid: mid.startswith(("kimi-", "k3-", "k3.")) or mid == "k3", probe="_probe_kimi",
        lock_copy={
            "none": "You need to link a Kimi membership.",
            "no_sub": "The linked Kimi account needs an active membership.",
            "unauthed": "The linked Kimi sign-in is no longer valid.",
        },
    ),
    _pool(
        "mimo", "Xiaomi", "Xiaomi pool", "mimo:MimoSource", error_label="MiMo",
        seeds=lambda: _m("mimo").MIMO_MODELS, efforts=lambda model, user_id: ("none", "high"),
        claim="hard", claims=_fn("mimo", "is_mimo_model"),
        connect_copy="Connect a Xiaomi Token Plan to use MiMo models", guard=True, usage_walk=True,
        removed_copy="Xiaomi account removed.",
        title="Xiaomi", context_window=1_048_576, guess=_starts("mimo-"), probe="_probe_mimo",
        lock_copy={
            "none": "You need to link a Xiaomi Token Plan.",
            "no_sub": "The linked Xiaomi key is not showing a Token Plan.",
            "unauthed": "The linked Xiaomi key is no longer valid.",
        },
    ),
    _pool(
        "claude", "Anthropic", "Claude pool", "claude:ClaudeSource", pool_label="Claude", error_label="Claude",
        seeds=lambda: _m("claude").CLAUDE_MODELS, efforts=_model_efforts("claude"),
        claim="hard", claims=_fn("claude", "is_claude_model"), upstream=_fn("claude", "upstream_id"),
        public_id=lambda upstream: _m("claude_catalog").fuse_id(upstream),
        revoke_upstream=_fn("claude", "revoke_remote"), removed_copy="Claude account removed.",
        connect_copy="Connect a Claude account to use Claude models", guard=True,
        title="Claude", context_window=1_000_000, guess=_starts("claude/"), probe="_probe_claude",
        lock_copy={
            "none": "You need to link a Claude account.",
            "no_sub": "The linked Claude account needs an active subscription.",
            "unauthed": "The linked Claude sign-in is no longer valid.",
        },
    ),
    _pool(
        "cursor", "Cursor", "Cursor pool", "cursor:CursorSource", error_label="Cursor",
        seeds=lambda: _m("cursor").CURSOR_MODELS, efforts=_model_efforts("cursor"),
        listable=_fn("cursor", "listable_models"),
        claim="hard", claims=_fn("cursor", "is_cursor_model"), upstream=_fn("cursor", "upstream_id"),
        public_id=lambda upstream: _m("cursor").PREFIX + upstream,
        connect_copy="Connect a Cursor account to use Cursor models", guard=True, usage_walk=True,
        removed_copy="Cursor account removed.",
        title="Cursor", context_window=200_000, guess=_starts("cursor/"), probe="_probe_cursor", reseller=True,
        lock_copy={
            "none": "You need to link a Cursor account.",
            "unauthed": "The linked Cursor sign-in is no longer valid.",
        },
    ),
    _pool(
        "nanogpt", "NanoGPT", "NanoGPT pool", "nanogpt:NanoGPTSource", error_label="NanoGPT",
        efforts=_model_efforts("nanogpt"), listable=_fn("nanogpt", "listable_models"),
        # Its catalog lists every id; resolve() applies the user's own list.
        claim="hard", claims=_fn("nanogpt", "is_nanogpt_model"), resolve=_fn("nanogpt", "resolve"), scan=False,
        public_id=_fn("nanogpt", "public_id"), catalog_ttl=1800.0,
        model_groups=(("subscription", "Subscription models", True), ("paid", "Paid models", False)),
        model_group=lambda model: "subscription" if (_m("nanogpt").catalog_record(model) or {}).get("subscription") else "paid",
        connect_copy="Connect a NanoGPT account to use NanoGPT models", guard=True, usage_walk=True, group="aggregator",
        removed_copy=("NanoGPT account removed from Fuse. NanoGPT has no revoke API: delete the \"OAuth: Fuse\" key "
                      "on nano-gpt.com/api to end its access."),
        title="NanoGPT", context_window=128_000, guess=_starts("nanogpt/"), aliases=("nanogpt", "nano-gpt"), probe="_probe_nanogpt", reseller=True,
        lock_copy={
            "none": "Sign in with NanoGPT to use its models.",
            "unauthed": "The linked NanoGPT key is no longer valid. Sign in with NanoGPT again.",
        },
    ),
    _pool(
        "refuseless", "Refuseless", "Refuseless pool", "refuseless:RefuselessSource", error_label="Refuseless",
        efforts=_model_efforts("refuseless"),
        claim="hard", claims=_fn("refuseless", "is_refuseless_model"), resolve=_fn("refuseless", "resolve"),
        public_id=_fn("refuseless", "public_id"),
        scan=False, connect_copy="Link a Refuseless API key to use Refuseless models",
        # Its keys bill their owner per token, so a new donor starts private.
        default_share="private", usage_walk=True, group="api_key",
        title="Refuseless", context_window=128_000, guess=_starts("refuseless/"), aliases=("refuseless",), probe="_probe_refuseless", reseller=True,
        lock_copy={
            "none": "Link your Refuseless API key to use its models.",
            "unauthed": "The linked Refuseless key is no longer valid. Link a new key.",
        },
    ),
    # Experiential Labs before OpenRouter: bootstrap creates their rows in this order.
    ProviderSpec(
        id="explabs", label="Experiential Labs", type_label="Experiential Labs",
        builder="explabs:ExperientialLabsSource", error_label="Experiential Labs",
        ensure=_fn("explabs", "ensure_explabs_source"), efforts=_explabs_efforts, public_id=_fn("explabs", "public_id"),
        # Private catalogs: nothing shared to warm or scan.
        warm=False, scan=False, personal=True, panel=True, group="api_key",
        title="Experiential Labs", context_window=128_000, guess=_starts("explabs/"), aliases=("explabs", "experiential-labs"), probe="_probe_explabs", reseller=True,
    ),
    ProviderSpec(
        id="openrouter", label="OpenRouter", type_label="OpenRouter", builder="openrouter:OpenRouterSource",
        error_label="OpenRouter", ensure=_fn("openrouter", "ensure_openrouter_source"),
        efforts=_model_efforts("openrouter"),
        # One shared catalog; listable keeps it to users with a key, and only
        # the per-account resolve() admits a model.
        listable=_fn("openrouter", "listable_models"), scan=False, personal=True, usage_walk=True,
        panel=True, group="aggregator", catalog_ttl=600.0, public_id=_fn("openrouter", "public_id"),
        model_groups=(("free", "Free models", True), ("paid", "Paid models", True)),
        model_group=lambda model: "free" if _m("openrouter").model_is_free(model, _m("openrouter").cached_catalog()) else "paid",
        title="OpenRouter", context_window=128_000, guess=_starts("openrouter/"), aliases=("openrouter",), probe="_probe_openrouter",
        lock_copy={
            "none": "Add your OpenRouter API key to use free OpenRouter models.",
        },
    ),
) + tuple(_keyed(p) for p in PRESETS)
