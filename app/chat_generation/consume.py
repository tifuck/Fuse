"""Consume one provider round into a done-summary.

Handles both the non-streaming body and the streamed SSE case:
accumulates content/reasoning/tool calls/thinking blocks, merges
usage, holds back provider-failure sentences painted as content,
and reports liveness beats while the model is silent."""

from __future__ import annotations

import asyncio
import logging
import time

from ..chat_prompts import _text_of
from ..proxy import merge_usage, usage_from_obj
from ..sources.base import THINKING_MARK

from .parsing import (
    _finalize_assistant_payload,
    _take_calls,
    _take_reasoning_details,
    _take_thinking,
    tool_xml_name,
    tool_xml_start,
)
from .policy import _error_obj, _provider_error_content
from .stream import (
    CHUNK_GAP_TIMEOUT_S,
    IDLE_TIMEOUT_S,
    PROVIDER_BEAT_S,
    StreamStalled,
    _client_obj,
    _paced_chunks,
    _parse_sse_line,
    _terminated_chunks,
)

logger = logging.getLogger(__name__)


_STALL_PHASES = {
    "waiting": "before the first output",
    "thinking": "while thinking",
    "tool": "while writing a tool call",
    "text": "mid-reply",
}


def _stall_message(exc: TimeoutError) -> str:
    """The detail of a stall the pacer called: how long, in which phase.
    Keeps "idle timeout" for the resume policy's stall counter."""
    if not isinstance(exc, StreamStalled):
        return "upstream timeout (idle)"
    minutes, seconds = divmod(int(round(exc.quiet_s)), 60)
    span = f"{minutes}m {seconds}s" if minutes and seconds else f"{minutes}m" if minutes else f"{seconds}s"
    where = _STALL_PHASES.get(exc.phase, "")
    kept = ", though keep-alives kept arriving" if exc.quiet_s - exc.unheard_s > 1.0 else ""
    return f"No output for {span} {where}{kept} (idle timeout).".replace("  ", " ")


def _now() -> float:
    return time.monotonic()


# A window shorter than this has no measurable speed: its tokens still count
# toward the reply, not toward tok/s.
GEN_MIN_S = 0.25


def _call_text(calls) -> str:
    if not isinstance(calls, list):
        return ""
    fns = [call.get("function") for call in calls if isinstance(call, dict)]
    return "".join(str(fn.get("name") or "") + (fn["arguments"] if isinstance(fn.get("arguments"), str) else "")
                   for fn in fns if isinstance(fn, dict))


class GenClock:
    """Generation time of one model call, for tok/s.

    The window runs from the first sign of generation (a source's thinking
    mark or the first output delta) to the last output delta, so the wait for
    the first token, tool execution and a stream lingering after its last
    delta stay outside it. Server-measured decode timings (llama.cpp's
    `timings`, which the Ollama translation also emits) win when present.

    gen_cal/gen_chars is the call's tokens over the characters it streamed:
    the live dock's ratio for turning a streaming call's characters into
    tokens. Only calls whose characters carry all their tokens count (none
    of their reasoning ran unseen).
    """

    def __init__(self, opened: float):
        self.opened = opened
        self.first = self.last = None
        self.marked = False
        self.bytes = self.first_bytes = self.reasoning_bytes = 0
        self.chars = self.first_chars = 0
        self.timings = None

    def mark(self, now: float) -> None:
        if self.first is None:
            self.first, self.marked = now, True

    def frame(self, now: float, text: str, reasoning: str) -> None:
        size, think = len(text.encode()), len(reasoning.encode())
        if not size + think:
            return
        if self.first is None:
            self.first = now
        chars = len(text) + len(reasoning)
        if now == self.first and not self.marked:
            # Generated before the first frame arrived, in no measured time.
            self.first_bytes += size + think
            self.first_chars += chars
        self.last = now
        self.bytes += size + think
        self.reasoning_bytes += think
        self.chars += chars

    def _calibration(self, tokens: int, chars: int, unseen: bool) -> dict:
        if self.marked or unseen or tokens <= 0 or chars <= 0:
            return {}
        return {"gen_cal": tokens, "gen_chars": chars}

    def result(self, usage: dict) -> dict | None:
        usage = usage if isinstance(usage, dict) else {}
        hidden = usage.get("reasoning_tokens")
        unseen = type(hidden) is int and hidden > 0 and self.reasoning_bytes < 2 * hidden
        timings = self.timings if isinstance(self.timings, dict) else {}
        n, ms = timings.get("predicted_n"), timings.get("predicted_ms")
        if type(n) is int and n > 0 and isinstance(ms, (int, float)) and ms > 0:
            return {"gen_ms": max(1, round(ms)), "gen_out": n, **self._calibration(n, self.chars, unseen)}
        out = usage.get("output_tokens")
        if type(out) is not int or out <= 0 or self.last is None:
            return None
        if unseen and not self.marked:
            # Reasoning the stream did not carry (hidden, or only a summary)
            # ran before the first delta. Its start is unknown: time it from
            # the stream opening, which also holds the wait for the first token.
            span = self.last - self.opened
            return {"gen_ms": round(span * 1000), "gen_out": out, "gen_est": 1} if span >= GEN_MIN_S else None
        span = self.last - self.first
        tokens = out - round(out * self.first_bytes / self.bytes)
        if span < GEN_MIN_S or tokens <= 0:
            return None
        return {"gen_ms": round(span * 1000), "gen_out": tokens,
                **self._calibration(tokens, self.chars - self.first_chars, unseen)}


def _calling_name(calls: dict, hints) -> str:
    """Name of the newest tool call the model has started writing."""
    for index in sorted(calls, reverse=True):
        name = (calls[index].get("function") or {}).get("name")
        if name:
            return str(name)
    return str((hints or {}).get("tool_call") or "") if isinstance(hints, dict) else ""


def _add_usage(total: dict, extra: dict | None) -> dict:
    extra = extra or {}
    if not total:
        return dict(extra)
    merged = dict(total)
    for key, value in extra.items():
        if key == "cache_usage_status":
            prior = merged.get(key)
            merged[key] = value if prior is None else (prior if prior == value else "partial")
            continue
        if key in ("usage_status",):
            if value == "complete" or not merged.get(key):
                merged[key] = value
            continue
        if key == "cost_micros":
            a, b = merged.get(key), value
            if isinstance(a, int) and isinstance(b, int):
                merged[key] = a + b
            elif b is not None and a is None:
                merged[key] = b
            continue
        if isinstance(value, int) and isinstance(merged.get(key), int):
            merged[key] = min(merged[key] + value, 9_000_000_000_000_000)
        elif isinstance(value, int) and not merged.get(key):
            merged[key] = value
    return merged


async def consume_round(result, user, src, upstream: str, requested: str, *, coding: bool = False):
    from .. import api

    calls: dict[int, dict] = {}
    finish = None
    content = ""
    reasoning = ""
    thinking: list[dict] = []
    details: list[dict] = []
    usage = {}
    error = None
    if result.stream is None:
        usage = result.usage or {}
        await asyncio.to_thread(
            api._safe_record_usage,
            user["id"],
            src.source_id,
            result.credential_id,
            upstream,
            usage,
            key_id=user.get("key_id"),
            provider=src.type_id,
            requested_model=requested,
        )
        body = result.body if isinstance(result.body, dict) else {}
        choice = (body.get("choices") or [{}])[0] if isinstance(body.get("choices"), list) else {}
        message = choice.get("message") if isinstance(choice, dict) else {}
        if isinstance(message, dict):
            content = _text_of(message.get("content"))
            reasoning = _text_of(message.get("reasoning_content")) or _text_of(message.get("reasoning"))
            _take_calls(calls, message.get("tool_calls"))
            _take_thinking(thinking, message.get("claude_thinking"))
            _take_thinking(thinking, [message])
            _take_reasoning_details(details, message.get("reasoning_details"))
            if details:
                thinking.append({"reasoning_details": details})
        finish = choice.get("finish_reason") if isinstance(choice, dict) else None
        content, reasoning, native, finish = _finalize_assistant_payload(
            content, reasoning, [calls[i] for i in sorted(calls)], finish, coding=coding
        )
        if isinstance(body, dict) and body.get("error"):
            error = body
        elif not native and (
            str(finish or "").lower() in ("error", "network_error") or _provider_error_content(content)
        ):
            error = _error_obj(503, {"error": {"message": (content or "upstream error").strip()}})
            content = ""
        if error:
            yield ("done", {"calls": native, "finish": finish, "content": content, "reasoning": reasoning, "thinking": thinking, "usage": usage, "error": error})
            return
        if body and isinstance(body.get("choices"), list) and body["choices"]:
            choice_out = dict(body["choices"][0]) if isinstance(body["choices"][0], dict) else {}
            message_out = dict(choice_out.get("message") or {}) if isinstance(choice_out.get("message"), dict) else {}
            message_out["content"] = content
            choice_out["message"] = message_out
            if finish:
                choice_out["finish_reason"] = finish
            body = {**body, "choices": [choice_out, *body["choices"][1:]]}
        safe = _client_obj(body) if body else None
        if safe:
            yield ("out", safe)
        yield ("done", {"calls": native, "finish": finish, "content": content, "reasoning": reasoning, "thinking": thinking, "usage": usage, "error": error})
        return

    tapped = api._tap(
        result.stream,
        user["id"],
        src.source_id,
        result.credential_id,
        upstream,
        key_id=user.get("key_id"),
        provider=src.type_id,
        requested_model=requested,
    )
    buf = b""
    terminal = False
    # Announced name of the tool call being written ("" until known), so the
    # status line can name it while its arguments still stream.
    calling = None
    hints = getattr(result.stream, "hints", None)
    xml_at = -1
    xml_scan = 0
    held_out: list[dict] = []
    clock = GenClock(_now())
    try:
        async for kind, payload in _paced_chunks(
            _terminated_chunks(tapped), IDLE_TIMEOUT_S, CHUNK_GAP_TIMEOUT_S, PROVIDER_BEAT_S
        ):
            if kind == "beat":
                yield ("beat", payload)
                continue
            now = _now()
            chunk = payload
            buf += chunk
            if len(buf) > 2_500_000:
                raise ValueError("provider stream frame limit exceeded")
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip() == THINKING_MARK:
                    # A think that may stream no text (Claude's omitted
                    # display): the status line says so instead of "waiting".
                    clock.mark(now)
                    yield ("out", {"fuse": {"event": "activity", "phase": "model", "thinking": True}})
                    continue
                parsed = _parse_sse_line(line.decode(errors="replace"))
                if parsed == "[DONE]":
                    terminal = True
                    continue
                if not isinstance(parsed, dict):
                    continue
                obj = parsed
                if obj.get("error"):
                    # Errors travel in the done summary so the caller can
                    # decide between failing the turn and resuming it; a
                    # resumed round must not have already painted an error
                    # into the client's run state.
                    error = obj
                    continue
                got = usage_from_obj(obj, reasoning_separate=(src.type_id == "grok"))
                if got:
                    usage = merge_usage(usage, got) if usage else dict(got)
                if isinstance(obj.get("timings"), dict):
                    clock.timings = obj["timings"]
                choice = (obj.get("choices") or [{}])[0] if isinstance(obj.get("choices"), list) else {}
                if isinstance(choice, dict):
                    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
                    text = _text_of(delta.get("content")) or (
                        _text_of(message.get("content")) if not choice.get("delta") else ""
                    )
                    thought = _text_of(delta.get("reasoning_content")) or _text_of(delta.get("reasoning")) or (
                        _text_of(message.get("reasoning_content")) or _text_of(message.get("reasoning"))
                        if not choice.get("delta")
                        else ""
                    )
                    content += text
                    reasoning += thought
                    clock.frame(now, text + _call_text(delta.get("tool_calls") or message.get("tool_calls")), thought)
                    _take_calls(calls, delta.get("tool_calls") or message.get("tool_calls"))
                    _take_thinking(thinking, delta.get("claude_thinking") or message.get("claude_thinking"))
                    _take_thinking(thinking, [delta if delta.get("devin_signature") else message])
                    _take_reasoning_details(details, delta.get("reasoning_details") or message.get("reasoning_details"))
                    if choice.get("finish_reason"):
                        finish = choice.get("finish_reason")
                safe = _client_obj(obj, hide_tool_calls=False)
                if safe:
                    candidate = (content or "").strip()
                    if not calls and candidate and _provider_error_content(candidate):
                        held_out.append(safe)
                    else:
                        for pending in held_out:
                            yield ("out", pending)
                        held_out.clear()
                        yield ("out", safe)
            if xml_at < 0 and len(content) > xml_scan:
                # Text-format calls (Qwen/Hermes/GLM <tool_call>) carry no
                # tool_calls delta; the opener is the first sign of one.
                xml_at = tool_xml_start(content, xml_scan - 16)
                xml_scan = len(content)
            name = _calling_name(calls, hints) or (tool_xml_name(content, xml_at) if xml_at >= 0 else "")
            if (calls or name or xml_at >= 0) and name != calling:
                # The model has moved on to writing a tool call: any thought
                # before it is finished, not still streaming. Each new name is
                # announced as soon as it is known, before its arguments end.
                calling = name
                event = {"event": "activity", "phase": "calling"}
                if name:
                    event["tool"] = name
                yield ("out", {"fuse": event})
            if terminal or error:
                # The round is over. A proxy that keeps the socket open after
                # [DONE] (or its terminal error frame) must not trip the gap
                # timeout and mask a completed reply as a resumable failure.
                break
    except asyncio.CancelledError:
        raise
    except TimeoutError as exc:
        if not error:
            error = _error_obj(502, {"error": {"message": _stall_message(exc), "fuse_kind": "stalled"}})
    except Exception as exc:
        logger.warning("Upstream stream read failed: %s", type(exc).__name__)
        if not error:
            # A socket read timeout is a silent stream; anything else broke it.
            kind = "stalled" if "Timeout" in type(exc).__name__ else "interrupted"
            error = _error_obj(502, {"error": {"message": f"upstream stream failed ({type(exc).__name__})", "fuse_kind": kind}})
    finally:
        close = getattr(tapped, "aclose", None)
        if close:
            try:
                await close()
            except Exception:
                pass
        extra = getattr(result.stream, "usage", None)
        if isinstance(extra, dict):
            usage = merge_usage(usage, extra)
    if not terminal and not finish and not error:
        error = _error_obj(502, {"error": {"message": "Upstream stream ended without a terminal frame.", "fuse_kind": "interrupted"}})
    if details:
        thinking.append({"reasoning_details": details})
    content, reasoning, native, finish = _finalize_assistant_payload(
        content, reasoning, [calls[i] for i in sorted(calls)], finish, coding=coding
    )
    if not error and not native and (
        str(finish or "").lower() in ("error", "network_error") or _provider_error_content(content)
    ):
        error = _error_obj(503, {"error": {"message": (content or "upstream error").strip()}})
        content = ""
        held_out.clear()
    else:
        for pending in held_out:
            yield ("out", pending)
        held_out.clear()
    yield ("done", {"calls": native, "finish": finish, "content": content, "reasoning": reasoning, "thinking": thinking, "usage": usage, "error": error,
                    "gen": None if error else clock.result(usage)})
