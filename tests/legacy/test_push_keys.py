"""VAPID keys from settings (.env) win over data/vapid.json; half a pair is ignored."""
from unittest import mock

from app import push


def test_settings_keys_win_over_file(tmp_path):
    with mock.patch.object(push.settings, "vapid_private_key", "priv"), \
            mock.patch.object(push.settings, "vapid_public_key", "pub"), \
            mock.patch.object(push, "_KEY_PATH", tmp_path / "vapid.json"):
        (tmp_path / "vapid.json").write_text('{"private_key": "file", "public_key": "file"}')
        assert push._load_keys() == {"private_key": "priv", "public_key": "pub"}


def test_half_a_pair_falls_back_to_file(tmp_path):
    with mock.patch.object(push.settings, "vapid_private_key", "priv"), \
            mock.patch.object(push.settings, "vapid_public_key", ""), \
            mock.patch.object(push, "_KEY_PATH", tmp_path / "vapid.json"):
        assert push._load_keys() is None


def test_corrupt_key_file_disables_push_and_is_never_replaced(tmp_path):
    path = tmp_path / "vapid.json"
    path.write_text('{"x":1}')
    before = path.read_bytes()
    with mock.patch.object(push.settings, "vapid_private_key", ""), \
            mock.patch.object(push.settings, "vapid_public_key", ""), \
            mock.patch.object(push, "_KEY_PATH", path), \
            mock.patch.object(push, "_keys", None), \
            mock.patch.object(push, "_generate_keys") as generate:
        assert push.vapid_public_key() == ""
        assert push.ready() is False
        generate.assert_not_called()
    assert path.read_bytes() == before
