"""API-key providers (app/providers/presets.py): one set of routes for all of
them. Users add and remove their own keys on Sources; admins switch each
provider on or off, choose who may use its site keys, and manage those."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ... import db
from ...providers import auth_flows
from ...security import limiter
from ...sources import invalidate_model_cache, refresh_catalog_now
from ...sources import keyed as keyed_mod
from ..deps import _notices, _pool_flash, require_admin, require_user

router = APIRouter()


def _preset(pid: str):
    p = keyed_mod.preset(pid)
    if p is None:
        raise HTTPException(404, "Not found")
    return p


def _back(user_id: int, kind: str, message: str, pid: str):
    _pool_flash[user_id] = (kind, message, pid)
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/keys/{pid}/add")
async def key_add(request: Request, pid: str, api_key: str = Form(""), label: str = Form(""),
                  method: str = Form(""), region: str = Form("")):
    user, redir = await asyncio.to_thread(require_user, request)
    if redir:
        return redir
    p = _preset(pid)
    if not limiter.allow(f"keyed-add:{user['id']}", 5, 60):
        return _back(user["id"], "err", "Too many attempts. Try again in a minute.", pid)
    row = db.fetchone("SELECT enabled FROM sources WHERE id=?", (keyed_mod.ensure_source(pid),))
    if not row or not row["enabled"]:
        return _back(user["id"], "err", f"{p.label} is turned off on this server.", pid)
    cred_id, err = await auth_flows.api_key_paste(pid, api_key, user_id=user["id"], label=label, method=method, region=region)
    if err or not cred_id:
        return _back(user["id"], "err", err or "Could not add that key.", pid)
    invalidate_model_cache(pid)
    refresh_catalog_now(pid)
    return _back(user["id"], "ok", f"{p.label} key saved for your account.", pid)


@router.post("/sources/keys/{pid}/{cred_id}/revoke")
async def key_revoke(request: Request, pid: str, cred_id: int):
    user, redir = await asyncio.to_thread(require_user, request)
    if redir:
        return redir
    p = _preset(pid)
    if not keyed_mod.remove_key(pid, cred_id, user_id=user["id"]):
        raise HTTPException(404, "Not found")
    invalidate_model_cache(pid)
    return _back(user["id"], "ok", f"{p.label} key removed.", pid)


@router.post("/admin/providers/{pid}")
async def admin_provider_save(request: Request, pid: str, enabled: str = Form("0"), site_access: str = Form("none")):
    user, redir = await asyncio.to_thread(require_admin, request)
    if redir:
        return redir
    p = _preset(pid)
    sid = keyed_mod.ensure_source(pid)
    with db.tx() as conn:
        row = conn.execute("SELECT config FROM sources WHERE id=? FOR UPDATE", (sid,)).fetchone()
        cfg = keyed_mod._cfg(row["config"] if row else None)
        cfg["site_access"] = site_access if site_access in keyed_mod.SITE_ACCESS else "none"
        on = 1 if enabled in ("1", "on", "true") else 0
        conn.execute("UPDATE sources SET enabled=?, config=? WHERE id=?", (on, json.dumps(cfg), sid))
    invalidate_model_cache(pid)
    _notices[user["id"]] = f"{p.label} settings saved."
    return RedirectResponse("/admin/sources#keyed", status_code=303)


@router.post("/admin/providers/{pid}/keys")
async def admin_site_key_add(request: Request, pid: str, api_key: str = Form(""), label: str = Form(""),
                             region: str = Form("")):
    user, redir = await asyncio.to_thread(require_admin, request)
    if redir:
        return redir
    p = _preset(pid)
    if not limiter.allow(f"keyed-add:{user['id']}", 5, 60):
        _notices[user["id"]] = "Too many attempts. Try again in a minute."
    else:
        cred_id, err = await keyed_mod.store_key(pid, api_key, user_id=None, label=label, region=region)
        _notices[user["id"]] = err if err or not cred_id else f"{p.label} site key saved."
        invalidate_model_cache(pid)
        if cred_id:
            refresh_catalog_now(pid)
    return RedirectResponse("/admin/sources#keyed", status_code=303)


@router.post("/admin/providers/{pid}/keys/{cred_id}/delete")
async def admin_site_key_delete(request: Request, pid: str, cred_id: int):
    user, redir = await asyncio.to_thread(require_admin, request)
    if redir:
        return redir
    p = _preset(pid)
    if not keyed_mod.remove_key(pid, cred_id, user_id=None):
        raise HTTPException(404, "Not found")
    invalidate_model_cache(pid)
    _notices[user["id"]] = f"{p.label} site key removed."
    return RedirectResponse("/admin/sources#keyed", status_code=303)
