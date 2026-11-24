"""OAuth re-link: signing the same account in again refreshes its row, even
when a metadata write (billing probe, refresh, guard) lands between the
writer's read of the pool rows and its update."""
import threading

import pytest

from app import accounts, db
from app.sources import claude, grok, kimi, pool_policy
from tests.core.helpers import wait_until
from tests.core.subscriptions._support import account, extra_of, jwt, user


def _waiting_on_credentials() -> bool:
    with db.tx() as conn:
        return conn.native(
            "SELECT 1 FROM information_schema.PROCESSLIST WHERE ID<>CONNECTION_ID() AND DB=DATABASE()"
            " AND COMMAND='Query' AND INFO LIKE %s",
            ("%credentials%FOR UPDATE%",),
        ).fetchone() is not None


CASES = {
    "claude": (claude, {"auth_kind": "oauth", "account_id": "acct-1"},
               lambda access: {"access_token": access, "refresh_token": "rt", "account": {"uuid": "acct-1"}}),
    "kimi": (kimi, {"auth_kind": "oauth", "account_id": "acct-1"},
             lambda access: {"access_token": access, "refresh_token": "rt", "account_id": "acct-1"}),
    "grok": (grok, {"account_id": "acct-1"},
             lambda access: {"access_token": jwt({"sub": "acct-1", "n": access}), "refresh_token": "rt"}),
}


@pytest.mark.parametrize("kind", list(CASES))
def test_a_relink_racing_a_billing_write_keeps_one_row(fresh_db, monkeypatch, kind):
    mod, old_extra, tokens = CASES[kind]
    owner = user()
    cred = account(kind, owner, {**old_extra, "access_token": "old"})
    real = accounts.cred_extra
    probes = []

    def billing_write_mid_relink(row):
        if len(probes) < 2:  # also beats one re-read-and-retry
            probe = threading.Thread(
                target=accounts.update_extra, args=(cred, {**real(row), "billing": {"plan": "max"}}), daemon=True)
            probes.append(probe)
            probe.start()
            wait_until(lambda: not probe.is_alive() or _waiting_on_credentials())
        return real(row)

    monkeypatch.setattr(accounts, "cred_extra", billing_write_mid_relink)
    fresh = tokens("new")
    got = mod.store_tokens(pool_policy.source_id(kind), fresh, owner)
    for probe in probes:
        probe.join(5)
    monkeypatch.setattr(accounts, "cred_extra", real)

    assert got == cred
    assert [r["id"] for r in db.fetchall("SELECT id FROM credentials WHERE donated_by=?", (owner,))] == [cred]
    assert extra_of(cred)["billing"] == {"plan": "max"}
    assert extra_of(cred)["access_token"] == fresh["access_token"]
