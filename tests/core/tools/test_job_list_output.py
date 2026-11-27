"""The dashboard job list reads a job's retained output only for jobs this
process does not hold live, and still shows it for them."""
from app import command_jobs, db
from tests.core.auth._support import sign_in, web_client
from tests.core.helpers import make_user, quick_user
from tests.core.tools._support import new_thread


def _job(uid, tid, ident, status, out, finished=None):
    db.execute(
        "INSERT INTO command_jobs (id,user_id,host_id,host,thread_id,command,cwd,background,timeout_ms,status,"
        "`out`,out_chars,created_at,started_at,finished_at) VALUES (?,?,1,'box',?,'make','/srv',1,60000,?,?,?,?,?,?)",
        (ident, uid, tid, status, out, len(out), db.now(), db.now(), finished),
    )


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


def test_a_live_job_lists_only_the_output_past_what_the_page_holds(fresh_db):
    uid = quick_user()
    _, tid = new_thread(uid)
    _job(uid, tid, "livejob00001", "running", "")
    command_jobs._live["livejob00001"] = {**dict(db.fetchone("SELECT * FROM command_jobs WHERE id='livejob00001'")), "out": "", "out_chars": 0}
    try:
        command_jobs._append_out(command_jobs._live["livejob00001"], "x" * 10 + "new")
        [job] = command_jobs.list_thread_jobs(uid, tid, {"livejob00001": 10})
        assert (job["output"], job["output_from"], job["output_retained_chars"], job["output_chars"]) == ("new", 10, 13, 13)

        # A point the retained tail no longer covers: the whole tail.
        command_jobs._append_out(command_jobs._live["livejob00001"], "y" * command_jobs.OUT_TAIL_CHARS)
        [job] = command_jobs.list_thread_jobs(uid, tid, {"livejob00001": 10})
        assert "output_from" not in job and job["output"] == "y" * command_jobs.OUT_TAIL_CHARS
    finally:
        command_jobs._live.pop("livejob00001", None)


def test_finished_jobs_the_page_holds_are_not_sent_again(fresh_db):
    uid = make_user()
    thread, tid = new_thread(uid)
    _job(uid, tid, "donejob00001", "completed", "built", finished=db.now())

    with web_client() as web:
        sign_in(web, uid)
        first = web.get(f"/chat/threads/{thread['id']}/jobs").json()
        again = web.get(f"/chat/threads/{thread['id']}/jobs?done=donejob00001").json()

    assert [(job["id"], job["output"]) for job in first["recent"]] == [("donejob00001", "built")]
    assert "out" not in first["recent"][0]
    assert again["recent"] == []
