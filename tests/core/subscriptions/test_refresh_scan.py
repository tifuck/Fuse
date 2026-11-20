"""The chat path's token-refresh scan (refresh_due(rows)) reads and decrypts
the pool's accounts only when one of them changed or its token comes due;
the background loop (refresh_due()) always scans."""
import time

import pytest

from app.config import settings
from app.sources import chatgpt, pool_refresh
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

    # A token already inside the window stays due until it is refreshed:
    # every request retries (refresh is off here, so it never succeeds).
    set_extra(cred, _extra(60))
    assert run(chatgpt.refresh_due([row(cred)])) is True
    assert run(chatgpt.refresh_due([row(cred)])) is True
    run(chatgpt.refresh_due())
    assert scans == [1, 1, 1, 1, 1, 1]
