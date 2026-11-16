"""What clients build on top of the catalog: starred models per user, the
OpenCode provider config (merged without losing the user's other settings,
installer safe from a forged Host), and the admin source probe for a plain
OpenAI-compatible server.
"""
import asyncio
import json

import httpx
import pytest

from app import db, model_favorites, opencode
from app.chat_schema import ChatValidationError
from app.sources import probe
from tests.core.helpers import quick_user

from ._support import completion, key, source


def test_favorites_are_per_user_ordered_bounded_and_published(fresh_db, monkeypatch):
    a, b = quick_user("a@core.test"), quick_user("b@core.test")
    db.execute("UPDATE users SET pool_prefs=? WHERE id=?", (json.dumps({"nanogpt": {"share": "private"}}), a))

    model_favorites.set_favorite(a, "m1", True)
    model_favorites.set_favorite(a, "m2", True)
    model_favorites.set_favorite(a, "m1", True)  # re-starring moves it last, once
    assert model_favorites.set_favorite(a, "m2", False) == ["m1"]
    assert model_favorites.list_favorites(a) == ["m1"] and model_favorites.list_favorites(b) == []
    # Other per-user pool settings in the same column survive.
    assert json.loads(db.fetchone("SELECT pool_prefs FROM users WHERE id=?", (a,))["pool_prefs"])["nanogpt"]
    published = db.fetchall("SELECT type, payload FROM chat_events WHERE user_id=? ORDER BY id", (a,))
    assert [e["type"] for e in published] == ["favorites.updated"] * 4
    assert json.loads(published[-1]["payload"]) == {"models": ["m1"]}

    for bad in ("", "x" * 201, "a\nb", 7):
        with pytest.raises(ChatValidationError):
            model_favorites.set_favorite(a, bad, True)
    monkeypatch.setattr(model_favorites, "MAX", 1)
    with pytest.raises(ChatValidationError):
        model_favorites.set_favorite(a, "m3", True)


def test_the_opencode_merge_replaces_only_fuses_models():
    existing = opencode.parse_opencode_text("""{
      // my setup
      "theme": "dark", "url": "https://x.test/a//b",
      "provider": {"other": {"npm": "x"}, "fuse": {"options": {"apiKey": "sk-saved"}, "models": {"old": {}}}},
      "enabled_providers": ["other"], "disabled_providers": ["fuse"]
    }""")

    merged = opencode.merge_opencode_config(existing, models={"m1": {"name": "M1"}}, base_url="https://f.test/v1/")

    assert merged["theme"] == "dark" and merged["url"] == "https://x.test/a//b"
    assert merged["provider"]["other"] == {"npm": "x"}
    assert merged["provider"]["fuse"]["models"] == {"m1": {"name": "M1"}}
    # An empty key never replaces the saved one.
    assert merged["provider"]["fuse"]["options"] == {"apiKey": "sk-saved", "baseURL": "https://f.test/v1"}
    assert merged["enabled_providers"] == ["other", "fuse"] and merged["disabled_providers"] == []
    assert opencode.existing_fuse_api_key(merged) == "sk-saved"


@pytest.mark.parametrize("origin", ["https://x.test'; rm -rf ~; '", "https://x.test$(id)", "file:///etc", ""])
def test_the_installer_refuses_anything_but_a_plain_origin(origin):
    with pytest.raises(ValueError):
        opencode.install_script(origin=origin, api_base="https://x.test/v1")


def test_the_installer_single_quotes_the_origin():
    script = opencode.install_script(origin="https://fuse.test:8443/", api_base="https://fuse.test:8443/v1")
    assert "'https://fuse.test:8443'" in script and "'https://fuse.test:8443/v1'" in script


def test_the_probe_of_a_compatible_server_checks_reach_models_and_one_tiny_chat(fresh_db, upstream):
    sid = source("openai", {"base_url": "https://up.test"})
    key(sid, "sk-k")
    upstream.on("GET", "/v1/models", httpx.Response(200, json={"data": [{"id": "m-a"}]}))
    upstream.on("POST", "/chat/completions", httpx.Response(200, json=completion("pong")),
                httpx.Response(401, json={"error": {"message": "bad key"}}))

    report = asyncio.run(probe.probe_source(sid))
    assert report["ok"] and [c["name"] for c in report["checks"]] == ["Reach", "Keys", "Models", "Chat"]
    (sent,) = upstream.sent("/chat/completions")
    assert (sent["model"], sent["max_tokens"], sent["stream"]) == ("m-a", probe.TEST_MAX_TOKENS, False)

    failed = asyncio.run(probe.probe_source(sid))
    assert not failed["ok"] and failed["checks"][-1]["detail"] == "m-a: bad key"
    assert asyncio.run(probe.probe_source(sid + 999))["error"] == "Unknown source"
