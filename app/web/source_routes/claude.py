"""Claude pool routes: console/OAuth sign-in, complete, cancel and revoke."""

from __future__ import annotations

import time
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from ...security import limiter
from ...sources import invalidate_model_cache
from ...sources import claude as claude_mod
from ...providers import logins
from ..deps import _pool_flash, require_user
from .sign_in import revoke

router = APIRouter()

@router.post("/sources/claude/start")
@router.post("/pool/claude/start")
async def claude_start(request: Request, console: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"claude-start:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "claude")
        return RedirectResponse("/sources", status_code=303)
    data = claude_mod.start_login(console=str(console).strip().lower() in ("1", "true", "console", "on"))
    if not data.get("auth_url") or not data.get("verifier") or not data.get("state"):
        _pool_flash[user["id"]] = ("err", "Claude sign-in could not be prepared. Try again shortly.", "claude")
        return RedirectResponse("/sources", status_code=303)
    data["_started"] = time.time()
    data["user_id"] = user["id"]
    logins.put(user["id"], "claude", data)
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/claude/complete")
@router.post("/pool/claude/complete")
async def claude_complete(request: Request, code: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    pending = logins.get(user["id"], "claude")
    if not pending:
        _pool_flash[user["id"]] = ("err", "Claude sign-in expired. Start again.", "claude")
        return RedirectResponse("/sources", status_code=303)
    cred_id, err = await claude_mod.complete_login(
        code,
        str(pending.get("verifier") or ""),
        str(pending.get("state") or ""),
        user["id"],
    )
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not link this Claude account.", "claude")
        return RedirectResponse("/sources", status_code=303)
    logins.drop(user["id"], "claude")
    invalidate_model_cache("claude")
    _pool_flash[user["id"]] = ("ok", "Claude account added to the pool.", "claude")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/claude/cancel")
@router.post("/pool/claude/cancel")
async def claude_cancel(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    logins.drop(user["id"], "claude")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/claude/{cred_id}/revoke")
@router.post("/pool/claude/{cred_id}/revoke")
async def claude_revoke(request: Request, cred_id: int):
    return await revoke(request, "claude", cred_id, auth=require_user)
