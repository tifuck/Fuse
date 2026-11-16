"""Operational regression checks; never touch services or real databases."""
import gzip
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from scripts import backup_mariadb

ROOT = Path(__file__).resolve().parents[2]


def test_simultaneous_backups_have_distinct_outputs(tmp_path, monkeypatch):
    class Clock:
        @staticmethod
        def now(_):
            return datetime(2026, 10, 11, tzinfo=timezone.utc)
    def dump(*args, **kwargs):
        return SimpleNamespace(stdout=io.BytesIO(b"sql fixture"), stderr=io.BytesIO(b""),
                               wait=lambda: 0, poll=lambda: 0)
    monkeypatch.setattr(backup_mariadb, "datetime", Clock)
    monkeypatch.setattr(backup_mariadb.subprocess, "Popen", dump)
    monkeypatch.setattr(backup_mariadb, "settings", SimpleNamespace(database_url="mysql://u:p@localhost/fixture"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(lambda _: backup_mariadb.backup(tmp_path), range(2)))
    assert len(set(paths)) == 2
    for path in paths:
        assert gzip.decompress(path.read_bytes()) == b"sql fixture"
        assert path.with_suffix(path.suffix + ".sha256").exists()


