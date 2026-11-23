from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import chats, hosts, memory, permissions, retention, secret_guard
from ..search import egress
from ..security import limiter
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
        secrets=secret_guard.overview(user["id"]),
        web=egress.overview(user["id"]),
    )


@router.post("/settings/web")
async def web_set(request: Request):
    """The connectors this user's web searches and page reads leave through."""
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    ids = raw.get("machines")
    if set(raw) != {"machines"} or not isinstance(ids, list) or len(ids) > 500 or any(type(i) is not int for i in ids):
        raise HTTPException(422, "machines must be a list of machine ids")
    egress.save(user["id"], ids)
    return JSONResponse(egress.overview(user["id"]), headers={"Cache-Control": "no-store"})


@router.post("/settings/web/detect")
async def web_detect(request: Request):
    """Read each online connector's public IP through the connector itself."""
    user, err = _chat_auth(request)
    if err:
        return err
    if not limiter.allow(f"web-detect:{user['id']}", 6, 60):
        raise HTTPException(429, "Too many checks. Try again in a minute.")
    await egress.detect(user["id"])
    return JSONResponse(egress.overview(user["id"]), headers={"Cache-Control": "no-store"})


def _secrets_json(user_id: int) -> JSONResponse:
    return JSONResponse(secret_guard.overview(user_id), headers={"Cache-Control": "no-store"})


@router.get("/settings/secrets")
async def secrets_get(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    return _secrets_json(user["id"])


@router.post("/settings/secrets")
async def secrets_set(request: Request):
    """Switches, a typed value to protect (with env: as an environment
    secret under that name), or a change to one kept value."""
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    if set(raw) - {"protect", "offer", "label", "value", "env", "id", "protected"}:
        raise HTTPException(422, "Unsupported fields")
    for key in ("protect", "offer", "protected", "env"):
        if key in raw and not isinstance(raw[key], bool):
            raise HTTPException(422, f"{key} must be true or false")
    if "value" in raw:
        if not isinstance(raw["value"], str) or not isinstance(raw.get("label", ""), str):
            raise HTTPException(422, "Invalid value")
        try:
            secret_guard.add_manual(user["id"], raw.get("label", ""), raw["value"], env=raw.get("env", False))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    elif "id" in raw:
        if not isinstance(raw["id"], int) or isinstance(raw["id"], bool) or "protected" not in raw:
            raise HTTPException(422, "Invalid change")
        if not secret_guard.set_status(user["id"], raw["id"], raw["protected"]):
            raise HTTPException(404, "Not found")
    else:
        secret_guard.set_switches(user["id"], protect=raw.get("protect"), offer_on=raw.get("offer"))
    return _secrets_json(user["id"])


@router.post("/settings/secrets/{secret_id}/reveal")
async def secrets_reveal(request: Request, secret_id: int):
    """A kept value, shown to its owner when they ask for it. A POST, so it
    needs the session's CSRF token and is never cached, linked or prefetched;
    the page and the list never carry values."""
    user, err = _chat_auth(request)
    if err:
        return err
    if not limiter.allow(f"secret-reveal:{user['id']}", 30, 60):
        raise HTTPException(429, "Too many requests")
    shown = secret_guard.reveal(user["id"], secret_id)
    if shown is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(shown, headers={"Cache-Control": "no-store"})


@router.delete("/settings/secrets")
async def secrets_reset(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    secret_guard.delete(user["id"])
    permissions.prune_env_grants(user["id"], ())
    return _secrets_json(user["id"])


@router.delete("/settings/secrets/{secret_id}")
async def secrets_delete(request: Request, secret_id: int):
    user, err = _chat_auth(request)
    if err:
        return err
    if not secret_guard.delete(user["id"], secret_id):
        raise HTTPException(404, "Not found")
    permissions.prune_env_grants(user["id"], secret_guard.env_names(user["id"]))
    return _secrets_json(user["id"])


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
