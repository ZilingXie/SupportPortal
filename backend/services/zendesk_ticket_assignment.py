from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from backend.services.account_zendesk_comments import normalize_snapshot
from backend.services.zendesk_comments import (
    ZendeskCommentError,
    zendesk_basic_auth_header,
)


ZENDESK_TICKET_API_BASE = "https://agoraio.zendesk.com/api/v2/tickets"
ZENDESK_USER_API_BASE = "https://agoraio.zendesk.com/api/v2/users"
ZENDESK_AI_ASSIGNEE_EMAIL_ENV = "ZENDESK_AI_ASSIGNEE_EMAIL"
# Fraud review handoff: the Zendesk agent that receives a fraud_account case
# after the automated public reply has been published.
ZENDESK_FRAUD_REVIEW_ASSIGNEE_EMAIL_ENV = "ZENDESK_FRAUD_REVIEW_ASSIGNEE_EMAIL"
# Zendesk ticket fields with field-level `required` reject every API ticket
# update with 422 RecordInvalid "... needed" while they are empty, which is
# what blocked the AI ownership assignment PUT on AC-12878/12879/12880/12893.
# Fill the default product only when the ticket has no value yet; never
# overwrite a value a human already chose.
ZENDESK_ASSIGNMENT_REQUIRED_FIELD_ID = "31503099534100"
ZENDESK_ASSIGNMENT_REQUIRED_FIELD_VALUE = "video_calling"


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


@dataclass(frozen=True, slots=True)
class ZendeskOwnershipSnapshot:
    ticket_id: str
    assignee_id: str | None
    group_id: str | None
    ticket_updated_at: str
    ai_assignee_id: str
    ai_group_id: str
    human_replied: bool
    blocking_comment_id: str | None
    unresolved_public_comment_id: str | None
    required_field_missing: bool = True


def _assignment_required_field_missing(ticket: dict[str, Any]) -> bool:
    for entry in ticket.get("custom_fields") or []:
        if (
            isinstance(entry, dict)
            and str(entry.get("id") or "") == ZENDESK_ASSIGNMENT_REQUIRED_FIELD_ID
        ):
            return not str(entry.get("value") or "").strip()
    return True


def _assignment_error(
    category: str,
    *,
    status_code: int | None = None,
    error_code: str = "zendesk_assignment_failed",
    detail: str | None = None,
) -> ZendeskCommentError:
    return ZendeskCommentError(category, status_code=status_code, error_code=error_code, detail=detail)


def _http_error_detail(exc: urllib.error.HTTPError) -> str | None:
    """Best-effort Zendesk error message from a failed response body.

    Zendesk 422 rejections carry the field-level rejection reason only in the
    ``details`` member (for example which assignment field the omnichannel
    routing window refused), so it is appended verbatim to distinguish routing
    collisions from other validation failures without reading ticket audits.
    """
    try:
        raw = exc.read()
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        payload = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, str):
        message = str(payload.get("message") or "").strip() or error
    elif isinstance(error, dict):
        message = str(error.get("message") or error.get("title") or "").strip()
    else:
        message = str(payload.get("message") or payload.get("description") or "").strip()
    parts = [message] if message.strip() else []
    details = payload.get("details")
    if isinstance(details, (dict, list)):
        try:
            parts.append(json.dumps(details, ensure_ascii=False, separators=(",", ":")))
        except (TypeError, ValueError):
            pass
    elif isinstance(details, str) and details.strip():
        parts.append(details.strip())
    combined = " | ".join(part.strip() for part in parts if part and part.strip())
    return combined[:1000] if combined else None


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


def _configured_assignee_group_id(user: dict[str, Any]) -> str:
    group_id = str(user.get("default_group_id") or "").strip()
    if not group_id.isdigit():
        raise _assignment_error("permanent", error_code="zendesk_assignee_group_invalid")
    return group_id


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
                error_code = "zendesk_update_conflict" if status_code == 409 else "zendesk_http_error"
                raise _assignment_error(category, status_code=status_code, error_code=error_code)
            return _decode_payload(response), status_code
    except ZendeskCommentError:
        raise
    except urllib.error.HTTPError as exc:
        status_code = int(exc.code or 0) or None
        category = "retryable" if status_code in {408, 425, 429} or (status_code is not None and status_code >= 500) else "permanent"
        error_code = "zendesk_update_conflict" if status_code == 409 else "zendesk_http_error"
        raise _assignment_error(
            category,
            status_code=status_code,
            error_code=error_code,
            detail=_http_error_detail(exc),
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise _assignment_error("outcome_unknown", error_code="zendesk_network_outcome_unknown") from exc


def read_ticket_assignment(
    *,
    ticket_id: str,
    timeout_seconds: float = 15.0,
) -> tuple[str | None, str | None]:
    """Read the current assignee and group of a ticket without writing."""
    normalized_ticket_id = str(ticket_id or "").strip()
    if not normalized_ticket_id:
        raise _assignment_error("permanent", error_code="zendesk_assignment_input_invalid")
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError):
        timeout = 15.0
    if timeout <= 0:
        timeout = 15.0
    ticket_payload, _ = _request(
        method="GET",
        url=f"{ZENDESK_TICKET_API_BASE}/{urllib.parse.quote(normalized_ticket_id, safe='')}.json",
        timeout_seconds=timeout,
    )
    ticket = ticket_payload.get("ticket") if isinstance(ticket_payload.get("ticket"), dict) else {}
    assignee_id = str(ticket.get("assignee_id") or "").strip() or None
    group_id = str(ticket.get("group_id") or "").strip() or None
    return assignee_id, group_id


def configured_ai_assignee_id(*, timeout_seconds: float = 15.0) -> str:
    """Resolve the configured AI agent user id without touching a ticket."""
    expected_email = _configured_assignee_email()
    assignee_id, _user = _resolve_configured_assignee(
        expected_email=expected_email,
        timeout_seconds=timeout_seconds,
    )
    return assignee_id


def read_ticket_ownership_snapshot(
    *,
    ticket_id: str,
    timeout_seconds: float = 15.0,
) -> ZendeskOwnershipSnapshot:
    """Read the current assignment and complete comment history without writing."""
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
    ai_assignee_id, ai_user = _resolve_configured_assignee(
        expected_email=expected_email,
        timeout_seconds=timeout,
    )
    ai_group_id = _configured_assignee_group_id(ai_user)
    quoted_ticket_id = urllib.parse.quote(normalized_ticket_id, safe="")
    ticket_payload, _ = _request(
        method="GET",
        url=f"{ZENDESK_TICKET_API_BASE}/{quoted_ticket_id}.json",
        timeout_seconds=timeout,
    )
    ticket = ticket_payload.get("ticket") if isinstance(ticket_payload.get("ticket"), dict) else {}
    ticket_updated_at = str(ticket.get("updated_at") or "").strip()
    if not ticket_updated_at:
        raise _assignment_error("outcome_unknown", error_code="zendesk_ticket_updated_at_missing")

    comments_endpoint = f"{ZENDESK_TICKET_API_BASE}/{quoted_ticket_id}/comments.json"
    next_url: str | None = f"{comments_endpoint}?include=users&per_page=100"
    seen_urls: set[str] = set()
    comments: list[dict[str, Any]] = []
    users: dict[str, dict[str, Any]] = {ai_assignee_id: ai_user}
    while next_url is not None:
        parsed = urllib.parse.urlparse(next_url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "agoraio.zendesk.com"
            or parsed.path != urllib.parse.urlparse(comments_endpoint).path
            or next_url in seen_urls
        ):
            raise _assignment_error("outcome_unknown", error_code="zendesk_comments_pagination_invalid")
        seen_urls.add(next_url)
        page, _ = _request(method="GET", url=next_url, timeout_seconds=timeout)
        page_comments = page.get("comments")
        page_users = page.get("users")
        if not isinstance(page_comments, list) or not isinstance(page_users, list):
            raise _assignment_error("outcome_unknown", error_code="zendesk_response_invalid")
        if any(not isinstance(comment, dict) for comment in page_comments):
            raise _assignment_error("outcome_unknown", error_code="zendesk_response_invalid")
        comments.extend(page_comments)
        for user in page_users:
            if not isinstance(user, dict):
                raise _assignment_error("outcome_unknown", error_code="zendesk_response_invalid")
            user_id = str(user.get("id") or "").strip()
            if user_id:
                users[user_id] = user
        raw_next_page = page.get("next_page")
        if raw_next_page is None:
            next_url = None
        elif isinstance(raw_next_page, str) and raw_next_page.strip():
            next_url = raw_next_page.strip()
        else:
            raise _assignment_error("outcome_unknown", error_code="zendesk_comments_pagination_invalid")

    normalized_comments: list[dict[str, Any]] = []
    for comment in comments:
        author_id = str(comment.get("author_id") or "").strip()
        author = users.get(author_id) or {}
        normalized_comments.append(
            {
                **comment,
                "author": {
                    "id": author_id or None,
                    "name": author.get("name"),
                    "role": author.get("role"),
                },
            }
        )
    try:
        snapshot = normalize_snapshot(
            {
                "snapshot_complete": True,
                "source_updated_at": ticket_updated_at,
                "comments": normalized_comments,
            }
        )
    except ValueError as exc:
        raise _assignment_error("outcome_unknown", error_code="zendesk_comment_snapshot_invalid") from exc

    blocking_comment_id: str | None = None
    unresolved_public_comment_id: str | None = None
    for comment in snapshot.comments:
        if comment.is_initial or not comment.is_public:
            continue
        if comment.author_id == ai_assignee_id:
            continue
        if comment.author_kind == "agent" and blocking_comment_id is None:
            blocking_comment_id = comment.zendesk_comment_id
        elif comment.author_kind in {"unknown", "system"} and unresolved_public_comment_id is None:
            unresolved_public_comment_id = comment.zendesk_comment_id

    return ZendeskOwnershipSnapshot(
        ticket_id=normalized_ticket_id,
        assignee_id=str(ticket.get("assignee_id") or "").strip() or None,
        group_id=str(ticket.get("group_id") or "").strip() or None,
        ticket_updated_at=ticket_updated_at,
        ai_assignee_id=ai_assignee_id,
        ai_group_id=ai_group_id,
        human_replied=blocking_comment_id is not None,
        blocking_comment_id=blocking_comment_id,
        unresolved_public_comment_id=unresolved_public_comment_id,
        required_field_missing=_assignment_required_field_missing(ticket),
    )


def assign_ticket_to_configured_ai(
    *,
    ticket_id: str,
    timeout_seconds: float = 15.0,
    ownership_snapshot: ZendeskOwnershipSnapshot | None = None,
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
    target_group_id = _configured_assignee_group_id(user)
    actual_email = str(user.get("email") or "").strip().lower()

    if ownership_snapshot is not None:
        if (
            ownership_snapshot.ticket_id != normalized_ticket_id
            or ownership_snapshot.ai_assignee_id != assignee_id
            or ownership_snapshot.ai_group_id != target_group_id
        ):
            raise _assignment_error("permanent", error_code="zendesk_assignment_snapshot_invalid")
        current_assignee_id = str(ownership_snapshot.assignee_id or "").strip()
        group_id = str(ownership_snapshot.group_id or "").strip() or None
        updated_stamp = str(ownership_snapshot.ticket_updated_at or "").strip()
        required_field_missing = ownership_snapshot.required_field_missing
    else:
        ticket_payload, _ = _request(
            method="GET",
            url=f"{ZENDESK_TICKET_API_BASE}/{urllib.parse.quote(normalized_ticket_id, safe='')}.json",
            timeout_seconds=timeout,
        )
        current_ticket = ticket_payload.get("ticket") if isinstance(ticket_payload.get("ticket"), dict) else {}
        current_assignee_id = str(current_ticket.get("assignee_id") or "").strip()
        group_id = str(current_ticket.get("group_id") or "").strip() or None
        updated_stamp = str(current_ticket.get("updated_at") or "").strip()
        required_field_missing = _assignment_required_field_missing(current_ticket)
    if current_assignee_id == assignee_id and group_id == target_group_id:
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

    if not updated_stamp:
        raise _assignment_error("outcome_unknown", error_code="zendesk_ticket_updated_at_missing")
    return _put_ticket_assignment(
        normalized_ticket_id=normalized_ticket_id,
        assignee_id=assignee_id,
        assignee_email=actual_email,
        assignee_name=str(user.get("name") or expected_email).strip(),
        target_group_id=target_group_id,
        previous_group_id=group_id,
        updated_stamp=updated_stamp,
        required_field_missing=required_field_missing,
        timeout_seconds=timeout,
    )


def _put_ticket_assignment(
    *,
    normalized_ticket_id: str,
    assignee_id: str,
    assignee_email: str,
    assignee_name: str,
    target_group_id: str,
    previous_group_id: str | None,
    updated_stamp: str,
    required_field_missing: bool,
    timeout_seconds: float,
) -> ZendeskAssignmentResult:
    update_payload: dict[str, Any] = {
        "assignee_id": int(assignee_id),
        "group_id": int(target_group_id),
        "safe_update": True,
        "updated_stamp": updated_stamp,
    }
    if required_field_missing:
        # The top-level "<field_id>": "<value>" form is silently ignored by this
        # Zendesk account (verified live on ticket 12893: the PUT still failed
        # with "needed" and the custom_fields array form was accepted), so the
        # required field must ride in custom_fields.
        update_payload["custom_fields"] = [
            {
                "id": int(ZENDESK_ASSIGNMENT_REQUIRED_FIELD_ID),
                "value": ZENDESK_ASSIGNMENT_REQUIRED_FIELD_VALUE,
            }
        ]
    updated_payload, status_code = _request(
        method="PUT",
        url=f"{ZENDESK_TICKET_API_BASE}/{urllib.parse.quote(normalized_ticket_id, safe='')}.json",
        data={"ticket": update_payload},
        timeout_seconds=timeout_seconds,
    )
    updated_ticket = updated_payload.get("ticket") if isinstance(updated_payload.get("ticket"), dict) else {}
    if (
        str(updated_ticket.get("assignee_id") or "").strip() != assignee_id
        or str(updated_ticket.get("group_id") or "").strip() != target_group_id
    ):
        raise _assignment_error("outcome_unknown", status_code=status_code, error_code="zendesk_assignment_unverified")
    final_group_id = str(updated_ticket.get("group_id") or "").strip() or None
    return ZendeskAssignmentResult(
        ticket_id=normalized_ticket_id,
        assignee_id=assignee_id,
        assignee_email=assignee_email,
        assignee_name=assignee_name,
        group_id=final_group_id,
        previous_group_id=previous_group_id,
        group_changed=bool(previous_group_id and final_group_id and previous_group_id != final_group_id),
        status_code=status_code,
        already_assigned=False,
    )


def _resolve_reviewer_assignee(*, email: str, timeout_seconds: float) -> dict[str, Any]:
    search_payload, _ = _request(
        method="GET",
        url=f"{ZENDESK_USER_API_BASE}/search.json?query={urllib.parse.quote(email)}",
        timeout_seconds=timeout_seconds,
    )
    candidates = search_payload.get("users") if isinstance(search_payload.get("users"), list) else []
    user = next(
        (
            candidate
            for candidate in candidates
            if isinstance(candidate, dict)
            and str(candidate.get("email") or "").strip().lower() == email
        ),
        None,
    )
    if not isinstance(user, dict):
        raise _assignment_error("permanent", error_code="zendesk_reviewer_not_found")
    role = str(user.get("role") or "").strip().lower()
    if (
        not str(user.get("id") or "").strip().isdigit()
        or not bool(user.get("active", False))
        or bool(user.get("suspended", False))
        or role != "agent"
    ):
        raise _assignment_error("permanent", error_code="zendesk_reviewer_invalid")
    return user


def assign_ticket_to_reviewer(
    *,
    ticket_id: str,
    reviewer_email: str,
    timeout_seconds: float = 15.0,
) -> ZendeskAssignmentResult:
    """Hand a ticket to a human reviewer agent and their default group."""
    normalized_ticket_id = str(ticket_id or "").strip()
    normalized_email = str(reviewer_email or "").strip().lower()
    if not normalized_ticket_id or not normalized_email:
        raise _assignment_error("permanent", error_code="zendesk_assignment_input_invalid")
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError):
        timeout = 15.0
    if timeout <= 0:
        timeout = 15.0

    user = _resolve_reviewer_assignee(email=normalized_email, timeout_seconds=timeout)
    assignee_id = str(user.get("id") or "").strip()
    target_group_id = _configured_assignee_group_id(user)

    ticket_payload, _ = _request(
        method="GET",
        url=f"{ZENDESK_TICKET_API_BASE}/{urllib.parse.quote(normalized_ticket_id, safe='')}.json",
        timeout_seconds=timeout,
    )
    current_ticket = ticket_payload.get("ticket") if isinstance(ticket_payload.get("ticket"), dict) else {}
    current_assignee_id = str(current_ticket.get("assignee_id") or "").strip()
    group_id = str(current_ticket.get("group_id") or "").strip() or None
    updated_stamp = str(current_ticket.get("updated_at") or "").strip()
    if current_assignee_id == assignee_id and group_id == target_group_id:
        return ZendeskAssignmentResult(
            ticket_id=normalized_ticket_id,
            assignee_id=assignee_id,
            assignee_email=normalized_email,
            assignee_name=str(user.get("name") or normalized_email).strip(),
            group_id=group_id,
            previous_group_id=group_id,
            group_changed=False,
            status_code=200,
            already_assigned=True,
        )
    if not updated_stamp:
        raise _assignment_error("outcome_unknown", error_code="zendesk_ticket_updated_at_missing")
    return _put_ticket_assignment(
        normalized_ticket_id=normalized_ticket_id,
        assignee_id=assignee_id,
        assignee_email=normalized_email,
        assignee_name=str(user.get("name") or normalized_email).strip(),
        target_group_id=target_group_id,
        previous_group_id=group_id,
        updated_stamp=updated_stamp,
        required_field_missing=_assignment_required_field_missing(current_ticket),
        timeout_seconds=timeout,
    )
