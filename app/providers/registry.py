"""The provider registry: the one list of providers. Every per-provider list
Fuse keeps (source builders, labels, pool kinds, claim order, ...) is derived
from it."""
from __future__ import annotations

import importlib
from functools import cache

from .builtin import BUILTIN
from .spec import ProviderSpec

_BY_ID = {s.id: s for s in BUILTIN}


def specs() -> tuple[ProviderSpec, ...]:
    return BUILTIN


def get(type_id: str) -> ProviderSpec | None:
    return _BY_ID.get(type_id)


def pools() -> tuple[ProviderSpec, ...]:
    return tuple(s for s in BUILTIN if s.pool)


def pool_kinds() -> tuple[str, ...]:
    return tuple(s.id for s in pools())


def claim_order() -> tuple[ProviderSpec, ...]:
    """Providers that claim model ids by name: hard claims first, then soft."""
    return tuple(s for s in BUILTIN if s.claim == "hard") + tuple(s for s in BUILTIN if s.claim == "soft")


@cache
def builder(type_id: str):
    mod, _, attr = _BY_ID[type_id].builder.partition(":")
    return getattr(importlib.import_module(f"app.sources.{mod}"), attr)
