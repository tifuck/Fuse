"""The Sources page directory: one pane per provider, grouped.

A provider with a hand-written panel (templates/sources/_pool_<id>.html) or a
personal key gets a pane; an API key whose spec names a vendor sits on that
vendor's pane instead (xAI API beside the x.ai pool).
"""
from __future__ import annotations

from .. import db
from . import model_prefs, registry
from .access import Access

# Providers whose panel draws its own model list (with prices and provider pins).
OWN_LIST = frozenset({"openrouter", "nanogpt"})

GROUPS = (("subscription", "Subscriptions"), ("api_key", "API keys"), ("aggregator", "Aggregators"), ("local", "Local"))


def _pane(spec, title: str, partial: str | None) -> dict:
    return {"id": spec.id, "title": title, "group": spec.group, "partial": partial, "keys": [],
            "names": list(dict.fromkeys([title.lower(), spec.label.lower()]))}


def panes(key_entries: list[dict]) -> list[dict]:
    """Panes in group order, then registry order. key_entries: the user's
    providers that take a personal key (keyed.directory)."""
    keys = {entry["id"]: entry for entry in key_entries}
    by_id: dict[str, dict] = {}
    for spec in registry.specs():
        if spec.panel:
            by_id[spec.id] = _pane(spec, spec.pool_label or spec.label, f"sources/_pool_{spec.id}.html")
    for spec in registry.specs():
        entry = keys.get(spec.id)
        if entry is None:
            continue
        pane = by_id.get(spec.vendor) or by_id.setdefault(spec.id, _pane(spec, spec.label, None))
        pane["keys"].append(entry)
        if spec.label.lower() not in pane["names"]:
            pane["names"].append(spec.label.lower())
    order = [group for group, _label in GROUPS]
    return sorted(by_id.values(), key=lambda pane: order.index(pane["group"]))


def group_switches(provider: str, user_id: int) -> list[dict]:
    prefs = model_prefs.load(user_id, provider)
    return [{"id": g, "label": label, "on": model_prefs.group_on(provider, g, prefs)}
            for g, label, _default in model_prefs.groups(provider)]


def _usd(value) -> float | None:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    return rate if rate >= 0 else None


def _refuseless_prices(rows: list[dict]) -> bool:
    """Refuseless bills per token at its own lineup's rates (USD per million):
    put them on its rows. False when the lineup lists none."""
    from ..sources import refuseless

    for row in rows:
        pricing = (refuseless.catalog_record(row["id"]) or {}).get("pricing") or {}
        row["input"], row["output"] = _usd(pricing.get("input_per_m")), _usd(pricing.get("output_per_m"))
    return any(row["input"] is not None for row in rows)


def model_lists(user_id: int, panes: list[dict]) -> dict[str, list[dict]]:
    """Per pane: one model list per provider on it the user can use, from
    cached catalogs only (a provider whose list has not loaded yet shows none)."""
    from ..sources import choosable_models

    access = Access(user_id)
    wanted = {}
    for pane in panes:
        ids = ([pane["id"]] if pane["partial"] else []) + [entry["id"] for entry in pane["keys"]]
        wanted[pane["id"]] = [pid for pid in ids if pid not in OWN_LIST and access.allows(pid)]
    names = choosable_models(user_id, {pid for ids in wanted.values() for pid in ids})
    out: dict[str, list[dict]] = {}
    for pane_id, ids in wanted.items():
        for pid in ids:
            if not names.get(pid):
                continue
            spec = registry.get(pid)
            rows = model_prefs.rows(pid, user_id, names[pid])
            out.setdefault(pane_id, []).append({
                "provider": pid, "label": spec.label, "rows": rows, "on": sum(1 for r in rows if r["on"]),
                "groups": group_switches(pid, user_id) if spec.model_groups else [],
                "priced": pid == "refuseless" and _refuseless_prices(rows),
            })
    return out


def admin_overview() -> list[dict]:
    """Every provider with a source row, for the admin providers table: what
    kinds of credentials it takes, whether it is on, and where its settings are."""
    rows: dict[str, list] = {}
    for row in db.fetchall("SELECT id, type, enabled FROM sources ORDER BY id"):
        rows.setdefault(row["type"], []).append(row)
    out = []
    for spec in sorted(registry.specs(), key=lambda s: [g for g, _l in GROUPS].index(s.group)):
        sources = rows.get(spec.id) or []
        if not sources:
            continue
        kinds = [label for on, label in (
            (spec.pool, "Pool"), (spec.site, "Site keys"), (spec.personal, "Personal keys"),
        ) if on]
        out.append({"id": spec.id, "label": spec.label, "kinds": kinds, "group": dict(GROUPS)[spec.group],
                    "source_id": sources[0]["id"] if len(sources) == 1 else None,
                    "enabled": any(r["enabled"] for r in sources), "count": len(sources),
                    "href": spec.admin_anchor or f"/admin/sources#{spec.id}"})
    return out
