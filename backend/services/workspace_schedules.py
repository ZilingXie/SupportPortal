from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


WORKSPACE_SCHEDULE_TIMEZONE = "Asia/Shanghai"


def is_shift_active(shift: dict[str, Any], now: datetime) -> bool:
    timezone_name = str(shift.get("timezone") or WORKSPACE_SCHEDULE_TIMEZONE)
    local_now = now.astimezone(ZoneInfo(timezone_name))
    weekday = int(shift["weekday"])
    start_minute = int(shift["start_minute"])
    end_minute = int(shift["end_minute"])
    current_minute = local_now.hour * 60 + local_now.minute
    if start_minute < end_minute:
        return local_now.weekday() == weekday and start_minute <= current_minute < end_minute
    return (
        local_now.weekday() == weekday
        and current_minute >= start_minute
    ) or (
        local_now.weekday() == (weekday + 1) % 7
        and current_minute < end_minute
    )


def on_schedule_engineer_ids(
    schedules: list[dict[str, Any]],
    now: datetime | None = None,
) -> set[str]:
    reference = now or datetime.now(timezone.utc)
    return {
        str(shift.get("engineer_id") or "").strip()
        for shift in schedules
        if str(shift.get("engineer_id") or "").strip() and is_shift_active(shift, reference)
    }


def minutes_to_time(value: int) -> str:
    hour, minute = divmod(int(value), 60)
    return f"{hour:02d}:{minute:02d}"


def time_to_minutes(value: str, *, allow_24: bool = False) -> int:
    parts = str(value or "").strip().split(":")
    if len(parts) != 2:
        raise ValueError("time must use HH:MM")
    try:
        hour, minute = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("time must use HH:MM") from exc
    if allow_24 and hour == 24 and minute == 0:
        return 24 * 60
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("time must use HH:MM")
    return hour * 60 + minute
