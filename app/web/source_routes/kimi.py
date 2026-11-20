"""Kimi pool routes: device sign-in, poll, cancel, add and revoke."""

from __future__ import annotations

import time
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from ... import db
from ...security import limiter
from ...sources import invalidate_model_cache
from ...sources import kimi as kimi_mod
from ...providers import auth_flows, logins
from ..deps import _pool_flash, logger, require_user
from .sign_in import revoke
from .context import _oauth_tokens

router = APIRouter()

@router.post("/sources/kimi/start")
@router.post("/pool/kimi/start")
async def kimi_start(request: Request, region: str = Form("global")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"kimi-start:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Kimi sign-in was rate limited. Wait a minute and try again.", "kimi")
        return RedirectResponse("/sources", status_code=303)
    chosen = "cn" if str(region or "").strip().lower() in ("cn", "mainland-cn", "com") else "global"
    try:
        data = await kimi_mod.start_device(chosen)
    except Exception:
        logger.warning("Could not start Kimi device authorization", exc_info=True)
        _pool_flash[user["id"]] = ("err", "Could not start Kimi sign-in. Check the connection and try again.", "kimi")
        return RedirectResponse("/sources", status_code=303)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("device_code"), str)
        or not data["device_code"]
        or not isinstance(data.get("user_code"), str)
        or not data["user_code"]
    ):
        _pool_flash[user["id"]] = ("err", "Kimi returned an invalid sign-in response. Try again shortly.", "kimi")
        return RedirectResponse("/sources", status_code=303)
    data["_started"] = time.time()
    logins.put(user["id"], "kimi", data)
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/kimi/poll")
@router.post("/pool/kimi/poll")
async def kimi_poll(request: Request):
    user, redir = require_user(request)
    if redir:
        return JSONResponse({"error": "auth"}, status_code=401)
    pending = logins.get(user["id"], "kimi", clamp=True)
    if not pending:
        return JSONResponse({"error": "No Kimi sign-in in progress."}, status_code=400)
    region = str(pending.get("_region") or "global")
    device_id = str(pending.get("_device_id") or "")
    try:
        data = await kimi_mod.poll_device(pending["device_code"], region=region, device_id=device_id)
    except Exception:
        logger.warning("Kimi device poll failed transiently", exc_info=True)
        return JSONResponse(
            {"pending": True, "error": "Kimi could not be reached; retrying.", "retry_after": pending.get("interval", 5)},
            status_code=502,
        )
    if not isinstance(data, dict):
        return JSONResponse(
            {"pending": True, "error": "Kimi returned an invalid response; retrying.", "retry_after": pending.get("interval", 5)},
            status_code=502,
        )
    if data.get("access_token") is not None:
        try:
            tokens = _oauth_tokens(data)
        except ValueError as exc:
            return JSONResponse(
                {"pending": True, "error": str(exc) + " Retrying.", "retry_after": pending.get("interval", 5)},
                status_code=502,
            )
        # The token carries no account id; without it store_tokens cannot
        # find this account's earlier row and a re-login adds a duplicate.
        if not tokens.get("account_id"):
            profile = await kimi_mod.fetch_userinfo(tokens["access_token"], region, {"device_id": device_id})
            if profile.get("account_id"):
                tokens["account_id"] = profile["account_id"]
        # Cancelled or restarted while Kimi answered: do not donate it.
        if not logins.claim(user["id"], "kimi", pending):
            return JSONResponse({"error": "That Kimi sign-in was cancelled or replaced."}, status_code=409)
        sid = kimi_mod.ensure_kimi_source()
        cred_id = kimi_mod.store_tokens(
            sid,
            tokens,
            donated_by=user["id"],
            label="Kimi account",
            region=region,
            device_id=device_id,
        )
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
        if row:
            try:
                await kimi_mod.refresh_quota(row, force=True)
            except Exception:
                logger.warning("Kimi quota refresh failed after OAuth", exc_info=True)
        invalidate_model_cache("kimi")
        return JSONResponse({"ok": True})
    status, body = auth_flows.device_step(user["id"], "kimi", "Kimi", pending, data)
    return JSONResponse(body, status_code=status)


@router.post("/sources/kimi/cancel")
@router.post("/pool/kimi/cancel")
async def kimi_cancel(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    logins.drop(user["id"], "kimi")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/kimi/add")
@router.post("/pool/kimi/add")
async def kimi_add(request: Request, api_key: str = Form(""), label: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"kimi-add:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "kimi")
        return RedirectResponse("/sources", status_code=303)
    sid = kimi_mod.ensure_kimi_source()
    cred_id, err = await kimi_mod.add_plan_key(sid, api_key, user["id"], label=label)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not add that key.", "kimi")
        return RedirectResponse("/sources", status_code=303)
    invalidate_model_cache("kimi")
    _pool_flash[user["id"]] = ("ok", "Kimi Code key added to the pool.", "kimi")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/kimi/{cred_id}/revoke")
@router.post("/pool/kimi/{cred_id}/revoke")
async def kimi_revoke(request: Request, cred_id: int):
    return await revoke(request, "kimi", cred_id, auth=require_user)
