"""Secret protection through a real turn: the first turn of a project finds its
env file and waits for the owner; from then on the model's stand-ins run as
the real values on the machine and read as the real values to the user.

A durable turn on the fake SSH host (in-memory files, scripted shell, nothing
executed). The provider is faked at app.api.invoke_chat, above the provider
door, so its bodies are what Fuse holds; the door itself is tested in
tests/core/secrets. The "model" answers with the stand-ins, as a model would.
"""
import asyncio
import json

from app import chats, hosts, secret_guard
from tests.core.helpers import fake_provider, run_async, run_turn, text_frames, tool_call_frames, wait_until_async
from tests.core.secrets._values import GITHUB, OPENAI
from tests.core.tools._support import make_project
from tests.core.tools.test_code_mode import code_call


def project_thread(h):
    hosts.set_settings(h.uid, agent_coding=True)
    project = make_project(h.uid, h.host_id, h.root)
    thread = chats.create_thread(h.uid, model="offline", tools=[], kind="project", host_id=h.host_id, project_id=project)
    return thread, chats.resolve_thread_pk(h.uid, thread["id"])


def fake_of(uid, real):
    return next(it.fake for it in secret_guard.map_for(uid).items if it.real == real)


async def decide_all(uid, pk):
    await wait_until_async(lambda: secret_guard.waiting(uid, pk), message="the turn never waited on the offer")
    offers = secret_guard.offers(uid)
    await asyncio.to_thread(secret_guard.decide, uid, [o["id"] for o in offers])
    return offers


def test_the_first_turn_offers_the_env_file_and_waits_then_runs_with_real_values(ssh_host):
    uid = ssh_host.uid
    ssh_host.fs.write(f"{ssh_host.root}/.env", f"OPENAI_API_KEY={OPENAI}\nPORT=3000\n")
    ssh_host.fs.write(f"{ssh_host.root}/.env.example", f"GITHUB_TOKEN={GITHUB}\n")
    thread, pk = project_thread(ssh_host)
    command = f"./deploy.sh --key {OPENAI}"
    ssh_host.shell.on(command, out="deployed\n")
    sent_before_decision = []

    def first(body):
        return tool_call_frames([("call-1", "run_terminal_command", {"command": f"./deploy.sh --key {fake_of(uid, OPENAI)}"})])

    def second(body):
        return text_frames(f"Deployed with {fake_of(uid, OPENAI)}.")

    async def body():
        with fake_provider(first, second) as provider:
            turn = asyncio.ensure_future(run_turn(uid, "deploy the app", thread=thread))
            offers = await decide_all(uid, pk)
            sent_before_decision.extend(provider.bodies)
            _, run = await turn
        return run, offers, provider.bodies

    run, offers, bodies = run_async(body())

    # One row per real secret, with where it was found; the template file is not read.
    assert [(o["key"], o["value"], o["file"], o["line"], o["host"]) for o in offers] == [("OPENAI_API_KEY", OPENAI, ".env", 1, "box")]
    assert sent_before_decision == [], "a request left before the owner decided"
    assert run["status"] == "completed"
    # The machine ran the real key; the user reads real values in the tool row and the reply.
    assert ssh_host.shell.reached == [command]
    assert run["message"]["tools"][0]["command"] == command
    assert run["message"]["content"] == f"Deployed with {OPENAI}."
    # The model is told how to handle such values.
    assert "Use each exactly and whole" in bodies[0]["messages"][0]["content"]
    assert not secret_guard.waiting(uid, pk)


def test_a_later_chat_is_protected_without_asking_again(ssh_host):
    uid = ssh_host.uid
    secret_guard.add_manual(uid, "OPENAI_API_KEY", OPENAI)
    ssh_host.fs.write(f"{ssh_host.root}/.env", f"OPENAI_API_KEY={OPENAI}\n")
    thread, pk = project_thread(ssh_host)
    fake = fake_of(uid, OPENAI)

    async def body():
        with fake_provider(tool_call_frames([("call-1", "write_file", {"path": "copy.env", "content": f"KEY={fake}\n"})]),
                           tool_call_frames([("call-2", "write_file", {"path": ".env", "content": f"KEY={fake[:30]}\n"})]),
                           text_frames("done")) as provider:
            _, run = await asyncio.wait_for(run_turn(uid, "copy the key", thread=thread), 20)
        return run, provider.bodies

    run, bodies = run_async(body())

    assert run["status"] == "completed" and not secret_guard.has_offers(uid)
    assert ssh_host.fs.text(f"{ssh_host.root}/copy.env") == f"KEY={OPENAI}\n"
    # A cut stand-in is refused: the real key in .env is not overwritten.
    assert ssh_host.fs.text(f"{ssh_host.root}/.env") == f"OPENAI_API_KEY={OPENAI}\n"
    refused = [m["content"] for m in bodies[2]["messages"] if m.get("role") == "tool"][-1]
    assert "OPENAI_API_KEY is incomplete" in refused


def test_code_only_ever_holds_stand_ins(ssh_host):
    uid = ssh_host.uid
    secret_guard.add_manual(uid, "OPENAI_API_KEY", OPENAI)
    ssh_host.fs.write(f"{ssh_host.root}/.env", f"OPENAI_API_KEY={OPENAI}\n")
    thread, _pk = project_thread(ssh_host)
    fake = fake_of(uid, OPENAI)
    code = 'const t = (await read_file(".env")).trim(); const v = t.split("=")[1]; return t + "|" + v.split("").reverse().join("");'

    async def body():
        with fake_provider(code_call(code), text_frames("done")) as provider:
            _, run = await asyncio.wait_for(run_turn(uid, "check the config", thread=thread), 30)
        return run, provider.bodies

    run, bodies = run_async(body())

    assert run["status"] == "completed"
    result = [m["content"] for m in bodies[1]["messages"] if m.get("role") == "tool"][-1]
    # The whole value reads real to the user; what the code made of it came from the stand-in.
    assert f"OPENAI_API_KEY={OPENAI}|" in result
    assert fake[::-1] in result and OPENAI[::-1] not in result
    assert json.dumps(bodies).count(OPENAI[::-1]) == 0
