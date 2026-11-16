"""Shared by every suite: the sandbox and process guards, repo on sys.path,
the real-schema guard, safe settings.

First, before anything imports app code: tests run only inside the sandbox
scripts/test.sh starts (tests/safety.py). A raw `python -m pytest` stops here.

Default runs collect only tests/core. tests/legacy is the frozen pre-redesign
suite: unmaintained, never required, collected only with FUSE_LEGACY_TESTS=1
(scripts/test.sh legacy). Run it on its own, never in the same session as core.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.safety import install_process_guard, sandbox_refusal  # noqa: E402

_refusal = sandbox_refusal()
if _refusal:
    sys.stderr.write(f"\nREFUSED: {_refusal}\n")
    # SystemExit, not pytest.exit: pytest wraps exceptions raised while a
    # conftest imports (and would go on loading the others); this ends the
    # process before any other conftest or app module is imported.
    raise SystemExit(3)

import os  # noqa: E402

import pytest  # noqa: E402

from tests.mariadb_fixture import install_guard  # noqa: E402

install_guard()

# Root-level test_*.py are local, gitignored legacy leftovers (real-page
# fixtures); they belong to tests/legacy.
collect_ignore_glob = ["test_*.py"] + ([] if os.environ.get("FUSE_LEGACY_TESTS") == "1" else ["legacy/*"])


@pytest.hookimpl(trylast=True)
def pytest_configure(config):
    """No test may start a process (tests/safety.py). Installed in every
    process that runs tests: a single-process run and each xdist worker.
    The xdist controller is the one exception: it starts the workers and
    never imports a test module."""
    controller = not hasattr(config, "workerinput") and config.pluginmanager.has_plugin("dsession")
    if not controller:
        install_process_guard()


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
