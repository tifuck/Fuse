"""ProviderSpec: everything Fuse needs to know about one model provider.

A spec is plain data plus small callables. Callables reach into app.sources
lazily (at call time), so the registry imports nothing heavy and every module,
pool_policy included, can derive its lists from it at import.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal


@dataclass(frozen=True)
class ProviderSpec:
    id: str  # sources.type and usage provider id
    label: str  # provider name on usage, models and picker groups
    type_label: str  # source type name on admin and dashboard pages
    builder: str  # "module:Class" under app.sources, called as Class(row, cfg, creds)
    pool: bool = False  # shared subscription pool (pool_policy, pool_pick, auto prices)
    pool_label: str = ""  # name on pool pages; defaults to label
    error_label: str = ""  # name on provider error cards; "" shows "Provider"
    builtin: bool = True  # its source row cannot be deleted by an admin
    # Makes sure the source row exists; bootstrap calls these in registry order.
    ensure: Callable[[], int] | None = None
    seeds: Callable[[], list] | None = None  # catalog shown until the live one loads
    # (model, user_id) -> picker efforts; None (or no callable) offers every level.
    efforts: Callable[[str | None, int | None], tuple | None] | None = None
    listable: Callable[[list, int | None], list] | None = None  # narrow a catalog per user
    warm: bool = True  # fetch its shared catalog at boot
    # Routing. "hard": a claimed id never leaves this provider; "soft": it
    # falls through to later providers when this one cannot take it.
    claim: Literal["", "hard", "soft"] = ""
    claims: Callable[[str], bool] | None = None
    upstream: Callable[[str], str] | None = None  # public id -> upstream id
    resolve: Callable | None = None  # async (model, user_id) -> upstream or None
    scan: bool = True  # a model in its cached catalog routes here
    # Which keyword arguments Source.chat takes (see base.Source).
    call_style: Literal["plain", "session", "pool", "user"] = "plain"
    # Access. No access callable: every user may use its site sources.
    access: Callable[[int], dict] | None = None  # {"ok": bool, "reason": str, ...}
    own_access: Callable[[int], dict] | None = None  # the user's own accounts alone
    connect_copy: str = ""  # what to connect when access is denied
    guard: bool = False  # pool_policy outside-usage guard
    default_share: str = "everyone"  # pool_policy share level of a new donor
    usage_walk: bool = False  # usage_walk refreshes its credentials
    keyed: object | None = None  # the KeyedPreset of an API-key provider (presets.py)
