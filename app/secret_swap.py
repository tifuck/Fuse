"""Secret swap: models see realistic stand-ins for a user's secrets.

A chat started while the account switch is on (users.chat_secret_swap, copied
to chat_threads.secret_swap at its first turn) never sends a detected secret
to a model. Each secret gets one fake per user (secret_tokens): the same
length, the same punctuation in the same places, letters and digits drawn
from the secret's own alphabet, its type prefix (sk-proj-, ghp_, AKIA...)
kept and format checksums (Luhn, GitHub CRC32) valid, so the model works with
it like any key. What a model writes goes back through `restore` before the
user sees it or a tool on their machines runs it. The model is never told.

What keeps data intact:
- Rows bound for a model are processed once and marked (MARK). A marked row
  is never rewritten, so prompt caches and signed reasoning stay valid.
- Each model transcript (a chat's main conversation, each subagent's) is the
  only record of what its model has seen: a value it already shows in clear
  stays clear there, so a model never meets one value in two forms. Nothing
  shared decides for it, so transcripts cannot contradict each other.
- Strong values (long, random) are swapped wherever they appear for the user;
  weak ones (short passwords) only where detected, or where their stand-in
  is already in view.
- Nothing here stops a chat: a missed swap is logged, never refused.
- Punctuation is never changed, so restoring cannot change quoting.
- A tool call still holding part of a fake after restore is refused, never run.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import math
import re
import threading
import time
import zlib
from dataclasses import dataclass
from urllib.parse import quote

from . import db
from .config import settings
from .crypto import DecryptionError, decrypt, encrypt

logger = logging.getLogger(__name__)

MARK = "_fuse_tok"
# Arguments of these keep the fake: web tools send them to third parties, and
# run_code's own calls are restored one by one, so the code never holds a
# real value it could transform past the swap.
NO_RESTORE = frozenset({"web_search", "read_url", "run_code"})
REFUSED = ("[Fuse] This tool call was NOT executed: one of its values is incomplete or altered. "
           "Re-issue it with the exact values from the conversation.")

_ANCHOR = 6          # every table key is longer than this
_MIN_WEAK = 8        # shortest context-detected value
_WINDOW = 12         # leftover check: alnum chars of a fake that must never reach a tool
_DIGITS = "0123456789"
_LOWER = "abcdefghijklmnopqrstuvwxyz"
_UPPER = _LOWER.upper()
_B62 = _DIGITS + _UPPER + _LOWER


class SwapUnavailable(RuntimeError):
    """A swapping chat whose stored pairs cannot be read (APP_SECRET changed)."""


# -- detection --------------------------------------------------------------

@dataclass(frozen=True)
class Hit:
    start: int
    end: int
    kind: str
    keep: int = 0   # leading characters kept as they are (type prefix)
    tail: int = 0   # trailing characters kept as they are


def _rule(kind, pattern, tail=4):
    return kind, re.compile(pattern), tail


# Provider formats, most specific first: on the same span the earlier wins.
_RULES = [
    _rule("anthropic", r"(?<![\w-])(?P<p>sk-ant-[a-z]{2,8}\d{2}-)[A-Za-z0-9_-]{32,}"),
    _rule("openrouter", r"(?<![\w-])(?P<p>sk-or-v1-)[a-f0-9]{64}(?![\w-])"),
    _rule("openai", r"(?<![\w-])(?P<p>sk-(?:proj-|svcacct-|admin-)?)[A-Za-z0-9_-]{20,}"),
    _rule("github", r"(?<![\w-])(?P<p>gh[pousr]_)[A-Za-z0-9]{36}(?![\w-])", tail=0),
    _rule("github_pat", r"(?<![\w-])(?P<p>github_pat_)[A-Za-z0-9_]{70,}"),
    _rule("gitlab", r"(?<![\w-])(?P<p>glpat-)[A-Za-z0-9_-]{20,}"),
    _rule("aws", r"(?<![A-Za-z0-9])(?P<p>AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}(?![A-Za-z0-9])"),
    _rule("slack", r"(?<![\w-])(?P<p>xox[abposr]-|xapp-)[A-Za-z0-9-]{10,}"),
    _rule("stripe", r"(?<![\w-])(?P<p>(?:sk|rk)_(?:live|test)_|whsec_)[A-Za-z0-9]{20,}"),
    _rule("google", r"(?<![\w-])(?P<p>AIza)[A-Za-z0-9_-]{35}(?![\w-])"),
    _rule("huggingface", r"(?<![\w-])(?P<p>hf_)[A-Za-z0-9]{30,}"),
    _rule("npm", r"(?<![\w-])(?P<p>npm_)[A-Za-z0-9]{36}(?![\w-])"),
    _rule("sendgrid", r"(?<![\w-])(?P<p>SG\.)[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}(?![\w-])"),
    _rule("digitalocean", r"(?<![\w-])(?P<p>do[opr]_v1_)[a-f0-9]{64}(?![\w-])"),
]
_STRONG_KINDS = frozenset([kind for kind, _rx, _tail in _RULES] + ["jwt", "pem", "card"])
# Only the signature: the header and claims stay readable, as a model expects.
_JWT = re.compile(r"(?<![\w-])eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.(?P<v>[A-Za-z0-9_-]{16,})(?![\w-])")
_PEM = re.compile(r"-----BEGIN ([A-Z0-9 ]*)PRIVATE KEY-----(.*?)-----END \1PRIVATE KEY-----", re.S)
_PEM_RUN = re.compile(r"[A-Za-z0-9+/=]+")
_BEARER = re.compile(r"(?i:\bbearer)[ \t]+(?P<v>[A-Za-z0-9._~+/-]{20,}=*)")
_URL_CRED = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]{1,20}://[^\s/:@'\"<>]{1,128}:(?P<v>[^\s@/'\"<>]{1,256})@")
_ASSIGN = re.compile(
    r"""(?<![A-Za-z0-9_.])(?P<name>[A-Za-z_][A-Za-z0-9_.-]{0,60})["']?[ \t]*(?:=|:|=>)[ \t]*"""
    # An unquoted value must end the token: `Optional[str]`, `os.getenv(` are code.
    r"""(?P<q>["'])?(?P<v>[^\s"'`,;<>(){}\[\]]{8,256})(?(q)(?P=q)|(?=[\s,;}\])'"]|$))""")
_SECRET_NAME = re.compile(
    r"(?i)pass(?:word|wd|phrase)?|pwd|secret|token|api[_.-]?key|apikey|access[_.-]?key|private[_.-]?key"
    r"|auth[_.-]?key|credential|signing[_.-]?key|encryption[_.-]?key|session[_.-]?key")
_NOT_SECRET_NAME = re.compile(
    r"(?i)(?:file|path|dir|url|uri|name|len|length|size|ttl|expir\w*|type|count|id|hint|prompt|header|field"
    r"|policy|rotation|ref|env|var|limit|max|min|timeout|regex|pattern|endpoint|mode|enabled|required"
    r"|label|format|prefix|suffix|usage|cost|budget)s?$|tokeniz")
_PASSWORD_NAME = re.compile(r"(?i)pass|pwd")
_PLACEHOLDER = re.compile(
    r"(?i)^(?:x+|\*+|\.+|-+|_+|0+)$|example|placeholder|changeme|change_me|redacted|dummy|your[_-]|xxxx"
    r"|replace[_-]?me|todo|fixme|<|>|\$\{|\{\{|%s|^\$|^/|^\./|^~|^https?:")
# Code, not a value: `self.db_password`, `settings.API_KEY`, `refreshToken`.
# Only real identifier shapes, so a random letters-only password is not one.
_IDENT = r"(?:[a-z]+(?:[A-Z][a-z]+)+|[A-Z][a-z]+(?:[A-Z][a-z]+)+|_?[a-z]+(?:_[a-z]+)+|[A-Z]+(?:_[A-Z]+)+)"
_PART = r"(?:%s|[a-z]+|[A-Z][a-z]+|[A-Z]+)" % _IDENT
_CODE = re.compile(r"%s(?:\.%s)+|%s" % (_PART, _PART, _IDENT))
# One word-like piece: `aws`, `secrets`, `v2`, `2024`, `Prod`.
_WORD = re.compile(r"(?:[a-z]+|[A-Z]+|[A-Z][a-z]+)?[0-9]*")
_CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")
_CARD_CONTEXT = re.compile(r"(?i)card|\bcc\b|credit|debit|visa|master|amex|\bpan\b|payment|cvv")
_CARD_IIN = re.compile(r"^(?:4|5[1-5]|2[2-7]|3[47]|6(?:011|5|4[4-9])|35)")
# Published test and documentation values: models know them by heart.
_PUBLIC = frozenset({
    "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "4242424242424242", "4000056655665556", "5555555555554444", "2223003122003222", "5200828282828210",
    "5105105105105100", "378282246310005", "371449635398431", "6011111111111117", "6011000990139424",
    "3056930009020004", "36227206271667", "3566002020360505", "4111111111111111", "4012888888881881",
    "4000000000000002", "4000000000009995", "4000000000000077",
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",  # the jwt.io example token's signature
})


def _classes(value: str) -> int:
    return (any(c in _LOWER for c in value) + any(c in _UPPER for c in value) + any(c in _DIGITS for c in value)
            + any(not (c.isascii() and c.isalnum()) for c in value))


def _weak_ok(value: str) -> bool:
    return (len(value) >= _MIN_WEAK and _classes(value) >= 2 and len(set(value)) > 3
            and not _PLACEHOLDER.search(value) and value not in _PUBLIC and not _CODE.fullmatch(value))


def _wordy(value: str) -> bool:
    """Hyphenated words and numbers (`aws-secrets-manager`, `sk-learn-tutorial-2024`):
    a name, never a random token."""
    return all(_WORD.fullmatch(piece) for piece in re.split(r"[-_.]+", value) if piece)


def _random_body(kind: str, body: str) -> bool:
    """A provider-shaped value whose random part is really random, not
    `xxxx` or words. AWS ids may lack digits, so only placeholders count there."""
    chars = [c for c in body if _alnum(c)]
    if len(set(chars)) <= 4:
        return False
    return kind == "aws" or not _wordy(body)


def _encodable(value: str) -> bool:
    try:
        value.encode()
    except UnicodeEncodeError:  # lone surrogates from decoded JSON
        return False
    return True


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def detect(text: str) -> list[Hit]:
    """Secret values in text, non-overlapping, in order. Specific formats win
    over context matches on any overlap."""
    if not text:
        return []
    specific, generic = [], []
    for order, (kind, rx, tail) in enumerate(_RULES):
        for m in rx.finditer(text):
            value = m.group(0)
            if value in _PUBLIC or "EXAMPLE" in value or not _random_body(kind, value[len(m.group("p")):]):
                continue
            specific.append((m.start(), -len(value), order, Hit(m.start(), m.end(), kind, len(m.group("p")),
                                                                tail if len(value) >= 20 else 0)))
    for m in _JWT.finditer(text):
        if m.group("v") in _PUBLIC:
            continue
        specific.append((m.start("v"), -len(m.group("v")), 90, Hit(m.start("v"), m.end("v"), "jwt")))
    for m in _PEM.finditer(text):
        body_start, first = m.start(2), True
        for run in _PEM_RUN.finditer(m.group(2)):
            start, end = body_start + run.start(), body_start + run.end()
            # A JSON-escaped body reads "\nMIIE...": the n belongs to the escape.
            if start > 0 and text[start - 1] == "\\" and text[start] in "nrt":
                start += 1
            if end - start < 16:
                continue
            specific.append((start, -(end - start), 91, Hit(start, end, "pem", min(40, end - start - 12) if first else 0)))
            first = False
    for rx, kind in ((_BEARER, "bearer"), (_URL_CRED, "url")):
        for m in rx.finditer(text):
            value = m.group("v")
            if (kind == "url" and not _weak_ok(value)) or _PLACEHOLDER.search(value) or _wordy(value):
                continue
            generic.append((m.start("v"), -len(value), 0, Hit(m.start("v"), m.end("v"), kind)))
    pos = 0
    while (m := _ASSIGN.search(text, pos)) is not None:
        # A rejected pair (`https://host/?access_token=...`) must not hide one inside it.
        pos = m.start() + 1
        name, value = m.group("name"), m.group("v")
        if not _SECRET_NAME.search(name) or _NOT_SECRET_NAME.search(name) or not _weak_ok(value):
            continue
        # A passphrase may be words; a token, secret or key never is.
        if _wordy(value) and not _PASSWORD_NAME.search(name):
            continue
        generic.append((m.start("v"), -len(value), 1, Hit(m.start("v"), m.end("v"), "assign")))
        pos = m.end()
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        before = text[max(0, m.start() - 40):m.start()].rsplit("\n", 1)[-1]  # this line only
        if (len(digits) < 13 or digits in _PUBLIC or not _CARD_IIN.match(digits) or not _luhn(digits)
                or not _CARD_CONTEXT.search(before)):
            continue
        generic.append((m.start(), -len(m.group(0)), 2, Hit(m.start(), m.end(), "card", 1, 4)))
    specific = [item for item in specific if _encodable(text[item[3].start:item[3].end])]
    generic = [item for item in generic if _encodable(text[item[3].start:item[3].end])]
    chosen: list[Hit] = []
    for pool in (specific, generic):
        for *_key, hit in sorted(pool, key=lambda item: item[:3]):
            if all(hit.end <= c.start or hit.start >= c.end for c in chosen):
                chosen.append(hit)
    return sorted(chosen, key=lambda h: h.start)


# -- fakes ------------------------------------------------------------------

def _key() -> bytes:
    return hashlib.sha256(b"fuse.secret-swap.v1\0" + settings.app_secret.encode()).digest()


def _digest(user_id: int, value: str) -> str:
    return hmac.new(_key(), f"{int(user_id)}\0".encode() + value.encode(), hashlib.sha256).hexdigest()


class _Rand:
    """Deterministic draws from an HMAC seed: the same secret always gets the same fake."""

    def __init__(self, seed: bytes):
        self.seed, self.buf, self.n = seed, b"", 0

    def choice(self, alphabet: str) -> str:
        limit = 256 - 256 % len(alphabet)
        while True:
            if not self.buf:
                self.buf = hashlib.shake_256(self.seed + self.n.to_bytes(4, "big")).digest(64)
                self.n += 1
            byte, self.buf = self.buf[0], self.buf[1:]
            if byte < limit:
                return alphabet[byte % len(alphabet)]


def _alnum(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def _alphabet(chars) -> str:
    present = set(chars)
    for alphabet in (_DIGITS, _DIGITS + "abcdef", _DIGITS + "ABCDEF"):
        if present <= set(alphabet):
            return alphabet
    return "".join(a for a in (_LOWER, _UPPER, _DIGITS) if present & set(a))


def _b62(n: int, width: int) -> str:
    out = ""
    while n:
        n, r = divmod(n, 62)
        out = _B62[r] + out
    return out.rjust(width, "0")


def make_fake(user_id: int, kind: str, value: str, keep: int = 0, tail: int = 0, attempt: int = 0) -> str:
    rng = _Rand(hmac.new(_key(), f"fake\0{int(user_id)}\0{kind}\0{attempt}\0".encode() + value.encode(),
                         hashlib.sha256).digest())
    if kind == "github" and len(value) == keep + 36:
        body = "".join(rng.choice(_B62) for _ in range(30))
        return value[:keep] + body + _b62(zlib.crc32(body.encode()), 6)
    end = len(value) - tail
    alphabet = _alphabet(c for c in value[keep:end] if _alnum(c))
    out = list(value)
    slots = [i for i in range(keep, end) if _alnum(out[i])]
    for i in slots:
        out[i] = rng.choice(alphabet)
    if kind == "card" and slots:
        last = slots[-1]
        for d in _DIGITS:
            out[last] = d
            if _luhn("".join(c for c in out if c.isdigit())):
                break
    return "".join(out)


def _randomized(value: str, keep: int, tail: int) -> list[str]:
    return [c for c in value[keep:len(value) - tail] if _alnum(c)]


def _strong(kind: str, value: str, keep: int, tail: int) -> bool:
    if kind in _STRONG_KINDS:
        return True
    middle = _randomized(value, keep, tail)
    bits = len(middle) * math.log2(len(_alphabet(middle))) if middle else 0
    return len(value) >= 12 and bits >= 72


@dataclass(frozen=True)
class Pair:
    id: int
    kind: str
    real: str
    fake: str
    strong: bool


def _forms(pair: Pair) -> list[tuple[str, str]]:
    """(real form, fake form): the encodings a value is met in. Punctuation is
    shared, so each encoding of the fake has exactly the real's shape."""
    real, fake = pair.real, pair.fake
    out = [(real, fake), (json.dumps(real)[1:-1], json.dumps(fake)[1:-1]),
           (json.dumps(real, ensure_ascii=False)[1:-1], json.dumps(fake, ensure_ascii=False)[1:-1]),
           (quote(real, safe=""), quote(fake, safe=""))]
    if pair.strong:
        out += [(real.upper(), fake.upper()), (real.lower(), fake.lower())]
    seen, forms = set(), []
    for a, b in out:
        if a not in seen and len(a) == len(b) and a != b:
            seen.add(a)
            forms.append((a, b))
    return forms


# -- exact replacement -------------------------------------------------------

_BEFORE = re.compile(r"(?:(?<![A-Za-z0-9])|(?<=\\[nrt]))(?=(.{%d}))" % _ANCHOR, re.S)


def _boundary_before(text: str, i: int) -> bool:
    if i == 0 or not _alnum(text[i - 1]):
        return True
    return i >= 2 and text[i - 2] == "\\" and text[i - 1] in "nrt"


class _Table:
    """Many literals replaced at token boundaries, leftmost-longest. Lookups
    go through a short anchor, so cost follows the text, not the key count."""

    def __init__(self, mapping: dict):
        self.mapping = {k: v for k, v in mapping.items() if len(k) > _ANCHOR}
        self.anchors: dict[str, list[str]] = {}
        self.short: set[str] = set()
        self.maxlen = 0
        for key in self.mapping:
            self.anchors.setdefault(key[:_ANCHOR], []).append(key)
            self.short.update(key[:n] for n in range(1, _ANCHOR + 1))
            self.maxlen = max(self.maxlen, len(key))
        for keys in self.anchors.values():
            keys.sort(key=len, reverse=True)

    def matches(self, text: str, head: str = "", tail: str = ""):
        if not self.mapping or not text:
            return
        ctx = head[-2:] + text
        off = len(ctx) - len(text)
        pos = off
        for m in _BEFORE.finditer(ctx, off):
            i = m.start()
            if i < pos:
                continue
            for key in self.anchors.get(m.group(1), ()):
                end = i + len(key)
                if not ctx.startswith(key, i):
                    continue
                after = ctx[end] if end < len(ctx) else tail[:1]
                if _alnum(key[-1]) and after and _alnum(after):
                    continue
                yield i - off, end - off, key
                pos = end
                break

    def sub(self, text: str, repl=None, head: str = "", tail: str = "") -> str:
        out, pos = [], 0
        for start, end, key in self.matches(text, head, tail):
            value = repl(key) if repl else self.mapping[key]
            if value is None:
                continue
            out.append(text[pos:start])
            out.append(value)
            pos = end
        if not out:
            return text
        out.append(text[pos:])
        return "".join(out)

    def pending(self, buf: str, head: str = "") -> int:
        """Length of buf's tail that could still grow into a key (or is one
        whose end boundary is not known yet)."""
        ctx = head[-2:] + buf
        off = len(ctx) - len(buf)
        for i in range(max(off, len(ctx) - self.maxlen), len(ctx)):
            if not _boundary_before(ctx, i):
                continue
            rest = ctx[i:]
            if len(rest) <= _ANCHOR:
                if rest in self.short:
                    return len(ctx) - i
            elif any(key.startswith(rest) for key in self.anchors.get(rest[:_ANCHOR], ())):
                return len(ctx) - i
        return 0


# -- per-user pairs ------------------------------------------------------------

class _Vault:
    def __init__(self, user_id: int):
        self.user_id = int(user_id)
        self.lock = threading.RLock()
        self.by_real: dict[str, Pair] = {}
        self.by_fake: dict[str, Pair] = {}
        self.by_id: dict[int, Pair] = {}
        self.last_id = 0
        self.loaded_at = 0.0
        self.broken = False
        self.version = 0
        self._cache: dict = {}

    def _load(self, row) -> Pair | None:
        try:
            data = json.loads(decrypt(row["cipher"]))
        except (DecryptionError, ValueError, TypeError):
            logger.error("Secret swap: pair %s of user %s cannot be decrypted", row["id"], self.user_id)
            self.broken = True
            return None
        pair = Pair(int(row["id"]), str(row["kind"]), data["real"], data["fake"], bool(row["strong"]))
        self.by_real[pair.real] = self.by_fake[pair.fake] = self.by_id[pair.id] = pair
        self.last_id = max(self.last_id, pair.id)
        self.version += 1
        return pair

    def refresh(self, conn=None, force: bool = False) -> None:
        with self.lock:
            if not force and time.monotonic() - self.loaded_at < 1.0:
                return
            sql = "SELECT id, kind, strong, cipher FROM secret_tokens WHERE user_id=? AND id>? ORDER BY id"
            args = (self.user_id, self.last_id)
            rows = conn.execute(sql, args).fetchall() if conn is not None else db.fetchall(sql, args)
            for row in rows:
                self._load(row)
            self.loaded_at = time.monotonic()

    def add(self, kind: str, real: str, keep: int, tail: int) -> Pair:
        with self.lock:
            if real in self.by_real:
                return self.by_real[real]
            self.refresh(force=True)
            if real in self.by_real:
                return self.by_real[real]
            strong = _strong(kind, real, keep, tail)
            for attempt in range(8):
                fake = make_fake(self.user_id, kind, real, keep, tail, attempt)
                if fake == real or fake in self.by_fake or fake in self.by_real:
                    continue
                cipher = encrypt(json.dumps({"real": real, "fake": fake}))
                # Its own transaction: a pair outlives a caller that rolls back.
                with db.tx() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO secret_tokens (user_id, real_hmac, fake_hmac, kind, strong, cipher, created_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (self.user_id, _digest(self.user_id, real), _digest(self.user_id, fake), kind, int(strong),
                         cipher, db.now()))
                    row = conn.execute("SELECT id, kind, strong, cipher FROM secret_tokens WHERE user_id=? AND real_hmac=?",
                                       (self.user_id, _digest(self.user_id, real))).fetchone()
                pair = self._load(row) if row else None
                if pair is not None and pair.real == real:
                    return pair
            raise SwapUnavailable("no free stand-in for a detected secret")

    def table(self, name, build, stamp=None):
        with self.lock:
            key = (self.version, stamp)
            hit = self._cache.get(name)
            if hit is None or hit[0] != key:
                hit = (key, build())
                self._cache[name] = hit
            return hit[1]

    def known_fake_in(self, value: str) -> bool:
        return value in self.by_fake or any(True for _ in self.restore_table().matches(value))

    def restore_table(self) -> _Table:
        return self.table("restore", lambda: _Table({fake: real for p in self.by_id.values() for real, fake in _forms(p)}))

    def windows(self) -> set[str]:
        def build():
            out = set()
            for p in self.by_id.values():
                if not p.strong:
                    continue
                for run in re.finditer(r"[A-Za-z0-9]{%d,}" % _WINDOW, p.fake):
                    text = run.group(0)
                    for i in range(len(text) - _WINDOW + 1):
                        window = text[i:i + _WINDOW]
                        if not window.isdigit() and window not in p.real:
                            out.add(window)
            return out
        return self.table("windows", build)

    def fragments(self) -> tuple[dict, dict]:
        """Starts and ends of strong values, for text clipped through a secret."""
        def build():
            starts, ends = {}, {}
            for p in self.by_id.values():
                if not p.strong or p.real.isdigit():
                    continue
                floor = 16 if len(_alphabet(_randomized(p.real, 0, 0))) <= 16 else _WINDOW
                for k in range(floor, len(p.real)):
                    starts[p.real[:k]] = (p, p.fake[:k])
                    ends[p.real[-k:]] = (p, p.fake[-k:])
            return starts, ends
        return self.table("fragments", build)


_vaults: dict[int, _Vault] = {}
_vaults_lock = threading.Lock()


def _vault(user_id: int, conn=None) -> _Vault:
    with _vaults_lock:
        vault = _vaults.get(int(user_id))
        if vault is None:
            vault = _vaults[int(user_id)] = _Vault(int(user_id))
    vault.refresh(conn)
    return vault


# -- one chat's view -----------------------------------------------------------

_RUN = re.compile(r"[^\s\"'`<>()\[\]{},;|]{%d,}" % _WINDOW)
_B64_TOKEN = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/_-]{16,}={0,2}(?![A-Za-z0-9+/=_-])")


def _row_texts(row: dict):
    content = row.get("content")
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                yield part["text"]
    for call in row.get("tool_calls") or []:
        fn = call.get("function") if isinstance(call, dict) else None
        if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
            yield fn["arguments"]
    if isinstance(row.get("reasoning_content"), str):
        yield row["reasoning_content"]


def _rewrite_row(row: dict, fn) -> None:
    """Apply fn to a row's text: never to images, files, signed or encrypted reasoning."""
    content = row.get("content")
    if isinstance(content, str):
        row["content"] = fn(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("text", "input_text") and isinstance(part.get("text"), str):
                part["text"] = fn(part["text"])
    for call in row.get("tool_calls") or []:
        fn_obj = call.get("function") if isinstance(call, dict) else None
        if isinstance(fn_obj, dict) and isinstance(fn_obj.get("arguments"), str):
            fn_obj["arguments"] = fn(fn_obj["arguments"])
    if isinstance(row.get("reasoning_content"), str):
        row["reasoning_content"] = fn(row["reasoning_content"])


def _lazy(fn):
    box = []

    def get():
        if not box:
            box.append(fn() if fn else "")
        return box[0]
    return get


class Swap:
    """The swap for one model transcript: a chat's main conversation, or one
    subagent's. What to show is decided by that transcript alone, never by a
    shared record: a value it already shows in clear stays clear, a value
    whose stand-in it shows stays swapped, a new strong value is swapped.
    Without a transcript (titles, Jev, background results) values are
    swapped for that one view and nothing is decided."""

    def __init__(self, vault: _Vault, thread_id: int | None = None):
        self.vault = vault
        self.thread_id = thread_id
        self.states: dict[int, bool] = {}  # pair id -> clear, for the bound transcript
        self._rows = None

    def bind(self, rows_fn) -> None:
        """The transcript this swap decides for: the rows its model has read."""
        self._rows = rows_fn
        self.states = {}

    # decisions

    def _use(self, pair: Pair, sent, detected: bool = True) -> bool:
        """Swap this value here? `detected`: found by detection right now, not
        only as a known value (weak values need that or their stand-in in view)."""
        with self.vault.lock:
            state = self.states.get(pair.id)
        if state is not None:
            return not state
        if sent is None:
            return detected or pair.strong  # a one-off view: swap, decide nothing
        seen = sent()
        if pair.real in seen:
            clear = True
        elif detected or pair.strong or pair.fake in seen:
            clear = False
        else:
            return False  # a weak value this transcript never had swapped
        with self.vault.lock:
            self.states[pair.id] = clear
        if clear:
            logger.info("Secret swap: a %s value stays clear in thread %s (its model already saw it)",
                        pair.kind, self.thread_id)
        return not clear

    def _tokenize_table(self) -> _Table:
        with self.vault.lock:
            clear = frozenset(i for i, c in self.states.items() if c)

        def build():
            mapping = {}
            for p in self.vault.by_id.values():
                if p.id in clear:
                    continue
                for real, fake in _forms(p):
                    mapping[real] = (p, fake)
            return _Table(mapping)
        return self.vault.table("tok", build, clear)

    # model-bound text

    def _sent_fn(self, sent):
        if sent is not None:
            return sent
        rows = self._rows
        if rows is None:
            return None
        return _lazy(lambda: "\n".join(t for r in (rows() or []) if isinstance(r, dict) for t in _row_texts(r)))

    def _exact(self, text: str, sent) -> str:
        table = self._tokenize_table()
        return table.sub(text, lambda key: (lambda p, f: f if self._use(p, sent, detected=False) else None)(
            *table.mapping[key]))

    def _fragments(self, text: str, sent) -> str:
        starts, ends = self.vault.fragments()
        if not starts:
            return text
        out, pos = [], 0
        for m in _RUN.finditer(text):
            run, base = m.group(0), m.start()
            found = None
            if run in ends:
                found = (base, base + len(run), ends[run])
            else:
                cut = max(run.find("="), run.find(":"))
                piece = run[cut + 1:] if cut >= 0 else run
                if piece in starts:
                    found = (base + len(run) - len(piece), base + len(run), starts[piece])
                elif run in starts:
                    found = (base, base + len(run), starts[run])
            if found and found[0] >= pos:
                start, end, (pair, fake) = found
                if self._use(pair, sent):
                    out += [text[pos:start], fake]
                    pos = end
        if not out:
            return text
        out.append(text[pos:])
        return "".join(out)

    def _b64(self, text: str, fn) -> str:
        if len(text) < 16:
            return text
        out, pos = [], 0
        for m in _B64_TOKEN.finditer(text):
            token = m.group(0)
            urlsafe = "-" in token or "_" in token
            body = token.rstrip("=")
            if len(body) % 4 == 1:
                continue
            try:
                raw = (base64.urlsafe_b64decode if urlsafe else base64.b64decode)(body + "=" * (-len(body) % 4))
                decoded = raw.decode("utf-8")
            except (binascii.Error, ValueError, UnicodeDecodeError):
                continue
            new = fn(decoded)
            if new == decoded:
                continue
            encoded = (base64.urlsafe_b64encode if urlsafe else base64.b64encode)(new.encode()).decode()
            if len(body) == len(token):
                encoded = encoded.rstrip("=")
            if len(encoded) != len(token):
                continue
            out += [text[pos:m.start()], encoded]
            pos = m.end()
        if not out:
            return text
        out.append(text[pos:])
        return "".join(out)

    def tokenize(self, text, *, sent=None, detect_new: bool = True, _depth: int = 0):
        """Real -> fake for text a model is about to read. Known strong values
        anywhere, weak ones this transcript has swapped, ends of known values
        cut by a clip, base64 copies, then newly detected values."""
        if not isinstance(text, str) or not text:
            return text
        sent = self._sent_fn(sent)
        out = self._exact(text, sent)
        if not _depth:
            out = self._b64(out, lambda decoded: self.tokenize(decoded, sent=sent, detect_new=detect_new, _depth=1))
        out = self._fragments(out, sent)
        if not detect_new:
            return out
        pieces, pos, again = [], 0, False
        for hit in detect(out):
            value = out[hit.start:hit.end]
            if self.vault.known_fake_in(value):
                continue
            pair = self.vault.add(hit.kind, value, hit.keep, hit.tail)
            if not self._use(pair, sent):
                continue
            pieces += [out[pos:hit.start], pair.fake]
            pos = hit.end
            again = again or pair.strong
        if pieces:
            pieces.append(out[pos:])
            out = "".join(pieces)
        return self._exact(out, sent) if again else out

    def to_model(self, text):
        """Model-written text shown to the user, back to what the model wrote:
        the inverse of restore for the bound transcript's values, no detection."""
        if not isinstance(text, str) or not text:
            return text
        return self._exact(text, self._sent_fn(None))

    def _note_authored(self, text: str) -> None:
        # Values the model wrote itself are known to it: they stay clear here.
        for hit in detect(text):
            value = text[hit.start:hit.end]
            if self.vault.known_fake_in(value):
                continue
            pair = self.vault.add(hit.kind, value, hit.keep, hit.tail)
            with self.vault.lock:
                known = pair.id in self.states
            if not known:
                self._use(pair, lambda: value)

    def prepare(self, rows: list) -> bool:
        """Swap every unprocessed row in place and mark it. Marked rows were
        sent already and are never touched again. True when a row changed."""
        batch = [r for r in rows if isinstance(r, dict) and not r.get(MARK)]
        if not batch:
            return False
        fresh = {id(r) for r in batch if r.get("role") != "assistant"}
        sent = _lazy(lambda: "\n".join(t for r in rows if isinstance(r, dict) and id(r) not in fresh for t in _row_texts(r)))
        changed = False
        for row in batch:
            if row.get("role") != "assistant":
                continue
            for text in list(_row_texts(row)):
                self._note_authored(text)
            before = list(_row_texts(row))
            _rewrite_row(row, lambda s: self._exact(s, sent))
            changed = changed or before != list(_row_texts(row))
        for row in batch:
            if row.get("role") == "assistant":
                continue
            before = list(_row_texts(row))
            _rewrite_row(row, lambda s: self.tokenize(s, sent=sent))
            changed = changed or before != list(_row_texts(row))
        for row in batch:
            row[MARK] = 1
        return changed

    def leaks(self, messages) -> list[str]:
        """Kinds of this transcript's swapped values still present in messages.
        A tripwire for a path that skipped prepare: logged, never blocking."""
        with self.vault.lock:
            swapped = {i for i, clear in self.states.items() if not clear}
        table = self._tokenize_table()
        found = []
        for row in messages or []:
            if not isinstance(row, dict):
                continue
            for text in _row_texts(row):
                for _s, _e, key in table.matches(text):
                    pair = table.mapping[key][0]
                    if pair.id in swapped:
                        found.append(pair.kind)
        return found

    # user- and tool-bound text

    def restore(self, text):
        if not isinstance(text, str) or not text:
            return text
        return self.vault.restore_table().sub(text)

    def restore_value(self, value):
        if isinstance(value, str):
            return self.restore(value)
        if isinstance(value, list):
            return [self.restore_value(v) for v in value]
        if isinstance(value, dict):
            return {k: self.restore_value(v) for k, v in value.items()}
        return value

    def restore_args(self, name: str, arguments: str) -> tuple[str, bool]:
        """Arguments for a tool on the user's side, and whether they may run.
        Third-party tools keep the fakes; any fake left after restoring (cut,
        split or altered) refuses the call."""
        if not arguments or name in NO_RESTORE:
            return arguments, True
        table = self.vault.restore_table()
        if not table.mapping:
            return arguments, True
        out = table.sub(arguments)
        out = self._b64(out, table.sub)
        windows = self.vault.windows()
        if windows:
            for run in re.finditer(r"[A-Za-z0-9]{%d,}" % _WINDOW, out):
                text = run.group(0)
                if any(text[i:i + _WINDOW] in windows for i in range(len(text) - _WINDOW + 1)):
                    logger.warning("Secret swap: refused a %s call holding part of a stand-in", name)
                    return arguments, False
        if out != arguments:
            try:
                json.loads(out)
            except (ValueError, RecursionError):
                return arguments, False
        return out, True

    def restorer(self) -> "Restorer":
        return Restorer(self)


class Restorer:
    """Restores a stream of deltas, holding back a tail that may be the start
    of a stand-in until the next delta (or flush) settles it."""

    def __init__(self, swap: Swap):
        self.swap = swap
        self.buf = ""
        self.prev = ""

    def push(self, text: str) -> str:
        if not text:
            return ""
        table = self.swap.vault.restore_table()
        buf = self.buf + text
        hold = table.pending(buf, self.prev) if table.mapping else 0
        emit, self.buf = buf[:len(buf) - hold], buf[len(buf) - hold:]
        if not emit:
            return ""
        out = table.sub(emit, head=self.prev, tail=self.buf)
        self.prev = (self.prev + emit)[-2:]
        return out

    def flush(self) -> str:
        if not self.buf:
            return ""
        emit, self.buf = self.buf, ""
        out = self.swap.vault.restore_table().sub(emit, head=self.prev)
        self.prev = (self.prev + emit)[-2:]
        return out


# -- entry points ------------------------------------------------------------

def _fetchone(conn, sql, args):
    return conn.execute(sql, args).fetchone() if conn is not None else db.fetchone(sql, args)


def for_thread(user_id, thread_id, conn=None) -> Swap | None:
    """The chat's swap, or None when the chat does not swap."""
    if not user_id or not thread_id:
        return None
    row = _fetchone(conn, "SELECT secret_swap FROM chat_threads WHERE id=? AND user_id=?", (int(thread_id), int(user_id)))
    if not row or not row["secret_swap"]:
        return None
    vault = _vault(int(user_id), conn)
    if vault.broken:
        raise SwapUnavailable("This chat's protected secrets cannot be decrypted (the server key changed). "
                              "Start a new chat to continue.")
    return Swap(vault, int(thread_id))


def for_user(user_id) -> Swap | None:
    """A one-off swap for a model call outside a chat's transcript (titles,
    recaps): it creates pairs and decides nothing for any transcript."""
    if not user_id:
        return None
    row = db.fetchone("SELECT chat_secret_swap FROM users WHERE id=?", (int(user_id),))
    if not row or not row["chat_secret_swap"]:
        return None
    vault = _vault(int(user_id))
    return None if vault.broken else Swap(vault)


def enable_for_new_thread(conn, account, thread_id, messages) -> None:
    """A chat swaps when the account switch is on at its first turn; it keeps
    that for life, so turning the switch off never runs a fake as a value."""
    keys = account.keys() if hasattr(account, "keys") else ()
    if "chat_secret_swap" not in keys or not account["chat_secret_swap"]:
        return
    if any(isinstance(m, dict) and m.get("role") == "assistant" for m in messages or []):
        return
    conn.execute("UPDATE chat_threads SET secret_swap=1 WHERE id=? AND secret_swap=0", (thread_id,))


def copy_thread(conn, source_id, target_id) -> None:
    """A fork swaps like its source (its transcripts carry the rest)."""
    row = conn.execute("SELECT secret_swap FROM chat_threads WHERE id=?", (source_id,)).fetchone()
    if row and row["secret_swap"]:
        conn.execute("UPDATE chat_threads SET secret_swap=1 WHERE id=?", (target_id,))


def init_schema(conn) -> None:
    db._ensure_column(conn, "users", "chat_secret_swap", "INTEGER NOT NULL DEFAULT 0")
    db._ensure_column(conn, "chat_threads", "secret_swap", "INTEGER NOT NULL DEFAULT 0")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS secret_tokens (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            real_hmac CHAR(64) NOT NULL,
            fake_hmac CHAR(64) NOT NULL,
            kind VARCHAR(32) NOT NULL,
            strong INTEGER NOT NULL DEFAULT 0,
            cipher TEXT NOT NULL,
            created_at VARCHAR(32) NOT NULL,
            UNIQUE(user_id, real_hmac),
            UNIQUE(user_id, fake_hmac)
        );
    """)
