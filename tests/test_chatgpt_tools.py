"""ChatGPT Responses tool mapping fixtures (no network)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-cg-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "chat.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app.sources.chatgpt import build_responses_payload
from app.sources.chatgpt_stream import _aggregate, _translated_stream


class ChatgptToolsTest(unittest.TestCase):
    def test_dictionary_tool_arguments_are_serialized_as_json(self):
        arguments = {"pattern": "café", "n": 1, "enabled": True, "optional": None}
        for raw in (arguments, {}, json.dumps(arguments), None):
            with self.subTest(arguments=raw):
                payload = build_responses_payload({"messages": [
                    {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "c1", "function": {"name": "grep", "arguments": raw}},
                    ]},
                ]}, "gpt-5")
                result = payload["input"][0]["arguments"]
                self.assertEqual(json.loads(result), arguments if raw else {})
                if isinstance(raw, str):
                    self.assertEqual(result, raw)

    def test_function_call_pairing(self):
        payload = build_responses_payload(
            {
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "search this"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_abc",
                                "type": "function",
                                "function": {"name": "web_search", "arguments": '{"query":"x"}'},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_abc", "content": "hits"},
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "web_search", "description": "s", "parameters": {"type": "object"}},
                    }
                ],
                "tool_choice": "none",
                "parallel_tool_calls": True,
            },
            "gpt-5",
            session_id="sess",
        )
        kinds = [i.get("type") or i.get("role") for i in payload["input"]]
        self.assertIn("function_call", kinds)
        self.assertIn("function_call_output", kinds)
        call = next(i for i in payload["input"] if i.get("type") == "function_call")
        out = next(i for i in payload["input"] if i.get("type") == "function_call_output")
        self.assertEqual(call["call_id"], "call_abc")
        self.assertEqual(out["call_id"], "call_abc")
        self.assertEqual(payload.get("tool_choice"), "none")


class _StreamResp:
    def __init__(self, frames):
        self._frames = frames
        self.closed = False

    async def aiter_text(self):
        for frame in self._frames:
            yield frame

    async def aclose(self):
        self.closed = True


class _FakeClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


def _frame(obj: dict) -> str:
    return "data: " + json.dumps(obj) + "\n\n"


class ChatgptStreamEdgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_zero_argument_call_gets_object_arguments(self):
        resp = _StreamResp(
            [
                _frame({"type": "response.created", "response": {"id": "r1"}}),
                _frame(
                    {
                        "type": "response.output_item.added",
                        "item": {"type": "function_call", "id": "i1", "call_id": "call_1", "name": "ping", "arguments": ""},
                    }
                ),
                _frame({"type": "response.function_call_arguments.done", "item_id": "i1", "arguments": ""}),
                _frame({"type": "response.completed", "response": {"id": "r1", "usage": {}}}),
            ]
        )
        body = await _aggregate(resp, _FakeClient(), "gpt-5")
        call = body["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(call["function"]["arguments"], "{}")

    async def _streamed_calls(self, frames):
        """Arguments per call as a chat-completions client accumulates them."""
        chunks = [c async for c in _translated_stream(_StreamResp(frames), _FakeClient(), "gpt-5", {})]
        calls: dict[int, dict] = {}
        for chunk in chunks:
            text = chunk.decode()
            if not text.startswith("data: {"):
                continue
            for choice in json.loads(text[6:]).get("choices") or []:
                for call in (choice.get("delta") or {}).get("tool_calls") or []:
                    rec = calls.setdefault(call["index"], {"name": "", "arguments": ""})
                    fn = call.get("function") or {}
                    rec["name"] += fn.get("name") or ""
                    rec["arguments"] += fn.get("arguments") or ""
        return [calls[i] for i in sorted(calls)]

    @staticmethod
    def _added(n, item_id="", output_index=None):
        event = {"type": "response.output_item.added",
                 "item": {"type": "function_call", "id": item_id, "call_id": f"call_{n}", "name": "grep", "arguments": ""}}
        if output_index is not None:
            event["output_index"] = output_index
        return _frame(event)

    async def test_parallel_calls_keep_arguments_that_arrive_only_when_done(self):
        # Production (Oct 2026): rounds of parallel ChatGPT calls all ran
        # with "{}" while single calls kept their arguments.
        frames = [
            _frame({"type": "response.created", "response": {"id": "r5"}}),
            self._added(1, "fc_1", 0), self._added(2, "fc_2", 1), self._added(3, "fc_3", 2),
            _frame({"type": "response.function_call_arguments.done", "item_id": "fc_1", "output_index": 0, "arguments": '{"pattern":"a"}'}),
            _frame({"type": "response.output_item.done", "output_index": 1,
                    "item": {"type": "function_call", "id": "fc_2", "call_id": "call_2", "name": "grep", "arguments": '{"pattern":"b"}'}}),
            _frame({"type": "response.function_call_arguments.delta", "item_id": "other_3", "output_index": 2, "delta": '{"pattern":'}),
            _frame({"type": "response.function_call_arguments.delta", "item_id": "other_3", "output_index": 2, "delta": '"c"}'}),
            _frame({"type": "response.function_call_arguments.done", "item_id": "other_3", "output_index": 2, "arguments": '{"pattern":"c"}'}),
            _frame({"type": "response.output_item.done", "output_index": 2,
                    "item": {"type": "function_call", "id": "fc_3", "call_id": "call_3", "name": "grep", "arguments": '{"pattern":"c"}'}}),
            _frame({"type": "response.completed", "response": {"id": "r5", "usage": {}}}),
        ]
        calls = await self._streamed_calls(frames)
        self.assertEqual([c["arguments"] for c in calls], ['{"pattern":"a"}', '{"pattern":"b"}', '{"pattern":"c"}'])
        body = await _aggregate(_StreamResp(frames), _FakeClient(), "gpt-5")
        self.assertEqual([c["function"]["arguments"] for c in body["choices"][0]["message"]["tool_calls"]],
                         ['{"pattern":"a"}', '{"pattern":"b"}', '{"pattern":"c"}'])

    async def test_streamed_arguments_are_not_sent_twice(self):
        frames = [
            _frame({"type": "response.created", "response": {"id": "r6"}}),
            self._added(1, "fc_1", 0),
            _frame({"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '{"pattern":'}),
            _frame({"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '"a"}'}),
            _frame({"type": "response.function_call_arguments.done", "item_id": "fc_1", "arguments": '{"pattern":"a"}'}),
            _frame({"type": "response.output_item.done", "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "grep", "arguments": '{"pattern":"a"}'}}),
            _frame({"type": "response.completed", "response": {"id": "r6", "usage": {}}}),
        ]
        self.assertEqual(await self._streamed_calls(frames), [{"name": "grep", "arguments": '{"pattern":"a"}'}])

    async def test_reused_item_id_stays_two_calls(self):
        # A repeated item id must not collapse both calls onto one record:
        # the second add used to overwrite the first while order kept both keys.
        frames = [
            _frame({"type": "response.created", "response": {"id": "r8"}}),
            self._added(1, "fc", 0),
            _frame({"type": "response.function_call_arguments.delta", "item_id": "fc", "output_index": 0, "delta": '{"a":1}'}),
            self._added(2, "fc", 1),
            _frame({"type": "response.function_call_arguments.delta", "item_id": "fc", "output_index": 1, "delta": '{"b":2}'}),
            _frame({"type": "response.output_item.done", "output_index": 0,
                    "item": {"type": "function_call", "id": "fc", "call_id": "call_1", "name": "grep", "arguments": '{"a":1}'}}),
            _frame({"type": "response.completed", "response": {"id": "r8", "usage": {}}}),
        ]
        calls = await self._streamed_calls(frames)
        self.assertEqual(calls, [
            {"name": "grep", "arguments": '{"a":1}'},
            {"name": "grep", "arguments": '{"b":2}'},
        ])
        body = await _aggregate(_StreamResp(frames), _FakeClient(), "gpt-5")
        message_calls = body["choices"][0]["message"]["tool_calls"]
        self.assertEqual(
            [(c["id"], c["function"]["arguments"]) for c in message_calls],
            [("call_1", '{"a":1}'), ("call_2", '{"b":2}')],
        )

    async def test_items_without_ids_stay_separate_calls(self):
        frames = [
            _frame({"type": "response.created", "response": {"id": "r7"}}),
            self._added(1, "", 0), self._added(2, "", 1),
            _frame({"type": "response.function_call_arguments.done", "output_index": 0, "arguments": '{"pattern":"a"}'}),
            _frame({"type": "response.function_call_arguments.done", "output_index": 1, "arguments": '{"pattern":"b"}'}),
            _frame({"type": "response.completed", "response": {"id": "r7", "usage": {}}}),
        ]
        self.assertEqual([c["arguments"] for c in await self._streamed_calls(frames)], ['{"pattern":"a"}', '{"pattern":"b"}'])

    async def test_disconnect_mid_stream_is_cancelled_not_failed(self):
        resp = _StreamResp(
            [
                _frame({"type": "response.created", "response": {"id": "r2"}}),
                _frame({"type": "response.output_text.delta", "delta": "partial"}),
            ]
        )
        collected = {}
        stream = _translated_stream(resp, _FakeClient(), "gpt-5", collected)
        await stream.__anext__()
        await stream.__anext__()
        await stream.aclose()
        # A cancelled stream must not cooldown the credential: only a failed
        # one may (chatgpt._bind_completed_stream).
        self.assertEqual(collected["stream_status"], "cancelled")


ENCRYPTED = {"type": "reasoning", "id": "rs_1", "encrypted_content": "gAAAA-opaque",
             "summary": [{"type": "summary_text", "text": "Plan the search."}]}


class ChatgptReasoningReplayTest(unittest.IsolatedAsyncioTestCase):
    """store=false: encrypted reasoning is captured and sent back, per account."""

    async def test_stream_and_aggregate_capture_encrypted_reasoning_with_account(self):
        frames = [
            _frame({"type": "response.created", "response": {"id": "r3"}}),
            _frame({"type": "response.output_item.done", "item": ENCRYPTED}),
            _frame({"type": "response.output_item.done", "item": {"type": "reasoning", "summary": []}}),
            _frame({"type": "response.completed", "response": {"id": "r3", "usage": {}}}),
        ]
        chunks = [c async for c in _translated_stream(_StreamResp(frames), _FakeClient(), "gpt-5", {}, account=7)]
        blocks = [json.loads(c[5:])["choices"][0]["delta"]["claude_thinking"]
                  for c in chunks if b"claude_thinking" in c]
        want = {"openai_reasoning": {"encrypted_content": "gAAAA-opaque", "account": 7,
                                     "summary": [{"type": "summary_text", "text": "Plan the search."}]}}
        self.assertEqual(blocks, [[want]], "only the item with encrypted content is replayable")
        body = await _aggregate(_StreamResp(frames), _FakeClient(), "gpt-5", account=7)
        self.assertEqual(body["choices"][0]["message"]["claude_thinking"], [want])

    def test_runtime_keeps_blocks_for_chatgpt_only(self):
        from app.chat_generation.parsing import _native_reasoning, _take_thinking

        bucket = []
        _take_thinking(bucket, [{"openai_reasoning": {"encrypted_content": "e", "account": 7, "summary": []}}])
        self.assertEqual(_native_reasoning("visible", "gpt-5.5", bucket),
                         {"claude_thinking": bucket, "_fuse_reasoning_model": "gpt-5.5"})
        self.assertEqual(_native_reasoning("visible", "openrouter/openai/gpt-5", []), {})

    def test_payload_replays_reasoning_before_calls_for_its_account_only(self):
        from app.sources.chatgpt_payload import drop_reasoning, for_account

        block = {"openai_reasoning": {"encrypted_content": "e", "account": 7, "summary": []}}
        payload = build_responses_payload({"messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "claude_thinking": [block],
             "tool_calls": [{"id": "c1", "function": {"name": "web_search", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "hits"},
        ]}, "gpt-5")
        self.assertEqual(payload["include"], ["reasoning.encrypted_content"])
        kinds = [i.get("type") or i.get("role") for i in payload["input"]]
        self.assertEqual(kinds, ["user", "reasoning", "function_call", "function_call_output"])
        same = for_account(payload, 7)
        self.assertEqual(same["input"][1], {"type": "reasoning", "summary": [], "encrypted_content": "e"})
        self.assertNotIn("reasoning", [i.get("type") for i in for_account(payload, 8)["input"]],
                         "another account cannot decrypt it")
        forged = {"input": [{"type": "reasoning", "_fuse_account": "abc", "encrypted_content": "e"}]}
        self.assertEqual(for_account(forged, 7)["input"], [], "a malformed replayed account id is dropped")
        self.assertIn("_fuse_account", payload["input"][1], "the shared payload is not mutated")
        self.assertTrue(drop_reasoning(payload))
        self.assertFalse(drop_reasoning(payload))

    async def test_undecryptable_reasoning_retries_once_without_it(self):
        from unittest.mock import AsyncMock, patch
        from app.sources import chatgpt

        class _ErrResp:
            status_code = 400
            text = json.dumps({"error": {"message": "The encrypted content could not be verified."}})

            async def aread(self):
                return b""

            async def aclose(self):
                pass

        ok = _StreamResp([
            _frame({"type": "response.created", "response": {"id": "r4"}}),
            _frame({"type": "response.output_text.delta", "delta": "done"}),
            _frame({"type": "response.completed", "response": {"id": "r4", "usage": {}}}),
        ])
        ok.status_code = 200
        sent = []

        async def post(token, account_id, payload, client, turn_state=None):
            sent.append(payload)
            return _ErrResp() if len(sent) == 1 else ok

        cred = {"id": 7, "_extra": {"access_token": "t"}}
        source = chatgpt.ChatGPTSource({"id": 1, "name": "ChatGPT"}, {}, [])
        block = {"openai_reasoning": {"encrypted_content": "e", "account": 7, "summary": []}}
        body = {"model": "gpt-5", "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "claude_thinking": [block],
             "tool_calls": [{"id": "c1", "function": {"name": "web_search", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "hits"}]}
        with patch.object(chatgpt, "refresh_due", AsyncMock()), patch.object(chatgpt, "refresh_pool_usage", AsyncMock()), \
             patch.object(chatgpt.db, "fetchall", return_value=[]), \
             patch.object(chatgpt.pool_policy, "filter_credentials", return_value=[cred]), \
             patch.object(source, "_pick", side_effect=lambda *a, **k: (cred, cred["_extra"]) if 7 not in k["exclude"] else None), \
             patch.object(source, "_post", side_effect=post), patch.object(chatgpt, "_note_invoke"), \
             patch.object(chatgpt, "reserve"), patch.object(chatgpt, "release"), \
             patch.object(chatgpt, "_client", return_value=_FakeClient()), patch.object(chatgpt, "bind"), \
             patch.object(chatgpt, "lookup", return_value=None):
            result = await source.chat(body)
        self.assertEqual(result.status, 200)
        self.assertEqual(len(sent), 2)
        self.assertIn("reasoning", [i.get("type") for i in sent[0]["input"]])
        self.assertNotIn("reasoning", [i.get("type") for i in sent[1]["input"]])

    def test_openrouter_fallback_strips_pool_reasoning_blocks(self):
        from app.sources.openrouter import _chat_payload

        body = {"messages": [{"role": "assistant", "content": "x",
                              "claude_thinking": [{"openai_reasoning": {"encrypted_content": "e"}}]}]}
        payload = _chat_payload(body, "openai/gpt-5", free=False)
        self.assertEqual(payload["messages"], [{"role": "assistant", "content": "x"}])
        self.assertIn("claude_thinking", body["messages"][0], "the caller's body is not mutated")


class ChatgptCatalogTraitsTest(unittest.TestCase):
    """Per-model request shape and window from the Codex catalog row."""

    def setUp(self):
        from app.sources import chatgpt_catalog

        self.catalog = chatgpt_catalog
        self.saved = (dict(chatgpt_catalog._windows), set(chatgpt_catalog._all_turns), set(chatgpt_catalog._no_summary))

    def tearDown(self):
        windows, all_turns, no_summary = self.saved
        for live, saved in ((self.catalog._windows, windows), (self.catalog._all_turns, all_turns),
                            (self.catalog._no_summary, no_summary)):
            live.clear()
            live.update(saved)

    def test_catalog_row_sets_reasoning_context_summary_and_window(self):
        from app import context_meter

        self.catalog.parse_catalog_payload({"models": [
            {"slug": "gpt-test-lite", "visibility": "list", "use_responses_lite": True,
             "context_window": 272000, "max_context_window": 872000},
            {"slug": "gpt-test-plain", "visibility": "list", "use_responses_lite": False,
             "supports_reasoning_summary_parameter": False, "max_context_window": 400000},
        ]})
        body = {"messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "high"}
        lite = build_responses_payload(body, "gpt-test-lite-fast")
        self.assertEqual(lite["reasoning"], {"effort": "high", "summary": "auto", "context": "all_turns"})
        plain = build_responses_payload({**body, "reasoning_summary": "detailed"}, "gpt-test-plain")
        self.assertEqual(plain["reasoning"], {"effort": "high"}, "no summary for a row without the parameter")
        self.assertNotIn("reasoning", build_responses_payload({"messages": body["messages"]}, "gpt-test-plain"))
        self.assertEqual(context_meter.window_for("gpt-test-lite", "chatgpt"), 272_000)
        self.assertEqual(context_meter.window_for("gpt-test-plain", "chatgpt"), 400_000)

    def test_rejected_nested_parameter_is_dropped(self):
        from app.sources.chatgpt import _drop_param, _unsupported_param

        payload = {"reasoning": {"effort": "high", "context": "all_turns"}, "top_p": 1}
        param = _unsupported_param({"detail": "Unsupported parameter: reasoning.context"})
        self.assertTrue(_drop_param(payload, param))
        self.assertEqual(payload["reasoning"], {"effort": "high"})
        self.assertFalse(_drop_param(payload, param))
        self.assertTrue(_drop_param(payload, "top_p"))
        self.assertFalse(_drop_param(payload, "missing"))


class ChatgptTurnKeyTest(unittest.TestCase):
    def test_tool_image_items_do_not_start_a_turn(self):
        from app.sources.chatgpt import _turn_key

        human = {"role": "user", "content": [{"type": "input_text", "text": "look at it"}]}
        image = {"role": "user", "content": [
            {"type": "input_text", "text": "[Fuse: untrusted read_file image output, not a human message]\n{}"},
            {"type": "input_image", "image_url": "data:image/png;base64,AA=="}]}
        call = {"type": "function_call", "call_id": "c1", "name": "read_file", "arguments": "{}"}
        out = {"type": "function_call_output", "call_id": "c1", "output": "ok"}
        first = _turn_key("s", {"input": [human, call, out]})
        self.assertEqual(_turn_key("s", {"input": [human, call, out, image]}), first)
        self.assertNotEqual(_turn_key("s", {"input": [human, call, out, image, human]}), first)


class ChatgptResponseShapeTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def _stream(frames, account=None):
        collected = {}
        chunks = [c async for c in _translated_stream(_StreamResp(frames), _FakeClient(), "gpt-5", collected, account=account)]
        deltas, finish = [], None
        for chunk in chunks:
            for line in chunk.decode().split("\n"):
                if not line.startswith("data: {"):
                    continue
                obj = json.loads(line[6:])
                for choice in obj.get("choices") or []:
                    deltas.append(choice.get("delta") or {})
                    finish = choice.get("finish_reason") or finish
                if obj.get("error"):
                    deltas.append({"error": obj["error"]})
        return deltas, finish, collected

    async def test_phase_rides_the_reasoning_blocks_back_to_the_payload(self):
        frames = [
            _frame({"type": "response.created", "response": {"id": "r9"}}),
            _frame({"type": "response.output_item.added", "item": {"type": "reasoning"}}),
            _frame({"type": "response.output_item.done", "item": ENCRYPTED}),
            _frame({"type": "response.output_item.added", "item": {"type": "message", "phase": "commentary"}}),
            _frame({"type": "response.output_text.delta", "delta": "Searching."}),
            _frame({"type": "response.output_item.added",
                    "item": {"type": "function_call", "id": "fc", "call_id": "c1", "name": "web_search", "arguments": "{}"}}),
            _frame({"type": "response.completed", "response": {"id": "r9", "usage": {}}}),
        ]
        deltas, finish, _ = await self._stream(frames, account=7)
        blocks = [b for d in deltas for b in d.get("claude_thinking") or []]
        self.assertEqual([b["openai_reasoning"].get("phase") for b in blocks], ["commentary"])
        self.assertEqual(finish, "tool_calls")
        body = await _aggregate(_StreamResp(frames), _FakeClient(), "gpt-5", account=7)
        message = body["choices"][0]["message"]
        self.assertEqual(message["claude_thinking"], blocks)
        from app.chat_generation.parsing import _native_reasoning, _take_thinking

        kept = []
        _take_thinking(kept, blocks)
        stored = _native_reasoning("", "gpt-5", kept)["claude_thinking"]

        payload = build_responses_payload({"messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": message["content"], "claude_thinking": stored,
             "tool_calls": message["tool_calls"]},
            {"role": "tool", "tool_call_id": "c1", "content": "hits"},
        ]}, "gpt-5")
        replayed = next(i for i in payload["input"] if i.get("role") == "assistant")
        self.assertEqual(replayed["phase"], "commentary")
        reasoning = next(i for i in payload["input"] if i.get("type") == "reasoning")
        self.assertNotIn("phase", reasoning)

    async def test_incomplete_at_the_output_limit_keeps_partial_output(self):
        def frames(reason):
            return [
                _frame({"type": "response.created", "response": {"id": "r10"}}),
                _frame({"type": "response.output_text.delta", "delta": "half an answ"}),
                _frame({"type": "response.incomplete", "response": {
                    "id": "r10", "incomplete_details": {"reason": reason},
                    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}}),
            ]

        for reason, want in (("max_output_tokens", "length"), ("content_filter", "content_filter")):
            with self.subTest(reason=reason):
                deltas, finish, collected = await self._stream(frames(reason))
                self.assertEqual(finish, want)
                self.assertEqual("".join(d.get("content") or "" for d in deltas), "half an answ")
                self.assertFalse(any("error" in d for d in deltas))
                self.assertEqual(collected["stream_status"], "complete")
                body = await _aggregate(_StreamResp(frames(reason)), _FakeClient(), "gpt-5")
                self.assertEqual(body["choices"][0]["finish_reason"], want)
                self.assertEqual(body["choices"][0]["message"]["content"], "half an answ")
                self.assertEqual(body["usage"]["prompt_tokens"], 10)
        deltas, _, collected = await self._stream(frames("invalid_recipient"))
        self.assertTrue(any("error" in d for d in deltas), "other incomplete reasons stay errors")
        self.assertIn("error", await _aggregate(_StreamResp(frames("invalid_recipient")), _FakeClient(), "gpt-5"))

    async def test_refusal_is_content_and_summary_parts_are_paragraphs(self):
        frames = [
            _frame({"type": "response.created", "response": {"id": "r11"}}),
            _frame({"type": "response.reasoning_summary_part.added", "summary_index": 0}),
            _frame({"type": "response.reasoning_summary_text.delta", "delta": "First."}),
            _frame({"type": "response.reasoning_summary_part.added", "summary_index": 1}),
            _frame({"type": "response.reasoning_summary_text.delta", "delta": "Second."}),
            _frame({"type": "response.refusal.delta", "delta": "I can't help with that."}),
            _frame({"type": "response.refusal.done", "refusal": "I can't help with that."}),
            _frame({"type": "response.completed", "response": {"id": "r11", "usage": {}}}),
        ]
        deltas, _, _ = await self._stream(frames)
        self.assertEqual("".join(d.get("reasoning_content") or "" for d in deltas), "First.\n\nSecond.")
        self.assertEqual("".join(d.get("content") or "" for d in deltas), "I can't help with that.")
        message = (await _aggregate(_StreamResp(frames), _FakeClient(), "gpt-5"))["choices"][0]["message"]
        self.assertEqual(message["reasoning_content"], "First.\n\nSecond.")
        self.assertEqual(message["content"], "I can't help with that.")


if __name__ == "__main__":
    unittest.main()
