from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from backend.services.zendesk_comments import (
    ZendeskCommentError,
    zendesk_basic_auth_header,
)


ZENDESK_TICKET_API_BASE = "https://agoraio.zendesk.com/api/v2/tickets"
ZENDESK_USER_API_BASE = "https://agoraio.zendesk.com/api/v2/users"
ZENDESK_AI_ASSIGNEE_EMAIL_ENV = "ZENDESK_AI_ASSIGNEE_EMAIL"


@dataclass(frozen=True, slots=True)
class ZendeskAssignmentResult:
    ticket_id: str
    assignee_id: str
    assignee_email: str
    assignee_name: str
    group_id: str | None
    previous_group_id: str | None
    group_changed: bool
    status_code: int
    already_assigned: bool


def _assignment_error(
    category: str,
    *,
    status_code: int | None = None,
    error_code: str = "zendesk_assignment_failed",
) -> ZendeskCommentError:
    return ZendeskCommentError(category, status_code=status_code, error_code=error_code)


def _configured_assignee_email() -> str:
    assignee_email = str(os.getenv(ZENDESK_AI_ASSIGNEE_EMAIL_ENV) or "").strip().lower()
    if not assignee_email:
        raise _assignment_error("permanent", error_code="zendesk_assignee_config_missing")
    return assignee_email


def _resolve_configured_assignee(*, expected_email: str, timeout_seconds: float) -> tuple[str, dict[str, Any]]:
    user_payload, _ = _request(
        method="GET",
        url=f"{ZENDESK_USER_API_BASE}/me.json",
        timeout_seconds=timeout_seconds,
    )
    user = user_payload.get("user") if isinstance(user_payload.get("user"), dict) else {}
    if str(user.get("email") or "").strip().lower() != expected_email:
        raise _assignment_error("permanent", error_code="zendesk_assignee_invalid")
    assignee_id = str(user.get("id") or "").strip()
    role = str(user.get("role") or "").strip().lower()
    if (
        not assignee_id.isdigit()
        or not bool(user.get("active", False))
        or bool(user.get("suspended", False))
        or role != "agent"
    ):
        raise _assignment_error("permanent", error_code="zendesk_assignee_invalid")
    return assignee_id, user


def _decode_payload(response: Any) -> dict[str, Any]:
    try:
        raw = response.read()
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise _assignment_error("outcome_unknown", error_code="zendesk_response_invalid") from exc
    if not isinstance(payload, dict):
        raise _assignment_error("outcome_unknown", error_code="zendesk_response_invalid")
    return payload


def _request(
    *,
    method: str,
    url: str,
    data: dict[str, Any] | None = None,
    timeout_seconds: float,
) -> tuple[dict[str, Any], int]:
    request = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8") if data is not None else None,
        method=method,
        headers={
            "Authorization": zendesk_basic_auth_header(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            if status_code < 200 or status_code >= 300:
                category = "retryable" if status_code in {408, 425, 429} or status_code >= 500 else "permanent"
                raise _assignment_error(category, status_code=status_code, error_code="zendesk_http_error")
            return _decode_payload(response), status_code
    except ZendeskCommentError:
        raise
    except urllib.error.HTTPError as exc:
        status_code = int(exc.code or 0) or None
        category = "retryable" if status_code in {408, 425, 429} or (status_code is not None and status_code >= 500) else "permanent"
        raise _assignment_error(category, status_code=status_code, error_code="zendesk_http_error") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise _assignment_error("outcome_unknown", error_code="zendesk_network_outcome_unknown") from exc


def assign_ticket_to_configured_ai(
    *,
    ticket_id: str,
    timeout_seconds: float = 15.0,
) -> ZendeskAssignmentResult:
    normalized_ticket_id = str(ticket_id or "").strip()
    if not normalized_ticket_id:
        raise _assignment_error("permanent", error_code="zendesk_assignment_input_invalid")
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError):
        timeout = 15.0
    if timeout <= 0:
        timeout = 15.0

    expected_email = _configured_assignee_email()
    assignee_id, user = _resolve_configured_assignee(
        expected_email=expected_email,
        timeout_seconds=timeout,
    )
    actual_email = str(user.get("email") or "").strip().lower()

    ticket_payload, _ = _request(
        method="GET",
        url=f"{ZENDESK_TICKET_API_BASE}/{urllib.parse.quote(normalized_ticket_id, safe='')}.json",
        timeout_seconds=timeout,
    )
    current_ticket = ticket_payload.get("ticket") if isinstance(ticket_payload.get("ticket"), dict) else {}
    current_assignee_id = str(current_ticket.get("assignee_id") or "").strip()
    group_id = str(current_ticket.get("group_id") or "").strip() or None
    if current_assignee_id == assignee_id:
        return ZendeskAssignmentResult(
            ticket_id=normalized_ticket_id,
            assignee_id=assignee_id,
            assignee_email=actual_email,
            assignee_name=str(user.get("name") or expected_email).strip(),
            group_id=group_id,
            previous_group_id=group_id,
            group_changed=False,
            status_code=200,
            already_assigned=True,
        )

    updated_payload, status_code = _request(
        method="PUT",
        url=f"{ZENDESK_TICKET_API_BASE}/{urllib.parse.quote(normalized_ticket_id, safe='')}.json",
        data={"ticket": {"assignee_id": int(assignee_id)}},
        timeout_seconds=timeout,
    )
    updated_ticket = updated_payload.get("ticket") if isinstance(updated_payload.get("ticket"), dict) else {}
    if str(updated_ticket.get("assignee_id") or "").strip() != assignee_id:
        raise _assignment_error("outcome_unknown", status_code=status_code, error_code="zendesk_assignment_unverified")
    final_group_id = str(updated_ticket.get("group_id") or "").strip() or None
    return ZendeskAssignmentResult(
        ticket_id=normalized_ticket_id,
        assignee_id=assignee_id,
        assignee_email=actual_email,
        assignee_name=str(user.get("name") or expected_email).strip(),
        group_id=final_group_id,
        previous_group_id=group_id,
        group_changed=bool(group_id and final_group_id and group_id != final_group_id),
        status_code=status_code,
        already_assigned=False,
    )
