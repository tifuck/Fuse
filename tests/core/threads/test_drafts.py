"""Composer drafts: capped per user, and deleting a chat deletes its draft."""
import pytest

from app import chat_drafts, chats
from app.chat_schema import ChatValidationError
from tests.core.helpers import quick_user


def test_a_new_draft_past_the_cap_is_refused_but_existing_ones_still_save(fresh_db, monkeypatch):
    monkeypatch.setattr(chat_drafts, "DRAFTS_MAX", 2)
    uid = quick_user()
    chat_drafts.set_draft(uid, "stash:a", "one")
    chat_drafts.set_draft(uid, "stash:b", "two")
    with pytest.raises(ChatValidationError, match="too many saved drafts"):
        chat_drafts.set_draft(uid, "stash:c", "three")
    chat_drafts.set_draft(uid, "stash:a", "one, edited")
    chat_drafts.set_draft(uid, "stash:b", "")
    chat_drafts.set_draft(uid, "stash:c", "three")
    assert chat_drafts.list_drafts(uid) == {"stash:a": "one, edited", "stash:c": "three"}


def test_deleting_a_chat_deletes_its_draft(fresh_db):
    uid = quick_user()
    gone, kept = chats.create_thread(uid, model="offline"), chats.create_thread(uid, model="offline")
    for thread in (gone, kept):
        chat_drafts.set_draft(uid, thread["id"], "unsent")
    assert chats.delete_thread(uid, gone["id"])
    assert chat_drafts.list_drafts(uid) == {kept["id"]: "unsent"}
