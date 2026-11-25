"""APP_SECRET encryption of pool credentials, and the boot check that refuses
to run with a key that cannot read them (a wrong key used to decrypt to
nothing and write blank values back)."""
import hashlib
import json
import os

import pytest

from app import accounts, crypto, db
from app.config import settings
from app.crypto import DecryptionError, decrypt, encrypt, hash_password, verify_password

SENTINEL = "credential_crypto_sentinel_v1"


@pytest.fixture
def other_secret():
    """Switch to a different APP_SECRET, as a misconfigured boot would."""
    saved = settings.app_secret

    def switch():
        settings.app_secret = "another-secret-of-sufficient-length-0123456789"
        crypto._fernet.cache_clear()

    try:
        yield switch
    finally:
        settings.app_secret = saved
        crypto._fernet.cache_clear()


@pytest.fixture
def sentinel(fresh_db):
    """schema_meta survives the per-test reset: put the boot sentinel back."""
    saved = db.fetchone("SELECT value FROM schema_meta WHERE key=?", (SENTINEL,))
    yield
    db.execute("DELETE FROM schema_meta WHERE key=?", (SENTINEL,))
    if saved:
        db.execute("INSERT INTO schema_meta(key, value) VALUES (?, ?)", (SENTINEL, saved["value"]))


def _source():
    from app.sources.grok import ensure_grok_source

    return ensure_grok_source()


def test_secrets_round_trip_and_a_wrong_key_is_an_error(other_secret):
    token = encrypt("sk-real-secret")
    assert "sk-real-secret" not in token and decrypt(token) == "sk-real-secret"
    other_secret()
    with pytest.raises(DecryptionError):
        decrypt(token)
    for garbage in ("", "not-fernet", token[:-4]):
        with pytest.raises(DecryptionError):
            decrypt(garbage)


def test_credentials_are_stored_encrypted(fresh_db):
    cred = accounts.add_credential(_source(), "label", "sk-real-secret", extra={"refresh_token": "rt-1"})
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred,))
    assert "sk-real-secret" not in row["secret"] and "rt-1" not in row["extra"]
    assert accounts.cred_secret(row) == "sk-real-secret"
    assert accounts.cred_extra(row) == {"refresh_token": "rt-1"}


def test_boot_writes_the_sentinel_and_migrates_plain_json_extras(sentinel):
    db.execute("DELETE FROM schema_meta WHERE key=?", (SENTINEL,))
    cred = accounts.add_credential(_source(), "label", "sk-real-secret")
    db.execute("UPDATE credentials SET extra=? WHERE id=?", (json.dumps({"plan": "pro"}), cred))
    accounts.validate_credential_encryption()
    stored = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred,))
    assert not stored["extra"].lstrip().startswith("{")
    assert accounts.cred_extra(stored) == {"plan": "pro"}
    assert decrypt(db.fetchone("SELECT value FROM schema_meta WHERE key=?", (SENTINEL,))["value"])
    # Idempotent: a second boot changes nothing.
    accounts.validate_credential_encryption()
    assert db.fetchone("SELECT extra FROM credentials WHERE id=?", (cred,))["extra"] == stored["extra"]


def test_boot_refuses_a_key_that_cannot_read_the_database(sentinel, other_secret):
    accounts.validate_credential_encryption()  # sentinel under the real key
    cred = accounts.add_credential(_source(), "label", "sk-real-secret")
    db.execute("UPDATE credentials SET extra=? WHERE id=?", (json.dumps({"plan": "pro"}), cred))
    before = db.fetchone("SELECT secret, extra FROM credentials WHERE id=?", (cred,))
    other_secret()
    with pytest.raises(DecryptionError):
        accounts.validate_credential_encryption()
    # Nothing was rewritten under the wrong key.
    assert db.fetchone("SELECT secret, extra FROM credentials WHERE id=?", (cred,)) == before


def test_boot_refuses_a_wrong_key_even_before_any_credential_exists(sentinel, other_secret):
    # The sentinel is what catches a wrong key on a database with no pool
    # accounts yet; the first one added would be encrypted under it.
    accounts.validate_credential_encryption()
    other_secret()
    with pytest.raises(DecryptionError):
        accounts.validate_credential_encryption()


def test_boot_refuses_a_credential_it_cannot_decrypt(sentinel):
    accounts.validate_credential_encryption()
    cred = accounts.add_credential(_source(), "label", "sk-real-secret")
    db.execute("UPDATE credentials SET secret='corrupt' WHERE id=?", (cred,))
    with pytest.raises(DecryptionError):
        accounts.validate_credential_encryption()
    assert db.fetchone("SELECT secret FROM credentials WHERE id=?", (cred,))["secret"] == "corrupt"


@pytest.mark.parametrize("plain", ["not json", "[1, 2]"])
def test_a_decrypted_extra_that_is_not_an_object_is_a_data_error(fresh_db, plain):
    cred = accounts.add_credential(_source(), "label", "sk-real-secret")
    db.execute("UPDATE credentials SET extra=? WHERE id=?", (encrypt(plain), cred))
    with pytest.raises(accounts.CredentialDataError):
        accounts.cred_extra(db.fetchone("SELECT * FROM credentials WHERE id=?", (cred,)))


def test_password_hashes_verify_only_the_right_password():
    stored = hash_password("password12")
    assert stored != hash_password("password12")  # salted
    assert verify_password("password12", stored)
    assert stored.startswith("scrypt$131072$8$1$") and not crypto.password_needs_rehash(stored)
    # Stored params past the current cost are refused, not run.
    tampered = stored.replace("scrypt$131072$", "scrypt$1073741824$")
    for wrong, against in [("password13", stored), ("x" * 257, stored), ("password12", "garbage"),
                           ("password12", "zz$zz"), ("password12", tampered)]:
        assert not verify_password(wrong, against)
    with pytest.raises(ValueError):
        hash_password("x" * 257)


def test_an_old_hash_still_signs_in_and_is_rehashed_with_the_current_params(fresh_db):
    from tests.core.helpers import quick_user

    uid = quick_user("ada@core.test")
    salt = os.urandom(16)
    legacy = salt.hex() + "$" + hashlib.scrypt(b"password12", salt=salt, n=2**14, r=8, p=1, dklen=32).hex()
    db.execute("UPDATE users SET password_hash=? WHERE id=?", (legacy, uid))
    assert verify_password("password12", legacy) and crypto.password_needs_rehash(legacy)
    # A wrong password leaves the old hash alone.
    assert accounts.login("ada@core.test", "password13") is None
    assert accounts.get_user(uid)["password_hash"] == legacy
    user = accounts.login("ada@core.test", "password12")
    stored = accounts.get_user(uid)["password_hash"]
    assert stored.startswith("scrypt$131072$8$1$") and verify_password("password12", stored)
    # The returned row is what is stored, so the new session's fingerprint holds.
    assert user["password_hash"] == stored
