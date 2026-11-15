"""The /chat themes. Each theme folder carries its own metadata in
``static/themes/<id>/theme.json``; this is the one list the templates (the
``fuse_chat_view`` cookie check, the Layout control), chat-appearance.js and
static/themes/registry.js all read, so a new theme is its two folders plus a
registry.js entry."""
from __future__ import annotations

import functools
import json

from .config import ROOT

_THEMES = ROOT / "static" / "themes"
_FIELDS = ("id", "label", "description", "font", "wallpaper", "scheme")


@functools.cache
def chat_themes() -> tuple[dict, ...]:
    """Every theme's metadata, in Layout order; the first is the default."""
    themes = []
    for path in sorted(_THEMES.glob("*/theme.json")):
        meta = json.loads(path.read_text())
        if meta.get("id") != path.parent.name or any(not isinstance(meta.get(k), str) for k in _FIELDS):
            raise ValueError(f"{path}: needs string {', '.join(_FIELDS)} and id == folder name")
        if meta["scheme"] not in ("dark", "light"):
            raise ValueError(f"{path}: scheme must be dark or light")
        themes.append({k: meta[k] for k in _FIELDS} | {"order": int(meta.get("order", 99))})
    return tuple(sorted(themes, key=lambda t: (t["order"], t["id"])))


def chat_view(request) -> str:
    """The theme the fuse_chat_view cookie picks, or the default."""
    themes = chat_themes()
    wanted = request.cookies.get("fuse_chat_view")
    return wanted if any(t["id"] == wanted for t in themes) else themes[0]["id"]
