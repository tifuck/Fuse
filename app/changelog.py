"""What's new: after a deploy, each user gets one notice listing what changed
since they last chatted.

The version is this instance's git checkout, so every install tracks its own
updates. At startup record_deploy stores when HEAD first ran here (schema_meta
"deployed:<sha>"). A user's last interaction is their newest chat turn they
sent themselves (chat_runs; nothing extra is written for it). The first time
they open /chat after a later deploy, announce posts one notice with the
commits between the version live at that turn and HEAD. A release commit
("Release dev <sha>") lists its dev commits in its body; those are shown.

check_upstream runs every few hours: a read-only git ls-remote of the branch
HEAD tracks. A commit this checkout lacks is logged and shown to admins.
Docker mounts the host checkout's .git read-only (docker-compose.yml).
Without git history all of this does nothing.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess

from . import db, notices
from .config import ROOT

logger = logging.getLogger(__name__)

KEY = "whats-new"
UPDATE_KEY = "update-available"
CHECK_INTERVAL = 6 * 3600
MAX_LINES = 12
_PREFIX = "deployed:"
_ONELINE = re.compile(r"^[0-9a-f]{7,40} (.+)$")

_head = ""
_announced: set[int] = set()


def _git(*args, timeout=10) -> str | None:
    """stdout, or None when git is missing or the command failed."""
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=timeout,
                             env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def record_deploy() -> str:
    """At startup: remember when this instance first ran HEAD."""
    global _head
    sha = (_git("rev-parse", "HEAD") or "").strip()
    if sha and not db.fetchone("SELECT 1 FROM schema_meta WHERE key=?", (_PREFIX + sha,)):
        db.execute("INSERT INTO schema_meta(key,value) VALUES(?,?)", (_PREFIX + sha, db.now()))
    _head = sha
    _announced.clear()
    return sha


def changes(base: str, head: str) -> list[str]:
    """Commit subjects in base..head, newest first, without merges. A release
    has its dev commit as a second parent, so a release's commits show up both
    in its body and as themselves: each subject is listed once."""
    lines = []
    for commit in (_git("log", "--format=%s%x1f%b%x1e", f"{base}..{head}") or "").split("\x1e"):
        subject, _, body = commit.strip().partition("\x1f")
        if subject.startswith("Release dev "):
            found = [m.group(1) for m in map(_ONELINE.match, body.splitlines()) if m]
        else:
            found = [subject]
        lines += [s.strip() for s in found if s.strip() and not s.startswith("Merge ")]
    return list(dict.fromkeys(lines))


def _body(lines: list[str]) -> str:
    more = len(lines) - MAX_LINES
    return "\n".join(f"• {s}" for s in lines[:MAX_LINES]) + (f"\n…and {more} more" if more > 0 else "")


def announce(user_id) -> None:
    """Post the what's-new notice for this deploy if the user has not chatted
    since it. Checks each user once per process: later calls cost nothing."""
    uid = int(user_id)
    if not _head or uid in _announced:
        return
    _announced.add(uid)
    deploys = {row["key"][len(_PREFIX):]: row["value"]
               for row in db.fetchall("SELECT key, value FROM schema_meta WHERE key LIKE ?", (_PREFIX + "%",))}
    row = db.fetchone(
        """SELECT MAX(created_at) AS at FROM chat_runs
           WHERE user_id=? AND parent_run_id IS NULL AND COALESCE(JSON_EXTRACT(request, '$.agent.auto') = 1, 0) = 0""",
        (uid,),
    )
    last, since = (row["at"] if row else None), deploys.get(_head)
    if not last or not since or last >= since:
        return
    base = max(((at, sha) for sha, at in deploys.items() if at <= last), default=None)
    lines = changes(base[1], _head) if base else []
    if lines:
        notices.try_post(uid, KEY, title="Fuse was updated", body=_body(lines), kind="ok", item=_head)


def check_upstream() -> str | None:
    """Log and tell admins when the branch this checkout tracks has moved on.
    Returns the newer commit, or None when up to date or unknown."""
    upstream = (_git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") or "").strip()
    remote, _, branch = upstream.partition("/")
    if not _head or not branch:
        return None
    found = (_git("ls-remote", remote, f"refs/heads/{branch}", timeout=30) or "").split()
    if not found:
        return None
    sha = found[0]
    admins = [row["id"] for row in db.fetchall("SELECT id FROM users WHERE is_admin=1")]
    if sha == _head or _git("merge-base", "--is-ancestor", sha, _head) is not None:
        for uid in admins:
            notices.dismiss_key(uid, UPDATE_KEY)
        return None
    logger.warning("Fuse update available: %s is at %s, this instance runs %s", upstream, sha[:12], _head[:12])
    for uid in admins:
        notices.try_post(uid, UPDATE_KEY, title="Fuse update available", kind="idle", item=sha,
                         body=f"{upstream} is at {sha[:12]}; this server runs {_head[:12]}. "
                              "Pull and restart to update; users see what changed when they next open a chat.")
    return sha
