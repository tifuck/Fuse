"""Generation-loop guards: repeated identical calls halt the tools, a run of
failing calls drops them, a tool call cut off by the output limit is
re-asked a bounded number of times, a model that ignores tool_choice=none
gets one more text-only round, and empty replies are retried a bounded
number of times."""
from contextlib import contextmanager
from unittest.mock import patch

from app import chat_runtime, chats
from app.chat_output import Output
from tests.core.helpers import fake_provider, frame, quick_user, run_async, run_turn, text_frames, tool_call_frames

from ._support import fake_tools, tool_thread


def _raw_call_frames(call_id, name, arguments):
    """A tool-call round whose arguments string is sent exactly as given."""
    delta = {"index": 0, "id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
    return [
        frame({"choices": [{"index": 0, "delta": {"tool_calls": [delta]}}]}),
        frame({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        b"data: [DONE]\n\n",
    ]


@contextmanager
def _notices():
    """Every run notice the worker received, in order (the stored reply keeps
    only the trailing ones: later output supersedes them)."""
    seen = []
    real = Output.apply

    def apply(self, obj):
        fuse = obj.get("fuse") if isinstance(obj, dict) else None
        if isinstance(fuse, dict) and fuse.get("event") == "notice":
            seen.append(fuse.get("message"))
        return real(self, obj)

    with patch.object(Output, "apply", apply):
        yield seen


def _long_budget():
    # A search chat's round budget, raised so only the guard under test stops it.
    return patch.object(chat_runtime, "MAX_COMPLETIONS", 40)


def _last_user(body):
    return next(m["content"] for m in reversed(body["messages"]) if m.get("role") == "user")


def test_identical_calls_differing_only_in_whitespace_halt_the_tools(fresh_db):
    uid = quick_user()
    # Fewer rounds than the real 8/12 keep the test fast; the guard is the same.
    halt = 4
    spellings = ('{"query": "same", "page": 1}', '{"page":1,"query":"same"}')
    script = [_raw_call_frames(f"c{i}", "web_search", spellings[i % 2]) for i in range(halt)]
    with _long_budget(), patch.object(chat_runtime, "STATIONARITY_NUDGE", 2), \
            patch.object(chat_runtime, "STATIONARITY_HALT", halt), _notices() as notices, fake_tools(), \
            fake_provider(*script, text_frames("Here is what I found.")) as provider:
        thread_id, run = run_async(run_turn(uid, "research", thread=tool_thread(uid)))

    assert run["status"] == "completed"
    assert "Stopped after repeating the same tool calls. Answer from the evidence you have." in notices
    assert len(provider.bodies) == halt + 1
    assert _last_user(provider.bodies[2]) == chat_runtime._STATIONARITY_NUDGE
    final = provider.bodies[-1]
    assert final.get("tool_choice") == "none"
    assert any(m.get("content") == chat_runtime._STALL_WRAP for m in final["messages"])
    assert chats.get_thread(uid, thread_id)["messages"][-1]["content"] == "Here is what I found."


def test_a_run_of_failing_calls_drops_the_tools(fresh_db):
    uid = quick_user()
    fails = 5

    def explode(name, args):
        raise RuntimeError("down")

    script = [tool_call_frames([(f"c{i}", "web_search", {"query": f"q{i}"})]) for i in range(fails)]
    with _long_budget(), patch.object(chat_runtime, "STALL_FAIL_CALLS", fails), _notices() as notices, \
            fake_tools(explode), \
            fake_provider(*script, text_frames("Nothing worked.")) as provider:
        thread_id, run = run_async(run_turn(uid, "research", thread=tool_thread(uid)))

    assert run["status"] == "completed"
    assert any("failed in a row" in str(n) for n in notices)
    assert len(provider.bodies) == fails + 1
    assert all(body.get("tool_choice") != "none" for body in provider.bodies[:-1])
    assert provider.bodies[-1].get("tool_choice") == "none"
    assert chats.get_thread(uid, thread_id)["messages"][-1]["content"] == "Nothing worked."


def test_a_tool_call_cut_off_every_time_fails_after_the_salvages(fresh_db):
    uid = quick_user()
    salvages = chat_runtime.MAX_LENGTH_SALVAGES
    cut = '{"query": "a very long argu'
    script = [_raw_call_frames(f"c{i}", "web_search", cut) for i in range(salvages + 1)]
    with fake_tools() as tools, fake_provider(*script) as provider:
        thread_id, run = run_async(run_turn(uid, "go", thread=tool_thread(uid)))

    assert len(provider.bodies) == salvages + 1
    assert tools.calls == []
    assert all(_last_user(body) == chat_runtime._TOOL_CONTINUE for body in provider.bodies[1:])
    assert run["status"] == "failed"
    error = chats.get_thread(uid, thread_id)["messages"][-1]["error"]
    assert f"cut off by its output limit {salvages + 1} times" in error


def test_a_last_round_that_ignores_tool_choice_none_gets_one_text_round(fresh_db):
    uid = quick_user()
    rounds = chat_runtime.MAX_COMPLETIONS
    # Every round calls a tool, the forced last one included.
    script = [tool_call_frames([(f"c{i}", "web_search", {"query": f"q{i}"})]) for i in range(rounds)]
    with fake_tools() as tools, fake_provider(*script, text_frames("Wrapped up.")) as provider:
        thread_id, run = run_async(run_turn(uid, "research", thread=tool_thread(uid)))

    assert run["status"] == "completed"
    assert len(provider.bodies) == rounds + 1
    assert provider.bodies[rounds - 1].get("tool_choice") == "none"
    assert provider.bodies[rounds].get("tool_choice") == "none"
    # The ignored round's call still ran, and its result reached the wrap-up.
    assert len(tools.calls) == rounds
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == f"c{rounds - 1}"
               for m in provider.bodies[rounds]["messages"])
    assert chats.get_thread(uid, thread_id)["messages"][-1]["content"] == "Wrapped up."


def test_empty_replies_after_tools_are_retried_a_bounded_number_of_times(fresh_db):
    uid = quick_user()
    retries = chat_runtime.MAX_EMPTY_RETRIES
    empties = [text_frames("") for _ in range(retries + 1)]
    with fake_tools(), fake_provider(
        tool_call_frames([("c1", "web_search", {"query": "x"})]), *empties,
    ) as provider:
        thread_id, run = run_async(run_turn(uid, "search", thread=tool_thread(uid)))

    assert len(provider.bodies) == retries + 2
    assert all(_last_user(body).startswith("[Fuse] The tools already ran.") for body in provider.bodies[2:])
    # The retries are spent: the turn ends on its tool round, without another call.
    assert run["status"] == "completed"
    assert chats.get_thread(uid, thread_id)["messages"][-1]["content"] == ""


def test_round_signature_ignores_argument_whitespace_and_key_order():
    from app.chat_generation.parsing import round_signature

    def call(arguments):
        return {"function": {"name": "web_search", "arguments": arguments}}

    assert round_signature([call('{"a": 1, "b": [1, 2]}')]) == round_signature([call('{"b":[1,2],"a":1}')])
    assert round_signature([call('{"a": 1}')]) != round_signature([call('{"a": 2}')])
    # Unparsable arguments still compare as written.
    assert round_signature([call('{"a": ')]) == round_signature([call('{"a": ')])
    assert round_signature([call('{"a": ')]) != round_signature([call('{"a":')])
