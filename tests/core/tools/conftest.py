"""Tools-area fixtures: a user with a fake SSH host and a fake connector host.

Both hosts are tests.core.tools._support fakes: an in-memory file system and
a shell that records commands and answers with scripted output. Nothing a
tools test does can run a command or touch a file of the real machine.
"""
from types import SimpleNamespace

import pytest

from tests.core.helpers import quick_user
from tests.core.tools._support import FakeConn, FakeConnector, FakeFS, FakeShell, make_host

ROOT = "/srv/app"
HOME = "/home/dev"


@pytest.fixture(autouse=True)
def _unprivileged_run_code(monkeypatch):
    """Tests run as an unprivileged user: run_code's worker has no uid to drop
    to and would refuse every run (app/code_worker.py _confine)."""
    from app import code_mode

    monkeypatch.setattr(code_mode, "UNCONFINED", True)


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


def _fake_machine():
    fs = FakeFS(home=HOME)
    fs.makedirs(ROOT)
    return fs, FakeShell(fs)


@pytest.fixture
def ssh_host(fresh_db, monkeypatch):
    """A user with an SSH host 'box' (working root /srv/app) on a fake machine.
    Every host of that user answers from the same fake machine."""
    from app import ssh_pool

    fs, shell = _fake_machine()
    uid = quick_user()
    host_id = make_host(uid, "box", default_cwd=ROOT)
    conn = FakeConn(fs, shell)

    async def connection(user_id, hid):
        assert int(user_id) == uid, "only the test user's hosts have a transport"
        return conn

    monkeypatch.setattr(ssh_pool, "connection", connection)
    return SimpleNamespace(uid=uid, host_id=host_id, alias="box", root=ROOT, home=HOME, fs=fs, shell=shell, conn=conn)


@pytest.fixture
def agent_host(fresh_db, monkeypatch):
    """A connector machine 'pc' (auth_type agent) answered by FakeConnector."""
    from app import agent_hub

    fs, shell = _fake_machine()
    uid = quick_user()
    host_id = make_host(uid, "pc", default_cwd=ROOT, auth_type="agent", agent_os="linux", agent_version="1.9.0")
    connector = FakeConnector(fs, shell)
    monkeypatch.setattr(agent_hub, "call", connector)
    return SimpleNamespace(uid=uid, host_id=host_id, alias="pc", root=ROOT, home=HOME, fs=fs, shell=shell,
                           connector=connector)
