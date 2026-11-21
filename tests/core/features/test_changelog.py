"""What's new: a deploy shows each user the commits since their last own turn,
once; the upstream check tells admins about a newer commit and clears after."""
import pytest

from app import changelog, db, notices
from tests.core.helpers import quick_user

LOG = (
    "Release dev abc1234\x1fdef5678 Add the changelog\n0123abc Merge branch 'x' into dev\n9876fed Fix a toast\n\n"
    "Migrations production needs: none\x1e"
    "Merge branch 'y'\x1f\x1e"
    "Tidy the sidebar\x1f\x1e"
)


def run(uid, at, rid, auto=False):
    request = '{"agent": {"auto": true}}' if auto else "{}"
    db.execute("""INSERT INTO chat_runs(id,thread_id,user_id,status,request,message_id,message,created_at,updated_at)
                  VALUES(?,1,?,'completed',?,'m','{}',?,?)""", (rid, uid, request, at, at))


@pytest.fixture
def git(fresh_db, monkeypatch):
    calls = []
    answers = {}

    def fake(*args, timeout=10):
        calls.append(args)
        return answers.get(args[0])
    monkeypatch.setattr(changelog, "_git", fake)
    db.execute("DELETE FROM schema_meta WHERE key LIKE 'deployed:%'")
    for sha, at in (("old", "2026-01-01T00:00:00Z"), ("mid", "2026-02-01T00:00:00Z"), ("new", "2026-03-01T00:00:00Z")):
        db.execute("INSERT INTO schema_meta(key,value) VALUES(?,?)", ("deployed:" + sha, at))
    answers.update({"rev-parse": "new\n", "log": LOG})
    changelog.record_deploy()
    return answers, calls


def test_a_user_sees_the_commits_since_their_last_own_turn_once(git):
    _answers, calls = git
    uid = quick_user()
    run(uid, "2026-01-15T00:00:00Z", "r1")
    run(uid, "2026-02-20T00:00:00Z", "r2", auto=True)  # a wake or schedule is not the user
    changelog.announce(uid)
    [shown] = notices.list_open(uid)
    assert shown["body"] == "• Add the changelog\n• Fix a toast\n• Tidy the sidebar"
    assert ("log", "--format=%s%x1f%b%x1e", "old..new") in calls
    notices.dismiss(uid, shown["id"])
    changelog.announce(uid)
    changelog._announced.clear()
    changelog.announce(uid)
    assert notices.list_open(uid) == []


def test_no_notice_after_a_turn_since_the_deploy_or_with_no_turns(git):
    fresh, active = quick_user("fresh@core.test"), quick_user("active@core.test")
    run(active, "2026-03-02T00:00:00Z", "r1")
    changelog.announce(fresh)
    changelog.announce(active)
    assert notices.list_open(fresh) == notices.list_open(active) == []


def test_admins_hear_of_a_newer_upstream_commit_until_it_runs_here(git):
    answers, _calls = git
    admin, user = quick_user("admin@core.test"), quick_user("user@core.test")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin,))
    answers.update({"rev-parse": "origin/main\n", "ls-remote": "newer\trefs/heads/main\n"})
    assert changelog.check_upstream() == "newer"
    assert [n["key"] for n in notices.list_open(admin)] == ["update-available"]
    assert notices.list_open(user) == []
    answers["merge-base"] = ""
    assert changelog.check_upstream() is None
    assert notices.list_open(admin) == []
