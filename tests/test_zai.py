"""Z.AI Coding Plan: GLM-5.3 effort levels, seeded catalog, image input."""

from __future__ import annotations

from unittest.mock import patch

from app import model_caps
from app.sources import reasoning_efforts
from app.sources import zai as zai_mod


def test_glm_53_forwards_mapped_reasoning_effort():
    for effort, level in (("minimal", "low"), ("low", "low"), ("medium", "high"), ("high", "high"),
                          ("xhigh", "max"), ("max", "max")):
        payload = zai_mod._chat_payload({"model": "glm-5.3", "messages": [], "reasoning_effort": effort})
        assert payload["reasoning_effort"] == level, effort
        assert "thinking" not in payload
    nested = zai_mod._chat_payload({"model": "glm-5.3-flash", "messages": [], "reasoning": {"effort": "high"}})
    assert nested["reasoning_effort"] == "high" and "reasoning" not in nested
    # No effort: Z.AI's default (max) applies.
    assert "reasoning_effort" not in zai_mod._chat_payload({"model": "glm-5.3", "messages": []})


def test_glm_53_cannot_disable_thinking():
    off = zai_mod._chat_payload({"model": "glm-5.3", "messages": [], "reasoning_effort": "none"})
    assert off["reasoning_effort"] == "low"
    assert "thinking" not in off
    native = zai_mod._chat_payload(
        {"model": "glm-5.3-flash", "messages": [], "thinking": {"type": "disabled", "clear_thinking": False}}
    )
    assert native["thinking"] == {"type": "enabled", "clear_thinking": False}
    assert native["reasoning_effort"] == "low"
    # An explicit effort wins over the toggle.
    both = zai_mod._chat_payload(
        {"model": "glm-5.3", "messages": [], "reasoning_effort": "max", "thinking": {"type": "disabled"}}
    )
    assert both["reasoning_effort"] == "max" and both["thinking"] == {"type": "enabled"}


def test_older_glm_keeps_the_thinking_switch():
    payload = zai_mod._chat_payload({"model": "glm-5-turbo", "messages": [], "reasoning_effort": "none"})
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in payload


def test_effort_picker_per_model():
    assert reasoning_efforts("zai", "glm-5.3") == ("low", "high", "max")
    assert reasoning_efforts("zai", "glm-5.3-flash") == ("low", "high", "max")
    assert reasoning_efforts("zai", "glm-5-turbo") == ("none", "high")


def test_glm_53_flash_takes_images():
    assert model_caps.image_input("zai", "glm-5.3-flash") is True
    assert model_caps.image_input("zai", "glm-5.3") is False


def test_live_listing_drops_retired_ids():
    with patch.object(zai_mod, "_models_cache", (0.0, ["glm-5.3", "glm-5.2", "glm-4.7"])):
        names = zai_mod._remember_models(["glm-5.3", "glm-5.3-flash"])
        assert "glm-5.2" not in names and "glm-4.7" not in names
        assert names[:2] == ["glm-5.3", "glm-5.3-flash"]
        assert set(zai_mod.ZAI_MODELS) <= set(names)
