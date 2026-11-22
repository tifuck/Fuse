"""Stand-ins for protected secrets (app/secret_guard.py).

A stand-in is random and has the real value's shape: the same length, every
non-alphanumeric character in the same place, each letter or digit redrawn
from its own class, the public type prefix kept (sk-proj-, ghp_, AKIA) and a
valid format checksum where the format has one. Nothing else of the real
value is kept, so a stand-in tells a provider the kind of secret and no more.
"""
from __future__ import annotations

import math
import re
import secrets as _random
import zlib

_DIGITS = "0123456789"
_LOWER = "abcdefghijklmnopqrstuvwxyz"
_UPPER = _LOWER.upper()
_HEX_LOWER = _DIGITS + "abcdef"
_HEX_UPPER = _DIGITS + "ABCDEF"
_B62 = _DIGITS + _UPPER + _LOWER

# Provider formats, most specific first: (kind, label, pattern). The group p
# is the public prefix a stand-in keeps.
_FORMATS = [
    ("anthropic", "Anthropic key", r"(?P<p>sk-ant-[a-z]{2,8}\d{2}-)[A-Za-z0-9_-]{32,}"),
    ("openrouter", "OpenRouter key", r"(?P<p>sk-or-v1-)[a-f0-9]{64}"),
    ("openai", "OpenAI key", r"(?P<p>sk-(?:proj-|svcacct-|admin-)?)[A-Za-z0-9_-]{20,}"),
    ("github", "GitHub token", r"(?P<p>gh[pousr]_)[A-Za-z0-9]{36}"),
    ("github_pat", "GitHub token", r"(?P<p>github_pat_)[A-Za-z0-9_]{70,}"),
    ("gitlab", "GitLab token", r"(?P<p>glpat-)[A-Za-z0-9_-]{20,}"),
    ("aws", "AWS access key ID", r"(?P<p>AKIA|ASIA)[A-Z0-9]{16}"),
    ("slack", "Slack token", r"(?P<p>xox[abposr]-|xapp-)[A-Za-z0-9-]{10,}"),
    ("stripe", "Stripe key", r"(?P<p>(?:sk|rk)_(?:live|test)_|whsec_)[A-Za-z0-9]{20,}"),
    ("google", "Google API key", r"(?P<p>AIza)[A-Za-z0-9_-]{35}"),
    ("huggingface", "Hugging Face token", r"(?P<p>hf_)[A-Za-z0-9]{30,}"),
    ("npm", "npm token", r"(?P<p>npm_)[A-Za-z0-9]{36}"),
    ("sendgrid", "SendGrid key", r"(?P<p>SG\.)[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}"),
    ("digitalocean", "DigitalOcean token", r"(?P<p>do[opr]_v1_)[a-f0-9]{64}"),
]
_FULL = [(kind, label, re.compile(rx)) for kind, label, rx in _FORMATS]
_INSIDE = [(kind, label, re.compile(r"(?<![A-Za-z0-9_-])" + rx + r"(?![A-Za-z0-9_-])")) for kind, label, rx in _FORMATS]
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{16,}")
_PEM = re.compile(r"-----BEGIN ([A-Z0-9 ]*)PRIVATE KEY-----(.*?)-----END \1PRIVATE KEY-----", re.S)
# Published example values: every model knows them, and they open nothing.
_PUBLIC = frozenset({"AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"})
# A real value with any of these is never placed into command text.
_SHELL = re.compile(r"[$`\\'\"();|&<>{}\r\n]")
_WORD = re.compile(r"(?:[a-z]+|[A-Z]+|[A-Z][a-z]+)?[0-9]{0,4}")
LABELS = {kind: label for kind, label, _rx in _FORMATS} | {
    "jwt": "JWT", "pem": "Private key", "url": "Password in a URL", "named": "Secret", "manual": "Secret"}


def is_public(value: str) -> bool:
    return value in _PUBLIC or "EXAMPLE" in value


def match_format(value: str) -> tuple[str, int] | None:
    """(kind, prefix length) when the whole value is a known provider format."""
    for kind, _label, rx in _FULL:
        m = rx.fullmatch(value)
        if m:
            return kind, len(m.group("p"))
    if _JWT.fullmatch(value):
        return "jwt", value.rfind(".") + 1
    if _PEM.search(value):
        return "pem", 0
    return None


def find_formats(text: str) -> list[tuple[str, str]]:
    """Provider-format values inside a line that is not a key=value pair: (kind, value)."""
    found, taken = [], []
    for kind, _label, rx in _INSIDE:
        for m in rx.finditer(text):
            if any(m.start() < end and start < m.end() for start, end in taken):
                continue
            taken.append((m.start(), m.end()))
            found.append((kind, m.group(0)))
    return found


def shell_safe(value: str) -> bool:
    return not _SHELL.search(value)


def wordy(value: str) -> bool:
    """Made of words: letter runs, each with up to four digits after it."""
    return all(_WORD.fullmatch(piece) for piece in re.split(r"[-_.]+", value) if piece)


def plain(value: str) -> bool:
    """Could be ordinary text (words, or a number). However long it is, such a
    value is swapped only as a whole word, and its owner is warned before
    protecting it; any other value is swapped wherever it appears."""
    return wordy(value) or value.isdigit()


def _alnum(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def _alphabets(body: str):
    """What each letter or digit of the body is redrawn from."""
    chars = {c for c in body if _alnum(c)}
    if chars and chars <= set(_DIGITS):
        return lambda ch: _DIGITS
    if chars <= set(_HEX_LOWER):
        return lambda ch: _HEX_LOWER
    if chars <= set(_HEX_UPPER):
        return lambda ch: _HEX_UPPER
    return lambda ch: _DIGITS if ch in _DIGITS else _LOWER if ch in _LOWER else _UPPER


def _slots(real: str, kind: str, prefix_len: int) -> list[int]:
    """Positions a stand-in redraws."""
    if kind == "pem":
        slots = []
        for m in _PEM.finditer(real):
            slots += [i for i in range(m.start(2), m.end(2)) if _alnum(real[i]) or real[i] in "+/"]
        return slots
    return [i for i in range(prefix_len, len(real)) if _alnum(real[i])]


def strength(real: str, kind: str = "", prefix_len: int = 0) -> tuple[bool, float]:
    """(strong, bits). A strong value is long and random enough to be matched
    anywhere in text; a weak one is matched only as a whole word."""
    slots = _slots(real, kind, prefix_len)
    pick = _alphabets("".join(real[i] for i in slots))
    bits = sum(math.log2(len(pick(real[i]))) for i in slots if _alnum(real[i]))
    return (len(slots) >= 16 and bits >= 64), bits


def _b62(n: int, width: int) -> str:
    out = ""
    while n:
        n, r = divmod(n, 62)
        out = _B62[r] + out
    return out.rjust(width, "0")


def _draw(real: str, kind: str, prefix_len: int) -> str:
    if kind == "github" and len(real) == prefix_len + 36:
        body = "".join(_random.choice(_B62) for _ in range(30))
        return real[:prefix_len] + body + _b62(zlib.crc32(body.encode()), 6)
    out = list(real)
    slots = _slots(real, kind, prefix_len)
    if kind == "pem":
        for i in slots:
            out[i] = _random.choice(_B62 + "+/")
        return "".join(out)
    pick = _alphabets("".join(real[i] for i in slots))
    for i in slots:
        out[i] = _random.choice(pick(real[i]))
    return "".join(out)


def make_fake(real: str, kind: str = "", prefix_len: int = 0, taken=()) -> str:
    """A stand-in for real that shares no run with it and collides with none
    of `taken` (the user's other real values and stand-ins)."""
    slots = _slots(real, kind, prefix_len)
    if not slots:
        raise ValueError("nothing to replace in this value")
    for _ in range(64):
        fake = _draw(real, kind, prefix_len)
        same = sum(1 for i in slots if fake[i] == real[i])
        if fake == real or same * 4 > len(slots):
            continue
        if any(fake in other or other in fake for other in taken if other):
            continue
        return fake
    raise ValueError("no stand-in could be made for this value")
