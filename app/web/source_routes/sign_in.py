"""Sign-in and removal routes every provider shares, mounted once.

/sources/p/{pid}/{action} reaches each provider's own sign-in handlers
(connect, poll, finish, callback, cancel, add); the older /sources/<pid>/...
and /pool/<pid>/... URLs stay as they were. Removing an account is one
handler for every provider: revoke().
"""
from __future__ import annotations

import importlib

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from ... import db
from ...providers import registry
from ...sources import invalidate_model_cache
from ..deps import _pool_flash, require_user

router = APIRouter()

# Generic action -> the suffix of each provider's handler name.
_ACTIONS = {"connect": "start", "poll": "poll", "finish": "complete", "callback": "callback", "cancel": "cancel",
            "add": "add", "device": "device_start"}


async def revoke(request: Request, pid: str, cred_id: int, *, back: str = "/sources", auth=None):
    """Remove one account from a provider: its donor (or an admin) only. Ends
    the grant upstream where the provider has a revoke call, then disables
    the row so it is never routed again. auth: the caller module's
    require_user (provider route modules pass their own)."""
    user, redir = (auth or require_user)(request)
    if redir:
        return redir
    spec = registry.get(pid)
    if spec is None or spec.ensure is None:
        raise HTTPException(404, "Not found")
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    if not row or row["source_id"] != spec.ensure():
        raise HTTPException(404, "Not found")
    if row["donated_by"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Forbidden")
    if spec.revoke_upstream:
        await spec.revoke_upstream(row)
    importlib.import_module(f"app.sources.{pid}").disable_credential(cred_id)
    invalidate_model_cache(pid)
    if spec.removed_copy:
        _pool_flash[user["id"]] = ("ok", spec.removed_copy, pid)
    return RedirectResponse(back, status_code=303)


@router.post("/sources/p/{pid}/{cred_id}/revoke")
async def revoke_route(request: Request, pid: str, cred_id: int):
    return await revoke(request, pid, cred_id)


def mount(modules: dict) -> None:
    """Add /sources/p/{pid}/{action} for each provider route module's sign-in
    handlers (<pid>_start, <pid>_poll, ...), with the same parameters and
    methods as their own route."""
    for pid, module in modules.items():
        for action, suffix in _ACTIONS.items():
            handler = getattr(module, f"{pid}_{suffix}", None)
            methods = next((sorted(r.methods) for r in module.router.routes if getattr(r, "endpoint", None) is handler), None)
            if handler is not None and methods:
                router.add_api_route(f"/sources/p/{pid}/{action}", handler, methods=methods)
