"""A failed run shows the user only text written for them; anything else
(driver, SQL, bugs) stays in the log behind a generic message."""
from unittest.mock import AsyncMock, patch

from app import chat_runtime, chats
from tests.core.helpers import quick_user, run_async, run_turn


def _failed_with(uid, exc):
    with patch.object(chat_runtime, "stream_dashboard_chat", new=AsyncMock(side_effect=exc)):
        thread_id, run = run_async(run_turn(uid, "hi"))
    assert run["status"] == "failed"
    return chats.get_thread(uid, thread_id)["messages"][-1]["error"]


def test_an_internal_error_is_logged_not_shown_and_a_user_facing_one_is_shown(fresh_db):
    uid = quick_user()
    assert _failed_with(uid, ValueError("Unported SQLite syntax: SELECT secret")) == "Generation failed."
    assert _failed_with(uid, chats.ChatValidationError("Generation frame limit reached.")) == "Generation frame limit reached."
