"""Thread writes: optimistic revisions, idempotent commands, active-run guard.

Sample for the database pattern: real functions on the fresh_db schema, the
assertion on what is stored afterwards, not on internal calls.
"""
import pytest

from app import chat_store, chat_tx, chats, hosts
from app.chats import ChatValidationError
from tests.core.features._support import make_host, make_project
from tests.core.helpers import make_user
from tests.core.threads.test_lock_order import _race


def test_a_stale_revision_is_refused_and_changes_nothing(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    renamed = chats.update_thread(uid, thread["id"], title="First", expected_revision=thread["revision"])
    assert renamed["revision"] > thread["revision"]

    with pytest.raises(ChatValidationError) as refused:
        chats.update_thread(uid, thread["id"], title="Lost update", expected_revision=thread["revision"])
    assert refused.value.status_code == 409
    assert chats.get_thread(uid, thread["id"])["title"] == "First"


def test_two_saves_from_one_revision_racing_lose_exactly_one(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    outcomes = []

    def save(title):
        def go():
            try:
                chats.update_thread(uid, thread["id"], title=title, expected_revision=thread["revision"])
                outcomes.append(title)
            except ChatValidationError as exc:
                outcomes.append(exc.status_code)
        return go

    # The first holds the thread row it locked; the second waits on it, then
    # sees the revision the first committed.
    _race(save("First"), (chat_tx, "lock_thread"), save("Second"), after=True)

    assert sorted(outcomes, key=str) == [409, "First"]
    assert chats.get_thread(uid, thread["id"])["title"] == "First"


def test_two_moves_from_one_revision_racing_lose_exactly_one(fresh_db):
    uid = make_user()
    host = make_host(uid)
    first, second = make_project(uid, host, "api"), make_project(uid, host, "web")
    thread = chats.create_thread(uid, model="offline")
    outcomes = []

    def move(project):
        def go():
            try:
                chats.move_thread_to_project(uid, thread["id"], host, project, expected_revision=thread["revision"])
                outcomes.append(project)
            except ChatValidationError as exc:
                outcomes.append(exc.status_code)
        return go

    def moves_the_thread(info):
        return info.startswith("UPDATE") and "chat_threads" in info

    # The first holds the thread row it moved; the second waits on it, then
    # must see the revision the first committed instead of moving it again.
    _race(move(first), (hosts, "projects_changed"), move(second), after=True, waiting=moves_the_thread)

    assert sorted(outcomes) == sorted([409, first])
    assert chats.get_thread(uid, thread["id"])["project_id"] == first


def test_a_retried_command_applies_once_and_returns_the_first_result(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    first = chats.update_thread(uid, thread["id"], title="Once", expected_revision=thread["revision"], command_id="cmd-1")
    # The client retries after a lost response: same id, same request.
    again = chats.update_thread(uid, thread["id"], title="Once", expected_revision=thread["revision"], command_id="cmd-1")

    assert again["revision"] == first["revision"]
    assert chats.get_thread(uid, thread["id"])["revision"] == first["revision"]


def test_a_command_id_reused_for_another_request_is_refused(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    chats.update_thread(uid, thread["id"], title="Once", command_id="cmd-1")

    with pytest.raises(ChatValidationError) as refused:
        chats.update_thread(uid, thread["id"], title="Something else", command_id="cmd-1")
    assert refused.value.status_code == 409
    assert chats.get_thread(uid, thread["id"])["title"] == "Once"


def test_command_ids_belong_to_one_account(fresh_db):
    alice, bob = make_user("alice@core.test"), make_user("bob@core.test")
    mine = chats.create_thread(alice, model="offline", title="Alice", command_id="same-id")
    theirs = chats.create_thread(bob, model="offline", title="Bob", command_id="same-id")

    assert theirs["id"] != mine["id"]
    assert [t["title"] for t in chats.list_threads(bob)] == ["Bob"]


def test_a_retried_create_or_turn_does_not_duplicate(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline", command_id="new-chat")
    assert chats.create_thread(uid, model="offline", command_id="new-chat")["id"] == thread["id"]
    assert len(chats.list_threads(uid)) == 1

    first = chat_store.start_turn(uid, thread["id"], "hello", thread["revision"], "turn-1")
    # The ACK was lost; the tab resends with the revision it last saw.
    again = chat_store.start_turn(uid, thread["id"], "hello", thread["revision"], "turn-1")
    assert again["idempotent"] is True
    assert again["run"]["id"] == first["run"]["id"]
    assert [m["content"] for m in chats.get_thread(uid, thread["id"])["messages"] if m["role"] == "user"] == ["hello"]


def test_a_stale_delete_is_a_no_op(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    chats.update_thread(uid, thread["id"], title="Moved on")

    assert chats.delete_thread(uid, thread["id"], expected_revision=thread["revision"]) is False
    assert chats.get_thread(uid, thread["id"])["title"] == "Moved on"


def test_account_context_has_its_own_revision(fresh_db):
    uid = make_user()
    context = chats.get_user_context(uid)
    saved = chats.set_user_context(uid, profile="I write Go", expected_revision=context["revision"])

    with pytest.raises(ChatValidationError) as refused:
        chats.set_user_context(uid, profile="Lost update", expected_revision=context["revision"])
    assert refused.value.status_code == 409
    assert chats.get_user_context(uid)["profile"] == "I write Go"
    assert saved["revision"] == context["revision"] + 1


def test_messages_cannot_be_replaced_while_a_run_is_active(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline")
    chat_store.start_turn(uid, thread["id"], "working on it", thread["revision"], None)
    current = chats.get_thread(uid, thread["id"])

    with pytest.raises(ChatValidationError) as refused:
        chats.update_thread(uid, thread["id"], messages=[], expected_revision=current["revision"])
    assert refused.value.status_code == 409
    after = chats.get_thread(uid, thread["id"])
    assert after["messages"] == current["messages"]
    assert after["messages"][0]["content"] == "working on it"
