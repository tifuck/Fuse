"""The chat page's pool badge: a slot letter and usage windows for the account
that served the chat, never its label, email, owner or credential id."""
import json

from app.sources import pool_badge, pool_pick
from tests.core.usage._support import pool_account, user


def _claude(five_hour, weekly, reset="2099-01-01T00:00:00Z"):
    return {"access_token": "tok", "billing": {"windows": [
        {"id": "five_hour", "label": "5-hour", "percent": five_hour, "resets_at": reset},
        {"id": "seven_day", "label": "Weekly", "percent": weekly, "resets_at": ""},
    ]}}


def test_badge_names_the_account_by_slot_and_hides_who_owns_it(fresh_db):
    donor, me = user("donor@core.test"), user("me@core.test")
    theirs = pool_account("claude", donor, _claude(42, 18), secret="donor-secret")
    mine = pool_account("claude", me, _claude(5, 1))

    view = pool_badge.public(me, pool_badge.state(None, theirs, "claude", "claude-opus-4-7"))

    assert view["slot"] == "A" and view["own"] is False and view["hop"] is None
    assert [(w["short"], w["percent"]) for w in view["windows"]] == [("5h", 42), ("wk", 18)]
    assert view["size"] == 2 and view["model"] == "claude-opus-4-7"
    sent = json.dumps(view)
    for leak in ("donor@core.test", "donor-secret", f'"{theirs}"', "cred"):
        assert leak not in sent
    assert pool_badge.public(me, pool_badge.state(None, mine, "claude", "m"))["own"] is True


def test_a_switch_records_the_slot_left_and_why(fresh_db):
    me = user("me@core.test")
    first = pool_account("claude", me, _claude(99, 50))
    second = pool_account("claude", me, _claude(3, 1))

    st = pool_badge.state(None, first, "claude", "m")
    pool_pick.note_failure(first, cooldown_seconds=60, limit=True)
    st = pool_badge.state(st, second, "claude", "m")

    view = pool_badge.public(me, st)
    assert view["slot"] == "B" and view["hop"] == {"from": "A", "reason": "limit"}
    assert view["ready"] == 1
    # The same account serving again keeps the record of the last switch.
    assert pool_badge.state(st, second, "claude", "m2")["prev"] == first


def test_windows_read_spans_and_restart_after_their_reset(fresh_db):
    me = user("me@core.test")
    cid = pool_account("chatgpt", me, {"billing": {"windows": [
        {"name": "codex", "percent": 80, "seconds": 18000, "reset_at": "2000-01-01T00:00:00Z"},
        {"name": "codex", "percent": 30, "seconds": 604800, "reset_at": 4102444800},
    ]}})

    windows = pool_badge.public(me, pool_badge.state(None, cid, "chatgpt", "m"))["windows"]

    assert windows == [
        {"label": "5-hour", "short": "5h", "percent": 0, "reset_at": None},
        {"label": "Weekly", "short": "wk", "percent": 30, "reset_at": 4102444800},
    ]


def test_a_malformed_record_shows_no_badge(fresh_db):
    me = user("me@core.test")
    assert pool_badge.public(me, None) is None
    assert pool_badge.public(me, {"cred": "x", "source": "claude"}) is None
    assert pool_badge.public(me, {"cred": 3, "source": "llama"}) is None
