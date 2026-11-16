"""Tools-area fixtures: a user with an SSH host whose transport is this machine.

``ssh_host`` gives a real user_hosts row whose working root is a scratch
directory outside /tmp (so /tmp stays a separate permission root), with
``app.ssh_pool.connection`` answering through tests.core.tools._support.LocalConn.
"""
import os
import shutil
import tempfile
from types import SimpleNamespace

import pytest

from tests.core.helpers import quick_user
from tests.core.tools._support import FakeConnector, LocalConn, make_host


@pytest.fixture(autouse=True)
def _clean_tool_state():
    """Process-wide tool state from an earlier test (jobs bound to a closed
    loop, cached SFTP clients, rate windows, a loop-bound semaphore)."""
    from app import command_jobs, jev, permissions, workspace

    def clear():
        command_jobs._live.clear()
        command_jobs._reserved.clear()
        workspace._exec_times.clear()
        workspace._host_homes.clear()
        workspace._agents_cache.clear()
        permissions._ARTIFACT_OK.clear()
        permissions._ASK_DESCRIPTIONS.clear()
        jev._gate_sem = None

    clear()
    yield
    clear()


@pytest.fixture
def scratch_dir():
    """A directory outside /tmp (a permission root of its own)."""
    base = "/dev/shm" if os.path.isdir("/dev/shm") else None
    path = os.path.realpath(tempfile.mkdtemp(prefix="fuse-core-tools-", dir=base))
    assert not path.startswith("/tmp/"), "the scratch tree must sit outside /tmp"
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def ssh_host(fresh_db, scratch_dir, monkeypatch):
    from app import ssh_pool

    root = os.path.join(scratch_dir, "proj")
    home = os.path.join(scratch_dir, "home")
    os.makedirs(root)
    os.makedirs(home)
    uid = quick_user()
    host_id = make_host(uid, "box", default_cwd=root)
    conn = LocalConn(home)

    async def connection(user_id, hid):
        assert int(user_id) == uid, "only the test user's hosts have a transport"
        return conn

    monkeypatch.setattr(ssh_pool, "connection", connection)
    return SimpleNamespace(uid=uid, host_id=host_id, alias="box", root=root, home=home,
                           base=scratch_dir, conn=conn)


@pytest.fixture
def agent_host(fresh_db, scratch_dir, monkeypatch):
    """A connector machine (auth_type agent) answered by FakeConnector."""
    from app import agent_hub

    root = os.path.join(scratch_dir, "proj")
    home = os.path.join(scratch_dir, "home")
    os.makedirs(root)
    os.makedirs(home)
    uid = quick_user()
    host_id = make_host(uid, "pc", default_cwd=root, auth_type="agent", agent_os="linux", agent_version="1.9.0")
    connector = FakeConnector(home)
    monkeypatch.setattr(agent_hub, "call", connector)
    return SimpleNamespace(uid=uid, host_id=host_id, alias="pc", root=root, home=home,
                           base=scratch_dir, connector=connector)
