"""Devin (Cascade over Connect-RPC protobuf): Connect frames split across
chunks, tool-call argument assembly, folded usage frames, stop reasons that
are segment boundaries, trailer errors (quota, SKU breaker), the request
encoding (Devin CLI identity, stable cascade id, effort → wire SKU), auth
strikes, and the catalog that collapses effort SKUs into one Fuse id."""
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.sources import devin, devin_catalog, pool_pick, pool_policy
from app.sources import devin_proto as proto
from tests.core.subscriptions._support import account, drain, finish_of, http, reasoning_of, run, text_of, tool_calls_of, user


def msg(text="", *, thinking="", signature="", stop=0, tool=None, usage=None, arg_chunks=None):
    e = proto.ProtoEncoder()
    e.string(1, "m1")
    e.string(3, text)
    if stop:
        e.uint32(5, stop)
    if tool:
        def call(t):
            t.string(1, tool.get("id", ""))
            t.string(2, tool.get("name", ""))
            for piece in arg_chunks or [tool.get("args", "")]:
                t.string(3, piece)
        e.message(6, call)
    if usage:
        e.message(7, lambda u: [u.uint32(k, v) for k, v in usage.items()])
    e.string(9, thinking)
    e.string(10, signature)
    return proto.connect_frame(e.finish())


def trailer(error=None):
    body = json.dumps({"error": error} if error else {}).encode()
    return bytes([proto.CONNECT_END_STREAM]) + len(body).to_bytes(4, "big") + body


def fields(data):
    """Top-level protobuf fields: number -> list of raw bytes / ints."""
    d = proto.ProtoDecoder(data)
    out = {}
    while not d.done:
        num, wire = d.tag()
        out.setdefault(num, []).append(d.raw() if wire == 2 else d._varint() if wire == 0 else d.skip(wire))
    return out


class Upstream:
    """api server: GetUserJwt, an empty catalog (seeds), and a chat stream."""

    def __init__(self, *bodies, auth=200):
        self.bodies = list(bodies)
        self.auth = auth

    def __call__(self, request):
        path = request.url.path
        if path == devin.AUTH_PATH:
            e = proto.ProtoEncoder()
            e.string(1, "user-jwt")
            return httpx.Response(self.auth, content=e.finish())
        if path == devin.MODELS_PATH:
            return httpx.Response(500)
        assert path == devin.CHAT_PATH
        body = self.bodies.pop(0)
        chunks = body if isinstance(body, list) else [body]
        async def stream():
            for chunk in chunks:
                yield chunk
        return httpx.Response(200, content=stream())


@pytest.fixture
def pool(fresh_db):
    owner = user()
    cred = account("devin", owner, {"api_key": "devin-session-token$abc"})
    source = devin.DevinSource({"id": pool_policy.source_id("devin"), "name": "Devin"}, {}, [])
    with patch.object(devin, "refresh_pool_status", new_callable=AsyncMock):
        yield owner, cred, source


def chat(pool, body=None, stream=True, **kwargs):
    async def go():
        owner, _cred, source = pool
        result = await source.chat({"model": "devin/swe-2", "stream": stream,
                                    "messages": [{"role": "user", "content": "hi"}], **(body or {})}, user_id=owner, **kwargs)
        return result, (await drain(result) if result.stream else [])
    return run(go())


def chat_requests(calls):
    return [fields(proto.iter_connect_frames(r.content)[0][0][1]) for r in calls.requests if r.url.path == devin.CHAT_PATH]


def test_frames_split_anywhere_assemble_text_thinking_tools_and_folded_usage(pool, monkeypatch):
    body = b"".join([
        msg(thinking="think", signature="SIG"),
        msg("Hel"),
        # A max_tokens stop followed by more output is a segment boundary.
        msg("lo", stop=proto.STOP_MAX_TOKENS, usage={2: 91, 4: 50, 5: 15616}),
        msg(" world"),
        msg(tool={"id": "t1", "name": "grep"}, arg_chunks=['{"pat', 'tern":']),
        msg(tool={"id": "t1"}, arg_chunks=['"x"}']),
        # A later partial usage frame must not drop the prompt and cache reads.
        msg(usage={3: 42}),
        trailer(),
    ])
    chunks = [body[i:i + 5] for i in range(0, len(body), 5)]
    with http(Upstream(chunks), monkeypatch):
        result, frames = chat(pool)

    assert (text_of(frames), reasoning_of(frames)) == ("Hello world", "think")
    assert tool_calls_of(frames) == [{"id": "t1", "name": "grep", "arguments": '{"pattern":"x"}'}]
    assert finish_of(frames) == ["tool_calls"]
    final = frames[-1]
    assert final["choices"][0]["delta"] == {"devin_signature": "SIG"}
    assert final["usage"]["prompt_tokens"] == 91 + 50 + 15616
    assert final["usage"]["prompt_tokens_details"] == {"cached_tokens": 15616, "cache_write_tokens": 50}
    assert final["usage"]["completion_tokens"] == 42
    assert result.stream.usage["stream_status"] == "complete"


@pytest.mark.parametrize("parts,finish", [
    ([msg("done", stop=proto.STOP_MAX_TOKENS)], "stop"),  # a terminal max_tokens is a normal end on Devin
    ([msg("x"), msg(stop=proto.STOP_FUNCTION_CALL)], "tool_calls"),
    ([msg("x", stop=proto.STOP_FUNCTION_CALL), msg("more")], "stop"),
])
def test_only_a_terminal_function_call_stop_means_tool_calls(pool, monkeypatch, parts, finish):
    with http(Upstream(b"".join(parts) + trailer()), monkeypatch):
        _result, frames = chat(pool)
    assert finish_of(frames) == [finish]


def test_a_stream_without_its_end_frame_is_incomplete_and_never_releases_tools(pool, monkeypatch):
    body = msg(tool={"id": "t1", "name": "run", "args": '{"cmd":"ls"}'}, stop=proto.STOP_FUNCTION_CALL)
    with http(Upstream(body), monkeypatch):
        result, frames = chat(pool)
    assert tool_calls_of(frames) == [] and finish_of(frames) == []
    assert frames[-1]["error"]["message"] == "incomplete Devin Connect response"
    assert result.stream.usage["stream_status"] == "failed"


def test_a_quota_trailer_is_a_429_and_cools_the_account_for_the_daily_window(pool, monkeypatch):
    _owner, cred, _source = pool
    holds = []
    with patch.object(devin, "note_failure", side_effect=lambda cid, **kw: holds.append((cid, kw))):
        with http(Upstream(msg("partial") + trailer({"code": "resource_exhausted", "message": "daily quota used"})), monkeypatch):
            result, frames = chat(pool)
        assert frames[-1]["error"] == {"message": "daily quota used", "type": "resource_exhausted", "code": 429}
        assert holds == [(cred, {"cooldown_seconds": 900, "affinity_safe": False, "limit": True})]

        # Without streaming the same trailer fails over as a 429 with the same hold.
        holds.clear()
        with http(Upstream(trailer({"code": "resource_exhausted", "message": "daily quota used"})), monkeypatch):
            result, _frames = chat(pool, stream=False)
    assert result.status == 429
    assert holds == [(cred, {"cooldown_seconds": 900, "affinity_safe": False, "limit": True})]


def test_a_model_error_trailer_trips_the_sku_breaker_but_an_outage_does_not(pool, monkeypatch):
    with http(Upstream(trailer({"code": "unavailable", "message": "model unavailable right now"}),
                       trailer({"code": "internal", "message": "an internal error occurred (trace ID: 1)"})),
              monkeypatch) as calls:
        chat(pool)
        assert not devin_catalog.sku_dead("swe-2-high")
        pool_pick.reset_health()  # the failed stream cooled the only account
        chat(pool)
        assert devin_catalog.sku_dead("swe-2-high")
        sent = len(calls.requests)
        pool_pick.reset_health()
        result, _frames = chat(pool)
    assert result.status == 503 and "temporarily unavailable" in result.body["error"]["message"]
    assert len(calls.requests) == sent  # fails fast, no pool attempt
    assert "high" not in devin_catalog.efforts_for("devin/swe-2")


def test_the_request_identifies_as_the_cli_with_a_stable_cascade_and_the_effort_sku(pool, monkeypatch):
    body = {"messages": [{"role": "system", "content": "rules"},
                         {"role": "user", "content": [{"type": "text", "text": "see"},
                                                      {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}]},
                         {"role": "assistant", "content": "", "reasoning_content": "thought", "devin_signature": "SIG",
                          "tool_calls": [{"id": "t1", "function": {"name": "grep", "arguments": {"p": 1}}}]},
                         {"role": "tool", "tool_call_id": "t1", "content": "hit"}],
            "reasoning_effort": "low", "prompt_cache_key": "raw"}
    with http(Upstream(msg("a") + trailer(), msg("b") + trailer(), msg("c") + trailer()), monkeypatch) as calls:
        chat(pool, body, session_id="sess")
        chat(pool, body, session_id="sess")
        chat(pool, body, session_id="other")
    first, again, other = chat_requests(calls)

    assert first[21] == [b"swe-2-low"]
    assert first[16] == again[16] != other[16]  # cascade id follows the session
    assert first[2] == [b"rules"]
    meta = fields(first[1][0])
    assert (meta[1], meta[3], meta[21], meta[28]) == ([b"devin"], [b"devin-session-token$abc"], [b"user-jwt"], [b"chisel"])
    prompts = [fields(p) for p in first[3]]
    assert [p[2][0] for p in prompts] == [proto.CHAT_SOURCE_USER, proto.CHAT_SOURCE_SYSTEM, proto.CHAT_SOURCE_TOOL]
    assert fields(prompts[0][10][0])[2] == [b"image/png"]
    assert (prompts[1][11], prompts[1][12]) == ([b"thought"], [b"SIG"])
    assert fields(prompts[1][6][0])[3] == [b'{"p": 1}']
    assert prompts[2][7] == [b"t1"]


def test_remote_images_are_refused_before_any_call(pool, monkeypatch):
    body = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://x/y.png"}}]}]}
    with http(Upstream(), monkeypatch) as calls:
        result, _frames = chat(pool, body)
    assert result.status == 400 and calls.requests == []


def test_only_an_auth_rejection_strikes_the_account(pool, monkeypatch):
    _owner, cred, _source = pool
    with patch.object(devin, "auth_failure_confirmed", return_value=False) as strike:
        with http(Upstream(auth=503), monkeypatch):
            result, _frames = chat(pool)
        assert result.status == 502 and not strike.called  # an outage, not a dead credential
        with http(Upstream(auth=401), monkeypatch):
            result, _frames = chat(pool)
    assert result.status == 502 and strike.call_args.args == (cred,)


def test_the_catalog_collapses_effort_skus_and_keeps_learned_wires_after_a_short_fetch(fresh_db):
    names, labels = devin_catalog.catalog_ids_from_records([
        {"id": "claude-opus-6-medium", "name": "Claude Opus 6 Medium Thinking"},
        {"id": "claude-opus-6-high", "name": "Claude Opus 6 High Thinking"},
        {"id": "claude-opus-6-high-fast", "name": "Claude Opus 6 High Fast"},
        {"id": "glm-5-2", "name": "GLM 5.2", "effort": "High"},
        {"id": "gone-promo", "name": "Promo", "promo_until": 1},
    ])
    devin_catalog._remember_models(names, labels)
    assert names[:2] == ["devin/claude-opus-6", "devin/glm-5-2"] and "devin/gone-promo" not in names
    assert set(devin_catalog.DEVIN_MODELS) <= set(names)
    assert devin_catalog.efforts_for("devin/claude-opus-6") == ("medium", "high")
    assert devin_catalog.resolve_wire_uid("devin/claude-opus-6", "high") == "claude-opus-6-high"  # not the fast variant
    assert devin_catalog.resolve_wire_uid("devin/claude-opus-6", "xhigh") == "claude-opus-6-medium"
    assert devin_catalog.resolve_wire_uid("devin/claude-opus-6-high-fast") == "claude-opus-6-high-fast"
    assert devin_catalog.resolve_wire_uid("devin/glm-5-2", "high") == "glm-5-2"

    later, later_labels = devin_catalog.catalog_ids_from_records([{"id": "swe-2-high", "name": "SWE-2"}])
    merged = devin_catalog._remember_models(later, later_labels)
    assert "devin/claude-opus-6" in merged
    assert devin_catalog.resolve_wire_uid("devin/claude-opus-6", "high") == "claude-opus-6-high"
