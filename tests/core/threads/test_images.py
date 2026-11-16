"""Transcript images: stored once per thread as a reference, back inline only
for their owner, copied on fork, and gone with the thread."""
import json
from pathlib import Path

import pytest

from app import chat_images, chat_store, chats, db

from tests.core.helpers import make_user

from ._support import image_attachment, image_url, pk


@pytest.fixture(autouse=True)
def _cold_image_cache():
    # Rehydration caches per (user, sha) for the process; a warm entry would
    # hide a row that is gone.
    chat_images._cache.clear()
    chat_images._cache_bytes = 0
    yield
    chat_images._cache.clear()
    chat_images._cache_bytes = 0


def _image_rows(thread_pk):
    return db.fetchall("SELECT sha FROM chat_images WHERE thread_id=?", (thread_pk,))


def _turn_with_image(uid, thread, seed=1):
    return chat_store.start_turn(uid, thread["id"], "what is this?", chats.get_thread(uid, thread["id"])["revision"], None,
                                 attachments=[image_attachment(seed)])["run"]


def _stored_request(run_id):
    return json.loads(db.fetchone("SELECT request FROM chat_runs WHERE id=?", (run_id,))["request"])


def test_an_image_is_stored_once_and_the_request_carries_a_reference(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    run = _turn_with_image(uid, thread)
    chat_store.finish_run(run["id"], {"content": "a cat"}, "completed")
    second = _turn_with_image(uid, thread)  # history replays the same picture

    assert len(_image_rows(pk(uid, thread["id"]))) == 1
    stored = json.dumps(_stored_request(second["id"])["messages"])
    assert image_url(1) not in stored
    assert stored.count(chat_images.PREFIX) == 2
    # On its way to a provider the reference becomes the original bytes again.
    back = chat_images.rehydrate(uid, _stored_request(second["id"])["messages"])
    urls = [p["image_url"]["url"] for m in back if isinstance(m.get("content"), list)
            for p in m["content"] if p.get("type") == "image_url"]
    assert urls == [image_url(1), image_url(1)]


def test_references_resolve_only_for_the_owner(fresh_db):
    owner, other = make_user("owner@core.test"), make_user("other@core.test")
    thread = chats.create_thread(owner, model="offline")
    run = _turn_with_image(owner, thread)
    messages = _stored_request(run["id"])["messages"]

    leaked = json.dumps(chat_images.rehydrate(other, messages))
    assert image_url(1) not in leaked and chat_images.MISSING in leaked
    ref = next(p["image_url"]["url"] for m in messages if isinstance(m.get("content"), list)
               for p in m["content"] if p.get("type") == "image_url")
    assert chat_images.resolve(other, ref) is None
    assert chat_images.resolve(owner, ref) == image_url(1)


def test_a_fork_keeps_its_images_after_the_source_is_deleted(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    run = _turn_with_image(uid, thread)
    chat_store.finish_run(run["id"], {"content": "a cat"}, "completed")
    source = chats.get_thread(uid, thread["id"])
    fork = chats.fork_thread(uid, source["id"])

    assert chats.delete_thread(uid, source["id"]) is True

    assert len(_image_rows(pk(uid, fork["id"]))) == 1
    # The fork's retained provider history replays the picture, not a gap.
    retained = json.loads(db.fetchone("SELECT retained_history FROM chat_threads WHERE id=?",
                                      (pk(uid, fork["id"]),))["retained_history"])
    assert chat_images.PREFIX in json.dumps(retained["messages"])
    back = json.dumps(chat_images.rehydrate(uid, retained["messages"]))
    assert image_url(1) in back and chat_images.MISSING not in back


def test_deleting_the_thread_deletes_its_images():
    """Thread deletion relies on the chat_images foreign key cascade; the core
    fixture schema is built with CREATE TABLE ... LIKE, which drops foreign
    keys, so check the schema fresh installs load declares it."""
    schema = (Path(__file__).resolve().parents[3] / "scripts/sql/schema.sql").read_text()
    table = schema[schema.index("CREATE TABLE `chat_images`"):]
    table = table[:table.index(";")]
    assert "FOREIGN KEY (`thread_id`) REFERENCES `chat_threads` (`id`) ON DELETE CASCADE" in table
