"""A root turn end to end: provider stream -> durable run -> thread transcript.

Sample for the async pattern: a real run through chat_runs and the generation
loop, the provider faked at app.api.invoke_chat (tests.core.helpers).
"""
from unittest.mock import patch

import pytest

from app import chat_runs, chat_store, chats
from app.chats import ChatValidationError
from tests.core.helpers import fake_provider, frame, make_user, run_async, run_turn, text_frames

from ._support import run_row


def test_streamed_reply_completes_the_run_and_lands_in_the_thread(fresh_db):
    uid = make_user()
    with fake_provider(text_frames("Hello from the model")) as provider:
        thread_id, run = run_async(run_turn(uid, "Say hello"))

    assert run["status"] == "completed"
    messages = chats.get_thread(uid, thread_id)["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "Say hello"
    assert messages[1]["content"] == "Hello from the model"
    # The provider saw the user's words as the last message of its request.
    assert provider.bodies[0]["messages"][-1]["content"].startswith("Say hello")


def test_second_turn_sends_the_first_exchange_as_history(fresh_db):
    uid = make_user()
    with fake_provider(text_frames("first answer"), text_frames("second answer")) as provider:
        thread_id, _ = run_async(run_turn(uid, "first question"))
        thread = chats.get_thread(uid, thread_id)
        _, run = run_async(run_turn(uid, "second question", thread=thread))

    assert run["status"] == "completed"
    sent = [(m["role"], m["content"]) for m in provider.bodies[1]["messages"] if m["role"] in ("user", "assistant")]
    assert ("assistant", "first answer") in sent
    assert sent[-1][0] == "user" and sent[-1][1].startswith("second question")
    assert [m["content"] for m in chats.get_thread(uid, thread_id)["messages"]][-1] == "second answer"


def test_runs_of_other_users_stay_invisible(fresh_db):
    owner, other = make_user("owner@core.test"), make_user("other@core.test")
    with fake_provider(text_frames("private")):
        thread_id, run = run_async(run_turn(owner, "mine"))

    assert chat_store.get_run(other, run["id"]) is None
    assert chats.get_thread(other, thread_id) is None


def test_an_empty_reply_fails_the_turn_with_a_reason(fresh_db):
    uid = make_user()
    empty = [frame({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}), b"data: [DONE]\n\n"]
    with fake_provider(empty):
        thread_id, run = run_async(run_turn(uid, "anything?"))

    assert run["status"] == "failed"
    reply = chats.get_thread(uid, thread_id)["messages"][-1]
    assert reply["error"] == "The model returned an empty reply."


def test_an_internal_error_fails_the_turn_without_leaking_its_text(fresh_db):
    uid = make_user()
    with fake_provider(), patch("app.chat_runtime.stream_dashboard_chat",
                                side_effect=RuntimeError("password=hunter2 at db01")):
        thread_id, run = run_async(run_turn(uid, "hi"))

    assert run["status"] == "failed"
    reply = chats.get_thread(uid, thread_id)["messages"][-1]
    assert reply["error"] == "Generation failed."
    assert "hunter2" not in str(chat_store.get_run(uid, run["id"]))


def test_a_finished_run_is_terminal_and_the_thread_accepts_the_next_turn(fresh_db):
    uid = make_user()
    with fake_provider(text_frames("one")):
        thread_id, run = run_async(run_turn(uid, "first"))

    assert run_row(run["id"])["status"] == "completed"
    # A terminal run is never started again.
    assert run_async(chat_runs.start(chat_runs.internal_request(uid), {"id": uid}, run)) is None
    thread = chats.get_thread(uid, thread_id)
    assert chat_store.start_turn(uid, thread_id, "second", thread["revision"], None)["run"]["status"] == "queued"


def test_only_one_active_root_run_per_thread(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline", tools=[])
    chat_store.start_turn(uid, thread["id"], "first", thread["revision"], None)
    current = chats.get_thread(uid, thread["id"])

    with pytest.raises(ChatValidationError) as refused:
        chat_store.start_turn(uid, thread["id"], "second", current["revision"], None)
    assert refused.value.status_code == 409


def test_a_retried_turn_command_starts_one_run(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline", tools=[])
    first = chat_store.start_turn(uid, thread["id"], "hello", thread["revision"], "turn-cmd-1")
    # The client lost the ACK and retries with the revision it refreshed since.
    again = chat_store.start_turn(uid, thread["id"], "hello", first["thread"]["revision"], "turn-cmd-1")

    assert again["idempotent"] is True
    assert again["run"]["id"] == first["run"]["id"]
    assert [m["content"] for m in chats.get_thread(uid, thread["id"])["messages"] if m["role"] == "user"] == ["hello"]
