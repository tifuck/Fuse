"""Usage area fixtures: process-wide pool state starts empty in every test.

Pool routing keeps cooldowns, in-flight counts, session pins and a share-level
cache in module globals; a test must never see another test's.
"""
import pytest

from app.sources import pool_pick, pool_policy, pool_session


def _clear():
    pool_pick.reset_health()
    pool_session.reset_store()
    pool_policy._share_cache.clear()


@pytest.fixture(autouse=True)
def _pool_state():
    _clear()
    yield
    _clear()
