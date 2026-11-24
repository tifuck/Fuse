"""The chat path's token-refresh scan (refresh_due(rows)) reads and decrypts
the pool's accounts only when one of them changed or its token comes due;
the background loop (refresh_due()) always scans. A request waits only for
tokens about to expire and leaves a failed refresh alone for a while."""
import asyncio
import time

import pytest

from app.config import settings
from app.sources import chatgpt, pool_refresh
from tests.core.helpers import wait_until_async
from tests.core.subscriptions._support import account, iso, row, run, set_extra, user


@pytest.fixture
def scans(monkeypatch):
    monkeypatch.setattr(settings, "oauth_refresh_enabled", False)
    seen = []
    due_accounts = chatgpt._due_accounts

    def counted(rows):
        seen.append(len(rows))
        return due_accounts(rows)

    monkeypatch.setattr(chatgpt, "_due_accounts", counted)
    return seen


def _extra(expires_in):
    return {"access_token": "at", "refresh_token": "rt", "expires_at": iso(expires_in)}


def test_a_request_rescans_only_after_a_change_or_once_a_token_comes_due(fresh_db, monkeypatch, scans):
    # Due in 10 minutes minus the 5-minute refresh window.
    cred = account("chatgpt", user(), _extra(600))
    pool = [row(cred)]

    assert run(chatgpt.refresh_due(pool)) is False
    assert run(chatgpt.refresh_due(pool)) is False
    assert scans == [1]

    set_extra(cred, _extra(900))
    assert run(chatgpt.refresh_due([row(cred)])) is False
    assert scans == [1, 1]

    real = time.time
    monkeypatch.setattr(pool_refresh.time, "time", lambda: real() + 700)
    run(chatgpt.refresh_due([row(cred)]))
    assert scans == [1, 1, 1]

    # A token about to expire stays due until it is refreshed: every request
    # rescans, but one whose refresh failed (refresh is off here) is not
    # retried by requests for a while; the background loop still scans.
    set_extra(cred, _extra(60))
    assert run(chatgpt.refresh_due([row(cred)])) is True
    assert run(chatgpt.refresh_due([row(cred)])) is False
    run(chatgpt.refresh_due())
    assert scans == [1, 1, 1, 1, 1, 1]


def test_a_request_waits_only_for_expired_tokens_and_backs_off_after_a_failure(fresh_db, monkeypatch):
    owner = user()
    expired = account("chatgpt", owner, _extra(-60))
    soon = account("chatgpt", owner, _extra(180))
    calls, finished = [], []

    async def go():
        gate = asyncio.Event()

        async def refresh_one(cred):
            calls.append(int(cred["id"]))
            if int(cred["id"]) == soon:
                await gate.wait()
                finished.append(soon)
                return True
            return False

        monkeypatch.setattr(chatgpt, "refresh_one", refresh_one)
        pool = [row(expired), row(soon)]
        # Waits for the expired token; the one due in 3 minutes refreshes behind it.
        assert await chatgpt.refresh_due(pool) is True
        assert sorted(calls) == sorted([expired, soon]) and not finished
        # Neither a second refresh of the running one nor a retry of the failed one.
        assert await chatgpt.refresh_due(pool) is False
        assert len(calls) == 2
        gate.set()
        await wait_until_async(lambda: finished)
        # The background loop retries everything due.
        await chatgpt.refresh_due()
        assert sorted(calls[2:]) == sorted([expired, soon])

    run(go())
