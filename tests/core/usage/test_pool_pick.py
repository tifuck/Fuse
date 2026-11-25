"""Which shared account a request picks (pool_pick.pick_account) on real
credential rows: health tiers, own accounts first, a warm session pin over
everything healthy, cooldown kinds, holds, plans, and what an empty pick says.
"""
from unittest.mock import patch

import pytest

from app import db
from app.sources import chatgpt, grok, pool_pick, zai
from app.sources.pool_pick import MAX_IN_FLIGHT, USAGE_BUSY, explain_empty, note_failure, pick_account, reset_health
from tests.core.usage._support import grok_extra, pool_account, pool_rows, user


@pytest.fixture
def ids(fresh_db):
    return {"alice": user("alice@core.test"), "donor": user("donor@core.test"), "bob": user("bob@core.test")}


def _rows(kind):
    return {int(r["id"]): r for r in pool_rows(kind)}


def _pick(kind, creds, **kwargs):
    picked = pick_account(creds, kind=kind, **kwargs)
    return int(picked[0]["id"]) if picked else None


def _at(when):
    return patch("app.sources.pool_pick.time.time", return_value=when)


def _fill(cid):
    for _ in range(MAX_IN_FLIGHT):
        pool_pick.reserve(cid)


def test_own_accounts_first_then_the_least_used_shared_account(ids):
    hot = pool_account("grok", ids["alice"], grok_extra(80))
    cool = pool_account("grok", ids["alice"], grok_extra(60))
    theirs = pool_account("grok", ids["donor"], grok_extra(5))
    rest = pool_account("grok", ids["bob"], grok_extra(30))
    busy = pool_account("grok", ids["alice"], grok_extra(USAGE_BUSY))
    busy_theirs = pool_account("grok", ids["donor"], grok_extra(USAGE_BUSY))
    rows = _rows("grok")
    pool = [rows[i] for i in (theirs, rest, hot, cool)]

    # A donor spends their own plan first, least used of theirs first.
    assert _pick("grok", pool, owner_id=ids["alice"]) == cool
    assert _pick("grok", pool, owner_id=ids["bob"]) == rest
    assert _pick("grok", pool) == theirs
    assert _pick("grok", pool, owner_id=ids["alice"], exclude={hot, cool}) == theirs
    # A busy own plan loses to any healthy one; with every plan busy, own goes first.
    assert _pick("grok", [rows[theirs], rows[busy]], owner_id=ids["alice"]) == theirs
    assert _pick("grok", [rows[busy_theirs], rows[busy]], owner_id=ids["alice"]) == busy
    assert _pick("grok", [rows[busy]]) == busy  # the only candidate still serves
    # Own accounts at the in-flight ceiling spill onto the pool.
    _fill(hot)
    _fill(cool)
    assert _pick("grok", pool, owner_id=ids["alice"]) == theirs


def test_unknown_usage_scores_as_zero_not_as_week_tokens(ids):
    known = pool_account("grok", ids["donor"], grok_extra(69))
    unread = pool_account("grok", ids["donor"], {"access_token": "t", "billing": {"fetched_at": "x", "tier": "SuperGrok"}})
    db.execute("UPDATE credentials SET week_tokens=24949597, week_start=? WHERE id=?", (db.iso_week_start(), unread))
    rows = _rows("grok")

    assert _pick("grok", [rows[known], rows[unread]]) == unread


def test_a_warm_session_pin_beats_usage_ownership_and_the_first_choice(ids):
    mine = pool_account("grok", ids["alice"], grok_extra(40))
    other_mine = pool_account("grok", ids["alice"], grok_extra(5))
    theirs = pool_account("grok", ids["donor"], grok_extra(10))
    busy = pool_account("grok", ids["donor"], grok_extra(USAGE_BUSY))
    rows = _rows("grok")
    alice = ids["alice"]

    assert _pick("grok", [rows[mine], rows[theirs]], prefer_id=theirs, owner_id=alice) == theirs
    assert _pick("grok", [rows[mine], rows[other_mine]], prefer_id=mine, owner_id=alice) == mine
    assert _pick("grok", [rows[mine], rows[busy]], prefer_id=busy, owner_id=alice) == busy
    assert _pick("grok", [rows[mine], rows[theirs]], prefer_id=mine, primary_id=theirs, owner_id=alice) == mine
    _fill(theirs)
    assert _pick("grok", [rows[mine], rows[theirs]], prefer_id=theirs, owner_id=alice) == theirs
    # A pin the caller cannot use (excluded after it failed this request,
    # or signed out) falls back to the usual order.
    pool_pick.reset_health()
    assert _pick("grok", [rows[mine], rows[theirs]], prefer_id=theirs, exclude={theirs}) == mine
    assert _pick("grok", [rows[mine], rows[theirs]], prefer_id=theirs,
                 is_authed=lambda cred, extra: int(cred["id"]) != theirs) == mine


def test_the_first_choice_beats_own_accounts_but_not_health(ids):
    mine = pool_account("grok", ids["alice"], grok_extra(10))
    theirs = pool_account("grok", ids["donor"], grok_extra(50))
    busy = pool_account("grok", ids["donor"], grok_extra(USAGE_BUSY))
    rows = _rows("grok")
    alice = ids["alice"]

    assert _pick("grok", [rows[mine], rows[theirs]], owner_id=alice, primary_id=theirs) == theirs
    assert _pick("grok", [rows[mine], rows[busy]], owner_id=alice, primary_id=busy) == mine
    assert _pick("grok", [rows[mine], rows[theirs]], owner_id=alice, primary_id=theirs, exclude={theirs}) == mine


def test_a_hard_cooldown_breaks_a_pin_but_a_blip_only_moves_other_conversations(ids):
    pinned = pool_account("grok", ids["donor"], grok_extra(USAGE_BUSY))
    other = pool_account("grok", ids["donor"], grok_extra(40))
    rows = _rows("grok")
    pool = [rows[pinned], rows[other]]

    note_failure(pinned, cooldown_seconds=60)
    assert _pick("grok", pool, prefer_id=pinned) == other

    pool_pick.reset_health()
    note_failure(pinned, cooldown_seconds=60, affinity_safe=True)
    assert _pick("grok", pool, prefer_id=pinned) == pinned
    assert _pick("grok", [rows[pinned]]) is None
    # A hard failure on top of the blip wins again.
    note_failure(pinned, cooldown_seconds=60)
    assert _pick("grok", pool, prefer_id=pinned) == other


def test_a_limited_account_is_skipped_but_asked_again_when_nothing_else_serves(ids):
    spent = pool_account("zai", ids["donor"], {"api_key": "a"})
    healthy = pool_account("zai", ids["donor"], {"api_key": "b"})
    rows = _rows("zai")
    with _at(1000):
        note_failure(spent, cooldown_seconds=5, affinity_safe=True)
    with _at(1001):
        note_failure(spent, cooldown_seconds=3600, limit=True)
    with _at(1010):
        assert _pick("zai", [rows[spent], rows[healthy]]) == healthy
        # Only the limit is left: the provider is asked, not a remembered answer.
        assert _pick("zai", [rows[spent]]) == spent
        assert _pick("zai", [rows[spent]], exclude={spent}) is None


def test_a_hard_cooldown_alone_explains_itself_with_its_reset(ids):
    cred = pool_account("zai", ids["donor"], {"api_key": "a"})
    rows = _rows("zai")
    with _at(1000):
        note_failure(cred, cooldown_seconds=3600, limit=True)
        note_failure(cred, cooldown_seconds=120)
    with _at(1050):
        why = []
        assert _pick("zai", [rows[cred]], why=why) is None
        result = explain_empty("zai", why)
    assert [reason for _cred, reason in why] == ["cooling"]
    assert (result.status, result.body["error"]["resets_at"]) == (503, 1120)
    with _at(1121):
        assert _pick("zai", [rows[cred]]) == cred


def test_an_off_site_hold_withholds_an_account_from_everyone_but_its_owner(ids):
    held = pool_account("grok", ids["alice"], grok_extra(10))
    other = pool_account("grok", ids["donor"], grok_extra(50))
    db.execute("UPDATE credentials SET pool_held_until='2999-01-01T00:00:00Z' WHERE id=?", (held,))
    rows = _rows("grok")

    def pick(creds, owner):
        why = []
        return _pick("grok", creds, owner_id=owner, is_held=grok.is_held, why=why), why

    assert pick([rows[held], rows[other]], ids["alice"])[0] == held
    assert pick([rows[held], rows[other]], ids["donor"])[0] == other
    picked, why = pick([rows[held]], ids["bob"])
    assert picked is None
    result = explain_empty("grok", why)
    assert result.status == 429 and result.body["error"]["code"] == "usage_limit_reached"


def test_an_account_known_to_have_no_plan_is_the_last_resort(ids):
    unsub = pool_account("grok", ids["alice"], {"access_token": "t", "billing": {"fetched_at": "x", "tier": "free"}})
    live = pool_account("grok", ids["donor"], grok_extra(90))
    busy = pool_account("grok", ids["donor"], grok_extra(USAGE_BUSY))
    fresh = pool_account("grok", ids["donor"], {"access_token": "t"})
    rows = _rows("grok")
    no_plan = grok.known_unsubscribed

    assert _pick("grok", [rows[unsub], rows[live]], no_plan=no_plan, owner_id=ids["alice"]) == live
    assert _pick("grok", [rows[unsub], rows[busy]], no_plan=no_plan) == busy
    assert _pick("grok", [rows[unsub]], no_plan=no_plan) == unsub
    # No billing read yet is not "no plan": a just-linked account serves.
    assert _pick("grok", [rows[unsub], rows[fresh]], no_plan=no_plan) == fresh
    assert zai.known_unsubscribed(rows[fresh], {"billing": {"fetched_at": "x"}})


def test_a_subagent_starts_on_its_parents_account_whenever_it_can_serve(ids):
    a = pool_account("grok", ids["donor"], grok_extra(10))
    b = pool_account("grok", ids["bob"], grok_extra(50))
    busy = pool_account("grok", ids["bob"], grok_extra(USAGE_BUSY))
    mine = pool_account("grok", ids["alice"], grok_extra(70))
    rows = _rows("grok")
    pool = [rows[a], rows[b]]

    assert _pick("grok", pool, hint_id=b) == b
    assert _pick("grok", pool, prefer_id=a, hint_id=b) == a  # its own pin wins
    # Its chat's account (a pick of someone else's account, or a busy one)
    # beats the caller's own accounts and lower usage.
    assert _pick("grok", [rows[a], rows[busy]], hint_id=busy) == busy
    assert _pick("grok", pool + [rows[mine]], hint_id=b, owner_id=ids["alice"]) == b
    note_failure(b, cooldown_seconds=60, limit=True)
    assert _pick("grok", pool, hint_id=b) == a
    reset_health()
    assert _pick("grok", pool, hint_id=b, exclude={b}) == a
    _fill(b)
    assert _pick("grok", pool, hint_id=b) == a


def _chatgpt(owner, plan, percent, **extra):
    seconds = 30 * 86400 if plan in ("free", "go") else 5 * 3600
    return pool_account("chatgpt", owner, {"refresh_token": "r", "access_token": "t", **extra, "billing": {
        "plan_type": plan, "windows": [{"percent": percent, "seconds": seconds}], **extra.pop("billing", {})}})


def test_chatgpt_free_capable_models_prefer_free_plans_and_paid_only_models_never_use_them(ids):
    free = _chatgpt(ids["donor"], "free", 40)
    go = _chatgpt(ids["donor"], "go", 50)
    plus = _chatgpt(ids["donor"], "plus", 1)
    busy_free = _chatgpt(ids["bob"], "free", USAGE_BUSY)
    rows = _rows("chatgpt")
    pool = [rows[free], rows[go], rows[plus]]

    for model in ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.5", "gpt-5.4-mini"):
        assert _pick("chatgpt", pool, model=model) == free, model
    assert _pick("chatgpt", pool, model="gpt-5.6-sol") == plus
    assert _pick("chatgpt", [rows[busy_free], rows[plus]], model="gpt-5.6-luna") == plus
    # A pin on a plan that cannot serve the model is not kept.
    assert _pick("chatgpt", pool, model="gpt-5.4", prefer_id=free) == plus
    assert _pick("chatgpt", [rows[free]], model="gpt-5.3-codex-spark") is None


def test_chatgpt_own_plan_beats_someone_elses_free_plan(ids):
    free = _chatgpt(ids["donor"], "free", 1)
    plus = _chatgpt(ids["alice"], "plus", 50)
    own_free = _chatgpt(ids["alice"], "free", 70)
    rows = _rows("chatgpt")

    assert _pick("chatgpt", [rows[free], rows[plus]], model="gpt-5.5", owner_id=ids["alice"]) == plus
    assert _pick("chatgpt", [rows[free], rows[plus], rows[own_free]], model="gpt-5.5", owner_id=ids["alice"]) == own_free
    assert _pick("chatgpt", [rows[free], rows[plus]], model="gpt-5.5", owner_id=ids["bob"]) == free


def test_signed_out_and_unreadable_accounts_are_skipped_and_explained(ids):
    dead = _chatgpt(ids["donor"], "free", 0, billing={"last_error": "http 401"})
    broken = pool_account("chatgpt", ids["donor"], {"refresh_token": "r"})
    db.execute("UPDATE credentials SET extra='not-a-ciphertext' WHERE id=?", (broken,))
    live = _chatgpt(ids["donor"], "free", 1)
    rows = _rows("chatgpt")

    assert _pick("chatgpt", list(rows.values()), model="gpt-5.6-luna", is_authed=chatgpt.credential_is_authed) == live
    why = []
    assert _pick("chatgpt", [rows[dead], rows[broken]], model="gpt-5.6-luna",
                 is_authed=chatgpt.credential_is_authed, why=why) is None
    assert sorted(reason for _cred, reason in why) == ["unauthed", "unauthed"]
    assert explain_empty("chatgpt", why).status == 401
    plan_why = []
    assert _pick("chatgpt", [rows[live]], model="gpt-5.6-sol", why=plan_why) is None
    assert explain_empty("chatgpt", plan_why, model="gpt-5.6-sol").body["error"]["code"] == "usage_not_included"
    assert explain_empty("chatgpt", []).status == 403


def test_one_poisonous_request_cannot_cool_the_whole_pool():
    failover = pool_pick.Failover()
    assert failover.server_error(1, 500, 30) is True
    assert failover.server_error(2, 502, 30) is False  # two accounts failed it: the request is the cause
    failover.failed(30)
    assert pool_pick.cooling_down(1) and not pool_pick.cooling_down(2)

    pool_pick.reset_health()
    served = pool_pick.Failover()
    served.server_error(3, 503, 30)
    served.served()  # another account served it: the 5xx was account 3's
    assert pool_pick.cooling_down(3) and not pool_pick._hard_cooldown(3)
    # A read timeout may already have run upstream; a connect timeout did not.
    assert pool_pick.Failover().server_error(4, 504, 30) is False
    assert pool_pick.Failover().server_error(4, 504, 30, phase="pre_generation") is True


def test_one_401_cools_an_account_and_a_second_within_the_hour_disables_it():
    with _at(1000):
        assert pool_pick.auth_failure_confirmed(9) is False
        assert pool_pick.cooling_down(9)
    with _at(1000 + pool_pick.AUTH_CONFIRM_WINDOW - 1):
        assert pool_pick.auth_failure_confirmed(9) is True
    with _at(5000):
        assert pool_pick.auth_failure_confirmed(9) is False  # strikes reset after confirming


def test_a_success_between_two_401s_clears_the_first_strike():
    with _at(1000):
        assert pool_pick.auth_failure_confirmed(9) is False
    pool_pick.forget_error(9)  # the account served a request since
    with _at(1000 + pool_pick.AUTH_CONFIRM_WINDOW - 1):
        assert pool_pick.auth_failure_confirmed(9) is False
