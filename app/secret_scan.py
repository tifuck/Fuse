"""Finds candidate secrets in a working folder's env files.

Only files named like env files are read (.env, .env.*, *.env, .envrc,
.npmrc, .pypirc), and each is parsed line by line as KEY=value: there is no
pattern search over free text. A line with no key is only a candidate when it
holds a value in a known provider format. Nothing found here is protected
until the user accepts it (app/secret_guard.py).
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass

from . import secret_fakes

logger = logging.getLogger(__name__)

MAX_FILES = 30
MAX_FILE_CHARS = 262_144
MAX_PER_SCAN = 50
SCAN_DEPTH = 3
_RESCAN_AFTER_S = 20.0

_TEMPLATE = re.compile(r"\.(example|sample|template|tpl|dist|defaults|schema)$")
_SKIP_DIRS = frozenset({"node_modules", ".git", "venv", ".venv", "vendor", "dist", "build", "target",
                        "__pycache__", ".tox", "site-packages", ".next", ".cache"})
_SECRET_NAME = re.compile(
    r"(?i)pass(?:word|wd|phrase)?|pwd|secret|token|api[_.-]?key|apikey|access[_.-]?key|private[_.-]?key"
    r"|auth|credential|signing|encryption|session[_.-]?key|dsn|salt|webhook")
_PLAIN_NAME = re.compile(
    r"(?i)(?:^|[_.-])(?:file|path|dir|url|uri|ttl|expir\w*|len|length|size|enabled?|mode|name|id|type|header"
    r"|prefix|suffix|alg|algorithm|version|endpoint|host|port|region|user|username|email|timeout|max|min|limit"
    r"|count|policy|rotation)s?$|public|publishable")
_PASSWORD_NAME = re.compile(r"(?i)pass|pwd")
_PLACEHOLDER = re.compile(
    r"(?i)^(?:x+|\*+|\.+|-+|_+|0+)$|example|placeholder|changeme|change[_-]me|redacted|dummy|your[_-]|xxxx"
    r"|replace[_-]?me|todo|fixme|<|>|\{\{|^op://|^vault:")
_INTERPOLATED = re.compile(r"\$\{|\$\(|`")
_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]{1,20}://")
_URL_CRED = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]{1,20}://[^\s/:@]{1,128}:(?P<v>[^\s@/]{1,256})@")
_HOSTLIKE = re.compile(r"^(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?::\d+)?$|^\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_NAMED_BEFORE = re.compile(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_.-]{0,80})\s*=\s*[\"']?$")
_WORD = re.compile(r"(?:[a-z]+|[A-Z]+|[A-Z][a-z]+)?[0-9]{0,4}")
# Values people leave as they came: guessable, and common words elsewhere, so
# swapping them would replace ordinary text. Never offered.
_GUESSABLE = frozenset("""
password password1 password123 passw0rd p@ssw0rd p@ssword admin admin123 administrator root toor secret
secret123 supersecret mysecret mysecretpassword mypassword postgres postgresql mysql mariadb redis mongo
mongodb rabbitmq guest default test test1234 testing testtest development production staging localhost
changeit letmein qwerty qwerty123 12345678 123456789 1234567890 abc12345 minioadmin rootpassword
dbpassword devpassword devsecret insecure django-insecure hunter2 welcome welcome1 example
""".split())


@dataclass(frozen=True)
class Found:
    path: str
    line: int
    key: str          # "" when the line had no key
    value: str
    kind: str
    prefix_len: int = 0


def is_env_name(name: str) -> bool:
    low = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    if _TEMPLATE.search(low):
        return False
    return low in (".envrc", ".npmrc", ".pypirc") or low == ".env" or low.startswith(".env.") or low.endswith(".env")


def _unquote(text: str, i: int, lines: list[str], n: int) -> tuple[str, int]:
    """The value starting at text[i:], and the index of its last line. A
    quoted value may run over several lines."""
    rest = text[i:].lstrip()
    if rest[:1] in ("'", '"'):
        quote, out, body, at = rest[0], [], rest[1:], n
        while True:
            j = 0
            while j < len(body):
                ch = body[j]
                if ch == "\\" and quote == '"' and j + 1 < len(body):
                    out.append({"n": "\n", "r": "\r", "t": "\t"}.get(body[j + 1], body[j + 1]))
                    j += 2
                    continue
                if ch == quote:
                    return "".join(out), at
                out.append(ch)
                j += 1
            at += 1
            if at >= len(lines) or at - n > 200:
                return "", n  # never closed: not a value
            out.append("\n")
            body = lines[at]
    cut = rest.find(" #")
    return (rest if cut < 0 else rest[:cut]).strip(), n


def parse(text: str) -> list[tuple[int, str, str]]:
    """(line number, key, value) for every line that holds something; key is
    "" for a line that is not KEY=value."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, n = [], 0
    while n < len(lines):
        raw, at = lines[n].strip(), n
        n += 1
        if not raw or raw[0] in ";[":
            continue
        if raw[0] == "#":
            # A commented-out line is still in the file a model may read.
            out.append((at + 1, "", raw.lstrip("# ")))
            continue
        if raw.startswith("export "):
            raw = raw[7:].lstrip()
        eq = raw.find("=")
        key = raw[:eq].strip() if eq > 0 else ""
        if not key or " " in key or len(key) > 120:
            out.append((at + 1, "", raw))
            continue
        value, last = _unquote(raw, eq + 1, lines, at)
        n = max(n, last + 1)
        if value:
            out.append((at + 1, key, value))
    return out


def _bits(value: str) -> float:
    size = (26 if re.search(r"[a-z]", value) else 0) + (26 if re.search(r"[A-Z]", value) else 0) \
        + (10 if re.search(r"\d", value) else 0) + (20 if re.search(r"[^A-Za-z0-9]", value) else 0)
    return len(value) * math.log2(size) if size else 0.0


def _wordy(value: str) -> bool:
    return all(_WORD.fullmatch(piece) for piece in re.split(r"[-_.]+", value) if piece)


def _plausible(key: str, value: str) -> bool:
    if len(value) < 8 or len(value) > 8192 or sum(c.isascii() and c.isalnum() for c in value) < 6:
        return False
    low = value.lower()
    if low in _GUESSABLE or low in ("true", "false", "null", "none", "undefined") or _PLACEHOLDER.search(value):
        return False
    if value.isdigit() and len(value) < 10:
        return False
    if value.startswith(("/", "./", "../", "~")) or re.match(r"^[A-Za-z]:\\", value):
        return False
    if _URL.match(value) or _EMAIL.match(value) or _HOSTLIKE.match(value) or len(set(value)) < 4:
        return False
    if _wordy(value) and not _PASSWORD_NAME.search(key):
        return False  # a passphrase may be words; a token or key never is
    classes = sum(bool(re.search(rx, value)) for rx in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if classes < 2 and len(value) < 16:
        return False  # one plain word: swapping it would replace ordinary text
    return _bits(value) >= 36


def classify(key: str, value: str) -> tuple[str, str, int] | None:
    """(kind, secret value, prefix length) when this pair is a candidate."""
    if not value or _INTERPOLATED.search(value) or secret_fakes.is_public(value):
        return None
    fmt = secret_fakes.match_format(value)
    if fmt:
        return fmt[0], value, fmt[1]
    cred = _URL_CRED.match(value)
    if cred:
        password = cred.group("v")
        return ("url", password, 0) if _plausible("password", password) else None
    if key and _SECRET_NAME.search(key) and not _PLAIN_NAME.search(key) and _plausible(key, value):
        return "named", value, 0
    return None


def find(path: str, text: str) -> list[Found]:
    out, seen = [], set()
    for line, key, value in parse(text):
        hits = []
        one = classify(key, value) if key else None
        if one:
            hits.append((key, *one))
        else:
            # No key, or a value that is not a secret as a whole: only a value
            # in a provider's own format counts.
            for kind, token in secret_fakes.find_formats(value):
                fmt = secret_fakes.match_format(token)
                if fmt and not secret_fakes.is_public(token):
                    named = _NAMED_BEFORE.search(value[:value.find(token)])
                    hits.append((named.group(1) if named and not key else "", kind, token, fmt[1]))
        for name, kind, secret, prefix_len in hits:
            if secret not in seen:
                seen.add(secret)
                out.append(Found(path, line, name, secret, kind, prefix_len))
    return out


def pick_files(names: list[str]) -> list[str]:
    files = [n for n in names if not n.endswith("/") and is_env_name(n)
             and not any(part in _SKIP_DIRS for part in n.replace("\\", "/").split("/")[:-1])]
    files.sort(key=lambda n: (n.count("/"), n))
    return files[:MAX_FILES]


# -- scanning a working folder ------------------------------------------------

_last: dict[tuple[int, int, str], float] = {}
_files: dict[tuple[int, int, str], list[str]] = {}
_dirty: set[tuple[int, int]] = set()
_locks: dict[tuple[int, int, str], asyncio.Lock] = {}


def mark_dirty(user_id: int, host_id: int) -> None:
    """A tool just ran on this host: its env files may have changed."""
    if user_id and host_id:
        _dirty.add((int(user_id), int(host_id)))


async def ensure(user_id: int, host_id: int, root: str, *, force: bool = False) -> int:
    """Scan the folder if it is due. Returns how many new candidates were
    offered. Never raises: a host that cannot be read is skipped. The folder
    is listed at most every _RESCAN_AFTER_S; between listings a tool round
    only re-reads the env files already found."""
    from . import secret_guard, workspace

    key = (int(user_id), int(host_id), str(root or ""))
    dirty = key[:2] in _dirty
    listed_recently = time.monotonic() - _last.get(key, -1e9) < _RESCAN_AFTER_S
    if not force and not dirty and listed_recently:
        return 0
    known = _files.get(key) if listed_recently and not force else None
    if known == []:
        _dirty.discard(key[:2])
        return 0
    if not await asyncio.to_thread(secret_guard.offers_enabled, user_id):
        return 0
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        _dirty.discard(key[:2])
        if known is None:
            _last[key] = time.monotonic()
        try:
            alias, files = await workspace.read_env_files(
                user_id, host_id, root, pick=pick_files, depth=SCAN_DEPTH, max_chars=MAX_FILE_CHARS, known=known)
        except Exception as exc:
            logger.info("Secret scan skipped for host %s: %s", host_id, str(exc)[:200])
            return 0
        if known is None:
            _files[key] = [path for path, _text in files]
        found: list[Found] = []
        for path, text in files:
            found += find(path, text)
        return await asyncio.to_thread(secret_guard.offer, user_id, host_id, alias, found[:MAX_PER_SCAN])
