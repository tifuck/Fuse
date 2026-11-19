"""A fork racing a new turn: the active-run check and the copy read the same
locked rows, so a turn committed after the fork's snapshot is either refused
or never copied, not copied as a live run."""
from app import chat_store, chats
from app.chats import ChatValidationError
from tests.core.helpers import quick_user
from tests.core.threads.test_lock_order import _race

from ._support import runs_of


def test_a_turn_started_while_a_fork_reads_is_never_copied_as_running(fresh_db):
    uid = quick_user()
    thread = chats.create_thread(uid, model="offline")
    outcome = {}

    def fork():
        try:
            outcome["fork"] = chats.fork_thread(uid, thread["id"])
        except ChatValidationError as exc:
            outcome["fork"] = exc.status_code

    # The fork has taken its snapshot (thread read, revision checked) when
    # the turn commits.
    _race(fork, (chat_store, "check_revision"),
          lambda: chat_store.start_turn(uid, thread["id"], "hello", thread["revision"], None), after=True)

    assert outcome["fork"] == 409
    assert len(chats.list_threads(uid)) == 1
    assert [r["status"] for r in runs_of(uid, thread["id"])] == ["queued"]

