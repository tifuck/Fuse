"""Auth-area fixtures: process-wide limiter and vault state reset per test, and
the first-run setup token kept out of the checkout's data/ directory."""
import pytest

from app.config import ROOT


@pytest.fixture(autouse=True, scope="session")
def _desktop_release_dir():
    # app.main mounts data/desktop-releases (runtime data, gitignored): a fresh
    # worktree has no data/ yet and the import would fail.
    (ROOT / "data" / "desktop-releases").mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _auth_state(tmp_path, monkeypatch):
    from app import accounts, vault
    from app.security import limiter

    monkeypatch.setattr(accounts, "SETUP_TOKEN_FILE", tmp_path / "data" / "setup-token")
    limiter._hits.clear()
    vault.lock_all()
    yield
    limiter._hits.clear()
    vault.lock_all()


@pytest.fixture(autouse=True)
def _first_user_unclaimed(request):
    """reset_tables keeps schema_meta, so the first-admin claim an earlier
    test's first account wrote would make every later "first" account a member."""
    if "fresh_db" in request.fixturenames:
        request.getfixturevalue("fresh_db")
        from app import db

        db.execute("DELETE FROM schema_meta WHERE key='first_user_claimed'")


@pytest.fixture
def web(fresh_db):
    """A TestClient over every route app.main registers (no lifespan)."""
    from tests.core.auth._support import web_client

    with web_client() as client:
        yield client
