"""Streaming parse: deltas split across provider frames land intact in the
stored reply, the reasoning, and the tool calls the executor receives."""
from app import chats
from tests.core.helpers import fake_provider, frame, quick_user, run_async, run_turn, text_frames

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
    usage = {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}
    with fake_provider(text_frames("ok", usage=usage)):
        thread_id, run = run_async(run_turn(uid, "count"))

    reply = chats.get_thread(uid, thread_id)["messages"][-1]
    assert reply["usage"]["prompt_tokens"] == 120
    assert reply["usage"]["completion_tokens"] == 30


def test_special_tokens_never_reach_the_stored_reply(fresh_db):
    uid = quick_user()
    with fake_provider(split_frames({"content": "Final answer"}, {"content": "<|im_end|>"})):
        thread_id, _ = run_async(run_turn(uid, "q"))

    assert chats.get_thread(uid, thread_id)["messages"][-1]["content"] == "Final answer"
