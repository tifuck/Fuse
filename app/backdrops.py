"""Custom backdrops: per-user HTML documents the chat draws as a wallpaper.

A backdrop is one self-contained HTML page (inline CSS/JS, canvas or WebGL)
that a model writes in an artifact and the user saves with the artifact's
"Use this" button (window.fuse.setBackdrop, app/artifacts.py). The chat draws
it as <iframe src="/backdrop/<id>" sandbox="allow-scripts">. The HTML is
opaque text: the CSP below and the sandbox (opaque origin, no network) are
the safety, not any filtering of the markup. Rows never change once written,
so a URL can be cached. docs/wallpapers.md, "Custom backdrops".
"""
from __future__ import annotations

import re
import secrets
import string
import unicodedata

from . import db
from .security import limiter

HTML_MAX = 131072  # UTF-8 bytes
NAME_MAX = 40
PER_USER = 8
SCHEMES = ("dark", "light")
WRITES_PER_MIN = 20
ID_RE = re.compile(r"[a-z0-9]{12}")
_ALPHABET = string.ascii_lowercase + string.digits

CSP = (
    "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; font-src data:; media-src data: blob:; worker-src 'none'; connect-src 'none'; "
    "frame-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'; webrtc 'block'"
)
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, max-age=300",
    "Referrer-Policy": "no-referrer",
    # The middleware's default is DENY; the chat page frames these.
    "X-Frame-Options": "SAMEORIGIN",
    # CSP does not cover WebRTC (no browser ships its webrtc directive yet) or
    # the frame navigating itself to another same-origin page. An empty
    # allowlist blocks both, and every other connection (Chromium 152+).
    "Connection-Allowlist": "()",
}


class BackdropError(ValueError):
    def __init__(self, detail: str, status_code: int = 422):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _name(raw) -> str:
    if not isinstance(raw, str):
        raise BackdropError("name must be text")
    # Control and format characters (bidi overrides, zero-width) go: a name must
    # read as what it is in the confirm dialog.
    name = "".join(c for c in re.sub(r"\s+", " ", raw) if unicodedata.category(c) not in ("Cc", "Cf")).strip()
    if not name:
        raise BackdropError("name is required")
    if len(name) > NAME_MAX:
        raise BackdropError(f"name is longer than {NAME_MAX} characters")
    return name


def _scheme(raw) -> str:
    if raw is None or raw == "":
        return "dark"
    if raw not in SCHEMES:
        raise BackdropError('scheme must be "dark" or "light"')
    return raw


def _html(raw) -> str:
    if not isinstance(raw, str):
        raise BackdropError("html must be text")
    if "\x00" in raw:
        raise BackdropError("html contains a NUL character")
    try:
        size = len(raw.encode("utf-8"))
    except UnicodeEncodeError:
        raise BackdropError("html is not valid UTF-8 text") from None
    if not raw.strip():
        raise BackdropError("html is empty")
    if size > HTML_MAX:
        raise BackdropError(f"html is {size} bytes; the limit is {HTML_MAX} (128 KB)")
    return raw


def _view(row) -> dict:
    return {"id": row["public_id"], "name": row["name"], "scheme": row["scheme"], "created": row["created_at"]}


def list_for_user(user_id: int) -> list[dict]:
    rows = db.fetchall(
        "SELECT public_id, name, scheme, created_at FROM user_backdrops WHERE user_id=? ORDER BY id DESC", (user_id,))
    return [_view(row) for row in rows]


def create(user_id: int, *, name, scheme, html, replace=None) -> dict:
    """Store a backdrop. A full account (PER_USER) answers 409 unless
    `replace` names one of its backdrops, which goes in the same step."""
    if not limiter.allow(f"backdrop-write:{user_id}", WRITES_PER_MIN, 60):
        raise BackdropError("Too many backdrops saved. Try again shortly.", 429)
    row = {"name": _name(name), "scheme": _scheme(scheme), "created_at": db.now()}
    html = _html(html)
    with db.tx() as conn:
        # The user's row lock serializes concurrent saves so the cap holds.
        conn.execute("SELECT id FROM users WHERE id=?" + db.for_update(conn), (user_id,)).fetchone()
        if isinstance(replace, str) and ID_RE.fullmatch(replace):
            conn.execute("DELETE FROM user_backdrops WHERE public_id=? AND user_id=?", (replace, user_id))
        count = conn.execute("SELECT COUNT(*) AS n FROM user_backdrops WHERE user_id=?", (user_id,)).fetchone()["n"]
        if int(count) >= PER_USER:
            raise BackdropError(f"You have {PER_USER} custom backdrops; choose one to replace.", 409)
        for _ in range(8):
            public_id = "".join(secrets.choice(_ALPHABET) for _ in range(12))
            if not conn.execute("SELECT 1 AS ok FROM user_backdrops WHERE public_id=?", (public_id,)).fetchone():
                break
        else:
            raise BackdropError("could not allocate an identifier", 500)
        conn.execute(
            "INSERT INTO user_backdrops (public_id, user_id, name, scheme, html, created_at) VALUES (?,?,?,?,?,?)",
            (public_id, user_id, row["name"], row["scheme"], html, row["created_at"]),
        )
    return _view({**row, "public_id": public_id})


def delete(user_id: int, public_id: str) -> bool:
    if not ID_RE.fullmatch(public_id or ""):
        return False
    with db.tx() as conn:
        found = conn.execute(
            "SELECT id FROM user_backdrops WHERE public_id=? AND user_id=?", (public_id, user_id)).fetchone()
        if found:
            conn.execute("DELETE FROM user_backdrops WHERE id=?", (found["id"],))
    return bool(found)


def html_for(user_id: int, public_id: str) -> str | None:
    """The owner's stored document, or None (missing or someone else's)."""
    if not ID_RE.fullmatch(public_id or ""):
        return None
    row = db.fetchone("SELECT html FROM user_backdrops WHERE public_id=? AND user_id=?", (public_id, user_id))
    return row["html"] if row else None
