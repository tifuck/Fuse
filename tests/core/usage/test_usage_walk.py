"""Paced background usage walk: live paid accounts only, stalest first, one
account refreshed per hop, and one bad row never stalls it."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app import accounts, db
from app.sources import usage_walk
from tests.core.usage._support import grok_extra, pool_account, source, user


def _grok(checked):
    return grok_extra(10, checked_at=checked)


def _zai(checked):
    return {"api_key": "zk", "billing": {"checked_at": checked, "fetched_at": checked, "plan_type": "pro"}}


@pytest.fixture
def owner(fresh_db):
    return user()


def test_only_live_paid_accounts_are_walked_stalest_first(owner):
    newer = pool_account("grok", owner, _grok("2026-03-01T00:00:00Z"))
    older = pool_account("zai", owner, _zai("2026-01-01T05:00:00-05:00"))  # 10:00 UTC
    oldest = pool_account("zai", owner, _zai("2026-01-01T09:00:00Z"))
    never = pool_account("grok", owner, {"access_token": "t"})  # never checked: first
    pool_account("chatgpt", owner, {"access_token": "t", "billing": {"plan_type": "free", "fetched_at": "x"}})
    off = pool_account("grok", owner, _grok("2025-01-01T00:00:00Z"))
    db.execute("UPDATE credentials SET disabled=1 WHERE id=?", (off,))
    accounts.add_credential(source("openrouter"), "or", "k", extra={"key_info": {"is_free_tier": True}},
                            donated_by=owner)
    broken = pool_account("zai", owner, {"api_key": "zk"})
    db.execute("UPDATE credentials SET extra='not-a-ciphertext' WHERE id=?", (broken,))

    targets = usage_walk.usage_targets()

    assert [int(row["id"]) for _kind, row in targets] == [never, oldest, older, newer]
    assert [kind for kind, _row in targets] == ["grok", "zai", "zai", "grok"]


def test_one_hop_refreshes_only_the_stalest_account_and_survives_its_failure(owner):
    stale = pool_account("grok", owner, _grok("2026-01-01T00:00:00Z"))
    pool_account("zai", owner, _zai("2026-02-01T00:00:00Z"))

    with patch.object(usage_walk.grok_mod, "refresh_billing", new=AsyncMock()) as grok, \
            patch.object(usage_walk.zai_mod, "refresh_quota", new=AsyncMock()) as zai:
        assert asyncio.run(usage_walk.walk_next()) == f"grok:{stale}"
        grok.side_effect = RuntimeError("upstream down")
        assert asyncio.run(usage_walk.walk_next()) == f"grok:{stale}"

    assert grok.await_count == 2 and grok.await_args.kwargs == {"force": True}
    zai.assert_not_awaited()


def test_a_walk_without_accounts_does_nothing(fresh_db):
    assert asyncio.run(usage_walk.walk_next()) is None
