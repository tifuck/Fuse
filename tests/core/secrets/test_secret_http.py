"""The secret protection endpoints: an offer's values go only to the chat's
owner, and only while a turn of that chat waits on them; decisions and the
settings changes are validated and never return a stored value."""
from types import SimpleNamespace

import pytest

from app import chats, secret_guard, secret_scan
from tests.core.auth._support import anonymous, refused, sign_in, web_client
from tests.core.helpers import quick_user

from ._values import GITHUB, OPENAI, PASSWORD


@pytest.fixture
def web(fresh_db):
    with web_client() as client:
        yield client


def waiting_chat(uid):
    """A chat whose turn waits on two offered values."""
    thread = chats.create_thread(uid, model="offline", tools=[])
    pk = chats.resolve_thread_pk(uid, thread["id"])
    secret_guard.offer(uid, 1, "box", secret_scan.find(".env", f"OPENAI_API_KEY={OPENAI}\nDB_PASSWORD={PASSWORD}\n"))
    woken = []
    event = SimpleNamespace(set=lambda: woken.append(True))
    secret_guard._waits[pk] = (uid, event, SimpleNamespace(call_soon_threadsafe=lambda fn: fn()))
    return thread["id"], pk, woken


def test_an_offer_is_shown_only_to_its_owner_while_the_turn_waits(web):
    uid, other = quick_user(), quick_user("other@core.test")
    tid, pk, _woken = waiting_chat(uid)
    try:
        url = f"/chat/threads/{tid}/secrets/offer"
        anonymous(web)
        assert refused(web.get(url))
        sign_in(web, other)
        assert web.get(url).status_code == 404
        sign_in(web, uid)
        got = web.get(url)
        assert got.headers["cache-control"] == "no-store"
        assert [(i["key"], i["value"], i["file"], i["line"]) for i in got.json()["items"]] == [
            ("OPENAI_API_KEY", OPENAI, ".env", 1), ("DB_PASSWORD", PASSWORD, ".env", 2)]
        # A chat of the same owner with no waiting turn shows nothing.
        idle = chats.create_thread(uid, model="offline", tools=[])["id"]
        assert web.get(f"/chat/threads/{idle}/secrets/offer").json() == {"pending": False, "items": []}
    finally:
        secret_guard._waits.pop(pk, None)


def test_a_decision_protects_the_ticked_values_and_wakes_the_turn(web):
    uid = quick_user()
    tid, pk, woken = waiting_chat(uid)
    try:
        sign_in(web, uid)
        url = f"/chat/threads/{tid}/secrets/offer"
        first = web.get(url).json()["items"][0]["id"]
        for bad in ({"protect": "all"}, {"protect": [True]}, {"protect": ["1"]}, {"protect": [first], "never": "yes"},
                    {"protect": [first], "extra": 1}):
            assert web.post(url, json=bad).status_code == 422
        assert secret_guard.has_offers(uid)
        # An offer that arrived after the window was drawn is not declined unseen.
        secret_guard.offer(uid, 1, "box", secret_scan.find(".env", f"GITHUB_TOKEN={GITHUB}\n"))
        shown = [i["id"] for i in web.get(url).json()["items"]][:2]
        assert web.post(url, json={"protect": [first], "shown": "all"}).status_code == 422
        assert web.post(url, json={"protect": [first], "shown": shown}).json() == {"ok": True, "protected": 1}
        assert woken and [o["value"] for o in secret_guard.offers(uid)] == [GITHUB]
        assert [it.real for it in secret_guard.map_for(uid).items] == [OPENAI]
        assert web.post(url, json={"protect": []}).json() == {"ok": True, "protected": 0}
        assert not secret_guard.has_offers(uid)
    finally:
        secret_guard._waits.pop(pk, None)


def test_settings_never_return_a_value(web):
    uid, other = quick_user(), quick_user("other@core.test")
    anonymous(web)
    assert refused(web.get("/settings/secrets")) and refused(web.post("/settings/secrets", json={"value": GITHUB}))
    sign_in(web, uid)
    kept = web.post("/settings/secrets", json={"label": "GITHUB_TOKEN", "value": GITHUB}).json()
    (item,) = kept["items"]
    assert (item["label"], item["status"], item["length"], item["where"]) == ("GITHUB_TOKEN", "protected", len(GITHUB), "Added by you")
    assert GITHUB not in web.get("/settings/secrets").text and GITHUB not in web.get("/settings").text
    assert web.post("/settings/secrets", json={"label": "X", "value": "short"}).status_code == 422
    assert web.post("/settings/secrets", json={"id": item["id"], "protected": False}).json()["items"][0]["status"] == "retired"
    assert web.post("/settings/secrets", json={"protect": False, "offer": False}).json()["offer"] is False
    assert not secret_guard.offers_enabled(uid)
    # Another account cannot touch it.
    sign_in(web, other)
    assert web.delete(f"/settings/secrets/{item['id']}").status_code == 404
    assert web.post("/settings/secrets", json={"id": item["id"], "protected": True}).status_code == 404
    sign_in(web, uid)
    assert web.delete(f"/settings/secrets/{item['id']}").json()["items"] == []
