import hashlib
import hmac
import os
import threading
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
import base64

from .config import settings


class DecryptionError(RuntimeError):
    """Encrypted application data cannot be authenticated with APP_SECRET."""


# Stored as "scrypt$n$r$p$salt$digest"; a bare "salt$digest" is the old
# n=2**14 format, still verified and rehashed at the next sign-in.
PASSWORD_SCRYPT = (2**17, 8, 1)
_LEGACY_SCRYPT = (2**14, 8, 1)
# n=2**17, r=8 needs 128 MiB per hash (hashlib's default cap is 32 MiB); the
# semaphore keeps a burst of sign-ins from holding more than a few at once.
_SCRYPT_MAXMEM = 2**28
_scrypt_slots = threading.BoundedSemaphore(4)


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    with _scrypt_slots:
        return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=_SCRYPT_MAXMEM)


def _password_params(stored: str) -> tuple[tuple[int, int, int], str, str]:
    parts = stored.split("$")
    if len(parts) == 2:
        return _LEGACY_SCRYPT, parts[0], parts[1]
    if len(parts) != 6 or parts[0] != "scrypt":
        raise ValueError("unknown password hash format")
    n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
    # A tampered row must not make scrypt allocate more than the current params.
    if not (2 <= n and 1 <= r and n * r <= 2**20 and 1 <= p <= 4):
        raise ValueError("invalid password hash params")
    return (n, r, p), parts[4], parts[5]


def hash_password(password: str) -> str:
    if len(password) > 256:
        raise ValueError("password too long")
    salt = os.urandom(16)
    n, r, p = PASSWORD_SCRYPT
    digest = _scrypt(password, salt, n, r, p)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if len(password) > 256:
        return False
    try:
        (n, r, p), salt_hex, digest_hex = _password_params(stored)
        digest = _scrypt(password, bytes.fromhex(salt_hex), n, r, p)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def password_needs_rehash(stored: str) -> bool:
    try:
        return _password_params(stored)[0] != PASSWORD_SCRYPT
    except Exception:
        return False


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b"fuse.v1", iterations=100_000)
    key = base64.urlsafe_b64encode(kdf.derive(settings.app_secret.encode()))
    return Fernet(key)


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise DecryptionError("encrypted credential data could not be decrypted") from exc


VAULT_KDF = {"alg": "scrypt", "n": 16384, "r": 8, "p": 1, "dklen": 32, "v": 1}


def _fernet_from_key(key32: bytes) -> Fernet:
    if not isinstance(key32, (bytes, bytearray)) or len(key32) != 32:
        raise ValueError("vault key must be 32 bytes")
    return Fernet(base64.urlsafe_b64encode(bytes(key32)))


def vault_kdf(password: str, salt: bytes, params: dict | None = None) -> bytes:
    spec = params or VAULT_KDF
    if spec.get("alg") != "scrypt":
        raise DecryptionError("unsupported vault kdf")
    try:
        n = int(spec.get("n") or 16384)
        r = int(spec.get("r") or 8)
        p = int(spec.get("p") or 1)
        dklen = int(spec.get("dklen") or 32)
    except (TypeError, ValueError) as exc:
        raise DecryptionError("invalid vault kdf params") from exc
    # Bound the stored params: a tampered vault_kdf row must not make
    # scrypt allocate unbounded memory (cost ~128*n*r bytes) at unlock.
    if not (2 <= n <= 2**20) or not (1 <= r <= 32) or not (1 <= p <= 8) or dklen != 32:
        raise DecryptionError("invalid vault kdf params")
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=dklen)


def wrap_dek(dek: bytes, kek: bytes) -> str:
    return _fernet_from_key(kek).encrypt(dek).decode()


def unwrap_dek(token: str, kek: bytes) -> bytes:
    try:
        return _fernet_from_key(kek).decrypt(token.encode())
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise DecryptionError("vault dek could not be decrypted") from exc


def encrypt_with_dek(plain: str, dek: bytes) -> str:
    return _fernet_from_key(dek).encrypt(plain.encode()).decode()


def decrypt_with_dek(token: str, dek: bytes) -> str:
    try:
        return _fernet_from_key(dek).decrypt(token.encode()).decode()
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise DecryptionError("host secret could not be decrypted") from exc
