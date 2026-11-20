"""Per-user model on/off for every provider (app/providers/model_prefs.py):
the JSON endpoint behind each model list on Sources."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...providers import model_prefs, registry
from ...security import limiter
from ..deps import require_user

router = APIRouter()


@router.post("/sources/models/{provider}")
async def model_choice(request: Request, provider: str):
    """One of {"model", "on"} (one model), {"group", "on"} (a group: every
    model in it, overrides cleared) or {"all": bool}. Answers with the state
    the list should show."""
    user, redir = require_user(request)
    if redir:
        return JSONResponse({"error": "auth"}, status_code=401)
    if registry.get(provider) is None:
        return JSONResponse({"error": "Unknown provider."}, status_code=404)
    if not limiter.allow(f"model-choice:{user['id']}", 240, 60):
        return JSONResponse({"error": "Too many changes. Try again in a minute."}, status_code=429)
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    uid = user["id"]
    try:
        if isinstance(body.get("all"), bool):
            prefs = model_prefs.set_all(uid, provider, body["all"])
            return JSONResponse({"groups": prefs["groups"]})
        on = body.get("on")
        if not isinstance(on, bool):
            return JSONResponse({"error": "Invalid request."}, status_code=400)
        group = body.get("group")
        if isinstance(group, str):
            prefs = model_prefs.set_group(uid, provider, group, on)
            return JSONResponse({"groups": prefs["groups"]})
        model = body.get("model")
        if not isinstance(model, str) or not model.strip() or len(model) > 300:
            return JSONResponse({"error": "Invalid model."}, status_code=400)
        return JSONResponse({"on": model_prefs.set_model(uid, provider, model.strip(), on)})
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
