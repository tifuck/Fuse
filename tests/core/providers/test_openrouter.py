"""OpenRouter: per-account only. Listing and routing follow the caller's own
key and toggles (free SKUs, paid all/allowlist), calls bill the caller's key,
bare pool ids point at the explicit SKU, payload translation, and a rejected
key is disabled only on a confirmed second strike of the key that failed.
"""
import asyncio
import time

import httpx
import pytest

import app.sources as sources
from app import db
from app.sources import openrouter, pool_pick

from tests.core.helpers import quick_user

from ._support import completion, cred_row, key

CATALOG = {"data": [
    {"id": "meta/llama:free", "name": "Llama (free)", "pricing": {"prompt": "0", "completion": "0"},
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
    {"id": "anthropic/claude-x", "pricing": {"prompt": "0.000003", "completion": "0.000015"},
     "architecture": {"input_modalities": ["text", "image"]}},
    {"id": "z-ai/glm-5.3", "pricing": {"prompt": "0.000001", "completion": "0.000002"},
     "reasoning": {"supported_efforts": ["low", "high"], "mandatory": True}},
    {"id": "z-ai/glm-5.3", "pricing": {"prompt": "0.000001", "completion": "0.000002"},
     "reasoning": {"supported_efforts": ["low", "high"], "mandatory": True}},
    {"id": "a/image-gen", "architecture": {"output_modalities": ["image"]}},
    {"id": "a/variant:extended", "pricing": {}},
    {"id": "openrouter/auto", "pricing": {}},
]}
FREE, CLAUDE, GLM = "openrouter/meta/llama:free", "openrouter/anthropic/claude-x", "openrouter/z-ai/glm-5.3"


@pytest.fixture
def setup(fresh_db, upstream):
    upstream.on("GET", "openrouter.ai/api/v1/models", httpx.Response(200, json=CATALOG))
    sid = openrouter.ensure_openrouter_source()
    users = [quick_user(f"u{i}@core.test") for i in range(3)]
    return sid, users


def _link(sid, uid, secret):
    return key(sid, secret, owner=uid, extra={"role": "user", "api_key": secret})


def _listed(uid):
    return asyncio.run(sources.models_union(user_id=uid))


def _chat(sid, uid, body, session_id=None):
    src = openrouter.OpenRouterSource({"id": sid, "name": "OpenRouter"}, {}, [])
    return asyncio.run(src.chat({"messages": [{"role": "user", "content": "x"}], **body}, user_id=uid,
                                session_id=session_id))


def test_the_catalog_keeps_text_models_once_with_their_lane(setup):
    catalog = asyncio.run(openrouter.fetch_catalog())
    assert catalog["free_ids"] == ["meta/llama:free"]
    assert catalog["paid_ids"] == ["anthropic/claude-x", "z-ai/glm-5.3"]
    assert catalog["by_id"]["anthropic/claude-x"]["image"] is True


def test_listing_follows_each_users_own_key_and_toggles(setup):
    sid, (keyless, alice, bob) = setup
    _link(sid, alice, "sk-or-alice-key")
    _link(sid, bob, "sk-or-bob-key-1")

    assert _listed(keyless) == []
    assert _listed(alice) == [FREE, CLAUDE, GLM]  # paid default: all

    openrouter.set_free_models(alice, False)
    openrouter.set_paid_models_enabled(alice, ["z-ai/glm-5.3"])
    sources.invalidate_model_cache("openrouter")
    assert _listed(alice) == [GLM]
    # The cache is per account: bob's list is his own, not alice's.
    assert _listed(bob) == [FREE, CLAUDE, GLM]


def test_resolve_and_denials_obey_the_callers_own_enablement(setup):
    sid, (keyless, alice, _bob) = setup
    _link(sid, alice, "sk-or-alice-key")
    resolve = lambda model, uid: asyncio.run(openrouter.resolve(model, uid))

    assert resolve(CLAUDE, alice) == ("anthropic/claude-x", False)
    assert resolve("anthropic/claude-x", alice) == ("anthropic/claude-x", False)  # exact upstream id too
    assert resolve(FREE, alice) == ("meta/llama:free", True)
    assert resolve(CLAUDE, keyless) is None
    assert openrouter.denied_reason(CLAUDE, keyless) == "Connect an OpenRouter API key to use OpenRouter models."

    openrouter.set_free_models(alice, False)
    openrouter.set_paid_models_all(alice, False)
    assert resolve(FREE, alice) is None and resolve(CLAUDE, alice) is None
    assert openrouter.denied_reason(FREE, alice) == "Free OpenRouter models are turned off for this account."
    assert openrouter.denied_reason(CLAUDE, alice).startswith("That paid OpenRouter model is not enabled")
    assert openrouter.set_paid_model(alice, "anthropic/claude-x", True)
    assert resolve(CLAUDE, alice) == ("anthropic/claude-x", False)


def test_a_bare_pool_id_never_runs_on_a_personal_key_and_points_at_the_sku(setup):
    sid, (_k, alice, _b) = setup
    _link(sid, alice, "sk-or-alice-key")
    assert asyncio.run(sources.pick("glm-5.3", user_id=alice)) is None
    assert openrouter.denied_reason("glm-5.3", alice) == (
        "glm-5.3 runs on its subscription pool, which is not connected for your account. "
        "With an OpenRouter key, pick openrouter/z-ai/glm-5.3 instead.")


def test_a_call_bills_the_callers_own_key_with_the_translated_payload(setup, upstream):
    sid, (_k, alice, bob) = setup
    _link(sid, alice, "sk-or-alice-key")
    _link(sid, bob, "sk-or-bob-key-1")
    openrouter.set_privacy(alice, data_collection_deny=True, zdr=True)
    openrouter.set_model_providers(alice, "anthropic/claude-x", ["anthropic", "bedrock"])
    upstream.on("POST", "/chat/completions", httpx.Response(200, json=completion()))

    result = _chat(sid, alice, {"model": CLAUDE, "reasoning_effort": "high", "prompt_cache_key": "raw-thread",
                                "models": ["x"]}, session_id="fx:scoped")
    _chat(sid, alice, {"model": FREE, "provider": {"only": ["mine"]}})

    assert result.status == 200
    (h1, h2) = upstream.headers("/chat/completions")
    assert h1["authorization"] == h2["authorization"] == "Bearer sk-or-alice-key"
    claude, free = upstream.sent("/chat/completions")
    assert claude["model"] == "anthropic/claude-x" and "models" not in claude
    assert claude["reasoning"] == {"effort": "high"} and "reasoning_effort" not in claude
    # The scoped id replaces the raw client key everywhere it could route on.
    assert claude["session_id"] == claude["prompt_cache_key"] == "fx:scoped"
    assert claude["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert claude["provider"] == {"only": ["anthropic", "bedrock"], "data_collection": "deny", "zdr": True,
                                  "allow_fallbacks": True}
    # The caller's own provider object wins; a free SKU is always capped at $0.
    assert free["provider"]["only"] == ["mine"] and free["provider"]["max_price"] == {"prompt": 0, "completion": 0}
    assert "cache_control" not in free


def test_a_mandatory_reasoning_model_drops_a_stale_none_effort(setup, upstream):
    sid, (_k, alice, _b) = setup
    _link(sid, alice, "sk-or-alice-key")
    upstream.on("POST", "/chat/completions", httpx.Response(200, json=completion()))
    _chat(sid, alice, {"model": GLM, "reasoning_effort": "none"})
    assert "effort" not in upstream.sent("/chat/completions")[0]["reasoning"]


def test_a_rejected_key_is_disabled_only_on_a_confirmed_second_strike(setup, upstream):
    sid, (_k, alice, _b) = setup
    cid = _link(sid, alice, "sk-or-alice-key")
    upstream.on("POST", "/chat/completions",
                httpx.Response(403, json={"error": {"message": "Flagged", "metadata": {
                    "flagged_input": "my prompt says invalid api key"}}}),
                httpx.Response(401, json={"error": {"message": "User not found"}}))

    moderated = _chat(sid, alice, {"model": CLAUDE})
    assert moderated.status == 403 and not cred_row(cid)["disabled"]  # the prompt is not an auth failure

    first = _chat(sid, alice, {"model": CLAUDE})
    assert first.status == 502 and first.body["error"]["message"] == "OpenRouter rejected the configured API key"
    assert not cred_row(cid)["disabled"]
    _chat(sid, alice, {"model": CLAUDE})
    assert cred_row(cid)["disabled"]


def test_a_rejection_of_a_replaced_key_does_not_disable_its_successor(setup):
    sid, (_k, alice, _b) = setup
    cid = _link(sid, alice, "sk-or-alice-key")
    stale = cred_row(cid)["secret"]
    db.execute("UPDATE credentials SET secret=? WHERE id=?", ("other-ciphertext", cid))
    openrouter.disable_credential(cid, secret=stale)
    assert not cred_row(cid)["disabled"]


def test_leftover_site_wide_keys_are_disabled_and_never_picked(setup):
    sid, (_k, alice, _b) = setup
    glob = key(sid, "sk-or-global-key")
    assert openrouter.pick_key_for(alice, sid) is None
    openrouter.clear_global_key(sid)
    assert cred_row(glob)["disabled"]


def test_an_admin_save_that_fails_part_way_disables_no_site_key(setup, monkeypatch):
    from app.web.source_routes import admin_actions
    from tests.core.auth._support import sign_in, web_client
    from tests.core.helpers import quick_user

    sid, _users = setup
    glob = key(sid, "sk-or-global-key")
    real = openrouter.clear_global_key

    def clear_then_fail(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("save failed part way")

    monkeypatch.setattr(admin_actions.openrouter_mod, "clear_global_key", clear_then_fail)
    with web_client() as web:
        sign_in(web, quick_user("root@core.test", admin=True))
        try:
            web.post("/admin/sources/openrouter", data={"enabled": "1"}, follow_redirects=False)
        except RuntimeError:
            pass
    assert not cred_row(glob)["disabled"]
