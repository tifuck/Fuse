"""Command receipts: pruning drops only receipts at or below the mark one
retention period old, and a receipt written after the mark keeps deduping."""
from datetime import datetime, timedelta, timezone

from app import chat_tx, chats, db
from tests.core.helpers import quick_user

T = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def _receipts(uid):
    return sorted(r["command_id"] for r in db.fetchall("SELECT command_id FROM chat_commands WHERE user_id=?", (uid,)))


def test_receipts_go_one_period_after_the_mark_and_newer_ones_still_dedupe(fresh_db):
    uid = quick_user("ada@core.test")
    for n in (1, 2):
        chats.create_thread(uid, model="offline", command_id=f"c{n}")
    db.execute("DELETE FROM schema_meta WHERE key=?", (chat_tx._RECEIPT_MARK,))
    assert chat_tx.prune_command_receipts(T) == 0  # sets the mark at c2
    late = chats.create_thread(uid, model="offline", command_id="c3")

    assert chat_tx.prune_command_receipts(T + timedelta(days=29)) == 0
    assert _receipts(uid) == ["c1", "c2", "c3"]
    assert chat_tx.prune_command_receipts(T + timedelta(days=31)) == 2
    assert _receipts(uid) == ["c3"]

    again = chats.create_thread(uid, model="offline", command_id="c3")
    assert again["id"] == late["id"]
    assert db.fetchone("SELECT COUNT(*) AS n FROM chat_threads WHERE user_id=?", (uid,))["n"] == 3


def test_receipts_delete_in_batches(fresh_db, monkeypatch):
    monkeypatch.setattr(chat_tx, "_RECEIPT_BATCH", 2)
    uid = quick_user("ada@core.test")
    for n in range(5):
        chats.create_thread(uid, model="offline", command_id=f"c{n}")
    db.execute("DELETE FROM schema_meta WHERE key=?", (chat_tx._RECEIPT_MARK,))
    chat_tx.prune_command_receipts(T)
    chats.create_thread(uid, model="offline", command_id="late")

    assert chat_tx.prune_command_receipts(T + timedelta(days=31)) == 5
    assert _receipts(uid) == ["late"]
