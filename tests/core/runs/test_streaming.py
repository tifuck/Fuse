"""Streaming parse: deltas split across provider frames land intact in the
stored reply, the reasoning, and the tool calls the executor receives."""
from app import chat_output, chats
from app.chat_generation import parsing
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
