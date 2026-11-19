"""Secret swap at the edges of a chat: the one-off model calls (titles, Jev)
follow the chat's own swap flag and make no call when its pairs cannot be
read, and third-party tools keep the stand-ins they were given.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

from app import api, chat_store, chat_titles, chats, db, jev, secret_swap
from tests.core.helpers import FakeProvider, quick_user, run_async, text_frames

OPENAI = "sk-proj-" + "Hm4Tq8Vz2Lc6" * 4


def swapping_chat(uid):
    """A chat whose first turn ran with the account switch on, and holds a key."""
    chats.set_user_context(uid, secret_swap=True)
    thread = chats.create_thread(uid, model="offline", tools=[])
    chat_store.start_turn(uid, thread["id"], f"deploy with OPENAI_API_KEY={OPENAI} please", thread["revision"], None)
    return chats.resolve_thread_pk(uid, thread["id"])


def break_pairs(uid, pk):
    secret_swap.for_thread(uid, pk).tokenize(f"KEY={OPENAI}")
    db.execute("UPDATE secret_tokens SET cipher='not-a-fernet-token' WHERE user_id=?", (uid,))
    secret_swap._vaults.clear()  # as after a restart with a changed APP_SECRET


def title_sweep():
    provider = FakeProvider(*[text_frames("Deploy the app")] * 4)

    async def body():
        await chat_titles.sweep()
        await asyncio.gather(*list(chat_titles._tasks))

    with patch.object(api, "invoke_chat", new=provider), patch.object(api, "_safe_record_usage"):
        run_async(body())
    return provider.bodies


def test_a_swapping_chat_titles_with_stand_ins_after_the_account_switch_is_off(fresh_db):
    uid = quick_user()
    swapping_chat(uid)
    chats.set_user_context(uid, secret_swap=False)

    bodies = title_sweep()

    assert bodies and "deploy with OPENAI_API_KEY=sk-proj-" in json.dumps(bodies)
    assert OPENAI not in json.dumps(bodies)


def test_unreadable_pairs_send_no_title_request(fresh_db):
    uid = quick_user()
    break_pairs(uid, swapping_chat(uid))

    assert title_sweep() == []


def test_unreadable_pairs_give_jev_nothing_and_no_verdict(fresh_db):
    uid = quick_user()
    pk = swapping_chat(uid)
    break_pairs(uid, pk)
    evaluate = AsyncMock(return_value={"answers": {"dangerous": {"noul": 0.0}}})

    with patch.object(jev, "user_can_auto", return_value=True), patch.object(jev, "evaluate", new=evaluate):
        verdict = asyncio.run(jev.judge(uid, pk, {"tool": "run_terminal_command", "command": f"echo {OPENAI}"}))

    assert verdict is None
    evaluate.assert_not_awaited()


def test_read_url_keeps_the_stand_in(fresh_db):
    uid = quick_user()
    swap = secret_swap.for_thread(uid, swapping_chat(uid))
    fake = swap.tokenize(f"key {OPENAI}").split(" ", 1)[1]
    args = json.dumps({"url": f"https://api.example.com/v1/models?key={fake}"})

    assert swap.restore_args("read_url", args) == (args, True)


def test_unreadable_pairs_refuse_a_manual_compact(fresh_db):
    from tests.core.auth._support import sign_in, web_client

    uid = quick_user()
    thread = chats.create_thread(uid, model="offline", tools=[])
    pk = chats.resolve_thread_pk(uid, thread["id"])
    db.execute("UPDATE chat_threads SET secret_swap=1 WHERE id=?", (pk,))
    break_pairs(uid, pk)
    provider = FakeProvider(text_frames("summary"))

    with patch.object(api, "invoke_chat", new=provider), web_client() as client:
        sign_in(client, uid)
        res = client.post(f"/chat/threads/{thread['id']}/compact",
                          json={"expected_revision": thread["revision"], "command_id": "c1"})

    assert res.status_code == 409 and "decrypted" in res.text
    assert provider.bodies == []
