"""Root admission caps count live root runs through the generated, indexed
active_parent_thread column: children never count against them."""
import uuid

import pytest

from app import chat_store, chats, db
from app.chats import ChatValidationError
from tests.core.helpers import quick_user


def _live_run(uid, thread_id, parent=None):
    ts = db.now()
    db.execute(
        "INSERT INTO chat_runs (id,thread_id,user_id,status,request,message_id,message,created_at,updated_at,parent_run_id)"
        " VALUES (?,?,?,'running','{}',?,'{}',?,?,?)",
        (str(uuid.uuid4()), thread_id, uid, str(uuid.uuid4()), ts, ts, parent),
    )


def test_live_roots_fill_the_global_cap_and_children_do_not(fresh_db):
    users = [quick_user(f"cap{i}@core.test") for i in range(2)]
    thread = chats.create_thread(users[0], model="offline", tools=[])
    for i in range(31):
        _live_run(users[i % 2], 10_000_000 + i)
    _live_run(users[0], chats.resolve_thread_pk(users[0], thread["id"]), parent="some-root")

    run = chat_store.start_turn(users[0], thread["id"], "fits", thread["revision"], None)["run"]
    assert run["status"] == "queued"

    other = chats.create_thread(users[1], model="offline", tools=[])
    with pytest.raises(ChatValidationError) as full:
        chat_store.start_turn(users[1], other["id"], "over", other["revision"], None)
    assert full.value.status_code == 429
