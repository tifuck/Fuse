"""run_code's worker confinement fails closed.

_confine runs in the worker process; here it runs in-process against a fake
libc and fake uid calls, so nothing unshares, drops privileges or changes uid.
"""
import asyncio
import json

import pytest

from app import code_mode, code_worker


class Libc:
    def __init__(self, unshare=0, prctl=0):
        self.results = {"unshare": unshare, "prctl": prctl}
        self.calls = []

    def unshare(self, flags):
        self.calls.append("unshare")
        return self.results["unshare"]

    def prctl(self, *args):
        self.calls.append("prctl")
        return self.results["prctl"]


@pytest.fixture
def worker_os(monkeypatch):
    state = {"uid": 0}
    monkeypatch.delenv("FUSE_CODE_UNCONFINED", raising=False)
    monkeypatch.setattr(code_worker.os, "getuid", lambda: state["uid"])
    monkeypatch.setattr(code_worker.os, "geteuid", lambda: state["uid"])
    monkeypatch.setattr(code_worker.os, "setgroups", lambda groups: None)
    monkeypatch.setattr(code_worker.os, "setgid", lambda gid: None)
    monkeypatch.setattr(code_worker.os, "setuid", lambda uid: state.update(uid=uid))

    def use(libc):
        monkeypatch.setattr(code_worker.ctypes, "CDLL", lambda *a, **kw: libc)
        return libc

    state["use"] = use
    return state


def test_root_leaves_the_network_and_drops_to_nobody(worker_os):
    libc = worker_os["use"](Libc())
    assert code_worker._confine() is True
    assert libc.calls == ["unshare", "prctl"] and worker_os["uid"] == 65534


@pytest.mark.parametrize("libc", [Libc(unshare=-1), Libc(prctl=-1)])
def test_a_refused_namespace_or_no_new_privs_fails_closed(worker_os, libc):
    worker_os["use"](libc)
    assert code_worker._confine() is False
    assert worker_os["uid"] == 0  # no uid change after a refusal either


def test_an_unprivileged_server_refuses_unless_the_parent_allows_it(worker_os, monkeypatch):
    worker_os["use"](Libc())
    worker_os["uid"] = 1000
    assert code_worker._confine() is False
    monkeypatch.setenv("FUSE_CODE_UNCONFINED", "1")
    assert code_worker._confine() is True


def test_run_code_says_it_is_unavailable_when_the_sandbox_cannot_be_set_up(monkeypatch):
    """The real worker, unprivileged as the suite is, without the parent's allowance."""
    monkeypatch.setattr(code_mode, "UNCONFINED", False)

    async def runner(name, args):
        raise AssertionError("no call may run")

    text, trace = asyncio.run(code_mode.run(json.dumps({"code": "return 1", "description": "Return one"}), call=runner))
    assert trace["status"] == "error"
    assert code_worker.SANDBOX_FAILED in text
