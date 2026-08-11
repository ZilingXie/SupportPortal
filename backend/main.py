from __future__ import annotations

import asyncio
import contextvars
import copy
import hashlib
import importlib.util
import json
import logging
import os
import random
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AliasChoices, BaseModel, Field
import psycopg

from backend.repositories.ticket_repository import (
    ACCOUNT_RERUN_RESET_AI_ONLY,
    ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
    AccountRerouteLeaseLostError,
    InMemoryTicketRepository,
    TicketRepository,
    create_ticket_repository,
)
from backend.repositories.asset_repository import (
    ASSET_STATUS_ATTACHED,
    ASSET_STATUS_UPLOADED,
    AssetRepository,
    InMemoryAssetRepository,
    create_asset_repository,
)
from backend.services.asset_storage import (
    build_asset_s3_key,
    create_asset_id,
    create_asset_storage,
    validate_asset_upload_request,
)
from backend.services.embedding_provider import (
    DEFAULT_PGVECTOR_TABLE,
    embedding_model_id,
    embedding_provider_name,
)
from backend.services.agora_service_events import get_agora_service_events_payload
from backend.services.billing_automation import build_billing_automation_result, send_billing_internal_email
from backend.services.detailed_invoice_field_extractor import DetailedInvoiceFieldExtraction
from backend.services.account_automation_handlers import account_automation_handler
from backend.services.account_billing_handlers import (
    account_billing_handler,
    account_billing_metadata,
)
from backend.services.account_verification_automation import build_account_verification_automation_result
from backend.services.account_verification_field_extractor import AccountVerificationFieldExtraction
from backend.services.account_suspension_field_extractor import (
    AccountSuspensionFieldExtraction,
    extract_account_suspension_fields,
)
from backend.services.account_full_reroute import reprocess_account_case
from backend.services.account_automation_reconciliation import (
    reconcile_automation_execution_failure,
    reconciliation_reason_code,
)
from backend.services.automation_routing import (
    AUTOMATED_ROUTE_FAMILY,
    automation_metadata,
    is_registered_automation,
)
from backend.services.enablement_automation import (
    build_enablement_automation_result_from_fields,
    send_enablement_internal_email,
)
from backend.services.enablement_field_extractor import (
    EnablementFieldExtraction,
    extract_enablement_fields,
)
from backend.services.quota_automation import (
    build_quota_automation_result,
    send_quota_internal_email,
)
from backend.services.quota_field_extractor import QuotaFieldExtraction, extract_quota_fields
from backend.services.billing_response_flow import (
    BILLING_RESPONSE_AI_FOLLOWUP_EVENT,
    BILLING_RESPONSE_EVENT,
    BillingResolutionValidationError,
    build_customer_followup_from_resolution,
    build_billing_internal_resolution_event,
    hash_billing_response_token,
    validate_billing_resolution_submission,
)
from backend.services.route_correction import (
    RouteCorrectionValidationError,
    validate_route_correction,
)
from backend.services.customer_reply_composer import (
    detect_customer_reply_language,
    ensure_customer_reply_email_style,
)
from backend.services.emotion_reply import build_initial_ack
from backend.services.engineer_agent import (
    build_engineer_agent_brief,
    normalize_engineer_agent_state,
)
from backend.services.engineer_guardrail_agent import (
    GUARDRAIL_VERSION,
    run_engineer_guardrail_final,
)
from backend.services.engineer_assignment import EngineerAssignmentService
from backend.services.workspace_invitations import WorkspaceInvitationService
from backend.services.workspace_schedules import (
    WORKSPACE_SCHEDULE_TIMEZONE,
    minutes_to_time,
    on_schedule_engineer_ids,
    time_to_minutes,
)
from backend.services.workspace_auth import (
    WorkspacePrincipal,
    create_workspace_access_token,
    hash_workspace_password,
    verify_workspace_access_token,
    verify_workspace_password,
)
from backend.services.account_admin import (
    AccountPersonaUnavailableError,
    account_automation_payload,
    environment_config_entries,
    route_execution_from_decision,
    routing_config_payload,
)
from backend.services.account_reply_jobs import (
    ACCOUNT_REPLY_PERSONA_PIPELINE,
    ACCOUNT_REPLY_PERSONA_PREPARING,
    ACCOUNT_REPLY_PERSONA_PUBLISHING,
    ACCOUNT_REPLY_PERSONA_QUEUED,
    ACCOUNT_REPLY_PERSONA_SCHEDULED,
    create_account_reply_job,
)
from backend.services.automation_persona import (
    AutomationPersonaError,
    build_automation_reply_facts,
    extract_automation_resolution_facts,
    render_automation_reply,
)
from backend.services.account_route_pipeline import (
    account_route_metadata,
    account_case_labels,
    classification_labels,
    classification_for_corrected_route,
    decide_account_route,
)
from backend.services.account_case_filters import (
    account_case_filter_definitions,
    normalize_account_case_filter,
)
from backend.services.agent_config import build_agent_config_payload
from backend.services.prompt_runtime import initialize_prompt_runtime, prompt_runtime_info, resolve_system_prompt
from backend.services.prompt_versioning import PromptVersionService
from backend.services.support_router_prompt import build_route_system_prompt, build_route_user_payload
from backend.services.case_memory_ledger import build_case_memory_ledger_record_from_feedback
from backend.services.engineer_hitl_review import build_engineer_auto_hitl_feedback
from backend.services.engineer_evidence_tools import (
    search_engineer_evidence,
    serialize_engineer_evidence_search_result,
)
from backend.services.engineer_cases import (
    apply_case_context_to_engineer_case,
    build_engineer_case_context,
    build_new_engineer_case,
    close_case_context_active_investigation,
    derive_engineer_case_title,
)
from backend.services.engineer_summary_agent import build_engineer_summary_packet
from backend.services.engineer_plan_agent import build_engineer_plan
from backend.services.engineer_execute_agent import execute_engineer_plan
from backend.services.engineer_review_agent import review_execution
from backend.services.engineer_replay_eval_dataset import build_engineer_replay_eval_item
from backend.services.investigation_flow import (
    COMMUNICATING_STATUS,
    ESCALATED_STATUS,
    INVESTIGATING_STATUS,
    INVESTIGATION_STATE_AWAITING_FINAL_APPROVAL,
    OPEN_STATUS,
    RESOLVED_STATUS,
    append_engineer_investigation_message,
    apply_investigation_confirmation,
    build_internal_message,
    build_investigation_opening_context,
    default_investigation_prompt as generate_investigation_ai_turn,
    ensure_ticket_investigation_defaults,
    normalize_ticket_status,
    surface_legacy_pending_question,
    start_or_refresh_investigation,
)
from backend.services.client_ticket_agent_runtime import (
    TicketExecutionResult,
    build_execution_route_payload,
    execute_client_ticket_agent_runtime,
    resolve_next_ticket_status,
)
from backend.services.app_build import get_app_build_info
from backend.services.dashboard_ticket_ops import (
    build_ticket_dashboard_metrics,
    normalize_ticket_dashboard_events,
)
from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import (
    CLIENT_ACK_SCENARIO,
    ENGINEER_HELPER_SCENARIO,
    get_config_warnings,
    resolve_model_profile,
)
from backend.services.event_bus import AsyncRedisEventBus
from backend.services.knowledge_monitoring import build_empty_knowledge_metrics
from backend.services.product_selection import resolve_support_product_context
from backend.services.openai_input_guardrail import (
    OpenAIInputGuardrailResult,
    evaluate_openai_input_guardrail,
)
from backend.services.rag_executor import build_sync_rag_executor
from backend.services.rag_qa import INSUFFICIENT_EVIDENCE_REPLY
from backend.services.rag_sufficiency_judge import judge_rag_answer_sufficiency
from backend.services.rag_service_client import (
    RagServiceClient,
    RagServiceError,
    async_to_thread,
)
from backend.services.support_router import (
    SupportResolution,
    SupportRouteDecision,
    decide_support_route,
    resolve_support_message as resolve_support_route_message,
)
from backend.services.ticket_resolution import (
    ASSISTANT_MESSAGE_SOURCE_ENGINEER_GUIDANCE,
    is_explicit_resolved_confirmation,
)
from backend.services.support_products import normalize_support_product
from backend.services.task_queue import AsyncRedisTaskQueue
from backend.services.ticket_title import derive_ticket_title
from backend.services.ticket_message_sentiment import (
    build_ticket_message_sentiment_event,
    classify_customer_message_sentiment,
)
from backend.services.troubleshooting_intake import evaluate_troubleshooting_intake
from backend.services.token_usage import aggregate_usage_ledger, resolve_ticket_family_identity

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "docs"
ROADMAP_DIR = DOCS_DIR / "roadmap"
ROADMAP_HTML = DOCS_DIR / "roadmap.html"
UI_DIR = BASE_DIR / "ui"
CLIENT_DIR = UI_DIR / "client-ui"
ACCOUNT_DIR = UI_DIR / "account-ui"
BILLING_RESPONSE_DIR = UI_DIR / "billing-response-ui"
ENGINEER_DIR = UI_DIR / "engineer-ui"
WORKSPACE_DIR = UI_DIR / "workspace-ui"
DASHBOARD_DIR = UI_DIR / "dashboard-ui"
SHARED_UI_DIR = UI_DIR / "shared-ui"

PRIMARY_RAG_WORKBENCH_PAGES = (
    "scorecard",
    "routing",
    "retrieval",
    "generation",
    "performance",
    "data-supply",
    "diagnosis",
    "review",
)

# Auto-load project environment variables from repository root.
load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)

ACTIVE_TICKET_STATUSES = {
    OPEN_STATUS,
    COMMUNICATING_STATUS,
    ESCALATED_STATUS,
    INVESTIGATING_STATUS,
}
DASHBOARD_TICKET_DETAIL_STATUSES = (
    INVESTIGATING_STATUS,
    ESCALATED_STATUS,
    COMMUNICATING_STATUS,
    RESOLVED_STATUS,
)
LOGGER = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _safe_int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _safe_float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


ASYNC_QUERY_ENABLED = _env_flag("ASYNC_QUERY_ENABLED", default=False)
INPUT_GUARDRAIL_ENABLED = _env_flag("INPUT_GUARDRAIL_ENABLED", default=False)
ENGINEER_MULTI_AGENT_ENABLED = _env_flag("ENGINEER_MULTI_AGENT_ENABLED", default=False)
KNOWLEDGE_OFFICIAL_MAX_BYTES = _safe_int_env("KNOWLEDGE_OFFICIAL_MAX_BYTES", 5 * 1024 * 1024)
KNOWLEDGE_ARTICLE_MAX_CHARS = _safe_int_env("KNOWLEDGE_ARTICLE_MAX_CHARS", 120000)
CLIENT_ACK_MAX_OUTPUT_TOKENS = _safe_int_env("CLIENT_ACK_MAX_OUTPUT_TOKENS", 32)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_billing_internal_email_attempt(
    *,
    action: str,
    message: str,
    ticket_id: str,
    billing_ticket_id: str,
    customer_email: str | None,
    requester: str | None,
    persona_instruction: str | None = None,
    already_requested_fields: list[str] | None = None,
) -> dict[str, Any]:
    billing_result = build_billing_automation_result(
        action=action,
        message=message,
        ticket_id=ticket_id,
        customer_email=customer_email,
        requester=requester,
        billing_ticket_id=billing_ticket_id,
        persona_instruction=persona_instruction,
        already_requested_fields=already_requested_fields,
        use_llm_field_extractor=True,
        generate_customer_reply=False,
    )
    missing_fields = list(billing_result.missing_fields)
    collected_fields = dict(billing_result.collected_fields)
    internal_email_payload: dict[str, Any] | None = None
    internal_email_to_send: dict[str, Any] | None = None
    internal_email_send_status = "not_ready"
    internal_email_send_reason = "missing_required_fields"

    if billing_result.internal_email:
        internal_email_to_send = dict(billing_result.internal_email or {})
        internal_email_payload = dict(internal_email_to_send)
        internal_email_send_status = "pending"
        internal_email_send_reason = ""

    return {
        "customer_reply": "",
        "missing_fields": missing_fields,
        "collected_fields": collected_fields,
        "internal_email_payload": internal_email_payload,
        "internal_email_to_send": internal_email_to_send,
        "internal_email_send_status": internal_email_send_status,
        "internal_email_send_reason": internal_email_send_reason,
        "requires_human_review": billing_result.requires_human_review,
        "field_extraction": billing_result.field_extraction,
    }


def _build_enablement_internal_email_attempt(
    *,
    message: str,
    ticket_subject: str,
    customer_messages: list[dict[str, Any]],
    ticket_id: str,
    account_case_id: str,
    customer_email: str | None,
    existing_fields: dict[str, Any] | None = None,
    already_requested_fields: list[str] | None = None,
) -> dict[str, Any]:
    extraction = extract_enablement_fields(
        ticket_subject=ticket_subject,
        customer_messages=customer_messages,
        existing_fields=existing_fields,
    )
    if extraction.requires_human_review:
        return {
            "customer_reply": "",
            "missing_fields": [],
            "collected_fields": dict(extraction.collected_fields),
            "internal_email_payload": None,
            "internal_email_to_send": None,
            "internal_email_send_status": "not_applicable",
            "internal_email_send_reason": f"field_extraction_{extraction.status}",
            "requires_human_review": True,
            "field_extraction": extraction,
        }
    enablement_result = build_enablement_automation_result_from_fields(
        collected_fields=extraction.collected_fields,
        missing_fields=extraction.missing_fields,
        missing_customer_reply=extraction.follow_up,
        customer_message=message,
        ticket_id=ticket_id,
        account_case_id=account_case_id,
        customer_email=customer_email,
        generate_customer_reply=False,
    )
    internal_email_to_send = dict(enablement_result.internal_email) if enablement_result.internal_email else None
    return {
        "customer_reply": "",
        "missing_fields": list(enablement_result.missing_fields),
        "collected_fields": dict(enablement_result.collected_fields),
        "internal_email_payload": dict(internal_email_to_send) if internal_email_to_send else None,
        "internal_email_to_send": internal_email_to_send,
        "internal_email_send_status": "pending" if internal_email_to_send else "not_ready",
        "internal_email_send_reason": "" if internal_email_to_send else "missing_required_fields",
        "requires_human_review": False,
        "field_extraction": extraction,
    }


def _build_quota_internal_email_attempt(
    *,
    message: str,
    ticket_subject: str,
    customer_messages: list[dict[str, Any]],
    ticket_id: str,
    account_case_id: str,
    customer_email: str | None,
    existing_fields: dict[str, Any] | None = None,
    follow_up_count: int = 0,
) -> dict[str, Any]:
    extraction = extract_quota_fields(
        ticket_subject=ticket_subject,
        customer_messages=customer_messages,
        existing_fields=existing_fields,
    )
    result = build_quota_automation_result(
        extraction=extraction,
        customer_message=message,
        ticket_id=ticket_id,
        account_case_id=account_case_id,
        customer_email=customer_email,
        follow_up_count=follow_up_count,
        generate_customer_reply=False,
    )
    internal_email_to_send = dict(result.internal_email) if result.internal_email else None
    return {
        "customer_reply": "",
        "missing_fields": list(result.missing_fields),
        "collected_fields": dict(result.collected_fields),
        "internal_email_payload": dict(internal_email_to_send) if internal_email_to_send else None,
        "internal_email_to_send": internal_email_to_send,
        "internal_email_send_status": "pending" if internal_email_to_send else "not_ready",
        "internal_email_send_reason": "" if internal_email_to_send else "missing_required_fields",
        "requires_human_review": extraction.requires_human_review,
        "field_extraction": extraction,
        "prompt_snapshots": {"quota_field_extractor": dict(extraction.prompt_snapshot)},
        "automation_context": {
            "handler": "quota",
            "extractor_version": extraction.audit_payload().get("prompt_version"),
            "extraction_status": extraction.status,
            "follow_up_count": result.follow_up_count,
            "proceed_with_missing_fields": result.proceed_with_missing_fields,
        },
    }


def _build_account_verification_internal_email_attempt(
    *,
    ticket_subject: str,
    customer_messages: list[dict[str, Any]],
    ticket_id: str,
    account_case_id: str,
    customer_email: str | None,
    existing_fields: dict[str, Any] | None = None,
    follow_up_count: int = 0,
) -> dict[str, Any]:
    result = build_account_verification_automation_result(
        ticket_subject=ticket_subject,
        customer_messages=customer_messages,
        ticket_id=ticket_id,
        account_case_id=account_case_id,
        customer_email=customer_email,
        existing_fields=existing_fields,
        follow_up_count=follow_up_count,
    )
    internal_email_to_send = dict(result.internal_email) if result.internal_email else None
    persisted_follow_up_count = result.follow_up_count
    follow_up_scheduled = False
    if result.missing_fields and not internal_email_to_send:
        persisted_follow_up_count = max(0, int(follow_up_count or 0))
        follow_up_scheduled = True
    return {
        "customer_reply": result.customer_reply,
        "missing_fields": list(result.missing_fields),
        "collected_fields": dict(result.collected_fields),
        "internal_email_payload": dict(internal_email_to_send) if internal_email_to_send else None,
        "internal_email_to_send": internal_email_to_send,
        "internal_email_send_status": "pending" if internal_email_to_send else "not_ready",
        "internal_email_send_reason": "" if internal_email_to_send else "missing_required_fields",
        "requires_human_review": result.requires_human_review,
        "field_extraction": result.extraction,
        "prompt_snapshots": dict(result.prompt_snapshots),
        "automation_context": {
            "handler": "fraud_account",
            "extractor_version": result.extraction.audit_payload().get("prompt_version"),
            "extraction_status": result.extraction.status,
            "follow_up_count": persisted_follow_up_count,
            "follow_up_scheduled": follow_up_scheduled,
            "proceed_with_missing_fields": result.proceed_with_missing_fields,
        },
    }


def _build_account_suspension_classification_attempt(
    *,
    ticket_subject: str,
    customer_messages: list[dict[str, Any]],
    existing_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    extraction = extract_account_suspension_fields(
        ticket_subject=ticket_subject,
        customer_messages=customer_messages,
        existing_fields=existing_fields,
    )
    return {
        "customer_reply": "",
        "missing_fields": [],
        "collected_fields": dict(extraction.collected_fields),
        "internal_email_payload": None,
        "internal_email_to_send": None,
        "internal_email_send_status": "not_applicable",
        "internal_email_send_reason": "account_billing_classification_only",
        "requires_human_review": False,
        "field_extraction": extraction,
        "prompt_snapshots": {"account_suspension_field_extractor": dict(extraction.prompt_snapshot)},
        "automation_context": {},
    }


def _rerun_account_persona_unavailable_human_review(
    *,
    account_case: dict[str, Any],
    route_execution: dict[str, Any],
    reason: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    handler = str(
        account_case.get("automation_handler")
        or account_case.get("execution_action")
        or account_case.get("route")
        or "automation"
    ).strip()
    execution_reason_code = reconciliation_reason_code(
        handler=handler,
        phase="persona",
        detail="unavailable",
    )
    updated_case = reconcile_automation_execution_failure(
        account_case,
        reason_code=execution_reason_code,
        context={
            "policy_decision": "account_persona_unavailable_human_review",
            "failure_detail": reason,
        },
    )
    updated_case["policy_decision"] = "account_persona_unavailable_human_review"
    updated_case["execution_reason_code"] = execution_reason_code
    updated_execution = {
        **route_execution,
        "final_route": str(updated_case.get("execution_action") or updated_case.get("route") or ""),
        "classification": dict(updated_case.get("route_classification") or {}),
    }
    return updated_case, updated_execution


def _automation_reply_facts(
    *,
    handler: str,
    action: str,
    missing_fields: list[str],
    collected_fields: dict[str, Any],
    submitted: bool = False,
    resolution_facts: list[str] | None = None,
    customer_name: str | None = None,
) -> dict[str, Any]:
    """Build Persona input without making the Behavior layer write customer copy."""
    if submitted:
        return build_automation_reply_facts(
            behavior=action or handler,
            reply_intent="submission_confirmation",
            known_information=collected_fields,
            performed_actions=["Submitted the request to the internal team for review."],
            next_step="The internal team will follow up after reviewing the request.",
            resolution_status="submitted_for_review",
            source_facts=resolution_facts,
            customer_name=customer_name,
        )
    return build_automation_reply_facts(
        behavior=action or handler,
        reply_intent="request_missing_information",
        known_information=collected_fields,
        missing_information=missing_fields,
        next_step=(
            "Submit the request to the internal team for review after receiving the missing information."
            if missing_fields
            else None
        ),
        resolution_status="awaiting_customer",
        source_facts=resolution_facts,
        customer_name=customer_name,
    )


ACCOUNT_REPLY_DELAY_MIN_SECONDS = 6 * 60
ACCOUNT_REPLY_DELAY_MAX_SECONDS = 10 * 60
_ACCOUNT_REPLY_RANDOM = random.SystemRandom()


def _account_reply_delay_seconds() -> int:
    return _ACCOUNT_REPLY_RANDOM.randint(ACCOUNT_REPLY_DELAY_MIN_SECONDS, ACCOUNT_REPLY_DELAY_MAX_SECONDS)


def _account_asked_field_keys(ticket: dict[str, Any]) -> set[str]:
    asked: set[str] = set()
    for message in ticket.get("messages", []):
        if not isinstance(message, dict) or str(message.get("role") or "").strip().lower() != "assistant":
            continue
        meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        for field_name in meta.get("asked_field_keys", []):
            normalized = str(field_name or "").strip().lower()
            if normalized:
                asked.add(normalized)
    return asked


def _account_reply_job_public(job: dict[str, Any] | None) -> dict[str, Any]:
    if not job:
        return {
            "ai_reply_status": None,
            "ai_reply_scheduled_for": None,
            "ai_reply_published_at": None,
            "ai_reply_error": None,
        }
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    status = str(job.get("status") or "")
    status = {
        ACCOUNT_REPLY_PERSONA_QUEUED: "queued",
        ACCOUNT_REPLY_PERSONA_PREPARING: "preparing",
        ACCOUNT_REPLY_PERSONA_SCHEDULED: "scheduled",
        ACCOUNT_REPLY_PERSONA_PUBLISHING: "publishing",
    }.get(status, status)
    return {
        "ai_reply_status": status or None,
        "ai_reply_scheduled_for": job.get("scheduled_for"),
        "ai_reply_published_at": job.get("published_at"),
        "ai_reply_error": str(payload.get("error") or "") or None,
    }


def _create_account_reply_job(
    *,
    ticket_id: str,
    trigger_message_created_at: str,
    draft_content: str = "",
    reply_facts: dict[str, Any] | None = None,
    asked_field_keys: list[str] | None = None,
    persona_assignment: dict[str, Any] | None = None,
    automation_delivery_key: str | None = None,
    rerun_job_id: str | None = None,
) -> dict[str, Any]:
    created_at = now_iso()
    return create_account_reply_job(
        ticket_repository,
        ticket_id=ticket_id,
        trigger_message_created_at=trigger_message_created_at,
        created_at=created_at,
        delay_seconds=_account_reply_delay_seconds(),
        draft_content=draft_content,
        reply_facts=reply_facts,
        asked_field_keys=asked_field_keys,
        persona_assignment=persona_assignment,
        automation_delivery_key=automation_delivery_key,
        rerun_job_id=rerun_job_id,
    )


async def _send_billing_internal_email_attempt(attempt: dict[str, Any]) -> tuple[str, str]:
    internal_email_to_send = attempt.get("internal_email_to_send")
    if not internal_email_to_send:
        return (
            str(attempt.get("internal_email_send_status") or "not_ready"),
            str(attempt.get("internal_email_send_reason") or "missing_required_fields"),
        )

    try:
        email_send_result = await _account_reroute_sync_call(
            send_billing_internal_email,
            internal_email_to_send,
        )
        send_status = str(email_send_result.get("status") or "failed")
        send_reason = str(email_send_result.get("reason") or "")
    except Exception as exc:
        send_status = "failed"
        send_reason = str(exc)

    return send_status, send_reason


async def _send_enablement_internal_email_attempt(attempt: dict[str, Any]) -> tuple[str, str]:
    internal_email_to_send = attempt.get("internal_email_to_send")
    if not internal_email_to_send:
        return (
            str(attempt.get("internal_email_send_status") or "not_ready"),
            str(attempt.get("internal_email_send_reason") or "missing_required_fields"),
        )
    try:
        email_send_result = await _account_reroute_sync_call(
            send_enablement_internal_email,
            internal_email_to_send,
        )
        resolved_to = str(email_send_result.get("resolved_to") or "").strip()
        if resolved_to:
            internal_email_to_send["resolved_to"] = resolved_to
        return (
            str(email_send_result.get("status") or "failed"),
            str(email_send_result.get("reason") or ""),
        )
    except Exception as exc:
        return "failed", str(exc)


async def _send_quota_internal_email_attempt(attempt: dict[str, Any]) -> tuple[str, str]:
    internal_email_to_send = attempt.get("internal_email_to_send")
    if not internal_email_to_send:
        return (
            str(attempt.get("internal_email_send_status") or "not_ready"),
            str(attempt.get("internal_email_send_reason") or "missing_required_fields"),
        )
    try:
        email_send_result = await _account_reroute_sync_call(
            send_quota_internal_email,
            internal_email_to_send,
        )
        resolved_to = str(email_send_result.get("resolved_to") or "").strip()
        if resolved_to:
            internal_email_to_send["resolved_to"] = resolved_to
        return (
            str(email_send_result.get("status") or "failed"),
            str(email_send_result.get("reason") or ""),
        )
    except Exception as exc:
        return "failed", str(exc)


def _ticket_db_startup_init_retries() -> int:
    return _safe_int_env("TICKET_DB_STARTUP_INIT_RETRIES", 2)


def _ticket_db_startup_init_retry_delay_seconds() -> float:
    return _safe_float_env("TICKET_DB_STARTUP_INIT_RETRY_DELAY_SECONDS", 1.0)


def _main_agent_async_enabled() -> bool:
    return bool(ASYNC_QUERY_ENABLED)


def _build_client_ack_instructions() -> str:
    return (
        "Write exactly one short acknowledgement sentence for a support chat. "
        "Use a concierge-style voice. "
        "Use a calm, warm, and polished tone. "
        "Only confirm that the request was received and will be checked. "
        "Do not provide technical guidance. Do not promise engineer escalation. "
        "Do not cite sources. Match the user's language."
    )


def _build_client_ack_user_prompt(message: str) -> str:
    return (
        "Customer message:\n"
        f"{' '.join(str(message or '').split()).strip()}\n\n"
        "Reply with exactly one acknowledgement sentence."
    )


def _normalize_client_ack_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _create_client_ack(message: str) -> dict[str, Any]:
    started_at = time.perf_counter()
    profile = resolve_model_profile(CLIENT_ACK_SCENARIO)
    response_payload: dict[str, Any] = {
        "ack_text": "",
        "source": "client_model",
        "model": profile.model,
        "reasoning_effort": profile.reasoning_effort,
        "latency_ms": 0.0,
        "error": None,
    }
    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt=_build_client_ack_instructions(),
            user_prompt=_build_client_ack_user_prompt(message),
            extra_payload={"max_output_tokens": CLIENT_ACK_MAX_OUTPUT_TOKENS},
        )
        ack_text = _normalize_client_ack_text(response.text)
        response_payload["model"] = str(response.model_name or profile.model).strip() or profile.model
        if ack_text:
            response_payload["ack_text"] = ack_text
        else:
            response_payload["error"] = "empty_ack_text"
    except LlmInvocationError as exc:
        LOGGER.warning("Client ack request failed: %s", exc)
        response_payload["error"] = str(exc)
    except Exception as exc:
        LOGGER.warning("Client ack request failed unexpectedly: %s", exc)
        response_payload["error"] = str(exc.__class__.__name__ or "client_ack_error")
    response_payload["latency_ms"] = round((time.perf_counter() - started_at) * 1000, 2)
    return response_payload


class TicketQueryRequest(BaseModel):
    ticket_id: str | None = None
    customer_id: str = Field(default="C-001")
    requester: str | None = None
    subject: str | None = None
    product: str | None = Field(default=None, max_length=64)
    content_format: str = Field(default="plaintext", pattern="^(plaintext|markdown)$")
    message: str = Field(min_length=1)
    asset_ids: list[str] = Field(default_factory=list)


class AccountIntakeRequest(BaseModel):
    ticket_id: str | None = Field(default=None, max_length=128)
    title: str = Field(default="", max_length=300)
    question: str = Field(default="", max_length=12000)
    customer_email: str | None = Field(default=None, max_length=320)
    customer_name: str | None = Field(
        default=None,
        max_length=160,
        validation_alias=AliasChoices(
            "customer_name",
            "cx_name",
            "cx name",
            "cxName",
            "requester_name",
        ),
    )
    requester: str | dict[str, Any] | None = Field(default=None)
    customer: str | dict[str, Any] | None = Field(default=None)
    external_id: str | None = Field(default=None, max_length=160)
    source: str | dict[str, Any] | None = Field(default=None)
    created_by: str | None = Field(default=None, max_length=160)


class AccountPersonaCreateRequest(BaseModel):
    persona_key: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    display_name: str = Field(min_length=1, max_length=120)
    content: dict[str, Any]


class AccountPersonaDraftRequest(BaseModel):
    content: dict[str, Any]
    change_note: str = Field(min_length=1, max_length=500)
    based_on_version: int | None = Field(default=None, ge=1)


class PromptDraftRequest(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    change_note: str = Field(min_length=1, max_length=500)
    based_on_version: int = Field(ge=1)


_ACCOUNT_TOP_LEVEL_NAME_KEYS = ("customer_name", "cx_name", "cx name", "cxName", "requester_name")
_ACCOUNT_NESTED_NAME_KEYS = ("name", "display_name", "full_name", "customer_name", "cx_name")
_ACCOUNT_CUSTOMER_NAME_MAX_LENGTH = 160


@dataclass(frozen=True)
class AccountIntakeIdentity:
    customer_name: str
    customer_email: str
    customer_id: str
    customer_name_source: str | None
    customer_email_source: str | None
    customer_email_status: str


def _normalized_account_intake_name(value: Any) -> str:
    raw = str(value or "")
    if len(raw) > _ACCOUNT_CUSTOMER_NAME_MAX_LENGTH:
        raise HTTPException(status_code=422, detail="customer name must not exceed 160 characters")
    return " ".join(raw.split()).strip()


def _account_intake_name_and_source(payload: Any) -> tuple[str, str | None]:
    if not isinstance(payload, dict):
        return "", None
    for key in _ACCOUNT_TOP_LEVEL_NAME_KEYS:
        candidate = _normalized_account_intake_name(payload.get(key))
        if candidate:
            return candidate, key
    for parent_key in ("requester", "customer"):
        nested = payload.get(parent_key)
        if isinstance(nested, str):
            candidate = _normalized_account_intake_name(nested)
            if candidate:
                return candidate, parent_key
        elif isinstance(nested, dict):
            for key in _ACCOUNT_NESTED_NAME_KEYS:
                candidate = _normalized_account_intake_name(nested.get(key))
                if candidate:
                    return candidate, f"{parent_key}.{key}"
    return "", None


def _normalized_account_intake_email(value: Any) -> tuple[str, str]:
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return "", "missing"
    if normalized.count("@") != 1 or any(character.isspace() for character in normalized):
        return "", "invalid"
    local_part, domain = normalized.split("@", 1)
    if not local_part or not domain:
        return "", "invalid"
    return normalized, "valid"


def _account_intake_identity(
    request: AccountIntakeRequest,
    *,
    payload: Any,
    ticket_id: str,
) -> AccountIntakeIdentity:
    customer_name, customer_name_source = _account_intake_name_and_source(payload)
    customer_email, customer_email_status = _normalized_account_intake_email(request.customer_email)
    anonymous_digest = hashlib.sha256(ticket_id.encode("utf-8")).hexdigest()[:20]
    return AccountIntakeIdentity(
        customer_name=customer_name,
        customer_email=customer_email,
        customer_id=customer_email or f"account-intake:{anonymous_digest}",
        customer_name_source=customer_name_source,
        customer_email_source="customer_email" if customer_email_status != "missing" else None,
        customer_email_status=customer_email_status,
    )


class AccountPersonaEnabledRequest(BaseModel):
    enabled: bool


class BillingResponseSubmitRequest(BaseModel):
    token: str | None = Field(default=None, max_length=256)
    result: str = Field(pattern="^(completed|refused|customer_action_required)$")
    notify_customer: bool
    note: str | None = Field(default=None, max_length=4000)


class BillingRouteCorrectionRequest(BaseModel):
    scope_label: str | None = Field(default=None, min_length=1, max_length=128)
    execution_action: str | None = Field(default=None, min_length=1, max_length=128)
    category: str | None = Field(default=None, min_length=1, max_length=128)
    subcategory: str | None = Field(default=None, min_length=1, max_length=128)
    corrector: str | None = Field(default=None, max_length=160)


class BillingRouteReviewRequest(BaseModel):
    review_status: str = Field(min_length=1, max_length=64)
    reviewer: str | None = Field(default=None, max_length=160)


class AccountCaseBatchDetailRequest(BaseModel):
    case_ids: list[str] = Field(min_length=1, max_length=10)


class AssetUploadIntentRequest(BaseModel):
    ticket_id: str = Field(min_length=1, max_length=128)
    customer_id: str = Field(min_length=1, max_length=128)
    file_name: str = Field(min_length=1, max_length=300)
    content_type: str = Field(default="text/plain", max_length=160)
    size_bytes: int = Field(gt=0)


class AssetCompleteRequest(BaseModel):
    customer_id: str | None = Field(default=None, max_length=128)


class ClientAckRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    ticket_id: str | None = Field(default=None, max_length=128)
    customer_id: str | None = Field(default=None, max_length=128)


class TicketActionRequest(BaseModel):
    action: str = Field(pattern="^(processing|resolved|investigate|reopen)$")
    engineer_id: str = Field(default="Jack")
    note: str | None = None

class ReviewSampleUpdateRequest(BaseModel):
    review_status: str | None = Field(default=None, pattern="^(pending|reviewed|dismissed)$")
    retrieval_ok: bool | None = None
    answer_ok: bool | None = None
    citation_ok: bool | None = None
    logic_ok: bool | None = None
    hallucination_present: bool | None = None
    route_family_override: str | None = Field(default=None, max_length=120)
    execution_action_override: str | None = Field(default=None, max_length=120)
    tooling_profile_override: str | None = Field(default=None, max_length=120)
    failure_stage_override: str | None = Field(default=None, max_length=120)
    failure_bucket_override: str | None = Field(default=None, max_length=120)
    dataset_decision: str | None = Field(default=None, pattern="^(promote_gold|keep_silver|needs_fix|reject)$")
    corrected_reference_answer: str | None = Field(default=None, max_length=12000)
    corrected_citation_targets: list[dict[str, Any]] | None = None
    note: str | None = Field(default=None, max_length=4000)


class DatasetGenerationRunRequest(BaseModel):
    dataset_name: str = Field(min_length=1, max_length=160)
    source_types: list[str]
    question_language: str = Field(default="en", pattern="^(en)$")


class DatasetBenchmarkRunRequest(BaseModel):
    experiment_id: str | None = Field(default=None, max_length=160)
    top_k: int | None = Field(default=None, ge=1, le=20)
    tier: str = Field(default="gold", pattern="^(gold|silver)$")


class BenchmarkSessionRunRequest(BaseModel):
    session_name: str | None = Field(default=None, max_length=160)
    top_k: int | None = Field(default=None, ge=1, le=20)


class InvestigationMessageRequest(BaseModel):
    engineer_id: str = Field(default="Jack")
    message: str = Field(min_length=1, max_length=4000)
    multi_agent_enabled: bool = False


class EngineerMultiAgentRunRequest(BaseModel):
    engineer_id: str = Field(default="Jack")


class EngineerCaseClaimRequest(BaseModel):
    engineer_id: str = Field(min_length=1, max_length=128)


class WorkspaceLoginRequest(BaseModel):
    email: str = Field(
        min_length=1,
        max_length=320,
        validation_alias=AliasChoices("email", "account_id"),
    )
    password: str = Field(min_length=1, max_length=512)


class WorkspaceAccountCreateRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=160)
    role: str = Field(pattern="^(admin|engineer)$")
    password: str = Field(min_length=10, max_length=512)


class WorkspaceInvitationCreateRequest(BaseModel):
    email: str = Field(
        min_length=3,
        max_length=320,
        pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
    )
    role: str = Field(pattern="^(admin|engineer)$")


class WorkspaceInvitationCompleteRequest(BaseModel):
    token: str = Field(min_length=16, max_length=512)
    display_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=10, max_length=512)
    confirm_password: str = Field(min_length=10, max_length=512)


class EngineerScheduleShiftRequest(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start: str = Field(pattern=r"^([01]\d|2[0-3]):(00|30)$")
    end: str = Field(pattern=r"^(([01]\d|2[0-3]):(00|30)|24:00)$")


class EngineerScheduleUpdateRequest(BaseModel):
    shifts: list[EngineerScheduleShiftRequest] = Field(max_length=7)


class EngineerAdminAssignmentRequest(BaseModel):
    engineer_id: str | None = Field(default=None, max_length=128)
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)


class InvestigationConfirmationRequest(BaseModel):
    engineer_id: str = Field(default="Jack")
    decision: str = Field(pattern="^(approve|revise|final_approve)$")
    note: str | None = Field(default=None, max_length=4000)


class EngineerHitlFeedbackRequest(BaseModel):
    engineer_id: str = Field(default="Jack", max_length=128)
    run_id: str | None = Field(default=None, max_length=160)
    message_id: str | None = Field(default=None, max_length=160)
    evidence_packet_id: str | None = Field(default=None, max_length=160)
    feedback_type: str = Field(pattern="^(approve|revise|reject|resolve|reopen)$")
    diagnosis_correctness: str = Field(
        pattern="^(correct|partially_correct|incorrect|not_applicable)$"
    )
    root_cause_correctness: str = Field(
        pattern="^(confirmed|likely|incorrect|unknown|not_applicable)$"
    )
    evidence_quality: str = Field(pattern="^(sufficient|partial|insufficient|wrong)$")
    citation_quality: str = Field(pattern="^(correct|partial|missing|wrong|not_applicable)$")
    customer_reply_quality: str = Field(pattern="^(sendable|needs_edit|unsafe|not_applicable)$")
    missing_information: list[dict[str, Any]] = Field(default_factory=list)
    incorrect_claims: list[dict[str, Any]] = Field(default_factory=list)
    corrected_root_cause: str | None = Field(default=None, max_length=12000)
    corrected_solution: str | None = Field(default=None, max_length=12000)
    corrected_customer_reply: str | None = Field(default=None, max_length=12000)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    memory_candidate: str = Field(pattern="^(yes|no|needs_review)$")
    memory_safety: str = Field(pattern="^(customer_safe|internal_only|do_not_store)$")
    memory_notes: str | None = Field(default=None, max_length=4000)
    prompt_version: str | None = Field(default=None, max_length=160)
    workflow_version: str | None = Field(default=None, max_length=160)
    tool_policy_version: str | None = Field(default=None, max_length=160)
    rag_access_policy_version: str | None = Field(default=None, max_length=160)
    evidence_packet_version: str | None = Field(default=None, max_length=160)


class CancelPendingRequest(BaseModel):
    customer_id: str | None = None
    message_created_at: str = Field(min_length=1, max_length=64)


class TechnicalKnowledgeArticleRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=200000)
    source_url: str = Field(min_length=1, max_length=2000)


class ConnectionHub:
    def __init__(self) -> None:
        self._channels: dict[str, set[WebSocket]] = {
            "client": set(),
            "engineer": set(),
            "dashboard": set(),
        }
        self._lock = asyncio.Lock()

    async def connect(self, channel: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._channels[channel].add(websocket)

    async def disconnect(self, channel: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._channels[channel].discard(websocket)

    async def broadcast(self, channel: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            recipients = list(self._channels[channel])
        stale: list[WebSocket] = []
        for websocket in recipients:
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)
        if stale:
            async with self._lock:
                for websocket in stale:
                    self._channels[channel].discard(websocket)


app = FastAPI(title="AI Ticket POC", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def disable_account_http_caching(request: Request, call_next: Any) -> Any:
    response = await call_next(request)
    path = request.url.path
    if path == "/account" or path.startswith("/account/") or path == "/api/account" or path.startswith("/api/account/"):
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

if CLIENT_DIR.exists():
    app.mount("/client", StaticFiles(directory=CLIENT_DIR, html=True), name="client-ui")
if ACCOUNT_DIR.exists():
    app.mount("/account", StaticFiles(directory=ACCOUNT_DIR, html=True), name="account-ui")
if BILLING_RESPONSE_DIR.exists():
    app.mount("/response", StaticFiles(directory=BILLING_RESPONSE_DIR, html=True), name="billing-response-ui")
if ENGINEER_DIR.exists():
    app.mount("/engineer", StaticFiles(directory=ENGINEER_DIR, html=True), name="engineer-ui")
if WORKSPACE_DIR.exists():
    app.mount("/workspace", StaticFiles(directory=WORKSPACE_DIR, html=True), name="workspace-ui")
if DASHBOARD_DIR.exists():
    app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard-ui")
if SHARED_UI_DIR.exists():
    app.mount("/shared-ui", StaticFiles(directory=SHARED_UI_DIR), name="shared-ui")
if ROADMAP_DIR.exists():
    app.mount("/roadmap", StaticFiles(directory=ROADMAP_DIR, html=True), name="roadmap-ui")


@app.get("/roadmap.html", include_in_schema=False)
async def roadmap_html() -> FileResponse:
    if not ROADMAP_HTML.exists():
        raise HTTPException(status_code=404, detail="Roadmap page not found")
    return FileResponse(ROADMAP_HTML)


ticket_repository: TicketRepository = create_ticket_repository()
asset_repository: AssetRepository = create_asset_repository()
_ACCOUNT_REROUTE_DISPATCH_STOP_EVENT = threading.Event()
_ACCOUNT_REROUTE_DISPATCH_THREAD_LOCK = threading.Lock()
_ACCOUNT_REROUTE_DISPATCH_THREAD: threading.Thread | None = None
_ACCOUNT_REROUTE_DISPATCH_CONTEXT = contextvars.ContextVar(
    "account_reroute_dispatch_context",
    default=False,
)
asset_storage = create_asset_storage()
hub = ConnectionHub()
event_bus = AsyncRedisEventBus()
task_queue = AsyncRedisTaskQueue()
rag_service_client = RagServiceClient()


def _engineer_assignment_service() -> EngineerAssignmentService:
    return EngineerAssignmentService(
        ticket_repository,
        sla_hours=_safe_int_env("ENGINEER_ASSIGNMENT_SLA_HOURS", 3),
    )


def _workspace_invitation_service() -> WorkspaceInvitationService:
    return WorkspaceInvitationService(ticket_repository)


def _workspace_schedule_payload() -> dict[str, Any]:
    schedules = ticket_repository.list_engineer_schedules()
    on_schedule = on_schedule_engineer_ids(schedules)
    shifts_by_engineer: dict[str, list[dict[str, Any]]] = {}
    for shift in schedules:
        engineer_id = str(shift.get("engineer_id") or "").strip()
        shifts_by_engineer.setdefault(engineer_id, []).append(
            {
                "weekday": int(shift["weekday"]),
                "start": minutes_to_time(int(shift["start_minute"])),
                "end": minutes_to_time(int(shift["end_minute"])),
            }
        )
    engineers = []
    for account in ticket_repository.list_workspace_accounts():
        if account.get("role") != "engineer" or not bool(account.get("active", True)):
            continue
        engineer_id = str(account.get("account_id") or "").strip()
        engineers.append(
            {
                **_public_workspace_account(account),
                "is_on_schedule_now": engineer_id in on_schedule,
                "shifts": shifts_by_engineer.get(engineer_id, []),
            }
        )
    return {"timezone": WORKSPACE_SCHEDULE_TIMEZONE, "engineers": engineers}


def _workspace_personal_schedule_payload(principal: WorkspacePrincipal) -> dict[str, Any]:
    schedule_payload = _workspace_schedule_payload()
    engineer = next(
        (
            item
            for item in schedule_payload["engineers"]
            if str(item.get("account_id") or "").strip() == principal.account_id
        ),
        None,
    )
    if engineer is None:
        raise HTTPException(status_code=404, detail="Engineer schedule not found")
    return {
        "timezone": schedule_payload["timezone"],
        "engineer": engineer,
    }


def _public_workspace_account(account: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in account.items()
        if key != "password_hash"
    }


def _authorization_bearer_token(authorization: str | None) -> str:
    value = str(authorization or "").strip()
    if not value.lower().startswith("bearer "):
        return ""
    return value[7:].strip()


def require_workspace_principal(
    authorization: str | None = Header(default=None),
) -> WorkspacePrincipal:
    principal = verify_workspace_access_token(_authorization_bearer_token(authorization))
    if principal is None:
        raise HTTPException(status_code=401, detail="Workspace authentication required")
    account = ticket_repository.get_workspace_account(principal.account_id)
    if (
        not isinstance(account, dict)
        or not bool(account.get("active", True))
        or str(account.get("role") or "").strip().lower() != principal.role
    ):
        raise HTTPException(status_code=401, detail="Workspace account is inactive or changed")
    return principal


def require_workspace_admin(
    principal: WorkspacePrincipal = Depends(require_workspace_principal),
) -> WorkspacePrincipal:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return principal


def _bootstrap_workspace_admin() -> None:
    account_id = str(os.getenv("WORKSPACE_BOOTSTRAP_ADMIN_ID") or "").strip()
    password = str(os.getenv("WORKSPACE_BOOTSTRAP_ADMIN_PASSWORD") or "")
    if not account_id or not password:
        return
    if ticket_repository.get_workspace_account(account_id) is not None:
        return
    created_at = now_iso()
    ticket_repository.save_workspace_account(
        {
            "account_id": account_id,
            "display_name": str(
                os.getenv("WORKSPACE_BOOTSTRAP_ADMIN_NAME") or account_id
            ).strip()
            or account_id,
            "role": "admin",
            "password_hash": hash_workspace_password(password),
            "active": True,
            "created_at": created_at,
            "updated_at": created_at,
        }
    )
    ticket_repository.record_workspace_audit_event(
        "workspace_account_bootstrapped",
        actor_id="system",
        target_id=account_id,
        payload={"role": "admin"},
        created_at=created_at,
    )


def _build_rag_answer_detail(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError("Legacy direct RAG answer detail builder is no longer used by backend.main.")


def derive_subject(message: str, preferred_subject: str | None = None) -> str:
    return derive_ticket_title(message, preferred_subject=preferred_subject)


def latest_customer_message(ticket: dict[str, Any]) -> str:
    messages = ticket.get("messages", [])
    for message in reversed(messages):
        if message.get("role") == "customer":
            return str(message.get("content", "")).strip()
    return ""


def latest_customer_message_created_at(ticket: dict[str, Any]) -> str:
    messages = ticket.get("messages", [])
    if not isinstance(messages, list):
        return ""
    customer_messages = [
        message
        for message in messages
        if isinstance(message, dict)
        and str(message.get("role") or "").strip().lower() in {"customer", "user"}
        and str(message.get("created_at") or "").strip()
    ]
    if not customer_messages:
        return ""
    latest_message = max(
        customer_messages,
        key=lambda message: str(message["created_at"]),
    )
    return str(latest_message["created_at"])


def _engineer_evidence_ticket_context(ticket: dict[str, Any]) -> list[dict[str, str]]:
    messages = ticket.get("messages")
    if not isinstance(messages, list):
        return []
    context: list[dict[str, str]] = []
    for message in messages[-8:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip()
        content = " ".join(str(message.get("content") or "").split()).strip()
        if not role or not content:
            continue
        context.append({"role": role, "content": content})
    return context


def _build_engineer_evidence_for_investigation(
    *,
    ticket: dict[str, Any],
    handoff_packet: dict[str, Any],
) -> dict[str, Any]:
    question = (
        str(handoff_packet.get("latest_customer_message") or "").strip()
        or latest_customer_message(ticket)
        or str(ticket.get("subject") or "").strip()
        or "Engineer investigation needs evidence."
    )
    client_findings = (
        dict(handoff_packet.get("rag_result"))
        if isinstance(handoff_packet.get("rag_result"), dict)
        else None
    )
    result = search_engineer_evidence(
        rag_service_client,
        question=question,
        ticket_id=str(ticket.get("ticket_id") or "").strip() or None,
        customer_id=str(ticket.get("customer_id") or "").strip() or None,
        requester=str(ticket.get("requester") or "").strip() or None,
        ticket_context=_engineer_evidence_ticket_context(ticket),
        product=str(ticket.get("product") or handoff_packet.get("product") or "").strip() or None,
        client_findings=client_findings,
    )
    return serialize_engineer_evidence_search_result(result)


def ensure_ticket_defaults(ticket: dict[str, Any]) -> None:
    created_at = ticket.get("created_at") or now_iso()
    ticket["created_at"] = created_at
    ticket.setdefault("updated_at", created_at)
    ticket["status"] = normalize_ticket_status(ticket.get("status"))
    ticket.setdefault("messages", [])
    ticket.setdefault("subject", "General support request")
    ticket.setdefault("requester", ticket.get("customer_id") or "Unknown")
    ticket["active_engineer_case_id"] = (
        str(ticket.get("active_engineer_case_id") or "").strip() or None
    )
    try:
        ticket["engineer_case_count"] = max(int(ticket.get("engineer_case_count") or 0), 0)
    except (TypeError, ValueError):
        ticket["engineer_case_count"] = 0
    ticket["product"] = normalize_support_product(ticket.get("product"))
    ticket["product_selection_state"] = (
        dict(ticket.get("product_selection_state"))
        if isinstance(ticket.get("product_selection_state"), dict)
        else None
    )
    ticket["client_intake_state"] = (
        dict(ticket.get("client_intake_state"))
        if isinstance(ticket.get("client_intake_state"), dict)
        else None
    )
    ticket["client_agent_runtime_state"] = (
        dict(ticket.get("client_agent_runtime_state"))
        if isinstance(ticket.get("client_agent_runtime_state"), dict)
        else None
    )
    ensure_ticket_investigation_defaults(ticket)
    surface_legacy_pending_question(ticket)
def _execution_client_agent_runtime_state(execution: Any) -> dict[str, Any] | None:
    candidate = getattr(execution, "client_agent_runtime_state", None)
    return dict(candidate) if isinstance(candidate, dict) else None


def _record_ticket_agent_runtime_events(execution: Any) -> None:
    events = getattr(execution, "client_agent_runtime_events", None)
    if not isinstance(events, list):
        return
    for item in events:
        if not isinstance(item, dict):
            continue
        ticket_id = str(item.get("ticket_id") or "").strip()
        run_id = str(item.get("run_id") or "").strip()
        agent_name = str(item.get("agent_name") or "").strip()
        phase = str(item.get("phase") or "").strip()
        event_type = str(item.get("event_type") or "").strip()
        if not ticket_id or not run_id or not agent_name or not phase or not event_type:
            continue
        payload = dict(item.get("payload") or {}) if isinstance(item.get("payload"), dict) else {}
        if str(item.get("created_at") or "").strip():
            payload.setdefault("created_at", str(item.get("created_at")).strip())
        ticket_repository.record_ticket_agent_event(
            ticket_id,
            str(item.get("message_id") or "").strip() or None,
            run_id,
            agent_name,
            phase,
            event_type,
            payload,
        )


def _run_client_ticket_review_agent(
    *,
    mode: str,
    message: str,
    product: str | None,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]] | None,
    current_state: dict[str, Any] | None,
    requester: str | None = None,
    customer_id: str | None = None,
    route_decision: SupportRouteDecision,
    resolution: SupportResolution,
    rag_result: dict[str, Any] | None,
    message_created_at: str | None = None,
) -> Any:
    if mode in {"rag_insufficient_evidence", "pre_engineer_intake"}:
        return evaluate_troubleshooting_intake(
            message=message,
            product=product,
            ticket_subject=ticket_subject,
            ticket_context=ticket_context,
            current_state=current_state,
            rag_result=rag_result,
            message_created_at=message_created_at,
            requester=requester,
            customer_id=customer_id,
        )

    skill_result = SimpleNamespace(
        answer=str(resolution.answer or "").strip(),
        sources=list(resolution.sources),
        citations=[dict(item) for item in resolution.citations],
        packed_evidence=dict(resolution.packed_evidence or {}) or None,
        evidence_summary=dict(resolution.evidence_summary or {}) or None,
    )
    try:
        sufficiency = judge_rag_answer_sufficiency(
            message=message,
            ticket_subject=ticket_subject,
            ticket_context=ticket_context,
            route_summary={
                "scope_label": route_decision.scope_label,
                "route_family": route_decision.route_family,
                "execution_action": route_decision.execution_action,
                "tooling_profile": route_decision.tooling_profile,
                "reason": route_decision.reason,
                "confidence": route_decision.confidence,
                "matched_signals": list(route_decision.matched_signals),
            },
            rag_answer=skill_result.answer,
            sources=skill_result.sources,
            citations=skill_result.citations,
            packed_evidence=skill_result.packed_evidence,
            evidence_summary=skill_result.evidence_summary,
        )
    except Exception:
        return {"decision": "open_engineer_ticket", "reason": "review_error", "confidence": 0.0}
    if str(sufficiency.decision or "").strip().lower() == "investigate":
        return {
            "decision": "open_engineer_ticket",
            "reason": "review_insufficient",
            "confidence": float(sufficiency.confidence or 0.0),
        }
    return {
        "decision": "approve_answer",
        "reason": str(sufficiency.reason or "review_passed").strip() or "review_passed",
        "confidence": float(sufficiency.confidence or 0.0),
    }


def _validated_new_session_product(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = normalize_support_product(raw)
    if normalized is None:
        raise HTTPException(status_code=400, detail="invalid product")
    return normalized


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


def _next_engineer_case_identity(ticket: dict[str, Any]) -> tuple[str, int]:
    case_sequence = int(ticket.get("engineer_case_count") or 0) + 1
    ticket_id = str(ticket.get("ticket_id") or "").strip()
    return f"{ticket_id}-{case_sequence}", case_sequence


def _active_engineer_case_payload(ticket: dict[str, Any]) -> dict[str, Any] | None:
    ticket_id = str(ticket.get("ticket_id") or "").strip()
    if not ticket_id:
        return None
    return ticket_repository.get_active_engineer_case(ticket_id, include_client_messages=True)


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

    engineer_case_id, case_sequence = _next_engineer_case_identity(ticket)
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


def _persist_engineer_case_and_ticket(
    ticket: dict[str, Any],
    engineer_case: dict[str, Any],
    *,
    new_messages: list[dict[str, Any]] | None = None,
) -> None:
    ticket["engineer_case_count"] = max(
        int(ticket.get("engineer_case_count") or 0),
        int(engineer_case.get("case_sequence") or 0),
    )
    if str(engineer_case.get("investigation_state") or "").strip().lower() == "closed":
        if str(ticket.get("active_engineer_case_id") or "").strip() == str(engineer_case.get("engineer_case_id") or "").strip():
            ticket["active_engineer_case_id"] = None
    else:
        ticket["active_engineer_case_id"] = str(engineer_case.get("engineer_case_id") or "").strip() or None
    ticket_repository.save_ticket(ticket, new_messages=[])
    ticket_repository.save_engineer_case(engineer_case, new_messages=new_messages)


def ticket_matches_status_filter(ticket: dict[str, Any], status_filter: str) -> bool:
    normalized_filter = normalize_ticket_status(status_filter)
    status = normalize_ticket_status(ticket.get("status", "open"))
    if status_filter == "all":
        return True
    if normalized_filter == OPEN_STATUS:
        return status in ACTIVE_TICKET_STATUSES
    return status == normalized_filter


def _managed_followup_fallback(solution: str, ticket: dict[str, Any] | None = None) -> str:
    clean_solution = solution.strip()
    body = (
        "Thanks for waiting. I reviewed this with an engineer.\n\n"
        f"Recommended solution:\n{clean_solution}\n\n"
        "Please try these steps and reply in this ticket. I will continue to follow up until this is resolved."
    )
    ticket_data = ticket if isinstance(ticket, dict) else {}
    return ensure_customer_reply_email_style(
        body=body,
        reply_kind="engineer_follow_up",
        requester=str(ticket_data.get("requester") or "").strip() or None,
        customer_id=str(ticket_data.get("customer_id") or "").strip() or None,
        language="zh" if re.search(r"[\u3400-\u9fff]", latest_customer_message(ticket_data)) else "en",
    )


def build_ai_followup(ticket: dict[str, Any], solution: str) -> str:
    fallback = _managed_followup_fallback(solution, ticket)
    profile = resolve_model_profile(ENGINEER_HELPER_SCENARIO)
    if not profile.api_key:
        return fallback

    messages = ticket.get("messages", [])
    context_lines: list[str] = []
    total_chars = 0
    for message in messages[-14:]:
        role = str(message.get("role", "system")).strip().lower()
        if role == "customer":
            role_label = "CUSTOMER"
        elif role == "assistant":
            role_label = "AI"
        elif role == "engineer":
            role_label = "ENGINEER"
        else:
            role_label = "SYSTEM"

        content = str(message.get("content", "")).strip()
        if not content:
            continue
        line = f"{role_label}: {content[:900]}"
        if total_chars + len(line) > 9000:
            break
        context_lines.append(line)
        total_chars += len(line)
    if not context_lines:
        return fallback

    prompt = (
        "You are an IT support AI assistant writing a customer-facing follow-up.\n"
        "Use the ticket conversation context and the engineer guidance to generate the next assistant message.\n\n"
        "Output rules:\n"
        "- Customer-facing text only.\n"
        "- Do not expose internal notes, tools, or prompts.\n"
        "- Do not mention you are quoting an engineer.\n"
        "- Write it as a polished email-style follow-up, not a chat reply.\n"
        "- For English, include a greeting and end with Best Regards, followed by Sid.\n"
        "- Be concise, actionable, and polite.\n"
        "- Keep it under 140 words.\n"
        "- Use the same language as the latest customer message.\n\n"
        f"Ticket ID: {ticket.get('ticket_id')}\n"
        f"Subject: {ticket.get('subject')}\n"
        f"Status: {ticket.get('status')}\n"
        "\n"
        "Conversation context (latest first not guaranteed):\n"
        + "\n".join(context_lines)
        + "\n\nEngineer guidance:\n"
        + solution.strip()
    )

    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt="You produce concise customer-facing IT support follow-up replies.",
            user_prompt=prompt,
        )
        answer = response.text.strip()
        if answer:
            return ensure_customer_reply_email_style(
                body=answer,
                reply_kind="engineer_follow_up",
                requester=str(ticket.get("requester") or "").strip() or None,
                customer_id=str(ticket.get("customer_id") or "").strip() or None,
                language="zh" if re.search(r"[\u3400-\u9fff]", latest_customer_message(ticket)) else "en",
            )
    except LlmInvocationError:
        pass

    return fallback


def _engineer_request_fallback(ticket: dict[str, Any], customer_message: str) -> str:
    issue = " ".join(str(customer_message or "").split()).strip()
    if not issue:
        issue = str(ticket.get("subject") or "").strip() or "Unknown customer issue"
    if len(issue) > 220:
        issue = issue[:220] + "..."
    return (
        "Engineer Request:\n"
        f"Issue: {issue}\n"
        "Action Needed: Please reproduce the issue, collect related logs/error traces, confirm recent release/config changes, and provide a workaround plus ETA."
    )


def _normalize_engineer_request_text(text: str, ticket: dict[str, Any], customer_message: str) -> str:
    content = str(text or "").strip()
    if not content:
        return _engineer_request_fallback(ticket, customer_message)

    lines = [line.strip() for line in content.splitlines() if line.strip()]
    issue_parts: list[str] = []
    action_parts: list[str] = []
    current_section: str | None = None

    for line in lines:
        lowered = line.lower()
        if lowered.startswith("engineer request"):
            current_section = None
            continue
        if lowered.startswith("issue:"):
            current_section = "issue"
            issue_line = line.split(":", 1)[1].strip()
            if issue_line:
                issue_parts.append(issue_line)
            continue
        if lowered.startswith("action needed:"):
            current_section = "action"
            action_line = line.split(":", 1)[1].strip()
            if action_line:
                action_parts.append(action_line)
            continue

        # Support wrapped lines without repeating the "Issue:" / "Action Needed:" prefix.
        if current_section == "issue":
            issue_parts.append(line)
        elif current_section == "action":
            action_parts.append(line)

    issue_value = " ".join(issue_parts).strip()
    action_value = " ".join(action_parts).strip()

    if not issue_value or not action_value:
        return _engineer_request_fallback(ticket, customer_message)

    return (
        "Engineer Request:\n"
        f"Issue: {issue_value}\n"
        f"Action Needed: {action_value}"
    )


def build_engineer_followup_request(ticket: dict[str, Any], customer_message: str) -> str:
    fallback = _engineer_request_fallback(ticket, customer_message)
    profile = resolve_model_profile(ENGINEER_HELPER_SCENARIO)
    if not profile.api_key:
        return fallback

    messages = ticket.get("messages", [])
    context_lines: list[str] = []
    total_chars = 0
    for message in messages:
        role = str(message.get("role", "system")).strip().upper() or "SYSTEM"
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        line = f"{role}: {content[:700]}"
        if total_chars + len(line) > 7500:
            break
        context_lines.append(line)
        total_chars += len(line)
    if not context_lines:
        return fallback

    prompt = (
        "You are assisting support investigation routing.\n"
        "Based on the full ticket context, create a concise engineer request.\n"
        "Output plain text only, exactly 3 lines in this exact format:\n"
        "Engineer Request:\n"
        "Issue: <one concise sentence>\n"
        "Action Needed: <one concise sentence describing what engineer should do or provide>\n\n"
        f"Ticket ID: {ticket.get('ticket_id')}\n"
        f"Subject: {ticket.get('subject')}\n"
        f"Status: {ticket.get('status')}\n"
        "Recent messages:\n"
        + "\n".join(context_lines)
    )

    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt="You generate concise support escalation requests for engineers.",
            user_prompt=prompt,
        )
        normalized = _normalize_engineer_request_text(response.text, ticket, customer_message)
        if normalized:
            return normalized
    except LlmInvocationError:
        pass
    return fallback


def _summary_fallback(ticket: dict[str, Any]) -> tuple[str, str]:
    agent_summary, agent_next_action = _dashboard_ticket_agent_brief(ticket)
    if normalize_ticket_status(ticket.get("status")) == INVESTIGATING_STATUS and agent_summary and agent_next_action:
        return agent_summary, agent_next_action

    subject = str(ticket.get("subject", "")).strip() or "General support request"
    status = str(ticket.get("status", "open")).strip().lower()
    active_investigation = (
        ticket.get("active_investigation")
        if isinstance(ticket.get("active_investigation"), dict)
        else None
    )
    investigation_state = ""
    latest_internal_update = ""
    if active_investigation is not None:
        investigation_state = str(active_investigation.get("state") or "active").strip().lower()
        internal_messages = active_investigation.get("messages")
        if isinstance(internal_messages, list):
            for internal_message in reversed(internal_messages):
                content = " ".join(str(internal_message.get("content", "")).split()).strip()
                if content:
                    latest_internal_update = content
                    break

    sub_tickets = _dashboard_sort_sub_tickets(
        [item for item in ticket.get("sub_tickets", []) if isinstance(item, dict)]
    )
    active_sub_ticket_count = sum(1 for item in sub_tickets if _dashboard_sub_ticket_is_active(item))
    latest_linked_sub_ticket_update = _dashboard_latest_linked_sub_ticket_update(sub_tickets)
    latest_open_sub_ticket = next(
        (item for item in sub_tickets if _dashboard_sub_ticket_is_active(item)),
        None,
    )
    if active_investigation is None and isinstance(latest_open_sub_ticket, dict):
        latest_open_investigation = _dashboard_sub_ticket_investigation_source(latest_open_sub_ticket) or {}
        investigation_state = str(latest_open_investigation.get("state") or "active").strip().lower()

    latest_customer = ""
    latest_assistant = ""
    messages = ticket.get("messages", [])
    for message in reversed(messages):
        role = str(message.get("role", "")).strip().lower()
        content = " ".join(str(message.get("content", "")).split()).strip()
        if not content:
            continue
        if not latest_customer and role == "customer":
            latest_customer = content
        if not latest_assistant and role == "assistant":
            latest_assistant = content
        if latest_customer and latest_assistant:
            break

    summary_parts = [f"Ticket subject is '{subject}' with status {status}."]
    if latest_customer:
        summary_parts.append(f"Latest customer request: {latest_customer[:260]}")
    if latest_assistant:
        summary_parts.append(f"Latest AI response: {latest_assistant[:260]}")
    if active_investigation is not None:
        summary_parts.append(
            f"Open engineer ticket state is {investigation_state or 'active'}."
        )
        if latest_internal_update:
            summary_parts.append(
                f"Latest engineer ticket update: {latest_internal_update[:260]}"
            )
    elif sub_tickets:
        summary_parts.append(
            f"Linked sub tickets: {len(sub_tickets)} total, {active_sub_ticket_count} active."
        )
        if investigation_state:
            summary_parts.append(
                f"Current active sub ticket state is {investigation_state}."
            )
        if latest_linked_sub_ticket_update:
            summary_parts.append(
                f"Latest engineer ticket update: {latest_linked_sub_ticket_update[:260]}"
            )
    if not latest_customer and not latest_assistant and active_investigation is None:
        summary_parts.append("No conversation history is available yet.")
    summary = " ".join(summary_parts).strip()

    if status == RESOLVED_STATUS:
        next_action = (
            "Confirm resolution details with the customer and close the ticket if no additional issue remains."
        )
    elif status == INVESTIGATING_STATUS:
        next_action = (
            "Continue the engineer ticket, gather the next missing detail, and request final confirmation when the customer reply is ready."
        )
    elif status == ESCALATED_STATUS:
        next_action = (
            "Review the customer escalation request, decide whether investigation must start now, and return the ticket to normal communication when safe."
        )
    else:
        next_action = (
            "Continue troubleshooting based on the latest customer message, then provide the next actionable step."
        )

    return summary, next_action


def _extract_json_dict(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    candidates = [cleaned]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return None


def _normalize_summary_fields(
    payload: dict[str, Any] | None, fallback_summary: str, fallback_next_action: str
) -> tuple[str, str]:
    def _to_text(value: Any, *, multiline: bool = False) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
            if not items:
                return ""
            if multiline:
                return "\n".join([f"{index + 1}. {item}" for index, item in enumerate(items)])
            return " ".join(items)
        if value is None:
            return ""
        text = str(value).strip()
        return text

    summary = ""
    next_action = ""
    if isinstance(payload, dict):
        summary = _to_text(payload.get("summary", ""), multiline=False)
        next_action = _to_text(
            payload.get("next_action_needed")
            or payload.get("next_action")
            or payload.get("nextActionNeeded")
            or "",
            multiline=True,
        )

    if not summary:
        summary = fallback_summary
    if not next_action:
        next_action = fallback_next_action

    normalized_summary = " ".join(summary.split())
    next_action_lines = [
        " ".join(line.split()).strip()
        for line in str(next_action).splitlines()
        if " ".join(line.split()).strip()
    ]
    if next_action_lines:
        normalized_next_action = "\n".join(next_action_lines)
    else:
        normalized_next_action = " ".join(str(next_action).split())
    return normalized_summary[:1500], normalized_next_action[:900]


def _llm_response_to_text(response: Any) -> str:
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")).strip())
            else:
                parts.append(str(item).strip())
        return "\n".join([part for part in parts if part]).strip()
    return str(content).strip()


def build_ticket_summary(ticket: dict[str, Any]) -> tuple[str, str, str]:
    agent_summary, agent_next_action = _dashboard_ticket_agent_brief(ticket)
    if normalize_ticket_status(ticket.get("status")) == INVESTIGATING_STATUS and agent_summary and agent_next_action:
        return agent_summary, agent_next_action, "engineer_agent_state"

    fallback_summary, fallback_next_action = _summary_fallback(ticket)
    profile = resolve_model_profile(ENGINEER_HELPER_SCENARIO)
    if not profile.api_key:
        return fallback_summary, fallback_next_action, "fallback"

    messages = ticket.get("messages", [])
    lines: list[str] = []
    for message in messages[-14:]:
        role = str(message.get("role", "system")).strip().upper() or "SYSTEM"
        content = " ".join(str(message.get("content", "")).split()).strip()
        if not content:
            continue
        lines.append(f"{role}: {content[:900]}")
    if not lines:
        return fallback_summary, fallback_next_action, "fallback"

    ticket_id = str(ticket.get("ticket_id", "")).strip()
    subject = str(ticket.get("subject", "")).strip()
    status = str(ticket.get("status", "")).strip()
    requester = str(ticket.get("requester") or ticket.get("customer_id") or "").strip()
    active_investigation = (
        ticket.get("active_investigation")
        if isinstance(ticket.get("active_investigation"), dict)
        else None
    )
    investigation_summary = "None"
    if active_investigation is not None:
        investigation_summary = (
            f"state={active_investigation.get('state') or 'active'}; "
            f"trigger_reason={active_investigation.get('trigger_reason') or 'unknown'}; "
            f"trigger_source={active_investigation.get('trigger_source') or 'unknown'}; "
            f"draft_customer_reply={str(active_investigation.get('draft_customer_reply') or '').strip()[:220] or 'None'}"
        )

    sub_tickets = _dashboard_sort_sub_tickets(
        [item for item in ticket.get("sub_tickets", []) if isinstance(item, dict)]
    )
    sub_ticket_lines: list[str] = []
    for sub_ticket in sub_tickets[:6]:
        source = _dashboard_sub_ticket_investigation_source(sub_ticket) or {}
        latest_update = _dashboard_sub_ticket_latest_update(sub_ticket)
        sub_ticket_lines.append(
            "ticket_id={ticket_id}; status={status}; state={state}; trigger_reason={trigger_reason}; "
            "trigger_source={trigger_source}; latest_update={latest_update}".format(
                ticket_id=str(
                    sub_ticket.get("engineer_case_id")
                    or sub_ticket.get("ticket_id")
                    or "-"
                ).strip(),
                status=str(sub_ticket.get("status") or "open").strip(),
                state=str(
                    source.get("state")
                    or ("active" if _dashboard_sub_ticket_is_active(sub_ticket) else "closed")
                ).strip(),
                trigger_reason=str(sub_ticket.get("trigger_reason") or "unknown").strip(),
                trigger_source=str(sub_ticket.get("trigger_source") or "unknown").strip(),
                latest_update=latest_update[:220] if latest_update else "None",
            )
        )
    sub_ticket_summary = "\n".join(sub_ticket_lines) if sub_ticket_lines else "None"

    prompt = (
        "Return a JSON object with exactly two keys: summary and next_action_needed.\n"
        "Requirements:\n"
        '- summary: 2-4 concise sentences describing current issue, current progress, and blocker if any.\n'
        "- next_action_needed: 1-3 concrete actions for the engineer to execute next.\n"
        "- Use plain English text values.\n"
        "- Do not use markdown, headings, or extra keys.\n\n"
        f"Ticket ID: {ticket_id}\n"
        f"Subject: {subject}\n"
        f"Requester: {requester}\n"
        f"Status: {status}\n"
        f"Active investigation: {investigation_summary}\n"
        f"Linked sub tickets: {len(sub_tickets)}\n"
        "Sub ticket details:\n"
        f"{sub_ticket_summary}\n"
        "Recent messages:\n"
        + "\n".join(lines)
    )

    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt=(
                "You summarize support tickets for engineers and output strict JSON "
                "with summary and next_action_needed."
            ),
            user_prompt=prompt,
        )
        parsed = _extract_json_dict(response.text)
        summary, next_action = _normalize_summary_fields(
            parsed, fallback_summary, fallback_next_action
        )
        if summary and next_action:
            return summary, next_action, response.model_name
    except LlmInvocationError:
        pass

    return fallback_summary, fallback_next_action, "fallback"


def resolve_support_message(
    message: str,
    *,
    ticket_id: str | None = None,
    customer_id: str | None = None,
    ticket_subject: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    product: str | None = None,
    latest_assistant_message: dict[str, Any] | None = None,
    current_ticket_status: str | None = None,
    has_active_engineer_case: bool = False,
    decision: SupportRouteDecision | None = None,
) -> SupportResolution:
    resolution = resolve_support_route_message(
        message,
        ticket_id=ticket_id,
        customer_id=customer_id,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        product=product,
        latest_assistant_message=latest_assistant_message,
        current_ticket_status=current_ticket_status,
        has_active_engineer_case=has_active_engineer_case,
        decision=decision,
    )
    if not ticket_id or resolution.route_family != AUTOMATED_ROUTE_FAMILY:
        return resolution
    action = str(resolution.execution_action or resolution.answer_route or "").strip()
    if not is_registered_automation(route_family=resolution.route_family, execution_action=action):
        return resolution
    evidence = dict(resolution.evidence_summary or {})
    if action == "enablement":
        missing_fields = list(evidence.get("enablement_missing_fields") or [])
        collected_fields = dict(evidence.get("enablement_collected_fields") or {})
        send_status = str(evidence.get("enablement_internal_email_send_status") or "").strip()
        requires_human_review = bool(evidence.get("enablement_requires_human_review"))
    else:
        missing_fields = list(evidence.get("billing_missing_fields") or [])
        collected_fields = dict(evidence.get("billing_collected_fields") or {})
        send_status = str(evidence.get("billing_internal_email_send_status") or "").strip()
        requires_human_review = bool(evidence.get("billing_requires_human_review"))
    if requires_human_review:
        reason = f"{action}_field_extraction_human_review"
        evidence.update(
            {
                "automation_persona_render_status": "human_review",
                "automation_persona_error": reason,
            }
        )
        return replace(
            resolution,
            answer="",
            answer_route="human_review_required",
            scope_label="human_review",
            route_family="human_review",
            execution_action="human_review_required",
            needs_engineer_guidance=True,
            route_reason=reason,
            evidence_summary=evidence,
        )
    if not missing_fields and not send_status == "sent":
        return resolution
    account_case = ticket_repository.get_account_case_by_ticket_id(ticket_id)
    facts = _automation_reply_facts(
        handler=action,
        action=action,
        missing_fields=[str(item) for item in missing_fields],
        collected_fields=collected_fields,
        submitted=send_status == "sent",
        customer_name=str((account_case or {}).get("customer_name") or ""),
    )
    try:
        persona = ticket_repository.resolve_account_persona(ticket_id)
    except AccountPersonaUnavailableError as exc:
        reason = str(exc)
        evidence.update(
            {
                "automation_persona_render_status": "human_review",
                "automation_persona_error": reason,
                "account_persona_unavailable": True,
            }
        )
        return replace(
            resolution,
            answer="",
            answer_route="human_review_required",
            scope_label="human_review",
            route_family="human_review",
            execution_action="human_review_required",
            needs_engineer_guidance=True,
            route_reason=reason,
            evidence_summary=evidence,
        )
    try:
        rendered = render_automation_reply(reply_facts=facts, persona_assignment=persona)
    except AutomationPersonaError as exc:
        reason = str(exc)
        evidence.update(
            {
                "automation_persona_render_status": "human_review",
                "automation_persona_error": reason,
            }
        )
        return replace(
            resolution,
            answer="",
            answer_route="human_review_required",
            scope_label="human_review",
            route_family="human_review",
            execution_action="human_review_required",
            needs_engineer_guidance=True,
            route_reason=reason,
            evidence_summary=evidence,
        )
    evidence.update(
        {
            "automation_persona_render_status": "generated",
            "automation_persona_model": rendered.model,
            "automation_persona_prompt_version": rendered.prompt_version,
        }
    )
    return replace(resolution, answer=rendered.content, evidence_summary=evidence)


def build_answer(
    message: str,
    *,
    ticket_id: str | None = None,
    customer_id: str | None = None,
    product: str | None = None,
) -> tuple[str, float, list[str], list[dict[str, str]], bool]:
    resolution = resolve_support_message(
        message,
        ticket_id=ticket_id,
        customer_id=customer_id,
        product=product,
    )
    return resolution.as_answer_tuple()


def _is_input_guardrail_blocked_message(message: dict[str, Any] | None) -> bool:
    return isinstance(message, dict) and bool(message.get("input_guardrail_blocked"))


def _build_input_guardrail_message_metadata(result: OpenAIInputGuardrailResult) -> dict[str, Any]:
    return {
        "input_guardrail_blocked": True,
        "input_guardrail_category": result.category,
        "input_guardrail_route_reason": result.route_reason,
        "input_guardrail_diagnostics": dict(result.diagnostics or {}),
    }


def _build_input_guardrail_route_payload(result: OpenAIInputGuardrailResult) -> dict[str, Any]:
    return {
        "answer_route": "guardrail",
        "scope_label": "input_guardrail",
        "route_family": "input_guardrail",
        "execution_action": "block_input",
        "tooling_profile": "openai_agents_input_guardrail",
        "route_reason": result.route_reason,
        "route_confidence": 1.0,
        "search_used": False,
        "matched_signals": [result.category],
    }


def build_emotion_context(ticket: dict[str, Any], limit: int = 6, max_chars: int = 240) -> list[dict[str, str]]:
    messages = ticket.get("messages", [])
    context: list[dict[str, str]] = []
    for item in messages[-max(1, int(limit)) :]:
        if _is_input_guardrail_blocked_message(item):
            continue
        role = str(item.get("role", "system")).strip().lower() or "system"
        content = " ".join(str(item.get("content", "")).split()).strip()
        if not content:
            continue
        if len(content) > max_chars:
            content = content[:max_chars] + "..."
        context.append({"role": role, "content": content})
    return context


def compose_emotion_and_answer(emotion_reply: str, answer: str) -> str:
    emotional = str(emotion_reply or "").strip()
    technical = str(answer or "").strip()
    if emotional and technical:
        return f"{emotional}\n\n{technical}"
    return emotional or technical


async def dispatch_event(channels: list[str], payload: dict[str, Any]) -> None:
    normalized_channels: list[str] = []
    for channel in channels:
        value = str(channel or "").strip().lower()
        if value and value not in normalized_channels:
            normalized_channels.append(value)

    for channel in normalized_channels:
        await hub.broadcast(channel, payload)

    if normalized_channels:
        bus_payload = dict(payload)
        bus_payload["targets"] = normalized_channels
        await event_bus.publish(bus_payload)


async def _async_to_thread_with_latency(method: Any, *args: Any, **kwargs: Any) -> tuple[Any, float]:
    started_at = time.perf_counter()
    result = await async_to_thread(method, *args, **kwargs)
    return result, round((time.perf_counter() - started_at) * 1000, 2)


def _round_timing(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def build_query_task(
    ticket_id: str,
    customer_message: str,
    message_created_at: str,
    *,
    app_build_ref: str | None = None,
    customer_id: str | None = None,
    requester: str | None = None,
    ticket_subject: str | None = None,
    product: str | None = None,
    product_selection_state: dict[str, Any] | None = None,
    route_context_tail: list[dict[str, str]] | None = None,
    client_intake_state: dict[str, Any] | None = None,
    latest_assistant_message: dict[str, Any] | None = None,
    current_ticket_status: str | None = None,
    ticket_updated_at: str | None = None,
    load_ticket_ms: float | None = None,
    save_ticket_ms: float | None = None,
    api_persist_latency_ms: float | None = None,
    api_return_latency_ms: float | None = None,
    processing_mode: str | None = None,
) -> dict[str, Any]:
    task: dict[str, Any] = {
        "task_type": "ticket_query",
        "ticket_id": ticket_id,
        "customer_message": customer_message,
        "message_created_at": message_created_at,
        "created_at": now_iso(),
    }
    if app_build_ref:
        task["app_build_ref"] = str(app_build_ref).strip()
    if customer_id:
        task["customer_id"] = str(customer_id).strip()
    if requester:
        task["requester"] = str(requester).strip()
    if ticket_subject:
        task["ticket_subject"] = str(ticket_subject).strip()
    if product:
        task["product"] = str(product).strip()
    if isinstance(product_selection_state, dict):
        task["product_selection_state"] = copy.deepcopy(product_selection_state)
    if isinstance(route_context_tail, list):
        task["route_context_tail"] = [
            {
                "role": str(item.get("role", "system")).strip().lower() or "system",
                "content": " ".join(str(item.get("content", "")).split()).strip(),
            }
            for item in route_context_tail
            if isinstance(item, dict) and " ".join(str(item.get("content", "")).split()).strip()
        ]
    if isinstance(client_intake_state, dict):
        task["client_intake_state"] = copy.deepcopy(client_intake_state)
    if isinstance(latest_assistant_message, dict):
        task["latest_assistant_message"] = {
            "role": str(latest_assistant_message.get("role", "assistant")).strip().lower() or "assistant",
            "content": " ".join(str(latest_assistant_message.get("content", "")).split()).strip(),
            "workflow_action": str(latest_assistant_message.get("workflow_action") or "").strip(),
            "answer_route": str(latest_assistant_message.get("answer_route") or "").strip(),
            "route_reason": str(latest_assistant_message.get("route_reason") or "").strip(),
        }
        assistant_message_source = str(latest_assistant_message.get("assistant_message_source") or "").strip()
        if assistant_message_source:
            task["latest_assistant_message"]["assistant_message_source"] = assistant_message_source
        if bool(latest_assistant_message.get("supports_customer_resolution")):
            task["latest_assistant_message"]["supports_customer_resolution"] = True
    if current_ticket_status:
        task["current_ticket_status"] = normalize_ticket_status(current_ticket_status)
    if ticket_updated_at:
        task["ticket_updated_at"] = str(ticket_updated_at).strip()
    if load_ticket_ms is not None:
        task["load_ticket_ms"] = round(float(load_ticket_ms), 2)
    if save_ticket_ms is not None:
        task["save_ticket_ms"] = round(float(save_ticket_ms), 2)
    if api_persist_latency_ms is not None:
        task["api_persist_latency_ms"] = round(float(api_persist_latency_ms), 2)
    if api_return_latency_ms is not None:
        task["api_return_latency_ms"] = round(float(api_return_latency_ms), 2)
    if processing_mode:
        task["processing_mode"] = str(processing_mode).strip()
    return task


async def _run_ticket_query_task_background(task: dict[str, Any]) -> None:
    from backend import worker as ticket_worker

    await async_to_thread(ticket_worker.process_ticket_query_task, dict(task))


async def _schedule_ticket_query_processing(
    background_tasks: BackgroundTasks,
    *,
    task: dict[str, Any],
) -> bool:
    queued = await task_queue.enqueue(dict(task))
    if queued:
        return True
    background_tasks.add_task(_run_ticket_query_task_background, dict(task))
    return True


def build_message_sentiment_task(
    ticket_id: str,
    customer_message: str,
    message_created_at: str,
) -> dict[str, str]:
    return {
        "task_type": "ticket_message_sentiment",
        "ticket_id": ticket_id,
        "customer_message": customer_message,
        "message_created_at": message_created_at,
        "created_at": now_iso(),
    }


async def _apply_deferred_message_sentiment_tag(
    ticket_id: str,
    customer_message: str,
    message_created_at: str,
) -> None:
    try:
        sentiment_result, sentiment_label = classify_customer_message_sentiment(customer_message)
        updated = ticket_repository.update_message_sentiment_label(
            ticket_id=ticket_id,
            role="customer",
            content=customer_message,
            created_at=message_created_at,
            sentiment_label=sentiment_label,
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
        ticket_repository.record_event(ticket_id, event["event"], event)
        await dispatch_event(["engineer", "dashboard"], event)
    except Exception:
        LOGGER.exception(
            "Deferred sentiment tagging failed for ticket %s at %s",
            ticket_id,
            message_created_at,
        )


async def _enqueue_or_defer_message_sentiment_tag(
    background_tasks: BackgroundTasks,
    *,
    ticket_id: str,
    customer_message: str,
    message_created_at: str,
) -> bool:
    queued = await task_queue.enqueue(
        build_message_sentiment_task(
            ticket_id=ticket_id,
            customer_message=customer_message,
            message_created_at=message_created_at,
        )
    )
    if not queued:
        background_tasks.add_task(
            _apply_deferred_message_sentiment_tag,
            ticket_id,
            customer_message,
            message_created_at,
        )
    return queued


def build_client_sync_event(ticket: dict[str, Any], event_name: str, message: str | None = None) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event": event_name,
        "ticket_id": str(ticket.get("ticket_id") or ""),
        "customer_id": str(ticket.get("customer_id") or ""),
        "status": normalize_ticket_status(ticket.get("status") or "open"),
        "updated_at": str(ticket.get("updated_at") or now_iso()),
        "created_at": now_iso(),
    }
    if message:
        event["message"] = message
    return event


def _persist_investigation_update(
    ticket_id: str,
    investigation: dict[str, Any] | None,
    *,
    new_messages: list[dict[str, Any]] | None = None,
) -> None:
    if not isinstance(investigation, dict):
        return
    ticket_repository.save_investigation(
        ticket_id=ticket_id,
        investigation=investigation,
        new_messages=new_messages or [],
    )


def _investigation_event_name(investigation_state: str, *, created: bool = False) -> str:
    state = str(investigation_state or "").strip().lower()
    if state == "awaiting_confirmation":
        return "ticket_investigation_confirmation_requested"
    if created:
        return "ticket_investigation_started"
    if state == "closed":
        return "ticket_investigation_closed"
    return "ticket_investigation_updated"


def _build_investigation_event(
    ticket: dict[str, Any],
    engineer_case: dict[str, Any],
    *,
    created: bool = False,
) -> dict[str, Any]:
    engineer_case_id = str(
        engineer_case.get("engineer_case_id") or engineer_case.get("ticket_id") or ""
    ).strip()
    investigation_state = str(
        engineer_case.get("investigation_state")
        or (
            (_active_investigation_from_case_payload(engineer_case) or {}).get("state")
            if isinstance(engineer_case, dict)
            else ""
        )
        or "active"
    ).strip()
    event_name = _investigation_event_name(investigation_state, created=created)
    latest_message = ""
    active_payload = _active_investigation_from_case_payload(engineer_case)
    messages = (
        active_payload.get("messages")
        if isinstance(active_payload, dict) and isinstance(active_payload.get("messages"), list)
        else (
            engineer_case.get("messages")
            if isinstance(engineer_case.get("messages"), list)
            else None
        )
    )
    if isinstance(messages, list) and messages:
        latest_message = str(messages[-1].get("content") or "").strip()
    payload = {
        "event": event_name,
        "ticket_id": engineer_case_id,
        "client_ticket_id": str(ticket.get("ticket_id") or ""),
        "engineer_case_id": engineer_case_id,
        "investigation_id": engineer_case_id,
        "status": normalize_ticket_status(engineer_case.get("status") or ticket.get("status")),
        "investigation_state": investigation_state or "active",
        "message": latest_message[:200],
        "created_at": now_iso(),
    }
    agent_state = engineer_case.get("engineer_agent_state")
    if isinstance(agent_state, dict):
        payload["agent_phase"] = str(agent_state.get("phase") or "").strip()
        payload["agent_ready_to_reply"] = bool(agent_state.get("ready_to_reply"))
        payload["agent_goal"] = str(agent_state.get("goal") or "").strip()
        payload["agent_next_request_for_engineer"] = str(
            agent_state.get("next_request_for_engineer") or ""
        ).strip()
        payload["agent_updated_at"] = str(agent_state.get("last_refreshed_at") or "").strip()
    return payload


async def _record_and_dispatch_investigation_event(
    ticket: dict[str, Any],
    engineer_case: dict[str, Any] | None,
    *,
    created: bool = False,
) -> None:
    if not isinstance(engineer_case, dict):
        return
    payload = _build_investigation_event(ticket, engineer_case, created=created)
    ticket_repository.record_event(str(ticket.get("ticket_id") or ""), payload["event"], payload)
    ticket_repository.record_engineer_case_event(
        str(payload.get("engineer_case_id") or ""),
        payload["event"],
        payload,
    )
    await dispatch_event(["engineer", "dashboard"], payload)


def _close_active_investigation(
    ticket: dict[str, Any],
    *,
    now_value: str,
    system_note: str | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    ensure_ticket_defaults(ticket)
    return close_case_context_active_investigation(
        ticket,
        now_value=now_value,
        system_note=system_note,
    )


def build_engineer_request_records(ticket_id: str) -> list[dict[str, Any]]:
    rows = ticket_repository.list_ticket_events(ticket_id=ticket_id, limit=200)
    records: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        event_type = str(row.get("event_type") or payload.get("event") or "").strip().lower()
        created_at = str(payload.get("created_at") or row.get("created_at") or now_iso())
        engineer_id = str(payload.get("engineer_id") or "").strip()
        detail = str(payload.get("message") or "").strip()

        status = ""
        if event_type == "ticket_guidance_applied":
            status = "received answer"
            if not detail:
                detail = "Engineer provided guidance for AI response."
        elif event_type == "ticket_escalated":
            status = "customer escalated"
            if not detail:
                detail = "Customer requested engineer assistance."

        if not status:
            continue

        records.append(
            {
                "id": f"{ticket_id}-{event_type}-{index}",
                "status": status,
                "detail": detail,
                "engineer_id": engineer_id,
                "created_at": created_at,
                "event_type": event_type,
            }
        )
    return records


def _dashboard_sub_ticket_investigation_source(sub_ticket: dict[str, Any]) -> dict[str, Any] | None:
    active = sub_ticket.get("active_investigation")
    if isinstance(active, dict):
        return active
    history = sub_ticket.get("investigation_history")
    if isinstance(history, list):
        for item in history:
            if isinstance(item, dict):
                return item
    return None


def _dashboard_sub_ticket_is_active(sub_ticket: dict[str, Any]) -> bool:
    return isinstance(sub_ticket.get("active_investigation"), dict)


def _dashboard_sub_ticket_latest_update(sub_ticket: dict[str, Any]) -> str:
    investigation = _dashboard_sub_ticket_investigation_source(sub_ticket) or {}
    messages = investigation.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        content = " ".join(str(message.get("content", "")).split()).strip()
        if content:
            return content
    return ""


def _dashboard_sort_sub_tickets(sub_tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [copy.deepcopy(item) for item in sub_tickets if isinstance(item, dict)],
        key=lambda item: (
            1 if _dashboard_sub_ticket_is_active(item) else 0,
            str(item.get("updated_at") or item.get("created_at") or ""),
            str(item.get("engineer_case_id") or item.get("ticket_id") or ""),
        ),
        reverse=True,
    )


def _dashboard_latest_linked_sub_ticket_update(sub_tickets: list[dict[str, Any]]) -> str:
    ordered = sorted(
        [item for item in sub_tickets if isinstance(item, dict)],
        key=lambda item: (
            str(item.get("updated_at") or item.get("created_at") or ""),
            str(item.get("engineer_case_id") or item.get("ticket_id") or ""),
        ),
        reverse=True,
    )
    for item in ordered:
        latest_update = _dashboard_sub_ticket_latest_update(item)
        if latest_update:
            return latest_update
    return ""


def _dashboard_ticket_sub_tickets(ticket_id: str) -> list[dict[str, Any]]:
    normalized_ticket_id = str(ticket_id or "").strip()
    if not normalized_ticket_id:
        return []
    return _dashboard_sort_sub_tickets(
        ticket_repository.list_ticket_engineer_cases(
            normalized_ticket_id,
            include_client_messages=True,
        )
    )


def _dashboard_ticket_agent_brief(ticket: dict[str, Any]) -> tuple[str, str]:
    agent_summary, agent_next_action = build_engineer_agent_brief(ticket)
    if agent_summary and agent_next_action:
        return agent_summary, agent_next_action

    for sub_ticket in _dashboard_sort_sub_tickets(
        [item for item in ticket.get("sub_tickets", []) if isinstance(item, dict)]
    ):
        sub_summary, sub_next_action = build_engineer_agent_brief(sub_ticket)
        if sub_summary and sub_next_action:
            return sub_summary, sub_next_action

    return "", ""


def _latest_assistant_message_for_ticket(ticket: dict[str, Any]) -> dict[str, Any] | None:
    messages = ticket.get("messages") if isinstance(ticket.get("messages"), list) else []
    latest_engineer_action = (
        ticket.get("last_engineer_action")
        if isinstance(ticket.get("last_engineer_action"), dict)
        else {}
    )
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if _is_input_guardrail_blocked_message(message):
            continue
        if str(message.get("role") or "").strip().lower() != "assistant":
            continue
        if not " ".join(str(message.get("content") or "").split()).strip():
            continue
        latest_message = copy.deepcopy(message)
        if (
            not str(latest_message.get("workflow_action") or "").strip()
            and str(latest_engineer_action.get("action") or "").strip() == "investigation_approve"
            and str(latest_engineer_action.get("created_at") or "").strip()
            and str(latest_engineer_action.get("created_at") or "").strip()
            == str(latest_message.get("created_at") or "").strip()
        ):
            latest_message["assistant_message_source"] = ASSISTANT_MESSAGE_SOURCE_ENGINEER_GUIDANCE
            latest_message["supports_customer_resolution"] = True
        return latest_message
    return None


def _build_ticket_auto_resolved_by_customer_confirmation_event(
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


def _close_active_engineer_case_for_customer_resolution(
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


def _build_dashboard_ticket_payload(
    ticket: dict[str, Any],
    *,
    include_sub_tickets: bool,
    include_agent_events: bool,
    include_token_usage: bool,
) -> dict[str, Any]:
    payload = copy.deepcopy(ticket)
    ensure_ticket_defaults(payload)
    ticket_id = str(payload.get("ticket_id") or "").strip()
    sub_tickets = _dashboard_ticket_sub_tickets(ticket_id)
    related_ticket_ids = [
        str(item.get("engineer_case_id") or item.get("ticket_id") or "").strip()
        for item in sub_tickets
        if str(item.get("engineer_case_id") or item.get("ticket_id") or "").strip()
    ]

    payload["linked_sub_ticket_count"] = len(sub_tickets)
    payload["active_sub_ticket_count"] = sum(1 for item in sub_tickets if _dashboard_sub_ticket_is_active(item))
    payload["latest_sub_ticket_update"] = _dashboard_latest_linked_sub_ticket_update(sub_tickets)
    payload["sub_tickets"] = copy.deepcopy(sub_tickets) if include_sub_tickets else []

    if include_agent_events:
        payload["client_agent_events"] = (
            ticket_repository.list_ticket_agent_events(ticket_id, limit=12)
            if ticket_id
            else []
        )

    if include_token_usage:
        try:
            payload["token_usage"] = rag_service_client.get_ticket_family_token_summary(
                ticket_id=ticket_id,
                client_ticket_id=ticket_id or None,
            )
        except RagServiceError:
            payload["token_usage"] = {
                **resolve_ticket_family_identity(
                    {
                        "ticket_id": ticket_id,
                        "client_ticket_id": ticket_id,
                    },
                    related_ticket_ids=related_ticket_ids,
                ),
                **aggregate_usage_ledger([]),
            }

    payload["engineer_request_records"] = build_engineer_request_records(ticket_id)
    return payload


def _dashboard_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _dashboard_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _dashboard_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _dashboard_latest_message(ticket: dict[str, Any], roles: set[str] | None = None) -> dict[str, Any] | None:
    messages = ticket.get("messages")
    if not isinstance(messages, list):
        return None
    normalized_roles = {item.lower() for item in roles or set()}
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        role = _dashboard_text(message.get("role")).lower()
        if normalized_roles and role not in normalized_roles:
            continue
        return message
    return None


def _dashboard_flow_status(value: Any, *, default: str = "queued") -> str:
    normalized = _dashboard_text(value).lower()
    if normalized in {"completed", "complete", "done", "success", "succeeded"}:
        return "completed"
    if normalized in {"cancelled", "canceled"}:
        return "cancelled"
    if normalized in {"failed", "failure", "error", "errored"}:
        return "failed"
    if normalized in {"skipped", "skip", "disabled", "not_applicable", "not applicable"}:
        return "skipped"
    if normalized in {"started", "running", "in_progress", "in progress", "processing"}:
        return "running"
    return default


def _dashboard_flow_events_for_agent(agent_events: list[dict[str, Any]], agent_name: str) -> list[dict[str, Any]]:
    return [
        event
        for event in agent_events
        if isinstance(event, dict) and _dashboard_text(event.get("agent_name")) == agent_name
    ]


def _dashboard_flow_event_summary(event: dict[str, Any]) -> dict[str, Any]:
    payload = _dashboard_dict(event.get("payload"))
    return {
        "agent_name": _dashboard_text(event.get("agent_name")) or None,
        "phase": _dashboard_text(event.get("phase")) or None,
        "event_type": _dashboard_text(event.get("event_type")) or None,
        "created_at": event.get("created_at"),
        "decision": _dashboard_text(payload.get("decision")) or None,
        "reason": _dashboard_text(payload.get("reason") or payload.get("error")) or None,
        "status": _dashboard_text(payload.get("status")) or None,
        "trace_id": _dashboard_text(
            payload.get("trace_id")
            or payload.get("latest_trace_id")
            or payload.get("openai_trace_id")
        ) or None,
        "rag_request_id": _dashboard_text(payload.get("rag_request_id")) or None,
    }


def _dashboard_flow_message_summary(message: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(message, dict):
        return {}
    return {
        "role": _dashboard_text(message.get("role")) or None,
        "created_at": message.get("created_at"),
        "content_summary": _dashboard_text(message.get("content"))[:500] or None,
        "answer_route": _dashboard_text(message.get("answer_route")) or None,
        "route_reason": _dashboard_text(message.get("route_reason")) or None,
        "needs_human": bool(message.get("needs_human")) if "needs_human" in message else None,
    }


def _dashboard_flow_agent_node(
    *,
    node_id: str,
    label: str,
    agent_name: str,
    summary: dict[str, Any],
    agent_events: list[dict[str, Any]],
    default_status: str = "queued",
    extra_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    events = _dashboard_flow_events_for_agent(agent_events, agent_name)
    details = {
        "phase": _dashboard_text(summary.get("phase")) or None,
        "event_count": len(events),
        "events": [_dashboard_flow_event_summary(event) for event in events[:5]],
    }
    if extra_details:
        details.update(extra_details)
    status = _dashboard_flow_status(summary.get("status") or summary.get("phase"), default=default_status)
    return {
        "id": node_id,
        "label": label,
        "kind": "rag" if node_id == "rag_service" else "agent",
        "status": status,
        "started_at": summary.get("started_at"),
        "completed_at": summary.get("completed_at"),
        "duration_ms": summary.get("duration_ms"),
        "decision": _dashboard_text(summary.get("decision")) or None,
        "reason": _dashboard_text(summary.get("reason")) or None,
        "summary": _dashboard_text(summary.get("reason") or summary.get("decision")) or None,
        "details": details,
    }


def _dashboard_final_action(*, workflow_action: str, ticket: dict[str, Any], final_message: dict[str, Any] | None) -> str:
    route = _dashboard_text((final_message or {}).get("answer_route")).lower()
    if workflow_action in {"answer_customer", "resolve_ticket"}:
        return "close_ticket" if workflow_action == "resolve_ticket" else "answer_customer"
    if workflow_action.startswith("clarify_customer"):
        return "clarify_customer"
    if workflow_action in {"open_engineer_ticket", "open_engineer_case"}:
        return "escalate_to_engineer"
    if route in {"handoff", "engineer", "escalate"} or normalize_ticket_status(ticket.get("status")) == INVESTIGATING_STATUS:
        return "escalate_to_engineer"
    if route == "rag":
        return "answer_customer"
    return "unknown"


def _dashboard_final_reason(final_action: str, runtime_state: dict[str, Any], final_message: dict[str, Any] | None) -> str:
    main_summary = _dashboard_dict(runtime_state.get("main_agent"))
    reason = _dashboard_text(main_summary.get("reason"))
    if reason and final_action != "escalate_to_engineer":
        return reason
    if final_action == "answer_customer":
        return "Grounded answer was sent to the customer."
    if final_action == "clarify_customer":
        return "The customer was asked for clarification before continuing."
    if final_action == "escalate_to_engineer":
        return "The ticket was escalated to an engineer for investigation."
    if final_action == "close_ticket":
        return "The ticket was closed after the customer confirmed resolution."
    if final_message is not None:
        return "Latest assistant message is available, but the final action is unknown."
    return "No completed agent outcome has been recorded yet."


def _build_dashboard_ticket_execution_flow(ticket: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(ticket)
    ensure_ticket_defaults(payload)
    ticket_id = _dashboard_text(payload.get("ticket_id"))
    runtime_state = _dashboard_dict(payload.get("client_agent_runtime_state"))
    agent_events = (
        ticket_repository.list_ticket_agent_events(ticket_id, limit=100)
        if ticket_id
        else []
    )
    final_message = _dashboard_latest_message(payload, {"assistant"})
    customer_message = _dashboard_latest_message(payload, {"customer"})
    retrieval_snapshot = _dashboard_dict((final_message or {}).get("retrieval_plan_snapshot"))
    timing_summary = _dashboard_dict(retrieval_snapshot.get("tool_timing_summary"))
    workflow_action = _dashboard_text(runtime_state.get("workflow_action"))
    final_action = _dashboard_final_action(
        workflow_action=workflow_action,
        ticket=payload,
        final_message=final_message,
    )
    runtime_status = _dashboard_flow_status(runtime_state.get("status"), default="queued")
    flow_status = runtime_status if runtime_state else "queued"
    route_reason = (
        _dashboard_text((final_message or {}).get("route_reason"))
        or _dashboard_text(_dashboard_dict(runtime_state.get("rag_service")).get("decision"))
        or _dashboard_text(_dashboard_dict(runtime_state.get("main_agent")).get("decision"))
        or None
    )
    needs_human = bool((final_message or {}).get("needs_human")) or final_action == "escalate_to_engineer"

    customer_created_at = (customer_message or {}).get("created_at") or payload.get("created_at")
    customer_node = {
        "id": "customer_message",
        "label": "Customer Message",
        "kind": "input",
        "status": "completed" if customer_message else "queued",
        "started_at": customer_created_at,
        "completed_at": customer_created_at,
        "duration_ms": None,
        "decision": None,
        "reason": "Customer submitted the latest message." if customer_message else "No customer message has been recorded yet.",
        "summary": _dashboard_text((customer_message or {}).get("content")) or _dashboard_text(payload.get("subject")) or None,
        "details": {
            "message_summary": _dashboard_flow_message_summary(customer_message),
            "ticket_subject": payload.get("subject"),
            "product": payload.get("product"),
        },
    }

    route_node = _dashboard_flow_agent_node(
        node_id="route_agent",
        label="Route Agent",
        agent_name="route_agent",
        summary=_dashboard_dict(runtime_state.get("route_agent")),
        agent_events=agent_events,
        default_status="queued",
    )

    rag_summary = _dashboard_dict(runtime_state.get("rag_service") or runtime_state.get("rag_agent"))
    rag_details = {
        "retrieval_strategy": retrieval_snapshot.get("retrieval_strategy"),
        "selected_chunk_ids": _dashboard_list(retrieval_snapshot.get("selected_chunk_ids")),
        "selected_contexts": _dashboard_list(retrieval_snapshot.get("selected_contexts")),
        "citations": _dashboard_list((final_message or {}).get("citations")),
        "tool_timing_summary": timing_summary,
        "fallback_reason": retrieval_snapshot.get("agent_fallback_reason"),
        "deadline_exhausted": retrieval_snapshot.get("deadline_exhausted"),
    }
    rag_node = _dashboard_flow_agent_node(
        node_id="rag_service",
        label="RAG Retrieval",
        agent_name="rag_service",
        summary=rag_summary,
        agent_events=agent_events,
        default_status="queued",
        extra_details=rag_details,
    )

    review_summary = _dashboard_dict(runtime_state.get("review_agent"))
    review_node = _dashboard_flow_agent_node(
        node_id="review_agent",
        label="Review Agent",
        agent_name="review_agent",
        summary=review_summary,
        agent_events=agent_events,
        default_status="queued",
        extra_details={"openai_tracing": _dashboard_dict(review_summary.get("openai_tracing"))},
    )

    final_reason = _dashboard_final_reason(final_action, runtime_state, final_message)
    final_completed_at = (final_message or {}).get("created_at") or runtime_state.get("completed_at")
    final_node = {
        "id": "final_outcome",
        "label": "Final Outcome",
        "kind": "outcome",
        "status": "completed" if final_action != "unknown" else flow_status,
        "started_at": None,
        "completed_at": final_completed_at,
        "duration_ms": None,
        "decision": final_action,
        "reason": final_reason,
        "summary": _dashboard_text((final_message or {}).get("content")) or final_reason,
        "details": {
            "message_summary": _dashboard_flow_message_summary(final_message),
            "ticket_status": payload.get("status"),
            "route_reason": route_reason,
        },
    }

    return {
        "ticket_id": ticket_id,
        "run_id": _dashboard_text(runtime_state.get("active_run_id")) or None,
        "status": flow_status,
        "summary": {
            "workflow_action": workflow_action or None,
            "final_action": final_action,
            "route_reason": route_reason,
            "total_latency_ms": timing_summary.get("total_latency_ms"),
            "needs_human": needs_human,
        },
        "nodes": [customer_node, route_node, rag_node, review_node, final_node],
        "edges": [
            {"from": "customer_message", "to": "route_agent"},
            {"from": "route_agent", "to": "rag_service"},
            {"from": "rag_service", "to": "review_agent"},
            {"from": "review_agent", "to": "final_outcome"},
        ],
    }


def _dashboard_ticket_detail_or_404(ticket_id: str, *, include_token_usage: bool) -> dict[str, Any]:
    ticket = ticket_repository.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return _build_dashboard_ticket_payload(
        ticket,
        include_sub_tickets=True,
        include_agent_events=True,
        include_token_usage=include_token_usage,
    )


def _resolve_engineer_case_payload(reference_id: str) -> dict[str, Any] | None:
    normalized_reference = str(reference_id or "").strip()
    if not normalized_reference:
        return None
    case_payload = ticket_repository.get_engineer_case(
        normalized_reference,
        include_client_messages=True,
    )
    if case_payload is not None:
        return _normalize_engineer_case_payload_for_read(case_payload)

    client_ticket = ticket_repository.get_ticket(normalized_reference)
    if client_ticket is None:
        return None
    active_cases = ticket_repository.list_ticket_engineer_cases(
        normalized_reference,
        include_client_messages=True,
    )
    open_cases = [
        item
        for item in active_cases
        if isinstance(item, dict)
        and (
            isinstance(item.get("active_investigation"), dict)
            or (
                isinstance(item.get("investigation_history"), list)
                and item.get("investigation_history")
                and str((item["investigation_history"][0] or {}).get("state") or "").strip().lower() != "closed"
            )
        )
    ]
    if len(open_cases) == 1:
        return _normalize_engineer_case_payload_for_read(open_cases[0])
    return None


def _normalize_engineer_case_payload_for_read(case_payload: dict[str, Any]) -> dict[str, Any]:
    normalized_payload = copy.deepcopy(case_payload)
    handoff_packet = (
        normalized_payload.get("engineer_handoff_packet")
        if isinstance(normalized_payload.get("engineer_handoff_packet"), dict)
        else None
    )
    raw_agent_state = (
        normalized_payload.get("engineer_agent_state")
        if isinstance(normalized_payload.get("engineer_agent_state"), dict)
        else None
    )
    active_investigation = (
        normalized_payload.get("active_investigation")
        if isinstance(normalized_payload.get("active_investigation"), dict)
        else None
    )
    ready_to_reply = (
        str(active_investigation.get("state") or "").strip().lower() == "awaiting_confirmation"
        if isinstance(active_investigation, dict)
        else False
    )
    normalized_agent_state = normalize_engineer_agent_state(
        raw_agent_state,
        ticket=normalized_payload,
        handoff_packet=handoff_packet,
        now_value=str(normalized_payload.get("updated_at") or normalized_payload.get("created_at") or now_iso()),
        ready_to_reply=ready_to_reply,
    )
    if isinstance(raw_agent_state, dict):
        preserved_agent_state = copy.deepcopy(raw_agent_state)
        preserved_agent_state["issue_understanding"] = normalized_agent_state.get("issue_understanding") or ""
        preserved_agent_state["known_facts"] = copy.deepcopy(normalized_agent_state.get("known_facts") or [])
        if not str(preserved_agent_state.get("last_refreshed_at") or "").strip():
            preserved_agent_state["last_refreshed_at"] = normalized_agent_state.get("last_refreshed_at")
        normalized_payload["engineer_agent_state"] = preserved_agent_state
    else:
        normalized_payload["engineer_agent_state"] = normalized_agent_state
    return normalized_payload


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/client")


def _client_compat_redirect_target(request: Request, legacy_path: str = "") -> str:
    legacy_path = legacy_path or ""
    target = "/client/"
    if legacy_path:
        target = f"/client/{legacy_path}"
    query = str(request.url.query or "").strip()
    if query:
        target = f"{target}?{query}"
    return target


@app.get("/client2")
@app.get("/client2/")
@app.get("/client2/{legacy_path:path}")
def client2_compat_redirect(request: Request, legacy_path: str = "") -> RedirectResponse:
    target = _client_compat_redirect_target(request, legacy_path)
    return RedirectResponse(url=target)


@app.get("/clienttest")
@app.get("/clienttest/")
@app.get("/clienttest/{legacy_path:path}")
def clienttest_compat_redirect(request: Request, legacy_path: str = "") -> RedirectResponse:
    target = _client_compat_redirect_target(request, legacy_path)
    return RedirectResponse(url=target)


@app.get("/login")
def login_entry() -> RedirectResponse:
    return RedirectResponse(url="/engineer")


@app.post("/api/v1/auth/logout")
def logout() -> dict[str, Any]:
    return {"ok": True, "logged_out_at": now_iso()}


def _initialize_asset_repository_with_fallback() -> None:
    global asset_repository
    if isinstance(ticket_repository, InMemoryTicketRepository):
        fallback_asset_repository = InMemoryAssetRepository()
        fallback_asset_repository.initialize()
        asset_repository = fallback_asset_repository
        LOGGER.warning("Using in-memory asset repository because ticket repository is in memory mode.")
        return
    try:
        asset_repository.initialize()
        LOGGER.info("Asset repository initialized: %s", asset_repository.storage_mode())
    except (psycopg.OperationalError, psycopg.Error, OSError, TimeoutError) as exc:
        LOGGER.error("Asset repository initialization failed: %s", exc)
        fallback_asset_repository = InMemoryAssetRepository()
        fallback_asset_repository.initialize()
        asset_repository = fallback_asset_repository
        LOGGER.warning("Falling back to in-memory asset repository for this process.")


@app.on_event("startup")
def startup_event() -> None:
    global ticket_repository, asset_repository
    attempts = max(1, _ticket_db_startup_init_retries() + 1)
    retry_delay_seconds = _ticket_db_startup_init_retry_delay_seconds()
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            ticket_repository.initialize()
            LOGGER.info("Ticket repository initialized: %s", ticket_repository.storage_mode())
            PromptVersionService(ticket_repository).sync_catalog()
            initialize_prompt_runtime(ticket_repository, service_name="api")
            _bootstrap_workspace_admin()
            _initialize_asset_repository_with_fallback()
            _start_account_reroute_dispatcher()
            return
        except (psycopg.OperationalError, psycopg.Error, OSError, TimeoutError) as exc:
            last_error = exc
            if attempt >= attempts:
                break
            LOGGER.warning(
                "Ticket repository initialization failed attempt %s/%s: %s",
                attempt,
                attempts,
                exc,
            )
            time.sleep(retry_delay_seconds)
    LOGGER.error(
        "Ticket repository initialization failed after %s attempts: %s",
        attempts,
        last_error,
    )
    fallback_repository = InMemoryTicketRepository()
    fallback_repository.initialize()
    ticket_repository = fallback_repository
    PromptVersionService(ticket_repository).sync_catalog()
    initialize_prompt_runtime(ticket_repository, service_name="api")
    _bootstrap_workspace_admin()
    LOGGER.warning("Falling back to in-memory ticket repository for this process.")
    _initialize_asset_repository_with_fallback()
    _start_account_reroute_dispatcher()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    dispatcher_stopped = _stop_account_reroute_dispatcher()
    await event_bus.close()
    await task_queue.close()
    if not dispatcher_stopped:
        LOGGER.error(
            "Account reroute dispatcher is still running; ticket and asset "
            "repositories must remain open until process exit."
        )
        return
    close_ticket_repository = getattr(ticket_repository, "close", None)
    if callable(close_ticket_repository):
        close_ticket_repository()
    close_asset_repository = getattr(asset_repository, "close", None)
    if callable(close_asset_repository):
        close_asset_repository()


def _dsn_host_database_signature(raw_dsn: str) -> tuple[str, str] | None:
    normalized_dsn = str(raw_dsn or "").strip()
    if not normalized_dsn:
        return None
    parsed = urllib.parse.urlparse(normalized_dsn)
    host = str(parsed.hostname or "").strip().lower()
    database = str(parsed.path or "").strip().lstrip("/").lower()
    if host and database:
        return host, database
    return None


def _module_spec_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _lightweight_runtime_warnings() -> set[str]:
    warnings: set[str] = set()
    sentiment_provider = (os.getenv("SENTIMENT_PROVIDER") or "model").strip().lower()
    embedding_provider = embedding_provider_name()
    torch_available = _module_spec_available("torch")
    sentence_transformers_available = _module_spec_available("sentence_transformers")

    if sentiment_provider == "model" and not torch_available:
        warnings.add("missing_local_sentiment_model_dependencies")
    if embedding_provider == "local_bge_m3" and not (torch_available and sentence_transformers_available):
        warnings.add("lightweight_image_incompatible_with_local_bge_m3")

    return warnings


def _health_config_warnings() -> list[str]:
    warnings = set(get_config_warnings())
    warnings.update(_lightweight_runtime_warnings())
    ticket_signature = _dsn_host_database_signature(os.getenv("TICKET_DB_DSN") or "")
    rag_signature = _dsn_host_database_signature(os.getenv("PGVECTOR_DSN") or "")
    if ticket_signature is not None and ticket_signature == rag_signature:
        warnings.add("shared_ticket_and_rag_database")
    return sorted(warnings)


def _runtime_profile() -> str:
    return (os.getenv("RUNTIME_PROFILE") or "full").strip() or "full"


@app.get("/health")
def health() -> dict[str, Any]:
    rag_health = rag_service_client.probe_health()
    return {
        "status": "ok",
        "time": now_iso(),
        "app_build": get_app_build_info(),
        "ticket_storage": ticket_repository.storage_mode(),
        "knowledge_storage": rag_health.get("knowledge_storage") or "proxy",
        "rag_service": rag_health.get("status") or "unknown",
        "async_query_enabled": "true" if ASYNC_QUERY_ENABLED else "false",
        "runtime_profile": _runtime_profile(),
        "config_warnings": _health_config_warnings(),
        "prompt_runtime": prompt_runtime_info(),
    }


def _sanitize_uploaded_file_name(file_name: str) -> str:
    normalized = Path(file_name or "document.md").name
    clean_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", normalized).strip(".-")
    return clean_name or "document.md"


def _knowledge_embedding_model() -> str:
    return embedding_model_id()


def _knowledge_vector_table() -> str:
    schema = (os.getenv("PGVECTOR_SCHEMA") or "supportportal").strip() or "supportportal"
    raw_table = (os.getenv("PGVECTOR_TABLE") or DEFAULT_PGVECTOR_TABLE).strip() or DEFAULT_PGVECTOR_TABLE
    if "." in raw_table:
        return raw_table
    return f"{schema}.{raw_table}"


def _raise_rag_service_http_error(exc: RagServiceError) -> None:
    status_code = exc.status_code if isinstance(exc.status_code, int) and exc.status_code > 0 else 503
    detail = exc.payload if exc.payload is not None else str(exc)
    raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post("/api/engineer/knowledge/official-documents", status_code=202)
async def upload_official_document(file: UploadFile = File(...)) -> dict[str, Any]:
    original_name = _sanitize_uploaded_file_name(file.filename or "document.md")
    suffix = Path(original_name).suffix.lower()
    if suffix != ".md":
        raise HTTPException(status_code=400, detail="Only .md files are supported for official documents")

    raw_bytes = await file.read()
    await file.close()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(raw_bytes) > KNOWLEDGE_OFFICIAL_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Official document is too large. Max size is {KNOWLEDGE_OFFICIAL_MAX_BYTES} bytes.",
        )
    try:
        return await async_to_thread(
            rag_service_client.upload_official_document,
            file_name=original_name,
            content=raw_bytes,
        )
    except RagServiceError as exc:
        _raise_rag_service_http_error(exc)


@app.post("/api/engineer/knowledge/articles", status_code=202)
async def upload_technical_article(request: TechnicalKnowledgeArticleRequest) -> dict[str, Any]:
    if len(request.content) > KNOWLEDGE_ARTICLE_MAX_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Technical article content exceeds {KNOWLEDGE_ARTICLE_MAX_CHARS} characters",
        )

    try:
        return await async_to_thread(
            rag_service_client.upload_article,
            title=request.title.strip(),
            content=request.content,
            source_url=request.source_url.strip(),
        )
    except RagServiceError as exc:
        _raise_rag_service_http_error(exc)


@app.get("/api/engineer/knowledge/ingestions")
def list_knowledge_ingestions(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    try:
        return rag_service_client.list_ingestions(limit=limit)
    except RagServiceError as exc:
        _raise_rag_service_http_error(exc)


@app.get("/api/engineer/knowledge/ingestions/{ingestion_id}")
def get_knowledge_ingestion(ingestion_id: str) -> dict[str, Any]:
    try:
        return rag_service_client.get_ingestion(ingestion_id)
    except RagServiceError as exc:
        _raise_rag_service_http_error(exc)

@app.post("/api/client/ack")
def create_client_ack(request: ClientAckRequest) -> dict[str, Any]:
    return _create_client_ack(request.message)


_ZENDESK_TICKET_API_RE = re.compile(r"^/api/v2/tickets/(\d+)\.json$")
_ZENDESK_TICKET_AGENT_RE = re.compile(r"^/agent/tickets/(\d+)$")


def _normalize_zendesk_source_link(link: str) -> str:
    """Normalize Zendesk ticket URLs to the agent-facing entry point.

    Converts /api/v2/tickets/{number}.json → /agent/tickets/{number}
    and keeps already-normalized /agent/tickets/{number} URLs as-is.
    Non-Zendesk URLs or Zendesk URLs that don't match a ticket path
    are returned unchanged.
    """
    parsed = urllib.parse.urlparse(link)
    host = (parsed.hostname or "").lower()
    if not (host.endswith(".zendesk.com") or host == "zendesk.com"):
        return link

    path = parsed.path or ""
    m = _ZENDESK_TICKET_API_RE.match(path)
    if m:
        authority = host
        try:
            port = parsed.port
        except ValueError:
            return link
        if port is not None:
            authority = f"{authority}:{port}"
        return f"{parsed.scheme}://{authority}/agent/tickets/{m.group(1)}"

    if _ZENDESK_TICKET_AGENT_RE.match(path):
        return link  # already in agent form

    return link


def _clean_account_source_link(value: Any) -> str | None:
    link = str(value or "").strip()
    if not link or len(link) > 2000:
        return None
    parsed = urllib.parse.urlparse(link)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return _normalize_zendesk_source_link(link)


def _extract_account_source_link(value: Any) -> str | None:
    """Extract a safe http/https link from common N8n / Zendesk source shapes.

    Supports:
    - plain string URL: "https://xxx.zendesk.com/agent/tickets/123"
    - dict with key Link, link, url, source_url, or source
    """
    if isinstance(value, str) and value.strip():
        return _clean_account_source_link(value)
    if isinstance(value, dict):
        for key in ("Link", "link", "url", "source_url", "source"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                link = _clean_account_source_link(candidate)
                if link:
                    return link
    return None


def _normalize_account_source(value: str | dict[str, Any] | None) -> str:
    if _extract_account_source_link(value):
        return "api"
    normalized = str(value or "").strip().lower().replace("_", "-")
    if normalized in {"manual", "account-manual", "/account-manual"}:
        return "manual"
    if normalized in {"ui", "account-ui", "/account-ui"}:
        return "manual"
    if normalized in {"http", "account-http", "/account-http"}:
        return "api"
    if normalized in {"api", "/api"}:
        return "api"
    return "manual"


def _serialize_billing_ticket_source(
    raw_source: str | dict[str, Any] | None,
    normalized: str,
) -> str:
    link = _extract_account_source_link(raw_source)
    if link:
        return json.dumps({"Link": link}, ensure_ascii=False)
    return normalized


def _intent_router_confidence_threshold() -> float:
    raw = os.environ.get("INTENT_ROUTER_CONFIDENCE_THRESHOLD", "0.7")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.7


def _account_not_automated_rollout_percent() -> int:
    raw = str(os.getenv("ACCOUNT_NOT_AUTOMATED_ENGINEER_ROLLOUT_PERCENT") or "10").strip()
    try:
        value = int(raw)
    except ValueError:
        return 10
    return max(0, min(value, 100))


def _account_intake_idempotency_key(request: AccountIntakeRequest) -> str:
    external_id = str(request.external_id or "").strip()
    if external_id:
        return f"external:{external_id}"
    ticket_id = str(request.ticket_id or "").strip()
    if ticket_id:
        return f"ticket:{ticket_id}"
    source_link = _extract_account_source_link(request.source)
    return f"source:{source_link}" if source_link else ""


def _zendesk_ticket_id_from_source(value: Any) -> str:
    link = _extract_account_source_link(value)
    if not link:
        return ""
    parsed = urllib.parse.urlparse(link)
    host = (parsed.hostname or "").lower()
    if not (host == "zendesk.com" or host.endswith(".zendesk.com")):
        return ""
    path = parsed.path or ""
    match = _ZENDESK_TICKET_AGENT_RE.match(path) or _ZENDESK_TICKET_API_RE.match(path)
    return match.group(1) if match else ""


def _resolve_account_ticket_id(request: AccountIntakeRequest) -> str:
    external_id = str(request.external_id or "").strip()
    if external_id:
        return external_id
    ticket_id = str(request.ticket_id or "").strip()
    if ticket_id:
        return ticket_id
    zendesk_ticket_id = _zendesk_ticket_id_from_source(request.source)
    return zendesk_ticket_id or f"TK-ACC-{uuid4().hex[:6].upper()}"


def _rollout_position_is_selected(position: int, percent: int) -> bool:
    if percent <= 0:
        return False
    if percent >= 100:
        return True
    interval = max(1, round(100 / percent))
    return position % interval == 0


def _is_low_route_confidence(ticket: dict[str, Any]) -> bool:
    try:
        confidence = float(ticket.get("route_confidence"))
    except (TypeError, ValueError):
        return False
    return confidence < _intent_router_confidence_threshold()


def _public_billing_route_correction(correction: dict[str, Any] | None) -> dict[str, Any] | None:
    if correction is None:
        return None
    return {
        "account_case_id": correction.get("account_case_id") or correction.get("billing_ticket_id"),
        "billing_ticket_id": correction.get("billing_ticket_id"),
        "client_ticket_id": correction.get("client_ticket_id"),
        "original_scope_label": correction.get("original_scope_label"),
        "original_route_family": correction.get("original_route_family"),
        "original_execution_action": correction.get("original_execution_action"),
        "original_tooling_profile": correction.get("original_tooling_profile"),
        "original_route_reason": correction.get("original_route_reason"),
        "original_route_confidence": correction.get("original_route_confidence"),
        "corrected_scope_label": correction.get("corrected_scope_label"),
        "corrected_route_family": correction.get("corrected_route_family"),
        "corrected_execution_action": correction.get("corrected_execution_action"),
        "corrected_tooling_profile": correction.get("corrected_tooling_profile"),
        "first_corrected_scope_label": correction.get("first_corrected_scope_label"),
        "first_corrected_route_family": correction.get("first_corrected_route_family"),
        "first_corrected_execution_action": correction.get("first_corrected_execution_action"),
        "first_corrected_tooling_profile": correction.get("first_corrected_tooling_profile"),
        "corrector": correction.get("corrector"),
        "correction_count": correction.get("correction_count") or 1,
        "created_at": correction.get("created_at"),
        "updated_at": correction.get("updated_at"),
    }


_MISSING = object()


def _route_error_fields(
    ticket: dict[str, Any],
    correction: dict[str, Any] | None | object = _MISSING,
) -> dict[str, Any]:
    billing_ticket_id = str(ticket.get("billing_ticket_id") or "").strip()
    if correction is _MISSING:
        correction = (
            ticket_repository.get_billing_route_correction(billing_ticket_id)
            if billing_ticket_id
            else None
        )
    route_corrected = correction is not None
    low_confidence = _is_low_route_confidence(ticket)
    return {
        "route_corrected": route_corrected,
        "route_low_confidence": low_confidence,
        "route_error": route_corrected or low_confidence,
        "route_correction": _public_billing_route_correction(correction),
    }


def _route_diagnostic_fields(classification: dict[str, Any]) -> dict[str, Any]:
    return {
        "execution_reason_code": classification.get("execution_reason_code"),
        "route_failure_family": classification.get("route_failure_family"),
        "stage_failure_types": dict(classification.get("stage_failure_types") or {}),
        "stage_failure_sources": dict(classification.get("stage_failure_sources") or {}),
        "stage_attempt_counts": dict(classification.get("stage_attempt_counts") or {}),
        "stage_recovered": dict(classification.get("stage_recovered") or {}),
    }


def _build_account_ticket_view_model(
    ticket: dict[str, Any],
    correction: dict[str, Any] | None | object = _MISSING,
) -> dict[str, Any]:
    canonical_ticket_id = (
        str(ticket.get("client_ticket_id") or "").strip()
        or str(ticket.get("ticket_id") or "").strip()
    )
    account_case_id = str(
        ticket.get("account_case_id") or ticket.get("billing_ticket_id") or ""
    ).strip() or None
    billing_ticket_id = str(ticket.get("billing_ticket_id") or account_case_id or "").strip() or None
    status = str(ticket.get("status") or ticket.get("automation_status") or "").strip() or "not_automated"
    execution_action = ticket.get("execution_action") or ticket.get("route")
    route_classification = (
        dict(ticket.get("route_classification"))
        if isinstance(ticket.get("route_classification"), dict)
        else {}
    )
    account_billing_subcategory = str(
        route_classification.get("account_billing_subcategory") or ticket.get("subcategory") or ""
    ).strip()
    metadata = account_route_metadata(
        classification=route_classification,
        route_family=ticket.get("route_family"),
        execution_action=execution_action,
    )
    category = ticket.get("category") or metadata["category"]
    subcategory = ticket.get("subcategory") or metadata["subcategory"]
    route_status = ticket.get("route_status") or metadata["route_status"]
    automation_handler = ticket.get("automation_handler") or metadata["automation_handler"]
    route_family = ticket.get("route_family")
    normalized_route = ticket.get("route")
    normalized_execution_action = ticket.get("execution_action")
    automation_family = str(route_family or "").strip().lower() in {
        AUTOMATED_ROUTE_FAMILY,
        "billing_automation",
    }
    if route_classification.get("agora_route") in {
        "account_billing",
        "backend_operation",
        "security_compliance",
    }:
        category = metadata["category"]
        subcategory = metadata["subcategory"]
        route_status = metadata["route_status"]
        automation_handler = metadata["automation_handler"]
    if automation_family and route_classification.get("agora_route") != "account_billing":
        route_status = metadata["route_status"]
        category = metadata["category"]
        subcategory = metadata["subcategory"]
        automation_handler = metadata["automation_handler"]
    if metadata["route_status"] == "automated":
        route_family = AUTOMATED_ROUTE_FAMILY
        normalized_route = metadata["subcategory"]
        normalized_execution_action = metadata["subcategory"]
    primary_label, secondary_label = account_case_labels(ticket)
    route_reason_code = str(
        route_classification.get("route_reason_code") or "legacy_reason_unavailable"
    ).strip()
    stage_reason_codes = dict(
        route_classification.get("stage_reason_codes")
        or route_classification.get("stage_reasons")
        or {}
    )

    raw_source = ticket.get("source")
    source_display: str | dict[str, Any]
    direct_link = _extract_account_source_link(raw_source)
    if direct_link:
        source_display = {"Link": direct_link}
    elif isinstance(raw_source, str) and raw_source.strip().startswith("{"):
        try:
            parsed = json.loads(raw_source)
            link = _extract_account_source_link(parsed) if isinstance(parsed, dict) else None
            if link:
                source_display = {"Link": link}
            else:
                source_display = _normalize_account_source(raw_source)
        except (json.JSONDecodeError, TypeError):
            source_display = _normalize_account_source(raw_source)
    else:
        source_display = _normalize_account_source(raw_source)

    return {
        **ticket,
        "ticket_id": canonical_ticket_id,
        "account_case_id": account_case_id,
        "billing_ticket_id": billing_ticket_id,
        "category": category,
        "subcategory": subcategory,
        "route_status": route_status,
        "route_family": route_family,
        "route": normalized_route,
        "execution_action": normalized_execution_action,
        "automation_handler": automation_handler,
        "automation_mode": route_classification.get("automation_mode") or (
            "classification_only"
            if subcategory == "account_suspension" or category == "security_compliance"
            else "active"
            if route_status == "automated" else None
        ),
        "primary_label": primary_label,
        "secondary_label": secondary_label,
        "route_reason_code": route_reason_code,
        "stage_reason_codes": stage_reason_codes,
        **_route_diagnostic_fields(route_classification),
        "source": source_display,
        "status": status,
        "automation_status": status,
        "route_review_status": str(ticket.get("route_review_status") or "pending").strip()
        or "pending",
        **_route_error_fields(ticket, correction),
    }


_ACCOUNT_CASE_SUMMARY_FIELDS = (
    "ticket_id",
    "account_case_id",
    "billing_ticket_id",
    "client_ticket_id",
    "title",
    "source",
    "status",
    "automation_status",
    "category",
    "subcategory",
    "route_status",
    "route_family",
    "route",
    "execution_action",
    "automation_handler",
    "automation_mode",
    "primary_label",
    "secondary_label",
        "route_reason_code",
        "execution_reason_code",
    "route_failure_family",
    "stage_failure_types",
    "stage_failure_sources",
    "stage_attempt_counts",
    "stage_recovered",
    "route_review_status",
    "route_corrected",
    "route_low_confidence",
    "route_error",
    "route_correction",
    "created_at",
    "updated_at",
)


def _build_account_case_summary(ticket: dict[str, Any]) -> dict[str, Any]:
    correction = ticket.get("_route_correction")
    view_model = _build_account_ticket_view_model(
        ticket,
        correction=correction if isinstance(correction, dict) else None,
    )
    summary = {field: view_model.get(field) for field in _ACCOUNT_CASE_SUMMARY_FIELDS}
    summary["detail_revision"] = str(ticket.get("_detail_revision") or "")
    summary.update(
        _account_reply_job_public(
            ticket.get("_latest_reply_job")
            if isinstance(ticket.get("_latest_reply_job"), dict)
            else None
        )
    )
    return summary


def _build_account_case_detail(bundle: dict[str, Any]) -> dict[str, Any]:
    account_case = bundle.get("account_case")
    if not isinstance(account_case, dict):
        raise ValueError("account case detail bundle is missing account_case")
    correction = bundle.get("route_correction")
    view_model = _build_account_ticket_view_model(
        account_case,
        correction=correction if isinstance(correction, dict) else None,
    )
    canonical_ticket = bundle.get("ticket")
    if isinstance(canonical_ticket, dict):
        view_model["messages"] = [
            message
            for message in canonical_ticket.get("messages", [])
            if not (
                isinstance(message, dict)
                and isinstance(message.get("meta"), dict)
                and message["meta"].get("superseded") is True
            )
        ]
        view_model["customer_id"] = canonical_ticket.get("customer_id")
        view_model["requester"] = canonical_ticket.get("requester")
        view_model["support_ticket_status"] = canonical_ticket.get("status")
    else:
        view_model["messages"] = []
        view_model["customer_id"] = account_case.get("client_ticket_id") or ""
        view_model["requester"] = account_case.get("client_ticket_id") or ""
        view_model["support_ticket_status"] = ""
    view_model.update(
        _account_reply_job_public(
            bundle.get("latest_reply_job")
            if isinstance(bundle.get("latest_reply_job"), dict)
            else None
        )
    )
    assignment = bundle.get("persona_assignment")
    view_model["persona_assignment"] = (
        {
            "persona_key": str(assignment.get("persona_key") or ""),
            "version": int(assignment.get("version") or 0),
            "assigned_at": assignment.get("assigned_at"),
            "display_name": str(
                assignment.get("display_name") or assignment.get("persona_key") or ""
            ),
        }
        if isinstance(assignment, dict)
        else None
    )
    view_model["detail_revision"] = str(bundle.get("detail_revision") or "")
    return {**account_case, **view_model}


@app.post("/account")
async def create_account_intake(request: AccountIntakeRequest, http_request: Request) -> dict[str, Any]:
    title = " ".join(str(request.title or "").split()).strip()
    question = str(request.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    if not title:
        title = derive_ticket_title(question)

    ticket_id = _resolve_account_ticket_id(request)
    intake_payload = await http_request.json()
    intake_identity = _account_intake_identity(request, payload=intake_payload, ticket_id=ticket_id)
    request_started_at = now_iso()
    idempotency_key = _account_intake_idempotency_key(request)
    if idempotency_key:
        idempotency_record = await async_to_thread(
            ticket_repository.begin_idempotent_request,
            "account_intake",
            idempotency_key,
            created_at=request_started_at,
        )
        if not idempotency_record.get("created"):
            replay_payload = idempotency_record.get("response_payload")
            if idempotency_record.get("state") == "completed" and isinstance(replay_payload, dict):
                return {**replay_payload, "idempotent_replay": True}
            raise HTTPException(status_code=409, detail="account intake request is already processing")

    account_case_id = f"AC-{ticket_id}"
    billing_ticket_id = account_case_id
    existing_ticket = await async_to_thread(ticket_repository.get_ticket, ticket_id)
    if existing_ticket is not None:
        raise HTTPException(status_code=409, detail="ticket_id already exists")

    account_source = _normalize_account_source(request.source)
    customer_id = intake_identity.customer_id
    customer_name = intake_identity.customer_name
    timestamp = now_iso()
    ticket: dict[str, Any] = {
        "ticket_id": ticket_id,
        "customer_id": customer_id,
        "requester": customer_id,
        "subject": title,
        "status": OPEN_STATUS,
        "source": account_source,
        "created_at": timestamp,
        "updated_at": timestamp,
        "messages": [
            {
                "role": "customer",
                "content": question,
                "created_at": timestamp,
                "content_format": "plaintext",
                "source": account_source,
                **({"external_id": str(request.external_id).strip()} if request.external_id else {}),
                **({"created_by": str(request.created_by).strip()} if request.created_by else {}),
            }
        ],
    }
    ensure_ticket_defaults(ticket)

    route_input = f"{title}\n\n{question}"
    ticket_context = [{"role": "customer", "content": question}]
    account_route_result = decide_account_route(
        route_input,
        ticket_subject=title,
        ticket_context=ticket_context,
        legacy_router=decide_support_route,
        require_latest=True,
    )
    decision = account_route_result.decision
    route_classification = dict(account_route_result.classification)
    route_prompt_snapshots = dict(account_route_result.prompt_snapshots)
    route = str(decision.execution_action or decision.route or "").strip()
    route_family = str(decision.route_family or "").strip()
    account_billing_subcategory = str(
        route_classification.get("account_billing_subcategory") or ""
    ).strip()
    route_metadata = account_route_metadata(
        classification=route_classification,
        route_family=route_family,
        execution_action=route,
    )
    is_automation_route = is_registered_automation(
        route_family=route_family,
        execution_action=route,
    )
    automation_handler = str(route_metadata.get("automation_handler") or "").strip()
    is_billing_route = (is_automation_route and automation_handler == "billing") or (
        route_family == "billing_review"
        and route == "human_review_required"
    )
    persona_assignment: dict[str, Any] | None = None
    execution_reason_code: str | None = None
    ticket_saved = False
    if is_automation_route:
        await async_to_thread(ticket_repository.save_ticket, ticket, new_messages=ticket.get("messages", []))
        ticket_saved = True
        try:
            persona_assignment = await async_to_thread(ticket_repository.resolve_account_persona, ticket_id)
        except AccountPersonaUnavailableError as exc:
            execution_reason_code = reconciliation_reason_code(
                handler=automation_handler or route,
                phase="persona",
                detail="unavailable",
            )

    resolution: SupportResolution | None = None
    response_status = "not_automated"
    customer_reply = ""
    missing_fields: list[str] = []
    collected_fields: dict[str, Any] = {}
    internal_email_payload: dict[str, Any] | None = None
    billing_email_attempt: dict[str, Any] | None = None
    enablement_email_attempt: dict[str, Any] | None = None
    quota_email_attempt: dict[str, Any] | None = None
    automation_context: dict[str, Any] = {}
    assistant_reply_facts: dict[str, Any] | None = None
    internal_email_send_status = "not_applicable"
    internal_email_send_reason = ""
    account_billing_registration = account_billing_handler(account_billing_subcategory)

    if is_automation_route and not execution_reason_code:
        response_status = "automation"
        handler_registration = account_automation_handler(route)
        if handler_registration is None:
            raise RuntimeError(f"unsupported account automation subcategory: {route}")
        if handler_registration.implementation == "account_verification":
            billing_email_attempt = _build_account_verification_internal_email_attempt(
                ticket_subject=title,
                customer_messages=list(ticket.get("messages") or []),
                ticket_id=ticket_id,
                account_case_id=account_case_id,
                customer_email=str(request.customer_email or "").strip() or None,
            )
            automation_attempt = billing_email_attempt
        elif handler_registration.implementation == "billing":
            billing_email_attempt = _build_billing_internal_email_attempt(
                action=route,
                message=question,
                ticket_id=ticket_id,
                billing_ticket_id=billing_ticket_id,
                customer_email=str(request.customer_email or "").strip() or None,
                requester=str(request.customer_email or "").strip() or None,
                persona_instruction=str(persona_assignment.get("content", {}).get("instruction") or "") if persona_assignment else None,
            )
            automation_attempt = billing_email_attempt
        elif handler_registration.implementation == "enablement":
            enablement_email_attempt = _build_enablement_internal_email_attempt(
                message=route_input,
                ticket_subject=title,
                customer_messages=list(ticket.get("messages") or []),
                ticket_id=ticket_id,
                account_case_id=account_case_id,
                customer_email=str(request.customer_email or "").strip() or None,
            )
            automation_attempt = enablement_email_attempt
        elif handler_registration.implementation == "quota":
            quota_email_attempt = _build_quota_internal_email_attempt(
                message=route_input,
                ticket_subject=title,
                customer_messages=list(ticket.get("messages") or []),
                ticket_id=ticket_id,
                account_case_id=account_case_id,
                customer_email=str(request.customer_email or "").strip() or None,
            )
            automation_attempt = quota_email_attempt
        else:
            raise RuntimeError(f"unsupported automation handler: {automation_handler}")
        extraction = automation_attempt.get("field_extraction")
        automation_context = dict(automation_attempt.get("automation_context") or {})
        route_prompt_snapshots.update(dict(automation_attempt.get("prompt_snapshots") or {}))
        if isinstance(
            extraction,
            (
                EnablementFieldExtraction,
                AccountVerificationFieldExtraction,
                QuotaFieldExtraction,
                DetailedInvoiceFieldExtraction,
                AccountSuspensionFieldExtraction,
            ),
        ):
            route_classification["field_extraction"] = extraction.audit_payload()
            snapshot_key = (
                "enablement_field_extractor"
                if isinstance(extraction, EnablementFieldExtraction)
                else (
                    "quota_field_extractor"
                    if isinstance(extraction, QuotaFieldExtraction)
                    else (
                        "detailed_invoice_field_extractor"
                        if isinstance(extraction, DetailedInvoiceFieldExtraction)
                        else (
                        "account_suspension_field_extractor"
                        if isinstance(extraction, AccountSuspensionFieldExtraction)
                        else "account_verification_field_extractor"
                        )
                    )
                )
            )
            route_prompt_snapshots[snapshot_key] = dict(extraction.prompt_snapshot)
        if automation_attempt.get("requires_human_review") and isinstance(
            extraction,
            (EnablementFieldExtraction, AccountVerificationFieldExtraction, QuotaFieldExtraction, DetailedInvoiceFieldExtraction),
        ):
            execution_reason_code = f"{automation_handler or route}_field_extraction_{extraction.status}"
            execution_failure_case = reconcile_automation_execution_failure(
                {
                    "route_classification": route_classification,
                    "automation_context": automation_context,
                    "collected_fields": dict(automation_attempt.get("collected_fields") or {}),
                },
                reason_code=execution_reason_code,
                extraction=extraction,
            )
            route_classification = dict(execution_failure_case.get("route_classification") or {})
            automation_context = dict(execution_failure_case.get("automation_context") or {})
            is_automation_route = False
            is_billing_route = False
            response_status = "human_review_required"
            collected_fields = dict(extraction.collected_fields)
            missing_fields = []
            internal_email_payload = None
            internal_email_send_status = "not_applicable"
            internal_email_send_reason = execution_reason_code
            billing_email_attempt = None
            enablement_email_attempt = None
            quota_email_attempt = None
            automation_attempt = None
        else:
            missing_fields = list(automation_attempt["missing_fields"])
            collected_fields = dict(automation_attempt["collected_fields"])
            assistant_reply_facts = _automation_reply_facts(
                handler=automation_handler,
                action=route,
                missing_fields=missing_fields,
                collected_fields=collected_fields,
                submitted=bool(automation_attempt.get("internal_email_to_send")),
                customer_name=customer_name,
            )
            internal_email_payload = automation_attempt["internal_email_payload"]
            internal_email_send_status = str(automation_attempt["internal_email_send_status"])
            internal_email_send_reason = str(automation_attempt["internal_email_send_reason"])
            route_classification["handler_binding_status"] = "active" if missing_fields else "completed"
            if automation_attempt.get("internal_email_to_send"):
                route_classification["handler_binding_status"] = "completed"
    elif (
        account_billing_registration is not None
        and account_billing_registration.implementation == "classification_only"
    ):
        classification_attempt = _build_account_suspension_classification_attempt(
            ticket_subject=title,
            customer_messages=list(ticket.get("messages") or []),
        )
        extraction = classification_attempt["field_extraction"]
        collected_fields = dict(classification_attempt["collected_fields"])
        internal_email_send_status = "not_applicable"
        internal_email_send_reason = "account_billing_classification_only"
        route_classification["field_extraction"] = extraction.audit_payload()
        route_prompt_snapshots.update(dict(classification_attempt["prompt_snapshots"]))
    if execution_reason_code:
        execution_failure_case = reconcile_automation_execution_failure(
            {
                "route_classification": route_classification,
                "automation_context": automation_context,
                "collected_fields": collected_fields,
            },
            reason_code=execution_reason_code,
        )
        route_classification = dict(execution_failure_case.get("route_classification") or {})
        automation_context = dict(execution_failure_case.get("automation_context") or {})
        response_status = "human_review_required"
        internal_email_payload = None
        internal_email_send_status = "not_applicable"
        internal_email_send_reason = execution_reason_code
        if not collected_fields:
            collected_fields = dict(execution_failure_case.get("collected_fields") or {})
        if not missing_fields:
            missing_fields = []
    if not ticket_saved:
        await async_to_thread(ticket_repository.save_ticket, ticket, new_messages=ticket.get("messages", []))

    billing_ticket: dict[str, Any] = {
        "account_case_id": account_case_id,
        "billing_ticket_id": billing_ticket_id,
        "client_ticket_id": ticket_id,
        "source": _serialize_billing_ticket_source(request.source, account_source),
        "external_id": str(request.external_id or "").strip() or _zendesk_ticket_id_from_source(request.source) or None,
        "created_by": str(request.created_by).strip() or None,
        "customer_name": customer_name or None,
        "title": title,
        "question": question,
        "route": route or None,
        # Route result fields: scope_label (大类) / route_family (执行族) / route (最终 action).
        "scope_label": decision.scope_label,
        "route_family": decision.route_family,
        "execution_action": route or None,
        "route_reason": decision.reason,
        "route_confidence": decision.confidence,
        "matched_signals": list(decision.matched_signals),
        "automation_status": response_status,
        "missing_fields": missing_fields,
        "collected_fields": collected_fields,
        "customer_reply": None,
        "internal_email_payload": internal_email_payload,
        "internal_email_send_status": internal_email_send_status,
        "internal_email_send_reason": internal_email_send_reason,
        # Semantic routing fields.
        "semantic_intent": decision.semantic_intent or None,
        "automation_eligibility": decision.automation_eligibility or None,
        "policy_decision": decision.policy_decision or None,
        "not_automated_reason": decision.not_automated_reason or None,
        "risk_flags": list(decision.risk_flags),
        "evidence_spans": list(decision.evidence_spans),
        "router_source": decision.router_source,
        "route_classification": route_classification,
        "automation_context": automation_context,
        **route_metadata,
    }
    await async_to_thread(ticket_repository.save_account_case, billing_ticket)
    await async_to_thread(
        ticket_repository.save_account_route_execution,
        route_execution_from_decision(
            ticket_id=ticket_id,
            decision=decision,
            system_prompt=None,
            user_prompt=None,
            created_at=timestamp,
            classification=route_classification,
            prompt_snapshots=route_prompt_snapshots,
            stage_attempts=getattr(account_route_result, "stage_attempts", None),
        ),
    )
    asked_field_keys = list(missing_fields) if missing_fields and not internal_email_payload else []
    reply_job = None
    if is_automation_route and asked_field_keys and assistant_reply_facts:
        reply_job = await async_to_thread(
            _create_account_reply_job,
            ticket_id=ticket_id,
            trigger_message_created_at=timestamp,
            reply_facts=assistant_reply_facts,
            draft_content="",
            asked_field_keys=asked_field_keys,
            persona_assignment=persona_assignment,
        )

    rollout_position: int | None = None
    rollout_selected = False
    engineer_case_id: str | None = None

    if billing_email_attempt and billing_email_attempt.get("internal_email_to_send"):
        internal_email_send_status, internal_email_send_reason = await _send_billing_internal_email_attempt(
            billing_email_attempt
        )
        billing_ticket["internal_email_send_status"] = internal_email_send_status
        billing_ticket["internal_email_send_reason"] = internal_email_send_reason
        billing_ticket["updated_at"] = now_iso()
        await async_to_thread(ticket_repository.save_account_case, billing_ticket)
        if internal_email_send_status != "sent":
            response_status = "human_review_required"
            execution_reason_code = reconciliation_reason_code(
                handler=automation_handler or "billing",
                phase="internal_email",
                detail=internal_email_send_status or "failed",
            )
            billing_ticket.update(
                reconcile_automation_execution_failure(
                    billing_ticket,
                    reason_code=execution_reason_code,
                )
            )
            route_classification = dict(billing_ticket.get("route_classification") or {})
            internal_email_send_status = "not_applicable"
            internal_email_send_reason = execution_reason_code
            await async_to_thread(ticket_repository.save_account_case, billing_ticket)
        if internal_email_send_status == "sent" and billing_email_attempt:
            confirmation_facts = _automation_reply_facts(
                handler=automation_handler or "billing",
                action=route,
                missing_fields=[],
                collected_fields=collected_fields,
                submitted=True,
                customer_name=customer_name,
            )
            reply_job = await async_to_thread(
                _create_account_reply_job,
                ticket_id=ticket_id,
                trigger_message_created_at=timestamp,
                draft_content="",
                reply_facts=confirmation_facts,
                asked_field_keys=[],
                persona_assignment=persona_assignment,
                automation_delivery_key=str(
                    (billing_ticket.get("internal_email_payload") or {}).get("delivery_key") or ""
                ),
            )
    if enablement_email_attempt and enablement_email_attempt.get("internal_email_to_send"):
        internal_email_send_status, internal_email_send_reason = await _send_enablement_internal_email_attempt(
            enablement_email_attempt
        )
        billing_ticket["internal_email_send_status"] = internal_email_send_status
        billing_ticket["internal_email_send_reason"] = internal_email_send_reason
        billing_ticket["internal_email_payload"] = dict(enablement_email_attempt["internal_email_to_send"])
        if internal_email_send_status != "sent":
            response_status = "human_review_required"
            execution_reason_code = reconciliation_reason_code(
                handler="enablement",
                phase="internal_email",
                detail=internal_email_send_status or "failed",
            )
            billing_ticket.update(
                reconcile_automation_execution_failure(
                    billing_ticket,
                    reason_code=execution_reason_code,
                )
            )
            route_classification = dict(billing_ticket.get("route_classification") or {})
            internal_email_send_status = "not_applicable"
            internal_email_send_reason = execution_reason_code
        if internal_email_send_status == "sent":
            confirmation_facts = _automation_reply_facts(
                handler="enablement",
                action="enablement",
                missing_fields=[],
                collected_fields=collected_fields,
                submitted=True,
                customer_name=customer_name,
            )
            reply_job = await async_to_thread(
                _create_account_reply_job,
                ticket_id=ticket_id,
                trigger_message_created_at=timestamp,
                draft_content="",
                reply_facts=confirmation_facts,
                asked_field_keys=[],
                persona_assignment=persona_assignment,
                automation_delivery_key=str(
                    billing_ticket["internal_email_payload"].get("delivery_key") or ""
                ),
            )
            billing_ticket["internal_email_payload"]["customer_confirmation_queued"] = True
        billing_ticket["updated_at"] = now_iso()
        await async_to_thread(ticket_repository.save_account_case, billing_ticket)
    if quota_email_attempt and quota_email_attempt.get("internal_email_to_send"):
        internal_email_send_status, internal_email_send_reason = await _send_quota_internal_email_attempt(
            quota_email_attempt
        )
        billing_ticket["internal_email_send_status"] = internal_email_send_status
        billing_ticket["internal_email_send_reason"] = internal_email_send_reason
        billing_ticket["internal_email_payload"] = dict(quota_email_attempt["internal_email_to_send"])
        if internal_email_send_status != "sent":
            response_status = "human_review_required"
            execution_reason_code = reconciliation_reason_code(
                handler="quota",
                phase="internal_email",
                detail=internal_email_send_status or "failed",
            )
            billing_ticket.update(
                reconcile_automation_execution_failure(
                    billing_ticket,
                    reason_code=execution_reason_code,
                )
            )
            route_classification = dict(billing_ticket.get("route_classification") or {})
            internal_email_send_status = "not_applicable"
            internal_email_send_reason = execution_reason_code
        if internal_email_send_status == "sent":
            confirmation_facts = _automation_reply_facts(
                handler="quota",
                action="quota",
                missing_fields=[],
                collected_fields=collected_fields,
                submitted=True,
                customer_name=customer_name,
            )
            reply_job = await async_to_thread(
                _create_account_reply_job,
                ticket_id=ticket_id,
                trigger_message_created_at=timestamp,
                draft_content="",
                reply_facts=confirmation_facts,
                asked_field_keys=[],
                persona_assignment=persona_assignment,
                automation_delivery_key=str(
                    billing_ticket["internal_email_payload"].get("delivery_key") or ""
                ),
            )
            billing_ticket["internal_email_payload"]["customer_confirmation_queued"] = True
        billing_ticket["updated_at"] = now_iso()
        await async_to_thread(ticket_repository.save_account_case, billing_ticket)

    primary_label, secondary_label = classification_labels(route_classification)
    route_reason_code = str(
        route_classification.get("route_reason_code") or "legacy_reason_unavailable"
    ).strip()
    stage_reason_codes = dict(
        route_classification.get("stage_reason_codes")
        or route_classification.get("stage_reasons")
        or {}
    )
    event = {
        "event": "ticket_created",
        "ticket_id": ticket_id,
        "status": ticket["status"],
        "source": account_source,
        "customer_name_present": bool(customer_name),
        "customer_name_source": intake_identity.customer_name_source,
        "customer_email_present": bool(intake_identity.customer_email),
        "customer_email_source": intake_identity.customer_email_source,
        "customer_email_status": intake_identity.customer_email_status,
        "message": question[:200],
        "created_at": now_iso(),
        "answer_route": resolution.answer_route if resolution is not None else None,
        "scope_label": decision.scope_label,
        "route_family": decision.route_family,
        "execution_action": route or None,
        "tooling_profile": decision.tooling_profile,
        "route_reason": decision.reason,
        "route_confidence": decision.confidence,
        "matched_signals": list(decision.matched_signals),
        "account_intake_status": response_status,
        "internal_email_send_status": internal_email_send_status,
        # Semantic routing fields.
        "semantic_intent": decision.semantic_intent or None,
        "policy_decision": decision.policy_decision or None,
        "not_automated_reason": decision.not_automated_reason or None,
        "router_source": decision.router_source,
        "route_classification": route_classification,
        "automation_mode": route_classification.get("automation_mode") or (
            "active" if route_metadata.get("route_status") == "automated" else None
        ),
        "primary_label": primary_label,
        "secondary_label": secondary_label,
        "route_reason_code": route_reason_code,
        "stage_reason_codes": stage_reason_codes,
        **_route_diagnostic_fields(route_classification),
        # Router audit fields
        "intent_router_attempted": decision.intent_router_attempted,
        "intent_router_confidence_threshold": decision.intent_router_confidence_threshold,
        "intent_router_model_confidence": decision.intent_router_model_confidence,
        "intent_router_fallback_reason": decision.intent_router_fallback_reason,
        "intent_router_failure_type": decision.intent_router_failure_type,
        "intent_router_failure_source": decision.intent_router_failure_source,
    }
    event["engineer_case_id"] = engineer_case_id
    event["rollout_position"] = rollout_position
    event["rollout_selected"] = rollout_selected
    await async_to_thread(ticket_repository.record_event, ticket_id, event["event"], event)
    response_payload = {
        "status": response_status,
        "automation_status": response_status,
        "route": route or None,
        "ticket_id": ticket_id,
        "account_case_id": account_case_id,
        "billing_ticket_id": billing_ticket_id,
        "customer_name": customer_name or None,
        "engineer_case_id": engineer_case_id,
        "rollout_position": rollout_position,
        "rollout_selected": rollout_selected,
        "customer_reply": "",
        "missing_fields": missing_fields,
        "collected_fields": collected_fields,
        "internal_email_send_status": internal_email_send_status,
        "internal_email_send_reason": internal_email_send_reason,
        "execution_reason_code": execution_reason_code,
        "semantic_intent": decision.semantic_intent or None,
        "route_family": decision.route_family,
        **route_metadata,
        "automation_eligibility": decision.automation_eligibility or None,
        "policy_decision": decision.policy_decision or None,
        "not_automated_reason": decision.not_automated_reason or None,
        "risk_flags": list(decision.risk_flags),
        "evidence_spans": list(decision.evidence_spans),
        "router_source": decision.router_source,
        "route_classification": route_classification,
        "automation_mode": route_classification.get("automation_mode") or (
            "active" if route_metadata.get("route_status") == "automated" else None
        ),
        "primary_label": primary_label,
        "secondary_label": secondary_label,
        "route_reason_code": route_reason_code,
        "stage_reason_codes": stage_reason_codes,
        **_route_diagnostic_fields(route_classification),
        "intent_router_attempted": decision.intent_router_attempted,
        "intent_router_confidence_threshold": decision.intent_router_confidence_threshold,
        "intent_router_model_confidence": decision.intent_router_model_confidence,
        "intent_router_fallback_reason": decision.intent_router_fallback_reason,
        "intent_router_failure_type": decision.intent_router_failure_type,
        "intent_router_failure_source": decision.intent_router_failure_source,
        **_account_reply_job_public(reply_job),
    }
    if idempotency_key:
        await async_to_thread(
            ticket_repository.complete_idempotent_request,
            "account_intake",
            idempotency_key,
            response_payload=response_payload,
            updated_at=now_iso(),
        )
    await dispatch_event(["engineer", "dashboard"], event)
    await dispatch_event(["client"], build_client_sync_event(ticket, event["event"], question[:200]))

    return response_payload


def _load_billing_response_token(raw_token: str | None) -> tuple[str, dict[str, Any]]:
    if not isinstance(raw_token, str):
        raise HTTPException(status_code=404, detail="billing response token not found")
    try:
        token_hash = hash_billing_response_token(raw_token)
    except BillingResolutionValidationError:
        raise HTTPException(status_code=404, detail="billing response token not found") from None
    token_record = ticket_repository.get_billing_response_token(token_hash)
    if token_record is None:
        raise HTTPException(status_code=404, detail="billing response token not found")
    return token_hash, token_record


def _billing_customer_email(billing_ticket: dict[str, Any]) -> str:
    client_ticket_id = str(billing_ticket.get("client_ticket_id") or "").strip()
    canonical_ticket = ticket_repository.get_ticket(client_ticket_id) if client_ticket_id else None
    if canonical_ticket:
        for key in ("requester", "customer_id"):
            value = " ".join(str(canonical_ticket.get(key) or "").split()).strip()
            if "@" in value:
                return value
    return ""


def _billing_resolution_automation_status(result: str, notify_customer: bool) -> str:
    if result == "customer_action_required":
        return "waiting_customer_action" if notify_customer else "internal_resolution_submitted"
    return "customer_notified" if notify_customer else "resolved_without_customer_notification"


def _render_billing_resolution_customer_reply(
    *,
    billing_ticket: dict[str, Any],
    note: str,
    customer_message: str,
    title: str,
    ticket_id: str,
    persist_failure: bool = True,
) -> str:
    behavior = str(
        billing_ticket.get("execution_action")
        or billing_ticket.get("route")
        or "billing"
    ).strip()
    try:
        persona = ticket_repository.resolve_account_persona(ticket_id)
        resolution = extract_automation_resolution_facts(
            behavior=behavior,
            source_text=note,
            known_information={"title": title},
        )
        source_facts = resolution.get("customer_shareable_facts")
        source_facts = source_facts if isinstance(source_facts, list) else []
        facts = build_automation_reply_facts(
            behavior=behavior,
            reply_intent="resolution_update",
            known_information={
                "title": title,
                "customer_action": resolution.get("customer_action"),
            },
            performed_actions=[],
            next_step=resolution.get("next_step"),
            resolution_status=resolution.get("status"),
            customer_language=detect_customer_reply_language(customer_message, note),
            source_facts=[str(item) for item in source_facts if str(item).strip()],
            customer_name=str(billing_ticket.get("customer_name") or ""),
        )
        return render_automation_reply(
            reply_facts=facts,
            persona_assignment=persona,
        ).content
    except (AccountPersonaUnavailableError, AutomationPersonaError) as exc:
        reason = str(exc)
        timestamp = now_iso()
        persona_unavailable = isinstance(exc, AccountPersonaUnavailableError)
        policy_decision = (
            "account_persona_unavailable_human_review"
            if persona_unavailable
            else "automation_persona_human_review"
        )
        execution_reason_code = reconciliation_reason_code(
            handler=str(billing_ticket.get("automation_handler") or behavior or "billing"),
            phase="persona" if persona_unavailable else "persona_render",
            detail="unavailable" if persona_unavailable else "failed",
        )
        billing_ticket.update(
            reconcile_automation_execution_failure(
                billing_ticket,
                reason_code=execution_reason_code,
                context={
                    "policy_decision": policy_decision,
                    "failure_detail": reason,
                },
            )
        )
        billing_ticket.update(
            {
                "policy_decision": policy_decision,
                "execution_reason_code": execution_reason_code,
                "updated_at": timestamp,
            }
        )
        if persist_failure:
            ticket_repository.save_billing_ticket(billing_ticket)
            ticket_repository.cancel_pending_account_reply_jobs(
                ticket_id,
                updated_at=timestamp,
            )
        return ""


@app.get("/api/billing-response")
def get_billing_response_context(token: str | None = None) -> dict[str, Any]:
    _, token_record = _load_billing_response_token(token)
    billing_ticket_id = str(token_record.get("billing_ticket_id") or "").strip()
    billing_ticket = ticket_repository.get_billing_ticket(billing_ticket_id)
    if billing_ticket is None:
        raise HTTPException(status_code=404, detail="billing ticket not found")

    return {
        "account_case_id": billing_ticket.get("account_case_id") or billing_ticket_id,
        "billing_ticket_id": billing_ticket_id,
        "submitted": token_record.get("used_at") is not None,
        "customer_email": _billing_customer_email(billing_ticket),
        "title": str(billing_ticket.get("title") or "").strip(),
        "question": str(billing_ticket.get("question") or "").strip(),
        "collected_fields": (
            copy.deepcopy(billing_ticket.get("collected_fields"))
            if isinstance(billing_ticket.get("collected_fields"), dict)
            else {}
        ),
    }


@app.post("/api/billing-response/submit")
async def submit_billing_response(request: BillingResponseSubmitRequest) -> dict[str, Any]:
    token_hash, token_record = await async_to_thread(_load_billing_response_token, request.token)
    if token_record.get("used_at") is not None:
        raise HTTPException(status_code=409, detail="billing response token already submitted")

    billing_ticket_id = str(token_record.get("billing_ticket_id") or "").strip()
    billing_ticket = await async_to_thread(ticket_repository.get_billing_ticket, billing_ticket_id)
    if billing_ticket is None:
        raise HTTPException(status_code=404, detail="billing ticket not found")

    client_ticket_id = str(billing_ticket.get("client_ticket_id") or "").strip()
    if not client_ticket_id:
        raise HTTPException(status_code=404, detail="linked support ticket not found")
    canonical_ticket = await async_to_thread(ticket_repository.get_ticket, client_ticket_id)
    if canonical_ticket is None:
        raise HTTPException(status_code=404, detail="linked support ticket not found")

    try:
        submission = validate_billing_resolution_submission(
            result=request.result,
            notify_customer=request.notify_customer,
            note=request.note,
        )
    except BillingResolutionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    timestamp = now_iso()
    resolution_event = build_billing_internal_resolution_event(
        billing_ticket_id=billing_ticket_id,
        client_ticket_id=client_ticket_id,
        result=submission["result"],
        notify_customer=submission["notify_customer"],
        note=submission["note"],
        created_at=timestamp,
    )
    automation_status = _billing_resolution_automation_status(
        submission["result"],
        submission["notify_customer"],
    )
    customer_reply = ""
    assistant_message: dict[str, Any] | None = None
    followup_event: dict[str, Any] | None = None
    human_review_required = False
    cancel_pending_reply_jobs = False
    events = [{"event_type": BILLING_RESPONSE_EVENT, "payload": resolution_event}]
    billing_ticket["automation_status"] = automation_status
    billing_ticket["updated_at"] = timestamp

    if submission["notify_customer"]:
        original_question = str(billing_ticket.get("question") or "").strip()
        latest_message = latest_customer_message(canonical_ticket)
        customer_message = "\n".join(
            part for part in (original_question, latest_message) if part
        )
        customer_reply = _render_billing_resolution_customer_reply(
            billing_ticket=billing_ticket,
            note=submission["note"],
            customer_message=customer_message,
            title=str(
                billing_ticket.get("title") or canonical_ticket.get("subject") or ""
            ).strip(),
            ticket_id=client_ticket_id,
            persist_failure=False,
        )
        if customer_reply:
            assistant_message = {
                "role": "assistant",
                "content": customer_reply,
                "created_at": timestamp,
                "content_format": "plaintext",
                "source": "billing_response_ai",
            }
            billing_ticket["customer_reply"] = customer_reply
            followup_event = {
                "event": BILLING_RESPONSE_AI_FOLLOWUP_EVENT,
                "billing_ticket_id": billing_ticket_id,
                "ticket_id": client_ticket_id,
                "resolution_result": submission["result"],
                "notify_customer": True,
                "customer_reply": customer_reply,
                "created_at": now_iso(),
                "source": "billing_response_ai",
            }
            events.append(
                {
                    "event_type": BILLING_RESPONSE_AI_FOLLOWUP_EVENT,
                    "payload": followup_event,
                }
            )
        else:
            human_review_required = True
            cancel_pending_reply_jobs = True

    committed = await async_to_thread(
        ticket_repository.commit_billing_response_submission,
        token_hash,
        billing_ticket_id=billing_ticket_id,
        ticket_id=client_ticket_id,
        assistant_message=assistant_message,
        account_case_updates=billing_ticket,
        events=events,
        cancel_pending_reply_jobs=cancel_pending_reply_jobs,
        completed_at=timestamp,
    )
    if not committed:
        raise HTTPException(status_code=409, detail="billing response token already submitted")

    await dispatch_event(["engineer", "dashboard"], resolution_event)
    if human_review_required:
        return {
            "submitted": True,
            "account_case_id": billing_ticket.get("account_case_id") or billing_ticket_id,
            "billing_ticket_id": billing_ticket_id,
            "result": submission["result"],
            "notify_customer": True,
            "customer_notified": False,
            "automation_status": str(
                billing_ticket.get("automation_status") or "not_automated"
            ),
            "human_review_required": True,
        }
    if not submission["notify_customer"]:
        return {
            "submitted": True,
            "billing_ticket_id": billing_ticket_id,
            "result": submission["result"],
            "notify_customer": False,
            "customer_notified": False,
            "automation_status": automation_status,
        }

    assert assistant_message is not None and followup_event is not None
    canonical_ticket.setdefault("messages", []).append(assistant_message)
    canonical_ticket["updated_at"] = timestamp
    await dispatch_event(["engineer", "dashboard"], followup_event)
    await dispatch_event(
        ["client"],
        build_client_sync_event(
            canonical_ticket,
            BILLING_RESPONSE_AI_FOLLOWUP_EVENT,
            customer_reply[:200],
        ),
    )

    return {
        "submitted": True,
        "account_case_id": billing_ticket.get("account_case_id") or billing_ticket_id,
        "billing_ticket_id": billing_ticket_id,
        "result": submission["result"],
        "notify_customer": submission["notify_customer"],
        "customer_notified": True,
        "automation_status": automation_status,
        "customer_reply": customer_reply,
    }


@app.get("/api/account/cases")
@app.get("/api/account/billing-tickets", deprecated=True)
def list_billing_tickets(
    limit: int = 30,
    page: int = 1,
    page_size: int | None = None,
    review_status: str | None = None,
    automation_status: str | None = None,
    route_status: str | None = None,
    route_errors: bool = False,
    route_label: str | None = Query(
        default=None,
        pattern="^(human_review|conversation|agora_technical|security_compliance|agora_non_technical|account_billing|uncertain|automation|all)$",
    ),
    route_group: str | None = Query(
        default=None,
        pattern="^(all|automation|backend_operation|account_billing|agora_technical|security_compliance|agora_non_technical|conversation|human_review)$",
    ),
    route_subcategory: str | None = Query(
        default=None,
        pattern="^(fraud_account|detailed_invoice|enablement|quota|unregistered|account_suspension|other|resolve|follow_up|human_review|uncategorized|uncertain|non_agora)$",
    ),
) -> dict[str, Any]:
    requested_page_size = page_size if page_size is not None else limit
    safe_page_size = max(1, min(requested_page_size, 100))
    normalized_review_status = str(review_status).strip() if review_status else None
    selected_route_status = route_status or automation_status
    normalized_automation_status = str(selected_route_status).strip() if selected_route_status else None
    try:
        normalized_route_filter = normalize_account_case_filter(
            group=route_group,
            subcategory=route_subcategory,
            legacy_label=route_label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    requested_page = max(1, page)
    requested_offset = (requested_page - 1) * safe_page_size
    tickets, total, filter_counts = ticket_repository.list_account_case_page_with_filter_counts(
        limit=safe_page_size,
        review_status=normalized_review_status,
        offset=requested_offset,
        route_status=normalized_automation_status,
        route_errors_only=route_errors,
        route_filter=normalized_route_filter,
    )
    total_pages = max(1, (total + safe_page_size - 1) // safe_page_size)
    safe_page = min(requested_page, total_pages)
    items = [_build_account_case_summary(item) for item in tickets]
    return {
        "cases": items,
        "tickets": items,
        "billing_tickets": items,
        "count": len(items),
        "page": safe_page,
        "page_size": safe_page_size,
        "total": total,
        "total_pages": total_pages,
        "has_more": safe_page < total_pages,
        "filter_counts": filter_counts,
        "filter_definitions": account_case_filter_definitions(),
    }


ACCOUNT_FULL_REROUTE_JOB_TICKET_ID = "__account-full-reroute__"
ACCOUNT_FULL_REROUTE_JOB_EVENT = "account_full_reroute_job"
ACCOUNT_FULL_REROUTE_STALE_AFTER = timedelta(hours=2)
ACCOUNT_REROUTE_EXECUTION_LEASE = timedelta(minutes=30)
ACCOUNT_REROUTE_DISPATCH_BATCH_LIMIT = 100
ACCOUNT_REROUTE_DISPATCH_POLL_INTERVAL_ENV = "ACCOUNT_REROUTE_DISPATCH_POLL_INTERVAL_SECONDS"
ACCOUNT_REROUTE_DISPATCH_SHUTDOWN_TIMEOUT_ENV = "ACCOUNT_REROUTE_DISPATCH_SHUTDOWN_TIMEOUT_SECONDS"
ACCOUNT_REROUTE_REPLY_POLL_INTERVAL_SECONDS = 2.0
ACCOUNT_CASE_RERUN_IDEMPOTENCY_SCOPE = "account_case_rerun"
ACCOUNT_CASE_RERUN_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
ACCOUNT_RERUN_STORAGE_ERROR_CODE = "account_storage_temporarily_unavailable"
ACCOUNT_RERUN_STORAGE_ERROR_MESSAGE = (
    "Account Case reprocessing is temporarily unavailable because the ticket database "
    "cannot be reached. Please retry in a moment."
)


def _account_rerun_storage_http_exception(exc: Exception) -> HTTPException:
    LOGGER.warning("Account rerun could not access the ticket database: %s", exc)
    return HTTPException(
        status_code=503,
        detail={
            "code": ACCOUNT_RERUN_STORAGE_ERROR_CODE,
            "message": ACCOUNT_RERUN_STORAGE_ERROR_MESSAGE,
            "retryable": True,
        },
        headers={"Retry-After": "5"},
    )


def _validated_account_case_rerun_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized or not ACCOUNT_CASE_RERUN_IDEMPOTENCY_KEY_RE.fullmatch(normalized):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_idempotency_key",
                "message": "Idempotency-Key must be 8-128 URL-safe ASCII characters.",
            },
        )
    return normalized


_ACCOUNT_REROUTE_INTERNAL_FIELDS = {
    "request_scope",
    "account_case_id",
    "idempotency_scope",
    "idempotency_key",
    "dispatch_status",
    "lease_token",
    "lease_expires_at",
    "result",
    "completed_case_ids",
}


def _public_account_reroute_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in job.items()
        if key not in _ACCOUNT_REROUTE_INTERNAL_FIELDS
    }


def _account_full_reroute_jobs(*, limit: int = 500) -> list[dict[str, Any]]:
    dedicated_jobs = ticket_repository.list_account_reroute_jobs(limit=limit)
    events = ticket_repository.list_ticket_events(ACCOUNT_FULL_REROUTE_JOB_TICKET_ID, limit=limit)
    legacy_jobs = [
        dict(event.get("payload") or {})
        for event in events
        if str(event.get("event_type") or "") == ACCOUNT_FULL_REROUTE_JOB_EVENT
        and isinstance(event.get("payload"), dict)
    ]
    by_job_id: dict[str, dict[str, Any]] = {}
    for job in dedicated_jobs:
        job_id = str(job.get("job_id") or "").strip()
        if job_id:
            by_job_id[job_id] = job
    for job in legacy_jobs:
        job_id = str(job.get("job_id") or "").strip()
        if job_id and job_id not in by_job_id:
            by_job_id[job_id] = job
    jobs = sorted(
        by_job_id.values(),
        key=lambda item: (
            str(item.get("updated_at") or ""),
            str(item.get("created_at") or ""),
        ),
        reverse=True,
    )
    return [_public_account_reroute_job(job) for job in jobs[:limit]]


def _account_full_reroute_job(job_id: str) -> dict[str, Any] | None:
    normalized = str(job_id or "").strip()
    dedicated_job = ticket_repository.get_account_reroute_job(normalized)
    if dedicated_job is not None:
        return _public_account_reroute_job(dedicated_job)
    return next(
        (job for job in _account_full_reroute_jobs() if str(job.get("job_id") or "") == normalized),
        None,
    )


def _account_full_reroute_job_is_active(job: dict[str, Any] | None) -> bool:
    if not job or str(job.get("status") or "") not in {"queued", "running"}:
        return False
    try:
        updated_at = datetime.fromisoformat(str(job.get("updated_at") or ""))
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - updated_at.astimezone(timezone.utc) < ACCOUNT_FULL_REROUTE_STALE_AFTER


def _save_account_full_reroute_job(
    job: dict[str, Any],
    *,
    lease_token: str | None = None,
) -> dict[str, Any]:
    if lease_token:
        return ticket_repository.update_account_reroute_job(
            job,
            lease_token=lease_token,
            lease_expires_at=(
                datetime.now(timezone.utc) + ACCOUNT_REROUTE_EXECUTION_LEASE
            ).isoformat(),
        )
    if ticket_repository.get_account_reroute_job(str(job.get("job_id") or "")) is not None:
        raise AccountRerouteLeaseLostError(
            f"Account reroute progress requires a lease for {job.get('job_id')}"
        )
    payload = {**job, "event": ACCOUNT_FULL_REROUTE_JOB_EVENT}
    ticket_repository.record_event(
        ACCOUNT_FULL_REROUTE_JOB_TICKET_ID,
        ACCOUNT_FULL_REROUTE_JOB_EVENT,
        payload,
    )
    return payload


def _fresh_rerun_delivery_key(payload: dict[str, Any], rerun_job_id: str) -> str:
    base_key = str(payload.get("delivery_key") or "automation").strip() or "automation"
    return f"{base_key}:rerun:{rerun_job_id}"


def _account_rerun_route_key(primary_label: str, secondary_label: str) -> str:
    primary = str(primary_label or "").strip()
    secondary = str(secondary_label or "").strip()
    if primary and secondary.startswith(f"{primary} /"):
        return secondary
    return " / ".join(item for item in (primary, secondary) if item) or "Unlabeled"


_ACCOUNT_RERUN_STORAGE_RETRY_DELAYS = (0.0, 1.0, 3.0, 8.0)
_ACCOUNT_RERUN_STORAGE_ERROR_MARKERS = (
    "pool acquire budget exhausted",
    "unexpected eof",
    "connection refused",
    "connection reset",
    "connection timeout",
    "server closed the connection",
    "could not connect",
    "ssl error",
)


def _is_retryable_account_rerun_storage_error(exc: BaseException) -> bool:
    if isinstance(exc, (psycopg.OperationalError, OSError, TimeoutError)):
        return True
    lowered = " ".join(str(exc or "").split()).lower()
    return any(marker in lowered for marker in _ACCOUNT_RERUN_STORAGE_ERROR_MARKERS)


def _complete_account_reroute_daemon_future(
    future: asyncio.Future[Any],
    result: Any,
    error: BaseException | None,
) -> None:
    if future.done():
        return
    if error is not None:
        future.set_exception(error)
        return
    future.set_result(result)


async def _account_reroute_daemon_thread_call(
    method: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()
    context = contextvars.copy_context()

    def invoke() -> None:
        result: Any = None
        error: BaseException | None = None
        try:
            result = context.run(method, *args, **kwargs)
        except BaseException as exc:
            error = exc
        try:
            loop.call_soon_threadsafe(
                _complete_account_reroute_daemon_future,
                future,
                result,
                error,
            )
        except RuntimeError:
            # The dispatcher event loop may already be gone during process shutdown.
            return

    thread = threading.Thread(
        target=invoke,
        name="account-reroute-sync-call",
        daemon=True,
    )
    thread.start()
    return await future


async def _account_reroute_sync_call(method: Any, *args: Any, **kwargs: Any) -> Any:
    if _ACCOUNT_REROUTE_DISPATCH_CONTEXT.get():
        return await _account_reroute_daemon_thread_call(method, *args, **kwargs)
    return await async_to_thread(method, *args, **kwargs)


async def _account_rerun_storage_call(method: Any, *args: Any, **kwargs: Any) -> Any:
    last_error: BaseException | None = None
    for attempt, delay in enumerate(_ACCOUNT_RERUN_STORAGE_RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await _account_reroute_sync_call(method, *args, **kwargs)
        except Exception as exc:
            last_error = exc
            if (
                not _is_retryable_account_rerun_storage_error(exc)
                or attempt >= len(_ACCOUNT_RERUN_STORAGE_RETRY_DELAYS) - 1
            ):
                raise
            LOGGER.warning(
                "Account rerun storage operation %s failed; retrying attempt %s/%s: %s",
                getattr(method, "__name__", repr(method)),
                attempt + 2,
                len(_ACCOUNT_RERUN_STORAGE_RETRY_DELAYS),
                exc,
            )
    if last_error is not None:
        raise last_error
    raise RuntimeError("Account rerun storage operation did not execute")


async def _save_account_full_reroute_job_with_retry(
    job: dict[str, Any],
    *,
    lease_token: str,
) -> dict[str, Any]:
    return await _account_rerun_storage_call(
        _save_account_full_reroute_job,
        job,
        lease_token=lease_token,
    )


async def _release_account_reroute_job_for_shutdown(
    job: dict[str, Any],
    *,
    lease_token: str,
) -> None:
    released_at = now_iso()
    job["updated_at"] = released_at
    await _account_rerun_storage_call(
        ticket_repository.release_account_reroute_job_execution,
        job,
        lease_token=lease_token,
        released_at=released_at,
    )


def _account_reroute_stop_requested(stop_event: threading.Event | None) -> bool:
    return stop_event is not None and stop_event.is_set()


async def _claim_and_run_account_reroute_job(
    job_id: str,
    *,
    stop_event: threading.Event | None = None,
) -> None:
    if _account_reroute_stop_requested(stop_event):
        return
    claimed_at = now_iso()
    lease_token = f"account-reroute-lease-{uuid4().hex}"
    claim = await _account_rerun_storage_call(
        ticket_repository.claim_account_reroute_job_execution,
        job_id,
        owner_token=lease_token,
        claimed_at=claimed_at,
        lease_expires_at=(
            datetime.now(timezone.utc) + ACCOUNT_REROUTE_EXECUTION_LEASE
        ).isoformat(),
    )
    if claim.get("status") != "acquired":
        return
    await _run_account_full_reroute_job(
        job_id,
        lease_token,
        stop_event=stop_event,
    )


async def _dispatch_pending_account_reroute_jobs_once(
    *,
    stop_event: threading.Event | None = None,
) -> int:
    if _account_reroute_stop_requested(stop_event):
        return 0
    jobs = await _account_rerun_storage_call(
        ticket_repository.list_dispatchable_account_reroute_jobs,
        as_of=now_iso(),
        limit=ACCOUNT_REROUTE_DISPATCH_BATCH_LIMIT,
    )
    attempted = 0
    for job in jobs:
        if _account_reroute_stop_requested(stop_event):
            break
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            continue
        attempted += 1
        try:
            await _claim_and_run_account_reroute_job(
                job_id,
                stop_event=stop_event,
            )
        except Exception:
            LOGGER.exception("Account reroute dispatcher failed job %s", job_id)
    return attempted


def _account_reroute_dispatch_poll_interval_seconds() -> float:
    configured = _safe_float_env(ACCOUNT_REROUTE_DISPATCH_POLL_INTERVAL_ENV, 5.0)
    return max(0.25, min(configured, 300.0))


def _account_reroute_dispatch_shutdown_timeout_seconds() -> float:
    configured = _safe_float_env(
        ACCOUNT_REROUTE_DISPATCH_SHUTDOWN_TIMEOUT_ENV,
        30.0,
    )
    return max(0.25, min(configured, 300.0))


async def _run_account_reroute_dispatch_loop() -> None:
    interval_seconds = _account_reroute_dispatch_poll_interval_seconds()
    LOGGER.info(
        "Account reroute dispatcher started with interval_seconds=%s.",
        interval_seconds,
    )
    while not _ACCOUNT_REROUTE_DISPATCH_STOP_EVENT.is_set():
        try:
            await _dispatch_pending_account_reroute_jobs_once(
                stop_event=_ACCOUNT_REROUTE_DISPATCH_STOP_EVENT,
            )
        except Exception:
            LOGGER.exception("Account reroute dispatcher scan failed")
        if _ACCOUNT_REROUTE_DISPATCH_STOP_EVENT.wait(interval_seconds):
            break
    LOGGER.info("Account reroute dispatcher stopped.")


def _run_account_reroute_dispatcher_thread() -> None:
    token = _ACCOUNT_REROUTE_DISPATCH_CONTEXT.set(True)
    try:
        asyncio.run(_run_account_reroute_dispatch_loop())
    finally:
        _ACCOUNT_REROUTE_DISPATCH_CONTEXT.reset(token)


def _start_account_reroute_dispatcher() -> threading.Thread:
    global _ACCOUNT_REROUTE_DISPATCH_THREAD
    with _ACCOUNT_REROUTE_DISPATCH_THREAD_LOCK:
        existing = _ACCOUNT_REROUTE_DISPATCH_THREAD
        if existing is not None and existing.is_alive():
            return existing
        _ACCOUNT_REROUTE_DISPATCH_STOP_EVENT.clear()
        thread = threading.Thread(
            target=_run_account_reroute_dispatcher_thread,
            name="account-reroute-dispatcher",
            daemon=True,
        )
        _ACCOUNT_REROUTE_DISPATCH_THREAD = thread
        thread.start()
        return thread


def _stop_account_reroute_dispatcher() -> bool:
    global _ACCOUNT_REROUTE_DISPATCH_THREAD
    _ACCOUNT_REROUTE_DISPATCH_STOP_EVENT.set()
    with _ACCOUNT_REROUTE_DISPATCH_THREAD_LOCK:
        thread = _ACCOUNT_REROUTE_DISPATCH_THREAD
    if thread is None:
        return True
    timeout_seconds = _account_reroute_dispatch_shutdown_timeout_seconds()
    thread.join(timeout=timeout_seconds)
    if thread.is_alive():
        LOGGER.error(
            "Account reroute dispatcher did not stop within %.3f seconds; "
            "its repositories must remain open until process exit.",
            timeout_seconds,
        )
        return False
    with _ACCOUNT_REROUTE_DISPATCH_THREAD_LOCK:
        if _ACCOUNT_REROUTE_DISPATCH_THREAD is thread:
            _ACCOUNT_REROUTE_DISPATCH_THREAD = None
    return True


def _account_case_for_identifier(identifier: str) -> dict[str, Any] | None:
    normalized = str(identifier or "").strip()
    if not normalized:
        return None
    account_case = ticket_repository.get_account_case(normalized)
    if isinstance(account_case, dict):
        return account_case
    account_case = ticket_repository.get_account_case_by_ticket_id(normalized)
    return account_case if isinstance(account_case, dict) else None


async def _enqueue_account_rerun_job(
    background_tasks: BackgroundTasks,
    *,
    target_case_ids: list[str] | None = None,
    idempotency_key: str | None = None,
    request_scope: str | None = None,
) -> dict[str, Any]:
    normalized_targets = list(dict.fromkeys(
        str(item or "").strip() for item in (target_case_ids or []) if str(item or "").strip()
    ))
    created_at = now_iso()
    single_case = bool(normalized_targets)
    job = {
        "job_id": f"account-rerun-{uuid4().hex}",
        "mode": "fresh_case_rerun",
        "scope": "single_case" if single_case else "all_cases",
        "target_case_ids": normalized_targets,
        "reset_mode": (
            ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY
            if single_case
            else ACCOUNT_RERUN_RESET_AI_ONLY
        ),
        "audit_actor_id": "account_ui" if single_case else "system",
        "requested_at": created_at,
        "build_ref": str(get_app_build_info().get("ref") or "unknown").strip() or "unknown",
        "status": "queued",
        "total": 0,
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "recovered": 0,
        "changed": 0,
        "route_counts": {},
        "handler_counts": {},
        "emails_sent": 0,
        "emails_skipped": 0,
        "emails_failed": 0,
        "replies_scheduled": 0,
        "replies_deleted": 0,
        "messages_deleted": 0,
        "customer_messages_retained": 0,
        "reply_jobs_deleted": 0,
        "reply_executions_deleted": 0,
        "customer_replies_cleared": 0,
        "persona_assignments_deleted": 0,
        "route_reviews_reset": 0,
        "route_corrections_cleared": 0,
        "new_replies_published": 0,
        "reply_job_ids": [],
        "reply_jobs_pending": 0,
        "reply_jobs_published": 0,
        "reply_jobs_manual_attention": 0,
        "reply_jobs_failed": 0,
        "reply_wait_timed_out": False,
        "wait_for_replies": True,
        "completed_case_ids": [],
        "failures": [],
        "recovered_cases": [],
        "created_at": created_at,
        "started_at": None,
        "updated_at": created_at,
        "completed_at": None,
    }
    claim = await _account_rerun_storage_call(
        ticket_repository.claim_account_case_rerun,
        job,
        job_ticket_id=ACCOUNT_FULL_REROUTE_JOB_TICKET_ID,
        event_type=ACCOUNT_FULL_REROUTE_JOB_EVENT,
        active_after=(datetime.now(timezone.utc) - ACCOUNT_FULL_REROUTE_STALE_AFTER).isoformat(),
        idempotency_scope=(ACCOUNT_CASE_RERUN_IDEMPOTENCY_SCOPE if idempotency_key else None),
        idempotency_key=idempotency_key,
        request_scope=request_scope or "POST:/api/account/rerun-jobs",
    )
    if claim.get("status") == "scope_conflict":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotency_scope_conflict",
                "message": "Idempotency-Key was already used for a different Account Case rerun.",
            },
        )
    if claim.get("status") == "active_conflict":
        raise HTTPException(status_code=409, detail="an Account rerun job is already running")
    canonical_job = claim.get("job")
    if not isinstance(canonical_job, dict):
        raise RuntimeError("Account rerun claim did not return a canonical job")
    dispatch_state = (
        str(canonical_job.get("status") or ""),
        str(canonical_job.get("dispatch_status") or ""),
    )
    if dispatch_state in {("queued", "queued"), ("running", "leased")}:
        background_tasks.add_task(
            _claim_and_run_account_reroute_job,
            canonical_job["job_id"],
        )
    return _public_account_reroute_job(canonical_job)


async def _record_account_rerun_terminal_audit(
    job: dict[str, Any],
    *,
    event_type: str,
    terminal_status: str,
    completed_at: str,
) -> None:
    if str(job.get("scope") or "") != "single_case":
        return
    target_case_id = next(
        (str(item or "").strip() for item in job.get("target_case_ids") or [] if str(item or "").strip()),
        "",
    )
    account_case = await _account_rerun_storage_call(
        _account_case_for_identifier,
        target_case_id,
    )
    ticket_number = str((account_case or {}).get("client_ticket_id") or "").strip()
    payload = {
        "audit_event_id": f"{event_type}:{job.get('job_id')}",
        "job_id": str(job.get("job_id") or "").strip(),
        "account_case_id": target_case_id,
        "ticket_number": ticket_number,
        "reset_mode": str(job.get("reset_mode") or "").strip(),
        "requested_at": str(job.get("requested_at") or job.get("created_at") or "").strip(),
        "completed_at": completed_at,
        "terminal_status": terminal_status,
        "build_ref": str(job.get("build_ref") or "unknown").strip() or "unknown",
        "processed": int(job.get("processed") or 0),
        "succeeded": int(job.get("succeeded") or 0),
        "failed": int(job.get("failed") or 0),
        "messages_deleted": int(job.get("messages_deleted") or 0),
        "customer_messages_retained": int(job.get("customer_messages_retained") or 0),
        "reply_jobs_deleted": int(job.get("reply_jobs_deleted") or 0),
        "reply_executions_deleted": int(job.get("reply_executions_deleted") or 0),
        "customer_replies_cleared": int(job.get("customer_replies_cleared") or 0),
        "persona_assignments_deleted": int(
            job.get("persona_assignments_deleted") or 0
        ),
        "route_reviews_reset": int(job.get("route_reviews_reset") or 0),
        "route_corrections_cleared": int(job.get("route_corrections_cleared") or 0),
        "emails_sent": int(job.get("emails_sent") or 0),
        "replies_scheduled": int(job.get("replies_scheduled") or 0),
        "route_counts": dict(job.get("route_counts") or {}),
        "handler_counts": dict(job.get("handler_counts") or {}),
    }
    await _account_rerun_storage_call(
        ticket_repository.record_workspace_audit_event,
        event_type,
        actor_id="account_ui",
        target_id=target_case_id or None,
        payload=payload,
        created_at=completed_at,
    )


def _account_rerun_reply_wait_timeout_seconds() -> float:
    return max(30.0, _safe_float_env("ACCOUNT_RERUN_REPLY_WAIT_TIMEOUT_SECONDS", 900.0))


async def _wait_for_account_rerun_replies(
    job: dict[str, Any],
    *,
    lease_token: str,
    stop_event: threading.Event | None = None,
) -> bool:
    if _account_reroute_stop_requested(stop_event):
        return False
    reply_job_ids = [str(item).strip() for item in job.get("reply_job_ids") or [] if str(item).strip()]
    if not reply_job_ids or not bool(job.get("wait_for_replies")):
        job["reply_jobs_pending"] = 0
        return True
    terminal_statuses = {"published", "manual_attention", "cancelled", "failed"}
    deadline = time.monotonic() + _account_rerun_reply_wait_timeout_seconds()
    while True:
        jobs = [
            await _account_rerun_storage_call(ticket_repository.get_account_reply_job, item)
            for item in reply_job_ids
        ]
        pending = [item for item in jobs if item is not None and str(item.get("status") or "") not in terminal_statuses]
        missing = [item for item in jobs if item is None]
        job["reply_jobs_pending"] = len(pending) + len(missing)
        job["reply_jobs_published"] = sum(1 for item in jobs if item and item.get("status") == "published")
        job["new_replies_published"] = job["reply_jobs_published"]
        job["reply_jobs_manual_attention"] = sum(1 for item in jobs if item and item.get("status") == "manual_attention")
        job["reply_jobs_failed"] = sum(1 for item in jobs if item and item.get("status") == "failed")
        job["updated_at"] = now_iso()
        await _save_account_full_reroute_job_with_retry(job, lease_token=lease_token)
        if not pending and not missing:
            return True
        if time.monotonic() >= deadline:
            job["reply_wait_timed_out"] = True
            job["failures"].append({"scope": "replies", "error": "reply jobs did not reach a terminal state before timeout"})
            return True
        if stop_event is None:
            await asyncio.sleep(ACCOUNT_REROUTE_REPLY_POLL_INTERVAL_SECONDS)
        elif await _account_reroute_sync_call(
            stop_event.wait,
            ACCOUNT_REROUTE_REPLY_POLL_INTERVAL_SECONDS,
        ):
            return False


async def _reconcile_account_rerun_failures(job: dict[str, Any]) -> None:
    failures = [
        item
        for item in job.get("failures") or []
        if isinstance(item, dict) and item.get("account_case_id")
    ]
    for failure in failures:
        if failure.get("recovered") is True:
            continue
        case_id = str(failure.get("account_case_id") or "").strip()
        client_ticket_id = str(failure.get("client_ticket_id") or "").strip()
        try:
            account_case = await _account_rerun_storage_call(
                ticket_repository.get_account_case,
                case_id,
            )
            if not isinstance(account_case, dict):
                continue
            client_ticket_id = client_ticket_id or str(account_case.get("client_ticket_id") or "").strip()
            route_executions = await _account_rerun_storage_call(
                ticket_repository.list_account_route_executions,
                client_ticket_id,
            )
            has_rerun_route = any(
                isinstance(execution, dict)
                and str(execution.get("rerun_job_id") or "") == str(job.get("job_id") or "")
                for execution in route_executions or []
            )
            if not has_rerun_route:
                continue

            email_payload = account_case.get("internal_email_payload")
            email_delivery_key = str(
                email_payload.get("delivery_key")
                if isinstance(email_payload, dict)
                else ""
            )
            email_expected = bool(failure.get("email_expected")) or (
                ":rerun:" in email_delivery_key
                and str(account_case.get("internal_email_send_status") or "") == "sent"
            )
            if email_expected and str(account_case.get("internal_email_send_status") or "") != "sent":
                continue

            reply_job_id = str(failure.get("reply_job_id") or "").strip()
            reply_job = (
                await _account_rerun_storage_call(ticket_repository.get_account_reply_job, reply_job_id)
                if reply_job_id
                else await _account_rerun_storage_call(
                    ticket_repository.get_latest_account_reply_job,
                    client_ticket_id,
                )
            )
            reply_expected = bool(failure.get("reply_expected")) or (
                isinstance(reply_job, dict)
                and str((reply_job.get("payload") or {}).get("rerun_job_id") or "")
                == str(job.get("job_id") or "")
            )
            if reply_expected and (
                not isinstance(reply_job, dict)
                or str(reply_job.get("status") or "") not in {"published", "manual_attention"}
            ):
                continue

            primary_label, secondary_label = account_case_labels(account_case)
            route_key = _account_rerun_route_key(primary_label, secondary_label)
            handler_status = str(failure.get("handler_status") or "") or (
                "completed" if str(account_case.get("route_status") or "") == "automated" else "not_automated"
            )
            failure.update(
                {
                    "recovered": True,
                    "recovered_at": now_iso(),
                    "recovery_reason": "all persisted rerun outputs are present",
                    "client_ticket_id": client_ticket_id,
                    "reply_job_id": str((reply_job or {}).get("job_id") or reply_job_id),
                }
            )
            job["failed"] = max(0, int(job.get("failed") or 0) - 1)
            job["succeeded"] = int(job.get("succeeded") or 0) + 1
            job["recovered"] = int(job.get("recovered") or 0) + 1
            job["changed"] += int(bool(failure.get("changed", True)))
            job["route_counts"][route_key] = int(job["route_counts"].get(route_key) or 0) + 1
            job["handler_counts"][handler_status] = int(job["handler_counts"].get(handler_status) or 0) + 1
            job.setdefault("recovered_cases", []).append(case_id)
        except Exception as exc:
            failure["reconciliation_error"] = str(exc)[:500]
            LOGGER.warning("Could not reconcile Account rerun case %s: %s", case_id, exc)


async def _run_account_full_reroute_job(
    job_id: str,
    lease_token: str | None = None,
    *,
    stop_event: threading.Event | None = None,
) -> None:
    if not lease_token:
        claimed_at = now_iso()
        candidate_token = f"account-reroute-lease-{uuid4().hex}"
        claim = await _account_rerun_storage_call(
            ticket_repository.claim_account_reroute_job_execution,
            job_id,
            owner_token=candidate_token,
            claimed_at=claimed_at,
            lease_expires_at=(
                datetime.now(timezone.utc) + ACCOUNT_REROUTE_EXECUTION_LEASE
            ).isoformat(),
        )
        if claim.get("status") != "acquired":
            return
        lease_token = candidate_token
    job = await _account_rerun_storage_call(
        ticket_repository.get_account_reroute_job,
        job_id,
    )
    if job is None:
        return
    resume_phase = str(job.get("phase") or "").strip()
    started_at = str(job.get("started_at") or "").strip() or now_iso()
    job.update(status="running", started_at=started_at, updated_at=now_iso())
    if resume_phase != "Waiting for replies":
        job["phase"] = "Routing and extracting"
    await _save_account_full_reroute_job_with_retry(job, lease_token=lease_token)
    try:
        if _account_reroute_stop_requested(stop_event):
            await _release_account_reroute_job_for_shutdown(
                job,
                lease_token=lease_token,
            )
            return
        if resume_phase == "Waiting for replies":
            cases: list[dict[str, Any]] = []
        else:
            target_case_ids = {
                str(item or "").strip()
                for item in list(job.get("target_case_ids") or [])
                if str(item or "").strip()
            }
            if target_case_ids:
                cases = []
                for target_case_id in target_case_ids:
                    account_case = await _account_rerun_storage_call(
                        _account_case_for_identifier,
                        target_case_id,
                    )
                    if isinstance(account_case, dict):
                        cases.append(account_case)
            else:
                cases = await _account_rerun_storage_call(
                    ticket_repository.list_account_cases,
                    limit=100_000,
                    offset=0,
                )
            job["total"] = max(int(job.get("total") or 0), len(cases))
            job["updated_at"] = now_iso()
            await _save_account_full_reroute_job_with_retry(job, lease_token=lease_token)
        completed_case_ids = list(dict.fromkeys(
            str(item or "").strip()
            for item in job.get("completed_case_ids") or []
            if str(item or "").strip()
        ))
        completed_case_id_set = set(completed_case_ids)
        job["completed_case_ids"] = completed_case_ids
        for account_case in cases:
            case_id = str(
                account_case.get("account_case_id")
                or account_case.get("billing_ticket_id")
                or account_case.get("client_ticket_id")
                or "unknown"
            )
            if case_id in completed_case_id_set:
                continue
            if _account_reroute_stop_requested(stop_event):
                await _release_account_reroute_job_for_shutdown(
                    job,
                    lease_token=lease_token,
                )
                return
            client_ticket_id = str(account_case.get("client_ticket_id") or "").strip()
            case_stage = "started"
            case_reply_expected = False
            case_email_expected = False
            case_delivery_key = ""
            case_reply_job_id = ""
            case_changed = False
            case_route_key = ""
            case_handler_status = ""
            try:
                canonical_ticket = await _account_rerun_storage_call(
                    ticket_repository.get_ticket,
                    client_ticket_id,
                )
                if canonical_ticket is None:
                    raise ValueError("canonical support ticket not found")
                case_stage = "reset"
                reset_counts = await _account_rerun_storage_call(
                    ticket_repository.reset_account_rerun_state,
                    client_ticket_id,
                    reset_at=now_iso(),
                    rerun_job_id=job_id,
                    reset_mode=str(job.get("reset_mode") or ACCOUNT_RERUN_RESET_AI_ONLY),
                    clear_persona_assignment=True,
                    audit_context={
                        "account_case_id": case_id,
                        "ticket_number": client_ticket_id,
                        "requested_at": job.get("requested_at") or job.get("created_at"),
                        "build_ref": job.get("build_ref"),
                    },
                )
                for stat_name, count_key in (
                    ("replies_deleted", "messages_deleted"),
                    ("messages_deleted", "messages_deleted"),
                    ("customer_messages_retained", "customer_messages_retained"),
                    ("reply_jobs_deleted", "reply_jobs_deleted"),
                    ("reply_executions_deleted", "reply_executions_deleted"),
                    ("customer_replies_cleared", "customer_replies_cleared"),
                    ("persona_assignments_deleted", "persona_assignments_deleted"),
                    ("route_reviews_reset", "route_review_reset"),
                    ("route_corrections_cleared", "route_correction_cleared"),
                ):
                    count_value = reset_counts.get(count_key)
                    if count_key == "messages_deleted" and count_value is None:
                        count_value = reset_counts.get("ai_messages_deleted")
                    job[stat_name] = int(job.get(stat_name) or 0) + int(
                        count_value or 0
                    )
                # Reset removes Account AI messages before routing so the latest
                # assistant context cannot influence the fresh classification.
                canonical_ticket = await _account_rerun_storage_call(
                    ticket_repository.get_ticket,
                    client_ticket_id,
                )
                if canonical_ticket is None:
                    raise ValueError("canonical support ticket disappeared during rerun reset")
                reset_account_case = await _account_rerun_storage_call(
                    ticket_repository.get_account_case,
                    case_id,
                )
                if not isinstance(reset_account_case, dict):
                    raise ValueError("Account Case disappeared during rerun reset")
                account_case = reset_account_case
                case_stage = "routed"
                result = await _account_reroute_sync_call(
                    reprocess_account_case,
                    account_case,
                    ticket=canonical_ticket,
                    fresh=True,
                )
                updated_case = dict(result.account_case)
                if str(job.get("scope") or "") == "single_case":
                    classification = dict(updated_case.get("route_classification") or {})
                    classification["classification_source"] = "single_case_rerun"
                    classification["rerun_trigger"] = "single_case_rerun"
                    updated_case["route_classification"] = classification
                    result.route_execution["classification"] = classification
                    result.route_execution["trigger"] = "single_case_rerun"
                updated_case["automation_context"] = {
                    **dict(updated_case.get("automation_context") or {}),
                    "rerun_job_id": job_id,
                    "rerun_mode": "fresh_case_rerun",
                }
                result.route_execution["rerun_job_id"] = job_id
                result.route_execution["rerun_mode"] = "fresh_case_rerun"
                case_changed = bool(result.changed)
                primary_label, secondary_label = account_case_labels(updated_case)
                case_route_key = _account_rerun_route_key(primary_label, secondary_label)
                case_handler_status = str(result.handler_status or "")
                persona: dict[str, Any] | None = None
                needs_persona = result.reply_kind in {
                    "field_follow_up",
                    "submission_confirmation",
                }
                if needs_persona:
                    try:
                        persona = await _account_rerun_storage_call(
                            ticket_repository.resolve_account_persona,
                            client_ticket_id,
                        )
                    except AccountPersonaUnavailableError as exc:
                        updated_case, route_execution = _rerun_account_persona_unavailable_human_review(
                            account_case=updated_case,
                            route_execution=result.route_execution,
                            reason=str(exc),
                        )
                        case_stage = "human_review"
                        case_changed = True
                        primary_label, secondary_label = account_case_labels(updated_case)
                        case_route_key = _account_rerun_route_key(primary_label, secondary_label)
                        case_handler_status = "human_review"
                        await _account_rerun_storage_call(
                            ticket_repository.save_account_case,
                            updated_case,
                        )
                        await _account_rerun_storage_call(
                            ticket_repository.save_account_route_execution,
                            route_execution,
                        )
                        job["route_counts"][case_route_key] = int(
                            job["route_counts"].get(case_route_key) or 0
                        ) + 1
                        job["handler_counts"][case_handler_status] = int(
                            job["handler_counts"].get(case_handler_status) or 0
                        ) + 1
                        job["changed"] += int(case_changed)
                        job["succeeded"] += 1
                        continue
                await _account_rerun_storage_call(ticket_repository.save_account_case, updated_case)
                await _account_rerun_storage_call(
                    ticket_repository.save_account_route_execution,
                    result.route_execution,
                )

                reply_ready = result.reply_kind == "field_follow_up"
                delivery_key = ""
                if result.internal_email_to_send and result.email_handler:
                    case_stage = "email"
                    case_email_expected = True
                    job["phase"] = "Sending internal emails"
                    job["updated_at"] = now_iso()
                    await _save_account_full_reroute_job_with_retry(
                        job,
                        lease_token=lease_token,
                    )
                    fresh_email = copy.deepcopy(result.internal_email_to_send)
                    fresh_email["delivery_key"] = _fresh_rerun_delivery_key(fresh_email, job_id)
                    updated_case["internal_email_payload"] = fresh_email
                    sender = {
                        "billing": _send_billing_internal_email_attempt,
                        "enablement": _send_enablement_internal_email_attempt,
                        "quota": _send_quota_internal_email_attempt,
                    }.get(result.email_handler)
                    if sender is None:
                        raise ValueError(f"registered handler has no sender: {result.email_handler}")
                    attempt = {
                        "internal_email_to_send": fresh_email,
                        "internal_email_send_status": updated_case.get("internal_email_send_status"),
                        "internal_email_send_reason": updated_case.get("internal_email_send_reason"),
                    }
                    send_status, send_reason = await sender(attempt)
                    updated_case["internal_email_send_status"] = send_status
                    updated_case["internal_email_send_reason"] = send_reason
                    if send_status != "sent":
                        failure_reason = reconciliation_reason_code(
                            handler=result.email_handler or "automation",
                            phase="internal_email",
                            detail=send_status or "failed",
                        )
                        updated_case = reconcile_automation_execution_failure(
                            updated_case,
                            reason_code=failure_reason,
                            context={"rerun_job_id": job_id},
                        )
                        updated_case["execution_reason_code"] = failure_reason
                        case_handler_status = "human_review"
                        reply_ready = False
                    updated_case["updated_at"] = now_iso()
                    # Persist a successful delivery before creating the reply job. If Persona
                    # preparation or job creation fails, the delivery retry poller must not send
                    # the same internal email again.
                    await _account_rerun_storage_call(ticket_repository.save_account_case, updated_case)
                    if send_status == "sent":
                        job["emails_sent"] += 1
                        reply_ready = result.reply_kind == "submission_confirmation"
                        delivery_key = str(fresh_email.get("delivery_key") or "")
                        case_delivery_key = delivery_key
                    else:
                        job["emails_failed"] += 1
                elif str(updated_case.get("internal_email_send_status") or "") == "sent":
                    job["emails_skipped"] += 1

                case_reply_expected = bool(reply_ready)
                if reply_ready:
                    case_stage = "reply"
                    job["phase"] = "Scheduling Persona replies"
                    job["updated_at"] = now_iso()
                    await _save_account_full_reroute_job_with_retry(
                        job,
                        lease_token=lease_token,
                    )
                    trigger_message_created_at = latest_customer_message_created_at(canonical_ticket)
                    if not trigger_message_created_at:
                        raise ValueError(
                            "latest customer message created_at is required to schedule Persona reply"
                        )
                    reply_facts = _automation_reply_facts(
                        handler=result.email_handler or "automation",
                        action=str(updated_case.get("execution_action") or "automation"),
                        missing_fields=list(updated_case.get("missing_fields") or []),
                        collected_fields=dict(updated_case.get("collected_fields") or {}),
                        submitted=result.reply_kind == "submission_confirmation",
                        customer_name=str(updated_case.get("customer_name") or ""),
                    )
                    existing_reply_job = await _account_rerun_storage_call(
                        ticket_repository.get_latest_account_reply_job,
                        client_ticket_id,
                    )
                    existing_payload = (
                        existing_reply_job.get("payload")
                        if isinstance(existing_reply_job, dict)
                        and isinstance(existing_reply_job.get("payload"), dict)
                        else {}
                    )
                    existing_matches = (
                        isinstance(existing_reply_job, dict)
                        and str(existing_reply_job.get("trigger_message_created_at") or "")
                        == str(trigger_message_created_at)
                        and str(existing_payload.get("rerun_job_id") or "") == job_id
                    )
                    reply_job = existing_reply_job if existing_matches else await _account_rerun_storage_call(
                        _create_account_reply_job,
                        ticket_id=client_ticket_id,
                        trigger_message_created_at=trigger_message_created_at,
                        draft_content="",
                        reply_facts=reply_facts,
                        asked_field_keys=list(result.asked_field_keys),
                        persona_assignment=persona,
                        automation_delivery_key=delivery_key or None,
                        rerun_job_id=job_id,
                    )
                    case_reply_job_id = str(reply_job.get("job_id") or "")
                    if case_reply_job_id and case_reply_job_id not in job.setdefault("reply_job_ids", []):
                        job["reply_job_ids"].append(case_reply_job_id)
                        job["replies_scheduled"] += 1
                    if delivery_key and isinstance(updated_case.get("internal_email_payload"), dict):
                        updated_case["internal_email_payload"]["customer_confirmation_queued"] = True

                case_stage = "finalizing"
                await _account_rerun_storage_call(ticket_repository.save_account_case, updated_case)
                job["route_counts"][case_route_key] = int(job["route_counts"].get(case_route_key) or 0) + 1
                job["handler_counts"][case_handler_status] = (
                    int(job["handler_counts"].get(case_handler_status) or 0) + 1
                )
                job["changed"] += int(case_changed)
                job["succeeded"] += 1
            except AccountRerouteLeaseLostError:
                raise
            except Exception as exc:
                LOGGER.exception("Account full reroute failed for %s", case_id)
                if client_ticket_id and not case_reply_job_id:
                    try:
                        await _account_rerun_storage_call(
                            ticket_repository.cancel_pending_account_reply_jobs,
                            client_ticket_id,
                            updated_at=now_iso(),
                            rerun_job_id=job_id,
                        )
                    except Exception:
                        LOGGER.exception("Could not cancel stale Account reply jobs for %s", case_id)
                job["failed"] += 1
                if len(job["failures"]) < 50:
                    job["failures"].append(
                        {
                            "account_case_id": case_id,
                            "client_ticket_id": client_ticket_id,
                            "error": str(exc)[:500],
                            "retryable": _is_retryable_account_rerun_storage_error(exc),
                            "stage": case_stage,
                            "reply_expected": case_reply_expected,
                            "email_expected": case_email_expected,
                            "delivery_key": case_delivery_key,
                            "reply_job_id": case_reply_job_id,
                            "changed": case_changed,
                            "route_key": case_route_key,
                            "handler_status": case_handler_status,
                        }
                    )
            finally:
                if case_id not in completed_case_id_set:
                    completed_case_id_set.add(case_id)
                    completed_case_ids.append(case_id)
                    job["completed_case_ids"] = completed_case_ids
                    job["processed"] += 1
                job["updated_at"] = now_iso()
                await _save_account_full_reroute_job_with_retry(
                    job,
                    lease_token=lease_token,
                )
                if _account_reroute_stop_requested(stop_event):
                    await _release_account_reroute_job_for_shutdown(
                        job,
                        lease_token=lease_token,
                    )
                    return
        job["phase"] = "Waiting for replies"
        job["updated_at"] = now_iso()
        await _save_account_full_reroute_job_with_retry(
            job,
            lease_token=lease_token,
        )
        if _account_reroute_stop_requested(stop_event):
            await _release_account_reroute_job_for_shutdown(
                job,
                lease_token=lease_token,
            )
            return
        replies_finished = await _wait_for_account_rerun_replies(
            job,
            lease_token=lease_token,
            stop_event=stop_event,
        )
        if not replies_finished or _account_reroute_stop_requested(stop_event):
            await _release_account_reroute_job_for_shutdown(
                job,
                lease_token=lease_token,
            )
            return
        await _reconcile_account_rerun_failures(job)
        completed_at = now_iso()
        terminal_status = (
            "completed_with_errors"
            if (
                job["failed"]
                or job.get("reply_wait_timed_out")
                or job.get("reply_jobs_manual_attention")
                or job.get("reply_jobs_failed")
            )
            else "completed"
        )
        await _record_account_rerun_terminal_audit(
            job,
            event_type=(
                "account_case_full_rerun_completed"
                if terminal_status == "completed"
                else "account_case_full_rerun_failed"
            ),
            terminal_status=terminal_status,
            completed_at=completed_at,
        )
        job.update(
            status=terminal_status,
            completed_at=completed_at,
            updated_at=completed_at,
        )
        await _save_account_full_reroute_job_with_retry(
            job,
            lease_token=lease_token,
        )
    except AccountRerouteLeaseLostError:
        LOGGER.warning("Account reroute worker lost its execution lease for %s", job_id)
        return
    except Exception as exc:
        LOGGER.exception("Account full reroute job failed")
        failed_at = now_iso()
        audit_persistence_failed = False
        if str(job.get("scope") or "") == "single_case":
            try:
                await _record_account_rerun_terminal_audit(
                    job,
                    event_type="account_case_full_rerun_failed",
                    terminal_status="failed",
                    completed_at=failed_at,
                )
            except Exception:
                audit_persistence_failed = True
                LOGGER.exception("Could not persist Account single-case rerun failure audit")
        job.update(
            status="failed",
            error=str(exc)[:1000],
            audit_persistence_failed=audit_persistence_failed,
            completed_at=failed_at,
            updated_at=failed_at,
        )
        await _save_account_full_reroute_job_with_retry(
            job,
            lease_token=lease_token,
        )


@app.post("/api/account/rerun-jobs", status_code=202)
@app.post("/api/account/reroute-jobs", status_code=202, include_in_schema=False)
async def create_account_full_rerun_job(background_tasks: BackgroundTasks) -> dict[str, Any]:
    try:
        return await _enqueue_account_rerun_job(background_tasks)
    except (psycopg.Error, OSError, TimeoutError) as exc:
        raise _account_rerun_storage_http_exception(exc) from exc


@app.post("/api/account/cases/{account_case_id}/rerun", status_code=202)
@app.post(
    "/api/account/billing-tickets/{account_case_id}/rerun",
    status_code=202,
    deprecated=True,
    include_in_schema=False,
)
async def create_account_case_rerun_job(
    account_case_id: str,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    normalized_idempotency_key = _validated_account_case_rerun_idempotency_key(idempotency_key)
    normalized_case_id = str(account_case_id or "").strip()
    try:
        account_case = await _account_rerun_storage_call(
            _account_case_for_identifier,
            normalized_case_id,
        )
        if not isinstance(account_case, dict):
            raise HTTPException(status_code=404, detail="account case not found")
        canonical_case_id = str(
            account_case.get("account_case_id")
            or account_case.get("billing_ticket_id")
            or normalized_case_id
        ).strip()
        return await _enqueue_account_rerun_job(
            background_tasks,
            target_case_ids=[canonical_case_id],
            idempotency_key=normalized_idempotency_key,
            request_scope=f"POST:/api/account/cases/{canonical_case_id}/rerun",
        )
    except HTTPException:
        raise
    except (psycopg.Error, OSError, TimeoutError) as exc:
        raise _account_rerun_storage_http_exception(exc) from exc


@app.get("/api/account/rerun-jobs/latest")
@app.get("/api/account/reroute-jobs/latest", include_in_schema=False)
def get_latest_account_rerun_job() -> dict[str, Any]:
    return next(iter(_account_full_reroute_jobs(limit=1)), {"status": "not_started"})


@app.get("/api/account/rerun-jobs/{job_id}")
@app.get("/api/account/reroute-jobs/{job_id}", include_in_schema=False)
def get_account_rerun_job(job_id: str) -> dict[str, Any]:
    job = _account_full_reroute_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="rerun job not found")
    return job


@app.get("/api/account/cases/{billing_ticket_id}")
@app.get("/api/account/billing-tickets/{billing_ticket_id}", deprecated=True)
def get_billing_ticket(billing_ticket_id: str) -> dict[str, Any]:
    details = ticket_repository.get_account_case_details([billing_ticket_id])
    bundle = details.get(str(billing_ticket_id).strip())
    if bundle is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    return _build_account_case_detail(bundle)


@app.post("/api/account/cases/batch-details")
def get_account_case_batch_details(request: AccountCaseBatchDetailRequest) -> dict[str, Any]:
    if any(len(str(case_id or "").strip()) > 128 for case_id in request.case_ids):
        raise HTTPException(status_code=422, detail="account case ids must not exceed 128 characters")
    case_ids = list(
        dict.fromkeys(
            str(case_id or "").strip()
            for case_id in request.case_ids
            if str(case_id or "").strip()
        )
    )
    if not case_ids:
        raise HTTPException(status_code=422, detail="at least one account case id is required")
    bundles = ticket_repository.get_account_case_details(case_ids)
    details = [
        _build_account_case_detail(bundles[case_id])
        for case_id in case_ids
        if case_id in bundles
    ]
    return {
        "details": details,
        "missing_case_ids": [case_id for case_id in case_ids if case_id not in bundles],
    }


async def _load_account_billing_ticket(identifier: str) -> dict[str, Any]:
    ticket = await async_to_thread(ticket_repository.get_account_case, identifier)
    if ticket is None and not str(identifier).startswith("BT-"):
        ticket = await async_to_thread(ticket_repository.get_account_case_by_ticket_id, identifier)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    return ticket


def _original_route_tuple_from_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    return {
        "original_scope_label": ticket.get("scope_label"),
        "original_route_family": ticket.get("route_family"),
        "original_execution_action": ticket.get("execution_action") or ticket.get("route"),
        "original_tooling_profile": ticket.get("tooling_profile"),
        "original_route_reason": ticket.get("route_reason"),
        "original_route_confidence": ticket.get("route_confidence"),
    }


@app.post("/api/account/cases/{billing_ticket_id}/route-correction")
@app.post("/api/account/billing-tickets/{billing_ticket_id}/route-correction", deprecated=True)
async def correct_billing_ticket_route(
    billing_ticket_id: str,
    request: BillingRouteCorrectionRequest,
) -> dict[str, Any]:
    category = str(request.category or "").strip().lower()
    scope_label = request.scope_label
    execution_action = request.execution_action
    if category:
        if category not in {
            "automation",
            "backend_operation",
            "account_billing",
            "security_compliance",
            "human_review",
        }:
            raise HTTPException(status_code=400, detail=f"invalid category: {request.category!r}")
        execution_action = request.subcategory
        normalized_action = str(execution_action or "").strip().lower()
        if category == "security_compliance":
            scope_label = "security_compliance"
            execution_action = "human_review_required"
        elif category == "account_billing":
            if normalized_action == "account_suspension":
                scope_label = "account_suspension"
                execution_action = "human_review_required"
            else:
                scope_label = "account_billing"
                execution_action = (
                    normalized_action
                    if normalized_action in {"fraud_account", "detailed_invoice"}
                    else "human_review_required"
                )
        elif category in {"automation", "backend_operation"}:
            if normalized_action in {"enablement", "quota"}:
                scope_label = "backend_operation"
            elif normalized_action in {"account_verification", "fraud_account", "detailed_invoice"}:
                scope_label = "account_billing"
                execution_action = "fraud_account" if normalized_action == "account_verification" else normalized_action
            elif normalized_action == "unregistered":
                scope_label = "backend_operation"
                execution_action = "human_review_required"
            else:
                scope_label = "backend_operation"
        else:
            human_review_scope = {
                "unregistered": "backend_operation",
                "uncategorized": "uncategorized",
                "uncertain": "uncertain",
                "non_agora": "non_agora",
                "other": "human_review",
            }
            scope_label = human_review_scope.get(normalized_action, "human_review")
            execution_action = "human_review_required"
    if not scope_label or not execution_action:
        raise HTTPException(
            status_code=400,
            detail="provide category/subcategory or scope_label/execution_action",
        )
    try:
        corrected = validate_route_correction(
            scope_label=scope_label,
            execution_action=execution_action,
        )
    except RouteCorrectionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    billing_ticket = await _load_account_billing_ticket(billing_ticket_id)
    canonical_billing_ticket_id = str(billing_ticket.get("billing_ticket_id") or "").strip()
    if not canonical_billing_ticket_id:
        raise HTTPException(status_code=404, detail="ticket not found")

    timestamp = now_iso()
    first_corrected = {
        "first_corrected_scope_label": corrected["scope_label"],
        "first_corrected_route_family": corrected["route_family"],
        "first_corrected_execution_action": corrected["execution_action"],
        "first_corrected_tooling_profile": corrected["tooling_profile"],
    }
    correction = {
        "billing_ticket_id": canonical_billing_ticket_id,
        "client_ticket_id": str(billing_ticket.get("client_ticket_id") or "").strip(),
        **_original_route_tuple_from_ticket(billing_ticket),
        "corrected_scope_label": corrected["scope_label"],
        "corrected_route_family": corrected["route_family"],
        "corrected_execution_action": corrected["execution_action"],
        "corrected_tooling_profile": corrected["tooling_profile"],
        **first_corrected,
        "corrector": " ".join(str(request.corrector or "operator").split()).strip() or "operator",
        "created_at": timestamp,
        "updated_at": timestamp,
    }

    active_route = {
        "route": corrected["execution_action"],
        "scope_label": corrected["scope_label"],
        "route_family": corrected["route_family"],
        "execution_action": corrected["execution_action"],
        "tooling_profile": corrected["tooling_profile"],
        "category": corrected["category"],
        "subcategory": corrected["subcategory"],
        "route_status": corrected["route_status"],
        "automation_handler": corrected["automation_handler"],
        "route_classification": classification_for_corrected_route(
            scope_label=corrected["scope_label"],
            route_family=corrected["route_family"],
            execution_action=corrected["execution_action"],
            subcategory=corrected.get("account_billing_subcategory"),
            previous=billing_ticket.get("route_classification"),
        ),
        "updated_at": timestamp,
    }
    try:
        persisted_correction = await async_to_thread(
            ticket_repository.apply_billing_route_correction,
            billing_ticket_id=canonical_billing_ticket_id,
            active_route=active_route,
            correction=correction,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="ticket not found") from None

    client_ticket_id = str(billing_ticket.get("client_ticket_id") or "").strip()
    event = {
        "event": "route_corrected",
        "billing_ticket_id": canonical_billing_ticket_id,
        "ticket_id": client_ticket_id,
        "corrector": persisted_correction.get("corrector") or correction["corrector"],
        "created_at": timestamp,
        "original_scope_label": persisted_correction.get("original_scope_label"),
        "original_route_family": persisted_correction.get("original_route_family"),
        "original_execution_action": persisted_correction.get("original_execution_action"),
        "original_tooling_profile": persisted_correction.get("original_tooling_profile"),
        "corrected_scope_label": persisted_correction.get("corrected_scope_label"),
        "corrected_route_family": persisted_correction.get("corrected_route_family"),
        "corrected_execution_action": persisted_correction.get("corrected_execution_action"),
        "corrected_tooling_profile": persisted_correction.get("corrected_tooling_profile"),
        "category": corrected["category"],
        "subcategory": corrected["subcategory"],
        "route_status": corrected["route_status"],
        "automation_handler": corrected["automation_handler"],
        "correction_count": persisted_correction.get("correction_count") or 1,
    }
    await async_to_thread(ticket_repository.record_event, client_ticket_id, "route_corrected", event)
    await dispatch_event(["engineer", "dashboard"], event)

    refreshed = await async_to_thread(ticket_repository.get_account_case, canonical_billing_ticket_id)
    return get_billing_ticket(canonical_billing_ticket_id) if refreshed is not None else {
        **billing_ticket,
        **_build_account_ticket_view_model(billing_ticket),
    }


@app.post("/api/account/cases/{billing_ticket_id}/route-review")
@app.post("/api/account/billing-tickets/{billing_ticket_id}/route-review", deprecated=True)
async def review_billing_ticket_route(
    billing_ticket_id: str,
    request: BillingRouteReviewRequest,
) -> dict[str, Any]:
    normalized_status = str(request.review_status or "").strip()
    if normalized_status not in {"pending", "reviewed"}:
        raise HTTPException(
            status_code=400,
            detail=f"invalid review_status: {request.review_status!r} (expected 'pending' or 'reviewed')",
        )

    billing_ticket = await _load_account_billing_ticket(billing_ticket_id)
    canonical_billing_ticket_id = str(billing_ticket.get("billing_ticket_id") or "").strip()
    if not canonical_billing_ticket_id:
        raise HTTPException(status_code=404, detail="ticket not found")

    reviewer = " ".join(str(request.reviewer or "operator").split()).strip() or "operator"
    timestamp = now_iso()

    try:
        updated_ticket = await async_to_thread(
            ticket_repository.mark_billing_route_reviewed,
            billing_ticket_id=canonical_billing_ticket_id,
            review_status=normalized_status,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="ticket not found") from None

    client_ticket_id = str(billing_ticket.get("client_ticket_id") or "").strip()
    event = {
        "event": "route_reviewed",
        "billing_ticket_id": canonical_billing_ticket_id,
        "ticket_id": client_ticket_id,
        "reviewer": reviewer,
        "review_status": normalized_status,
        "created_at": timestamp,
        "route": updated_ticket.get("route"),
        "scope_label": updated_ticket.get("scope_label"),
        "execution_action": updated_ticket.get("execution_action"),
        "route_family": updated_ticket.get("route_family"),
    }
    await async_to_thread(ticket_repository.record_event, client_ticket_id, "route_reviewed", event)
    await dispatch_event(["engineer", "dashboard"], event)

    refreshed = await async_to_thread(ticket_repository.get_account_case, canonical_billing_ticket_id)
    return get_billing_ticket(canonical_billing_ticket_id) if refreshed is not None else {
        **billing_ticket,
        **_build_account_ticket_view_model(updated_ticket),
    }


@app.get("/api/account/route-errors/summary")
def get_account_route_error_summary(limit: int = 100) -> dict[str, Any]:
    safe_limit = max(1, min(limit, 500))
    tickets = ticket_repository.list_account_cases(limit=safe_limit)
    billing_ticket_ids = [
        str(ticket.get("billing_ticket_id") or "").strip()
        for ticket in tickets
        if ticket.get("billing_ticket_id")
    ]
    corrections = (
        ticket_repository.get_billing_route_corrections_for_tickets(billing_ticket_ids)
        if billing_ticket_ids
        else {}
    )
    total = 0
    corrected_count = 0
    low_confidence_count = 0
    transitions: dict[str, int] = {}
    for ticket in tickets:
        billing_ticket_id = str(ticket.get("billing_ticket_id") or "").strip()
        correction = corrections.get(billing_ticket_id)
        low_confidence = _is_low_route_confidence(ticket)
        if correction is None and not low_confidence:
            continue
        total += 1
        if correction is not None:
            corrected_count += 1
            original_action = str(correction.get("original_execution_action") or "").strip() or "unknown"
            corrected_action = str(correction.get("corrected_execution_action") or "").strip() or "unknown"
            transition = f"{original_action} -> {corrected_action}"
            transitions[transition] = transitions.get(transition, 0) + 1
        if low_confidence:
            low_confidence_count += 1
    transition_items = [
        {"transition": key, "count": count}
        for key, count in sorted(transitions.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        "total": total,
        "corrected_count": corrected_count,
        "low_confidence_count": low_confidence_count,
        "transitions": transition_items,
    }


class BillingReplyRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)


@app.post("/api/account/cases/{billing_ticket_id}/reply")
@app.post("/api/account/billing-tickets/{billing_ticket_id}/reply", deprecated=True)
async def reply_to_billing_ticket(
    billing_ticket_id: str,
    request: BillingReplyRequest,
) -> dict[str, Any]:
    billing_ticket = await _load_account_billing_ticket(billing_ticket_id)

    client_ticket_id = str(billing_ticket.get("client_ticket_id") or "").strip()
    if not client_ticket_id:
        raise HTTPException(status_code=400, detail="account case has no linked support ticket")

    canonical_ticket = await async_to_thread(ticket_repository.get_ticket, client_ticket_id)
    if canonical_ticket is None:
        raise HTTPException(status_code=404, detail="linked support ticket not found")

    customer_message = str(request.message or "").strip()
    if not customer_message:
        raise HTTPException(status_code=400, detail="message is required")

    timestamp = now_iso()
    initial_message_count = len(canonical_ticket.get("messages", [])) if isinstance(canonical_ticket.get("messages"), list) else 0

    # Append customer reply to canonical ticket messages.
    customer_msg = {
        "role": "customer",
        "content": customer_message,
        "created_at": timestamp,
        "content_format": "plaintext",
        "source": "account-ui",
    }
    canonical_ticket.setdefault("messages", []).append(customer_msg)
    canonical_ticket["updated_at"] = timestamp
    assistant_reply = ""
    reply_ready = False
    assistant_reply_facts: dict[str, Any] | None = None
    requested_field_keys: list[str] = []
    persona_assignment: dict[str, Any] | None = None
    already_requested_fields = _account_asked_field_keys(canonical_ticket)
    all_customer_contents = [
        str(msg.get("content") or "")
        for msg in canonical_ticket.get("messages", [])
        if isinstance(msg, dict)
        and str(msg.get("role") or "").strip().lower() in {"customer", "user"}
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

    def build_automation_attempt(handler: str, action: str) -> dict[str, Any]:
        registration = account_automation_handler(action)
        if registration is None or registration.handler != handler:
            raise HTTPException(status_code=409, detail="account case has no registered automation handler")
        if registration.implementation == "account_verification":
            persisted_follow_up_count = int(prior_automation_context.get("follow_up_count") or 0)
            if persisted_follow_up_count == 0 and already_requested_fields:
                persisted_follow_up_count = 1
            return _build_account_verification_internal_email_attempt(
                ticket_subject=str(canonical_ticket.get("subject") or billing_ticket.get("title") or ""),
                customer_messages=list(canonical_ticket.get("messages") or []),
                ticket_id=client_ticket_id,
                account_case_id=str(billing_ticket.get("account_case_id") or billing_ticket_id),
                customer_email=str(canonical_ticket.get("customer_id") or "").strip() or None,
                existing_fields=prior_collected_fields,
                follow_up_count=persisted_follow_up_count,
            )
        if registration.implementation == "billing":
            return _build_billing_internal_email_attempt(
                action=action,
                message=conversation_text,
                ticket_id=client_ticket_id,
                billing_ticket_id=billing_ticket_id,
                customer_email=str(canonical_ticket.get("customer_id") or "").strip() or None,
                requester=str(canonical_ticket.get("requester") or "").strip() or None,
                already_requested_fields=sorted(already_requested_fields),
            )
        if registration.implementation == "enablement":
            return _build_enablement_internal_email_attempt(
                message=conversation_text,
                ticket_subject=str(canonical_ticket.get("subject") or billing_ticket.get("title") or ""),
                customer_messages=list(canonical_ticket.get("messages") or []),
                ticket_id=client_ticket_id,
                account_case_id=str(billing_ticket.get("account_case_id") or billing_ticket_id),
                customer_email=str(canonical_ticket.get("customer_id") or "").strip() or None,
                existing_fields=prior_collected_fields,
                already_requested_fields=sorted(already_requested_fields),
            )
        if registration.implementation == "quota":
            return _build_quota_internal_email_attempt(
                message=conversation_text,
                ticket_subject=str(canonical_ticket.get("subject") or billing_ticket.get("title") or ""),
                customer_messages=list(canonical_ticket.get("messages") or []),
                ticket_id=client_ticket_id,
                account_case_id=str(billing_ticket.get("account_case_id") or billing_ticket_id),
                customer_email=str(canonical_ticket.get("customer_id") or "").strip() or None,
                existing_fields=prior_collected_fields,
                follow_up_count=int(prior_automation_context.get("follow_up_count") or 0),
            )
        raise HTTPException(status_code=409, detail="account case has no registered automation handler")

    automation_attempt: dict[str, Any] | None = None
    classification_attempt: dict[str, Any] | None = None
    handler_continued = False
    route_result = None
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
            route_result = decide_account_route(
                customer_message,
                ticket_subject=str(canonical_ticket.get("subject") or billing_ticket.get("title") or ""),
                ticket_context=ticket_context,
                latest_assistant_message=latest_assistant_message,
                current_ticket_status=str(canonical_ticket.get("status") or ""),
                legacy_router=decide_support_route,
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
                or (
                    probe_classification.get("intent_class") == "agora"
                    and probe_action == prior_action
                )
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
                legacy_router=decide_support_route,
                require_latest=True,
            )
        decision = route_result.decision
        route = str(decision.execution_action or decision.route or "").strip()
        route_classification = dict(route_result.classification)
        account_billing_subcategory = str(
            route_classification.get("account_billing_subcategory") or ""
        ).strip()
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
                "tooling_profile": decision.tooling_profile,
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
            extraction = automation_attempt.get("field_extraction")
            if automation_attempt.get("requires_human_review") and isinstance(
                extraction,
                (EnablementFieldExtraction, AccountVerificationFieldExtraction, QuotaFieldExtraction, DetailedInvoiceFieldExtraction),
            ):
                execution_reason_code = f"{route}_field_extraction_{extraction.status}"
                execution_failure_case = reconcile_automation_execution_failure(
                    {
                        "route_classification": route_classification,
                        "automation_context": dict(automation_attempt.get("automation_context") or prior_automation_context),
                        "collected_fields": dict(extraction.collected_fields),
                    },
                    reason_code=execution_reason_code,
                    extraction=extraction,
                )
                route_classification = dict(execution_failure_case.get("route_classification") or {})
                billing_ticket.update(
                    automation_status="human_review_required",
                    execution_reason_code=execution_reason_code,
                    missing_fields=[],
                    collected_fields=dict(extraction.collected_fields),
                    customer_reply=None,
                    internal_email_payload=None,
                    internal_email_send_status="not_applicable",
                    internal_email_send_reason=execution_reason_code,
                    route_classification=route_classification,
                )
                is_automation_route = False
                automation_attempt = None
                await async_to_thread(
                    ticket_repository.cancel_pending_account_reply_jobs,
                    client_ticket_id,
                    updated_at=timestamp,
                )
            else:
                registration = account_automation_handler(route)
                billing_ticket["automation_status"] = "automation"
        else:
            account_billing_registration = account_billing_handler(account_billing_subcategory)
            if (
                account_billing_registration is not None
                and account_billing_registration.implementation == "classification_only"
            ):
                classification_attempt = _build_account_suspension_classification_attempt(
                    ticket_subject=str(
                        canonical_ticket.get("subject") or billing_ticket.get("title") or ""
                    ),
                    customer_messages=list(canonical_ticket.get("messages") or []),
                )
                extraction = classification_attempt["field_extraction"]
                route_classification["field_extraction"] = extraction.audit_payload()
            billing_ticket.update(
                automation_status="not_automated",
                missing_fields=[],
                collected_fields=(
                    dict(classification_attempt["collected_fields"])
                    if classification_attempt is not None
                    else {}
                ),
                customer_reply=None,
                internal_email_payload=None,
                internal_email_send_status="not_applicable",
                internal_email_send_reason=(
                    "account_billing_classification_only"
                    if classification_attempt is not None
                    else ""
                ),
                automation_context={},
                route_classification=route_classification,
            )
        route_prompt_snapshots = dict(route_result.prompt_snapshots)
        if classification_attempt is not None:
            route_prompt_snapshots.update(
                dict(classification_attempt.get("prompt_snapshots") or {})
            )
        extraction = automation_attempt.get("field_extraction") if automation_attempt else None
        if isinstance(
            extraction,
            (
                EnablementFieldExtraction,
                AccountVerificationFieldExtraction,
                QuotaFieldExtraction,
                DetailedInvoiceFieldExtraction,
                AccountSuspensionFieldExtraction,
            ),
        ):
            route_classification["field_extraction"] = extraction.audit_payload()
            snapshot_key = (
                "enablement_field_extractor"
                if isinstance(extraction, EnablementFieldExtraction)
                else (
                    "quota_field_extractor"
                    if isinstance(extraction, QuotaFieldExtraction)
                    else (
                        "detailed_invoice_field_extractor"
                        if isinstance(extraction, DetailedInvoiceFieldExtraction)
                        else (
                        "account_suspension_field_extractor"
                        if isinstance(extraction, AccountSuspensionFieldExtraction)
                        else "account_verification_field_extractor"
                        )
                    )
                )
            )
            route_prompt_snapshots[snapshot_key] = dict(extraction.prompt_snapshot)
            route_prompt_snapshots.update(dict((automation_attempt or {}).get("prompt_snapshots") or {}))
            billing_ticket["route_classification"] = route_classification
        await async_to_thread(
            ticket_repository.save_account_route_execution,
            route_execution_from_decision(
                ticket_id=client_ticket_id,
                decision=decision,
                system_prompt=None,
                user_prompt=None,
                created_at=timestamp,
                classification=route_classification,
                prompt_snapshots=route_prompt_snapshots,
                stage_attempts=getattr(route_result, "stage_attempts", None) if route_result is not None else None,
            ),
        )

    should_send_internal_email = False
    if automation_attempt is not None:
        extraction = automation_attempt.get("field_extraction")
        if automation_attempt.get("requires_human_review") and isinstance(
            extraction,
            (EnablementFieldExtraction, AccountVerificationFieldExtraction, QuotaFieldExtraction, DetailedInvoiceFieldExtraction),
        ):
            failed_subcategory = (
                "enablement"
                if isinstance(extraction, EnablementFieldExtraction)
                else (
                    "quota"
                    if isinstance(extraction, QuotaFieldExtraction)
                    else "detailed_invoice"
                    if isinstance(extraction, DetailedInvoiceFieldExtraction)
                    else "account_verification"
                )
            )
            failure_reason = f"{failed_subcategory}_field_extraction_{extraction.status}"
            execution_failure_case = reconcile_automation_execution_failure(
                billing_ticket,
                reason_code=failure_reason,
                extraction=extraction,
                context=dict(automation_attempt.get("automation_context") or prior_automation_context),
            )
            current_classification = dict(execution_failure_case.get("route_classification") or {})
            billing_ticket.update(
                execution_reason_code=failure_reason,
                automation_status="human_review_required",
                missing_fields=[],
                collected_fields=dict(extraction.collected_fields),
                customer_reply=None,
                internal_email_payload=None,
                internal_email_send_status="not_applicable",
                internal_email_send_reason=f"field_extraction_{extraction.status}",
                route_classification=current_classification,
                automation_context=dict(automation_attempt.get("automation_context") or prior_automation_context),
            )
            await async_to_thread(
                ticket_repository.cancel_pending_account_reply_jobs,
                client_ticket_id,
                updated_at=timestamp,
            )
            automation_attempt = None

    if automation_attempt is not None:
        missing_fields = list(automation_attempt["missing_fields"])
        requested_field_keys = (
            [field_name for field_name in missing_fields if field_name not in already_requested_fields]
            if not automation_attempt.get("internal_email_to_send")
            else []
        )
        collected_fields = dict(automation_attempt["collected_fields"])
        assistant_reply_facts = _automation_reply_facts(
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
        billing_ticket.update(
            missing_fields=missing_fields,
            collected_fields=collected_fields,
            customer_reply=None,
            internal_email_payload=automation_attempt["internal_email_payload"],
            route_classification=current_classification,
            automation_context=dict(automation_attempt.get("automation_context") or prior_automation_context),
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
    await async_to_thread(ticket_repository.save_ticket, canonical_ticket, new_messages=new_messages)
    await async_to_thread(ticket_repository.save_account_case, billing_ticket)

    if should_send_internal_email and automation_attempt and automation_attempt.get("internal_email_to_send"):
        active_handler = str(billing_ticket.get("automation_handler") or "").strip()
        send_attempt = {
            "billing": _send_billing_internal_email_attempt,
            "enablement": _send_enablement_internal_email_attempt,
            "quota": _send_quota_internal_email_attempt,
        }.get(active_handler)
        if send_attempt is None:
            raise HTTPException(status_code=409, detail="account case has no registered automation sender")
        internal_email_send_status, internal_email_send_reason = await send_attempt(automation_attempt)
        billing_ticket["internal_email_send_status"] = internal_email_send_status
        billing_ticket["internal_email_send_reason"] = internal_email_send_reason
        billing_ticket["internal_email_payload"] = dict(automation_attempt["internal_email_to_send"])
        if internal_email_send_status == "sent":
            assistant_reply_facts = _automation_reply_facts(
                handler=active_handler or "billing",
                action=str(billing_ticket.get("execution_action") or billing_ticket.get("route") or active_handler),
                missing_fields=[],
                collected_fields=collected_fields,
                submitted=True,
                customer_name=str(billing_ticket.get("customer_name") or ""),
            )
            reply_ready = True
        billing_ticket["updated_at"] = now_iso()
        await async_to_thread(ticket_repository.save_account_case, billing_ticket)

    reply_job = None
    if automation_attempt is not None and (requested_field_keys or reply_ready):
        reply_job = await async_to_thread(
            _create_account_reply_job,
            ticket_id=client_ticket_id,
            trigger_message_created_at=timestamp,
            draft_content="",
            reply_facts=assistant_reply_facts,
            asked_field_keys=requested_field_keys,
            persona_assignment=persona_assignment,
            automation_delivery_key=(
                str((billing_ticket.get("internal_email_payload") or {}).get("delivery_key") or "")
                if not requested_field_keys
                else None
            ),
        )
        if (
            str(billing_ticket.get("automation_handler") or "").strip() in {"enablement", "quota"}
            and str(billing_ticket.get("internal_email_send_status") or "").strip() == "sent"
            and isinstance(billing_ticket.get("internal_email_payload"), dict)
        ):
            billing_ticket["internal_email_payload"]["customer_confirmation_queued"] = True
            billing_ticket["updated_at"] = now_iso()
            await async_to_thread(ticket_repository.save_account_case, billing_ticket)

    # Return refreshed detail view.
    view_model = _build_account_ticket_view_model(billing_ticket)
    view_model["messages"] = canonical_ticket.get("messages", [])
    view_model["customer_id"] = canonical_ticket.get("customer_id")
    view_model["requester"] = canonical_ticket.get("requester")
    view_model["support_ticket_status"] = canonical_ticket.get("status")
    view_model.update(_account_reply_job_public(reply_job))
    return {
        **billing_ticket,
        **view_model,
    }


def _public_asset_payload(asset: dict[str, Any]) -> dict[str, Any]:
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


def _attachment_summary_from_asset(asset: dict[str, Any]) -> dict[str, Any]:
    summary = _public_asset_payload(asset)
    summary["agent_read_enabled"] = False
    return summary


def _normalize_asset_ids(asset_ids: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for asset_id in asset_ids or []:
        value = str(asset_id or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _prepare_message_asset_attachments_sync(
    *,
    asset_ids: list[str] | None,
    ticket_id: str,
    customer_id: str,
) -> list[dict[str, Any]]:
    normalized_ids = _normalize_asset_ids(asset_ids)
    if len(normalized_ids) > 10:
        raise HTTPException(status_code=400, detail="At most 10 attachments can be sent with one message")
    attachments: list[dict[str, Any]] = []
    for asset_id in normalized_ids:
        asset = asset_repository.get_asset(asset_id)
        if asset is None:
            raise HTTPException(status_code=400, detail=f"Asset {asset_id} was not found")
        if str(asset.get("ticket_id") or "").strip() != ticket_id:
            raise HTTPException(status_code=403, detail="Asset does not belong to this ticket")
        if str(asset.get("customer_id") or "").strip() != customer_id:
            raise HTTPException(status_code=403, detail="Asset does not belong to this customer")
        if str(asset.get("status") or "").strip() != ASSET_STATUS_UPLOADED:
            raise HTTPException(status_code=400, detail="Only uploaded assets can be attached to a message")
        attachments.append(_attachment_summary_from_asset(asset))
    return attachments


@app.post("/api/assets/upload-intents")
def create_asset_upload_intent(request: AssetUploadIntentRequest) -> dict[str, Any]:
    try:
        safe_filename, extension = validate_asset_upload_request(request.file_name, request.size_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    asset_id = create_asset_id()
    bucket = str(getattr(asset_storage, "bucket", "") or os.getenv("ASSET_S3_BUCKET") or "").strip()
    asset = {
        "asset_id": asset_id,
        "ticket_id": request.ticket_id.strip(),
        "customer_id": request.customer_id.strip(),
        "original_filename": safe_filename,
        "content_type": str(request.content_type or "text/plain").strip() or "text/plain",
        "size_bytes": int(request.size_bytes),
        "extension": extension,
        "status": "pending_upload",
        "storage_provider": "s3",
        "bucket": bucket,
        "s3_key": build_asset_s3_key(
            ticket_id=request.ticket_id,
            asset_id=asset_id,
            file_name=safe_filename,
        ),
        "meta": {"agent_read_enabled": False},
    }
    try:
        upload = asset_storage.create_presigned_post(asset)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        LOGGER.warning("Failed to create asset upload intent: %s", exc)
        raise HTTPException(status_code=503, detail="Failed to create upload intent") from exc

    stored = asset_repository.create_asset(asset)
    return {
        "asset": _public_asset_payload(stored),
        "upload": upload,
    }


@app.post("/api/assets/{asset_id}/complete")
def complete_asset_upload(asset_id: str, request: AssetCompleteRequest) -> dict[str, Any]:
    asset = asset_repository.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    normalized_customer_id = str(request.customer_id or "").strip()
    if normalized_customer_id and normalized_customer_id != str(asset.get("customer_id") or "").strip():
        raise HTTPException(status_code=403, detail="Asset does not belong to this customer")
    try:
        upload_info = asset_storage.verify_uploaded(asset)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        LOGGER.warning("Failed to verify asset upload %s: %s", asset_id, exc)
        raise HTTPException(status_code=400, detail="Uploaded object could not be verified") from exc
    uploaded = asset_repository.mark_uploaded(
        asset_id,
        size_bytes=int(upload_info.get("size_bytes") or asset.get("size_bytes") or 0),
        etag=str(upload_info.get("etag") or "").strip() or None,
        checksum=str(upload_info.get("checksum") or "").strip() or None,
    )
    if uploaded is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return {"asset": _public_asset_payload(uploaded)}


@app.get("/api/assets/{asset_id}/download-url")
def get_asset_download_url(
    asset_id: str,
    customer_id: str | None = Query(default=None),
) -> dict[str, Any]:
    asset = asset_repository.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    normalized_customer_id = str(customer_id or "").strip()
    if normalized_customer_id and normalized_customer_id != str(asset.get("customer_id") or "").strip():
        raise HTTPException(status_code=403, detail="Asset does not belong to this customer")
    if str(asset.get("status") or "").strip() not in {ASSET_STATUS_UPLOADED, ASSET_STATUS_ATTACHED}:
        raise HTTPException(status_code=400, detail="Asset is not ready for download")
    try:
        download_url = asset_storage.create_download_url(asset)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        LOGGER.warning("Failed to create asset download URL %s: %s", asset_id, exc)
        raise HTTPException(status_code=503, detail="Failed to create download URL") from exc
    return {"download_url": download_url, "asset": _public_asset_payload(asset)}


@app.get("/api/client/service-events")
def list_client_service_events() -> dict[str, Any]:
    return get_agora_service_events_payload()


@app.post("/api/tickets/query")
async def create_or_update_ticket(
    request: TicketQueryRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    api_started_at = time.perf_counter()
    ticket_id = request.ticket_id or f"T-{uuid4().hex[:6].upper()}"
    existing_ticket, load_ticket_ms = await _async_to_thread_with_latency(ticket_repository.get_ticket, ticket_id)
    is_new_ticket = existing_ticket is None
    save_ticket_ms: float | None = None
    record_ticket_created_event_ms: float | None = None
    enqueue_ticket_query_ms: float | None = None
    enqueue_sentiment_ms: float | None = None

    ticket = existing_ticket or {
        "ticket_id": ticket_id,
        "customer_id": request.customer_id,
        "status": OPEN_STATUS,
        "created_at": now_iso(),
        "messages": [],
    }
    ensure_ticket_defaults(ticket)
    initial_message_count = len(ticket.get("messages", []))
    customer_message = request.message.strip()
    message_attachments = await async_to_thread(
        _prepare_message_asset_attachments_sync,
        asset_ids=request.asset_ids,
        ticket_id=ticket_id,
        customer_id=request.customer_id,
    )
    attached_asset_ids = [str(item.get("asset_id") or "").strip() for item in message_attachments if item.get("asset_id")]
    ticket_status_before_customer_message = normalize_ticket_status(ticket.get("status"))
    latest_assistant_message = _latest_assistant_message_for_ticket(ticket)
    customer_resolution_signal = is_explicit_resolved_confirmation(customer_message)

    ticket["customer_id"] = request.customer_id
    ticket["requester"] = (
        request.requester.strip()
        if request.requester and request.requester.strip()
        else ticket.get("requester") or request.customer_id
    )
    existing_subject = str(ticket.get("subject") or "").strip()
    normalized_requested_subject = request.subject.strip() if request.subject and request.subject.strip() else None

    if initial_message_count == 0:
        selected_product = _validated_new_session_product(request.product) or normalize_support_product(
            ticket.get("product")
        )
        ticket["product"] = selected_product
    else:
        ticket["product"] = normalize_support_product(ticket.get("product"))

    if normalize_ticket_status(ticket.get("status")) == RESOLVED_STATUS:
        ticket["status"] = COMMUNICATING_STATUS

    timestamp = now_iso()
    current_app_build_ref = str(get_app_build_info().get("ref") or "").strip() or None
    customer_message_content_format = str(request.content_format or "plaintext").strip() or "plaintext"
    initial_ack = None
    follow_up_answer = ""
    follow_up_sources: list[str] = []
    follow_up_citations: list[dict[str, str]] = []
    needs_engineer_input = False
    ai_replied = False
    task_enqueued = False
    ack_source = "client_model"
    processing_mode = "main_agent_async"
    route_payload: dict[str, Any] = {
        "answer_route": None,
        "scope_label": None,
        "route_family": None,
        "execution_action": None,
        "tooling_profile": None,
        "route_reason": None,
        "route_confidence": None,
        "search_used": False,
        "matched_signals": [],
    }
    if INPUT_GUARDRAIL_ENABLED:
        input_guardrail_result = await evaluate_openai_input_guardrail(
            customer_message,
            subject=normalized_requested_subject or existing_subject or None,
            requester=str(ticket.get("requester") or "").strip() or None,
            customer_id=request.customer_id,
        )
    else:
        input_guardrail_result = OpenAIInputGuardrailResult.allow_result(
            reason="input guardrail disabled by feature flag",
            diagnostics={
                "guardrail_mode": "disabled",
                "source": "feature_flag",
                "feature_flag": "INPUT_GUARDRAIL_ENABLED",
                "enabled": False,
            },
        )
    persisted_customer_message = customer_message
    active_engineer_case_payload: dict[str, Any] | None = None
    if input_guardrail_result.blocked:
        if is_new_ticket or not existing_subject or existing_subject == "General support request":
            ticket["subject"] = normalized_requested_subject or existing_subject or "General support request"
        persisted_customer_message = input_guardrail_result.sanitized_customer_placeholder
        ticket["messages"].append(
            {
                "role": "customer",
                "content": persisted_customer_message,
                "created_at": timestamp,
                "content_format": customer_message_content_format,
                **({"attachments": message_attachments} if message_attachments else {}),
                **_build_input_guardrail_message_metadata(input_guardrail_result),
            }
        )
        route_payload.update(_build_input_guardrail_route_payload(input_guardrail_result))
        follow_up_answer = input_guardrail_result.customer_reply
        ack_source = "guardrail"
        processing_mode = "input_guardrail_blocked"
        active_engineer_case_payload = _active_engineer_case_payload(ticket)
        if active_engineer_case_payload is None and normalize_ticket_status(ticket.get("status")) != INVESTIGATING_STATUS:
            ticket["status"] = resolve_next_ticket_status(ticket.get("status"), COMMUNICATING_STATUS)
    else:
        if is_new_ticket or not existing_subject or existing_subject == "General support request":
            ticket["subject"] = derive_subject(
                request.message,
                preferred_subject=normalized_requested_subject,
            )
        ticket["messages"].append(
            {
                "role": "customer",
                "content": customer_message,
                "created_at": timestamp,
                "content_format": customer_message_content_format,
                **({"attachments": message_attachments} if message_attachments else {}),
            }
        )
        active_engineer_case_payload = _active_engineer_case_payload(ticket)

    route_context = build_emotion_context(ticket, limit=6, max_chars=400)
    investigation_result: dict[str, Any] | None = None
    engineer_case: dict[str, Any] | None = None
    engineer_case_created = False
    execution: TicketExecutionResult | None = None

    main_agent_async_eligible = (
        not input_guardrail_result.blocked
        and active_engineer_case_payload is None
        and _main_agent_async_enabled()
        and not customer_resolution_signal
    )
    if input_guardrail_result.blocked:
        main_agent_async_eligible = False
    elif not main_agent_async_eligible:
        initial_ack = build_initial_ack(customer_message)
        ack_source = str(getattr(initial_ack, "source", "") or "server_ack").strip() or "server_ack"
        processing_mode = "main_agent_sync"
    if not input_guardrail_result.blocked and isinstance(active_engineer_case_payload, dict):
        engineer_case = _engineer_case_payload_to_record(active_engineer_case_payload)
        active_case_route = decide_support_route(
            customer_message,
            ticket_subject=str(ticket.get("subject") or "").strip() or None,
            ticket_context=route_context,
            product=ticket.get("product"),
            latest_assistant_message=latest_assistant_message,
            current_ticket_status=ticket_status_before_customer_message,
            has_active_engineer_case=True,
        )
        if str(active_case_route.execution_action or "").strip() == "resolve_ticket":
            resolution = resolve_support_message(
                customer_message,
                ticket_id=ticket_id,
                customer_id=request.customer_id,
                ticket_subject=str(ticket.get("subject") or "").strip() or None,
                ticket_context=route_context,
                product=ticket.get("product"),
                latest_assistant_message=latest_assistant_message,
                current_ticket_status=ticket_status_before_customer_message,
                has_active_engineer_case=True,
                decision=active_case_route,
            )
            execution = TicketExecutionResult(
                answer=str(resolution.answer or "").strip(),
                confidence=float(resolution.confidence or 0.0),
                sources=list(resolution.sources),
                citations=[dict(item) for item in resolution.citations],
                evidence_summary=dict(resolution.evidence_summary or {}) or None,
                packed_evidence=dict(resolution.packed_evidence or {}) or None,
                needs_investigating=False,
                next_status=RESOLVED_STATUS,
                answer_route=resolution.answer_route,
                scope_label=resolution.scope_label,
                route_family=resolution.route_family,
                execution_action=str(resolution.execution_action or resolution.answer_route or "resolve_ticket"),
                tooling_profile=resolution.tooling_profile,
                route_reason=resolution.route_reason,
                route_confidence=float(resolution.route_confidence or 0.0),
                search_used=bool(resolution.search_used),
                matched_signals=list(resolution.matched_signals),
                workflow_action="resolve_ticket",
            )
            engineer_case, investigation_messages = _close_active_engineer_case_for_customer_resolution(
                ticket,
                engineer_case,
                now_value=now_iso(),
            )
            investigation_result = {
                "created": False,
                "new_internal_messages": investigation_messages,
            }
            processing_mode = "active_investigation_resolution"
        else:
            case_context = build_engineer_case_context(ticket, engineer_case)
            investigation_result = start_or_refresh_investigation(
                case_context,
                trigger_reason="customer_follow_up",
                trigger_source="customer_follow_up",
                now_value=now_iso(),
                next_status=str(engineer_case.get("status") or ticket.get("status") or INVESTIGATING_STATUS),
                ai_turn_builder=generate_investigation_ai_turn,
            )
            engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
            follow_up_answer = str(investigation_result.get("public_reply") or "").strip()
            needs_engineer_input = True
            ticket["status"] = normalize_ticket_status(engineer_case.get("status"))
            ticket["active_engineer_case_id"] = str(engineer_case.get("engineer_case_id") or "").strip() or None
            ticket["client_intake_state"] = None
            processing_mode = "active_investigation_followup"
    elif not input_guardrail_result.blocked:
        if main_agent_async_eligible:
            ticket["status"] = resolve_next_ticket_status(ticket.get("status"), COMMUNICATING_STATUS)
            processing_mode = "main_agent_async"
        else:
            processing_mode = "main_agent_sync"
            product_context = resolve_support_product_context(
                message=customer_message,
                ticket_subject=str(ticket.get("subject") or "").strip() or None,
                ticket_context=route_context,
                product=ticket.get("product"),
                product_selection_state=ticket.get("product_selection_state"),
                latest_assistant_message=latest_assistant_message,
                current_ticket_status=ticket_status_before_customer_message,
                requester=str(ticket.get("requester") or "").strip() or None,
                customer_id=request.customer_id,
                message_created_at=timestamp,
                route_agent=decide_support_route,
            )
            ticket["product_selection_state"] = product_context.product_selection_state
            if product_context.product_changed:
                ticket["client_intake_state"] = None
            if product_context.product is not None:
                ticket["product"] = product_context.product
            effective_customer_message = str(product_context.effective_message or customer_message).strip() or customer_message
            if product_context.preflight_execution is not None:
                execution = product_context.preflight_execution
            else:
                runtime_execution = execute_client_ticket_agent_runtime(
                    effective_customer_message,
                    ticket_id=ticket_id,
                    customer_id=request.customer_id,
                    requester=str(ticket.get("requester") or "").strip() or None,
                    ticket_subject=str(ticket.get("subject") or "").strip() or None,
                    ticket_context=route_context,
                    product=ticket.get("product"),
                    message_id=timestamp,
                    client_intake_state=ticket.get("client_intake_state"),
                    latest_assistant_message=latest_assistant_message,
                    current_ticket_status=ticket_status_before_customer_message,
                    has_active_engineer_case=False,
                    route_agent=decide_support_route,
                    route_executor=resolve_support_message,
                    rag_executor=build_sync_rag_executor(rag_service_client),
                    review_agent=_run_client_ticket_review_agent,
                )
                execution = runtime_execution.result
        if execution is not None:
            execution_route_payload = build_execution_route_payload(execution)
            route_payload.update(execution_route_payload)
            follow_up_answer = execution.answer
            follow_up_sources = list(execution.sources)
            follow_up_citations = [dict(item) for item in execution.citations]
            execution_client_intake_state = (
                dict(getattr(execution, "client_intake_state"))
                if isinstance(getattr(execution, "client_intake_state", None), dict)
                else None
            )
            execution_client_agent_runtime_state = _execution_client_agent_runtime_state(execution)
            if execution_client_agent_runtime_state is not None:
                ticket["client_agent_runtime_state"] = execution_client_agent_runtime_state
            if execution.needs_investigating:
                ticket["client_intake_state"] = execution_client_intake_state
                engineer_case, engineer_case_created = _prepare_engineer_case_for_ticket(
                    ticket,
                    case_status=INVESTIGATING_STATUS,
                    trigger_source="support_query",
                    trigger_reason=str(execution.investigation_reason or "rag_insufficient_evidence"),
                    now_value=now_iso(),
                )
                summary_packet = build_engineer_summary_packet(
                    client_ticket=ticket,
                    engineer_case=engineer_case,
                    customer_message=effective_customer_message,
                    execution=execution,
                    route_payload=execution_route_payload,
                    now_value=now_iso(),
                )
                engineer_case["engineer_handoff_packet"] = summary_packet
                summary_agent_state = {
                    **(engineer_case.get("engineer_agent_state") or {}),
                    "summary_packet_id": summary_packet["packet_id"],
                    "summary_agent_version": summary_packet["summary_agent_version"],
                    "summary_packet_version": summary_packet["packet_version"],
                    "issue_understanding": summary_packet["engineer_ticket_input"]["opening_summary"],
                    "missing_information": list(summary_packet.get("missing_information") or []),
                    "next_request_for_engineer": summary_packet["engineer_ticket_input"]["requested_action"],
                }
                engineer_case["engineer_agent_state"] = summary_agent_state
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
                    trigger_source="support_query",
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
                    engineer_evidence_builder=_build_engineer_evidence_for_investigation,
                )
                engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
                if engineer_case_created:
                    engineer_case["title"] = derive_engineer_case_title(
                        ticket,
                        handoff_packet=case_context.get("engineer_handoff_packet"),
                        engineer_agent_state=case_context.get("engineer_agent_state"),
                    )
                follow_up_answer = str(investigation_result.get("public_reply") or "").strip()
                follow_up_sources = []
                follow_up_citations = []
                needs_engineer_input = True
                ticket["status"] = INVESTIGATING_STATUS
                ticket["active_engineer_case_id"] = str(engineer_case.get("engineer_case_id") or "").strip() or None
                ticket["client_intake_state"] = None
            else:
                ticket["status"] = resolve_next_ticket_status(ticket.get("status"), execution.next_status)
                ticket["client_intake_state"] = execution_client_intake_state
        elif not main_agent_async_eligible:
            ticket["status"] = resolve_next_ticket_status(ticket.get("status"), COMMUNICATING_STATUS)

    if not input_guardrail_result.blocked and isinstance(active_engineer_case_payload, dict) and execution is not None:
        execution_route_payload = build_execution_route_payload(execution)
        route_payload.update(execution_route_payload)
        follow_up_answer = execution.answer
        follow_up_sources = list(execution.sources)
        follow_up_citations = [dict(item) for item in execution.citations]
        execution_client_intake_state = (
            dict(getattr(execution, "client_intake_state"))
            if isinstance(getattr(execution, "client_intake_state", None), dict)
            else None
        )
        execution_client_agent_runtime_state = _execution_client_agent_runtime_state(execution)
        if execution_client_agent_runtime_state is not None:
            ticket["client_agent_runtime_state"] = execution_client_agent_runtime_state
        ticket["status"] = resolve_next_ticket_status(ticket.get("status"), execution.next_status)
        ticket["client_intake_state"] = execution_client_intake_state

    if str(follow_up_answer).strip():
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": follow_up_answer,
            "created_at": now_iso(),
        }
        if route_payload.get("answer_route"):
            assistant_message["answer_route"] = route_payload.get("answer_route")
            assistant_message["scope_label"] = route_payload.get("scope_label")
            assistant_message["route_family"] = route_payload.get("route_family")
            assistant_message["execution_action"] = route_payload.get("execution_action")
            assistant_message["tooling_profile"] = route_payload.get("tooling_profile")
            assistant_message["route_reason"] = route_payload.get("route_reason")
            assistant_message["route_confidence"] = route_payload.get("route_confidence")
            assistant_message["search_used"] = bool(route_payload.get("search_used"))
            assistant_message["matched_signals"] = list(route_payload.get("matched_signals") or [])
            if route_payload.get("workflow_action"):
                assistant_message["workflow_action"] = route_payload.get("workflow_action")
            if isinstance(route_payload.get("retrieval_plan_snapshot"), dict):
                assistant_message["retrieval_plan_snapshot"] = dict(route_payload.get("retrieval_plan_snapshot") or {})
            if route_payload.get("client_intake_phase"):
                assistant_message["client_intake_phase"] = route_payload.get("client_intake_phase")
                assistant_message["client_intake_ready_for_engineer_ticket"] = bool(
                    route_payload.get("client_intake_ready_for_engineer_ticket")
                )
                assistant_message["client_intake_missing_information"] = list(
                    route_payload.get("client_intake_missing_information") or []
                )
        if input_guardrail_result.blocked:
            assistant_message.update(_build_input_guardrail_message_metadata(input_guardrail_result))
        if follow_up_sources:
            assistant_message["sources"] = follow_up_sources
        if follow_up_citations:
            assistant_message["citations"] = follow_up_citations
        ticket["messages"].append(assistant_message)

    response_answer = str(follow_up_answer or "").strip()
    ai_replied = bool(response_answer)

    ticket["updated_at"] = now_iso()
    new_messages = ticket.get("messages", [])[initial_message_count:]
    _, save_ticket_ms = await _async_to_thread_with_latency(
        ticket_repository.save_ticket,
        ticket,
        new_messages=new_messages,
    )
    if attached_asset_ids:
        await async_to_thread(asset_repository.mark_attached, attached_asset_ids)
    if execution is not None:
        await async_to_thread(_record_ticket_agent_runtime_events, execution)
    if engineer_case is not None:
        ticket["engineer_case_count"] = max(
            int(ticket.get("engineer_case_count") or 0),
            int(engineer_case.get("case_sequence") or 0),
        )
        await async_to_thread(ticket_repository.save_ticket, ticket, new_messages=[])
        await async_to_thread(
            ticket_repository.save_engineer_case,
            engineer_case,
            new_messages=investigation_result.get("new_internal_messages") if investigation_result else [],
        )

    api_persist_latency_ms = round((time.perf_counter() - api_started_at) * 1000, 2)
    if main_agent_async_eligible:
        enqueue_started_at = time.perf_counter()
        task_enqueued = await _schedule_ticket_query_processing(
            background_tasks,
            task=build_query_task(
                ticket_id=ticket_id,
                customer_message=customer_message,
                message_created_at=timestamp,
                app_build_ref=current_app_build_ref,
                customer_id=str(ticket.get("customer_id") or "").strip() or None,
                requester=str(ticket.get("requester") or "").strip() or None,
                ticket_subject=str(ticket.get("subject") or "").strip() or None,
                product=str(ticket.get("product") or "").strip() or None,
                product_selection_state=(
                    dict(ticket.get("product_selection_state"))
                    if isinstance(ticket.get("product_selection_state"), dict)
                    else None
                ),
                route_context_tail=route_context,
                client_intake_state=(
                    dict(ticket.get("client_intake_state"))
                    if isinstance(ticket.get("client_intake_state"), dict)
                    else None
                ),
                latest_assistant_message=latest_assistant_message,
                current_ticket_status=ticket_status_before_customer_message,
                ticket_updated_at=str(ticket.get("updated_at") or "").strip() or None,
                load_ticket_ms=load_ticket_ms,
                save_ticket_ms=save_ticket_ms,
                api_persist_latency_ms=api_persist_latency_ms,
                processing_mode="main_agent_async",
            ),
        )
        enqueue_ticket_query_ms = round((time.perf_counter() - enqueue_started_at) * 1000, 2)

    if not input_guardrail_result.blocked:
        enqueue_sentiment_started_at = time.perf_counter()
        await _enqueue_or_defer_message_sentiment_tag(
            background_tasks,
            ticket_id=ticket_id,
            customer_message=customer_message,
            message_created_at=timestamp,
        )
        enqueue_sentiment_ms = round((time.perf_counter() - enqueue_sentiment_started_at) * 1000, 2)

    admission_timing_payload = {
        "message_created_at": timestamp,
        "load_ticket_ms": _round_timing(load_ticket_ms),
        "save_ticket_ms": _round_timing(save_ticket_ms),
        "enqueue_ticket_query_ms": _round_timing(enqueue_ticket_query_ms),
        "enqueue_sentiment_ms": _round_timing(enqueue_sentiment_ms),
        "api_persist_latency_ms": _round_timing(api_persist_latency_ms),
    }

    event = {
        "event": "ticket_created" if is_new_ticket else "ticket_updated",
        "ticket_id": ticket_id,
        "status": ticket["status"],
        "message": persisted_customer_message,
        "created_at": now_iso(),
        "parallel_mode": processing_mode,
        **admission_timing_payload,
    }
    if current_app_build_ref:
        event["admission_app_build_ref"] = current_app_build_ref
    if route_payload.get("answer_route"):
        event["answer_route"] = route_payload.get("answer_route")
        event["scope_label"] = route_payload.get("scope_label")
        event["route_family"] = route_payload.get("route_family")
        event["execution_action"] = route_payload.get("execution_action")
        event["tooling_profile"] = route_payload.get("tooling_profile")
        event["route_reason"] = route_payload.get("route_reason")
        event["route_confidence"] = route_payload.get("route_confidence")
        event["search_used"] = bool(route_payload.get("search_used"))
        event["matched_signals"] = list(route_payload.get("matched_signals") or [])
    if route_payload.get("workflow_action"):
        event["workflow_action"] = route_payload.get("workflow_action")
    if route_payload.get("client_intake_phase"):
        event["client_intake_phase"] = route_payload.get("client_intake_phase")
        event["client_intake_ready_for_engineer_ticket"] = bool(
            route_payload.get("client_intake_ready_for_engineer_ticket")
        )
        event["client_intake_missing_information"] = list(
            route_payload.get("client_intake_missing_information") or []
        )
    record_ticket_created_started_at = time.perf_counter()
    await async_to_thread(ticket_repository.record_event, ticket_id, event["event"], event)
    record_ticket_created_event_ms = round((time.perf_counter() - record_ticket_created_started_at) * 1000, 2)
    await dispatch_event(["engineer", "dashboard"], event)
    await dispatch_event(
        ["client"],
        build_client_sync_event(ticket, event["event"], persisted_customer_message[:200]),
    )
    if execution is not None and str(getattr(execution, "workflow_action", "") or "").strip() == "resolve_ticket":
        auto_resolved_event = _build_ticket_auto_resolved_by_customer_confirmation_event(
            ticket_id=ticket_id,
            status=ticket["status"],
            message_created_at=timestamp,
            answer_created_at=(
                str(ticket["messages"][-1].get("created_at") or "").strip()
                if isinstance(ticket.get("messages"), list) and ticket.get("messages")
                else None
            ),
        )
        await async_to_thread(
            ticket_repository.record_event,
            ticket_id,
            auto_resolved_event["event"],
            auto_resolved_event,
        )
        if isinstance(engineer_case, dict) and str(engineer_case.get("engineer_case_id") or "").strip():
            engineer_auto_resolved_event = {
                **auto_resolved_event,
                "ticket_id": str(engineer_case.get("engineer_case_id") or ""),
                "client_ticket_id": ticket_id,
                "engineer_case_id": str(engineer_case.get("engineer_case_id") or ""),
            }
            await async_to_thread(
                ticket_repository.record_engineer_case_event,
                str(engineer_case.get("engineer_case_id") or ""),
                engineer_auto_resolved_event["event"],
                engineer_auto_resolved_event,
            )
        await dispatch_event(["engineer", "dashboard"], auto_resolved_event)
    if investigation_result is not None:
        await _record_and_dispatch_investigation_event(
            ticket,
            engineer_case,
            created=bool(engineer_case_created or investigation_result.get("created")),
        )

    api_return_latency_ms = round((time.perf_counter() - api_started_at) * 1000, 2)
    if task_enqueued:
        processing_event = {
            "event": "ticket_ai_processing",
            "ticket_id": ticket_id,
            "status": ticket["status"],
            "message": "AI is processing this request asynchronously.",
            "created_at": now_iso(),
            "parallel_mode": processing_mode,
            **admission_timing_payload,
            "record_ticket_created_event_ms": record_ticket_created_event_ms,
            "api_return_latency_ms": api_return_latency_ms,
        }
        if current_app_build_ref:
            processing_event["task_app_build_ref"] = current_app_build_ref
        await async_to_thread(ticket_repository.record_event, ticket_id, processing_event["event"], processing_event)
        await dispatch_event(["engineer", "dashboard"], processing_event)
        await dispatch_event(
            ["client"],
            build_client_sync_event(ticket, processing_event["event"]),
        )

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
            "created_at": now_iso(),
        }
        ticket_repository.record_event(ticket_id, attention_event["event"], attention_event)
        await dispatch_event(["engineer", "dashboard"], attention_event)
        await dispatch_event(
            ["client"],
            build_client_sync_event(ticket, attention_event["event"]),
        )

    return {
        "ticket_id": ticket_id,
        "answer": response_answer,
        "confidence": 0.0,
        "sources": list(follow_up_sources),
        "citations": [dict(item) for item in follow_up_citations],
        "sentiment": {
            "label": None,
            "raw_label": None,
            "score": None,
            "is_alert": False,
            "provider": "deferred",
            "intent": getattr(initial_ack, "intent", None) if initial_ack is not None else None,
        },
        "status": ticket["status"],
        "ai_replied": ai_replied,
        "needs_engineer_input": needs_engineer_input,
        "queued_for_ai": task_enqueued,
        "message_created_at": timestamp,
        "queued_message_created_at": timestamp if task_enqueued else None,
        "answer_route": route_payload.get("answer_route"),
        "scope_label": route_payload.get("scope_label"),
        "route_reason": route_payload.get("route_reason"),
        "route_confidence": route_payload.get("route_confidence"),
        "search_used": bool(route_payload.get("search_used")),
        "ack_source": ack_source,
        "processing_mode": processing_mode,
        "api_persist_latency_ms": api_persist_latency_ms,
        "api_return_latency_ms": api_return_latency_ms,
    }


@app.get("/api/tickets")
def list_client_tickets(
    customer_id: str | None = Query(default=None),
    status: str = Query(default="all", pattern="^(open|all|resolved|communicating|escalated|investigating)$"),
) -> dict[str, Any]:
    normalized_customer_id = str(customer_id or "").strip()
    all_tickets = ticket_repository.list_tickets(include_messages=True)
    filtered_tickets: list[dict[str, Any]] = []
    for ticket in all_tickets:
        if normalized_customer_id and str(ticket.get("customer_id") or "").strip() != normalized_customer_id:
            continue
        if status != "all" and normalize_ticket_status(ticket.get("status")) != normalize_ticket_status(status):
            continue
        filtered_tickets.append(ticket)

    tickets = sorted(
        filtered_tickets,
        key=lambda item: item.get("updated_at", item.get("created_at", "")),
        reverse=True,
    )
    return {
        "tickets": tickets,
        "customer_id": normalized_customer_id or None,
        "status_filter": status if status == "all" else normalize_ticket_status(status),
    }


@app.get("/internal/trace/tickets/{ticket_id}")
def get_internal_trace_ticket_snapshot(
    ticket_id: str,
    event_limit: int = Query(default=100, ge=1, le=500),
    message_created_at: str | None = Query(default=None),
    include_messages: bool = Query(default=False),
    message_limit: int = Query(default=0, ge=0, le=200),
) -> dict[str, Any]:
    snapshot = ticket_repository.get_trace_ticket_snapshot(
        ticket_id,
        event_limit=event_limit,
        message_created_at=str(message_created_at or "").strip() or None,
        include_messages=bool(include_messages),
        message_limit=message_limit,
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return snapshot


@app.get("/api/dashboard/tickets")
def list_dashboard_tickets(
    status: str = Query(
        default=INVESTIGATING_STATUS,
        pattern="^(resolved|communicating|escalated|investigating)$",
    ),
) -> dict[str, Any]:
    normalized_filter = normalize_ticket_status(status)
    all_tickets = ticket_repository.list_tickets(include_messages=False)
    filtered_tickets: list[dict[str, Any]] = []
    for ticket in all_tickets:
        if normalize_ticket_status(ticket.get("status")) != normalized_filter:
            continue
        filtered_tickets.append(
            _build_dashboard_ticket_payload(
                ticket,
                include_sub_tickets=False,
                include_agent_events=False,
                include_token_usage=False,
            )
        )

    tickets = sorted(
        filtered_tickets,
        key=lambda item: item.get("updated_at", item.get("created_at", "")),
        reverse=True,
    )
    return {"tickets": tickets, "status_filter": normalized_filter}


@app.get("/api/dashboard/tickets/{ticket_id}")
def get_dashboard_ticket_detail(ticket_id: str) -> dict[str, Any]:
    ticket = _dashboard_ticket_detail_or_404(ticket_id, include_token_usage=True)
    return {"ticket": ticket}


@app.get("/api/dashboard/tickets/{ticket_id}/execution-flow")
def get_dashboard_ticket_execution_flow(ticket_id: str) -> dict[str, Any]:
    ticket = _dashboard_ticket_detail_or_404(ticket_id, include_token_usage=False)
    return _build_dashboard_ticket_execution_flow(ticket)


@app.get("/api/dashboard/tickets/{ticket_id}/summary")
def get_dashboard_ticket_summary(ticket_id: str) -> dict[str, Any]:
    ticket = _dashboard_ticket_detail_or_404(ticket_id, include_token_usage=False)
    summary, next_action_needed, model = build_ticket_summary(ticket)
    return {
        "ticket_id": str(ticket.get("ticket_id") or ticket_id),
        "summary": summary,
        "next_action_needed": next_action_needed,
        "model": model,
        "generated_at": now_iso(),
    }


@app.post("/api/workspace/auth/login")
def workspace_login(request: WorkspaceLoginRequest) -> dict[str, Any]:
    identity = request.email.strip()
    account = ticket_repository.get_workspace_account_by_email(identity)
    if account is None:
        account = ticket_repository.get_workspace_account(identity)
    if (
        not isinstance(account, dict)
        or not bool(account.get("active", True))
        or not verify_workspace_password(request.password, str(account.get("password_hash") or ""))
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_workspace_access_token(account)
    return {
        "access_token": token,
        "token_type": "bearer",
        "account": _public_workspace_account(account),
    }


@app.get("/api/workspace/me")
def workspace_me(
    principal: WorkspacePrincipal = Depends(require_workspace_principal),
) -> dict[str, Any]:
    account = ticket_repository.get_workspace_account(principal.account_id)
    assert account is not None
    return {"account": _public_workspace_account(account)}


@app.get("/api/workspace/cases")
def list_workspace_cases(
    assignment_status: str = Query(
        default="all", pattern="^(all|pending|assigned|resolved)$"
    ),
    principal: WorkspacePrincipal = Depends(require_workspace_principal),
) -> dict[str, Any]:
    engineer_cases = ticket_repository.list_engineer_case_headers()
    visible_cases = []
    for engineer_case in engineer_cases:
        case_assignment_status = str(engineer_case.get("assignment_status") or "pending")
        if assignment_status != "all" and case_assignment_status != assignment_status:
            continue
        if principal.role == "engineer" and (
            case_assignment_status != "assigned"
            or str(engineer_case.get("assigned_engineer_id") or "").strip()
            != principal.account_id
        ):
            continue
        visible_cases.append(engineer_case)
    visible_cases.sort(
        key=lambda item: str(item.get("assignment_updated_at") or item.get("updated_at") or ""),
        reverse=True,
    )
    return {"cases": visible_cases, "assignment_status_filter": assignment_status}


@app.get("/api/workspace/cases/{engineer_case_id}")
def get_workspace_case(
    engineer_case_id: str,
    principal: WorkspacePrincipal = Depends(require_workspace_principal),
) -> dict[str, Any]:
    engineer_case = _resolve_engineer_case_payload(engineer_case_id)
    if engineer_case is None:
        raise HTTPException(status_code=404, detail="Engineer Case not found")
    if principal.role == "engineer" and (
        str(engineer_case.get("assignment_status") or "pending") != "assigned"
        or str(engineer_case.get("assigned_engineer_id") or "").strip()
        != principal.account_id
    ):
        raise HTTPException(status_code=403, detail="Engineer Case is not assigned to this account")
    return get_ticket_detail(engineer_case_id)


@app.get("/api/workspace/admin/accounts")
def list_workspace_admin_accounts(
    _principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    return {
        "accounts": [
            _public_workspace_account(account)
            for account in ticket_repository.list_workspace_accounts()
        ]
    }


@app.get("/api/workspace/schedule")
def get_workspace_personal_schedule(
    principal: WorkspacePrincipal = Depends(require_workspace_principal),
) -> dict[str, Any]:
    return _workspace_personal_schedule_payload(principal)


@app.post("/api/workspace/admin/accounts")
def create_workspace_admin_account_retired(
    _request: WorkspaceAccountCreateRequest,
    _principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> None:
    raise HTTPException(status_code=410, detail="Use the workspace invitation flow")


@app.post("/api/workspace/admin/invitations", status_code=201)
def create_workspace_invitation(
    request: WorkspaceInvitationCreateRequest,
    principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    try:
        invitation = _workspace_invitation_service().create(
            email=request.email,
            role=request.role,
            created_by=principal.account_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"invitation": invitation}


@app.get("/api/workspace/invitations/{token}")
def inspect_workspace_invitation(token: str) -> dict[str, Any]:
    try:
        invitation = _workspace_invitation_service().inspect(token)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Invitation is invalid, expired, or already used") from exc
    return {"invitation": invitation}


@app.post("/api/workspace/invitations/complete", status_code=201)
def complete_workspace_invitation(
    request: WorkspaceInvitationCompleteRequest,
) -> dict[str, Any]:
    if request.password != request.confirm_password:
        raise HTTPException(status_code=422, detail="Passwords do not match")
    if not request.display_name.strip():
        raise HTTPException(status_code=422, detail="Display name is required")
    try:
        account = _workspace_invitation_service().complete(
            raw_token=request.token,
            display_name=request.display_name.strip(),
            password=request.password,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 409 if "account" in message else 410
        raise HTTPException(status_code=status_code, detail=message) from exc
    return {"account": _public_workspace_account(account)}


@app.get("/api/workspace/admin/engineer-schedules")
def list_workspace_engineer_schedules(
    _principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    return _workspace_schedule_payload()


@app.put("/api/workspace/admin/engineers/{engineer_id}/schedule")
def replace_workspace_engineer_schedule(
    engineer_id: str,
    request: EngineerScheduleUpdateRequest,
    principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    weekdays = [shift.weekday for shift in request.shifts]
    if len(weekdays) != len(set(weekdays)):
        raise HTTPException(status_code=422, detail="Each weekday can have only one shift")
    normalized_shifts = []
    for shift in request.shifts:
        start_minute = time_to_minutes(shift.start)
        end_minute = time_to_minutes(shift.end, allow_24=True)
        if start_minute == end_minute:
            raise HTTPException(status_code=422, detail="Shift start and end must differ")
        normalized_shifts.append(
            {
                "weekday": shift.weekday,
                "start_minute": start_minute,
                "end_minute": end_minute,
            }
        )
    updated_at = now_iso()
    schedule = ticket_repository.replace_engineer_schedule(
        engineer_id,
        timezone_name=WORKSPACE_SCHEDULE_TIMEZONE,
        shifts=normalized_shifts,
        actor_id=principal.account_id,
        updated_at=updated_at,
    )
    if schedule is None:
        raise HTTPException(status_code=404, detail="Engineer account not found")
    reassigned = _engineer_assignment_service().reassign_off_schedule_cases()
    dispatched = _engineer_assignment_service().dispatch_pending_cases()
    return {
        **_workspace_schedule_payload(),
        "assignment_updates": reassigned + dispatched,
    }


@app.post("/api/workspace/admin/cases/{engineer_case_id}/assignment")
def update_workspace_admin_assignment(
    engineer_case_id: str,
    request: EngineerAdminAssignmentRequest,
    principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    assigned_engineer_id = str(request.engineer_id or "").strip() or None
    if assigned_engineer_id:
        account = ticket_repository.get_workspace_account(assigned_engineer_id)
        on_schedule = on_schedule_engineer_ids(ticket_repository.list_engineer_schedules())
        if (
            not isinstance(account, dict)
            or account.get("role") != "engineer"
            or not bool(account.get("active", True))
            or assigned_engineer_id not in on_schedule
        ):
            raise HTTPException(status_code=409, detail="Engineer is not on schedule")
    updated_at = datetime.now(timezone.utc)
    engineer_case = ticket_repository.update_engineer_case_assignment(
        engineer_case_id,
        expected_version=request.expected_version,
        assignment_status="assigned" if assigned_engineer_id else "pending",
        assigned_engineer_id=assigned_engineer_id,
        assigned_at=updated_at.isoformat() if assigned_engineer_id else None,
        sla_due_at=(updated_at + timedelta(hours=3)).isoformat() if assigned_engineer_id else None,
        reason=request.reason,
        updated_at=updated_at.isoformat(),
        actor=principal.account_id,
        event_type="engineer_case_admin_reassigned",
        dispatch_status="assigned" if assigned_engineer_id else "pending",
    )
    if engineer_case is None:
        current = ticket_repository.get_engineer_case(
            engineer_case_id, include_client_messages=False
        )
        if current is None:
            raise HTTPException(status_code=404, detail="Engineer Case not found")
        raise HTTPException(status_code=409, detail="Engineer Case assignment version changed")
    return {"case": engineer_case}


@app.post("/api/workspace/admin/dispatch")
def dispatch_workspace_pending_cases(
    _principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    cases = _engineer_assignment_service().dispatch_pending_cases()
    return {"cases": cases, "dispatched_count": len(cases)}


@app.post("/api/workspace/admin/reassign-due")
def reassign_workspace_due_cases(
    _principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    cases = _engineer_assignment_service().reassign_due_cases()
    return {"cases": cases, "reassigned_count": len(cases)}


@app.get("/api/workspace/admin/audit")
def list_workspace_admin_audit(
    limit: int = Query(default=100, ge=1, le=1000),
    _principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    events = ticket_repository.list_workspace_audit_events(limit=limit)
    for engineer_case in ticket_repository.list_engineer_case_headers():
        engineer_case_id = str(engineer_case.get("engineer_case_id") or "").strip()
        if not engineer_case_id:
            continue
        for event in ticket_repository.list_engineer_case_events(
            engineer_case_id, limit=min(limit, 500)
        ):
            event_type = str(event.get("event_type") or "")
            if "assign" not in event_type and "dispatch" not in event_type and "sla" not in event_type:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            events.append(
                {
                    "event_type": event_type,
                    "actor_id": str(payload.get("actor") or "system"),
                    "target_id": engineer_case_id,
                    "payload": payload,
                    "created_at": str(event.get("created_at") or payload.get("created_at") or ""),
                }
            )
    events.sort(key=lambda event: str(event.get("created_at") or ""), reverse=True)
    return {"events": events[:limit]}


@app.get("/api/workspace/admin/metrics")
def get_workspace_admin_metrics(
    _principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    engineer_cases = ticket_repository.list_engineer_case_headers()
    accounts = ticket_repository.list_workspace_accounts()
    client_tickets = ticket_repository.list_tickets(include_messages=False)
    billing_tickets = ticket_repository.list_billing_tickets(limit=10000)
    assignment_counts = {"pending": 0, "assigned": 0, "resolved": 0}
    client_status_counts = {
        "open": 0,
        "communicating": 0,
        "escalated": 0,
        "investigating": 0,
        "resolved": 0,
    }
    overdue_count = 0
    first_assignment_seconds: list[float] = []
    resolution_seconds: list[float] = []
    sla_reassign_count = 0
    schedule_reassign_count = 0
    guardrail_reject_count = 0
    now = datetime.now(timezone.utc)

    def _metric_datetime(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    for client_ticket in client_tickets:
        client_status = normalize_ticket_status(client_ticket.get("status"))
        if client_status in client_status_counts:
            client_status_counts[client_status] += 1
    for engineer_case in engineer_cases:
        assignment_status = str(engineer_case.get("assignment_status") or "pending")
        if assignment_status in assignment_counts:
            assignment_counts[assignment_status] += 1
        sla_due_at = _metric_datetime(engineer_case.get("sla_due_at"))
        if assignment_status == "assigned" and sla_due_at and sla_due_at <= now:
            overdue_count += 1
        opened_at = _metric_datetime(engineer_case.get("opened_at"))
        assigned_at = _metric_datetime(engineer_case.get("assigned_at"))
        closed_at = _metric_datetime(engineer_case.get("closed_at"))
        if opened_at and assigned_at and assigned_at >= opened_at:
            first_assignment_seconds.append((assigned_at - opened_at).total_seconds())
        if opened_at and closed_at and closed_at >= opened_at:
            resolution_seconds.append((closed_at - opened_at).total_seconds())
        for event in ticket_repository.list_engineer_case_events(
            str(engineer_case.get("engineer_case_id") or ""), limit=500
        ):
            event_type = str(event.get("event_type") or "").lower()
            sla_reassign_count += int(event_type == "engineer_case_sla_reassigned")
            schedule_reassign_count += int(
                event_type == "engineer_case_schedule_reassigned"
            )
            guardrail_reject_count += int(
                "guardrail" in event_type and ("reject" in event_type or "fail" in event_type)
            )
    engineer_accounts = [account for account in accounts if account.get("role") == "engineer"]
    active_engineer_ids = {
        str(account.get("account_id") or "").strip()
        for account in engineer_accounts
        if bool(account.get("active", True))
    }
    on_schedule = on_schedule_engineer_ids(ticket_repository.list_engineer_schedules(), now)
    on_schedule.intersection_update(active_engineer_ids)
    billing_automation_count = sum(
        1
        for ticket in billing_tickets
        if (ticket.get("route_status") or automation_metadata(
            route_family=ticket.get("route_family"),
            execution_action=ticket.get("execution_action") or ticket.get("route"),
        )["route_status"]) == "automated"
    )
    billing_not_automated_count = len(billing_tickets) - billing_automation_count
    return {
        "client_tickets": {
            **client_status_counts,
            "total": len(client_tickets),
            "not_automated": billing_not_automated_count,
        },
        "engineer_cases": {
            **assignment_counts,
            "total": len(engineer_cases),
            "sla_overdue": overdue_count,
            "dispatch_failed": sum(
                1 for case in engineer_cases if case.get("dispatch_status") == "failed"
            ),
            "rollout_created": sum(
                1
                for case in engineer_cases
                if case.get("trigger_source") == "account_not_automated"
            ),
            "average_first_assignment_seconds": (
                round(sum(first_assignment_seconds) / len(first_assignment_seconds), 2)
                if first_assignment_seconds
                else None
            ),
            "average_resolution_seconds": (
                round(sum(resolution_seconds) / len(resolution_seconds), 2)
                if resolution_seconds
                else None
            ),
            "sla_reassigned": sla_reassign_count,
            "schedule_reassigned": schedule_reassign_count,
        },
        "engineers": {
            "total": len(engineer_accounts),
            "on_schedule": len(on_schedule),
            "off_schedule": len(active_engineer_ids - on_schedule),
            "dispatch_eligible": len(on_schedule),
        },
        "billing": {
            "total": len(billing_tickets),
            "automation": billing_automation_count,
            "not_automated": billing_not_automated_count,
            "internal_email_failed": sum(
                1
                for ticket in billing_tickets
                if str(ticket.get("internal_email_send_status") or "").lower() == "failed"
            ),
        },
        "guardrail": {"rejected": guardrail_reject_count},
        "generated_at": now.isoformat(),
    }


@app.get("/api/workspace/admin/account-automation")
def get_workspace_admin_account_automation(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    route_status: str | None = Query(default=None, pattern="^(automation|automated|not_automated)$"),
    category: str | None = Query(default=None, max_length=128),
    created_from: str | None = Query(default=None, max_length=64),
    created_to: str | None = Query(default=None, max_length=64),
    _principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    return account_automation_payload(ticket_repository, page=page, page_size=page_size, route_status=route_status, category=category, created_from=created_from, created_to=created_to)


@app.get("/api/workspace/admin/account-routing/config")
def get_workspace_admin_account_routing_config(
    _principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    return routing_config_payload()


@app.get("/api/workspace/admin/agent-config")
def get_workspace_admin_agent_config(
    _principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    prompt_service = PromptVersionService(ticket_repository)
    prompt_service.sync_catalog()
    return build_agent_config_payload(
        ticket_repository.list_account_personas(),
        prompt_service.list_prompts(),
    )


@app.get("/api/workspace/admin/prompts")
def get_workspace_admin_prompts(
    _principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    prompt_service = PromptVersionService(ticket_repository)
    prompt_service.sync_catalog()
    return {
        "prompts": prompt_service.list_prompts(),
        "active_release": prompt_service.active_release(),
    }


@app.get("/api/workspace/admin/prompts/{prompt_key}")
def get_workspace_admin_prompt(
    prompt_key: str,
    _principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    try:
        return {"prompt": PromptVersionService(ticket_repository).get_prompt(prompt_key)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/workspace/admin/prompts/{prompt_key}/drafts")
def create_workspace_admin_prompt_draft(
    prompt_key: str,
    request: PromptDraftRequest,
    principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    timestamp = now_iso()
    try:
        version = PromptVersionService(ticket_repository).create_draft(
            prompt_key,
            content=request.content,
            change_note=request.change_note,
            based_on_version=request.based_on_version,
            actor_id=principal.account_id,
            created_at=timestamp,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ticket_repository.record_workspace_audit_event(
        "prompt_draft_created",
        actor_id=principal.account_id,
        target_id=prompt_key,
        payload={"version": version["version"], "change_note": request.change_note},
        created_at=timestamp,
    )
    return {"version": version}


def _workspace_admin_prompt_version_action(
    prompt_key: str,
    version: int,
    principal: WorkspacePrincipal,
    action: str,
) -> dict[str, Any]:
    timestamp = now_iso()
    prompt_service = PromptVersionService(ticket_repository)
    try:
        if action == "schedule":
            result = prompt_service.schedule(prompt_key, version, actor_id=principal.account_id, scheduled_at=timestamp)
        elif action == "unschedule":
            result = prompt_service.unschedule(prompt_key, version)
        elif action == "restore":
            result = prompt_service.restore(prompt_key, version, actor_id=principal.account_id, created_at=timestamp)
        else:  # pragma: no cover - internal caller contract
            raise ValueError("unsupported prompt version action")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ticket_repository.record_workspace_audit_event(
        f"prompt_version_{action}d" if action != "schedule" else "prompt_version_scheduled",
        actor_id=principal.account_id,
        target_id=prompt_key,
        payload={"version": result["version"]},
        created_at=timestamp,
    )
    return {"version": result}


@app.post("/api/workspace/admin/prompts/{prompt_key}/versions/{version}/schedule")
def schedule_workspace_admin_prompt_version(
    prompt_key: str,
    version: int,
    principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    return _workspace_admin_prompt_version_action(prompt_key, version, principal, "schedule")


@app.post("/api/workspace/admin/prompts/{prompt_key}/versions/{version}/unschedule")
def unschedule_workspace_admin_prompt_version(
    prompt_key: str,
    version: int,
    principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    return _workspace_admin_prompt_version_action(prompt_key, version, principal, "unschedule")


@app.post("/api/workspace/admin/prompts/{prompt_key}/versions/{version}/restore")
def restore_workspace_admin_prompt_version(
    prompt_key: str,
    version: int,
    principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    return _workspace_admin_prompt_version_action(prompt_key, version, principal, "restore")


@app.get("/api/workspace/admin/prompt-releases")
def get_workspace_admin_prompt_releases(
    limit: int = 50,
    _principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    return {"releases": PromptVersionService(ticket_repository).list_releases(limit=limit)}


@app.get("/api/workspace/admin/account-routes")
def get_workspace_admin_account_routes(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    executions = ticket_repository.list_account_route_executions()
    summaries = [{key: value for key, value in item.items() if key not in {"system_prompt", "user_prompt"}} for item in executions]
    start = (page - 1) * page_size
    return {"routes": summaries[start : start + page_size], "page": page, "page_size": page_size, "total": len(summaries)}


@app.get("/api/workspace/admin/account-routes/{ticket_id}")
def get_workspace_admin_account_route_detail(
    ticket_id: str,
    _principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    executions = ticket_repository.list_account_route_executions(ticket_id)
    if executions:
        return {"ticket_id": ticket_id, "executions": executions, "legacy": False}
    legacy = ticket_repository.get_billing_ticket_by_client_ticket_id(ticket_id)
    if legacy is None:
        raise HTTPException(status_code=404, detail="Account route not found")
    return {"ticket_id": ticket_id, "executions": [], "legacy": True, "legacy_route": legacy, "prompt_snapshot_available": False}


@app.get("/api/workspace/admin/account-personas")
def get_workspace_admin_account_personas(
    _principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    return {"personas": ticket_repository.list_account_personas()}


@app.post("/api/workspace/admin/account-personas")
def create_workspace_admin_account_persona(
    request: AccountPersonaCreateRequest,
    principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    timestamp = now_iso()
    try:
        version = ticket_repository.create_account_persona(request.persona_key, request.display_name, content=request.content, actor_id=principal.account_id, created_at=timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ticket_repository.record_workspace_audit_event("account_persona_created", actor_id=principal.account_id, target_id=request.persona_key, payload={"version": version["version"]}, created_at=timestamp)
    return {"version": version}


@app.post("/api/workspace/admin/account-personas/{persona_key}/drafts")
def create_workspace_admin_account_persona_draft(
    persona_key: str,
    request: AccountPersonaDraftRequest,
    principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    timestamp = now_iso()
    try:
        version = ticket_repository.create_account_persona_draft(persona_key, content=request.content, change_note=request.change_note, based_on_version=request.based_on_version, actor_id=principal.account_id, created_at=timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ticket_repository.record_workspace_audit_event("account_persona_draft_created", actor_id=principal.account_id, target_id=persona_key, payload={"version": version["version"], "change_note": request.change_note}, created_at=timestamp)
    return {"version": version}


def _account_persona_version_action(persona_key: str, version: int, principal: WorkspacePrincipal, action: str) -> dict[str, Any]:
    timestamp = now_iso()
    method = ticket_repository.publish_account_persona_version if action == "publish" else ticket_repository.rollback_account_persona_version
    try:
        result = method(persona_key, version, actor_id=principal.account_id, published_at=timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ticket_repository.record_workspace_audit_event(f"account_persona_{action}ed", actor_id=principal.account_id, target_id=persona_key, payload={"version": result["version"], "source_version": version}, created_at=timestamp)
    return {"version": result}


@app.post("/api/workspace/admin/account-personas/{persona_key}/versions/{version}/publish")
def publish_workspace_admin_account_persona(persona_key: str, version: int, principal: WorkspacePrincipal = Depends(require_workspace_admin)) -> dict[str, Any]:
    return _account_persona_version_action(persona_key, version, principal, "publish")


@app.post("/api/workspace/admin/account-personas/{persona_key}/versions/{version}/rollback")
def rollback_workspace_admin_account_persona(persona_key: str, version: int, principal: WorkspacePrincipal = Depends(require_workspace_admin)) -> dict[str, Any]:
    return _account_persona_version_action(persona_key, version, principal, "rollback")


@app.patch("/api/workspace/admin/account-personas/{persona_key}")
def set_workspace_admin_account_persona_enabled(persona_key: str, request: AccountPersonaEnabledRequest, principal: WorkspacePrincipal = Depends(require_workspace_admin)) -> dict[str, Any]:
    try:
        persona = ticket_repository.set_account_persona_enabled(persona_key, request.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ticket_repository.record_workspace_audit_event("account_persona_enabled_changed", actor_id=principal.account_id, target_id=persona_key, payload={"enabled": request.enabled}, created_at=now_iso())
    return {"persona": persona}


@app.get("/api/workspace/admin/environment-config")
def get_workspace_admin_environment_config(
    _principal: WorkspacePrincipal = Depends(require_workspace_admin),
) -> dict[str, Any]:
    configured_path = str(os.getenv("SUPPORTPORTAL_ENV_CONFIG_PATH") or "").strip()
    env_path = Path(configured_path) if configured_path else BASE_DIR / ".env"
    try:
        items = environment_config_entries(env_path, required=bool(configured_path))
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Environment configuration inventory unavailable") from exc
    return {"names": [item["name"] for item in items], "items": items}


@app.get("/api/engineer/tickets")
def list_tickets(
    status: str = Query(default="open", pattern="^(open|all|resolved|communicating|escalated|investigating)$"),
) -> dict[str, Any]:
    all_cases = ticket_repository.list_engineer_case_headers()
    filtered_cases: list[dict[str, Any]] = []
    for engineer_case in all_cases:
        if ticket_matches_status_filter(engineer_case, status):
            filtered_cases.append(engineer_case)

    tickets = sorted(
        filtered_cases,
        key=lambda item: (
            1 if normalize_ticket_status(item.get("status")) == INVESTIGATING_STATUS else 0,
            item.get("updated_at", item.get("created_at", "")),
        ),
        reverse=True,
    )
    normalized_filter = status if status == "all" else normalize_ticket_status(status)
    return {"tickets": tickets, "status_filter": normalized_filter}


def _engineer_case_has_multi_agent_run(engineer_case: dict[str, Any]) -> bool:
    agent_state = (
        engineer_case.get("engineer_agent_state")
        if isinstance(engineer_case.get("engineer_agent_state"), dict)
        else {}
    )
    return bool(
        isinstance(agent_state.get("active_plan"), dict)
        and isinstance(agent_state.get("active_execution"), dict)
        and isinstance(agent_state.get("active_review"), dict)
    )


@app.get("/api/engineer/tickets/{ticket_id}")
def get_ticket_detail(ticket_id: str, include_context: bool = Query(default=True)) -> dict[str, Any]:
    engineer_case = _resolve_engineer_case_payload(ticket_id)
    if engineer_case is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    client_ref = engineer_case.get("client_ticket_ref") if isinstance(engineer_case.get("client_ticket_ref"), dict) else {}
    client_ticket_id = str(client_ref.get("ticket_id") or "").strip() or None
    token_usage_fallback = {
        **resolve_ticket_family_identity(
            {
                "ticket_id": ticket_id,
                "client_ticket_id": client_ticket_id,
                "client_ticket_ref": client_ref,
            },
            related_ticket_ids=[str(engineer_case.get("engineer_case_id") or ticket_id)],
        ),
        **aggregate_usage_ledger([]),
    }
    if not include_context:
        engineer_case["client_agent_runtime_state"] = None
        engineer_case["client_agent_events"] = []
        engineer_case["token_usage"] = token_usage_fallback
        engineer_case["engineer_request_records"] = []
        return {"ticket": engineer_case}

    client_ticket = ticket_repository.get_ticket(client_ticket_id) if client_ticket_id else None
    engineer_case["client_agent_runtime_state"] = (
        dict(client_ticket.get("client_agent_runtime_state"))
        if isinstance(client_ticket, dict) and isinstance(client_ticket.get("client_agent_runtime_state"), dict)
        else None
    )
    engineer_case["client_agent_events"] = (
        ticket_repository.list_ticket_agent_events(client_ticket_id, limit=12)
        if client_ticket_id
        else []
    )
    try:
        engineer_case["token_usage"] = rag_service_client.get_ticket_family_token_summary(
            ticket_id=ticket_id,
            client_ticket_id=client_ticket_id,
        )
    except RagServiceError:
        engineer_case["token_usage"] = token_usage_fallback
    engineer_case["engineer_request_records"] = build_engineer_request_records(
        str(client_ref.get("ticket_id") or "")
    )
    return {"ticket": engineer_case}


@app.post("/api/engineer/tickets/{ticket_id}/claim")
async def claim_engineer_ticket(
    ticket_id: str,
    _request: EngineerCaseClaimRequest,
) -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail=(
            f"Manual claim is disabled for Engineer Case {ticket_id}; "
            "use system dispatch or the audited Workspace Admin assignment API"
        ),
    )


@app.post("/api/engineer/tickets/{ticket_id}/multi-agent/run")
async def run_engineer_multi_agent_for_ticket(
    ticket_id: str,
    request: EngineerMultiAgentRunRequest,
) -> dict[str, Any]:
    if not ENGINEER_MULTI_AGENT_ENABLED:
        raise HTTPException(status_code=404, detail="Engineer multi-agent is not enabled")
    engineer_case_payload = _resolve_engineer_case_payload(ticket_id)
    if engineer_case_payload is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if normalize_ticket_status(engineer_case_payload.get("status")) != INVESTIGATING_STATUS:
        raise HTTPException(status_code=400, detail="Multi-agent can only run for investigating tickets.")
    if not isinstance(engineer_case_payload.get("active_investigation"), dict):
        raise HTTPException(status_code=400, detail="No active investigation exists")

    engineer_case = _engineer_case_payload_to_record(engineer_case_payload)
    timestamp = now_iso()
    ensure_engineer_multi_agent_run(
        engineer_case,
        now_value=timestamp,
        reason="manual_investigating_click",
        force=True,
    )
    ticket_repository.save_engineer_case(engineer_case, new_messages=[])

    engineer_case_id = str(engineer_case.get("engineer_case_id") or ticket_id)
    event = {
        "event": "engineer_multi_agent_run",
        "ticket_id": engineer_case_id,
        "client_ticket_id": str((engineer_case.get("client_ticket_ref") or {}).get("ticket_id") or ""),
        "engineer_case_id": engineer_case_id,
        "status": normalize_ticket_status(engineer_case.get("status")),
        "engineer_id": request.engineer_id,
        "message": "Multi-agent investigation run completed.",
        "created_at": timestamp,
    }
    ticket_repository.record_engineer_case_event(engineer_case_id, event["event"], event)
    await dispatch_event(["engineer", "dashboard"], event)

    return {
        "ticket_id": engineer_case_id,
        "status": normalize_ticket_status(engineer_case.get("status")),
        "engineer_agent_state": (
            engineer_case.get("engineer_agent_state")
            if isinstance(engineer_case.get("engineer_agent_state"), dict)
            else None
        ),
        "updated_at": engineer_case.get("updated_at"),
    }


@app.get("/api/engineer/tickets/{ticket_id}/summary")
def get_ticket_summary(ticket_id: str) -> dict[str, Any]:
    ticket = _resolve_engineer_case_payload(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    summary, next_action_needed, model = build_ticket_summary(ticket)
    return {
        "ticket_id": str(ticket.get("ticket_id") or ticket_id),
        "summary": summary,
        "next_action_needed": next_action_needed,
        "model": model,
        "generated_at": now_iso(),
    }


def _record_case_memory_ledger_from_feedback(feedback: dict[str, Any]) -> dict[str, Any]:
    ledger_record = build_case_memory_ledger_record_from_feedback(feedback)
    saved_record = ticket_repository.record_case_memory_ledger(ledger_record)
    ticket_repository.record_engineer_case_event(
        str(saved_record.get("engineer_case_id") or ""),
        "case_memory_ledger_recorded",
        {
            "event": "case_memory_ledger_recorded",
            "ticket_id": str(saved_record.get("engineer_case_id") or ""),
            "client_ticket_id": str(saved_record.get("client_ticket_id") or ""),
            "engineer_case_id": str(saved_record.get("engineer_case_id") or ""),
            "memory_record_id": str(saved_record.get("memory_record_id") or ""),
            "source_feedback_id": str(saved_record.get("source_feedback_id") or ""),
            "ledger_status": str(saved_record.get("ledger_status") or ""),
            "retrieval_enabled": bool(saved_record.get("retrieval_enabled")),
            "active_memory_status": str(saved_record.get("active_memory_status") or ""),
            "created_at": str(saved_record.get("created_at") or now_iso()),
        },
    )
    return saved_record


def _record_engineer_replay_eval_item_from_closed_case(
    *,
    ticket: dict[str, Any],
    engineer_case: dict[str, Any],
    closed_investigation: dict[str, Any] | None,
    saved_feedback: dict[str, Any],
    saved_ledger: dict[str, Any],
    customer_reply: str,
    created_at: str,
) -> dict[str, Any]:
    """Build and save a replay eval dataset candidate after closure."""
    item = build_engineer_replay_eval_item(
        client_ticket=ticket,
        engineer_case=engineer_case,
        closed_investigation=closed_investigation,
        saved_feedback=saved_feedback,
        saved_ledger=saved_ledger,
        customer_reply=customer_reply,
        created_at=created_at,
    )
    saved_item = ticket_repository.record_engineer_replay_eval_item(item)
    ticket_repository.record_engineer_case_event(
        str(saved_item.get("engineer_case_id") or ""),
        "engineer_replay_eval_item_recorded",
        {
            "event": "engineer_replay_eval_item_recorded",
            "eval_item_id": str(saved_item.get("eval_item_id") or ""),
            "engineer_case_id": str(saved_item.get("engineer_case_id") or ""),
            "client_ticket_id": str(saved_item.get("client_ticket_id") or ""),
            "dataset_status": str(saved_item.get("dataset_status") or ""),
            "schema_version": str(saved_item.get("schema_version") or ""),
            "created_at": created_at,
        },
    )
    return saved_item


def _build_engineer_case_closed_after_customer_reply_event(
    *,
    ticket: dict[str, Any],
    engineer_case: dict[str, Any],
    engineer_id: str,
    guardrail_final_id: str | None,
    guardrail_final_decision: str | None,
    feedback: dict[str, Any],
    ledger_record: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    """Build the audit event payload for engineer case closure after customer reply."""
    return {
        "event": "engineer_case_closed_after_customer_reply",
        "ticket_id": str(engineer_case.get("engineer_case_id") or ""),
        "client_ticket_id": str(ticket.get("ticket_id") or ""),
        "engineer_case_id": str(engineer_case.get("engineer_case_id") or ""),
        "engineer_id": engineer_id,
        "status": str(engineer_case.get("status") or ""),
        "customer_reply_message_source": ASSISTANT_MESSAGE_SOURCE_ENGINEER_GUIDANCE,
        "guardrail_final_id": guardrail_final_id or "",
        "guardrail_final_decision": guardrail_final_decision or "",
        "feedback_id": str(feedback.get("feedback_id") or ""),
        "memory_record_id": str(ledger_record.get("memory_record_id") or ""),
        "ledger_status": str(ledger_record.get("ledger_status") or ""),
        "retrieval_enabled": bool(ledger_record.get("retrieval_enabled")),
        "active_memory_status": str(ledger_record.get("active_memory_status") or ""),
        "created_at": created_at,
    }


@app.post("/api/engineer/tickets/{ticket_id}/feedback")
def record_engineer_hitl_feedback(
    ticket_id: str,
    request: EngineerHitlFeedbackRequest,
) -> dict[str, Any]:
    engineer_case = _resolve_engineer_case_payload(ticket_id)
    if engineer_case is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    client_ref = engineer_case.get("client_ticket_ref") if isinstance(engineer_case.get("client_ticket_ref"), dict) else {}
    client_ticket_id = str(client_ref.get("ticket_id") or engineer_case.get("client_ticket_id") or "").strip()
    if not client_ticket_id:
        raise HTTPException(status_code=400, detail="Engineer case is missing client ticket reference")

    feedback = ticket_repository.record_engineer_hitl_feedback(
        {
            "feedback_id": f"hitl_{uuid4().hex}",
            "engineer_case_id": str(engineer_case.get("engineer_case_id") or ticket_id).strip(),
            "client_ticket_id": client_ticket_id,
            "run_id": request.run_id,
            "message_id": request.message_id,
            "evidence_packet_id": request.evidence_packet_id,
            "feedback_type": request.feedback_type,
            "diagnosis_correctness": request.diagnosis_correctness,
            "root_cause_correctness": request.root_cause_correctness,
            "evidence_quality": request.evidence_quality,
            "citation_quality": request.citation_quality,
            "customer_reply_quality": request.customer_reply_quality,
            "missing_information": request.missing_information,
            "incorrect_claims": request.incorrect_claims,
            "corrected_root_cause": request.corrected_root_cause,
            "corrected_solution": request.corrected_solution,
            "corrected_customer_reply": request.corrected_customer_reply,
            "evidence_refs": request.evidence_refs,
            "memory_candidate": request.memory_candidate,
            "memory_safety": request.memory_safety,
            "memory_notes": request.memory_notes,
            "prompt_version": request.prompt_version,
            "workflow_version": request.workflow_version,
            "tool_policy_version": request.tool_policy_version,
            "rag_access_policy_version": request.rag_access_policy_version,
            "evidence_packet_version": request.evidence_packet_version,
            "created_by": request.engineer_id,
            "created_at": now_iso(),
        }
    )
    ledger_record = _record_case_memory_ledger_from_feedback(feedback)
    ticket_repository.record_engineer_case_event(
        str(feedback.get("engineer_case_id") or ""),
        "engineer_hitl_feedback_recorded",
        {
            "event": "engineer_hitl_feedback_recorded",
            "ticket_id": str(feedback.get("engineer_case_id") or ticket_id),
            "client_ticket_id": str(feedback.get("client_ticket_id") or ""),
            "engineer_case_id": str(feedback.get("engineer_case_id") or ""),
            "feedback_id": str(feedback.get("feedback_id") or ""),
            "feedback_type": str(feedback.get("feedback_type") or ""),
            "memory_candidate": str(feedback.get("memory_candidate") or ""),
            "memory_safety": str(feedback.get("memory_safety") or ""),
            "created_at": str(feedback.get("created_at") or now_iso()),
        },
    )
    return {"ticket_id": ticket_id, "feedback": feedback, "case_memory_ledger": ledger_record}


@app.get("/api/engineer/tickets/{ticket_id}/feedback")
def list_engineer_hitl_feedback(
    ticket_id: str,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    engineer_case = _resolve_engineer_case_payload(ticket_id)
    if engineer_case is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    engineer_case_id = str(engineer_case.get("engineer_case_id") or ticket_id).strip()
    client_ref = engineer_case.get("client_ticket_ref") if isinstance(engineer_case.get("client_ticket_ref"), dict) else {}
    client_ticket_id = str(client_ref.get("ticket_id") or engineer_case.get("client_ticket_id") or "").strip()
    return {
        "ticket_id": ticket_id,
        "engineer_case_id": engineer_case_id,
        "client_ticket_id": client_ticket_id,
        "feedback": ticket_repository.list_engineer_hitl_feedback(
            engineer_case_id,
            limit=limit,
        ),
    }


@app.get("/api/engineer/tickets/{ticket_id}/case-memory-ledger")
def list_case_memory_ledger(
    ticket_id: str,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    engineer_case = _resolve_engineer_case_payload(ticket_id)
    if engineer_case is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    engineer_case_id = str(engineer_case.get("engineer_case_id") or ticket_id).strip()
    client_ref = engineer_case.get("client_ticket_ref") if isinstance(engineer_case.get("client_ticket_ref"), dict) else {}
    client_ticket_id = str(client_ref.get("ticket_id") or engineer_case.get("client_ticket_id") or "").strip()
    return {
        "ticket_id": ticket_id,
        "engineer_case_id": engineer_case_id,
        "client_ticket_id": client_ticket_id,
        "ledger": ticket_repository.list_case_memory_ledger(
            engineer_case_id,
            limit=limit,
        ),
    }


@app.post("/api/tickets/{ticket_id}/request-engineer-assistance")
async def request_engineer_assistance(ticket_id: str) -> dict[str, Any]:
    ticket = ticket_repository.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ensure_ticket_defaults(ticket)
    active_case_payload = _active_engineer_case_payload(ticket)
    if active_case_payload is not None:
        return {
            "ticket_id": ticket_id,
            "status": ticket["status"],
            "engineer_case_id": str(active_case_payload.get("engineer_case_id") or ""),
            "updated_at": ticket["updated_at"],
        }

    timestamp = now_iso()
    engineer_case, created = _prepare_engineer_case_for_ticket(
        ticket,
        case_status=ESCALATED_STATUS,
        trigger_source="customer_request",
        trigger_reason="customer_requested_engineer_assistance",
        now_value=timestamp,
    )
    case_context = build_engineer_case_context(ticket, engineer_case)
    opening_context = build_investigation_opening_context(
        case_context,
        trigger_reason="customer_requested_engineer_assistance",
    )
    investigation_result = start_or_refresh_investigation(
        case_context,
        trigger_reason="customer_requested_engineer_assistance",
        trigger_source="customer_request",
        now_value=timestamp,
        next_status=ESCALATED_STATUS,
        opening_context=opening_context,
        ai_turn_builder=generate_investigation_ai_turn,
        engineer_evidence_builder=_build_engineer_evidence_for_investigation,
    )
    engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
    if created:
        engineer_case["title"] = derive_engineer_case_title(
            ticket,
            handoff_packet=case_context.get("engineer_handoff_packet"),
            engineer_agent_state=case_context.get("engineer_agent_state"),
        )
    ticket["status"] = ESCALATED_STATUS
    ticket["updated_at"] = timestamp
    ticket["active_engineer_case_id"] = str(engineer_case.get("engineer_case_id") or "").strip() or None
    ticket["engineer_case_count"] = max(
        int(ticket.get("engineer_case_count") or 0),
        int(engineer_case.get("case_sequence") or 0),
    )
    ticket_repository.save_ticket(ticket, new_messages=[])
    ticket_repository.save_engineer_case(
        engineer_case,
        new_messages=investigation_result.get("new_internal_messages") or [],
    )

    payload = {
        "event": "ticket_escalated",
        "ticket_id": str(engineer_case.get("engineer_case_id") or ticket_id),
        "client_ticket_id": ticket_id,
        "engineer_case_id": str(engineer_case.get("engineer_case_id") or ""),
        "status": ticket["status"],
        "message": "Customer requested engineer assistance.",
        "created_at": timestamp,
    }
    ticket_repository.record_event(ticket_id, payload["event"], payload)
    ticket_repository.record_engineer_case_event(
        str(engineer_case.get("engineer_case_id") or ""),
        payload["event"],
        payload,
    )
    await dispatch_event(["engineer", "dashboard"], payload)
    await dispatch_event(["client"], build_client_sync_event(ticket, payload["event"], payload["message"]))
    await _record_and_dispatch_investigation_event(
        ticket,
        engineer_case,
        created=created,
    )

    return {
        "ticket_id": ticket_id,
        "engineer_case_id": str(engineer_case.get("engineer_case_id") or ""),
        "status": ticket["status"],
        "updated_at": ticket["updated_at"],
    }


@app.post("/api/tickets/{ticket_id}/action")
async def update_ticket(ticket_id: str, request: TicketActionRequest) -> dict[str, Any]:
    ticket = ticket_repository.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    status_map = {
        "processing": COMMUNICATING_STATUS,
        "reopen": COMMUNICATING_STATUS,
        "resolved": RESOLVED_STATUS,
        "investigate": INVESTIGATING_STATUS,
    }

    ensure_ticket_defaults(ticket)
    initial_message_count = len(ticket.get("messages", []))
    ticket["status"] = status_map[request.action]
    engineer_case_payload = _active_engineer_case_payload(ticket)
    engineer_case = (
        _engineer_case_payload_to_record(engineer_case_payload)
        if isinstance(engineer_case_payload, dict)
        else None
    )
    investigation_created = False
    investigation_messages: list[dict[str, Any]] = []
    if request.action == "investigate":
        engineer_case, investigation_created = (
            (engineer_case, False)
            if isinstance(engineer_case, dict)
            else _prepare_engineer_case_for_ticket(
                ticket,
                case_status=INVESTIGATING_STATUS,
                trigger_source="engineer_action",
                trigger_reason="engineer_investigate",
                now_value=now_iso(),
            )
        )
        case_context = build_engineer_case_context(ticket, engineer_case)
        opening_context = build_investigation_opening_context(
            case_context,
            trigger_reason="engineer_investigate",
        )
        investigate_result = start_or_refresh_investigation(
            case_context,
            trigger_reason="engineer_investigate",
            trigger_source="engineer_action",
            now_value=now_iso(),
            next_status=INVESTIGATING_STATUS,
            opening_context=opening_context,
            ai_turn_builder=generate_investigation_ai_turn,
            engineer_evidence_builder=_build_engineer_evidence_for_investigation,
        )
        investigation_messages = investigate_result.get("new_internal_messages") or []
        engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
        engineer_case["assigned_engineer_id"] = str(request.engineer_id or "").strip() or None
        if investigation_created:
            engineer_case["title"] = derive_engineer_case_title(
                ticket,
                handoff_packet=case_context.get("engineer_handoff_packet"),
                engineer_agent_state=case_context.get("engineer_agent_state"),
            )
    elif request.action == "resolved":
        if isinstance(engineer_case, dict):
            case_context = build_engineer_case_context(ticket, engineer_case)
            investigation_to_persist, investigation_messages = _close_active_investigation(
                case_context,
                now_value=now_iso(),
                system_note="Investigation closed because the ticket was marked resolved.",
            )
            engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
            engineer_case["status"] = RESOLVED_STATUS
    elif request.action in {"processing", "reopen"}:
        if isinstance(engineer_case, dict):
            case_context = build_engineer_case_context(ticket, engineer_case)
            investigation_to_persist, investigation_messages = _close_active_investigation(
                case_context,
                now_value=now_iso(),
                system_note="Investigation closed because the ticket returned to the normal AI-managed communication flow.",
            )
            engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
            engineer_case["status"] = COMMUNICATING_STATUS

    ticket["updated_at"] = now_iso()
    ticket["last_engineer_action"] = {
        "action": request.action,
        "engineer_id": request.engineer_id,
        "note": request.note,
        "created_at": now_iso(),
    }
    new_messages = ticket.get("messages", [])[initial_message_count:]
    ticket_repository.save_ticket(ticket, new_messages=new_messages)
    if isinstance(engineer_case, dict):
        ticket["active_engineer_case_id"] = (
            None
            if str(engineer_case.get("investigation_state") or "").strip().lower() == "closed"
            else str(engineer_case.get("engineer_case_id") or "").strip() or None
        )
        ticket["engineer_case_count"] = max(
            int(ticket.get("engineer_case_count") or 0),
            int(engineer_case.get("case_sequence") or 0),
        )
        ticket_repository.save_ticket(ticket, new_messages=[])
        ticket_repository.save_engineer_case(
            engineer_case,
            new_messages=investigation_messages,
        )

    payload = {
        "event": "ticket_updated",
        "ticket_id": str(engineer_case.get("engineer_case_id") or ticket_id) if isinstance(engineer_case, dict) else ticket_id,
        "client_ticket_id": ticket_id,
        "engineer_case_id": str(engineer_case.get("engineer_case_id") or "") if isinstance(engineer_case, dict) else None,
        "status": ticket["status"],
        "engineer_id": request.engineer_id,
        "created_at": now_iso(),
    }
    ticket_repository.record_event(ticket_id, payload["event"], payload)
    if isinstance(engineer_case, dict):
        ticket_repository.record_engineer_case_event(
            str(engineer_case.get("engineer_case_id") or ""),
            payload["event"],
            payload,
        )
    await dispatch_event(["engineer", "dashboard"], payload)
    await dispatch_event(["client"], build_client_sync_event(ticket, payload["event"]))
    if isinstance(engineer_case, dict):
        await _record_and_dispatch_investigation_event(
            ticket,
            engineer_case,
            created=investigation_created,
        )

    return {
        "ticket_id": ticket_id,
        "status": ticket["status"],
        "updated_at": ticket["updated_at"],
    }


def _ticket_turn_has_completed_response(ticket: dict[str, Any], message_created_at: str) -> bool:
    expected_created_at = str(message_created_at or "").strip()
    if not expected_created_at:
        return False

    seen_customer_turn = False
    for message in list(ticket.get("messages") or []):
        role = str(message.get("role") or "").strip().lower()
        created_at = str(message.get("created_at") or "").strip()
        if role == "customer" and created_at == expected_created_at:
            seen_customer_turn = True
            continue
        if not seen_customer_turn:
            continue
        if role == "customer":
            return False
        if role:
            return True

    for event in ticket_repository.list_ticket_events(str(ticket.get("ticket_id") or "")):
        if str(event.get("event_type") or "").strip().lower() != "ticket_ai_response_ready":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if str(payload.get("message_created_at") or "").strip() == expected_created_at:
            return True
    return False


@app.post("/api/tickets/{ticket_id}/cancel-pending")
async def cancel_pending_ticket_query(ticket_id: str, request: CancelPendingRequest) -> dict[str, Any]:
    ticket = ticket_repository.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ensure_ticket_defaults(ticket)
    customer_id = str(ticket.get("customer_id") or "").strip()
    request_customer_id = str(request.customer_id or "").strip()
    if request_customer_id and customer_id and request_customer_id != customer_id:
        raise HTTPException(status_code=403, detail="Ticket customer mismatch")

    message_created_at = request.message_created_at.strip()
    if not message_created_at:
        raise HTTPException(status_code=400, detail="message_created_at is required")

    if _ticket_turn_has_completed_response(ticket, message_created_at):
        return {
            "ticket_id": ticket_id,
            "canceled": False,
            "already_completed": True,
            "message_created_at": message_created_at,
            "updated_at": str(ticket.get("updated_at") or now_iso()),
        }

    ticket["updated_at"] = now_iso()
    ticket_repository.save_ticket(ticket, new_messages=[])

    payload = {
        "event": "ticket_ai_generation_stopped",
        "ticket_id": ticket_id,
        "customer_id": customer_id,
        "status": ticket["status"],
        "message_created_at": message_created_at,
        "message": "AI generation stopped by customer.",
        "created_at": now_iso(),
    }
    ticket_repository.record_event(ticket_id, payload["event"], payload)
    await dispatch_event(["engineer", "dashboard"], payload)

    client_payload = build_client_sync_event(ticket, payload["event"], payload["message"])
    client_payload["message_created_at"] = message_created_at
    await dispatch_event(["client"], client_payload)

    return {
        "ticket_id": ticket_id,
        "canceled": True,
        "already_completed": False,
        "message_created_at": message_created_at,
        "updated_at": ticket["updated_at"],
    }


@app.post("/api/engineer/tickets/{ticket_id}/investigation/messages")
async def post_investigation_message(
    ticket_id: str,
    request: InvestigationMessageRequest,
) -> dict[str, Any]:
    engineer_case_payload = _resolve_engineer_case_payload(ticket_id)
    if engineer_case_payload is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket = ticket_repository.get_ticket(
        str((engineer_case_payload.get("client_ticket_ref") or {}).get("ticket_id") or "")
    )
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ensure_ticket_defaults(ticket)
    if not isinstance(engineer_case_payload.get("active_investigation"), dict):
        raise HTTPException(status_code=400, detail="No active investigation exists")

    engineer_case = _engineer_case_payload_to_record(engineer_case_payload)
    case_context = build_engineer_case_context(ticket, engineer_case)
    timestamp = now_iso()
    if request.multi_agent_enabled and ENGINEER_MULTI_AGENT_ENABLED:
        # Multi-agent workspace is active for this ticket: refresh the
        # Plan/Execute/Review state from the engineer note BEFORE the Engineer
        # AI turn so it reasons over the updated multi-agent state. The refresh
        # does not enforce replan limits or branch on replan_required.
        ensure_engineer_multi_agent_run(
            engineer_case,
            now_value=timestamp,
            reason="engineer_message",
            engineer_note=request.message.strip(),
            force=True,
        )
    else:
        # Guardrail-only mode intentionally does not run or hydrate the
        # multi-agent pipeline on message submit. Empty legacy cases are seeded
        # when the case is entered/read, not when the engineer replies.
        pass
    # Snapshot the multi-agent state (refreshed or hydrated) so it can be
    # re-applied after the AI turn re-normalizes engineer_agent_state, which
    # otherwise drops active_plan/active_execution/active_review when the AI
    # turn payload does not carry them.
    preserved_multi_agent_state = copy.deepcopy(
        engineer_case.get("engineer_agent_state")
        if isinstance(engineer_case.get("engineer_agent_state"), dict)
        else {}
    )
    if isinstance(preserved_multi_agent_state, dict) and preserved_multi_agent_state:
        case_context["engineer_agent_state"] = copy.deepcopy(preserved_multi_agent_state)
    result = append_engineer_investigation_message(
        case_context,
        engineer_message=request.message.strip(),
        now_value=timestamp,
        ai_turn_builder=generate_investigation_ai_turn,
    )
    engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
    # Re-apply the preserved multi-agent state so the panel stays populated
    # regardless of whether the AI turn carried the multi-agent fields.
    if isinstance(preserved_multi_agent_state, dict) and preserved_multi_agent_state:
        merged_state = dict(
            engineer_case.get("engineer_agent_state")
            if isinstance(engineer_case.get("engineer_agent_state"), dict)
            else {}
        )
        for key in (
            "active_plan",
            "plan_id",
            "plan_version",
            "plan_agent_version",
            "active_execution",
            "execution_id",
            "execution_version",
            "execute_agent_version",
            "evidence_packet",
            "task_results",
            "active_review",
            "review_id",
            "review_version",
            "review_agent_version",
            "review_decision",
            "replan_count",
            "last_revise_context",
            "multi_agent_last_run",
        ):
            if key in preserved_multi_agent_state:
                merged_state[key] = preserved_multi_agent_state[key]
        engineer_case["engineer_agent_state"] = merged_state
        case_context["engineer_agent_state"] = copy.deepcopy(merged_state)
    ticket["updated_at"] = timestamp
    ticket["last_engineer_action"] = {
        "action": "investigation_message",
        "engineer_id": request.engineer_id,
        "note": request.message.strip(),
        "created_at": timestamp,
    }
    ticket_repository.save_ticket(ticket, new_messages=[])
    ticket_repository.save_engineer_case(
        engineer_case,
        new_messages=result.get("new_internal_messages"),
    )
    await _record_and_dispatch_investigation_event(ticket, engineer_case)

    return {
        "ticket_id": str(engineer_case.get("engineer_case_id") or ticket_id),
        "status": ticket["status"],
        "active_investigation": result.get("active_investigation"),
        "engineer_agent_state": (
            case_context.get("engineer_agent_state")
            if isinstance(case_context.get("engineer_agent_state"), dict)
            else None
        ),
        "updated_at": ticket["updated_at"],
    }


def ensure_engineer_multi_agent_run(
    engineer_case: dict[str, Any],
    *,
    now_value: str,
    reason: str,
    engineer_note: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run one Plan → Execute → Review cycle and merge it into agent state.

    Used to seed every new Engineer case with an initial multi-agent run and to
    refresh the multi-agent state when an engineer message is submitted with
    ``multi_agent_enabled=true``. It does NOT enforce replan limits or branch on
    ``replan_required`` — the runtime pauses automatic replan while this
    activation is in effect.

    Args:
        engineer_case: The engineer case record. Mutated in place; the
            ``engineer_agent_state`` is refreshed from the existing handoff
            packet on the case. Not saved to the repository by this helper.
        now_value: ISO-8601 timestamp for created_at.
        reason: Short label describing why the run is happening (e.g.
            ``"manual_investigating_click"`` or ``"engineer_message"``). Recorded in
            ``multi_agent_last_run`` for observability only.
        engineer_note: Optional engineer message to carry as revise_context for
            the refresh round. ``None`` for initial creation runs.
        force: When ``True``, run the round even if active_plan /
            active_execution / active_review already exist. Defaults to
            ``False`` so initial creation is idempotent.

    Returns the updated engineer_case dict (mutated in place).
    """
    handoff_packet = (
        engineer_case.get("engineer_handoff_packet")
        if isinstance(engineer_case.get("engineer_handoff_packet"), dict)
        else {}
    )

    agent_state = (
        engineer_case.get("engineer_agent_state")
        if isinstance(engineer_case.get("engineer_agent_state"), dict)
        else {}
    )

    has_existing_run = bool(
        isinstance(agent_state.get("active_plan"), dict)
        and isinstance(agent_state.get("active_execution"), dict)
        and isinstance(agent_state.get("active_review"), dict)
    )
    if has_existing_run and not force:
        return engineer_case

    revise_context: dict[str, Any] | None = None
    if engineer_note:
        revise_context = {
            "revise_note": engineer_note,
            "engineer_feedback": {
                "note": engineer_note,
                "created_at": now_value,
            },
            "refresh_reason": reason,
        }

    # 1. Plan
    active_plan = build_engineer_plan(
        summary_packet=handoff_packet,
        mem0_context=None,
        skill_inventory=None,
        revise_context=revise_context,
        now_value=now_value,
    )

    # 2. Execute
    active_execution = execute_engineer_plan(
        active_plan=active_plan,
        summary_packet=handoff_packet,
        engineer_agent_state=agent_state,
        execution_context=None,
        now_value=now_value,
    )

    # 3. Review
    active_review = review_execution(
        active_execution=active_execution,
        engineer_agent_state=agent_state,
        handoff_packet=handoff_packet,
        ticket=engineer_case,
        now_value=now_value,
    )

    # 4. Merge into agent_state while preserving reply_readiness, guardrail
    #    fields, investigation state, and any summary-packet fields the RAG
    #    escalation path may have already stamped onto the case.
    merged_state = dict(agent_state)
    merged_state.update({
        "active_plan": active_plan,
        "plan_id": active_plan.get("plan_id", ""),
        "plan_version": active_plan.get("plan_version", ""),
        "plan_agent_version": active_plan.get("plan_agent_version", ""),
        "active_execution": active_execution,
        "execution_id": active_execution.get("execution_id", ""),
        "execution_version": active_execution.get("execution_version", ""),
        "execute_agent_version": active_execution.get("execute_agent_version", ""),
        "evidence_packet": active_execution.get("evidence_packet", {}),
        "task_results": active_execution.get("task_results", []),
        "active_review": active_review,
        "review_id": active_review.get("review_id", ""),
        "review_version": active_review.get("review_version", ""),
        "review_agent_version": active_review.get("review_agent_version", ""),
        "review_decision": active_review.get("review_decision", ""),
        "replan_count": active_review.get("replan_count", 0),
        "multi_agent_last_run": {
            "reason": reason,
            "plan_id": active_plan.get("plan_id", ""),
            "execution_id": active_execution.get("execution_id", ""),
            "review_id": active_review.get("review_id", ""),
            "created_at": now_value,
        },
    })
    if revise_context is not None:
        merged_state["last_revise_context"] = revise_context

    engineer_case["engineer_agent_state"] = merged_state
    return engineer_case


def _run_engineer_multi_agent_round(
    engineer_case: dict[str, Any],
    *,
    revise_context: dict[str, Any] | None,
    now_value: str,
) -> dict[str, Any]:
    """Deprecated single-round Plan → Execute → Review wrapper.

    The revise/replan loop that used this helper is paused for the current
    runtime activation (see confirm_investigation_reply). It is kept as a thin
    delegate over ensure_engineer_multi_agent_run so any remaining internal
    callers continue to work without implying max-retry replan semantics. New
    code should call ensure_engineer_multi_agent_run directly.

    Returns the updated engineer_case dict (mutated in place). Does NOT save.
    """
    engineer_note = None
    if isinstance(revise_context, dict):
        engineer_note = str(revise_context.get("revise_note") or "").strip() or None
    return ensure_engineer_multi_agent_run(
        engineer_case,
        now_value=now_value,
        reason="legacy_multi_agent_round",
        engineer_note=engineer_note,
        force=True,
    )


@app.post("/api/engineer/tickets/{ticket_id}/investigation/confirmation")
async def confirm_investigation_reply(
    ticket_id: str,
    request: InvestigationConfirmationRequest,
) -> dict[str, Any]:
    engineer_case_payload = _resolve_engineer_case_payload(ticket_id)
    if engineer_case_payload is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket = ticket_repository.get_ticket(
        str((engineer_case_payload.get("client_ticket_ref") or {}).get("ticket_id") or "")
    )
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ensure_ticket_defaults(ticket)
    if not isinstance(engineer_case_payload.get("active_investigation"), dict):
        raise HTTPException(status_code=400, detail="No active investigation exists")
    if request.decision in ("approve", "final_approve"):
        draft_customer_reply = str(
            (engineer_case_payload.get("active_investigation") or {}).get("draft_customer_reply") or ""
        ).strip()
        if not draft_customer_reply:
            raise HTTPException(status_code=400, detail="A draft customer reply is required before approval.")
        reply_readiness = (
            (engineer_case_payload.get("engineer_agent_state") or {}).get("reply_readiness")
            if isinstance(engineer_case_payload.get("engineer_agent_state"), dict)
            else None
        )
        if not (
            isinstance(reply_readiness, dict)
            and bool(reply_readiness.get("ready_for_customer_reply"))
        ):
            raise HTTPException(
                status_code=400,
                detail="A backend-validated customer reply is required before approval.",
            )

    engineer_case = _engineer_case_payload_to_record(engineer_case_payload)
    case_context = build_engineer_case_context(ticket, engineer_case)
    timestamp = now_iso()

    if request.decision == "revise":
        # ---- Revise path (replan/max-retry paused) ----
        # The automatic Plan/Execute/Review replan loop and its max-2 retry
        # enforcement are intentionally paused for this runtime activation.
        # Revise now only appends the engineer note, clears any blocked/final
        # guardrail state, sets the investigation back to active, and lets the
        # next engineer message or guardrail attempt continue. The existing
        # active_plan/active_execution/active_review are left untouched so the
        # Multi-Agent Run panel keeps showing the last captured run.
        new_internal_messages: list[dict[str, Any]] = []

        # Append engineer revision message to investigation
        active_investigation = case_context.get("active_investigation")
        if isinstance(active_investigation, dict):
            sequence = len(active_investigation.get("messages", [])) + 1
            revision_message = build_internal_message(
                str(active_investigation.get("id") or ""),
                "engineer",
                str(request.note or "").strip(),
                timestamp,
                sequence=sequence,
            )
            active_investigation.setdefault("messages", []).append(revision_message)
            previous_state = str(active_investigation.get("state") or "").strip().lower()
            if previous_state == INVESTIGATION_STATE_AWAITING_FINAL_APPROVAL:
                active_investigation["state"] = "active"
                active_investigation["final_confirmation_requested_at"] = None
            active_investigation["updated_at"] = timestamp
            new_internal_messages.append(revision_message)

        # Clear any blocked/final guardrail state so the engineer can continue
        # editing and re-run the guardrail on the next approve.
        revised_agent_state = (
            engineer_case.get("engineer_agent_state")
            if isinstance(engineer_case.get("engineer_agent_state"), dict)
            else {}
        )
        for guardrail_key in (
            "active_guardrail_final",
            "guardrail_final_id",
            "guardrail_final_version",
            "guardrail_final_decision",
            "final_approval_required",
            "final_approved_at",
        ):
            revised_agent_state.pop(guardrail_key, None)
        case_context["engineer_agent_state"] = engineer_case.get("engineer_agent_state")
        engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
        engineer_case["status"] = INVESTIGATING_STATUS
        ticket["status"] = INVESTIGATING_STATUS
        result = {
            "active_investigation": case_context.get("active_investigation"),
            "closed_investigation": None,
            "new_internal_messages": new_internal_messages,
            "customer_reply": "",
        }
    elif request.decision == "approve":
        # ---- Guardrail final review path (first approve) ----
        active_investigation = case_context.get("active_investigation")
        if not isinstance(active_investigation, dict):
            raise HTTPException(status_code=400, detail="No active investigation exists")

        agent_state = (
            engineer_case.get("engineer_agent_state")
            if isinstance(engineer_case.get("engineer_agent_state"), dict)
            else {}
        )
        draft_reply = str(active_investigation.get("draft_customer_reply") or "").strip()
        reply_readiness = agent_state.get("reply_readiness") if isinstance(agent_state.get("reply_readiness"), dict) else None
        active_review = agent_state.get("active_review") if isinstance(agent_state.get("active_review"), dict) else None
        evidence_packet = agent_state.get("evidence_packet") if isinstance(agent_state.get("evidence_packet"), dict) else None
        task_results = agent_state.get("task_results") if isinstance(agent_state.get("task_results"), list) else None
        handoff_packet = (
            ticket.get("engineer_handoff_packet")
            if isinstance(ticket.get("engineer_handoff_packet"), dict)
            else None
        )

        # Run deterministic guardrail final agent
        guardrail_packet = run_engineer_guardrail_final(
            draft_customer_reply=draft_reply,
            reply_readiness=reply_readiness,
            active_review=active_review,
            evidence_packet=evidence_packet,
            task_results=task_results,
            engineer_handoff_packet=handoff_packet,
            requester=str(ticket.get("requester") or "").strip() or None,
            customer_id=str(ticket.get("customer_id") or "").strip() or None,
            language_hint=detect_customer_reply_language(
                (handoff_packet or {}).get("latest_customer_message", "") if isinstance(handoff_packet, dict) else "",
                draft_reply,
            ),
        )
        guardrail_packet["created_at"] = timestamp

        # Append internal message about guardrail result
        sequence = len(active_investigation.get("messages", [])) + 1
        guardrail_decision = guardrail_packet.get("decision", "blocked")
        guardrail_summary = (
            f"Guardrail final review complete. Decision: {guardrail_decision}. "
            f"Guardrail ID: {guardrail_packet.get('guardrail_id', 'unknown')}. "
        )
        if guardrail_decision == "approved_for_final_engineer_review":
            guardrail_summary += "Customer reply passed all guardrail checks. Awaiting final engineer approval."
        else:
            blockers_list = guardrail_packet.get("blockers", [])
            blockers_text = "; ".join(blockers_list) if blockers_list else "unknown reason"
            guardrail_summary += f"Customer reply BLOCKED: {blockers_text}"
        guardrail_message = build_internal_message(
            str(active_investigation.get("id") or ""),
            "engineer_ai",
            guardrail_summary,
            timestamp,
            sequence=sequence,
        )
        active_investigation.setdefault("messages", []).append(guardrail_message)

        guardrail_approved = guardrail_decision == "approved_for_final_engineer_review"
        # Blocked guardrail results must leave the investigation editable so the
        # engineer can revise instead of getting stuck without a final approve.
        active_investigation["state"] = (
            INVESTIGATION_STATE_AWAITING_FINAL_APPROVAL if guardrail_approved else "active"
        )
        active_investigation["updated_at"] = timestamp

        # Update engineer_agent_state with guardrail final packet
        agent_state["phase"] = "awaiting_final_approval" if guardrail_approved else "guardrail_blocked"
        agent_state["active_guardrail_final"] = guardrail_packet
        agent_state["guardrail_final_id"] = guardrail_packet.get("guardrail_id", "")
        agent_state["guardrail_final_version"] = guardrail_packet.get("guardrail_version", "")
        agent_state["guardrail_final_decision"] = guardrail_decision
        agent_state["final_approval_required"] = guardrail_approved
        case_context["engineer_agent_state"] = agent_state
        engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
        engineer_case["status"] = INVESTIGATING_STATUS
        engineer_case["investigation_state"] = active_investigation["state"]
        ticket["status"] = INVESTIGATING_STATUS
        result = {
            "active_investigation": active_investigation,
            "closed_investigation": None,
            "new_internal_messages": [guardrail_message],
            "customer_reply": "",
            "active_guardrail_final": guardrail_packet,
        }
    elif request.decision == "final_approve":
        # ---- Final approve path (second approval) ----
        active_investigation = case_context.get("active_investigation")
        if not isinstance(active_investigation, dict):
            raise HTTPException(status_code=400, detail="No active investigation exists")

        agent_state = (
            engineer_case.get("engineer_agent_state")
            if isinstance(engineer_case.get("engineer_agent_state"), dict)
            else {}
        )
        active_guardrail_final = agent_state.get("active_guardrail_final") if isinstance(agent_state.get("active_guardrail_final"), dict) else None

        # Validate preconditions for final_approve
        if str(active_investigation.get("state") or "").strip().lower() != INVESTIGATION_STATE_AWAITING_FINAL_APPROVAL:
            raise HTTPException(
                status_code=400,
                detail="Investigation must be in awaiting_final_approval state before final approval.",
            )
        if not isinstance(active_guardrail_final, dict):
            raise HTTPException(
                status_code=400,
                detail="Guardrail final review has not been completed. Approve first to run guardrail.",
            )
        if active_guardrail_final.get("decision") != "approved_for_final_engineer_review":
            raise HTTPException(
                status_code=400,
                detail="Guardrail final review did not approve the customer reply. Address blockers before final approval.",
            )
        guardrail_customer_reply = str(active_guardrail_final.get("customer_reply") or "").strip()
        if not guardrail_customer_reply:
            raise HTTPException(
                status_code=400,
                detail="Guardrail final packet is missing the customer reply.",
            )

        # Execute the old close logic (call apply_investigation_confirmation)
        active_investigation["draft_customer_reply"] = guardrail_customer_reply
        result = apply_investigation_confirmation(
            case_context,
            decision="approve",
            note=str(request.note or "").strip(),
            now_value=timestamp,
            ai_turn_builder=generate_investigation_ai_turn,
        )
        engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
        engineer_case["status"] = RESOLVED_STATUS
        ticket["status"] = COMMUNICATING_STATUS

        # Stamp final_approved_at in agent state
        agent_state["final_approved_at"] = timestamp
        agent_state["phase"] = "closed"
        case_context["engineer_agent_state"] = agent_state
        engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
        engineer_case["status"] = RESOLVED_STATUS

    initial_message_count = len(ticket.get("messages", []))
    customer_reply = str(result.get("customer_reply") or "").strip()
    if request.decision == "final_approve" and customer_reply:
        ticket["messages"].append(
            {
                "role": "assistant",
                "content": customer_reply,
                "created_at": timestamp,
                "assistant_message_source": ASSISTANT_MESSAGE_SOURCE_ENGINEER_GUIDANCE,
                "supports_customer_resolution": True,
            }
        )

    ticket["updated_at"] = timestamp
    ticket["last_engineer_action"] = {
        "action": f"investigation_{request.decision}",
        "engineer_id": request.engineer_id,
        "note": str(request.note or "").strip() or customer_reply,
        "created_at": timestamp,
    }
    new_messages = ticket.get("messages", [])[initial_message_count:]
    ticket_repository.save_ticket(ticket, new_messages=new_messages)
    ticket["active_engineer_case_id"] = (
        None if request.decision == "final_approve" else str(engineer_case.get("engineer_case_id") or "").strip() or None
    )
    ticket_repository.save_ticket(ticket, new_messages=[])
    ticket_repository.save_engineer_case(
        engineer_case,
        new_messages=result.get("new_internal_messages"),
    )
    if request.decision == "final_approve":
        resolved_assignment = _engineer_assignment_service().resolve_case(
            str(engineer_case.get("engineer_case_id") or ticket_id),
            actor=str(request.engineer_id or "engineer").strip() or "engineer",
        )
        if isinstance(resolved_assignment, dict):
            engineer_case.update(resolved_assignment)
        auto_feedback = build_engineer_auto_hitl_feedback(
            client_ticket=ticket,
            engineer_case=engineer_case,
            closed_investigation=(
                result.get("closed_investigation")
                if isinstance(result.get("closed_investigation"), dict)
                else None
            ),
            engineer_id=request.engineer_id,
            customer_reply=customer_reply,
            created_at=timestamp,
        )
        saved_feedback = ticket_repository.record_engineer_hitl_feedback(auto_feedback)
        saved_ledger = _record_case_memory_ledger_from_feedback(saved_feedback)
        ticket_repository.record_engineer_case_event(
            str(saved_feedback.get("engineer_case_id") or ""),
            "engineer_hitl_feedback_auto_reviewed",
            {
                "event": "engineer_hitl_feedback_auto_reviewed",
                "ticket_id": str(saved_feedback.get("engineer_case_id") or ticket_id),
                "client_ticket_id": str(saved_feedback.get("client_ticket_id") or ""),
                "engineer_case_id": str(saved_feedback.get("engineer_case_id") or ""),
                "feedback_id": str(saved_feedback.get("feedback_id") or ""),
                "feedback_type": str(saved_feedback.get("feedback_type") or ""),
                "memory_candidate": str(saved_feedback.get("memory_candidate") or ""),
                "memory_safety": str(saved_feedback.get("memory_safety") or ""),
                "created_at": str(saved_feedback.get("created_at") or timestamp),
            },
        )
    await _record_and_dispatch_investigation_event(ticket, engineer_case)

    if request.decision == "final_approve" and customer_reply:
        payload = {
            "event": "ticket_guidance_applied",
            "ticket_id": str(engineer_case.get("engineer_case_id") or ticket_id),
            "client_ticket_id": str(ticket.get("ticket_id") or ""),
            "engineer_case_id": str(engineer_case.get("engineer_case_id") or ""),
            "status": engineer_case["status"],
            "engineer_id": request.engineer_id,
            "message": customer_reply[:200],
            "created_at": timestamp,
        }
        ticket_repository.record_event(str(ticket.get("ticket_id") or ""), payload["event"], payload)
        ticket_repository.record_engineer_case_event(
            str(engineer_case.get("engineer_case_id") or ""),
            payload["event"],
            payload,
        )
        await dispatch_event(["engineer", "dashboard"], payload)
        await dispatch_event(["client"], build_client_sync_event(ticket, payload["event"]))

        guardrail_final_id = ""
        guardrail_final_decision = ""
        if isinstance(active_guardrail_final, dict):
            guardrail_final_id = str(active_guardrail_final.get("guardrail_id") or "").strip()
            guardrail_final_decision = str(active_guardrail_final.get("decision") or "").strip()

        closure_event = _build_engineer_case_closed_after_customer_reply_event(
            ticket=ticket,
            engineer_case=engineer_case,
            engineer_id=request.engineer_id,
            guardrail_final_id=guardrail_final_id,
            guardrail_final_decision=guardrail_final_decision,
            feedback=saved_feedback,
            ledger_record=saved_ledger,
            created_at=timestamp,
        )
        ticket_repository.record_event(
            str(ticket.get("ticket_id") or ""),
            closure_event["event"],
            closure_event,
        )
        ticket_repository.record_engineer_case_event(
            str(engineer_case.get("engineer_case_id") or ""),
            closure_event["event"],
            closure_event,
        )

        _record_engineer_replay_eval_item_from_closed_case(
            ticket=ticket,
            engineer_case=engineer_case,
            closed_investigation=(
                result.get("closed_investigation")
                if isinstance(result.get("closed_investigation"), dict)
                else None
            ),
            saved_feedback=saved_feedback,
            saved_ledger=saved_ledger,
            customer_reply=customer_reply,
            created_at=timestamp,
        )

    return {
        "ticket_id": str(engineer_case.get("engineer_case_id") or ticket_id),
        "status": engineer_case["status"],
        "active_investigation": result.get("active_investigation"),
        "closed_investigation": result.get("closed_investigation"),
        "engineer_agent_state": (
            case_context.get("engineer_agent_state")
            if isinstance(case_context.get("engineer_agent_state"), dict)
            else None
        ),
        "active_guardrail_final": (
            result.get("active_guardrail_final")
            if isinstance(result.get("active_guardrail_final"), dict)
            else None
        ),
        "updated_at": ticket["updated_at"],
    }


@app.get("/api/engineer/replay-eval-dataset")
def list_engineer_replay_eval_dataset(
    limit: int = Query(default=100, ge=1, le=1000),
    status: str | None = Query(default=None, pattern="^(candidate|active|archived)$"),
) -> dict[str, Any]:
    """List engineer replay eval dataset items (internal API)."""
    items = ticket_repository.list_engineer_replay_eval_items(limit=limit, status=status)
    return {"items": items, "total": len(items)}


@app.get("/api/engineer/replay-eval-dataset/export")
def export_engineer_replay_eval_dataset(
    limit: int = Query(default=500, ge=1, le=5000),
    status: str | None = Query(default=None, pattern="^(candidate|active|archived)$"),
) -> StreamingResponse:
    """Export engineer replay eval dataset as JSONL (internal API)."""
    import io

    items = ticket_repository.list_engineer_replay_eval_items(limit=limit, status=status)

    buffer = io.StringIO()
    for item in items:
        buffer.write(json.dumps(item, ensure_ascii=False) + "\n")
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=engineer-replay-eval-dataset.jsonl"},
    )


@app.get("/api/dashboard/metrics")
def dashboard_metrics() -> dict[str, Any]:
    tickets = ticket_repository.list_tickets(include_messages=False)
    recent_event_rows = ticket_repository.list_events(limit=240)
    recent_events = normalize_ticket_dashboard_events(recent_event_rows)
    return build_ticket_dashboard_metrics(tickets, recent_events)


@app.get("/api/dashboard/knowledge-metrics")
def dashboard_knowledge_metrics() -> dict[str, Any]:
    try:
        return rag_service_client.knowledge_metrics()
    except RagServiceError:
        return build_empty_knowledge_metrics(
            storage_mode="unreachable",
            embedding_model=_knowledge_embedding_model(),
            vector_table=_knowledge_vector_table(),
        )


@app.get("/api/dashboard/knowledge-ingestions")
def dashboard_knowledge_ingestions(
    limit: int = Query(default=20, ge=1, le=100),
    status: str = Query(default="all", pattern="^(all|queued|processing|completed|failed)$"),
    knowledge_type: str = Query(default="all", pattern="^(all|official|technical)$"),
) -> dict[str, Any]:
    try:
        return rag_service_client.list_ingestions(
            limit=limit,
            status=status,
            knowledge_type=knowledge_type,
        )
    except RagServiceError:
        return {
            "ingestions": [],
            "status_filter": status,
            "knowledge_type_filter": knowledge_type,
        }


@app.get("/api/dashboard/knowledge-ingestions/{ingestion_id}/report")
def dashboard_knowledge_ingestion_report(ingestion_id: str) -> dict[str, Any]:
    try:
        return rag_service_client.get_ingestion_report(ingestion_id)
    except RagServiceError as exc:
        _raise_rag_service_http_error(exc)


@app.get("/api/dashboard/rag/{page}")
def dashboard_rag_page(
    page: str,
    range: str = Query(default="7d", pattern="^(7d|30d)$"),
    source_type: str | None = Query(default=None),
    product: str | None = Query(default=None),
    language: str | None = Query(default=None),
    status: str | None = Query(default=None),
    query_type: str | None = Query(default=None),
    retrieval_strategy: str | None = Query(default=None),
    chunk_strategy: str | None = Query(default=None),
    experiment_id: str | None = Query(default=None),
    sample_id: str | None = Query(default=None),
    request_id: str | None = Query(default=None),
    eval_run_id: str | None = Query(default=None),
    test_case_id: str | None = Query(default=None),
    baseline_experiment_id: str | None = Query(default=None),
    candidate_experiment_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return rag_service_client.rag_dashboard_page(
            page,
            range_value=range,
            filters={
                "source_type": source_type,
                "product": product,
                "language": language,
                "status": status,
                "query_type": query_type,
                "retrieval_strategy": retrieval_strategy,
                "chunk_strategy": chunk_strategy,
                "experiment_id": experiment_id,
                "sample_id": sample_id,
                "request_id": request_id,
                "eval_run_id": eval_run_id,
                "test_case_id": test_case_id,
                "baseline_experiment_id": baseline_experiment_id,
                "candidate_experiment_id": candidate_experiment_id,
                "limit": limit,
                "cursor": cursor,
            },
        )
    except RagServiceError:
        normalized_filters = {
            "source_type": source_type or "all",
            "product": product or "all",
            "language": language or "all",
            "status": status or "all",
            "query_type": query_type or "all",
            "retrieval_strategy": retrieval_strategy or "all",
            "chunk_strategy": chunk_strategy or "all",
            "experiment_id": experiment_id or "all",
            "sample_id": sample_id,
            "request_id": request_id,
            "eval_run_id": eval_run_id,
            "test_case_id": test_case_id,
            "baseline_experiment_id": baseline_experiment_id,
            "candidate_experiment_id": candidate_experiment_id,
            "limit": limit,
            "cursor": cursor,
        }
        if page in {"scorecard", "routing", "retrieval", "generation", "performance", "data-supply", "diagnosis", "review"}:
            return {
                "layout": page,
                "range": range,
                "filters": normalized_filters,
                "sections": {},
                "has_eval_data": False,
                "last_refreshed_at": now_iso(),
            }
        return {
            "range": range,
            "filters": normalized_filters,
            "cards": {},
            "charts": {},
            "tables": {},
            "has_eval_data": False,
            "last_refreshed_at": now_iso(),
        }


@app.get("/api/dashboard/rag/cases/benchmark-detail")
def dashboard_rag_benchmark_case_detail(
    eval_run_id: str = Query(..., min_length=1),
    test_case_id: str = Query(..., min_length=1),
    baseline_eval_run_id: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return rag_service_client.rag_dashboard_benchmark_case_detail(
            eval_run_id,
            test_case_id,
            baseline_eval_run_id=baseline_eval_run_id,
        )
    except RagServiceError as exc:
        _raise_rag_service_http_error(exc)


@app.get("/api/dashboard/rag/cases/live-detail")
def dashboard_rag_live_case_detail(
    request_id: str = Query(..., min_length=1),
) -> dict[str, Any]:
    try:
        return rag_service_client.rag_dashboard_live_case_detail(request_id)
    except RagServiceError as exc:
        _raise_rag_service_http_error(exc)


@app.post("/api/dashboard/rag/review-samples/{sample_id}")
def dashboard_update_review_sample(
    sample_id: str,
    request: ReviewSampleUpdateRequest,
) -> dict[str, Any]:
    try:
        return rag_service_client.update_review_sample(
            sample_id,
            review_status=request.review_status,
            retrieval_ok=request.retrieval_ok,
            answer_ok=request.answer_ok,
            citation_ok=request.citation_ok,
            logic_ok=request.logic_ok,
            hallucination_present=request.hallucination_present,
            route_family_override=request.route_family_override,
            execution_action_override=request.execution_action_override,
            tooling_profile_override=request.tooling_profile_override,
            failure_stage_override=request.failure_stage_override,
            failure_bucket_override=request.failure_bucket_override,
            dataset_decision=request.dataset_decision,
            corrected_reference_answer=request.corrected_reference_answer,
            corrected_citation_targets=request.corrected_citation_targets,
            note=request.note,
        )
    except RagServiceError as exc:
        _raise_rag_service_http_error(exc)


@app.post("/api/dashboard/rag/datasets/generation-runs")
def dashboard_create_dataset_generation_run(
    request: DatasetGenerationRunRequest,
) -> dict[str, Any]:
    try:
        return rag_service_client.create_dataset_generation_run(
            dataset_name=request.dataset_name,
            source_types=request.source_types,
            question_language=request.question_language,
        )
    except RagServiceError as exc:
        _raise_rag_service_http_error(exc)


@app.post("/api/dashboard/rag/benchmarks/local-sync")
def dashboard_sync_local_benchmarks() -> dict[str, Any]:
    try:
        return rag_service_client.sync_local_benchmarks()
    except RagServiceError as exc:
        _raise_rag_service_http_error(exc)


@app.post("/api/dashboard/rag/benchmarks/sessions/local-run", status_code=202)
def dashboard_create_local_benchmark_session_run(
    request: BenchmarkSessionRunRequest,
) -> dict[str, Any]:
    try:
        return rag_service_client.create_local_benchmark_session_run(
            session_name=request.session_name,
            top_k=request.top_k,
        )
    except RagServiceError as exc:
        _raise_rag_service_http_error(exc)


@app.post("/api/dashboard/rag/datasets/{dataset_id}/benchmark-runs")
def dashboard_create_dataset_benchmark_run(
    dataset_id: str,
    request: DatasetBenchmarkRunRequest,
) -> dict[str, Any]:
    try:
        return rag_service_client.create_dataset_benchmark_run(
            dataset_id,
            experiment_id=request.experiment_id,
            top_k=request.top_k,
            tier=request.tier,
        )
    except RagServiceError as exc:
        _raise_rag_service_http_error(exc)


@app.get("/api/dashboard/rag/datasets/{dataset_id}/export", response_class=PlainTextResponse)
def dashboard_export_dataset_snapshot(
    dataset_id: str,
    tier: str = Query(default="gold", pattern="^(gold|silver)$"),
) -> PlainTextResponse:
    try:
        body = rag_service_client.export_dataset_snapshot(dataset_id, tier=tier)
    except RagServiceError as exc:
        _raise_rag_service_http_error(exc)
    return PlainTextResponse(content=body, media_type="application/x-ndjson")


@app.get("/api/dashboard/events")
def dashboard_events(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    rows = ticket_repository.list_events(limit=limit)
    return {"events": normalize_ticket_dashboard_events(rows)}


@app.websocket("/ws/client")
async def client_ws(websocket: WebSocket) -> None:
    await hub.connect("client", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect("client", websocket)


@app.websocket("/ws/engineer")
async def engineer_ws(websocket: WebSocket) -> None:
    await hub.connect("engineer", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect("engineer", websocket)


@app.websocket("/ws/workspace")
async def workspace_ws(
    websocket: WebSocket,
    access_token: str = Query(default=""),
) -> None:
    principal = verify_workspace_access_token(access_token)
    account = (
        ticket_repository.get_workspace_account(principal.account_id)
        if principal is not None
        else None
    )
    if (
        principal is None
        or not isinstance(account, dict)
        or not bool(account.get("active", True))
        or str(account.get("role") or "").strip().lower() != principal.role
    ):
        await websocket.close(code=1008, reason="Workspace authentication required")
        return
    await hub.connect("engineer", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect("engineer", websocket)


@app.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket) -> None:
    await hub.connect("dashboard", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect("dashboard", websocket)


def _require_workspace_case_access(
    engineer_case_id: str,
    principal: WorkspacePrincipal,
) -> dict[str, Any]:
    engineer_case = _resolve_engineer_case_payload(engineer_case_id)
    if engineer_case is None:
        raise HTTPException(status_code=404, detail="Engineer Case not found")
    if principal.role == "admin":
        return engineer_case
    if (
        str(engineer_case.get("assignment_status") or "pending") != "assigned"
        or str(engineer_case.get("assigned_engineer_id") or "").strip()
        != principal.account_id
    ):
        raise HTTPException(status_code=403, detail="Engineer Case is not assigned to this account")
    return engineer_case


@app.get("/api/workspace/cases/{engineer_case_id}/feedback")
def get_workspace_case_feedback(
    engineer_case_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    principal: WorkspacePrincipal = Depends(require_workspace_principal),
) -> dict[str, Any]:
    _require_workspace_case_access(engineer_case_id, principal)
    return list_engineer_hitl_feedback(engineer_case_id, limit=limit)


@app.post("/api/workspace/cases/{engineer_case_id}/investigation/messages")
async def post_workspace_case_message(
    engineer_case_id: str,
    request: InvestigationMessageRequest,
    principal: WorkspacePrincipal = Depends(require_workspace_principal),
) -> dict[str, Any]:
    _require_workspace_case_access(engineer_case_id, principal)
    secured_request = request.model_copy(
        update={"engineer_id": principal.account_id, "multi_agent_enabled": False}
    )
    return await post_investigation_message(engineer_case_id, secured_request)


@app.post("/api/workspace/cases/{engineer_case_id}/investigation/confirmation")
async def confirm_workspace_case_reply(
    engineer_case_id: str,
    request: InvestigationConfirmationRequest,
    principal: WorkspacePrincipal = Depends(require_workspace_principal),
) -> dict[str, Any]:
    _require_workspace_case_access(engineer_case_id, principal)
    secured_request = request.model_copy(update={"engineer_id": principal.account_id})
    return await confirm_investigation_reply(engineer_case_id, secured_request)


@app.post("/api/workspace/cases/{engineer_case_id}/action")
async def update_workspace_case_action(
    engineer_case_id: str,
    request: TicketActionRequest,
    principal: WorkspacePrincipal = Depends(require_workspace_principal),
) -> dict[str, Any]:
    engineer_case = _require_workspace_case_access(engineer_case_id, principal)
    client_ticket_id = str(
        engineer_case.get("client_ticket_id")
        or (engineer_case.get("client_ticket_ref") or {}).get("ticket_id")
        or ""
    ).strip()
    if not client_ticket_id:
        raise HTTPException(status_code=400, detail="Engineer Case has no Client Ticket")
    secured_request = request.model_copy(update={"engineer_id": principal.account_id})
    return await update_ticket(client_ticket_id, secured_request)
