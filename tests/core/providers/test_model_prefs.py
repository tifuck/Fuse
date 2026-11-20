"""Per-user model on/off: a model's own choice wins over its group, a group
switch sets every model of the group and leaves single rows editable, the
choices narrow every listing and refuse routing with a clear 403, and the
older OpenRouter and NanoGPT switches move over once."""
import asyncio
import json
import time

import httpx
import pytest
from fastapi import HTTPException

import app.sources as sources
from app import accounts, api, chat_runs, db
from app.providers import model_prefs
from app.sources import keyed, nanogpt, openrouter, pool_policy
from tests.core.helpers import quick_user

from ._support import key

OR_CATALOG = {"by_id": {"a/free:free": {"free": True}, "a/paid": {"free": False}, "b/paid": {"free": False}},
              "free_ids": ["a/free:free"], "paid_ids": ["a/paid", "b/paid"], "all_ids": []}
FREE, PAID, OTHER = "openrouter/a/free:free", "openrouter/a/paid", "openrouter/b/paid"


def test_a_single_choice_wins_and_a_group_switch_resets_its_group(fresh_db):
    openrouter._catalog = (time.time(), OR_CATALOG)
    uid = quick_user()
    names = [FREE, PAID, OTHER]
    assert model_prefs.visible("openrouter", uid, names) == names  # defaults: every group on

    model_prefs.set_group(uid, "openrouter", "paid", False)
    assert model_prefs.visible("openrouter", uid, names) == [FREE]
    # The bug: with a group on, single rows could not be turned off. Now they can,
    # and a row can also be turned on inside a group that is off.
    assert model_prefs.set_model(uid, "openrouter", PAID, True) is True
    assert model_prefs.set_model(uid, "openrouter", FREE, False) is False
    assert model_prefs.visible("openrouter", uid, names) == [PAID]

    # Turning the paid group on clears paid overrides only.
    model_prefs.set_group(uid, "openrouter", "paid", True)
    assert model_prefs.visible("openrouter", uid, names) == [PAID, OTHER]
    assert model_prefs.load(uid, "openrouter")["models"] == {FREE: False}

    model_prefs.set_all(uid, "openrouter", False)
    assert model_prefs.visible("openrouter", uid, names) == []
    model_prefs.set_all(uid, "openrouter", True)
    assert model_prefs.visible("openrouter", uid, names) == names and not model_prefs.load(uid, "openrouter")["models"]
    with pytest.raises(ValueError):
        model_prefs.set_group(uid, "openrouter", "nope", True)


def test_a_choice_matching_its_group_stores_nothing(fresh_db):
    uid = quick_user()
    model_prefs.set_model(uid, "deepseek", "deepseek/x", False)
    model_prefs.set_model(uid, "deepseek", "deepseek/x", True)
    assert model_prefs.load(uid, "deepseek") == {"groups": {}, "models": {}}


def test_turned_off_models_leave_the_listing_and_are_refused_by_name(fresh_db, upstream):
    alice, bob = quick_user("a@core.test"), quick_user("b@core.test")
    sid = keyed.ensure_source("deepseek")
    key(sid, "sk-deepseek-alice-1", owner=alice)
    key(sid, "sk-deepseek-bob-123", owner=bob)
    upstream.on("GET", "api.deepseek.com/models",
                httpx.Response(200, json={"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}))
    model_prefs.set_model(alice, "deepseek", "deepseek/deepseek-reasoner", False)

    assert asyncio.run(sources.models_union(user_id=alice)) == ["deepseek/deepseek-chat"]
    assert len(asyncio.run(sources.models_union(user_id=bob))) == 2  # her choice is hers
    groups = asyncio.run(sources.models_by_source(user_id=alice))
    assert [g["models"] for g in groups if g["type"] == "deepseek"] == [["deepseek/deepseek-chat"]]

    assert asyncio.run(sources.pick("deepseek/deepseek-reasoner", user_id=alice)) is None
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api.invoke_chat(chat_runs.internal_request(alice), {"id": alice},
                                    {"model": "deepseek/deepseek-reasoner", "messages": [{"role": "user", "content": "x"}]}))
    assert caught.value.status_code == 403
    assert caught.value.detail == ("DeepSeek: deepseek/deepseek-reasoner is turned off for your account. "
                                   "Turn it on in Sources.")


def test_openrouter_rows_stay_editable_under_the_free_switch(fresh_db, upstream):
    openrouter._catalog = (time.time(), OR_CATALOG)
    sid = openrouter.ensure_openrouter_source()
    uid = quick_user()
    key(sid, "sk-or-user-key-1", owner=uid, extra={"role": "user", "api_key": "sk-or-user-key-1"})
    assert asyncio.run(openrouter.resolve(FREE, uid)) == ("a/free:free", True)
    model_prefs.set_model(uid, "openrouter", FREE, False)  # free group stays on
    assert openrouter.free_models_enabled(uid)
    assert asyncio.run(openrouter.resolve(FREE, uid)) is None
    assert openrouter.denied_reason(FREE, uid) == "That OpenRouter model is turned off for your account. Turn it on under Sources."


def test_older_switches_move_over_once(fresh_db):
    openrouter._catalog = (time.time(), OR_CATALOG)
    nanogpt._catalog = (time.time(), {"by_id": {"s": {"subscription": True}, "p": {"subscription": False}}})
    or_sid = openrouter.ensure_openrouter_source()
    a, b, c = quick_user("a@core.test"), quick_user("b@core.test"), quick_user("c@core.test")
    extra = {"role": "user", "free_models": False, "paid_models": {"all": False, "enabled": ["a/paid"]}}
    key(or_sid, "sk-or-legacy-key-a", owner=a, extra={**extra, "api_key": "sk-or-legacy-key-a"})
    key(or_sid, "sk-or-legacy-key-b", owner=b, extra={"role": "user", "api_key": "sk-or-legacy-key-b"})  # defaults
    db.execute("UPDATE users SET pool_prefs=? WHERE id=?", (json.dumps({"nanogpt": {
        "share": "private", "models": {"subscription": False, "paid_all": False, "paid": ["p"],
                                       "providers": {"p": ["x"]}}}}), c))

    assert model_prefs.migrate_legacy() == 2
    assert model_prefs.load(a, "openrouter") == {"groups": {"free": False, "paid": False}, "models": {PAID: True}}
    assert model_prefs.load(b, "openrouter") == {"groups": {}, "models": {}}
    assert model_prefs.load(c, "nanogpt") == {"groups": {"subscription": False}, "models": {"nanogpt/p": True}}
    assert nanogpt.model_prefs(c)["providers"] == {"p": ["x"]}  # pins stay where they were
    assert pool_policy.user_pref(c, "nanogpt")["share"] == "private"
    # A later change is the user's: running again does not overwrite it.
    model_prefs.set_all(a, "openrouter", True)
    assert model_prefs.migrate_legacy() == 0
    assert model_prefs.load(a, "openrouter")["models"] == {}
    assert accounts.cred_extra(db.fetchone("SELECT * FROM credentials WHERE donated_by=?", (a,)))["free_models"] is False
