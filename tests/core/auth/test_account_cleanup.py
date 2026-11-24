"""What deleting an account stops or removes outside its own rows: live
command jobs, skill files on disk, a leftover invite for its email."""
from app import accounts, command_jobs, db, skill_install
from tests.core.helpers import quick_user


def _job(job_id, user_id, thread_id, finished_at=None):
    db.execute(
        "INSERT INTO command_jobs (id, user_id, host_id, thread_id, command, timeout_ms, status, created_at, finished_at)"
        " VALUES (?,?,1,?,'true',1000,?,?,?)",
        (job_id, user_id, thread_id, "completed" if finished_at else "running", db.now(), finished_at),
    )


def test_deleting_an_account_stops_its_live_command_jobs(fresh_db, monkeypatch):
    admin = quick_user("root@core.test", admin=True)
    uid = quick_user("leaver@core.test")
    _job("a1", uid, 7001)
    _job("a2", uid, 7001)
    _job("a3", uid, 7002, finished_at=db.now())
    _job("a4", admin, 7003)
    cancelled = []
    monkeypatch.setattr(command_jobs, "cancel_thread", lambda user_id, thread_id: cancelled.append((user_id, thread_id)))

    accounts.delete_user(uid, actor_id=admin)

    assert cancelled == [(uid, 7001)]


def test_deleting_an_account_removes_its_skill_files_and_its_invite(fresh_db):
    admin = quick_user("root@core.test", admin=True)
    uid = quick_user("leaver@core.test")
    db.execute("INSERT INTO registration_whitelist (email, created_at, token_hash) VALUES (?,?,?)",
               ("leaver@core.test", db.now(), "0" * 64))
    (skill_install.user_dir(uid) / "demo").mkdir()
    (skill_install.user_dir(uid) / "demo" / "SKILL.md").write_text("# demo\n")
    kept = skill_install.user_dir(admin)

    accounts.delete_user(uid, actor_id=admin)

    assert not (skill_install.data_dir() / str(uid)).exists()
    assert kept.is_dir()
    assert db.fetchone("SELECT COUNT(*) AS n FROM registration_whitelist")["n"] == 0
