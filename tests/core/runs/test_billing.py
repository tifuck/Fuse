"""Chat-turn billing: every provider request of a turn records one usage
row through api._safe_record_usage, with the status that request ended in."""
import asyncio

import httpx

from app import chat_runs
from tests.core.helpers import fake_provider, frame, quick_user, run_async, run_turn, text_frames, wait_run

from ._support import held_stream, no_backoff, start_run


def _dies_after(text, exc):
    def stream(_body):
        def frames():
            yield frame({"choices": [{"index": 0, "delta": {"content": text}}]})
            raise exc
        return frames()
    return stream


def _recorded(mock):
    """(request_status, usage) per recorded request, in order."""
    return [(call.kwargs["request_status"], call.args[4]) for call in mock.call_args_list]


def test_a_dropped_request_and_its_retry_each_record_usage(fresh_db):
    uid = quick_user()
    usage = {"prompt_tokens": 120, "completion_tokens": 7, "total_tokens": 127}
    with no_backoff(), fake_provider(
        _dies_after("The answer is ", httpx.ReadError("connection reset")),
        text_frames("42.", usage=usage),
    ) as provider:
        _, run = run_async(run_turn(uid, "what is it?"))

    assert run["status"] == "completed"
    recorded = _recorded(provider.record_usage)
    assert [status for status, _ in recorded] == ["failed", "success"]
    billed = recorded[1][1]
    assert {key: billed[key] for key in usage} == usage
    assert provider.record_usage.call_args_list[1].args[0] == uid


def test_stop_mid_stream_records_the_request_as_cancelled(fresh_db):
    uid = quick_user()

    async def body():
        provider.arm()
        _, run, task = await start_run(uid, "write a story")
        await asyncio.wait_for(provider.sent.wait(), 5)
        await chat_runs.cancel_wait(uid, run["id"])
        await wait_run(task, 5)

    with held_stream("Once upon a time") as provider:
        run_async(body())

    assert [status for status, _ in _recorded(provider.record_usage)] == ["cancelled"]
