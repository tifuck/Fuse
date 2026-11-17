"""Agents area fixtures: process-wide hub and work-leg state starts empty.

The connector hub keeps live sockets, held jobs, reconnect marks and cancel
replays in module globals, and work time keeps live legs; a test must never
see another test's.
"""
import pytest


def _clear():
    from app import agent_hub, chat_run_state

    agent_hub._conns.clear()
    agent_hub._held.clear()
    agent_hub._online.clear()
    agent_hub._cancel_replay.clear()
    agent_hub._dropped_at.clear()
    agent_hub._started_at = None
    agent_hub._stat_watch.clear()
    for handle in agent_hub._stat_off.values():
        handle.cancel()
    agent_hub._stat_off.clear()
    chat_run_state._live_legs.clear()


@pytest.fixture(autouse=True)
def _agents_state():
    _clear()
    yield
    _clear()
