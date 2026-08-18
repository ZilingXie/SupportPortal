from __future__ import annotations

import base64
import binascii
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


ZENDESK_TICKET_API_BASE = "https://agoraio.zendesk.com/api/v2/tickets"
ZENDESK_BASIC_AUTH_ENV = "zendesk_basic_auth"


@dataclass(frozen=True, slots=True)
class ZendeskCommentResult:
    comment_id: str | None
    status_code: int


class ZendeskCommentError(RuntimeError):
    """A sanitized Zendesk write failure suitable for an API boundary."""

    def __init__(
        self,
        category: str,
        *,
        status_code: int | None = None,
        error_code: str = "zendesk_comment_failed",
    ) -> None:
        self.category = str(category or "permanent").strip().lower() or "permanent"
        self.status_code = status_code
        self.error_code = str(error_code or "zendesk_comment_failed").strip() or "zendesk_comment_failed"
        super().__init__(self.error_code)


def zendesk_basic_auth_header() -> str:
    raw = str(os.getenv(ZENDESK_BASIC_AUTH_ENV) or "").strip()
    if raw.lower().startswith("basic "):
        raw = raw[6:].strip()
    if not raw:
        raise ZendeskCommentError("permanent", error_code="zendesk_basic_auth_missing")
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ZendeskCommentError("permanent", error_code="zendesk_basic_auth_invalid") from exc
    if ":" not in decoded:
        raise ZendeskCommentError("permanent", error_code="zendesk_basic_auth_invalid")
    username, secret = decoded.split(":", 1)
    if not username or not secret:
        raise ZendeskCommentError("permanent", error_code="zendesk_basic_auth_invalid")
    return "Basic " + base64.b64encode(decoded.encode("utf-8")).decode("ascii")


def _basic_auth_header() -> str:
    return zendesk_basic_auth_header()


def _comment_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    audit = payload.get("audit") if isinstance(payload.get("audit"), dict) else {}
    events = audit.get("events") if isinstance(audit.get("events"), list) else []
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        if str(event.get("type") or "").strip().lower() == "comment":
            return event
    return None


def _decode_json_response(response: Any) -> dict[str, Any]:
    try:
        raw = response.read()
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ZendeskCommentError("outcome_unknown", error_code="zendesk_response_invalid") from exc
    if not isinstance(payload, dict):
        raise ZendeskCommentError("outcome_unknown", error_code="zendesk_response_invalid")
    return payload


def add_internal_comment(*, ticket_id: str, body: str, timeout_seconds: float = 15.0) -> ZendeskCommentResult:
    normalized_ticket_id = str(ticket_id or "").strip()
    normalized_body = str(body or "").strip()
    if not normalized_ticket_id or not normalized_body:
        raise ZendeskCommentError("permanent", error_code="zendesk_comment_input_invalid")
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError):
        timeout = 15.0
    if timeout <= 0:
        timeout = 15.0
    url = f"{ZENDESK_TICKET_API_BASE}/{urllib.parse.quote(normalized_ticket_id, safe='')}.json"
    request = urllib.request.Request(
        url,
        data=json.dumps(
            {"ticket": {"comment": {"body": normalized_body, "public": False}}},
            ensure_ascii=False,
        ).encode("utf-8"),
        method="PUT",
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            if status_code < 200 or status_code >= 300:
                category = "retryable" if status_code in {408, 425, 429} or status_code >= 500 else "permanent"
                raise ZendeskCommentError(category, status_code=status_code, error_code="zendesk_http_error")
            payload = _decode_json_response(response)
    except ZendeskCommentError:
        raise
    except urllib.error.HTTPError as exc:
        status_code = int(exc.code or 0) or None
        category = "retryable" if status_code in {408, 425, 429} or (status_code is not None and status_code >= 500) else "permanent"
        raise ZendeskCommentError(category, status_code=status_code, error_code="zendesk_http_error") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ZendeskCommentError("outcome_unknown", error_code="zendesk_network_outcome_unknown") from exc

    comment = _comment_event(payload)
    if not isinstance(comment, dict) or comment.get("public") is not False:
        raise ZendeskCommentError("outcome_unknown", error_code="zendesk_comment_visibility_unverified")
    comment_id = comment.get("id")
    normalized_comment_id = str(comment_id).strip() if comment_id is not None else None
    return ZendeskCommentResult(comment_id=normalized_comment_id or None, status_code=status_code)
