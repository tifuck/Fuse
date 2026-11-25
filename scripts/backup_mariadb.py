"""Consistent MariaDB backup; credentials never appear in process arguments."""
import gzip
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, unquote
from uuid import uuid4

from app.config import settings


# A partial this old belongs to a dump that was killed (its finally never ran).
PARTIAL_MAX_AGE = 86400
# A dump that sends nothing for this long is stalled (server hung, network
# gone): kill it so the backup fails instead of hanging forever.
STALL_SECONDS = 1800


def sweep_partials(root: Path, now: float | None = None) -> None:
    now = time.time() if now is None else now
    for stale in root.glob("fuse-mariadb-*.sql.partial"):
        try:
            if now - stale.stat().st_mtime > PARTIAL_MAX_AGE:
                stale.unlink()
        except FileNotFoundError:
            pass


def backup(directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = root / f"fuse-mariadb-{stamp}-{uuid4().hex}.sql.gz"
    partial = target.with_suffix(".partial")
    parsed = urlsplit(settings.database_url)
    def quote(value):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'
    config = "[client]\n" + "\n".join(f"{k}={quote(str(v))}" for k, v in {
        "host": parsed.hostname or "127.0.0.1", "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""), "password": unquote(parsed.password or ""),
    }.items())
    with tempfile.NamedTemporaryFile(mode="w", prefix="fuse-db-", suffix=".cnf") as auth:
        auth.write(config)
        auth.flush()
        # The client's default packet is smaller than our largest rows (chat_runs);
        # past it the server drops the connection mid-table (error 2013).
        proc = subprocess.Popen(["mariadb-dump", f"--defaults-extra-file={auth.name}",
            "--single-transaction", "--quick", "--hex-blob", "--skip-lock-tables", "--max-allowed-packet=1G",
            parsed.path.strip("/")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Drain stderr concurrently: unread, a full pipe would block the dump
        # mid-stream and stall the stdout copy loop below.
        stderr_buf = []
        drain = threading.Thread(target=lambda: stderr_buf.append(proc.stderr.read()), daemon=True)
        drain.start()
        progress = [time.monotonic()]
        stalled = threading.Event()
        done = threading.Event()
        def watchdog():
            while not done.wait(30):
                if time.monotonic() - progress[0] > STALL_SECONDS:
                    stalled.set()
                    proc.kill()
                    return
        threading.Thread(target=watchdog, daemon=True).start()
        created = False
        try:
            fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(fd, "wb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb") as stream:
                    while block := proc.stdout.read(1024 * 1024):
                        progress[0] = time.monotonic()
                        stream.write(block)
                raw.flush()
                os.fsync(raw.fileno())
            status = proc.wait()
            drain.join()
            if stalled.is_set():
                raise RuntimeError(f"MariaDB dump stalled: no output for {STALL_SECONDS} s, killed")
            if status:
                raise RuntimeError("MariaDB dump failed: " + b"".join(stderr_buf).decode(errors="replace"))
            partial.rename(target)
            dir_fd = os.open(root, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            done.set()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if created:
                partial.unlink(missing_ok=True)
    with target.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    target.with_suffix(target.suffix + ".sha256").write_text(f"{digest}  {target.name}\n")
    retain = max(1, int(os.environ.get("FUSE_BACKUP_RETAIN_DB", "14")))
    for old in sorted(root.glob("fuse-mariadb-*.sql.gz"), reverse=True)[retain:]:
        old.unlink(missing_ok=True)
        old.with_suffix(old.suffix + ".sha256").unlink(missing_ok=True)
    sweep_partials(root)
    print(target)
    return target


if __name__ == "__main__":
    backup(sys.argv[1] if len(sys.argv) > 1 else "backups")
