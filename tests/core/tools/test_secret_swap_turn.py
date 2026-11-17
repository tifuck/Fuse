"""A chat that swaps secrets, end to end: the model only ever reads stand-ins,
the user's machine runs the real values, and the user sees the real values.

A real durable turn (provider faked at app.api.invoke_chat) on the fake SSH
host (recorded, scripted output, nothing executed). The model echoes the
stand-ins it was given, as a model would.
"""
import json
import re

from app import chats, hosts
from tests.core.helpers import fake_provider, run_async, run_turn, text_frames, tool_call_frames
from tests.core.tools._support import make_project

OPENAI = "sk-proj-" + "Q7v2Lp9X" * 6
GITHUB = "ghp_" + "R4m8Tz2Kc9" * 3 + "W1x5Yb"


def sent(body):
    return json.dumps(body["messages"])


def test_a_swapping_chat_runs_and_shows_real_values_the_model_never_reads(ssh_host):
    uid = ssh_host.uid
    hosts.set_settings(uid, agent_coding=True)
    chats.set_user_context(uid, secret_swap=True)
    project = make_project(uid, ssh_host.host_id, ssh_host.root)
    thread = chats.create_thread(uid, model="offline", tools=[], kind="project",
                                 host_id=ssh_host.host_id, project_id=project)
    command = f"./deploy.sh --key {OPENAI}"
    ssh_host.shell.on(command, out=f"deployed; ci token {GITHUB}\n")
    model = {}

    def first(body):
        model["key"] = re.search(r"sk-proj-[A-Za-z0-9_-]{48}", sent(body)).group(0)
        return tool_call_frames([("call-1", "run_terminal_command", {"command": f"./deploy.sh --key {model['key']}"})])

    def second(body):
        model["gh"] = re.search(r"ghp_[A-Za-z0-9]{36}", sent(body)).group(0)
        return text_frames(f"Deployed with {model['key']}. CI token: {model['gh']}")

    async def body():
        with fake_provider(first, second, text_frames("noted")) as provider:
            _, run = await run_turn(uid, f"deploy with OPENAI_API_KEY={OPENAI}", thread=thread)
            _, later = await run_turn(uid, "thanks", thread=chats.get_thread(uid, thread["id"]))
        return run, later, provider.bodies

    run, later, bodies = run_async(body())

    assert run["status"] == later["status"] == "completed"
    assert model["key"] != OPENAI and model["gh"] != GITHUB
    for request in bodies:
        assert OPENAI not in sent(request) and GITHUB not in sent(request)
    # The machine got the real key; the user sees real values everywhere.
    assert ssh_host.shell.reached == [command]
    assert run["message"]["tools"][0]["command"] == command
    assert run["message"]["content"] == f"Deployed with {OPENAI}. CI token: {GITHUB}"
    # The model is asked to write values whole (they map back only then); the
    # prompt says nothing of a swap.
    system = bodies[0]["messages"][0]["content"]
    assert "write it in full, exactly as it appears" in system
    assert "Never print full secret values" not in system and "swap" not in system.lower()
    # The next turn replays the reply once, as the model wrote it.
    assert sent(bodies[2]).count(f"Deployed with {model['key']}. CI token: {model['gh']}") == 1
