from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


ACCOUNT_SLACK_EVENT_TYPE = "account_automation_handoff_confirmed"
ACCOUNT_SLACK_SCHEMA_VERSION = 1
ACCOUNT_SLACK_DELIVERY_STATUSES = frozenset(
    {"pending", "delivered", "failed", "outcome_unknown"}
)
ACCOUNT_SLACK_STATUS_STATUSES = ACCOUNT_SLACK_DELIVERY_STATUSES | {"missing"}

_SLACK_CASE_LABELS = {
    ("fraud_account", "fraud_handoff_confirmation"): "Fraud Account",
    (
        "account_suspension",
        "account_suspension_handoff_and_close",
    ): "Account Suspension",
}


@dataclass(frozen=True)
class AccountSlackN8nError(RuntimeError):
    code: str
    outcome_unknown: bool = False

    def __str__(self) -> str:
        return self.code


def account_slack_n8n_configured() -> bool:
    urls = (
        str(os.getenv("ACCOUNT_SLACK_N8N_WEBHOOK_URL") or "").strip(),
        str(os.getenv("ACCOUNT_SLACK_N8N_STATUS_URL") or "").strip(),
    )
    token = str(os.getenv("n8n_request_token") or "").strip()
    return bool(token) and all(
        (parsed := urllib.parse.urlparse(value)).scheme in {"http", "https"}
        and bool(parsed.netloc)
        for value in urls
    )


def build_account_slack_event(
    *,
    account_case: dict[str, Any],
    message_id: str,
    reply_intent: str,
) -> dict[str, Any] | None:
    normalized_intent = str(reply_intent or "").strip().lower()
    execution_action = str(
        account_case.get("execution_action")
        or account_case.get("route")
        or account_case.get("subcategory")
        or ""
    ).strip().lower()
    case_label = _SLACK_CASE_LABELS.get((execution_action, normalized_intent))
    if case_label is None:
        return None
    account_case_id = str(account_case.get("account_case_id") or "").strip()
    normalized_message_id = str(message_id or "").strip()
    zendesk_ticket_id = str(account_case.get("zendesk_ticket_id") or "").strip()
    title = " ".join(str(account_case.get("title") or "").split())
    summary = " ".join(str(account_case.get("question") or "").split()) or title
    if not all((account_case_id, normalized_message_id, zendesk_ticket_id, title)):
        raise ValueError("Slack handoff event requires case, message, Zendesk ticket, and title")
    event_id = f"account-automation-slack:{account_case_id}:{normalized_message_id}"
    zendesk_url = f"https://agoraio.zendesk.com/agent/tickets/{urllib.parse.quote(zendesk_ticket_id, safe='')}"
    message_text = f"[{case_label}] {title}\nzendesk: {zendesk_url}\n{summary}"
    return {
        "schema_version": ACCOUNT_SLACK_SCHEMA_VERSION,
        "event_id": event_id,
        "event_type": ACCOUNT_SLACK_EVENT_TYPE,
        "account_case_id": account_case_id,
        "message_id": normalized_message_id,
        "reply_intent": normalized_intent,
        "case_type": case_label,
        "case_title": title,
        "zendesk_ticket_id": zendesk_ticket_id,
        "zendesk_url": zendesk_url,
        "ticket_summary": summary,
        "message_text": message_text,
    }


def _timeout_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("ACCOUNT_SLACK_N8N_TIMEOUT_SECONDS") or "15"))
    except ValueError:
        return 15.0


def _validate_response(
    payload: Any,
    *,
    event_id: str,
    allowed_statuses: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AccountSlackN8nError("account_slack_n8n_invalid_response")
    response_event_id = str(payload.get("event_id") or "").strip()
    status = str(payload.get("status") or "").strip().lower()
    if response_event_id != event_id or status not in allowed_statuses:
        raise AccountSlackN8nError("account_slack_n8n_invalid_response")
    return {
        "event_id": response_event_id,
        "status": status,
        "failure_code": str(payload.get("failure_code") or "").strip() or None,
    }


def _request_json(request: urllib.request.Request, *, outcome_unknown: bool) -> Any:
    try:
        with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
            return json.loads(response.read().decode("utf-8"))
    except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
        raise AccountSlackN8nError(
            "account_slack_n8n_request_failed", outcome_unknown=outcome_unknown
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AccountSlackN8nError(
            "account_slack_n8n_invalid_response", outcome_unknown=outcome_unknown
        ) from exc


def post_account_slack_event(event: dict[str, Any]) -> dict[str, Any]:
    event_id = str(event.get("event_id") or "").strip()
    request = urllib.request.Request(
        str(os.getenv("ACCOUNT_SLACK_N8N_WEBHOOK_URL") or "").strip(),
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
            allowed_statuses=ACCOUNT_SLACK_DELIVERY_STATUSES,
        )
    except AccountSlackN8nError as exc:
        raise AccountSlackN8nError(exc.code, outcome_unknown=True) from exc


def get_account_slack_event_status(event_id: str) -> dict[str, Any]:
    normalized_event_id = str(event_id or "").strip()
    base_url = str(os.getenv("ACCOUNT_SLACK_N8N_STATUS_URL") or "").strip()
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
        allowed_statuses=ACCOUNT_SLACK_STATUS_STATUSES,
    )
