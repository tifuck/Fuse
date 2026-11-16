"""Tool rounds through the real generation loop: the model's call reaches the
executor intact, its result goes back to the model, the final answer lands,
and the retained transcript replays the round on the next turn."""
from app import chats
from tests.core.helpers import fake_provider, frame, make_user, run_async, run_turn, text_frames, tool_call_frames

from ._support import fake_tools, provider_messages, tool_thread


def _tool_results(body):
    return [m for m in body["messages"] if m.get("role") == "tool"]


def test_tool_result_is_fed_back_and_the_final_answer_completes_the_turn(fresh_db):
    uid = make_user()
    thread = tool_thread(uid)
    with fake_tools(lambda name, args: f"found: {args['query']}") as tools, fake_provider(
        tool_call_frames([("call_1", "web_search", {"query": "fuse docs"})]),
        text_frames("The docs say hello."),
    ) as provider:
        thread_id, run = run_async(run_turn(uid, "look it up", thread=thread))

    assert run["status"] == "completed"
    assert tools.calls == [("web_search", {"query": "fuse docs"})]
    assert len(provider.bodies) == 2
    # Second request: the assistant's call, then exactly one result for it.
    second = provider.bodies[1]["messages"]
    calls = [m for m in second if m.get("role") == "assistant" and m.get("tool_calls")]
    assert [c["id"] for c in calls[-1]["tool_calls"]] == ["call_1"]
    results = _tool_results(provider.bodies[1])
    assert [(r["tool_call_id"], "found: fuse docs" in r["content"]) for r in results] == [("call_1", True)]

    reply = chats.get_thread(uid, thread_id)["messages"][-1]
    assert reply["content"] == "The docs say hello."
    assert [(t["name"], t["status"]) for t in reply["tools"]] == [("web_search", "done")]
    # The retained transcript holds the whole round for replay.
    saved = provider_messages(run["id"])
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "call_1" for m in saved)


def test_next_turn_replays_the_tool_round_verbatim(fresh_db):
    uid = make_user()
    thread = tool_thread(uid)
    with fake_tools(lambda name, args: "RESULT-123"), fake_provider(
        tool_call_frames([("call_a", "web_search", {"query": "q"})]),
        text_frames("first answer"),
        text_frames("second answer"),
    ) as provider:
        thread_id, _ = run_async(run_turn(uid, "first", thread=thread))
        _, run = run_async(run_turn(uid, "second", thread=chats.get_thread(uid, thread_id)))

    assert run["status"] == "completed"
    replay = provider.bodies[2]["messages"]
    assert any(m.get("role") == "tool" and "RESULT-123" in str(m.get("content")) for m in replay)
    assert any(m.get("role") == "assistant" and m.get("content") == "first answer" for m in replay)
    # One copy of the round, not one per turn.
    assert sum(1 for m in replay if m.get("role") == "tool") == 1


def test_parallel_calls_each_get_their_own_result_in_call_order(fresh_db):
    uid = make_user()
    thread = tool_thread(uid)
    with fake_tools(lambda name, args: "answer for " + args["query"]) as tools, fake_provider(
        tool_call_frames([("c1", "web_search", {"query": "alpha"}), ("c2", "web_search", {"query": "beta"})]),
        text_frames("done"),
    ) as provider:
        _, run = run_async(run_turn(uid, "two searches", thread=thread))

    assert run["status"] == "completed"
    assert sorted(args["query"] for _, args in tools.calls) == ["alpha", "beta"]
    results = _tool_results(provider.bodies[1])
    assert [r["tool_call_id"] for r in results] == ["c1", "c2"]
    assert "answer for alpha" in results[0]["content"] and "answer for beta" in results[1]["content"]


def test_a_crashing_tool_reports_failure_to_the_model_and_the_turn_continues(fresh_db):
    uid = make_user()
    thread = tool_thread(uid)

    def explode(name, args):
        raise RuntimeError("secret internal detail")

    with fake_tools(explode), fake_provider(
        tool_call_frames([("c1", "web_search", {"query": "x"})]),
        text_frames("Search failed, sorry."),
    ) as provider:
        thread_id, run = run_async(run_turn(uid, "search", thread=thread))

    assert run["status"] == "completed"
    result = _tool_results(provider.bodies[1])[0]
    assert result["content"].startswith("Tool failed.")
    assert "secret internal detail" not in result["content"]
    reply = chats.get_thread(uid, thread_id)["messages"][-1]
    assert reply["tools"][0]["status"] == "error"


def test_invalid_tool_arguments_are_refused_without_executing(fresh_db):
    uid = make_user()
    thread = tool_thread(uid)
    broken = [
        frame({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "[1, 2]"}}]}}]}),
        frame({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        b"data: [DONE]\n\n",
    ]
    with fake_tools() as tools, fake_provider(broken, text_frames("ok")) as provider:
        _, run = run_async(run_turn(uid, "go", thread=thread))

    assert run["status"] == "completed"
    assert tools.calls == []
    assert "NOT executed" in _tool_results(provider.bodies[1])[0]["content"]


def test_plain_chat_wraps_up_with_text_after_its_tool_round_budget(fresh_db):
    """A search chat gets MAX_COMPLETIONS rounds; the last one is forced to
    text (tool_choice none) instead of looping on tools forever."""
    from app import chat_runtime

    uid = make_user()
    thread = tool_thread(uid)
    rounds = chat_runtime.MAX_COMPLETIONS
    script = [tool_call_frames([(f"c{i}", "web_search", {"query": f"q{i}"})]) for i in range(rounds - 1)]
    with fake_tools(), fake_provider(*script, text_frames("summary of what I found")) as provider:
        thread_id, run = run_async(run_turn(uid, "research", thread=thread))

    assert run["status"] == "completed"
    assert len(provider.bodies) == rounds
    assert provider.bodies[-1].get("tool_choice") == "none"
    assert chats.get_thread(uid, thread_id)["messages"][-1]["content"] == "summary of what I found"
