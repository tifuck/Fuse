"""Boot order: the app's lifespan claims refresh ownership before the refresh
loop first runs, so a database that does not own the pooled tokens (a clone,
dev) never spends a single-use refresh token, not even in its first cycle.
Everything else the lifespan starts is stubbed; the real lifespan and the real
refresh loop run."""
from app import (
    accounts, agent_hub, changelog, chat_events, chat_recaps, chat_runs, chat_store, chat_titles, command_jobs, db,
    host_events, main, schedules, skill_install, ssh_pool, vault,
)
from app import sources as sources_module
from app.config import settings
from app.providers import logins
from app.search import client as search_client, engine as search_engine
from app.sources import http_pool, token_store
from tests.core.helpers import wait_until_async
from tests.core.subscriptions._support import run

JOBS = ("grok_version_refresh_due", "refresh_due", "chatgpt_refresh_due", "kimi_refresh_due", "claude_refresh_due",
        "cursor_refresh_due", "openrouter_refresh_due", "nanogpt_refresh_due", "refuseless_refresh_due",
        "refresh_due_catalogs", "auto_price_due")


def _stub_startup(monkeypatch, tmp_path):
    def nothing(*args, **kwargs):
        return None

    async def idle(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "ROOT", tmp_path)  # the worker lock file
    monkeypatch.setattr(settings, "static_immutable", False)
    for mod, name in ((main.static_assets, "warm"), (main.static_assets, "build_id"), (changelog, "record_deploy"),
                      (accounts, "validate_credential_encryption"), (accounts, "ensure_setup_token"),
                      (chat_store, "recover_runs"), (command_jobs, "recover"), (chat_events, "start"),
                      (host_events, "start"), (chat_store, "start_publisher"), (main, "start_pools"),
                      (main, "warm_catalogs"), (agent_hub, "mark_started"), (chat_runs, "sweep_wakes"),
                      (schedules, "recover"), (schedules, "reconcile"), (search_client, "start_client"),
                      (search_engine, "warn_if_unkeyed"), (vault, "lock_all"), (db, "close")):
        monkeypatch.setattr(mod, name, nothing)
    monkeypatch.setattr(skill_install, "sweep_partial_swaps", lambda **kwargs: [])
    for mod, name in ((main, "bootstrap"), (chat_runs, "resume_queued"), (schedules, "loop"),
                      (main, "_usage_walk_loop"), (main, "_maintenance_loop"), (chat_recaps, "loop"),
                      (chat_titles, "loop"), (main, "_update_check_loop"), (chat_events, "stop"),
                      (host_events, "stop"), (chat_titles, "drain"), (chat_recaps, "drain"), (chat_runs, "stop"),
                      (chat_store, "stop_publisher"), (agent_hub, "drain"), (ssh_pool, "drain"),
                      (search_client, "stop_client"), (http_pool, "aclose_all"), (sources_module, "stop_pools")):
        monkeypatch.setattr(mod, name, idle)
    monkeypatch.setattr(chat_runs, "_stopping", chat_runs._stopping)


def test_boot_turns_refresh_off_before_the_first_refresh_cycle(fresh_db, monkeypatch, tmp_path):
    # The fixture schema is not the one schema_meta names as token owner.
    # (The conftest turns refresh on and restores the flag the lifespan writes.)
    owner = db.fetchone("SELECT value FROM schema_meta WHERE key=?", (token_store.OWNER_KEY,))
    assert owner and owner["value"] not in settings.database_url
    assert settings.oauth_refresh_enabled is True
    _stub_startup(monkeypatch, tmp_path)
    seen = []

    async def job():
        seen.append(settings.oauth_refresh_enabled)

    for name in JOBS:
        monkeypatch.setattr(main, name, job)
    monkeypatch.setattr(logins, "sweep", job)
    monkeypatch.setattr(main, "_REFRESH_INTERVAL", 0.001)

    async def boot():
        async with main.lifespan(main.app):
            await wait_until_async(lambda: len(seen) >= len(JOBS) + 1)

    run(boot())
    assert seen and not any(seen)
