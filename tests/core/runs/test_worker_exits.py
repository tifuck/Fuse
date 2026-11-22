"""How the run worker ends a turn whose generation misbehaves: past its wall
clock, with a frame over the size limit, or with a stream that stops before
its terminal frame. Each fails the turn with an error the user can read and
keeps what was already shown."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from app import chat_runs, chat_store, chats
from tests.core.helpers import fake_provider, frame, quick_user, run_async, run_turn, text_frames, wait_run

from ._support import held_stream, start_run


def _reply(uid, thread_id):
    return chats.get_thread(uid, thread_id)["messages"][-1]


def test_a_turn_past_its_time_limit_fails_and_keeps_its_text(fresh_db):
    uid = quick_user()

    async def body():
        provider.arm()
        thread, run, task = await start_run(uid, "write a story")
        await asyncio.wait_for(provider.sent.wait(), 5)
        await wait_run(task, 10)
        return thread, run

    # A root turn has no per-run budget of its own: the process limit applies.
    with held_stream("Once upon") as provider, patch.object(chat_runs, "MAX_RUNTIME_SECONDS", 0.3):
        thread, run = run_async(body())

    assert chat_store.get_run(uid, run["id"])["status"] == "failed"
    reply = _reply(uid, thread["id"])
    assert reply["error"] == "Generation exceeded its time limit."
    assert reply["content"] == "Once upon"


def test_a_frame_over_the_size_limit_fails_the_turn(fresh_db):
    uid = quick_user()
    with fake_provider(text_frames("x" * 4000)), patch.object(chat_runs, "MAX_FRAME_BYTES", 1000):
        thread_id, run = run_async(run_turn(uid, "go"))

    assert run["status"] == "failed"
    assert _reply(uid, thread_id)["error"] == "Generation frame limit reached."


def test_a_generation_stream_without_its_terminal_frame_fails_the_turn(fresh_db):
    uid = quick_user()

    async def cut_short(*_args, **_kwargs):
        async def frames():
            yield frame({"choices": [{"index": 0, "delta": {"content": "partial"}}]})

        return SimpleNamespace(body_iterator=frames())

    async def body():
        thread, run, task = await start_run(uid, "go")
        await wait_run(task, 10)
        return thread, run

    with patch("app.chat_runtime.stream_dashboard_chat", new=cut_short):
        thread, run = run_async(body())

    assert chat_store.get_run(uid, run["id"])["status"] == "failed"
    reply = _reply(uid, thread["id"])
    assert reply["error"] == "Generation stream ended without [DONE]."
    assert reply["content"] == "partial"
