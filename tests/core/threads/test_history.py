"""Canonical history: the next turn replays the provider rows that were
really sent, whatever the page re-serializes, while explicit edits and
deletions stay effective and runs never leak across accounts."""
import copy

from app import chats
from tests.core.helpers import fake_provider, make_user, run_async, run_turn, text_frames, tool_call_frames

from ._support import fake_tools

RESULT = "RESULT-7f3a: the exact tool output the model saw"
RECOVERED = "Recovered historical tool trace"


def _tool_turn(uid, thread=None):
    """One finished turn with a web_search round; returns the thread view."""
    thread = thread or chats.create_thread(uid, model="offline", tools=["web_search"])
    with fake_tools(lambda name, args: RESULT), fake_provider(
        tool_call_frames([("call_1", "web_search", {"query": "fuse"})]),
        text_frames("first answer"),
    ):
        thread_id, run = run_async(run_turn(uid, "look it up", thread=thread))
    assert run["status"] == "completed"
    return chats.get_thread(uid, thread_id)


def _next_turn(uid, thread):
    with fake_provider(text_frames("second answer")) as provider:
        run_async(run_turn(uid, "and then?", thread=chats.get_thread(uid, thread["id"])))
    return provider.bodies[0]["messages"]


def _tool_rows(sent):
    return [m for m in sent if m.get("role") == "tool"]


def test_a_page_save_keeps_the_exact_tool_round_for_replay(fresh_db):
    uid = make_user()
    thread = _tool_turn(uid)
    # The page sends back its settled, clipped view of every row.
    page = copy.deepcopy(thread["messages"])
    trace = page[-1]["tools"][0]
    trace["output"] = "RESULT-7f3a…"
    for key in ("arguments", "summary", "preview"):
        trace.pop(key, None)
    saved = chats.update_thread(uid, thread["id"], messages=page, expected_revision=thread["revision"])
    assert saved["revision"] > thread["revision"]

    sent = _next_turn(uid, thread)

    assert [m["content"] for m in _tool_rows(sent)] == [RESULT]
    assert not any(RECOVERED in str(m.get("content")) for m in sent)
    assert any(m.get("role") == "assistant" and m.get("content") == "first answer" for m in sent)


def test_deleting_a_trace_or_editing_a_prompt_is_respected(fresh_db):
    uid = make_user()
    thread = _tool_turn(uid)
    page = copy.deepcopy(thread["messages"])
    page[0]["content"] = "look it up, but politely"
    page[-1]["tools"] = []
    page[-1]["segments"] = [s for s in page[-1].get("segments") or [] if s.get("type") != "tool"]
    chats.update_thread(uid, thread["id"], messages=page, expected_revision=thread["revision"])

    sent = _next_turn(uid, thread)

    assert _tool_rows(sent) == []
    assert not any(RESULT in str(m.get("content")) for m in sent)
    users = [m["content"] for m in sent if m.get("role") == "user"]
    assert users[0].startswith("look it up, but politely")
    assert not any(u.startswith("look it up\n") or u == "look it up" for u in users)


def test_copied_message_ids_do_not_recover_another_accounts_runs(fresh_db):
    owner, other = make_user("owner@core.test"), make_user("other@core.test")
    source = _tool_turn(owner)
    # Same message ids, another account: the owner's run transcripts stay private.
    stolen = chats.create_thread(other, model="offline", tools=["web_search"], messages=source["messages"])
    assert [m["id"] for m in stolen["messages"]] == [m["id"] for m in source["messages"]]

    sent = _next_turn(other, stolen)

    assert not any(RESULT == m.get("content") for m in _tool_rows(sent))
    assert not any(RESULT in str(m.get("content")) and RECOVERED not in str(m.get("content")) for m in sent)
