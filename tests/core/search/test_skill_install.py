"""Skill installs from GitHub and skills.sh: the zipball and its redirect, the
repo-hash/ prefix, the contents-API fallback, the catalog search, which
SKILL.md a skill id picks, and the archive limits.

DNS is the fake resolver from conftest (``dns``) and every request goes to the
scripted transport (``web``), pinned to the vetted IP as in production.
"""
import io
import struct
import zipfile

import httpx
import pytest

from app import security, skill_install
from app.config import settings
from app.search import client
from app.skill_install import catalog_search, extract_skill_files, fetch_github_skill, files_from_zip
from app.skill_parse import SkillError, parse_skill_md

from ._support import run

REPO = "https://api.github.com/repos/o/r"
ZIPBALL = REPO + "/zipball/main"
CODELOAD = "https://codeload.github.com/o/r/legacy.zip/refs/heads/main"


def md(name):
    return f"---\nname: {name}\ndescription: Use when testing installs.\n---\nBody of {name}.\n".encode()


def zipped(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def name_of(files):
    return parse_skill_md(files["SKILL.md"].decode())["name"]


@pytest.fixture
def github(web, dns, monkeypatch):
    capped = client.CappedTransport
    monkeypatch.setattr(client, "CappedTransport", lambda cap: capped(cap, inner=httpx.MockTransport(web.handler)))
    monkeypatch.setattr(skill_install, "limiter", security.RateLimiter())
    monkeypatch.setattr(skill_install, "_CACHE", {})
    monkeypatch.setattr(settings, "github_token", "")
    monkeypatch.setattr(settings, "skills_sh_token", "")
    for i, host in enumerate(("api.github.com", "codeload.github.com", "raw.githubusercontent.com", "skills.sh")):
        dns.add(host, f"140.82.112.{10 + i}")
    web.on(REPO, lambda _r: httpx.Response(200, json={"default_branch": "main"}))
    return web


# -- GitHub -------------------------------------------------------------------

def test_a_zipball_redirect_is_followed_to_codeload_and_the_named_skill_is_taken(github):
    github.on(ZIPBALL, httpx.Response(302, headers={"location": CODELOAD}))
    github.on(CODELOAD, httpx.Response(200, content=zipped({
        "o-r-1a2b3c/SKILL.md": md("rootskill"),
        "o-r-1a2b3c/plugins/p/skills/wanted/SKILL.md": md("wanted"),
        "o-r-1a2b3c/plugins/p/skills/wanted/refs/notes.md": b"notes",
        "o-r-1a2b3c/plugins/p/skills/other/SKILL.md": md("other"),
    })))

    files = run(fetch_github_skill("o/r", "wanted"))

    assert name_of(files) == "wanted" and files["SKILL.md"] == md("wanted")
    assert sorted(files) == ["SKILL.md", "refs/notes.md"]
    assert github.urls() == [REPO, ZIPBALL, CODELOAD]
    assert github.requests[-1].url.host == "140.82.112.11"


def test_the_zipball_repo_hash_prefix_is_stripped(github):
    # Unstripped, the root skill's folder would be o-r-1a2b3c/ and take docs/ too.
    github.on(ZIPBALL, httpx.Response(200, content=zipped({
        "o-r-1a2b3c/SKILL.md": md("wanted"), "o-r-1a2b3c/notes.md": b"n", "o-r-1a2b3c/docs/guide.md": b"g",
    })))
    files = run(fetch_github_skill("o/r", "wanted"))
    assert sorted(files) == ["SKILL.md", "notes.md"] and name_of(files) == "wanted"


def test_a_zipball_redirect_off_github_is_never_requested(github, dns):
    github.on(ZIPBALL, httpx.Response(302, headers={"location": "https://evil.example/r.zip"}))
    github.on(REPO + "/git/trees/", lambda _r: httpx.Response(200, json={"truncated": False}))
    github.on(REPO + "/contents/", lambda _r: httpx.Response(404))
    with pytest.raises(SkillError, match="Could not find SKILL.md"):
        run(fetch_github_skill("o/r", "wanted"))
    assert not any("evil.example" in url for url in github.urls())
    assert "evil.example" not in dns.lookups


def test_a_truncated_tree_falls_back_to_the_contents_api(github):
    raw = "https://raw.githubusercontent.com/o/r/main/skills/wanted/SKILL.md"
    github.on(ZIPBALL, httpx.Response(404))
    github.on(REPO + "/git/trees/main", httpx.Response(200, json={"truncated": True}))
    github.on(REPO + "/contents/skills/wanted/SKILL.md", httpx.Response(200, json={"download_url": raw}))
    github.on(raw, httpx.Response(200, content=md("wanted")))

    assert run(fetch_github_skill("o/r", "wanted")) == {"SKILL.md": md("wanted")}
    assert github.urls() == [REPO, ZIPBALL, REPO + "/git/trees/main?recursive=1",
                             REPO + "/contents/skills/wanted/SKILL.md?ref=main", raw]


def test_the_contents_fallback_refuses_a_root_skill_of_another_name(github):
    raw = "https://raw.githubusercontent.com/o/r/main/SKILL.md"
    github.on(ZIPBALL, httpx.Response(404))
    github.on(REPO + "/git/trees/main", httpx.Response(200, json={"truncated": True}))
    github.on(REPO + "/contents/", lambda _r: httpx.Response(404))
    github.on(REPO + "/contents/SKILL.md", httpx.Response(200, json={"download_url": raw}))
    github.on(raw, httpx.Response(200, content=md("rootskill")))
    with pytest.raises(SkillError):
        run(fetch_github_skill("o/r", "wanted"))


# -- skills.sh ----------------------------------------------------------------

def test_catalog_search_maps_valid_hits_and_caches_the_answer(github):
    github.on("https://skills.sh/api/search?q=pdf%20tools", httpx.Response(200, json={"skills": [
        {"source": "o/r", "skillId": "pdf", "installs": "12"},
        {"source": "not a repo", "skillId": "x"},
        {"source": "o/r", "name": "docs", "installs": "lots"},
    ]}))
    out = run(catalog_search("pdf tools"))
    assert out == {"query": "pdf tools", "skills": [
        {"id": "o/r/pdf", "skillId": "pdf", "name": "pdf", "source": "o/r", "installs": 12,
         "url": "https://skills.sh/o/r/pdf"},
        {"id": "o/r/docs", "skillId": "docs", "name": "docs", "source": "o/r", "installs": 0,
         "url": "https://skills.sh/o/r/docs"},
    ]}
    assert run(catalog_search("PDF tools")) is out
    [req] = github.requests
    assert req.url.host == "140.82.112.13" and req.headers["host"] == "skills.sh"


def test_a_busy_catalog_is_a_retryable_error(github):
    github.on("https://skills.sh/api/search", httpx.Response(429))
    with pytest.raises(SkillError) as refused:
        run(catalog_search("pdf"))
    assert refused.value.status_code == 503


# -- which SKILL.md a skill id picks ----------------------------------------------

def test_a_repo_root_skill_never_stands_in_for_another_skill_id():
    files = {"SKILL.md": md("rootskill"), "plugins/p/skills/wanted/SKILL.md": md("wanted"),
             "plugins/p/skills/wanted/run.md": b"r"}
    slug, picked = extract_skill_files(files, skill_id="wanted")
    assert slug == "wanted" and picked == {"SKILL.md": md("wanted"), "run.md": b"r"}
    with pytest.raises(SkillError):
        extract_skill_files({"SKILL.md": md("rootskill")}, skill_id="wanted")
    assert extract_skill_files({"SKILL.md": md("wanted")}, skill_id="wanted")[0] == "wanted"


# -- archive limits ----------------------------------------------------------------

def understated(name, data):
    """A zip whose central directory declares a 1-byte entry."""
    raw = bytearray(zipped({name: data}))
    struct.pack_into("<I", raw, raw.find(b"PK\x01\x02") + 24, 1)
    return bytes(raw)


@pytest.mark.parametrize("archive, error", [
    (understated("SKILL.md", md("demo") * 50), "invalid zip"),
    (zipped({"SKILL.md": md("demo"), **{f"f{i}.md": b"x" * 250_000 for i in range(5)}}), "skill too large"),
    (zipped({"/SKILL.md": md("demo")}), "invalid skill path"),
    (zipped({"C:/SKILL.md": md("demo")}), "invalid skill path"),
    (zipped({"C:SKILL.md": md("demo")}), "invalid skill path"),
    (zipped({"a\\..\\..\\SKILL.md": md("demo")}), "invalid skill path"),
])
def test_unsafe_or_lying_archives_are_refused(archive, error):
    with pytest.raises(SkillError, match=error):
        files_from_zip(archive)


def test_a_loose_repo_scan_refuses_a_many_entry_archive():
    many = zipped({f"r/f{i}.md": b"x" for i in range(skill_install.MAX_LOOSE_FILES + 1)})
    with pytest.raises(SkillError, match="too many files"):
        files_from_zip(many, max_compressed=skill_install.MAX_ZIPBALL, loose=True)
    assert len(files_from_zip(zipped({f"r/f{i}.md": b"x" for i in range(10)}), loose=True)) == 10
