"""Keyed providers: one declarative preset per API-key provider.

Adding a typical API-key provider is one KeyedPreset below. Each becomes a
ProviderSpec (builtin.py) served by app.sources.keyed.KeyedSource: users add
their own key on Sources (personal, used only by them), an admin may add site
keys (used by nobody until the admin sets who may use them). Model ids are
"<prefix><upstream id>" and the prefix is a soft claim: an id the user has no
key for falls through to the rest of routing (an OpenRouter "openai/gpt-x").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

# Sent to no keyed provider: Fuse-internal or provider-specific fields that a
# strict OpenAI-compatible API rejects.
STRIP = ("reasoning", "thinking", "include_reasoning", "reasoning_effort", "prompt_cache_key",
         "service_tier", "provider", "transforms", "usage", "chat_template_kwargs")


@dataclass(frozen=True)
class Region:
    id: str
    label: str
    base_url: str


@dataclass(frozen=True)
class KeyMethod:
    id: str
    label: str
    help: str = ""


API_KEY = KeyMethod("api_key", "API key")


@dataclass(frozen=True)
class KeyedPreset:
    id: str  # sources.type and usage provider id
    label: str
    base_url: str = ""  # used when the preset has no regions
    regions: tuple[Region, ...] = ()  # the first is the default
    prefix: str = ""  # public model id prefix; default the id with hyphens, "opencode-zen/"
    protocol: str = "openai_chat"  # or "anthropic_messages"
    auth: str = "bearer"  # "bearer" or "x-api-key"
    headers: tuple[tuple[str, str], ...] = ()
    # Model catalog: a path under the base URL or an absolute URL. It also
    # validates a pasted key.
    catalog: str = "/models"
    id_strip: str = ""  # drop this prefix from catalog ids (Gemini "models/")
    hide: str = ""  # regex of catalog ids that are not chat models
    # (model id regex, levels) pairs, first match wins. No match: Fuse sends
    # no reasoning_effort and the picker offers none.
    efforts: tuple[tuple[str, tuple[str, ...]], ...] = ()
    methods: tuple[KeyMethod, ...] = (API_KEY,)
    group: str = "api_key"  # Sources group: api_key, subscription or aggregator
    vendor: str = ""  # shown on this provider's pane: xAI API on x.ai (grok)
    key_url: str = ""  # where a user creates a key
    cache_key: bool = False  # forwards prompt_cache_key (session-scoped)
    max_tokens: int = 8192  # anthropic_messages: default when the client sets none
    strip: tuple[str, ...] = field(default=STRIP)
    validate: Callable | None = None  # async (key) -> error; default: GET the catalog with the key
    # Each key lists models of its own account (fine-tunes, deployments):
    # the catalog is read per user, never shared.
    per_key: bool = False

    @property
    def public_prefix(self) -> str:
        return self.prefix or self.id.replace("_", "-") + "/"

    def upstream(self, model: str) -> str:
        mid = (model or "").strip()
        return mid[len(self.public_prefix):] if mid.startswith(self.public_prefix) else mid

    def base(self, region: str = "") -> str:
        for r in self.regions:
            if r.id == region:
                return r.base_url
        return self.regions[0].base_url if self.regions else self.base_url

    def efforts_for(self, model: str | None) -> tuple[str, ...]:
        mid = self.upstream(model or "")
        for pattern, levels in self.efforts:
            if re.search(pattern, mid):
                return levels
        return ()


_LOW_HIGH = ("low", "medium", "high")

PRESETS: tuple[KeyedPreset, ...] = (
    KeyedPreset("opencode_zen", "OpenCode Zen", base_url="https://opencode.ai/inference/openai/v1", group="aggregator",
                key_url="https://opencode.ai/auth"),
    KeyedPreset("opencode_go", "OpenCode Go", base_url="https://opencode.ai/inference/go/openai/v1",
                catalog="https://opencode.ai/inference/go/v1/models", group="subscription",
                key_url="https://opencode.ai/auth"),
    KeyedPreset("openai_api", "OpenAI API", base_url="https://api.openai.com/v1", prefix="openai/", vendor="chatgpt",
                hide=r"embedding|whisper|tts|dall-e|image|moderation|transcribe|realtime|audio|search|davinci|babbage|sora",
                efforts=((r"^(o\d|gpt-5)", _LOW_HIGH),), cache_key=True, per_key=True,
                key_url="https://platform.openai.com/api-keys"),
    KeyedPreset("anthropic", "Anthropic API", base_url="https://api.anthropic.com/v1", vendor="claude",
                protocol="anthropic_messages", auth="x-api-key", headers=(("anthropic-version", "2023-06-01"),),
                key_url="https://console.anthropic.com/settings/keys"),
    KeyedPreset("xai", "xAI API", base_url="https://api.x.ai/v1", hide=r"image|video|embed", vendor="grok",
                key_url="https://console.x.ai"),
    KeyedPreset("gemini", "Gemini API", base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                id_strip="models/", hide=r"embedding|imagen|veo|aqa|tts|image",
                efforts=((r"^gemini-(2\.5|[3-9])", _LOW_HIGH),), key_url="https://aistudio.google.com/apikey"),
    KeyedPreset("deepseek", "DeepSeek", base_url="https://api.deepseek.com",
                key_url="https://platform.deepseek.com/api_keys"),
    KeyedPreset("minimax", "MiniMax", regions=(
                Region("global", "Global (minimax.io)", "https://api.minimax.io/v1"),
                Region("cn", "Mainland China (minimaxi.com)", "https://api.minimaxi.com/v1"),
                ),
                methods=(KeyMethod("api_key", "Pay-as-you-go API key", "billed per token from your MiniMax balance"),
                         KeyMethod("m_plan", "M Plan key", "the subscription key under Plan Details")),
                key_url="https://platform.minimax.io"),
    KeyedPreset("mistral", "Mistral", base_url="https://api.mistral.ai/v1", hide=r"embed|moderation|ocr",
                key_url="https://console.mistral.ai/api-keys"),
    KeyedPreset("groq", "Groq", base_url="https://api.groq.com/openai/v1", hide=r"whisper|tts|guard|playai",
                efforts=((r"gpt-oss", _LOW_HIGH),), key_url="https://console.groq.com/keys"),
    KeyedPreset("cerebras", "Cerebras", base_url="https://api.cerebras.ai/v1",
                efforts=((r"gpt-oss", _LOW_HIGH),), key_url="https://cloud.cerebras.ai"),
    KeyedPreset("together", "Together AI", base_url="https://api.together.xyz/v1",
                hide=r"embed|rerank|flux|stable-diffusion|whisper|sonic|guard", per_key=True, key_url="https://api.together.ai/settings/api-keys"),
    KeyedPreset("fireworks", "Fireworks AI", base_url="https://api.fireworks.ai/inference/v1",
                hide=r"embed|flux|whisper|stable-diffusion", per_key=True, key_url="https://fireworks.ai/account/api-keys"),
    KeyedPreset("deepinfra", "DeepInfra", base_url="https://api.deepinfra.com/v1/openai",
                hide=r"embed|whisper|flux|stable-diffusion|sdxl|rerank", per_key=True, key_url="https://deepinfra.com/dash/api_keys"),
    KeyedPreset("moonshot", "Moonshot AI", base_url="https://api.moonshot.ai/v1", vendor="kimi",
                key_url="https://platform.moonshot.ai/console/api-keys"),
    KeyedPreset("huggingface", "Hugging Face", base_url="https://router.huggingface.co/v1", group="aggregator",
                key_url="https://huggingface.co/settings/tokens"),
    KeyedPreset("venice", "Venice AI", base_url="https://api.venice.ai/api/v1", hide=r"embed|tts|image|upscale",
                key_url="https://venice.ai/settings/api"),
)
