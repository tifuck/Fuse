"""Grok (x.ai) pool routes: device sign-in, poll, cancel and revoke."""

from __future__ import annotations

import time
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from ... import db
from ...security import limiter
from ...sources import invalidate_model_cache
from ...sources import grok as grok_mod
from ...sources.grok import ensure_grok_source, refresh_billing
from ...providers import auth_flows, logins
from ..deps import _pool_flash, logger, require_user
from .sign_in import revoke
from .context import _oauth_tokens

router = APIRouter()

@router.post("/sources/grok/start")
@router.post("/pool/grok/start")
async def grok_start(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"grok-start:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "x.ai sign-in was rate limited. Wait a minute and try again.", "grok")
        return RedirectResponse("/sources", status_code=303)
    try:
        data = await grok_mod.start_device()
    except Exception:
        logger.warning("Could not start Grok device authorization", exc_info=True)
        _pool_flash[user["id"]] = ("err", "Could not start x.ai sign-in. Check the connection and try again.", "grok")
        return RedirectResponse("/sources", status_code=303)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("device_code"), str)
        or not data["device_code"]
        or not isinstance(data.get("user_code"), str)
        or not data["user_code"]
    ):
        _pool_flash[user["id"]] = ("err", "x.ai returned an invalid sign-in response. Try again shortly.", "grok")
        return RedirectResponse("/sources", status_code=303)
    data["_started"] = time.time()
    logins.put(user["id"], "grok", data)
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/grok/poll")
@router.post("/pool/grok/poll")
async def grok_poll(request: Request):
    user, redir = require_user(request)
    if redir:
        return JSONResponse({"error": "auth"}, status_code=401)
    pending = logins.get(user["id"], "grok", clamp=True)
    if not pending:
        return JSONResponse({"error": "No Grok sign-in in progress."}, status_code=400)
    try:
        data = await grok_mod.poll_device(pending["device_code"])
    except Exception:
        logger.warning("Grok device poll failed transiently", exc_info=True)
        return JSONResponse(
            {"pending": True, "error": "x.ai could not be reached; retrying.", "retry_after": pending.get("interval", 5)},
            status_code=502,
        )
    if not isinstance(data, dict):
        return JSONResponse(
            {"pending": True, "error": "x.ai returned an invalid response; retrying.", "retry_after": pending.get("interval", 5)},
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
        sid = ensure_grok_source()
        profile = await grok_mod.fetch_userinfo(tokens["access_token"])
        # Cancelled or restarted while x.ai answered: this flow no longer
        # stands for the user's intent, so it must not donate an account.
        if not logins.claim(user["id"], "grok", pending):
            return JSONResponse({"error": "That x.ai sign-in was cancelled or replaced."}, status_code=409)
        cred_id = grok_mod.store_tokens(
            sid, tokens, donated_by=user["id"], label=profile.get("email") or "Grok account", profile=profile
        )
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
        if row:
            try:
                await refresh_billing(row, force=True)
            except Exception:
                logger.warning("Grok billing refresh failed after OAuth", exc_info=True)
        invalidate_model_cache("grok")
        return JSONResponse({"ok": True})
    status, body = auth_flows.device_step(user["id"], "grok", "x.ai", pending, data)
    return JSONResponse(body, status_code=status)


@router.post("/sources/grok/cancel")
@router.post("/pool/grok/cancel")
async def grok_cancel(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    logins.drop(user["id"], "grok")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/grok/{cred_id}/revoke")
@router.post("/pool/grok/{cred_id}/revoke")
async def grok_revoke(request: Request, cred_id: int):
    return await revoke(request, "grok", cred_id)
