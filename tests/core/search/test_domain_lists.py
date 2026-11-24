"""Fetch domain lists: a read that raced a save never caches the lists it read
before the save."""
from app import db
from app.search import policy


def test_a_read_racing_a_save_does_not_cache_the_old_lists(fresh_db, monkeypatch):
    policy.save_domain_lists([], [])
    real_fetchall = db.fetchall

    def save_lands_after_the_read(sql, args=()):
        rows = real_fetchall(sql, args)
        monkeypatch.setattr(db, "fetchall", real_fetchall)
        policy.save_domain_lists(["blocked.example"], [])
        return rows

    monkeypatch.setattr(db, "fetchall", save_lands_after_the_read)
    assert policy.domain_lists() == ([], [])
    assert policy.domain_lists() == (["blocked.example"], [])
