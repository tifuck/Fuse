"""Shared by every suite: repo on sys.path, the real-schema guard, safe settings.

Default runs collect only tests/core. tests/legacy is the frozen pre-redesign
suite: unmaintained, never required, collected only with FUSE_LEGACY_TESTS=1
(scripts/test.sh legacy). Run it on its own, never in the same session as core.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.mariadb_fixture import install_guard

install_guard()

import pytest

# Root-level test_*.py are local, gitignored legacy leftovers (real-page
# fixtures); they belong to tests/legacy.
collect_ignore_glob = ["test_*.py"] + ([] if os.environ.get("FUSE_LEGACY_TESTS") == "1" else ["legacy/*"])


@pytest.fixture(autouse=True, scope="session")
def _no_search_time_page_reads():
    """web_search reads its top result pages for excerpts; tests fake the
    search client, not the page fetcher, so those reads would leave the
    machine. Tests of the excerpt step opt back in by patching the setting."""
    from app.config import settings

    saved = settings.web_search_excerpt_pages
    settings.web_search_excerpt_pages = 0
    yield
    settings.web_search_excerpt_pages = saved


@pytest.fixture(autouse=True, scope="session")
def _production_oauth_refresh():
    """Dev's config turns pooled OAuth refresh off (production owns those
    tokens); tests run the production path against faked token endpoints."""
    from app.config import settings

    saved = settings.oauth_refresh_enabled
    settings.oauth_refresh_enabled = True
    yield
    settings.oauth_refresh_enabled = saved
