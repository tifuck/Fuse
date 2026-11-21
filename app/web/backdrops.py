from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import backdrops
from .deps import _chat_auth, _json_object

router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store"}


@router.get("/api/backdrops")
async def backdrops_list(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    items = await asyncio.to_thread(backdrops.list_for_user, user["id"])
    return JSONResponse({"backdrops": items}, headers=_NO_STORE)


@router.post("/api/backdrops")
async def backdrops_create(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    try:
        item = await asyncio.to_thread(
            backdrops.create, user["id"], name=raw.get("name"), scheme=raw.get("scheme"), html=raw.get("html"),
            replace=raw.get("replace"))
    except backdrops.BackdropError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    return JSONResponse({"backdrop": item}, headers=_NO_STORE)


@router.delete("/api/backdrops/{public_id}")
async def backdrops_delete(request: Request, public_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    if not await asyncio.to_thread(backdrops.delete, user["id"], public_id):
        raise HTTPException(404, "Backdrop not found")
    return JSONResponse({"ok": True}, headers=_NO_STORE)


@router.get("/backdrop/{public_id}")
async def backdrop_document(request: Request, public_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    html = await asyncio.to_thread(backdrops.html_for, user["id"], public_id)
    if html is None:
        raise HTTPException(404, "Backdrop not found")
    return HTMLResponse(html, headers=backdrops.HEADERS)
