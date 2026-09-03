from __future__ import annotations

import hashlib
import logging
import os
import re
import signal
import sys
import threading
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import psycopg

if str(os.getenv("AUTOMATION_ECS_ACCOUNT_ONLY") or "").strip() == "1":
    ticket_repository: Any = None
    asset_repository: Any = None
    asset_storage: Any = None

    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _full_runtime_unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("general ticket processing is unavailable in the Account-only ECS Worker")

    _record_ticket_agent_runtime_events = _full_runtime_unavailable
    _run_client_ticket_review_agent = _full_runtime_unavailable
    build_query_task = _full_runtime_unavailable
    build_client_sync_event = _full_runtime_unavailable
    ensure_ticket_defaults = _full_runtime_unavailable
    resolve_support_message = _full_runtime_unavailable
else:
    from backend.main import (
        _record_ticket_agent_runtime_events,
        _run_client_ticket_review_agent,
        build_query_task,
        build_client_sync_event,
        ensure_ticket_defaults,
        now_iso,
        resolve_support_message,
        asset_repository,
        asset_storage,
        ticket_repository,
    )
from backend.services.account_ai_execution import AccountProcessingFailure
from backend.services.account_failure_alerts import notify_account_failure
from backend.services.account_admin import (
    AccountPersonaUnavailableError,
    apply_persona_to_customer_reply,
    normalize_account_persona_content,
)
from backend.services.account_reply_jobs import (
    AccountReplyContractError,
    ACCOUNT_REPLY_INTENT_RAG_FALLBACK_ANSWER,
    ACCOUNT_REPLY_INTENT_DETAILED_INVOICE_COMPLETED_AND_CLOSE,
    ACCOUNT_REPLY_PERSONA_LEGACY_PIPELINE,
    ACCOUNT_REPLY_PERSONA_PIPELINE,
    ACCOUNT_REPLY_PERSONA_PREPARING,
    ACCOUNT_REPLY_PERSONA_PUBLISHING,
    ACCOUNT_REPLY_PERSONA_QUEUED,
    ACCOUNT_REPLY_PERSONA_SCHEDULED,
    ACCOUNT_REPLY_PERSONA_V8_PREPARING,
    ACCOUNT_REPLY_PERSONA_V8_PUBLISHING,
    ACCOUNT_REPLY_PERSONA_V8_QUEUED,
    ACCOUNT_REPLY_PERSONA_V8_SCHEDULED,
    account_reply_delay_seconds_for_profile,
    account_reply_persona_pipeline_for_job,
    account_reply_persona_status_for_stage,
    create_account_reply_job,
    ACCOUNT_REPLY_INTENT_ENABLEMENT_COMPLETED_AND_CLOSE,
    ACCOUNT_REPLY_INTENT_FRAUD_HANDOFF_CONFIRMATION,
    ACCOUNT_REPLY_INTENT_REQUEST_MISSING_INFORMATION,
    ACCOUNT_REPLY_INTENT_RESOLUTION_UPDATE,
    ACCOUNT_REPLY_INTENT_SUSPENSION_HANDOFF_AND_CLOSE,
    normalize_account_reply_contract,
    is_account_reply_persona_preparing_status,
    is_account_reply_persona_publishing_status,
)
from backend.services.automation_persona import (
    AUTOMATION_PERSONA_PROMPT_VERSION,
    AutomationPersonaError,
    build_account_automation_reply_facts,
    build_automation_reply_facts,
    assert_no_trailing_automation_signature,
    extract_automation_resolution_facts,
    render_automation_reply,
    resolve_customer_greeting_name,
    sanitize_enablement_completion_note,
    validate_account_reply_contract,
)
from backend.services.enablement_completion_classifier import (
    classify_enablement_completion,
)
from backend.services.account_automation_reconciliation import (
    reconcile_automation_execution_failure,
    reconciliation_reason_code,
)
from backend.services.account_automation_delivery import (
    deliver_account_internal_email,
    ensure_account_delivery_key,
    is_rerun_owned_delivery,
)
from backend.services.automation_routing import is_registered_automation
from backend.services.account_automation_ownership import (
    ensure_production_automation_ownership,
    mark_production_ownership_handed_to_reviewer,
)
from backend.services.account_human_review_escalation import (
    escalate_account_case_to_human_review,
    reconcile_account_human_review_queue_mismatches,
)
from backend.services.account_reply_rag_fallback import format_rag_fallback_references
from backend.services.zendesk_comments import (
    ZendeskCommentError,
    add_ticket_comment,
    read_ticket_comment_audit,
)
from backend.services.zendesk_ticket_assignment import (
    ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID_ENV,
    assign_ticket_to_reviewer,
    read_ticket_ownership_snapshot,
)
from backend.services.account_zendesk_internal_comment import (
    AccountZendeskInternalCommentError,
    deliver_account_ai_message_as_internal_comment,
    reconcile_account_ai_message_internal_comment,
)
from backend.services.account_slack_n8n import (
    AccountSlackN8nError,
    account_slack_n8n_configured,
    get_account_slack_event_status,
    post_account_slack_event,
)
from backend.services.engineer_slack import (
    EngineerSlackDeliveryError,
    build_engineer_case_thread_event,
    engineer_slack_configured,
    post_engineer_slack_event,
)
from backend.services.app_build import get_app_build_info
from backend.services.asset_storage import build_asset_s3_key, sanitize_asset_filename
from backend.services.engineer_cases import (
    build_new_engineer_case,
    apply_case_context_to_engineer_case,
    build_engineer_case_context,
    close_case_context_active_investigation,
    derive_engineer_case_title,
)
from backend.services.engineer_assignment import EngineerAssignmentService
from backend.services.event_bus import SyncRedisEventBus
from backend.services.investigation_flow import (
    INVESTIGATING_STATUS,
    RESOLVED_STATUS,
    build_investigation_opening_context,
    default_investigation_prompt as generate_investigation_ai_turn,
    normalize_ticket_status,
    start_or_refresh_investigation,
)
from backend.services.billing_automation import (
    BILLING_INTERNAL_EMAIL_SUBJECT_PREFIX,
    poll_automation_request_replies,
    record_billing_request_reply,
    send_billing_internal_email,
)
from backend.services.internal_email_template import namespaced_internal_email_subject
from backend.services.account_internal_email_recipients import (
    resolve_account_internal_email_recipients,
)
from backend.services.account_suspension_automation import (
    SUSPENSION_CONTACT_WORKFLOW_KEY,
    SUSPENSION_REPLY_INTENT_CONTACT_CONFIRMATION,
    SUSPENSION_STATE_CLOSED,
)
from backend.services.automation_account_intake import _zendesk_ticket_url
from backend.services.enablement_automation import (
    ENABLEMENT_INTERNAL_EMAIL_SUBJECT_PREFIX,
    send_enablement_internal_email,
)
from backend.services.internal_email_payload import (
    InternalEmailPayloadUpgradeError,
    upgrade_internal_email_payload,
)
from backend.services.graph_mail import send_graph_mail
from backend.services.quota_automation import (
    QUOTA_INTERNAL_EMAIL_SUBJECT_PREFIX,
)
from backend.services.billing_response_flow import (
    BILLING_RESPONSE_AI_FOLLOWUP_EVENT,
    BILLING_RESPONSE_EVENT,
    BILLING_RESPONSE_RESULT_COMPLETED,
    build_billing_internal_resolution_event,
)
from backend.services.client_ticket_agent_runtime import (
    TicketExecutionResult,
    build_execution_route_payload,
    execute_client_ticket_agent_runtime,
    resolve_next_ticket_status,
)
from backend.services.product_selection import resolve_support_product_context
from backend.services.prompt_runtime import initialize_prompt_runtime
from backend.services.runtime_schema import check_runtime_schema, runtime_schema_check_enabled
from backend.services.rag_executor import build_ragflow_worker_executor, build_worker_rag_executor
from backend.services.ragflow_docs_search_skill import RagflowDocsSearchSkillClient
from backend.services.rag_service_client import (
    RagServiceClient,
    RagServiceError,
    RagTicketAnswerDetail,
)
from backend.services.sentiment_classifier import classify_sentiment
from backend.services.support_router import decide_support_route
from backend.services.task_queue import SyncRedisTaskQueue
from backend.services.llm_usage_capture import (
    begin_case_usage_capture,
    end_case_usage_capture,
    flush_case_usage_capture,
)
from backend.services.ticket_message_sentiment import (
    build_ticket_message_sentiment_event,
    classify_customer_message_sentiment,
)

LOGGER = logging.getLogger(__name__)
SHUTTING_DOWN = False
TICKET_LOOKUP_RETRY_MAX = 6


def _engineer_case_record_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    active = payload.get("active_investigation")
    investigation = active if isinstance(active, dict) else {}
    return {
        "engineer_case_id": str(payload.get("engineer_case_id") or payload.get("ticket_id") or "").strip(),
        "client_ticket_id": str(
            payload.get("client_ticket_id")
            or ((payload.get("client_ticket_ref") or {}).get("ticket_id"))
            or ""
        ).strip(),
        "case_sequence": payload.get("case_sequence"),
        "title": str(payload.get("title") or payload.get("subject") or "Engineer case").strip(),
        "status": normalize_ticket_status(payload.get("status")),
        "assigned_engineer_id": str(payload.get("assigned_engineer_id") or "").strip() or None,
        "trigger_source": str(investigation.get("trigger_source") or payload.get("trigger_source") or "").strip(),
        "trigger_reason": str(investigation.get("trigger_reason") or payload.get("trigger_reason") or "").strip(),
        "thread_id": str(investigation.get("id") or payload.get("thread_id") or "").strip(),
        "draft_customer_reply": str(investigation.get("draft_customer_reply") or "").strip(),
        "final_confirmation_requested_at": investigation.get("final_confirmation_requested_at"),
        "engineer_handoff_packet": payload.get("engineer_handoff_packet") if isinstance(payload.get("engineer_handoff_packet"), dict) else None,
        "engineer_agent_state": payload.get("engineer_agent_state") if isinstance(payload.get("engineer_agent_state"), dict) else None,
        "opened_at": investigation.get("opened_at") or payload.get("opened_at"),
        "updated_at": investigation.get("updated_at") or payload.get("updated_at"),
        "closed_at": investigation.get("closed_at") or payload.get("closed_at"),
        "investigation_state": str(investigation.get("state") or "active").strip().lower(),
        "messages": investigation.get("messages") if isinstance(investigation.get("messages"), list) else [],
    }
TICKET_LOOKUP_RETRY_BASE_DELAY_SECONDS = 0.12
MESSAGE_TIMESTAMP_TOLERANCE_SECONDS = 1.0
SUPPORTED_WORKER_TASK_TYPES = ("ticket_query", "ticket_message_sentiment")
PRODUCTION_ACCOUNT_ZENDESK_ACTOR_ID = "system:production-account-reply"


def _latest_customer_author_name(ticket: dict[str, Any] | None) -> str:
    """Author name of the latest customer message, from the persisted meta."""
    for message in reversed(list((ticket or {}).get("messages") or [])):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").strip().lower() not in {"customer", "user"}:
            continue
        meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        return str(meta.get("author_name") or "").strip()
    return ""


def _account_greeting_customer_name(
    account_case: dict[str, Any],
    client_ticket_id: str,
    *,
    canonical_ticket: dict[str, Any] | None = None,
) -> str:
    """Greeting first name: latest customer-comment author, then the case name."""
    ticket = canonical_ticket
    if ticket is None and str(client_ticket_id or "").strip():
        try:
            ticket = ticket_repository.get_ticket(client_ticket_id)
        except Exception:
            ticket = None
    return resolve_customer_greeting_name(
        latest_customer_author_name=_latest_customer_author_name(ticket),
        case_customer_name=account_case.get("customer_name"),
    )


def _enablement_completion_acknowledgement(ticket: dict[str, Any]) -> str:
    requested_missing_information = False
    for message in ticket.get("messages", []):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role == "assistant":
            meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
            reply_intent = str(
                meta.get("reply_intent") or message.get("reply_intent") or ""
            ).strip().lower()
            if reply_intent == ACCOUNT_REPLY_INTENT_REQUEST_MISSING_INFORMATION:
                requested_missing_information = True
        elif role in {"customer", "user"} and requested_missing_information:
            return "additional_information"
    return "patience"


def _with_enablement_completion_acknowledgement(
    payload: dict[str, Any],
    ticket: dict[str, Any],
) -> dict[str, Any]:
    facts = payload.get("reply_facts") if isinstance(payload.get("reply_facts"), dict) else {}
    if (
        str(facts.get("reply_intent") or payload.get("reply_intent") or "").strip().lower()
        == ACCOUNT_REPLY_INTENT_ENABLEMENT_COMPLETED_AND_CLOSE
        and not str(facts.get("completion_acknowledgement") or "").strip()
    ):
        updated_facts = dict(facts)
        updated_facts["completion_acknowledgement"] = (
            _enablement_completion_acknowledgement(ticket)
        )
        payload["reply_facts"] = updated_facts
    return payload


def _account_reply_needs_persona_render(payload: dict[str, Any]) -> bool:
    """Return whether an unpublished Persona payload needs the current policy."""
    if not isinstance(payload.get("reply_facts"), dict) or not payload.get("reply_facts"):
        return False
    return (
        not str(payload.get("generated_content") or "").strip()
        or str(payload.get("persona_prompt_version") or "").strip()
        != AUTOMATION_PERSONA_PROMPT_VERSION
    )


def _normalize_account_reply_job_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], str | None, bool]:
    """Normalize persisted intent fields before rendering or publication."""
    reply_facts = payload.get("reply_facts") if isinstance(payload.get("reply_facts"), dict) else {}
    top_level_intent = str(payload.get("reply_intent") or "").strip() or None
    close_after_publish = bool(payload.get("close_after_publish"))
    if not reply_facts and not top_level_intent and not close_after_publish:
        return payload, None, False
    try:
        normalized_facts, intent, derived_close = normalize_account_reply_contract(
            reply_facts,
            reply_intent=top_level_intent,
            close_after_publish=close_after_publish,
            reject_legacy_fraud_close=True,
        )
    except AccountReplyContractError as exc:
        raise AutomationPersonaError(str(exc)) from exc
    if reply_facts and not intent:
        raise AutomationPersonaError("automation_persona_missing_reply_intent")
    if normalized_facts:
        payload["reply_facts"] = normalized_facts
    if intent:
        payload["reply_intent"] = intent
    if derived_close:
        payload["close_after_publish"] = True
    else:
        payload.pop("close_after_publish", None)
    return payload, intent, derived_close


def _account_reply_contract_required(payload: dict[str, Any]) -> bool:
    facts = payload.get("reply_facts") if isinstance(payload.get("reply_facts"), dict) else {}
    behavior = str(facts.get("behavior") or "").strip().lower()
    intent = str(facts.get("reply_intent") or payload.get("reply_intent") or "").strip().lower()
    return behavior in {"fraud_account", "enablement", "account_suspension"} or intent in {
        ACCOUNT_REPLY_INTENT_FRAUD_HANDOFF_CONFIRMATION,
        SUSPENSION_REPLY_INTENT_CONTACT_CONFIRMATION,
        ACCOUNT_REPLY_INTENT_SUSPENSION_HANDOFF_AND_CLOSE,
        ACCOUNT_REPLY_INTENT_ENABLEMENT_COMPLETED_AND_CLOSE,
    }


def _move_invalid_account_reply_to_human_review(
    job: dict[str, Any],
    ticket: dict[str, Any],
    failure: BaseException,
) -> None:
    transitioned = _move_automation_reply_to_human_review(
        job,
        ticket,
        str(failure),
        policy_decision="account_reply_contract_human_review",
        failure_stage="account_reply_contract",
        failure_code=str(getattr(failure, "code", "account_reply_contract_failed")),
    )
    if transitioned:
        _record_account_worker_failure(job=job, ticket=ticket, failure=failure)


def _record_account_worker_failure(
    *,
    job: dict[str, Any],
    ticket: dict[str, Any] | None,
    failure: BaseException,
) -> None:
    ticket_id = str(job.get("ticket_id") or (ticket or {}).get("ticket_id") or "").strip()
    case = ticket_repository.get_account_case_by_ticket_id(ticket_id) if ticket_id else None
    case_id = str((case or {}).get("account_case_id") or (case or {}).get("billing_ticket_id") or "").strip()
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    stage = str(
        payload.get("failure_stage")
        or getattr(failure, "stage", "account_reply_worker")
        or "account_reply_worker"
    ).strip()
    code = str(
        payload.get("failure_code")
        or getattr(failure, "code", "account_reply_worker_failed")
        or "account_reply_worker_failed"
    ).strip()
    detail = str(getattr(failure, "detail", "") or "").strip()
    attempt_count = max(0, int(getattr(failure, "attempt_count", 1)))
    if not isinstance(failure, AccountProcessingFailure):
        detail = type(failure).__name__
    reply_job_id = str(job.get("job_id") or "unknown-job").strip() or "unknown-job"
    incident_id = (
        f"account-processing:{case_id or ticket_id or 'unknown'}:"
        f"{reply_job_id}:{stage}:{code}"
    )
    now = now_iso()
    if isinstance(case, dict):
        updated = dict(case)
        reason_code = f"account_processing_{code}"[:160]
        updated.update(
            reconcile_automation_execution_failure(
                updated,
                reason_code=reason_code,
                context={
                    "policy_decision": "account_processing_failure_human_review",
                    "failure_stage": stage,
                    "failure_code": code,
                    "failure_attempt_count": attempt_count,
                    "failure_incident_id": incident_id,
                },
            )
        )
        updated.update(
            {
                "failure_stage": stage,
                "failure_code": code,
                "failure_attempt_count": attempt_count,
                "failure_incident_id": incident_id,
                "policy_decision": "account_processing_failure_human_review",
                "updated_at": now,
            }
        )
        classification = dict(updated.get("route_classification") or {})
        classification["handler_binding_status"] = "human_review"
        classification.update(
            {
                "failure_stage": stage,
                "failure_code": code,
                "failure_incident_id": incident_id,
            }
        )
        classification["account_processing_failure"] = {
            "incident_id": incident_id,
            "stage": stage,
            "code": code,
            "attempt_count": attempt_count,
        }
        updated["route_classification"] = classification
        execution_context = dict(updated.get("automation_context") or {})
        execution_context["account_processing_failure"] = dict(classification["account_processing_failure"])
        updated["automation_context"] = execution_context
        ticket_repository.save_account_case(updated)
        ticket_repository.cancel_pending_account_reply_jobs(ticket_id, updated_at=now)
        escalate_account_case_to_human_review(
            account_case=updated,
            ticket_id=ticket_id,
            handler=str(updated.get("automation_handler") or updated.get("execution_action") or "automation"),
            failure_stage=stage,
            failure_code=code,
            reason=detail or "Account reply worker failed and requires human review.",
            repository=ticket_repository,
            timestamp=now,
        )
    if not str(payload.get("rerun_job_id") or "").strip():
        notify_account_failure(
            repository=ticket_repository,
            incident_id=incident_id,
            stage=stage,
            code=code,
            ticket_id=ticket_id or None,
            account_case_id=case_id or None,
            job_id=str(job.get("job_id") or "") or None,
            attempts=attempt_count,
            detail=detail,
            now=now,
        )
BILLING_REPLY_POLL_ENABLED_ENV = "BILLING_AUTOMATION_REPLY_POLL_ENABLED"
BILLING_REPLY_POLL_INTERVAL_ENV = "BILLING_AUTOMATION_REPLY_POLL_INTERVAL_SECONDS"
BILLING_REPLY_POLL_MAX_MESSAGES_ENV = "BILLING_AUTOMATION_REPLY_POLL_MAX_MESSAGES"
AUTOMATION_REPLY_POLL_ENABLED_ENV = "AUTOMATION_REPLY_POLL_ENABLED"
AUTOMATION_REPLY_POLL_INTERVAL_ENV = "AUTOMATION_REPLY_POLL_INTERVAL_SECONDS"
AUTOMATION_REPLY_POLL_MAX_MESSAGES_ENV = "AUTOMATION_REPLY_POLL_MAX_MESSAGES"
AUTOMATION_REPLY_CLAIM_LEASE_SECONDS = 15 * 60
ENGINEER_ASSIGNMENT_POLLER_ENABLED_ENV = "ENGINEER_ASSIGNMENT_POLLER_ENABLED"
ENGINEER_ASSIGNMENT_POLL_INTERVAL_ENV = "ENGINEER_ASSIGNMENT_POLL_INTERVAL_SECONDS"
ACCOUNT_REPLY_POLL_INTERVAL_ENV = "ACCOUNT_REPLY_POLL_INTERVAL_SECONDS"
ACCOUNT_REPLY_POLLER_ENABLED_ENV = "ACCOUNT_REPLY_POLLER_ENABLED"
ACCOUNT_REPLY_LEGACY_POLLER_ENABLED_ENV = "ACCOUNT_REPLY_LEGACY_POLLER_ENABLED"
ENABLEMENT_DELIVERY_RETRY_POLL_INTERVAL_ENV = "ENABLEMENT_DELIVERY_RETRY_POLL_INTERVAL_SECONDS"
BILLING_REPLY_SUBJECT_TICKET_RE = re.compile(
    r"\bTicket\s+((?:TK-[A-Z0-9-]+)|(?:[0-9]+))\b",
    re.IGNORECASE,
)


def _safe_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _safe_positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _safe_non_negative_int(value: Any, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


TICKET_REPOSITORY_RETRY_MAX = _safe_non_negative_int(
    os.getenv("TICKET_WORKER_REPOSITORY_RETRY_MAX"),
    2,
)
TICKET_REPOSITORY_RETRY_BASE_DELAY_SECONDS = _safe_positive_float(
    os.getenv("TICKET_WORKER_REPOSITORY_RETRY_BASE_DELAY_SECONDS"),
    0.25,
)
TICKET_TASK_RETRY_MAX = _safe_non_negative_int(
    os.getenv("TICKET_WORKER_TASK_RETRY_MAX"),
    3,
)
TICKET_TASK_RETRY_BASE_DELAY_SECONDS = _safe_positive_float(
    os.getenv("TICKET_WORKER_TASK_RETRY_BASE_DELAY_SECONDS"),
    1.0,
)
OPTIMISTIC_ROUTE_TIMEOUT_SECONDS = _safe_positive_float(
    os.getenv("OPTIMISTIC_ROUTE_TIMEOUT_SECONDS"),
    8.0,
)
rag_service_client = RagServiceClient()


def _worker_rag_timeout_seconds() -> float:
    return _safe_positive_float(os.getenv("TICKET_WORKER_RAG_SERVICE_TIMEOUT_SECONDS"), 90.0)


def _worker_rag_max_wait_seconds() -> float:
    timeout_seconds = _worker_rag_timeout_seconds()
    default_max_wait = max(timeout_seconds, 300.0)
    return _safe_positive_float(os.getenv("TICKET_WORKER_RAG_MAX_WAIT_SECONDS"), default_max_wait)


def _worker_rag_recovery_window_seconds() -> float:
    default_window = max(0.0, _worker_rag_max_wait_seconds() - _worker_rag_timeout_seconds())
    raw = str(os.getenv("TICKET_WORKER_RAG_RECOVERY_WINDOW_SECONDS") or "").strip()
    if raw:
        return _safe_positive_float(raw, default_window)
    return default_window


def _worker_rag_recovery_poll_interval_seconds() -> float:
    return _safe_positive_float(os.getenv("TICKET_WORKER_RAG_RECOVERY_POLL_INTERVAL_SECONDS"), 1.0)


def _worker_task_types_from_env() -> tuple[str, ...]:
    raw = str(os.getenv("WORKER_TASK_TYPES") or "").strip().lower()
    if not raw or raw == "all":
        return SUPPORTED_WORKER_TASK_TYPES
    normalized: list[str] = []
    for token in raw.split(","):
        value = str(token or "").strip().lower()
        if value in SUPPORTED_WORKER_TASK_TYPES and value not in normalized:
            normalized.append(value)
    return tuple(normalized) if normalized else SUPPORTED_WORKER_TASK_TYPES


def _worker_concurrency_from_env() -> int:
    return _safe_positive_int(os.getenv("WORKER_CONCURRENCY"), 1)


def _billing_reply_poller_enabled_from_env() -> bool:
    value = os.getenv(AUTOMATION_REPLY_POLL_ENABLED_ENV)
    if value is None or not str(value).strip():
        value = os.getenv(BILLING_REPLY_POLL_ENABLED_ENV)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _billing_reply_poll_interval_from_env() -> float:
    return _safe_positive_float(
        os.getenv(AUTOMATION_REPLY_POLL_INTERVAL_ENV) or os.getenv(BILLING_REPLY_POLL_INTERVAL_ENV),
        300.0,
    )


def _billing_reply_poll_max_messages_from_env() -> int:
    return _safe_positive_int(
        os.getenv(AUTOMATION_REPLY_POLL_MAX_MESSAGES_ENV) or os.getenv(BILLING_REPLY_POLL_MAX_MESSAGES_ENV),
        25,
    )


def _engineer_assignment_poller_enabled_from_env() -> bool:
    return str(os.getenv(ENGINEER_ASSIGNMENT_POLLER_ENABLED_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _engineer_assignment_poll_interval_from_env() -> float:
    return _safe_positive_float(os.getenv(ENGINEER_ASSIGNMENT_POLL_INTERVAL_ENV), 60.0)


def _account_reply_poll_interval_from_env() -> float:
    return _safe_positive_float(os.getenv(ACCOUNT_REPLY_POLL_INTERVAL_ENV), 2.0)


def _account_reply_poller_enabled_from_env() -> bool:
    return str(os.getenv(ACCOUNT_REPLY_POLLER_ENABLED_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _account_reply_legacy_poller_enabled_from_env() -> bool:
    return str(os.getenv(ACCOUNT_REPLY_LEGACY_POLLER_ENABLED_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _install_signal_handlers() -> None:
    def _handle_signal(signum: int, _frame: Any) -> None:
        global SHUTTING_DOWN
        SHUTTING_DOWN = True
        LOGGER.info("Worker received signal %s, shutting down...", signum)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)


def _run_billing_reply_poller(interval_seconds: float) -> None:
    LOGGER.info("Automation reply poller started with interval_seconds=%s.", interval_seconds)
    while not SHUTTING_DOWN:
        try:
            process_automation_request_replies_once()
        except Exception as exc:
            LOGGER.warning("Automation reply poller failed: %s", exc)
        sleep_until = time.time() + max(interval_seconds, 1.0)
        while not SHUTTING_DOWN and time.time() < sleep_until:
            time.sleep(min(1.0, max(0.0, sleep_until - time.time())))
    LOGGER.info("Automation reply poller stopped.")


def process_automation_request_replies_once() -> list[Any]:
    """Consume one bounded Billing, Enablement, and Quota Outlook reply batch."""
    replies = poll_automation_request_replies(
        handler=handle_automation_request_reply,
        max_messages=_billing_reply_poll_max_messages_from_env(),
        subject_prefixes=(
            namespaced_internal_email_subject(BILLING_INTERNAL_EMAIL_SUBJECT_PREFIX),
            namespaced_internal_email_subject(ENABLEMENT_INTERNAL_EMAIL_SUBJECT_PREFIX),
            namespaced_internal_email_subject(QUOTA_INTERNAL_EMAIL_SUBJECT_PREFIX),
        ),
    )
    if replies:
        LOGGER.info("Automation reply poller handled %s reply message(s).", len(replies))
    return replies


def _run_engineer_assignment_poller(interval_seconds: float) -> None:
    LOGGER.info("Engineer assignment poller started with interval_seconds=%s.", interval_seconds)
    service = EngineerAssignmentService(ticket_repository)
    while not SHUTTING_DOWN:
        try:
            resolved = service.resolve_closed_cases()
            off_schedule_reassigned = service.reassign_off_schedule_cases()
            sla_reassigned = service.reassign_due_cases()
            dispatched = service.dispatch_pending_cases()
            if resolved or off_schedule_reassigned or sla_reassigned or dispatched:
                LOGGER.info(
                    "Engineer assignment poller handled resolved=%s off_schedule_reassigned=%s "
                    "sla_reassigned=%s dispatched=%s.",
                    len(resolved),
                    len(off_schedule_reassigned),
                    len(sla_reassigned),
                    len(dispatched),
                )
        except Exception:
            LOGGER.exception("Engineer assignment poller failed")
        sleep_until = time.time() + max(interval_seconds, 1.0)
        while not SHUTTING_DOWN and time.time() < sleep_until:
            time.sleep(min(1.0, max(0.0, sleep_until - time.time())))
    LOGGER.info("Engineer assignment poller stopped.")


def _account_customer_message_timestamps(ticket: dict[str, Any]) -> list[str]:
    return [
        str(message.get("created_at") or "")
        for message in ticket.get("messages", [])
        if isinstance(message, dict)
        and str(message.get("role") or "").strip().lower() in {"customer", "user"}
        and str(message.get("created_at") or "").strip()
    ]


def _account_reply_trigger_is_latest(ticket: dict[str, Any], trigger_created_at: str) -> bool:
    customer_timestamps = _account_customer_message_timestamps(ticket)
    return bool(customer_timestamps) and max(customer_timestamps) == str(trigger_created_at)


def _account_reply_message_job_id(message: dict[str, Any]) -> str:
    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
    return str(meta.get("account_reply_job_id") or message.get("account_reply_job_id") or "").strip()


def _account_reply_claim_is_current(
    claimed_job: dict[str, Any],
    current_job: dict[str, Any] | None,
    *,
    expected_status: str,
) -> bool:
    if not isinstance(current_job, dict):
        return False
    return (
        str(current_job.get("job_id") or "")
        == str(claimed_job.get("job_id") or "")
        and str(current_job.get("ticket_id") or "")
        == str(claimed_job.get("ticket_id") or "")
        and str(current_job.get("trigger_message_created_at") or "")
        == str(claimed_job.get("trigger_message_created_at") or "")
        and str(current_job.get("status") or "") == expected_status
        and str(current_job.get("claimed_at") or "")
        == str(claimed_job.get("claimed_at") or "")
        and int(current_job.get("attempt_count") or 0)
        == int(claimed_job.get("attempt_count") or 0)
    )


def _update_claimed_account_reply_job(
    job: dict[str, Any],
    *,
    expected_status: str,
) -> bool:
    return ticket_repository.update_claimed_account_reply_job(
        job,
        expected_status=expected_status,
        expected_claimed_at=job.get("claimed_at"),
        expected_attempt_count=int(job.get("attempt_count") or 0),
    ) is not None


def _cancel_stale_account_reply_job(
    job: dict[str, Any],
    *,
    expected_status: str,
) -> None:
    payload = dict(job.get("payload") or {})
    payload["cancel_reason"] = "stale_customer_revision"
    job["payload"] = payload
    job["status"] = "cancelled"
    job["updated_at"] = now_iso()
    _update_claimed_account_reply_job(job, expected_status=expected_status)


def _resolve_account_persona_for_claimed_reply(
    job: dict[str, Any],
    *,
    expected_status: str,
) -> dict[str, Any] | None:
    return ticket_repository.resolve_account_persona_for_claimed_reply(
        job,
        expected_status=expected_status,
        expected_claimed_at=job.get("claimed_at"),
        expected_attempt_count=int(job.get("attempt_count") or 0),
    )


def _prepare_account_reply_job(job: dict[str, Any]) -> None:
    ticket_id = str(job.get("ticket_id") or "").strip()
    usage_capture, usage_token = begin_case_usage_capture(client_ticket_id=ticket_id or None)
    try:
        _prepare_account_reply_job_impl(job)
    finally:
        end_case_usage_capture(usage_token)
        if usage_capture.entries:
            billing_ticket = (
                ticket_repository.get_billing_ticket_by_client_ticket_id(ticket_id)
                if ticket_id
                else None
            )
            usage_capture.bind_case(
                billing_ticket_id=str(
                    (billing_ticket or {}).get("billing_ticket_id") or ""
                ).strip()
                or None,
                client_ticket_id=ticket_id or None,
            )
            flush_case_usage_capture(ticket_repository, usage_capture)


def _prepare_account_reply_job_impl(job: dict[str, Any]) -> None:
    job_id = str(job.get("job_id") or "").strip()
    ticket_id = str(job.get("ticket_id") or "").strip()
    claimed_status = str(job.get("status") or "")
    if not _account_reply_claim_is_current(
        job,
        ticket_repository.get_account_reply_job(job_id),
        expected_status=claimed_status,
    ):
        return
    ticket = ticket_repository.get_ticket(ticket_id)
    if not job_id or ticket is None:
        raise RuntimeError("account reply job is missing its linked ticket")
    payload = dict(job.get("payload") or {})
    if (
        (isinstance(payload.get("reply_facts"), dict) and payload.get("reply_facts"))
        or payload.get("reply_intent")
        or payload.get("close_after_publish")
    ):
        try:
            payload, _, _ = _normalize_account_reply_job_payload(payload)
        except AutomationPersonaError as exc:
            _move_invalid_account_reply_to_human_review(job, ticket, exc)
            return
        payload = _with_enablement_completion_acknowledgement(payload, ticket)
    if isinstance(payload.get("reply_facts"), dict) and payload.get("reply_facts"):
        if payload.get("reply_pipeline") not in {
            None,
            ACCOUNT_REPLY_PERSONA_PIPELINE,
            ACCOUNT_REPLY_PERSONA_LEGACY_PIPELINE,
        }:
            raise RuntimeError("unsupported Account reply pipeline")
        payload["reply_pipeline"] = account_reply_persona_pipeline_for_job(job, payload)
        if not payload.get("persona_key") or not payload.get("effective_prompt"):
            try:
                persona = _resolve_account_persona_for_claimed_reply(
                    job,
                    expected_status=claimed_status,
                )
            except AccountPersonaUnavailableError as exc:
                _move_automation_reply_to_human_review(
                    job,
                    ticket,
                    str(exc),
                    policy_decision="account_persona_unavailable_human_review",
                )
                return
            if persona is None:
                return
            if not isinstance(persona, dict):
                _move_automation_reply_to_human_review(
                    job,
                    ticket,
                    "automation_persona_missing_assignment",
                )
                return
            payload.update(
                {
                    "persona_key": persona.get("persona_key"),
                    "persona_version": persona.get("version"),
                    "effective_prompt": normalize_account_persona_content(
                        dict(persona.get("content") or {}),
                        allow_legacy_fields=True,
                    ),
                }
            )
        if _account_reply_needs_persona_render(payload):
            payload.pop("generated_content", None)
            payload.pop("persona_render_status", None)
            payload.pop("persona_model", None)
            payload.pop("persona_prompt_version", None)
            persona_assignment = {
                "persona_key": payload.get("persona_key"),
                "version": payload.get("persona_version"),
                "content": dict(payload.get("effective_prompt") or {}),
            }
            try:
                rendered = render_automation_reply(
                    reply_facts=dict(payload["reply_facts"]),
                    persona_assignment=persona_assignment,
                    account_scope=True,
                )
            except AutomationPersonaError as exc:
                transitioned = _move_automation_reply_to_human_review(
                    job,
                    ticket,
                    str(exc),
                    policy_decision="automation_persona_human_review",
                    failure_stage="automation_persona",
                    failure_code=exc.code,
                )
                if transitioned:
                    _record_account_worker_failure(job=job, ticket=ticket, failure=exc)
                return
            payload.update(
                {
                    "generated_content": rendered.content,
                    "persona_render_status": "generated",
                    "persona_model": rendered.model,
                    "persona_prompt_version": rendered.prompt_version,
                }
            )
        job["payload"] = payload
        job["status"] = (
            account_reply_persona_status_for_stage(job, "scheduled")
            if payload.get("reply_pipeline")
            else "scheduled"
        )
        job["updated_at"] = now_iso()
        _update_claimed_account_reply_job(job, expected_status=claimed_status)
        return
    job_payload_gate = dict(job.get("payload") or {})
    if not job_payload_gate.get("internal_resolution") and not _account_reply_trigger_is_latest(
        ticket, str(job.get("trigger_message_created_at") or "")
    ):
        _cancel_stale_account_reply_job(job, expected_status=claimed_status)
        return

    trigger_message = next(
        (
            message
            for message in reversed(ticket.get("messages", []))
            if isinstance(message, dict)
            and str(message.get("role") or "").strip().lower() in {"customer", "user"}
            and str(message.get("created_at") or "") == str(job.get("trigger_message_created_at") or "")
        ),
        None,
    )
    if trigger_message is None:
        raise RuntimeError("account reply trigger message was not found")

    context = [
        {"role": str(message.get("role") or "system"), "content": str(message.get("content") or "")}
        for message in ticket.get("messages", [])
        if isinstance(message, dict) and str(message.get("content") or "").strip()
    ]
    latest_assistant = next(
        (
            message for message in reversed(ticket.get("messages", []))
            if isinstance(message, dict) and str(message.get("role") or "").strip().lower() == "assistant"
        ),
        None,
    )
    resolution = resolve_support_message(
        str(trigger_message.get("content") or ""),
        ticket_id=ticket_id,
        customer_id=str(ticket.get("customer_id") or "") or None,
        ticket_subject=str(ticket.get("subject") or "") or None,
        ticket_context=context,
        product=str(ticket.get("product") or "") or None,
        latest_assistant_message=latest_assistant,
        current_ticket_status=str(ticket.get("status") or "") or None,
        has_active_engineer_case=bool(ticket_repository.get_active_engineer_case(ticket_id)),
    )
    draft = str(resolution.answer or "").strip()
    if not draft:
        if str(resolution.route_family or "").strip() == "human_review":
            evidence = (
                resolution.evidence_summary
                if isinstance(resolution.evidence_summary, dict)
                else {}
            )
            _move_automation_reply_to_human_review(
                job,
                ticket,
                str(resolution.route_reason or "automation_persona_human_review").strip(),
                policy_decision=(
                    "account_persona_unavailable_human_review"
                    if evidence.get("account_persona_unavailable") is True
                    else "automation_persona_human_review"
                ),
            )
            return
        failure = AutomationPersonaError(
            "account_reply_preparation_failed",
            "AI could not prepare a reliable account-only reply.",
        )
        transitioned = _move_automation_reply_to_human_review(
            job,
            ticket,
            str(resolution.route_reason or failure),
            policy_decision="account_processing_failure_human_review",
            failure_stage="reply_prepare",
            failure_code=failure.code,
        )
        if transitioned:
            _record_account_worker_failure(job=job, ticket=ticket, failure=failure)
    else:
        try:
            persona = _resolve_account_persona_for_claimed_reply(
                job,
                expected_status=claimed_status,
            )
        except AccountPersonaUnavailableError as exc:
            _move_automation_reply_to_human_review(
                job,
                ticket,
                str(exc),
                policy_decision="account_persona_unavailable_human_review",
            )
            return
        if persona is None:
            return
        rendered_by_automation_persona = bool(
            isinstance(resolution.evidence_summary, dict)
            and resolution.evidence_summary.get("automation_persona_render_status") == "generated"
        )
        payload.update(
            {
                "draft_content": draft if rendered_by_automation_persona else apply_persona_to_customer_reply(draft, persona),
                "persona_key": persona.get("persona_key"),
                "persona_version": persona.get("version"),
                "effective_prompt": dict(persona.get("content") or {}),
                "answer_route": resolution.answer_route,
                "route_reason": resolution.route_reason,
            }
        )
        job["status"] = "scheduled"
    current_job = ticket_repository.get_account_reply_job(job_id)
    if not _account_reply_claim_is_current(
        job,
        current_job,
        expected_status=claimed_status,
    ):
        return
    job["payload"] = payload
    job["updated_at"] = now_iso()
    _update_claimed_account_reply_job(job, expected_status=claimed_status)


def _publish_account_reply_job(job: dict[str, Any]) -> None:
    job_id = str(job.get("job_id") or "").strip()
    ticket_id = str(job.get("ticket_id") or "").strip()
    claimed_status = str(job.get("status") or "")
    current_job = ticket_repository.get_account_reply_job(job_id)
    ticket = ticket_repository.get_ticket(ticket_id)
    if current_job is None or ticket is None:
        raise RuntimeError("account reply job is missing its linked ticket")
    if not is_account_reply_persona_publishing_status(claimed_status) or not _account_reply_claim_is_current(
        job,
        current_job,
        expected_status=claimed_status,
    ):
        return
    payload = dict(current_job.get("payload") or {})
    existing_message = next(
        (
            message
            for message in ticket.get("messages", [])
            if isinstance(message, dict)
            and _account_reply_message_job_id(message) == job_id
        ),
        None,
    )
    if (
        existing_message is None
        and not dict(payload).get("internal_resolution")
        and not _account_reply_trigger_is_latest(
            ticket, str(job.get("trigger_message_created_at") or "")
        )
    ):
        _cancel_stale_account_reply_job(current_job, expected_status=claimed_status)
        return

    rag_facts = payload.get("reply_facts") if isinstance(payload.get("reply_facts"), dict) else {}
    legacy_rag_draft = (
        str(payload.get("reply_intent") or "").strip() == ACCOUNT_REPLY_INTENT_RAG_FALLBACK_ANSWER
        and not str(rag_facts.get("provided_answer") or "").strip()
    )
    if (
        existing_message is None
        # Legacy draft-only rag_fallback jobs still publish verbatim:
        # normalization would attach synthetic intent-only facts and push them
        # into the persona render, which rejects them for missing facts.
        and not legacy_rag_draft
        and (
            rag_facts
            or payload.get("reply_intent")
            or payload.get("close_after_publish")
        )
    ):
        try:
            payload, _, _ = _normalize_account_reply_job_payload(payload)
        except AutomationPersonaError as exc:
            _move_invalid_account_reply_to_human_review(current_job, ticket, exc)
            return
        payload = _with_enablement_completion_acknowledgement(payload, ticket)

    if (
        existing_message is None
        and isinstance(payload.get("reply_facts"), dict)
        and payload.get("reply_facts")
        and _account_reply_needs_persona_render(payload)
    ):
        payload.pop("generated_content", None)
        payload.pop("persona_render_status", None)
        payload.pop("persona_model", None)
        payload.pop("persona_prompt_version", None)
        persona_assignment = {
            "persona_key": payload.get("persona_key"),
            "version": payload.get("persona_version"),
            "content": dict(payload.get("effective_prompt") or {}),
        }
        try:
            rendered = render_automation_reply(
                reply_facts=dict(payload["reply_facts"]),
                persona_assignment=persona_assignment,
                account_scope=True,
            )
        except AutomationPersonaError as exc:
            transitioned = _move_automation_reply_to_human_review(
                current_job,
                ticket,
                str(exc),
                policy_decision="automation_persona_human_review",
                failure_stage="automation_persona",
                failure_code=exc.code,
            )
            if transitioned:
                _record_account_worker_failure(job=current_job, ticket=ticket, failure=exc)
            return
        payload["generated_content"] = rendered.content
        payload["persona_render_status"] = "generated"
        payload["persona_model"] = rendered.model
        payload["persona_prompt_version"] = rendered.prompt_version
        current_job["payload"] = payload
        if not _update_claimed_account_reply_job(
            current_job,
            expected_status=claimed_status,
        ):
            return

    content = str(
        (existing_message or {}).get("content")
        or payload.get("generated_content")
        or payload.get("draft_content")
        or ""
    ).strip()
    if not content:
        if existing_message is None:
            _move_invalid_account_reply_to_human_review(
                current_job,
                ticket,
                AutomationPersonaError("automation_persona_empty_response"),
            )
        else:
            _move_automation_reply_to_human_review(
                current_job,
                ticket,
                "AI reply draft is unavailable.",
                policy_decision="account_processing_failure_human_review",
                failure_stage="reply_publish",
                failure_code="account_reply_publication_empty_draft",
            )
        return

    if existing_message is None:
        try:
            assert_no_trailing_automation_signature(content)
            if (
                isinstance(payload.get("reply_facts"), dict)
                and payload.get("reply_facts")
                and _account_reply_contract_required(payload)
            ):
                normalized_facts, derived_close = validate_account_reply_contract(
                    content,
                    dict(payload["reply_facts"]),
                    top_level_reply_intent=str(payload.get("reply_intent") or "").strip() or None,
                    close_after_publish=bool(payload.get("close_after_publish")),
                )
                payload["reply_facts"] = normalized_facts
                if derived_close:
                    payload["close_after_publish"] = True
                else:
                    payload.pop("close_after_publish", None)
        except (AutomationPersonaError, AccountReplyContractError) as exc:
            _move_invalid_account_reply_to_human_review(current_job, ticket, exc)
            return
        if not content:
            _move_invalid_account_reply_to_human_review(
                current_job,
                ticket,
                AutomationPersonaError("automation_persona_empty_response"),
            )
            return
        # RAGFlow fallback replies append the reference links deterministically
        # after the persona body so the links survive the persona rendering.
        rag_references = []
        if isinstance(payload.get("reply_facts"), dict):
            rag_references = [
                str(item).strip()
                for item in (payload["reply_facts"].get("references") or [])
                if str(item).strip()
            ]
        if rag_references:
            content = f"{content}\n{format_rag_fallback_references(rag_references)}"

    published_at = str((existing_message or {}).get("created_at") or now_iso())
    billing_ticket = ticket_repository.get_billing_ticket_by_client_ticket_id(ticket_id)
    reply_execution = {
        "execution_id": f"reply-{job_id}",
        "ticket_id": ticket_id,
        "reply_kind": str((billing_ticket or {}).get("route") or payload.get("answer_route") or "account_reply"),
        "persona_key": payload.get("persona_key"),
        "persona_version": payload.get("persona_version"),
        "effective_prompt": dict(payload.get("effective_prompt") or {}),
        "visibility": "account_only",
        "scheduled_for": current_job.get("scheduled_for"),
        "published_at": published_at,
        "created_at": published_at,
    }
    published_reply = ticket_repository.publish_account_reply(
        current_job,
        content=content,
        payload=payload,
        published_at=published_at,
        reply_execution=reply_execution,
    )
    if not str(published_reply.get("content") or "").strip():
        return
    message_id = str(published_reply.get("message_id") or "").strip() or job_id
    _deliver_production_account_reply_to_zendesk(
        ticket_id=ticket_id,
        message_id=message_id,
        job_id=job_id,
        reply_intent=str(payload.get("reply_intent") or "").strip() or None,
    )


def _deliver_production_account_reply_to_zendesk(
    *,
    ticket_id: str,
    message_id: str | None = None,
    job_id: str | None = None,
    reply_intent: str | None = None,
) -> None:
    """Write one published production reply to Zendesk with its ledger visibility."""
    effective_message_id = str(message_id or job_id or "").strip()
    effective_job_id = str(job_id or "").strip() or effective_message_id
    account_case = ticket_repository.get_account_case_by_ticket_id(ticket_id)
    if not isinstance(account_case, dict):
        return
    if str(account_case.get("processing_profile") or "staging").strip().lower() != "production":
        return
    # RAG fallback answers reply to an unexpected customer message after the
    # case was re-routed away from its automation handler, so the case no
    # longer carries a registered route; the answer itself must still be
    # delivered.
    if (
        str(reply_intent or "").strip() != ACCOUNT_REPLY_INTENT_RAG_FALLBACK_ANSWER
        and not is_registered_automation(
            route_family=str(account_case.get("route_family") or ""),
            execution_action=str(account_case.get("execution_action") or account_case.get("route") or ""),
        )
    ):
        LOGGER.info(
            "production_zendesk_delivery_skipped job_id=%s ticket_id=%s account_case_id=%s "
            "message_id=%s delivery_status=skipped failure_code=unregistered_automation",
            effective_job_id,
            ticket_id,
            account_case.get("account_case_id") or account_case.get("billing_ticket_id") or "unknown",
            effective_message_id or "unknown",
        )
        return
    account_case_id = str(
        account_case.get("account_case_id") or account_case.get("billing_ticket_id") or ""
    ).strip()
    zendesk_ticket_id = str(account_case.get("zendesk_ticket_id") or "").strip()
    if not account_case_id or not zendesk_ticket_id:
        LOGGER.error(
            "production_zendesk_delivery_skipped job_id=%s ticket_id=%s account_case_id=%s "
            "message_id=%s delivery_status=skipped failure_code=missing_external_ticket",
            effective_job_id,
            ticket_id,
            account_case_id or "unknown",
            effective_message_id or "unknown",
        )
        return
    if not effective_message_id:
        LOGGER.error(
            "production_zendesk_delivery_skipped job_id=%s ticket_id=%s account_case_id=%s "
            "message_id=unknown delivery_status=skipped failure_code=missing_message_id",
            effective_job_id or "unknown",
            ticket_id,
            account_case_id,
        )
        return

    claim = ticket_repository.claim_account_zendesk_comment_delivery(
        account_case_id=account_case_id,
        message_id=effective_message_id,
        claimed_at=now_iso(),
    )
    delivery_status = str(claim.get("status") or "").strip().lower()
    delivery_is_public = bool(claim.get("is_public"))
    delivery_solve = (
        str(claim.get("target_status") or "").strip().lower() == "solved"
    )
    if not bool(claim.get("claimed")):
        if delivery_status in {"pending", "outcome_unknown"}:
            _reconcile_production_zendesk_delivery(
                account_case_id=account_case_id,
                message_id=effective_message_id,
                public_comment=delivery_is_public,
                solve_ticket=delivery_solve,
            )
        elif delivery_status == "missing":
            LOGGER.error(
                "production_zendesk_delivery_skipped job_id=%s ticket_id=%s account_case_id=%s "
                "message_id=%s delivery_status=missing failure_code=delivery_ledger_missing",
                effective_job_id,
                ticket_id,
                account_case_id,
                effective_message_id,
            )
        return

    # Read-only ownership confirmation right before the public write: a ticket a
    # human took over must not receive further automated replies.
    ownership = ensure_production_automation_ownership(
        account_case,
        mode="verify",
        updated_at=now_iso(),
    )
    if ownership.failure_category == "policy":
        failure_code = ownership.failure_code or "zendesk_ownership_policy_blocked"
        ticket_repository.complete_account_zendesk_comment_delivery(
            account_case_id=account_case_id,
            message_id=effective_message_id,
            status="failed",
            zendesk_comment_id=None,
            failure_code=failure_code,
            completed_at=now_iso(),
        )
        LOGGER.warning(
            "production_zendesk_delivery_stopped job_id=%s ticket_id=%s account_case_id=%s "
            "message_id=%s delivery_status=failed failure_code=%s ownership_state=%s "
            "assignee_id=%s blocking_comment_id=%s",
            effective_job_id,
            ticket_id,
            account_case_id,
            effective_message_id,
            failure_code,
            ownership.state,
            ownership.assignee_id or "unknown",
            ownership.blocking_comment_id or "none",
        )
        return
    if not ownership.confirmed:
        # Transient ownership read failure: undo the claim so the next drain
        # retries instead of reconciling a comment that was never written.
        ticket_repository.requeue_account_zendesk_comment_delivery(
            account_case_id=account_case_id,
            message_id=effective_message_id,
            requeued_at=now_iso(),
        )
        LOGGER.warning(
            "production_zendesk_delivery_verify_failed job_id=%s ticket_id=%s account_case_id=%s "
            "message_id=%s failure_code=%s",
            effective_job_id,
            ticket_id,
            account_case_id,
            effective_message_id,
            ownership.failure_code or "unknown",
        )
        return

    try:
        result = deliver_account_ai_message_as_internal_comment(
            repository=ticket_repository,
            account_case_id=account_case_id,
            message_id=effective_message_id,
            actor_id=PRODUCTION_ACCOUNT_ZENDESK_ACTOR_ID,
            trigger="production_worker",
            retry_failed=False,
            public_comment=delivery_is_public,
            solve_ticket=delivery_solve,
            asset_repository=asset_repository,
            asset_storage=asset_storage,
        )
    except AccountZendeskInternalCommentError as exc:
        if exc.outcome_unknown:
            _reconcile_production_zendesk_delivery(
                account_case_id=account_case_id,
                message_id=effective_message_id,
                public_comment=delivery_is_public,
                solve_ticket=delivery_solve,
            )
        else:
            ticket_repository.complete_account_zendesk_comment_delivery(
                account_case_id=account_case_id,
                message_id=effective_message_id,
                status="failed",
                zendesk_comment_id=None,
                failure_code=exc.code,
                completed_at=now_iso(),
            )
        LOGGER.warning(
            "production_zendesk_delivery_failed job_id=%s ticket_id=%s account_case_id=%s "
            "message_id=%s delivery_status=%s failure_code=%s category=%s",
            effective_job_id,
            ticket_id,
            account_case_id,
            effective_message_id,
            "outcome_unknown" if exc.outcome_unknown else "failed",
            exc.code,
            "outcome_unknown" if exc.outcome_unknown else "permanent",
        )
    except Exception:
        ticket_repository.complete_account_zendesk_comment_delivery(
            account_case_id=account_case_id,
            message_id=effective_message_id,
            status="failed",
            zendesk_comment_id=None,
            failure_code="zendesk_delivery_unexpected_error",
            completed_at=now_iso(),
        )
        LOGGER.exception(
            "production_zendesk_delivery_failed job_id=%s ticket_id=%s account_case_id=%s "
            "message_id=%s delivery_status=failed failure_code=zendesk_delivery_unexpected_error",
            effective_job_id,
            ticket_id,
            account_case_id,
            effective_message_id,
        )
    else:
        if result.status == "in_progress":
            LOGGER.info(
                "production_zendesk_delivery_deferred job_id=%s ticket_id=%s account_case_id=%s "
                "message_id=%s delivery_status=in_progress failure_code=none",
                effective_job_id,
                ticket_id,
                account_case_id,
                effective_message_id,
            )
        elif result.status == "outcome_unknown":
            _reconcile_production_zendesk_delivery(
                account_case_id=account_case_id,
                message_id=effective_message_id,
                public_comment=delivery_is_public,
                solve_ticket=delivery_solve,
            )
        elif result.status == "failed":
            LOGGER.warning(
                "production_zendesk_delivery_failed job_id=%s ticket_id=%s account_case_id=%s "
                "message_id=%s delivery_status=failed failure_code=%s retryable=%s",
                effective_job_id,
                ticket_id,
                account_case_id,
                effective_message_id,
                result.error_code or "none",
                result.retryable,
            )
        else:
            LOGGER.info(
                "production_zendesk_delivery_completed job_id=%s ticket_id=%s account_case_id=%s "
                "message_id=%s delivery_status=%s failure_code=%s is_public=%s target_status=%s",
                effective_job_id,
                ticket_id,
                account_case_id,
                effective_message_id,
                result.status,
                result.error_code or "none",
                delivery_is_public,
                claim.get("target_status") or "none",
            )
            if delivery_is_public:
                _hand_off_review_after_public_reply(
                    account_case=account_case,
                    ticket_id=ticket_id,
                    job_id=effective_job_id,
                    message_id=effective_message_id,
                    reply_intent=reply_intent,
                )


FRAUD_REVIEW_HANDOFF_EVENT_TYPE = "zendesk_fraud_review_handoff"

# The final public reply intents that hand the ticket to the reviewer once
# published. Interim replies (missing-information asks) keep AI ownership.
_REVIEW_HANDOFF_FINAL_INTENTS_BY_ACTION = {
    "fraud_account": {ACCOUNT_REPLY_INTENT_FRAUD_HANDOFF_CONFIRMATION},
    "account_suspension": {ACCOUNT_REPLY_INTENT_SUSPENSION_HANDOFF_AND_CLOSE},
}

REVIEWER_NOTIFY_EMAIL_EVENT_TYPE = "zendesk_reviewer_notify_email"


def _notify_suspension_reviewer_by_email(
    account_case: dict[str, Any],
    *,
    ticket_id: str,
    zendesk_ticket_id: str,
    job_id: str,
) -> None:
    """Send the suspension reviewer notification email with a persisted state.

    Idempotent on the workflow's reviewer_notify_email state: a sent
    notification is never re-sent, and a failure records a failed state plus
    an owner-visible event without rolling back the reviewer assignment (the
    assignee can already see the ticket in Zendesk).
    """
    automation_context = (
        dict(account_case.get("automation_context"))
        if isinstance(account_case.get("automation_context"), dict)
        else {}
    )
    suspension_workflow = automation_context.get(SUSPENSION_CONTACT_WORKFLOW_KEY)
    suspension_workflow = dict(suspension_workflow) if isinstance(suspension_workflow, dict) else {}
    if str(suspension_workflow.get("reviewer_notify_email") or "").strip() == "sent":
        return

    def _record_notify_event(state: str, **fields: Any) -> None:
        try:
            ticket_repository.record_event(
                ticket_id or None,
                REVIEWER_NOTIFY_EMAIL_EVENT_TYPE,
                {
                    "zendesk_ticket_id": zendesk_ticket_id,
                    "job_id": job_id,
                    "state": state,
                    "created_at": now_iso(),
                    **fields,
                },
            )
        except Exception:
            LOGGER.exception(
                "reviewer_notify_email_event_failed job_id=%s ticket_id=%s",
                job_id,
                ticket_id,
            )

    try:
        recipients = resolve_account_internal_email_recipients("account_suspension")
        payload = recipients.apply(
            {
                "subject": f"[Suspension Review Assigned] Zendesk ticket {zendesk_ticket_id}",
                "body": (
                    "Hello,\n\n"
                    f"Suspension ticket {zendesk_ticket_id} has been assigned to you for "
                    "review after the customer confirmed the contact email and received "
                    "the 24-hour handoff reply.\n\n"
                    f"Zendesk: {_zendesk_ticket_url(zendesk_ticket_id)}\n\n"
                    "This is an automated notification from the account automation pipeline."
                ),
            }
        )
        result = send_billing_internal_email(payload)
        status = str((result or {}).get("status") or "").strip()
        if status == "sent":
            suspension_workflow["reviewer_notify_email"] = "sent"
            suspension_workflow["reviewer_notify_failure_reason"] = None
            _record_notify_event(
                "sent",
                to=",".join(recipients.to),
                cc=",".join(recipients.cc),
            )
        else:
            suspension_workflow["reviewer_notify_email"] = "failed"
            suspension_workflow["reviewer_notify_failure_reason"] = status or "unknown"
            _record_notify_event(
                "failed",
                failure_code=status or "unknown",
                reason=str((result or {}).get("reason") or ""),
            )
    except Exception as exc:
        suspension_workflow["reviewer_notify_email"] = "failed"
        suspension_workflow["reviewer_notify_failure_reason"] = type(exc).__name__
        _record_notify_event("failed", failure_code=type(exc).__name__, reason=str(exc))
    suspension_workflow["reviewer_notify_updated_at"] = now_iso()
    automation_context[SUSPENSION_CONTACT_WORKFLOW_KEY] = suspension_workflow
    account_case["automation_context"] = automation_context


def _hand_off_review_after_public_reply(
    *,
    account_case: dict[str, Any],
    ticket_id: str,
    job_id: str | None = None,
    message_id: str | None = None,
    reply_intent: str | None = None,
) -> None:
    """After the final confirmation reply lands, hand the ticket to the reviewer.

    Fraud and Account Suspension share this handoff: once the final public
    reply is published, the ticket moves to the configured reviewer instead of
    keeping AI ownership. The reply is already published at this point, so a
    handoff failure is an owner-visible signal (event + log), never a delivery
    failure.
    """
    execution_action = str(
        account_case.get("execution_action") or account_case.get("route") or ""
    ).strip()
    final_intents = _REVIEW_HANDOFF_FINAL_INTENTS_BY_ACTION.get(execution_action)
    if final_intents is None:
        return
    if str(reply_intent or "").strip() not in final_intents:
        # Missing-information and other interim public replies keep AI ownership;
        # only the final 24h confirmation reply completes the handoff.
        LOGGER.info(
            "review_handoff_deferred job_id=%s ticket_id=%s message_id=%s "
            "failure_code=pending_final_confirmation reply_intent=%s",
            str(job_id or "").strip() or "unknown",
            ticket_id,
            str(message_id or "").strip() or "unknown",
            str(reply_intent or "").strip() or "none",
        )
        return
    effective_job_id = str(job_id or "").strip() or "unknown"
    effective_message_id = str(message_id or "").strip() or "unknown"
    account_case_id = str(
        account_case.get("account_case_id") or account_case.get("billing_ticket_id") or ""
    ).strip()
    zendesk_ticket_id = str(account_case.get("zendesk_ticket_id") or "").strip()
    reviewer_user_id = str(os.getenv(ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID_ENV) or "").strip()
    timestamp = now_iso()

    def _record_event(state: str, **fields: Any) -> None:
        ticket_repository.record_event(
            ticket_id or None,
            FRAUD_REVIEW_HANDOFF_EVENT_TYPE,
            {
                "account_case_id": account_case_id,
                "state": state,
                "created_at": timestamp,
                **fields,
            },
        )

    if not reviewer_user_id.isdigit() or not zendesk_ticket_id:
        _record_event(
            "skipped",
            failure_code=(
                "fraud_review_assignee_config_missing"
                if not reviewer_user_id.isdigit()
                else "fraud_review_zendesk_ticket_missing"
            ),
        )
        LOGGER.warning(
            "fraud_review_handoff_skipped job_id=%s ticket_id=%s account_case_id=%s "
            "message_id=%s failure_code=%s",
            effective_job_id,
            ticket_id,
            account_case_id or "unknown",
            effective_message_id,
            "fraud_review_assignee_config_missing" if not reviewer_user_id.isdigit() else "fraud_review_zendesk_ticket_missing",
        )
        return
    try:
        result = assign_ticket_to_reviewer(
            ticket_id=zendesk_ticket_id,
            reviewer_user_id=reviewer_user_id,
        )
    except ZendeskCommentError as exc:
        _record_event(
            "failed",
            failure_code=exc.error_code,
            failure_category=exc.category,
            zendesk_status_code=exc.status_code,
            failure_detail=getattr(exc, "detail", None),
        )
        LOGGER.warning(
            "fraud_review_handoff_failed job_id=%s ticket_id=%s account_case_id=%s "
            "message_id=%s failure_code=%s category=%s zendesk_status_code=%s failure_detail=%s",
            effective_job_id,
            ticket_id,
            account_case_id,
            effective_message_id,
            exc.error_code or "unknown",
            exc.category,
            exc.status_code or "none",
            getattr(exc, "detail", None) or "none",
        )
        return
    state = "already_assigned" if result.already_assigned else "assigned"
    _record_event(
        state,
        assignee_id=result.assignee_id,
        group_id=result.group_id,
        reviewer_email=result.assignee_email,
        case_automation_status="human_review_required",
    )
    # Expected handoff completion moves the lifecycle directly; it must not go
    # through the escalation path, which cancels pending jobs, rewrites
    # route_status, and routes the Zendesk ticket back to the queue.
    account_case["automation_status"] = "human_review_required"
    account_case["updated_at"] = timestamp
    if execution_action == "account_suspension":
        # Close out the suspension contact workflow so later customer replies
        # hit the terminal idempotency branch instead of reopening the loop.
        automation_context = (
            dict(account_case["automation_context"])
            if isinstance(account_case.get("automation_context"), dict)
            else {}
        )
        suspension_workflow = automation_context.get(SUSPENSION_CONTACT_WORKFLOW_KEY)
        if isinstance(suspension_workflow, dict):
            suspension_workflow = dict(suspension_workflow)
            suspension_workflow["state"] = SUSPENSION_STATE_CLOSED
            suspension_workflow["updated_at"] = timestamp
            automation_context[SUSPENSION_CONTACT_WORKFLOW_KEY] = suspension_workflow
            account_case["automation_context"] = automation_context
        # p2-140: after the assignee is in place (assigned or already
        # assigned), notify the reviewer by email with a persisted
        # send state; a send failure is an owner-visible event only.
        _notify_suspension_reviewer_by_email(
            account_case,
            ticket_id=ticket_id,
            zendesk_ticket_id=zendesk_ticket_id,
            job_id=effective_job_id,
        )
    mark_production_ownership_handed_to_reviewer(
        account_case,
        updated_at=timestamp,
        assignee_id=result.assignee_id,
        group_id=result.group_id,
    )
    ticket_repository.save_account_case(account_case)
    LOGGER.info(
        "fraud_review_handoff_%s job_id=%s ticket_id=%s account_case_id=%s "
        "message_id=%s assignee_id=%s group_id=%s",
        state,
        effective_job_id,
        ticket_id,
        account_case_id,
        effective_message_id,
        result.assignee_id,
        result.group_id,
    )


def _account_reply_message_for_delivery(
    ticket: dict[str, Any],
    *,
    message_id: str,
) -> dict[str, Any] | None:
    normalized_message_id = str(message_id or "").strip()
    messages = ticket.get("messages") if isinstance(ticket.get("messages"), list) else []
    ticket_id = str(ticket.get("ticket_id") or "").strip()
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        candidate_ids = {
            str(message.get("message_id") or "").strip(),
            str(message.get("id") or "").strip(),
            f"{ticket_id}:{index}",
            str(meta.get("account_reply_job_id") or "").strip(),
        }
        if normalized_message_id not in candidate_ids:
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            return None
        return message
    return None


def _drain_production_zendesk_comment_deliveries(*, limit: int = 20) -> None:
    """Recover persisted Account reply intents without creating new ones."""
    deliveries = ticket_repository.list_account_zendesk_comment_deliveries(
        statuses=("queued", "pending", "outcome_unknown"),
        limit=limit,
    )
    for delivery in deliveries:
        if str(delivery.get("source") or "account").strip().lower() == "engineer":
            _deliver_engineer_approved_zendesk_comment(delivery)
            continue
        account_case_id = str(delivery.get("account_case_id") or "").strip()
        message_id = str(delivery.get("message_id") or "").strip()
        delivery_status = str(delivery.get("status") or "").strip().lower()
        account_case = ticket_repository.get_account_case(account_case_id)
        ticket_id = str((account_case or {}).get("client_ticket_id") or "").strip()
        ticket = ticket_repository.get_ticket(ticket_id) if ticket_id else None
        message = (
            _account_reply_message_for_delivery(ticket, message_id=message_id)
            if isinstance(ticket, dict)
            else None
        )
        if not isinstance(message, dict):
            failure_code = "zendesk_delivery_message_missing"
            LOGGER.error(
                "production_zendesk_delivery_recovery_failed job_id=%s ticket_id=%s "
                "account_case_id=%s message_id=%s delivery_status=%s failure_code=%s",
                "unknown",
                ticket_id or "unknown",
                account_case_id or "unknown",
                message_id or "unknown",
                delivery_status or "unknown",
                failure_code,
            )
            if delivery_status == "queued":
                ticket_repository.complete_account_zendesk_comment_delivery(
                    account_case_id=account_case_id,
                    message_id=message_id,
                    status="failed",
                    zendesk_comment_id=None,
                    failure_code=failure_code,
                    completed_at=now_iso(),
                )
            continue
        meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        _deliver_production_account_reply_to_zendesk(
            ticket_id=ticket_id,
            message_id=message_id,
            job_id=str(meta.get("account_reply_job_id") or "").strip() or message_id,
            reply_intent=str(meta.get("reply_intent") or "").strip() or None,
        )


def _record_engineer_delivery_slack_event(
    delivery: dict[str, Any],
    *,
    event_type: str,
    message_text: str,
    failure_code: str | None = None,
) -> None:
    engineer_case_id = str(delivery.get("engineer_case_id") or "").strip()
    case_payload = ticket_repository.get_engineer_case(
        engineer_case_id,
        include_client_messages=False,
    )
    if not isinstance(case_payload, dict):
        return
    engineer_case = _engineer_case_record_from_payload(case_payload)
    event = build_engineer_case_thread_event(
        event_id=(
            f"engineer-zendesk:{engineer_case_id}:"
            f"{delivery.get('investigation_id')}:{delivery.get('draft_version')}:{event_type}"
        ),
        event_type=event_type,
        engineer_case_id=engineer_case_id,
        message_text=message_text,
        investigation_id=str(delivery.get("investigation_id") or "") or None,
        draft_version=int(delivery.get("draft_version") or 0) or None,
        failure_code=failure_code,
    )
    ticket_repository.save_engineer_case(engineer_case, new_messages=[], slack_events=[event])


def _complete_engineer_delivery_round(delivery: dict[str, Any], *, comment_id: str | None) -> None:
    engineer_case_id = str(delivery.get("engineer_case_id") or "").strip()
    case_payload = ticket_repository.get_engineer_case(engineer_case_id, include_client_messages=True)
    if not isinstance(case_payload, dict):
        return
    client_ticket_id = str((case_payload.get("client_ticket_ref") or {}).get("ticket_id") or "").strip()
    ticket = ticket_repository.get_ticket(client_ticket_id)
    if not isinstance(ticket, dict):
        return
    engineer_case = _engineer_case_record_from_payload(case_payload)
    case_context = build_engineer_case_context(ticket, engineer_case)
    active = case_context.get("active_investigation")
    if not isinstance(active, dict):
        return
    if str(active.get("id") or "").strip() != str(delivery.get("investigation_id") or "").strip():
        return
    timestamp = now_iso()
    sequence = len(active.get("messages") or []) + 1
    delivery_message = {
        "id": f"{active.get('id')}-m-{sequence}",
        "role": "system",
        "content": "Zendesk public comment delivered. This investigation round is complete.",
        "created_at": timestamp,
        "meta": {
            "source": "zendesk_delivery",
            "zendesk_comment_id": str(comment_id or "").strip() or None,
            "draft_version": int(delivery.get("draft_version") or 0),
        },
    }
    active.setdefault("messages", []).append(delivery_message)
    active["state"] = "active"
    active["draft_customer_reply"] = ""
    active["final_confirmation_requested_at"] = None
    active["updated_at"] = timestamp
    agent_state = dict(
        engineer_case.get("engineer_agent_state")
        if isinstance(engineer_case.get("engineer_agent_state"), dict)
        else {}
    )
    for key in (
        "active_guardrail_final",
        "guardrail_final_id",
        "guardrail_final_version",
        "guardrail_final_decision",
        "final_approval_required",
    ):
        agent_state.pop(key, None)
    agent_state.update(
        phase="communicating",
        round_state="delivered",
        round_number=int(agent_state.get("round_number") or 1) + 1,
        delivered_draft_version=int(delivery.get("draft_version") or 0),
        delivered_at=timestamp,
    )
    case_context["engineer_agent_state"] = agent_state
    engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
    engineer_case["status"] = "communicating"
    ticket["status"] = "communicating"
    ticket["updated_at"] = timestamp
    ticket.setdefault("messages", []).append(
        {
            "role": "assistant",
            "content": str(delivery.get("immutable_content") or "").strip(),
            "created_at": timestamp,
            "meta": {
                "source": "engineer_guidance",
                "zendesk_comment_id": str(comment_id or "").strip() or None,
                "engineer_case_id": engineer_case_id,
            },
        }
    )
    slack_event = build_engineer_case_thread_event(
        event_id=(
            f"engineer-zendesk:{engineer_case_id}:"
            f"{delivery.get('investigation_id')}:{delivery.get('draft_version')}:delivered"
        ),
        event_type="zendesk_publish_delivered",
        engineer_case_id=engineer_case_id,
        message_text="Zendesk public comment delivered. The Case remains active for follow-up.",
        investigation_id=str(delivery.get("investigation_id") or "") or None,
        draft_version=int(delivery.get("draft_version") or 0) or None,
    )
    ticket_repository.save_ticket(ticket, new_messages=[ticket["messages"][-1]])
    ticket_repository.save_engineer_case(
        engineer_case,
        new_messages=[delivery_message],
        slack_events=[slack_event],
    )


def _deliver_engineer_approved_zendesk_comment(delivery: dict[str, Any]) -> None:
    account_case_id = str(delivery.get("account_case_id") or "").strip()
    message_id = str(delivery.get("message_id") or "").strip()
    status = str(delivery.get("status") or "").strip().lower()
    content = str(delivery.get("immutable_content") or "").strip()
    zendesk_ticket_id = str(delivery.get("zendesk_ticket_id") or "").strip()
    if not all((account_case_id, message_id, content, zendesk_ticket_id)):
        return

    if status == "queued":
        sync_state = ticket_repository.get_account_case_comment_sync(
            str((ticket_repository.get_account_case(account_case_id) or {}).get("client_ticket_id") or "")
        )
        current_revision = str((sync_state or {}).get("comments_revision") or "").strip()
        if not current_revision:
            try:
                current_revision = str(
                    read_ticket_ownership_snapshot(
                        ticket_id=zendesk_ticket_id,
                    ).comments_revision
                    or ""
                ).strip()
            except ZendeskCommentError as exc:
                LOGGER.warning(
                    "engineer_zendesk_revision_verify_failed ticket_id=%s account_case_id=%s "
                    "message_id=%s failure_code=%s",
                    zendesk_ticket_id,
                    account_case_id,
                    message_id,
                    exc.error_code,
                )
                return
        if current_revision != str(delivery.get("comments_revision") or "").strip():
            ticket_repository.complete_account_zendesk_comment_delivery(
                account_case_id=account_case_id,
                message_id=message_id,
                status="failed",
                zendesk_comment_id=None,
                failure_code="stale_comments_revision",
                completed_at=now_iso(),
            )
            _record_engineer_delivery_slack_event(
                delivery,
                event_type="zendesk_publish_failed",
                message_text="Zendesk delivery canceled because a newer customer comment arrived.",
                failure_code="stale_comments_revision",
            )
            return
        claimed = ticket_repository.claim_account_zendesk_comment_delivery(
            account_case_id=account_case_id,
            message_id=message_id,
            claimed_at=now_iso(),
        )
        if not bool(claimed.get("claimed")):
            return
        try:
            result = add_ticket_comment(
                ticket_id=zendesk_ticket_id,
                body=content,
                public=True,
                solve=False,
            )
        except ZendeskCommentError as exc:
            next_status = "outcome_unknown" if exc.category == "outcome_unknown" else "failed"
            ticket_repository.complete_account_zendesk_comment_delivery(
                account_case_id=account_case_id,
                message_id=message_id,
                status=next_status,
                zendesk_comment_id=None,
                failure_code=exc.error_code,
                completed_at=now_iso(),
            )
            if next_status == "failed":
                _record_engineer_delivery_slack_event(
                    delivery,
                    event_type="zendesk_publish_failed",
                    message_text="Zendesk public comment delivery failed.",
                    failure_code=exc.error_code,
                )
            return
        ticket_repository.complete_account_zendesk_comment_delivery(
            account_case_id=account_case_id,
            message_id=message_id,
            status="delivered",
            zendesk_comment_id=result.comment_id,
            failure_code=None,
            completed_at=now_iso(),
        )
        _complete_engineer_delivery_round(delivery, comment_id=result.comment_id)
        return

    try:
        result, _solved_seen = read_ticket_comment_audit(
            ticket_id=zendesk_ticket_id,
            body=content,
            public=True,
        )
    except ZendeskCommentError:
        return
    if result is None:
        return
    ticket_repository.complete_account_zendesk_comment_delivery(
        account_case_id=account_case_id,
        message_id=message_id,
        status="delivered",
        zendesk_comment_id=result.comment_id,
        failure_code=None,
        completed_at=now_iso(),
    )
    _complete_engineer_delivery_round(delivery, comment_id=result.comment_id)


def _drain_account_slack_deliveries(*, limit: int = 20) -> None:
    if str(os.getenv("ACCOUNT_DEFAULT_PROCESSING_PROFILE") or "staging").strip().lower() != "production":
        return
    if not account_slack_n8n_configured():
        LOGGER.warning("account_slack_delivery_paused failure_code=account_slack_n8n_config_incomplete")
        return
    deliveries = ticket_repository.list_account_slack_deliveries(
        statuses=("queued", "pending", "outcome_unknown"),
        limit=limit,
    )
    for delivery in deliveries:
        event_id = str(delivery.get("event_id") or "").strip()
        status = str(delivery.get("status") or "").strip().lower()
        if not event_id:
            continue
        if status == "queued":
            claimed = ticket_repository.claim_account_slack_delivery(
                event_id=event_id,
                claimed_at=now_iso(),
            )
            if not isinstance(claimed, dict) or not claimed.get("claimed"):
                continue
            event = claimed.get("payload") if isinstance(claimed.get("payload"), dict) else {}
            try:
                result = post_account_slack_event(event)
            except AccountSlackN8nError as exc:
                ticket_repository.complete_account_slack_delivery(
                    event_id=event_id,
                    status="outcome_unknown" if exc.outcome_unknown else "failed",
                    failure_code=exc.code,
                    completed_at=now_iso(),
                )
                LOGGER.warning(
                    "account_slack_delivery_failed event_id=%s status=%s failure_code=%s",
                    event_id,
                    "outcome_unknown" if exc.outcome_unknown else "failed",
                    exc.code,
                )
                continue
        else:
            try:
                result = get_account_slack_event_status(event_id)
            except AccountSlackN8nError as exc:
                LOGGER.warning(
                    "account_slack_reconciliation_failed event_id=%s status=%s failure_code=%s",
                    event_id,
                    status,
                    exc.code,
                )
                continue

        remote_status = str(result.get("status") or "").strip().lower()
        if remote_status == "missing":
            ticket_repository.requeue_account_slack_delivery(
                event_id=event_id,
                requeued_at=now_iso(),
            )
            LOGGER.warning(
                "account_slack_delivery_requeued event_id=%s failure_code=remote_event_missing",
                event_id,
            )
            continue
        ticket_repository.complete_account_slack_delivery(
            event_id=event_id,
            status=remote_status,
            failure_code=result.get("failure_code"),
            completed_at=now_iso(),
        )
        LOGGER.info(
            "account_slack_delivery_recorded event_id=%s status=%s failure_code=%s",
            event_id,
            remote_status,
            result.get("failure_code") or "none",
        )


def _drain_engineer_slack_events(*, limit: int = 20) -> None:
    if str(os.getenv("ACCOUNT_DEFAULT_PROCESSING_PROFILE") or "staging").strip().lower() != "production":
        return
    if not engineer_slack_configured():
        LOGGER.warning("engineer_slack_delivery_paused failure_code=engineer_slack_config_incomplete")
        return
    events = ticket_repository.list_engineer_slack_events(
        statuses=("queued",),
        limit=limit,
    )
    for event_record in events:
        event_id = str(event_record.get("event_id") or "").strip()
        if not event_id:
            continue
        event = event_record.get("payload") if isinstance(event_record.get("payload"), dict) else {}
        event_type = str(event.get("event_type") or event_record.get("event_type") or "").strip()
        thread_ts: str | None = None
        if event_type != "engineer_case_opened":
            engineer_case_id = str(event_record.get("engineer_case_id") or "").strip()
            binding = ticket_repository.get_engineer_slack_thread_binding(
                engineer_case_id,
                active_only=False,
            )
            if not isinstance(binding, dict):
                LOGGER.info(
                    "engineer_slack_delivery_waiting event_id=%s failure_code=engineer_slack_thread_binding_missing",
                    event_id,
                )
                continue
            configured_channel = str(os.getenv("ENGINEER_SLACK_CHANNEL_ID") or "").strip()
            if str(binding.get("slack_channel_id") or "").strip() != configured_channel:
                LOGGER.warning(
                    "engineer_slack_delivery_waiting event_id=%s failure_code=engineer_slack_thread_channel_mismatch",
                    event_id,
                )
                continue
            thread_ts = str(binding.get("slack_thread_ts") or "").strip()
        claimed = ticket_repository.claim_engineer_slack_event(
            event_id=event_id,
            claimed_at=now_iso(),
        )
        if not isinstance(claimed, dict) or not claimed.get("claimed"):
            continue
        event = claimed.get("payload") if isinstance(claimed.get("payload"), dict) else {}
        try:
            result = post_engineer_slack_event(event, thread_ts=thread_ts)
        except EngineerSlackDeliveryError as exc:
            failed_status = "outcome_unknown" if exc.outcome_unknown else "failed"
            ticket_repository.complete_engineer_slack_event(
                event_id=event_id,
                status=failed_status,
                failure_code=exc.code,
                completed_at=now_iso(),
            )
            LOGGER.warning(
                "engineer_slack_delivery_failed event_id=%s status=%s failure_code=%s",
                event_id,
                failed_status,
                exc.code,
            )
            continue
        ticket_repository.complete_engineer_slack_event(
            event_id=event_id,
            status="delivered",
            failure_code=result.get("failure_code"),
            completed_at=now_iso(),
            slack_channel_id=result.get("slack_channel_id"),
            slack_message_ts=result.get("slack_message_ts"),
            slack_thread_ts=result.get("slack_thread_ts"),
        )
        LOGGER.info(
            "engineer_slack_delivery_recorded event_id=%s status=%s failure_code=%s",
            event_id,
            "delivered",
            result.get("failure_code") or "none",
        )


def _reconcile_production_zendesk_delivery(
    *,
    account_case_id: str,
    message_id: str,
    public_comment: bool = False,
    solve_ticket: bool = False,
) -> None:
    """Confirm a possibly successful write with a read-only audit query; never resend it."""
    try:
        result = reconcile_account_ai_message_internal_comment(
            repository=ticket_repository,
            account_case_id=account_case_id,
            message_id=message_id,
            actor_id=PRODUCTION_ACCOUNT_ZENDESK_ACTOR_ID,
            trigger="production_worker",
            public_comment=public_comment,
            solve_ticket=solve_ticket,
        )
    except AccountZendeskInternalCommentError as exc:
        ticket_repository.complete_account_zendesk_comment_delivery(
            account_case_id=account_case_id,
            message_id=message_id,
            status="outcome_unknown" if exc.outcome_unknown else "failed",
            zendesk_comment_id=None,
            failure_code=exc.code,
            completed_at=now_iso(),
        )
        return
    LOGGER.info(
        "production_zendesk_delivery_reconciled account_case_id=%s message_id=%s "
        "delivery_status=%s comment_id=%s failure_code=%s",
        account_case_id,
        message_id,
        result.status,
        result.comment_id or "none",
        result.error_code or "none",
    )


def _move_automation_reply_to_human_review(
    job: dict[str, Any],
    ticket: dict[str, Any],
    reason: str,
    *,
    policy_decision: str = "automation_persona_human_review",
    failure_stage: str | None = None,
    failure_code: str | None = None,
) -> bool:
    """Stop an Automation send when Persona generation is unavailable."""
    expected_status = str(job.get("status") or "")
    payload = dict(job.get("payload") or {})
    if failure_stage:
        payload["failure_stage"] = str(failure_stage).strip()
    if failure_code:
        payload["failure_code"] = str(failure_code).strip()
    job["payload"] = payload
    transitioned = ticket_repository.transition_claimed_account_reply_to_human_review(
        job,
        expected_status=expected_status,
        expected_claimed_at=job.get("claimed_at"),
        expected_attempt_count=int(job.get("attempt_count") or 0),
        reason=reason,
        policy_decision=policy_decision,
        transitioned_at=now_iso(),
    )
    if not isinstance(transitioned, dict):
        return False
    job.clear()
    job.update(transitioned)
    account_case = ticket_repository.get_account_case_by_ticket_id(
        str(job.get("ticket_id") or ticket.get("ticket_id") or "").strip()
    )
    if isinstance(account_case, dict):
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        escalate_account_case_to_human_review(
            account_case=account_case,
            ticket_id=str(job.get("ticket_id") or ticket.get("ticket_id") or "").strip(),
            handler=str(account_case.get("automation_handler") or account_case.get("execution_action") or "automation"),
            failure_stage=str(failure_stage or payload.get("failure_stage") or "reply_worker"),
            failure_code=str(failure_code or payload.get("failure_code") or "automation_human_review"),
            reason=reason,
            repository=ticket_repository,
            timestamp=now_iso(),
        )
    return True


def _mark_account_case_for_human_review(
    account_case: dict[str, Any],
    *,
    reason: str,
    timestamp: str,
    policy_decision: str,
) -> None:
    persona_unavailable = policy_decision == "account_persona_unavailable_human_review"
    reason_code = reconciliation_reason_code(
        handler=str(account_case.get("automation_handler") or account_case.get("execution_action") or "automation"),
        phase="persona" if persona_unavailable else "persona_render",
        detail="unavailable" if persona_unavailable else "failed",
    )
    account_case.update(
        reconcile_automation_execution_failure(
            account_case,
            reason_code=reason_code,
            context={
                "policy_decision": policy_decision,
                "failure_detail": reason,
            },
        )
    )
    account_case["policy_decision"] = policy_decision
    account_case["updated_at"] = timestamp
    ticket_id = str(account_case.get("client_ticket_id") or "").strip()
    if ticket_id:
        escalate_account_case_to_human_review(
            account_case=account_case,
            ticket_id=ticket_id,
            handler=str(account_case.get("automation_handler") or account_case.get("execution_action") or "automation"),
            failure_stage="persona" if persona_unavailable else "persona_render",
            failure_code=reason_code,
            reason=reason,
            repository=ticket_repository,
            timestamp=timestamp,
        )


def _render_case_persona_reply(
    *,
    ticket_id: str,
    case: dict[str, Any],
    behavior: str,
    reply_intent: str,
    known_information: dict[str, Any] | None = None,
    source_facts: list[str] | None = None,
    performed_actions: list[str] | None = None,
    next_step: str | None = None,
    resolution_status: str | None = None,
    save_case: Any,
    persist_failure: bool = True,
) -> str:
    try:
        persona = ticket_repository.resolve_account_persona(ticket_id)
    except AccountPersonaUnavailableError as exc:
        timestamp = now_iso()
        reason = str(exc)
        _mark_account_case_for_human_review(
            case,
            reason=reason,
            timestamp=timestamp,
            policy_decision="account_persona_unavailable_human_review",
        )
        if persist_failure:
            save_case(case)
        return ""
    known_information = {
        **dict(known_information or {}),
        "ticket_id": ticket_id,
        "account_case_id": str(case.get("account_case_id") or case.get("billing_ticket_id") or "").strip(),
        "customer_email": str(case.get("customer_email") or "").strip(),
    }
    extracted_resolution: dict[str, Any] | None = None
    if source_facts:
        try:
            extracted_resolution = extract_automation_resolution_facts(
                behavior=behavior,
                source_text="\n".join(source_facts),
                known_information=known_information,
            )
        except AutomationPersonaError as exc:
            timestamp = now_iso()
            reason = str(exc)
            _mark_account_case_for_human_review(
                case,
                reason=reason,
                timestamp=timestamp,
                policy_decision="automation_persona_human_review",
            )
            if persist_failure:
                save_case(case)
            return ""
        resolution_facts = extracted_resolution.get("customer_shareable_facts")
        resolution_facts = resolution_facts if isinstance(resolution_facts, list) else []
        known_information = {
            **dict(known_information or {}),
            "resolution_status": extracted_resolution.get("status"),
            "customer_action": extracted_resolution.get("customer_action"),
        }
        source_facts = [str(item) for item in resolution_facts if str(item).strip()]
        next_step = str(extracted_resolution.get("next_step") or next_step or "").strip() or None
    if reply_intent in {"submission_confirmation", "request_missing_information"}:
        facts = build_account_automation_reply_facts(
            handler=behavior,
            action=behavior,
            missing_fields=list(known_information.get("missing_fields") or []),
            collected_fields=known_information,
            submitted=reply_intent == "submission_confirmation",
            resolution_facts=source_facts,
            customer_name=str(case.get("customer_name") or ""),
        )
    else:
        facts = build_automation_reply_facts(
            behavior=behavior,
            reply_intent=reply_intent,
            known_information=known_information,
            source_facts=source_facts,
            performed_actions=performed_actions,
            next_step=next_step,
            resolution_status=(
                str(extracted_resolution.get("status") or "").strip()
                if extracted_resolution
                else resolution_status
            ),
            customer_name=str(case.get("customer_name") or ""),
        )
    try:
        return render_automation_reply(
            reply_facts=facts,
            persona_assignment=persona,
            account_scope=reply_intent in {
                "submission_confirmation",
                "request_missing_information",
                ACCOUNT_REPLY_INTENT_FRAUD_HANDOFF_CONFIRMATION,
                SUSPENSION_REPLY_INTENT_CONTACT_CONFIRMATION,
                ACCOUNT_REPLY_INTENT_SUSPENSION_HANDOFF_AND_CLOSE,
                ACCOUNT_REPLY_INTENT_ENABLEMENT_COMPLETED_AND_CLOSE,
            },
        ).content
    except AutomationPersonaError as exc:
        timestamp = now_iso()
        reason = str(exc)
        _mark_account_case_for_human_review(
            case,
            reason=reason,
            timestamp=timestamp,
            policy_decision="automation_persona_human_review",
        )
        if persist_failure:
            save_case(case)
        return ""


def _enablement_reply_explicitly_confirms_completion(note: str) -> bool:
    """Return the latest explicit current enablement state in the note."""
    positive = re.compile(r"\b(?:enabled|activated|provisioned|turned\s+on)\b", re.IGNORECASE)
    revoked = re.compile(
        r"\b(?:disabled|deactivated|deprovisioned|turned\s+off)\b"
        r"|\bno\s+longer\s+(?:enabled|activated|provisioned|turned\s+on)\b",
        re.IGNORECASE,
    )
    negative_or_future = re.compile(
        r"\b(?:not|never|cannot|can't|couldn't|unable\s+to|failed\s+to|will|would|may|might|"
        r"could|should|please|need\s+to|trying\s+to|plan\s+to|request(?:ed)?\s+to)\b"
        r"[^.!?\n]{0,60}\b(?:enable|enabled|activate|activated|provision|provisioned|turn(?:ed)?\s+on)\b",
        re.IGNORECASE,
    )
    completed = False
    for clause in re.split(r"(?<=[.!?])\s+|[;\n]+|\bbut\b", str(note or ""), flags=re.IGNORECASE):
        if revoked.search(clause):
            completed = False
            continue
        if not positive.search(clause):
            continue
        if "?" in clause or negative_or_future.search(clause):
            completed = False
            continue
        completed = True
    return completed


def _process_claimed_account_reply_jobs(
    *,
    from_status: str,
    to_status: str,
    due_only: bool,
    limit: int,
) -> None:
    retry_status = from_status
    if from_status == ACCOUNT_REPLY_PERSONA_V8_QUEUED:
        retry_status = ACCOUNT_REPLY_PERSONA_V8_QUEUED
    elif from_status == ACCOUNT_REPLY_PERSONA_QUEUED:
        retry_status = ACCOUNT_REPLY_PERSONA_QUEUED
    elif from_status == ACCOUNT_REPLY_PERSONA_V8_SCHEDULED:
        retry_status = ACCOUNT_REPLY_PERSONA_V8_SCHEDULED
    elif from_status == ACCOUNT_REPLY_PERSONA_SCHEDULED:
        retry_status = ACCOUNT_REPLY_PERSONA_SCHEDULED
    elif from_status == "queued":
        retry_status = "queued"
    elif from_status == "scheduled":
        retry_status = "scheduled"
    for job in ticket_repository.claim_account_reply_jobs(
        from_status=from_status,
        to_status=to_status,
        now_value=now_iso(),
        limit=limit,
        due_only=due_only,
    ):
        try:
            if is_account_reply_persona_preparing_status(to_status):
                _prepare_account_reply_job(job)
            else:
                _publish_account_reply_job(job)
        except Exception as exc:
            preparing = is_account_reply_persona_preparing_status(to_status)
            operation = "preparation" if preparing else "publication"
            failure_stage = "reply_prepare" if preparing else "reply_publish"
            failure_code = (
                str(exc.code)
                if isinstance(exc, AccountProcessingFailure)
                else (
                    "account_reply_preparation_failed"
                    if preparing
                    else "account_reply_publication_failed"
                )
            )
            LOGGER.exception("Account reply %s failed for %s", operation, job.get("job_id"))
            current = ticket_repository.get_account_reply_job(str(job.get("job_id") or ""))
            if not _account_reply_claim_is_current(
                job,
                current,
                expected_status=to_status,
            ):
                continue
            failed_job = current
            failed_job["status"] = retry_status if int(failed_job.get("attempt_count") or 0) < 4 else "failed"
            failed_job["payload"] = {
                **dict(failed_job.get("payload") or {}),
                "error": str(exc),
            }
            if failed_job["status"] == "failed":
                failed_job["payload"].update(
                    failure_stage=failure_stage,
                    failure_code=failure_code,
                )
            failed_job["updated_at"] = now_iso()
            _update_claimed_account_reply_job(
                failed_job,
                expected_status=to_status,
            )
            if failed_job.get("status") == "failed":
                _record_account_worker_failure(
                    job=failed_job,
                    ticket=ticket_repository.get_ticket(str(failed_job.get("ticket_id") or "")),
                    failure=exc,
                )


def _drain_production_automation_classification_emails(*, limit: int = 20) -> None:
    deliveries = ticket_repository.list_account_automation_classification_emails(
        statuses=("queued",), limit=limit
    )
    for delivery in deliveries:
        account_case_id = str(delivery.get("account_case_id") or "").strip()
        claimed = ticket_repository.claim_account_automation_classification_email(
            account_case_id=account_case_id,
            claimed_at=now_iso(),
        )
        if not claimed or not claimed.get("claimed"):
            continue
        status = "delivered"
        failure_code: str | None = None
        try:
            send_graph_mail(
                to_address=str(claimed.get("recipient") or "").strip(),
                subject=str(claimed.get("subject") or "").strip(),
                body=str(claimed.get("body") or ""),
                content_type="Text",
            )
        except urllib.error.HTTPError as exc:
            status = "outcome_unknown" if int(exc.code or 0) >= 500 else "failed"
            failure_code = f"graph_http_{exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            status = "outcome_unknown"
            failure_code = f"graph_transport_{type(exc).__name__}"
        except ValueError as exc:
            status = "failed"
            failure_code = f"graph_config_{type(exc).__name__}"
        except Exception as exc:
            status = "failed"
            failure_code = f"graph_unexpected_{type(exc).__name__}"

        ticket_repository.complete_account_automation_classification_email(
            account_case_id=account_case_id,
            status=status,
            failure_code=failure_code,
            completed_at=now_iso(),
        )
        if status == "delivered":
            LOGGER.info(
                "production_automation_classification_email_delivered account_case_id=%s",
                account_case_id,
            )
        else:
            LOGGER.error(
                "production_automation_classification_email_%s account_case_id=%s failure_code=%s",
                status,
                account_case_id,
                failure_code,
            )


def process_account_automation_once() -> None:
    """Run one Account-only reply/delivery cycle without Redis ticket consumers."""
    processing_profile = str(
        os.getenv("ACCOUNT_DEFAULT_PROCESSING_PROFILE") or "staging"
    ).strip().lower()
    if processing_profile == "production":
        reconcile_account_human_review_queue_mismatches(
            repository=ticket_repository,
            processing_profile=processing_profile,
            limit=25,
            timestamp=now_iso(),
        )
        _drain_production_automation_classification_emails(limit=20)
    _process_claimed_account_reply_jobs(
        from_status=ACCOUNT_REPLY_PERSONA_V8_QUEUED,
        to_status=ACCOUNT_REPLY_PERSONA_V8_PREPARING,
        due_only=False,
        limit=5,
    )
    _process_claimed_account_reply_jobs(
        from_status=ACCOUNT_REPLY_PERSONA_V8_SCHEDULED,
        to_status=ACCOUNT_REPLY_PERSONA_V8_PUBLISHING,
        due_only=True,
        limit=10,
    )
    if _account_reply_legacy_poller_enabled_from_env():
        _process_claimed_account_reply_jobs(
            from_status=ACCOUNT_REPLY_PERSONA_QUEUED,
            to_status=ACCOUNT_REPLY_PERSONA_PREPARING,
            due_only=False,
            limit=5,
        )
        _process_claimed_account_reply_jobs(
            from_status=ACCOUNT_REPLY_PERSONA_SCHEDULED,
            to_status=ACCOUNT_REPLY_PERSONA_PUBLISHING,
            due_only=True,
            limit=10,
        )
        _process_claimed_account_reply_jobs(
            from_status="queued",
            to_status="preparing",
            due_only=False,
            limit=5,
        )
        _process_claimed_account_reply_jobs(
            from_status="scheduled",
            to_status="publishing",
            due_only=True,
            limit=10,
        )
    _drain_production_zendesk_comment_deliveries(limit=20)
    _drain_account_slack_deliveries(limit=20)
    _drain_engineer_slack_events(limit=20)


def _run_account_reply_poller(interval_seconds: float) -> None:
    LOGGER.info("Account reply poller started with interval_seconds=%s.", interval_seconds)
    while not SHUTTING_DOWN:
        try:
            process_account_automation_once()
        except Exception:
            LOGGER.exception("Account reply poller failed")
        sleep_until = time.time() + max(interval_seconds, 1.0)
        while not SHUTTING_DOWN and time.time() < sleep_until:
            time.sleep(min(1.0, max(0.0, sleep_until - time.time())))
    LOGGER.info("Account reply poller stopped.")


def _queue_enablement_submission_confirmation(
    account_case: dict[str, Any],
    *,
    repair_malformed_cancelled: bool = False,
) -> bool:
    payload = (
        dict(account_case.get("internal_email_payload"))
        if isinstance(account_case.get("internal_email_payload"), dict)
        else {}
    )
    if is_rerun_owned_delivery(payload):
        LOGGER.info(
            "Skipping legacy Enablement confirmation for rerun-owned delivery %s",
            str(payload.get("delivery_key") or "").split(":rerun:", 1)[0],
        )
        return False
    ticket_id = str(account_case.get("client_ticket_id") or "").strip()
    delivery_key = str(payload.get("delivery_key") or "").strip()
    if not ticket_id or not delivery_key:
        raise ValueError("Enablement delivery is missing ticket or delivery key")
    ticket = ticket_repository.get_ticket(ticket_id)
    if not isinstance(ticket, dict):
        raise RuntimeError("Enablement delivery is missing its linked ticket")
    customer_timestamps = _account_customer_message_timestamps(ticket)
    if not customer_timestamps:
        raise ValueError("Enablement delivery is missing a customer message timestamp")
    trigger_message_created_at = max(customer_timestamps)
    latest_job = ticket_repository.get_latest_account_reply_job(ticket_id)
    latest_payload = (
        dict(latest_job.get("payload"))
        if isinstance(latest_job, dict) and isinstance(latest_job.get("payload"), dict)
        else {}
    )
    latest_delivery_key = str(latest_payload.get("automation_delivery_key") or "").strip()
    latest_status = str((latest_job or {}).get("status") or "").strip()
    latest_trigger = str((latest_job or {}).get("trigger_message_created_at") or "").strip()
    delivery_base = delivery_key.split(":rerun:", 1)[0]
    latest_delivery_base = latest_delivery_key.split(":rerun:", 1)[0]
    latest_is_rerun_for_delivery = bool(
        latest_payload.get("rerun_job_id")
        and latest_delivery_base
        and latest_delivery_base == delivery_base
    )
    malformed_cancelled_confirmation = bool(
        latest_delivery_key == delivery_key
        and latest_status == "cancelled"
        and latest_trigger not in customer_timestamps
    )
    should_repair = malformed_cancelled_confirmation and repair_malformed_cancelled
    same_trigger_delivery_exists = bool(
        latest_trigger == trigger_message_created_at
        and (
            latest_delivery_key == delivery_key
            or latest_is_rerun_for_delivery
        )
    )
    should_create = (not same_trigger_delivery_exists) or should_repair
    if bool(payload.get("customer_confirmation_queued")) and not should_repair:
        should_create = False
    if not should_create:
        return False
    fields = account_case.get("collected_fields")
    fields = fields if isinstance(fields, dict) else {}
    reply_facts = build_account_automation_reply_facts(
        handler="enablement",
        action="enablement",
        missing_fields=[],
        collected_fields=fields,
        submitted=True,
        customer_name=str(account_case.get("customer_name") or ""),
    )
    try:
        persona_assignment = ticket_repository.resolve_account_persona(ticket_id)
    except AccountPersonaUnavailableError as exc:
        timestamp = now_iso()
        reason = str(exc)
        _mark_account_case_for_human_review(
            account_case,
            reason=reason,
            timestamp=timestamp,
            policy_decision="account_persona_unavailable_human_review",
        )
        ticket_repository.save_account_case(account_case)
        return False
    created_at = now_iso()
    delay_seconds = account_reply_delay_seconds_for_profile(
        str(account_case.get("processing_profile") or "staging")
    )
    ticket_repository.cancel_pending_account_reply_jobs(ticket_id, updated_at=created_at)
    reply_payload: dict[str, Any] = {
        "draft_content": "",
        "reply_facts": reply_facts,
        "reply_pipeline": ACCOUNT_REPLY_PERSONA_PIPELINE,
        "asked_field_keys": [],
        "visibility": "account_only",
        "automation_delivery_key": delivery_key,
    }
    if persona_assignment:
        reply_payload.update(
            {
                "persona_key": persona_assignment.get("persona_key"),
                "persona_version": persona_assignment.get("version"),
                "effective_prompt": dict(persona_assignment.get("content") or {}),
            }
        )
    ticket_repository.save_account_reply_job(
        {
            "job_id": f"account-reply-{uuid4().hex}",
            "ticket_id": ticket_id,
            "trigger_message_created_at": trigger_message_created_at,
            "status": ACCOUNT_REPLY_PERSONA_V8_QUEUED,
            "scheduled_for": (
                datetime.fromisoformat(created_at).astimezone(timezone.utc)
                + timedelta(seconds=delay_seconds)
            ).isoformat(),
            "payload": reply_payload,
            "attempt_count": 0,
            "claimed_at": None,
            "published_at": None,
            "created_at": created_at,
            "updated_at": created_at,
        }
    )
    payload["customer_confirmation_queued"] = True
    account_case["internal_email_payload"] = payload
    account_case["updated_at"] = now_iso()
    ticket_repository.save_account_case(account_case)
    return True


def _send_claimed_enablement_delivery(account_case: dict[str, Any]) -> dict[str, Any]:
    existing_payload = (
        dict(account_case.get("internal_email_payload"))
        if isinstance(account_case.get("internal_email_payload"), dict)
        else {}
    )
    if is_rerun_owned_delivery(existing_payload):
        return {
            "status": "skipped",
            "reason": "rerun_owned_delivery",
            "claimed": False,
            "delivery_state": "known_not_sent",
        }
    account_case_id = str(
        account_case.get("account_case_id") or account_case.get("billing_ticket_id") or ""
    ).strip()
    ticket_id = str(account_case.get("client_ticket_id") or "").strip()
    if not account_case_id or not ticket_id:
        return {"status": "skipped", "reason": "missing_case_or_ticket_id", "claimed": False}
    canonical_ticket = ticket_repository.get_ticket(ticket_id)
    if not isinstance(canonical_ticket, dict):
        return {"status": "skipped", "reason": "linked_ticket_not_found", "claimed": False}
    try:
        payload, upgraded = upgrade_internal_email_payload(account_case, canonical_ticket)
    except InternalEmailPayloadUpgradeError:
        payload = (
            dict(account_case.get("internal_email_payload"))
            if isinstance(account_case.get("internal_email_payload"), dict)
            else {}
        )
        delivery_key = str(payload.get("delivery_key") or "").strip()
        claim_token = uuid4().hex
        marked_manual = bool(
            delivery_key
            and ticket_repository.claim_account_internal_email_delivery(
                account_case_id,
                delivery_key=delivery_key,
                claim_token=claim_token,
                claimed_at=now_iso(),
                payload=payload,
            )
            and ticket_repository.complete_account_internal_email_delivery(
                account_case_id,
                delivery_key=delivery_key,
                claim_token=claim_token,
                payload=payload,
                send_status="manual_attention",
                send_reason="template_upgrade_failed",
                completed_at=now_iso(),
            )
        )
        if marked_manual:
            account_case["internal_email_send_status"] = "manual_attention"
            account_case["internal_email_send_reason"] = "template_upgrade_failed"
        return {
            "status": "manual_attention" if marked_manual else "failed",
            "reason": "template_upgrade_failed",
            "claimed": marked_manual,
        }
    upgraded_payload = ensure_account_delivery_key(
        payload,
        handler="enablement",
        account_case_id=account_case_id,
    )
    if upgraded_payload != payload:
        payload = upgraded_payload
        account_case["internal_email_payload"] = payload
        account_case["updated_at"] = now_iso()
        ticket_repository.save_account_case(account_case)
    delivery_key = str(payload.get("delivery_key") or "").strip()
    if not delivery_key:
        return {"status": "not_ready", "reason": "missing_delivery_key", "claimed": False}
    result = deliver_account_internal_email(
        ticket_repository,
        account_case_id=account_case_id,
        payload=payload,
        sender=send_enablement_internal_email,
    )
    if result.persisted:
        account_case["internal_email_payload"] = dict(result.payload)
        account_case["internal_email_send_status"] = result.status
        account_case["internal_email_send_reason"] = result.reason
        account_case["updated_at"] = now_iso()
    if result.status != "sent":
        failure_code = reconciliation_reason_code(
            handler="enablement",
            phase="internal_email",
            detail=result.status or "failed",
        )
        updated = reconcile_automation_execution_failure(
            {
                **account_case,
                "internal_email_payload": dict(result.payload or {}) or None,
                "internal_email_send_status": result.status,
                "internal_email_send_reason": result.reason,
            },
            reason_code=failure_code,
            context={
                "failure_stage": "internal_email",
                "failure_code": failure_code,
                "delivery_state": result.delivery_state,
            },
        )
        classification = dict(updated.get("route_classification") or {})
        classification.update(
            failure_stage="internal_email",
            failure_code=failure_code,
        )
        updated["route_classification"] = classification
        ticket_repository.save_account_case(updated)
        notify_account_failure(
            repository=ticket_repository,
            incident_id=(
                f"account-automation:{account_case_id}:internal_email:"
                f"{delivery_key}"
            ),
            stage="internal_email",
            code=failure_code,
            ticket_id=ticket_id or None,
            account_case_id=account_case_id or None,
            attempts=int((result.payload or {}).get("delivery_attempt_count") or 0),
            detail=result.reason or result.status,
            now=now_iso(),
        )
    return {
        "status": result.status,
        "reason": result.reason,
        "delivery_state": result.delivery_state,
        "claimed": result.claimed,
        "persisted": result.persisted,
        "upgraded": upgraded,
        "payload": dict(result.payload),
    }


def retry_enablement_internal_deliveries_once(*, limit: int = 100) -> dict[str, int]:
    counts = {
        "examined": 0,
        "sent": 0,
        "retried": 0,
        "confirmations": 0,
        "rerun_owned_skipped": 0,
    }
    cases = ticket_repository.list_billing_tickets(limit=max(1, limit))
    for account_case in cases:
        if str(account_case.get("automation_handler") or "").strip() != "enablement":
            continue
        if list(account_case.get("missing_fields") or []):
            continue
        payload = (
            dict(account_case.get("internal_email_payload"))
            if isinstance(account_case.get("internal_email_payload"), dict)
            else {}
        )
        if is_rerun_owned_delivery(payload):
            counts["rerun_owned_skipped"] = int(counts.get("rerun_owned_skipped") or 0) + 1
            continue
        status = str(account_case.get("internal_email_send_status") or "").strip()
        if status == "sent":
            if payload.get("delivery_key") and not bool(payload.get("customer_confirmation_queued")):
                counts["confirmations"] += int(_queue_enablement_submission_confirmation(account_case))
            continue
        if status not in {"pending", "retry", "failed", "skipped_config_missing"} or not payload:
            continue
        updated_at = _parse_iso_datetime(str(account_case.get("updated_at") or ""))
        if updated_at is not None and (datetime.now(timezone.utc) - updated_at).total_seconds() < 30:
            continue
        counts["examined"] += 1
        result = _send_claimed_enablement_delivery(account_case)
        if not result.get("claimed"):
            continue
        if account_case["internal_email_send_status"] == "sent":
            counts["sent"] += 1
            counts["confirmations"] += int(_queue_enablement_submission_confirmation(account_case))
        else:
            counts["retried"] += 1
    return counts


def _run_enablement_delivery_retry_poller(interval_seconds: float) -> None:
    LOGGER.info("Enablement delivery retry poller started with interval_seconds=%s.", interval_seconds)
    while not SHUTTING_DOWN:
        try:
            counts = retry_enablement_internal_deliveries_once()
            if counts["examined"] or counts["confirmations"] or counts["rerun_owned_skipped"]:
                LOGGER.info("Enablement delivery retry result: %s", counts)
        except Exception:
            LOGGER.exception("Enablement delivery retry poller failed")
        sleep_until = time.time() + max(interval_seconds, 5.0)
        while not SHUTTING_DOWN and time.time() < sleep_until:
            time.sleep(min(1.0, max(0.0, sleep_until - time.time())))
    LOGGER.info("Enablement delivery retry poller stopped.")


def _start_enablement_delivery_retry_poller(task_types: tuple[str, ...]) -> threading.Thread | None:
    if "ticket_message_sentiment" not in task_types:
        return None
    interval_seconds = _safe_positive_float(
        os.getenv(ENABLEMENT_DELIVERY_RETRY_POLL_INTERVAL_ENV),
        60.0,
    )
    thread = threading.Thread(
        target=_run_enablement_delivery_retry_poller,
        args=(interval_seconds,),
        name="enablement-delivery-retry-poller",
        daemon=True,
    )
    thread.start()
    return thread


def _ticket_id_from_billing_reply_subject(subject: str) -> str:
    match = BILLING_REPLY_SUBJECT_TICKET_RE.search(str(subject or ""))
    return match.group(1).upper() if match else ""


def _billing_resolution_automation_status(result: str, notify_customer: bool) -> str:
    if result == "customer_action_required":
        return "waiting_customer_action" if notify_customer else "internal_resolution_submitted"
    return "customer_notified" if notify_customer else "resolved_without_customer_notification"


def _automation_reply_key(message_id: str) -> str:
    normalized = str(message_id or "").strip()
    if not normalized:
        raise ValueError("automation reply message id is required")
    return f"graph:{normalized}"


def _claim_automation_reply(*, client_ticket_id: str, message_id: str, handler: str) -> tuple[str, str, str]:
    claimed_at = datetime.now(timezone.utc)
    owner_token = uuid4().hex
    key = _automation_reply_key(message_id)
    try:
        claim = ticket_repository.claim_automation_reply(
            key,
            client_ticket_id=client_ticket_id,
            handler=handler,
            owner_token=owner_token,
            claimed_at=claimed_at.isoformat(),
            lease_expires_at=(claimed_at + timedelta(seconds=AUTOMATION_REPLY_CLAIM_LEASE_SECONDS)).isoformat(),
        )
    except ValueError as exc:
        if "linked support ticket not found" not in str(exc):
            raise
        # The claim itself cannot start without the ticket; persist a terminal
        # dismissal so this cross-environment message never retries.
        ticket_repository.record_dismissed_automation_reply(
            key,
            client_ticket_id=client_ticket_id,
            handler=handler,
            reason="linked_ticket_not_found_at_claim",
            dismissed_at=claimed_at.isoformat(),
        )
        LOGGER.warning(
            "Automation reply dismissed cross-environment ticket_id=%s reason=linked_ticket_not_found_at_claim reply_key=%s",
            client_ticket_id,
            key,
        )
        return "already_completed", key, owner_token
    return str(claim.get("status") or "in_progress"), key, owner_token


def _fail_automation_reply(key: str, owner_token: str, exc: Exception) -> None:
    error_code = str(exc) if isinstance(exc, AutomationPersonaError) else type(exc).__name__
    ticket_repository.fail_automation_reply_claim(
        key, owner_token=owner_token, error_code=error_code, failed_at=now_iso()
    )


def _billing_reply_attachment_note(reply: Any) -> str:
    attachment_text = str(getattr(reply, "attachment_text", "") or "").strip()
    if not attachment_text:
        names = tuple(str(name or "").strip() for name in getattr(reply, "attachment_names", ()) or ())
        names = tuple(name for name in names if name)
        return f"[PDF attachment: {', '.join(names)}]" if names else ""
    if "[PDF attachment:" in attachment_text:
        return attachment_text
    names = tuple(str(name or "").strip() for name in getattr(reply, "attachment_names", ()) or ())
    names = tuple(name for name in names if name)
    if not names:
        return attachment_text
    return f"[PDF attachment: {', '.join(names)}]\n{attachment_text}"


def _public_billing_attachment_summary(asset: dict[str, Any]) -> dict[str, Any]:
    original_filename = str(asset.get("original_filename") or "").strip()
    return {
        "asset_id": str(asset.get("asset_id") or "").strip(),
        "ticket_id": str(asset.get("ticket_id") or "").strip(),
        "customer_id": str(asset.get("customer_id") or "").strip(),
        "original_filename": original_filename,
        "file_name": original_filename,
        "content_type": str(asset.get("content_type") or "application/octet-stream").strip(),
        "size_bytes": int(asset.get("size_bytes") or 0),
        "extension": str(asset.get("extension") or "").strip().lower(),
        "status": str(asset.get("status") or "").strip(),
        "agent_read_enabled": False,
        "created_at": asset.get("created_at"),
        "uploaded_at": asset.get("uploaded_at"),
        "attached_at": asset.get("attached_at"),
    }


def _reply_pdf_attachments(reply: Any) -> list[Any]:
    attachments = getattr(reply, "attachments", ()) or ()
    normalized: list[Any] = []
    for item in attachments:
        name = str(getattr(item, "name", "") if not isinstance(item, dict) else item.get("name") or "").strip()
        content = getattr(item, "content", None) if not isinstance(item, dict) else item.get("content")
        content_type = str(
            getattr(item, "content_type", "") if not isinstance(item, dict) else item.get("content_type") or ""
        ).strip()
        if not name.lower().endswith(".pdf") and content_type.lower() != "application/pdf":
            continue
        if not isinstance(content, (bytes, bytearray)) or not content:
            continue
        normalized.append(item)
    return normalized


def _store_billing_reply_pdf_attachments(
    *,
    reply: Any,
    ticket_id: str,
    customer_id: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    attachments = _reply_pdf_attachments(reply)
    if not attachments:
        return [], []
    if not customer_id:
        raise ValueError("linked support ticket is missing customer_id for billing PDF attachment")

    message_attachments: list[dict[str, Any]] = []
    asset_ids: list[str] = []
    message_id = str(getattr(reply, "message_id", "") or "").strip()
    for attachment_index, item in enumerate(attachments):
        raw_name = getattr(item, "name", "") if not isinstance(item, dict) else item.get("name")
        safe_name = sanitize_asset_filename(str(raw_name or "billing-attachment.pdf"))
        content = getattr(item, "content", b"") if not isinstance(item, dict) else item.get("content") or b""
        content_bytes = bytes(content)
        content_type = str(
            getattr(item, "content_type", "") if not isinstance(item, dict) else item.get("content_type") or ""
        ).strip() or "application/pdf"
        stable_material = b"\0".join(
            (message_id.encode("utf-8"), str(attachment_index).encode("ascii"), safe_name.encode("utf-8"), content_bytes)
        )
        asset_id = f"ASSET-{hashlib.sha256(stable_material).hexdigest()[:24].upper()}"
        existing_asset = asset_repository.get_asset(asset_id)
        if existing_asset is not None:
            message_attachments.append(_public_billing_attachment_summary(existing_asset))
            asset_ids.append(asset_id)
            continue
        bucket = str(getattr(asset_storage, "bucket", "") or os.getenv("ASSET_S3_BUCKET") or "").strip()
        asset = {
            "asset_id": asset_id,
            "ticket_id": ticket_id,
            "customer_id": customer_id,
            "original_filename": safe_name,
            "content_type": content_type,
            "size_bytes": len(content_bytes),
            "extension": ".pdf",
            "status": "uploaded",
            "storage_provider": "s3",
            "bucket": bucket,
            "s3_key": build_asset_s3_key(ticket_id=ticket_id, asset_id=asset_id, file_name=safe_name),
            "meta": {
                "agent_read_enabled": False,
                "source": "billing_reply_email",
                "billing_reply_message_id": message_id,
            },
        }
        upload_info = asset_storage.store_bytes(asset, content_bytes)
        asset["etag"] = str(upload_info.get("etag") or "").strip() or None
        asset["checksum"] = str(upload_info.get("checksum") or "").strip() or None
        stored_asset = asset_repository.create_asset(asset)
        message_attachments.append(_public_billing_attachment_summary(stored_asset))
        asset_ids.append(asset_id)
    return message_attachments, asset_ids


def _dismiss_cross_environment_reply(
    reply_key: str,
    owner_token: str,
    client_ticket_id: str,
    reason: str,
) -> str:
    """Terminally ignore a reply whose case lives in the other environment.

    Both stacks poll the same mailbox; a not-found case means this stack is
    not the owner. Failing the claim would retry every poll cycle forever.
    """
    ticket_repository.dismiss_automation_reply_claim(
        reply_key,
        owner_token=owner_token,
        reason=reason,
        dismissed_at=now_iso(),
    )
    LOGGER.warning(
        "Automation reply dismissed cross-environment ticket_id=%s reason=%s reply_key=%s",
        client_ticket_id,
        reason,
        reply_key,
    )
    return "completed"


def handle_billing_request_reply(reply: Any) -> str:
    client_ticket_id = _ticket_id_from_billing_reply_subject(getattr(reply, "subject", ""))
    if not client_ticket_id:
        raise ValueError("billing reply subject does not include client ticket id")
    message_id = str(getattr(reply, "message_id", "") or "").strip()
    status, reply_key, owner_token = _claim_automation_reply(
        client_ticket_id=client_ticket_id, message_id=message_id, handler="billing"
    )
    if status != "acquired":
        return status
    attached_asset_ids: list[str] = []
    try:
        record_billing_request_reply(reply)
        billing_ticket = ticket_repository.get_billing_ticket_by_client_ticket_id(client_ticket_id)
        if billing_ticket is None:
            return _dismiss_cross_environment_reply(
                reply_key, owner_token, client_ticket_id, "billing_ticket_not_found"
            )
        if not is_registered_automation(
            route_family=billing_ticket.get("route_family"),
            execution_action=billing_ticket.get("execution_action") or billing_ticket.get("route"),
        ):
            return _dismiss_cross_environment_reply(
                reply_key, owner_token, client_ticket_id, "inactive_automation"
            )
        canonical_ticket = ticket_repository.get_ticket(client_ticket_id)
        if canonical_ticket is None:
            return _dismiss_cross_environment_reply(
                reply_key, owner_token, client_ticket_id, "linked_ticket_not_found"
            )
        body_note = str(getattr(reply, "body_text", "") or "").strip()
        attachment_note = _billing_reply_attachment_note(reply)
        note = "\n\n".join(part for part in (body_note, attachment_note) if part)
        if not note:
            raise ValueError("billing reply body is empty")
        timestamp = now_iso()
        billing_ticket_id = str(billing_ticket.get("billing_ticket_id") or "").strip()
        execution_action = str(
            billing_ticket.get("execution_action") or billing_ticket.get("route") or ""
        ).strip()
        if execution_action == "detailed_invoice":
            message_attachments, attached_asset_ids = _store_billing_reply_pdf_attachments(
                reply=reply, ticket_id=client_ticket_id,
                customer_id=str(canonical_ticket.get("customer_id") or "").strip(),
            )
            return _queue_billing_completion_reply_job(
                reply_key=reply_key,
                owner_token=owner_token,
                account_case=billing_ticket,
                billing_ticket_id=billing_ticket_id,
                client_ticket_id=client_ticket_id,
                note=note,
                message_id=message_id,
                message_attachments=message_attachments,
                attached_asset_ids=attached_asset_ids,
            )
        customer_reply = _render_case_persona_reply(
            ticket_id=client_ticket_id, case=billing_ticket, behavior="billing",
            reply_intent="resolution_update",
            known_information={"title": str(billing_ticket.get("title") or canonical_ticket.get("subject") or "").strip()},
            source_facts=[note], resolution_status="completed",
            save_case=ticket_repository.save_billing_ticket, persist_failure=False,
        )
        if not customer_reply:
            event_payload = {
                "event": "automation_persona_human_review", "ticket_id": client_ticket_id,
                "account_case_id": billing_ticket.get("account_case_id") or billing_ticket_id,
                "billing_reply_message_id": message_id,
                "reason": str(billing_ticket.get("not_automated_reason") or "automation_persona_human_review"),
                "created_at": timestamp, "source": "billing_reply_email",
            }
            committed = ticket_repository.commit_automation_reply_result(
                reply_key, owner_token=owner_token, ticket_id=client_ticket_id,
                assistant_message=None, account_case_updates=billing_ticket,
                events=[{"event_type": "automation_persona_human_review", "payload": event_payload}],
                completed_at=timestamp,
            )
            return "completed" if committed else "in_progress"
        message_attachments, attached_asset_ids = _store_billing_reply_pdf_attachments(
            reply=reply, ticket_id=client_ticket_id,
            customer_id=str(canonical_ticket.get("customer_id") or "").strip(),
        )
        assistant_message = {
            "role": "assistant", "content": customer_reply, "created_at": timestamp,
            "content_format": "plaintext", "source": "billing_reply_email",
            **({"attachments": message_attachments} if message_attachments else {}),
        }
        resolution_event = build_billing_internal_resolution_event(
            billing_ticket_id=billing_ticket_id, client_ticket_id=client_ticket_id,
            result=BILLING_RESPONSE_RESULT_COMPLETED, notify_customer=True, note=note, created_at=timestamp,
        )
        resolution_event.update({"source": "billing_reply_email", "billing_reply_message_id": message_id})
        followup_event = {
            "event": BILLING_RESPONSE_AI_FOLLOWUP_EVENT, "billing_ticket_id": billing_ticket_id,
            "ticket_id": client_ticket_id, "resolution_result": BILLING_RESPONSE_RESULT_COMPLETED,
            "notify_customer": True, "customer_reply": customer_reply, "created_at": timestamp,
            "source": "billing_reply_email", "billing_reply_message_id": message_id,
        }
        committed = ticket_repository.commit_automation_reply_result(
            reply_key, owner_token=owner_token, ticket_id=client_ticket_id,
            assistant_message=assistant_message,
            account_case_updates={"automation_status": _billing_resolution_automation_status(BILLING_RESPONSE_RESULT_COMPLETED, True),
                                  "customer_reply": customer_reply, "updated_at": timestamp},
            events=[{"event_type": BILLING_RESPONSE_EVENT, "payload": resolution_event},
                    {"event_type": BILLING_RESPONSE_AI_FOLLOWUP_EVENT, "payload": followup_event}],
            completed_at=timestamp,
        )
        if committed and attached_asset_ids:
            asset_repository.mark_attached(attached_asset_ids)
        return "completed" if committed else "in_progress"
    except Exception as exc:
        _fail_automation_reply(reply_key, owner_token, exc)
        raise


def _queue_enablement_completion_reply_job(
    *,
    reply_key: str,
    owner_token: str,
    account_case: dict[str, Any],
    canonical_ticket: dict[str, Any],
    client_ticket_id: str,
    note: str,
    known_information: dict[str, Any],
    message_id: str,
    handler: str,
    completion_source: str = "regex",
) -> str:
    """Queue the enablement completion as a standard Account reply job.

    The completion reply must flow through the normal publication pipeline so
    production cases deliver it as a public Zendesk comment and close (local and
    solved) only after the delivery readback confirms it.
    """
    timestamp = now_iso()
    source = f"{handler}_reply_email"
    case_id = (
        account_case.get("account_case_id")
        or account_case.get("billing_ticket_id")
        or ""
    )
    # The completion is triggered by the internal resolution email, not by a
    # customer message: it carries its own unique trigger timestamp and an
    # internal_resolution marker that exempts it from the customer-currency
    # publication gate. Reusing the customer trigger would collide with the
    # submission job for the same customer message.
    trigger_message_created_at = timestamp
    try:
        persona_assignment = ticket_repository.resolve_account_persona(client_ticket_id)
    except AccountPersonaUnavailableError as exc:
        _mark_account_case_for_human_review(
            account_case,
            reason=str(exc),
            timestamp=timestamp,
            policy_decision="account_persona_unavailable_human_review",
        )
        manual_event = {
            "event": "automation_persona_human_review", "ticket_id": client_ticket_id,
            "account_case_id": case_id, "automation_reply_message_id": message_id,
            "reason": str(account_case.get("not_automated_reason") or str(exc)),
            "created_at": timestamp, "source": source,
        }
        committed = ticket_repository.commit_automation_reply_result(
            reply_key, owner_token=owner_token, ticket_id=client_ticket_id,
            assistant_message=None, account_case_updates=account_case,
            events=[{"event_type": "automation_persona_human_review", "payload": manual_event}],
            completed_at=timestamp,
        )
        ticket_repository.save_account_case(account_case)
        return "completed" if committed else "in_progress"
    enriched_information = {
        **dict(known_information or {}),
        "ticket_id": client_ticket_id,
        "account_case_id": str(case_id),
        "customer_email": str(account_case.get("customer_email") or "").strip(),
    }
    sanitized_note = sanitize_enablement_completion_note(note, enriched_information)
    reply_facts = build_automation_reply_facts(
        behavior=handler,
        reply_intent=ACCOUNT_REPLY_INTENT_ENABLEMENT_COMPLETED_AND_CLOSE,
        known_information=enriched_information,
        source_facts=[sanitized_note],
        resolution_status="completed",
        customer_name=_account_greeting_customer_name(
            account_case, client_ticket_id, canonical_ticket=canonical_ticket
        ),
    )
    reply_facts["completion_acknowledgement"] = (
        _enablement_completion_acknowledgement(canonical_ticket)
    )
    delay_seconds = account_reply_delay_seconds_for_profile(
        str(account_case.get("processing_profile") or "staging")
    )
    try:
        normalized_facts, _intent, _close = normalize_account_reply_contract(
            reply_facts,
            reply_intent=ACCOUNT_REPLY_INTENT_ENABLEMENT_COMPLETED_AND_CLOSE,
            close_after_publish=True,
        )
    except AccountReplyContractError as exc:
        _mark_account_case_for_human_review(
            account_case,
            reason=str(exc),
            timestamp=timestamp,
            policy_decision="automation_persona_human_review",
        )
        manual_event = {
            "event": "automation_persona_human_review", "ticket_id": client_ticket_id,
            "account_case_id": case_id, "automation_reply_message_id": message_id,
            "reason": str(exc),
            "created_at": timestamp, "source": source,
        }
        committed = ticket_repository.commit_automation_reply_result(
            reply_key, owner_token=owner_token, ticket_id=client_ticket_id,
            assistant_message=None, account_case_updates=account_case,
            events=[{"event_type": "automation_persona_human_review", "payload": manual_event}],
            completed_at=timestamp,
        )
        ticket_repository.save_account_case(account_case)
        return "completed" if committed else "in_progress"
    reply_payload: dict[str, Any] = {
        "draft_content": "",
        "reply_facts": normalized_facts,
        "reply_pipeline": ACCOUNT_REPLY_PERSONA_PIPELINE,
        "asked_field_keys": [],
        "visibility": "account_only",
        "internal_resolution": True,
        "close_after_publish": True,
        "reply_intent": ACCOUNT_REPLY_INTENT_ENABLEMENT_COMPLETED_AND_CLOSE,
        "automation_delivery_key": str(
            (account_case.get("internal_email_payload") or {}).get("delivery_key") or ""
        ),
    }
    if persona_assignment:
        reply_payload.update(
            {
                "persona_key": persona_assignment.get("persona_key"),
                "persona_version": persona_assignment.get("version"),
                "effective_prompt": dict(persona_assignment.get("content") or {}),
            }
        )
    ticket_repository.cancel_pending_account_reply_jobs(
        client_ticket_id, updated_at=timestamp
    )
    job = ticket_repository.save_account_reply_job(
        {
            "job_id": f"account-reply-{uuid4().hex}",
            "ticket_id": client_ticket_id,
            "trigger_message_created_at": trigger_message_created_at,
            "status": ACCOUNT_REPLY_PERSONA_V8_QUEUED,
            "scheduled_for": (
                datetime.fromisoformat(timestamp).astimezone(timezone.utc)
                + timedelta(seconds=delay_seconds)
            ).isoformat(),
            "payload": reply_payload,
            "attempt_count": 0,
            "claimed_at": None,
            "published_at": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
    )
    resolution_event = {
        "event": f"{handler}_internal_resolution_received", "account_case_id": case_id,
        "ticket_id": client_ticket_id, "note": note, "created_at": timestamp,
        "source": source, "automation_reply_message_id": message_id,
        "completion_source": completion_source,
    }
    queued_event = {
        "event": f"{handler}_completion_reply_job_queued", "account_case_id": case_id,
        "ticket_id": client_ticket_id, "reply_job_id": str(job.get("job_id") or ""),
        "created_at": timestamp, "source": source,
        "automation_reply_message_id": message_id,
    }
    committed = ticket_repository.commit_automation_reply_result(
        reply_key, owner_token=owner_token, ticket_id=client_ticket_id,
        assistant_message=None,
        account_case_updates={"automation_status": "automation", "updated_at": timestamp},
        events=[{"event_type": resolution_event["event"], "payload": resolution_event},
                {"event_type": queued_event["event"], "payload": queued_event}],
        completed_at=timestamp,
    )
    return "completed" if committed else "in_progress"


def _queue_internal_followup_reply_job(
    *,
    reply_key: str,
    owner_token: str,
    account_case: dict[str, Any],
    client_ticket_id: str,
    note: str,
    known_information: dict[str, Any],
    message_id: str,
    handler: str,
) -> str:
    """Queue a non-completion internal resolution as a standard Account reply job.

    The internal reply (e.g. "the App ID is not correct") must reach the
    customer as a public Zendesk comment without closing the ticket, so it
    flows through the normal publication pipeline like the completion job.
    Cancelling pending jobs first also retires a not-yet-published
    submission_confirmation, preventing the stale boilerplate from overriding
    this substantive follow-up in the case record.
    """
    timestamp = now_iso()
    source = f"{handler}_reply_email"
    case_id = (
        account_case.get("account_case_id")
        or account_case.get("billing_ticket_id")
        or ""
    )
    # The follow-up is triggered by the internal resolution email, not by a
    # customer message: it carries its own unique trigger timestamp and an
    # internal_resolution marker that exempts it from the customer-currency
    # publication gate. Reusing the customer trigger would collide with the
    # submission job for the same customer message.
    trigger_message_created_at = timestamp
    try:
        persona_assignment = ticket_repository.resolve_account_persona(client_ticket_id)
    except AccountPersonaUnavailableError as exc:
        _mark_account_case_for_human_review(
            account_case,
            reason=str(exc),
            timestamp=timestamp,
            policy_decision="account_persona_unavailable_human_review",
        )
        manual_event = {
            "event": "automation_persona_human_review", "ticket_id": client_ticket_id,
            "account_case_id": case_id, "automation_reply_message_id": message_id,
            "reason": str(account_case.get("not_automated_reason") or str(exc)),
            "created_at": timestamp, "source": source,
        }
        committed = ticket_repository.commit_automation_reply_result(
            reply_key, owner_token=owner_token, ticket_id=client_ticket_id,
            assistant_message=None, account_case_updates=account_case,
            events=[{"event_type": "automation_persona_human_review", "payload": manual_event}],
            completed_at=timestamp,
        )
        ticket_repository.save_account_case(account_case)
        return "completed" if committed else "in_progress"
    enriched_information = {
        **dict(known_information or {}),
        "ticket_id": client_ticket_id,
        "account_case_id": str(case_id),
        "customer_email": str(account_case.get("customer_email") or "").strip(),
    }
    sanitized_note = sanitize_enablement_completion_note(note, enriched_information)
    reply_facts = build_automation_reply_facts(
        behavior=handler,
        reply_intent=ACCOUNT_REPLY_INTENT_RESOLUTION_UPDATE,
        known_information=enriched_information,
        source_facts=[sanitized_note],
        # The internal note is the only authoritative state source here: the
        # legacy inline-render fallback asserted "completed" because the LLM
        # extraction used to overwrite it, but the job pipeline has no
        # extraction step, so a hardcoded status would mislead the Persona
        # (AC-13096 rendered "request has been completed" for "the appid is
        # incorrect"). Leave it unset and let the sanitized note speak.
        resolution_status=None,
        customer_name=_account_greeting_customer_name(account_case, client_ticket_id),
    )
    delay_seconds = account_reply_delay_seconds_for_profile(
        str(account_case.get("processing_profile") or "staging")
    )
    try:
        normalized_facts, _intent, _close = normalize_account_reply_contract(
            reply_facts,
            reply_intent=ACCOUNT_REPLY_INTENT_RESOLUTION_UPDATE,
            close_after_publish=False,
        )
    except AccountReplyContractError as exc:
        _mark_account_case_for_human_review(
            account_case,
            reason=str(exc),
            timestamp=timestamp,
            policy_decision="automation_persona_human_review",
        )
        manual_event = {
            "event": "automation_persona_human_review", "ticket_id": client_ticket_id,
            "account_case_id": case_id, "automation_reply_message_id": message_id,
            "reason": str(exc),
            "created_at": timestamp, "source": source,
        }
        committed = ticket_repository.commit_automation_reply_result(
            reply_key, owner_token=owner_token, ticket_id=client_ticket_id,
            assistant_message=None, account_case_updates=account_case,
            events=[{"event_type": "automation_persona_human_review", "payload": manual_event}],
            completed_at=timestamp,
        )
        ticket_repository.save_account_case(account_case)
        return "completed" if committed else "in_progress"
    reply_payload: dict[str, Any] = {
        "draft_content": "",
        "reply_facts": normalized_facts,
        "reply_pipeline": ACCOUNT_REPLY_PERSONA_PIPELINE,
        "asked_field_keys": [],
        "visibility": "account_only",
        "internal_resolution": True,
        "close_after_publish": False,
        "reply_intent": ACCOUNT_REPLY_INTENT_RESOLUTION_UPDATE,
        "automation_delivery_key": str(
            (account_case.get("internal_email_payload") or {}).get("delivery_key") or ""
        ),
    }
    if persona_assignment:
        reply_payload.update(
            {
                "persona_key": persona_assignment.get("persona_key"),
                "persona_version": persona_assignment.get("version"),
                "effective_prompt": dict(persona_assignment.get("content") or {}),
            }
        )
    ticket_repository.cancel_pending_account_reply_jobs(
        client_ticket_id, updated_at=timestamp
    )
    job = ticket_repository.save_account_reply_job(
        {
            "job_id": f"account-reply-{uuid4().hex}",
            "ticket_id": client_ticket_id,
            "trigger_message_created_at": trigger_message_created_at,
            "status": ACCOUNT_REPLY_PERSONA_V8_QUEUED,
            "scheduled_for": (
                datetime.fromisoformat(timestamp).astimezone(timezone.utc)
                + timedelta(seconds=delay_seconds)
            ).isoformat(),
            "payload": reply_payload,
            "attempt_count": 0,
            "claimed_at": None,
            "published_at": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
    )
    resolution_event = {
        "event": f"{handler}_internal_resolution_received", "account_case_id": case_id,
        "ticket_id": client_ticket_id, "note": note, "created_at": timestamp,
        "source": source, "automation_reply_message_id": message_id,
    }
    queued_event = {
        "event": f"{handler}_customer_followup_job_queued", "account_case_id": case_id,
        "ticket_id": client_ticket_id, "reply_job_id": str(job.get("job_id") or ""),
        "created_at": timestamp, "source": source,
        "automation_reply_message_id": message_id,
    }
    committed = ticket_repository.commit_automation_reply_result(
        reply_key, owner_token=owner_token, ticket_id=client_ticket_id,
        assistant_message=None,
        account_case_updates={"automation_status": "customer_notified", "updated_at": timestamp},
        events=[{"event_type": resolution_event["event"], "payload": resolution_event},
                {"event_type": queued_event["event"], "payload": queued_event}],
        completed_at=timestamp,
    )
    return "completed" if committed else "in_progress"


def _queue_billing_completion_reply_job(
    *,
    reply_key: str,
    owner_token: str,
    account_case: dict[str, Any],
    billing_ticket_id: str,
    client_ticket_id: str,
    note: str,
    message_id: str,
    message_attachments: list[dict[str, Any]],
    attached_asset_ids: list[str],
) -> str:
    """Queue the detailed-invoice completion as a standard Account reply job.

    The completion reply must flow through the normal publication pipeline so
    production cases deliver it as a public Zendesk comment (with the reply
    PDF attachments uploaded to the Zendesk ticket) and close (local and
    solved) only after the delivery readback confirms it.
    """
    timestamp = now_iso()
    source = "billing_reply_email"
    case_id = (
        account_case.get("account_case_id")
        or account_case.get("billing_ticket_id")
        or ""
    )
    # The completion is triggered by the internal resolution email, not by a
    # customer message: it carries its own unique trigger timestamp and an
    # internal_resolution marker that exempts it from the customer-currency
    # publication gate. Reusing the customer trigger would collide with the
    # submission confirmation job for the same customer message.
    trigger_message_created_at = timestamp
    try:
        persona_assignment = ticket_repository.resolve_account_persona(client_ticket_id)
    except AccountPersonaUnavailableError as exc:
        _mark_account_case_for_human_review(
            account_case,
            reason=str(exc),
            timestamp=timestamp,
            policy_decision="account_persona_unavailable_human_review",
        )
        manual_event = {
            "event": "automation_persona_human_review", "ticket_id": client_ticket_id,
            "account_case_id": case_id, "automation_reply_message_id": message_id,
            "reason": str(account_case.get("not_automated_reason") or str(exc)),
            "created_at": timestamp, "source": source,
        }
        committed = ticket_repository.commit_automation_reply_result(
            reply_key, owner_token=owner_token, ticket_id=client_ticket_id,
            assistant_message=None, account_case_updates=account_case,
            events=[{"event_type": "automation_persona_human_review", "payload": manual_event}],
            completed_at=timestamp,
        )
        ticket_repository.save_account_case(account_case)
        return "completed" if committed else "in_progress"
    reply_facts = build_automation_reply_facts(
        behavior="detailed_invoice",
        reply_intent=ACCOUNT_REPLY_INTENT_DETAILED_INVOICE_COMPLETED_AND_CLOSE,
        known_information={
            "title": str(account_case.get("title") or "").strip(),
        },
        source_facts=[note],
        resolution_status="completed",
        customer_name=_account_greeting_customer_name(account_case, client_ticket_id),
    )
    reply_facts["attachments_included"] = bool(message_attachments)
    delay_seconds = account_reply_delay_seconds_for_profile(
        str(account_case.get("processing_profile") or "staging")
    )
    try:
        normalized_facts, _intent, _close = normalize_account_reply_contract(
            reply_facts,
            reply_intent=ACCOUNT_REPLY_INTENT_DETAILED_INVOICE_COMPLETED_AND_CLOSE,
            close_after_publish=True,
        )
    except AccountReplyContractError as exc:
        _mark_account_case_for_human_review(
            account_case,
            reason=str(exc),
            timestamp=timestamp,
            policy_decision="automation_persona_human_review",
        )
        manual_event = {
            "event": "automation_persona_human_review", "ticket_id": client_ticket_id,
            "account_case_id": case_id, "automation_reply_message_id": message_id,
            "reason": str(exc),
            "created_at": timestamp, "source": source,
        }
        committed = ticket_repository.commit_automation_reply_result(
            reply_key, owner_token=owner_token, ticket_id=client_ticket_id,
            assistant_message=None, account_case_updates=account_case,
            events=[{"event_type": "automation_persona_human_review", "payload": manual_event}],
            completed_at=timestamp,
        )
        ticket_repository.save_account_case(account_case)
        return "completed" if committed else "in_progress"
    reply_payload: dict[str, Any] = {
        "draft_content": "",
        "reply_facts": normalized_facts,
        "reply_pipeline": ACCOUNT_REPLY_PERSONA_PIPELINE,
        "asked_field_keys": [],
        "visibility": "account_only",
        "internal_resolution": True,
        "close_after_publish": True,
        "reply_intent": ACCOUNT_REPLY_INTENT_DETAILED_INVOICE_COMPLETED_AND_CLOSE,
        "automation_delivery_key": str(
            (account_case.get("internal_email_payload") or {}).get("delivery_key") or ""
        ),
    }
    if message_attachments:
        reply_payload["attachments"] = list(message_attachments)
    if persona_assignment:
        reply_payload.update(
            {
                "persona_key": persona_assignment.get("persona_key"),
                "persona_version": persona_assignment.get("version"),
                "effective_prompt": dict(persona_assignment.get("content") or {}),
            }
        )
    ticket_repository.cancel_pending_account_reply_jobs(
        client_ticket_id, updated_at=timestamp
    )
    job = ticket_repository.save_account_reply_job(
        {
            "job_id": f"account-reply-{uuid4().hex}",
            "ticket_id": client_ticket_id,
            "trigger_message_created_at": trigger_message_created_at,
            "status": ACCOUNT_REPLY_PERSONA_V8_QUEUED,
            "scheduled_for": (
                datetime.fromisoformat(timestamp).astimezone(timezone.utc)
                + timedelta(seconds=delay_seconds)
            ).isoformat(),
            "payload": reply_payload,
            "attempt_count": 0,
            "claimed_at": None,
            "published_at": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
    )
    resolution_event = build_billing_internal_resolution_event(
        billing_ticket_id=billing_ticket_id, client_ticket_id=client_ticket_id,
        result=BILLING_RESPONSE_RESULT_COMPLETED, notify_customer=True, note=note,
        created_at=timestamp,
    )
    resolution_event.update({"source": source, "billing_reply_message_id": message_id})
    queued_event = {
        "event": "detailed_invoice_completion_reply_job_queued", "account_case_id": case_id,
        "ticket_id": client_ticket_id, "reply_job_id": str(job.get("job_id") or ""),
        "attachments": [str(item.get("asset_id") or "") for item in message_attachments],
        "created_at": timestamp, "source": source, "billing_reply_message_id": message_id,
    }
    committed = ticket_repository.commit_automation_reply_result(
        reply_key, owner_token=owner_token, ticket_id=client_ticket_id,
        assistant_message=None,
        account_case_updates={"automation_status": "automation", "updated_at": timestamp},
        events=[{"event_type": BILLING_RESPONSE_EVENT, "payload": resolution_event},
                {"event_type": "detailed_invoice_completion_reply_job_queued", "payload": queued_event}],
        completed_at=timestamp,
    )
    if committed and attached_asset_ids:
        asset_repository.mark_attached(attached_asset_ids)
    return "completed" if committed else "in_progress"


def _handle_non_billing_automation_reply(reply: Any, *, handler: str) -> str:
    client_ticket_id = _ticket_id_from_billing_reply_subject(getattr(reply, "subject", ""))
    if not client_ticket_id:
        raise ValueError(f"{handler} reply subject does not include client ticket id")
    message_id = str(getattr(reply, "message_id", "") or "").strip()
    status, reply_key, owner_token = _claim_automation_reply(
        client_ticket_id=client_ticket_id, message_id=message_id, handler=handler
    )
    if status != "acquired":
        return status
    try:
        account_case = ticket_repository.get_billing_ticket_by_client_ticket_id(client_ticket_id)
        if account_case is None:
            return _dismiss_cross_environment_reply(
                reply_key, owner_token, client_ticket_id, "account_case_not_found"
            )
        if str(account_case.get("automation_handler") or "").strip() != handler:
            return _dismiss_cross_environment_reply(
                reply_key, owner_token, client_ticket_id, "automation_handler_mismatch"
            )
        canonical_ticket = ticket_repository.get_ticket(client_ticket_id)
        if canonical_ticket is None:
            return _dismiss_cross_environment_reply(
                reply_key, owner_token, client_ticket_id, "linked_ticket_not_found"
            )
        note = str(getattr(reply, "body_text", "") or "").strip()
        if not note:
            raise ValueError(f"{handler} reply body is empty")
        collected_fields = account_case.get("collected_fields")
        collected_fields = collected_fields if isinstance(collected_fields, dict) else {}
        known_information = (
            dict(collected_fields)
            if handler == "enablement" else {"products": collected_fields.get("products") or []}
        )
        completion_source: str | None = None
        enablement_completed = False
        if handler == "enablement":
            if _enablement_reply_explicitly_confirms_completion(note):
                enablement_completed = True
                completion_source = "regex"
            else:
                classification = classify_enablement_completion(
                    note,
                    feature_label=str(collected_fields.get("requested_feature_label") or "").strip() or None,
                )
                enablement_completed = classification.completed
                completion_source = classification.source
                if classification.failure_reason:
                    LOGGER.info(
                        "enablement_completion_classification completed=%s source=%s failure_reason=%s",
                        classification.completed,
                        classification.source,
                        classification.failure_reason,
                    )
                else:
                    LOGGER.info(
                        "enablement_completion_classification completed=%s source=%s",
                        classification.completed,
                        classification.source,
                    )
        if enablement_completed:
            return _queue_enablement_completion_reply_job(
                reply_key=reply_key,
                owner_token=owner_token,
                account_case=account_case,
                canonical_ticket=canonical_ticket,
                client_ticket_id=client_ticket_id,
                note=note,
                known_information=known_information,
                message_id=message_id,
                handler=handler,
                completion_source=completion_source or "regex",
            )
        return _queue_internal_followup_reply_job(
            reply_key=reply_key,
            owner_token=owner_token,
            account_case=account_case,
            client_ticket_id=client_ticket_id,
            note=note,
            known_information=known_information,
            message_id=message_id,
            handler=handler,
        )
    except Exception as exc:
        _fail_automation_reply(reply_key, owner_token, exc)
        raise


def handle_enablement_request_reply(reply: Any) -> str:
    return _handle_non_billing_automation_reply(reply, handler="enablement")


def handle_quota_request_reply(reply: Any) -> str:
    return _handle_non_billing_automation_reply(reply, handler="quota")


def handle_automation_request_reply(reply: Any) -> str:
    subject = str(getattr(reply, "subject", "") or "")
    if ENABLEMENT_INTERNAL_EMAIL_SUBJECT_PREFIX.lower() in subject.lower():
        return handle_enablement_request_reply(reply)
    if QUOTA_INTERNAL_EMAIL_SUBJECT_PREFIX.lower() in subject.lower():
        return handle_quota_request_reply(reply)
    if "[billing request]" in subject.lower():
        return handle_billing_request_reply(reply)
    raise ValueError("unsupported automation reply subject")


def _start_billing_reply_poller_if_enabled() -> threading.Thread | None:
    if not _billing_reply_poller_enabled_from_env():
        return None
    interval_seconds = _billing_reply_poll_interval_from_env()
    thread = threading.Thread(
        target=_run_billing_reply_poller,
        args=(interval_seconds,),
        name="automation-reply-poller",
        daemon=True,
    )
    thread.start()
    return thread


def _start_engineer_assignment_poller_if_enabled() -> threading.Thread | None:
    if not _engineer_assignment_poller_enabled_from_env():
        return None
    thread = threading.Thread(
        target=_run_engineer_assignment_poller,
        args=(_engineer_assignment_poll_interval_from_env(),),
        name="engineer-assignment-poller",
        daemon=True,
    )
    thread.start()
    return thread


def _start_account_reply_poller() -> threading.Thread | None:
    if not _account_reply_poller_enabled_from_env():
        return None
    thread = threading.Thread(
        target=_run_account_reply_poller,
        args=(_account_reply_poll_interval_from_env(),),
        name="account-reply-poller",
        daemon=True,
    )
    thread.start()
    return thread


def _publish(bus: SyncRedisEventBus, channels: list[str], payload: dict[str, Any]) -> None:
    bus_payload = dict(payload)
    bus_payload["targets"] = channels
    bus.publish(bus_payload)


def _active_engineer_case_payload(ticket: dict[str, Any]) -> dict[str, Any] | None:
    ticket_id = str(ticket.get("ticket_id") or "").strip()
    if not ticket_id:
        return None
    return ticket_repository.get_active_engineer_case(ticket_id, include_client_messages=True)


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
        "trigger_source": str(investigation.get("trigger_source") or engineer_case.get("trigger_source") or "").strip(),
        "trigger_reason": str(investigation.get("trigger_reason") or engineer_case.get("trigger_reason") or "").strip(),
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


def _prepare_engineer_case_for_ticket(
    ticket: dict[str, Any],
    *,
    case_status: str,
    trigger_source: str,
    trigger_reason: str,
    now_value: str,
) -> tuple[dict[str, Any], bool]:
    active_case = _active_engineer_case_payload(ticket)
    if isinstance(active_case, dict):
        return _engineer_case_payload_to_record(active_case), False
    case_sequence = int(ticket.get("engineer_case_count") or 0) + 1
    engineer_case_id = f"{str(ticket.get('ticket_id') or '').strip()}-{case_sequence}"
    return (
        build_new_engineer_case(
            ticket,
            engineer_case_id=engineer_case_id,
            case_sequence=case_sequence,
            title=derive_engineer_case_title(ticket),
            status=case_status,
            trigger_source=trigger_source,
            trigger_reason=trigger_reason,
            now_value=now_value,
        ),
        True,
    )


def _build_worker_investigation_event(
    ticket: dict[str, Any],
    engineer_case: dict[str, Any],
    *,
    created: bool = False,
) -> dict[str, Any]:
    engineer_case_id = str(engineer_case.get("engineer_case_id") or engineer_case.get("ticket_id") or "").strip()
    state = str(
        engineer_case.get("investigation_state")
        or (
            (
                engineer_case.get("active_investigation")
                if isinstance(engineer_case.get("active_investigation"), dict)
                else {}
            ).get("state")
        )
        or "active"
    ).strip().lower()
    if state == "awaiting_confirmation":
        event_name = "ticket_investigation_confirmation_requested"
    elif created:
        event_name = "ticket_investigation_started"
    elif state == "closed":
        event_name = "ticket_investigation_closed"
    else:
        event_name = "ticket_investigation_updated"

    latest_message = ""
    active_payload = _active_investigation_from_case_payload(engineer_case)
    messages = (
        active_payload.get("messages")
        if isinstance(active_payload, dict) and isinstance(active_payload.get("messages"), list)
        else engineer_case.get("messages")
    )
    if isinstance(messages, list) and messages:
        latest_message = str(messages[-1].get("content") or "").strip()
    return {
        "event": event_name,
        "ticket_id": engineer_case_id or str(ticket.get("ticket_id") or ""),
        "client_ticket_id": str(ticket.get("ticket_id") or ""),
        "engineer_case_id": engineer_case_id or None,
        "investigation_id": engineer_case_id or None,
        "status": normalize_ticket_status(engineer_case.get("status") or ticket.get("status")),
        "investigation_state": state or "active",
        "message": latest_message[:200],
        "created_at": now_iso(),
        **(
            {
                "agent_phase": str(engineer_case["engineer_agent_state"].get("phase") or "").strip(),
                "agent_ready_to_reply": bool(engineer_case["engineer_agent_state"].get("ready_to_reply")),
                "agent_goal": str(engineer_case["engineer_agent_state"].get("goal") or "").strip(),
                "agent_next_request_for_engineer": str(
                    engineer_case["engineer_agent_state"].get("next_request_for_engineer") or ""
                ).strip(),
                "agent_updated_at": str(engineer_case["engineer_agent_state"].get("last_refreshed_at") or "").strip(),
            }
            if isinstance(engineer_case.get("engineer_agent_state"), dict)
            else {}
        ),
    }


class _WorkerRagCancelled(RuntimeError):
    def __init__(self, stage: str | None = None) -> None:
        super().__init__("Worker RAG execution cancelled")
        self.stage = str(stage or "").strip() or None


def _build_current_worker_rag_executor():
    if str(os.getenv("AUTOMATION_ECS_ACCOUNT_ONLY") or "").strip() == "1":
        return build_ragflow_worker_executor(
            RagflowDocsSearchSkillClient(),
            timeout_seconds=_worker_rag_timeout_seconds(),
        )
    return build_worker_rag_executor(
        rag_service_client,
        timeout_seconds=_worker_rag_timeout_seconds(),
        max_wait_seconds=_worker_rag_max_wait_seconds(),
        recovery_window_seconds=_worker_rag_recovery_window_seconds(),
        recovery_poll_interval_seconds=_worker_rag_recovery_poll_interval_seconds(),
    )


def _worker_rag_with_cancel_guard(**kwargs: Any) -> RagTicketAnswerDetail:
    try:
        return _build_current_worker_rag_executor()(**kwargs)
    except RagServiceError as exc:
        payload = exc.payload if isinstance(exc.payload, dict) else {}
        if exc.status_code == 409 and str(payload.get("reason") or "").strip() == "cancelled_by_route_flip":
            raise _WorkerRagCancelled(stage=str(payload.get("stage") or "").strip() or None) from exc
        raise


def _execute_agent_runtime_ticket_query(
    customer_message: str,
    *,
    ticket_id: str,
    customer_id: str | None,
    requester: str | None = None,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]],
    message_created_at: str,
    product: str | None = None,
    client_intake_state: dict[str, object] | None = None,
    latest_assistant_message: dict[str, Any] | None = None,
    current_ticket_status: str | None = None,
) -> tuple[TicketExecutionResult, dict[str, Any]]:
    runtime_execution = execute_client_ticket_agent_runtime(
        customer_message,
        ticket_id=ticket_id,
        customer_id=customer_id,
        requester=requester,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        product=product,
        message_id=message_created_at,
        client_intake_state=client_intake_state,
        latest_assistant_message=latest_assistant_message,
        current_ticket_status=current_ticket_status,
        has_active_engineer_case=False,
        route_agent=decide_support_route,
        route_executor=resolve_support_message,
        rag_executor=_worker_rag_with_cancel_guard,
        review_agent=_run_client_ticket_review_agent,
        route_timeout_seconds=OPTIMISTIC_ROUTE_TIMEOUT_SECONDS,
    )
    runtime_state = runtime_execution.runtime_state
    rag_service_state = runtime_state.rag_service if isinstance(runtime_state.rag_service, dict) else {}
    route_agent_state = runtime_state.route_agent if isinstance(runtime_state.route_agent, dict) else {}
    route_status = str(route_agent_state.get("status") or "").strip().lower()
    rag_status = str(rag_service_state.get("status") or "").strip().lower()
    rag_cancelled = rag_status == "cancelled"
    rag_cancel_stage = str(rag_service_state.get("reason") if rag_cancelled else "").strip()
    diagnostics: dict[str, Any] = {
        "parallel_mode": "main_agent",
        "api_persist_latency_ms": None,
        "api_return_latency_ms": None,
        "route_latency_ms": 0.0,
        "route_timeout_seconds": float(
            runtime_execution.diagnostics.get("route_timeout_seconds") or OPTIMISTIC_ROUTE_TIMEOUT_SECONDS
        ),
        "route_final_action": str(route_agent_state.get("decision") or runtime_execution.result.execution_action or "").strip() or None,
        "route_result_source": "route_first" if route_status == "completed" else "route_fail_open",
        "route_fail_open": bool(runtime_execution.diagnostics.get("route_fail_open"))
        or route_status != "completed",
        "rag_started_at": rag_service_state.get("started_at"),
        "rag_finished_at": rag_service_state.get("completed_at"),
        "rag_cancelled": rag_cancelled,
        "rag_cancel_stage": rag_cancel_stage or None,
    }
    return runtime_execution.result, diagnostics


def _execute_parallel_ticket_query(
    customer_message: str,
    *,
    ticket_id: str,
    customer_id: str | None,
    requester: str | None = None,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]],
    message_created_at: str,
    product: str | None = None,
    client_intake_state: dict[str, object] | None = None,
    latest_assistant_message: dict[str, Any] | None = None,
    current_ticket_status: str | None = None,
) -> tuple[TicketExecutionResult, dict[str, Any]]:
    return _execute_agent_runtime_ticket_query(
        customer_message,
        ticket_id=ticket_id,
        customer_id=customer_id,
        requester=requester,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        message_created_at=message_created_at,
        product=product,
        client_intake_state=client_intake_state,
        latest_assistant_message=latest_assistant_message,
        current_ticket_status=current_ticket_status,
    )


def _orchestrate_worker_support_message(
    customer_message: str,
    *,
    ticket_id: str,
    customer_id: str | None,
    requester: str | None = None,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]],
    message_created_at: str = "",
    product: str | None = None,
    product_selection_state: dict[str, object] | None = None,
    client_intake_state: dict[str, object] | None = None,
    latest_assistant_message: dict[str, Any] | None = None,
    current_ticket_status: str | None = None,
) -> tuple[TicketExecutionResult, dict[str, Any]]:
    product_context = resolve_support_product_context(
        message=customer_message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        product=product,
        product_selection_state=product_selection_state,
        latest_assistant_message=latest_assistant_message,
        current_ticket_status=current_ticket_status,
        requester=requester,
        customer_id=customer_id,
        message_created_at=message_created_at,
        route_agent=decide_support_route,
    )
    effective_message = str(product_context.effective_message or customer_message).strip() or customer_message
    effective_product = product_context.product if product_context.product is not None else product
    effective_client_intake_state = None if product_context.product_changed else client_intake_state
    diagnostics_context = {
        "resolved_product": effective_product,
        "product_selection_state": (
            dict(product_context.product_selection_state)
            if isinstance(product_context.product_selection_state, dict)
            else None
        ),
        "product_changed": bool(product_context.product_changed),
    }
    if product_context.preflight_execution is not None:
        return product_context.preflight_execution, {
            "parallel_mode": "main_agent",
            **diagnostics_context,
        }
    execution, diagnostics = _execute_agent_runtime_ticket_query(
        effective_message,
        ticket_id=ticket_id,
        customer_id=customer_id,
        requester=requester,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        message_created_at=message_created_at,
        product=effective_product,
        client_intake_state=effective_client_intake_state,
        latest_assistant_message=latest_assistant_message,
        current_ticket_status=current_ticket_status,
    )
    diagnostics["parallel_mode"] = "main_agent"
    diagnostics.update(diagnostics_context)
    return execution, diagnostics


def _is_retryable_ticket_storage_error(exc: BaseException) -> bool:
    return isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError, OSError, TimeoutError))


def _call_ticket_repository(operation_name: str, callback: Any) -> Any:
    attempts = max(1, TICKET_REPOSITORY_RETRY_MAX + 1)
    for attempt in range(1, attempts + 1):
        try:
            return callback()
        except Exception as exc:
            if not _is_retryable_ticket_storage_error(exc) or attempt >= attempts:
                raise
            LOGGER.warning(
                "Worker retrying ticket repository operation %s after attempt %s/%s: %s",
                operation_name,
                attempt,
                attempts,
                exc,
            )
            time.sleep(TICKET_REPOSITORY_RETRY_BASE_DELAY_SECONDS * attempt)


def _find_latest_customer_message_index(
    ticket: dict[str, Any],
    message: str,
    created_at: str,
) -> int | None:
    expected_content = str(message).strip()
    expected_created_at = str(created_at).strip()
    messages = ticket.get("messages", [])
    if not isinstance(messages, list):
        return None
    for index in range(len(messages) - 1, -1, -1):
        item = messages[index]
        if str(item.get("role", "")).strip().lower() != "customer":
            continue
        content = str(item.get("content", "")).strip()
        ts = str(item.get("created_at", "")).strip()
        if expected_content and content != expected_content:
            return None
        if expected_created_at and ts and not _timestamps_match(ts, expected_created_at):
            return None
        return index
    return None


def _is_latest_customer_message(ticket: dict[str, Any], message: str, created_at: str) -> bool:
    return _find_latest_customer_message_index(ticket, message, created_at) is not None


def _find_matching_customer_message_index(
    ticket: dict[str, Any],
    message: str,
    created_at: str,
) -> int | None:
    expected_content = str(message).strip()
    expected_created_at = str(created_at).strip()
    messages = ticket.get("messages", [])
    if not isinstance(messages, list):
        return None
    for index in range(len(messages) - 1, -1, -1):
        item = messages[index]
        if str(item.get("role", "")).strip().lower() != "customer":
            continue
        if expected_content and str(item.get("content", "")).strip() != expected_content:
            continue
        ts = str(item.get("created_at", "")).strip()
        if expected_created_at and ts and not _timestamps_match(ts, expected_created_at):
            continue
        return index
    return None


def _has_matching_customer_message(ticket: dict[str, Any], message: str, created_at: str) -> bool:
    return _find_matching_customer_message_index(ticket, message, created_at) is not None


def _parse_iso_datetime(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamps_match(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    actual_dt = _parse_iso_datetime(actual)
    expected_dt = _parse_iso_datetime(expected)
    if actual_dt is not None and expected_dt is not None:
        return (
            abs((actual_dt - expected_dt).total_seconds())
            <= MESSAGE_TIMESTAMP_TOLERANCE_SECONDS
        )
    return actual[:19] == expected[:19]


def _duration_between_timestamps_ms(start: str | None, end: str | None) -> float | None:
    start_dt = _parse_iso_datetime(start or "")
    end_dt = _parse_iso_datetime(end or "")
    if start_dt is None or end_dt is None:
        return None
    return round(max((end_dt - start_dt).total_seconds() * 1000, 0.0), 2)


def _latest_admission_metrics(ticket_id: str, message_created_at: str) -> dict[str, Any]:
    del ticket_id, message_created_at
    return {}


def _task_route_context(task: dict[str, Any]) -> list[dict[str, str]]:
    context = task.get("route_context_tail")
    if not isinstance(context, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in context:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "system")).strip().lower() or "system"
        content = " ".join(str(item.get("content", "")).split()).strip()
        if not content:
            continue
        normalized.append({"role": role, "content": content})
    return normalized


def _task_latest_assistant_message(task: dict[str, Any]) -> dict[str, Any] | None:
    message = task.get("latest_assistant_message")
    if not isinstance(message, dict):
        return None
    payload = {
        "role": str(message.get("role", "assistant")).strip().lower() or "assistant",
        "content": " ".join(str(message.get("content", "")).split()).strip(),
        "workflow_action": str(message.get("workflow_action") or "").strip(),
        "answer_route": str(message.get("answer_route") or "").strip(),
        "route_reason": str(message.get("route_reason") or "").strip(),
    }
    assistant_message_source = str(message.get("assistant_message_source") or "").strip()
    if assistant_message_source:
        payload["assistant_message_source"] = assistant_message_source
    if bool(message.get("supports_customer_resolution")):
        payload["supports_customer_resolution"] = True
    return payload


def _build_worker_auto_resolved_by_customer_confirmation_event(
    *,
    ticket_id: str,
    status: str,
    message_created_at: str | None,
    answer_created_at: str | None,
) -> dict[str, Any]:
    return {
        "event": "ticket_auto_resolved_by_customer_confirmation",
        "ticket_id": ticket_id,
        "status": normalize_ticket_status(status),
        "workflow_action": "resolve_ticket",
        "answer_route": "workflow",
        "route_family": "ticket_resolution",
        "execution_action": "resolve_ticket",
        "tooling_profile": "deterministic_resolution",
        "route_reason": "customer_confirmed_resolved",
        "message_created_at": str(message_created_at or "").strip() or None,
        "assistant_message_created_at": str(answer_created_at or "").strip() or None,
        "created_at": now_iso(),
    }


def _is_task_cancelled(ticket_id: str, message_created_at: str) -> bool:
    expected_created_at = str(message_created_at or "").strip()
    if not expected_created_at:
        return False

    events = _call_ticket_repository(
        "list_ticket_events",
        lambda: ticket_repository.list_ticket_events(ticket_id=ticket_id, limit=200),
    )
    for row in events:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        event_type = str(row.get("event_type") or payload.get("event") or "").strip().lower()
        if event_type != "ticket_ai_generation_stopped":
            continue
        cancelled_created_at = str(payload.get("message_created_at") or "").strip()
        if cancelled_created_at and cancelled_created_at == expected_created_at:
            return True
    return False


def _load_ticket_with_retry(
    ticket_id: str,
    expected_message: str,
    expected_created_at: str,
) -> tuple[dict[str, Any] | None, int, bool]:
    """Retry short-lived lookup misses until latest customer message is persisted."""
    attempt = 0
    ticket = _call_ticket_repository(
        "get_ticket",
        lambda: ticket_repository.get_ticket(ticket_id),
    )
    while True:
        if ticket is not None and _is_latest_customer_message(
            ticket,
            expected_message,
            expected_created_at,
        ):
            return ticket, attempt, True
        if attempt >= TICKET_LOOKUP_RETRY_MAX:
            break
        attempt += 1
        time.sleep(TICKET_LOOKUP_RETRY_BASE_DELAY_SECONDS * attempt)
        ticket = _call_ticket_repository(
            "get_ticket",
            lambda: ticket_repository.get_ticket(ticket_id),
        )
    return ticket, attempt, False


def _load_ticket_message_with_retry(
    ticket_id: str,
    expected_message: str,
    expected_created_at: str,
) -> tuple[dict[str, Any] | None, int, bool]:
    attempt = 0
    ticket = _call_ticket_repository(
        "get_ticket",
        lambda: ticket_repository.get_ticket(ticket_id),
    )
    while True:
        if ticket is not None and _has_matching_customer_message(
            ticket,
            expected_message,
            expected_created_at,
        ):
            return ticket, attempt, True
        if attempt >= TICKET_LOOKUP_RETRY_MAX:
            break
        attempt += 1
        time.sleep(TICKET_LOOKUP_RETRY_BASE_DELAY_SECONDS * attempt)
        ticket = _call_ticket_repository(
            "get_ticket",
            lambda: ticket_repository.get_ticket(ticket_id),
        )
    return ticket, attempt, False


def _find_existing_worker_response(
    ticket: dict[str, Any],
    customer_message: str,
    message_created_at: str,
) -> dict[str, Any] | None:
    customer_index = _find_latest_customer_message_index(
        ticket,
        customer_message,
        message_created_at,
    )
    if customer_index is None:
        return None
    assistant_messages: list[dict[str, Any]] = []
    for item in ticket.get("messages", [])[customer_index + 1 :]:
        if str(item.get("role", "")).strip().lower() != "assistant":
            continue
        if not _assistant_message_looks_like_persisted_response(item):
            continue
        assistant_messages.append(item)
    if not assistant_messages:
        return None
    return assistant_messages[-1]


def _assistant_message_looks_like_persisted_response(message: dict[str, Any]) -> bool:
    if not isinstance(message, dict):
        return False
    if isinstance(message.get("sources"), list) and message.get("sources"):
        return True
    if isinstance(message.get("citations"), list) and message.get("citations"):
        return True
    for key in (
        "workflow_action",
        "answer_route",
        "scope_label",
        "route_reason",
        "execution_action",
        "assistant_message_source",
        "client_agent_run_id",
        "client_agent_runtime_status",
    ):
        if str(message.get(key) or "").strip():
            return True
    if bool(message.get("supports_customer_resolution")):
        return True
    return False


def _find_latest_assistant_message_before_index(
    ticket: dict[str, Any],
    customer_index: int,
) -> dict[str, Any] | None:
    messages = ticket.get("messages", [])
    if not isinstance(messages, list):
        return None
    safe_end = min(max(customer_index, 0), len(messages))
    for index in range(safe_end - 1, -1, -1):
        item = messages[index]
        if not isinstance(item, dict):
            continue
        if str(item.get("role", "")).strip().lower() != "assistant":
            continue
        content = " ".join(str(item.get("content", "")).split()).strip()
        if not content:
            continue
        payload: dict[str, Any] = {
            "role": "assistant",
            "content": content,
            "workflow_action": str(item.get("workflow_action") or "").strip(),
            "answer_route": str(item.get("answer_route") or "").strip(),
            "route_reason": str(item.get("route_reason") or "").strip(),
        }
        assistant_message_source = str(item.get("assistant_message_source") or "").strip()
        if assistant_message_source:
            payload["assistant_message_source"] = assistant_message_source
        if bool(item.get("supports_customer_resolution")):
            payload["supports_customer_resolution"] = True
        return payload
    return None


def _has_assistant_message_after_index(ticket: dict[str, Any], customer_index: int) -> bool:
    messages = ticket.get("messages", [])
    if not isinstance(messages, list):
        return False
    for item in messages[customer_index + 1 :]:
        if not isinstance(item, dict):
            continue
        if str(item.get("role", "")).strip().lower() == "assistant":
            return True
    return False


def _event_matches_message_created_at(event: dict[str, Any], message_created_at: str) -> bool:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return False
    candidate = str(payload.get("message_created_at") or "").strip()
    if not candidate:
        return False
    return _timestamps_match(candidate, message_created_at)


def _find_latest_ticket_event_for_message(
    events: list[dict[str, Any]],
    *,
    event_types: tuple[str, ...],
    message_created_at: str,
) -> dict[str, Any] | None:
    normalized_types = {str(item or "").strip() for item in event_types if str(item or "").strip()}
    if not normalized_types:
        return None
    for event in events:
        if str(event.get("event_type") or "").strip() not in normalized_types:
            continue
        if _event_matches_message_created_at(event, message_created_at):
            return event
    return None


def _pending_ticket_query_keys(queue: SyncRedisTaskQueue) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for task in queue.list_pending_tasks():
        if str(task.get("task_type") or "").strip().lower() != "ticket_query":
            continue
        ticket_id = str(task.get("ticket_id") or "").strip()
        message_created_at = str(task.get("message_created_at") or "").strip()
        if ticket_id and message_created_at:
            keys.add((ticket_id, message_created_at))
    return keys


def _recover_stale_ticket_query_tasks_on_worker_start(
    queue: SyncRedisTaskQueue,
    *,
    worker_started_at: str,
) -> int:
    worker_started_dt = _parse_iso_datetime(worker_started_at)
    if worker_started_dt is None:
        return 0
    pending_keys = _pending_ticket_query_keys(queue)
    tickets = _call_ticket_repository(
        "list_tickets",
        lambda: ticket_repository.list_tickets(include_messages=True),
    )
    recovered_count = 0
    for ticket in tickets:
        if normalize_ticket_status(ticket.get("status")) == RESOLVED_STATUS:
            continue
        ticket_id = str(ticket.get("ticket_id") or "").strip()
        if not ticket_id:
            continue
        customer_index = _find_latest_customer_message_index(ticket, "", "")
        if customer_index is None:
            continue
        messages = ticket.get("messages", [])
        if not isinstance(messages, list) or customer_index >= len(messages):
            continue
        latest_customer = messages[customer_index]
        if not isinstance(latest_customer, dict):
            continue
        customer_message = " ".join(str(latest_customer.get("content", "")).split()).strip()
        message_created_at = str(latest_customer.get("created_at") or "").strip()
        message_created_dt = _parse_iso_datetime(message_created_at)
        if not customer_message or message_created_dt is None:
            continue
        if message_created_dt >= worker_started_dt:
            continue
        if _has_assistant_message_after_index(ticket, customer_index):
            continue
        if (ticket_id, message_created_at) in pending_keys:
            continue
        events = _call_ticket_repository(
            "list_ticket_events",
            lambda ticket_id=ticket_id: ticket_repository.list_ticket_events(ticket_id, limit=50),
        )
        processing_event = _find_latest_ticket_event_for_message(
            events,
            event_types=("ticket_ai_processing",),
            message_created_at=message_created_at,
        )
        if processing_event is None:
            continue
        processing_created_at = str(processing_event.get("created_at") or "").strip()
        processing_created_dt = _parse_iso_datetime(processing_created_at)
        if processing_created_dt is None or processing_created_dt >= worker_started_dt:
            continue
        completion_event = _find_latest_ticket_event_for_message(
            events,
            event_types=("ticket_ai_response_ready", "ticket_ai_generation_stopped"),
            message_created_at=message_created_at,
        )
        if completion_event is not None:
            continue
        recovery_app_build_ref = str(get_app_build_info().get("ref") or "").strip() or None
        recovery_task = build_query_task(
            ticket_id=ticket_id,
            customer_message=customer_message,
            message_created_at=message_created_at,
            app_build_ref=recovery_app_build_ref,
            customer_id=str(ticket.get("customer_id") or "").strip() or None,
            requester=str(ticket.get("requester") or "").strip() or None,
            ticket_subject=str(ticket.get("subject") or "").strip() or None,
            product=str(ticket.get("product") or "").strip() or None,
            product_selection_state=(
                dict(ticket.get("product_selection_state"))
                if isinstance(ticket.get("product_selection_state"), dict)
                else None
            ),
            route_context_tail=messages[max(0, len(messages) - 6) :],
            client_intake_state=(
                dict(ticket.get("client_intake_state"))
                if isinstance(ticket.get("client_intake_state"), dict)
                else None
            ),
            latest_assistant_message=_find_latest_assistant_message_before_index(ticket, customer_index),
            current_ticket_status=normalize_ticket_status(ticket.get("status")),
            ticket_updated_at=str(ticket.get("updated_at") or "").strip() or None,
            processing_mode="worker_startup_recovery",
        )
        recovery_task["recovered_from_worker_startup"] = True
        recovery_task["original_processing_created_at"] = processing_created_at
        if not queue.enqueue(recovery_task):
            LOGGER.warning(
                "Worker startup recovery could not requeue ticket %s for message %s",
                ticket_id,
                message_created_at,
            )
            continue
        recovery_event = {
            "event": "ticket_ai_recovery_queued",
            "ticket_id": ticket_id,
            "status": normalize_ticket_status(ticket.get("status")),
            "message_created_at": message_created_at,
            "created_at": now_iso(),
            "parallel_mode": "worker_startup_recovery",
            "recovery_reason": "missing_async_completion_after_worker_restart",
            "worker_started_at": worker_started_at,
            "original_processing_created_at": processing_created_at,
        }
        _call_ticket_repository(
            "record_event",
            lambda ticket_id=ticket_id, recovery_event=recovery_event: ticket_repository.record_event(
                ticket_id,
                recovery_event["event"],
                recovery_event,
            ),
        )
        pending_keys.add((ticket_id, message_created_at))
        recovered_count += 1
    return recovered_count


def _schedule_ticket_task_retry(
    queue: SyncRedisTaskQueue,
    task: dict[str, Any],
    exc: BaseException,
) -> bool:
    if not _is_retryable_ticket_storage_error(exc):
        return False
    current_retry_count = _safe_non_negative_int(task.get("worker_retry_count"), 0)
    if current_retry_count >= TICKET_TASK_RETRY_MAX:
        return False
    next_retry_count = current_retry_count + 1
    delay_seconds = TICKET_TASK_RETRY_BASE_DELAY_SECONDS * next_retry_count
    time.sleep(delay_seconds)
    retry_task = dict(task)
    retry_task["worker_retry_count"] = next_retry_count
    retry_task["last_error"] = str(exc)
    retry_task["last_retry_at"] = now_iso()
    if not queue.enqueue(retry_task):
        return False
    LOGGER.warning(
        "Worker requeued ticket task %s after transient storage failure (retry %s/%s): %s",
        str(task.get("ticket_id") or "").strip(),
        next_retry_count,
        TICKET_TASK_RETRY_MAX,
        exc,
    )
    return True


def _process_ticket_query(bus: SyncRedisEventBus, task: dict[str, Any]) -> None:
    ticket_id = str(task.get("ticket_id", "")).strip()
    customer_message = str(task.get("customer_message", "")).strip()
    message_created_at = str(task.get("message_created_at", "")).strip()
    task_app_build_ref = str(task.get("app_build_ref") or "").strip() or None
    execution_app_build_ref = str(get_app_build_info().get("ref") or "").strip() or None
    if not ticket_id or not customer_message:
        return
    task_dequeued_at = now_iso()
    queue_wait_ms = _duration_between_timestamps_ms(
        str(task.get("created_at") or "").strip() or None,
        task_dequeued_at,
    )
    message_to_task_dequeued_ms = _duration_between_timestamps_ms(message_created_at, task_dequeued_at)
    if _is_task_cancelled(ticket_id, message_created_at):
        LOGGER.info("Worker skipped cancelled task for ticket %s", ticket_id)
        return

    route_context = _task_route_context(task)
    main_agent_started_at = now_iso()
    dequeued_to_main_agent_started_ms = _duration_between_timestamps_ms(
        task_dequeued_at,
        main_agent_started_at,
    )
    orchestration_result = _orchestrate_worker_support_message(
        customer_message,
        ticket_id=ticket_id,
        customer_id=str(task.get("customer_id") or "").strip() or None,
        requester=str(task.get("requester") or "").strip() or None,
        ticket_subject=str(task.get("ticket_subject") or "").strip() or None,
        ticket_context=route_context[-6:],
        message_created_at=message_created_at,
        product=str(task.get("product") or "").strip() or None,
        product_selection_state=(
            dict(task.get("product_selection_state"))
            if isinstance(task.get("product_selection_state"), dict)
            else None
        ),
        client_intake_state=(
            dict(task.get("client_intake_state"))
            if isinstance(task.get("client_intake_state"), dict)
            else None
        ),
        latest_assistant_message=_task_latest_assistant_message(task),
        current_ticket_status=str(task.get("current_ticket_status") or "").strip() or None,
    )
    main_agent_completed_at = now_iso()
    main_agent_total_ms = _duration_between_timestamps_ms(
        main_agent_started_at,
        main_agent_completed_at,
    )
    if (
        isinstance(orchestration_result, tuple)
        and len(orchestration_result) == 2
        and isinstance(orchestration_result[1], dict)
    ):
        execution, execution_diagnostics = orchestration_result
    else:
        execution = orchestration_result
        execution_diagnostics = {
            "parallel_mode": "main_agent",
            "route_latency_ms": 0.0,
            "route_final_action": None,
            "route_result_source": "main_agent_fallback",
            "rag_started_at": None,
            "rag_finished_at": None,
            "rag_cancelled": False,
            "rag_cancel_stage": None,
        }
    answer = execution.answer
    sources = list(execution.sources)
    citations = [dict(item) for item in execution.citations]
    if _is_task_cancelled(ticket_id, message_created_at):
        LOGGER.info("Worker dropped result for cancelled task %s", ticket_id)
        return
    refreshed_ticket = _call_ticket_repository(
        "get_ticket",
        lambda: ticket_repository.get_ticket(ticket_id),
    )
    if refreshed_ticket is None:
        LOGGER.warning("Worker dropped result: ticket disappeared (%s)", ticket_id)
        return
    ensure_ticket_defaults(refreshed_ticket)
    if not _is_latest_customer_message(refreshed_ticket, customer_message, message_created_at):
        LOGGER.info("Worker dropped stale result for ticket %s", ticket_id)
        return

    ticket = refreshed_ticket
    existing_response = _find_existing_worker_response(ticket, customer_message, message_created_at)
    needs_engineer_input = False
    investigation_result: dict[str, Any] | None = None
    engineer_case: dict[str, Any] | None = None
    engineer_case_created = False
    answer_saved_at: str | None = None
    if existing_response is not None:
        LOGGER.info(
            "Worker detected an existing final assistant response for ticket %s and skipped duplicate save",
            ticket_id,
        )
        answer = str(existing_response.get("content") or answer)
        sources = existing_response.get("sources") if isinstance(existing_response.get("sources"), list) else sources
        citations = (
            existing_response.get("citations")
            if isinstance(existing_response.get("citations"), list)
            else citations
        )
        answer_saved_at = str(existing_response.get("created_at") or "").strip() or None
        needs_engineer_input = (
            normalize_ticket_status(ticket.get("status")) == INVESTIGATING_STATUS
            or _active_engineer_case_payload(ticket) is not None
        )
    else:
        initial_message_count = len(ticket.get("messages", []))
        resolved_product = str(execution_diagnostics.get("resolved_product") or "").strip() or None
        resolved_product_selection_state = (
            dict(execution_diagnostics.get("product_selection_state"))
            if isinstance(execution_diagnostics.get("product_selection_state"), dict)
            else None
        )
        if resolved_product is not None:
            ticket["product"] = resolved_product
        ticket["product_selection_state"] = resolved_product_selection_state
        if bool(execution_diagnostics.get("product_changed")):
            ticket["client_intake_state"] = None
        execution_client_intake_state = (
            dict(getattr(execution, "client_intake_state"))
            if isinstance(getattr(execution, "client_intake_state", None), dict)
            else None
        )
        execution_client_agent_runtime_state = (
            dict(getattr(execution, "client_agent_runtime_state"))
            if isinstance(getattr(execution, "client_agent_runtime_state", None), dict)
            else None
        )
        if execution_client_agent_runtime_state is not None:
            build_provenance = (
                dict(execution_client_agent_runtime_state.get("build_provenance"))
                if isinstance(execution_client_agent_runtime_state.get("build_provenance"), dict)
                else {}
            )
            if task_app_build_ref:
                build_provenance["task_app_build_ref"] = task_app_build_ref
            if execution_app_build_ref:
                build_provenance["execution_app_build_ref"] = execution_app_build_ref
            if build_provenance:
                execution_client_agent_runtime_state["build_provenance"] = build_provenance
            ticket["client_agent_runtime_state"] = execution_client_agent_runtime_state
        execution_workflow_action = str(getattr(execution, "workflow_action", "") or "").strip()
        execution_route_payload = build_execution_route_payload(execution)
        active_engineer_case_payload = _active_engineer_case_payload(ticket)
        if execution_workflow_action == "resolve_ticket" and isinstance(active_engineer_case_payload, dict):
            engineer_case = _engineer_case_payload_to_record(active_engineer_case_payload)
            case_context = build_engineer_case_context(ticket, engineer_case)
            _, investigation_messages = close_case_context_active_investigation(
                case_context,
                now_value=now_iso(),
                system_note="Investigation closed because the customer confirmed the issue is resolved.",
            )
            engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
            engineer_case["status"] = RESOLVED_STATUS
            engineer_case["investigation_state"] = "closed"
            investigation_result = {
                "created": False,
                "new_internal_messages": investigation_messages,
            }
            ticket["active_engineer_case_id"] = None
        if execution.needs_investigating:
            ticket["client_intake_state"] = execution_client_intake_state
            engineer_case, engineer_case_created = _prepare_engineer_case_for_ticket(
                ticket,
                case_status=INVESTIGATING_STATUS,
                trigger_source="worker_async_rag",
                trigger_reason=str(execution.investigation_reason or "rag_insufficient_evidence"),
                now_value=now_iso(),
            )
            case_context = build_engineer_case_context(ticket, engineer_case)
            opening_context = build_investigation_opening_context(
                case_context,
                trigger_reason=str(execution.investigation_reason or "rag_insufficient_evidence"),
                rag_answer=execution.answer,
                sources=list(execution.sources),
                citations=[dict(item) for item in execution.citations],
            )
            investigation_result = start_or_refresh_investigation(
                case_context,
                trigger_reason=str(execution.investigation_reason or "rag_insufficient_evidence"),
                trigger_source="worker_async_rag",
                now_value=now_iso(),
                next_status=INVESTIGATING_STATUS,
                opening_context=opening_context,
                ai_turn_builder=generate_investigation_ai_turn,
                execution_context={
                    **execution_route_payload,
                    "answer": execution.answer,
                    "sources": list(execution.sources),
                    "citations": [dict(item) for item in execution.citations],
                    "evidence_summary": dict(execution.evidence_summary or {}) or {},
                },
            )
            engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
            if engineer_case_created:
                engineer_case["title"] = derive_engineer_case_title(
                    ticket,
                    handoff_packet=case_context.get("engineer_handoff_packet"),
                    engineer_agent_state=case_context.get("engineer_agent_state"),
                )
            answer = str(investigation_result.get("public_reply") or "").strip()
            sources = []
            citations = []
            needs_engineer_input = True
            ticket["status"] = INVESTIGATING_STATUS
            ticket["active_engineer_case_id"] = str(engineer_case.get("engineer_case_id") or "").strip() or None
            ticket["client_intake_state"] = None
        else:
            ticket["status"] = resolve_next_ticket_status(ticket.get("status"), execution.next_status)
            ticket["client_intake_state"] = execution_client_intake_state

        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": answer,
            "created_at": now_iso(),
        }
        answer_saved_at = str(assistant_message.get("created_at") or "").strip() or None
        assistant_message["answer_route"] = execution.answer_route
        assistant_message["scope_label"] = execution.scope_label
        assistant_message["route_family"] = execution.route_family
        assistant_message["execution_action"] = execution.execution_action
        assistant_message["tooling_profile"] = execution.tooling_profile
        assistant_message["route_reason"] = execution.route_reason
        assistant_message["route_confidence"] = round(float(execution.route_confidence), 4)
        assistant_message["search_used"] = bool(execution.search_used)
        assistant_message["matched_signals"] = list(execution.matched_signals)
        assistant_message["workflow_action"] = execution_workflow_action
        if isinstance(execution_route_payload.get("retrieval_plan_snapshot"), dict):
            assistant_message["retrieval_plan_snapshot"] = dict(
                execution_route_payload.get("retrieval_plan_snapshot") or {}
            )
        if str(getattr(execution, "run_id", "") or "").strip():
            assistant_message["client_agent_run_id"] = str(getattr(execution, "run_id") or "").strip()
        if isinstance(execution_client_agent_runtime_state, dict):
            assistant_message["client_agent_runtime_status"] = str(execution_client_agent_runtime_state.get("status") or "").strip()
        if isinstance(execution_client_intake_state, dict):
            assistant_message["client_intake_phase"] = str(execution_client_intake_state.get("phase") or "").strip()
            assistant_message["client_intake_ready_for_engineer_ticket"] = bool(
                execution_client_intake_state.get("ready_for_engineer_ticket")
            )
            assistant_message["client_intake_missing_information"] = list(
                execution_client_intake_state.get("missing_information") or []
            )
        if sources:
            assistant_message["sources"] = sources
        if citations:
            assistant_message["citations"] = citations
        ticket["messages"].append(assistant_message)

        ticket["updated_at"] = now_iso()
        new_messages = ticket.get("messages", [])[initial_message_count:]
        _call_ticket_repository(
            "save_ticket",
            lambda: ticket_repository.save_ticket(ticket, new_messages=new_messages),
        )
        answer_saved_at = now_iso()
        if engineer_case is not None:
            ticket["engineer_case_count"] = max(
                int(ticket.get("engineer_case_count") or 0),
                int(engineer_case.get("case_sequence") or 0),
            )
            _call_ticket_repository(
                "save_ticket",
                lambda: ticket_repository.save_ticket(ticket, new_messages=[]),
            )
            _call_ticket_repository(
                "save_engineer_case",
                lambda: ticket_repository.save_engineer_case(
                    engineer_case=engineer_case,
                    new_messages=investigation_result.get("new_internal_messages") if investigation_result else [],
                ),
            )

    response_ready_created_at = now_iso()
    main_agent_to_answer_saved_ms = _duration_between_timestamps_ms(
        main_agent_completed_at,
        answer_saved_at,
    )
    answer_saved_to_response_ready_ms = _duration_between_timestamps_ms(
        answer_saved_at,
        response_ready_created_at,
    )
    event = {
        "event": "ticket_ai_response_ready",
        "ticket_id": ticket_id,
        "status": ticket["status"],
        "message": answer[:200],
        "message_created_at": message_created_at,
        "created_at": response_ready_created_at,
        "answer_route": execution.answer_route,
        "scope_label": execution.scope_label,
        "route_family": execution.route_family,
        "execution_action": execution.execution_action,
        "tooling_profile": execution.tooling_profile,
        "route_reason": execution.route_reason,
        "route_confidence": round(float(execution.route_confidence), 4),
        "search_used": bool(execution.search_used),
        "matched_signals": list(execution.matched_signals),
        "parallel_mode": execution_diagnostics.get("parallel_mode"),
        "api_persist_latency_ms": task.get("api_persist_latency_ms"),
        "api_return_latency_ms": task.get("api_return_latency_ms"),
        "load_ticket_ms": task.get("load_ticket_ms"),
        "save_ticket_ms": task.get("save_ticket_ms"),
        "record_ticket_created_event_ms": task.get("record_ticket_created_event_ms"),
        "enqueue_ticket_query_ms": task.get("enqueue_ticket_query_ms"),
        "enqueue_sentiment_ms": task.get("enqueue_sentiment_ms"),
        "task_dequeued_at": task_dequeued_at,
        "message_to_task_dequeued_ms": message_to_task_dequeued_ms,
        "queue_wait_ms": queue_wait_ms,
        "main_agent_started_at": main_agent_started_at,
        "dequeued_to_main_agent_started_ms": dequeued_to_main_agent_started_ms,
        "main_agent_completed_at": main_agent_completed_at,
        "main_agent_total_ms": main_agent_total_ms,
        "main_agent_to_answer_saved_ms": main_agent_to_answer_saved_ms,
        "response_ready_dispatch_ms": _duration_between_timestamps_ms(
            main_agent_completed_at,
            response_ready_created_at,
        ),
        "answer_saved_to_response_ready_ms": answer_saved_to_response_ready_ms,
        "route_latency_ms": execution_diagnostics.get("route_latency_ms"),
        "route_final_action": execution_diagnostics.get("route_final_action") or execution.execution_action,
        "route_result_source": execution_diagnostics.get("route_result_source"),
        "rag_started_at": execution_diagnostics.get("rag_started_at"),
        "rag_finished_at": execution_diagnostics.get("rag_finished_at"),
        "rag_cancelled": bool(execution_diagnostics.get("rag_cancelled")),
        "rag_cancel_stage": execution_diagnostics.get("rag_cancel_stage"),
        "workflow_action": str(getattr(execution, "workflow_action", "") or "").strip(),
        "client_agent_run_id": str(getattr(execution, "run_id", "") or "").strip() or None,
        "client_agent_runtime_status": str(
            (((getattr(execution, "client_agent_runtime_state", None) or {}) if isinstance(getattr(execution, "client_agent_runtime_state", None), dict) else {}).get("status") or "")
        ).strip()
        or None,
    }
    if task_app_build_ref:
        event["task_app_build_ref"] = task_app_build_ref
    if execution_app_build_ref:
        event["execution_app_build_ref"] = execution_app_build_ref
    execution_client_intake_state = (
        dict(getattr(execution, "client_intake_state"))
        if isinstance(getattr(execution, "client_intake_state", None), dict)
        else None
    )
    if isinstance(execution_client_intake_state, dict):
        event["client_intake_phase"] = str(execution_client_intake_state.get("phase") or "").strip()
        event["client_intake_ready_for_engineer_ticket"] = bool(
            execution_client_intake_state.get("ready_for_engineer_ticket")
        )
        event["client_intake_missing_information"] = list(
            execution_client_intake_state.get("missing_information") or []
        )
    # Publish the ready signal immediately after the assistant message is durable so
    # event-log writes do not add avoidable tail latency to the client-visible path.
    _publish(bus, ["client"], build_client_sync_event(ticket, event["event"]))
    _publish(bus, ["engineer", "dashboard"], event)
    _call_ticket_repository(
        "record_event",
        lambda: ticket_repository.record_event(ticket_id, event["event"], event),
    )
    _call_ticket_repository(
        "record_ticket_agent_runtime_events",
        lambda: _record_ticket_agent_runtime_events(execution),
    )
    if str(getattr(execution, "workflow_action", "") or "").strip() == "resolve_ticket":
        auto_resolved_event = _build_worker_auto_resolved_by_customer_confirmation_event(
            ticket_id=ticket_id,
            status=ticket["status"],
            message_created_at=message_created_at,
            answer_created_at=answer_saved_at,
        )
        _call_ticket_repository(
            "record_event",
            lambda: ticket_repository.record_event(
                ticket_id,
                auto_resolved_event["event"],
                auto_resolved_event,
            ),
        )
        if isinstance(engineer_case, dict) and str(engineer_case.get("engineer_case_id") or "").strip():
            engineer_auto_resolved_event = {
                **auto_resolved_event,
                "ticket_id": str(engineer_case.get("engineer_case_id") or ""),
                "client_ticket_id": ticket_id,
                "engineer_case_id": str(engineer_case.get("engineer_case_id") or ""),
            }
            _call_ticket_repository(
                "record_engineer_case_event",
                lambda: ticket_repository.record_engineer_case_event(
                    str(engineer_case.get("engineer_case_id") or ""),
                    engineer_auto_resolved_event["event"],
                    engineer_auto_resolved_event,
                ),
            )
        _publish(bus, ["engineer", "dashboard"], auto_resolved_event)
    if investigation_result is not None:
        investigation_event = _build_worker_investigation_event(
            ticket,
            engineer_case or {},
            created=bool(engineer_case_created or investigation_result.get("created")),
        )
        _call_ticket_repository(
            "record_event",
            lambda: ticket_repository.record_event(
                ticket_id,
                investigation_event["event"],
                investigation_event,
            ),
        )
        if investigation_event.get("engineer_case_id"):
            _call_ticket_repository(
                "record_engineer_case_event",
                lambda: ticket_repository.record_engineer_case_event(
                    investigation_event["engineer_case_id"],
                    investigation_event["event"],
                    investigation_event,
                ),
            )
        _publish(bus, ["engineer", "dashboard"], investigation_event)

    if needs_engineer_input:
        attention_message = ""
        if isinstance(engineer_case, dict):
            internal_messages = engineer_case.get("messages")
            if isinstance(internal_messages, list) and internal_messages:
                attention_message = str(internal_messages[-1].get("content") or "").strip()
        attention_event = {
            "event": "engineer_attention_required",
            "ticket_id": str(engineer_case.get("engineer_case_id") or ticket_id) if isinstance(engineer_case, dict) else ticket_id,
            "client_ticket_id": ticket_id,
            "engineer_case_id": str(engineer_case.get("engineer_case_id") or "") if isinstance(engineer_case, dict) else None,
            "status": ticket["status"],
            "message": attention_message or "Engineer attention required",
            "message_created_at": message_created_at,
            "created_at": now_iso(),
        }
        _call_ticket_repository(
            "record_event",
            lambda: ticket_repository.record_event(ticket_id, attention_event["event"], attention_event),
        )
        if attention_event.get("engineer_case_id"):
            _call_ticket_repository(
                "record_engineer_case_event",
                lambda: ticket_repository.record_engineer_case_event(
                    attention_event["engineer_case_id"],
                    attention_event["event"],
                    attention_event,
                ),
            )
        _publish(bus, ["engineer", "dashboard"], attention_event)
        _publish(bus, ["client"], build_client_sync_event(ticket, attention_event["event"]))


def _process_ticket_message_sentiment(bus: SyncRedisEventBus, task: dict[str, Any]) -> None:
    ticket_id = str(task.get("ticket_id", "")).strip()
    customer_message = str(task.get("customer_message", "")).strip()
    message_created_at = str(task.get("message_created_at", "")).strip()
    if not ticket_id or not customer_message or not message_created_at:
        return

    ticket, lookup_attempts, message_found = _load_ticket_message_with_retry(
        ticket_id,
        customer_message,
        message_created_at,
    )
    if ticket is None:
        LOGGER.warning(
            "Worker skipped sentiment tagging: ticket not found (%s) after %s retries",
            ticket_id,
            lookup_attempts,
        )
        return
    if not message_found:
        LOGGER.info(
            "Worker skipped stale sentiment task for ticket %s after %s retries",
            ticket_id,
            lookup_attempts,
        )
        return
    if lookup_attempts > 0:
        LOGGER.info(
            "Worker recovered delayed ticket/message state for sentiment task %s after %s retries",
            ticket_id,
            lookup_attempts,
        )

    sentiment_result, sentiment_label = classify_customer_message_sentiment(
        customer_message,
        classifier=classify_sentiment,
    )
    updated = _call_ticket_repository(
        "update_message_sentiment_label",
        lambda: ticket_repository.update_message_sentiment_label(
            ticket_id=ticket_id,
            role="customer",
            content=customer_message,
            created_at=message_created_at,
            sentiment_label=sentiment_label,
        ),
    )
    if not updated:
        return

    event = build_ticket_message_sentiment_event(
        ticket_id=ticket_id,
        message_created_at=message_created_at,
        sentiment_label=sentiment_label,
        sentiment_result=sentiment_result,
        created_at=now_iso(),
    )
    _call_ticket_repository(
        "record_event",
        lambda: ticket_repository.record_event(ticket_id, event["event"], event),
    )
    _publish(bus, ["engineer", "dashboard"], event)


def process_ticket_query_task(task: dict[str, Any]) -> None:
    bus = SyncRedisEventBus()
    try:
        _process_ticket_query(bus, dict(task))
    finally:
        bus.close()


def _process_worker_task(
    *,
    queue: SyncRedisTaskQueue,
    bus: SyncRedisEventBus,
    task: dict[str, Any],
) -> None:
    task_type = str(task.get("task_type", "")).strip().lower()
    if task_type == "ticket_query":
        try:
            _process_ticket_query(bus, task)
        except Exception as exc:
            if _schedule_ticket_task_retry(queue, task, exc):
                return
            LOGGER.exception("Worker failed to process ticket task: %s", exc)
        return
    if task_type == "ticket_message_sentiment":
        try:
            _process_ticket_message_sentiment(bus, task)
        except Exception as exc:
            if _schedule_ticket_task_retry(queue, task, exc):
                return
            LOGGER.exception("Worker failed to process sentiment task: %s", exc)
        return
    if task_type:
        LOGGER.warning("Worker ignored unknown task type: %s", task_type)
        return
    LOGGER.warning("Worker ignored task without task_type")


def _run_worker_consumer(task_types: tuple[str, ...], consumer_index: int) -> None:
    queue = SyncRedisTaskQueue(task_types=task_types)
    bus = SyncRedisEventBus()
    if not queue.is_enabled():
        LOGGER.error(
            "Worker consumer %s requires REDIS_URL and queue configuration for task types %s.",
            consumer_index,
            ",".join(task_types),
        )
        return
    LOGGER.info(
        "Worker consumer %s started for task types=%s.",
        consumer_index,
        ",".join(task_types),
    )
    try:
        while not SHUTTING_DOWN:
            task = queue.dequeue(timeout_seconds=5)
            if not task:
                continue
            _process_worker_task(queue=queue, bus=bus, task=task)
    finally:
        queue.close()
        bus.close()
        LOGGER.info("Worker consumer %s stopped.", consumer_index)


def run_worker() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _install_signal_handlers()
    worker_started_at = now_iso()

    try:
        if runtime_schema_check_enabled():
            check_runtime_schema()
        else:
            ticket_repository.initialize()
        initialize_prompt_runtime(
            ticket_repository,
            service_name=str(os.getenv("PROMPT_RUNTIME_SERVICE") or "worker"),
        )
    except Exception as exc:
        LOGGER.error("Worker failed to initialize ticket repository or Prompt Release: %s", exc)
        return 1

    task_types = _worker_task_types_from_env()
    concurrency = _worker_concurrency_from_env()
    queue = SyncRedisTaskQueue(task_types=task_types)
    if not queue.is_enabled():
        LOGGER.error("Worker requires REDIS_URL and ticket queue configuration.")
        return 1
    if "ticket_query" in task_types:
        try:
            recovered_count = _recover_stale_ticket_query_tasks_on_worker_start(
                queue,
                worker_started_at=worker_started_at,
            )
            if recovered_count > 0:
                LOGGER.warning(
                    "Worker startup recovery requeued %s stale ticket query task(s).",
                    recovered_count,
                )
        except Exception as exc:
            LOGGER.exception("Worker startup recovery failed: %s", exc)
    queue.close()

    LOGGER.info(
        "Worker started with task types=%s concurrency=%s.",
        ",".join(task_types),
        concurrency,
    )
    _start_billing_reply_poller_if_enabled()
    _start_engineer_assignment_poller_if_enabled()
    _start_account_reply_poller()
    _start_enablement_delivery_retry_poller(task_types)
    if concurrency <= 1:
        _run_worker_consumer(task_types, 1)
        LOGGER.info("Worker stopped.")
        return 0

    threads: list[threading.Thread] = []
    for consumer_index in range(1, concurrency + 1):
        thread = threading.Thread(
            target=_run_worker_consumer,
            args=(task_types, consumer_index),
            name=f"ticket-worker-{consumer_index}",
            daemon=False,
        )
        thread.start()
        threads.append(thread)

    while any(thread.is_alive() for thread in threads):
        for thread in threads:
            thread.join(timeout=0.5)

    LOGGER.info("Worker stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(run_worker())
