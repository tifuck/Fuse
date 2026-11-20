"""The credential helpers pools share (app/providers/creds.py)."""
from app.providers import creds
from app.sources import kimi
from tests.core.subscriptions._support import account, user


def test_times_numbers_and_rows():
    assert creds.parse_ts("2030-01-02T03:04:05+00:00").year == 2030
    assert creds.parse_ts("2030-01-02T03:04:05+00:00", strip_offset=False) is None  # Claude's stricter reading
    assert creds.parse_ts(None) is None and creds.parse_ts("2030-01-02T03:04:05.5Z").microsecond == 500000
    assert (creds.as_float("1.5"), creds.as_float("nan"), creds.as_float(True)) == (1.5, None, None)
    assert (creds.as_int("7"), creds.positive_int("7.9"), creds.positive_int(0)) == (7, 7, None)
    assert creds.row_get({"a": None}, "a", 3) is None and creds.row_get(None, "a", 3) == 3


def test_own_access_reasons_and_key_lookup(fresh_db):
    owner = user()
    sid = kimi.ensure_kimi_source()
    assert creds.own_access(sid, owner, lambda row, extra: True) == {"ok": False, "reason": "none"}
    cid = account("kimi", owner, extra={"api_key": "sk-kimi-plan-key"}, secret="sk-kimi-plan-key")
    assert creds.own_access(sid, owner, lambda row, extra: False)["reason"] == "unauthed"
    assert creds.own_access(sid, owner, lambda row, extra: True, lambda row, extra: False)["reason"] == "no_sub"
    assert creds.own_access(sid, owner, lambda row, extra: True)["ok"]
    assert creds.find_key(sid, "sk-kimi-plan-key")["id"] == cid
    assert creds.find_key(sid, "other") is None
    assert creds.ensure_source("kimi", "ignored") == sid
