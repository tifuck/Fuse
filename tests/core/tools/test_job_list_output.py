"""The dashboard job list reads a job's retained output only for jobs this
process does not hold live, and still shows it for them."""
from app import command_jobs, db
from tests.core.helpers import quick_user
from tests.core.tools._support import new_thread


def test_a_job_held_by_no_live_worker_lists_its_stored_output(fresh_db):
    uid = quick_user()
    _, tid = new_thread(uid)
    db.execute(
        "INSERT INTO command_jobs (id,user_id,host_id,host,thread_id,command,cwd,background,timeout_ms,status,"
        "`out`,created_at,started_at) VALUES ('coldjob00001',?,1,'box',?,'make','/srv',1,60000,'running','half',?,?)",
        (uid, tid, db.now(), db.now()),
    )

    listed = command_jobs.list_thread_jobs(uid, tid)
    assert [(job["id"], job["output"]) for job in listed] == [("coldjob00001", "half")]
