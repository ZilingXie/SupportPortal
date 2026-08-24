from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, uuid5


ENGINEER_SLACK_SCHEMA_VERSION = 1
SLACK_CHAT_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
_SLACK_ACTIONS = frozenset({"guardrail", "final_approve"})


@dataclass(frozen=True)
class EngineerSlackDeliveryError(RuntimeError):
    code: str
    outcome_unknown: bool = False

    def __str__(self) -> str:
        return self.code


def engineer_slack_configured() -> bool:
    token = str(os.getenv("ENGINEER_SLACK_ACCESS_TOKEN") or "").strip()
    team_id = str(os.getenv("ENGINEER_SLACK_TEAM_ID") or "").strip()
    channel_id = str(os.getenv("ENGINEER_SLACK_CHANNEL_ID") or "").strip()
    return token.startswith(("xoxb-", "xoxp-")) and bool(team_id and channel_id)


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
        return max(1.0, float(os.getenv("ENGINEER_SLACK_TIMEOUT_SECONDS") or "15"))
    except ValueError:
        return 15.0


def _failure_code(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(value or "unknown").strip().lower())
    return f"engineer_slack_api_{normalized.strip('_') or 'unknown'}"


def _action_blocks(event: dict[str, Any]) -> list[dict[str, Any]] | None:
    action = str(event.get("action") or "").strip()
    if not action:
        return None
    if action not in _SLACK_ACTIONS:
        raise EngineerSlackDeliveryError("engineer_slack_action_invalid")
    investigation_id = str(event.get("investigation_id") or "").strip()
    try:
        draft_version = int(event.get("draft_version") or 0)
    except (TypeError, ValueError) as exc:
        raise EngineerSlackDeliveryError("engineer_slack_action_payload_invalid") from exc
    if not investigation_id or draft_version < 1:
        raise EngineerSlackDeliveryError("engineer_slack_action_payload_invalid")
    value = json.dumps(
        {
            "action": action,
            "investigation_id": investigation_id,
            "draft_version": draft_version,
            "guardrail_id": str(event.get("guardrail_id") or "").strip() or None,
            "guardrail_version": str(event.get("guardrail_version") or "").strip() or None,
        },
        separators=(",", ":"),
    )
    button: dict[str, Any] = {
        "type": "button",
        "text": {
            "type": "plain_text",
            "text": "Approve & publish" if action == "final_approve" else "Run guardrail",
        },
        "action_id": action,
        "value": value,
    }
    if action == "final_approve":
        button["style"] = "primary"
    message_text = str(event.get("message_text") or "").strip()
    sections = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": message_text[offset : offset + 3000]},
        }
        for offset in range(0, len(message_text), 3000)
    ]
    return [*sections, {"type": "actions", "elements": [button]}]


def _message_payload(event: dict[str, Any], *, thread_ts: str | None) -> dict[str, Any]:
    event_id = str(event.get("event_id") or "").strip()
    event_type = str(event.get("event_type") or "").strip()
    message_text = str(event.get("message_text") or "").strip()
    if not all((event_id, event_type, message_text)):
        raise EngineerSlackDeliveryError("engineer_slack_event_invalid")
    is_root = event_type == "engineer_case_opened"
    normalized_thread_ts = str(thread_ts or "").strip()
    if not is_root and not normalized_thread_ts:
        raise EngineerSlackDeliveryError("engineer_slack_thread_binding_missing")
    payload: dict[str, Any] = {
        "channel": str(os.getenv("ENGINEER_SLACK_CHANNEL_ID") or "").strip(),
        "text": message_text,
        "client_msg_id": str(uuid5(NAMESPACE_URL, event_id)),
    }
    if not is_root:
        payload["thread_ts"] = normalized_thread_ts
        blocks = _action_blocks(event)
        if blocks is not None:
            payload["blocks"] = blocks
    return payload


def post_engineer_slack_event(
    event: dict[str, Any], *, thread_ts: str | None = None
) -> dict[str, Any]:
    if not engineer_slack_configured():
        raise EngineerSlackDeliveryError("engineer_slack_config_incomplete")
    request_payload = _message_payload(event, thread_ts=thread_ts)
    request = urllib.request.Request(
        SLACK_CHAT_POST_MESSAGE_URL,
        data=json.dumps(request_payload, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": (
                "Bearer " + str(os.getenv("ENGINEER_SLACK_ACCESS_TOKEN") or "").strip()
            ),
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        raise EngineerSlackDeliveryError(
            _failure_code(payload.get("error") or f"http_{exc.code}"),
            outcome_unknown=exc.code >= 500,
        ) from exc
    except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
        raise EngineerSlackDeliveryError(
            "engineer_slack_request_failed", outcome_unknown=True
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EngineerSlackDeliveryError(
            "engineer_slack_response_invalid", outcome_unknown=True
        ) from exc

    if not isinstance(response_payload, dict) or not response_payload.get("ok"):
        raise EngineerSlackDeliveryError(
            _failure_code(
                response_payload.get("error")
                if isinstance(response_payload, dict)
                else "invalid_response"
            )
        )
    expected_channel = str(os.getenv("ENGINEER_SLACK_CHANNEL_ID") or "").strip()
    channel_id = str(response_payload.get("channel") or "").strip()
    message_ts = str(response_payload.get("ts") or "").strip()
    if channel_id != expected_channel or not message_ts:
        raise EngineerSlackDeliveryError(
            "engineer_slack_response_invalid", outcome_unknown=True
        )
    return {
        "event_id": str(event.get("event_id") or "").strip(),
        "status": "delivered",
        "failure_code": None,
        "slack_channel_id": channel_id,
        "slack_message_ts": message_ts,
        "slack_thread_ts": str(thread_ts or "").strip() or message_ts,
    }
