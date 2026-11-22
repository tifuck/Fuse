"""5-field Unix cron parser. Stdlib only. Numeric fields; 0=Sunday."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CronError = ValueError

_FIELD_RANGES = (
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # dom
    (1, 12),  # month
    (0, 6),   # dow, 0=Sunday
)


def parse_tz(name: str) -> ZoneInfo:
    text = (name or "").strip() or "UTC"
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, KeyError, TypeError) as exc:
        raise CronError(f"unknown timezone {text!r}") from exc


def parse_cron(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    raw = (expr or "").strip()
    parts = raw.split()
    if len(parts) != 5:
        raise CronError("cron must have 5 fields (minute hour dom month dow)")
    out = []
    for i, part in enumerate(parts):
        lo, hi = _FIELD_RANGES[i]
        out.append(_parse_field(part, lo, hi))
    return tuple(out)  # type: ignore[return-value]


def _parse_field(part: str, lo: int, hi: int) -> set[int]:
    text = (part or "").strip()
    if not text:
        raise CronError("empty cron field")
    if any(ch.isalpha() for ch in text):
        raise CronError("cron names (MON, JAN) are not supported")
    values: set[int] = set()
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            raise CronError("empty cron list item")
        step = 1
        stepped = "/" in chunk
        if stepped:
            base, step_s = chunk.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                raise CronError("invalid cron step")
            step = int(step_s)
            chunk = base or "*"
        if chunk == "*":
            start, end = lo, hi
        elif "-" in chunk:
            a, b = chunk.split("-", 1)
            if not a.isdigit() or not b.isdigit():
                raise CronError("invalid cron range")
            start, end = int(a), int(b)
        else:
            if not chunk.isdigit():
                raise CronError("invalid cron value")
            # `5/15` steps from 5 to the field maximum, as in cron(5).
            start = int(chunk)
            end = hi if stepped else start
        if start > end or start < lo or end > hi:
            raise CronError("cron value out of range")
        values.update(range(start, end + 1, step))
    if not values:
        raise CronError("cron field matched nothing")
    return values


def compile_daily(hour: int, minute: int) -> str:
    _check_hm(hour, minute)
    return f"{minute} {hour} * * *"


def compile_weekly(hour: int, minute: int, weekdays: int) -> str:
    """weekdays bitmask: Monday=bit0 … Sunday=bit6. Cron dow 0=Sunday."""
    _check_hm(hour, minute)
    if not (1 <= int(weekdays) <= 127):
        raise CronError("select at least one weekday")
    cron_days = []
    for bit in range(7):
        if weekdays & (1 << bit):
            cron_days.append(str((bit + 1) % 7))  # Mon=1 … Sun=0
    cron_days = sorted(cron_days, key=lambda s: (s != "0", s))
    return f"{minute} {hour} * * {','.join(cron_days)}"


def parse_hhmm(value: str) -> tuple[int, int]:
    text = (value or "").strip()
    parts = text.split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise CronError("time must be HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    _check_hm(hour, minute)
    return hour, minute


def _check_hm(hour: int, minute: int) -> None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise CronError("time must be HH:MM")


def _local_utc(wall: datetime, tz: ZoneInfo) -> datetime | None:
    """UTC instant at which the local clock reads `wall`; None inside a DST gap."""
    inst = wall.replace(tzinfo=tz, fold=0).astimezone(timezone.utc)
    back = inst.astimezone(tz)
    if (back.year, back.month, back.day, back.hour, back.minute) != (
        wall.year, wall.month, wall.day, wall.hour, wall.minute,
    ):
        return None
    return inst


def _date_or(expr: str) -> bool:
    """dom/dow follow POSIX: when both fields are restricted, a match in either
    is enough. A field that starts with `*` (`*/2`) is not restricted."""
    parts = (expr or "").strip().split()
    return not parts[2].startswith("*") and not parts[4].startswith("*")


def _date_ok(local, dom: set[int], dow: set[int], date_or: bool) -> bool:
    day_ok = local.day in dom
    dow_ok = (local.isoweekday() % 7) in dow
    return (day_ok or dow_ok) if date_or else (day_ok and dow_ok)


def _fixed_time(expr: str) -> bool:
    """Minute and hour both fixed (neither starts with `*`). As in Vixie cron
    and cronie, only such a job catches up a slot lost to a spring-forward gap."""
    parts = (expr or "").strip().split()
    return not parts[0].startswith("*") and not parts[1].startswith("*")


def _gap_had_slot(cursor: datetime, local: datetime, tz: ZoneInfo, fields, date_or: bool) -> bool:
    """cursor is the first instant after a spring-forward gap, and a wall
    minute the gap skipped matches the expression."""
    minute, hour, dom, month, dow = fields
    wall = (cursor - timedelta(minutes=1)).astimezone(tz).replace(tzinfo=None) + timedelta(minutes=1)
    end = local.replace(tzinfo=None)
    while wall < end:
        if wall.month in month and _date_ok(wall, dom, dow, date_or) and wall.hour in hour and wall.minute in minute:
            return True
        wall += timedelta(minutes=1)
    return False


def next_fire(expr: str, tz_name: str, after_utc: datetime) -> datetime | None:
    """Next UTC instant strictly after after_utc where the local clock in tz_name matches expr.

    Walks real time and re-reads local wall fields per candidate, so a slot
    that occurs twice (DST fall-back) fires at both real occurrences. A slot
    inside a spring-forward gap never appears on the local clock: when minute
    and hour are both fixed (`0 0 * * *`, not `30 * * * *`) it fires once at
    the first instant after the gap, so a daily run is never lost; otherwise
    it does not fire. dom/dow follow POSIX: when both fields are restricted, a
    match in either is enough. As in Vixie cron and cronie, a field that
    starts with `*` (`*/2`) is not restricted, so `0 0 */2 * 1` is
    odd-dated Mondays.
    """
    fields = parse_cron(expr)
    minute, hour, dom, month, dow = fields
    tz = parse_tz(tz_name)
    date_or = _date_or(expr)
    fixed = _fixed_time(expr)
    if after_utc.tzinfo is None:
        after_utc = after_utc.replace(tzinfo=timezone.utc)
    cursor = (after_utc.replace(second=0, microsecond=0) + timedelta(minutes=1))
    # At least one leap day lies within 8 years (e.g. 2096-02-29 -> 2104-02-29).
    limit = after_utc + timedelta(days=366 * 8)
    while cursor <= limit:
        local = cursor.astimezone(tz)
        if fixed and _gap_had_slot(cursor, local, tz, fields, date_or):
            return cursor
        if local.month not in month or not _date_ok(local, dom, dow, date_or):
            wall = datetime(local.year, local.month, local.day) + timedelta(days=1)
        elif local.hour not in hour:
            # Step to the next local hour boundary in real time: a wall-clock
            # jump can leap over a repeated slot (a 2-hour fall-back).
            cursor += timedelta(minutes=60 - local.minute)
            continue
        elif local.minute not in minute:
            cursor += timedelta(minutes=1)
            continue
        else:
            return cursor
        # Skip whole days/hours that cannot match. A jump whose target falls in
        # a DST gap has no local instant, so step a minute instead; the loop
        # re-derives local fields and jumps again past the gap.
        nxt = _local_utc(wall, tz)
        cursor = nxt if nxt is not None and nxt > cursor else cursor + timedelta(minutes=1)
    return None


def next_n(expr: str, tz_name: str, after_utc: datetime, n: int = 5) -> list[datetime]:
    out: list[datetime] = []
    cursor = after_utc
    for _ in range(max(1, min(n, 24))):
        nxt = next_fire(expr, tz_name, cursor)
        if nxt is None:
            break
        out.append(nxt)
        cursor = nxt
    return out


def implied_min_gap(expr: str, tz_name: str, after_utc: datetime) -> int | None:
    """Smallest real gap in seconds between consecutive fires over a full cycle
    of the pattern: within a day, across day, month and year ends, and through
    DST shifts. None when it fires less than twice.

    Leap days and weekday alignment repeat every 28 years (1901-2099) and zone
    rules repeat yearly, so 28 years from after_utc's year cover every case.
    """
    if after_utc.tzinfo is None:
        after_utc = after_utc.replace(tzinfo=timezone.utc)
    return _min_gap(expr, tz_name, after_utc.year)


# The catch-up grace asks on every fire claim; the answer depends on the year
# only through the cycle's start.
@lru_cache(maxsize=256)
def _min_gap(expr: str, tz_name: str, year: int) -> int | None:
    minute, hour, dom, month, dow = parse_cron(expr)
    tz = parse_tz(tz_name)
    date_or = _date_or(expr)
    fixed = _fixed_time(expr)
    slots = [timedelta(hours=h, minutes=m) for h in sorted(hour) for m in sorted(minute)]
    same_day = min((b - a for a, b in zip(slots, slots[1:])), default=None)
    one_day = timedelta(days=1)
    floor = timedelta(minutes=1)
    best = None
    prev_last = None
    # Offset of the next local midnight, kept for the following day.
    next_off = None
    midnight = datetime(year, 1, 1)
    end = datetime(year + 28, 1, 1)
    while midnight < end and best != floor:
        if midnight.month not in month:
            midnight = (midnight.replace(day=1) + timedelta(days=32)).replace(day=1)
            next_off = None
            continue
        if not _date_ok(midnight, dom, dow, date_or):
            midnight += one_day
            next_off = None
            continue
        off = next_off if next_off is not None else midnight.replace(tzinfo=tz).utcoffset()
        next_off = (midnight + one_day).replace(tzinfo=tz).utcoffset()
        if off == next_off:
            # The offset holds all day (fold=0 reads a DST gap or fold at a
            # midnight with the earlier offset, so an edge shift differs).
            first, last = midnight + slots[0] - off, midnight + slots[-1] - off
            gap = same_day
        else:
            # One shift today: find the first wall minute on the new offset.
            # Slots in the window before it fall in a spring-forward gap (a
            # fixed-time job fires them once at the gap's end, others never),
            # or in a fall-back fold and fire at both occurrences.
            lo, hi = 0, 1440
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if (midnight + timedelta(minutes=mid)).replace(tzinfo=tz).utcoffset() == off:
                    lo = mid
                else:
                    hi = mid
            shift = timedelta(minutes=hi)
            window = abs(next_off - off)
            fires = set()
            for t in slots:
                if t >= shift:
                    fires.add(midnight + t - next_off)
                elif t < shift - window:
                    fires.add(midnight + t - off)
                elif next_off < off:
                    fires.update((midnight + t - off, midnight + t - next_off))
                elif fixed:
                    fires.add(midnight + shift - next_off)
            fires = sorted(fires)
            if not fires:
                midnight += one_day
                continue
            first, last = fires[0], fires[-1]
            gap = min((b - a for a, b in zip(fires, fires[1:])), default=None)
        if prev_last is not None:
            gap = first - prev_last if gap is None else min(gap, first - prev_last)
        if gap is not None:
            best = gap if best is None else min(best, gap)
        prev_last = last
        midnight += one_day
    return int(best.total_seconds()) if best is not None else None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_utc(value: str, tz: str = "") -> datetime | None:
    """A time with no offset is wall time in tz (default UTC)."""
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=parse_tz(tz))
    return dt.astimezone(timezone.utc)
