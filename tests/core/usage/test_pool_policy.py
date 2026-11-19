"""Which shared accounts a caller may be routed onto: each donor's share
level (everyone / contributors / private), each caller's use (pool / mine),
the admin's Shared and Open access switches, and per-user account choices.
"""
import json

import pytest

from app import db
from app.sources import devin, grok, pool_policy, zai
from tests.core.usage._support import grok_extra, pool_account, pool_rows, user

ZAI = {"api_key": "zk", "billing": {"plan_type": "pro"}}
DEVIN = {"api_key": "dk"}


@pytest.fixture
def people(fresh_db):
    return user("donor@core.test"), user("alice@core.test"), user("bob@core.test")


def _visible(caller, kind="grok", **kwargs):
    return {int(r["id"]) for r in pool_policy.filter_credentials(pool_rows(kind), kind, caller, **kwargs)}


def test_defaults_share_with_everyone_except_pay_as_you_go_pools(people):
    _donor, alice, _bob = people
    for kind in pool_policy.POOL_KINDS:
        share = "private" if kind == "refuseless" else "everyone"
        assert pool_policy.admin_policy(kind) == {"pool_shared": True, "open_access": False}
        assert pool_policy.user_pref(alice, kind) == {"share": share, "use": "pool"}
        assert pool_policy.uses_shared_pool(alice, kind) is (share != "private")


def test_share_levels_decide_who_reaches_a_donors_accounts(people):
    donor, alice, bob = people
    theirs = pool_account("grok", donor, grok_extra(10))
    hers = pool_account("grok", alice, grok_extra(10))
    assert _visible(bob) == {theirs, hers}

    pool_policy.set_user_pref(donor, "grok", share="contributors")
    # Alice shares a live account in this pool, so she counts; Bob and a
    # system caller (no user) do not.
    assert _visible(alice) == {theirs, hers}
    assert _visible(bob) == {hers}
    assert _visible(None) == {hers}

    pool_policy.set_user_pref(donor, "grok", share="private")
    assert _visible(alice) == {hers} and _visible(None) == {hers}
    assert _visible(donor) == {theirs}  # a private donor uses only their own accounts
    assert pool_policy.effective_pref(donor, "grok") == {"share": "private", "use": "mine"}
    # Other pools are unaffected by a grok preference.
    pool_account("zai", donor, ZAI)
    assert len(_visible(bob, "zai")) == 1


def test_use_mine_keeps_requests_on_own_accounts_but_still_shares_them(people):
    donor, alice, bob = people
    theirs = pool_account("grok", donor, grok_extra(10))
    hers = pool_account("grok", alice, grok_extra(10))
    pool_policy.set_user_pref(donor, "grok", share="contributors")

    pool_policy.set_user_pref(alice, "grok", share="everyone", use="mine")

    assert _visible(alice) == {hers}
    assert pool_policy.contributes(alice, "grok")  # sharing, though not borrowing
    assert _visible(bob) == {hers}
    assert theirs not in _visible(alice)


def test_a_dead_or_private_account_does_not_count_as_contributing(people):
    donor, alice, _bob = people
    theirs = pool_account("grok", donor, grok_extra(10))
    pool_policy.set_user_pref(donor, "grok", share="contributors")
    pool_account("grok", alice, {"access_token": ""})
    assert not pool_policy.contributes(alice, "grok") and theirs not in _visible(alice)

    pool_account("grok", alice, grok_extra(10))
    assert theirs in _visible(alice)
    pool_policy.set_user_pref(alice, "grok", share="private")
    assert not pool_policy.contributes(alice, "grok") and theirs not in _visible(alice)


def test_the_admin_shared_switch_off_isolates_everyone_whatever_they_chose(people):
    donor, alice, bob = people
    theirs = pool_account("grok", donor, grok_extra(10))
    hers = pool_account("grok", alice, grok_extra(10))
    pool_policy.set_account_choice(alice, "grok", skip=[hers], primary=theirs)

    pool_policy.set_admin_policy("grok", pool_shared=False, open_access=True)

    assert pool_policy.effective_pref(alice, "grok") == {"share": "private", "use": "mine"}
    assert _visible(alice) == {hers}  # her skip list does not apply either
    assert _visible(bob) == set() and _visible(None) == set()
    assert pool_policy.share_level("grok", donor) == "private"
    assert pool_policy.ui_state(alice, "grok")["can_override"] is False
    assert not grok.user_has_grok(bob)  # open access cannot open an unshared pool


def test_left_out_accounts_and_the_first_choice_apply_only_to_that_user(people):
    donor, alice, bob = people
    theirs = pool_account("grok", donor, grok_extra(10))
    hers = pool_account("grok", alice, grok_extra(10))

    pool_policy.set_account_choice(alice, "grok", skip=[hers], primary=theirs)

    assert _visible(alice) == {theirs}
    assert _visible(alice, chosen=False) == {theirs, hers}
    assert _visible(bob) == {theirs, hers}
    assert pool_policy.primary_account(alice, "grok") == theirs
    # Share/use writes keep the choice and the choice keeps share/use.
    pool_policy.set_user_pref(alice, "grok", share="contributors")
    assert pool_policy.account_choice(alice, "grok")["skip"] == {hers}
    assert pool_policy.user_pref(alice, "grok")["share"] == "contributors"
    # A left-out account is never the first choice.
    assert pool_policy.set_account_choice(alice, "grok", skip=[theirs], primary=theirs)["primary"] is None
    with pytest.raises(ValueError):
        pool_policy.set_account_choice(alice, "grok", skip=range(pool_policy.MAX_SKIP + 1), primary=None)


def test_legacy_own_only_reads_as_private_and_writes_keep_a_conservative_mirror(people):
    _donor, alice, _bob = people
    db.execute("UPDATE users SET pool_prefs=? WHERE id=?",
               (json.dumps({"grok": {"own_only": True}, "chatgpt": {"own_only": False}}), alice))
    pool_policy._share_cache.clear()

    assert pool_policy.user_pref(alice, "grok") == {"share": "private", "use": "mine"}
    assert pool_policy.user_pref(alice, "chatgpt") == {"share": "everyone", "use": "pool"}
    assert pool_policy.share_level("grok", alice) == "private"

    def stored():
        return pool_policy.user_prefs(alice)["grok"]

    pool_policy.set_user_pref(alice, "grok", share="everyone", use="pool")
    assert stored() == {"own_only": False, "share": "everyone", "use": "pool"}
    # Code rolled back to the own_only reader must never share more than chosen.
    pool_policy.set_user_pref(alice, "grok", share="contributors")
    assert stored()["own_only"] is True
    pool_policy.set_user_pref(alice, "grok", share="everyone", use="mine")
    assert stored()["own_only"] is True
    assert pool_policy.user_prefs(alice)["chatgpt"] == {"own_only": False}
    with pytest.raises(ValueError):
        pool_policy.set_user_pref(alice, "grok", share="friends")


def test_open_access_admits_only_onto_live_accounts_the_caller_can_reach(people):
    donor, alice, _bob = people
    pool_account("devin", donor, DEVIN)
    assert not devin.user_has_devin(alice)

    pool_policy.set_admin_policy("devin", pool_shared=True, open_access=True)
    assert devin.devin_access_state(alice) == {"ok": True, "reason": "open"}
    # The only live account is private or sharers-only: listing Devin would
    # then fail every request.
    for share, expected in (("private", False), ("contributors", False), ("everyone", True)):
        pool_policy.set_user_pref(donor, "devin", share=share)
        assert devin.user_has_devin(alice) is expected, share
    pool_policy.set_user_pref(alice, "devin", use="mine")
    assert not devin.user_has_devin(alice)

    held = pool_account("grok", donor, grok_extra(10))
    pool_policy.set_admin_policy("grok", pool_shared=True, open_access=True)
    assert grok.user_has_grok(alice)
    db.execute("UPDATE credentials SET pool_held_until='2999-01-01T00:00:00Z' WHERE id=?", (held,))
    assert not grok.user_has_grok(alice) and grok.user_has_grok(donor)


def test_zai_cross_unlock_needs_a_shared_live_xai_account_and_shared_routing(people):
    donor, alice, bob = people
    pool_account("zai", donor, ZAI)
    pool_account("grok", alice, grok_extra(10))
    assert zai.zai_access_state(alice) == {"ok": True, "reason": "pool"}

    pool_policy.set_user_pref(alice, "grok", share="private")
    assert not zai.user_has_zai(alice)
    pool_policy.set_user_pref(alice, "grok", share="contributors")
    assert zai.user_has_zai(alice)
    pool_policy.set_user_pref(alice, "zai", use="mine")
    assert not zai.user_has_zai(alice)
    pool_policy.set_user_pref(alice, "zai", use="pool")
    pool_policy.set_admin_policy("grok", pool_shared=False, open_access=False)
    assert not zai.user_has_zai(alice)
    # Grok open access does not cross-unlock Z.AI for someone without an account.
    pool_policy.set_admin_policy("grok", pool_shared=True, open_access=True)
    pool_policy.set_user_pref(alice, "grok", share="everyone")
    assert grok.user_has_grok(bob) and not zai.user_has_zai(bob)


def test_a_donor_new_since_the_share_cache_filled_gets_the_pools_default(people):
    _donor, alice, bob = people
    pool_policy.set_user_pref(bob, "refuseless", share="everyone")  # Bob borrows from the pool
    pool_account("refuseless", alice, {"api_key": "rk"})
    pool_account("grok", alice, grok_extra(10))
    assert _visible(bob, "refuseless") == set() and _visible(bob) != set()  # fills both caches
    carol = user("carol@core.test")

    late = pool_account("refuseless", carol, {"api_key": "rk2"})
    shared = pool_account("grok", carol, grok_extra(10))

    assert late not in _visible(bob, "refuseless") and late not in _visible(None, "refuseless")
    assert pool_policy.share_level("refuseless", carol) == "private"
    assert shared in _visible(bob) and pool_policy.share_level("grok", carol) == "everyone"
    # Sharing a pay-as-you-go key on purpose still reaches others.
    pool_policy.set_user_pref(carol, "refuseless", share="everyone")
    assert late in _visible(bob, "refuseless")
    assert pool_policy.share_level("refuseless", carol) == "everyone"


def test_a_share_read_that_raced_a_pref_write_is_not_cached(people, monkeypatch):
    donor, _alice, _bob = people
    real = pool_policy.db.fetchall
    raced = []

    def stale_then_write(sql, params=()):
        rows = real(sql, params)
        if "pool_prefs FROM users" in sql and not raced:
            raced.append(True)
            pool_policy.set_user_pref(donor, "grok", share="private")
        return rows

    monkeypatch.setattr(pool_policy.db, "fetchall", stale_then_write)
    assert pool_policy.share_level("grok", donor) == "everyone"  # read before the write
    assert pool_policy.share_level("grok", donor) == "private"


def test_turning_a_usage_guard_off_releases_every_held_account(people):
    donor, _alice, _bob = people
    held = pool_account("grok", donor, grok_extra(10))
    db.execute("UPDATE credentials SET pool_held_until='2999-01-01T00:00:00Z' WHERE id=?", (held,))
    assert pool_policy.guard_enabled("grok") and not pool_policy.guard_enabled("chatgpt")

    pool_policy.set_guard("grok", False)

    assert not pool_policy.guard_enabled("grok")
    assert db.fetchone("SELECT pool_held_until FROM credentials WHERE id=?", (held,))["pool_held_until"] is None
    with pytest.raises(ValueError):
        pool_policy.set_guard("chatgpt", True)
