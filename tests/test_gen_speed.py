"""tok/s: generation time per model call (consume.GenClock) and its sources."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.chat_generation.consume import GenClock, _add_usage
from app.sources.base import THINKING_MARK


def _usage(out, reasoning=0):
    return {"output_tokens": out, "reasoning_tokens": reasoning}


class GenClockTest(unittest.TestCase):
    def test_window_is_first_to_last_delta_minus_the_first_burst(self):
        clock = GenClock(opened=0.0)
        for t in (1.0, 1.5, 2.0, 3.0):
            clock.frame(t, "abcd", "")
        # 4 of 16 bytes arrived with the first frame, generated in no measured
        # time: their share of the 100 tokens is left out with them.
        self.assertEqual(clock.result(_usage(100)), {"gen_ms": 2000, "gen_out": 75})

    def test_frames_in_one_chunk_share_the_first_burst(self):
        clock = GenClock(opened=0.0)
        clock.frame(1.0, "ab", "")
        clock.frame(1.0, "", "cd")
        clock.frame(2.0, "abcd", "")
        self.assertEqual(clock.result(_usage(80)), {"gen_ms": 1000, "gen_out": 40})

    def test_one_burst_or_a_tiny_window_is_not_a_speed(self):
        burst = GenClock(opened=0.0)
        burst.frame(1.0, "x" * 400, "")
        self.assertIsNone(burst.result(_usage(100)))
        quick = GenClock(opened=0.0)
        quick.frame(1.0, "ab", "")
        quick.frame(1.1, "cd", "")
        self.assertIsNone(quick.result(_usage(100)))

    def test_no_usage_or_no_output_is_not_a_speed(self):
        clock = GenClock(opened=0.0)
        clock.frame(1.0, "ab", "")
        clock.frame(3.0, "cd", "")
        self.assertIsNone(clock.result({}))
        self.assertIsNone(clock.result(_usage(0)))
        self.assertIsNone(GenClock(opened=0.0).result(_usage(50)))

    def test_thinking_mark_opens_the_window_with_no_burst(self):
        clock = GenClock(opened=0.0)
        clock.mark(1.0)
        clock.frame(3.0, "x" * 400, "")
        self.assertEqual(clock.result(_usage(500, reasoning=400)), {"gen_ms": 2000, "gen_out": 500})

    def test_unstreamed_reasoning_is_timed_from_the_stream_opening(self):
        clock = GenClock(opened=0.5)
        clock.frame(2.0, "", "short summary")
        clock.frame(3.0, "answer", "")
        self.assertEqual(clock.result(_usage(900, reasoning=800)), {"gen_ms": 2500, "gen_out": 900, "gen_est": 1})

    def test_streamed_reasoning_is_measured_normally(self):
        clock = GenClock(opened=0.0)
        clock.frame(1.0, "", "t" * 40)
        clock.frame(2.0, "", "t" * 400)
        clock.frame(3.0, "done", "")
        got = clock.result(_usage(120, reasoning=110))
        self.assertNotIn("gen_est", got)
        self.assertEqual(got["gen_ms"], 2000)

    def test_server_timings_win(self):
        clock = GenClock(opened=0.0)
        clock.frame(1.0, "x", "")
        clock.timings = {"predicted_n": 120, "predicted_ms": 2400.4}
        self.assertEqual(clock.result(_usage(999)), {"gen_ms": 2400, "gen_out": 120})
        clock.timings = {"predicted_n": 0, "predicted_ms": 0}
        self.assertIsNone(clock.result(_usage(5)))

    def test_rounds_and_legs_sum(self):
        total = _add_usage({}, {"output_tokens": 10, "gen_ms": 1000, "gen_out": 50})
        total = _add_usage(total, {"gen_ms": 500, "gen_out": 30, "gen_est": 1})
        total = _add_usage(total, None)
        self.assertEqual((total["gen_ms"], total["gen_out"], total["gen_est"]), (1500, 80, 1))


class ConsumeRoundGenTest(unittest.IsolatedAsyncioTestCase):
    async def _consume(self, frames, times):
        from app import chat_runtime as rt

        async def gen():
            for frame in frames:
                yield frame

        result = SimpleNamespace(status=200, body={}, stream=gen(), usage={}, credential_id=None, attempts=[])
        clock = iter(times)
        with patch("app.chat_generation.consume._now", lambda: next(clock)), \
                patch.object(rt.api, "_safe_record_usage", lambda *a, **k: None), \
                patch.object(rt.api, "_tap", side_effect=lambda s, *a, **k: s):
            events = [e async for e in rt._consume_round(result, {"id": 1, "key_id": None},
                                                         SimpleNamespace(source_id=1, type_id="openai"), "m", "m")]
        return events[-1][1]

    @staticmethod
    def _sse(obj):
        return f"data: {json.dumps(obj)}\n\n".encode()

    async def test_streamed_round_reports_its_generation(self):
        done = await self._consume([
            self._sse({"choices": [{"delta": {"role": "assistant", "content": ""}}]}),
            self._sse({"choices": [{"delta": {"content": "abcd"}}]}),
            self._sse({"choices": [{"delta": {"content": "abcd"}}]}),
            self._sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c", "function": {"name": "ab", "arguments": "cd"}}]}}]}),
            self._sse({"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 30}}),
            b"data: [DONE]\n\n",
        ], [0.0, 0.2, 1.0, 2.0, 3.0, 3.5, 9.0])
        # The opening envelope (0.2) and the usage/[DONE] frames are not output:
        # the window is 1.0..3.0 and the first 4 of 12 bytes are its burst.
        self.assertEqual(done["gen"], {"gen_ms": 2000, "gen_out": 20})
        self.assertEqual(done["usage"]["output_tokens"], 30)

    async def test_llama_timings_and_errors(self):
        done = await self._consume([
            self._sse({"choices": [{"delta": {"content": "hi"}}]}),
            self._sse({"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"completion_tokens": 64},
                       "timings": {"predicted_n": 64, "predicted_ms": 812.5}}),
            b"data: [DONE]\n\n",
        ], [0.0, 1.0, 1.1, 1.2])
        self.assertEqual(done["gen"], {"gen_ms": 812, "gen_out": 64})
        failed = await self._consume([
            self._sse({"choices": [{"delta": {"content": "abcd"}}]}),
            self._sse({"choices": [{"delta": {"content": "abcd"}}], "usage": {"completion_tokens": 9}}),
            self._sse({"error": {"message": "boom"}}),
        ], [0.0, 1.0, 3.0, 4.0])
        self.assertIsNone(failed["gen"])

    async def test_thinking_mark_starts_the_window(self):
        done = await self._consume([
            THINKING_MARK + b"\n\n",
            self._sse({"choices": [{"delta": {"content": "x" * 40}, "finish_reason": "stop"}],
                       "usage": {"completion_tokens": 50, "completion_tokens_details": {"reasoning_tokens": 40}}}),
            b"data: [DONE]\n\n",
        ], [0.0, 1.0, 5.0, 5.1])
        self.assertEqual(done["gen"], {"gen_ms": 4000, "gen_out": 50})


class SourceTimingTest(unittest.IsolatedAsyncioTestCase):
    def test_chatgpt_marks_the_start_of_reasoning(self):
        from app.sources.chatgpt_stream import _Translator

        translator = _Translator("gpt", None)
        self.assertEqual(translator._handle({"type": "response.output_item.added", "item": {"type": "reasoning"}}),
                         THINKING_MARK + b"\n\n")
        self.assertIsNone(translator._handle({"type": "response.output_item.added", "item": {"type": "message"}}))

    async def test_ollama_passes_its_decode_timings(self):
        from app.sources.ollama import _stream

        lines = [
            json.dumps({"message": {"content": "hi"}, "done": False}),
            json.dumps({"message": {"content": ""}, "done": True, "prompt_eval_count": 7,
                        "eval_count": 42, "eval_duration": 1_500_000_000}),
        ]

        class Resp:
            async def aiter_lines(self):
                for line in lines:
                    yield line

            async def aclose(self):
                pass

        frames = [chunk async for chunk in _stream(Resp(), "m")]
        usage_frame = next(json.loads(f[6:]) for f in frames if b'"usage"' in f)
        self.assertEqual(usage_frame["timings"], {"predicted_n": 42, "predicted_ms": 1500.0})


class OutputStreamMsTest(unittest.TestCase):
    def test_stream_ms_spans_the_rounds_output_and_resets(self):
        from app import chat_output

        times = iter([0.0, 1.0, 1.0, 2.5, 4.0, 10.0, 10.0, 10.0])
        with patch.object(chat_output, "_now", lambda: next(times)):
            out = chat_output.Output({"message": {}})
            out.apply({"choices": [{"delta": {"content": "abcdefgh"}}]})
            out.apply({"choices": [{"delta": {"content": "abcdefgh"}}]})
            self.assertEqual(out.stream_ms(), 1500)
            out.apply({"fuse": {"event": "activity", "phase": "model"}})
            self.assertEqual(out.stream_ms(), 0)


if __name__ == "__main__":
    unittest.main()
