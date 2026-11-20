"""Which models each user shows and may call, per provider.

One row per user and provider in user_model_prefs, JSON:
  {"groups": {"<group>": bool}, "models": {"<public model id>": bool}}
A model's state is its own override if it has one, else its group's default
(the spec's model_groups; providers without groups have one, "all", on).
Turning a group on or off sets that group's default and clears the overrides
of its models, so a bulk switch means "every model in the group"; single
models stay editable after that. Listing and routing both read allowed().

A separate table rather than users.pool_prefs: pool_policy reads every
user's pool_prefs to work out share levels, and per-model lists for many
providers would weigh down that scan; and OpenRouter's choices no longer
vanish with the key they used to live on.
"""
from __future__ import annotations

import json
import logging

from .. import db
from . import registry

logger = logging.getLogger(__name__)

MAX_OVERRIDES = 2000
ALL = (("all", "All models", True),)


def groups(provider: str) -> tuple:
    spec = registry.get(provider)
    return (spec.model_groups if spec and spec.model_groups else ALL)


def group_of(provider: str, model: str) -> str:
    spec = registry.get(provider)
    ids = [g[0] for g in groups(provider)]
    group = spec.model_group(model) if spec and spec.model_group else ids[0]
    return group if group in ids else ids[-1]


def _clean(raw) -> dict:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        data = None
    data = data if isinstance(data, dict) else {}
    group_raw = data.get("groups") if isinstance(data.get("groups"), dict) else {}
    model_raw = data.get("models") if isinstance(data.get("models"), dict) else {}
    return {
        "groups": {str(k): v for k, v in group_raw.items() if isinstance(v, bool)},
        "models": {str(k): v for k, v in model_raw.items() if isinstance(v, bool)},
    }


def load(user_id: int | None, provider: str) -> dict:
    if not user_id:
        return {"groups": {}, "models": {}}
    row = db.fetchone_memo("SELECT prefs FROM user_model_prefs WHERE user_id=? AND provider=?", (int(user_id), provider))
    return _clean(row["prefs"] if row else None)


def group_on(provider: str, group: str, prefs: dict) -> bool:
    default = next((g[2] for g in groups(provider) if g[0] == group), True)
    return prefs["groups"].get(group, default)


def state(provider: str, model: str, prefs: dict) -> bool:
    if model in prefs["models"]:
        return prefs["models"][model]
    return group_on(provider, group_of(provider, model), prefs)


def allowed(provider: str, user_id: int | None, model: str) -> bool:
    """False when this user turned the model off. No user: no preferences."""
    if not user_id:
        return True
    return state(provider, model, load(user_id, provider))


def visible(provider: str, user_id: int | None, names: list[str]) -> list[str]:
    if not user_id or not names:
        return names
    prefs = load(user_id, provider)
    if not prefs["groups"] and not prefs["models"] and all(g[2] for g in groups(provider)):
        return names
    return [name for name in names if state(provider, name, prefs)]


def _save(user_id: int, provider: str, change) -> dict:
    uid = int(user_id)
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO user_model_prefs (user_id, provider, prefs, updated_at) VALUES (?,?,?,?)"
            " ON CONFLICT(user_id, provider) DO UPDATE SET updated_at=excluded.updated_at",
            (uid, provider, "{}", db.now()),
        )
        row = conn.execute("SELECT prefs FROM user_model_prefs WHERE user_id=? AND provider=? FOR UPDATE",
                           (uid, provider)).fetchone()
        prefs = _clean(row["prefs"] if row else None)
        change(prefs)
        if len(prefs["models"]) > MAX_OVERRIDES:
            raise ValueError(f"at most {MAX_OVERRIDES} models can be set one by one")
        conn.execute("UPDATE user_model_prefs SET prefs=? WHERE user_id=? AND provider=?",
                     (json.dumps(prefs, separators=(",", ":")), uid, provider))
    return prefs


def set_model(user_id: int, provider: str, model: str, on: bool) -> bool:
    """Turn one model on or off; returns its state."""
    def change(prefs):
        if on == group_on(provider, group_of(provider, model), prefs):
            prefs["models"].pop(model, None)  # matches its group: no override needed
        else:
            prefs["models"][model] = bool(on)

    return state(provider, model, _save(user_id, provider, change))


def set_group(user_id: int, provider: str, group: str, on: bool) -> dict:
    """Every model of one group on or off: sets the default, clears its overrides."""
    if group not in [g[0] for g in groups(provider)]:
        raise ValueError("unknown model group")

    def change(prefs):
        prefs["groups"][group] = bool(on)
        prefs["models"] = {m: v for m, v in prefs["models"].items() if group_of(provider, m) != group}

    return _save(user_id, provider, change)


def set_all(user_id: int, provider: str, on: bool) -> dict:
    def change(prefs):
        prefs["groups"] = {g[0]: bool(on) for g in groups(provider)}
        prefs["models"] = {}

    return _save(user_id, provider, change)


def replace(user_id: int, provider: str, *, groups_on: dict | None = None, models: dict | None = None) -> dict:
    """Set group defaults and overrides at once (legacy setters, migration)."""
    def change(prefs):
        if groups_on is not None:
            prefs["groups"].update({k: bool(v) for k, v in groups_on.items()})
        if models is not None:
            prefs["models"] = {str(k): bool(v) for k, v in models.items()}

    return _save(user_id, provider, change)


def rows(provider: str, user_id: int, names: list[str]) -> list[dict]:
    """One row per model for the Sources list: its group and current state."""
    prefs = load(user_id, provider)
    return [{"id": name, "group": group_of(provider, name), "on": state(provider, name, prefs)} for name in names]


# --- One-time move of the older per-provider switches ------------------------

def _has_row(user_id: int, provider: str) -> bool:
    return db.fetchone("SELECT 1 FROM user_model_prefs WHERE user_id=? AND provider=?", (user_id, provider)) is not None


def migrate_legacy() -> int:
    """Copy OpenRouter's and NanoGPT's earlier model switches here, once per
    user (a user who already has a row keeps it). The old fields stay where
    they were, so rolling back loses nothing. Experiential Labs and
    Refuseless had no per-model choices to move."""
    from .. import accounts
    from ..sources import nanogpt, openrouter

    moved = 0
    for row in db.fetchall(
        "SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id"
        " WHERE s.type='openrouter' AND c.donated_by IS NOT NULL AND c.disabled=0 ORDER BY c.id"
    ):
        try:
            extra = accounts.cred_extra(row)
        except Exception:
            continue
        uid = int(row["donated_by"])
        paid = extra.get("paid_models") if isinstance(extra.get("paid_models"), dict) else None
        groups_on = {}
        if extra.get("free_models") is False:
            groups_on["free"] = False
        models = {}
        if paid is not None and not paid.get("all"):
            groups_on["paid"] = False
            models = {openrouter.public_id(m): True for m in paid.get("enabled") or [] if isinstance(m, str) and m}
        if groups_on and not _has_row(uid, "openrouter"):
            replace(uid, "openrouter", groups_on=groups_on, models=dict(list(models.items())[:MAX_OVERRIDES]))
            moved += 1
    for row in db.fetchall("SELECT id, pool_prefs FROM users WHERE pool_prefs LIKE ?", ('%"nanogpt"%',)):
        try:
            item = json.loads(row["pool_prefs"] or "{}").get("nanogpt")
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue
        raw = item.get("models") if isinstance(item, dict) and isinstance(item.get("models"), dict) else {}
        groups_on = {}
        if raw.get("subscription") is False:
            groups_on["subscription"] = False
        if raw.get("paid_all") is True:
            groups_on["paid"] = True
        paid = [m for m in raw.get("paid") or [] if isinstance(m, str) and m]
        models = {} if raw.get("paid_all") is True else {nanogpt.public_id(m): True for m in paid}
        if (groups_on or models) and not _has_row(int(row["id"]), "nanogpt"):
            replace(int(row["id"]), "nanogpt", groups_on=groups_on, models=dict(list(models.items())[:MAX_OVERRIDES]))
            moved += 1
    if moved:
        logger.info("Moved model choices of %s user/provider pairs to user_model_prefs", moved)
    return moved
