"""OpenAI chat bodies as Anthropic Messages payloads, for API-key providers.

The block conversion is the Claude pool's (claude_payload); its subscription
shaping is not: no Claude Code identity line, no OAuth beta, no catalog
thinking rules. Fuse requests no thinking here, so signed thinking blocks
replayed from another model are dropped. Responses go through
claude_stream's translator (KeyedSource._anthropic).
"""
from __future__ import annotations

from ...sources.claude_payload import _mark_newest_eligible, _tool_choice, convert_messages, convert_tools

_THINKING = ("thinking", "redacted_thinking")
_MAX_OUT = "max_" + "completion" + "_tokens"


def _cache(block: dict) -> None:
    # The default 5-minute TTL: a 1h write costs twice the input price.
    block["cache_control"] = {"type": "ephemeral"}


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def build_payload(body: dict, model: str, *, max_tokens: int = 8192) -> dict:
    system, converted = convert_messages(body)
    messages = []
    for msg in converted:
        if msg["role"] == "assistant":
            content = [block for block in msg["content"] if block.get("type") not in _THINKING]
            if not content:
                continue
            msg = {**msg, "content": content}
        messages.append(msg)
    try:
        limit = int(body.get("max_tokens") or body.get(_MAX_OUT) or max_tokens)
    except (TypeError, ValueError):
        limit = max_tokens
    payload: dict = {"model": model, "max_tokens": max(1, limit), "messages": messages, "stream": True}
    if system:
        payload["system"] = system
        _cache(system[-1])
    tools = convert_tools(body)
    for tool in tools:
        tool.pop("eager_input_streaming", None)
    if tools:
        payload["tools"] = tools
        _cache(tools[-1])
        choice = _tool_choice(body.get("tool_choice"), body.get("parallel_tool_calls"))
        if choice:
            payload["tool_choice"] = choice
    newest = _mark_newest_eligible(messages)
    if newest is not None:
        _cache(newest)
    if _number(body.get("temperature")):
        payload["temperature"] = body["temperature"]
    elif _number(body.get("top_p")):
        payload["top_p"] = body["top_p"]
    stop = body.get("stop")
    stops = [stop] if isinstance(stop, str) else stop if isinstance(stop, list) else []
    stops = [str(s)[:64] for s in stops if isinstance(s, str) and s][:4]
    if stops:
        payload["stop_sequences"] = stops
    return payload
