"""Persistent notices: a message for one user that stays on screen, on every
device, until it is dismissed on any one of them.

Any part of the server calls `post` (or `dismiss_key`); the chat page shows
open notices as toasts that do not time out (static/chat/notices.js). A notice
with a thread shows only while that chat is open; one without shows
everywhere on /chat. /chat/sync lists the open ones, and notice.updated /
notice.dismissed events keep every open page in step, so a device that was
closed or offline shows them when it comes back.

A key names one notice per user ("usage-limit:<thread id>"): posting it again
updates it in place. `item` names one thing the notice announces (a secret's
id, a run id): the notice counts its items and opens again, after a dismiss,
only for an item it has not announced yet.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from . import chat_tx, db

logger = logging.getLogger(__name__)

KINDS = ("idle", "ok", "warn", "err")
KEY_MAX = 191
TITLE_MAX = 200
BODY_MAX = 2000
ITEMS_MAX = 200

_later = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fuse-notices")

_COLS = """n.id, n.notice_key, n.kind, n.title, n.body, n.data, n.created_at, n.updated_at,
           n.dismissed_at, t.public_id AS thread_public_id"""


def init_schema(conn) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_notices (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            thread_id INTEGER REFERENCES chat_threads(id) ON DELETE CASCADE,
            notice_key VARCHAR(191) NOT NULL,
            kind VARCHAR(8) NOT NULL DEFAULT 'idle',
            title VARCHAR(200) NOT NULL DEFAULT '',
            body TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at VARCHAR(32) NOT NULL,
            updated_at VARCHAR(32) NOT NULL,
            dismissed_at VARCHAR(32),
            UNIQUE(user_id, notice_key)
        );
    """)


def _view(row) -> dict:
    data = json.loads(row["data"] or "{}")
    return {
        "id": int(row["id"]),
        "key": row["notice_key"],
        "kind": row["kind"],
        "title": row["title"],
        "body": row["body"],
        "count": len(data.get("items") or []),
        "thread_id": row["thread_public_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row(conn, user_id: int, key: str):
    return conn.execute(
        f"""SELECT {_COLS} FROM user_notices n LEFT JOIN chat_threads t ON t.id=n.thread_id
            WHERE n.user_id=? AND n.notice_key=?""",
        (user_id, key),
    ).fetchone()


def post(user_id, key: str, *, title: str = "", body="", kind: str = "idle", thread_id=None, item=None) -> dict | None:
    """Show (or update) notice `key` for the user on every device. thread_id is
    chat_threads.id. body may be a function of the item count. Returns the
    notice, or None when `item` was announced already (nothing changes)."""
    if not user_id or not isinstance(key, str) or not key or len(key) > KEY_MAX:
        raise ValueError("invalid notice key")
    if kind not in KINDS:
        raise ValueError("invalid notice kind")
    user_id = int(user_id)
    thread_id = int(thread_id) if thread_id else None
    with chat_tx.transaction() as conn:
        old = conn.execute(
            "SELECT data FROM user_notices WHERE user_id=? AND notice_key=?" + db.for_update(conn),
            (user_id, key),
        ).fetchone()
        items = list(json.loads(old["data"] or "{}").get("items") or []) if old else []
        if item is not None:
            item = str(item)
            if item in items:
                return None
            items = (items + [item])[-ITEMS_MAX:]
        text = body(len(items)) if callable(body) else body
        now = db.now()
        conn.execute(
            """INSERT INTO user_notices(user_id,thread_id,notice_key,kind,title,body,data,created_at,updated_at,dismissed_at)
               VALUES(?,?,?,?,?,?,?,?,?,NULL)
               ON CONFLICT(user_id,notice_key) DO UPDATE SET thread_id=excluded.thread_id, kind=excluded.kind,
                 title=excluded.title, body=excluded.body, data=excluded.data, updated_at=excluded.updated_at,
                 dismissed_at=NULL""",
            (user_id, thread_id, key, kind, str(title or "")[:TITLE_MAX], str(text or "")[:BODY_MAX],
             json.dumps({"items": items}), now, now),
        )
        view = _view(_row(conn, user_id, key))
        chat_tx.event(conn, user_id, "notice.updated", thread_id, view)
    return view


def try_post(user_id, key: str, **kw) -> dict | None:
    """post() for callers that must never fail because of a notice."""
    try:
        return post(user_id, key, **kw)
    except Exception:
        logger.exception("Notice %s for user %s was not posted", key, user_id)
        return None


def post_later(user_id, key: str, **kw) -> None:
    """try_post() on a worker thread, in order: for callers that may hold a
    transaction of their own (the notice's event must not wait on it)."""
    _later.submit(try_post, user_id, key, **kw)


def flush(timeout: float = 10.0) -> None:
    """Wait for every post_later queued so far."""
    _later.submit(lambda: None).result(timeout=timeout)


def _dismiss(conn, user_id: int, where: str, arg) -> bool:
    row = conn.execute(
        f"SELECT id, notice_key, thread_id FROM user_notices WHERE user_id=? AND {where} AND dismissed_at IS NULL",
        (user_id, arg),
    ).fetchone()
    if not row:
        return False
    conn.execute("UPDATE user_notices SET dismissed_at=? WHERE id=?", (db.now(), row["id"]))
    chat_tx.event(conn, user_id, "notice.dismissed", row["thread_id"], {"id": int(row["id"]), "key": row["notice_key"]})
    return True


def dismiss(user_id, notice_id) -> bool:
    """Dismiss on every device. False when it is not this user's open notice."""
    with chat_tx.transaction() as conn:
        return _dismiss(conn, int(user_id), "id=?", int(notice_id))


def dismiss_key(user_id, key: str) -> bool:
    """Dismiss by key: the condition it reported is over."""
    with chat_tx.transaction() as conn:
        return _dismiss(conn, int(user_id), "notice_key=?", key)


def list_open(user_id, conn=None) -> list[dict]:
    sql = f"""SELECT {_COLS} FROM user_notices n LEFT JOIN chat_threads t ON t.id=n.thread_id
              WHERE n.user_id=? AND n.dismissed_at IS NULL ORDER BY n.updated_at, n.id"""
    rows = conn.execute(sql, (int(user_id),)).fetchall() if conn is not None else db.fetchall(sql, (int(user_id),))
    return [_view(row) for row in rows]
