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
    event = snapshot.get("current_event") if isinstance(snapshot, dict) else None
    trigger_id = event.get("trigger_comment_id") if isinstance(event, dict) else None
    customer_messages: list[dict[str, Any]] = []
    for item in conversation if isinstance(conversation, list) else []:
        if not isinstance(item, dict):
            continue
        author = item.get("author")
        role = author.get("role") if isinstance(author, dict) else author
        if (
            str(role or "").strip().lower() in {"customer", "user", "end-user", "end_user"}
            and not (isinstance(author, dict) and author.get("is_agent") is True)
        ):
            if trigger_id is None or str(item.get("id") or "") == str(trigger_id):
                customer_messages.append(item)
    if customer_messages:
        return str(customer_messages[-1].get("body") or "")
    # Zendesk's first ticket.created event carries the initial customer text
    # in ticket.description; it is intentionally not copied into conversation.
    event = snapshot.get("current_event") if isinstance(snapshot, dict) else None
    ticket = snapshot.get("ticket") if isinstance(snapshot, dict) else None
    if (
        isinstance(event, dict)
        and str(event.get("event_type") or "") == "ticket.created"
        and not event.get("trigger_comment_id")
        and isinstance(ticket, dict)
    ):
        return str(ticket.get("description") or "")
    return ""


def _relative_today_window(turn: dict[str, Any], end: datetime) -> dict[str, Any] | None:
    snapshot = turn.get("input_snapshot") if isinstance(turn.get("input_snapshot"), dict) else {}
    conversation = snapshot.get("conversation") if isinstance(snapshot, dict) else []
    if not isinstance(conversation, list):
        conversation = []
    has_customer_comment = any(
        isinstance(item, dict)
        and (
            str((item.get("author") or {}).get("role") if isinstance(item.get("author"), dict) else item.get("author") or "").strip().lower()
            in {"customer", "user", "end-user", "end_user"}
        )
        and not (isinstance(item.get("author"), dict) and item["author"].get("is_agent") is True)
        for item in conversation if isinstance(conversation, list)
    )
    source = "current_turn_customer_comment" if has_customer_comment else "initial_ticket_description"
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
        "window_source": source,
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
