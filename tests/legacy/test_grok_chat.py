import asyncio
import base64
import io
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image

from app import db
from app.sources import grok as grok_mod
from app.sources import pool_session
from app.sources.pool_pick import reset_health
from app.sources.pool_session import reset_store


def _data_url(fmt: str, mime: str) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 10, 10)).save(buf, format=fmt)
    return f"data:{mime};base64," + base64.b64encode(buf.getvalue()).decode()


def test_payload_drops_fields_reasoning_models_reject_and_maps_effort():
    body = {"model": "grok-4.7", "messages": [], "stop": ["x"], "presence_penalty": 0.1,
            "frequency_penalty": 0.2, "prompt_cache_key": "k", "max_tokens": 50, "reasoning_effort": "max"}
    payload = grok_mod.chat_payload(body)
    for key in ("stop", "presence_penalty", "frequency_penalty", "prompt_cache_key", "max_tokens"):
        assert key not in payload
    assert payload["max_completion_tokens"] == 50
    assert payload["reasoning_effort"] == "xhigh"
    assert "stop" in body  # the caller's body is not mutated
    for given, sent in (("none", "low"), ("minimal", "low"), ("medium", "medium"), ("xhigh", "xhigh")):
        assert grok_mod.chat_payload({"reasoning_effort": given})["reasoning_effort"] == sent
    assert grok_mod.chat_payload({"max_tokens": 5, "max_completion_tokens": 9})["max_completion_tokens"] == 9


def test_only_jpeg_and_png_images_reach_grok():
    png, webp, gif = (_data_url("PNG", "image/png"), _data_url("WEBP", "image/webp"),
                      _data_url("GIF", "image/gif"))
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "see"},
        {"type": "image_url", "image_url": {"url": png}},
        {"type": "image_url", "image_url": {"url": webp, "detail": "high"}},
        {"type": "image_url", "image_url": {"url": gif}},
    ]}, {"role": "assistant", "content": "ok"}]
    out = asyncio.run(grok_mod.jpeg_png_images(messages))
    parts = out[0]["content"]
    assert parts[1]["image_url"]["url"] == png
    for part in parts[2:]:
        assert part["image_url"]["url"].startswith(("data:image/png;base64,", "data:image/jpeg;base64,"))
    assert parts[2]["image_url"]["detail"] == "high"
    assert out[1] == messages[1]


class GrokOutdatedClientTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_health()
        pool_session.persist = False
        reset_store()
        admin = patch("app.sources.pool_policy.admin_policy",
                      return_value={"pool_shared": True, "open_access": False})
        admin.start()
        self.addCleanup(admin.stop)

    def tearDown(self):
        reset_store()
        pool_session.persist = True

    async def _chat(self, responses, refreshed):
        cred = {"id": 7, "disabled": 0, "last_usage_percent": 1.0, "donated_by": None,
                "week_tokens": 0, "week_start": db.iso_week_start(), "_extra": {"access_token": "tok"}}
        ok = (200, {"id": "c"}, None, {"prompt_tokens": 1, "completion_tokens": 1}, {})
        with (
            patch("app.sources.grok.note_pool_invoke"),
            patch("app.sources.grok.accounts.cred_extra", side_effect=lambda c: c["_extra"]),
            patch("app.sources.grok.db.fetchall", return_value=[cred]),
            patch("app.sources.grok.refresh_pool_billing", new_callable=AsyncMock),
            patch("app.sources.grok.force_refresh", new_callable=AsyncMock, return_value=refreshed) as force,
            patch("app.sources.grok.openai_chat", new_callable=AsyncMock,
                  side_effect=[r if r else ok for r in responses]) as chat,
        ):
            src = grok_mod.GrokSource({"id": 1, "name": "Grok"}, {}, [cred])
            result = await src.chat({"model": "grok-4.7", "messages": [{"role": "user", "content": "hi"}]})
        return result, chat, force

    async def test_426_refreshes_the_client_version_and_retries_once(self):
        refused = (426, {"error": "Upgrade Required"}, None, {}, {})
        result, chat, force = await self._chat([refused, None], refreshed=True)
        self.assertEqual(result.status, 200)
        self.assertEqual(chat.call_count, 2)
        force.assert_awaited_once()

    async def test_426_is_returned_when_the_version_did_not_change(self):
        refused = (426, {"error": "Upgrade Required"}, None, {}, {})
        result, chat, _force = await self._chat([refused], refreshed=False)
        self.assertEqual(result.status, 426)
        self.assertEqual(chat.call_count, 1)
