"""What deleting an account stops or removes outside its own rows: live
command jobs, skill files on disk, a leftover invite for its email."""
from app import accounts, command_jobs, db, skill_install
from tests.core.helpers import quick_user


def test_deleting_an_account_stops_its_live_command_jobs(fresh_db, monkeypatch):
    admin = quick_user("root@core.test", admin=True)
    uid = quick_user("leaver@core.test")
    live = {
        "a1": {"id": "a1", "user_id": uid, "thread_id": 7001, "status": "running"},
        "a2": {"id": "a2", "user_id": uid, "thread_id": None, "status": "running"},
        "a3": {"id": "a3", "user_id": uid, "thread_id": 7002, "status": "completed"},
        "a4": {"id": "a4", "user_id": admin, "thread_id": 7003, "status": "running"},
    }
    monkeypatch.setattr(command_jobs, "_live", live)
    killed = []
    monkeypatch.setattr(command_jobs, "_kill_from_anywhere", lambda rec: killed.append(rec["id"]))

    accounts.delete_user(uid, actor_id=admin)

    assert sorted(killed) == ["a1", "a2"]


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
