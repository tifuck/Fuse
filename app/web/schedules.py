from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import hosts, schedules
from ..cron import CronError
from ..security import limiter
from .deps import _chat_auth, _json_object, render, require_user, vault_locked_json

router = APIRouter()


def _tasks_rows(user_id: int) -> tuple[dict, list[dict], int]:
    return (hosts.list_bundle(user_id), [schedules.public(s) for s in schedules.list_schedules(user_id)],
            schedules.failure_count(user_id))


async def _tasks_page(request: Request):
    user, redir = await asyncio.to_thread(require_user, request)
    if redir:
        return redir
    from .models import _models_page_families

    bundle, items, failures = await asyncio.to_thread(_tasks_rows, user["id"])
    families, _locked = await _models_page_families(user)
    models = []
    for fam in families:
        for item in fam.get("models") or []:
            mid = item.get("id")
            if mid:
                models.append({"id": mid, "name": item.get("name") or mid, "type": fam.get("type")})
    projects = []
    for host in bundle.get("hosts") or []:
        for proj in host.get("projects") or []:
            projects.append({
                "id": proj.get("id"),
                "name": proj.get("name") or "",
                "host_id": host.get("id"),
                "host_alias": host.get("alias") or "",
                "auth_type": host.get("auth_type") or "",
            })
    schedule_bundle = {
        "schedules": items,
        "projects": projects,
        "models": models,
        "failures": failures,
        "vault": bundle.get("vault"),
    }
    return render(request, "tasks.html", user, schedule_bundle=schedule_bundle)


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request):
    return await _tasks_page(request)


def _fail(exc: schedules.ScheduleError):
    if exc.status_code == 401:
        return vault_locked_json()
    return JSONResponse(
        {"error": {"type": "schedule", "code": exc.status_code, "message": exc.detail}},
        status_code=exc.status_code,
        headers={"Cache-Control": "no-store"},
    )


def _ok(item, *, fires=None, extra=None):
    data = schedules.public(item, fires=fires)
    if extra:
        data.update(extra)
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.get("/schedules")
def schedules_list(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    items = [schedules.public(s) for s in schedules.list_schedules(user["id"])]
    return JSONResponse(
        {"schedules": items, "failures": schedules.failure_count(user["id"])},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/schedules")
async def schedules_create(request: Request):
    user, err = await asyncio.to_thread(_chat_auth, request)
    if err:
        return err
    body = await _json_object(request)
    try:
        item = await asyncio.to_thread(schedules.create_schedule, user["id"], body)
    except (schedules.ScheduleError, CronError) as exc:
        if isinstance(exc, CronError):
            return _fail(schedules.ScheduleError(str(exc)))
        return _fail(exc)
    return _ok(item)


@router.get("/schedules/{token}")
def schedules_get(request: Request, token: str):
    user, err = _chat_auth(request)
    if err:
        return err
    item = schedules.get_schedule(user["id"], token)
    if item is None:
        raise HTTPException(404, "schedule not found")
    fires = schedules.list_fires(user["id"], token)
    extra = {"preview": schedules.preview_fires(item)}
    return _ok(item, fires=fires, extra=extra)


@router.patch("/schedules/{token}")
async def schedules_patch(request: Request, token: str):
    user, err = await asyncio.to_thread(_chat_auth, request)
    if err:
        return err
    raw = await _json_object(request)
    try:
        item = await asyncio.to_thread(schedules.patch_schedule, user["id"], token, raw)
    except (schedules.ScheduleError, CronError) as exc:
        if isinstance(exc, CronError):
            return _fail(schedules.ScheduleError(str(exc)))
        return _fail(exc)
    return _ok(item)


@router.delete("/schedules/{token}")
def schedules_delete(request: Request, token: str):
    user, err = _chat_auth(request)
    if err:
        return err
    try:
        schedules.delete_schedule(user["id"], token)
    except schedules.ScheduleError as exc:
        return _fail(exc)
    return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})


@router.post("/schedules/{token}/run")
async def schedules_run(request: Request, token: str):
    user, err = await asyncio.to_thread(_chat_auth, request)
    if err:
        return err
    try:
        item = await schedules.run_now(user["id"], token)
    except (schedules.ScheduleError, CronError) as exc:
        if isinstance(exc, CronError):
            return _fail(schedules.ScheduleError(str(exc)))
        return _fail(exc)
    fires = await asyncio.to_thread(schedules.list_fires, user["id"], item["public_id"])
    return _ok(item, fires=fires)


@router.get("/schedules/{token}/fires")
def schedules_fires(request: Request, token: str):
    user, err = _chat_auth(request)
    if err:
        return err
    try:
        fires = schedules.list_fires(user["id"], token)
    except schedules.ScheduleError as exc:
        return _fail(exc)
    return JSONResponse({"fires": fires}, headers={"Cache-Control": "no-store"})


@router.post("/schedules/preview")
async def schedules_preview(request: Request):
    user, err = await asyncio.to_thread(_chat_auth, request)
    if err:
        return err
    # _normalize projects cron fires; without a limit that is repeatable CPU
    # burn from one client.
    if not limiter.allow(f"schedules:{user['id']}", 30, 60):
        return _fail(schedules.ScheduleError("too many requests", 429))
    raw = await _json_object(request)
    try:
        item, times = await asyncio.to_thread(_preview, user["id"], raw)
    except (schedules.ScheduleError, CronError) as exc:
        if isinstance(exc, CronError):
            return _fail(schedules.ScheduleError(str(exc)))
        return _fail(exc)
    return JSONResponse({"preview": times, "when": schedules._when_label(item)}, headers={"Cache-Control": "no-store"})


def _preview(user_id: int, raw: dict):
    item = schedules._normalize(user_id, raw)
    return item, schedules.preview_fires(item)
