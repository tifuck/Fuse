"""Web Push: only browser push services are stored, a subscription stays with
its account, gone subscriptions are pruned (404/410) and others kept, VAPID
keys are never regenerated over an existing file, payloads are redacted, and
a finished turn previews its final text block."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app import chats, db, push
from tests.core.helpers import fake_provider, frame, quick_user, run_async, run_turn, text_frames

FCM = "https://fcm.googleapis.com/fcm/send/"


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code


def subscribe(uid, name, key="k"):
    push.save_subscription(uid, FCM + name, key, "auth")
    return dict(db.fetchone("SELECT * FROM push_subscriptions WHERE endpoint=?", (FCM + name,)))


@pytest.fixture
def keys(tmp_path, monkeypatch):
    """No .env keys and an empty data dir: data/vapid.json lives in tmp_path."""
    monkeypatch.setattr(push.settings, "vapid_private_key", "")
    monkeypatch.setattr(push.settings, "vapid_public_key", "")
    monkeypatch.setattr(push, "_KEY_PATH", tmp_path / "vapid.json")
    return tmp_path / "vapid.json"


def test_only_browser_push_services_are_accepted():
    good = (FCM + "abc", "https://updates.push.services.mozilla.com/push/v1/x",
            "https://web.push.apple.com/x", "https://db5p.notify.windows.com/?token=t")
    bad = ("http://fcm.googleapis.com/x", "https://evil.test/push", "https://fcm.googleapis.com.evil.test/x",
           "https://fcm.googleapis.com:8443/x", "https://127.0.0.1/x", "https://evil.test@fcm.googleapis.com.evil.test/x",
           "ftp://fcm.googleapis.com/x", None, "https://" + "a" * 2050 + ".push.apple.com/")
    assert all(push.valid_endpoint(e) for e in good)
    assert not any(push.valid_endpoint(e) for e in bad)


# requests sends 'https://10.0.0.206:11435\\@fcm.googleapis.com/x' to
# 10.0.0.206 though urlsplit names fcm.googleapis.com as its host.
SMUGGLED = ("https://10.0.0.206:11435\\@fcm.googleapis.com/x", "https://evil.test\\@fcm.googleapis.com/",
            "https://u@fcm.googleapis.com/x", "https://fcm.googleapis.com/x y", "https://fcm.googleapis.com/x\ty",
            "https://fcm.googleapis.com /x", "https://\tfcm.googleapis.com/x", "https://fcm.googleapis.com/x\n")


def test_endpoints_parsers_could_read_differently_are_refused(fresh_db):
    uid = quick_user()
    for endpoint in SMUGGLED:
        assert not push.valid_endpoint(endpoint), endpoint
        with pytest.raises(ValueError):
            push.save_subscription(uid, endpoint, "k", "a")
    assert push.count_for(uid) == 0
    assert push.valid_endpoint(FCM + "abc:APA91b-x_y")
    assert push.valid_endpoint("https://updates.push.services.mozilla.com/wpush/v2/gAAAAAB")


def test_a_stored_smuggled_endpoint_is_never_posted_to():
    with patch.object(push, "webpush") as send:
        assert push._send_one({"id": 1, "endpoint": SMUGGLED[0], "p256dh": "k", "auth": "a"}, "{}") is False
    send.assert_not_called()


def test_a_subscription_stays_with_its_account(fresh_db):
    owner, other = quick_user(), quick_user("other@core.test")
    with pytest.raises(ValueError):
        push.save_subscription(owner, "https://evil.test/push", "k", "a")
    subscribe(owner, "take", "k1")
    with pytest.raises(ValueError):
        push.save_subscription(other, FCM + "take", "k2", "a2")
    assert push.count_for(other) == 0
    subscribe(owner, "take", "k3")  # the owner may refresh its keys
    assert [(r["endpoint"], r["p256dh"]) for r in push.subscriptions_for(owner)] == [(FCM + "take", "k3")]


def test_the_newest_subscriptions_are_kept_at_the_cap(fresh_db):
    uid = quick_user()
    with patch.object(push, "MAX_SUBSCRIPTIONS", 3):
        for n in range(5):
            subscribe(uid, f"cap{n}")
    assert {r["endpoint"] for r in push.subscriptions_for(uid)} == {FCM + "cap2", FCM + "cap3", FCM + "cap4"}


def test_a_password_change_drops_every_other_device(fresh_db):
    uid, other = quick_user(), quick_user("other@core.test")
    for name in ("phone", "laptop", "tablet"):
        subscribe(uid, name)
    subscribe(other, "theirs")
    push.remove_others(uid, keep=FCM + "laptop")
    assert [r["endpoint"] for r in push.subscriptions_for(uid)] == [FCM + "laptop"]
    assert push.count_for(other) == 1


@pytest.mark.parametrize("status, kept", [(404, False), (410, False), (500, True), (429, True)])
def test_a_gone_subscription_is_pruned_and_a_failing_one_kept(fresh_db, status, kept):
    uid = quick_user()
    row = subscribe(uid, "device")
    db.execute("UPDATE push_subscriptions SET last_push_at='2026-11-01T00:00:00Z' WHERE id=?", (row["id"],))
    error = push.WebPushException("rejected", response=_Response(status))
    with patch.object(push, "webpush", side_effect=error):
        assert push._send_one(row, "{}") is False
    after = db.fetchone("SELECT last_push_at FROM push_subscriptions WHERE id=?", (row["id"],))
    assert (after is not None) is kept
    if kept:
        assert after["last_push_at"] == "2026-11-01T00:00:00Z"
    with patch.object(push, "webpush") as ok:
        if kept:
            assert push._send_one(row, "{}") is True
            assert db.fetchone("SELECT last_push_at FROM push_subscriptions WHERE id=?", (row["id"],))["last_push_at"] != "2026-11-01T00:00:00Z"
            assert ok.call_args.kwargs["timeout"]  # a stalled endpoint cannot pin a worker forever


def test_keys_are_generated_once_and_reused(keys):
    first = push.vapid_public_key()
    assert first and json.loads(keys.read_text())["public_key"] == first
    on_disk = keys.read_bytes()
    push._keys = None  # a restart
    with patch.object(push, "_generate_keys") as generate:
        assert push.vapid_public_key() == first
    generate.assert_not_called()
    assert keys.read_bytes() == on_disk


def test_settings_keys_win_and_half_a_pair_is_ignored(keys, monkeypatch):
    keys.write_text('{"private_key": "file", "public_key": "file-pub"}')
    monkeypatch.setattr(push.settings, "vapid_private_key", "priv")
    monkeypatch.setattr(push.settings, "vapid_public_key", "pub")
    assert push._load_keys() == {"private_key": "priv", "public_key": "pub"}
    monkeypatch.setattr(push.settings, "vapid_public_key", "")
    assert push._load_keys()["public_key"] == "file-pub"


def test_a_corrupt_key_file_disables_push_and_is_never_replaced(keys, monkeypatch):
    keys.write_text('{"x":1}')
    before = keys.read_bytes()
    monkeypatch.setattr(push.settings, "public_base_url", "https://fuse.example.test")
    with patch.object(push, "_generate_keys") as generate:
        assert push.vapid_public_key() == ""
        assert push.ready() is False
    generate.assert_not_called()
    assert keys.read_bytes() == before


def test_push_needs_a_contact_address(keys, monkeypatch):
    push.vapid_public_key()
    monkeypatch.setattr(push.settings, "public_base_url", "")
    assert push.ready() is False
    monkeypatch.setattr(push.settings, "public_base_url", "https://fuse.example.test")
    assert push.ready() is (push.HAVE_PYWEBPUSH is True)


def test_payloads_are_redacted_and_storage_errors_never_escape(fresh_db):
    uid = quick_user()
    thread = chats.create_thread(uid, model="offline", title="deploy with password=hunter2")
    pk = chats.resolve_thread_pk(uid, thread["id"])
    subscribe(uid, "phone")
    sent = []
    with patch.object(push, "ready", return_value=True), \
            patch.object(push, "_send_one", side_effect=lambda row, payload: sent.append(json.loads(payload)) or True):
        content = "Done. Key sk-abc123def456ghi789jkl and ghp_0123456789abcdef0123456789abcdef0123 rotated."
        assert run_async(push.notify_run_done(uid, pk, "completed", content=content)) == 1
        assert run_async(push.notify_run_done(uid, pk, "failed", error="Bearer abcdefghijklmnopqrstuvwxyz")) == 1
        assert run_async(push.notify_run_done(uid, 10 ** 9, "completed", content="gone")) == 0
    text = json.dumps(sent)
    for secret in ("hunter2", "sk-abc123def456ghi789jkl", "ghp_0123456789abcdef", "abcdefghijklmnopqrstuvwxyz"):
        assert secret not in text
    assert sent[0]["url"] == f"/chat?t={thread['id']}" and sent[0]["status"] == "completed"
    assert sent[1]["status"] == "failed"
    with patch.object(push, "ready", return_value=True), \
            patch.object(push, "subscriptions_for", side_effect=RuntimeError("pool exhausted")):
        assert run_async(push.notify_user(uid, {"title": "t"})) == 0
    with patch.object(push, "ready", return_value=False), patch.object(push, "_send_one") as send:
        assert run_async(push.notify_user(uid, {"title": "t"})) == 0
    send.assert_not_called()


def test_a_finished_turn_previews_its_final_text_block(fresh_db):
    uid = quick_user()
    call = {"index": 0, "id": "c1", "type": "function", "function": {"name": "no_such_tool", "arguments": "{}"}}
    remark_then_call = [
        frame({"choices": [{"index": 0, "delta": {"content": "Let me look first."}}]}),
        frame({"choices": [{"index": 0, "delta": {"tool_calls": [call]}}]}),
        frame({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        b"data: [DONE]\n\n",
    ]
    notify = AsyncMock(return_value=1)
    with patch("app.push.notify_run_done", new=notify), \
            fake_provider(remark_then_call, text_frames("Final answer here.")):
        thread_id, run = run_async(run_turn(uid, "go"))
    assert run["status"] == "completed"
    notify.assert_awaited_once()
    assert notify.await_args.args[2] == "completed"
    content = notify.await_args.kwargs["content"]
    assert "Final answer here." in content and "Let me look first." not in content
