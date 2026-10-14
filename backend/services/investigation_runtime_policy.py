"""Server-owned, replayable Investigation run policy; never parsed from case text."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

PROFILE = "supportportal-investigation-v1"


def investigation_policy(turn: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any] | None:
    task = binding.get("case_task") or {}
    if turn.get("direction") != "investigation" or task.get("direction") not in {None, "investigation"}:
        return None
    stamp = turn["created_at"]
    end = stamp if isinstance(stamp, datetime) else datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    if end.tzinfo is None:
        raise ValueError("investigation_turn_timestamp_requires_timezone")
    end = end.astimezone(timezone.utc)
    return {
        "skill_profile": PROFILE,
        "allow_close": (turn.get("work_result") or {}).get("engineer_authority", {}).get("action") == "solve_bound_case",
        "initial_search_window": {
            "fromTs": int((end - timedelta(days=30)).timestamp()),
            "toTs": int(end.timestamp()),
            "label": "initial search range; not the customer's incident time",
            "max_channel_searches": 3,
        },
    }


def public_investigation(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {key: item for key, item in value.items() if not key.startswith("_zac_")}
