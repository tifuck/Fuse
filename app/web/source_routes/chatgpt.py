"""ChatGPT (OpenAI) pool routes: device sign-in, poll, cancel and revoke."""

from __future__ import annotations

import time
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from ... import db
from ...security import limiter
from ...sources import invalidate_model_cache
from ...sources import chatgpt as chatgpt_mod
from ...providers import logins
from ..deps import _pool_flash, logger, require_user
from .sign_in import revoke
from .context import _oauth_tokens, _terminal_oauth_error

router = APIRouter()

@router.post("/sources/chatgpt/start")
@router.post("/pool/chatgpt/start")
async def chatgpt_start(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"chatgpt-start:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "OpenAI sign-in was rate limited. Wait a minute and try again.", "chatgpt")
        return RedirectResponse("/sources", status_code=303)
    try:
        data = await chatgpt_mod.start_device()
    except Exception:
        logger.warning("Could not start ChatGPT device authorization", exc_info=True)
        _pool_flash[user["id"]] = ("err", "Could not start OpenAI sign-in. Check the connection and try again.", "chatgpt")
        return RedirectResponse("/sources", status_code=303)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("user_code"), str)
        or not data["user_code"]
        or not isinstance(data.get("device_auth_id"), str)
        or not data["device_auth_id"]
    ):
        _pool_flash[user["id"]] = ("err", "OpenAI returned an invalid sign-in response. Try again shortly.", "chatgpt")
        return RedirectResponse("/sources", status_code=303)
    data["_started"] = time.time()
    logins.put(user["id"], "chatgpt", data)
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/chatgpt/poll")
@router.post("/pool/chatgpt/poll")
async def chatgpt_poll(request: Request):
    user, redir = require_user(request)
    if redir:
        return JSONResponse({"error": "auth"}, status_code=401)
    pending = logins.get(user["id"], "chatgpt", clamp=True)
    if not pending:
        return JSONResponse({"error": "No ChatGPT sign-in in progress."}, status_code=400)
    try:
        result = await chatgpt_mod.poll_device(pending["device_auth_id"], pending["user_code"])
    except Exception:
        logger.warning("ChatGPT device poll failed transiently", exc_info=True)
        return JSONResponse(
            {"pending": True, "error": "OpenAI could not be reached; retrying.", "retry_after": pending.get("interval", 5)},
            status_code=502,
        )
    if not isinstance(result, dict):
        return JSONResponse(
            {"pending": True, "error": "OpenAI returned an invalid response; retrying.", "retry_after": pending.get("interval", 5)},
            status_code=502,
        )
    if result.get("status") == "ok":
        try:
            tokens = _oauth_tokens(result.get("tokens"))
        except ValueError as exc:
            return JSONResponse(
                {"pending": True, "error": str(exc) + " Retrying.", "retry_after": pending.get("interval", 5)},
                status_code=502,
            )
        # Cancelled or restarted while OpenAI answered: do not donate it.
        if not logins.claim(user["id"], "chatgpt", pending):
            return JSONResponse({"error": "That ChatGPT sign-in was cancelled or replaced."}, status_code=409)
        sid = chatgpt_mod.ensure_chatgpt_source()
        cred_id = chatgpt_mod.store_tokens(sid, tokens, donated_by=user["id"], label="ChatGPT account")
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
        if row:
            try:
                await chatgpt_mod.refresh_usage(row, force=True)
            except Exception:
                logger.warning("ChatGPT usage refresh failed after OAuth", exc_info=True)
        invalidate_model_cache("chatgpt")
        return JSONResponse({"ok": True})
    if result.get("status") == "pending":
        return JSONResponse({"pending": True})
    error_text = str(result.get("error") or "")
    if _terminal_oauth_error(error_text):
        logins.drop(user["id"], "chatgpt")
        return JSONResponse({"error": "OpenAI sign-in was denied or expired. Start again."}, status_code=400)
    return JSONResponse(
        {"pending": True, "error": error_text or "OpenAI returned an unexpected response; retrying.", "retry_after": pending.get("interval", 5)},
        status_code=502,
    )


@router.post("/sources/chatgpt/cancel")
@router.post("/pool/chatgpt/cancel")
async def chatgpt_cancel(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    logins.drop(user["id"], "chatgpt")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/chatgpt/{cred_id}/revoke")
@router.post("/pool/chatgpt/{cred_id}/revoke")
async def chatgpt_revoke(request: Request, cred_id: int):
    return await revoke(request, "chatgpt", cred_id, auth=require_user)
