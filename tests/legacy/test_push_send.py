"""push._send_one: a gone subscription (404/410) is deleted, any other push
service error keeps it untouched."""
from unittest import mock

import pytest

from app import accounts, db, push

pytestmark = pytest.mark.usefixtures("isolated_db")


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code


def _subscription(endpoint):
    user = db.fetchone("SELECT id FROM users ORDER BY id") or accounts.create_user("push@test.invalid", "password12")
    push.save_subscription(user["id"], endpoint, "p256dh-key", "auth-key")
    return dict(db.fetchone("SELECT * FROM push_subscriptions WHERE endpoint=?", (endpoint,)))


def _send(row, status):
    exc = push.WebPushException("rejected", response=_Response(status))
    with mock.patch.object(push, "webpush", side_effect=exc) as webpush:
        assert push._send_one(row, "{}") is False
    webpush.assert_called_once()


@pytest.mark.parametrize("status", [404, 410])
def test_gone_subscription_is_deleted(status):
    row = _subscription(f"https://fcm.googleapis.com/fcm/send/gone-{status}")
    _send(row, status)
    assert db.fetchone("SELECT 1 FROM push_subscriptions WHERE id=?", (row["id"],)) is None


def test_server_error_keeps_the_subscription_and_its_last_push():
    row = _subscription("https://fcm.googleapis.com/fcm/send/flaky")
    db.execute("UPDATE push_subscriptions SET last_push_at='2026-11-01T00:00:00Z' WHERE id=?", (row["id"],))
    _send(row, 500)
    kept = db.fetchone("SELECT last_push_at FROM push_subscriptions WHERE id=?", (row["id"],))
    assert kept["last_push_at"] == "2026-11-01T00:00:00Z"
