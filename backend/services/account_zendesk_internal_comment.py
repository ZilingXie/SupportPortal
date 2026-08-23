"""Shared Account AI message to Zendesk internal-comment application service."""

from __future__ import annotations

import copy
import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Literal

from backend.repositories.ticket_repository import TicketRepository
from backend.services.zendesk_comments import (
    ZendeskCommentError,
    add_ticket_comment,
    read_ticket_comment_audit,
    upload_ticket_attachment,
)


ACCOUNT_ZENDESK_COMMENT_IDEMPOTENCY_SCOPE = "account_zendesk_internal_comment"
AccountZendeskCommentTrigger = Literal[
    "account_admin",
    "production_worker",
    "production_recovery",
]

_ZENDESK_TICKET_AGENT_RE = re.compile(r"^/agent/tickets/(\d+)$")
_ZENDESK_TICKET_API_RE = re.compile(r"^/api/v2/tickets/(\d+)\.json$")


class AccountZendeskInternalCommentError(ValueError):
    """A target or local persistence error that must be surfaced to the caller."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        status_code: int,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(detail)
        self.code = str(code or "account_zendesk_comment_error").strip()
        self.detail = str(detail or "Zendesk internal comment failed").strip()
        self.status_code = int(status_code)
        self.outcome_unknown = bool(outcome_unknown)


@dataclass(frozen=True)
class AccountZendeskCommentResult:
    status: str
    account_case_id: str
    message_id: str
    actor_id: str
    trigger: str
    comment_id: str | None = None
    retryable: bool = False
    error_code: str | None = None
    audit_persisted: bool | None = None
    idempotent_replay: bool = False

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "account_case_id": self.account_case_id,
            "message_id": self.message_id,
            "actor_id": self.actor_id,
            "trigger": self.trigger,
        }
        if self.comment_id:
            payload["comment_id"] = self.comment_id
        if self.retryable:
            payload["retryable"] = True
        if self.error_code:
            payload["error_code"] = self.error_code
        if self.audit_persisted is not None:
            payload["audit_persisted"] = self.audit_persisted
        if self.idempotent_replay:
            payload["idempotent_replay"] = True
        return payload


def account_zendesk_comment_key(account_case_id: str, message_id: str) -> str:
    return f"{str(account_case_id or '').strip()}:{str(message_id or '').strip()}"


def _result_from_payload(
    payload: dict[str, Any],
    *,
    actor_id: str,
    trigger: AccountZendeskCommentTrigger,
    idempotent_replay: bool = False,
) -> AccountZendeskCommentResult:
    return AccountZendeskCommentResult(
        status=str(payload.get("status") or "failed").strip() or "failed",
        account_case_id=str(payload.get("account_case_id") or "").strip(),
        message_id=str(payload.get("message_id") or "").strip(),
        actor_id=str(payload.get("actor_id") or actor_id).strip(),
        trigger=str(payload.get("trigger") or trigger).strip(),
        comment_id=str(payload.get("comment_id") or "").strip() or None,
        retryable=bool(payload.get("retryable")),
        error_code=str(payload.get("error_code") or "").strip() or None,
        audit_persisted=(
            bool(payload.get("audit_persisted"))
            if "audit_persisted" in payload
            else None
        ),
        idempotent_replay=idempotent_replay,
    )


def _result_payload(
    *,
    status: str,
    account_case_id: str,
    message_id: str,
    actor_id: str,
    trigger: AccountZendeskCommentTrigger,
    comment_id: str | None = None,
    retryable: bool = False,
    error_code: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": str(status or "failed").strip() or "failed",
        "account_case_id": str(account_case_id or "").strip(),
        "message_id": str(message_id or "").strip(),
        "actor_id": str(actor_id or "system").strip() or "system",
        "trigger": str(trigger or "account_admin").strip() or "account_admin",
    }
    if comment_id:
        payload["comment_id"] = str(comment_id).strip()
    if retryable:
        payload["retryable"] = True
    if error_code:
        payload["error_code"] = str(error_code).strip()
    return payload


def _extract_source_link(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        candidate = value.strip()
        if candidate.startswith("{"):
            try:
                parsed = json.loads(candidate)
            except (TypeError, json.JSONDecodeError):
                parsed = None
            if isinstance(parsed, dict):
                return _extract_source_link(parsed)
        return candidate
    if isinstance(value, dict):
        for key in ("Link", "link", "url", "source_url", "source"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


def _zendesk_ticket_id_from_source(value: Any) -> str:
    link = _extract_source_link(value)
    if not link:
        return ""
    try:
        parsed = urllib.parse.urlparse(link)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if not (host == "zendesk.com" or host.endswith(".zendesk.com")):
        return ""
    match = _ZENDESK_TICKET_AGENT_RE.match(parsed.path or "") or _ZENDESK_TICKET_API_RE.match(
        parsed.path or ""
    )
    return match.group(1) if match else ""


def _message_target(
    repository: TicketRepository,
    *,
    account_case_id: str,
    message_id: str,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    normalized_case_id = str(account_case_id or "").strip()
    normalized_message_id = str(message_id or "").strip()
    if not normalized_case_id or not normalized_message_id:
        raise AccountZendeskInternalCommentError(
            "account_zendesk_comment_input_invalid",
            "account case and message are required",
            status_code=422,
        )

    bundles = repository.get_account_case_details([normalized_case_id])
    bundle = bundles.get(normalized_case_id)
    if not isinstance(bundle, dict):
        raise AccountZendeskInternalCommentError(
            "account_case_not_found",
            "account case not found",
            status_code=404,
        )
    account_case = bundle.get("account_case")
    ticket = bundle.get("ticket")
    if not isinstance(account_case, dict) or not isinstance(ticket, dict):
        raise AccountZendeskInternalCommentError(
            "account_case_not_found",
            "account case not found",
            status_code=404,
        )

    ticket_id = str(ticket.get("ticket_id") or account_case.get("client_ticket_id") or "").strip()
    messages = ticket.get("messages") if isinstance(ticket.get("messages"), list) else []
    for index, raw_message in enumerate(messages):
        if not isinstance(raw_message, dict):
            continue
        message = copy.deepcopy(raw_message)
        candidate_id = str(
            message.get("message_id") or message.get("id") or f"{ticket_id}:{index}"
        ).strip()
        if candidate_id != normalized_message_id:
            continue
        message["message_id"] = candidate_id
        role = str(message.get("role") or "").strip().lower()
        if role not in {"assistant", "ai"}:
            raise AccountZendeskInternalCommentError(
                "account_zendesk_comment_role_invalid",
                "Only AI messages can be added as internal comments",
                status_code=400,
            )
        if not str(message.get("content") or "").strip():
            raise AccountZendeskInternalCommentError(
                "account_zendesk_comment_body_empty",
                "AI message is empty",
                status_code=400,
            )
        zendesk_ticket_id = str(account_case.get("zendesk_ticket_id") or "").strip()
        zendesk_ticket_id = zendesk_ticket_id or _zendesk_ticket_id_from_source(account_case.get("source"))
        if not zendesk_ticket_id and ticket_id.isdigit():
            zendesk_ticket_id = ticket_id
        if not zendesk_ticket_id:
            raise AccountZendeskInternalCommentError(
                "account_zendesk_ticket_missing",
                "Account Case is not linked to a Zendesk ticket",
                status_code=400,
            )
        return account_case, message, ticket_id, zendesk_ticket_id

    raise AccountZendeskInternalCommentError(
        "account_message_not_found",
        "AI message not found",
        status_code=404,
    )


def _persist_result(
    repository: TicketRepository,
    *,
    account_case_id: str,
    ticket_id: str,
    message_id: str,
    idempotency_key: str,
    payload: dict[str, Any],
    recorded_at: str,
    close_local_ticket: bool = False,
) -> AccountZendeskCommentResult:
    try:
        persisted = repository.record_account_zendesk_internal_comment_result(
            account_case_id=account_case_id,
            ticket_id=ticket_id,
            message_id=message_id,
            idempotency_key=idempotency_key,
            result_payload=payload,
            recorded_at=recorded_at,
            close_local_ticket=close_local_ticket,
        )
    except Exception as exc:
        raise AccountZendeskInternalCommentError(
            "account_zendesk_comment_persistence_unknown",
            "Zendesk comment result is unknown; verify the ticket before retrying.",
            status_code=409,
            outcome_unknown=True,
        ) from exc
    result = _result_from_payload(payload, actor_id=str(payload.get("actor_id") or "system"), trigger=str(payload.get("trigger") or "account_admin"))
    if isinstance(persisted, dict) and persisted.get("audit_persisted") is not None:
        result = AccountZendeskCommentResult(
            **{
                **result.__dict__,
                "audit_persisted": bool(persisted.get("audit_persisted")),
            }
        )
    return result


def _existing_result(
    record: dict[str, Any],
    *,
    account_case_id: str,
    message_id: str,
    actor_id: str,
    trigger: AccountZendeskCommentTrigger,
) -> AccountZendeskCommentResult | None:
    state = str(record.get("state") or "").strip().lower()
    payload = record.get("response_payload")
    if state == "completed" and isinstance(payload, dict):
        return _result_from_payload(
            payload,
            actor_id=actor_id,
            trigger=trigger,
            idempotent_replay=True,
        )
    if state == "failed" and isinstance(payload, dict):
        status = str(payload.get("status") or "failed").strip().lower()
        if status == "outcome_unknown" or not bool(payload.get("retryable")):
            return _result_from_payload(
                payload,
                actor_id=actor_id,
                trigger=trigger,
            )
    if state == "processing":
        return AccountZendeskCommentResult(
            status="in_progress",
            account_case_id=account_case_id,
            message_id=message_id,
            actor_id=actor_id,
            trigger=trigger,
        )
    return None


def _message_attachment_files(
    message: dict[str, Any],
) -> list[tuple[str, str, bytes]]:
    """Resolve the message's asset attachments to uploadable file tuples.

    Runs before the idempotency claim: a missing asset or unreadable storage is
    a permanent local failure, and no Zendesk write has happened yet.
    """
    from backend import main as backend_main

    # Postgres reads spread message meta onto the message, while in-memory
    # replies keep meta nested; accept both attachment locations.
    raw_attachments = message.get("attachments")
    if not isinstance(raw_attachments, list):
        nested_meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        raw_attachments = nested_meta.get("attachments")
    attachments = raw_attachments if isinstance(raw_attachments, list) else []
    files: list[tuple[str, str, bytes]] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        asset_id = str(attachment.get("asset_id") or "").strip()
        if not asset_id:
            continue
        asset = backend_main.asset_repository.get_asset(asset_id)
        if asset is None:
            raise AccountZendeskInternalCommentError(
                "account_zendesk_comment_attachment_missing",
                f"Attachment asset {asset_id} was not found",
                status_code=422,
            )
        try:
            data = backend_main.asset_storage.fetch_bytes(asset)
        except RuntimeError as exc:
            raise AccountZendeskInternalCommentError(
                "account_zendesk_comment_attachment_unreadable",
                f"Attachment asset {asset_id} could not be read from storage: {exc}",
                status_code=502,
            ) from exc
        if not data:
            raise AccountZendeskInternalCommentError(
                "account_zendesk_comment_attachment_unreadable",
                f"Attachment asset {asset_id} is empty",
                status_code=502,
            )
        filename = (
            str(attachment.get("original_filename") or attachment.get("file_name") or "").strip()
            or "attachment.pdf"
        )
        content_type = (
            str(attachment.get("content_type") or asset.get("content_type") or "").strip()
            or "application/pdf"
        )
        files.append((filename, content_type, data))
    return files


def deliver_account_ai_message_as_internal_comment(
    *,
    repository: TicketRepository,
    account_case_id: str,
    message_id: str,
    actor_id: str,
    trigger: AccountZendeskCommentTrigger,
    retry_failed: bool = False,
    public_comment: bool = False,
    solve_ticket: bool = False,
) -> AccountZendeskCommentResult:
    """Write the persisted AI message once through the Zendesk API.

    ``public_comment`` selects a customer-visible Zendesk comment instead of the
    default private internal note; ``solve_ticket`` additionally solves the ticket
    in the same PUT and is only valid together with ``public_comment``.
    """
    if solve_ticket and not public_comment:
        raise AccountZendeskInternalCommentError(
            "account_zendesk_comment_visibility_invalid",
            "Solving the ticket requires a public customer reply",
            status_code=422,
        )
    account_case, message, ticket_id, zendesk_ticket_id = _message_target(
        repository,
        account_case_id=account_case_id,
        message_id=message_id,
    )
    _ = account_case
    attachment_files = _message_attachment_files(message)
    normalized_case_id = str(account_case_id).strip()
    normalized_message_id = str(message_id).strip()
    normalized_actor_id = str(actor_id or "system").strip() or "system"
    idempotency_key = account_zendesk_comment_key(normalized_case_id, normalized_message_id)
    record = repository.begin_idempotent_request(
        ACCOUNT_ZENDESK_COMMENT_IDEMPOTENCY_SCOPE,
        idempotency_key,
        created_at=_utc_now(),
        retry_failed=False,
    )
    if not bool(record.get("created")):
        existing_payload = record.get("response_payload")
        existing_state = str(record.get("state") or "").strip().lower()
        if (
            retry_failed
            and existing_state == "failed"
            and isinstance(existing_payload, dict)
            and bool(existing_payload.get("retryable"))
        ):
            record = repository.begin_idempotent_request(
                ACCOUNT_ZENDESK_COMMENT_IDEMPOTENCY_SCOPE,
                idempotency_key,
                created_at=_utc_now(),
                retry_failed=True,
            )
            if not bool(record.get("created")):
                return AccountZendeskCommentResult(
                    status="in_progress",
                    account_case_id=normalized_case_id,
                    message_id=normalized_message_id,
                    actor_id=normalized_actor_id,
                    trigger=trigger,
                )
        elif existing_state == "failed" and isinstance(existing_payload, dict):
            # Reconcile local delivery/message state from legacy or partial failures
            # without issuing another Zendesk write.
            return _persist_result(
                repository,
                account_case_id=normalized_case_id,
                ticket_id=ticket_id,
                message_id=normalized_message_id,
                idempotency_key=idempotency_key,
                payload=existing_payload,
                recorded_at=_utc_now(),
            )
        else:
            existing = _existing_result(
                record,
                account_case_id=normalized_case_id,
                message_id=normalized_message_id,
                actor_id=normalized_actor_id,
                trigger=trigger,
            )
            if existing is not None:
                return existing

    try:
        upload_tokens = tuple(
            upload_ticket_attachment(filename=filename, content_type=content_type, data=data)
            for filename, content_type, data in attachment_files
        )
        zendesk_result = add_ticket_comment(
            ticket_id=zendesk_ticket_id,
            body=str(message.get("content") or "").strip(),
            public=bool(public_comment),
            solve=bool(solve_ticket),
            **({"uploads": upload_tokens} if upload_tokens else {}),
        )
    except ZendeskCommentError as exc:
        payload = _result_payload(
            status="outcome_unknown" if exc.category == "outcome_unknown" else "failed",
            account_case_id=normalized_case_id,
            message_id=normalized_message_id,
            actor_id=normalized_actor_id,
            trigger=trigger,
            retryable=exc.category == "retryable",
            error_code=exc.error_code,
        )
        return _persist_result(
            repository,
            account_case_id=normalized_case_id,
            ticket_id=ticket_id,
            message_id=normalized_message_id,
            idempotency_key=idempotency_key,
            payload=payload,
            recorded_at=_utc_now(),
        )

    payload = _result_payload(
        status="added",
        account_case_id=normalized_case_id,
        message_id=normalized_message_id,
        actor_id=normalized_actor_id,
        trigger=trigger,
        comment_id=zendesk_result.comment_id,
    )
    return _persist_result(
        repository,
        account_case_id=normalized_case_id,
        ticket_id=ticket_id,
        message_id=normalized_message_id,
        idempotency_key=idempotency_key,
        payload=payload,
        recorded_at=_utc_now(),
        close_local_ticket=bool(solve_ticket),
    )


def reconcile_account_ai_message_internal_comment(
    *,
    repository: TicketRepository,
    account_case_id: str,
    message_id: str,
    actor_id: str,
    trigger: AccountZendeskCommentTrigger,
    public_comment: bool = False,
    solve_ticket: bool = False,
) -> AccountZendeskCommentResult:
    """Audit a possibly completed Zendesk write without issuing another PUT."""
    _account_case, message, ticket_id, zendesk_ticket_id = _message_target(
        repository,
        account_case_id=account_case_id,
        message_id=message_id,
    )
    normalized_case_id = str(account_case_id).strip()
    normalized_message_id = str(message_id).strip()
    normalized_actor_id = str(actor_id or "system").strip() or "system"
    idempotency_key = account_zendesk_comment_key(normalized_case_id, normalized_message_id)
    record = repository.begin_idempotent_request(
        ACCOUNT_ZENDESK_COMMENT_IDEMPOTENCY_SCOPE,
        idempotency_key,
        created_at=_utc_now(),
        retry_failed=False,
    )
    if str(record.get("state") or "").strip().lower() == "completed":
        payload = record.get("response_payload")
        if isinstance(payload, dict):
            return _result_from_payload(
                payload,
                actor_id=normalized_actor_id,
                trigger=trigger,
                idempotent_replay=True,
            )
    body = str(message.get("content") or "").strip()
    try:
        comment, solved_seen = read_ticket_comment_audit(
            ticket_id=zendesk_ticket_id,
            body=body,
            public=bool(public_comment),
        )
    except ZendeskCommentError as exc:
        payload = _result_payload(
            status="outcome_unknown",
            account_case_id=normalized_case_id,
            message_id=normalized_message_id,
            actor_id=normalized_actor_id,
            trigger=trigger,
            error_code=exc.error_code,
        )
    else:
        status = "added" if comment is not None else "outcome_unknown"
        error_code = None if comment is not None else "zendesk_comment_not_found"
        if comment is not None and solve_ticket and not solved_seen:
            # The comment exists but the ticket was never solved; the solve half of
            # the write is unverified, so the delivery stays reconcilable.
            status = "outcome_unknown"
            error_code = "zendesk_ticket_status_unverified"
        payload = _result_payload(
            status=status,
            account_case_id=normalized_case_id,
            message_id=normalized_message_id,
            actor_id=normalized_actor_id,
            trigger=trigger,
            comment_id=comment.comment_id if comment is not None else None,
            error_code=error_code,
        )
    return _persist_result(
        repository,
        account_case_id=normalized_case_id,
        ticket_id=ticket_id,
        message_id=normalized_message_id,
        idempotency_key=idempotency_key,
        payload=payload,
        recorded_at=_utc_now(),
        close_local_ticket=bool(solve_ticket),
    )


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
