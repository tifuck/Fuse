"""What a save and a send answer with: no transcript for a save that sent
none, and turn and queue replies packed like the thread snapshot."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app import chat_runs, chat_store, chats
from tests.core.auth._support import sign_in, web_client
from tests.core.helpers import fake_provider, make_user, run_async, run_turn, text_frames

# What only a transcript snapshot carries.
SNAPSHOT_ONLY = {"messages", "compaction", "recaps", "accepted_queue_ids", "event_cursor", "thread_cursor"}


@pytest.fixture
def web(fresh_db):
    with web_client() as client:
        yield client


def _finished_thread(uid):
    with fake_provider(text_frames("world \U0001f642 done")):
        thread_id, _ = run_async(run_turn(uid, "hello"))
    return chats.get_thread(uid, thread_id)


def _unpacked(thread):
    """What static/chat/net.js unpackThread does to a packed reply."""
    assert thread.pop("segments_packed") is True
    for msg in thread["messages"]:
        at = {"content": 0, "reasoning": 0}
        for seg in msg.get("segments") or []:
            if seg.get("type") not in at:
                continue
            if "text" not in seg:
                units = (msg.get(seg["type"]) or "").encode("utf-16-le")
                start = at[seg["type"]] * 2
                seg["text"] = units[start:start + seg.pop("n") * 2].decode("utf-16-le")
            at[seg["type"]] += len(seg["text"].encode("utf-16-le")) // 2
    return thread


def _stored_view(uid, thread_id):
    return json.loads(json.dumps(chats.get_thread(uid, thread_id)["messages"]))


def test_a_save_without_messages_answers_without_the_transcript(fresh_db):
    uid = make_user()
    thread = _finished_thread(uid)

    saved = chats.update_thread(uid, thread["id"], title="Renamed", model="other", context="be brief", tools=[],
                                pinned=True, memory_off=True, reasoning_effort="high",
                                expected_revision=thread["revision"])

    full = chats.get_thread(uid, thread["id"])
    assert "messages" not in saved
    # Every other field of the full view is there: no caller loses one.
    assert saved == {key: value for key, value in full.items() if key not in SNAPSHOT_ONLY}
    assert (saved["title"], saved["memory_off"], saved["reasoning_effort"]) == ("Renamed", True, "high")


def test_a_save_with_messages_answers_with_the_transcript(fresh_db):
    uid = make_user()
    thread = _finished_thread(uid)

    # Undo and redo apply this reply as the thread's snapshot, an empty one too.
    cleared = chats.update_thread(uid, thread["id"], messages=[], expected_revision=thread["revision"])

    assert cleared["messages"] == [] and "event_cursor" in cleared


def test_a_retried_save_without_messages_answers_the_same_way(fresh_db):
    uid = make_user()
    thread = _finished_thread(uid)
    first = chats.update_thread(uid, thread["id"], title="Once", expected_revision=thread["revision"], command_id="cmd-1")

    again = chats.update_thread(uid, thread["id"], title="Once", expected_revision=thread["revision"], command_id="cmd-1")

    assert again == first and "messages" not in again


def test_a_save_reports_the_running_turn_without_its_transcript(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    run = chat_store.start_turn(uid, thread["id"], "working", thread["revision"], None)["run"]

    pinned = chats.update_thread(uid, thread["id"], pinned=True)

    assert pinned["running"] is True and "messages" not in pinned
    assert (pinned["run"]["id"], pinned["run"]["status"], pinned["run"]["cancel_requested"]) == (run["id"], "queued", False)
    assert "message" not in pinned["run"] and "request" not in pinned["run"]


def test_the_patch_route_sends_the_transcript_back_only_when_it_received_one(web):
    uid = make_user()
    thread = _finished_thread(uid)
    sign_in(web, uid)

    renamed = web.patch(f"/chat/threads/{thread['id']}", json={"title": "Renamed", "expected_revision": thread["revision"]})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Renamed" and "messages" not in renamed.json()

    kept = _stored_view(uid, thread["id"])[:1]
    edited = web.patch(f"/chat/threads/{thread['id']}",
                       json={"messages": kept, "expected_revision": renamed.json()["revision"]})
    assert edited.status_code == 200, edited.text
    assert [m["id"] for m in edited.json()["messages"]] == [kept[0]["id"]]


@pytest.mark.parametrize("route", ["turns", "queue"])
def test_turn_and_queue_replies_are_packed_on_request(web, route):
    uid = make_user()
    thread = _finished_thread(uid)
    sign_in(web, uid)
    body = ({"text": "again", "expected_revision": thread["revision"], "command_id": "t-1"} if route == "turns"
            else {"id": "q-1", "text": "again", "created": 1})

    with patch.object(chat_runs, "start", new=AsyncMock(return_value=None)):
        plain = web.post(f"/chat/threads/{thread['id']}/{route}", json=body)
        packed = web.post(f"/chat/threads/{thread['id']}/{route}?pack=1", json=body)

    assert plain.status_code == 200 and packed.status_code == 200, (plain.text, packed.text)
    stored = _stored_view(uid, thread["id"])
    # A page that did not ask (an older one) still gets whole segments.
    assert "segments_packed" not in plain.json()["thread"] and plain.json()["thread"]["messages"] == stored
    reply = packed.json()["thread"]
    assert any("n" in seg and "text" not in seg for msg in reply["messages"] for seg in msg.get("segments") or [])
    assert _unpacked(reply)["messages"] == stored
