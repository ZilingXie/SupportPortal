from __future__ import annotations

import base64
import binascii
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from backend.services.customer_reply_composer import has_trailing_customer_signature


ZENDESK_TICKET_API_BASE = "https://agoraio.zendesk.com/api/v2/tickets"
ZENDESK_UPLOADS_API_BASE = "https://agoraio.zendesk.com/api/v2/uploads"
ZENDESK_BASIC_AUTH_ENV = "zendesk_basic_auth"
# This Zendesk workspace marks these checkbox fields as required-on-solve; the
# API rejects a solving PUT without them (422 RecordInvalid).
ZENDESK_SOLVE_REQUIRED_CHECKBOX_FIELDS = ("36379228408724",)

# Matches ```lang\n...\n``` fenced code blocks in Markdown-style bodies.
_FENCED_CODE_RE = re.compile(r"```([A-Za-z0-9_+-]*)\n(.*?)```", re.DOTALL)


def _fenced_code_html_body(body: str) -> str | None:
    """Render a plain-text body with fenced code blocks as Zendesk html_body.

    Returns None when the body has no fenced code block, so callers keep the
    plain-text-only path (and the audit reconciliation stays byte-identical).
    """
    matches = list(_FENCED_CODE_RE.finditer(body))
    if not matches:
        return None
    parts: list[str] = []
    cursor = 0
    for match in matches:
        before = body[cursor : match.start()].strip()
        if before:
            parts.append(f"<p>{html.escape(before).replace(chr(10), '<br>')}</p>")
        parts.append(f"<pre><code>{html.escape(match.group(2).rstrip())}</code></pre>")
        cursor = match.end()
    trailing = body[cursor:].strip()
    if trailing:
        parts.append(f"<p>{html.escape(trailing).replace(chr(10), '<br>')}</p>")
    return "".join(parts)


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
        detail: str | None = None,
    ) -> None:
        self.category = str(category or "permanent").strip().lower() or "permanent"
        self.status_code = status_code
        self.error_code = str(error_code or "zendesk_comment_failed").strip() or "zendesk_comment_failed"
        self.detail = (str(detail).strip() or None) if detail is not None else None
        super().__init__(self.error_code)


def zendesk_basic_auth_header() -> str:
    raw = str(os.getenv(ZENDESK_BASIC_AUTH_ENV) or "").strip()
    if raw.lower().startswith("basic "):
        raw = raw[6:].strip()
    if not raw:
        raise ZendeskCommentError("permanent", error_code="zendesk_basic_auth_missing")
    # The deployment stores either the literal "username:token" or its base64
    # form. A literal always contains ":"; the base64 alphabet never does, so
    # the two forms are unambiguous.
    if ":" in raw:
        decoded = raw
    else:
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
    if str(os.getenv("AUTOMATION_ENVIRONMENT") or "").strip().lower() == "staging":
        raise ZendeskCommentError(
            "permanent",
            error_code="zendesk_outbound_forbidden_staging",
            detail="staging automation cannot access Zendesk",
        )
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


def _audit_body_matches(actual: Any, expected: str) -> bool:
    """Exact match, or the platform appended a signature/footer after our body.

    Zendesk appends the agent's signature and marketing footer to public
    comments; the stored audit body then equals our content plus that suffix.
    """
    normalized_actual = str(actual or "").strip()
    if not normalized_actual:
        return False
    return normalized_actual == expected or normalized_actual.startswith(expected)


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
                and _audit_body_matches(event.get("body"), normalized_body)
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


def upload_ticket_attachment(
    *,
    filename: str,
    data: bytes,
    content_type: str = "application/pdf",
    timeout_seconds: float = 60.0,
) -> str:
    """Upload one binary attachment and return its Zendesk upload token.

    An upload that is never attached to a comment is inert on the Zendesk side,
    so an unreadable response is safely retryable rather than outcome-unknown:
    re-uploading only orphans the previous file.
    """
    normalized_filename = str(filename or "").strip()
    normalized_content_type = str(content_type or "").strip() or "application/pdf"
    if not normalized_filename or not isinstance(data, (bytes, bytearray)) or not data:
        raise ZendeskCommentError("permanent", error_code="zendesk_upload_input_invalid")
    url = f"{ZENDESK_UPLOADS_API_BASE}?{urllib.parse.urlencode({'filename': normalized_filename})}"
    request = urllib.request.Request(
        url,
        data=bytes(data),
        method="POST",
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": normalized_content_type,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_request_timeout(timeout_seconds)) as response:
            status_code = int(getattr(response, "status", 201) or 201)
            if status_code < 200 or status_code >= 300:
                raise ZendeskCommentError(
                    _http_status_category(status_code),
                    status_code=status_code,
                    error_code="zendesk_upload_http_error",
                )
            payload = _decode_json_response(response)
    except ZendeskCommentError as exc:
        raise ZendeskCommentError(
            "retryable" if exc.category == "outcome_unknown" else exc.category,
            status_code=exc.status_code,
            error_code=exc.error_code,
            detail=exc.detail,
        ) from exc
    except urllib.error.HTTPError as exc:
        error = _zendesk_request_error(exc)
        raise ZendeskCommentError(
            error.category,
            status_code=error.status_code,
            error_code="zendesk_upload_http_error",
            detail=error.detail,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ZendeskCommentError("retryable", error_code="zendesk_upload_network_failed") from exc
    upload = payload.get("upload") if isinstance(payload.get("upload"), dict) else None
    token = str((upload or {}).get("token") or "").strip()
    if not token:
        raise ZendeskCommentError("retryable", error_code="zendesk_upload_token_missing")
    return token


def add_ticket_comment(
    *,
    ticket_id: str,
    body: str,
    public: bool = False,
    solve: bool = False,
    uploads: tuple[str, ...] | list[str] | None = None,
    timeout_seconds: float = 15.0,
) -> ZendeskCommentResult:
    normalized_ticket_id = str(ticket_id or "").strip()
    normalized_body = str(body or "").strip()
    expected_public = bool(public)
    if not normalized_ticket_id or not normalized_body:
        raise ZendeskCommentError("permanent", error_code="zendesk_comment_input_invalid")
    if expected_public and has_trailing_customer_signature(normalized_body):
        raise ZendeskCommentError(
            "permanent",
            error_code="zendesk_public_comment_signature_forbidden",
        )
    timeout = _request_timeout(timeout_seconds)
    url = f"{ZENDESK_TICKET_API_BASE}/{urllib.parse.quote(normalized_ticket_id, safe='')}.json"
    comment_payload: dict[str, Any] = {"body": normalized_body, "public": expected_public}
    html_body = _fenced_code_html_body(normalized_body)
    if html_body:
        comment_payload["html_body"] = html_body
    normalized_uploads = tuple(
        token for token in (str(item or "").strip() for item in (uploads or ())) if token
    )
    if normalized_uploads:
        comment_payload["uploads"] = list(normalized_uploads)
    ticket_payload: dict[str, Any] = {"comment": comment_payload}
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


def update_ticket_status(
    *,
    ticket_id: str,
    status: str,
    timeout_seconds: float = 15.0,
) -> str:
    """Update and read back a Zendesk ticket status without adding a comment."""
    normalized_ticket_id = str(ticket_id or "").strip()
    normalized_status = str(status or "").strip().lower()
    if not normalized_ticket_id or not normalized_status:
        raise ZendeskCommentError("permanent", error_code="zendesk_ticket_status_input_invalid")
    url = f"{ZENDESK_TICKET_API_BASE}/{urllib.parse.quote(normalized_ticket_id, safe='')}.json"
    request = urllib.request.Request(
        url,
        data=json.dumps({"ticket": {"status": normalized_status}}, ensure_ascii=False).encode("utf-8"),
        method="PUT",
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
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
        raise ZendeskCommentError("outcome_unknown", error_code="zendesk_status_update_outcome_unknown") from exc
    observed = _ticket_status_from_payload(payload)
    if observed != normalized_status:
        raise ZendeskCommentError("outcome_unknown", error_code="zendesk_ticket_status_unverified")
    return observed
