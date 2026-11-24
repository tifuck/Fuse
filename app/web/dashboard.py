from __future__ import annotations

import asyncio
import logging
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from .. import accounts, db, pricing as pricing_mod, stats, tool_failures
from .. import opencode as opencode_mod
from ..config import settings
from ..sources import TYPES, models_by_source
from .deps import (
    _chat_auth,
    _clear_vault_cookie,
    _json_object,
    _notices,
    _peek_key_reveal,
    _public_base,
    _set_vault_cookie,
    _stash_key_reveal,
    current_user,
    render,
    require_admin,
    require_user,
)

router = APIRouter()
logger = logging.getLogger(__name__)

def _keys_response(
    request: Request,
    user,
    *,
    new_key: str | None = None,
    key_error: str | None = None,
    key_name: str = "",
    status_code: int = 200,
):
    return render(
        request,
        "keys.html",
        user,
        status_code=status_code,
        usage=stats.current_usage(user["id"]),
        new_key=new_key,
        key_error=key_error,
        key_name=key_name,
        base_url=_public_base(request),
    )


def _pricing_response(request: Request, user):
    return render(
        request,
        "admin_pricing.html",
        user,
        pricing=stats.pricing_views(),
        provider_types=TYPES,
        pricing_synced=request.query_params.get("synced", ""),
        pricing_sync_error=request.query_params.get("error") == "1",
    )


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return RedirectResponse("/chat", status_code=303)


@router.get("/usage", response_class=HTMLResponse)
def dashboard(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    return render(request, "dashboard.html", user)


@router.get("/keys", response_class=HTMLResponse)
def api_keys(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    new_key = _peek_key_reveal(request, user["id"])
    response = _keys_response(request, user, new_key=new_key)
    if new_key:
        request.session.pop("key_reveal", None)
    return response


@router.get("/usage.json")
async def usage_json(request: Request):
    user, redir = await asyncio.to_thread(require_user, request)
    if redir:
        return JSONResponse({"error": "auth"}, status_code=401)
    range_id = request.query_params.get("range", "24h")
    provider = request.query_params.get("provider", "").strip()[:32]
    scope = request.query_params.get("scope", "")
    all_users = bool(user["is_admin"] and scope == "all")
    subject_id = None
    if user["is_admin"] and scope.startswith("user:"):
        try:
            subject_id = int(scope[5:])
        except ValueError:
            subject_id = None
        else:
            if subject_id < 1 or await asyncio.to_thread(db.fetchone, "SELECT id FROM users WHERE id=?", (subject_id,)) is None:
                return JSONResponse({"error": "user"}, status_code=404)
    # Several window scans over usage_events: never on the event loop.
    data = await asyncio.to_thread(
        stats.usage_analytics,
        user["id"],
        range_id=range_id,
        provider=provider,
        all_users=all_users,
        subject_id=subject_id,
        include_accounts=bool(user["is_admin"]),
    )
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.get("/admin/tool-failures")
def admin_tool_failures(request: Request):
    """Analysis pack of every logged failed tool call, shaped for LLM triage."""
    user, redir = require_admin(request)
    if redir:
        return JSONResponse({"error": "auth"}, status_code=401)
    try:
        limit = int(request.query_params.get("limit", "200"))
    except (TypeError, ValueError):
        limit = 200
    try:
        hours = int(request.query_params.get("hours", "168"))
    except (TypeError, ValueError):
        hours = 168
    kind = request.query_params.get("kind", "").strip()[:64]
    tool = request.query_params.get("tool", "").strip()[:64]
    model = request.query_params.get("model", "").strip()[:200]
    return JSONResponse(
        tool_failures.analysis_pack(limit=limit, hours=hours, kind=kind, tool=tool, model=model),
        headers={"Cache-Control": "no-store"},
    )


def _price_rate(value: str, *, optional: bool = False) -> int | None:
    if optional and not value.strip():
        return None
    try:
        rate = Decimal(value or "0")
    except InvalidOperation:
        raise ValueError("rate") from None
    if not rate.is_finite() or rate < 0 or rate > 1_000_000:
        raise ValueError("rate")
    return int((rate * 1_000_000).quantize(Decimal("1")))


@router.get("/admin/pricing", response_class=HTMLResponse)
def pricing_page(request: Request):
    user, redir = require_admin(request)
    if redir:
        return redir
    return _pricing_response(request, user)


@router.post("/admin/pricing")
async def save_pricing(
    request: Request,
    provider: str = Form(),
    model: str = Form(),
    input_rate: str = Form("0"),
    cached_rate: str = Form(""),
    cache_write_rate: str = Form(""),
    output_rate: str = Form("0"),
    reasoning_rate: str = Form(""),
):
    user, redir = await asyncio.to_thread(require_admin, request)
    if redir:
        return redir
    provider = provider.strip()
    model = model.strip()
    if provider not in TYPES or not model or len(model) > 200:
        raise HTTPException(400, "Invalid provider or model")
    try:
        input_v = _price_rate(input_rate)
        output_v = _price_rate(output_rate)
        cached_v = _price_rate(cached_rate, optional=True)
        write_v = _price_rate(cache_write_rate, optional=True)
        reasoning_v = _price_rate(reasoning_rate, optional=True)
    except ValueError:
        raise HTTPException(400, "Rates must be non-negative USD amounts per million tokens") from None
    if input_v is None or output_v is None:
        raise HTTPException(400, "Input and output rates are required")
    # Repricing walks the model's whole history: never on the event loop.
    # The form edits base rates only: an existing row keeps its long-context
    # tiers, and a new row starts with the built-in ones.
    await asyncio.to_thread(
        db.save_model_price, provider, model, input_v, cached_v, output_v, reasoning_v,
        cache_write_rate=write_v, tiers=pricing_mod.default_tiers(provider, model), keep_long_context=True,
    )
    return RedirectResponse("/admin/pricing", status_code=303)


@router.post("/admin/pricing/openrouter")
async def sync_openrouter_pricing(request: Request):
    user, redir = await asyncio.to_thread(require_admin, request)
    if redir:
        return redir
    try:
        result = await pricing_mod.sync_openrouter_prices()
    except Exception:
        logger.exception("OpenRouter price sync failed")
        return RedirectResponse("/admin/pricing?error=1", status_code=303)
    return RedirectResponse(f"/admin/pricing?synced={result['count']}", status_code=303)


@router.post("/admin/pricing/delete")
async def remove_pricing(request: Request, provider: str = Form(), model: str = Form()):
    user, redir = await asyncio.to_thread(require_admin, request)
    if redir:
        return redir
    if provider in TYPES and model:
        await asyncio.to_thread(db.delete_model_price, provider, model[:200])
    return RedirectResponse("/admin/pricing", status_code=303)


@router.post("/keys")
def create_key(request: Request, name: str = Form()):
    user, redir = require_user(request)
    if redir:
        return redir
    try:
        raw = accounts.create_api_key(user["id"], name)
    except ValueError:
        return _keys_response(
            request,
            user,
            key_error="Name must be 1–80 characters and cannot contain control characters.",
            key_name=name,
            status_code=400,
        )
    _stash_key_reveal(request, user["id"], raw)
    return RedirectResponse("/keys?created=1", status_code=303)


@router.post("/keys/{key_id}/delete")
def delete_key(request: Request, key_id: int):
    user, redir = require_user(request)
    if redir:
        return redir
    accounts.delete_api_key(user["id"], key_id)
    return RedirectResponse("/keys", status_code=303)


# One desktop build serves every Fuse server: .github/workflows/desktop.yml
# publishes it to GitHub Releases under these fixed file names.
_DESKTOP_DOWNLOADS = "https://github.com/tifuck/Fuse/releases/latest/download/"

_DESKTOP_BUILDS = (
    # key, label, detail, file name, one-line install note
    ("windows", "Windows", "10 and 11, 64-bit installer", "Fuse-Setup.exe",
     "Unsigned: if SmartScreen appears, choose More info, then Run anyway. No admin needed."),
    ("mac", "macOS", "Apple Silicon and Intel", "Fuse.dmg",
     "Unsigned: open it the first time with right-click, then Open. Get new versions here."),
    ("linux", "Linux AppImage", "x86_64, portable", "Fuse.AppImage",
     "chmod +x the file and run it. Updates itself."),
    ("deb", "Linux .deb", "Debian and Ubuntu", "Fuse.deb",
     "Install with your package manager. Updates itself (asks for your password to install)."),
)


@router.get("/download", response_class=HTMLResponse)
def desktop_app(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    builds = [{"key": key, "label": label, "detail": detail, "note": note,
               "url": _DESKTOP_DOWNLOADS + name, "file": name}
              for key, label, detail, name, note in _DESKTOP_BUILDS]
    return render(request, "desktop.html", user, builds=builds)


@router.get("/docs", response_class=HTMLResponse)
async def api_docs(request: Request):
    user, redir = await asyncio.to_thread(require_user, request)
    if redir:
        return redir
    groups = await models_by_source(user_id=user["id"])
    groups = [group for group in groups if group.get("models")]
    model_options = await opencode_mod.model_options(user["id"], groups)
    models = [item["id"] for item in model_options]
    site_url = (settings.public_base_url or "").rstrip("/") or str(request.base_url).rstrip("/")
    return render(
        request,
        "docs.html",
        user,
        base_url=_public_base(request),
        site_url=site_url,
        models=models,
        model_options=model_options,
    )


@router.get("/install-opencode.sh")
async def install_opencode_sh(request: Request):
    configured = (settings.public_base_url or "").rstrip("/")
    # A poisoned Host header must be refused, not silently replaced by
    # Starlette's fallback: the script's origin comes from config or a plainly
    # valid request host, never anything else.
    raw_host = (request.headers.get("host") or "").strip()
    if not configured and not opencode_mod.safe_origin("https://" + raw_host):
        raise HTTPException(400, "Set PUBLIC_BASE_URL to a plain http(s) origin to serve the installer") from None
    site_url = configured or str(request.base_url).rstrip("/")
    try:
        script = opencode_mod.install_script(origin=site_url, api_base=_public_base(request))
    except ValueError:
        raise HTTPException(400, "Set PUBLIC_BASE_URL to a plain http(s) origin to serve the installer") from None
    return PlainTextResponse(
        script,
        media_type="text/x-shellscript; charset=utf-8",
        headers={
            "Content-Disposition": "inline; filename=\"install-opencode.sh\"",
            "Cache-Control": "no-store",
        },
    )


_USER_ROLE_ERRORS = {
    "self": "You cannot change your own role.",
    "last_admin": "That is the last admin.",
    "missing": "Account not found.",
}
_USER_DELETE_ERRORS = {
    "self": "You cannot delete your own account.",
    "last_admin": "That is the last admin.",
    "missing": "Account not found.",
}


def _admin_users_page(request: Request, user, *, error: str | None = None, notice: str | None = None, status_code: int = 200):
    users = []
    for row in accounts.list_users():
        item = dict(row)
        item["cost_label"] = stats.format_usd_micros(item["cost_micros"]) if item["cost_micros"] else "—"
        users.append(item)
    return render(
        request,
        "admin_users.html",
        user,
        users=users,
        invites=accounts.list_whitelist(),
        admin_count=accounts.admin_count(),
        error=error,
        notice=notice,
        status_code=status_code,
    )


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request):
    user, redir = require_admin(request)
    if redir:
        return redir
    notice = _notices.pop(user["id"], None)
    return _admin_users_page(request, user, notice=notice)


@router.post("/admin/users")
def admin_users_invite(request: Request, email: str = Form()):
    user, redir = require_admin(request)
    if redir:
        return redir
    try:
        listed, token = accounts.add_whitelist(email)
    except ValueError as exc:
        msg = {
            "email": "Enter a valid email.",
            "exists": "That email already has an account.",
            "listed": "That email is already listed.",
        }.get(str(exc), "Could not add that email.")
        return _admin_users_page(request, user, error=msg, status_code=400)
    site_url = (settings.public_base_url or "").rstrip("/") or str(request.base_url).rstrip("/")
    _notices[user["id"]] = (
        f"Listed {listed}. Send them this one-time invite link (shown only now): "
        f"{site_url}/register?invite={token}"
    )
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/invites/{invite_id}/delete")
def admin_users_invite_delete(request: Request, invite_id: int):
    user, redir = require_admin(request)
    if redir:
        return redir
    if accounts.remove_whitelist(invite_id):
        _notices[user["id"]] = "Removed that email from the list."
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/role")
def admin_users_role(request: Request, user_id: int, admin: str = Form("0")):
    user, redir = require_admin(request)
    if redir:
        return redir
    make_admin = admin in ("1", "on", "true")
    try:
        accounts.set_admin(user_id, make_admin, actor_id=user["id"])
    except ValueError as exc:
        return _admin_users_page(
            request,
            user,
            error=_USER_ROLE_ERRORS.get(str(exc), "Could not update that account."),
            status_code=400,
        )
    _notices[user["id"]] = "Granted admin." if make_admin else "Removed admin."
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/delete")
async def admin_users_delete(request: Request, user_id: int):
    user, redir = await asyncio.to_thread(require_admin, request)
    if redir:
        return redir
    try:
        email = accounts.delete_user(user_id, actor_id=user["id"])
    except ValueError as exc:
        return _admin_users_page(
            request,
            user,
            error=_USER_DELETE_ERRORS.get(str(exc), "Could not delete that account."),
            status_code=400,
        )
    try:
        from .. import terminal

        await terminal.close_where(user_id, "account_deleted")
    except Exception:
        logger.exception("terminal close on account delete failed")
    try:
        from .. import agent_hub

        await agent_hub.close_user(user_id)
    except Exception:
        logger.exception("agent disconnect on account delete failed")
    _notices[user["id"]] = f"Deleted {email}."
    return RedirectResponse("/admin/users", status_code=303)
