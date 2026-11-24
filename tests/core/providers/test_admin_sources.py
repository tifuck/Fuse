"""Admin source routes: deleting a source with a legacy model route, and the
pool access form saved all or nothing."""
import pytest
from starlette.datastructures import ImmutableMultiDict

from app import db
from app.sources import pool_policy
from tests.core.auth._support import sign_in, web_client
from tests.core.helpers import quick_user

from ._support import route, source


def test_deleting_a_source_drops_its_legacy_model_routes(fresh_db_fk):
    sid = source("openai", {"base_url": "https://up.test/v1"})
    route("legacy-model", sid)
    with web_client() as web:
        sign_in(web, quick_user("admin@core.test", admin=True))
        assert web.post(f"/admin/sources/{sid}/delete", follow_redirects=False).status_code == 303
    assert db.fetchone("SELECT id FROM sources WHERE id=?", (sid,)) is None
    assert db.fetchone("SELECT model FROM model_routes WHERE source_id=?", (sid,)) is None


def test_a_pool_access_save_that_fails_part_way_changes_no_pool(fresh_db, monkeypatch):
    first, second = pool_policy.POOL_KINDS[:2]
    before = pool_policy.admin_policy(first)
    real_get = ImmutableMultiDict.get

    def failing_get(self, key, default=None):
        if key == f"{second}_pool_shared":
            raise RuntimeError("save failed part way")
        return real_get(self, key, default)

    monkeypatch.setattr(ImmutableMultiDict, "get", failing_get)
    form = {f"{first}_pool_shared": "0", f"{first}_open_access": "1"}
    with web_client() as web, pytest.raises(RuntimeError):
        sign_in(web, quick_user("admin@core.test", admin=True))
        web.post("/admin/pools", data=form, follow_redirects=False)
    monkeypatch.setattr(ImmutableMultiDict, "get", real_get)
    assert pool_policy.admin_policy(first) == before
