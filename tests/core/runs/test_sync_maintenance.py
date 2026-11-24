"""/chat/sync answers from its snapshot; the orphan reap and queued-run resume
it triggers run after the reply instead of holding it."""
import threading
from unittest.mock import AsyncMock, patch

from app import chat_runs, chats
from tests.core.auth._support import sign_in, web_client
from tests.core.helpers import make_user, wait_until


def test_sync_replies_before_its_reap_and_resume_finish(fresh_db):
    uid = make_user()
    thread = chats.create_thread(uid, model="offline", tools=[])
    release = threading.Event()
    reaped = []

    def slow_reap(user_id=None):
        release.wait(5)
        reaped.append(user_id)

    resume = AsyncMock()
    with web_client() as web, patch.object(chat_runs, "reap_orphans", slow_reap), \
            patch.object(chat_runs, "resume_queued", resume):
        sign_in(web, uid)
        try:
            got = web.get("/chat/sync")
            assert got.status_code == 200, got.text
            assert [item["id"] for item in got.json()["threads"]] == [thread["id"]]
            assert not reaped
        finally:
            release.set()
        wait_until(lambda: resume.await_count)
    assert reaped == [uid]
    resume.assert_awaited_once_with(uid)
