"""Manual compaction: a checkpoint applies once, the next turn sends the
summary plus the rows after it, edits before the boundary drop it; and the
context meter's arithmetic."""
from unittest.mock import patch

import pytest

from app import chat_store, chats, context_compact, context_meter
from app.chats import ChatValidationError
from app.config import settings
from tests.core.helpers import fake_provider, make_user, run_async, run_turn, text_frames

OLD = "the original long discussion about the parser rewrite"


def _long_chat(uid):
    rows = []
    for n in range(4):
        rows += [{"role": "user", "content": f"{OLD} part {n}\n" + "detail " * 300},
                 {"role": "assistant", "content": f"answer {n}"}]
    return chats.create_thread(uid, model="offline", messages=rows)


def _compact(uid, thread, through, command_id="cmp-1", summary="SUMMARY: parser rewrite agreed"):
    prepared = chat_store.prepare_compact(uid, thread["id"], thread["revision"], command_id)
    checkpoint = context_compact.make_checkpoint(summary=summary, through_message_id=through["id"],
                                                 strategy="summarize", trigger="manual")
    return prepared, chat_store.finish_compact(uid, thread["id"], checkpoint, command_id=command_id,
                                               fingerprint=prepared["fingerprint"], expected_revision=thread["revision"])


def _sent_after_compaction(uid, thread_id):
    with fake_provider(text_frames("ok")) as provider:
        run_async(run_turn(uid, "carry on", thread=chats.get_thread(uid, thread_id)))
    return [(m["role"], str(m["content"])) for m in provider.bodies[0]["messages"] if m["role"] in ("user", "assistant")]


def test_the_next_turn_sends_the_summary_and_only_later_rows(fresh_db):
    uid = make_user()
    thread = _long_chat(uid)
    before = thread["compaction"]["meter"]["used"]
    _, view = _compact(uid, thread, through=thread["messages"][5])

    assert len(view["compaction"]["checkpoints"]) == 1
    assert view["compaction"]["meter"]["used"] < before
    sent = _sent_after_compaction(uid, thread["id"])
    texts = "\n".join(text for _, text in sent)
    assert "SUMMARY: parser rewrite agreed" in texts
    assert f"{OLD} part 0" not in texts and "answer 2" not in texts
    assert f"{OLD} part 3" in texts and "answer 3" in texts
    assert sent[-1][0] == "user" and sent[-1][1].startswith("carry on")
    # The transcript itself is never cut: every row is still there to read.
    assert len(chats.get_thread(uid, thread["id"])["messages"]) == 10


def test_a_compaction_applies_once(fresh_db):
    uid = make_user()
    thread = _long_chat(uid)
    _, view = _compact(uid, thread, through=thread["messages"][5])

    # The client retries the command after a lost response.
    replay = chat_store.prepare_compact(uid, thread["id"], thread["revision"], "cmp-1")
    assert replay["compacted"] is True and replay["thread"]["revision"] == view["revision"]
    # A second compaction derived from the same revision conflicts.
    checkpoint = context_compact.make_checkpoint(summary="again", through_message_id=thread["messages"][5]["id"],
                                                 strategy="summarize", trigger="manual")
    with pytest.raises(ChatValidationError) as refused:
        chat_store.finish_compact(uid, thread["id"], checkpoint, command_id=None, fingerprint="x",
                                  expected_revision=thread["revision"])
    assert refused.value.status_code == 409
    assert len(chats.get_thread(uid, thread["id"])["compaction"]["checkpoints"]) == 1


def test_compaction_waits_for_a_running_turn(fresh_db):
    uid = make_user()
    thread = _long_chat(uid)
    current = chat_store.start_turn(uid, thread["id"], "busy", thread["revision"], None)["thread"]

    with pytest.raises(ChatValidationError) as refused:
        chat_store.prepare_compact(uid, thread["id"], current["revision"], None)
    assert refused.value.status_code == 409


def test_editing_before_the_boundary_drops_the_summary_and_after_keeps_it(fresh_db):
    uid = make_user()
    thread = _long_chat(uid)
    _, view = _compact(uid, thread, through=thread["messages"][5])

    kept = [dict(m) for m in view["messages"]]
    kept[7] = {**kept[7], "content": "answer 3, revised"}
    view = chats.update_thread(uid, thread["id"], messages=kept, expected_revision=view["revision"])
    assert len(view["compaction"]["checkpoints"]) == 1

    edited = [dict(m) for m in view["messages"]]
    edited[0] = {**edited[0], "content": "a different opening"}
    view = chats.update_thread(uid, thread["id"], messages=edited, expected_revision=view["revision"])
    assert view["compaction"]["checkpoints"] == []
    texts = "\n".join(text for _, text in _sent_after_compaction(uid, thread["id"]))
    assert "SUMMARY" not in texts and "a different opening" in texts and "answer 2" in texts


@pytest.fixture
def meter_settings():
    with patch.multiple(settings, context_warn_ratio=0.8, context_auto_ratio=0.9, context_auto_compact=True,
                        context_output_reserve=16_384, context_default_window=128_000):
        yield


def test_meter_thresholds(meter_settings):
    low = context_meter.meter(used=79_999, limit=100_000)
    assert (low["ratio"], low["warn"], low["auto"]) == (0.8, False, False)
    assert context_meter.meter(used=80_000, limit=100_000)["warn"] is True
    assert context_meter.trigger_used(100_000) == 90_000
    assert context_meter.trigger_used(1_000_000) == 900_000
    assert context_meter.should_compact(899_999, 1_000_000) is False
    assert context_meter.should_compact(89_999, 100_000) is False
    assert context_meter.should_compact(90_000, 100_000) is True
    # A small window keeps room for the compaction call itself.
    assert context_meter.trigger_used(4_000) == 3_600
    # An estimate may warn but never compacts unless the last real reading was over.
    assert context_meter.should_auto_compact(95_000, 100_000, estimated=True, prior_used=10_000) is False
    assert context_meter.should_auto_compact(95_000, 100_000, estimated=True, prior_used=91_000) is True
    assert context_meter.should_auto_compact(95_000, 100_000) is True


def test_token_estimates(meter_settings):
    assert context_meter.estimate_text("abcd" * 25) == 25
    assert context_meter.estimate_text("你好世界") == 4
    # Non-Latin scripts are not counted at Latin's 4 chars per token.
    assert context_meter.estimate_text("สวัสดี") == 6
    assert context_meter.estimate_text("नमस्ते") == 6
    assert context_meter.estimate_text("Привет") == 3
    assert context_meter.estimate_text("שלום") == 2
    image = [{"type": "text", "text": "abcd"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
    assert context_meter.estimate_content(image) == 1 + context_meter.IMAGE_TOKENS
    rows = [{"role": "user", "content": "abcd" * 10},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "grep", "arguments": "{}"}}]},
            {"role": "tool", "content": "x" * 400}]
    assert context_meter.estimate_messages(rows) == 3 * context_meter.ROLE_OVERHEAD + 10 + 2 + 100
    # One request's size, not the turn's summed billing.
    assert context_meter.used_from_usage({"prompt_tokens": 900, "completion_tokens": 100, "total_tokens": 9_000}) == 1_000


def test_standing_meter_counts_retained_tool_results(meter_settings):
    rows = [{"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c", "function": {"name": "grep", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c", "content": "y" * 40_000}]
    with_tools = context_meter.standing_meter(rows, model="offline", override=100_000)
    without = context_meter.standing_meter(rows[:1], model="offline", override=100_000)
    assert with_tools["used"] - without["used"] >= 10_000
    assert with_tools["limit"] == 100_000 and with_tools["estimated"] is True
