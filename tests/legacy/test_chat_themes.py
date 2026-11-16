"""The /chat theme list: static/themes/*/theme.json is the one source the
templates, chat-appearance.js and registry.js read."""
from __future__ import annotations

import re
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader

from app import chat_themes
from app.config import ROOT


def _env():
    env = Environment(loader=FileSystemLoader(str(ROOT / "templates")))
    env.globals["chat_themes"] = chat_themes.chat_themes
    return env


def test_every_theme_folder_is_listed_with_its_parts():
    themes = chat_themes.chat_themes()
    ids = [t["id"] for t in themes]
    assert ids[0] == "console", "Console stays the default"
    folders = sorted(p.name for p in (ROOT / "static" / "themes").iterdir() if p.is_dir())
    assert sorted(ids) == folders
    registry = (ROOT / "static" / "themes" / "registry.js").read_text()
    for t in themes:
        assert (ROOT / "templates" / "themes" / t["id"] / "_chat.html").is_file(), t["id"]
        assert re.search(rf"\b{t['id']}: create\w+Theme\b", registry), f"registry.js has no entry for {t['id']}"
        assert t["scheme"] in ("dark", "light") and t["label"] and t["description"] and t["font"] and t["wallpaper"]


def test_layout_control_is_generated_from_the_list():
    html = _env().get_template("_appearance_body.html").render()
    themes = chat_themes.chat_themes()
    buttons = re.findall(r'<button type="button" data-chat-view-choice="([a-z]+)" aria-pressed="(true|false)">([^<]+)</button>', html)
    assert [b[0] for b in buttons] == [t["id"] for t in themes]
    assert [b[2] for b in buttons] == [t["label"] for t in themes]
    assert [b[1] for b in buttons] == ["true"] + ["false"] * (len(themes) - 1)
    for t in themes:
        assert t["description"] in html


def test_cookie_picks_only_a_listed_theme():
    req = lambda cookie: SimpleNamespace(cookies={"fuse_chat_view": cookie} if cookie is not None else {})
    for t in chat_themes.chat_themes():
        assert chat_themes.chat_view(req(t["id"])) == t["id"]
    for bad in (None, "", "prism", "../console", "CONSOLE"):
        assert chat_themes.chat_view(req(bad)) == "console"
