from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import chats, hosts, memory, retention
from .deps import _chat_auth, _json_object, render, require_user

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Personal settings that used to be scattered: chat defaults lived only
    in the chat Context sheet, coding-agent switches under Machines ›
    Advanced, and password change had an endpoint but no page."""
    user, redir = require_user(request)
    if redir:
        return redir
    return render(
        request,
        "settings.html",
        user,
        context=chats.get_user_context(user["id"]),
        agent=hosts.settings_for(user["id"]),
        cleanup=retention.get_settings(user["id"]),
        memories=memory.list_all(user["id"]),
        memory_types=memory.TYPES,
    )


@router.get("/settings/cleanup")
async def cleanup_get(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    return JSONResponse(retention.get_settings(user["id"]), headers={"Cache-Control": "no-store"})


@router.put("/settings/memories/{memory_id}")
async def memory_edit(request: Request, memory_id: int):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    if set(raw) - {"type", "description", "body"}:
        raise HTTPException(422, "Unsupported memory fields")
    try:
        found = memory.edit(user["id"], memory_id, type=raw.get("type"), description=raw.get("description"),
                            body=raw.get("body"))
    except memory.MemoryRefused as exc:
        raise HTTPException(422, str(exc)) from None
    if not found:
        raise HTTPException(404, "Memory not found")
    return JSONResponse({"ok": True})


@router.delete("/settings/memories/{memory_id}")
async def memory_delete(request: Request, memory_id: int):
    user, err = _chat_auth(request)
    if err:
        return err
    if not memory.delete(user["id"], memory_id):
        raise HTTPException(404, "Memory not found")
    return JSONResponse({"ok": True})


@router.post("/settings/cleanup")
async def cleanup_set(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    try:
        data = retention.set_settings(user["id"], raw)
    except retention.RetentionError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(data)
