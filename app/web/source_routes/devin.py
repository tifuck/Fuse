"""Devin pool routes: PKCE sign-in, callback, complete, cancel, add and revoke."""

from __future__ import annotations

import hmac
import time
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from ...security import limiter
from ...config import settings
from ...sources import invalidate_model_cache
from ...sources import devin as devin_mod
from ...providers import logins
from ..deps import _pool_flash, logger, require_user
from .sign_in import revoke

router = APIRouter()

def _devin_redirect_uri(request: Request) -> str:
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
    return base + "/sources/devin/callback"


@router.post("/sources/devin/start")
@router.post("/pool/devin/start")
async def devin_start(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"devin-start:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "devin")
        return RedirectResponse("/sources", status_code=303)
    try:
        data = devin_mod.start_cli_login()
    except Exception:
        logger.warning("Could not start Devin PKCE login", exc_info=True)
        _pool_flash[user["id"]] = ("err", "Could not start Devin sign-in. Try again shortly.", "devin")
        return RedirectResponse("/sources", status_code=303)
    if not isinstance(data.get("auth_url"), str) or not data.get("verifier") or not data.get("state"):
        _pool_flash[user["id"]] = ("err", "Devin sign-in could not be prepared. Try again shortly.", "devin")
        return RedirectResponse("/sources", status_code=303)
    data["_started"] = time.time()
    data["user_id"] = user["id"]
    logins.put(user["id"], "devin", data)
    return RedirectResponse("/sources", status_code=303)


@router.get("/sources/devin/callback")
@router.get("/pool/devin/callback")
async def devin_callback(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    pending = logins.get(user["id"], "devin")
    code = str(request.query_params.get("code") or "").strip()
    state = str(request.query_params.get("state") or "").strip()
    # State first: a cross-site link to ?error=… must not cancel a sign-in.
    if not pending or not state or not hmac.compare_digest(state.encode(), str(pending.get("state") or "").encode()):
        _pool_flash[user["id"]] = ("err", "Devin sign-in expired or did not match this session. Start again.", "devin")
        return RedirectResponse("/sources", status_code=303)
    error = str(request.query_params.get("error") or "").strip()
    if error:
        logins.drop(user["id"], "devin")
        denied = error.lower() in ("access_denied", "denied", "cancelled", "canceled")
        msg = "Devin sign-in was cancelled." if denied else "Devin sign-in was denied or expired. Start again."
        _pool_flash[user["id"]] = ("err", msg, "devin")
        return RedirectResponse("/sources", status_code=303)
    if not code:
        logins.drop(user["id"], "devin")
        _pool_flash[user["id"]] = ("err", "Devin did not return an authorization code. Start again.", "devin")
        return RedirectResponse("/sources", status_code=303)
    cred_id, err = await devin_mod.complete_cli_login(
        code,
        str(pending.get("verifier") or ""),
        user["id"],
        redirect_uri=str(pending.get("redirect_uri") or _devin_redirect_uri(request)),
    )
    logins.drop(user["id"], "devin")
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not link this Devin account.", "devin")
        return RedirectResponse("/sources", status_code=303)
    invalidate_model_cache("devin")
    _pool_flash[user["id"]] = ("ok", "Devin account added to the pool.", "devin")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/devin/complete")
@router.post("/pool/devin/complete")
async def devin_complete(request: Request, code: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    pending = logins.get(user["id"], "devin")
    if not pending:
        _pool_flash[user["id"]] = ("err", "Devin sign-in expired. Start again.", "devin")
        return RedirectResponse("/sources", status_code=303)
    cred_id, err = await devin_mod.complete_cli_login(
        code,
        str(pending.get("verifier") or ""),
        user["id"],
    )
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not link this Devin account.", "devin")
        return RedirectResponse("/sources", status_code=303)
    logins.drop(user["id"], "devin")
    invalidate_model_cache("devin")
    _pool_flash[user["id"]] = ("ok", "Devin account added to the pool.", "devin")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/devin/cancel")
@router.post("/pool/devin/cancel")
async def devin_cancel(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    logins.drop(user["id"], "devin")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/devin/add")
@router.post("/pool/devin/add")
async def devin_add(request: Request, api_key: str = Form(""), label: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"devin-add:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "devin")
        return RedirectResponse("/sources", status_code=303)
    sid = devin_mod.ensure_devin_source()
    cred_id, err = await devin_mod.add_session_token(sid, api_key, user["id"], label=label)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not add that token.", "devin")
        return RedirectResponse("/sources", status_code=303)
    invalidate_model_cache("devin")
    _pool_flash[user["id"]] = ("ok", "Devin account added to the pool.", "devin")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/devin/{cred_id}/revoke")
@router.post("/pool/devin/{cred_id}/revoke")
async def devin_revoke(request: Request, cred_id: int):
    return await revoke(request, "devin", cred_id, auth=require_user)
