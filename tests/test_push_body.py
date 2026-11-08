"""A finished agent turn's push previews its final text block, not the first
remark the model made before its tool calls."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import chat_runs as runs, chat_runtime as runtime, chat_store as store, chats, db

pytestmark = pytest.mark.usefixtures("isolated_db")

SEARCH_CALL = {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": '{"query":"x"}'}}


def result(message, finish="stop"):
    return SimpleNamespace(status=200, body={"choices": [{"message": message, "finish_reason": finish}]},
                           usage={"prompt_tokens": 100, "completion_tokens": 5}, stream=None, credential_id=1)


async def fake_tools(calls, **_kw):
    yield {"event": "tool", "id": "ts_0_0", "name": "web_search", "status": "running"}
    yield {"event": "tool", "id": "ts_0_0", "name": "web_search", "status": "done"}
    yield [runtime.ToolJob(call_id=call["id"], name="web_search", arguments=call["function"]["arguments"], text="hits",
                           trace={"name": "web_search", "status": "done"}, orig_index=i, event_id="ts_0_0")
           for i, call in enumerate(calls)]


def test_push_body_is_last_text_block():
    db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'push@test.invalid','unused',?)", (db.now(),))
    thread_id = chats.create_thread(1, model="offline")["id"]
    run = store.start_turn(1, thread_id, "go", chats.get_thread(1, thread_id)["revision"], "cmd-go")["run"]
    calls = []

    async def invoke(request, user, payload, **_kw):
        calls.append(payload)
        if len(calls) == 1:
            reply = result({"content": "Let me search first.", "tool_calls": [SEARCH_CALL]}, "tool_calls")
        else:
            reply = result({"content": "Final answer here."})
        return user, SimpleNamespace(type_id="llama", source_id=1), "offline", "offline", reply

    notify = AsyncMock()

    async def go():
        with patch.object(runtime.api, "invoke_chat", invoke), patch.object(runtime.api, "_safe_record_usage"), \
             patch.object(runtime, "_run_tools", fake_tools), \
             patch.object(runs, "start", AsyncMock()), patch.object(runs, "schedule_wake"), \
             patch("app.push.notify_run_done", new=notify):
            await runs._run(runs.internal_request(1), {"id": 1}, run)
            await asyncio.sleep(0)

    asyncio.run(go())
    assert len(calls) == 2
    content = notify.call_args.kwargs["content"]
    assert "Final answer here." in content
    assert "Let me search first." not in content
