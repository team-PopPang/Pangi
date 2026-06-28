from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pangi.domain.models import ScheduleType, ScheduledTask


class ScheduleValidationError(ValueError):
    pass


def parse_time_of_day(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ScheduleValidationError("time_of_day must use HH:MM")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        raise ScheduleValidationError("time_of_day must use HH:MM") from None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ScheduleValidationError("time_of_day must be a valid clock time")
    return time(hour=hour, minute=minute)


def parse_days_of_week(value: str | None) -> tuple[int, ...]:
    if not value:
        return ()
    days: list[int] = []
    for raw_day in value.split(","):
        raw_day = raw_day.strip()
        if not raw_day:
            continue
        try:
            day = int(raw_day)
        except ValueError:
            raise ScheduleValidationError("days_of_week must contain integers") from None
        if day < 0 or day > 6:
            raise ScheduleValidationError("days_of_week must contain values from 0 to 6")
        days.append(day)
    return tuple(sorted(set(days)))


def normalize_timezone(value: str) -> str:
    timezone_name = value.strip() or "Asia/Seoul"
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise ScheduleValidationError("timezone is not supported") from None
    return timezone_name


def local_datetime_to_utc(*, local_date: date, local_time: time, timezone_name: str) -> datetime:
    tz = ZoneInfo(normalize_timezone(timezone_name))
    return datetime.combine(local_date, local_time, tzinfo=tz).astimezone(timezone.utc)


def compute_initial_next_run(
    *,
    schedule_type: ScheduleType,
    timezone_name: str,
    time_of_day: str | None,
    days_of_week: str | None,
    run_at: datetime | None,
    after: datetime,
) -> datetime | None:
    after = _ensure_utc(after)
    if schedule_type == ScheduleType.ONCE:
        if run_at is None:
            raise ScheduleValidationError("once schedule requires run_at")
        run_at = _ensure_utc(run_at)
        return run_at if run_at > after else None
    if time_of_day is None:
        raise ScheduleValidationError("recurring schedule requires time_of_day")
    return _next_recurring_run(
        schedule_type=schedule_type,
        timezone_name=timezone_name,
        time_of_day=parse_time_of_day(time_of_day),
        days_of_week=parse_days_of_week(days_of_week),
        after=after,
    )


def compute_next_run_after_claim(task: ScheduledTask, *, after: datetime) -> datetime | None:
    after = _ensure_utc(after)
    if task.schedule_type == ScheduleType.ONCE:
        return None
    if task.time_of_day is None:
        raise ScheduleValidationError("recurring schedule requires time_of_day")
    return _next_recurring_run(
        schedule_type=task.schedule_type,
        timezone_name=task.timezone,
        time_of_day=parse_time_of_day(task.time_of_day),
        days_of_week=parse_days_of_week(task.days_of_week),
        after=after,
    )


def _next_recurring_run(
    *,
    schedule_type: ScheduleType,
    timezone_name: str,
    time_of_day: time,
    days_of_week: tuple[int, ...],
    after: datetime,
) -> datetime:
    tz = ZoneInfo(normalize_timezone(timezone_name))
    local_after = _ensure_utc(after).astimezone(tz)
    if schedule_type == ScheduleType.WEEKLY and not days_of_week:
        raise ScheduleValidationError("weekly schedule requires at least one day")

    allowed_days = set(days_of_week)
    for offset in range(0, 370):
        candidate_date = local_after.date() + timedelta(days=offset)
        if schedule_type == ScheduleType.WEEKLY and candidate_date.weekday() not in allowed_days:
            continue
        candidate = datetime.combine(candidate_date, time_of_day, tzinfo=tz).astimezone(timezone.utc)
        if candidate > after:
            return candidate
    raise ScheduleValidationError("could not compute next run")


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ScheduleValidationError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)
