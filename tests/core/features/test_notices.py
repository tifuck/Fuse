"""Persistent notices: one per key, shown until dismissed on any device,
reopened only for something new; a provider usage limit posts one, and a
later good turn clears it."""
import json
from types import SimpleNamespace

import pytest

from app import chat_runs, chats, db, notices
from tests.core.helpers import quick_user


def events(uid, kind):
    rows = db.fetchall("SELECT payload FROM chat_events WHERE user_id=? AND type=? ORDER BY id", (uid, kind))
    return [json.loads(row["payload"]) for row in rows]


@pytest.fixture
def owner(fresh_db):
    uid = quick_user()
    thread = chats.create_thread(uid, model="offline", tools=[])
    return uid, thread["id"], chats.resolve_thread_pk(uid, thread["id"])


def test_a_notice_is_listed_with_its_chat_until_dismissed_on_any_device(owner):
    uid, public, pk = owner
    shown = notices.post(uid, "k", title="Title", body="Body", kind="warn", thread_id=pk)
    assert (shown["thread_id"], shown["kind"], shown["body"]) == (public, "warn", "Body")
    assert [n["id"] for n in notices.list_open(uid)] == [shown["id"]]
    assert events(uid, "notice.updated")[-1]["id"] == shown["id"]
    assert notices.dismiss(quick_user("other@core.test"), shown["id"]) is False
    assert notices.dismiss(uid, shown["id"]) is True
    assert notices.list_open(uid) == []
    assert events(uid, "notice.dismissed") == [{"id": shown["id"], "key": "k"}]
    assert notices.dismiss(uid, shown["id"]) is False  # once only


def test_an_item_is_announced_once_and_a_new_one_reopens_the_notice(owner):
    uid, _public, pk = owner
    body = lambda n: f"{n} things"  # noqa: E731
    assert notices.post(uid, "k", body=body, thread_id=pk, item=1)["body"] == "1 things"
    assert notices.post(uid, "k", body=body, thread_id=pk, item=1) is None
    notices.dismiss_key(uid, "k")
    assert notices.post(uid, "k", body=body, thread_id=pk, item=1) is None
    assert notices.list_open(uid) == []
    again = notices.post(uid, "k", body=body, thread_id=pk, item=2)
    assert (again["body"], again["count"]) == ("2 things", 2)
    assert len(notices.list_open(uid)) == 1


def test_bad_keys_and_kinds_are_refused(owner):
    uid, _public, _pk = owner
    for key, kind in (("", "idle"), ("x" * 192, "idle"), ("k", "loud")):
        with pytest.raises(ValueError):
            notices.post(uid, key, kind=kind)


def test_a_usage_limit_leaves_a_notice_that_a_good_turn_clears(owner):
    uid, public, pk = owner
    failed = SimpleNamespace(error_kind="usage_exhausted", message={"error": "Claude: 5h usage limit reached."})
    chat_runs._limit_notice(uid, pk, "run-1", "failed", failed)
    (shown,) = notices.list_open(uid)
    assert (shown["title"], shown["body"], shown["thread_id"]) == ("Usage limit reached", "Claude: 5h usage limit reached.", public)
    other = SimpleNamespace(error_kind="server_error", message={"error": "Claude: overloaded."})
    chat_runs._limit_notice(uid, pk, "run-2", "failed", other)
    assert notices.list_open(uid)[0]["body"] == "Claude: 5h usage limit reached."
    chat_runs._limit_notice(uid, pk, "run-3", "completed", SimpleNamespace(error_kind="", message={}))
    assert notices.list_open(uid) == []

