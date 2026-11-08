"""ChatGPT/Codex model identity and catalog state.

Picker ids are Codex slugs plus an optional `-fast` suffix for the Fast
service tier (`gpt-6.1-sol-fast` → `gpt-6.1-sol` at tier `priority`). This module
owns the slug/tier translation, the Codex catalog parsing, and the live
catalog state (`_models_cache`).
"""
from __future__ import annotations

import time

from .. import model_caps
from .base import EFFORTS

CHATGPT_MODELS = [
    "gpt-6-astra",
    "gpt-6.1-sol",
    "gpt-6.1-terra",
    "gpt-6-sol",
    "gpt-6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
]
# Codex Fast mode is a service tier on the base slug, not its own model. The
# catalog advertises it as service_tiers id "priority" (named Fast) plus
# additional_speed_tiers "fast". Requests send "priority", as Codex does: the
# backend answers "Unsupported service_tier: fast".
_FAST_TIER_IDS = frozenset({"fast", "priority"})
FAST_SERVICE_TIER = "priority"
# Picker levels for a slug whose catalog row has not been read: the ones every
# current Codex row lists (none offers none or minimal; newer ones add xhigh, max).
FALLBACK_EFFORTS = ("low", "medium", "high")

_models_cache: tuple[float, list[str]] | None = None
# slug -> (supported_reasoning_levels, default_reasoning_level) in Fuse's level
# names. Codex-only levels (ultra) are left out.
_levels: dict[str, tuple[tuple[str, ...], str]] = {}
# slug -> context window Codex uses (context_window, else max_context_window;
# the larger max_context_window is only an opt-in ceiling). Seeded from Codex's
# models.json until the live catalog is read.
_windows: dict[str, int] = {**{slug: 272_000 for slug in CHATGPT_MODELS}, "gpt-6.1-terra": 500_000}
# Responses Lite rows: Codex sends reasoning.context=all_turns so replayed
# reasoning of earlier turns is rendered, not only the current turn's.
_all_turns: set[str] = set()
# Rows whose supports_reasoning_summary_parameter is false.
_no_summary: set[str] = set()


def fast_id(slug: str) -> str:
    return f"{slug}-fast"


def base_slug(model: str) -> str:
    """Picker id → Codex slug. `gpt-6.1-sol-fast` is `gpt-6.1-sol` at the Fast tier."""
    mid = (model or "").strip()
    if len(mid) > 5 and mid.endswith("-fast"):
        return mid[:-5]
    return mid


def is_fast(model: str) -> bool:
    return bool(model) and model.endswith("-fast") and base_slug(model) != model


def _row_has_fast(row: dict) -> bool:
    tiers = row.get("service_tiers")
    if isinstance(tiers, list):
        for tier in tiers:
            tid = tier.get("id") if isinstance(tier, dict) else tier
            if str(tid or "") in _FAST_TIER_IDS:
                return True
    speeds = row.get("additional_speed_tiers")
    return isinstance(speeds, list) and any(str(s) in _FAST_TIER_IDS for s in speeds)


def parse_catalog_payload(data) -> list[str]:
    """Listed Codex slugs. `visibility: hide` rows (auto-review, reserve) are
    internal and not callable as chat models.

    A slug whose row advertises the fast service tier is also listed as
    `{slug}-fast`, right after the base slug, so the picker can offer it.
    """
    rows = (data.get("models") or data.get("data")) if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    names = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("visibility") or "list") == "hide":
            continue
        slug = str(row.get("slug") or row.get("id") or "")
        if not slug or slug in names:
            continue
        names.append(slug)
        levels = row.get("supported_reasoning_levels")
        if isinstance(levels, list):
            offered = {level.get("effort") for level in levels if isinstance(level, dict)}
            _levels[slug] = (tuple(e for e in EFFORTS if e in offered), str(row.get("default_reasoning_level") or ""))
        inputs = row.get("input_modalities")
        if isinstance(inputs, list):
            model_caps.remember("chatgpt", slug, "image" in inputs)
        window = row.get("context_window") or row.get("max_context_window")
        if isinstance(window, int) and window > 0:
            _windows[slug] = window
        _all_turns.discard(slug)
        _no_summary.discard(slug)
        if row.get("use_responses_lite") is True:
            _all_turns.add(slug)
        if row.get("supports_reasoning_summary_parameter") is False:
            _no_summary.add(slug)
        if _row_has_fast(row):
            fid = fast_id(slug)
            if fid not in names:
                names.append(fid)
                if isinstance(inputs, list):
                    model_caps.remember("chatgpt", fid, "image" in inputs)
    return names


def catalog_efforts(model: str | None) -> tuple[str, ...] | None:
    """The levels Codex lists for this model; None while its row is unread."""
    row = _levels.get(base_slug(model or ""))
    return row[0] if row else None


def efforts_for(model: str | None) -> tuple[str, ...]:
    known = catalog_efforts(model)
    return FALLBACK_EFFORTS if known is None else known


def default_effort(model: str | None) -> str:
    efforts, default = _levels.get(base_slug(model or ""), ((), ""))
    return default if default in efforts else ""


def model_window(model: str | None) -> int | None:
    """The catalog's context window for this model; None while its row is unread."""
    return _windows.get(base_slug(model or ""))


def reasoning_all_turns(model: str | None) -> bool:
    return base_slug(model or "") in _all_turns


def sends_summary(model: str | None) -> bool:
    return base_slug(model or "") not in _no_summary


def is_chatgpt_model(model: str) -> bool:
    """Seeded or live-listed Codex slug.

    `{slug}-fast` counts only when the catalog listed it: Fast mode is a
    service tier some models do not advertise, so a guessed suffix must not
    route to the pool. Once a catalog was read, a seed it dropped is gone.
    """
    if _models_cache and _models_cache[1]:
        return model in _models_cache[1]
    return model in CHATGPT_MODELS


def cached_models(ttl: float) -> list[str] | None:
    """Cached catalog names when younger than `ttl` seconds; None when a fetch is due."""
    if _models_cache and time.time() - _models_cache[0] < ttl and _models_cache[1]:
        return list(_models_cache[1])
    return None


def _remember_models(names: list[str], complete: bool = False) -> list[str]:
    """Seed plus anything Codex lists. A partial fetch only adds ids, so a
    short or failed catalog cannot drop a model the picker already showed. A
    complete read (every account tier answered) is the whole list: seeded or
    earlier ids Codex no longer lists drop out."""
    global _models_cache
    fresh = [n for n in names if isinstance(n, str) and n.startswith("gpt-")]
    if not fresh and _models_cache and _models_cache[1]:
        return list(_models_cache[1])
    base = list(_models_cache[1]) if _models_cache and _models_cache[1] else list(CHATGPT_MODELS)
    if complete and fresh:
        base = []
    merged: list[str] = []
    # Upstream order first (new models sit where the provider puts them),
    # then ids an earlier catalog listed and this one omits.
    for name in fresh + base:
        if name not in merged:
            merged.append(name)
    if merged:
        _models_cache = (time.time(), merged)
    return merged or list(CHATGPT_MODELS)
