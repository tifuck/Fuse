import asyncio
import base64
import binascii
import inspect
import json
import logging
import math
import re
import time
from functools import partial

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, StreamingResponse

from . import accounts, chat_images, db, model_caps, opencode as opencode_mod, provider_errors
from .config import settings
from .database_errors import is_lock_conflict
from .providers.access import Access
from .proxy import STREAM_STALLED, merge_usage, scrub_secrets, usage_from_obj
from .security import client_ip, limiter
from .sources import (
    POOL_TYPES,
    catalog_unavailable_for,
    metadata_effort,
    model_allowed,
    models_by_source,
    pick,
    pool_denial,
    pool_denial_message,
    read_credentials,
    union_names,
)
from .sources import pool_pick
from .sources.base import ChatResult
from .sources import openrouter as openrouter_mod
from .sources import explabs as explabs_mod
from .sources import nanogpt as nanogpt_mod
from .sources import devin as devin_mod
from .sources.chatgpt_catalog import base_slug as chatgpt_base_slug
from .sources.pool_session import related_session_id, session_id_from

router = APIRouter()
_LINE_CAP = 1_048_576
# An inline error frame (scrubbed when whole) that outgrows _LINE_CAP is
# dropped rather than forwarded raw.
_ERROR_FRAME_HEAD = re.compile(rb'\s*data:\s*\{\s*"error"')
_CLOSE_SECONDS = 5
_MAX_MESSAGES = 1_000
_MAX_MESSAGE_CHARS = 1_500_000
_MAX_RETAINED_MESSAGES = 32_768
_MAX_RETAINED_CHARS = 48_000_000
# Inline image data (base64 chars) one outgoing request may carry. Images are
# not text: the limits above count text, and the context meter counts each
# image as ~1,500 tokens however many bytes its PNG takes. Past this the
# older half of the images is left out (frozen, like a provider refusal)
# instead of failing the run.
_MAX_IMAGE_CHARS = 48_000_000
_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
# Request bodies past this are decoded and validated off the event loop.
_LARGE_BODY = 65_536
logger = logging.getLogger(__name__)


def _bearer(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


async def http_errors(request: Request, exc):
    """App-wide HTTPException handler (registered in main). OpenAI SDKs read
    error.message; Fuse's own /v1 rejections carry it beside FastAPI's detail,
    like the upstream errors /v1 relays."""
    if request.url.path.startswith("/v1/") and isinstance(exc.detail, str):
        kind = "invalid_request_error" if exc.status_code < 500 else "api_error"
        return JSONResponse(
            {"detail": exc.detail, "error": {"message": exc.detail, "type": kind, "code": exc.status_code}},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )
    return await http_exception_handler(request, exc)


def require_key(request: Request):
    ip = client_ip(request)
    if not limiter.allow(f"api-ip:{ip}", settings.api_rate_per_minute * 4, 60):
        raise HTTPException(429, "Too many requests")
    token = _bearer(request)
    if not token:
        raise HTTPException(401, "Missing API key")
    user = accounts.user_by_api_key(token)
    if not user:
        raise HTTPException(401, "Invalid API key")
    if not limiter.allow(f"api-user:{user['id']}", settings.api_rate_per_minute, 60):
        raise HTTPException(429, "Too many requests")
    return user


def _error_message(body) -> str:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, str) and err.strip():
            return scrub_secrets(err.strip())[:400]
        if isinstance(err, dict):
            msg = err.get("message") or err.get("msg") or ""
            if isinstance(msg, str) and msg.strip():
                return scrub_secrets(msg.strip())[:400]
        for key in ("message", "msg", "detail"):
            val = body.get(key)
            if isinstance(val, str) and val.strip():
                return scrub_secrets(val.strip())[:400]
    return ""


def _upstream_error(status: int, body=None):
    code = 502 if status == 401 else status if 400 <= status <= 599 else 502
    msg = _error_message(body) or "upstream error"
    err_type = provider_errors.kind_of(body) or ("access_denied" if code in (401, 402, 403) else "api_error")
    return JSONResponse(
        {"error": {"message": msg, "type": err_type, "code": code}},
        status_code=code,
    )


def _safe_record_usage(*args, **kwargs) -> bool:
    try:
        provider = kwargs.get("provider") or ""
        if provider == "devin" and len(args) >= 4 and isinstance(args[3], str):
            args = list(args)
            args[3] = devin_mod.canonical_upstream(args[3]) or args[3]
        try:
            db.record_usage(*args, **kwargs)
        except Exception as exc:
            # A deadlock victim or lock wait timeout rolled the whole write
            # back, so one more try keeps the usage. Anything else may have
            # committed (a lost connection) and is never replayed.
            if not is_lock_conflict(exc):
                raise
            logger.warning("Usage write hit lock contention; retrying once")
            db.record_usage(*args, **kwargs)
        return True
    except Exception:
        logger.exception("Usage accounting degraded after an upstream result")
        return False


def _validate_messages(messages, *, retained=False) -> None:
    max_messages = _MAX_RETAINED_MESSAGES if retained else _MAX_MESSAGES
    max_chars = _MAX_RETAINED_CHARS if retained else _MAX_MESSAGE_CHARS
    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "messages must be a non-empty array")
    if len(messages) > max_messages:
        raise HTTPException(
            400,
            f"too many messages ({len(messages)}; max {max_messages}) - "
            "compact the conversation or start a new session",
        )
    roles = {"system", "developer", "user", "assistant", "tool"}
    total = 0
    checked: set[str] = set()
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise HTTPException(400, f"messages[{index}] must be an object")
        if not isinstance(message.get("role"), str) or message["role"] not in roles:
            raise HTTPException(400, f"messages[{index}] has an unsupported role")
        content = message.get("content")
        if content is not None and not isinstance(content, (str, list)):
            raise HTTPException(400, f"messages[{index}].content must be a string or array")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            if len(content) > 100:
                raise HTTPException(400, f"messages[{index}].content has too many parts (max 100)")
            for part_index, part in enumerate(content):
                if not isinstance(part, dict) or not isinstance(part.get("type"), str):
                    raise HTTPException(
                        400, f"messages[{index}].content[{part_index}] must be an object with a type"
                    )
                text = part.get("text")
                if text is not None and not isinstance(text, str):
                    raise HTTPException(
                        400, f"messages[{index}].content[{part_index}].text must be a string"
                    )
                total += len(text or "")
                image = part.get("image_url")
                if image is not None and not isinstance(image, (str, dict)):
                    raise HTTPException(
                        400,
                        f"messages[{index}].content[{part_index}].image_url must be a string or object",
                    )
                if isinstance(image, dict) and not isinstance(image.get("url"), str):
                    raise HTTPException(
                        400, f"messages[{index}].content[{part_index}].image_url.url must be a string"
                    )
                # Image data is not counted here: _MAX_IMAGE_CHARS bounds it
                # once the route is known and older images can be left out.
                if part.get("type") in ("image_url", "input_image"):
                    url = image.get("url") if isinstance(image, dict) else image
                    if not isinstance(url, str) or not url:
                        raise HTTPException(400, "Image parts must include image_url")
                    if url in checked:
                        pass  # one decode per distinct image, however often it repeats
                    elif url.startswith("data:"):
                        checked.add(url)
                        header, sep, data = url.partition(",")
                        if not sep or not header.endswith(";base64") or not data:
                            raise HTTPException(400, "Images must use a base64 data URI")
                        if header[5:].split(";", 1)[0] not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
                            raise HTTPException(400, "Unsupported image MIME type")
                        try:
                            base64.b64decode(data, validate=True)
                        except (ValueError, binascii.Error):
                            raise HTTPException(400, "Invalid base64 image data") from None
                    elif not url.startswith(("https://", "http://")):
                        raise HTTPException(400, "Images must use an HTTP(S) URL or base64 data URI")
                if part.get("type") in ("file", "input_file"):
                    file = part.get("file")
                    file = file if isinstance(file, dict) else part
                    if not any(isinstance(file.get(key), str) and file[key] for key in ("file_id", "file_data")):
                        raise HTTPException(
                            400,
                            f"messages[{index}].content[{part_index}] must include file_id or file_data",
                        )
                    for key in ("file_id", "file_data", "filename"):
                        value = file.get(key)
                        if value is not None and not isinstance(value, str):
                            raise HTTPException(
                                400,
                                f"messages[{index}].content[{part_index}].{key} must be a string",
                            )
                        if isinstance(value, str):
                            total += len(value)
        calls = message.get("tool_calls")
        reasoning = message.get("reasoning_content")
        if reasoning is not None:
            if not isinstance(reasoning, str):
                raise HTTPException(400, f"messages[{index}].reasoning_content must be a string")
            total += len(reasoning)
        if calls is not None:
            if not isinstance(calls, list) or len(calls) > 100:
                raise HTTPException(400, f"messages[{index}].tool_calls must be an array (max 100)")
            for call_index, call in enumerate(calls):
                fn = call.get("function") if isinstance(call, dict) else None
                if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
                    raise HTTPException(
                        400, f"messages[{index}].tool_calls[{call_index}] must include function.name"
                    )
                if fn.get("arguments") is not None and not isinstance(fn.get("arguments"), (str, dict)):
                    raise HTTPException(
                        400,
                        f"messages[{index}].tool_calls[{call_index}].function.arguments "
                        "must be a string or object",
                    )
                arguments = fn.get("arguments") or ""
                total += len(arguments) if isinstance(arguments, str) else len(json.dumps(arguments))
        if total > max_chars:
            break
    if total > max_chars:
        raise HTTPException(
            400,
            f"messages too large ({total} chars; max {max_chars}) - "
            "compact the conversation or start a new session",
        )


def _ensure_encodable(body: dict) -> None:
    """httpx encodes with allow_nan=False and strict UTF-8. A body it cannot
    encode fails identically on every account, so reject it up front rather
    than letting pools blame (and cool down) each credential in turn."""
    try:
        json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise HTTPException(400, "Request body contains values that cannot be encoded as JSON") from None


def _validate_reasoning(body: dict) -> None:
    effort = body.get("reasoning_effort")
    if effort is not None and (not isinstance(effort, str) or effort not in _REASONING_EFFORTS):
        raise HTTPException(400, "Invalid reasoning_effort")
    reasoning = body.get("reasoning")
    if reasoning is not None and not isinstance(reasoning, dict):
        raise HTTPException(400, "reasoning must be an object")
    nested_effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
    if nested_effort is not None and (
        not isinstance(nested_effort, str) or nested_effort not in _REASONING_EFFORTS
    ):
        raise HTTPException(400, "Invalid reasoning.effort")
    if "thinking" not in body:
        return
    thinking = body["thinking"]
    if not isinstance(thinking, dict) or thinking.get("type") not in ("enabled", "disabled"):
        raise HTTPException(400, "thinking.type must be enabled or disabled")
    if "clear_thinking" in thinking and not isinstance(thinking["clear_thinking"], bool):
        raise HTTPException(400, "thinking.clear_thinking must be a boolean")


async def _run_chat(src, body: dict, user_id: int, session_id: str | None = None, affinity_id: str | None = None,
                    *, mask: bool = True):
    if mask:
        # The provider door (app/secret_guard.py): every attempt and retry
        # passes here last, so no caller can send a protected value in clear.
        from . import secret_guard

        body = await secret_guard.outbound(user_id, body)
    return await src.chat(body, session_id=session_id, user_id=user_id, affinity_id=affinity_id)


def _record_attempts(result, user, src, upstream: str, requested_model: str) -> None:
    """Persist retried upstream attempts: quota may have been consumed even
    though the attempt failed and no tokens were reported."""
    for attempt in getattr(result, "attempts", None) or []:
        if not isinstance(attempt, dict):
            continue
        usage = attempt.get("usage") if isinstance(attempt.get("usage"), dict) else {}
        if not usage and not attempt.get("status"):
            continue
        _safe_record_usage(
            user["id"],
            src.source_id,
            attempt.get("credential_id"),
            upstream,
            usage,
            key_id=user.get("key_id"),
            provider=src.type_id,
            requested_model=requested_model,
            request_status="failed",
            count_request=False,
            source_name=getattr(src, "name", ""),
        )


async def _tap(
    stream,
    user_id: int,
    source_id: int,
    cred_id,
    model: str,
    *,
    key_id=None,
    provider: str = "",
    requested_model: str = "",
    source_name: str = "",
):
    usage = {}
    buf = b""
    saw_done = False
    saw_error = False
    skip_line = False
    request_status = "success"

    def inspect(line: bytes) -> bytes:
        """Account for one SSE line and return it as it may reach the client."""
        nonlocal usage, saw_done, saw_error, request_status
        text = line.decode(errors="replace").strip()
        if not text.startswith("data:"):
            return line
        data = text[5:].strip()
        if not data:
            return line
        if data == "[DONE]":
            saw_done = True
            return line
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            request_status = "failed"
            return line
        if not isinstance(obj, dict):
            request_status = "failed"
            return line
        # Only frames that carry a usage dict update the counts; a later
        # delta frame must not zero what an earlier usage frame reported.
        if isinstance(obj.get("usage"), dict) and obj["usage"]:
            usage = merge_usage(usage, usage_from_obj(obj, reasoning_separate=(provider == "grok")))
        if obj.get("error"):
            request_status = "failed"
            saw_error = True
            # Inline errors get the same scrub as non-stream error bodies.
            return scrub_secrets(line.decode(errors="replace")).encode()
        return line

    try:
        async for chunk in stream:
            buf += chunk
            # Inspect complete lines first: a capped chunk may still carry the
            # usage and [DONE] frames after a huge one. The remainder holds no
            # newline, so only this chunk can end a line.
            parts = []
            if b"\n" in chunk:
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    if skip_line:
                        skip_line = False
                        continue
                    parts.append(inspect(line) + b"\n")
            out = b"".join(parts)
            if len(buf) > _LINE_CAP:
                if skip_line or _ERROR_FRAME_HEAD.match(buf):
                    if not skip_line:
                        request_status, saw_error = "failed", True
                        error = {"error": {"message": "upstream error frame too large", "type": "api_error", "code": 502}}
                        out += f"data: {json.dumps(error, separators=(',', ':'))}\n".encode()
                    skip_line, buf = True, b""
                else:
                    out, buf = out + buf, b""
            if out:
                yield out
        if buf and not skip_line:
            yield inspect(buf)
        buf = b""
        if not saw_done:
            # Upstream closed cleanly but early: without a terminal frame the
            # client would read a cut-off answer as a normal end.
            if not saw_error:
                error = {
                    "error": {
                        "message": "upstream stream ended before completion",
                        "type": "api_error",
                        "code": 502,
                    }
                }
                yield f"data: {json.dumps(error, separators=(',', ':'))}\n\n".encode()
            yield b"data: [DONE]\n\n"
    except asyncio.CancelledError as exc:
        # Client disconnected mid-stream. The provider may still have consumed
        # quota; record whatever usage we saw as a cancelled request instead of
        # a silent zero-token success. A read abandoned on a stall failed.
        request_status = "failed" if exc.args[:1] == (STREAM_STALLED,) else "cancelled"
        raise
    except GeneratorExit:
        # Closed by its consumer: after [DONE] that is a normal end, before
        # it a client gone between chunks.
        if not saw_done:
            request_status = "cancelled"
        raise
    except Exception as exc:
        request_status = "failed"
        if not saw_done:
            error = {
                "error": {
                    "message": f"upstream stream failed ({type(exc).__name__})",
                    "type": "api_error",
                    "code": 502,
                }
            }
            yield f"data: {json.dumps(error, separators=(',', ':'))}\n\n".encode()
            yield b"data: [DONE]\n\n"
    finally:
        if request_status == "success" and not saw_done:
            # A stream that never reached [DONE] was truncated or errored
            # inline; it is not a successful request even at HTTP 200.
            request_status = "failed"
        await _settle_tap(stream, usage, request_status, user_id, source_id, cred_id, model,
                          key_id=key_id, provider=provider, requested_model=requested_model, source_name=source_name)


async def _settle_tap(stream, usage: dict, request_status: str, user_id: int, source_id: int, cred_id, model: str, *,
                      key_id=None, provider: str = "", requested_model: str = "", source_name: str = "") -> None:
    """Record a /v1 stream's usage row and close its upstream."""
    extra = getattr(stream, "usage", None)
    usage = merge_usage(usage, extra if isinstance(extra, dict) else None)
    usage.pop("stream_status", None)
    if not usage:
        # No usage frame anywhere: record the explicit unknown/zero shape.
        usage = usage_from_obj({})
    # usage_status stays "complete" when a usage frame arrived (tokens are
    # known even if framing truncated); without one it is unknown.
    if usage and not any(v for k, v in usage.items() if k.endswith("tokens") and v):
        usage["usage_status"] = "unknown"
    elif usage:
        usage.setdefault("usage_status", "complete")
    record = (user_id, source_id, cred_id, model, usage)
    options = {"key_id": key_id, "provider": provider, "requested_model": requested_model,
               "request_status": request_status, "source_name": source_name}
    try:
        # Off the event loop, and finished even if this generator is being
        # cancelled or closed: anyio's shield stops scope re-cancellation,
        # asyncio.shield keeps a native cancel from dropping the write.
        with anyio.CancelScope(shield=True):
            await asyncio.shield(asyncio.to_thread(_safe_record_usage, *record, **options))
    except RuntimeError:
        # No executor (loop shutting down): write inline rather than lose it.
        _safe_record_usage(*record, **options)
    close = getattr(stream, "aclose", None)
    if close:
        try:
            # After a client disconnect the enclosing anyio scope is
            # cancelled and every further await would be re-cancelled,
            # skipping the upstream close. Shield it, bounded.
            with anyio.move_on_after(_CLOSE_SECONDS, shield=True):
                await close()
        except Exception:
            logger.warning("Could not close upstream stream", exc_info=True)


class _TapResponse(StreamingResponse):
    """A /v1 stream that settles however the response ends. A client gone
    before the first body chunk never starts the tap, so its finally (usage
    row, upstream close, the pool account's release) would never run:
    `settle_unstarted` does that work instead. `on_close` runs either way."""

    def __init__(self, tap, settle_unstarted, **kwargs):
        super().__init__(tap, **kwargs)
        self._settle_unstarted = settle_unstarted
        self.on_close: list = []

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    if inspect.getasyncgenstate(self.body_iterator) == inspect.AGEN_CREATED:
                        await self._settle_unstarted()
                    else:
                        await self.body_iterator.aclose()
                finally:
                    for close in self.on_close:
                        close()


@router.get("/v1/models")
async def list_models(request: Request):
    user = await asyncio.to_thread(require_key, request)
    groups = await models_by_source(user_id=user["id"])
    names = union_names(group["models"] for group in groups)
    if not names and catalog_unavailable_for(""):
        raise HTTPException(503, "Model catalog is temporarily unavailable")
    image = {e["id"]: e["image"] for e in await opencode_mod.model_options(user["id"], groups)}
    data = []
    for n in names:
        row = {"id": n, "object": "model", "owned_by": "fuse"}
        if image.get(n) is not None:
            row["input_modalities"] = ["text", "image"] if image[n] else ["text"]
        data.append(row)
    return {"object": "list", "data": data}


@router.get("/v1/opencode")
async def opencode_provider(request: Request):
    user = await asyncio.to_thread(require_key, request)
    entries = await opencode_mod.model_options(user["id"])
    if not entries and catalog_unavailable_for(""):
        raise HTTPException(503, "Model catalog is temporarily unavailable")
    origin = (settings.public_base_url or "").rstrip("/")
    api_base = (origin + "/v1") if origin else str(request.base_url).rstrip("/") + "/v1"
    return {
        "object": "opencode.provider",
        "provider": {"fuse": opencode_mod.fuse_provider(entries, base_url=api_base)},
    }


async def invoke_chat(request: Request, user, body: dict, *, cheap_reasoning: bool = False,
                      image_snapshot: dict | None = None, prepared=None):
    """Pick a source and run one completion.

    Returns (user, src, upstream, requested_model, result). Failed pool
    attempts are recorded. The caller records successful usage.
    cheap_reasoning: replace any reasoning_effort with the cheapest one the
    resolved provider accepts (title/recap calls).
    image_snapshot/prepared are server-only replay state and its durable
    checkpoint hook, run after admission and before dispatch.
    """
    try:
        user = dict(user)
        return await _invoke_chat(request, user, body, cheap_reasoning=cheap_reasoning,
                                  image_snapshot=image_snapshot, prepared=prepared)
    except HTTPException as exc:
        model = body.get("model") if isinstance(body, dict) else ""
        messages = body.get("messages") if isinstance(body, dict) else None
        logger.warning(
            "Rejected chat request user=%s key=%s model=%r status=%s messages=%s detail=%s",
            user.get("id"),
            user.get("key_id"),
            (model or "")[:100] if isinstance(model, str) else model,
            exc.status_code,
            len(messages) if isinstance(messages, list) else "invalid",
            exc.detail,
        )
        raise


def _set_cheap_reasoning(body: dict, type_id: str, model: str, user_id: int) -> None:
    body.pop("reasoning_effort", None)
    effort = metadata_effort(type_id, model, user_id)
    if effort:
        body["reasoning_effort"] = effort
    if type_id == "llama":
        # llama.cpp takes no effort, so thinking ate the whole title/recap
        # budget (empty content). Templates without this switch ignore it.
        body["chat_template_kwargs"] = {**(body.get("chat_template_kwargs") or {}), "enable_thinking": False}


def _admitted(src, upstream: str, model: str, user_id: int, access: Access, body: dict, cheap_reasoning: bool) -> bool:
    """A routed request's sync checks, in one worker-thread hop: False when
    the user turned the model off; raises the 403/400 that refuses it."""
    if not model_allowed(src.type_id, user_id, model, upstream):
        return False
    if not access.allows(src.type_id):
        raise HTTPException(403, pool_denial_message(src.type_id, user_id))
    _ensure_encodable(body)
    if cheap_reasoning:
        _set_cheap_reasoning(body, src.type_id, model, user_id)
    try:
        read_credentials(src)
    except Exception:
        pass  # src.chat reads them again, inside the attempt's error handling
    return True


def _refusal(model: str, user_id: int, access: Access, off) -> HTTPException:
    """Why `model` did not route for this user (off: where it routes with
    the user's model choices ignored), in one worker-thread hop."""
    if model.startswith("explabs/") and not explabs_mod.user_has_explabs(user_id):
        return HTTPException(403, "Reconnect your Experiential Labs API key to use its models")
    denied = nanogpt_mod.denied_reason(model, user_id) or openrouter_mod.denied_reason(model, user_id)
    if denied:
        return HTTPException(403, denied)
    # Turned off on Sources: say so, not "Unknown model".
    if off:
        return HTTPException(403, f"{provider_errors.label(off[0].type_id)}: {model} is turned off for your account. "
                                  "Turn it on in Sources.")
    if catalog_unavailable_for(model):
        return HTTPException(503, "Model catalog is temporarily unavailable")
    # A model of a pool the user lost their account for (e.g. an OAuth
    # grant revoked upstream) says what to reconnect, not "Unknown model".
    denial = pool_denial(model, user_id, access)
    if denial:
        return HTTPException(403, denial)
    return HTTPException(404, "Unknown model")


def _prepare_images(original, type_id: str, upstream: str, user_id: int, images: dict) -> tuple[list, int, int]:
    """model_caps.prepare, shedding older images while the request's inline
    image data is over _MAX_IMAGE_CHARS. Returns (messages, omitted, shed)."""
    messages, omitted = model_caps.prepare(original, type_id, upstream, user_id=user_id, snapshot=images)
    shed_total = 0
    while model_caps.inline_image_chars(messages) > _MAX_IMAGE_CHARS:
        shed = model_caps.shed(images, type_id, upstream, reason="size")
        if not shed:
            break
        shed_total += shed
        logger.warning("Request image data over %s chars provider=%s model=%s; dropped %s older image(s)",
                       _MAX_IMAGE_CHARS, type_id, upstream, shed)
        messages, omitted = model_caps.prepare(original, type_id, upstream, user_id=user_id, snapshot=images)
    return messages, omitted, shed_total


async def _invoke_chat(request: Request, user, body: dict, *, cheap_reasoning: bool = False,
                       image_snapshot: dict | None = None, prepared=None):
    user = dict(user)
    if not isinstance(body, dict):
        raise HTTPException(400, "Invalid JSON")
    model = body.get("model") or ""
    if not isinstance(model, str) or len(model) > 200:
        raise HTTPException(400, "Invalid model")
    if chat_images.has_refs(body.get("messages")):
        # Stored transcripts keep large images once per thread; providers get
        # the same inline bytes as the first time. Lookups are this user's own.
        body = {**body, "messages": await asyncio.to_thread(chat_images.rehydrate, user["id"], body["messages"])}
    # Validation decodes every distinct image: off the loop, like the image
    # work below, so one large transcript does not stall every other stream.
    await asyncio.to_thread(_validate_messages, body.get("messages"),
                            retained=bool(request.scope.get("fuse_retained_history")))
    if not request.scope.get("fuse_secret_exempt"):
        from . import secret_guard

        if (await asyncio.to_thread(secret_guard.map_for, user["id"])).broken:
            # Fail closed: values that cannot be read cannot be kept out.
            raise HTTPException(409, secret_guard.BROKEN)
    body = dict(body)
    body.pop("prompt_snapshot", None)
    body.pop("image_snapshot", None)
    if "reasoning_effort" not in body and "reasoningEffort" in body:
        body["reasoning_effort"] = body.pop("reasoningEffort")
    _validate_reasoning(body)
    if "stream" in body and not isinstance(body["stream"], bool):
        raise HTTPException(400, "Invalid stream")
    # Both spellings reach upstreams; each is validated the same way. The
    # ceiling is the upstream's, not Fuse's.
    for field in ("max_tokens", "max_completion_tokens"):
        if body.get(field) is None:
            continue
        try:
            value = body[field]
            if isinstance(value, bool):
                raise ValueError
            number = float(value)
            if not math.isfinite(number) or not number.is_integer():
                raise ValueError
            body[field] = max(1, int(number))
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(400, f"Invalid {field}")
    # Routing and access re-read the same pool switches, prefs and source rows
    # many times; reuse them for this phase only (never across the upstream call).
    with db.read_memo():
        access = Access(user["id"])
        if model.startswith("explabs/") and not await asyncio.to_thread(explabs_mod.user_has_explabs, user["id"]):
            raise HTTPException(403, "Connect an Experiential Labs API key to use its models")
        try:
            # Where the model routes before the user's model choices, which
            # _admitted applies: a model turned off is refused as such.
            routed = await pick(model, user_id=user["id"], access=access, ignore_prefs=True)
        except explabs_mod.CatalogError:
            raise HTTPException(503, "Experiential Labs model catalog is temporarily unavailable") from None
        if not routed or not await asyncio.to_thread(_admitted, *routed, model, user["id"], access, body,
                                                     cheap_reasoning):
            raise await asyncio.to_thread(_refusal, model, user["id"], access, routed)
        src, upstream = routed
        requested = model
        body["model"] = upstream
        original = body["messages"]
        images = image_snapshot if image_snapshot is not None else {}
        body["messages"], omitted, shed_total = await asyncio.to_thread(
            _prepare_images, original, src.type_id, upstream, user["id"], images)
        if prepared is not None:
            await prepared()
        affinity_id = None
        # ChatGPT Fast is a service tier on the same model: toggling it keeps the
        # conversation's cache key and account pin.
        scope_model = chatgpt_base_slug(upstream) if src.type_id == "chatgpt" else upstream
        session_id = session_id_from(request.headers, body, user_id=user["id"], model=scope_model)
        if src.type_id in POOL_TYPES:
            # A subagent's run sets its parent's cache key (server-side scope,
            # never client input) so its first request can start where the parent
            # already cached the shared tools and system prompt.
            affinity_id = related_session_id(request.scope.get("fuse_cache_parent"), user_id=user["id"], model=scope_model)
    while True:
        try:
            result = await _run_chat(src, body, user["id"], session_id=session_id, affinity_id=affinity_id,
                                     mask=not request.scope.get("fuse_secret_exempt"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Source invocation failed provider=%s", src.type_id)
            result = ChatResult(
                status=502,
                body={"error": {"message": f"upstream request failed ({type(exc).__name__})"}},
                model=upstream,
            )
        if getattr(result, "attempts", None):
            await asyncio.to_thread(_record_attempts, result, user, src, upstream, requested)
        if result.status == 200 or not model_caps.image_refusal(result.status, _error_message(result.body)):
            break
        # The provider refused this many images or bytes: drop the older
        # half, freeze that in the snapshot, and retry until it fits.
        shed = model_caps.shed(images, src.type_id, upstream)
        if not shed:
            break
        shed_total += shed
        logger.warning("Provider refused images provider=%s model=%s status=%s; dropped %s older image(s)",
                       src.type_id, upstream, result.status, shed)
        await asyncio.to_thread(_safe_record_usage, user["id"], src.source_id, result.credential_id, upstream,
                                result.usage, key_id=user.get("key_id"), provider=src.type_id,
                                requested_model=requested, request_status="failed", count_request=False,
                                source_name=getattr(src, "name", ""))
        body["messages"], omitted = await asyncio.to_thread(model_caps.prepare, original, src.type_id, upstream,
                                                            user_id=user["id"], snapshot=images)
        if prepared is not None:
            await prepared()
    if result.status != 200:
        raw = result.body
        result.body, info = provider_errors.annotate(src.type_id, result.status, result.body, result.response_headers)
        if src.type_id in POOL_TYPES and result.credential_id:
            # What benched this account, so a later request that finds the
            # whole pool benched reports it (pool_pick.explain_empty).
            pool_pick.remember_error(result.credential_id, result.status, raw, result.response_headers, info)
        wait = info["retry_after"] if info and info["kind"] in provider_errors.TRANSIENT else None
        if wait and not any(k.lower() == "retry-after" for k in result.response_headers):
            result.response_headers = {**result.response_headers, "Retry-After": str(math.ceil(wait))}
    elif src.type_id in POOL_TYPES and result.credential_id:
        pool_pick.forget_error(result.credential_id)
    headers = {}
    if omitted:
        headers["X-Fuse-Images-Omitted"] = str(omitted)
    if shed_total:
        headers["X-Fuse-Images-Shed"] = str(shed_total)
    if headers:
        result.response_headers = {**result.response_headers, **headers}
    return user, src, upstream, requested, result


async def dispatch_chat(request: Request, user, body: dict):
    user, src, upstream, requested, result = await invoke_chat(request, user, body)
    if result.status != 200:
        await asyncio.to_thread(
            _safe_record_usage,
            user["id"],
            src.source_id,
            result.credential_id,
            upstream,
            result.usage,
            key_id=user.get("key_id"),
            provider=src.type_id,
            requested_model=requested,
            request_status="failed",
            count_request=True,
            source_name=getattr(src, "name", ""),
        )
        if result.status in (402, 403) and isinstance(result.body, dict):
            msg = _error_message(result.body)
            if msg:
                return JSONResponse(
                    {"error": {"message": msg, "type": provider_errors.kind_of(result.body) or "access_denied", "code": result.status}},
                    status_code=result.status,
                    headers=result.response_headers,
                )
        response = _upstream_error(result.status, result.body)
        response.headers.update(result.response_headers)
        return response
    key_id = user.get("key_id")
    omitted = {k: v for k, v in result.response_headers.items() if k in ("X-Fuse-Images-Omitted", "X-Fuse-Images-Shed")}
    if result.stream is not None:
        record = (user["id"], src.source_id, result.credential_id, upstream)
        options = {"key_id": key_id, "provider": src.type_id, "requested_model": requested,
                   "source_name": getattr(src, "name", "")}
        return _TapResponse(
            _tap(result.stream, *record, **options),
            partial(_settle_tap, result.stream, {}, "cancelled", *record, **options),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", **omitted},
        )
    await asyncio.to_thread(
        _safe_record_usage,
        user["id"],
        src.source_id,
        result.credential_id,
        upstream,
        result.usage,
        key_id=key_id,
        provider=src.type_id,
        requested_model=requested,
        source_name=getattr(src, "name", ""),
    )
    return JSONResponse(result.body or {}, headers=omitted)


# Open /v1 requests per user: {user_id: {token: started}}. The per-minute
# limit alone let one user hold long streams until every uvicorn slot
# (--limit-concurrency) and pool account in-flight cap was theirs. Entries
# expire after _IN_FLIGHT_TTL, so a stream whose generator never started (a
# client gone before the first byte) cannot pin a slot forever.
_in_flight: dict[int, dict[object, float]] = {}
_IN_FLIGHT_TTL = 3600.0


def _admit(user_id: int):
    now = time.monotonic()
    live = {t: s for t, s in _in_flight.get(user_id, {}).items() if now - s < _IN_FLIGHT_TTL}
    _in_flight[user_id] = live
    if len(live) >= settings.api_max_in_flight:
        return None
    token = object()
    live[token] = now
    return token


def _release(user_id: int, token) -> None:
    live = _in_flight.get(user_id)
    if live is not None:
        live.pop(token, None)
        if not live:
            _in_flight.pop(user_id, None)


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    from .web.deps import _json_object

    user = await asyncio.to_thread(require_key, request)
    # API clients get their bodies as sent: Fuse cannot restore a stand-in on
    # their side (app/secret_guard.py).
    request.scope["fuse_secret_exempt"] = True
    token = _admit(user["id"])
    if token is None:
        raise HTTPException(429, "Too many concurrent requests")
    try:
        try:
            # Same strict parser as the dashboard: invalid UTF-8, lone surrogates,
            # NaN/Infinity and pathological nesting are client errors, not 500s or
            # upstream encode failures blamed on a pool account.
            if len(await request.body()) > _LARGE_BODY:
                # The body is read already, so _json_object never waits on
                # the request: its own loop in a worker thread runs it whole.
                body = await asyncio.to_thread(asyncio.run, _json_object(request))
            else:
                body = await _json_object(request)
        except HTTPException as exc:
            logger.warning(
                "Rejected chat request user=%s status=%s detail=%s", user["id"], exc.status_code, exc.detail
            )
            raise
        response = await dispatch_chat(request, user, body)
    except BaseException:
        _release(user["id"], token)
        raise
    if not isinstance(response, _TapResponse):
        _release(user["id"], token)
        return response
    response.on_close.append(partial(_release, user["id"], token))
    return response
