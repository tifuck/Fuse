"""Context-meter saves run beside the turn: a slow save never holds the model
request, and every save lands while the run is still live."""
import threading
from unittest.mock import patch

from app import chat_store, db
from tests.core.helpers import fake_provider, quick_user, run_async, run_turn, text_frames


def test_a_slow_meter_save_does_not_hold_the_request_and_all_land_before_the_run_settles(fresh_db):
    uid = quick_user()
    asked = threading.Event()
    held, statuses = [], []
    real = chat_store.save_meter

    def slow_save(user_id, thread_id, meter):
        if not statuses:
            # The turn's first meter save waits until the provider has the request.
            held.append(asked.wait(5))
        statuses.append(db.fetchone("SELECT status FROM chat_runs WHERE id=?", (meter["run_id"],))["status"])
        real(user_id, thread_id, meter)

    def answer(body):
        asked.set()
        return text_frames("done")

    with fake_provider(answer), patch.object(chat_store, "save_meter", side_effect=slow_save):
        _, run = run_async(run_turn(uid, "hi"))

    assert run["status"] == "completed"
    assert held == [True]
    assert statuses and set(statuses) == {"running"}
