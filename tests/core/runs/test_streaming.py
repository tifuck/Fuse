"""Streaming parse: deltas split across provider frames land intact in the
stored reply, the reasoning, and the tool calls the executor receives."""
import asyncio
import random
import time

import pytest

from app import chat_output, chat_runtime, chats
from app.chat_generation import parsing, stream
from tests.core.helpers import fake_provider, frame, quick_user, run_async, run_turn, text_frames, tool_call_frames

from ._support import fake_tools, split_frames, tool_thread


def test_content_and_reasoning_split_across_frames_are_stored_whole(fresh_db):
    uid = quick_user()
    frames = split_frames(
        {"reasoning_content": "Let me "},
        {"reasoning_content": "think. 😀"},
        {"content": "Hel"},
        {"content": "lo, "},
        {"content": "wörld 😀"},
    )
    with fake_provider(frames):
        thread_id, run = run_async(run_turn(uid, "hi"))

    assert run["status"] == "completed"
    reply = chats.get_thread(uid, thread_id)["messages"][-1]
    assert reply["content"] == "Hello, wörld 😀"
    assert reply["reasoning"] == "Let me think. 😀"
    # Reasoning is shown before the answer.
    kinds = [s["type"] for s in reply["segments"] if s["type"] in ("reasoning", "content")]
    assert kinds == ["reasoning", "content"]


def test_inline_think_tags_split_mid_tag_become_reasoning_not_content(fresh_db):
    uid = quick_user()
    frames = split_frames({"content": "<thi"}, {"content": "nk>plan it</th"}, {"content": "ink>The answer."})
    with fake_provider(frames):
        thread_id, run = run_async(run_turn(uid, "q"))

    reply = chats.get_thread(uid, thread_id)["messages"][-1]
    assert reply["content"] == "The answer."
    assert "plan it" in reply["reasoning"]
    assert "<think" not in reply["content"] and "think>" not in reply["content"]


def test_tool_call_deltas_split_across_frames_reach_the_executor_intact(fresh_db):
    uid = quick_user()
    thread = tool_thread(uid)
    first = [
        # Two parallel calls, interleaved by index, name and JSON in pieces.
        frame({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_a", "type": "function", "function": {"name": "web_", "arguments": ""}}]}}]}),
        frame({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"name": "search", "arguments": "{\"query\": \"sp"}}]}}]}),
        frame({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 1, "id": "call_b", "type": "function", "function": {"name": "web_search", "arguments": "{\"query\""}}]}}]}),
        frame({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "lit \\\"quoted\\\" ü\"}"}}]}}]}),
        frame({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 1, "function": {"arguments": ": \"second\"}"}}]}}]}),
        frame({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        b"data: [DONE]\n\n",
    ]
    with fake_tools() as tools, fake_provider(first, text_frames("done")) as provider:
        _, run = run_async(run_turn(uid, "go", thread=thread))

    assert run["status"] == "completed"
    assert sorted(tools.calls, key=lambda c: c[1]["query"]) == [
        ("web_search", {"query": "second"}),
        ("web_search", {"query": 'split "quoted" ü'}),
    ]
    sent = next(m for m in provider.bodies[1]["messages"] if m.get("tool_calls"))
    assert [(c["id"], c["function"]["name"]) for c in sent["tool_calls"]] == [("call_a", "web_search"), ("call_b", "web_search")]


def test_cumulative_usage_is_recorded_on_the_reply(fresh_db):
    uid = quick_user()
    first = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    second = {"prompt_tokens": 140, "completion_tokens": 30, "total_tokens": 170}
    tool_round = [*tool_call_frames([("c1", "web_search", {"query": "x"})])[:-1],
                  frame({"choices": [], "usage": first}), b"data: [DONE]\n\n"]
    with fake_tools(), fake_provider(tool_round, text_frames("ok", usage=second)):
        thread_id, run = run_async(run_turn(uid, "count", thread=tool_thread(uid)))

    # The turn's usage sums both rounds; the last round alone is not the total.
    reply = chats.get_thread(uid, thread_id)["messages"][-1]
    assert reply["usage"]["prompt_tokens"] == 240
    assert reply["usage"]["completion_tokens"] == 50


def test_special_tokens_never_reach_the_stored_reply(fresh_db):
    uid = quick_user()
    with fake_provider(split_frames({"content": "Final answer"}, {"content": "<|im_end|>"})):
        thread_id, _ = run_async(run_turn(uid, "q"))

    assert chats.get_thread(uid, thread_id)["messages"][-1]["content"] == "Final answer"


def _stream(*deltas):
    out = chat_output.Output({})
    for delta in deltas:
        out.apply({"choices": [{"delta": delta}]})
    return out.stored(light=True)


def test_think_tags_in_code_or_mid_reply_stay_in_the_reply():
    # Prose that names a tag, even split across frames, is not a thought: one
    # opens only where the round's text starts.
    reply = _stream({"content": "Qwen wraps its thoughts in `<thi"}, {"content": "nk>` tags. Strip them."})
    assert reply["content"] == "Qwen wraps its thoughts in `<think>` tags. Strip them."
    assert reply["reasoning"] == ""
    # Inside a thought, a closing tag quoted in code (its span still open at
    # a frame end) does not end it; the real one does.
    reply = _stream({"content": "<think>the tag is `</thi"}, {"content": "nk"}, {"content": ">` here\n</think>Answer."})
    assert reply["reasoning"] == "the tag is `</think>` here\n"
    assert reply["content"] == "Answer."
    # A bare close (the template opened the thought in the prompt) is dropped;
    # one quoted in code, its span still open at a frame end, stays.
    reply = _stream({"content": "plan</thi"}, {"content": "nk>Use `</think"}, {"content": ">` to end it."})
    assert reply["content"] == "planUse `</think>` to end it."
    assert reply["reasoning"] == ""


def test_reasoning_sent_under_two_keys_is_kept_once():
    reply = _stream({"reasoning_content": "plan", "reasoning": "plan"}, {"content": "ok"})
    assert reply["reasoning"] == "plan"


def test_named_tool_call_deltas_append_fragments_that_parse_alone():
    calls = {}
    for fragment in ('{"a": ', "{}", "}"):
        parsing._take_calls(calls, [{"index": 0, "function": {"name": "web_search", "arguments": fragment}}])
    assert calls[0]["function"]["arguments"] == '{"a": {}}'
    # A named delta that resends the whole arguments so far replaces them.
    calls = {}
    for fragment in ('{"a": ', '{"a": 1}'):
        parsing._take_calls(calls, [{"index": 0, "function": {"name": "web_search", "arguments": fragment}}])
    assert calls[0]["function"]["arguments"] == '{"a": 1}'


def test_text_tool_call_is_held_from_its_first_unclosed_opener():
    visible, held = parsing.drop_tool_xml('hi <tool_call>{"name": "a" <tool_call>{"name"')
    assert (visible, held) == ("hi ", '<tool_call>{"name": "a" <tool_call>{"name"')


def test_a_long_text_tool_call_is_dropped_once_closed_and_text_after_it_shown():
    call = '<tool_call>{"name": "read_file", "arguments": {"path": "' + "a" * 3000 + '"}}</tool_call>'
    text = "Before " + call + " after `<tool_c" + "all>` quoted."
    reply = _stream(*({"content": text[at:at + 16]} for at in range(0, len(text), 16)))
    assert reply["content"] == "Before  after `<tool_call>` quoted."


def test_a_held_tool_call_past_the_cap_is_shown_as_written(monkeypatch):
    monkeypatch.setattr(chat_output, "_XML_HELD_MAX", 1_000)
    text = "Hi <tool_call>" + "x" * 2_000
    out = chat_output.Output({})
    for at in range(0, len(text), 16):
        out.apply({"choices": [{"delta": {"content": text[at:at + 16]}}]})
    assert out.stored(light=True)["content"].startswith(text[:1_000])
    out.apply({"choices": [{"delta": {"content": "</tool_call> done"}}]})
    assert out.stored(light=True)["content"] == text + "</tool_call> done"


def cpu_cost(work):
    best = float("inf")
    for _ in range(3):
        started = time.thread_time()
        work()
        best = min(best, time.thread_time() - started)
    return best


@pytest.mark.parametrize("body", [
    "x",
    "<function=write_file><parameter=content>\n<p>a</p>\n",
    "y</tool_call>",
    "<tool_call>",
    "<function=a>",
])
def test_streaming_and_parsing_a_long_text_tool_call_stay_linear(body):
    # The held tail was rescanned on every chunk (40 KB took 2.5 s) and
    # extract_text_tool_calls retried from every opener. Only the ratio is
    # asserted: 8x the input costs ~8x when linear, ~64x when quadratic.
    def stream(n):
        text = "Hi.\n<tool_call>" + body * (n // len(body))
        return lambda: _stream(*({"content": text[at:at + 16]} for at in range(0, len(text), 16)))

    def parse(n):
        text = "Hi.\n<tool_call>" + body * (n // len(body))
        return lambda: (parsing.extract_text_tool_calls(text), parsing.drop_tool_xml(text))

    for make in (stream, parse):
        small, large = cpu_cost(make(12_500)), cpu_cost(make(100_000))
        assert large < 24 * max(small, 0.002), f"8x input cost {large / max(small, 1e-9):.1f}x"


@pytest.mark.parametrize("head, unit, step", [
    ("", "abc</think>", 16),
    ("", "abc</think>", 10**9),
    ("`", "abc</think>", 16),
    ("<think>```\n", "abc</think>", 16),
    ("<think>```\n", "abc</think>", 10**9),
    ("", 'a `b` <tool_call>{"name": "x"}</tool_call>', 16),
])
def test_many_think_tags_and_tool_calls_stay_linear(head, unit, step):
    # Each closing tag, and each chunk holding a tool call, rescanned the
    # whole reply for code spans (40 KB of abc</think> took 0.6 s streamed,
    # 2.7 s in one delta). Only the ratio is asserted: 8x the input costs ~8x
    # when linear, ~64x when quadratic.
    def stream(n):
        text = head + unit * (n // len(unit))
        return lambda: _stream(*({"content": text[at:at + step]} for at in range(0, len(text), step)))

    small, large = cpu_cost(stream(5_000)), cpu_cost(stream(40_000))
    assert large < 24 * max(small, 0.002), f"8x input cost {large / max(small, 1e-9):.1f}x"


_CODE_PIECES = ["`", "``", "```", "~~~", "~", " ", "  ", "    ", "\t", "\n", "a", "b c", "<", "<x>"]


def test_code_lookups_kept_across_edits_match_a_full_rescan():
    # MdCode answers in_md_code from what it scanned before; between lookups
    # the text grows, loses a piece (a dropped tag or tool call) or is cut.
    rng = random.Random(5)
    for _ in range(400):
        code, text = parsing.MdCode(), ""
        for _ in range(rng.randrange(1, 25)):
            step = rng.random()
            if step < 0.6 or not text:
                text += "".join(rng.choice(_CODE_PIECES) for _ in range(rng.randrange(1, 6)))
            elif step < 0.8:
                at = rng.randrange(len(text) + 1)
                text = text[:at] + text[at + rng.randrange(1, 4):]
                if rng.random() < 0.5:
                    code.forget(at)
            else:
                text = text[:rng.randrange(len(text) + 1)]
            points = [at for at, char in enumerate(text) if char not in "`\n"]
            points = rng.sample(points, min(len(points), 4))
            if rng.random() < 0.7:
                points.sort()
            for at in points:
                assert code.quoted(text, at) == parsing.in_md_code(text, at), (text, at)


class _Rescan:
    """The reference: every lookup rescans the whole text."""

    def quoted(self, text, at):
        return parsing.in_md_code(text, at)

    def forget(self, at):
        pass


_STREAM_PIECES = [
    "<think>", "</think>", "</thinking>", "`", "``", "```\n", "```", "~~~\n", "\n", "abc", " ", "<", "</th", "ink>",
    "<tool_call>", "</tool_call>", '{"name": "web_search", "arguments": {"query": "x"}}', "<function=a>", "</function>",
    "<|im_end|>", "<|e", "os|>", "<|end_of_text|>", "</s>",
]


def _reply(chunks):
    out = chat_output.Output({})
    for chunk in chunks:
        out.apply({"choices": [{"delta": {"content": chunk}}]})
    out.settle_tool_xml()
    reply = out.stored(light=True)
    return reply["content"], reply["reasoning"], [(part["type"], part.get("text")) for part in reply["segments"]]


def test_streamed_replies_match_a_full_rescan_on_every_chunk():
    # Think tags, tool calls, code and special tokens in random chunks: the
    # kept code spans and the held tail stripped only where it is new give
    # what rescanning everything on every chunk gave.
    rng = random.Random(7)
    streams = []
    for _ in range(300):
        text = "".join(rng.choice(_STREAM_PIECES) for _ in range(rng.randrange(1, 60)))
        cuts = sorted(rng.sample(range(1, len(text)), min(len(text) - 1, rng.randrange(20))))
        streams.append([text[lo:hi] for lo, hi in zip([0, *cuts], [*cuts, len(text)])])
    got = [_reply(chunks) for chunks in streams]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(chat_output, "MdCode", _Rescan)
        patch.setattr(chat_runtime, "strip_special_tokens", lambda text, clean=0: parsing.strip_special_tokens(text))
        want = [_reply(chunks) for chunks in streams]
    for chunks, reply, expected in zip(streams, got, want):
        assert reply == expected, chunks

class _HeldRead:
    """A provider stream whose pending read takes a while to unwind once cancelled."""

    def __init__(self):
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            await self.release.wait()
            raise


def test_a_stop_while_the_stream_read_unwinds_still_cancels_the_run():
    async def body():
        held = _HeldRead()

        async def consume():
            paced = stream._paced_chunks(held, 60, 60, 0.01)
            async for kind, _ in paced:
                assert kind == "beat"
                break
            await paced.aclose()
            return "carried on"

        task = asyncio.create_task(consume())
        await asyncio.wait_for(held.cancelled.wait(), 5)
        task.cancel()  # Stop lands while the cleanup waits on the read
        held.release.set()
        (outcome,) = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(outcome, asyncio.CancelledError)

    asyncio.run(body())
