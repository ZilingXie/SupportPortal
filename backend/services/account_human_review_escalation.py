"""Shared Account Automation Human Review escalation side effects."""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.services.account_automation_ownership import mark_production_ownership_released
from backend.services.account_automation_reconciliation import reconcile_automation_execution_failure
from backend.services.automation_routing import ACTIVE_AUTOMATION_SUBCATEGORIES, canonical_automation_subcategory
from backend.services.zendesk_comments import (
    ZendeskCommentError,
    add_ticket_comment,
    read_ticket_comment_audit,
)
from backend.services.zendesk_ticket_assignment import route_ticket_back_to_queue

LOGGER = logging.getLogger("supportportal.account_human_review_escalation")

INTERNAL_NOTE_HEADLINE = "AI agent unable to handle this request, require human review."
IDEMPOTENCY_SCOPE = "account_human_review_internal_note"
NOTE_REASON_LIMIT = 240
CUSTOMER_CONTEXT_LIMIT = 200
_ZENDESK_TICKET_RE = re.compile(r"^/agent/tickets/(\d+)$")


@dataclass(frozen=True, slots=True)
class AccountHumanReviewEscalationResult:
    status: str
    account_case_id: str
    handler: str
    zendesk_ticket_id: str | None
    internal_note_status: str
    route_back_status: str
    handoff_status: str | None
    note_comment_id: str | None = None
    failure_code: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "account_case_id": self.account_case_id,
            "handler": self.handler,
            "zendesk_ticket_id": self.zendesk_ticket_id,
            "internal_note_status": self.internal_note_status,
            "route_back_status": self.route_back_status,
            "handoff_status": self.handoff_status,
            "note_comment_id": self.note_comment_id,
            "failure_code": self.failure_code,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_detail(value: Any) -> str:
    detail = " ".join(str(value or "").split())
    return detail[:NOTE_REASON_LIMIT] + ("..." if len(detail) > NOTE_REASON_LIMIT else "")


def _ticket_id_from_source(value: Any) -> str:
    source = value
    if isinstance(source, str) and source.strip().startswith("{"):
        try:
            import json

            source = json.loads(source)
        except (TypeError, ValueError):
            return ""
    if isinstance(source, dict):
        source = next(
            (source.get(key) for key in ("Link", "link", "url", "source_url", "source") if source.get(key)),
            "",
        )
    if not isinstance(source, str) or not source.strip():
        return ""
    try:
        parsed = urllib.parse.urlparse(source.strip())
    except ValueError:
        return ""
    if (parsed.hostname or "").lower() != "agoraio.zendesk.com":
        return ""
    match = _ZENDESK_TICKET_RE.match(parsed.path or "")
    return match.group(1) if match else ""


def _zendesk_ticket_id(account_case: dict[str, Any], ticket_id: str) -> str:
    candidate = str(account_case.get("zendesk_ticket_id") or "").strip()
    candidate = candidate or _ticket_id_from_source(account_case.get("source"))
    candidate = candidate or (str(ticket_id or "").strip() if str(ticket_id or "").strip().isdigit() else "")
    return candidate if candidate.isdigit() else ""


def _handler(account_case: dict[str, Any], fallback: str | None) -> tuple[str, str]:
    action = canonical_automation_subcategory(
        account_case.get("execution_action") or account_case.get("route") or ""
    )
    classification = account_case.get("route_classification")
    classification = classification if isinstance(classification, dict) else {}
    handler = str(
        account_case.get("automation_handler")
        or classification.get("superseded_automation_handler")
        or fallback
        or action
        or "automation"
    ).strip().lower() or "automation"
    return handler, action


def _note_body(*, handler: str, stage: str, code: str, reason: str, customer_context: str = "") -> str:
    lines = [
        INTERNAL_NOTE_HEADLINE,
        "",
        f"Automation: {handler}",
        f"Failure stage: {_safe_detail(stage) or 'unknown'}",
        f"Failure code: {_safe_detail(code) or 'unknown'}",
    ]
    safe_reason = _safe_detail(reason)
    if safe_reason:
        lines.append(f"Reason: {safe_reason}")
    context = _safe_detail(customer_context)[:CUSTOMER_CONTEXT_LIMIT]
    if context:
        lines.append(f"Customer context: {context}")
    lines.append("The ticket has been returned to its source queue for human review.")
    return "\n".join(lines)


def _persist_note_result(
    repository: Any,
    *,
    key: str,
    payload: dict[str, Any],
    status: str,
    timestamp: str,
) -> None:
    try:
        if status == "sent":
            repository.complete_idempotent_request(
                IDEMPOTENCY_SCOPE,
                key,
                response_payload=payload,
                updated_at=timestamp,
            )
        else:
            repository.fail_idempotent_request(
                IDEMPOTENCY_SCOPE,
                key,
                response_payload=payload,
                updated_at=timestamp,
            )
    except Exception:
        LOGGER.exception("Could not persist Account Human Review note result key=%s", key)


def _deliver_internal_note(
    *,
    repository: Any,
    account_case_id: str,
    zendesk_ticket_id: str,
    body: str,
    incident_id: str,
    timestamp: str,
) -> tuple[str, str | None, str | None]:
    key = f"{account_case_id}:{incident_id}"
    try:
        claim = repository.begin_idempotent_request(
            IDEMPOTENCY_SCOPE,
            key,
            created_at=timestamp,
            retry_failed=False,
        )
    except Exception as exc:
        return "failed:claim_error", None, type(exc).__name__
    if not claim.get("created"):
        existing = claim.get("response_payload") if isinstance(claim, dict) else None
        if isinstance(existing, dict):
            return (
                str(existing.get("status") or "in_progress"),
                str(existing.get("comment_id") or "").strip() or None,
                str(existing.get("error_code") or "").strip() or None,
            )
        return "in_progress", None, None

    try:
        existing_comment, _solved_seen = read_ticket_comment_audit(
            ticket_id=zendesk_ticket_id,
            body=body,
            public=False,
        )
    except ZendeskCommentError as exc:
        if exc.category == "outcome_unknown":
            payload = {"status": "outcome_unknown", "error_code": exc.error_code}
            _persist_note_result(
                repository,
                key=key,
                payload=payload,
                status="outcome_unknown",
                timestamp=timestamp,
            )
            return "outcome_unknown", None, exc.error_code
        existing_comment = None
    if existing_comment is not None:
        comment_id = str(existing_comment.comment_id or "").strip() or None
        payload = {"status": "sent", "comment_id": comment_id, "idempotent_replay": True}
        _persist_note_result(repository, key=key, payload=payload, status="sent", timestamp=timestamp)
        return "sent", comment_id, None

    try:
        result = add_ticket_comment(ticket_id=zendesk_ticket_id, body=body, public=False)
        comment_id = str(result.comment_id or "").strip() or None
        payload = {"status": "sent", "comment_id": comment_id}
        _persist_note_result(repository, key=key, payload=payload, status="sent", timestamp=timestamp)
        return "sent", comment_id, None
    except ZendeskCommentError as exc:
        status = "outcome_unknown" if exc.category == "outcome_unknown" else f"failed:{exc.error_code}"
        payload = {"status": status, "error_code": exc.error_code}
        _persist_note_result(repository, key=key, payload=payload, status="outcome_unknown" if status == "outcome_unknown" else "failed", timestamp=timestamp)
        return status, None, exc.error_code
    except Exception as exc:
        error_code = type(exc).__name__
        payload = {"status": f"failed:{error_code}", "error_code": error_code}
        _persist_note_result(repository, key=key, payload=payload, status="failed", timestamp=timestamp)
        return f"failed:{error_code}", None, error_code


def escalate_account_case_to_human_review(
    *,
    account_case: dict[str, Any],
    ticket_id: str,
    handler: str | None,
    failure_stage: str,
    failure_code: str,
    reason: str,
    customer_context: str = "",
    repository: Any,
    timestamp: str | None = None,
) -> AccountHumanReviewEscalationResult:
    """Persist Human Review and perform Production-only Zendesk handoff."""
    timestamp = timestamp or _utc_now()
    account_case_id = str(
        account_case.get("account_case_id") or account_case.get("billing_ticket_id") or ticket_id or ""
    ).strip()
    normalized_handler, action = _handler(account_case, handler)
    active = action in ACTIVE_AUTOMATION_SUBCATEGORIES or normalized_handler in {
        "automation",
        "billing",
        "enablement",
        "account_suspension",
        "fraud_account",
        "detailed_invoice",
    }
    if not active:
        return AccountHumanReviewEscalationResult(
            status="skipped_inactive_handler",
            account_case_id=account_case_id,
            handler=normalized_handler,
            zendesk_ticket_id=None,
            internal_note_status="skipped_inactive_handler",
            route_back_status="skipped_inactive_handler",
            handoff_status=None,
        )
    normalized_stage = _safe_detail(failure_stage) or "human_review"
    normalized_code = _safe_detail(failure_code) or "automation_human_review"
    incident_id = str(
        account_case.get("failure_incident_id")
        or f"{account_case_id or ticket_id}:{normalized_stage}:{normalized_code}"
    ).strip()

    updated = reconcile_automation_execution_failure(
        dict(account_case),
        reason_code=normalized_code,
        context={
            "policy_decision": "account_human_review_escalation",
            "failure_stage": normalized_stage,
            "failure_code": normalized_code,
            "failure_incident_id": incident_id,
        },
    )
    existing_policy_decision = str(account_case.get("policy_decision") or "").strip()
    updated.update(
        {
            "automation_status": "human_review_required",
            "policy_decision": existing_policy_decision or "account_human_review_escalation",
            "execution_reason_code": normalized_code,
            "not_automated_reason": f"{normalized_code}:{_safe_detail(reason)}"[:400],
            "failure_stage": normalized_stage,
            "failure_code": normalized_code,
            "failure_incident_id": incident_id,
            "route_status": "not_automated",
            "updated_at": timestamp,
        }
    )
    classification = dict(updated.get("route_classification") or {})
    classification.update(
        {
            "handler_binding_status": "human_review",
            "failure_stage": normalized_stage,
            "failure_code": normalized_code,
            "failure_incident_id": incident_id,
        }
    )
    updated["route_classification"] = classification
    account_case.clear()
    account_case.update(updated)
    repository.cancel_pending_account_reply_jobs(str(ticket_id or "").strip(), updated_at=timestamp)

    zendesk_ticket_id = _zendesk_ticket_id(account_case, ticket_id)
    processing_profile = str(account_case.get("processing_profile") or "staging").strip().lower()
    internal_note_status = "skipped_not_production"
    route_back_status = "skipped_not_production"
    handoff_status: str | None = None
    note_comment_id: str | None = None
    route_failure_code: str | None = None

    if processing_profile == "production" and active and zendesk_ticket_id:
        prior_context = account_case.get("automation_context")
        prior_context = prior_context if isinstance(prior_context, dict) else {}
        ownership = prior_context.get("zendesk_ownership")
        ownership = ownership if isinstance(ownership, dict) else {}
        source_group_id = str(ownership.get("source_group_id") or "").strip() or None
        body = _note_body(
            handler=normalized_handler,
            stage=normalized_stage,
            code=normalized_code,
            reason=reason,
            customer_context=customer_context,
        )
        internal_note_status, note_comment_id, note_error = _deliver_internal_note(
            repository=repository,
            account_case_id=account_case_id,
            zendesk_ticket_id=zendesk_ticket_id,
            body=body,
            incident_id=incident_id,
            timestamp=timestamp,
        )
        mark_production_ownership_released(
            account_case,
            updated_at=timestamp,
            handoff_status="pending",
            assignee_id=str(ownership.get("assignee_id") or "").strip() or None,
            group_id=str(ownership.get("group_id") or "").strip() or None,
        )
        repository.save_account_case(account_case)
        try:
            route_result = route_ticket_back_to_queue(
                ticket_id=zendesk_ticket_id,
                source_group_id=source_group_id,
            )
            route_back_status = str(route_result.status or "queued").strip() or "queued"
            handoff_status = route_back_status
        except ZendeskCommentError as exc:
            route_failure_code = exc.error_code
            handoff_status = "outcome_unknown" if exc.category == "outcome_unknown" else "failed"
            route_back_status = f"failed:{exc.error_code}"
        completed_at = _utc_now()
        account_case["updated_at"] = completed_at
        mark_production_ownership_released(
            account_case,
            updated_at=completed_at,
            handoff_status=handoff_status or "failed",
            assignee_id=str(ownership.get("assignee_id") or "").strip() or None,
            group_id=str(ownership.get("group_id") or "").strip() or None,
            failure_code=route_failure_code or note_error,
        )
    elif processing_profile == "production" and active:
        internal_note_status = "skipped_missing_zendesk_ticket"
        route_back_status = "skipped_missing_zendesk_ticket"

    account_case_context = dict(account_case.get("automation_context") or {})
    account_case_context["human_review_escalation"] = {
        "incident_id": incident_id,
        "handler": normalized_handler,
        "failure_stage": normalized_stage,
        "failure_code": normalized_code,
        "internal_note_status": internal_note_status,
        "route_back_status": route_back_status,
        "handoff_status": handoff_status,
        "note_comment_id": note_comment_id,
        "updated_at": account_case.get("updated_at") or timestamp,
    }
    account_case["automation_context"] = account_case_context
    repository.save_account_case(account_case)
    try:
        repository.record_workspace_audit_event(
            "account_human_review_escalation",
            actor_id="account-system",
            target_id=account_case_id or ticket_id,
            payload={
                "account_case_id": account_case_id or None,
                "ticket_id": ticket_id or None,
                "zendesk_ticket_id": zendesk_ticket_id or None,
                "handler": normalized_handler,
                "failure_stage": normalized_stage,
                "failure_code": normalized_code,
                "internal_note_status": internal_note_status,
                "route_back_status": route_back_status,
                "handoff_status": handoff_status,
                "note_comment_id": note_comment_id,
            },
            created_at=account_case.get("updated_at") or timestamp,
        )
    except Exception:
        LOGGER.exception("Could not record Account Human Review escalation audit for %s", account_case_id or ticket_id)

    overall_status = "completed" if (
        internal_note_status in {"sent", "skipped_not_production", "skipped_missing_zendesk_ticket"}
        and route_back_status in {"queued", "already_human_owned", "skipped_not_production", "skipped_missing_zendesk_ticket"}
    ) else "degraded"
    return AccountHumanReviewEscalationResult(
        status=overall_status,
        account_case_id=account_case_id,
        handler=normalized_handler,
        zendesk_ticket_id=zendesk_ticket_id or None,
        internal_note_status=internal_note_status,
        route_back_status=route_back_status,
        handoff_status=handoff_status,
        note_comment_id=note_comment_id,
        failure_code=route_failure_code,
    )
