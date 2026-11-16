"""Features-area fixtures: process-wide scheduler, vault and push state starts
empty in every test, and skills live in a per-test directory."""
import pytest


@pytest.fixture(autouse=True)
def _clean_feature_state(monkeypatch):
    from app import push, schedules, vault

    schedules._INFLIGHT.clear()
    # reconcile() is throttled to once a minute of monotonic time; tests call
    # it directly, never through the throttle.
    monkeypatch.setattr(schedules, "_last_reconcile", float("inf"))
    monkeypatch.setattr(push, "_keys", None)
    with vault._lock:
        vault._vault.clear()
    yield
    schedules._INFLIGHT.clear()
    with vault._lock:
        vault._vault.clear()


@pytest.fixture
def skills_dir(tmp_path, monkeypatch):
    """data/skills redirected to a scratch directory for the test."""
    from app.config import settings

    path = tmp_path / "skills"
    monkeypatch.setattr(settings, "skills_directory", str(path))
    return path
