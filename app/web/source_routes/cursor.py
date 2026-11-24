"""Cursor pool routes: browser sign-in (start, poll, cancel), API key add, revoke."""

from __future__ import annotations

import asyncio
import time
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from ... import db
from ...security import limiter
from ...sources import invalidate_model_cache
from ...sources import cursor as cursor_mod
from ...providers import logins
from ..deps import _pool_flash, logger, require_user
from .sign_in import revoke

router = APIRouter()


@router.post("/sources/cursor/start")
@router.post("/pool/cursor/start")
async def cursor_start(request: Request):
    user, redir = await asyncio.to_thread(require_user, request)
    if redir:
        return redir
    if not limiter.allow(f"cursor-start:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "cursor")
        return RedirectResponse("/sources#pool-cursor", status_code=303)
    data = cursor_mod.start_login()
    data["_started"] = time.time()
    logins.put(user["id"], "cursor", data)
    return RedirectResponse("/sources#pool-cursor", status_code=303)


@router.post("/sources/cursor/poll")
@router.post("/pool/cursor/poll")
async def cursor_poll(request: Request):
    user, redir = await asyncio.to_thread(require_user, request)
    if redir:
        return JSONResponse({"error": "auth"}, status_code=401)
    pending = logins.get(user["id"], "cursor", clamp=True)
    if not pending:
        return JSONResponse({"error": "No Cursor sign-in in progress."}, status_code=400)
    interval = pending.get("interval", 3)
    try:
        data = await cursor_mod.poll_login(str(pending["uuid"]), str(pending["verifier"]))
    except Exception:
        logger.warning("Cursor sign-in poll failed transiently", exc_info=True)
        return JSONResponse({"pending": True, "error": "Cursor could not be reached; retrying.", "retry_after": interval}, status_code=502)
    if data.get("error"):
        logins.drop(user["id"], "cursor")
        return JSONResponse({"error": "Cursor sign-in was denied or expired. Start again."}, status_code=400)
    if data.get("pending"):
        return JSONResponse({"pending": True, "retry_after": interval})
    # Cancelled or restarted while Cursor answered: do not donate it.
    if not logins.claim(user["id"], "cursor", pending):
        return JSONResponse({"error": "That Cursor sign-in was cancelled or replaced."}, status_code=409)
    sid = cursor_mod.ensure_cursor_source()
    try:
        cred_id = cursor_mod.store_tokens(sid, data["access_token"], data["refresh_token"], user["id"])
    except ValueError:
        return JSONResponse({"error": "Cursor returned an invalid token. Start again."}, status_code=502)
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    if row:
        try:
            await cursor_mod.refresh_quota(row, force=True)
        except Exception:
            logger.warning("Cursor usage refresh failed after sign-in", exc_info=True)
    invalidate_model_cache("cursor")
    _pool_flash[user["id"]] = ("ok", "Cursor account added to the pool.", "cursor")
    return JSONResponse({"ok": True})


@router.post("/sources/cursor/cancel")
@router.post("/pool/cursor/cancel")
async def cursor_cancel(request: Request):
    user, redir = await asyncio.to_thread(require_user, request)
    if redir:
        return redir
    logins.drop(user["id"], "cursor")
    return RedirectResponse("/sources#pool-cursor", status_code=303)


@router.post("/sources/cursor/add")
@router.post("/pool/cursor/add")
async def cursor_add(request: Request, api_key: str = Form(""), label: str = Form("")):
    user, redir = await asyncio.to_thread(require_user, request)
    if redir:
        return redir
    if not limiter.allow(f"cursor-add:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "cursor")
        return RedirectResponse("/sources#pool-cursor", status_code=303)
    sid = cursor_mod.ensure_cursor_source()
    cred_id, err = await cursor_mod.add_api_key(sid, api_key, user["id"], label=label)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not add that key.", "cursor")
        return RedirectResponse("/sources#pool-cursor", status_code=303)
    logins.drop(user["id"], "cursor")
    invalidate_model_cache("cursor")
    _pool_flash[user["id"]] = ("ok", "Cursor account added to the pool.", "cursor")
    return RedirectResponse("/sources#pool-cursor", status_code=303)


@router.post("/sources/cursor/{cred_id}/revoke")
@router.post("/pool/cursor/{cred_id}/revoke")
async def cursor_revoke(request: Request, cred_id: int):
    return await revoke(request, "cursor", cred_id, auth=require_user)
