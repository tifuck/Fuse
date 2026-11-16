"""Shared by every suite: the sandbox and process guards, repo on sys.path,
the real-schema guard, safe settings, data writes kept in the run dir.

First, before anything imports app code: tests run only inside the sandbox
scripts/test.sh starts, and may not start processes (tests/safety.py). A raw
`python -m pytest` stops here. tests/core/__init__.py and
tests/legacy/__init__.py repeat both, so --noconftest cannot skip them.

Default runs collect only tests/core. tests/legacy is the frozen pre-redesign
suite: unmaintained, never required, collected only with FUSE_LEGACY_TESTS=1
(scripts/test.sh legacy). Run it on its own, never in the same session as core.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.safety import allow_xdist_workers, install_process_guard, refuse_unless_sandboxed  # noqa: E402

refuse_unless_sandboxed(hard=False)
install_process_guard()

import os  # noqa: E402

import pytest  # noqa: E402

from tests.mariadb_fixture import install_guard  # noqa: E402

install_guard()

# Root-level test_*.py are local, gitignored legacy leftovers (real-page
# fixtures); they belong to tests/legacy.
collect_ignore_glob = ["test_*.py"] + ([] if os.environ.get("FUSE_LEGACY_TESTS") == "1" else ["legacy/*"])


@pytest.hookimpl(trylast=True)
def pytest_configure(config):
    """The xdist controller may start its workers (the exact execnet
    bootstrap, nothing else). It never imports a test module; workers and
    single-process runs never get this."""
    if (not hasattr(config, "workerinput") and config.getoption("dist", "no") != "no"
            and not config.getoption("collectonly")):
        allow_xdist_workers()


@pytest.fixture(autouse=True, scope="session")
def _data_writes_go_to_the_run_dir(tmp_path_factory):
    """The tree under test is read-only in the sandbox. App files that live
    under ROOT/data (push keys, setup token, skills, provider caches) go to
    this run's temp dir instead, so no test touches a real tree's data."""
    from app import accounts, push
    from app.config import settings
    from app.sources import devin_stream, grok_version, ollama, ollama_access

    root = tmp_path_factory.mktemp("root")
    data = root / "data"
    (data / "desktop-releases").mkdir(parents=True)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(push, "_KEY_PATH", data / "vapid.json")
        patch.setattr(accounts, "SETUP_TOKEN_FILE", data / "setup-token")
        patch.setattr(settings, "skills_directory", str(data / "skills"))
        patch.setattr(grok_version, "CACHE_PATH", data / "grok-client-version.json")
        patch.setattr(devin_stream, "_DEBUG_LOG", data / "devin-stream-debug.log")
        patch.setattr(ollama, "ROOT", root)
        patch.setattr(ollama_access, "ROOT", root)
        yield data


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
