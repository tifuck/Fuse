"""What clients build on top of the catalog: starred models per user, the
OpenCode provider config (merged without losing the user's other settings,
installer safe from a forged Host), the admin source probe for a plain
OpenAI-compatible server, and model display names.
"""
import asyncio
import json

import httpx
import pytest

from app import db, model_favorites, opencode
from app.chat_schema import ChatValidationError
from app.sources import probe
from app.web.models import _model_display_name
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


def _installer(monkeypatch):
    """The installer's Python, loaded in-process (no shell, no python3 child)
    with the catalog fetch faked."""
    script = opencode.install_script(origin="https://fuse.test", api_base="https://fuse.test/v1")
    ns = {"__name__": "fuse_installer"}
    exec(compile(script.split("<<'PY'\n", 1)[1].rsplit("\nPY\n", 1)[0], "installer", "exec"), ns)
    sent = []
    ns["fetch_catalog"] = lambda api, key: sent.append((api, key)) or {"models": {"m1": {"name": "M1"}}}
    ns["ask"] = lambda *a, **kw: ""
    monkeypatch.setenv("FUSE_API_KEY", "sk-env")
    return ns, sent


@pytest.mark.parametrize("base_url, used", [("https://fuse.test/v1", "sk-saved"), ("https://other.test/v1", "sk-env")])
def test_the_installer_sends_a_saved_key_only_to_the_server_it_was_saved_for(monkeypatch, tmp_path, base_url, used):
    ns, sent = _installer(monkeypatch)
    path = tmp_path / "opencode.json"
    path.write_text(json.dumps({"provider": {"fuse": {"options": {
        "baseURL": "https://fuse.test/v1/", "apiKey": "sk-saved"}}}}))
    path.chmod(0o644)
    assert ns["main"](["--base-url", base_url, "--config", str(path), "-y"]) == 0
    assert sent == [(base_url, used)]
    assert json.loads(path.read_text())["provider"]["fuse"]["options"]["apiKey"] == used
    backups = list(tmp_path.glob("opencode.json.bak-*"))
    assert backups and all(p.stat().st_mode & 0o777 == 0o600 for p in [path, *backups])


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


@pytest.mark.parametrize("model,type_id,name", [
    # API-key providers: the group header names the provider, so it is not repeated.
    ("deepseek/deepseek-chat", "deepseek", "DeepSeek Chat"),
    ("deepseek/deepseek-v4-pro", "deepseek", "DeepSeek V4 Pro"),
    ("openai/gpt-5.5", "openai_api", "GPT 5.5"),
    ("gemini/gemini-2.5-pro", "gemini", "Gemini 2.5 Pro"),
    ("together/meta-llama/Llama-3.3-70B", "together", "Llama 3.3 70B"),
    ("opencode-zen/glm-5.3", "opencode_zen", "GLM 5.3"),
    # Unchanged for providers that predate them.
    ("gpt-5.5", "chatgpt", "GPT 5.5"),
    ("glm-5.3", "zai", "GLM 5.3"),
    ("qwen3-32b", "llama", "Qwen3 32B"),
    ("deepseek-v4.1:671b", "ollama", "DeepSeek V4.1 671B"),
])
def test_display_names_never_repeat_the_provider(model, type_id, name):
    assert _model_display_name(model, type_id) == name
