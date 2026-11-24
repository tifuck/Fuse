import pytest

from app import agent_hub, db, ssh_pool, terminal
from app.config import ROOT
from scripts import init_db
from tests.core.terminal._support import FakePty

MIGRATION = ROOT / "scripts" / "sql" / "2026-11-24-terminal-sessions.sql"
_EXISTS = "SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='terminal_sessions'"


@pytest.fixture(autouse=True)
def terminals(fresh_db):
    """An empty registry over a schema with terminal_sessions. Until the
    migration is applied to the source schema the test applies it to its own
    (it is idempotent) and removes it afterwards."""
    with db.tx() as conn:
        created = not conn.native(_EXISTS).fetchone()
        if created:
            for statement in init_db.statements(MIGRATION.read_text(encoding="utf-8")):
                conn.native(statement)
    terminal._sessions.clear()
    yield
    terminal._sessions.clear()
    agent_hub._ptys.clear()
    if created:
        with db.tx() as conn:
            conn.native("DROP TABLE IF EXISTS terminal_sessions")


@pytest.fixture
def ssh_ptys(monkeypatch):
    """ssh_pool.open_pty answers with FakePty channels, kept in this list."""
    made: list[FakePty] = []

    async def open_pty(user_id, host_id, *, cols, rows, command=None):
        made.append(FakePty(cols, rows, command))
        return made[-1]

    monkeypatch.setattr(ssh_pool, "open_pty", open_pty)
    return made
