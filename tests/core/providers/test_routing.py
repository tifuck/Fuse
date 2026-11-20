"""Model listing and routing (sources.pick / models_union): each pool's access
gate hides its models and refuses to route them, admin routes obey the same
gates, namespaced ids never fall into another backend, the lone-llama
fallback, catalog outages per namespace, and cache invalidation.
"""
import asyncio
import time

import httpx
import pytest
from fastapi import HTTPException

import app.sources as sources
from app import accounts, api, chat_runs, db
from app.providers.access import Access
from app.sources import nanogpt, openrouter, refuseless
from tests.core.helpers import quick_user

from ._support import key, route, source

NANO = {"data": [{"id": "z-ai/glm-5.3", "subscription": {"included": True}}]}
REFUSE = {"data": [{"id": "glm-5.3-abliterated"}]}


def _access(uid, allow):
    return Access.fixed(uid, {name.removeprefix("allow_"): on for name, on in allow.items()})


def _union(uid=None, **allow):
    return asyncio.run(sources.models_union(user_id=uid, access=_access(uid, allow)))


def _pick(model, uid=None, **allow):
    picked = asyncio.run(sources.pick(model, user_id=uid, access=_access(uid, allow)))
    return picked and (picked[0].type_id, picked[1])


@pytest.fixture
def pools(fresh_db):
    nanogpt._catalog = (time.time(), nanogpt.parse_catalog(NANO))
    refuseless._catalog = (time.time(), refuseless.parse_catalog(REFUSE))
    return nanogpt.ensure_nanogpt_source(), refuseless.ensure_refuseless_source()


def test_a_pool_gate_hides_its_models_and_refuses_to_route_them(pools):
    uid = quick_user()

    assert _union(uid) == []
    assert _pick("refuseless/glm-5.3-abliterated", uid) is None
    assert _pick("nanogpt/z-ai/glm-5.3", uid) is None

    assert _union(uid, allow_nanogpt=True, allow_refuseless=True) == ["nanogpt/z-ai/glm-5.3",
                                                                      "refuseless/glm-5.3-abliterated"]
    assert _pick("refuseless/glm-5.3-abliterated", uid, allow_refuseless=True) == ("refuseless",
                                                                                  "glm-5.3-abliterated")
    assert _pick("nanogpt/z-ai/glm-5.3", uid, allow_nanogpt=True) == ("nanogpt", "z-ai/glm-5.3")
    # Ids outside the catalog never route, even with access.
    assert _pick("refuseless/gone", uid, allow_refuseless=True) is None


def test_an_admin_route_onto_a_gated_pool_obeys_the_gate(pools):
    _nano, refuse = pools
    route("alias", refuse, "glm-5.3-abliterated")
    assert _pick("alias", allow_refuseless=False) is None
    assert _pick("alias", allow_refuseless=True) == ("refuseless", "glm-5.3-abliterated")


def test_a_route_onto_openrouter_obeys_the_callers_enablement(fresh_db, upstream):
    or_sid = openrouter.ensure_openrouter_source()
    openrouter._catalog = (time.time(), {"by_id": {"a/paid": {"free": False}}, "free_ids": [],
                                         "paid_ids": ["a/paid"], "all_ids": ["a/paid"]})
    route("house", or_sid, "a/paid")
    keyless, owner = quick_user("a@core.test"), quick_user("b@core.test")
    key(or_sid, "sk-or-owner-key", owner=owner, extra={"role": "user"})

    assert _pick("house", keyless) is None
    assert _pick("house", owner) == ("openrouter", "a/paid")
    openrouter.set_paid_models_enabled(owner, [])
    assert _pick("house", owner) is None


def test_namespaced_ids_never_fall_into_a_generic_route_or_openrouter(fresh_db, upstream):
    sid = source("openai", {"base_url": "https://up.test"})
    key(sid, "sk-k")
    from app.sources import explabs
    explabs.ensure_explabs_source()
    nanogpt.ensure_nanogpt_source()
    route("explabs/model", sid)
    or_sid = openrouter.ensure_openrouter_source()
    uid = quick_user()
    key(or_sid, "sk-or-user-key", owner=uid, extra={"role": "user"})
    openrouter._catalog = (time.time(), {"by_id": {"nanogpt/x": {"free": False}}, "free_ids": [],
                                         "paid_ids": ["nanogpt/x"], "all_ids": ["nanogpt/x"]})

    # explabs/ belongs to the caller's own Experiential Labs key, never a route.
    assert _pick("explabs/model", uid) is None
    # A pool prefix wins over the OpenRouter author/slug shape.
    assert _pick("nanogpt/x", uid) is None


def test_a_lone_llama_server_serves_any_id_and_a_named_one_only_its_name(fresh_db, upstream):
    upstream.on("GET", "/models", httpx.Response(200, json={"data": []}))
    sid = source("llama", {"base_url": "https://llama.test", "model": "local"})
    assert _pick("anything") == ("llama", "anything")

    source("openai", {"base_url": "https://up.test"})
    assert _pick("anything") is None
    db.execute("UPDATE sources SET config=? WHERE id=?", ('{"base_url": "https://llama.test", "model": "qwen"}', sid))
    assert _pick("qwen") == ("llama", "qwen")


def test_an_outage_marks_only_its_own_namespace_unavailable():
    sources._unavailable_types.add("nanogpt")
    assert sources.catalog_unavailable_for("nanogpt/x")
    assert not sources.catalog_unavailable_for("glm-5.3")
    assert not sources.catalog_unavailable_for("m1")
    sources._unavailable_types.add("openrouter")
    assert sources.catalog_unavailable_for("a/b") and not sources.catalog_unavailable_for("explabs/a")
    sources._unavailable_types.clear()
    sources._unavailable_types.add("llama")
    assert sources.catalog_unavailable_for("m1") and sources.catalog_unavailable_for("")


def test_a_failed_catalog_marks_the_source_unavailable_until_it_answers(fresh_db, upstream):
    upstream.on("GET", "/models", httpx.Response(500), httpx.Response(200, json={"data": [{"id": "m-a"}]}))
    sid = source("openai", {"base_url": "https://up.test"})
    key(sid, "sk-k")

    assert _union() == [] and sources.catalog_unavailable_for("m-a")
    sources.invalidate_model_cache()
    assert _union() == ["m-a"] and not sources.catalog_unavailable_for("m-a")


def test_invalidating_a_type_drops_its_cached_upstream_list(fresh_db, upstream):
    upstream.on("GET", "/props", httpx.Response(404))
    upstream.on("GET", "/models", httpx.Response(200, json={"data": [{"id": "old-model"}]}))
    source("llama", {"base_url": "https://llama.test"})
    assert _union() == ["old-model"]

    upstream.routes.pop()
    upstream.on("GET", "/models", httpx.Response(200, json={"data": [{"id": "new-model"}]}))
    assert _union() == ["old-model"]  # cached
    sources.invalidate_model_cache("llama")
    assert _union() == ["new-model"]


def _refused(uid, model):
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api.invoke_chat(chat_runs.internal_request(uid), {"id": uid},
                                    {"model": model, "messages": [{"role": "user", "content": "x"}]}))
    return caught.value.status_code, caught.value.detail


def test_a_disconnected_pool_says_what_to_reconnect_instead_of_unknown_model(pools, upstream):
    nano, refuse = pools
    uid = quick_user()
    cid = key(refuse, "rl-key-abcdefghijkl", owner=uid)
    assert _pick("refuseless/glm-5.3-abliterated", uid, allow_refuseless=refuseless.user_has_refuseless(uid))
    # The provider rejected the key (the path every pool's repeated 401 takes).
    accounts.disable_credential(cid, extra=db.fetchone("SELECT extra FROM credentials WHERE id=?", (cid,))["extra"])

    status, detail = _refused(uid, "refuseless/glm-5.3-abliterated")
    assert status == 403 and detail.startswith("Refuseless: account sign-in rejected.")
    assert _refused(uid, "nanogpt/z-ai/glm-5.3") == (403, "Connect a NanoGPT account to use NanoGPT models.")
    assert _refused(uid, "no-such-model")[0] == 404


@pytest.mark.parametrize("kind", ["grok", "chatgpt", "zai", "devin", "kimi", "mimo", "claude", "cursor", "nanogpt"])
def test_an_admin_route_onto_any_pool_obeys_that_pools_gate(fresh_db, kind):
    route("alias", source(kind), "up-model")
    others = {f"allow_{k}": True for k in ("grok", "chatgpt", "zai", "devin", "kimi", "mimo", "claude", "cursor",
                                         "nanogpt", "refuseless") if k != kind}
    assert _pick("alias", **others) is None
    assert _pick("alias", **{f"allow_{kind}": True}) == (kind, "up-model")
