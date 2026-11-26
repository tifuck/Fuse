"""Per-user automatic archiving and cleanup of chats and projects.

A chat (project sessions included) is inactive once nothing has happened in
it for the user's window: no message, no run, no restore. Running work,
live background commands, scheduled-task threads and pinned chats are never
touched. Projects archive when neither they nor any of their sessions changed
in the window (a pinned session keeps its project out), and take their open
sessions with them (restoring brings those back).
Deletion only ever removes items that are already archived, counted from
when they were archived. Instance-wide ceilings (RETENTION_FORCE_*) apply
on top of what users choose and can only shorten it, and CHAT_MAX_IDLE_DAYS
deletes any chat idle that long, archived or not, pinned or not, whatever the
user chose. Artifacts expire on their own clock (artifacts.expire_idle).

Media and run transcripts have shorter clocks of their own, for every chat
whatever its user chose: an archived chat, and one idle CHAT_MEDIA_IDLE_DAYS,
loses its images and attached files (each becomes a short note), and a
finished run's stored provider transcript goes CHAT_AGENT_TRANSCRIPT_DAYS after a subagent ended, or
CHAT_RUN_TRANSCRIPT_DAYS after a newer turn replaced a root run. The chat, its
model history (retained_history) and each run's final message stay.
Deleting a chat or project deletes its media with it (chat_images cascades
from chat_threads; attachments live in the thread row), and its runs, command
jobs and events (chat_threads.forget_thread_rows).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from . import db
from .config import settings

logger = logging.getLogger(__name__)
SWEEP_SECONDS = 600
MAX_ARCHIVE_HOURS = 24 * 365
MAX_DELETE_DAYS = 3650
# Operational history nothing else deletes: fire rows cascade only with their
# schedule and command_jobs rows outlive their threads.
HISTORY_DAYS = 30
_last_sweep = 0.0


def init_schema(conn):
    db._ensure_column(conn, "user_projects", "color", "TEXT NOT NULL DEFAULT ''")
    db._ensure_column(conn, "user_projects", "logo_rev", "INTEGER NOT NULL DEFAULT 0")
    db._ensure_column(conn, "user_projects", "archived", "INTEGER NOT NULL DEFAULT 0")
    db._ensure_column(conn, "user_projects", "archived_at", "TEXT NOT NULL DEFAULT ''")
    db._ensure_column(conn, "chat_threads", "archived_at", "TEXT NOT NULL DEFAULT ''")
    db._ensure_column(conn, "chat_threads", "active_at", "TEXT NOT NULL DEFAULT ''")
    db._ensure_column(conn, "chat_threads", "archived_by_project", "INTEGER NOT NULL DEFAULT 0")
    db._ensure_column(conn, "users", "retention_archive_hours", "INTEGER NOT NULL DEFAULT 0")
    db._ensure_column(conn, "users", "retention_archive_chats", "INTEGER NOT NULL DEFAULT 1")
    db._ensure_column(conn, "users", "retention_archive_projects", "INTEGER NOT NULL DEFAULT 1")
    db._ensure_column(conn, "users", "retention_delete_days", "INTEGER NOT NULL DEFAULT 0")
    # On MariaDB this only verifies the table: scripts/sql/schema.sql creates it.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS project_logos (
            project_id INTEGER PRIMARY KEY REFERENCES user_projects(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            mime TEXT NOT NULL,
            data BLOB NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chat_media_purges (
            thread_id INTEGER PRIMARY KEY REFERENCES chat_threads(id) ON DELETE CASCADE,
            purged_at TEXT NOT NULL
        );
    """)


class RetentionError(ValueError):
    pass


def _limits() -> dict:
    return {
        "archive_days": max(0, int(getattr(settings, "retention_force_archive_days", 0) or 0)),
        "delete_days": max(0, int(getattr(settings, "retention_force_delete_days", 0) or 0)),
        "idle_days": max(0, int(getattr(settings, "chat_max_idle_days", 0) or 0)),
        "media_days": max(0, int(getattr(settings, "chat_media_idle_days", 0) or 0)),
    }


def get_settings(user_id: int) -> dict:
    row = db.fetchone(
        """SELECT retention_archive_hours, retention_archive_chats, retention_archive_projects, retention_delete_days
           FROM users WHERE id=?""",
        (user_id,),
    )
    return {
        "archive_hours": int(row["retention_archive_hours"] or 0) if row else 0,
        "archive_chats": bool(row["retention_archive_chats"]) if row else True,
        "archive_projects": bool(row["retention_archive_projects"]) if row else True,
        "delete_days": int(row["retention_delete_days"] or 0) if row else 0,
        "limits": _limits(),
    }


def _bounded_int(raw, field: str, maximum: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0 or raw > maximum:
        raise RetentionError(f"{field} must be a whole number from 0 to {maximum}")
    return raw


def set_settings(user_id: int, raw: dict) -> dict:
    allowed = {"archive_hours", "archive_chats", "archive_projects", "delete_days"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise RetentionError("unsupported cleanup fields")
    # Only the fields sent: writing back a read of the others would revert
    # another save of them that landed in between.
    sets = {}
    if "archive_hours" in raw:
        sets["retention_archive_hours"] = _bounded_int(raw["archive_hours"], "archive_hours", MAX_ARCHIVE_HOURS)
    if "delete_days" in raw:
        sets["retention_delete_days"] = _bounded_int(raw["delete_days"], "delete_days", MAX_DELETE_DAYS)
    for key in ("archive_chats", "archive_projects"):
        if key in raw:
            if not isinstance(raw[key], bool):
                raise RetentionError(f"{key} must be true or false")
            sets[f"retention_{key}"] = int(raw[key])
    if sets:
        db.execute(f"UPDATE users SET {', '.join(f'{col}=?' for col in sets)} WHERE id=?", (*sets.values(), user_id))
    return get_settings(user_id)


def effective(prefs: dict) -> tuple[int, int]:
    """(archive window in hours, delete window in days); 0 means off."""
    limits = prefs.get("limits") or _limits()
    hours = int(prefs.get("archive_hours") or 0)
    if limits["archive_days"]:
        hours = min(hours, limits["archive_days"] * 24) if hours else limits["archive_days"] * 24
    days = int(prefs.get("delete_days") or 0)
    if limits["delete_days"]:
        days = min(days, limits["delete_days"]) if days else limits["delete_days"]
    return hours, days


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _latest(*values) -> str:
    return max((v for v in values if isinstance(v, str) and v), default="")


def _thread_activity(conn, user_id: int) -> tuple[list[dict], set[int]]:
    rows = conn.execute(
        """SELECT id, kind, project_id, archived, archived_at, archived_by_project, schedule_id,
                  created_at, updated_at, active_at, revision, pinned
           FROM chat_threads WHERE user_id=?""",
        (user_id,),
    ).fetchall()
    runs = {int(r["thread_id"]): r for r in conn.execute(
        """SELECT thread_id, MAX(updated_at) AS last_at,
                  SUM(CASE WHEN status IN ('queued','running') THEN 1 ELSE 0 END) AS live
           FROM chat_runs WHERE user_id=? GROUP BY thread_id""",
        (user_id,),
    ).fetchall()}
    busy = {tid for tid, r in runs.items() if int(r["live"] or 0)}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='command_jobs'").fetchone():
        for r in conn.execute(
            """SELECT thread_id, COUNT(*) AS n FROM command_jobs
               WHERE user_id=? AND thread_id IS NOT NULL AND status IN ('queued','running','cancelling') GROUP BY thread_id""",
            (user_id,),
        ).fetchall():
            busy.add(int(r["thread_id"]))
    out = []
    for row in rows:
        tid = int(row["id"])
        run = runs.get(tid)
        # active_at is only written since cleanup existed; older rows fall back
        # to updated_at, which also moves on renames and compaction (later, so
        # never archives early).
        last = _latest(row["active_at"] or row["updated_at"], row["created_at"], run["last_at"] if run else "")
        out.append({**dict(row), "id": tid, "last_active": last})
    return out, busy


def _project_unchanged(conn, user_id: int, project: dict, sessions: list[dict]) -> bool:
    """Re-check under the write lock: a project edited, or a session used or
    added after the sweep's snapshot, is no longer idle."""
    row = conn.execute(
        "SELECT archived, updated_at FROM user_projects WHERE id=? AND user_id=?" + db.for_update(conn),
        (project["id"], user_id),
    ).fetchone()
    if row is None or row["archived"] or row["updated_at"] != project["updated_at"]:
        return False
    current = {int(r["id"]): int(r["revision"]) for r in conn.execute(
        "SELECT id, revision FROM chat_threads WHERE user_id=? AND project_id=? AND kind='project'" + db.for_update(conn),
        (user_id, project["id"]),
    ).fetchall()}
    return current == {int(t["id"]): int(t["revision"]) for t in sessions}


def sweep_user(user_id: int, prefs: dict | None = None, *, now: datetime | None = None) -> dict:
    """Apply one user's policy once. Returns counts of what changed."""
    from . import chat_store as store, chats, hosts

    prefs = prefs or get_settings(user_id)
    hours, days = effective(prefs)
    limits = prefs.get("limits") or _limits()
    forced_archive = bool(limits["archive_days"])
    archive_chats = prefs.get("archive_chats", True) or forced_archive
    archive_projects = prefs.get("archive_projects", True) or forced_archive
    now = now or datetime.now(timezone.utc)
    stats = {"archived_chats": 0, "archived_projects": 0, "deleted_chats": 0, "deleted_projects": 0}
    idle_days = int(limits.get("idle_days") or 0)
    if not hours and not days and not idle_days:
        return stats
    cutoff = _iso(now - timedelta(hours=hours)) if hours else ""
    delete_cutoff = _iso(now - timedelta(days=days)) if days else ""
    idle_cutoff = _iso(now - timedelta(days=idle_days)) if idle_days else ""

    with db.tx() as conn:
        conn.execute("BEGIN")
        threads, busy = _thread_activity(conn, user_id)
        projects = [dict(r) for r in conn.execute(
            "SELECT id, host_id, archived, archived_at, created_at, updated_at FROM user_projects WHERE user_id=?",
            (user_id,),
        ).fetchall()]

    if cutoff:
        if archive_projects:
            by_project: dict[int, list[dict]] = {}
            for t in threads:
                if t["kind"] == "project" and t["project_id"] is not None:
                    by_project.setdefault(int(t["project_id"]), []).append(t)
            for p in projects:
                if p["archived"]:
                    continue
                sessions = by_project.get(int(p["id"]), [])
                # Archiving a project archives its sessions: one that a
                # schedule owns or that is pinned keeps the whole project out.
                if any(t["id"] in busy or t["schedule_id"] is not None or t["pinned"] for t in sessions):
                    continue
                last = _latest(p["updated_at"], p["created_at"], *(t["last_active"] for t in sessions))
                if last and last < cutoff:
                    with store.transaction() as conn:
                        if _project_unchanged(conn, user_id, p, sessions) and \
                                hosts.set_project_archived(conn, user_id, int(p["id"]), True, skip_running=True) is not None:
                            stats["archived_projects"] += 1
                            hosts.projects_changed(conn, user_id)
                            p["archived"] = 1
                            # Archived just now: the delete pass must age them from
                            # this moment, not from their old activity timestamps.
                            archived_now = _iso(now)
                            p["archived_at"] = archived_now
                            for t in sessions:
                                t["archived"] = 1
                                t["archived_at"] = archived_now
        if archive_chats:
            for t in threads:
                if t["archived"] or t["pinned"] or t["schedule_id"] is not None or t["id"] in busy:
                    continue
                if not t["last_active"] or t["last_active"] >= cutoff:
                    continue
                ts = db.now()
                with store.transaction() as conn:
                    # Guard on the snapshot's revision: a chat used since the
                    # sweep read it is no longer idle.
                    cur = conn.execute(
                        """UPDATE chat_threads SET archived=1, archived_at=?, archived_by_project=0, revision=revision+1
                           WHERE id=? AND user_id=? AND archived=0 AND revision=?""",
                        (ts, t["id"], user_id, t["revision"]),
                    )
                    if cur.rowcount:
                        store.thread_event(conn, user_id, t["id"])
                        stats["archived_chats"] += 1

    if delete_cutoff:
        for p in projects:
            if not p["archived"]:
                continue
            since = p["archived_at"] or p["updated_at"] or p["created_at"]
            if not since or since >= delete_cutoff:
                continue
            open_sessions = db.fetchone(
                "SELECT COUNT(*) AS n FROM chat_threads WHERE user_id=? AND project_id=? AND archived=0",
                (user_id, p["id"]),
            )
            if int(open_sessions["n"] if open_sessions else 0):
                continue
            if any(t["id"] in busy or t["schedule_id"] is not None or t["pinned"]
                   for t in threads if t.get("project_id") == p["id"]):
                continue
            # The snapshot is from the start of the sweep: a project restored
            # (or re-archived) since must be judged by its current row.
            # delete_project re-checks it under the row lock (archived_before).
            fresh = db.fetchone(
                "SELECT archived, archived_at, updated_at, created_at FROM user_projects WHERE id=? AND user_id=?",
                (p["id"], user_id),
            )
            if fresh is None or not fresh["archived"]:
                continue
            since = fresh["archived_at"] or fresh["updated_at"] or fresh["created_at"]
            if not since or since >= delete_cutoff:
                continue
            try:
                if hosts.delete_project(user_id, int(p["host_id"]), int(p["id"]), sessions="delete", cancel_active=False,
                                        archived_before=delete_cutoff):
                    stats["deleted_projects"] += 1
            except hosts.HostError as exc:
                # Restored or still busy since the check, or a real failure:
                # either way say why the project was kept.
                logger.info("Retention kept project %s for user %s: %s", p["id"], user_id, exc.detail)
                continue
        for t in threads:
            if not t["archived"] or t["pinned"] or t["schedule_id"] is not None or t["id"] in busy:
                continue
            since = t["archived_at"] or t["updated_at"]
            if not since or since >= delete_cutoff:
                continue
            fresh = db.fetchone("SELECT archived, archived_at, updated_at, revision FROM chat_threads WHERE id=? AND user_id=?",
                                (t["id"], user_id))
            if fresh is None or not fresh["archived"]:
                continue
            # Restored and re-archived since the snapshot: age it from the new
            # archive time, not the old one.
            since = fresh["archived_at"] or fresh["updated_at"]
            if not since or since >= delete_cutoff:
                continue
            # expected_revision closes the check/delete window: delete_thread
            # aborts when the thread changed (or gained a run) underneath us.
            try:
                deleted = chats.delete_thread(user_id, t["id"], expected_revision=int(fresh["revision"]), cancel_active=False)
            except chats.ChatValidationError:
                deleted = False
            if deleted:
                stats["deleted_chats"] += 1
    if idle_cutoff:
        # Mandatory: no user setting or pin keeps a chat this idle. A schedule's
        # thread belongs to the schedule; a busy one is in use.
        for t in threads:
            if t["schedule_id"] is not None or t["id"] in busy:
                continue
            if not t["last_active"] or t["last_active"] >= idle_cutoff:
                continue
            # The snapshot's revision: a chat used (or archived) since is left
            # for the next sweep to judge.
            try:
                deleted = chats.delete_thread(user_id, t["id"], expected_revision=int(t["revision"]), cancel_active=False)
            except chats.ChatValidationError:
                deleted = False
            if deleted:
                stats["deleted_chats"] += 1
    if any(stats.values()):
        logger.info("retention user=%s %s", user_id, stats)
    return stats


def prune_history(*, now: datetime | None = None) -> dict:
    """Age out terminal fire/command history older than HISTORY_DAYS."""
    cutoff = _iso((now or datetime.now(timezone.utc)) - timedelta(days=HISTORY_DAYS))
    counts = {"fires": 0, "jobs": 0, "orphan_jobs": 0, "terminals": 0, "orphan_runs": 0}
    fires = "status NOT IN ('queued','running') AND finished_at IS NOT NULL AND finished_at < ?"
    counts["fires"] = _delete_ids("schedule_fires", db.fetchall(f"SELECT id FROM schedule_fires WHERE {fires}", (cutoff,)),
                                  fires, (cutoff,))
    if db.fetchone("SELECT 1 FROM sqlite_master WHERE type='table' AND name='command_jobs'"):
        jobs = "status NOT IN ('running','cancelling') AND finished_at IS NOT NULL AND finished_at < ?"
        counts["jobs"] = _delete_ids("command_jobs", db.fetchall(f"SELECT id FROM command_jobs WHERE {jobs}", (cutoff,)),
                                     jobs, (cutoff,))
        # command_jobs has no foreign keys (adding one copies the table): jobs
        # whose account or chat an older path deleted without them.
        gone = """status NOT IN ('running','cancelling')
                  AND (NOT EXISTS (SELECT 1 FROM users u WHERE u.id=command_jobs.user_id)
                       OR (thread_id IS NOT NULL
                           AND NOT EXISTS (SELECT 1 FROM chat_threads t WHERE t.id=command_jobs.thread_id)))"""
        counts["orphan_jobs"] = _delete_ids("command_jobs", db.fetchall(f"SELECT id FROM command_jobs WHERE {gone}"),
                                            gone, ())
    with db.tx() as conn:
        if conn.native("SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE()"
                       " AND table_name='terminal_sessions'").fetchone():
            cur = conn.execute(
                "DELETE FROM terminal_sessions WHERE closed_at IS NOT NULL AND closed_at < ?", (cutoff,)
            )
            counts["terminals"] = int(cur.rowcount or 0)
    # chat_runs has no foreign key to its thread: runs some old path left
    # behind when it deleted the thread. Their threads come from a loose
    # scan of the thread index; a plain read, so nothing is locked.
    orphans = db.fetchall(
        """SELECT r.id FROM (SELECT DISTINCT thread_id FROM chat_runs WHERE thread_id IS NOT NULL
                                AND NOT EXISTS (SELECT 1 FROM chat_threads t WHERE t.id=chat_runs.thread_id)) o
           JOIN chat_runs r ON r.thread_id=o.thread_id
           WHERE r.status NOT IN ('queued','running') AND r.updated_at < ?""",
        (cutoff,),
    )
    counts["orphan_runs"] = _delete_ids(
        "chat_runs", orphans,
        """status NOT IN ('queued','running') AND updated_at < ? AND thread_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM chat_threads t WHERE t.id=chat_runs.thread_id)""",
        (cutoff,),
    )
    return counts


_DELETE_BATCH = 500


def _delete_ids(table: str, rows, where: str, args: tuple) -> int:
    """Delete rows by primary key, _DELETE_BATCH per short transaction.
    where repeats the selection, so a row that changed since is kept."""
    ids = [row["id"] for row in rows]
    deleted = 0
    for start in range(0, len(ids), _DELETE_BATCH):
        batch = ids[start:start + _DELETE_BATCH]
        with db.tx() as conn:
            cur = conn.execute(f"DELETE FROM {table} WHERE id IN ({','.join('?' * len(batch))}) AND {where}",
                               (*batch, *args))
        deleted += int(cur.rowcount or 0)
    return deleted


def _days(name: str) -> int:
    return max(0, int(getattr(settings, name, 0) or 0))


_TERMINAL = "('completed','cancelled','failed','interrupted')"
# Rows per transcript UPDATE: each holds megabytes, and one statement over
# thousands of them would build an undo log as large.
_CLEAR_BATCH = 50
# Work per sweep: the maintenance loop waits for it (run reaper, wakes), so a
# backlog (the first sweep on a large instance) drains over several sweeps.
_CLEAR_MAX = 1000
_PURGE_MAX = 50


def _clear_transcripts(where: str, args: tuple) -> int:
    split = db.provider_split()
    held = "(r.provider_base IS NOT NULL OR r.provider_tail IS NOT NULL)" if split else "r.provider_messages IS NOT NULL"
    clear = "provider_base=NULL, provider_tail=NULL" if split else "provider_messages=NULL"
    total = 0
    after = ""
    while total < _CLEAR_MAX:
        # Keyset on the primary key: a batch resumes where the last ended
        # instead of rescanning the runs it already passed.
        ids = [row["id"] for row in db.fetchall(
            f"SELECT r.id FROM chat_runs r WHERE r.status IN {_TERMINAL} AND {held} AND {where} AND r.id > ?"
            f" ORDER BY r.id LIMIT {_CLEAR_BATCH}", (*args, after))]
        if not ids:
            return total
        after = ids[-1]
        with db.tx() as conn:
            cur = conn.execute(
                f"UPDATE chat_runs SET {clear} WHERE status IN {_TERMINAL} AND id IN ({','.join('?' * len(ids))})", ids)
        if not cur.rowcount:
            return total
        total += int(cur.rowcount)
    return total


def prune_transcripts(*, now: datetime | None = None) -> dict:
    """Drop stored provider transcripts nothing needs any more.

    Every run stores the whole conversation up to it, so a chat's runs grow
    with the square of its length. A finished root run a newer turn replaced
    is covered by the thread's retained_history; a finished subagent keeps its
    final message (its report). Their transcripts only fed old-run views,
    resuming (a subagent then restarts from its task) and the rare replay
    that misses retained_history (it falls back to the chat's tool traces)."""
    now = now or datetime.now(timezone.utc)
    counts = {"agent_runs": 0, "replaced_runs": 0}
    agent_days, run_days = _days("chat_agent_transcript_days"), _days("chat_run_transcript_days")
    if agent_days:
        # A live root may still steer or continue its subagents.
        counts["agent_runs"] = _clear_transcripts(
            f"""r.parent_run_id IS NOT NULL AND r.updated_at < ?
                AND NOT EXISTS (SELECT 1 FROM chat_runs p WHERE p.id=r.root_run_id AND p.status NOT IN {_TERMINAL})""",
            (_iso(now - timedelta(days=agent_days)),),
        )
    if run_days:
        counts["replaced_runs"] = _clear_transcripts(
            """r.parent_run_id IS NULL AND r.updated_at < ?
               AND EXISTS (SELECT 1 FROM chat_runs n WHERE n.thread_id=r.thread_id AND n.parent_run_id IS NULL
                           AND n.created_at > r.created_at)""",
            (_iso(now - timedelta(days=run_days)),),
        )
    return counts


def _strip_history(raw: str, old_messages: list, new_messages: list) -> str | None:
    """retained_history with its provider rows' media stripped and its anchors
    moved to the stripped display rows, or None when it needs no change. An
    anchor that matched the old row matches the new one, so replay still
    reuses the retained prefix instead of rebuilding it from traces."""
    from . import chat_history, chat_images

    try:
        memory = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    if not isinstance(memory, dict):
        return None
    changed = False
    rows = memory.get("messages")
    stripped = chat_images.strip_media(rows)
    if stripped is not rows:
        memory["messages"] = stripped
        changed = True
    anchor = memory.get("anchor")
    if new_messages is not old_messages and isinstance(anchor, list):
        old, new = chat_history.anchors(old_messages), chat_history.anchors(new_messages)
        moved = [new[i] if i < len(old) and value == old[i] else value for i, value in enumerate(anchor)]
        if moved != anchor:
            memory["anchor"] = moved
            changed = True
    return chat_history._json(memory) if changed else None


def _purge_thread_media(user_id: int, thread: dict) -> int | None:
    """One archived or idle thread's media, gone. The number of stored images deleted, or
    None when the thread moved since the sweep read it or its transcript is unreadable."""
    from . import chat_images, chat_tx

    tid = thread["id"]
    with chat_tx.transaction() as conn:
        # Run before thread, the order finalizers and thread writers use.
        if conn.execute("SELECT 1 FROM chat_runs WHERE thread_id=? AND status IN ('queued','running') LIMIT 1",
                        (tid,)).fetchone():
            return None
        row = conn.execute(
            "SELECT messages, retained_history, revision FROM chat_threads WHERE id=? AND user_id=?" + db.for_update(conn),
            (tid, user_id),
        ).fetchone()
        if row is None or int(row["revision"]) != int(thread["revision"]):
            return None
        try:
            messages = json.loads(row["messages"] or "[]")
        except (TypeError, ValueError):
            messages = None
        if not isinstance(messages, list):
            # Its images may be all that is left of it: never delete them blind.
            logger.warning("media purge skipped for thread %s: unreadable transcript", tid)
            return None
        stripped = chat_images.strip_media(messages)
        history = _strip_history(row["retained_history"], messages, stripped)
        shas = [r["sha"] for r in conn.execute("SELECT sha FROM chat_images WHERE thread_id=?", (tid,)).fetchall()]
        conn.execute("DELETE FROM chat_images WHERE thread_id=?", (tid,))
        purged_at = db.now()
        sets, args = [], []
        if history is not None:
            sets.append("retained_history=?")
            args.append(history)
        if stripped is not messages:
            # Not active_at or updated_at: losing media is not activity.
            sets += ["messages=?", "revision=revision+1"]
            args.append(chat_tx.dumps(stripped))
        if sets:
            conn.execute(f"UPDATE chat_threads SET {','.join(sets)} WHERE id=?", (*args, tid))
        if stripped is not messages:
            chat_tx.thread_event(conn, user_id, tid, transcript_changed=True)
    chat_images.forget(user_id, shas)
    _strip_runs(user_id, tid)
    # Marked purged only once its runs are stripped too: a purge cut off
    # before that runs again on the next sweep instead of leaving runs that
    # point at deleted images.
    db.execute(
        "INSERT INTO chat_media_purges(thread_id, purged_at) VALUES(?,?)"
        " ON CONFLICT(thread_id) DO UPDATE SET purged_at=excluded.purged_at",
        (tid, purged_at),
    )
    return len(shas)


def _strip_runs(user_id: int, thread_id: int) -> None:
    """Media out of the finished runs a purged thread still keeps transcripts
    or requests for (its latest turn, recent subagents)."""
    from . import chat_images, chat_tx

    for ident in [r["id"] for r in db.fetchall(
            f"SELECT id FROM chat_runs WHERE thread_id=? AND user_id=? AND status IN {_TERMINAL}", (thread_id, user_id))]:
        with chat_tx.transaction() as conn:
            run = conn.execute(
                f"SELECT request, provider_messages FROM chat_runs WHERE id=? AND status IN {_TERMINAL}" + db.for_update(conn),
                (ident,),
            ).fetchone()
            if run is None:
                continue
            sets, args = [], []
            try:
                transcript = json.loads(run["provider_messages"]) if run["provider_messages"] else None
            except (TypeError, ValueError):
                transcript = None
            stripped = chat_images.strip_media(transcript)
            if stripped is not transcript:
                sets.append(db.set_provider_sql(conn))
                args.append(chat_tx.dumps(stripped))
            try:
                request = json.loads(run["request"] or "{}")
            except (TypeError, ValueError):
                request = None
            if isinstance(request, dict):
                out = {key: chat_images.strip_media(request.get(key)) for key in ("messages", "display_seed")}
                if any(out[key] is not request.get(key) for key in out):
                    sets.append("request=?")
                    args.append(chat_tx.dumps({**request, **{k: v for k, v in out.items() if k in request}}))
            if sets:
                conn.execute(f"UPDATE chat_runs SET {','.join(sets)} WHERE id=?", (*args, ident))


def purge_idle_media(*, now: datetime | None = None) -> dict:
    """Delete the images and attached files of every chat or project session
    that is archived, or idle CHAT_MEDIA_IDLE_DAYS (0 = archived ones only),
    pinned ones included; a busy one is in use. A thread purged since its
    last activity is skipped; used again, it is purged again once archived
    or idle."""
    days = _days("chat_media_idle_days")
    counts = {"threads": 0, "images": 0}
    cutoff = _iso((now or datetime.now(timezone.utc)) - timedelta(days=days)) if days else ""
    # Only users with a thread that may be due. The thread's own timestamps
    # bound last_active from below (it also takes its runs' updated_at), so
    # this finds every due thread and some that the check below skips.
    # active_at alone is not enough: run activity does not move it.
    for user in db.fetchall(
        """SELECT DISTINCT t.user_id FROM chat_threads t
           LEFT JOIN chat_media_purges m ON m.thread_id=t.id
           WHERE (t.archived<>0 OR (COALESCE(NULLIF(t.active_at,''), t.updated_at) < ? AND t.created_at < ?))
             AND (m.purged_at IS NULL OR m.purged_at <= COALESCE(NULLIF(t.active_at,''), t.updated_at)
                  OR m.purged_at <= t.created_at
                  OR EXISTS (SELECT 1 FROM chat_runs r WHERE r.thread_id=t.id AND r.updated_at >= m.purged_at))""",
        (cutoff, cutoff),
    ):
        if counts["threads"] >= _PURGE_MAX:
            break
        uid = int(user["user_id"])
        with db.tx() as conn:
            conn.execute("BEGIN")
            threads, busy = _thread_activity(conn, uid)
            purged = {int(r["thread_id"]): r["purged_at"] for r in conn.execute(
                "SELECT m.thread_id, m.purged_at FROM chat_media_purges m JOIN chat_threads t ON t.id=m.thread_id"
                " WHERE t.user_id=?", (uid,)).fetchall()}
        for t in threads:
            if counts["threads"] >= _PURGE_MAX:
                break
            if t["id"] in busy or not (t["archived"] or "" < t["last_active"] < cutoff):
                continue
            # Strictly after: activity in the purge's own second may follow it.
            if purged.get(t["id"], "") > t["last_active"]:
                continue
            try:
                deleted = _purge_thread_media(uid, t)
            except Exception:
                logger.exception("media purge failed for thread %s", t["id"])
                continue
            if deleted is not None:
                counts["threads"] += 1
                counts["images"] += deleted
    return counts


def sweep(*, now: datetime | None = None) -> dict:
    limits = _limits()
    pruned = prune_history(now=now)
    if any(pruned.values()):
        logger.info("retention pruned history %s", pruned)
    for name, step in (("transcripts", prune_transcripts), ("media", purge_idle_media)):
        try:
            done = step(now=now)
        except Exception:
            logger.exception("retention %s failed", name)
        else:
            if any(done.values()):
                logger.info("retention %s %s", name, done)
    from . import artifacts

    try:
        expired = artifacts.expire_idle(now=now)
    except Exception:
        logger.exception("artifact expiry failed")
    else:
        if any(expired.values()):
            logger.info("retention artifacts %s", expired)
    if limits["archive_days"] or limits["delete_days"] or limits["idle_days"]:
        rows = db.fetchall("SELECT id FROM users")
    else:
        rows = db.fetchall("SELECT id FROM users WHERE retention_archive_hours>0 OR retention_delete_days>0")
    total = {"archived_chats": 0, "archived_projects": 0, "deleted_chats": 0, "deleted_projects": 0}
    for row in rows:
        try:
            stats = sweep_user(int(row["id"]), now=now)
        except Exception:
            logger.exception("retention sweep failed for user %s", row["id"])
            continue
        for key, value in stats.items():
            total[key] += value
    return total


async def tick() -> None:
    """Called from the maintenance loop every minute; sweeps every 10."""
    global _last_sweep
    if time.monotonic() - _last_sweep < SWEEP_SECONDS:
        return
    _last_sweep = time.monotonic()
    await asyncio.to_thread(sweep)
