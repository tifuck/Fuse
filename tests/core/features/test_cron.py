"""5-field cron: parsing, builder presets, and next-fire times across time
zones and DST (a spring-forward gap never fires, a fall-back fold fires at
both real occurrences). Pure functions: no database."""
from datetime import datetime, timezone

import pytest

from app import cron


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


@pytest.mark.parametrize("expr", [
    "0 9 * * * *",      # six fields
    "0 9 * *",          # four
    "0 9 * * MON",      # names are not supported
    "60 * * * *",       # minute out of range
    "0 24 * * *",
    "0 0 0 * *",        # day of month starts at 1
    "0 0 * 13 *",
    "0 0 * * 7",        # 0 = Sunday, 7 is refused
    "*/0 * * * *",      # zero step
    "5-2 * * * *",      # reversed range
    "1,,2 * * * *",
])
def test_invalid_expressions_are_refused(expr):
    with pytest.raises(cron.CronError):
        cron.parse_cron(expr)


def test_lists_ranges_and_steps_expand_like_cron():
    minute, hour, dom, month, dow = cron.parse_cron("5/15 1-3,22 */10 * 1-5")
    assert minute == {5, 20, 35, 50}
    assert hour == {1, 2, 3, 22}
    assert dom == {1, 11, 21, 31}
    assert month == set(range(1, 13))
    assert dow == {1, 2, 3, 4, 5}


def test_builder_presets_compile_to_cron():
    assert cron.compile_daily(9, 5) == "5 9 * * *"
    # weekdays bitmask: Monday = bit 0 ... Sunday = bit 6; cron Sunday is 0.
    assert cron.compile_weekly(9, 30, 1) == "30 9 * * 1"
    assert cron.compile_weekly(7, 0, 0b1100001) == "0 7 * * 0,1,6"
    with pytest.raises(cron.CronError):
        cron.compile_weekly(9, 0, 0)
    with pytest.raises(cron.CronError):
        cron.compile_daily(24, 0)
    assert cron.parse_hhmm("07:45") == (7, 45)
    for bad in ("7", "07:60", "aa:bb", "25:00"):
        with pytest.raises(cron.CronError):
            cron.parse_hhmm(bad)


def test_next_fire_is_strictly_after_the_given_instant():
    assert cron.next_fire("0 9 * * *", "UTC", utc(2026, 9, 14, 8, 0)) == utc(2026, 9, 14, 9, 0)
    # Exactly on a slot: that slot already fired, the next one is tomorrow.
    assert cron.next_fire("0 9 * * *", "UTC", utc(2026, 9, 14, 9, 0)) == utc(2026, 9, 15, 9, 0)
    # Seconds inside the slot minute do not make it fire again.
    assert cron.next_fire("0 9 * * *", "UTC", utc(2026, 9, 14, 9, 0, 30)) == utc(2026, 9, 15, 9, 0)
    # A naive instant is read as UTC.
    assert cron.next_fire("0 9 * * *", "UTC", datetime(2026, 9, 14, 8, 0)) == utc(2026, 9, 14, 9, 0)


def test_local_wall_clock_follows_the_zone_offset_through_the_year():
    winter = cron.next_fire("0 9 * * *", "Europe/Berlin", utc(2026, 1, 10))
    summer = cron.next_fire("0 9 * * *", "Europe/Berlin", utc(2026, 7, 10))
    assert winter == utc(2026, 1, 10, 8, 0)   # CET, UTC+1
    assert summer == utc(2026, 7, 10, 7, 0)   # CEST, UTC+2
    with pytest.raises(cron.CronError):
        cron.next_fire("0 9 * * *", "Mars/Olympus", utc(2026, 1, 1))


def test_a_slot_inside_the_spring_forward_gap_does_not_fire_that_day():
    # 2026-03-08 02:30 never appears on the America/New_York clock.
    assert cron.next_fire("30 2 * * *", "America/New_York", utc(2026, 3, 8)) == utc(2026, 3, 9, 6, 30)


def test_a_slot_in_the_fall_back_fold_fires_at_both_occurrences():
    # 2026-11-01 01:30 happens twice in America/New_York (EDT, then EST).
    assert cron.next_n("30 1 * * *", "America/New_York", utc(2026, 11, 1), 3) == [
        utc(2026, 11, 1, 5, 30), utc(2026, 11, 1, 6, 30), utc(2026, 11, 2, 6, 30),
    ]
    # Every minute walks real time through the repeated hour, no gap or skip.
    assert cron.next_n("* * * * *", "America/New_York", utc(2026, 11, 1, 5, 58), 3) == [
        utc(2026, 11, 1, 5, 59), utc(2026, 11, 1, 6, 0), utc(2026, 11, 1, 6, 1),
    ]


def test_a_two_hour_fall_back_does_not_skip_the_repeated_slot():
    # Antarctica/Troll falls back two hours (CEST+2 -> UTC+0) at 01:00 UTC on
    # the last Sunday of October: local 02:30 occurs at 00:30Z and again at 02:30Z.
    times = cron.next_n("30 2 * * *", "Antarctica/Troll", utc(2026, 10, 25), 2)
    assert times == [utc(2026, 10, 25, 0, 30), utc(2026, 10, 25, 2, 30)]


def test_leap_day_and_posix_day_matching():
    assert cron.next_fire("0 0 29 2 *", "UTC", utc(2026, 3, 1)) == utc(2028, 2, 29)
    # Both day fields restricted: either may match (the 1st, or any Monday).
    assert cron.next_fire("0 9 1 * 1", "UTC", utc(2026, 9, 14, 10, 0)) == utc(2026, 9, 21, 9, 0)
    assert cron.next_fire("0 9 1 * 1", "UTC", utc(2026, 9, 30)) == utc(2026, 10, 1, 9, 0)
    # A '*'-prefixed day field is unrestricted (Vixie cron): odd-dated Mondays only.
    assert cron.next_fire("0 0 */2 * 1", "UTC", utc(2026, 9, 1)) == utc(2026, 9, 7)


def test_implied_gap_measures_the_tightest_spacing():
    assert cron.implied_min_gap("* * * * *", "UTC", utc(2026, 9, 14), 5) == 60
    assert cron.implied_min_gap("0,10 9 * * *", "UTC", utc(2026, 9, 14), 6) == 600
    assert cron.implied_min_gap("0 9 * * *", "UTC", utc(2026, 9, 14), 4) == 86400


def test_iso_round_trip():
    assert cron.iso_utc(utc(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05Z"
    assert cron.parse_iso_utc("2026-01-02T03:04:05Z") == utc(2026, 1, 2, 3, 4, 5)
    assert cron.parse_iso_utc("2026-01-02T04:04:05+01:00") == utc(2026, 1, 2, 3, 4, 5)
    assert cron.parse_iso_utc("yesterday") is None
