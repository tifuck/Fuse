"""scripts/backup_mariadb.py: a dump killed mid-write leaves its .partial
file; the next backup removes it once it is a day old."""
import os

from scripts import backup_mariadb


def test_only_day_old_partials_are_swept(tmp_path):
    now = 1_900_000_000.0
    stale, fresh, done = (tmp_path / name for name in (
        "fuse-mariadb-a.sql.partial", "fuse-mariadb-b.sql.partial", "fuse-mariadb-c.sql.gz"))
    for path in (stale, fresh, done):
        path.write_bytes(b"x")
        os.utime(path, (now - 2 * backup_mariadb.PARTIAL_MAX_AGE,) * 2)
    os.utime(fresh, (now - 60, now - 60))

    backup_mariadb.sweep_partials(tmp_path, now=now)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["fuse-mariadb-b.sql.partial", "fuse-mariadb-c.sql.gz"]
