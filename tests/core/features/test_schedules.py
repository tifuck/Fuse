"""Scheduled chats as configuration: validation and interval floors, builder
presets, variables, limits, owner isolation, edits that must not move the
next run, and what a paused or deleted schedule leaves behind."""
from datetime import timedelta
from unittest.mock import patch

import pytest

from app import chats, db, schedules
from app.schedules import ScheduleError
from tests.core.helpers import quick_user

from ._support import clock, iso_utc, make_host, make_project, make_schedule, stored


@pytest.mark.parametrize("raw, why", [
    ({"recur_kind": "cron", "cron_expr": "* * * * *"}, "frequent"),
    ({"every_seconds": 120}, "unsupported"),
    ({"every_seconds": 300, "allow_coding": True}, "frequent"),
    ({"recur_kind": "cron", "cron_expr": "*/5 * * * *", "allow_coding": True}, "frequent"),
    ({"every_seconds": [300]}, "integer"),
    ({"enabled": "false"}, "true or false"),
    ({"prompt": "   "}, "prompt"),
    ({"model": ""}, "model"),
    ({"timezone": "Mars/Olympus"}, "timezone"),
    ({"recur_kind": "monitor"}, "recur_kind"),
    ({"recur_kind": "once"}, "once_at"),
    ({"recur_kind": "once", "once_at": "tomorrow"}, "once_at"),
    ({"prompt": "hi {{nope}}"}, "unknown variable"),
    ({"prompt": "hi {{api_key}}", "variables": {"api_key": "x"}}, "reserved"),
    ({"variables": {"date": "x"}}, "reserved"),
    ({"variables": {"Bad": "x"}}, "invalid variable"),
    ({"variables": {"note": "x" * 201}}, "invalid variable"),
])
def test_invalid_schedules_are_refused_and_nothing_is_stored(fresh_db, raw, why):
    uid = quick_user()
    with pytest.raises((ScheduleError, ValueError)) as refused:
        make_schedule(uid, **raw)
    assert why in str(getattr(refused.value, "detail", refused.value))
    assert schedules.list_schedules(uid) == []


def test_search_chats_may_run_every_five_minutes_but_coding_needs_fifteen(fresh_db):
    uid = quick_user()
    assert make_schedule(uid, every_seconds=300)["every_seconds"] == 300
    assert make_schedule(uid, name="code", every_seconds=900, allow_coding=True)["allow_coding"] is True
    host = make_host(uid)
    project = make_project(uid, host)
    # A project target always codes, so it gets the coding floor too.
    with pytest.raises(ScheduleError):
        make_schedule(uid, name="proj", target="project", project_id=project, every_seconds=300)
    made = make_schedule(uid, name="proj", target="project", project_id=project, every_seconds=900, allow_coding=False)
    assert made["allow_coding"] is True
    other = quick_user("other@core.test")
    with pytest.raises(ScheduleError) as refused:
        make_schedule(other, target="project", project_id=project)
    assert refused.value.status_code == 404


def test_builder_presets_store_cron_and_a_custom_cron_drops_them(fresh_db):
    uid = quick_user()
    with clock("2026-10-20T06:00:00Z"):
        daily = make_schedule(uid, recur_kind="cron", builder="daily", at_local="09:30", timezone="Europe/Berlin")
        weekly = make_schedule(uid, name="weekly", recur_kind="cron", builder="weekly", at_local="07:00",
                               weekdays=0b0010001)  # Monday and Friday
    assert (daily["cron_expr"], daily["weekdays"]) == ("30 9 * * *", 0)
    assert daily["next_fire_at"] == "2026-10-20T07:30:00Z"  # 09:30 CEST
    assert weekly["cron_expr"] == "0 7 * * 1,5"
    assert weekly["next_fire_at"] == "2026-10-23T07:00:00Z"  # the Friday
    custom = schedules.patch_schedule(uid, weekly["public_id"], {"cron_expr": "15 8 * * *"})
    assert (custom["cron_expr"], custom["weekdays"], custom["at_local"]) == ("15 8 * * *", 0, "")
    # A later unrelated edit keeps the custom expression (no builder re-inferred).
    renamed = schedules.patch_schedule(uid, weekly["public_id"], {"name": "renamed"})
    assert renamed["cron_expr"] == "15 8 * * *"


def test_renaming_keeps_the_next_run_but_a_new_cadence_recomputes_it(fresh_db):
    uid = quick_user()
    with clock("2026-10-20T06:00:00Z"):
        item = make_schedule(uid)
    assert item["next_fire_at"] == "2026-10-20T07:00:00Z"
    with clock("2026-10-20T06:50:00Z"):
        renamed = schedules.patch_schedule(uid, item["public_id"], {"name": "new name", "prompt": "other"})
        assert renamed["next_fire_at"] == "2026-10-20T07:00:00Z"
        faster = schedules.patch_schedule(uid, item["public_id"], {"every_seconds": 1800})
        assert faster["next_fire_at"] == "2026-10-20T07:20:00Z"


def test_an_edit_that_keeps_the_cadence_is_not_refused_by_the_interval_floor(fresh_db):
    uid = quick_user()
    item = make_schedule(uid, recur_kind="cron", cron_expr="0 9 * * *", allow_coding=True)
    # Saved under older rules: 01:55 EST to 03:00 EDT is five minutes.
    db.execute("UPDATE chat_schedules SET cron_expr=?, timezone=? WHERE id=?",
               ("0,55 1,3 * * *", "America/New_York", item["id"]))
    assert schedules.patch_schedule(uid, item["public_id"], {"paused": True})["paused"] is True
    # The form resends the unchanged cadence with every save.
    renamed = schedules.patch_schedule(uid, item["public_id"], {
        "name": "renamed", "recur_kind": "cron", "cron_expr": "0,55 1,3 * * *", "timezone": "America/New_York"})
    assert renamed["name"] == "renamed"
    with pytest.raises(ScheduleError, match="frequent"):
        schedules.patch_schedule(uid, item["public_id"], {"timezone": "Europe/Berlin"})


def test_pausing_clears_the_next_run_and_preview(fresh_db):
    uid = quick_user()
    with clock("2026-10-20T06:00:00Z"):
        item = make_schedule(uid)
        assert schedules.preview_fires(item, 3) == [
            "2026-10-20T07:00:00Z", "2026-10-20T08:00:00Z", "2026-10-20T09:00:00Z"]
        paused = schedules.patch_schedule(uid, item["public_id"], {"paused": True})
        assert paused["next_fire_at"] is None
        assert schedules.preview_fires(paused) == []
        resumed = schedules.patch_schedule(uid, item["public_id"], {"paused": False})
    assert resumed["next_fire_at"] == "2026-10-20T07:00:00Z"


def test_schedule_count_is_capped_per_account(fresh_db):
    uid, other = quick_user(), quick_user("other@core.test")
    with patch.object(schedules, "MAX_SCHEDULES", 2):
        make_schedule(uid)
        make_schedule(uid)
        with pytest.raises(ScheduleError) as refused:
            make_schedule(uid)
        assert refused.value.status_code == 413
        make_schedule(other)  # another account has its own room
    assert len(schedules.list_schedules(uid)) == 2


def test_schedules_belong_to_their_owner(fresh_db):
    owner, other = quick_user(), quick_user("other@core.test")
    item = make_schedule(owner)
    for token in (item["public_id"], item["id"]):
        assert schedules.get_schedule(other, token) is None
    with pytest.raises(ScheduleError) as refused:
        schedules.patch_schedule(other, item["public_id"], {"name": "mine now"})
    assert refused.value.status_code == 404
    with pytest.raises(ScheduleError):
        schedules.delete_schedule(other, item["public_id"])
    with pytest.raises(ScheduleError):
        schedules.list_fires(other, item["public_id"])
    assert stored(item["id"])["name"] == "task"


def test_deleting_a_schedule_detaches_its_threads(fresh_db):
    uid = quick_user()
    item = make_schedule(uid)
    thread = chats.create_thread(uid, model="offline", schedule_id=item["id"])
    schedules.delete_schedule(uid, item["public_id"])
    assert schedules.get_schedule(uid, item["public_id"]) is None
    pk = chats.resolve_thread_pk(uid, thread["id"])
    assert db.fetchone("SELECT schedule_id FROM chat_threads WHERE id=?", (pk,))["schedule_id"] is None


def test_stored_variables_that_fail_todays_rules_do_not_break_the_list(fresh_db):
    uid = quick_user()
    item = make_schedule(uid)
    db.execute("UPDATE chat_schedules SET variables=? WHERE id=?", ('{"api_key": "x", "1bad": "y"}', item["id"]))
    assert schedules.list_schedules(uid)[0]["variables"] == {}


def test_prompt_expansion_uses_the_schedule_zone_and_sanitizes_the_last_error(fresh_db):
    uid = quick_user()
    item = make_schedule(uid, name="digest", timezone="Asia/Tokyo",
                         prompt="{{date}} {{time}} {{weekday}} {{iso}} {{keyboard}} [{{last_error}}]",
                         variables={"keyboard": "us"})
    item["last_error"] = "upstream said Bearer abc.def and sk-live123"
    with clock("2026-10-20T23:30:00Z"):
        text = schedules.expand_prompt(item, fire_id="f1")
    assert text.startswith("SCHEDULED RUN")
    assert "2026-10-21 08:30 Wednesday 2026-10-20T23:30:00Z us" in text
    assert "abc.def" not in text and "sk-live123" not in text
    item["prompt"] = "{{vanished}}"
    with pytest.raises(ScheduleError):
        schedules.expand_prompt(item, fire_id="f1")


def test_a_once_schedule_stores_a_normalized_utc_instant(fresh_db):
    uid = quick_user()
    with clock("2026-10-20T06:00:00Z") as now:
        item = make_schedule(uid, recur_kind="once", once_at=iso_utc(now + timedelta(hours=2)).replace("Z", "+00:00"))
    assert item["once_at"] == item["next_fire_at"] == "2026-10-20T08:00:00Z"

def test_a_once_schedule_refuses_a_past_instant_but_keeps_its_own_on_edit(fresh_db):
    uid = quick_user()
    with clock("2026-10-20T06:00:00Z") as now:
        with pytest.raises(ScheduleError, match="future"):
            make_schedule(uid, recur_kind="once", once_at=iso_utc(now - timedelta(hours=2)))
        assert schedules.list_schedules(uid) == []
        # Clock skew and form latency: a few seconds late is still accepted.
        item = make_schedule(uid, recur_kind="once", once_at=iso_utc(now - timedelta(seconds=30)))
        with pytest.raises(ScheduleError, match="future"):
            schedules.patch_schedule(uid, item["public_id"], {"once_at": iso_utc(now - timedelta(hours=1))})
    # Hours after it fired, the schedule can still be renamed with its stored instant.
    with clock("2026-10-21T06:00:00Z"):
        renamed = schedules.patch_schedule(uid, item["public_id"], {"name": "renamed", "once_at": item["once_at"]})
    assert (renamed["name"], renamed["once_at"]) == ("renamed", "2026-10-20T05:59:30Z")
