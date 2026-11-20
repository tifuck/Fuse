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
    # async (credential row) -> None: end the grant at the provider when a
    # user removes the account (only where the provider documents a revoke).
    revoke_upstream: Callable | None = None
    removed_copy: str = ""  # flash after a user removes an account; "" shows none
    keyed: object | None = None  # the KeyedPreset of an API-key provider (presets.py)
    # How users add a personal key on Sources (a KeyedPreset); keyed providers
    # and Ollama Cloud have one.
    key: object | None = None
    # True for a user whose model list is their own (Source.models(user_id=))
    # rather than the shared cached catalog.
    user_catalog: Callable[[int | None], bool] | None = None
    # Sources directory (app/providers/directory.py).
    group: str = "local"  # subscription, api_key, aggregator or local
    vendor: str = ""  # shown on this provider's pane (an API key beside its subscription)
    panel: bool = False  # has its own panel, templates/sources/_pool_<id>.html
    site: bool = False  # an admin adds keys (or a server) that serve users
    # Shared catalog refresh (app.sources.refresh_due_catalogs): seconds
    # between background fetches, and whether a fetch needs no credential.
    catalog_ttl: float = 21600.0
    catalog_keyless: bool = False
    # models() also learns per-model facts kept only in memory (context
    # windows, vision, local aliases, catalog metadata): a saved list alone
    # is not enough, so the catalog is fetched at boot and the first page
    # waits for it briefly, as for a pool.
    catalog_state: bool = False
    # Per-user model visibility (app/providers/model_prefs.py): groups as
    # (id, label, on by default), the group of a public model id, and the
    # public id of an upstream one (routed aliases are checked by it).
    model_groups: tuple = ()
    model_group: Callable[[str], str] | None = None
    public_id: Callable[[str], str] | None = None
    admin_anchor: str = ""  # its settings on the admin pages; default /admin/sources#<id>
