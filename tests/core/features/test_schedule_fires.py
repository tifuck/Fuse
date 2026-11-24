"""Scheduled fires: one claim per slot under concurrent ticks, catch-up and
grace, capacity skips retried after a cooldown, the vault-locked one-shot
retry, the run id written in the run's own commit, and fire/schedule status
reconciled with the run's real outcome.

The scheduler clock is pinned in 2030 (``clock``): fire rows record the real
database time as started_at, and a pinned clock after it keeps retry
cooldowns from depending on the wall clock. Model resolution
(``app.sources.pick``) is the boundary faked here; a full fire runs a real
durable turn against ``fake_provider``.
"""
import threading
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app import chat_runs, chat_store, chats, db, schedules
from tests.core.helpers import fake_provider, quick_user, run_async, text_frames, wait_until, wait_until_async

from ._support import (add_fire, at, clock, fire_row, fires, make_host, make_project, make_schedule, set_due, stored,
                       unlock_vault, waiting_on)

T0 = "2030-03-04T06:00:00Z"   # schedules are created at this instant
SLOT = "2030-03-04T07:00:00Z"  # the first hourly slot


def picked(model=object()):
    return patch("app.sources.pick", new=AsyncMock(return_value=model))


def hourly(uid, **raw):
    with clock(T0):
        return make_schedule(uid, **raw)


def busy_scheduled_runs(uid, n):
    """n active scheduled root runs (queued; nothing executes them)."""
    runs = []
    for _ in range(n):
        thread = chats.create_thread(uid, model="offline", tools=[])
        runs.append(chat_store.start_turn(uid, thread["id"], "busy", None, None, auto=True, scheduled=True)["run"])
    return runs


# -- a full fire --------------------------------------------------------

def test_a_due_slot_runs_one_real_turn_and_the_fire_records_its_outcome(fresh_db):
    uid = quick_user()
    item = hourly(uid, name="digest", prompt="Digest for {{date}}")
    assert item["next_fire_at"] == SLOT

    async def body():
        with clock("2030-03-04T07:00:20Z"):
            await schedules.tick()
        await wait_until_async(lambda: (fire_row(item["id"], SLOT) or {}).get("status") == "completed")
        with clock("2030-03-04T07:01:00Z"):
            await schedules.tick()  # the slot is consumed: nothing fires again

    with fake_provider(text_frames("Here is the digest")) as provider, picked():
        run_async(body())

    assert len(provider.bodies) == 1
    fire = fire_row(item["id"], SLOT)
    run = chat_store.get_run(uid, fire["run_id"])
    assert run["status"] == "completed" and run["request"]["agent"]["scheduled"] is True
    row = stored(item["id"])
    assert (row["last_status"], row["last_run_id"], row["firing_at"]) == ("completed", fire["run_id"], None)
    assert row["next_fire_at"] == "2030-03-04T08:00:00Z"
    thread = chats.get_thread(uid, chats.public_id_for(row["thread_id"]))
    assert [m["role"] for m in thread["messages"]] == ["user", "assistant"]
    assert thread["messages"][0]["content"].startswith("SCHEDULED RUN")
    assert "Digest for 2030-03-04" in thread["messages"][0]["content"]
    assert thread["messages"][1]["content"] == "Here is the digest"
    assert len(fires(item["id"])) == 1


def test_concurrent_ticks_claim_a_due_slot_once(fresh_db):
    uid = quick_user()
    item = hourly(uid)
    barrier = threading.Barrier(2)
    claims, errors = [], []

    def tick_once():
        try:
            barrier.wait(5)
            claims.extend(schedules._claim_due()[0])
        except Exception as exc:  # surfaced below
            errors.append(exc)

    with clock("2030-03-04T07:00:05Z"):
        workers = [threading.Thread(target=tick_once) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(15)
        assert errors == []
        assert [fire["slot_key"] for _, fire in claims] == [SLOT]
        # The claim holds until the fire finishes: a third tick sees nothing due.
        assert schedules._claim_due()[0] == []
    assert len(fires(item["id"])) == 1
    assert stored(item["id"])["firing_at"] is not None


def test_a_slot_whose_fire_already_started_is_never_fired_again(fresh_db):
    # A crash after the fire claimed 'running' (its run may exist and resume):
    # the next tick must move past the slot, not start a second run.
    uid = quick_user()
    item = hourly(uid)
    add_fire(item, SLOT, "running")
    with clock("2030-03-04T07:00:30Z"):
        assert schedules._claim_due()[0] == []
    assert stored(item["id"])["next_fire_at"] == "2030-03-04T08:00:00Z"
    assert [f["status"] for f in fires(item["id"])] == ["running"]


def test_recover_releases_a_stale_claim_and_keeps_a_live_one(fresh_db):
    uid = quick_user()
    stale, live, mine = hourly(uid, name="stale"), hourly(uid, name="live"), hourly(uid, name="mine")
    add_fire(stale, SLOT, "running")
    db.execute("UPDATE chat_schedules SET firing_at=? WHERE id=?", ("2030-03-04T06:57:00Z", stale["id"]))
    db.execute("UPDATE chat_schedules SET firing_at=? WHERE id=?", ("2030-03-04T06:59:30Z", live["id"]))
    db.execute("UPDATE chat_schedules SET firing_at=? WHERE id=?", ("2030-03-04T06:50:00Z", mine["id"]))
    schedules._INFLIGHT[mine["id"]] = 1  # this process is still firing it
    with clock("2030-03-04T07:00:00Z"):
        schedules.recover()
    assert stored(stale["id"])["firing_at"] is None
    assert stored(stale["id"])["next_fire_at"] == "2030-03-04T08:00:00Z"  # its started slot is consumed
    assert stored(live["id"])["firing_at"] == "2030-03-04T06:59:30Z"
    assert stored(mine["id"])["firing_at"] == "2030-03-04T07:00:00Z"  # refreshed, not released


# -- catch-up and grace ---------------------------------------------------

@pytest.mark.parametrize("late, status", [(3600, "failed"), (3601, "skipped_catchup")])
def test_a_late_slot_fires_within_its_grace_and_is_skipped_after(fresh_db, late, status):
    uid = quick_user()
    item = hourly(uid)
    fire = add_fire(item, SLOT)
    with clock(at(SLOT) + timedelta(seconds=late)), \
            patch("app.sources.pick", new=AsyncMock(return_value=None)) as pick:
        run_async(schedules._fire(item, fire, consume_slot=True))
    # Inside the grace the fire goes on (and fails on the missing model);
    # past it nothing is resolved or started.
    assert fire_row(item["id"], SLOT)["status"] == status
    assert pick.await_count == (1 if status == "failed" else 0)


def test_after_an_outage_one_catchup_row_skips_every_missed_slot(fresh_db):
    uid = quick_user()
    hourly_item = hourly(uid, name="hourly")
    quarter = hourly(uid, name="quarter", recur_kind="cron", cron_expr="*/15 * * * *")
    for item in (hourly_item, quarter):
        set_due(item["id"], "2030-03-04T00:00:00Z")
    with clock("2030-03-04T02:07:00Z"), patch("app.sources.pick", new=AsyncMock()) as pick:
        run_async(schedules.tick())
        run_async(schedules.tick())
    pick.assert_not_awaited()
    assert stored(hourly_item["id"])["next_fire_at"] == "2030-03-04T03:00:00Z"  # stays on the hour grid
    assert stored(quarter["id"])["next_fire_at"] == "2030-03-04T02:15:00Z"
    for item in (hourly_item, quarter):
        assert [(f["slot_key"], f["status"]) for f in fires(item["id"])] == [("2030-03-04T00:00:00Z", "skipped_catchup")]
        assert stored(item["id"])["last_status"] == "skipped_catchup"


# -- capacity ----------------------------------------------------------------

def test_a_capacity_skip_keeps_its_slot_and_retries_after_the_cooldown(fresh_db):
    uid = quick_user()
    item = hourly(uid)
    running = busy_scheduled_runs(uid, schedules.MAX_CONCURRENT_USER)
    with clock("2030-03-04T07:00:10Z"), picked() as pick, patch.object(chat_runs, "start", new=AsyncMock()):
        run_async(schedules.tick())
    pick.assert_not_awaited()  # capacity is checked before the model is resolved
    first = fire_row(item["id"], SLOT)
    assert (first["status"], first["error"], first["run_id"]) == ("skipped_capacity", "capacity", None)
    assert stored(item["id"])["next_fire_at"] == SLOT
    db.execute("UPDATE schedule_fires SET started_at=? WHERE id=?", ("2030-03-04T07:00:10Z", first["id"]))

    for run in running:
        chat_store.finish_run(run["id"], dict(run["message"]), "completed")
    with patch.object(schedules, "_fire", new=AsyncMock()) as early, clock("2030-03-04T07:00:40Z"):
        run_async(schedules.tick())
    early.assert_not_awaited()  # still inside RETRY_COOLDOWN_S

    with clock("2030-03-04T07:01:15Z"), picked(), patch.object(chat_runs, "start", new=AsyncMock()):
        run_async(schedules.tick())
    retried = fire_row(item["id"], SLOT)
    assert (retried["id"], retried["fire_id"]) == (first["id"], first["fire_id"])
    assert retried["status"] == "running" and retried["run_id"]
    assert stored(item["id"])["next_fire_at"] == "2030-03-04T08:00:00Z"


def test_a_capacity_skip_past_its_grace_is_given_up(fresh_db):
    uid = quick_user()
    item = hourly(uid)
    skipped = dict(add_fire(item, SLOT, "skipped_capacity"))
    with clock("2030-03-04T07:30:00Z"):
        assert schedules.fire_row_retryable(skipped, item) is True
    with clock("2030-03-04T08:00:01Z"):
        assert schedules.fire_row_retryable(skipped, item) is False
    manual = {"slot_key": "manual:x", "status": "queued", "run_id": None}
    assert schedules.fire_row_retryable(manual, item) is False


# -- the vault ---------------------------------------------------------------

def test_a_vault_locked_one_shot_waits_for_the_vault_past_its_grace(fresh_db):
    uid = quick_user()
    host = make_host(uid, "ssh", auth_type="password")
    project = make_project(uid, host)
    with clock(T0):
        item = make_schedule(uid, target="project", project_id=project, recur_kind="once", once_at=SLOT)
    notify = AsyncMock()
    with patch("app.push.notify_user", new=notify), picked(), patch.object(chat_runs, "start", new=AsyncMock()):
        with clock("2030-03-04T07:00:30Z"):
            run_async(schedules.tick())
        locked = fire_row(item["id"], SLOT)
        assert (locked["status"], locked["error"]) == ("vault_locked", "Vault locked")
        row = stored(item["id"])
        assert (row["enabled"], row["next_fire_at"], row["last_status"]) == (1, SLOT, "vault_locked")

        # Seven hours later (past the 6 h one-shot grace) it is still not
        # skipped as missed: the retry keeps waiting for the vault.
        with clock("2030-03-04T14:00:00Z"):
            run_async(schedules.tick())
        assert fire_row(item["id"], SLOT)["status"] == "vault_locked"

        # Unlocked but at capacity: the retry must stay a vault-locked one
        # (retryable past the grace), not become a capacity skip that the
        # grace then gives up on.
        unlock_vault(uid)
        running = busy_scheduled_runs(uid, schedules.MAX_CONCURRENT_USER)
        with clock("2030-03-04T15:00:00Z"):
            run_async(schedules.tick())
        busy = fire_row(item["id"], SLOT)
        assert (busy["status"], busy["error"]) == ("vault_locked", "capacity")
        for run in running:
            chat_store.finish_run(run["id"], dict(run["message"]), "completed")
        with clock("2030-03-04T16:00:00Z"):
            run_async(schedules.tick())
    done = fire_row(item["id"], SLOT)
    assert done["status"] == "running" and done["run_id"]
    assert len(fires(item["id"])) == 1
    row = stored(item["id"])
    assert (row["enabled"], row["next_fire_at"]) == (0, None)  # the one-shot is spent
    # One alert for the whole locked streak, pointing at the Tasks page.
    notify.assert_awaited_once()
    assert notify.await_args.args[1]["url"] == "/tasks"


def test_a_recurring_vault_locked_fire_moves_on_to_the_next_slot(fresh_db):
    uid = quick_user()
    host = make_host(uid, "ssh", auth_type="password")
    item = hourly(uid, target="project", project_id=make_project(uid, host))
    with clock("2030-03-04T07:00:30Z"), picked(), patch("app.push.notify_user", new=AsyncMock()):
        run_async(schedules.tick())
    assert fire_row(item["id"], SLOT)["status"] == "vault_locked"
    assert stored(item["id"])["next_fire_at"] == "2030-03-04T08:00:00Z"


def test_a_project_schedule_whose_project_is_gone_fails_and_moves_on(fresh_db):
    uid = quick_user()
    host = make_host(uid)
    project = make_project(uid, host)
    item = hourly(uid, target="project", project_id=project)
    # Gone without hosts.delete_project, which pauses the schedule: as when
    # the delete commits after this fire was claimed.
    db.execute("DELETE FROM user_projects WHERE id=?", (project,))
    notify = AsyncMock()
    with clock("2030-03-04T07:00:05Z"), picked() as pick, patch("app.push.notify_user", new=notify):
        run_async(schedules.tick())
    pick.assert_not_awaited()
    fire = fire_row(item["id"], SLOT)
    assert (fire["status"], fire["error"], fire["run_id"]) == ("failed", "project_gone", None)
    assert stored(item["id"])["next_fire_at"] == "2030-03-04T08:00:00Z"
    notify.assert_awaited_once()  # a fire that never started a run says so


# -- run id and outcome --------------------------------------------------------

def test_the_fire_learns_its_run_in_the_run_commit(fresh_db):
    # A crash right after the run commits must not leave a running fire with
    # no run id: reconcile would close it while the run resumes.
    uid = quick_user()
    item = hourly(uid)
    add_fire(item, SLOT, "running")
    thread = chats.create_thread(uid, model="offline", tools=[])
    run = chat_store.start_turn(uid, thread["id"], "hi", None, None, auto=True, scheduled=True,
                                schedule_id=item["id"], schedule_slot=SLOT)["run"]
    assert fire_row(item["id"], SLOT)["run_id"] == run["id"]
    schedules.reconcile()
    assert fire_row(item["id"], SLOT)["status"] == "running"


def test_a_launch_failure_after_the_run_exists_keeps_the_fire_tracking_it(fresh_db):
    uid = quick_user()
    item = hourly(uid)
    fire = add_fire(item, SLOT)
    with clock("2030-03-04T07:00:10Z"), picked(), \
            patch.object(chat_runs, "start", new=AsyncMock(side_effect=RuntimeError("launch failed"))):
        run_async(schedules._fire(item, fire, consume_slot=True))
    row = fire_row(item["id"], SLOT)
    assert row["status"] == "running" and row["run_id"]
    # The run still executes later (resume); its real outcome lands on the fire.
    run = chat_store.get_run(uid, row["run_id"])
    chat_store.finish_run(run["id"], dict(run["message"]), "completed")
    assert fire_row(item["id"], SLOT)["status"] == "completed"
    assert stored(item["id"])["last_status"] == "completed"


@pytest.mark.parametrize("end", ["interrupted", "cancelled"])
def test_a_finished_or_aborted_run_closes_its_fire_and_schedule(fresh_db, end):
    uid = quick_user()
    item = hourly(uid)
    thread = chats.create_thread(uid, model="offline", tools=[])
    add_fire(item, SLOT, "running")
    run = chat_store.start_turn(uid, thread["id"], "hi", None, None, auto=True, scheduled=True,
                                schedule_id=item["id"], schedule_slot=SLOT)["run"]
    db.execute("UPDATE chat_schedules SET last_status='running', last_run_id=? WHERE id=?", (run["id"], item["id"]))
    if end == "cancelled":
        assert chat_store.abort_active(run["id"], "cancelled")
    else:
        chat_store.finish_run(run["id"], dict(run["message"]), "interrupted")
    fire = fire_row(item["id"], SLOT)
    assert fire["status"] == end and fire["finished_at"]
    assert stored(item["id"])["last_status"] == end


def test_reconcile_closes_fires_whose_runs_ended_without_a_sync(fresh_db):
    uid = quick_user()
    ended, lost, active, runless, waiting = (hourly(uid, name=n) for n in ("ended", "lost", "active", "runless", "waiting"))
    runs = busy_scheduled_runs(uid, 2)
    db.execute("UPDATE chat_runs SET status='interrupted' WHERE id=?", (runs[0]["id"],))  # restart recovery
    add_fire(ended, SLOT, "running", run_id=runs[0]["id"])
    add_fire(lost, SLOT, "running", run_id="run-that-vanished")
    add_fire(active, SLOT, "running", run_id=runs[1]["id"])
    add_fire(runless, SLOT, "running")
    add_fire(waiting, SLOT, "queued")
    for item, run_id in ((ended, runs[0]["id"]), (lost, "run-that-vanished"), (active, runs[1]["id"]), (runless, "")):
        db.execute("UPDATE chat_schedules SET last_status='running', last_run_id=? WHERE id=?", (run_id, item["id"]))

    schedules.reconcile()

    def state(item):
        fire = fire_row(item["id"], SLOT)
        return fire["status"], fire["error"], stored(item["id"])["last_status"]

    assert state(ended) == ("interrupted", "", "interrupted")
    assert state(lost) == ("interrupted", "run lost", "interrupted")
    assert state(active) == ("running", "", "running")
    assert state(runless) == ("interrupted", "no run started", "interrupted")
    assert fire_row(waiting["id"], SLOT)["status"] == "queued"  # still retryable


# -- edits racing a fire ---------------------------------------------------

def test_a_fire_finishing_after_an_edit_keeps_the_new_cadence(fresh_db):
    uid = quick_user()
    item = hourly(uid)
    with clock("2030-03-04T06:30:00Z"):
        patched = schedules.patch_schedule(uid, item["public_id"], {"every_seconds": 600})
    with clock("2030-03-04T07:00:05Z"):
        schedules._finish_fire(item, {"slot_key": SLOT}, "completed", "", None, "run-c", consume=True)
    row = stored(item["id"])
    assert (row["next_fire_at"], row["last_status"]) == (patched["next_fire_at"], "completed")
    # Unchanged config: the slot is consumed.
    with clock("2030-03-04T06:00:00Z"):
        other = make_schedule(uid, name="other")
    with clock("2030-03-04T07:00:05Z"):
        schedules._finish_fire(other, {"slot_key": SLOT}, "completed", "", None, "run-d", consume=True)
    assert stored(other["id"])["next_fire_at"] == "2030-03-04T08:00:00Z"


def test_a_fire_finishing_waits_for_an_uncommitted_disable(fresh_db):
    # On MariaDB the schedule read is a snapshot unless it locks: a fire
    # finishing while a PATCH disabling the schedule is still open re-enabled it.
    uid = quick_user()
    item = hourly(uid)
    done = threading.Event()
    errors = []

    def finish():
        try:
            with clock("2030-03-04T07:00:05Z"):
                schedules._finish_fire(item, {"slot_key": SLOT}, "completed", "", None, "run-c", consume=True)
        except Exception as exc:  # surfaced below
            errors.append(exc)
        finally:
            done.set()

    def blocked_on_schedule_row():
        assert not done.is_set(), "the fire finished without waiting for the open edit"
        return waiting_on("chat_schedules")

    with db.tx() as edit:
        edit.execute("BEGIN")
        edit.execute("UPDATE chat_schedules SET enabled=0, next_fire_at=NULL WHERE id=?", (item["id"],))
        worker = threading.Thread(target=finish)
        worker.start()
        wait_until(blocked_on_schedule_row, timeout=10, message="the fire never reached the schedule row")
    worker.join(15)
    assert errors == []
    row = stored(item["id"])
    assert (row["enabled"], row["next_fire_at"], row["last_status"]) == (0, None, "completed")


def test_a_schedule_deleted_mid_fire_starts_nothing(fresh_db):
    uid = quick_user()
    item = hourly(uid)
    fire = add_fire(item, SLOT)

    async def pick_then_delete(*_args, **_kwargs):
        schedules.delete_schedule(uid, item["public_id"])
        return object()

    with clock("2030-03-04T07:00:05Z"), patch("app.sources.pick", new=pick_then_delete), \
            patch.object(chat_runs, "start", new=AsyncMock()) as start:
        run_async(schedules._fire(item, fire, consume_slot=True))
    start.assert_not_awaited()
    assert db.fetchone("SELECT COUNT(*) AS n FROM chat_threads WHERE user_id=?", (uid,))["n"] == 0
    assert db.fetchone("SELECT COUNT(*) AS n FROM chat_runs WHERE user_id=?", (uid,))["n"] == 0


# -- threads, overlap, Stop, run now -------------------------------------

def test_a_continuity_schedule_reuses_its_thread_and_skips_while_it_is_busy(fresh_db):
    uid = quick_user()
    item = hourly(uid)
    with clock("2030-03-04T07:00:05Z"), picked(), patch.object(chat_runs, "start", new=AsyncMock()):
        run_async(schedules.tick())
    first = fire_row(item["id"], SLOT)
    assert first["status"] == "running"
    # The first run is still active at the next slot: skip, do not stack runs.
    with clock("2030-03-04T08:00:05Z"), picked() as pick:
        run_async(schedules.tick())
    pick.assert_not_awaited()
    second = fire_row(item["id"], "2030-03-04T08:00:00Z")
    assert (second["status"], second["error"]) == ("skipped_overlap", "already running")
    assert stored(item["id"])["next_fire_at"] == "2030-03-04T09:00:00Z"

    run = chat_store.get_run(uid, first["run_id"])
    chat_store.finish_run(run["id"], dict(run["message"]), "completed")
    with clock("2030-03-04T09:00:05Z"), picked(), patch.object(chat_runs, "start", new=AsyncMock()):
        run_async(schedules.tick())
    third = fire_row(item["id"], "2030-03-04T09:00:00Z")
    assert third["status"] == "running" and third["thread_id"] == first["thread_id"]


def test_fresh_threads_are_trimmed_to_the_cap_without_touching_other_chats(fresh_db):
    uid = quick_user()
    item = hourly(uid, fresh_each_fire=True)
    for n in range(3):
        chats.create_thread(uid, model="offline", title=f"old {n}", schedule_id=item["id"])
    mine = chats.create_thread(uid, model="offline", title="my own chat")
    with patch.object(schedules, "FRESH_CAP", 3), clock("2030-03-04T07:00:05Z"), picked(), \
            patch.object(chat_runs, "start", new=AsyncMock()):
        run_async(schedules.tick())
    left = {t["title"] for t in chats.list_threads(uid)}
    assert "old 0" not in left  # the oldest schedule thread made room
    assert {"old 1", "old 2", "my own chat"} <= left
    assert chats.get_thread(uid, mine["id"]) is not None
    assert db.fetchone("SELECT COUNT(*) AS n FROM chat_threads WHERE schedule_id=?", (item["id"],))["n"] == 3


def test_a_paused_disabled_or_deleted_schedule_admits_no_automatic_run(fresh_db):
    uid = quick_user()
    thread = chats.create_thread(uid, model="offline", tools=[])
    item = hourly(uid)

    def start(**extra):
        current = chats.get_thread(uid, thread["id"])
        return chat_store.start_turn(uid, thread["id"], "scheduled prompt", current["revision"], None,
                                     auto=True, scheduled=True, **extra)

    first = start(schedule_id=item["id"])["run"]
    chat_store.finish_run(first["id"], {"content": "done"}, "completed")
    db.execute("UPDATE chat_schedules SET paused=1 WHERE id=?", (item["id"],))
    with pytest.raises(chats.ChatValidationError) as refused:
        start(schedule_id=item["id"])
    assert refused.value.status_code == 409
    manual = start(schedule_id=item["id"], schedule_manual=True)["run"]  # Run now is explicit intent
    chat_store.finish_run(manual["id"], {"content": "done"}, "completed")
    db.execute("UPDATE chat_schedules SET enabled=0, paused=0 WHERE id=?", (item["id"],))
    with pytest.raises(chats.ChatValidationError):
        start(schedule_id=item["id"])
    schedules.delete_schedule(uid, item["public_id"])
    with pytest.raises(chats.ChatValidationError):
        start(schedule_id=item["id"], schedule_manual=True)


def test_stopping_a_chat_pauses_only_the_continuity_schedules_bound_to_it(fresh_db):
    uid = quick_user()
    thread = chats.create_thread(uid, model="offline", tools=[])
    other = chats.create_thread(uid, model="offline", tools=[])
    pk, other_pk = chats.resolve_thread_pk(uid, thread["id"]), chats.resolve_thread_pk(uid, other["id"])
    continuity, fresh, elsewhere = hourly(uid, name="c"), hourly(uid, name="f", fresh_each_fire=True), hourly(uid, name="e")
    for item, tid in ((continuity, pk), (fresh, pk), (elsewhere, other_pk)):
        db.execute("UPDATE chat_schedules SET thread_id=? WHERE id=?", (tid, item["id"]))
    run = chat_store.start_turn(uid, thread["id"], "hello", thread["revision"], None)["run"]
    assert chat_store.request_cancel(uid, run["id"])
    assert [stored(i["id"])["paused"] for i in (continuity, fresh, elsewhere)] == [1, 0, 0]


def test_run_now_fires_without_consuming_the_next_slot(fresh_db):
    uid = quick_user()
    item = hourly(uid)
    db.execute("UPDATE chat_schedules SET firing_at=? WHERE id=?", ("2030-03-04T06:30:00Z", item["id"]))
    with clock("2030-03-04T06:30:00Z"), picked(), patch.object(chat_runs, "start", new=AsyncMock()):
        after = run_async(schedules.run_now(uid, item["public_id"]))
    assert after["next_fire_at"] == SLOT
    # A concurrent scheduled claim's marker is left alone.
    assert stored(item["id"])["firing_at"] == "2030-03-04T06:30:00Z"
    [fire] = schedules.list_fires(uid, item["public_id"])
    assert (fire["trigger"], fire["status"]) == ("manual", "running") and fire["run_id"]
    with pytest.raises(schedules.ScheduleError):
        run_async(schedules.run_now(quick_user("other@core.test"), item["public_id"]))
