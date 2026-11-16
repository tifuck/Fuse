"""The SSH connection pool: one dial per host, reuse while in use, safe eviction.

The dial itself (ssh_pool._connect_once: vault, pinning, asyncssh) is the
boundary and is replaced by a counter handing out fake connections.
"""
import asyncio
import time

import pytest

from app import ssh_pool


class Conn:
    def __init__(self, channels=()):
        self._channels = list(channels)
        self.closed = False

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def pool(monkeypatch):
    """A private pool (and loop-free locks) for each test."""
    monkeypatch.setattr(ssh_pool, "_pool", {})
    monkeypatch.setattr(ssh_pool, "_gen", {})
    monkeypatch.setattr(ssh_pool, "_key_locks", {})
    monkeypatch.setattr(ssh_pool, "_lock", asyncio.Lock())
    return ssh_pool._pool


@pytest.fixture
def dials(monkeypatch):
    made = []

    async def connect_once(user_id, host_id, *, probe=False):
        await asyncio.sleep(0)  # a real dial yields; concurrent callers interleave here
        made.append(Conn())
        return made[-1], "ssh-ed25519", "digest"

    monkeypatch.setattr(ssh_pool, "_connect_once", connect_once)
    return made


def stale():
    return time.monotonic() - ssh_pool.IDLE_CLOSE - 5


def test_concurrent_first_uses_share_one_dial(dials):
    async def body():
        return await asyncio.gather(*(ssh_pool.connection(1, 9) for _ in range(5)))

    conns = asyncio.run(body())
    assert len(dials) == 1 and all(c is dials[0] for c in conns)
    assert asyncio.run(ssh_pool.connection(1, 9)) is dials[0]
    assert asyncio.run(ssh_pool.connection(1, 10)) is not dials[0]


def test_the_reaper_closes_idle_sessions_but_never_busy_ones(pool):
    idle, busy, fresh, sftp_only = Conn(), Conn(channels=["job"]), Conn(), Conn(channels=["sftp"])
    ssh_pool.note_cached_channel(sftp_only)
    pool.update({(1, 1): {"conn": idle, "last": stale()}, (1, 2): {"conn": busy, "last": stale()},
                 (1, 3): {"conn": fresh, "last": time.monotonic()}, (1, 4): {"conn": sftp_only, "last": stale()}})

    asyncio.run(ssh_pool.reap_idle())

    assert idle.closed and (1, 1) not in pool
    assert not busy.closed and (1, 2) in pool  # a running command's channel
    assert not fresh.closed and (1, 3) in pool
    assert sftp_only.closed and (1, 4) not in pool  # the cached SFTP channel alone is not "in use"


def test_a_busy_connection_past_the_idle_window_is_reused_not_replaced(pool, dials):
    busy = Conn(channels=["running-job"])
    pool[(1, 5)] = {"conn": busy, "last": stale()}

    assert asyncio.run(ssh_pool.connection(1, 5)) is busy
    assert not busy.closed and dials == []


def test_a_connect_racing_an_eviction_is_discarded(pool, monkeypatch):
    old_key, new_key = Conn(), Conn()
    calls = []

    async def connect_once(user_id, host_id, *, probe=False):
        calls.append(1)
        if len(calls) == 1:
            ssh_pool.evict(user_id, host_id)  # the host's pin changed while this dial ran
            return old_key, "a", "d"
        return new_key, "a", "d"

    monkeypatch.setattr(ssh_pool, "_connect_once", connect_once)
    got = asyncio.run(ssh_pool.connection(1, 7))

    assert got is new_key and old_key.closed
    assert pool[(1, 7)]["conn"] is new_key
    ssh_pool.evict(1, 7)
    assert new_key.closed and (1, 7) not in pool


@pytest.mark.parametrize("address, private", [
    ("192.168.254.77", True), ("172.31.250.9", True), ("10.200.1.2", True), ("::ffff:192.168.254.77", True),
    ("fd12:3456::7", None), ("127.0.0.1", None), ("169.254.169.254", None), ("::1", None), ("0.0.0.0", None),
])
def test_hosts_on_private_or_local_addresses_are_never_dialed_by_default(monkeypatch, address, private):
    """private=True: allowed only with ALLOW_PRIVATE_HOSTS; None: never (loopback, link-local, ULA)."""
    monkeypatch.setattr(ssh_pool.settings, "allow_private_hosts", False)
    with pytest.raises(ssh_pool.SshError):
        ssh_pool.resolve_public(address, 22)

    monkeypatch.setattr(ssh_pool.settings, "allow_private_hosts", True)
    if private:
        assert ssh_pool.resolve_public(address, 22) == [address]
    else:
        with pytest.raises(ssh_pool.SshError):
            ssh_pool.resolve_public(address, 22)


def test_a_public_address_is_dialable():
    assert ssh_pool.resolve_public("93.184.216.34", 22) == ["93.184.216.34"]
