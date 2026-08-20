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
# This Zendesk workspace marks these checkbox fields as required-on-solve; the
# API rejects a solving PUT without them (422 RecordInvalid).
ZENDESK_SOLVE_REQUIRED_CHECKBOX_FIELDS = ("36379228408724",)


@dataclass(frozen=True, slots=True)
class ZendeskCommentResult:
    comment_id: str | None
    status_code: int
    ticket_status: str | None = None


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


def _ticket_status_from_payload(payload: dict[str, Any]) -> str | None:
    ticket = payload.get("ticket") if isinstance(payload.get("ticket"), dict) else None
    if ticket is None:
        return None
    return str(ticket.get("status") or "").strip().lower() or None


def _decode_json_response(response: Any) -> dict[str, Any]:
    try:
        raw = response.read()
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ZendeskCommentError("outcome_unknown", error_code="zendesk_response_invalid") from exc
    if not isinstance(payload, dict):
        raise ZendeskCommentError("outcome_unknown", error_code="zendesk_response_invalid")
    return payload


def _request_timeout(timeout_seconds: float) -> float:
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError):
        timeout = 15.0
    return timeout if timeout > 0 else 15.0


def _zendesk_request_error(exc: urllib.error.HTTPError) -> ZendeskCommentError:
    status_code = int(exc.code or 0) or None
    category = (
        "retryable"
        if status_code in {408, 425, 429} or (status_code is not None and status_code >= 500)
        else "permanent"
    )
    return ZendeskCommentError(category, status_code=status_code, error_code="zendesk_http_error")


def _http_status_category(status_code: int) -> str:
    return "retryable" if status_code in {408, 425, 429} or status_code >= 500 else "permanent"


def _fetch_ticket_audits(
    *,
    ticket_id: str,
    timeout_seconds: float,
) -> tuple[int, list[dict[str, Any]]]:
    url = (
        f"{ZENDESK_TICKET_API_BASE}/{urllib.parse.quote(str(ticket_id), safe='')}/audits.json"
    )
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": _basic_auth_header(), "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_request_timeout(timeout_seconds)) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            if status_code < 200 or status_code >= 300:
                raise ZendeskCommentError(
                    _http_status_category(status_code), status_code=status_code, error_code="zendesk_http_error"
                )
            payload = _decode_json_response(response)
    except ZendeskCommentError:
        raise
    except urllib.error.HTTPError as exc:
        raise _zendesk_request_error(exc) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ZendeskCommentError("outcome_unknown", error_code="zendesk_audit_read_outcome_unknown") from exc
    audits = payload.get("audits") if isinstance(payload.get("audits"), list) else []
    return status_code, audits


def _audit_contains_solved_change(audits: list[dict[str, Any]]) -> bool:
    for audit in audits:
        events = audit.get("events") if isinstance(audit, dict) and isinstance(audit.get("events"), list) else []
        for event in events:
            if not isinstance(event, dict):
                continue
            if (
                str(event.get("type") or "").strip().lower() == "change"
                and str(event.get("field_name") or "").strip().lower() == "status"
                and str(event.get("value") or "").strip().lower() == "solved"
            ):
                return True
    return False


def read_ticket_comment_audit(
    *,
    ticket_id: str,
    body: str,
    public: bool = False,
    timeout_seconds: float = 15.0,
) -> tuple[ZendeskCommentResult | None, bool]:
    """Locate the exact comment in ticket audits and report whether a solved change exists.

    The solved flag distinguishes "solve never happened" from "solved then reopened
    by the requester", so a later reopen does not break delivery reconciliation.
    """
    normalized_ticket_id = str(ticket_id or "").strip()
    normalized_body = str(body or "").strip()
    expected_public = bool(public)
    if not normalized_ticket_id or not normalized_body:
        raise ZendeskCommentError("permanent", error_code="zendesk_comment_input_invalid")
    status_code, audits = _fetch_ticket_audits(
        ticket_id=normalized_ticket_id,
        timeout_seconds=timeout_seconds,
    )
    solved_seen = _audit_contains_solved_change(audits)
    for audit in audits:
        events = audit.get("events") if isinstance(audit, dict) and isinstance(audit.get("events"), list) else []
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            if (
                str(event.get("type") or "").strip().lower() == "comment"
                and event.get("public") is expected_public
                and str(event.get("body") or "").strip() == normalized_body
            ):
                comment_id = str(event.get("id") or "").strip() or None
                return (
                    ZendeskCommentResult(comment_id=comment_id, status_code=status_code),
                    solved_seen,
                )
    return None, solved_seen


def find_ticket_comment(
    *,
    ticket_id: str,
    body: str,
    public: bool = False,
    timeout_seconds: float = 15.0,
) -> ZendeskCommentResult | None:
    """Read recent ticket audits and locate the exact comment with the expected visibility."""
    comment, _solved_seen = read_ticket_comment_audit(
        ticket_id=ticket_id,
        body=body,
        public=public,
        timeout_seconds=timeout_seconds,
    )
    return comment


def find_private_internal_comment(
    *,
    ticket_id: str,
    body: str,
    timeout_seconds: float = 15.0,
) -> ZendeskCommentResult | None:
    return find_ticket_comment(
        ticket_id=ticket_id,
        body=body,
        public=False,
        timeout_seconds=timeout_seconds,
    )


def get_ticket_status(*, ticket_id: str, timeout_seconds: float = 15.0) -> str | None:
    """Read the current Zendesk ticket status without writing."""
    normalized_ticket_id = str(ticket_id or "").strip()
    if not normalized_ticket_id:
        raise ZendeskCommentError("permanent", error_code="zendesk_comment_input_invalid")
    url = f"{ZENDESK_TICKET_API_BASE}/{urllib.parse.quote(normalized_ticket_id, safe='')}.json"
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": _basic_auth_header(), "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_request_timeout(timeout_seconds)) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            if status_code < 200 or status_code >= 300:
                raise ZendeskCommentError(
                    _http_status_category(status_code), status_code=status_code, error_code="zendesk_http_error"
                )
            payload = _decode_json_response(response)
    except ZendeskCommentError:
        raise
    except urllib.error.HTTPError as exc:
        raise _zendesk_request_error(exc) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ZendeskCommentError("outcome_unknown", error_code="zendesk_network_outcome_unknown") from exc
    return _ticket_status_from_payload(payload)


def add_ticket_comment(
    *,
    ticket_id: str,
    body: str,
    public: bool = False,
    solve: bool = False,
    timeout_seconds: float = 15.0,
) -> ZendeskCommentResult:
    normalized_ticket_id = str(ticket_id or "").strip()
    normalized_body = str(body or "").strip()
    expected_public = bool(public)
    if not normalized_ticket_id or not normalized_body:
        raise ZendeskCommentError("permanent", error_code="zendesk_comment_input_invalid")
    timeout = _request_timeout(timeout_seconds)
    url = f"{ZENDESK_TICKET_API_BASE}/{urllib.parse.quote(normalized_ticket_id, safe='')}.json"
    ticket_payload: dict[str, Any] = {
        "comment": {"body": normalized_body, "public": expected_public}
    }
    if solve:
        # solved (not closed) keeps the requester able to reopen by replying.
        ticket_payload["status"] = "solved"
        # Zendesk silently ignores flat field_<id> keys for these required
        # checkbox validations; only the custom_fields array form satisfies
        # the required-on-solve check.
        ticket_payload["custom_fields"] = [
            {"id": int(field_id), "value": True}
            for field_id in ZENDESK_SOLVE_REQUIRED_CHECKBOX_FIELDS
        ]
    request = urllib.request.Request(
        url,
        data=json.dumps({"ticket": ticket_payload}, ensure_ascii=False).encode("utf-8"),
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
                raise ZendeskCommentError(
                    _http_status_category(status_code), status_code=status_code, error_code="zendesk_http_error"
                )
            payload = _decode_json_response(response)
    except ZendeskCommentError:
        raise
    except urllib.error.HTTPError as exc:
        raise _zendesk_request_error(exc) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ZendeskCommentError("outcome_unknown", error_code="zendesk_network_outcome_unknown") from exc

    comment = _comment_event(payload)
    if not isinstance(comment, dict) or comment.get("public") is not expected_public:
        raise ZendeskCommentError("outcome_unknown", error_code="zendesk_comment_visibility_unverified")
    ticket_status = _ticket_status_from_payload(payload)
    if solve and ticket_status != "solved":
        raise ZendeskCommentError("outcome_unknown", error_code="zendesk_ticket_status_unverified")
    comment_id = comment.get("id")
    normalized_comment_id = str(comment_id).strip() if comment_id is not None else None
    return ZendeskCommentResult(
        comment_id=normalized_comment_id or None,
        status_code=status_code,
        ticket_status=ticket_status,
    )


def add_internal_comment(*, ticket_id: str, body: str, timeout_seconds: float = 15.0) -> ZendeskCommentResult:
    return add_ticket_comment(
        ticket_id=ticket_id,
        body=body,
        public=False,
        solve=False,
        timeout_seconds=timeout_seconds,
    )
