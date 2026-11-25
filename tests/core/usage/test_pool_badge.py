"""The chat page's pool card: the serving account's plan limits, every account
the caller can reach, named as the Sources page names them, and the caller's
sharing settings, which the card saves through the Sources endpoint."""
import asyncio
import json

from app.config import ROOT
from app.sources import claude, pool_badge, pool_pick, pool_policy, pool_session
from tests.core.usage._support import pool_account, user


def _claude_email(five_hour, weekly, email):
    data = _claude(five_hour, weekly)
    data["email"] = email
    return data


def _runtime_session(me, thread):
    """The pool session the chat runtime's requests for this chat use: its
    cache key "thread:<pk>" (chat_runtime), scoped like any client key."""
    from app import chats

    return pool_session.related_session_id(f"thread:{chats.resolve_thread_pk(me, thread)}", user_id=me, model="claude-opus-5-5")


def _claude(five_hour, weekly, reset="2099-01-01T00:00:00Z"):
    return {"access_token": "tok", "billing": {"windows": [
        {"id": "five_hour", "label": "5-hour", "percent": five_hour, "resets_at": reset},
        {"id": "seven_day", "label": "Weekly", "percent": weekly, "resets_at": ""},
    ]}}


def test_accounts_are_named_as_the_sources_page_names_them(fresh_db):
    donor, me, admin = user("donor@core.test"), user("me@core.test"), user("root@core.test", admin=True)
    theirs = pool_account("claude", donor, _claude_email(42, 18, "donor.plan@example.com"), secret="donor-secret")
    mine = pool_account("claude", me, _claude_email(5, 1, "me.work@example.com"))

    view = pool_badge.public(me, pool_badge.state(None, theirs, "claude", "claude-opus-4-7"))

    assert view["name"] == "Claude plan 1" and view["own"] is False and view["hop"] is None
    assert [(a["id"], a["name"], a["own"]) for a in view["accounts"]] == [
        (theirs, "Claude plan 1", False), (mine, "me.work@example.com", True)]
    assert [(w["short"], w["percent"]) for w in view["windows"]] == [("5h", 42), ("7d", 18)]
    assert view["size"] == 2 and view["model"] == "claude-opus-4-7"
    sent = json.dumps(view)
    for leak in ("donor.plan@example.com", "donor@core.test", "donor-secret"):
        assert leak not in sent
    # An admin sees every account by its email, as on Sources.
    names = [a["name"] for a in pool_badge.public(admin, pool_badge.state(None, theirs, "claude", "m"))["accounts"]]
    assert names == ["donor.plan@example.com", "me.work@example.com"]


def test_a_switch_records_the_account_left_and_why(fresh_db):
    me = user("me@core.test")
    first = pool_account("claude", me, _claude(99, 50))
    second = pool_account("claude", me, _claude(3, 1))

    st = pool_badge.state(None, first, "claude", "m")
    pool_pick.note_failure(first, cooldown_seconds=60, limit=True)
    st = pool_badge.state(st, second, "claude", "m")

    view = pool_badge.public(me, st)
    assert view["id"] == second and view["hop"] == {"from": "claude", "reason": "limit"}
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
        {"label": "Weekly", "short": "7d", "percent": 30, "reset_at": 4102444800},
    ]


def test_model_and_product_limits_and_plan_come_with_the_plan_windows(fresh_db):
    me = user("me@core.test")
    claude = _claude(42, 18)
    claude["billing"]["model_windows"] = [{"id": "seven_day_fable", "label": "Fable weekly", "percent": 61, "resets_at": ""}]
    claude["billing"]["plan_type"] = "Max 20x"
    cid = pool_account("claude", me, claude)
    cursor = pool_account("cursor", me, {"billing": {"credit_usage_percent": 37, "period_type": "MONTH", "plan_type": "pro",
                                                     "products": [{"product": "Auto + Composer", "usage_percent": 12}]}})

    view = pool_badge.public(me, pool_badge.state(None, cid, "claude", "m"))
    assert view["plan"] == "Max 20x"
    assert [(w["label"], w["percent"]) for w in view["limits"]] == [("Fable weekly", 61)]

    view = pool_badge.public(me, pool_badge.state(None, cursor, "cursor", "m"))
    assert [(w["label"], w["short"], w["percent"]) for w in view["windows"]] == [("Monthly", "30d", 37)]
    assert [(w["label"], w["percent"]) for w in view["limits"]] == [("Auto + Composer", 12)]
    assert view["plan"] == "Pro"


def test_the_account_list_shows_every_reachable_account_and_its_state(fresh_db):
    donor, me = user("donor@core.test"), user("me@core.test")
    serving = pool_account("claude", me, _claude(42, 18))
    spent = pool_account("claude", donor, _claude(99, 70), secret="donor-secret")
    pool_account("claude", donor, _claude(99, 20))
    left_out = pool_account("claude", me, _claude(1, 1))
    pool_pick.note_failure(spent, cooldown_seconds=600, limit=True)
    pool_policy.set_account_choice(me, "claude", skip=[left_out], primary=None)

    rows = pool_badge.public(me, pool_badge.state(None, serving, "claude", "m"))["accounts"]

    assert [(a["id"], a["own"], a["status"], a["percent"]) for a in rows] == [
        (serving, True, "current", 42), (spent, False, "limit", 99), (rows[2]["id"], False, "full", 99), (left_out, True, "off", 1)]
    assert rows[1]["until"] > 0 and rows[1]["name"] == "Claude plan 2"
    assert "donor@core.test" not in json.dumps(rows)


def test_the_card_saves_sharing_through_the_sources_endpoint(fresh_db):
    from tests.core.auth._support import sign_in, web_client

    (ROOT / "data" / "desktop-releases").mkdir(parents=True, exist_ok=True)
    me = user("me@core.test")
    first = pool_account("claude", me, _claude(5, 1))
    second = pool_account("claude", me, _claude(9, 2))
    with web_client() as client:
        sign_in(client, me)
        res = client.post("/sources/pool/claude/prefs", headers={"Accept": "application/json"}, data={
            "share": "contributors", "use": "mine", "accounts_listed": f"{first},{second}",
            "account": [str(second)], "primary": str(second)})
        assert res.status_code == 200 and res.json()["ok"] is True
        prefs = res.json()["prefs"]
        assert (prefs["share"], prefs["use"], prefs["skip"], prefs["primary"]) == ("contributors", "mine", [first], second)
        # A non-ASCII digit ("²" passes isdigit, not int) is ignored like any junk id.
        res = client.post("/sources/pool/claude/prefs", headers={"Accept": "application/json"}, data={
            "share": "contributors", "use": "mine", "accounts_listed": f"{first},{second},²",
            "account": [str(second), "²"], "primary": "²"})
        assert res.status_code == 200 and res.json()["ok"] is True
        assert (res.json()["prefs"]["skip"], res.json()["prefs"]["primary"]) == ([first], None)
        # Leaving out every account is refused, and the reply says so.
        res = client.post("/sources/pool/claude/prefs", headers={"Accept": "application/json"}, data={
            "share": "contributors", "use": "mine", "accounts_listed": f"{first},{second}"})
        assert res.json()["ok"] is False and "at least one" in res.json()["message"]
        # The Sources form still gets its page back.
        res = client.post("/sources/pool/claude/prefs", data={"share": "everyone", "use": "pool"}, follow_redirects=False)
        assert res.status_code == 303 and res.headers["location"] == "/sources#pool-claude"


def test_a_malformed_record_shows_no_badge(fresh_db):
    me = user("me@core.test")
    assert pool_badge.public(me, None) is None
    assert pool_badge.public(me, {"cred": "x", "source": "claude"}) is None
    assert pool_badge.public(me, {"cred": 3, "source": "llama"}) is None


def test_before_a_reply_the_gauge_shows_the_account_the_next_request_uses(fresh_db, monkeypatch):
    me = user("me@core.test")
    busy = pool_account("claude", me, _claude(70, 60))
    fresh = pool_account("claude", me, _claude(5, 1))
    src = claude.ClaudeSource({"id": pool_policy.source_id("claude"), "name": "Claude"}, {}, [])

    async def pick(model, *, user_id=None, access=None, ignore_prefs=False):
        return src, "claude-opus-5-5"

    import app.sources as sources
    monkeypatch.setattr(sources, "pick", pick)
    monkeypatch.setattr(pool_session, "persist", False)

    # A new chat: the account the pool's picker chooses now, a best guess.
    view = asyncio.run(pool_badge.preview(me, "claude/claude-opus-5-5"))
    assert view["id"] == fresh and view["preview"] is True and view["model"] == "claude/claude-opus-5-5"
    # A chat pinned to an account: that account, exactly, and the pin is not refreshed.
    from app import chats

    thread = chats.create_thread(me, model="claude/claude-opus-5-5")["id"]
    sid = _runtime_session(me, thread)
    pool_session.bind("claude", sid, busy)
    stamp = pool_session._mem[("claude", sid)]
    view = asyncio.run(pool_badge.preview(me, "claude/claude-opus-5-5", thread))
    assert view["id"] == busy and view["preview"] is False
    assert pool_session._mem[("claude", sid)] == stamp
    assert src.creds == []  # the live source is untouched


def test_an_idle_chats_reread_starts_the_pools_usage_refresh(fresh_db, monkeypatch):
    me = user("me@core.test")
    pool_account("claude", me, _claude(5, 1))
    src = claude.ClaudeSource({"id": pool_policy.source_id("claude"), "name": "Claude"}, {}, [])

    async def pick(model, *, user_id=None, access=None, ignore_prefs=False):
        return src, "claude-opus-5-5"

    import app.sources as sources
    from app.sources import pool_refresh

    started = []
    monkeypatch.setattr(sources, "pick", pick)
    monkeypatch.setattr(pool_session, "persist", False)
    monkeypatch.setattr(pool_refresh, "start", lambda name, refresh: started.append((name, refresh)))

    asyncio.run(pool_badge.preview(me, "claude/claude-opus-5-5"))
    assert started == []
    asyncio.run(pool_badge.preview(me, "claude/claude-opus-5-5", refresh=True))
    assert started == [("claude", claude.refresh_pool_quota)]


def test_every_pool_refresh_names_a_function_of_its_source():
    import importlib

    for kind, name in pool_badge.POOL_REFRESH.items():
        assert callable(getattr(importlib.import_module(f"app.sources.{kind}"), name)), kind


def test_money_left_shows_to_its_owner_only_and_a_spent_subscription_hides_it(fresh_db):
    donor, me = user("donor@core.test"), user("me@core.test")
    from app.sources import refuseless

    prepaid = refuseless.parse_credits({"available": 12.5})
    mine = pool_account("refuseless", me, {"billing": prepaid})
    empty = pool_account("refuseless", me, {"billing": refuseless.parse_credits({"available": 0})})
    view = pool_badge.public(me, pool_badge.state(None, mine, "refuseless", "m"))
    assert view["windows"] == [] and view["balance"] == {"amount": 12.5, "currency": "USD"}
    # Spent prepaid credit is all a Refuseless key reports, so its owner sees $0.
    assert pool_badge.public(me, pool_badge.state(None, empty, "refuseless", "m"))["balance"] == {"amount": 0.0, "currency": "USD"}

    theirs = _claude(10, 5)
    theirs["billing"]["prepaid_cents"] = 500
    theirs = pool_account("claude", donor, theirs, secret="donor-secret")
    assert pool_badge.public(me, pool_badge.state(None, theirs, "claude", "m"))["balance"] is None
    assert [a["balance"] for a in pool_badge.public(me, pool_badge.state(None, theirs, "claude", "m"))["accounts"]] == [None]
    assert pool_badge.public(donor, pool_badge.state(None, theirs, "claude", "m"))["balance"] == {"amount": 5.0, "currency": "USD"}
    # A subscription's usage is the gauge; an empty prepaid balance beside it is noise.
    grok_sub = pool_account("grok", me, {"billing": {"credit_usage_percent": 20, "prepaid_cents": 0}})
    assert pool_badge.public(me, pool_badge.state(None, grok_sub, "grok", "m"))["balance"] is None


def test_plan_windows_listed_again_as_products_are_not_model_limits(fresh_db):
    me = user("me@core.test")
    cid = pool_account("zai", me, {"billing": {"windows": [{"name": "5-hour", "percent": 10, "seconds": 18000},
                                                           {"name": "Weekly", "percent": 30, "seconds": 604800}],
                                               "products": [{"product": "5-hour", "usage_percent": 10},
                                                            {"product": "Weekly", "usage_percent": 30},
                                                            {"product": "MCP", "usage_percent": 4}]}})

    view = pool_badge.public(me, pool_badge.state(None, cid, "zai", "m"))

    assert [w["label"] for w in view["windows"]] == ["5-hour", "Weekly"]
    assert [(w["label"], w["percent"]) for w in view["limits"]] == [("MCP", 4)]


def _picks(monkeypatch, src):
    async def pick(model, *, user_id=None, access=None, ignore_prefs=False):
        return src, "m"

    import app.sources as sources
    monkeypatch.setattr(sources, "pick", pick)


def test_an_openrouter_key_shows_its_spend_limit_and_the_money_left(fresh_db, monkeypatch):
    from app.sources import openrouter

    me = user("me@core.test")
    row = {"id": 7, "label": "My key", "donated_by": me}
    info = {"limit": 50.0, "limit_remaining": 40.0, "credits_remaining": None,
            "effective_budget": {"limit_usd": 20.0, "remaining_usd": 5.0, "reset_interval": "weekly", "resets_at": "2099-01-01T00:00:00Z"}}

    async def refresh(cred, *, force=False):
        return info

    _picks(monkeypatch, type("Src", (), {"type_id": "openrouter", "source_id": 1})())
    monkeypatch.setattr(openrouter, "pick_key_for", lambda user_id, source_id=None: (row, {}, "k", "user"))
    monkeypatch.setattr(openrouter, "refresh_key_info", refresh)

    view = asyncio.run(pool_badge.preview(me, "openrouter/x"))
    assert (view["provider"], view["name"], view["own"], view["prefs"], view["accounts"]) == ("openrouter", "My key", True, None, [])
    assert [(w["label"], w["percent"]) for w in view["windows"]] == [("Weekly limit", 75)]
    assert view["balance"] == {"amount": 5.0, "currency": "USD"}
    # The account's credits, when the key may read them, are the money left.
    info["credits_remaining"] = 61.5
    assert asyncio.run(pool_badge.preview(me, "openrouter/x"))["balance"] == {"amount": 61.5, "currency": "USD"}
    # An unlimited key that cannot read credits reports nothing to show.
    info.update(effective_budget=None, limit=None, limit_remaining=None, credits_remaining=None)
    assert asyncio.run(pool_badge.preview(me, "openrouter/x")) is None


def test_keyed_balances_parse_and_show_only_on_the_callers_own_key(fresh_db, monkeypatch):
    from app.providers import registry
    from app.sources import keyed

    assert keyed.parse_balance({"is_available": True, "balance_infos": [
        {"currency": "CNY", "total_balance": "70.00"}, {"currency": "USD", "total_balance": "9.80"}]}) == {"amount": 9.8, "currency": "USD"}
    assert keyed.parse_balance({"code": 0, "data": {"available_balance": 49.58894}}) == {"amount": 49.58894, "currency": "USD"}
    assert keyed.parse_balance({"error": {"message": "nope"}}) is None

    me = user("me@core.test")
    preset = registry.get("deepseek").key
    rows = [{"id": 3, "label": "", "donated_by": me}]
    src = type("Src", (), {"type_id": "deepseek", "preset": preset, "usable": lambda self, user_id: rows})()

    async def refresh(p, row, *, force=False):
        return {"amount": 9.8, "currency": "USD"}

    _picks(monkeypatch, src)
    monkeypatch.setattr(keyed, "refresh_balance", refresh)

    view = asyncio.run(pool_badge.preview(me, "deepseek/x"))
    assert (view["name"], view["balance"], view["windows"]) == ("DeepSeek key", {"amount": 9.8, "currency": "USD"}, [])
    # A site key's balance is its admin's business.
    rows[0] = {"id": 4, "label": "Site", "donated_by": None}
    assert asyncio.run(pool_badge.preview(me, "deepseek/x")) is None


def test_a_click_moves_the_chat_to_another_account_of_its_pool(fresh_db, monkeypatch):
    from app import chats, context_compact, db

    me, stranger = user("me@core.test"), user("stranger@core.test")
    first = pool_account("claude", me, _claude(70, 60))
    second = pool_account("claude", me, _claude(5, 1))
    theirs = pool_account("claude", stranger, _claude(1, 1))
    pool_policy.set_user_pref(stranger, "claude", share="private")
    src = claude.ClaudeSource({"id": pool_policy.source_id("claude"), "name": "Claude"}, {}, [])

    async def pick(model, *, user_id=None, access=None, ignore_prefs=False):
        return src, "claude-opus-5-5"

    import app.sources as sources
    monkeypatch.setattr(sources, "pick", pick)
    monkeypatch.setattr(pool_session, "persist", False)
    thread = chats.create_thread(me, model="claude/claude-opus-5-5")["id"]
    sid = _runtime_session(me, thread)
    pool_session.bind("claude", sid, first)
    from app import chat_checkpoint
    chat_checkpoint.save_pool(me, thread, lambda prior: pool_badge.state(prior, first, "claude", "claude/claude-opus-5-5"))

    view = asyncio.run(pool_badge.switch(me, "claude/claude-opus-5-5", thread, second))

    assert view["id"] == second and view["hop"] == {"from": "claude", "reason": ""}
    # The next request goes there, and the chat shows it before that reply.
    assert pool_session.peek("claude", sid) == second
    row = db.fetchone("SELECT compaction FROM chat_threads WHERE id=?", (chats.resolve_thread_pk(me, thread),))
    stored = context_compact.parse_state(row["compaction"])["pool"]
    assert (stored["cred"], stored["prev"]) == (second, first)
    assert asyncio.run(pool_badge.preview(me, "claude/claude-opus-5-5", thread))["id"] == second
    # The pick beats a conversation kept on the old account (Cursor's), once.
    from app.sources import pool_refresh
    assert pool_refresh.pins("claude", sid, None, me, pinned=first)[0] == second
    assert pool_refresh.pins("claude", sid, None, me, pinned=first)[0] == first
    # An account the caller's requests may not use is refused, and nothing moves.
    assert asyncio.run(pool_badge.switch(me, "claude/claude-opus-5-5", thread, theirs)) is None
    assert pool_session.peek("claude", sid) == second


def test_a_pick_during_a_turn_survives_the_request_in_flight(fresh_db, monkeypatch):
    from app import chat_checkpoint, chats

    me = user("me@core.test")
    first = pool_account("claude", me, _claude(70, 60))
    second = pool_account("claude", me, _claude(5, 1))
    src = claude.ClaudeSource({"id": pool_policy.source_id("claude"), "name": "Claude"}, {}, [])

    async def pick(model, *, user_id=None, access=None, ignore_prefs=False):
        return src, "claude-opus-5-5"

    import app.sources as sources
    monkeypatch.setattr(sources, "pick", pick)
    monkeypatch.setattr(pool_session, "persist", False)
    model = "claude/claude-opus-5-5"
    thread = chats.create_thread(me, model=model)["id"]
    sid = _runtime_session(me, thread)
    pool_session.bind("claude", sid, first)
    chat_checkpoint.save_pool(me, thread, lambda prior: pool_badge.state(prior, first, "claude", model))

    view = asyncio.run(pool_badge.switch(me, model, thread, second, running=True))

    # The turn still shows the account in use, and the pick as next.
    assert view["id"] == first and view["next"] == {"id": second, "name": "claude"}
    assert {a["id"]: a["status"] for a in view["accounts"]} == {first: "current", second: "next"}
    # The request in flight ends on its account: neither its pin nor its round undoes the pick.
    pool_session.bind("claude", sid, first)
    stored = chat_checkpoint.save_pool(me, thread, lambda prior: pool_badge.state(prior, first, "claude", model))
    assert pool_session.peek("claude", sid) == second and stored["next"] == second
    # The turn's next request takes the pick; once it serves, it is the account in use.
    assert pool_session.lookup("claude", sid) == second
    stored = chat_checkpoint.save_pool(me, thread, lambda prior: pool_badge.state(prior, second, "claude", model))
    assert (stored["cred"], stored["prev"], stored["next"]) == (second, first, None)
    # Picking the account in use again cancels a pending pick.
    asyncio.run(pool_badge.switch(me, model, thread, first, running=True))
    assert asyncio.run(pool_badge.switch(me, model, thread, second, running=True))["next"] is None
    # A chat not sent yet only previews the pick: nothing is pinned.
    fresh = asyncio.run(pool_badge.switch(me, model, None, first))
    assert fresh["id"] == first and fresh["preview"] is True


def test_a_pick_no_pin_backs_is_not_shown_as_waiting(fresh_db, monkeypatch):
    from app import chat_checkpoint, chats

    me = user("me@core.test")
    first = pool_account("claude", me, _claude(70, 60))
    second = pool_account("claude", me, _claude(5, 1))
    src = claude.ClaudeSource({"id": pool_policy.source_id("claude"), "name": "Claude"}, {}, [])

    async def pick(model, *, user_id=None, access=None, ignore_prefs=False):
        return src, "claude-opus-5-5"

    import app.sources as sources
    monkeypatch.setattr(sources, "pick", pick)
    monkeypatch.setattr(pool_session, "persist", False)
    model = "claude/claude-opus-5-5"
    # A pick saved before picks named their pin waited forever: it is dropped.
    assert pool_badge.parse({"cred": first, "source": "claude", "next": second})["next"] is None
    thread = chats.create_thread(me, model=model)["id"]
    sid = _runtime_session(me, thread)
    pool_session.bind("claude", sid, first)
    chat_checkpoint.save_pool(me, thread, lambda prior: pool_badge.state(prior, first, "claude", model))
    asyncio.run(pool_badge.switch(me, model, thread, second, running=True))
    # A restart forgets the pending pick, so the request in flight re-pins its
    # account: the round it serves clears the pick instead of keeping it.
    pool_session.reset_store()
    pool_session.bind("claude", sid, first)
    stored = chat_checkpoint.save_pool(me, thread, lambda prior: pool_badge.state(prior, first, "claude", model))
    assert (stored["cred"], stored["next"]) == (first, None)


def test_the_badge_previews_a_lazily_routed_pool_from_the_callers_accounts(fresh_db, monkeypatch):
    # Routing hands out sources._LazySource, and copying one recursed forever:
    # every /chat/pool answered 500 in production after 2026-11-24's update.
    from app import db
    import app.sources as sources

    me, stranger = user("me@core.test"), user("stranger@core.test")
    mine = pool_account("claude", me, _claude(70, 60))
    pool_account("claude", stranger, _claude(1, 1))
    pool_policy.set_user_pref(stranger, "claude", share="private")
    row = db.fetchone("SELECT * FROM sources WHERE id=?", (pool_policy.source_id("claude"),))

    async def pick(model, *, user_id=None, access=None, ignore_prefs=False):
        return sources._LazySource(row), "claude-opus-5-5"

    monkeypatch.setattr(sources, "pick", pick)
    view = asyncio.run(pool_badge.preview(me, "claude/claude-opus-5-5"))

    assert view["id"] == mine
