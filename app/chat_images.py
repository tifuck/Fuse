"""Inline images in stored provider transcripts, kept once per thread.

A read_file screenshot is a base64 data URL of a few hundred KB, and every
transcript save, retained history and continuation request repeated all of
them: a session with sixty screenshots rewrote 17 MB rows several times per
tool round, and MariaDB and Fuse each kept that much per connection or worker
thread until the host ran out of memory. Stored JSON now carries
``fuse-image:<sha256>`` in each large image's place. The data URL lives once in
chat_images, and goes back inline only on its way to a provider
(api._invoke_chat) or the run image route, so the bytes a provider sees, and
its prompt cache, are unchanged.

Hashes and comparisons of provider rows use the canonical (reference) form, so
a row matches whichever form it was stored in.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import threading
from collections import OrderedDict

from . import db
from .chat_schema import ChatValidationError

logger = logging.getLogger(__name__)

PREFIX = "fuse-image:"
# A reference saves nothing worth a lookup on a small image.
MIN_CHARS = 8 * 1024
MISSING = "[Image unavailable: Fuse no longer stores it.]"
# Recently rehydrated images, per user: a running turn re-sends the same
# screenshots every round and must not fetch them from MariaDB each time.
CACHE_BYTES = 64 * 1024 * 1024
# Rows per lookup: each is a whole image, so a batch is a few MB at most.
_FETCH_BATCH = 16
# Characters one rehydration may put back inline, newest images first; twice
# api._MAX_IMAGE_CHARS, so shedding there still decides in any real transcript.
# Without it a 2 MB request repeating one stored 6 MB image's reference
# expanded to tens of GB before any size check.
REHYDRATE_CHARS = 96_000_000
# What a thread view sends in place of a stored attachment image's bytes: the
# path of its route (web/chat.py chat_image), then the sha.
ATTACHMENT_PATH = "/chat/images/"
_ATTACHMENT_HEADERS = {"data:image/" + kind + ";base64": "image/" + kind for kind in ("png", "jpeg", "gif", "webp")}

_cache: OrderedDict[tuple[int, str], str] = OrderedDict()
_cache_bytes = 0
_cache_lock = threading.Lock()


def _url(part):
    """The URL an image part carries, or None for any other part."""
    if not isinstance(part, dict) or part.get("type") not in ("image_url", "input_image"):
        return None
    image = part.get("image_url")
    url = image.get("url") if isinstance(image, dict) else image
    return url if isinstance(url, str) else None


def _with_url(part, url):
    image = part.get("image_url")
    if isinstance(image, dict):
        return {**part, "image_url": {**image, "url": url}}
    return {**part, "image_url": url}


def _map(messages, swap):
    """A copy of messages with each image part replaced by swap(part, url).

    swap returns a new part, or None to keep it. Rows without a replaced part
    are shared with the input, never mutated."""
    if not isinstance(messages, list):
        return messages
    out = []
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            out.append(msg)
            continue
        parts = None
        for index, part in enumerate(content):
            url = _url(part)
            new = swap(part, url) if url is not None else None
            if new is None:
                if parts is not None:
                    parts.append(part)
                continue
            if parts is None:
                parts = list(content[:index])
            parts.append(new)
        out.append(msg if parts is None else {**msg, "content": parts})
    return out


def is_ref(url) -> bool:
    return (isinstance(url, str) and len(url) == len(PREFIX) + 64 and url.startswith(PREFIX)
            and all(c in "0123456789abcdef" for c in url[len(PREFIX):]))


def canonical(messages):
    """(messages with each large inline image as a reference, {sha: data URL})."""
    found: dict[str, str] = {}

    def swap(part, url):
        if len(url) < MIN_CHARS or not url.startswith("data:image/") or not url.isascii():
            return None
        sha = hashlib.sha256(url.encode("ascii")).hexdigest()
        found[sha] = url
        return _with_url(part, PREFIX + sha)

    return _map(messages, swap), found


def canonical_row(row):
    """One provider row in canonical form, for hashing and comparison."""
    return canonical([row])[0][0]


def canonical_content(content):
    """A message content value (string or parts) in canonical form."""
    return canonical([{"content": content}])[0][0]["content"]


def save(conn, thread_id, user_id, images) -> None:
    """Store {sha: data URL} for a thread; rows it already has are skipped."""
    if not images or thread_id is None:
        return
    shas = sorted(images)
    have = set()
    for start in range(0, len(shas), 200):
        batch = shas[start:start + 200]
        have.update(row["sha"] for row in conn.execute(
            "SELECT sha FROM chat_images WHERE thread_id=? AND sha IN (" + ",".join("?" * len(batch)) + ")",
            (thread_id, *batch),
        ).fetchall())
    ts = db.now()
    for sha in shas:
        if sha in have:
            continue
        # One statement per image keeps each packet small. IGNORE: a
        # concurrent writer may store the same image first, and a deleted
        # thread's late save is dropped rather than failing the turn.
        conn.execute(
            "INSERT OR IGNORE INTO chat_images(thread_id, sha, user_id, url, created_at) VALUES (?,?,?,?,?)",
            (thread_id, sha, user_id, images[sha], ts),
        )


def store(conn, thread_id, user_id, messages):
    """Canonical messages, with their images saved for the thread."""
    out, images = canonical(messages)
    save(conn, thread_id, user_id, images)
    return out


def store_request(conn, thread_id, user_id, request):
    """A run request with its messages and display seed in canonical form."""
    if not isinstance(request, dict):
        return request
    out = request
    for key in ("messages", "display_seed"):
        value = out.get(key)
        if isinstance(value, list):
            stored = store(conn, thread_id, user_id, value)
            if stored is not value:
                out = {**out, key: stored}
    return out


def _refs(messages) -> list[str]:
    """The sha of every reference in messages, in order."""
    shas = []
    for msg in messages if isinstance(messages, list) else ():
        content = msg.get("content") if isinstance(msg, dict) else None
        for part in content if isinstance(content, list) else ():
            url = _url(part)
            if is_ref(url):
                shas.append(url[len(PREFIX):])
    return shas


def has_refs(messages) -> bool:
    return bool(_refs(messages))


def _cache_put(user_id, sha, url) -> None:
    global _cache_bytes
    key = (user_id, sha)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return
        _cache[key] = url
        _cache_bytes += len(url)
        while _cache_bytes > CACHE_BYTES and _cache:
            _, old = _cache.popitem(last=False)
            _cache_bytes -= len(old)


def lookup(user_id, shas) -> dict[str, str]:
    """{sha: data URL} for the user's stored images; unknown shas are absent."""
    found: dict[str, str] = {}
    missing = []
    with _cache_lock:
        for sha in dict.fromkeys(shas):
            url = _cache.get((user_id, sha))
            if url is None:
                missing.append(sha)
            else:
                _cache.move_to_end((user_id, sha))
                found[sha] = url
    for start in range(0, len(missing), _FETCH_BATCH):
        batch = missing[start:start + _FETCH_BATCH]
        marks = ",".join("?" * len(batch))
        # A forked thread holds copies of its source's images: fetch one each.
        rows = db.fetchall(
            "SELECT i.sha, i.url FROM chat_images i JOIN ("
            "SELECT sha, MIN(thread_id) AS thread_id FROM chat_images "
            f"WHERE user_id=? AND sha IN ({marks}) GROUP BY sha) f "
            "ON f.thread_id=i.thread_id AND f.sha=i.sha",
            (user_id, *batch),
        )
        for row in rows:
            found[row["sha"]] = row["url"]
            _cache_put(user_id, row["sha"], row["url"])
    return found


def rehydrate(user_id, messages):
    """Messages with every reference back inline, as originally sent.

    An image no longer stored becomes a short note, never a reference a
    provider would reject."""
    shas = _refs(messages)
    if not shas:
        return messages
    urls = lookup(user_id, shas)
    lost = [sha for sha in dict.fromkeys(shas) if sha not in urls]
    if lost:
        logger.warning("Chat images missing for user=%s: %s", user_id, ", ".join(sha[:12] for sha in lost))
    keep = [False] * len(shas)
    budget = REHYDRATE_CHARS
    for index in range(len(shas) - 1, -1, -1):
        size = len(urls.get(shas[index]) or "")
        if size and size <= budget:
            keep[index] = True
            budget -= size
    over = sum(1 for i, sha in enumerate(shas) if sha in urls and not keep[i])
    if over:
        logger.warning("Chat images over %s chars for user=%s: left out %s older reference(s)",
                       REHYDRATE_CHARS, user_id, over)
    seen = iter(range(len(shas)))

    def fill(part, url):
        if not is_ref(url):
            return None
        data = urls.get(url[len(PREFIX):]) if keep[next(seen)] else None
        return _with_url(part, data) if data is not None else {"type": "text", "text": MISSING}

    return _map(messages, fill)


def resolve(user_id, url):
    """A data URL for an image part's URL: itself when inline, the stored
    image for a reference, None when that image is gone."""
    if not is_ref(url):
        return url
    return lookup(user_id, [url[len(PREFIX):]]).get(url[len(PREFIX):])


def copy_thread(conn, source_thread_id, thread_id) -> None:
    """A fork's runs and history keep the source's references."""
    conn.execute(
        "INSERT OR IGNORE INTO chat_images(thread_id, sha, user_id, url, created_at) "
        "SELECT ?, sha, user_id, url, created_at FROM chat_images WHERE thread_id=?",
        (thread_id, source_thread_id),
    )


def _attachment_mime(url) -> str | None:
    return _ATTACHMENT_HEADERS.get(url[:40].partition(",")[0]) if isinstance(url, str) else None


def link_attachments(thread_id, messages):
    """Messages for the page: each user attachment image this thread stores
    goes as ATTACHMENT_PATH + sha in ``data``, not as its bytes.

    Every snapshot repeated them, megabytes of base64 per picture. The
    transcript keeps the bytes; restore_attachments puts them back into what
    the page sends. An image the thread has no row for stays inline."""
    if thread_id is None or not isinstance(messages, list):
        return messages
    stored = None
    out = []
    for msg in messages:
        attachments = msg.get("attachments") if isinstance(msg, dict) else None
        linked = None
        for index, att in enumerate(attachments if isinstance(attachments, list) else ()):
            data = att.get("data") if isinstance(att, dict) and att.get("kind") == "image" else None
            if not _attachment_mime(data) or len(data) < MIN_CHARS or not data.isascii():
                continue
            if stored is None:
                stored = {row["sha"] for row in db.fetchall("SELECT sha FROM chat_images WHERE thread_id=?", (thread_id,))}
            if not stored:
                return messages
            sha = hashlib.sha256(data.encode("ascii")).hexdigest()
            if sha in stored:
                linked = linked or list(attachments)
                linked[index] = {**att, "data": ATTACHMENT_PATH + sha}
        out.append(msg if linked is None else {**msg, "attachments": linked})
    return out


def _linked_sha(att) -> str | None:
    data = att.get("data") if isinstance(att, dict) and att.get("kind") == "image" else None
    if isinstance(data, str) and data.startswith(ATTACHMENT_PATH) and is_ref(PREFIX + data[len(ATTACHMENT_PATH):]):
        return data[len(ATTACHMENT_PATH):]
    return None


def restore_attachments(user_id, attachments):
    """Attachments the page sent, with the bytes back in each image
    link_attachments replaced.

    The page echoes what a view gave it (transcript saves, edit and resend,
    undo, queued turns). An image that is gone is refused: the message would
    otherwise be stored, and sent to the model, without it."""
    if not isinstance(attachments, list):
        return attachments
    shas = {index: sha for index, att in enumerate(attachments) if (sha := _linked_sha(att))}
    if not shas:
        return attachments
    urls = lookup(user_id, shas.values())
    out = list(attachments)
    for index, sha in shas.items():
        url = urls.get(sha)
        if url is None or hashlib.sha256(url.encode("utf-8")).hexdigest() != sha:
            raise ChatValidationError("an attached image is no longer stored; reload the chat", 409)
        out[index] = {**attachments[index], "data": url}
    return out


def restore_messages(user_id, messages):
    """restore_attachments for every message of a transcript the page sent."""
    if not isinstance(messages, list):
        return messages
    out = None
    for index, msg in enumerate(messages):
        attachments = msg.get("attachments") if isinstance(msg, dict) else None
        restored = restore_attachments(user_id, attachments)
        if restored is not attachments:
            out = out or list(messages)
            out[index] = {**msg, "attachments": restored}
    return messages if out is None else out


def attachment_image(user_id, sha):
    """(mime, bytes) of a stored image for its owner's page, else None."""
    if not is_ref(PREFIX + sha):
        return None
    url = lookup(user_id, [sha]).get(sha)
    mime = _attachment_mime(url)
    if not mime:
        return None
    try:
        return mime, base64.b64decode(url.partition(",")[2])
    except ValueError:
        return None


# What a stored message keeps of an image or file once its chat's media
# retention ran out (retention.purge_idle_media).
FILE_MISSING = "[File unavailable: Fuse no longer stores it.]"
ATTACHMENT_REMOVED = "[Attachment removed: Fuse deletes images and files from chats left idle.]"
_MEDIA_KINDS = ("image", "file")


def _media_note(part):
    if _url(part) is not None:
        return MISSING
    if isinstance(part, dict) and part.get("type") in ("file", "input_file"):
        return FILE_MISSING
    return None


def _removed(att):
    kept = {key: att[key] for key in ("id", "name") if isinstance(att.get(key), str)}
    return {**kept, "kind": "text", "mime": "text/plain", "data": ATTACHMENT_REMOVED}


def strip_media(messages):
    """Messages with every image and file as a short note.

    Provider parts become text parts; a user row's image and file attachments
    become text attachments, so every parser still accepts the row and the
    model is told what was there. Rows without media are shared, and the
    input itself comes back when nothing changed."""
    if not isinstance(messages, list):
        return messages
    out = None
    for index, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        new = msg
        content = msg.get("content")
        if isinstance(content, list) and any(_media_note(part) for part in content):
            new = {**new, "content": [{"type": "text", "text": note} if (note := _media_note(part)) else part
                                      for part in content]}
        attachments = msg.get("attachments")
        if isinstance(attachments, list) and any(isinstance(att, dict) and att.get("kind") in _MEDIA_KINDS
                                                 for att in attachments):
            new = {**new, "attachments": [_removed(att) if isinstance(att, dict) and att.get("kind") in _MEDIA_KINDS
                                          else att for att in attachments]}
        if new is not msg:
            out = out or list(messages)
            out[index] = new
    return messages if out is None else out


def forget(user_id, shas) -> None:
    """Drop cached copies of images whose rows were deleted."""
    global _cache_bytes
    with _cache_lock:
        for sha in shas:
            url = _cache.pop((user_id, sha), None)
            if url is not None:
                _cache_bytes -= len(url)
