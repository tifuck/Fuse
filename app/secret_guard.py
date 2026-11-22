"""Secret protection: providers get stand-ins for the secrets a user protects.

Only values the user accepted are ever replaced, by exact match: nothing is
detected in free text here. There are three doors and nothing else swaps:

- to a provider (api._run_chat, jev.evaluate): `mask` turns each protected
  value into its stand-in (app/secret_fakes.py);
- to a machine (workspace._execute): `restore_tool_args` turns whole
  stand-ins back in the fields a tool acts on, and refuses a call that holds
  a cut stand-in or would put a value with shell characters into a command;
- to the user (chat_generation.consume): `StreamRestorer` and `restore_text`
  turn stand-ins in a reply back, so the user and the stored chat hold real
  values.

A user's values are one registry for all their chats, projects and machines.
Both directions are pure functions of it: there is no per-chat state.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import quote

from . import db, secret_fakes
from .config import settings
from .crypto import DecryptionError, decrypt, encrypt

logger = logging.getLogger(__name__)

MAX_PER_USER = 200
MIN_LEN = 8
MAX_LEN = 8192
_FRAGMENT = 16     # head or tail run of a strong value still masked when the rest was cut
_CUT = 12          # run of a stand-in that must not reach a machine outside the whole stand-in
_PROBE = 6
_WAIT_S = 1800.0
BROKEN = ("Secret protection can't read its saved values (the server key changed). "
          "Open Settings > Secret protection and reset it to continue.")


# -- storage -------------------------------------------------------------------

def init_schema(conn) -> None:
    db._ensure_column(conn, "users", "secret_protect", "INTEGER NOT NULL DEFAULT 1")
    db._ensure_column(conn, "users", "secret_offer", "INTEGER NOT NULL DEFAULT 1")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_secrets (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            label VARCHAR(190) NOT NULL,
            kind VARCHAR(32) NOT NULL,
            origin VARCHAR(16) NOT NULL,
            status VARCHAR(16) NOT NULL,
            strong INTEGER NOT NULL DEFAULT 0,
            prefix_len INTEGER NOT NULL DEFAULT 0,
            length INTEGER NOT NULL DEFAULT 0,
            real_hmac CHAR(64) NOT NULL,
            fake TEXT NOT NULL,
            cipher TEXT NOT NULL,
            sources TEXT NOT NULL,
            created_at VARCHAR(32) NOT NULL,
            updated_at VARCHAR(32) NOT NULL,
            UNIQUE(user_id, real_hmac)
        );
    """)


def _digest(user_id: int, value: str) -> str:
    key = hashlib.sha256(b"fuse.secret-protect.v1\0" + settings.app_secret.encode()).digest()
    return hmac.new(key, f"{int(user_id)}\0".encode() + value.encode("utf-8", "surrogatepass"), hashlib.sha256).hexdigest()


def _switches(user_id: int) -> tuple[bool, bool]:
    row = db.fetchone("SELECT secret_protect, secret_offer FROM users WHERE id=?", (int(user_id),))
    return bool(row and row["secret_protect"]), bool(row and row["secret_offer"])


def offers_enabled(user_id: int) -> bool:
    return _switches(user_id)[1]


# -- the per-user map ------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    id: int
    label: str
    real: str
    fake: str
    strong: bool
    prefix_len: int
    shell_safe: bool
    masked: bool      # False: restore only (switched off, or retired)
    random: bool = True   # the stand-in is random from prefix_len on (a PEM keeps its header lines)


def _forms(real: str, fake: str, strong: bool) -> list[tuple[str, str]]:
    """(real form, stand-in form) for each spelling a value is met in. The
    stand-in keeps the real value's punctuation, so every form has one shape."""
    out = [(real, fake), (json.dumps(real)[1:-1], json.dumps(fake)[1:-1]),
           (json.dumps(real, ensure_ascii=False)[1:-1], json.dumps(fake, ensure_ascii=False)[1:-1]),
           (quote(real, safe=""), quote(fake, safe=""))]
    if strong:
        raw_real, raw_fake = real.encode(), fake.encode()
        for encode in (base64.b64encode, base64.urlsafe_b64encode):
            a, b = encode(raw_real).decode(), encode(raw_fake).decode()
            out += [(a, b), (a.rstrip("="), b.rstrip("="))]
        out += [(raw_real.hex(), raw_fake.hex()), (raw_real.hex().upper(), raw_fake.hex().upper())]
    seen, forms = set(), []
    for a, b in out:
        if a not in seen and len(a) == len(b) and a != b:
            seen.add(a)
            forms.append((a, b))
    return forms


class SecretMap:
    def __init__(self, items: list[Item], broken: bool = False):
        self.items = items
        self.broken = broken
        self.token = object()   # identity of this version, for caches
        masks, restores = [], []
        for it in items:
            for a, b in _forms(it.real, it.fake, it.strong):
                if it.masked:
                    masks.append((a, b, it))
                restores.append((b, a, it, a == it.real))
        self.masks = sorted(masks, key=lambda p: -len(p[0]))
        self.restores = sorted(restores, key=lambda p: -len(p[0]))
        self.fakes = sorted({it.fake for it in items}, key=len, reverse=True)

    def __bool__(self) -> bool:
        return bool(self.items)


_EMPTY = SecretMap([])
_maps: dict[int, SecretMap] = {}
_maps_lock = threading.Lock()


def _changed(user_id: int) -> None:
    with _maps_lock:
        _maps.pop(int(user_id), None)


def map_for(user_id) -> SecretMap:
    """The user's map (cached until their registry changes). Blocking on a miss."""
    if not user_id:
        return _EMPTY
    user_id = int(user_id)
    with _maps_lock:
        hit = _maps.get(user_id)
    if hit is not None:
        return hit
    on = _switches(user_id)[0]
    rows = db.fetchall(
        "SELECT id, label, kind, status, strong, prefix_len, fake, cipher FROM user_secrets "
        "WHERE user_id=? AND status IN ('protected','retired') ORDER BY id", (user_id,))
    items, broken = [], False
    for row in rows:
        try:
            real = decrypt(row["cipher"])
        except (DecryptionError, ValueError, TypeError):
            broken = True
            continue
        items.append(Item(int(row["id"]), row["label"] or "", real, row["fake"], bool(row["strong"]),
                          int(row["prefix_len"] or 0), secret_fakes.shell_safe(real),
                          on and row["status"] == "protected", row["kind"] != "pem"))
    if broken:
        logger.error("Secret protection: saved values of user %s cannot be decrypted", user_id)
    built = SecretMap(items, broken) if rows else _EMPTY
    with _maps_lock:
        _maps[user_id] = built
    return built


# -- real -> stand-in (provider door) --------------------------------------------

_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()
_CACHE_MAX = 4096
_WORD_EDGE = "(?<![A-Za-z0-9])%s(?![A-Za-z0-9])"


def _swap(text: str, old: str, new: str, anywhere: bool) -> str:
    if anywhere:
        return text.replace(old, new)
    return re.sub(_WORD_EDGE % re.escape(old), lambda _m: new, text)


def _mask_cut(text: str, it: Item) -> str:
    """A strong value cut by a clip: its head or tail run is still masked.
    Only for provider formats, whose text after the prefix is random: a
    word-like start of any other value could be ordinary text."""
    real, fake = it.real, it.fake
    head = it.prefix_len + _FRAGMENT
    if not it.prefix_len or not it.random or len(real) < head + 8:
        return text
    for anchor, forward in ((real[:head], True), (real[-_FRAGMENT:], False)):
        at = text.find(anchor)
        while at >= 0:
            if forward:
                n = len(anchor)
                while n < len(real) and at + n < len(text) and text[at + n] == real[n]:
                    n += 1
                text = text[:at] + fake[:n] + text[at + n:]
                at = text.find(anchor, at + n)
            else:
                end, n = at + len(anchor), len(anchor)
                while n < len(real) and end - n - 1 >= 0 and text[end - n - 1] == real[-n - 1]:
                    n += 1
                text = text[:end - n] + fake[len(fake) - n:] + text[end:]
                at = text.find(anchor, end)
    return text


def _mask(m: SecretMap, text: str) -> str:
    for old, new, it in m.masks:
        if old in text:
            text = _swap(text, old, new, it.strong)
    for it in m.items:
        if it.masked and it.strong:
            text = _mask_cut(text, it)
    return text


def mask_text(m: SecretMap, text):
    if not m.masks or not isinstance(text, str) or len(text) < MIN_LEN:
        return text
    if len(text) < 512:
        return _mask(m, text)
    key = (m.token, hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=16).digest())
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            hit = _cache[key]
            return text if hit is None else hit
    out = _mask(m, text)
    with _cache_lock:
        _cache[key] = None if out == text else out
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return out


# Never rewritten: identifiers, and anything a provider signed or encrypted
# (it must come back byte for byte) or that is not text.
_OPAQUE = frozenset({"id", "tool_call_id", "call_id", "role", "type", "name", "model", "signature",
                     "encrypted_content", "reasoning_content", "reasoning", "reasoning_details",
                     "claude_thinking", "thinking", "devin_signature", "image_url", "input_audio", "file"})


def mask_value(m: SecretMap, value):
    """value with every string leaf masked; shares what did not change."""
    if not m.masks:
        return value
    if isinstance(value, str):
        return mask_text(m, value)
    if isinstance(value, list):
        out = [mask_value(m, v) for v in value]
        return out if any(a is not b for a, b in zip(out, value)) else value
    if isinstance(value, dict):
        out, same = {}, True
        for k, v in value.items():
            new = v if k in _OPAQUE else mask_value(m, v)
            same = same and new is v
            out[k] = new
        return value if same else out
    return value


async def outbound(user_id, body: dict) -> dict:
    """A request body as its provider may read it."""
    m = await asyncio.to_thread(map_for, user_id)
    if not m.masks or not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        return body
    messages = await asyncio.to_thread(mask_value, m, body["messages"])
    return body if messages is body["messages"] else {**body, "messages": messages}


# -- stand-in -> real ---------------------------------------------------------------

def restore_text(m: SecretMap, text):
    """Whole stand-ins in model-written text, back to the real values."""
    if not m.restores or not isinstance(text, str) or len(text) < MIN_LEN:
        return text
    for old, new, it, plain in m.restores:
        if plain and old in text:
            text = _swap(text, old, new, it.strong)
    return text


def _cut_stand_in(text: str, it: Item) -> bool:
    """Does text hold a run of this stand-in's random part, outside a whole one?"""
    part = it.fake[it.prefix_len:]
    if not it.random or len(part) < _CUT:
        return False
    for off in range(0, len(part) - _PROBE + 1, _PROBE):
        probe = part[off:off + _PROBE]
        at = text.find(probe)
        while at >= 0:
            left = 0
            while left < off and at - left - 1 >= 0 and text[at - left - 1] == part[off - left - 1]:
                left += 1
            right = _PROBE
            while off + right < len(part) and at + right < len(text) and text[at + right] == part[off + right]:
                right += 1
            if left + right >= _CUT:
                return True
            at = text.find(probe, at + 1)
    return False


def _regex_literal(text: str) -> str:
    return re.sub(r"[.^$*+?()\[\]{}|\\]", lambda m: "\\" + m.group(0), text)


def restore_field(m: SecretMap, text, kind: str) -> tuple[str, str]:
    """(text for the machine, refusal). kind: "command", "content" or "regex".
    Everything that is not a whole stand-in stays byte for byte."""
    if not m.restores or not isinstance(text, str) or len(text) < MIN_LEN:
        return text, ""
    for old, new, it, _plain in m.restores:
        if old not in text:
            continue
        if kind == "command" and not it.shell_safe:
            if _swap(text, old, new, it.strong) == text:
                continue
            name = it.label or "a protected value"
            return text, (f"[Fuse] Not run: {name} contains shell characters, so Fuse does not place it in a "
                          "command. Read it from its file or environment variable instead.")
        text = _swap(text, old, _regex_literal(new) if kind == "regex" else new, it.strong)
    for it in m.items:
        if it.strong and _cut_stand_in(text, it):
            name = it.label or "a protected value"
            return text, (f"[Fuse] Not run: the value of {name} is incomplete in this call. "
                          "Use it whole, exactly as it appears.")
    return text, ""


_TOOL_FIELDS = {
    "run_terminal_command": (("command", "command"),),
    "monitor": (("command", "command"),),
    "write_file": (("content", "content"),),
    "search_replace": (("old_string", "content"), ("new_string", "content")),
    "grep": (("pattern", "regex"),),
}


def restore_tool_args(user_id, name: str, args: dict) -> str:
    """Restore the fields of a machine tool call in place. Returns a refusal
    text, or "" when the call may go on. Paths and every other field are never touched."""
    fields = _TOOL_FIELDS.get(name)
    if not fields or not isinstance(args, dict):
        return ""
    m = map_for(user_id)
    if not m.restores:
        return ""
    targets = [(args, key, kind) for key, kind in fields]
    if name == "search_replace" and isinstance(args.get("edits"), list):
        targets += [(edit, key, "content") for edit in args["edits"] if isinstance(edit, dict)
                    for key in ("old_string", "new_string")]
    done = []
    for holder, key, kind in targets:
        if not isinstance(holder.get(key), str):
            continue
        text, refusal = restore_field(m, holder[key], kind)
        if refusal:
            return refusal
        done.append((holder, key, text))
    for holder, key, text in done:
        holder[key] = text
    return ""


class StreamRestorer:
    """Restores a reply as it streams. The tail that could still grow into a
    stand-in is held until the next piece (or flush) settles it, so the pieces
    joined are exactly restore_text of the whole reply."""

    def __init__(self, m: SecretMap):
        self.m = m
        self.buf = ""
        self.prev = ""

    def _hold(self, buf: str) -> int:
        hold = 0
        for fake in self.m.fakes:
            if len(fake) <= hold:
                break
            start = max(0, len(buf) - len(fake))
            at = buf.find(fake[0], start)
            while at >= 0:
                tail = buf[at:]
                # A proper prefix may still grow; a whole one needs the next
                # character to know where the word ends.
                if fake.startswith(tail):
                    hold = max(hold, len(tail))
                    break
                at = buf.find(fake[0], at + 1)
        return hold

    def _emit(self, text: str, after: str) -> str:
        if not text:
            return ""
        out = restore_text(self.m, self.prev + text + after)
        out = out[len(self.prev):len(out) - len(after)] if after else out[len(self.prev):]
        self.prev = text[-1:]
        return out

    def push(self, text: str) -> str:
        if not self.m.restores or not text:
            return text
        buf = self.buf + text
        hold = self._hold(buf)
        emit, self.buf = buf[:len(buf) - hold], buf[len(buf) - hold:]
        return self._emit(emit, self.buf[:1])

    def flush(self) -> str:
        emit, self.buf = self.buf, ""
        return self._emit(emit, "")


# -- candidates and decisions ---------------------------------------------------------

def _source(host_id, alias: str, found) -> dict:
    return {"host_id": int(host_id or 0), "host": str(alias or ""), "path": found.path, "line": int(found.line),
            "key": found.key}


def _taken(conn, user_id: int) -> list[str]:
    out = []
    for row in conn.execute("SELECT fake, cipher FROM user_secrets WHERE user_id=? AND cipher<>''", (user_id,)).fetchall():
        out.append(row["fake"])
        try:
            out.append(decrypt(row["cipher"]))
        except (DecryptionError, ValueError, TypeError):
            pass
    return out


def _insert(conn, user_id: int, value: str, taken: list, *, label: str, kind: str, prefix_len: int, origin: str,
            status: str, sources: list) -> int | None:
    if any(value in other or other in value for other in taken):
        return None  # would overlap a value or stand-in already kept
    fake = secret_fakes.make_fake(value, kind, prefix_len, taken)
    taken += [value, fake]
    strong, _bits = secret_fakes.strength(value, kind, prefix_len)
    now = db.now()
    conn.execute(
        "INSERT INTO user_secrets (user_id, label, kind, origin, status, strong, prefix_len, length, real_hmac, fake, "
        "cipher, sources, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, label[:190], kind, origin, status, int(strong), int(prefix_len), len(value), _digest(user_id, value),
         fake, encrypt(value), json.dumps(sources[:10]), now, now))
    row = conn.execute("SELECT id FROM user_secrets WHERE user_id=? AND real_hmac=?", (user_id, _digest(user_id, value))).fetchone()
    return int(row["id"]) if row else None


def _place(src: dict) -> tuple:
    return int(src.get("host_id") or 0), src.get("path") or "", src.get("key") or ""


def _decided(conn, user_id: int) -> dict:
    """Where the owner already decided: (host, file, name) -> the latest status."""
    out = {}
    for row in conn.execute("SELECT status, sources FROM user_secrets WHERE user_id=? AND status<>'offered' "
                            "ORDER BY updated_at, id", (user_id,)).fetchall():
        for src in json.loads(row["sources"] or "[]"):
            if src.get("key"):
                out[_place(src)] = row["status"]
    return out


# What Fuse did unasked since the user's last turn, to say so in the chat.
_notes: dict[int, list[str]] = {}


def take_notes(user_id: int) -> list[str]:
    return _notes.pop(int(user_id), [])


def offer(user_id: int, host_id: int, alias: str, found: list) -> int:
    """Keep what a scan found. The owner is asked once per secret: a value
    already decided (or already offered) only gains this place as a source,
    and a new value under a name the owner decided on in this file follows
    that decision unasked (a rotated key stays protected, a declined one
    stays declined). Only a value never seen, in a place never decided, is
    offered. Returns how many were offered."""
    user_id, new, renewed = int(user_id), 0, []
    if not found:
        return 0
    with db.tx() as conn:
        count = conn.execute("SELECT COUNT(*) FROM user_secrets WHERE user_id=?", (user_id,)).fetchone()[0]
        taken = places = None
        for one in found:
            if not (MIN_LEN <= len(one.value) <= MAX_LEN):
                continue
            digest = _digest(user_id, one.value)
            row = conn.execute("SELECT id, status, sources FROM user_secrets WHERE user_id=? AND real_hmac=?",
                               (user_id, digest)).fetchone()
            src = _source(host_id, alias, one)
            if row is not None:
                if row["status"] != "ignored":
                    sources = json.loads(row["sources"] or "[]")
                    if src not in sources:
                        # The same place again (its line moved) replaces the old entry.
                        rest = [s for s in sources if not (src["key"] and _place(s) == _place(src))]
                        conn.execute("UPDATE user_secrets SET sources=? WHERE id=?",
                                     (json.dumps(([src] + rest)[:10]), row["id"]))
                continue
            if count >= MAX_PER_USER:
                break
            if places is None:
                places = _decided(conn, user_id)
            before = places.get(_place(src)) if one.key else None
            if before in ("ignored", "retired"):
                # Declined here before: its new value is declined too, and not kept.
                now = db.now()
                conn.execute(
                    "INSERT INTO user_secrets (user_id, label, kind, origin, status, strong, prefix_len, length, real_hmac, "
                    "fake, cipher, sources, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (user_id, one.key[:190], one.kind, "scan", "ignored", 0, 0, len(one.value), digest, "", "",
                     json.dumps([src]), now, now))
                count += 1
                continue
            if taken is None:
                taken = _taken(conn, user_id)
            status = "protected" if before == "protected" else "offered"
            try:
                made = _insert(conn, user_id, one.value, taken, label=one.key, kind=one.kind, prefix_len=one.prefix_len,
                               origin="scan", status=status, sources=[src])
            except ValueError:
                made = None
            if made:
                count += 1
                if status == "protected":
                    renewed.append(f"{one.key} changed in {one.path}: Fuse protects its new value too.")
                else:
                    new += 1
    if renewed:
        _changed(user_id)
        _notes[user_id] = (_notes.get(user_id, []) + renewed)[-20:]
    return new


def offers(user_id: int) -> list[dict]:
    """The undecided candidates, with their values: for the owner's eyes only."""
    out = []
    for row in db.fetchall("SELECT * FROM user_secrets WHERE user_id=? AND status='offered' ORDER BY id", (int(user_id),)):
        try:
            value = decrypt(row["cipher"])
        except (DecryptionError, ValueError, TypeError):
            continue
        src = (json.loads(row["sources"] or "[]") or [{}])[0]
        out.append({"id": int(row["id"]), "key": row["label"] or "", "value": value,
                    "kind": secret_fakes.LABELS.get(row["kind"], "Secret"), "host": src.get("host", ""),
                    "file": src.get("path", ""), "line": int(src.get("line") or 0)})
    return out


def noted(user_id: int) -> bool:
    """Does this user have values a model will (or may soon) see as stand-ins?"""
    return bool(map_for(user_id).masks) or has_offers(user_id)


def has_offers(user_id: int) -> bool:
    """Is there something for the owner to decide? Not while detection is
    switched off: a turn never waits on offers the user asked not to get."""
    return bool(db.fetchone(
        "SELECT 1 FROM user_secrets s JOIN users u ON u.id=s.user_id "
        "WHERE s.user_id=? AND s.status='offered' AND u.secret_offer=1 LIMIT 1", (int(user_id),)))


def decide(user_id: int, protect, *, shown=None, never: bool = False) -> int:
    """Settle the open offers the owner was shown (all of them when `shown`
    is None): the ids in `protect` are protected for good, in every chat; the
    rest are declined and their values dropped (the keyed fingerprint stays,
    so they are not offered again). An offer that arrived after the window
    was drawn stays open, so nothing is declined unseen."""
    user_id = int(user_id)
    keep = {int(i) for i in protect or []}
    seen = None if shown is None else {int(i) for i in shown}
    now, done = db.now(), 0
    with db.tx() as conn:
        for row in conn.execute("SELECT id FROM user_secrets WHERE user_id=? AND status='offered'", (user_id,)).fetchall():
            if seen is not None and int(row["id"]) not in seen:
                continue
            if int(row["id"]) in keep:
                conn.execute("UPDATE user_secrets SET status='protected', updated_at=? WHERE id=?", (now, row["id"]))
                done += 1
            else:
                conn.execute("UPDATE user_secrets SET status='ignored', cipher='', fake='', updated_at=? WHERE id=?",
                             (now, row["id"]))
        if never:
            conn.execute("UPDATE users SET secret_offer=0 WHERE id=?", (user_id,))
    _changed(user_id)
    _wake(user_id)
    return done


# The turn that waits on an offer: thread id -> (user id, event, its loop).
_waits: dict[int, tuple] = {}


def _wake(user_id: int) -> None:
    for uid, event, loop in list(_waits.values()):
        if uid == int(user_id):
            loop.call_soon_threadsafe(event.set)  # decisions arrive on worker threads


def waiting(user_id: int, thread_id: int) -> bool:
    hit = _waits.get(int(thread_id or 0))
    return bool(hit and hit[0] == int(user_id))


async def wait_for_decision(user_id: int, thread_id: int, timeout: float = _WAIT_S) -> str:
    """Hold a turn until the user settles the open offers: "decided" or "timeout"."""
    event = asyncio.Event()
    key = int(thread_id or 0)
    _waits[key] = (int(user_id), event, asyncio.get_running_loop())
    try:
        deadline = asyncio.get_running_loop().time() + timeout
        while await asyncio.to_thread(has_offers, user_id):
            left = deadline - asyncio.get_running_loop().time()
            if left <= 0:
                return "timeout"
            event.clear()
            try:
                # Re-read now and then: a decision from another worker or a
                # deleted account must not leave the turn waiting.
                await asyncio.wait_for(event.wait(), min(left, 15.0))
            except asyncio.TimeoutError:
                pass
        return "decided"
    finally:
        if _waits.get(key, (None, None, None))[1] is event:
            _waits.pop(key, None)


# -- settings -------------------------------------------------------------------------

def overview(user_id: int) -> dict:
    protect, offer_on = _switches(user_id)
    items = []
    for row in db.fetchall("SELECT * FROM user_secrets WHERE user_id=? ORDER BY status='ignored', id DESC", (int(user_id),)):
        src = (json.loads(row["sources"] or "[]") or [{}])[0]
        where = f"{src.get('host', '')}:{src.get('path', '')}".strip(":") if src else ""
        items.append({"id": int(row["id"]), "label": row["label"] or "", "kind": secret_fakes.LABELS.get(row["kind"], "Secret"),
                      "status": row["status"], "length": int(row["length"] or 0), "strong": bool(row["strong"]),
                      "origin": row["origin"], "where": where if row["origin"] == "scan" else "Added by you",
                      "line": int(src.get("line") or 0) if src else 0})
    return {"protect": protect, "offer": offer_on, "items": items, "broken": map_for(user_id).broken}


def set_switches(user_id: int, *, protect=None, offer_on=None) -> None:
    with db.tx() as conn:
        if protect is not None:
            conn.execute("UPDATE users SET secret_protect=? WHERE id=?", (1 if protect else 0, int(user_id)))
        if offer_on is not None:
            conn.execute("UPDATE users SET secret_offer=? WHERE id=?", (1 if offer_on else 0, int(user_id)))
    _changed(user_id)


def add_manual(user_id: int, label: str, value: str) -> int:
    """Protect a value the user typed. ValueError says why it cannot be kept."""
    user_id, label = int(user_id), (label or "").strip()
    if not isinstance(value, str) or not (MIN_LEN <= len(value) <= MAX_LEN):
        raise ValueError(f"A protected value is {MIN_LEN} to {MAX_LEN} characters long.")
    if sum(c.isascii() and c.isalnum() for c in value) < 6 or len(set(value)) < 4:
        raise ValueError("This value is too simple to protect: it would replace ordinary text.")
    fmt = secret_fakes.match_format(value)
    with db.tx() as conn:
        row = conn.execute("SELECT id FROM user_secrets WHERE user_id=? AND real_hmac=?", (user_id, _digest(user_id, value))).fetchone()
        if row is not None:
            conn.execute("DELETE FROM user_secrets WHERE id=?", (row["id"],))
        if conn.execute("SELECT COUNT(*) FROM user_secrets WHERE user_id=?", (user_id,)).fetchone()[0] >= MAX_PER_USER:
            raise ValueError(f"You can keep up to {MAX_PER_USER} values.")
        made = _insert(conn, user_id, value, _taken(conn, user_id), label=label, kind=fmt[0] if fmt else "manual",
                       prefix_len=fmt[1] if fmt else 0, origin="manual", status="protected", sources=[])
        if not made:
            raise ValueError("This value overlaps one you already keep.")
    _changed(user_id)
    return made


def set_status(user_id: int, secret_id: int, protected: bool) -> bool:
    """Protect a kept value again, or stop protecting it. A value that stops
    being protected keeps its stand-in, so chats that hold it still work."""
    with db.tx() as conn:
        cur = conn.execute(
            "UPDATE user_secrets SET status=?, updated_at=? WHERE id=? AND user_id=? AND status IN ('protected','retired','offered')",
            ("protected" if protected else "retired", db.now(), int(secret_id), int(user_id)))
    _changed(user_id)
    _wake(user_id)
    return bool(cur.rowcount)


def delete(user_id: int, secret_id: int | None = None) -> int:
    """Forget one value, or all of them (also the reset after a key change)."""
    with db.tx() as conn:
        if secret_id is None:
            cur = conn.execute("DELETE FROM user_secrets WHERE user_id=?", (int(user_id),))
        else:
            cur = conn.execute("DELETE FROM user_secrets WHERE id=? AND user_id=?", (int(secret_id), int(user_id)))
    _changed(user_id)
    _wake(user_id)
    return int(cur.rowcount or 0)
