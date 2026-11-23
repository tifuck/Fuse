"""Model capability gating: image input per resolved route (stripped only from
the outgoing copy, frozen per route in the snapshot, older half shed on a
refusal or past the image-data ceiling) and reasoning efforts per provider.
"""
import asyncio
import json

import httpx
import pytest

from app import api, chat_runs, model_caps
from app.sources import base, metadata_effort, ollama, openrouter, reasoning_efforts
from tests.core.helpers import quick_user

from ._support import completion, events, key, route, source


def _img(n):
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{'A' * 4 * n}"}}


def test_image_input_comes_from_the_catalog_first_then_the_rules():
    assert model_caps.image_input("zai", "glm-5.3") is False
    assert model_caps.image_input("zai", "glm-5.3-flash") is True
    assert model_caps.image_input("claude", "claude/claude-opus-5") is True
    assert model_caps.image_input("openai", "whatever") is None  # unknown: images are sent
    model_caps.remember("zai", "glm-5.3", True)
    assert model_caps.image_input("zai", "glm-5.3") is True
    openrouter._catalog = (0, {"by_id": {"a/text": {"image": False}, "a/vision": {"image": True}}})
    assert model_caps.image_input("openrouter", "openrouter/a/text") is False
    assert model_caps.image_input("openrouter", "openrouter/a/vision") is True


def test_a_text_only_route_gets_notes_and_the_saved_messages_keep_their_images():
    messages = [{"role": "user", "content": [{"type": "text", "text": "look"}, _img(1)]},
                {"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,AAAA",
                                              "detail": "high"}]}]
    original = json.loads(json.dumps(messages))

    stripped, omitted = model_caps.prepare(messages, "zai", "glm-5.3")
    kept, none = model_caps.prepare(messages, "zai", "glm-5.3-flash")

    assert omitted == 2 and none == 0
    assert stripped[0]["content"] == "look\n\n" + model_caps.OMITTED
    # Responses input_image becomes the image_url every adapter reads.
    assert kept[1]["content"] == [{"type": "image_url",
                                   "image_url": {"url": "data:image/png;base64,AAAA", "detail": "high"}}]
    assert messages == original


def test_shedding_drops_the_older_half_and_the_snapshot_freezes_it_per_route():
    messages = [{"role": "user", "content": [_img(i + 1)]} for i in range(4)]
    snapshot = {}
    model_caps.prepare(messages, "openai", "m", snapshot=snapshot)

    assert model_caps.shed(snapshot, "openai", "m") == 2
    out, omitted = model_caps.prepare(messages, "openai", "m", snapshot=snapshot)

    assert omitted == 2
    assert [m["content"] for m in out[:2]] == [model_caps.LIMIT_OMITTED] * 2
    assert out[2]["content"] == [_img(3)] and out[3]["content"] == [_img(4)]
    # A new image appended later is admitted without evicting the kept ones.
    more, omitted = model_caps.prepare(messages + [{"role": "user", "content": [_img(9)]}], "openai", "m",
                                       snapshot=snapshot)
    assert omitted == 2 and more[4]["content"] == [_img(9)]
    # Another route starts from its own decisions.
    assert model_caps.prepare(messages, "openai", "other", snapshot=snapshot)[1] == 0


def test_image_refusals_are_recognized_from_status_or_wording():
    assert model_caps.image_refusal(413, "")
    assert model_caps.image_refusal(400, "Too many images in request (max 20)")
    assert model_caps.image_refusal(400, "image exceeds the 5 MB maximum")
    assert not model_caps.image_refusal(400, "invalid tool schema")
    assert not model_caps.image_refusal(500, "too many images")


# -- through invoke_chat ---------------------------------------------------------

@pytest.fixture
def uid(fresh_db, upstream):
    uid = quick_user()
    sid = source("openai", {"base_url": "https://up.test"})
    key(sid, "sk-upstream-key")
    route("m1", sid, "up-1")
    return uid


def _invoke(uid, body, snapshot=None, cheap=False):
    return asyncio.run(api.invoke_chat(chat_runs.internal_request(uid), {"id": uid}, {"model": "m1", **body},
                                       image_snapshot=snapshot, cheap_reasoning=cheap))


def _contents(sent):
    return [m["content"] for m in sent["messages"]]


def test_a_text_only_route_reports_how_many_images_it_left_out(uid, upstream):
    model_caps.remember("openai", "up-1", False)
    upstream.on("POST", "/chat/completions", httpx.Response(200, json=completion()))

    *_, result = _invoke(uid, {"messages": [{"role": "user", "content": [_img(1), _img(2)]}]})

    assert result.response_headers["X-Fuse-Images-Omitted"] == "2"
    assert _contents(upstream.sent("/chat/completions")[0]) == [model_caps.OMITTED + "\n\n" + model_caps.OMITTED]


def test_image_data_past_the_ceiling_sheds_the_oldest_until_it_fits(uid, upstream, monkeypatch):
    monkeypatch.setattr(api, "_MAX_IMAGE_CHARS", 150)
    upstream.on("POST", "/chat/completions", httpx.Response(200, json=completion()))
    snapshot = {}
    messages = [{"role": "user", "content": [_img(30)]} for _ in range(4)]  # ~142 chars each

    *_, result = _invoke(uid, {"messages": messages}, snapshot)

    assert result.response_headers["X-Fuse-Images-Shed"] == "3"
    assert _contents(upstream.sent("/chat/completions")[0])[:3] == [model_caps.SIZE_OMITTED] * 3
    assert sorted(next(iter(snapshot.values())).values()) == ["admit", "size", "size", "size"]


def test_a_provider_refusing_the_images_is_retried_with_the_older_half_left_out(uid, upstream):
    upstream.on("POST", "/chat/completions",
                httpx.Response(400, json={"error": {"message": "Too many images in request"}}),
                httpx.Response(200, json=completion()))
    messages = [{"role": "user", "content": [_img(i + 1)]} for i in range(4)]

    *_, result = _invoke(uid, {"messages": messages}, {})

    assert result.status == 200 and result.response_headers["X-Fuse-Images-Shed"] == "2"
    first, second = upstream.sent("/chat/completions")
    assert len(_contents(first)) == 4 and _contents(second)[:2] == [model_caps.LIMIT_OMITTED] * 2
    # The refused attempt is accounted for without counting as a request.
    (row,) = events()
    assert (row["request_status"], row["count_request"]) == ("failed", 0)


def test_efforts_follow_each_providers_catalog_and_cheap_calls_use_the_lowest():
    assert metadata_effort("openai") is None  # unknown levels: send none
    ollama._think_values.update({"on-off": [True, False], "named": ["low", "high"], "plain": []})
    assert reasoning_efforts("ollama", "on-off") == ("none", "high")
    assert reasoning_efforts("ollama", "named") == ("low", "high") and metadata_effort("ollama", "named") == "low"
    assert reasoning_efforts("ollama", "plain") == ()
    openrouter._catalog = (0, {"by_id": {
        "a/m": {"reasoning": {"supported_efforts": ["low", "high"], "mandatory": True}},
        "a/onoff": {"reasoning": {}},
        "a/none": {"reasoning": None, "reasoning_param": False},
    }})
    assert reasoning_efforts("openrouter", "openrouter/a/m") == ("low", "high")
    assert reasoning_efforts("openrouter", "openrouter/a/onoff") == ("none", "medium")
    assert reasoning_efforts("openrouter", "openrouter/a/none") == ()


def test_map_effort_picks_the_nearest_level_and_never_turns_thinking_off():
    assert base.map_effort("medium", ("low", "high", "max")) == "high"  # higher on a tie
    assert base.map_effort("xhigh", ("low", "high", "max")) == "max"
    assert base.map_effort("none", ("low", "high")) is None
    assert base.map_effort("low", ("none",)) is None
    assert base.map_effort("bogus", ("low",)) is None


def test_cheap_reasoning_drops_a_guessed_effort_on_a_generic_route(uid, upstream):
    upstream.on("POST", "/chat/completions", httpx.Response(200, json=completion()))
    _invoke(uid, {"messages": [{"role": "user", "content": "t"}], "reasoning_effort": "high"}, cheap=True)
    assert "reasoning_effort" not in upstream.sent("/chat/completions")[0]


def test_cheap_reasoning_turns_thinking_off_on_llama_cpp(fresh_db, upstream):
    uid = quick_user()
    sid = source("llama", {"base_url": "https://up.test/v1"})
    key(sid, "sk-llama-key")
    route("m1", sid, "up-1")
    upstream.on("POST", "/chat/completions", httpx.Response(200, json=completion()))
    _invoke(uid, {"messages": [{"role": "user", "content": "t"}]}, cheap=True)
    _invoke(uid, {"messages": [{"role": "user", "content": "t"}]})
    cheap, chat = upstream.sent("/chat/completions")
    assert cheap["chat_template_kwargs"] == {"enable_thinking": False}
    assert "chat_template_kwargs" not in chat
