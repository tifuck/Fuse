"""Ollama Cloud personal keys: a user's own key serves only them and comes
before the admin's keys, never touches the shared key pool or access map, and
gives them their own catalog; site_access decides who the admin's keys serve."""
import asyncio
import json

import httpx
import pytest

import app.sources as sources
from app import api, chat_runs, db
from app.sources import keyed, ollama, ollama_access
from tests.core.helpers import quick_user

from ._support import drain, events, key, route, source

BASE = {"base_url": "https://ollama.test/api"}
OK = {"message": {"role": "assistant", "content": "ok"}, "done": True}


def _world(site_access=None):
    cfg = dict(BASE, **({"site_access": site_access} if site_access else {}))
    sid = source("ollama", cfg)
    alice, bob = quick_user("alice@core.test"), quick_user("bob@core.test")
    key(sid, "ollama-site-key")
    key(sid, "ollama-alice-key", owner=alice)
    return sid, alice, bob


def _chat(sid, uid, model="qwen3"):
    return asyncio.run(sources.get(sid).chat({"model": model, "messages": [{"role": "user", "content": "x"}]}, user_id=uid))


def _bearers(upstream):
    return [h.get("authorization") for h in upstream.headers("ollama.test/api/chat")]


def test_a_personal_key_comes_first_and_serves_only_its_owner(fresh_db, upstream):
    sid, alice, bob = _world()
    upstream.on("POST", "ollama.test/api/chat", httpx.Response(200, json=OK))

    assert _chat(sid, alice).status == 200
    assert _chat(sid, bob).status == 200
    assert _bearers(upstream) == ["Bearer ollama-alice-key", "Bearer ollama-site-key"]
    # The personal key never joins the shared pool, its plain-key cache or the access map.
    assert set(ollama._pools[sid].states) == {"ollama-site-key"}
    assert set(ollama._plain[sid].values()) == {"ollama-site-key"}
    sources.get(sid)  # a rebuild keeps it out too
    assert set(ollama._pools[sid].states) == {"ollama-site-key"}
    assert ollama_access.of(sid, "qwen3") == ollama_access.FREE  # only from bob's site-key answer


def test_verdicts_from_a_personal_key_stay_out_of_the_shared_access_map(fresh_db, upstream):
    sid, alice, _bob = _world()
    upstream.on("POST", "ollama.test/api/chat", httpx.Response(200, json=OK))
    assert _chat(sid, alice).status == 200
    assert ollama_access.of(sid, "qwen3") is None


def test_access_verdicts_follow_the_server_and_site_keys_they_were_taken_with(fresh_db):
    sid, _alice, _bob = _world()
    ollama_access.note(sid, "qwen3", ollama_access.FREE, status=200)
    sources.invalidate_model_cache("ollama")  # e.g. a personal key added
    assert ollama_access.of(sid, "qwen3") == ollama_access.FREE

    key(sid, "ollama-second-site-key")
    sources.invalidate_model_cache("ollama")
    assert ollama_access.of(sid, "qwen3") is None
    ollama_access.note(sid, "qwen3", ollama_access.FREE, status=200)
    db.execute("UPDATE sources SET config=? WHERE id=?", (json.dumps({"base_url": "https://other.test/api"}), sid))
    ollama_access._cache.clear()  # a restart
    assert ollama_access.of(sid, "qwen3") is None


def test_a_rejected_personal_key_falls_back_to_the_site_keys(fresh_db, upstream):
    sid, alice, _bob = _world()
    upstream.on("POST", "ollama.test/api/chat", httpx.Response(401, json={"error": "unauthorized"}),
                httpx.Response(200, json=OK))
    result = _chat(sid, alice)
    assert result.status == 200 and [a["status"] for a in result.attempts] == [401]
    assert _bearers(upstream) == ["Bearer ollama-alice-key", "Bearer ollama-site-key"]


@pytest.mark.parametrize("stream", [False, True])
def test_a_rate_limited_site_key_is_recorded_beside_the_answer_from_the_next(fresh_db, upstream, stream):
    sid = source("ollama", BASE)
    uid = quick_user()
    key(sid, "ollama-site-key-1")
    key(sid, "ollama-site-key-2")
    route("qwen3", sid, "qwen3")
    upstream.on("POST", "ollama.test/api/chat", httpx.Response(429, json={"error": "too many requests"}),
                httpx.Response(200, json=OK))
    body = {"model": "qwen3", "stream": stream, "messages": [{"role": "user", "content": "x"}]}

    async def go():
        response = await api.dispatch_chat(chat_runs.internal_request(uid), {"id": uid}, body)
        if stream:
            await drain(response.body_iterator)
        return response

    assert asyncio.run(go()).status_code == 200
    assert len(upstream.calls("ollama.test/api/chat")) == 2
    assert [(r["request_status"], r["count_request"]) for r in events()] == [("failed", 0), ("success", 1)]


def test_site_access_none_leaves_only_personal_keys(fresh_db, upstream):
    sid, alice, bob = _world("none")
    upstream.on("POST", "ollama.test/api/chat", httpx.Response(200, json=OK))
    upstream.on("GET", "ollama.test/api/tags", httpx.Response(200, json={"models": [{"name": "qwen3"}, {"name": "big:671b"}]}))
    upstream.on("POST", "ollama.test/api/show", httpx.Response(404))

    assert _chat(sid, bob).status == 403
    assert asyncio.run(sources.models_union(user_id=bob)) == []
    # Alice lists what her own key lists, with no shared free/paid filter.
    assert sorted(asyncio.run(sources.models_union(user_id=alice))) == ["big:671b", "qwen3"]
    assert asyncio.run(sources.pick("big:671b", user_id=alice))[0].type_id == "ollama"
    assert asyncio.run(sources.pick("big:671b", user_id=bob)) is None
    assert _chat(sid, alice).status == 200 and _bearers(upstream) == ["Bearer ollama-alice-key"]


def test_a_personal_key_is_checked_with_a_one_token_chat(fresh_db, upstream, monkeypatch):
    sid = source("ollama", BASE)
    uid = quick_user()
    upstream.on("GET", "ollama.com/api/tags", httpx.Response(200, json={"models": [{"name": "paid:1t"}, {"name": "free:8b"}]}))
    ollama_access.note(sid, "free:8b", ollama_access.FREE, status=200)
    upstream.on("POST", "ollama.com/api/chat",
                httpx.Response(401, json={"error": "unauthorized ollama-bad-key-1"}),
                httpx.Response(403, json={"error": "this model requires a subscription"}))

    cid, err = asyncio.run(keyed.store_key("ollama", "ollama-bad-key-1", user_id=uid))
    assert cid is None and err == "Ollama rejected this key." and "ollama-bad" not in err
    sent = upstream.sent("ollama.com/api/chat")[-1]
    assert sent["model"] == "free:8b" and sent["options"] == {"num_predict": 1}
    # A plan refusal still proves the key works.
    cid, err = asyncio.run(keyed.store_key("ollama", "ollama-good-key-1", user_id=uid))
    assert cid and not err
    row = db.fetchone("SELECT donated_by, source_id FROM credentials WHERE id=?", (cid,))
    assert (row["donated_by"], row["source_id"]) == (uid, sid)
    assert json.loads(db.fetchone("SELECT config FROM sources WHERE id=?", (sid,))["config"]) == BASE
