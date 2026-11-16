"""A background command started by the model reaches the model exactly once.

End to end: a real durable turn (provider faked at app.api.invoke_chat) calls
run_terminal_command with background:true on the fake SSH host (recorded,
scripted output, nothing executed). Whether the job finishes while the turn
is still generating (its inbox) or after the turn ended (a wake turn), the
result is delivered once, the job row is acknowledged, and the envelope
appears once in what the model sees.
"""
import json

import pytest

from app import chats, db, hosts
from tests.core.helpers import fake_provider, run_async, run_turn, text_frames, tool_call_frames, wait_until_async
from tests.core.tools._support import make_project


def messages_text(body):
    parts = []
    for message in body["messages"]:
        content = message.get("content")
        parts.append(content if isinstance(content, str) else json.dumps(content))
    return "\n".join(parts)


@pytest.mark.parametrize("late", [False, True], ids=["during-the-turn", "after-the-turn"])
def test_a_background_command_result_is_delivered_once(ssh_host, late):
    hosts.set_settings(ssh_host.uid, agent_coding=True)
    project = make_project(ssh_host.uid, ssh_host.host_id, ssh_host.root)
    thread = chats.create_thread(ssh_host.uid, model="offline", tools=[], kind="project",
                                 host_id=ssh_host.host_id, project_id=project)
    command = "make report"
    report = ssh_host.shell.on(command, out="bg-done\n", hold=True)

    def second_round(body):
        if not late:
            report.release()  # finishes while the turn is still generating
        return text_frames("started it")
    start = tool_call_frames([("call-1", "run_terminal_command",
                               {"command": command, "background": True, "description": "Say done"})])

    def settled():
        job = db.fetchone("SELECT id, status, delivered FROM command_jobs")
        active = db.fetchone("SELECT COUNT(*) AS n FROM chat_runs WHERE status IN ('queued','running')")
        return job if job and job["delivered"] and not active["n"] else None

    async def body():
        # The second answer ends the turn; a wake turn takes the third.
        with fake_provider(start, second_round, text_frames("saw the result")) as provider:
            _, run = await run_turn(ssh_host.uid, "run it in the background", thread=thread)
            if late:
                report.release()
            job = await wait_until_async(settled, timeout=10)
        return run, job, provider.bodies

    run, job, bodies = run_async(body())

    assert run["status"] == "completed" and ssh_host.shell.reached == [command]
    assert job["status"] == "completed" and job["delivered"] == 1
    needle = f'<command_result id="{job["id"]}"'
    assert messages_text(bodies[-1]).count(needle) == 1
    assert "bg-done" in messages_text(bodies[-1])
    if late:
        assert len(bodies) == 3  # the wake turn
        assert needle not in messages_text(bodies[1])
