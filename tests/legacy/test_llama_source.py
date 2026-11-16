"""llama.cpp catalog metadata per model (no network)."""

import asyncio

import httpx

from app import context_meter, model_caps
from app.sources import llama


def test_router_rows_set_vision_and_window_per_model(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [
                {"id": "seeing", "architecture": {"input_modalities": ["text", "image"]},
                 "memory": {"effective": {"n_ctx": 65536}, "max": {"n_ctx": 131072}}},
                {"id": "blind", "architecture": {"input_modalities": ["text"]}, "memory": {"max": {"n_ctx": 32768}}},
            ]})
        return httpx.Response(200, json={"modalities": {"vision": True}, "default_generation_settings": {"n_ctx": 4096}})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(llama.httpx, "AsyncClient", lambda **kw: client_type(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(llama, "_models_cache", {})
    monkeypatch.setattr(model_caps, "_seen", {})
    monkeypatch.setattr(context_meter, "_WINDOW_CACHE", {})
    monkeypatch.setattr(context_meter, "_WINDOW_SOURCES", {})
    src = llama.LlamaSource({"id": 9301, "name": "router"}, {"base_url": "https://llama.test"}, [])

    assert asyncio.run(src.models()) == ["seeing", "blind"]
    # Every model described itself, so the single-model /props is not asked.
    assert seen == ["/v1/models"]
    assert model_caps.image_input("llama", "seeing") is True
    assert model_caps.image_input("llama", "blind") is False
    assert context_meter.cached_window("llama", "seeing") == 65536
    assert context_meter.cached_window("llama", "blind") == 32768
