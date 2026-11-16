"""Provider failures mid-turn: transient ones re-run the same step (no
duplicated text, no error on the reply), definitive ones fail the turn at
once, and the retry budget is bounded."""
import httpx
from fastapi import HTTPException

from app import chat_runtime, chats
from tests.core.helpers import fake_provider, frame, make_user, run_async, run_turn, text_frames, tool_call_frames

from ._support import fake_tools, no_backoff, tool_thread


def _reply(uid, thread_id):
    return chats.get_thread(uid, thread_id)["messages"][-1]


def _dies_after(text, exc):
    """A stream that delivers ``text`` and then breaks with ``exc``."""
    def stream(_body):
        def frames():
            yield frame({"choices": [{"index": 0, "delta": {"content": text}}]})
            raise exc
        return frames()
    return stream


def test_transient_error_retries_the_same_step_and_leaves_no_error(fresh_db):
    uid = make_user()
    with no_backoff(), fake_provider(HTTPException(503, "upstream unavailable"), text_frames("recovered")) as provider:
        thread_id, run = run_async(run_turn(uid, "hi"))

    assert run["status"] == "completed"
    assert len(provider.bodies) == 2
    # Nothing was shown before the failure, so the retry is the identical request.
    assert provider.bodies[1]["messages"] == provider.bodies[0]["messages"]
    reply = _reply(uid, thread_id)
    assert reply["content"] == "recovered"
    assert not reply.get("error")


def test_retry_budget_is_bounded_then_the_turn_fails(fresh_db):
    uid = make_user()
    attempts = chat_runtime.MAX_RESUME_RETRIES + 1
    errors = [HTTPException(503, "upstream unavailable") for _ in range(attempts)]
    with no_backoff(), fake_provider(*errors) as provider:
        thread_id, run = run_async(run_turn(uid, "hi"))

    assert len(provider.bodies) == attempts
    assert run["status"] == "failed"
    assert _reply(uid, thread_id)["error"]


def test_definitive_errors_fail_at_once_without_retrying(fresh_db):
    uid = make_user()
    for exc in (HTTPException(401, "invalid api key"),
                HTTPException(400, "context length exceeded"),
                # Devin's dead-SKU breaker looks transient but fails the same
                # way for the next 30 minutes.
                HTTPException(503, "glm-x is temporarily unavailable on Devin"),
                # api.invoke_chat's wrapper for a local adapter bug: retrying
                # the identical payload fails identically.
                HTTPException(502, "upstream request failed (TypeError)")):
        with no_backoff(), fake_provider(exc) as provider:
            thread_id, run = run_async(run_turn(uid, "hi"))
        assert len(provider.bodies) == 1, exc.detail
        assert run["status"] == "failed"
        assert _reply(uid, thread_id)["error"]


def test_midstream_drop_resumes_from_the_shown_text_without_repeating_it(fresh_db):
    uid = make_user()
    with no_backoff(), fake_provider(
        _dies_after("The answer is ", httpx.ReadError("connection reset")),
        text_frames("42."),
    ) as provider:
        thread_id, run = run_async(run_turn(uid, "what is it?"))

    assert run["status"] == "completed"
    resumed = provider.bodies[1]["messages"]
    # The partial text goes back as the assistant's own words plus a
    # continue-from-here note, once.
    assert resumed[-2] == {"role": "assistant", "content": "The answer is "}
    assert resumed[-1]["role"] == "user" and resumed[-1]["content"] == chat_runtime._RESUME_NOTE
    reply = _reply(uid, thread_id)
    assert reply["content"] == "The answer is 42."
    assert not reply.get("error")


def test_drop_after_tool_results_resumes_without_rerunning_the_tool(fresh_db):
    uid = make_user()
    thread = tool_thread(uid)
    with no_backoff(), fake_tools() as tools, fake_provider(
        tool_call_frames([("c1", "web_search", {"query": "x"})]),
        HTTPException(502, "upstream stream failed"),
        text_frames("final"),
    ) as provider:
        _, run = run_async(run_turn(uid, "go", thread=thread))

    assert run["status"] == "completed"
    assert len(tools.calls) == 1
    retry = provider.bodies[2]["messages"]
    assert sum(1 for m in retry if m.get("role") == "tool") == 1


def test_overload_sentence_sent_as_content_is_retried_not_accepted(fresh_db):
    uid = make_user()
    with no_backoff(), fake_provider(
        text_frames("Our servers are currently overloaded. Please try again later."),
        text_frames("Real answer."),
    ) as provider:
        thread_id, run = run_async(run_turn(uid, "hi"))

    assert len(provider.bodies) == 2
    assert run["status"] == "completed"
    assert _reply(uid, thread_id)["content"] == "Real answer."
    assert not any("overloaded" in str(m.get("content")) for m in provider.bodies[1]["messages"])


def test_a_stream_cut_before_its_terminal_frame_is_retried(fresh_db):
    uid = make_user()
    cut = [frame({"choices": [{"index": 0, "delta": {"role": "assistant"}}]})]  # no finish, no [DONE]
    with no_backoff(), fake_provider(cut, text_frames("complete")) as provider:
        thread_id, run = run_async(run_turn(uid, "hi"))

    assert len(provider.bodies) == 2
    assert run["status"] == "completed"
    assert _reply(uid, thread_id)["content"] == "complete"
