"""The command_jobs table check asks information_schema once per schema."""
from app import mysql_compat

CHECK = "SELECT 1 FROM sqlite_master WHERE type='table' AND name='command_jobs'"


class _Raw:
    open = True

    def __init__(self, db):
        self.db = db
        self.encoders = {}


def test_a_found_table_is_remembered_per_schema_and_a_missing_one_is_asked_again(monkeypatch):
    monkeypatch.setattr(mysql_compat, "_HAS_COMMAND_JOBS", set())
    asked = []
    present = {b"with_jobs": True, b"without_jobs": False}

    def native(self, sql, args=()):
        asked.append(self._raw.db)
        return mysql_compat.Cursor([(1,)] if present[self._raw.db] else [])

    monkeypatch.setattr(mysql_compat.Conn, "native", native)
    found = mysql_compat.Conn(_Raw(b"with_jobs"))
    missing = mysql_compat.Conn(_Raw(b"without_jobs"))
    assert found.execute(CHECK).fetchone() and found.execute(CHECK).fetchone()
    assert missing.execute(CHECK).fetchone() is None and missing.execute(CHECK).fetchone() is None
    assert asked == [b"with_jobs", b"without_jobs", b"without_jobs"]
