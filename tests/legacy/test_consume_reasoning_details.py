"""consume_round keeps OpenRouter reasoning_details for replay."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch


def _sse(obj):
    return f"data: {json.dumps(obj)}\n\n".encode()


class ConsumeReasoningDetailsTest(unittest.IsolatedAsyncioTestCase):
    async def _done(self, result):
        from app import chat_runtime as rt

        with patch.object(rt.api, "_safe_record_usage", lambda *a, **k: None), \
                patch.object(rt.api, "_tap", side_effect=lambda s, *a, **k: s):
            events = [e async for e in rt._consume_round(result, {"id": 1, "key_id": None},
                                                         SimpleNamespace(source_id=1, type_id="openrouter"), "m", "m")]
        return events[-1][1]

    async def test_streamed_details_are_merged_into_the_round_thinking(self):
        async def gen():
            for frame in (
                _sse({"choices": [{"delta": {"reasoning": "Let ", "reasoning_details": [
                    {"type": "reasoning.text", "text": "Let ", "index": 0}]}}]}),
                _sse({"choices": [{"delta": {"reasoning": "me.", "reasoning_details": [
                    {"type": "reasoning.text", "text": "me.", "index": 0, "signature": "S"}]}}]}),
                _sse({"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}),
                b"data: [DONE]\n\n",
            ):
                yield frame

        done = await self._done(SimpleNamespace(status=200, body={}, stream=gen(), usage={}, credential_id=None, attempts=[]))
        self.assertEqual(done["reasoning"], "Let me.")
        self.assertEqual(done["thinking"], [{"reasoning_details": [
            {"type": "reasoning.text", "text": "Let me.", "index": 0, "signature": "S"}]}])

    async def test_non_streamed_message_details_are_kept(self):
        details = [{"type": "reasoning.encrypted", "data": "BLOB", "index": 0}]
        body = {"choices": [{"message": {"role": "assistant", "content": "hi", "reasoning_details": details},
                             "finish_reason": "stop"}]}
        done = await self._done(SimpleNamespace(status=200, body=body, stream=None, usage={}, credential_id=None, attempts=[]))
        self.assertEqual(done["thinking"], [{"reasoning_details": details}])
