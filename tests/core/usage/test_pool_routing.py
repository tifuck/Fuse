"""Pool routing end to end through a real pool source's chat(): policy,
pick, failover, session pins and what a borrowed request may carry. Only the
upstream call (openai_chat) and the background usage refreshers are faked.
"""
import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from app import db
from app.sources import grok, kimi, mimo, pool_policy, pool_session, zai
from tests.core.usage._support import grok_extra, pool_account, user

BODY = {"model": "grok-4.6", "messages": [{"role": "user", "content": "hi"}]}
USAGE = {"prompt_tokens": 1, "completion_tokens": 1, "cached_tokens": 0}


def reply(status):
    return (status, {"id": "c", "usage": USAGE} if status == 200 else {"error": "x"}, None, dict(USAGE), {})


class Pool:
    """One pool source with its upstream faked; `sent` holds each payload."""

    REFRESHERS = {"grok": ("refresh_pool_billing",), "kimi": ("refresh_due", "refresh_pool_quota"),
                  "zai": ("refresh_pool_quota",), "mimo": ("refresh_pool_quota",)}
    MODULES = {"grok": (grok, grok.GrokSource), "kimi": (kimi, kimi.KimiSource),
               "zai": (zai, zai.ZaiSource), "mimo": (mimo, mimo.MimoSource)}

    def __init__(self, kind, stack):
        mod, cls = self.MODULES[kind]
        for name in self.REFRESHERS[kind]:
            stack.enter_context(patch.object(mod, name, new_callable=AsyncMock))
        self.upstream = stack.enter_context(patch.object(mod, "openai_chat", new_callable=AsyncMock,
                                                         return_value=reply(200)))
        self.source = cls({"id": pool_policy.source_id(kind), "name": kind}, {}, [])

    def chat(self, body=BODY, **kwargs):
        return asyncio.run(self.source.chat(dict(body), **kwargs))

    @property
    def sent(self):
        return [call.args[3] for call in self.upstream.await_args_list]


@pytest.fixture
def pools(fresh_db):
    with ExitStack() as stack:
        yield lambda kind="grok": Pool(kind, stack)


def _session(key, uid):
    return pool_session.session_id_from({}, {"prompt_cache_key": key}, user_id=uid, model="grok-4.6")


def test_own_account_first_then_the_pool_and_never_a_private_donor(pools):
    alice, donor, shy, outsider = (user(f"{n}@core.test") for n in ("alice", "donor", "shy", "outsider"))
    mine = pool_account("grok", alice, grok_extra(80))
    theirs = pool_account("grok", donor, grok_extra(5))
    secret = pool_account("grok", shy, grok_extra(1))
    pool_policy.set_user_pref(shy, "grok", share="private")
    pool = pools()

    assert pool.chat(user_id=alice).credential_id == mine
    # The private account at 1% is the least used, and still never reached.
    assert pool.chat(user_id=outsider).credential_id == theirs
    assert secret not in {int(c["id"]) for c in pool.source.creds}

    # Own plan rate-limited: the same request moves to the pool, unseen.
    pool.upstream.side_effect = [reply(429), reply(200)]
    result = pool.chat(user_id=alice)
    assert (result.status, result.credential_id, pool.upstream.await_count) == (200, theirs, 4)
    assert [a["credential_id"] for a in result.attempts] == [mine]


def test_a_left_out_own_account_is_never_used_even_as_the_last_resort(pools):
    alice, donor = user("alice@core.test"), user("donor@core.test")
    mine = pool_account("grok", alice, grok_extra(10))
    theirs = pool_account("grok", donor, grok_extra(50))
    pool_policy.set_account_choice(alice, "grok", skip=[mine], primary=None)
    pool = pools()
    pool.upstream.return_value = reply(429)

    result = pool.chat(user_id=alice)

    assert (result.status, result.credential_id, pool.upstream.await_count) == (429, theirs, 1)


def test_a_warm_conversation_keeps_its_account_across_a_restart_and_a_new_own_account(pools):
    alice, donor = user("alice@core.test"), user("donor@core.test")
    cool = pool_account("grok", donor, grok_extra(5))
    pool_account("grok", donor, grok_extra(40))
    pool_policy.set_admin_policy("grok", pool_shared=True, open_access=True)
    convo = _session("thread:1", alice)
    pool = pools()
    assert pool.chat(user_id=alice, session_id=convo).credential_id == cool

    mine = pool_account("grok", alice, grok_extra(0))
    pool_session.reset_store()  # a restart: only the durable pin is left
    assert pool.chat(user_id=alice, session_id=convo).credential_id == cool
    assert db.fetchone("SELECT credential_id FROM pool_sessions WHERE session_id=?", (convo,))["credential_id"] == cool
    # Own-first decides only new conversations.
    assert pool.chat(user_id=alice, session_id=_session("thread:2", alice)).credential_id == mine


def test_a_subagent_starts_on_its_parents_account_then_keeps_its_own_pin(pools):
    donor, caller = user("donor@core.test"), user("caller@core.test")
    cool = pool_account("grok", donor, grok_extra(5))
    warm = pool_account("grok", donor, grok_extra(40))
    parent, child = _session("thread:9", caller), _session("run:child", caller)
    pool_session.bind("grok", parent, warm)
    pool = pools()

    assert pool.chat(user_id=caller, session_id=child, affinity_id=parent).credential_id == warm
    assert pool_session.lookup("grok", child) == warm
    assert pool.chat(user_id=caller, session_id=_session("run:other", caller)).credential_id == cool
    pool_session.bind("grok", parent, cool)
    assert pool.chat(user_id=caller, session_id=child, affinity_id=parent).credential_id == warm


def test_an_owner_keeps_using_an_account_held_for_their_off_site_use(pools):
    alice, donor = user("alice@core.test"), user("donor@core.test")
    mine = pool_account("grok", alice, grok_extra(10))
    theirs = pool_account("grok", donor, grok_extra(50))
    db.execute("UPDATE credentials SET pool_held_until='2999-01-01T00:00:00Z' WHERE id=?", (mine,))
    pool = pools()

    pool_policy.set_user_pref(alice, "grok", use="mine")
    assert pool.chat(user_id=alice).credential_id == mine
    assert pool.chat(user_id=donor).credential_id == theirs
    outsider = user("outsider@core.test")
    pool_policy.set_user_pref(donor, "grok", share="private")
    result = pool.chat(user_id=outsider)
    assert result.status == 429 and result.credential_id is None


FN = {"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}
IMAGE = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
EXTRAS = {"grok": grok_extra(1), "kimi": {"access_token": "tok"}, "zai": {"api_key": "key"}, "mimo": {"api_key": "key"}}
MODELS = {"grok": "grok-4.7", "kimi": "k3", "zai": "glm-5.3", "mimo": "mimo-v2.6-pro"}


@pytest.mark.parametrize("kind", sorted(EXTRAS))
def test_a_borrowed_request_cannot_multiply_or_extend_the_donors_bill(pools, kind):
    donor, borrower = user("donor@core.test"), user("borrower@core.test")
    pool_account(kind, donor, EXTRAS[kind])
    pool = pools(kind)
    body = {"model": MODELS[kind], "messages": [{"role": "user", "content": [IMAGE]}], "stream": False, "n": 8,
            "service_tier": "priority", "tools": [FN, {"type": "web_search"}], "tool_choice": "auto",
            "max_tokens": 50}

    assert pool.chat(body, user_id=borrower, session_id="s").status == 200
    assert pool.chat(body, user_id=donor, session_id="s").status == 200

    lent, own = pool.sent
    assert "n" not in lent and "service_tier" not in lent
    assert lent["tools"] == [FN] and lent["messages"][0]["content"][0]["type"] == "image_url"
    assert own["n"] == 8 and own["service_tier"] == "priority" and len(own["tools"]) == 2


def test_strip_borrowed_keeps_generation_controls_and_drops_provider_side_tools():
    builtin = {"type": "builtin_function", "function": {"name": "$web_search"}}
    body = {"model": "m", "stream": True, "messages": [], "max_tokens": 9, "tools": [FN, builtin],
            "tool_choice": "auto", "reasoning_effort": "high", "prompt_cache_key": "fx:1", "n": 16,
            "best_of": 4, "search_parameters": {"mode": "on"}, "provider": {"only": ["x"]}}

    out = pool_policy.strip_borrowed(body)

    assert set(out) == {"model", "stream", "messages", "max_tokens", "tools", "tool_choice", "reasoning_effort",
                        "prompt_cache_key"}
    assert out["tools"] == [FN] and body["n"] == 16
    assert pool_policy.strip_borrowed({"tools": [builtin], "tool_choice": "required", "parallel_tool_calls": True}) == {}
    assert pool_policy.strip_borrowed({"tools": [FN], "tool_choice": {"type": "web_search"}}) == {"tools": [FN]}
    assert pool_policy.borrowed({"donated_by": 2}, None) and not pool_policy.borrowed({"donated_by": None}, 1)
