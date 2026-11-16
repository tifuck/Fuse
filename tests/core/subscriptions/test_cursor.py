"""Cursor (cursor-agent RunSSE + BidiAppend): one run spans a user turn, tool
calls park it and the next request's results go back as mcp_result; a
finished turn continues from its checkpoint; anything that does not match
the kept state replays history as root prompt blobs. Plus trailer status
mapping, the Free-plan gate, 401 refresh, the catalog and the client version."""
import asyncio
import base64
import hashlib
import json
import struct
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import settings
from app.sources import cursor, cursor_agent as agent, pool_pick, pool_policy
from app.sources.devin_proto import ProtoDecoder, ProtoEncoder
from tests.core.helpers import wait_until_async
from tests.core.subscriptions._support import _RealClient, extra_of, http, jwt, row, run, user


def _wrap(field, payload):
    e = ProtoEncoder()
    e.blob(field, payload)
    return e.finish()


def _enc(build):
    e = ProtoEncoder()
    build(e)
    return e.finish()


def _frame(payload, flag=0):
    return bytes([flag]) + struct.pack(">I", len(payload)) + payload


def _update(field, inner):
    return _frame(_wrap(1, _wrap(field, inner)))


def _text(field, text):
    return _enc(lambda e: e.string(field, text))


def _turn_end(**usage):
    keys = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")
    return _update(14, _enc(lambda e: [e.uint64(i + 1, usage[k]) for i, k in enumerate(keys) if k in usage]))


def _top_field(payload):
    return ProtoDecoder(payload).tag()[0]


def routed(monkeypatch, handler):
    monkeypatch.setattr(agent.httpx, "AsyncClient",
                        lambda **kw: _RealClient(transport=httpx.MockTransport(handler), **kw))


def test_a_run_parks_on_a_tool_call_and_resumes_with_the_result_as_mcp_result(monkeypatch):
    appends: asyncio.Queue = asyncio.Queue()
    seen = {"seq": [], "headers": {}}
    blob = json.dumps({"role": "user", "content": [{"type": "text", "text": "sys"}]}, separators=(",", ":")).encode()
    blob_id = hashlib.sha256(blob).digest()
    mcp_call = _enc(lambda e: (e.string(1, "mcp_fuse_get_weather"),
                               e.blob(2, _enc(lambda p: (p.string(1, "city"), p.blob(2, b"\x1a\x05Osaka")))),
                               e.string(3, "call-1\nfc_2"), e.string(5, "get_weather")))

    async def server():
        assert _top_field(await appends.get()) == 1  # the run request
        yield _frame(_wrap(4, _enc(lambda e: (e.uint32(1, 4), e.blob(2, _wrap(1, blob_id))))))  # get blob
        got = await appends.get()
        assert _top_field(got) == 3 and blob in got
        yield _frame(_wrap(2, _enc(lambda e: (e.uint32(1, 1), e.string(15, "x1"), e.blob(10, b"")))))  # request context
        assert b"get_weather" in await appends.get()
        yield _frame(_wrap(2, _enc(lambda e: (e.uint32(1, 3), e.string(15, "x3"), e.blob(36, b"")))))  # mcp state
        assert b"get_weather" in await appends.get()
        yield _update(4, _text(1, "hmm"))
        yield _update(1, _text(1, "Checking"))
        yield _update(8, _enc(lambda e: e.uint32(1, 7)))
        yield _frame(_wrap(3, _enc(lambda e: (e.blob(5, _enc(lambda t: t.uint32(1, 107))), e.blob(8, b"turn")))))
        yield _frame(_wrap(2, _enc(lambda e: (e.uint32(1, 2), e.string(15, "x2"), e.blob(11, mcp_call)))))
        result = await appends.get()  # the caller's tool result, into the same run
        assert _top_field(result) == 2 and b"sunny" in result
        yield _update(1, _text(1, "Sunny."))
        yield _turn_end(input_tokens=300, output_tokens=20, cache_read_tokens=150, cache_write_tokens=20)
        yield _frame(b"{}", 0x02)

    def handler(request):
        if request.url.path.endswith("/RunSSE"):
            seen["headers"] = dict(request.headers)
            return httpx.Response(200, content=server())
        body = json.loads(request.content)
        seen["seq"].append(body["appendSeqno"])
        appends.put_nowait(base64.b64decode(body["dataBinary"]))
        return httpx.Response(200, json={})

    routed(monkeypatch, handler)
    monkeypatch.setattr(agent, "TOOL_GRACE_S", 0.05)
    monkeypatch.setattr(agent, "HEARTBEAT_S", 60)
    tools = agent.mcp_tools([{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}])

    async def go():
        r = agent.CursorRun(base="https://api2.cursor.sh", headers=cursor._headers("tok"), timeout=httpx.Timeout(5.0),
                            blobs={blob_id: blob}, tools=tools)
        await r.start(agent.encode_run_request(state=agent.encode_state([blob_id]), user_text="weather?", model_id="default",
                                               params=[], max_mode=False, display_name="Auto", tools=tools, conversation_id="c-1"))
        first = [e async for e in r.round()]
        parked = not r.closed
        await r.resume([("call-1_fc_2", "sunny", [])])
        second = [e async for e in r.round()]
        await r.aclose()
        return r, first, parked, second

    r, first, parked, second = run(asyncio.wait_for(go(), 10))
    assert first[:2] == [("thinking", "hmm"), ("text", "Checking")]
    kind, call = first[2]
    assert (kind, call["id"], call["name"], json.loads(call["arguments"])) == ("tool_call", "call-1_fc_2", "get_weather", {"city": "Osaka"})
    assert first[3] == ("usage", {"prompt_tokens": 100, "completion_tokens": 7, "total_tokens": 107,
                                  "prompt_tokens_details": {"cached_tokens": 0}})
    assert first[4] == ("end", "tool_calls") and parked
    assert second[0] == ("text", "Sunny.")
    usage = second[1][1]
    assert (usage["prompt_tokens"], usage["completion_tokens"]) == (200, 13)
    assert usage["prompt_tokens_details"] == {"cached_tokens": 150, "cache_write_tokens": 20}
    assert second[2] == ("end", "stop") and r.ended and r.checkpoint
    assert seen["headers"]["content-type"] == "application/connect+proto"
    assert "get_mcp_tools_tool_call" in seen["headers"]["x-cursor-agent-allowed-tools"]
    assert seen["seq"][:4] == ["0", "1", "2", "3"]


@pytest.mark.parametrize("frames,status", [
    ([_frame(json.dumps({"error": {"code": "resource_exhausted", "message": "usage limit"}}).encode(), 0x02)], 429),
    ([_update(1, _text(1, "partial")), _frame(b"\x0a\xff"), _frame(b"{}", 0x02)], 502),  # an undecodable frame
])
def test_trailer_errors_and_bad_frames_fail_the_turn_with_a_status(monkeypatch, frames, status):
    async def server():
        for f in frames:
            yield f

    routed(monkeypatch, lambda request: httpx.Response(200, content=server()) if request.url.path.endswith("/RunSSE")
           else httpx.Response(200, json={}))

    async def go():
        r = agent.CursorRun(base="https://x", headers={}, timeout=httpx.Timeout(5.0), blobs={}, tools=[])
        await r.start(b"\x0a\x00")
        try:
            async for _ in r.round():
                pass
        except agent.TurnError as exc:
            return exc.status
        finally:
            await r.aclose()

    assert run(asyncio.wait_for(go(), 10)) == status


class FakeRun:
    """Stands in for agent.CursorRun: a tool round, then the turn's end."""

    fail: list = []
    instances: list = []

    def __init__(self, **kw):
        self.kw = kw
        self.pending = {}
        self.ended = self.closed = False
        self.checkpoint = None
        self.used_tokens = 0
        self.started = self.resumed = None
        FakeRun.instances.append(self)

    async def start(self, request):
        if FakeRun.fail:
            item = FakeRun.fail.pop(0)
            raise agent.TurnError(*item) if isinstance(item, tuple) else agent.TurnError(item, "refused")
        self.started = request

    async def round(self):
        if self.resumed is None and b"next" not in (self.started or b""):
            self.pending = {"c1": (1, "x1")}
            yield ("text", "Hi")
            yield ("tool_call", {"id": "c1", "name": "read_file", "arguments": "{}"})
            yield ("usage", {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12})
            yield ("end", "tool_calls")
            return
        self.ended, self.checkpoint, self.used_tokens = True, b"CHECKPOINT", 120
        yield ("text", "Done")
        yield ("usage", {"prompt_tokens": 20, "completion_tokens": 2, "total_tokens": 22})
        yield ("end", "stop")

    async def resume(self, results):
        self.resumed, self.pending = results, {}

    def park(self):
        pass

    async def aclose(self):
        self.closed = True


CALL = {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
TAIL = "\n\n[Fuse session context — server-added, not user input. Current date: 2026-11-16 UTC.]"


@pytest.fixture
def pool(fresh_db, monkeypatch):
    FakeRun.instances, FakeRun.fail = [], []
    owner = user()
    sid = pool_policy.source_id("cursor")
    cred = cursor.store_tokens(sid, jwt({"sub": "auth0|user_1"}), "refresh-token-1234", owner)
    monkeypatch.setattr(agent, "CursorRun", FakeRun)
    monkeypatch.setattr(cursor, "refresh_due", AsyncMock())
    monkeypatch.setattr(cursor, "refresh_pool_quota", AsyncMock())
    source = cursor.CursorSource({"id": sid, "name": "Cursor"}, {}, [])
    return owner, cred, source


async def chat(pool, messages, stream=True, model="auto"):
    owner, _cred, source = pool
    result = await source.chat({"model": model, "stream": stream, "messages": messages,
                                "tools": [{"type": "function", "function": {"name": "read_file"}}]}, user_id=owner)
    if result.stream is not None:
        body = b"".join([c async for c in result.stream]).decode()
        return result, [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {")]
    return result, result.body


def test_tool_rounds_stay_in_one_run_and_the_next_turn_continues_the_checkpoint(pool):
    system = {"role": "system", "content": "sys"}
    turn1 = [system, {"role": "user", "content": "hi" + TAIL}]
    round2 = turn1 + [{"role": "assistant", "content": "Hi", "tool_calls": [CALL]},
                      {"role": "tool", "tool_call_id": "c1", "content": "file data"}]
    # The stored history drops the session tail; the follow-up still matches.
    turn2 = [system, {"role": "user", "content": "hi"}, *round2[2:], {"role": "assistant", "content": "Done"},
             {"role": "user", "content": "next" + TAIL}]

    async def go():
        result, frames = await chat(pool, turn1)
        assert frames[-1]["choices"][0]["finish_reason"] == "tool_calls"
        assert result.stream.usage["prompt_tokens"] == 9
        await chat(pool, round2)
        assert len(FakeRun.instances) == 1 and FakeRun.instances[0].resumed == [("c1", "file data", [])]
        result, body = await chat(pool, turn2, stream=False)
        assert body["choices"][0]["message"]["content"] == "Done"

    run(go())
    assert len(FakeRun.instances) == 2
    follow = FakeRun.instances[1]
    assert b"CHECKPOINT" in follow.started and follow.kw["sent_tokens"] == 120
    assert list(agent._conversations)[0].encode() in follow.started
    assert b"Configuration for this session" not in follow.started
    # The first turn replayed its history as root prompt blobs.
    assert any(agent.SYSTEM_LEAD.encode() in blob for blob in FakeRun.instances[0].kw["blobs"].values())


def test_a_duplicate_request_cannot_resume_the_same_parked_run(pool):
    turn1 = [{"role": "user", "content": "hi"}]
    round2 = turn1 + [{"role": "assistant", "content": "Hi", "tool_calls": [CALL]},
                      {"role": "tool", "tool_call_id": "c1", "content": "file data"}]

    async def go():
        await chat(pool, turn1)
        await asyncio.gather(chat(pool, round2), chat(pool, round2))

    run(go())
    resumed = [r for r in FakeRun.instances if r.resumed is not None]
    assert len(resumed) == 1 and len(FakeRun.instances) == 2  # the duplicate replayed on a run of its own
    assert not any(conv.claimed for conv in agent._conversations.values())


def test_a_steer_between_tool_results_replays_and_closes_the_parked_run(pool):
    steered = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hi", "tool_calls": [CALL]},
               {"role": "tool", "tool_call_id": "c1", "content": "x"}, {"role": "user", "content": "stop"}]

    async def go():
        await chat(pool, steered[:1])
        await chat(pool, steered)
        await wait_until_async(lambda: FakeRun.instances[0].closed)

    run(go())
    assert len(FakeRun.instances) == 2 and FakeRun.instances[1].resumed is None
    assert b"stop" in FakeRun.instances[1].started


def test_a_401_refreshes_once_then_the_run_starts_on_the_same_account(pool):
    _owner, cred, _source = pool
    FakeRun.fail = [401]
    with patch.object(cursor, "refresh_one", new_callable=AsyncMock, return_value=True) as refresh:
        result, _frames = run(chat(pool, [{"role": "user", "content": "hi"}]))
    assert (result.status, result.credential_id, refresh.await_count) == (200, cred, 1)
    assert len(FakeRun.instances) == 2


def test_the_free_plan_gate_is_a_403_without_cooldown_and_free_users_list_only_auto(pool):
    owner, cred, _source = pool
    extra = extra_of(cred)
    extra["billing"] = {"plan_type": "free"}
    cursor.accounts.update_extra(cred, extra)
    assert cursor.listable_models(["cursor/auto", "cursor/grok-4.7"], owner) == ["cursor/auto"]

    FakeRun.fail = [(429, "Free plans can only use Auto. Switch to Auto or upgrade plans to continue.")]
    denied, body = run(chat(pool, [{"role": "user", "content": "hi"}], model="cursor/grok-4.7"))
    assert denied.status == 403 and "paid plan" in json.dumps(body)
    assert not pool_pick.cooling_down(cred)
    result, _frames = run(chat(pool, [{"role": "user", "content": "hi"}]))
    assert result.status == 200


OPUS = {"name": "claude-opus-5", "clientDisplayName": "Opus 5", "contextTokenLimit": 200000,
        "contextTokenLimitForMaxMode": 1000000, "variants": [
            {"parameterValues": [{"id": "thinking", "value": "true"}, {"id": "effort", "value": "high"}], "isDefaultNonMaxConfig": True},
            {"parameterValues": [{"id": "thinking", "value": "true"}, {"id": "effort", "value": "low"}]},
            {"parameterValues": [{"id": "thinking", "value": "false"}]},
            {"parameterValues": [{"id": "thinking", "value": "true"}, {"id": "effort", "value": "high"}], "isMaxMode": True,
             "isDefaultMaxConfig": True}]}
GROK = {"name": "grok-4.6", "clientDisplayName": "Grok 4.6", "variants": [
    {"parameterValues": [{"id": "effort", "value": "high"}, {"id": "fast", "value": "true"}], "isDefaultNonMaxConfig": True},
    {"parameterValues": [{"id": "effort", "value": "low"}, {"id": "fast", "value": "true"}]},
    {"parameterValues": [{"id": "effort", "value": "high"}, {"id": "fast", "value": "false"}]}]}


def test_the_catalog_folds_efforts_and_splits_fast_and_max_variants(fresh_db):
    rows = {r["id"]: r for r in cursor.parse_available_models([OPUS, GROK, {"name": "hidden", "isHidden": True},
                                                                 {"name": "default", "clientDisplayName": "Auto"}])}
    assert set(rows) == {"cursor/claude-opus-5", "cursor/claude-opus-5:max", "cursor/grok-4.6", "cursor/grok-4.6:slow", "cursor/auto"}
    cursor._remember_catalog(list(rows.values()))
    assert (cursor.efforts_for("cursor/claude-opus-5"), cursor.default_effort("cursor/claude-opus-5")) == (("none", "low", "high"), "high")
    assert cursor.model_window("cursor/claude-opus-5:max") == 1000000
    off = cursor.resolve("claude-opus-5", "none")
    assert (off["model_id"], off["params"], off["max"]) == ("claude-opus-5", [("thinking", "false")], False)
    maxed = cursor.resolve("claude-opus-5:max", "xhigh")
    assert maxed["max"] and maxed["slug"] == "claude-opus-5-high-max"
    assert cursor.resolve("grok-4.6:slow", None)["params"] == [("effort", "high"), ("fast", "false")]
    assert cursor.resolve("grok-4.6", "low")["slug"] == "grok-4.6-low-fast"
    assert cursor.resolve("cursor/auto", None)["model_id"] == "default"


def test_the_client_version_follows_the_installer_and_retries_hourly(fresh_db, monkeypatch):
    script = 'DOWNLOAD_URL="https://downloads.cursor.com/lab/2026.12.01-abc1234/${OS}/${ARCH}/agent-cli-package.tar.gz"'
    monkeypatch.setattr(cursor, "_client_version", None)
    monkeypatch.setattr(cursor, "_version_check", 0.0)
    monkeypatch.setattr(settings, "cursor_client_version_auto_refresh", True)
    with http([httpx.Response(200, text="no build here"), httpx.Response(200, text=script)], monkeypatch) as calls:
        run(cursor.refresh_client_version())
        assert cursor.client_version() == settings.cursor_client_version
        run(cursor.refresh_client_version())  # not again within the hour
        assert len(calls.requests) == 1
        monkeypatch.setattr(cursor, "_version_check", time.time() - 1)
        run(cursor.refresh_client_version())
    assert cursor.client_version() == "cli-2026.12.01-abc1234"
    assert cursor._headers("t")["x-cursor-client-version"] == "cli-2026.12.01-abc1234"


def test_a_relink_updates_the_same_row(fresh_db):
    owner = user()
    sid = pool_policy.source_id("cursor")
    token = jwt({"sub": "auth0|user_1"})
    first = cursor.store_tokens(sid, token, "refresh-token-1234", owner)
    again = cursor.store_tokens(sid, token, "refresh-token-5678", owner)
    assert first == again and extra_of(first)["refresh_token"] == "refresh-token-5678"
    assert cursor.user_has_cursor(owner) and row(first)["disabled"] == 0
