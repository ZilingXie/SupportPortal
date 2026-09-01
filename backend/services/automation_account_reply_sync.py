"""Zendesk comment ingestion and the customer-reply chain for /automation/production.

Pure port of the /production trigger path (main.py `_process_zendesk_comment_trigger`
and `_process_account_customer_reply_impl`) onto the split production schema,
per the user's Phase C decision (option B: faithful port, image keeps the
physical-exclusion contract). The engineer-case branch records the customer
comment into the engineer Slack thread; the engineer AI investigation round is
wired in the Slack collaboration phase instead.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from backend.services.account_automation_reconciliation import (
    reconcile_automation_execution_failure,
)
from backend.services.account_automation_handlers import account_automation_handler
from backend.services.account_reply_jobs import ACCOUNT_REPLY_INTENT_RAG_FALLBACK_ANSWER
from backend.services.account_reply_rag_fallback import (
    escalate_unexpected_reply_to_human,
    should_run_reply_rag_fallback,
    try_rag_fallback_answer,
)
from backend.services.account_route_pipeline import (
    account_route_metadata,
    decide_account_route,
)
from backend.services.account_suspension_automation import (
    SUSPENSION_CONTACT_WORKFLOW_KEY,
    SUSPENSION_STATE_AWAITING_CONTACT_CONFIRMATION,
    SUSPENSION_STATE_CLOSING_REPLY_PENDING,
    SUSPENSION_STATE_HANDOFF_PENDING,
    SUSPENSION_STATE_HUMAN_REVIEW_REQUIRED,
    closing_reply_facts,
    suspension_contact_confirmation,
)
from backend.services.account_verification_field_extractor import (
    AccountVerificationFieldExtraction,
)
from backend.services.account_zendesk_comments import (
    ZendeskCommentSnapshotError,
    normalize_snapshot,
)
from backend.services.automation_account_intake import (
    _apply_ownership_gate,
    _build_billing_attempt,
    _build_enablement_attempt,
    _build_verification_attempt,
    _create_reply_job,
    _record_execution_failure,
    _reply_facts,
    _run_internal_email_delivery,
)
from backend.services.automation_routing import is_registered_automation
from backend.services.billing_automation import (
    build_billing_internal_email_payload,
    send_billing_internal_email,
)
from backend.services.enablement_automation import send_enablement_internal_email
from backend.services.engineer_assignment import EngineerAssignmentService
from backend.services.engineer_cases import (
    apply_case_context_to_engineer_case,
    build_engineer_case_context,
    close_case_context_active_investigation,
)
from backend.services.engineer_slack import (
    build_engineer_case_status_changed_event,
    build_engineer_case_thread_event,
)
from backend.services.investigation_flow import (
    RESOLVED_STATUS,
    normalize_ticket_status,
    record_engineer_customer_comment,
)
from backend.services.automation_account_intake import _zendesk_ticket_url as _ticket_url_helper


def _account_zendesk_ticket_url(source: Any, ticket_id: str) -> str | None:
    return _ticket_url_helper(ticket_id)

LOGGER = logging.getLogger("supportportal.automation_account_reply_sync")

ZENDESK_COMMENT_TRIGGER_IDEMPOTENCY_SCOPE = "zendesk_customer_comment_trigger"
ZENDESK_COMMENT_TRIGGER_IGNORED_CASE_STATUSES = frozenset(
    {"human_review_required", "human_review", "closed"}
)


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


class ReplySyncError(Exception):
    """Maps to HTTP error responses with a status code and payload."""

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


def _asked_field_keys(canonical_ticket: dict[str, Any]) -> set[str]:
    asked: set[str] = set()
    for message in canonical_ticket.get("messages", []):
        if not isinstance(message, dict) or str(message.get("role") or "").strip().lower() != "assistant":
            continue
        meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        for source in (message, meta):
            for field_name in source.get("asked_field_keys") or []:
                normalized = str(field_name or "").strip().lower()
                if normalized:
                    asked.add(normalized)
    return asked


def _reply_job_public(job: dict[str, Any] | None) -> dict[str, Any]:
    if not job:
        return {
            "ai_reply_status": None,
            "ai_reply_scheduled_for": None,
            "ai_reply_published_at": None,
            "ai_reply_error": None,
        }
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    return {
        "ai_reply_status": str(job.get("status") or "") or None,
        "ai_reply_scheduled_for": job.get("scheduled_for"),
        "ai_reply_published_at": job.get("published_at"),
        "ai_reply_error": str(payload.get("error") or payload.get("cancel_reason") or "") or None,
    }


def _rag_fallback_ticket_context(canonical_ticket: dict[str, Any]) -> list[dict[str, str]]:
    context: list[dict[str, str]] = []
    for message in (canonical_ticket.get("messages") or [])[-6:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip()
        content = str(message.get("content") or "").strip()
        if role and content:
            context.append({"role": role, "content": content})
    return context


def _rag_fallback_zendesk_ticket_id(billing_ticket: dict[str, Any], client_ticket_id: str) -> str:
    zendesk_ticket_id = str(billing_ticket.get("zendesk_ticket_id") or "").strip()
    if not zendesk_ticket_id and client_ticket_id.isdigit():
        zendesk_ticket_id = client_ticket_id
    return zendesk_ticket_id


async def process_zendesk_comment_trigger(
    *,
    repository: Any,
    account_case: dict[str, Any],
    snapshot: Any,
    trigger_comment_id: str | None,
    zendesk_side_effects_enabled: bool = True,
    precomputed_route: dict[str, Any] | None = None,
    route_prompt_snapshots: dict[str, Any] | None = None,
    processing_profile: str = "production",
) -> dict[str, Any]:
    import asyncio

    def _sync(call, *args, **kwargs):
        return asyncio.get_running_loop().run_in_executor(None, lambda: call(*args, **kwargs))

    ignored: dict[str, Any] = {"trigger_status": "ignored_no_trigger"}
    if not trigger_comment_id:
        return ignored
    trigger_comment = next(
        (comment for comment in snapshot.comments if str(comment.zendesk_comment_id) == trigger_comment_id),
        None,
    )
    if trigger_comment is None:
        raise ReplySyncError(
            422,
            {
                "code": "trigger_comment_missing",
                "message": "trigger_comment_id is not present in the snapshot",
            },
        )

    def _ignored(reason: str) -> dict[str, Any]:
        return {"trigger_status": reason, "trigger_comment_id": trigger_comment_id}

    if trigger_comment.author_kind == "agent":
        return _ignored("ignored_agent_comment")
    if trigger_comment.author_kind in {"system", "unknown"}:
        return _ignored("ignored_non_customer_author")
    if not bool(trigger_comment.is_public):
        return _ignored("ignored_private_comment")
    if not str(trigger_comment.body or "").strip():
        return _ignored("ignored_empty_comment")
    if bool(trigger_comment.is_initial):
        return _ignored("ignored_initial_comment")

    account_case_id = str(
        account_case.get("account_case_id") or account_case.get("billing_ticket_id") or ""
    ).strip()
    automation_status = str(account_case.get("automation_status") or "").strip().lower()

    claim = await _sync(
        repository.begin_idempotent_request,
        ZENDESK_COMMENT_TRIGGER_IDEMPOTENCY_SCOPE,
        f"{account_case_id}:{trigger_comment_id}",
        created_at=_now_iso(),
        retry_failed=True,
    )

    async def _complete(trigger_payload: dict[str, Any]) -> dict[str, Any]:
        await _sync(
            repository.complete_idempotent_request,
            ZENDESK_COMMENT_TRIGGER_IDEMPOTENCY_SCOPE,
            f"{account_case_id}:{trigger_comment_id}",
            response_payload=trigger_payload,
            updated_at=_now_iso(),
        )
        return trigger_payload

    if not bool(claim.get("created")):
        existing_state = str(claim.get("state") or "").strip().lower()
        if existing_state == "completed":
            payload = claim.get("response_payload")
            if isinstance(payload, dict):
                return dict(payload)
        return _ignored("already_processing")

    expected_profile = str(processing_profile or "production").strip().lower()
    if str(account_case.get("processing_profile") or "staging").strip().lower() != expected_profile:
        return await _complete(_ignored("ignored_non_production_case"))
    client_ticket_id = str(account_case.get("client_ticket_id") or "").strip()
    active_engineer_case = (
        repository.get_active_engineer_case(client_ticket_id, include_client_messages=True)
        if client_ticket_id
        else None
    )
    if (
        isinstance(active_engineer_case, dict)
        and automation_status == "not_automated"
        and isinstance(active_engineer_case.get("active_investigation"), dict)
    ):
        engineer_case_id = str(active_engineer_case.get("engineer_case_id") or "").strip()
        investigation_id = str(
            (active_engineer_case.get("active_investigation") or {}).get("id") or ""
        ).strip()
        customer_text = str(trigger_comment.body or "").strip()
        engineer_case_record = _engineer_case_payload_to_record(active_engineer_case)
        timestamp = _now_iso()
        engineer_case_record, customer_messages = record_engineer_customer_comment(
            engineer_case_record,
            customer_message=customer_text,
            now_value=timestamp,
            message_meta={
                "source": "zendesk_comment",
                "zendesk_comment_id": trigger_comment_id,
                "occurred_at": str(trigger_comment.created_at or ""),
            },
        )
        customer_event = build_engineer_case_thread_event(
            event_id=f"zendesk-comment:{account_case_id}:{trigger_comment_id}:customer",
            event_type="zendesk_customer_comment",
            engineer_case_id=engineer_case_id,
            message_text="Cx has added a new comment",
            investigation_id=investigation_id or None,
        )
        repository.save_engineer_case(
            engineer_case_record,
            new_messages=customer_messages,
            slack_events=[customer_event],
        )
        state = engineer_case_record.get("engineer_agent_state") or {}
        return await _complete(
            {
                "trigger_status": "processed_engineer_notification",
                "trigger_comment_id": trigger_comment_id,
                "engineer_case_id": engineer_case_id,
                "conversation_version": int(state.get("conversation_version") or 0),
                "draft_version": int(state.get("draft_version") or 0),
            }
        )
    if (
        not is_registered_automation(
            route_family=account_case.get("route_family"),
            execution_action=account_case.get("execution_action") or account_case.get("route"),
        )
        and automation_status != "not_automated"
    ):
        return await _complete(_ignored("ignored_unregistered_automation"))
    if automation_status in ZENDESK_COMMENT_TRIGGER_IGNORED_CASE_STATUSES:
        return await _complete(_ignored("ignored_inactive_case"))
    case_created_at = str(account_case.get("created_at") or "").strip()
    if case_created_at and str(trigger_comment.created_at or "") <= case_created_at:
        return await _complete(_ignored("ignored_pre_intake_comment"))

    try:
        processed = await process_account_customer_reply(
            repository=repository,
            billing_ticket_id=account_case_id,
            message=str(trigger_comment.body or "").strip(),
            source="zendesk-comment",
            message_source_id=trigger_comment_id,
            zendesk_side_effects_enabled=zendesk_side_effects_enabled,
            precomputed_route=precomputed_route,
            route_prompt_snapshots=route_prompt_snapshots,
            processing_profile=expected_profile,
            customer_name_hint=str(getattr(trigger_comment, "author_name", None) or "").strip() or None,
        )
    except ReplySyncError as exc:
        failure_payload = {
            "trigger_status": "failed",
            "trigger_comment_id": trigger_comment_id,
            "error": str(exc.detail),
        }
        # A failed trigger outcome stays replayable: the same comment id
        # re-claims and re-runs instead of returning this failure forever.
        await _sync(
            repository.fail_idempotent_request,
            ZENDESK_COMMENT_TRIGGER_IDEMPOTENCY_SCOPE,
            f"{account_case_id}:{trigger_comment_id}",
            response_payload=failure_payload,
            updated_at=_now_iso(),
        )
        return failure_payload

    return await _complete(
        {
            "trigger_status": "processed",
            "trigger_comment_id": trigger_comment_id,
            "internal_email_status": str(processed.get("internal_email_send_status") or "") or None,
            "ai_reply_status": processed.get("ai_reply_status"),
            "ai_reply_scheduled_for": processed.get("ai_reply_scheduled_for"),
        }
    )


async def process_account_customer_reply(
    *,
    repository: Any,
    billing_ticket_id: str,
    message: str,
    source: str,
    message_source_id: str | None = None,
    zendesk_side_effects_enabled: bool = True,
    precomputed_route: dict[str, Any] | None = None,
    route_prompt_snapshots: dict[str, Any] | None = None,
    processing_profile: str = "production",
    customer_name_hint: str | None = None,
) -> dict[str, Any]:
    from backend.services.llm_usage_capture import (
        begin_case_usage_capture,
        end_case_usage_capture,
        flush_case_usage_capture,
    )

    usage_capture, usage_token = begin_case_usage_capture(billing_ticket_id=billing_ticket_id)
    try:
        return await _process_account_customer_reply_impl(
            repository=repository,
            billing_ticket_id=billing_ticket_id,
            message=message,
            source=source,
            message_source_id=message_source_id,
            zendesk_side_effects_enabled=zendesk_side_effects_enabled,
            precomputed_route=precomputed_route,
            route_prompt_snapshots=route_prompt_snapshots,
            processing_profile=processing_profile,
            customer_name_hint=customer_name_hint,
        )
    finally:
        end_case_usage_capture(usage_token)
        if usage_capture.entries:
            import asyncio

            await asyncio.get_running_loop().run_in_executor(
                None, lambda: flush_case_usage_capture(repository, usage_capture)
            )


async def _process_account_customer_reply_impl(
    *,
    repository: Any,
    billing_ticket_id: str,
    message: str,
    source: str,
    message_source_id: str | None = None,
    zendesk_side_effects_enabled: bool = True,
    precomputed_route: dict[str, Any] | None = None,
    route_prompt_snapshots: dict[str, Any] | None = None,
    processing_profile: str = "production",
    customer_name_hint: str | None = None,
) -> dict[str, Any]:
    import asyncio

    def _sync(call, *args, **kwargs):
        return asyncio.get_running_loop().run_in_executor(None, lambda: call(*args, **kwargs))

    billing_ticket = await _sync(repository.get_account_case, billing_ticket_id)
    if billing_ticket is None:
        billing_ticket = await _sync(repository.get_account_case_by_ticket_id, billing_ticket_id)
    if billing_ticket is None:
        raise ReplySyncError(404, "ticket not found")

    client_ticket_id = str(billing_ticket.get("client_ticket_id") or "").strip()
    if not client_ticket_id:
        raise ReplySyncError(400, "account case has no linked support ticket")
    canonical_ticket = await _sync(repository.get_ticket, client_ticket_id)
    if canonical_ticket is None:
        raise ReplySyncError(404, "linked support ticket not found")

    customer_message = str(message or "").strip()
    if not customer_message:
        raise ReplySyncError(400, "message is required")

    timestamp = _now_iso()
    initial_message_count = len(canonical_ticket.get("messages", [])) if isinstance(canonical_ticket.get("messages"), list) else 0
    customer_msg: dict[str, Any] = {
        "role": "customer",
        "content": customer_message,
        "created_at": timestamp,
        "content_format": "plaintext",
        "source": str(source or "account-ui").strip() or "account-ui",
    }
    normalized_source_id = str(message_source_id or "").strip()
    if normalized_source_id:
        customer_msg["external_id"] = normalized_source_id
        duplicate = any(
            isinstance(existing, dict)
            and str(existing.get("external_id") or "").strip() == normalized_source_id
            for existing in canonical_ticket.get("messages", [])
        )
        if duplicate:
            return {
                **billing_ticket,
                "messages": canonical_ticket.get("messages", []),
                "support_ticket_status": canonical_ticket.get("status"),
                "ai_reply_status": None,
            }
    canonical_ticket.setdefault("messages", []).append(customer_msg)
    canonical_ticket["updated_at"] = timestamp
    reply_ready = False
    assistant_reply_facts: dict[str, Any] | None = None
    requested_field_keys: list[str] = []
    persona_assignment: dict[str, Any] | None = None
    already_requested_fields = _asked_field_keys(canonical_ticket)
    all_customer_contents = [
        str(msg.get("content") or "")
        for msg in canonical_ticket.get("messages", [])
        if isinstance(msg, dict) and str(msg.get("role") or "").strip().lower() in {"customer", "user"}
    ]
    conversation_text = "\n".join(all_customer_contents)
    prior_classification = (
        dict(billing_ticket.get("route_classification"))
        if isinstance(billing_ticket.get("route_classification"), dict)
        else {}
    )
    prior_action = str(billing_ticket.get("execution_action") or billing_ticket.get("route") or "").strip()
    prior_handler = str(billing_ticket.get("automation_handler") or "").strip()
    prior_collected_fields = (
        dict(billing_ticket.get("collected_fields"))
        if isinstance(billing_ticket.get("collected_fields"), dict)
        else {}
    )
    prior_missing_fields = list(billing_ticket.get("missing_fields") or [])
    prior_automation_context = (
        dict(billing_ticket.get("automation_context"))
        if isinstance(billing_ticket.get("automation_context"), dict)
        else {}
    )

    if not await _sync(
        _apply_ownership_gate,
        repository=repository,
        account_case=billing_ticket,
        timestamp=timestamp,
        zendesk_side_effects_enabled=zendesk_side_effects_enabled,
    ):
        await _sync(repository.cancel_pending_account_reply_jobs, client_ticket_id, updated_at=timestamp)
        await _sync(repository.save_ticket, canonical_ticket, new_messages=[customer_msg])
        await _sync(repository.save_account_case, billing_ticket)
        return {
            **billing_ticket,
            "messages": canonical_ticket.get("messages", []),
            "support_ticket_status": canonical_ticket.get("status"),
            "ai_reply_status": None,
        }

    suspension_workflow = prior_automation_context.get(SUSPENSION_CONTACT_WORKFLOW_KEY)
    if (
        prior_handler == "account_suspension"
        and isinstance(suspension_workflow, dict)
        and str(suspension_workflow.get("state") or "").strip() == SUSPENSION_STATE_AWAITING_CONTACT_CONFIRMATION
    ):
        confirmation = suspension_contact_confirmation(
            customer_message,
            ticket_email=suspension_workflow.get("ticket_email") or canonical_ticket.get("customer_id"),
            state=suspension_workflow.get("state"),
        )
        if confirmation.get("status") != "confirmed":
            suspension_workflow["failure_reason"] = str(confirmation.get("reason") or "confirmation_required")
            if confirmation.get("status") == "human_review":
                suspension_workflow["state"] = SUSPENSION_STATE_HUMAN_REVIEW_REQUIRED
                billing_ticket["automation_status"] = "human_review_required"
            suspension_workflow["updated_at"] = timestamp
            billing_ticket["automation_context"] = {
                **prior_automation_context,
                SUSPENSION_CONTACT_WORKFLOW_KEY: suspension_workflow,
            }
            await _sync(repository.cancel_pending_account_reply_jobs, client_ticket_id, updated_at=timestamp)
            await _sync(repository.save_ticket, canonical_ticket, new_messages=[customer_msg])
            await _sync(repository.save_account_case, billing_ticket)
            return {
                **billing_ticket,
                "messages": canonical_ticket.get("messages", []),
                "support_ticket_status": canonical_ticket.get("status"),
                "ai_reply_status": None,
            }

        confirmed_email = str(confirmation.get("email") or "").strip().lower()
        suspension_workflow.update(
            {
                "state": SUSPENSION_STATE_HANDOFF_PENDING,
                "confirmed_email": confirmed_email,
                "confirmation_message_id": str(customer_msg.get("external_id") or timestamp),
                "updated_at": timestamp,
                "failure_reason": None,
            }
        )
        fields = prior_collected_fields if isinstance(prior_collected_fields, dict) else {}
        handoff_payload = build_billing_internal_email_payload(
            action="account_suspension",
            collected_fields={str(key): str(value) for key, value in fields.items() if value is not None},
            ticket_id=client_ticket_id,
            customer_email=confirmed_email,
            customer_message=conversation_text,
            billing_ticket_id=str(billing_ticket.get("billing_ticket_id") or billing_ticket_id),
            zendesk_ticket_url=_account_zendesk_ticket_url(canonical_ticket.get("source"), client_ticket_id),
        )
        billing_ticket["internal_email_payload"] = handoff_payload
        billing_ticket["internal_email_send_status"] = "pending"
        billing_ticket["internal_email_send_reason"] = "contact_confirmed"
        billing_ticket["automation_context"] = {
            **prior_automation_context,
            SUSPENSION_CONTACT_WORKFLOW_KEY: suspension_workflow,
        }
        await _sync(repository.save_ticket, canonical_ticket, new_messages=[customer_msg])
        await _sync(repository.save_account_case, billing_ticket)
        delivery_result, billing_ticket = await _run_internal_email_delivery(
            repository=repository,
            account_case=billing_ticket,
            ticket_id=client_ticket_id,
            handler="account_suspension",
            payload=handoff_payload,
            sender=send_billing_internal_email,
        )
        if not delivery_result.succeeded:
            suspension_workflow["state"] = SUSPENSION_STATE_HUMAN_REVIEW_REQUIRED
            suspension_workflow["failure_reason"] = delivery_result.reason or delivery_result.status
            billing_ticket["automation_context"] = {
                **dict(billing_ticket.get("automation_context") or {}),
                SUSPENSION_CONTACT_WORKFLOW_KEY: suspension_workflow,
            }
            await _sync(repository.save_account_case, billing_ticket)
            return {
                **billing_ticket,
                "messages": canonical_ticket.get("messages", []),
                "support_ticket_status": canonical_ticket.get("status"),
                "ai_reply_status": None,
            }

        suspension_workflow["state"] = SUSPENSION_STATE_CLOSING_REPLY_PENDING
        suspension_workflow["handoff_delivery_key"] = str(
            (billing_ticket.get("internal_email_payload") or {}).get("delivery_key") or ""
        )
        suspension_workflow["updated_at"] = _now_iso()
        billing_ticket["automation_context"] = {
            **dict(billing_ticket.get("automation_context") or {}),
            SUSPENSION_CONTACT_WORKFLOW_KEY: suspension_workflow,
        }
        billing_ticket["automation_status"] = "automation"
        await _sync(repository.save_account_case, billing_ticket)
        try:
            persona_assignment = await _sync(repository.resolve_account_persona, client_ticket_id)
            closing_job = await _sync(
                _create_reply_job,
                repository=repository,
                ticket_id=client_ticket_id,
                trigger_message_created_at=timestamp,
                reply_facts=closing_reply_facts(
                    confirmed_email=confirmed_email,
                    customer_name=billing_ticket.get("customer_name"),
                ),
                asked_field_keys=[],
                persona_assignment=persona_assignment,
                automation_delivery_key=str(
                    (billing_ticket.get("internal_email_payload") or {}).get("delivery_key") or ""
                ),
                close_after_publish=True,
                reply_intent="account_suspension_handoff_and_close",
                    processing_profile=processing_profile,
            )
        except Exception as exc:
            suspension_workflow["state"] = SUSPENSION_STATE_HUMAN_REVIEW_REQUIRED
            suspension_workflow["failure_reason"] = "account_suspension_closing_reply_job_failed"
            billing_ticket["automation_context"] = {
                **dict(billing_ticket.get("automation_context") or {}),
                SUSPENSION_CONTACT_WORKFLOW_KEY: suspension_workflow,
            }
            billing_ticket = await _record_execution_failure(
                repository=repository,
                account_case=billing_ticket,
                ticket_id=client_ticket_id,
                handler="account_suspension",
                stage="reply_job",
                reason_code="account_reply_job_creation_failed",
                detail=exc,
            )
            return {
                **billing_ticket,
                "messages": canonical_ticket.get("messages", []),
                "support_ticket_status": canonical_ticket.get("status"),
                "ai_reply_status": None,
            }
        suspension_workflow["closing_reply_job_id"] = closing_job.get("job_id")
        suspension_workflow["updated_at"] = _now_iso()
        billing_ticket["automation_context"] = {
            **dict(billing_ticket.get("automation_context") or {}),
            SUSPENSION_CONTACT_WORKFLOW_KEY: suspension_workflow,
        }
        await _sync(repository.save_account_case, billing_ticket)
        return {
            **billing_ticket,
            "messages": canonical_ticket.get("messages", []),
            "support_ticket_status": canonical_ticket.get("status"),
            **_reply_job_public(closing_job),
        }
    elif prior_handler == "account_suspension" and isinstance(suspension_workflow, dict) and str(
        suspension_workflow.get("state") or ""
    ).strip() in {
        SUSPENSION_STATE_HANDOFF_PENDING,
        SUSPENSION_STATE_CLOSING_REPLY_PENDING,
        "closed",
        "human_review_required",
    }:
        billing_ticket["automation_context"] = {
            **prior_automation_context,
            SUSPENSION_CONTACT_WORKFLOW_KEY: suspension_workflow,
        }
        await _sync(repository.save_ticket, canonical_ticket, new_messages=[customer_msg])
        await _sync(repository.save_account_case, billing_ticket)
        return {
            **billing_ticket,
            "messages": canonical_ticket.get("messages", []),
            "support_ticket_status": canonical_ticket.get("status"),
            "ai_reply_status": None,
        }

    def build_automation_attempt(handler: str, action: str) -> dict[str, Any]:
        registration = account_automation_handler(action)
        if registration is None or registration.handler != handler:
            raise ReplySyncError(409, "account case has no registered automation handler")
        ticket_subject = str(canonical_ticket.get("subject") or billing_ticket.get("title") or "")
        customer_messages = list(canonical_ticket.get("messages") or [])
        customer_email = str(canonical_ticket.get("customer_id") or "").strip() or None
        url = _account_zendesk_ticket_url(canonical_ticket.get("source"), client_ticket_id)
        account_case_id_value = str(billing_ticket.get("account_case_id") or billing_ticket_id)
        if registration.implementation == "account_verification":
            persisted_follow_up_count = int(prior_automation_context.get("follow_up_count") or 0)
            if persisted_follow_up_count == 0 and already_requested_fields:
                persisted_follow_up_count = 1
            return _build_verification_attempt(
                ticket_subject=ticket_subject,
                customer_messages=customer_messages,
                ticket_id=client_ticket_id,
                account_case_id=account_case_id_value,
                customer_email=customer_email,
                zendesk_ticket_url=url,
                existing_fields=prior_collected_fields,
                follow_up_count=persisted_follow_up_count,
            )
        if registration.implementation == "billing":
            return _build_billing_attempt(
                action=action,
                message=conversation_text,
                ticket_id=client_ticket_id,
                billing_ticket_id=billing_ticket_id,
                customer_email=customer_email,
                requester=str(canonical_ticket.get("requester") or "").strip() or None,
                zendesk_ticket_url=url,
                already_requested_fields=sorted(already_requested_fields),
            )
        if registration.implementation == "enablement":
            return _build_enablement_attempt(
                message=conversation_text,
                ticket_subject=ticket_subject,
                customer_messages=customer_messages,
                ticket_id=client_ticket_id,
                account_case_id=account_case_id_value,
                customer_email=customer_email,
                zendesk_ticket_url=url,
                existing_fields=prior_collected_fields,
                already_requested_fields=sorted(already_requested_fields),
            )
        raise ReplySyncError(409, "account case has no registered automation handler")

    automation_attempt: dict[str, Any] | None = None
    handler_continued = False
    route_result = None
    if precomputed_route is not None:
        route_payload = dict(precomputed_route)
        classification = dict(route_payload.pop("classification", {}) or {})
        route_result = SimpleNamespace(
            decision=SimpleNamespace(**route_payload),
            classification=classification,
            prompt_snapshots=dict(route_prompt_snapshots or {}),
            stage_attempts=list(route_payload.get("stage_attempts") or []),
        )
    if prior_classification.get("handler_binding_status") == "active" and prior_handler:
        candidate_attempt = build_automation_attempt(prior_handler, prior_action)
        candidate_collected = dict(candidate_attempt["collected_fields"])
        candidate_missing = list(candidate_attempt["missing_fields"])
        registration = account_automation_handler(prior_action)
        if registration and registration.implementation == "account_verification":
            ticket_context = [
                {"role": str(msg.get("role") or ""), "content": str(msg.get("content") or "")}
                for msg in canonical_ticket.get("messages", [])
                if isinstance(msg, dict)
            ]
            latest_assistant_message = next(
                (
                    msg
                    for msg in reversed(canonical_ticket.get("messages", []))
                    if isinstance(msg, dict) and str(msg.get("role") or "").lower() == "assistant"
                ),
                None,
            )
            if route_result is None:
                route_result = decide_account_route(
                    customer_message,
                    ticket_subject=str(canonical_ticket.get("subject") or billing_ticket.get("title") or ""),
                    ticket_context=ticket_context,
                    latest_assistant_message=latest_assistant_message,
                    current_ticket_status=str(canonical_ticket.get("status") or ""),
                    require_latest=True,
                )
            probe_classification = route_result.classification
            probe_action = str(route_result.decision.execution_action or route_result.decision.route or "").strip()
            field_progress = any(
                value and value != prior_collected_fields.get(key)
                for key, value in candidate_collected.items()
            )
            handler_continued = (
                bool(candidate_attempt.get("requires_human_review"))
                or field_progress
                or probe_classification.get("intent_class") == "conversation"
                or (probe_classification.get("intent_class") == "agora" and probe_action == prior_action)
            )
        else:
            handler_continued = bool(candidate_attempt.get("requires_human_review")) or (
                any(
                    value and value != prior_collected_fields.get(key)
                    for key, value in candidate_collected.items()
                )
                or len(candidate_missing) < len(prior_missing_fields)
                or bool(candidate_attempt.get("internal_email_to_send"))
            )
        if handler_continued:
            automation_attempt = candidate_attempt

    decision = None
    if not handler_continued:
        ticket_context = [
            {"role": str(msg.get("role") or ""), "content": str(msg.get("content") or "")}
            for msg in canonical_ticket.get("messages", [])
            if isinstance(msg, dict)
        ]
        latest_assistant_message = next(
            (
                msg
                for msg in reversed(canonical_ticket.get("messages", []))
                if isinstance(msg, dict) and str(msg.get("role") or "").lower() == "assistant"
            ),
            None,
        )
        if route_result is None:
            route_result = decide_account_route(
                customer_message,
                ticket_subject=str(canonical_ticket.get("subject") or billing_ticket.get("title") or ""),
                ticket_context=ticket_context,
                latest_assistant_message=latest_assistant_message,
                current_ticket_status=str(canonical_ticket.get("status") or ""),
                require_latest=True,
            )
        decision = route_result.decision
        route = str(decision.execution_action or decision.route or "").strip()
        route_classification = dict(route_result.classification)
        route_metadata = account_route_metadata(
            classification=route_classification,
            route_family=decision.route_family,
            execution_action=route,
        )
        if prior_classification.get("handler_binding_status") == "active":
            route_classification["superseded_automation_handler"] = prior_handler or None
            route_classification["previous_handler_binding_status"] = "superseded"
        billing_ticket.update(
            {
                "route": route or None,
                "scope_label": decision.scope_label,
                "route_family": decision.route_family,
                "execution_action": route or None,
                "route_reason": decision.reason,
                "route_confidence": decision.confidence,
                "matched_signals": list(decision.matched_signals),
                "semantic_intent": decision.semantic_intent,
                "automation_eligibility": decision.automation_eligibility,
                "policy_decision": decision.policy_decision,
                "not_automated_reason": decision.not_automated_reason,
                "risk_flags": list(decision.risk_flags),
                "evidence_spans": list(decision.evidence_spans),
                "router_source": decision.router_source,
                "route_classification": route_classification,
                **route_metadata,
            }
        )
        is_automation_route = is_registered_automation(
            route_family=decision.route_family,
            execution_action=route,
        )
        if is_automation_route:
            new_handler = str(route_metadata.get("automation_handler") or "").strip()
            automation_attempt = build_automation_attempt(new_handler, route)
            billing_ticket["automation_status"] = "automation"
        else:
            billing_ticket.update(
                automation_status="not_automated",
                missing_fields=[],
                collected_fields={},
                customer_reply=None,
                internal_email_payload=None,
                internal_email_send_status="not_applicable",
                internal_email_send_reason="",
                automation_context={},
                route_classification=route_classification,
            )
        from backend.services.account_admin import route_execution_from_decision

        await _sync(
            repository.save_account_route_execution,
            route_execution_from_decision(
                ticket_id=client_ticket_id,
                decision=decision,
                system_prompt=None,
                user_prompt=None,
                created_at=timestamp,
                classification=route_classification,
                prompt_snapshots=dict(route_result.prompt_snapshots),
                stage_attempts=getattr(route_result, "stage_attempts", None) if route_result is not None else None,
            ),
        )

    should_send_internal_email = False
    collected_fields: dict[str, Any] = dict(billing_ticket.get("collected_fields") or {})
    if automation_attempt is not None and automation_attempt.get("requires_human_review"):
        extraction = automation_attempt.get("field_extraction")
        if isinstance(extraction, AccountVerificationFieldExtraction):
            failure_reason = f"account_verification_field_extraction_{extraction.status}"
            billing_ticket = reconcile_automation_execution_failure(
                billing_ticket,
                reason_code=failure_reason,
                extraction=extraction,
                context=dict(
                    automation_attempt.get("automation_context")
                    or prior_automation_context
                ),
            )
            billing_ticket["internal_email_send_reason"] = (
                f"field_extraction_{extraction.status}"
            )
        automation_attempt = None
        await _sync(repository.cancel_pending_account_reply_jobs, client_ticket_id, updated_at=timestamp)

    if automation_attempt is not None:
        missing_fields = list(automation_attempt["missing_fields"])
        requested_field_keys = (
            [field_name for field_name in missing_fields if field_name not in already_requested_fields]
            if not automation_attempt.get("internal_email_to_send")
            else []
        )
        collected_fields = dict(automation_attempt["collected_fields"])
        assistant_reply_facts = _reply_facts(
            handler=str(billing_ticket.get("automation_handler") or ""),
            action=str(billing_ticket.get("execution_action") or billing_ticket.get("route") or ""),
            missing_fields=missing_fields,
            collected_fields=collected_fields,
            submitted=bool(automation_attempt.get("internal_email_to_send")),
            customer_name=str(billing_ticket.get("customer_name") or ""),
        )
        current_classification = (
            dict(billing_ticket.get("route_classification"))
            if isinstance(billing_ticket.get("route_classification"), dict)
            else prior_classification
        )
        current_classification["handler_binding_status"] = "active" if missing_fields else "completed"
        if automation_attempt.get("internal_email_to_send"):
            current_classification["handler_binding_status"] = "completed"
        merged_automation_context = dict(
            automation_attempt.get("automation_context") or prior_automation_context
        )
        ownership_state = (billing_ticket.get("automation_context") or {}).get("zendesk_ownership")
        if isinstance(ownership_state, dict):
            merged_automation_context["zendesk_ownership"] = ownership_state
        billing_ticket.update(
            missing_fields=missing_fields,
            collected_fields=collected_fields,
            customer_reply=None,
            internal_email_payload=automation_attempt["internal_email_payload"],
            route_classification=current_classification,
            automation_context=merged_automation_context,
        )
        same_automation = (
            prior_action == str(billing_ticket.get("execution_action") or billing_ticket.get("route") or "").strip()
            and prior_handler == str(billing_ticket.get("automation_handler") or "").strip()
        )
        prior_send_status = str(billing_ticket.get("internal_email_send_status") or "").strip()
        should_send_internal_email = not same_automation or prior_send_status in {
            "",
            "not_ready",
            "pending",
            "retry",
            "failed",
            "skipped_config_missing",
        }
        if should_send_internal_email:
            billing_ticket["internal_email_send_status"] = automation_attempt["internal_email_send_status"]
            billing_ticket["internal_email_send_reason"] = automation_attempt["internal_email_send_reason"]
    billing_ticket["updated_at"] = timestamp
    new_messages = canonical_ticket.get("messages", [])[initial_message_count:]
    await _sync(repository.save_ticket, canonical_ticket, new_messages=new_messages)
    await _sync(repository.save_account_case, billing_ticket)

    if should_send_internal_email and automation_attempt and automation_attempt.get("internal_email_to_send"):
        active_handler = str(billing_ticket.get("automation_handler") or "").strip()
        sender = send_enablement_internal_email if active_handler == "enablement" else send_billing_internal_email
        delivery_result, billing_ticket = await _run_internal_email_delivery(
            repository=repository,
            account_case=billing_ticket,
            ticket_id=client_ticket_id,
            handler=active_handler,
            payload=dict(automation_attempt["internal_email_to_send"]),
            sender=sender,
        )
        if delivery_result.succeeded:
            assistant_reply_facts = _reply_facts(
                handler=active_handler or "billing",
                action=str(billing_ticket.get("execution_action") or billing_ticket.get("route") or active_handler),
                missing_fields=[],
                collected_fields=collected_fields,
                submitted=True,
                customer_name=str(billing_ticket.get("customer_name") or ""),
            )
            reply_ready = True
        billing_ticket["updated_at"] = _now_iso()
        await _sync(repository.save_account_case, billing_ticket)

    reply_job = None
    if automation_attempt is not None and (requested_field_keys or reply_ready):
        followup_action = str(
            billing_ticket.get("execution_action") or billing_ticket.get("route") or ""
        ).strip()
        try:
            reply_job = await _sync(
                _create_reply_job,
                repository=repository,
                ticket_id=client_ticket_id,
                trigger_message_created_at=timestamp,
                reply_facts=assistant_reply_facts,
                asked_field_keys=requested_field_keys,
                persona_assignment=persona_assignment,
                automation_delivery_key=(
                    str((billing_ticket.get("internal_email_payload") or {}).get("delivery_key") or "")
                    if not requested_field_keys
                    else None
                ),
                close_after_publish=bool(reply_ready and followup_action == "account_suspension"),
                reply_intent=(
                    "fraud_handoff_confirmation"
                    if reply_ready and followup_action == "fraud_account"
                    else "account_suspension_handoff_and_close"
                    if reply_ready and followup_action == "account_suspension"
                    else None
                ),
                processing_profile=processing_profile,
            )
        except Exception as exc:
            billing_ticket = await _record_execution_failure(
                repository=repository,
                account_case=billing_ticket,
                ticket_id=client_ticket_id,
                handler=str(billing_ticket.get("automation_handler") or "automation"),
                stage="reply_job",
                reason_code="account_reply_job_creation_failed",
                detail=exc,
            )
            reply_job = None
    elif should_run_reply_rag_fallback(billing_ticket):
        fallback = await _sync(
            try_rag_fallback_answer,
            question=customer_message,
            request_id=f"reply-rag-fallback:{client_ticket_id}:{timestamp}",
            ticket_id=client_ticket_id or None,
            ticket_context=_rag_fallback_ticket_context(canonical_ticket),
        )
        if fallback.kind == "answer":
            try:
                reply_job = await _sync(
                    _create_reply_job,
                    repository=repository,
                    ticket_id=client_ticket_id,
                    trigger_message_created_at=timestamp,
                    # The RAGFlow answer is core technical content: the persona
                    # render voices the customer reply and the references are
                    # appended deterministically before publication.
                    reply_facts={
                        "behavior": "rag_fallback_answer",
                        "reply_intent": ACCOUNT_REPLY_INTENT_RAG_FALLBACK_ANSWER,
                        "provided_answer": fallback.answer,
                        "references": list(fallback.references),
                        # Greeting name lookup: the account case carries the
                        # intake name, the Zendesk comment author name covers
                        # cases whose intake form omitted it, and an empty
                        # value lets the persona fall back to "Customer".
                        "customer_first_name": str(
                            billing_ticket.get("customer_name")
                            or customer_name_hint
                            or ""
                        ).strip(),
                    },
                    reply_intent=ACCOUNT_REPLY_INTENT_RAG_FALLBACK_ANSWER,
                    processing_profile=processing_profile,
                )
            except Exception as exc:
                billing_ticket = await _record_execution_failure(
                    repository=repository,
                    account_case=billing_ticket,
                    ticket_id=client_ticket_id,
                    handler=str(billing_ticket.get("automation_handler") or "automation"),
                    stage="reply_job",
                    reason_code="account_reply_job_creation_failed",
                    detail=exc,
                )
                reply_job = None
        else:
            escalation = await _sync(
                escalate_unexpected_reply_to_human,
                account_case=billing_ticket,
                ticket_id=client_ticket_id,
                zendesk_ticket_id=_rag_fallback_zendesk_ticket_id(billing_ticket, client_ticket_id),
                customer_reply_text=customer_message,
                reason=fallback.reason,
                repository=repository,
                timestamp=timestamp,
            )
            LOGGER.info(
                "reply RAG fallback escalated case %s to human review: %s",
                client_ticket_id,
                escalation,
            )

    return {
        **billing_ticket,
        "messages": canonical_ticket.get("messages", []),
        "support_ticket_status": canonical_ticket.get("status"),
        **_reply_job_public(reply_job),
    }


def _active_investigation_from_case_payload(engineer_case: dict[str, Any]) -> dict[str, Any] | None:
    active = engineer_case.get("active_investigation")
    if isinstance(active, dict):
        return active
    history = engineer_case.get("investigation_history")
    if isinstance(history, list) and history and isinstance(history[0], dict):
        return history[0]
    return None


def _engineer_case_payload_to_record(engineer_case: dict[str, Any]) -> dict[str, Any]:
    investigation = _active_investigation_from_case_payload(engineer_case) or {}
    return {
        "engineer_case_id": str(engineer_case.get("engineer_case_id") or engineer_case.get("ticket_id") or "").strip(),
        "client_ticket_id": str(
            engineer_case.get("client_ticket_id")
            or ((engineer_case.get("client_ticket_ref") or {}).get("ticket_id"))
            or ""
        ).strip(),
        "case_sequence": engineer_case.get("case_sequence"),
        "title": str(engineer_case.get("title") or engineer_case.get("subject") or "Engineer case").strip(),
        "status": normalize_ticket_status(engineer_case.get("status")),
        "assigned_engineer_id": str(engineer_case.get("assigned_engineer_id") or "").strip() or None,
        "trigger_source": str(investigation.get("trigger_source") or engineer_case.get("trigger_source") or "").strip(),
        "trigger_reason": str(investigation.get("trigger_reason") or engineer_case.get("trigger_reason") or "").strip(),
        "thread_id": str(investigation.get("id") or engineer_case.get("thread_id") or "").strip(),
        "draft_customer_reply": str(investigation.get("draft_customer_reply") or "").strip(),
        "final_confirmation_requested_at": investigation.get("final_confirmation_requested_at"),
        "engineer_handoff_packet": (
            engineer_case.get("engineer_handoff_packet")
            if isinstance(engineer_case.get("engineer_handoff_packet"), dict)
            else None
        ),
        "engineer_agent_state": (
            engineer_case.get("engineer_agent_state")
            if isinstance(engineer_case.get("engineer_agent_state"), dict)
            else None
        ),
        "opened_at": investigation.get("opened_at") or engineer_case.get("opened_at") or engineer_case.get("created_at"),
        "updated_at": investigation.get("updated_at") or engineer_case.get("updated_at"),
        "closed_at": investigation.get("closed_at") or engineer_case.get("closed_at"),
        "investigation_state": str(investigation.get("state") or ("closed" if engineer_case.get("closed_at") else "active")).strip().lower(),
        "messages": investigation.get("messages") if isinstance(investigation.get("messages"), list) else [],
    }


def _close_engineer_case_for_customer_resolution(
    ticket: dict[str, Any],
    engineer_case: dict[str, Any],
    *,
    now_value: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    case_context = build_engineer_case_context(ticket, engineer_case)
    _, investigation_messages = close_case_context_active_investigation(
        case_context,
        now_value=now_value,
        system_note="Investigation closed because the customer confirmed the issue is resolved.",
    )
    engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
    engineer_case["status"] = RESOLVED_STATUS
    engineer_case["investigation_state"] = "closed"
    ticket["active_engineer_case_id"] = None
    return engineer_case, investigation_messages


async def sync_account_case_ticket_status(
    *,
    repository: Any,
    normalized_ticket_id: str,
    zendesk_status: str,
    source_updated_at: str | None,
) -> dict[str, Any]:
    """Port of the old /production PUT .../status semantics (p2-112 Phase D)."""
    import asyncio

    def _sync(call, *args, **kwargs):
        return asyncio.get_running_loop().run_in_executor(None, lambda: call(*args, **kwargs))

    account_case = await _sync(repository.get_account_case_by_ticket_id, normalized_ticket_id)
    if not isinstance(account_case, dict):
        raise ReplySyncError(404, "Account Case not found")
    account_case_id = str(
        account_case.get("account_case_id") or account_case.get("billing_ticket_id") or ""
    ).strip()
    if not account_case_id:
        raise ReplySyncError(409, "Account Case has no canonical id")
    automation_status = str(account_case.get("automation_status") or "").strip().lower()

    sync_timestamp = _now_iso()
    status_engineer_case = None
    engineer_slack_event = None
    if (
        str(account_case.get("processing_profile") or "staging").strip().lower()
        == "production"
        and automation_status == "not_automated"
    ):
        client_ticket_id = str(account_case.get("client_ticket_id") or "").strip()
        status_engineer_case = (
            await _sync(repository.get_active_engineer_case, client_ticket_id, include_client_messages=True)
            if client_ticket_id
            else None
        )
        engineer_case_id = (
            str(status_engineer_case.get("engineer_case_id") or "").strip()
            if isinstance(status_engineer_case, dict)
            else ""
        )
        if engineer_case_id:
            active_investigation = (
                status_engineer_case.get("active_investigation")
                if isinstance(status_engineer_case.get("active_investigation"), dict)
                else {}
            )
            revision_token = str(source_updated_at or sync_timestamp).strip()
            prior_status = str(account_case.get("zendesk_ticket_status") or "").strip().lower() or None
            engineer_slack_event = build_engineer_case_status_changed_event(
                event_id=(
                    f"engineer-slack:{engineer_case_id}:zendesk-status:"
                    f"{prior_status or 'unknown'}:{zendesk_status}:{revision_token}"
                ),
                engineer_case_id=engineer_case_id,
                prior_status=prior_status,
                zendesk_status=zendesk_status,
                investigation_id=str(active_investigation.get("id") or "").strip() or None,
            )

    try:
        result = await _sync(
            repository.update_account_case_zendesk_status,
            account_case_id=account_case_id,
            zendesk_status=zendesk_status,
            synced_at=sync_timestamp,
            source_updated_at=source_updated_at,
            engineer_slack_event=engineer_slack_event,
        )
    except KeyError as exc:
        raise ReplySyncError(404, "Account Case not found") from exc
    engineer_case_closed = False
    normalized_zendesk_status = str(zendesk_status or "").strip().lower()
    if (
        normalized_zendesk_status in {"solved", "closed"}
        and str(result.get("status") or "").strip().lower() != "stale_ignored"
    ):
        client_ticket_id = str(account_case.get("client_ticket_id") or "").strip()
        active_case_payload = status_engineer_case or (
            repository.get_active_engineer_case(client_ticket_id, include_client_messages=True)
            if client_ticket_id
            else None
        )
        ticket = repository.get_ticket(client_ticket_id) if client_ticket_id else None
        if isinstance(active_case_payload, dict) and isinstance(ticket, dict):
            engineer_case = _engineer_case_payload_to_record(active_case_payload)
            engineer_case, closed_messages = _close_engineer_case_for_customer_resolution(
                ticket,
                engineer_case,
                now_value=_now_iso(),
            )
            engineer_case_id = str(engineer_case.get("engineer_case_id") or "").strip()
            thread_events = []
            if (
                str(result.get("status") or "").strip().lower() == "updated"
                and not bool(result.get("engineer_slack_event_queued"))
            ):
                thread_events.append(
                    build_engineer_case_thread_event(
                        event_id=f"engineer-slack:{engineer_case_id}:closed:{normalized_zendesk_status}",
                        event_type="engineer_case_closed",
                        engineer_case_id=engineer_case_id,
                        message_text=f"Zendesk ticket is {normalized_zendesk_status}. This Case thread is closed.",
                        investigation_id=str(engineer_case.get("thread_id") or "") or None,
                    )
                )
            ticket["status"] = RESOLVED_STATUS
            ticket["closed_at"] = _now_iso()
            ticket["updated_at"] = ticket["closed_at"]
            repository.save_ticket(ticket, new_messages=[])
            repository.save_engineer_case(
                engineer_case,
                new_messages=closed_messages,
                slack_events=thread_events,
            )
            EngineerAssignmentService(repository).resolve_case(
                engineer_case_id,
                actor="zendesk_status_sync",
            )
            engineer_case_closed = True
    return {
        "status": str(result.get("status") or "updated"),
        "is_account_case": True,
        "zendesk_ticket_id": normalized_ticket_id,
        "account_case_id": account_case_id,
        "zendesk_status": normalized_zendesk_status,
        "engineer_case_closed": engineer_case_closed,
        "engineer_slack_event_queued": bool(result.get("engineer_slack_event_queued")),
        "source_updated_at": source_updated_at,
        "synced_at": result.get("synced_at"),
    }
