"""User attachment images in what the page receives and sends back: a thread
view links an image the thread stores instead of repeating its bytes, the link
serves only its owner, and every way back in gets the stored bytes again."""
import asyncio
import base64
import hashlib
from unittest.mock import AsyncMock, patch

import pytest

from app import chat_images, chat_queue, chat_runs, chat_store, chats, db
from tests.core.auth._support import anonymous, refused, sign_in, web_client
from tests.core.helpers import fake_provider, make_user, quick_user, run_async, text_frames

from ._support import image_attachment, image_url, pk


@pytest.fixture(autouse=True)
def _cold_image_cache():
    # Lookups cache per (user, sha) for the process; a warm entry would hide a
    # row that is gone.
    chat_images._cache.clear()
    chat_images._cache_bytes = 0
    yield
    chat_images._cache.clear()
    chat_images._cache_bytes = 0


@pytest.fixture
def web(fresh_db):
    with web_client() as client:
        yield client


IMAGE = image_url(1)


def _link():
    return chat_images.ATTACHMENT_PATH + hashlib.sha256(IMAGE.encode()).hexdigest()


def _thread_with_image(uid):
    """A finished turn whose user message carries IMAGE."""
    thread = chats.create_thread(uid, model="offline", tools=[])
    run = chat_store.start_turn(uid, thread["id"], "what is this?", thread["revision"], None,
                                attachments=[image_attachment(1)])["run"]
    chat_store.finish_run(run["id"], {"content": "a cat"}, "completed")
    return chats.get_thread(uid, thread["id"])


def _page(web, thread):
    """The thread as the page holds it."""
    got = web.get(f"/chat/threads/{thread['id']}")
    assert got.status_code == 200, got.text
    return got.json()


def _stored_image(uid, thread, index=0):
    return chats.get_thread(uid, thread["id"])["messages"][index]["attachments"][0]["data"]


def test_a_thread_view_links_a_stored_image_and_the_transcript_keeps_its_bytes(web):
    uid = make_user()
    thread = _thread_with_image(uid)
    sign_in(web, uid)

    got = web.get(f"/chat/threads/{thread['id']}")

    shown = got.json()["messages"][0]["attachments"][0]
    kept = thread["messages"][0]["attachments"][0]
    assert shown == {**kept, "data": _link()}
    assert IMAGE not in got.text
    assert IMAGE not in web.get(f"/chat/threads/{thread['id']}?pack=1&stream=1").text
    assert _stored_image(uid, thread) == IMAGE


def test_an_image_the_thread_does_not_store_stays_inline(web):
    uid = make_user()
    # Saved without a turn: nothing put the picture in chat_images.
    thread = chats.create_thread(uid, model="offline",
                                 messages=[{"role": "user", "content": "kept", "attachments": [image_attachment(2)]}])
    sign_in(web, uid)

    assert _page(web, thread)["messages"][0]["attachments"][0]["data"] == image_url(2)


def test_a_linked_image_is_served_only_to_its_owner(web):
    owner, other = make_user("owner@core.test"), quick_user("other@core.test")
    _thread_with_image(owner)

    sign_in(web, owner)
    got = web.get(_link())
    assert got.status_code == 200
    assert got.content == base64.b64decode(IMAGE.split(",", 1)[1])
    assert got.headers["content-type"] == "image/png"
    assert got.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert web.get(chat_images.ATTACHMENT_PATH + "0" * 64).status_code == 404
    assert web.get(chat_images.ATTACHMENT_PATH + "not-a-sha").status_code == 404

    sign_in(web, other)
    assert web.get(_link()).status_code == 404
    anonymous(web)
    assert refused(web.get(_link(), follow_redirects=False))


def test_a_save_that_echoes_a_linked_image_keeps_the_stored_bytes(web):
    uid = make_user()
    thread = _thread_with_image(uid)
    sign_in(web, uid)
    page = _page(web, thread)
    page["messages"][0]["content"] = "what is this, exactly?"

    saved = web.patch(f"/chat/threads/{thread['id']}",
                      json={"messages": page["messages"], "expected_revision": page["revision"]})

    assert saved.status_code == 200, saved.text
    assert saved.json()["messages"][0]["attachments"][0]["data"] == _link()
    stored = chats.get_thread(uid, thread["id"])["messages"][0]
    assert stored["content"] == "what is this, exactly?" and stored["attachments"][0]["data"] == IMAGE


def test_undo_brings_a_deleted_message_back_with_its_image(web):
    uid = make_user()
    thread = _thread_with_image(uid)
    sign_in(web, uid)
    page = _page(web, thread)
    cleared = web.patch(f"/chat/threads/{thread['id']}", json={"messages": [], "expected_revision": page["revision"]})
    assert cleared.status_code == 200 and chats.get_thread(uid, thread["id"])["messages"] == []

    # The message is no longer in the transcript: only the image's content names it.
    undone = web.patch(f"/chat/threads/{thread['id']}",
                       json={"messages": page["messages"], "expected_revision": cleared.json()["revision"]})

    assert undone.status_code == 200, undone.text
    assert _stored_image(uid, thread) == IMAGE


def test_a_linked_image_that_is_gone_is_refused_and_nothing_is_stored_without_it(web):
    uid = make_user()
    thread = _thread_with_image(uid)
    sign_in(web, uid)
    page = _page(web, thread)
    db.execute("DELETE FROM chat_images WHERE thread_id=?", (pk(uid, thread["id"]),))
    before = chats.get_thread(uid, thread["id"])

    saved = web.patch(f"/chat/threads/{thread['id']}",
                      json={"messages": page["messages"], "expected_revision": page["revision"]})
    with patch.object(chat_runs, "start", new=AsyncMock(return_value=None)):
        resent = web.post(f"/chat/threads/{thread['id']}/turns", json={
            "text": "look again", "attachments": page["messages"][0]["attachments"], "replace_from": 0,
            "expected_revision": page["revision"], "command_id": "t-1"})

    assert (saved.status_code, resent.status_code) == (409, 409), (saved.text, resent.text)
    after = chats.get_thread(uid, thread["id"])
    assert (after["revision"], after["messages"]) == (before["revision"], before["messages"])
    # With no stored copy to point at, the next view carries the bytes again.
    assert _page(web, thread)["messages"][0]["attachments"][0]["data"] == IMAGE


def test_a_resent_linked_image_reaches_the_model_as_its_bytes(web):
    uid = make_user()
    thread = _thread_with_image(uid)
    sign_in(web, uid)
    page = _page(web, thread)

    # Edit and resend: the page sends back the attachment as the view gave it.
    with patch.object(chat_runs, "start", new=AsyncMock(return_value=None)):
        sent = web.post(f"/chat/threads/{thread['id']}/turns?pack=1", json={
            "text": "look again", "attachments": page["messages"][0]["attachments"], "replace_from": 0,
            "expected_revision": page["revision"], "command_id": "t-1"})
    assert sent.status_code == 200, sent.text
    assert sent.json()["thread"]["messages"][0]["attachments"][0]["data"] == _link()
    assert _stored_image(uid, thread) == IMAGE

    async def generate():
        run = chat_store.get_run(uid, sent.json()["run"]["id"])
        task = await chat_runs.start(chat_runs.internal_request(uid), {"id": uid}, run)
        await asyncio.wait_for(task, 10)

    with fake_provider(text_frames("still a cat")) as provider:
        run_async(generate())
    images = [chat_images.resolve(uid, part["image_url"]["url"]) for msg in provider.bodies[0]["messages"]
              if isinstance(msg.get("content"), list) for part in msg["content"] if part.get("type") == "image_url"]
    assert images == [IMAGE]


def test_a_queued_and_a_steered_message_get_their_linked_images_back(web):
    uid = make_user()
    thread = _thread_with_image(uid)
    sign_in(web, uid)
    linked = _page(web, thread)["messages"][0]["attachments"]
    chat_store.start_turn(uid, thread["id"], "busy", thread["revision"], None)

    with patch.object(chat_runs, "start", new=AsyncMock(return_value=None)):
        queued = web.post(f"/chat/threads/{thread['id']}/queue",
                          json={"id": "q-1", "text": "and this", "attachments": linked, "created": 1})
        steered = web.post(f"/chat/threads/{thread['id']}/steer",
                           json={"text": "also this", "attachments": linked, "command_id": "s-1"})

    assert (queued.status_code, steered.status_code) == (200, 200), (queued.text, steered.text)
    assert chat_queue.next_item(uid, pk(uid, thread["id"]))["attachments"][0]["data"] == IMAGE
    bubble = next(m for m in chats.get_thread(uid, thread["id"])["messages"] if m["content"] == "also this")
    assert bubble["attachments"][0]["data"] == IMAGE


def test_a_chat_created_from_a_linked_image_stores_its_bytes(web):
    uid = make_user()
    thread = _thread_with_image(uid)
    sign_in(web, uid)
    linked = _page(web, thread)["messages"][0]["attachments"]

    made = web.post("/chat/threads", json={"model": "offline", "command_id": "c-1",
                                           "messages": [{"role": "user", "content": "copy", "attachments": linked}]})

    assert made.status_code == 201, made.text
    assert _stored_image(uid, made.json()) == IMAGE
