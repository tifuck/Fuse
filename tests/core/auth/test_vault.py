"""The per-user vault: host secrets are encrypted with a DEK that only the
account password unwraps, never with APP_SECRET, and no failure to decrypt
ever writes a blank secret back."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from app import accounts, db, hosts, vault
from app.crypto import DecryptionError, decrypt, decrypt_with_dek, encrypt, unwrap_dek, vault_kdf
from tests.core.auth._support import PASSWORD, cookie, login, set_session, sign_in
from tests.core.helpers import make_user, wait_until

NEW_PASSWORD = "newpass12345"


def _host(uid, alias="box", **fields):
    return hosts.add_host(uid, {"alias": alias, "hostname": "box.example.com", "username": "deploy",
                                "auth_type": "password", "secret": "s3cret", **fields})


def _stored(host_id):
    return db.fetchone("SELECT secret, passphrase FROM user_hosts WHERE id=?", (host_id,))


def _unwrap(uid, password):
    row = db.fetchone("SELECT vault_salt, wrapped_dek, vault_kdf FROM users WHERE id=?", (uid,))
    salt = row["vault_salt"] if isinstance(row["vault_salt"], bytes) else bytes(row["vault_salt"])
    return unwrap_dek(row["wrapped_dek"], vault_kdf(password, salt, json.loads(row["vault_kdf"])))


def test_host_secrets_are_encrypted_with_the_password_wrapped_dek_not_app_secret(fresh_db):
    uid = make_user()
    vault.unlock(uid, PASSWORD)
    host = _host(uid, passphrase="pass-phrase")
    stored = _stored(host["id"])
    assert "s3cret" not in stored["secret"] and "s3cret" not in json.dumps(host)
    with pytest.raises(DecryptionError):
        decrypt(stored["secret"])
    # The DEK in RAM is exactly the one the account password unwraps.
    dek = vault.get_dek(uid)
    assert len(dek) == 32 and _unwrap(uid, PASSWORD) == dek
    assert decrypt_with_dek(stored["secret"], dek) == "s3cret"
    assert hosts.secret_plain(uid, host["id"])[:2] == ("s3cret", "pass-phrase")


def test_a_wrong_password_unlocks_nothing_and_leaves_the_vault_as_it_was(fresh_db):
    uid = make_user()
    before = accounts.get_user(uid)["wrapped_dek"]
    with pytest.raises(DecryptionError):
        vault.unlock(uid, "wrong-password")
    with pytest.raises(ValueError, match="auth"):
        accounts.unlock_vault(uid, "wrong-password")
    assert not vault.unlocked(uid)
    assert accounts.get_user(uid)["wrapped_dek"] == before
    accounts.unlock_vault(uid, PASSWORD)
    assert vault.unlocked(uid)


@pytest.mark.parametrize("params", [
    {"alg": "scrypt", "n": 2**30, "r": 8, "p": 1, "dklen": 32},
    {"alg": "scrypt", "n": 16384, "r": 8, "p": 1, "dklen": 64},
    {"alg": "pbkdf2", "n": 16384},
])
def test_tampered_kdf_parameters_are_refused_before_any_key_derivation(fresh_db, params):
    # A stored n=2**30 would make scrypt allocate ~128 GiB at unlock.
    uid = make_user()
    db.execute("UPDATE users SET vault_kdf=? WHERE id=?", (json.dumps(params), uid))
    with pytest.raises(DecryptionError):
        vault.unlock(uid, PASSWORD)
    assert not vault.unlocked(uid)


def test_a_locked_vault_refuses_secrets_and_edits_keep_the_ciphertext(fresh_db):
    uid = make_user()
    vault.unlock(uid, PASSWORD)
    host = _host(uid, passphrase="pass-phrase")
    cipher = _stored(host["id"])
    vault.lock(uid)
    with pytest.raises(hosts.HostError) as locked:
        hosts.secret_plain(uid, host["id"])
    assert locked.value.status_code == 401
    with pytest.raises(hosts.HostError):
        hosts.patch_host(uid, host["id"], {"secret": "new-secret"})
    # Editing anything else needs no DEK and must not touch the secret.
    hosts.patch_host(uid, host["id"], {"label": "renamed", "default_cwd": "/srv"})
    assert _stored(host["id"]) == cipher
    vault.unlock(uid, PASSWORD)
    assert hosts.secret_plain(uid, host["id"])[:2] == ("s3cret", "pass-phrase")


def test_a_password_change_rewraps_the_same_dek(fresh_db):
    uid = make_user()
    vault.unlock(uid, PASSWORD)
    host = _host(uid)
    dek = vault.get_dek(uid)
    accounts.change_password(uid, PASSWORD, NEW_PASSWORD)
    assert hosts.secret_plain(uid, host["id"])[0] == "s3cret"
    assert _unwrap(uid, NEW_PASSWORD) == dek
    vault.lock(uid)
    with pytest.raises(DecryptionError):
        vault.unlock(uid, PASSWORD)
    vault.unlock(uid, NEW_PASSWORD)
    assert hosts.secret_plain(uid, host["id"])[0] == "s3cret"


def test_an_undecryptable_vault_fails_loudly_and_changes_nothing(web):
    uid = make_user()
    vault.unlock(uid, PASSWORD)
    host = _host(uid)
    vault.lock(uid)
    # Corrupt the wrapped DEK: the password is right, the vault is broken.
    wrapped = accounts.get_user(uid)["wrapped_dek"]
    broken = encrypt("not-a-dek")
    db.execute("UPDATE users SET wrapped_dek=? WHERE id=?", (broken, uid))
    before = accounts.get_user(uid)
    cipher = _stored(host["id"])

    with pytest.raises(ValueError, match="vault"):
        accounts.change_password(uid, PASSWORD, NEW_PASSWORD)
    with pytest.raises(ValueError, match="vault"):
        accounts.unlock_vault(uid, PASSWORD)
    sign_in(web, uid)
    # Reported as a broken vault, never as a wrong password.
    assert web.post("/account/vault/unlock", json={"password": PASSWORD}).status_code == 500
    changed = web.post("/account/password", json={"old_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert changed.status_code == 500
    after = accounts.get_user(uid)
    assert (after["password_hash"], after["wrapped_dek"], after["vault_salt"]) == (
        before["password_hash"], broken, before["vault_salt"])
    assert _stored(host["id"]) == cipher
    assert wrapped != broken


def test_a_secret_under_another_key_is_an_error_not_a_blank(fresh_db):
    uid = make_user()
    vault.unlock(uid, PASSWORD)
    host = _host(uid)
    foreign = encrypt("someone else's key")  # APP_SECRET Fernet, not the DEK
    db.execute("UPDATE user_hosts SET secret=? WHERE id=?", (foreign, host["id"]))
    with pytest.raises(hosts.HostError) as failed:
        hosts.secret_plain(uid, host["id"])
    assert failed.value.status_code == 500
    assert _stored(host["id"])["secret"] == foreign


def test_concurrent_first_unlocks_of_an_account_without_a_vault_share_one_dek(fresh_db):
    # An account whose vault was dropped (password reset) makes its DEK at the
    # next unlock. Two at once used to each make one; the second overwrote the
    # first, whose DEK may already have encrypted a host secret.
    uid = make_user()
    db.execute("UPDATE users SET vault_salt=NULL, wrapped_dek=NULL, vault_kdf=NULL WHERE id=?", (uid,))
    second = threading.Thread(target=vault.unlock, args=(uid, PASSWORD))
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        first = vault.ensure_user_vault(conn, uid, PASSWORD)
        mine = conn.native("SELECT CONNECTION_ID()").fetchone()[0]
        second.start()

        def second_reached_the_row():
            # Wait until the other unlock is inside its read or write of the
            # row this transaction holds, then let this one commit.
            rows = conn.native("SHOW FULL PROCESSLIST").fetchall()
            return any(row[0] != mine and "users" in str(row[7] or "") and "vault" in str(row[7] or "")
                       for row in rows)

        wait_until(second_reached_the_row, message="the second unlock never reached the users row")
    second.join(10)
    assert not second.is_alive()
    assert vault.get_dek(uid) == first
    assert _unwrap(uid, PASSWORD) == first


def test_the_vault_locks_itself_when_idle_unless_a_run_holds_it(fresh_db, monkeypatch):
    uid = make_user()
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(vault, "time", SimpleNamespace(monotonic=lambda: clock.now, time=time.time))
    vault.unlock(uid, PASSWORD)
    vault.pin_run(uid, "run-1")
    clock.now += vault.IDLE_SECONDS + 1
    assert vault.unlocked(uid)
    vault.unpin_run(uid, "run-1")
    clock.now += vault.IDLE_SECONDS + 1
    assert not vault.unlocked(uid)
    with pytest.raises(vault.VaultLocked):
        vault.get_dek(uid)


def _keep_payload(uid, sid="sid-1"):
    return json.loads(decrypt(vault.issue_keep(uid, sid)))


def _keep(payload, **changes):
    return encrypt(json.dumps({**payload, **changes}))


def test_a_keep_cookie_restores_only_its_own_sign_in_and_vault(fresh_db):
    uid = make_user()
    other = make_user("other@core.test")
    vault.unlock(uid, PASSWORD)
    dek = vault.get_dek(uid)
    payload = _keep_payload(uid)
    vault.lock_all()
    # Positive control: an untouched keep-cookie restores the same DEK.
    assert vault.restore_keep(uid, _keep(payload), "sid-1") and vault.get_dek(uid) == dek
    vault.lock_all()

    refused = [
        (_keep(payload), other, "sid-1"),           # another account
        (_keep(payload), uid, "sid-2"),             # another sign-in
        (_keep(payload), uid, ""),
        (_keep(payload, v=1), uid, "sid-1"),        # no vault fingerprint
        (_keep(payload, exp=int(time.time()) - 1), uid, "sid-1"),
        (_keep(payload, fp="0" * 32), uid, "sid-1"),  # another vault
        (_keep(payload, dek="AAAA"), uid, "sid-1"),
        ("not-a-token", uid, "sid-1"),
    ]
    for token, who, sid in refused:
        assert not vault.restore_keep(who, token, sid)
        assert not vault.unlocked(uid) and not vault.unlocked(other)


def test_a_keep_cookie_from_before_a_password_reset_is_refused(fresh_db):
    uid = make_user("ada@core.test")
    vault.unlock(uid, PASSWORD)
    token = _keep(_keep_payload(uid))
    vault.lock_all()
    accounts.reset_password("ada@core.test", NEW_PASSWORD)
    assert not vault.restore_keep(uid, token, "sid-1")
    assert not vault.unlocked(uid)


def test_a_keep_cookie_reloads_the_vault_only_for_the_session_it_was_issued_to(web):
    uid = make_user("ada@core.test")
    login(web, "ada@core.test")
    mine, keep = cookie(web), cookie(web, vault.KEEP_COOKIE)
    sign_in(web, uid)  # the same account, another sign-in
    vault.lock(uid)
    web.cookies.set(vault.KEEP_COOKIE, keep)
    assert web.get("/chat/threads").status_code == 200
    assert not vault.unlocked(uid)

    web.cookies.clear()
    web.cookies.set("session", mine)
    web.cookies.set(vault.KEEP_COOKIE, keep)
    assert web.get("/chat/threads").status_code == 200
    assert vault.unlocked(uid)


def test_a_password_reset_drops_the_vault_and_every_sign_in(web):
    uid = make_user("ada@core.test")
    vault.unlock(uid, PASSWORD)
    ssh = _host(uid, passphrase="pass-phrase")
    db.execute(
        "INSERT INTO user_hosts (user_id, alias, label, hostname, port, username, auth_type, secret, passphrase,"
        " default_cwd, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (uid, "laptop", "", "", 22, "", "agent", "", "", "", db.now(), db.now()),
    )
    db.execute(
        "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, user_agent, created_at) VALUES (?,?,?,?,?,?)",
        (uid, "https://push.example/phone", "k", "a", "", db.now()),
    )
    session = sign_in(web, uid)

    assert accounts.reset_password("ada@core.test", NEW_PASSWORD) == ["box"]
    user = accounts.get_user(uid)
    assert user["wrapped_dek"] is None and user["vault_salt"] is None
    # The old DEK is gone, so its ciphertext is dead weight: cleared, not kept.
    assert _stored(ssh["id"]) == {"secret": "", "passphrase": ""}
    assert db.fetchone("SELECT COUNT(*) AS n FROM user_hosts WHERE user_id=? AND auth_type='agent'", (uid,))["n"] == 1
    assert db.fetchone("SELECT COUNT(*) AS n FROM push_subscriptions WHERE user_id=?", (uid,))["n"] == 0
    set_session(web, session)
    assert web.get("/chat/threads", follow_redirects=False).status_code == 401
    assert accounts.login("ada@core.test", PASSWORD) is None
    # The next unlock makes a fresh vault under the new password.
    vault.lock(uid)
    accounts.unlock_vault(uid, NEW_PASSWORD)
    assert accounts.get_user(uid)["wrapped_dek"]
    with pytest.raises(ValueError, match="missing"):
        accounts.reset_password("nobody@core.test", NEW_PASSWORD)
