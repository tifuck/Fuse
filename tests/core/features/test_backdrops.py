"""Custom backdrops: validation at the save boundary, one per chat theme,
owner-only access over HTTP, the sandboxing headers of /backdrop/<id>, the
account cascade, and the artifact bridge's setBackdrop."""
import pytest

from app import artifacts, backdrops, db
from app.backdrops import BackdropError
from scripts import init_db
from tests.core.auth._support import sign_in, web_client
from tests.core.helpers import quick_user

PAGE = "<!doctype html><html><body style='margin:0;background:#000'></body></html>"
MIGRATION = init_db.SCHEMA_SQL.parent / "2026-11-20-user-backdrops.sql"


@pytest.fixture
def store(fresh_db_fk, monkeypatch):
    # shortcut: the fixture schema is cloned from fuse_dev, which gets the
    # table when the migration is applied there; until then add it here.
    with db.tx() as conn:
        for statement in init_db.statements(MIGRATION.read_text(encoding="utf-8")):
            conn.native(statement)
    monkeypatch.setattr(backdrops, "WRITES_PER_MIN", 1000)
    return fresh_db_fk


def refused(uid, **kw):
    args = {"view": "desktop", "name": "Dusk", "scheme": "dark", "html": PAGE, **kw}
    with pytest.raises(BackdropError) as err:
        backdrops.create(uid, **args)
    return err.value


def test_save_validates_name_scheme_and_html(store):
    uid = quick_user()
    for kw in ({"name": ""}, {"name": " \x01\t "}, {"name": "n" * 41}, {"name": 5}, {"scheme": "blue"},
               {"html": ""}, {"html": None}, {"html": "a\x00b"}, {"html": "\ud800"},
               {"html": "x" * (backdrops.HTML_MAX + 1)}, {"html": "é" * (backdrops.HTML_MAX // 2 + 1)},
               {"view": None}, {"view": "nope"}):
        assert refused(uid, **kw).status_code == 422, kw
    saved = backdrops.create(uid, view="console", name="  Night\x07\n sky ", scheme=None, html="x" * backdrops.HTML_MAX)
    assert (saved["view"], saved["name"], saved["scheme"]) == ("console", "Night sky", "dark")
    assert backdrops.ID_RE.fullmatch(saved["id"]) and saved["created"].endswith("Z")
    assert backdrops.create(uid, view="desktop", name="n" * 40, scheme="light", html=PAGE)["scheme"] == "light"
    assert [b["name"] for b in backdrops.list_for_user(uid)] == ["n" * 40, "Night sky"]


def test_a_save_replaces_its_themes_backdrop_and_older_unthemed_ones(store):
    uid, other = quick_user(), quick_user("other@core.test")
    db.execute("INSERT INTO user_backdrops (public_id, user_id, name, scheme, html, created_at) "
               "VALUES ('legacy000001', ?, 'Old', 'dark', ?, '2026-11-20T00:00:00Z')", (uid,PAGE))
    first = backdrops.create(uid, view="desktop", name="One", scheme="dark", html=PAGE)
    kept = backdrops.create(uid, view="console", name="Two", scheme="dark", html=PAGE)
    mine = backdrops.create(other, view="desktop", name="Theirs", scheme="dark", html=PAGE)
    second = backdrops.create(uid, view="desktop", name="Three", scheme="dark", html=PAGE)
    assert [(b["id"], b["view"]) for b in backdrops.list_for_user(uid)] == [(second["id"], "desktop"), (kept["id"], "console")]
    assert backdrops.html_for(uid, first["id"]) is None
    assert [b["id"] for b in backdrops.list_for_user(other)] == [mine["id"]]


def test_only_the_owner_reads_or_deletes_and_the_document_is_sandboxed(store):
    owner, other = quick_user(), quick_user("other@core.test")
    with web_client() as web:
        sign_in(web, owner)
        res = web.post("/api/backdrops", json={"view": "combined", "name": "Dusk", "scheme": "light", "html": PAGE})
        assert res.status_code == 200, res.text
        ident = res.json()["backdrop"]["id"]
        bad = web.post("/api/backdrops", json={"view": "combined", "name": "Dusk", "scheme": "dark", "html": "a\x00"})
        assert bad.status_code == 422 and "NUL" in bad.json()["detail"]
        assert [b["id"] for b in web.get("/api/backdrops").json()["backdrops"]] == [ident]
        doc = web.get(f"/backdrop/{ident}")
        assert doc.status_code == 200 and doc.text == PAGE
        assert doc.headers["content-type"] == "text/html; charset=utf-8"
        assert doc.headers["content-security-policy"] == (
            "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src data: blob:; font-src data:; media-src data: blob:; worker-src blob:; connect-src 'none'; "
            "frame-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'")
        assert doc.headers["x-content-type-options"] == "nosniff"
        assert doc.headers["cache-control"] == "private, max-age=300"
        assert doc.headers["referrer-policy"] == "no-referrer"

        sign_in(web, other)
        assert web.get(f"/backdrop/{ident}").status_code == 404
        assert web.delete(f"/api/backdrops/{ident}").status_code == 404
        assert web.get("/api/backdrops").json() == {"backdrops": []}

        sign_in(web, owner)
        assert web.delete(f"/api/backdrops/{ident}").json() == {"ok": True}
        assert web.get(f"/backdrop/{ident}").status_code == 404


def test_deleting_the_account_deletes_its_backdrops(store):
    uid = quick_user()
    backdrops.create(uid, view="desktop", name="Dusk", scheme="dark", html=PAGE)
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    assert db.fetchone("SELECT COUNT(*) AS n FROM user_backdrops WHERE user_id=?", (uid,))["n"] == 0


def test_artifact_pages_offer_set_backdrop():
    page = artifacts.render_document(PAGE)
    assert "setBackdrop:function" in page and 'send("backdrop"' in page
