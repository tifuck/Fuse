"""Account administration: what only admins may do, the guards that keep a
site from losing its last admin, and what deleting an account takes with it."""
import pytest

from app import accounts, db, vault
from tests.core.auth._support import PASSWORD, sign_in
from tests.core.helpers import make_user, quick_user


def _state():
    return (db.fetchall("SELECT id, email, is_admin FROM users ORDER BY id"),
            db.fetchall("SELECT email FROM registration_whitelist ORDER BY id"),
            db.fetchall("SELECT provider, model FROM model_prices ORDER BY provider, model"))


def test_a_member_cannot_invite_promote_demote_delete_or_reprice(web):
    admin = quick_user("root@core.test", admin=True)
    member = quick_user("member@core.test")
    other = quick_user("other@core.test")
    accounts.add_whitelist("invited@core.test")
    invite_id = accounts.list_whitelist()[0]["id"]
    before = _state()

    sign_in(web, member)
    for path, form in [
        ("/admin/users", {"email": "evil@core.test"}),
        (f"/admin/users/{member}/role", {"admin": "1"}),
        (f"/admin/users/{admin}/role", {"admin": "0"}),
        (f"/admin/users/{other}/delete", {}),
        (f"/admin/users/invites/{invite_id}/delete", {}),
        ("/admin/pricing", {"provider": "grok", "model": "evil", "input_rate": "9", "output_rate": "9"}),
        ("/admin/pricing/delete", {"provider": "grok", "model": "x"}),
    ]:
        res = web.post(path, data=form, follow_redirects=False)
        assert res.status_code == 303 and res.headers["location"] == "/", path
    assert _state() == before

    # Positive control: the same requests from an admin take effect.
    sign_in(web, admin)
    assert web.post(f"/admin/users/{member}/role", data={"admin": "1"}, follow_redirects=False).status_code == 303
    assert web.post("/admin/users", data={"email": "new@core.test"}, follow_redirects=False).status_code == 303
    assert web.post(f"/admin/users/{other}/delete", follow_redirects=False).status_code == 303
    assert accounts.get_user(member)["is_admin"] == 1 and accounts.get_user(other) is None
    assert [row["email"] for row in accounts.list_whitelist()] == ["invited@core.test", "new@core.test"]


def test_the_last_admin_cannot_be_demoted_or_deleted_and_nobody_can_act_on_themselves(fresh_db):
    root = quick_user("root@core.test", admin=True)
    other = quick_user("other@core.test")
    with pytest.raises(ValueError, match="last_admin"):
        accounts.set_admin(root, False, actor_id=other)
    with pytest.raises(ValueError, match="last_admin"):
        accounts.delete_user(root, actor_id=other)
    with pytest.raises(ValueError, match="self"):
        accounts.set_admin(root, False, actor_id=root)
    with pytest.raises(ValueError, match="self"):
        accounts.delete_user(root, actor_id=root)
    with pytest.raises(ValueError, match="missing"):
        accounts.delete_user(root + other + 1000, actor_id=root)
    assert accounts.admin_count() == 1

    # With a second admin the first may go.
    accounts.set_admin(other, True, actor_id=root)
    accounts.set_admin(root, False, actor_id=other)
    assert accounts.admin_count() == 1 and accounts.get_user(other)["is_admin"] == 1


def test_deleting_an_account_ends_its_keys_vault_and_donations(fresh_db):
    from app.sources.grok import ensure_grok_source

    admin = quick_user("root@core.test", admin=True)
    uid = make_user("leaver@core.test")
    raw = accounts.create_api_key(uid, "ci")
    vault.unlock(uid, PASSWORD)
    cred = accounts.add_credential(ensure_grok_source(), "leaver", "not-a-real-secret", donated_by=uid)

    assert accounts.delete_user(uid, actor_id=admin) == "leaver@core.test"
    assert accounts.get_user(uid) is None
    assert accounts.user_by_api_key(raw) is None
    assert db.fetchone("SELECT COUNT(*) AS n FROM api_keys WHERE user_id=?", (uid,))["n"] == 0
    assert not vault.unlocked(uid)
    # The donated pool account stays (others' usage history points at it) but
    # stops serving and belongs to nobody.
    row = db.fetchone("SELECT donated_by, disabled FROM credentials WHERE id=?", (cred,))
    assert (row["donated_by"], row["disabled"]) == (None, 1)
