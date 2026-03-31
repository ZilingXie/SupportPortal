from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

_STATUS_ORDER = ("communicating", "escalated", "investigating", "resolved", "open")
_STATUS_LABELS = {
    "communicating": "Communicating",
    "escalated": "Escalated",
    "investigating": "Investigating",
    "resolved": "Resolved",
    "open": "Open",
}
_PRIORITY_ORDER = ("urgent", "high", "normal", "low")
_PRIORITY_LABELS = {
    "urgent": "Urgent",
    "high": "High",
    "normal": "Normal",
    "low": "Low",
}
_FLOW_ORDER = ("communicating", "escalated", "investigating", "resolved")
_FLOW_LABELS = {
    "communicating": "Communicating",
    "escalated": "Escalated",
    "investigating": "Investigating",
    "resolved": "Resolved",
}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _normalize_ticket_status(value: Any) -> str:
    status = _clean_text(value).lower()
    if status == "waiting_for_engineer":
        return "investigating"
    if status in {"open", "communicating", "escalated", "investigating", "resolved"}:
        return status
    return "open"


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)

    text = _clean_text(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _humanize_token(value: Any) -> str:
    normalized = _clean_text(value)
    if not normalized:
        return "-"
    return normalized.replace("_", " ").title()


def _pluralize(count: int, singular: str, plural: str | None = None) -> str:
    if count == 1:
        return singular
    return plural or f"{singular}s"


def is_ticket_dashboard_event(event: dict[str, Any]) -> bool:
    ingestion_id = _clean_text(event.get("ingestion_id"))
    event_name = _clean_text(event.get("event")).lower()
    return not ingestion_id and not event_name.startswith("knowledge_ingestion_")


def normalize_ticket_dashboard_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in rows:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        event_type = _clean_text(row.get("event_type") or payload.get("event") or "ticket_updated") or "ticket_updated"
        ticket_id = row.get("ticket_id") or payload.get("ticket_id")
        ingestion_id = payload.get("ingestion_id")
        events.append(
            {
                "event": payload.get("event") or event_type,
                "ticket_id": str(ticket_id) if ticket_id is not None else "-",
                "ingestion_id": str(ingestion_id) if ingestion_id is not None else None,
                "title": payload.get("title"),
                "message": payload.get("message"),
                "status": _normalize_ticket_status(payload.get("status")),
                "priority": payload.get("priority"),
                "knowledge_type": payload.get("knowledge_type"),
                "source_type": payload.get("source_type"),
                "chunk_count": payload.get("chunk_count"),
                "dedupe_action": payload.get("dedupe_action"),
                "error_message": payload.get("error_message"),
                "created_at": payload.get("created_at") or row.get("created_at") or _utc_now_iso(),
            }
        )
    return events


def _ordered_breakdown(
    tickets: list[dict[str, Any]],
    *,
    field: str,
    order: tuple[str, ...],
    labels: dict[str, str],
) -> list[dict[str, Any]]:
    counts = Counter(_clean_text(ticket.get(field)).lower() for ticket in tickets)
    return [
        {"label": labels[token], "value": counts.get(token, 0)}
        for token in order
    ]


def _event_volume_12h(events: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    bucket_end = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    bucket_start = bucket_end - timedelta(hours=12)
    labels = [
        (bucket_start + timedelta(hours=index)).strftime("%H:%M")
        for index in range(12)
    ]
    counts = [0] * 12

    for event in events:
        created_at = _parse_datetime(event.get("created_at"))
        if created_at is None or created_at < bucket_start or created_at >= bucket_end:
            continue
        bucket_index = int((created_at - bucket_start).total_seconds() // 3600)
        if 0 <= bucket_index < len(counts):
            counts[bucket_index] += 1

    return [{"label": labels[index], "value": counts[index]} for index in range(12)]


def _latest_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    ordered = sorted(
        events,
        key=lambda item: _parse_datetime(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return ordered[0] if ordered else None


def _latest_escalation_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    ordered = sorted(
        events,
        key=lambda item: _parse_datetime(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    for event in ordered:
        priority = _clean_text(event.get("priority")).lower()
        status = _normalize_ticket_status(event.get("status"))
        event_name = _clean_text(event.get("event")).lower()
        if priority in {"urgent", "high"} or status == "investigating" or "alert" in event_name or "attention" in event_name:
            return event
    return ordered[0] if ordered else None


def build_ticket_dashboard_metrics(
    tickets: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    safe_now = now.astimezone(timezone.utc) if isinstance(now, datetime) else _utc_now()
    normalized_tickets = [{**ticket, "status": _normalize_ticket_status(ticket.get("status"))} for ticket in tickets]
    ticket_events = [event for event in events if is_ticket_dashboard_event(event)]

    total = len(normalized_tickets)
    resolved_count = sum(_clean_text(ticket.get("status")).lower() == "resolved" for ticket in normalized_tickets)
    resolution_rate = round((resolved_count / total) * 100, 1) if total else 0.0
    sentiment_alert_count = sum(_clean_text(ticket.get("priority")).lower() == "high" for ticket in normalized_tickets)

    investigating_ticket_count = sum(_clean_text(ticket.get("status")).lower() == "investigating" for ticket in normalized_tickets)
    open_ticket_count = sum(_clean_text(ticket.get("status")).lower() == "open" for ticket in normalized_tickets)
    communicating_ticket_count = sum(
        _clean_text(ticket.get("status")).lower() == "communicating" for ticket in normalized_tickets
    )
    escalated_ticket_count = sum(
        _clean_text(ticket.get("status")).lower() == "escalated" for ticket in normalized_tickets
    )
    urgent_ticket_count = sum(_clean_text(ticket.get("priority")).lower() == "urgent" for ticket in normalized_tickets)

    charts = {
        "event_volume_12h": _event_volume_12h(ticket_events, safe_now),
        "status_breakdown": _ordered_breakdown(
            normalized_tickets,
            field="status",
            order=_STATUS_ORDER,
            labels=_STATUS_LABELS,
        ),
        "priority_breakdown": _ordered_breakdown(
            normalized_tickets,
            field="priority",
            order=_PRIORITY_ORDER,
            labels=_PRIORITY_LABELS,
        ),
        "flow_breakdown": _ordered_breakdown(
            normalized_tickets,
            field="status",
            order=_FLOW_ORDER,
            labels=_FLOW_LABELS,
        ),
    }

    cards = {
        "investigating_ticket_count": investigating_ticket_count,
        "open_ticket_count": open_ticket_count,
        "communicating_ticket_count": communicating_ticket_count,
        "escalated_ticket_count": escalated_ticket_count,
        "resolved_ticket_count": resolved_count,
        "urgent_ticket_count": urgent_ticket_count,
    }

    active_count = open_ticket_count + communicating_ticket_count + escalated_ticket_count + investigating_ticket_count
    latest_event = _latest_event(ticket_events)
    escalation_event = _latest_escalation_event(ticket_events)
    recent_volume = sum(item["value"] for item in charts["event_volume_12h"][-3:])

    if active_count == 0:
        queue_health_label = "Queue is clear."
    elif escalated_ticket_count or investigating_ticket_count or urgent_ticket_count:
        queue_health_label = "Escalation pressure is active."
    elif communicating_ticket_count:
        queue_health_label = "Communicating queue is stable."
    else:
        queue_health_label = "Queue is stable but active."

    queue_health_detail = (
        f"{active_count} active {_pluralize(active_count, 'ticket')}, "
        f"{communicating_ticket_count} communicating, "
        f"{escalated_ticket_count} escalated, "
        f"{investigating_ticket_count} investigating, and "
        f"{recent_volume} {_pluralize(recent_volume, 'event')} in the last 3 hours."
    )

    if escalated_ticket_count or investigating_ticket_count:
        attention_count = escalated_ticket_count + investigating_ticket_count
        operator_summary_title = (
            f"{attention_count} engineer-facing {_pluralize(attention_count, 'ticket')} active."
        )
    elif communicating_ticket_count:
        operator_summary_title = "AI-managed conversations are progressing."
    else:
        operator_summary_title = "No active operator workload."

    operator_summary_detail = (
        f"{communicating_ticket_count} communicating, "
        f"{escalated_ticket_count} escalated, "
        f"{investigating_ticket_count} investigating, and {resolved_count} resolved."
    )

    if escalation_event is not None:
        escalation_ticket_id = _clean_text(escalation_event.get("ticket_id")) or "Queue"
        escalation_message = _clean_text(escalation_event.get("message")) or _clean_text(escalation_event.get("title"))
        escalation_summary_title = f"{escalation_ticket_id} is the sharpest live signal."
        escalation_summary_detail = escalation_message or "Recent ticket activity suggests closer triage is needed."
    else:
        escalation_summary_title = "No fresh escalation spike."
        escalation_summary_detail = "Recent ticket traffic is calm across the queue."

    latest_event_message = _clean_text(latest_event.get("message") if latest_event else "") or _clean_text(
        latest_event.get("title") if latest_event else ""
    )
    if latest_event_message:
        queue_health_detail = f"{queue_health_detail} Latest signal: {latest_event_message}"

    summaries = {
        "queue_health_label": queue_health_label,
        "queue_health_detail": queue_health_detail,
        "operator_summary_title": operator_summary_title,
        "operator_summary_detail": operator_summary_detail,
        "escalation_summary_title": escalation_summary_title,
        "escalation_summary_detail": escalation_summary_detail,
    }

    return {
        "today_ticket_count": total,
        "resolution_rate": resolution_rate,
        "sentiment_alert_count": sentiment_alert_count,
        "cards": cards,
        "summaries": summaries,
        "charts": charts,
    }
