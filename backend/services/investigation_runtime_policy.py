"""Server-owned, replayable Investigation run policy; never parsed from case text."""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

PROFILE = "supportportal-investigation-v1"
DEFAULT_TIMEZONE = "Asia/Shanghai"
_TODAY_RE = re.compile(r"(?<![A-Za-z])today(?:['’]s)?(?![A-Za-z])|今天")


def _latest_customer_message(turn: dict[str, Any]) -> str:
    snapshot = turn.get("input_snapshot")
    conversation = snapshot.get("conversation") if isinstance(snapshot, dict) else None
    if not isinstance(conversation, list):
        return ""
    for item in reversed(conversation):
        if not isinstance(item, dict):
            continue
        author = item.get("author")
        role = author.get("role") if isinstance(author, dict) else author
        if str(role or "").strip().lower() in {"customer", "user", "end_user"}:
            return str(item.get("body") or "")
    return ""


def _relative_today_window(turn: dict[str, Any], end: datetime) -> dict[str, Any] | None:
    if not _TODAY_RE.search(_latest_customer_message(turn)):
        return None
    local_end = end.astimezone(ZoneInfo(DEFAULT_TIMEZONE))
    local_start = datetime.combine(local_end.date(), time.min, tzinfo=local_end.tzinfo)
    return {
        "fromTs": int(local_start.astimezone(timezone.utc).timestamp()),
        "toTs": int(end.timestamp()),
        "label": "customer said today; bounded to service-default local date",
        "window_basis": "relative_customer_date",
        "timezone": DEFAULT_TIMEZONE,
        "timezone_source": "service_default",
        "query_priority": "targeted_current_turn",
    }


def investigation_policy(turn: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any] | None:
    task = binding.get("case_task") or {}
    if turn.get("direction") != "investigation" or task.get("direction") not in {None, "investigation"}:
        return None
    stamp = turn["created_at"]
    end = stamp if isinstance(stamp, datetime) else datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    if end.tzinfo is None:
        raise ValueError("investigation_turn_timestamp_requires_timezone")
    end = end.astimezone(timezone.utc)
    policy = {
        "skill_profile": PROFILE,
        "allow_close": (turn.get("work_result") or {}).get("engineer_authority", {}).get("action") == "solve_bound_case",
        "initial_search_window": {
            "fromTs": int((end - timedelta(days=30)).timestamp()),
            "toTs": int(end.timestamp()),
            "label": "initial search range; not the customer's incident time",
            "max_channel_searches": 3,
        },
    }
    relative_today = _relative_today_window(turn, end)
    if relative_today:
        policy["relative_date_window"] = relative_today
    return policy


def public_investigation(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {key: item for key, item in value.items() if not key.startswith("_zac_")}
