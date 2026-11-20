from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable

from .. import db
from ..config import settings
from ..providers import registry
from ..providers.access import Access
from .base import ChatResult, Source
from .chatgpt import CHATGPT_MODELS, ChatGPTSource, ensure_chatgpt_source, refresh_due as chatgpt_refresh_due, user_has_chatgpt
from .grok import GROK_MODELS, GrokSource, ensure_grok_source, grok_access_state, refresh_due, user_has_grok
from . import keyed as keyed_mod, llama as llama_mod, ollama as ollama_mod
from .llama import LlamaSource
from .openai_compat import OpenAICompatSource
from .ollama import DEFAULT_BASE as OLLAMA_BASE, OllamaSource
from .openrouter import OpenRouterSource, ensure_openrouter_source, user_has_openrouter
from .explabs import ExperientialLabsSource, ensure_explabs_source
from .zai import ZAI_MODELS, ZaiSource, ensure_zai_source, user_has_zai
from .devin import DEVIN_MODELS, DevinSource, ensure_devin_source, user_has_devin
from .kimi import KIMI_MODELS, KimiSource, ensure_kimi_source, user_has_kimi
from .mimo import MIMO_MODELS, MimoSource, ensure_mimo_source, user_has_mimo
from .claude import CLAUDE_MODELS, ClaudeSource, ensure_claude_source, user_has_claude
from .cursor import CURSOR_MODELS, CursorSource, ensure_cursor_source, user_has_cursor
from .nanogpt import NanoGPTSource, ensure_nanogpt_source, user_has_nanogpt
from .refuseless import RefuselessSource, ensure_refuseless_source, user_has_refuseless

logger = logging.getLogger(__name__)

_BUILDERS: dict[str, Callable] = {spec.id: registry.builder(spec.id) for spec in registry.specs()}
TYPES = {spec.id: spec.type_label for spec in registry.specs()}
PROVIDER_LABELS = {**{spec.id: spec.label for spec in registry.specs()}, "unknown": "Unknown"}
POOL_TYPES = frozenset(registry.pool_kinds())
_SEEDED_MODELS = {spec.id: spec.seeds() for spec in registry.specs() if spec.seeds}


def _row_source(row, creds=None) -> Source:
    cfg = json.loads(row["config"] or "{}")
    if not isinstance(cfg, dict):
        raise ValueError("source config must be a JSON object")
    if creds is None:
        creds = db.fetchall(
            "SELECT * FROM credentials WHERE source_id=? AND disabled=0", (row["id"],)
        )
    return _BUILDERS[row["type"]](row, cfg, creds)


def all_enabled() -> list[Source]:
    rows = [row for row in db.fetchall("SELECT * FROM sources WHERE enabled=1 ORDER BY id") if row["type"] in _BUILDERS]
    # One credentials read for every source, not one per source: this runs
    # on every model listing and every routed request.
    creds: dict[int, list] = {row["id"]: [] for row in rows}
    if creds:
        marks = ",".join("?" for _ in creds)
        for cred in db.fetchall(
            f"SELECT * FROM credentials WHERE disabled=0 AND source_id IN ({marks}) ORDER BY id", tuple(creds)
        ):
            creds[cred["source_id"]].append(cred)
    out = []
    for row in rows:
        try:
            out.append(_row_source(row, creds[row["id"]]))
        except Exception as exc:
            logger.warning(
                "Skipping malformed source id=%s type=%s: %s",
                row["id"],
                row["type"],
                type(exc).__name__,
            )
    return out


def get(source_id: int) -> Source | None:
    row = db.fetchone("SELECT * FROM sources WHERE id=?", (source_id,))
    if not row or row["type"] not in _BUILDERS or not row["enabled"]:
        return None
    try:
        return _row_source(row)
    except Exception as exc:
        logger.warning("Could not load source id=%s: %s", source_id, type(exc).__name__)
        return None


_cache: dict[int, tuple[float, list[str]]] = {}
_MODEL_TTL = 300.0
_model_cache: dict[tuple[int, int | None], tuple[float, list[str]]] = {}
_unavailable_types: set[str] = set()
_refreshing: dict[tuple[int, int | None], asyncio.Task] = {}
# Keys whose live catalog has loaded, or whose first load was already waited
# on once. A failing provider must not stall every page for the full wait.
_live: set[tuple[int, int | None]] = set()
# How long a page waits for a pool's first live catalog before showing seeds.
_FIRST_FETCH_WAIT = 4.0
# A source with no catalog and no seeds retries a failed fetch this often, in
# the background, instead of on every page that lists models.
_FAILED_RETRY_S = 60.0


def _cache_key(s: Source, user_id: int | None) -> tuple[int, int | None]:
    """Shared catalogs are one per source; per-user lists go through
    spec.user_catalog instead."""
    return (s.source_id, None)


def _ttl(s: Source) -> float:
    spec = registry.get(s.type_id)
    return spec.catalog_ttl if spec else _MODEL_TTL


# Upstream catalog fetches running at once, per event loop: a restart or a
# quiet spell must not fire every provider's fetch together.
MAX_FETCHES = 2
_slots: dict[int, asyncio.Semaphore] = {}


def _fetch_slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    slot = _slots.get(id(loop))
    if slot is None or getattr(slot, "_fuse_loop", None) is not loop:
        slot = asyncio.Semaphore(MAX_FETCHES)
        slot._fuse_loop = loop
        _slots.clear()
        _slots[id(loop)] = slot
    return slot


def _sticky_names(key: tuple[int, int | None], names: list[str]) -> list[str]:
    """Pool catalogs only grow. A short or failed upstream list must not drop a
    model the picker already showed. The upstream order leads, so a new model
    sits where the provider lists it rather than after every older id."""
    prev = _model_cache.get(key)
    if not prev:
        return names
    merged = list(names)
    for name in prev[1]:
        if name not in merged:
            merged.append(name)
    return merged


def _store_models(key: tuple[int, int | None], names: list[str], *, sticky: bool = False) -> None:
    if sticky:
        names = _sticky_names(key, names)
    now = time.time()
    _model_cache[key] = (now, names)
    _cache[key[0]] = (now, names)


async def _refresh_source_models(s: Source, user_id: int | None) -> list[str]:
    key = _cache_key(s, user_id)
    hit = _model_cache.get(key)
    try:
        async with _fetch_slot():
            m = await s.models()
        stale_only = False
        empty_ok = s.type_id in POOL_TYPES
        if s.type_id == "openrouter":
            from . import openrouter as openrouter_mod

            stale_only = not openrouter_mod.catalog_available()
        if not isinstance(m, list) or (not m and not empty_ok):
            raise ValueError("empty or invalid model catalog")
        m = [name for name in m if isinstance(name, str) and name]
        if not m and not empty_ok:
            raise ValueError("empty model catalog")
    except Exception as exc:
        _unavailable_types.add(s.type_id)
        logger.warning("Model catalog unavailable for source id=%s: %s", s.source_id, type(exc).__name__)
        delay = _persist_failure(s, exc)
        if not (hit and hit[1]) and not _fallback_models(s):
            _model_cache[key] = (time.time() - _ttl(s) + delay, [])
        return hit[1] if hit else []
    if stale_only:
        _unavailable_types.add(s.type_id)
    else:
        _unavailable_types.discard(s.type_id)
    _store_models(key, m, sticky=s.type_id in POOL_TYPES)
    _live.add(key)
    _persist_success(s, _model_cache[key][1])
    return _model_cache[key][1]


def _persist_success(s: Source, names: list[str]) -> None:
    from ..providers import catalogs

    meta = getattr(s, "catalog_meta", None) or {}
    try:
        catalogs.saved(s.source_id, s.type_id, names, _ttl(s), etag=meta.get("etag"),
                       last_modified=meta.get("last_modified"))
    except Exception:
        logger.warning("Could not store the catalog of source id=%s", s.source_id, exc_info=True)


def _persist_failure(s: Source, exc: Exception) -> float:
    """Record the failure; returns how long until the next try (backoff)."""
    from ..providers import catalogs

    try:
        return catalogs.failed(s.source_id, s.type_id, type(exc).__name__, _ttl(s))
    except Exception:
        logger.warning("Could not store the catalog failure of source id=%s", s.source_id, exc_info=True)
        return _FAILED_RETRY_S


def _schedule_model_refresh(s: Source, user_id: int | None) -> asyncio.Task | None:
    key = _cache_key(s, user_id)
    running = _refreshing.get(key)
    if running is not None:
        return running
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None

    async def _run():
        try:
            await _refresh_source_models(s, user_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Background model catalog refresh failed for source id=%s", s.source_id, exc_info=True)
        finally:
            _refreshing.pop(key, None)

    task = loop.create_task(_run())
    _refreshing[key] = task
    return task


async def _await_first_fetch(s: Source, user_id: int | None) -> None:
    """Give a pool still on its seed list a moment to load the live catalog.

    Without this, the first page after every restart showed only the seeds
    (no Sonnet 5.5, no gpt-6.1-sol) and the live list appeared on reload.
    """
    task = _schedule_model_refresh(s, user_id)
    if task is None:
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), _FIRST_FETCH_WAIT)
    except (asyncio.TimeoutError, Exception):
        pass
    _live.add(_cache_key(s, user_id))


def _fallback_models(s: Source) -> list[str]:
    """Pool seeds only. A configured llama/openai name is a placeholder for a
    down server, not a catalog: storing it would pin the picker to that name
    after the server is swapped to a different model."""
    return list(_SEEDED_MODELS.get(s.type_id) or [])


async def _source_models(s: Source, user_id: int | None) -> list[str]:
    if s.type_id == "explabs":
        # Private catalogs have credential revisions and never enter shared caches.
        from .explabs import CatalogError, cached_models

        names = cached_models(user_id)
        if names is not None:
            return names
        try:
            return await s.models(user_id=user_id)
        except CatalogError:
            return []
    spec = registry.get(s.type_id)
    if spec.user_catalog and spec.user_catalog(user_id):
        # The catalog follows the key this user routes through; the source caches it.
        return await s.models(user_id=user_id)
    key = _cache_key(s, user_id)
    hit = _model_cache.get(key) or _stored(s)
    if hit and key not in _live and (s.type_id in POOL_TYPES or spec.catalog_state):
        await _await_first_fetch(s, user_id)
        hit = _model_cache.get(key) or hit
    if hit:
        if time.time() - hit[0] >= _ttl(s):
            _schedule_model_refresh(s, user_id)
        return hit[1]
    fallback = _fallback_models(s)
    if fallback:
        await _await_first_fetch(s, user_id)
        hit = _model_cache.get(key)
        return hit[1] if hit else fallback
    # Nothing cached or saved yet: wait for the (single-flight) first fetch.
    task = _schedule_model_refresh(s, user_id)
    if task is None:
        return await _refresh_source_models(s, user_id)
    try:
        await asyncio.shield(task)
    except Exception:
        pass
    hit = _model_cache.get(key)
    return hit[1] if hit else []


def _stored(s: Source) -> tuple[float, list[str]] | None:
    """The saved catalog of a source missing from memory (a cache was just
    dropped): served at once while a fresh fetch runs behind it."""
    from ..providers import catalogs

    try:
        row = catalogs.get(s.source_id)
    except Exception:
        return None
    if not row or not row["models"]:
        return None
    _remember_saved(s, row)
    return _model_cache[_cache_key(s, None)]


def _remember_saved(s: Source, row: dict) -> None:
    """Put a saved list in memory. A source whose fetch also learns per-model
    facts (spec.catalog_state) gets it as a stale fallback: its first page
    waits briefly for a live fetch, which restores those facts."""
    key = _cache_key(s, None)
    spec = registry.get(s.type_id)
    if spec and spec.catalog_state:
        stale = time.time() - _ttl(s) - 1
        _model_cache[key] = _cache[s.source_id] = (stale, list(row["models"]))
        return
    _model_cache[key] = _cache[s.source_id] = (row["fetched_at"], list(row["models"]))
    _live.add(key)


async def _user_source_models(s: Source, user_id: int | None) -> list[str]:
    """What this user lists from one source: what they could run, minus the
    models they turned off on Sources."""
    from ..providers import model_prefs

    names = await _source_models(s, user_id)
    spec = registry.get(s.type_id)
    names = spec.listable(names, user_id) if spec and spec.listable else names
    return model_prefs.visible(s.type_id, user_id, names)


def choosable_models(user_id: int, type_ids) -> dict[str, list[str]]:
    """Every model this user could turn on, per provider, from what is
    already cached (no fetch): the Sources model lists."""
    from . import nanogpt as nanogpt_mod
    from .explabs import cached_models as explabs_models

    wanted = set(type_ids)
    out: dict[str, list[str]] = {type_id: [] for type_id in wanted}
    for s in all_enabled():
        spec = registry.get(s.type_id)
        if s.type_id not in wanted or spec is None:
            continue
        if s.type_id == "explabs":
            listed = explabs_models(user_id) or []
        elif spec.user_catalog and spec.user_catalog(user_id):
            cached = getattr(s, "cached_models", None)
            listed = cached(user_id) if cached else []
        else:
            hit = _model_cache.get(_cache_key(s, None)) or _stored(s)
            listed = list(hit[1]) if hit else []
        if s.type_id == "nanogpt":
            listed = nanogpt_mod.lane_models(listed, user_id)
        elif spec.listable:
            listed = spec.listable(listed, user_id)
        names = out[s.type_id]
        names.extend(name for name in listed if name not in names)
    return out


# Picker efforts per provider. Providers without their own list get every
# OpenAI-style level; GENERIC_EFFORTS marks that as a guess, not a capability.
GENERIC_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def reasoning_efforts(type_id: str, model: str | None = None, user_id: int | None = None) -> tuple[str, ...]:
    spec = registry.get(type_id)
    efforts = spec.efforts(model, user_id) if spec and spec.efforts else None
    return GENERIC_EFFORTS if efforts is None else efforts


def metadata_effort(type_id: str, model: str | None = None, user_id: int | None = None) -> str | None:
    """Cheapest effort a provider accepts, for short title/recap calls.

    None means send no effort: the provider's levels are unknown (the generic
    list) and a guess like "none" is a 400 on Codex and Grok."""
    efforts = reasoning_efforts(type_id, model, user_id)
    if not efforts or efforts == GENERIC_EFFORTS:
        return None
    return "none" if "none" in efforts else efforts[0]


def invalidate_model_cache(source_type: str | None = None) -> None:
    """Drop cached model lists, e.g. after a per-user key or preference change."""
    if source_type is None:
        _model_cache.clear()
        _cache.clear()
        _live.clear()
        llama_mod._models_cache.clear()
        ollama_mod._tags_cache.clear()
        ollama_mod._own_tags.clear()
        keyed_mod.clear_catalogs()
        _forget_stored(None)
        return
    sids = [s.source_id for s in all_enabled() if s.type_id == source_type]
    for sid in sids:
        _cache.pop(sid, None)
        # Their own upstream lists: a changed server URL must not keep
        # advertising the previous server's models until the TTL.
        llama_mod._models_cache.pop(sid, None)
        ollama_mod._tags_cache.pop(sid, None)
        for key in [k for k in ollama_mod._own_tags if k[0] == sid]:
            ollama_mod._own_tags.pop(key, None)
        keyed_mod.clear_catalogs(sid)
    for key in [k for k in _model_cache if k[0] in sids]:
        _model_cache.pop(key, None)
        _live.discard(key)
    _forget_stored(sids)


def _forget_stored(sids) -> None:
    from ..providers import catalogs

    try:
        catalogs.forget(sids)
    except Exception:
        logger.warning("Could not drop stored catalogs", exc_info=True)


def catalog_unavailable_for(model: str) -> bool:
    if not model:
        # No id (an empty /v1/models listing): any catalog outage explains it.
        return bool(_unavailable_types)
    if model.startswith("explabs/"):
        return False
    if model.startswith("devin/") or model.startswith("devin-"):
        return "devin" in _unavailable_types
    if model.startswith("claude/"):
        return "claude" in _unavailable_types
    if model.startswith("cursor/"):
        return "cursor" in _unavailable_types
    if model.startswith("nanogpt/"):
        return "nanogpt" in _unavailable_types
    if model.startswith("refuseless/"):
        return "refuseless" in _unavailable_types
    if model.startswith("openrouter/") or ("/" in model and not model.startswith("/")):
        return "openrouter" in _unavailable_types
    if model.startswith("grok"):
        return "grok" in _unavailable_types
    if model.startswith("glm-"):
        return "zai" in _unavailable_types
    if model.startswith("gpt-"):
        return "chatgpt" in _unavailable_types
    if model in KIMI_MODELS or model.startswith("kimi-for-coding") or model == "k3" or model.startswith(("k3-", "k3.")):
        return "kimi" in _unavailable_types
    if model in MIMO_MODELS or model.startswith("mimo-"):
        return "mimo" in _unavailable_types
    return bool(_unavailable_types & {"llama", "openai", "ollama"})


def _sources(access: Access, enabled: list[Source] | None = None) -> list[Source]:
    return [s for s in (all_enabled() if enabled is None else enabled) if access.allows(s.type_id)]


def _openrouter_source(enabled: list[Source] | None = None) -> Source | None:
    for s in all_enabled() if enabled is None else enabled:
        if s.type_id == "openrouter":
            return s
    return None


def _seed_pool_catalogs() -> None:
    """At boot: each source's saved catalog, else its seed list. A saved list
    is served without the first-load wait (unless spec.catalog_state); the
    refresh loop updates it when due."""
    from ..providers import catalogs

    try:
        saved = catalogs.all_rows()
    except Exception:
        logger.warning("Could not read saved catalogs", exc_info=True)
        saved = {}
    stale = time.time() - _MODEL_TTL - 1
    for s in all_enabled():
        key = _cache_key(s, None)
        if key in _model_cache:
            continue
        row = saved.get(s.source_id)
        if row and row["models"]:
            _remember_saved(s, row)
            continue
        names = _SEEDED_MODELS.get(s.type_id)
        if names:
            _model_cache[key] = (stale, list(names))
            _cache[s.source_id] = (stale, list(names))


async def models_union(*, user_id: int | None = None, access: Access | None = None,
                       enabled: list[Source] | None = None) -> list[str]:
    sources = _sources(access or Access(user_id), enabled)
    results = await asyncio.gather(*(_user_source_models(s, user_id) for s in sources))
    names: list[str] = []
    for m in results:
        for n in m:
            if n not in names:
                names.append(n)
    return names


async def _claimed(spec, s: Source, model: str, user_id: int | None) -> tuple[Source, str] | None:
    if spec.resolve:
        upstream = await spec.resolve(model, user_id)
        return (s, upstream) if upstream else None
    return s, spec.upstream(model) if spec.upstream else model


async def pick(model: str, *, user_id: int | None = None, access: Access | None = None,
               ignore_prefs: bool = False) -> tuple[Source, str] | None:
    """The source and upstream id for `model`, or None. `access` (default:
    computed for user_id) gates every provider that has an access verdict,
    and a model the user turned off on Sources is refused (ignore_prefs:
    say where it would have gone, for the 403 that explains it)."""
    picked = await _pick(model, user_id=user_id, access=access or Access(user_id))
    if picked and not ignore_prefs and not model_allowed(picked[0].type_id, user_id, model, picked[1]):
        return None
    return picked


def model_allowed(type_id: str, user_id: int | None, model: str, upstream: str) -> bool:
    """The user's on/off choice for a routed model, read by its public id (a
    routed alias by its target's)."""
    from ..providers import model_prefs

    spec = registry.get(type_id)
    public = spec.public_id(upstream) if spec and spec.public_id and model != upstream else model
    return model_prefs.allowed(type_id, user_id, public)


async def _pick(model: str, *, user_id: int | None, access: Access) -> tuple[Source, str] | None:
    from . import openrouter as openrouter_mod
    from . import explabs as explabs_mod

    if model.startswith(explabs_mod.PUBLIC_PREFIX):
        for src in all_enabled():
            if src.type_id == "explabs":
                resolved = await explabs_mod.resolve(model, user_id)
                return (src, resolved) if resolved else None
        return None
    route = db.fetchone("SELECT * FROM model_routes WHERE model=?", (model,))
    if route:
        src = get(route["source_id"])
        if src and src.type_id != "explabs" and access.allows(src.type_id):
            upstream = route["upstream_model"] or model
            if src.type_id != "openrouter":
                return src, upstream
            # A route onto OpenRouter bills the caller's own key, so it obeys
            # the same per-account free/paid enablement as a direct id.
            if await openrouter_mod.resolve(openrouter_mod.public_id(upstream), user_id):
                return src, upstream
            return None
    # One read of the enabled sources serves every lookup below (two queries
    # each, and this runs on every routed request).
    enabled = all_enabled()
    # Claims by name (registry order). A hard claim never leaves its provider;
    # a soft one falls through when its provider cannot take the id.
    for spec in registry.claim_order():
        if not spec.claims(model):
            continue
        if access.allows(spec.id):
            for s in enabled:
                if s.type_id == spec.id:
                    return await _claimed(spec, s, model, user_id)
        if spec.claim == "hard":
            return None
    or_src = _openrouter_source(enabled)
    if or_src:
        resolved = await openrouter_mod.resolve(model, user_id)
        if resolved:
            return or_src, resolved[0]
    await models_union(user_id=user_id, access=access, enabled=enabled)
    for s in _sources(access, enabled):
        # OpenRouter's _cache is whichever account refreshed last; only its
        # per-account resolve() above may admit a model for this user.
        # NanoGPT's lists every catalog id; its resolve() applies the user's list.
        spec = registry.get(s.type_id)
        if not spec.scan:
            continue
        if spec.user_catalog and spec.user_catalog(user_id):
            names = await s.models(user_id=user_id)
        else:
            names = (_cache.get(s.source_id) or (0, []))[1]
        if model in names:
            return s, spec.upstream(model) if spec.upstream else model
    no_gates = Access()
    for s in _sources(no_gates, enabled):
        if s.type_id != "llama":
            continue
        cfg = getattr(s, "cfg", None) or {}
        named = str(cfg.get("model") or "")
        if named and named not in ("local", "llama") and model == named:
            return s, named
    others = [s for s in _sources(no_gates, enabled) if s.type_id == "llama"]
    if len(others) == 1 and len(_sources(no_gates, enabled)) == 1:
        return others[0], model
    return None


_POOL_CONNECT = {spec.id: spec.connect_copy for spec in registry.specs() if spec.connect_copy}


def _signed_out(type_id: str, user_id: int) -> bool:
    """The user's own account here was disabled because the provider
    rejected it (accounts.disable_credential marks those)."""
    from .. import accounts

    rows = db.fetchall(
        "SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id"
        " WHERE s.type=? AND c.donated_by=? AND c.disabled=1",
        (type_id, user_id),
    )
    for row in rows:
        try:
            if accounts.cred_extra(row).get("disabled_reason") == "rejected":
                return True
        except Exception:
            continue
    return False


def pool_denial_message(type_id: str, user_id: int) -> str:
    """Why this user may not use a pool, in the "<Provider>: <summary>. <detail>"
    shape the chat card and provider errors share."""
    from . import pool_policy
    from ..provider_errors import label

    name = label(type_id)
    if _signed_out(type_id, user_id):
        return (f"{name}: account sign-in rejected. {name} stopped accepting your account's sign-in "
                "(it expired or was revoked), so Fuse disconnected it. Reconnect it on Sources.")
    connect = _POOL_CONNECT.get(type_id, f"Connect a {name} account to use its models")
    if type_id == "zai" and pool_policy.uses_shared_pool(user_id, "zai"):
        connect = "Connect a Z.AI key, or share a live x.ai or OpenAI account, to use GLM models"
    return f"{name}: access denied. {connect}."


def pool_denial(model: str, user_id: int, access: Access) -> str | None:
    """The 403 message when `model` belongs to a configured pool the caller
    has no usable account for (`access` denies it), or None.

    pick() resolves such ids to None (a disabled credential must not keep
    serving), so without this a user whose account was disabled mid-session
    saw "Unknown model" with nothing to reconnect."""
    enabled = {s.type_id for s in all_enabled()}
    # Detection order mirrors pick()'s, so an id two pools could claim denies
    # as the one pick would have resolved it to.
    for spec in registry.claim_order():
        if spec.pool and spec.id in enabled and spec.claims(model) and not access.allows(spec.id):
            return pool_denial_message(spec.id, user_id)
    return None


def _ensure_typed_source(type_id: str, name: str, config: dict) -> int:
    row = db.fetchone_memo("SELECT id FROM sources WHERE type=?", (type_id,))
    if row:
        return row["id"]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,?,?,?)",
            (type_id, name, 1, json.dumps(config), db.now()),
        )
        return db.last_id(conn)


def ensure_llama_source() -> int:
    return _ensure_typed_source(
        "llama",
        "Local llama",
        {"base_url": settings.llama_base_url or "http://127.0.0.1:8080/v1"},
    )


def ensure_ollama_source() -> int:
    return _ensure_typed_source("ollama", "Ollama Cloud", {"base_url": OLLAMA_BASE})


def ensure_chatgpt_source_row() -> int:
    return ensure_chatgpt_source()


async def models_by_source(*, user_id: int | None = None, access: Access | None = None) -> list[dict]:
    sources = _sources(access or Access(user_id))
    results = await asyncio.gather(*(_user_source_models(s, user_id) for s in sources))
    groups = [
        {
            "type": s.type_id,
            "name": s.name,
            "label": PROVIDER_LABELS.get(s.type_id, s.name),
            "models": names,
        }
        for s, names in zip(sources, results)
    ]
    return groups


def start_pools() -> None:
    for s in all_enabled():
        pool = getattr(s, "pool", None)
        if pool is not None:
            pool.start_sweeper()


def warm_catalogs() -> asyncio.Task | None:
    """At boot: fetch the catalogs that are due (no saved list, or a stale
    one) in the background, at most MAX_FETCHES at a time."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return loop.create_task(refresh_due_catalogs(boot=True))


def _can_fetch(s: Source, spec) -> bool:
    """A shared catalog worth fetching: the provider is on (all_enabled), it
    has a shared list, and some credential can read it."""
    if not spec or not spec.warm:
        return False
    if spec.catalog_keyless:
        return True
    return bool(getattr(s, "all_creds", None) or getattr(s, "creds", None))


def due_catalogs(now: float | None = None, *, boot: bool = False) -> list[Source]:
    """Enabled sources whose shared catalog is due, soonest first. Never one
    whose fetch is already running (single flight). At boot, a source whose
    fetch learns per-model facts (spec.catalog_state) is always due."""
    from ..providers import catalogs

    now = time.time() if now is None else now
    try:
        saved = catalogs.all_rows()
    except Exception:
        logger.warning("Could not read saved catalogs", exc_info=True)
        saved = {}
    due = []
    for s in all_enabled():
        if not _can_fetch(s, registry.get(s.type_id)) or _cache_key(s, None) in _refreshing:
            continue
        row = saved.get(s.source_id)
        if row and row["next_due"] > now and not (boot and registry.get(s.type_id).catalog_state):
            continue
        due.append((row["next_due"] if row else 0.0, s))
    due.sort(key=lambda item: item[0])
    return [s for _due, s in due]


async def refresh_due_catalogs(*, boot: bool = False) -> None:
    """Refresh-loop job: fetch every shared catalog that is due. Fetches share
    MAX_FETCHES slots; a failure backs off (catalogs.failed); due times carry
    jitter, so providers drift apart instead of firing together."""
    tasks = [task for s in due_catalogs(boot=boot) if (task := _schedule_model_refresh(s, None)) is not None]
    if tasks:
        await asyncio.gather(*(asyncio.shield(t) for t in tasks), return_exceptions=True)


def refresh_catalog_now(type_id: str) -> None:
    """A first key for this provider: fetch its catalog now, in the background."""
    from ..providers import catalogs

    for s in all_enabled():
        if s.type_id != type_id or not _can_fetch(s, registry.get(type_id)):
            continue
        try:
            catalogs.mark_due(s.source_id)
        except Exception:
            pass
        _schedule_model_refresh(s, None)


async def stop_pools() -> None:
    from .ollama import stop_pools as stop_ollama_pools

    await stop_ollama_pools()


async def bootstrap() -> None:
    from ..accounts import add_credential
    from ..pricing import ensure_default_prices

    for spec in registry.specs():
        if spec.ensure:
            spec.ensure()
    from .openrouter import clear_global_key

    clear_global_key()
    ensure_default_prices()
    llama_id = ensure_llama_source()
    if settings.llama_api_key:
        existing = db.fetchone("SELECT id FROM credentials WHERE source_id=?", (llama_id,))
        if not existing:
            add_credential(llama_id, "llama", settings.llama_api_key)
    if settings.llama_base_url:
        row = db.fetchone("SELECT id, config FROM sources WHERE id=?", (llama_id,))
        try:
            cfg = json.loads(row["config"] or "{}") if row else {}
            if not isinstance(cfg, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.warning("Resetting malformed llama source config during bootstrap")
            cfg = {}
        if not cfg.get("base_url"):
            cfg["base_url"] = settings.llama_base_url
            db.execute("UPDATE sources SET config=? WHERE id=?", (json.dumps(cfg), llama_id))
    ensure_ollama_source()
    from . import nanogpt as nanogpt_mod, openrouter as openrouter_mod, refuseless as refuseless_mod
    from ..providers import model_prefs

    for load in (openrouter_mod.load_saved, nanogpt_mod.load_saved, refuseless_mod.load_saved):
        try:
            load()
        except Exception:
            logger.warning("Could not load a saved catalog", exc_info=True)
    _seed_pool_catalogs()
    try:
        model_prefs.migrate_legacy()
    except Exception:
        logger.warning("Could not move older model choices", exc_info=True)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if openrouter_mod.cached_catalog() is None:
        loop.create_task(openrouter_mod.fetch_catalog())
    if nanogpt_mod.cached_catalog() is None:
        loop.create_task(nanogpt_mod.fetch_catalog())
