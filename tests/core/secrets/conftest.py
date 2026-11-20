import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def swap_on(monkeypatch):
    """These tests are about the swap itself: the site switch is on."""
    monkeypatch.setattr(settings, "secret_swap_enabled", True)
