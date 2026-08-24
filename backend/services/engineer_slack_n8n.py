from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


ENGINEER_SLACK_SCHEMA_VERSION = 1
ENGINEER_SLACK_DELIVERY_STATUSES = frozenset(
    {"pending", "delivered", "failed", "outcome_unknown"}
)
ENGINEER_SLACK_STATUS_STATUSES = ENGINEER_SLACK_DELIVERY_STATUSES | {"missing"}


@dataclass(frozen=True)
class EngineerSlackN8nError(RuntimeError):
    code: str
    outcome_unknown: bool = False

    def __str__(self) -> str:
        return self.code


def engineer_slack_n8n_configured() -> bool:
    urls = (
        str(os.getenv("ENGINEER_SLACK_N8N_WEBHOOK_URL") or "").strip(),
        str(os.getenv("ENGINEER_SLACK_N8N_STATUS_URL") or "").strip(),
    )
    token = str(os.getenv("n8n_request_token") or "").strip()
    return bool(token) and all(
        (parsed := urllib.parse.urlparse(value)).scheme in {"http", "https"}
        and bool(parsed.netloc)
        for value in urls
    )


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def build_engineer_case_opened_event(
    *,
    account_case: dict[str, Any],
    engineer_case: dict[str, Any],
) -> dict[str, Any]:
    engineer_case_id = str(engineer_case.get("engineer_case_id") or "").strip()
    account_case_id = str(account_case.get("account_case_id") or "").strip()
    zendesk_ticket_id = str(account_case.get("zendesk_ticket_id") or "").strip()
    title = _clean_text(account_case.get("title") or engineer_case.get("title"))
    problem = _clean_text(account_case.get("question")) or title
    route_reason = _clean_text(
        account_case.get("not_automated_reason")
        or account_case.get("route_reason")
        or engineer_case.get("trigger_reason")
    )
    if not all((engineer_case_id, account_case_id, title, problem)):
        raise ValueError(
            "Engineer Slack root event requires Engineer Case, Account Case, title, and problem"
        )
    zendesk_url = (
        "https://agoraio.zendesk.com/agent/tickets/"
        f"{urllib.parse.quote(zendesk_ticket_id, safe='')}"
        if zendesk_ticket_id
        else None
    )
    message_lines = [title, problem]
    if zendesk_url:
        message_lines.append(f"zendesk: {zendesk_url}")
    if route_reason:
        message_lines.append(f"route reason: {route_reason}")
    return {
        "schema_version": ENGINEER_SLACK_SCHEMA_VERSION,
        "event_id": f"engineer-slack:{engineer_case_id}:opened",
        "event_type": "engineer_case_opened",
        "engineer_case_id": engineer_case_id,
        "account_case_id": account_case_id,
        "case_title": title,
        "problem": problem,
        "route_reason": route_reason or None,
        "zendesk_ticket_id": zendesk_ticket_id or None,
        "zendesk_url": zendesk_url,
        "message_text": "\n".join(message_lines),
    }


def build_engineer_case_thread_event(
    *,
    event_id: str,
    event_type: str,
    engineer_case_id: str,
    message_text: str,
    investigation_id: str | None = None,
    conversation_version: int | None = None,
    draft_version: int | None = None,
    customer_draft: str | None = None,
    guardrail_id: str | None = None,
    guardrail_version: str | None = None,
    action: str | None = None,
    blockers: list[str] | None = None,
    failure_code: str | None = None,
) -> dict[str, Any]:
    normalized_event_id = str(event_id or "").strip()
    normalized_event_type = str(event_type or "").strip()
    normalized_case_id = str(engineer_case_id or "").strip()
    normalized_message = str(message_text or "").strip()
    if not all((normalized_event_id, normalized_event_type, normalized_case_id, normalized_message)):
        raise ValueError("Engineer Slack thread event requires event, type, case, and message")
    payload: dict[str, Any] = {
        "schema_version": ENGINEER_SLACK_SCHEMA_VERSION,
        "event_id": normalized_event_id,
        "event_type": normalized_event_type,
        "engineer_case_id": normalized_case_id,
        "message_text": normalized_message,
    }
    optional = {
        "investigation_id": str(investigation_id or "").strip() or None,
        "conversation_version": conversation_version,
        "draft_version": draft_version,
        "customer_draft": str(customer_draft or "").strip() or None,
        "guardrail_id": str(guardrail_id or "").strip() or None,
        "guardrail_version": str(guardrail_version or "").strip() or None,
        "action": str(action or "").strip() or None,
        "blockers": [str(item).strip() for item in blockers or [] if str(item).strip()] or None,
        "failure_code": str(failure_code or "").strip() or None,
    }
    payload.update({key: value for key, value in optional.items() if value is not None})
    return payload


def _timeout_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("ENGINEER_SLACK_N8N_TIMEOUT_SECONDS") or "15"))
    except ValueError:
        return 15.0


def _request_json(request: urllib.request.Request, *, outcome_unknown: bool) -> Any:
    try:
        with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
            return json.loads(response.read().decode("utf-8"))
    except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
        raise EngineerSlackN8nError(
            "engineer_slack_n8n_request_failed", outcome_unknown=outcome_unknown
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EngineerSlackN8nError(
            "engineer_slack_n8n_invalid_response", outcome_unknown=outcome_unknown
        ) from exc


def _validate_response(
    payload: Any,
    *,
    event_id: str,
    allowed_statuses: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise EngineerSlackN8nError("engineer_slack_n8n_invalid_response")
    response_event_id = str(payload.get("event_id") or "").strip()
    status = str(payload.get("status") or "").strip().lower()
    if response_event_id != event_id or status not in allowed_statuses:
        raise EngineerSlackN8nError("engineer_slack_n8n_invalid_response")
    return {
        "event_id": response_event_id,
        "status": status,
        "failure_code": str(payload.get("failure_code") or "").strip() or None,
    }


def post_engineer_slack_event(event: dict[str, Any]) -> dict[str, Any]:
    event_id = str(event.get("event_id") or "").strip()
    request = urllib.request.Request(
        str(os.getenv("ENGINEER_SLACK_N8N_WEBHOOK_URL") or "").strip(),
        data=json.dumps(event, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-N8n-Request-Token": str(os.getenv("n8n_request_token") or "").strip(),
        },
    )
    try:
        return _validate_response(
            _request_json(request, outcome_unknown=True),
            event_id=event_id,
            allowed_statuses=ENGINEER_SLACK_DELIVERY_STATUSES,
        )
    except EngineerSlackN8nError as exc:
        raise EngineerSlackN8nError(exc.code, outcome_unknown=True) from exc


def get_engineer_slack_event_status(event_id: str) -> dict[str, Any]:
    normalized_event_id = str(event_id or "").strip()
    base_url = str(os.getenv("ENGINEER_SLACK_N8N_STATUS_URL") or "").strip()
    separator = "&" if "?" in base_url else "?"
    request = urllib.request.Request(
        f"{base_url}{separator}{urllib.parse.urlencode({'event_id': normalized_event_id})}",
        method="GET",
        headers={
            "Accept": "application/json",
            "X-N8n-Request-Token": str(os.getenv("n8n_request_token") or "").strip(),
        },
    )
    return _validate_response(
        _request_json(request, outcome_unknown=False),
        event_id=normalized_event_id,
        allowed_statuses=ENGINEER_SLACK_STATUS_STATUSES,
    )
