"""save_turn_progress tells an unchanged transcript from the stored base and
tail digests (scripts/sql/2026-11-20-run-sweeps.sql) instead of hashing the
virtual provider_messages."""
from app import chat_checkpoint, chat_store, chats
from tests.core.helpers import quick_user

from ._support import provider_messages
from .test_checkpoints import Statements


def test_repeating_a_transcript_stored_as_base_and_tail_rewrites_nothing(fresh_db):
    uid = quick_user()
    thread = chats.create_thread(uid, model="offline", tools=[])
    run = chat_store.start_turn(uid, thread["id"], "inspect", thread["revision"], None)["run"]
    rows = [*run["request"]["messages"], {"role": "assistant", "content": "a"}]
    longer = [*rows, {"role": "assistant", "content": "b"}]
    chat_checkpoint.save_turn_progress(run["id"], rows)
    chat_checkpoint.save_turn_progress(run["id"], longer)

    with Statements() as repeat:
        chat_checkpoint.save_turn_progress(run["id"], longer)
    assert repeat.run_columns() == [{"updated_at"}]

    changed = [*rows, {"role": "assistant", "content": "c"}]
    with Statements() as edit:
        chat_checkpoint.save_turn_progress(run["id"], changed)
    assert edit.run_columns() == [{"provider_tail", "updated_at"}]
    assert provider_messages(run["id"]) == changed
