"""Features-area helpers: hosts and projects as rows, schedules with sane
defaults, schedule fire rows, and a pinned scheduler clock.

The scheduler reads time through ``app.schedules.utcnow`` (imported from
app.cron); ``clock(at)`` patches that seam so slots, grace windows and retry
cooldowns are exact instead of following the wall clock.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

from app import db, schedules
from app.cron import iso_utc


def at(text):
    """A UTC instant from '2026-10-20T05:00:00Z'."""
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


@contextmanager
def clock(now):
    """Pin the scheduler's clock (``schedules.utcnow``) to ``now``."""
    instant = at(now) if isinstance(now, str) else now
    with patch.object(schedules, "utcnow", return_value=instant):
        yield instant


def unlock_vault(uid) -> None:
    """An unlocked vault for the user without the password KDF."""
    import time

    from app import vault

    with vault._lock:
        vault._vault[int(uid)] = {"dek": b"k" * 32, "last_seen": time.monotonic(), "pinned_runs": set()}


def waiting_on(table) -> bool:
    """Another connection is blocked in a locking read or write of table
    (a condition to wait on before releasing the transaction holding it)."""
    with db.tx() as conn:
        return conn.native(
            "SELECT 1 FROM information_schema.PROCESSLIST WHERE ID<>CONNECTION_ID() AND DB=DATABASE() AND COMMAND='Query'"
            " AND INFO LIKE %s AND (INFO LIKE 'UPDATE%%' OR INFO LIKE '%%FOR UPDATE%%')",
            (f"%{table}%",),
        ).fetchone() is not None


def make_host(uid, alias="box", *, auth_type="agent", default_cwd="") -> int:
    """A user_hosts row; nothing connects to it."""
    ts = db.now()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO user_hosts (user_id, alias, hostname, username, auth_type, secret, default_cwd,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (uid, alias, "host.invalid", "dev", auth_type, "", default_cwd, ts, ts),
        )
        return int(db.last_id(conn))


def make_project(uid, host_id, name="api", path=None) -> int:
    ts = db.now()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO user_projects (user_id, host_id, name, remote_path, notes, created_at, updated_at)"
            " VALUES (?,?,?,?,'',?,?)",
            (uid, host_id, name, path or "/srv/" + name, ts, ts),
        )
        return int(db.last_id(conn))


def make_schedule(uid, **raw):
    """create_schedule with a chat target, hourly cadence and the offline model."""
    body = {"name": "task", "target": "chat", "prompt": "hi", "model": "offline",
            "timezone": "UTC", "recur_kind": "every", "every_seconds": 3600}
    body.update(raw)
    return schedules.create_schedule(uid, body)


def stored(schedule_id) -> dict:
    return dict(db.fetchone("SELECT * FROM chat_schedules WHERE id=?", (schedule_id,)))


def set_due(schedule_id, slot) -> None:
    db.execute("UPDATE chat_schedules SET next_fire_at=? WHERE id=?", (slot, schedule_id))


def add_fire(item, slot, status="queued", *, started_at=None, run_id=None, fire_id=None) -> dict:
    """A schedule_fires row as the scheduler writes it; returns the fire dict _fire takes."""
    fid = fire_id or f"fire-{item['id']}-{slot}"
    db.execute(
        """INSERT INTO schedule_fires(schedule_id, user_id, trigger, status, slot_key, fire_id, started_at, run_id)
           VALUES(?,?, 'scheduled', ?, ?, ?, ?, ?)""",
        (item["id"], item["user_id"], status, slot, fid, started_at or db.now(), run_id),
    )
    return {"slot_key": slot, "fire_id": fid, "status": status, "run_id": run_id}


def fires(schedule_id) -> list[dict]:
    return [dict(r) for r in db.fetchall(
        "SELECT * FROM schedule_fires WHERE schedule_id=? ORDER BY id", (schedule_id,))]


def fire_row(schedule_id, slot) -> dict | None:
    row = db.fetchone("SELECT * FROM schedule_fires WHERE schedule_id=? AND slot_key=?", (schedule_id, slot))
    return dict(row) if row else None


__all__ = ["at", "clock", "iso_utc", "unlock_vault", "waiting_on", "make_host", "make_project", "make_schedule", "stored", "set_due",
           "add_fire", "fires", "fire_row"]
