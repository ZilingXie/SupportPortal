from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import logging
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import psycopg

from backend.repositories.ticket_repository import (
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
from backend.services.billing_response_flow import generate_billing_response_token, hash_billing_response_token
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
UI_DIR = BASE_DIR / "ui"
CLIENT_DIR = UI_DIR / "client-ui"
ACCOUNT_DIR = UI_DIR / "account-ui"
ENGINEER_DIR = UI_DIR / "engineer-ui"
ASSIGNMENT_DIR = UI_DIR / "assignment-ui"
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
KNOWLEDGE_OFFICIAL_MAX_BYTES = _safe_int_env("KNOWLEDGE_OFFICIAL_MAX_BYTES", 5 * 1024 * 1024)
KNOWLEDGE_ARTICLE_MAX_CHARS = _safe_int_env("KNOWLEDGE_ARTICLE_MAX_CHARS", 120000)
CLIENT_ACK_MAX_OUTPUT_TOKENS = _safe_int_env("CLIENT_ACK_MAX_OUTPUT_TOKENS", 32)
BILLING_RESPONSE_PUBLIC_BASE_URL_ENV = "BILLING_RESPONSE_PUBLIC_BASE_URL"
DEFAULT_BILLING_RESPONSE_PUBLIC_BASE_URL = "https://support.stellarix.space"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_billing_response_link(raw_token: str) -> str:
    base_url = (
        os.getenv(BILLING_RESPONSE_PUBLIC_BASE_URL_ENV) or DEFAULT_BILLING_RESPONSE_PUBLIC_BASE_URL
    ).strip().rstrip("/")
    return f"{base_url}/response?token={urllib.parse.quote(raw_token, safe='')}"


def _redact_billing_response_token(email_payload: dict[str, Any], raw_token: str) -> dict[str, Any]:
    redacted = dict(email_payload)
    redacted["body"] = str(redacted.get("body") or "").replace(raw_token, "<redacted>")
    return redacted


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
    external_id: str | None = Field(default=None, max_length=160)
    source: str | dict[str, Any] | None = Field(default=None)
    created_by: str | None = Field(default=None, max_length=160)


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

if CLIENT_DIR.exists():
    app.mount("/client", StaticFiles(directory=CLIENT_DIR, html=True), name="client-ui")
if ACCOUNT_DIR.exists():
    app.mount("/account", StaticFiles(directory=ACCOUNT_DIR, html=True), name="account-ui")
if ENGINEER_DIR.exists():
    app.mount("/engineer", StaticFiles(directory=ENGINEER_DIR, html=True), name="engineer-ui")
if ASSIGNMENT_DIR.exists():
    app.mount("/assignment", StaticFiles(directory=ASSIGNMENT_DIR, html=True), name="assignment-ui")
if DASHBOARD_DIR.exists():
    app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard-ui")
if SHARED_UI_DIR.exists():
    app.mount("/shared-ui", StaticFiles(directory=SHARED_UI_DIR), name="shared-ui")


ticket_repository: TicketRepository = create_ticket_repository()
asset_repository: AssetRepository = create_asset_repository()
asset_storage = create_asset_storage()
hub = ConnectionHub()
event_bus = AsyncRedisEventBus()
task_queue = AsyncRedisTaskQueue()
rag_service_client = RagServiceClient()


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
    return resolve_support_route_message(
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
            _initialize_asset_repository_with_fallback()
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
    LOGGER.warning("Falling back to in-memory ticket repository for this process.")
    _initialize_asset_repository_with_fallback()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await event_bus.close()
    await task_queue.close()
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


def _build_account_ticket_view_model(ticket: dict[str, Any]) -> dict[str, Any]:
    canonical_ticket_id = (
        str(ticket.get("client_ticket_id") or "").strip()
        or str(ticket.get("ticket_id") or "").strip()
    )
    billing_ticket_id = str(ticket.get("billing_ticket_id") or "").strip() or None
    status = str(ticket.get("status") or ticket.get("automation_status") or "").strip() or "not_automated"

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
        "billing_ticket_id": billing_ticket_id,
        "source": source_display,
        "status": status,
        "automation_status": status,
    }


@app.post("/account")
async def create_account_intake(request: AccountIntakeRequest) -> dict[str, Any]:
    title = " ".join(str(request.title or "").split()).strip()
    question = str(request.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    if not title:
        title = derive_ticket_title(question)

    ticket_id = str(request.ticket_id or "").strip() or f"TK-ACC-{uuid4().hex[:6].upper()}"
    billing_ticket_id = f"BT-{ticket_id}"
    existing_ticket = await async_to_thread(ticket_repository.get_ticket, ticket_id)
    if existing_ticket is not None:
        raise HTTPException(status_code=409, detail="ticket_id already exists")

    account_source = _normalize_account_source(request.source)
    customer_id = str(request.customer_email or "").strip() or "account-intake"
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
    decision = decide_support_route(
        route_input,
        ticket_subject=title,
        ticket_context=ticket_context,
        semantic_first=True,
    )
    route = str(decision.execution_action or decision.route or "").strip()
    route_family = str(decision.route_family or "").strip()
    is_billing_automation_route = (
        route_family == "billing_automation"
        and route in {"detailed_invoice", "account_suspension", "account_verification"}
    )
    is_billing_route = is_billing_automation_route or (
        route_family == "billing_review"
        and route == "human_review_required"
    )

    resolution: SupportResolution | None = None
    response_status = "not_automated"
    customer_reply = ""
    missing_fields: list[str] = []
    collected_fields: dict[str, Any] = {}
    internal_email_payload: dict[str, Any] | None = None
    internal_email_to_send: dict[str, Any] | None = None
    billing_response_token_record: dict[str, Any] | None = None
    internal_email_send_status = "not_applicable"
    internal_email_send_reason = ""

    if is_billing_automation_route:
        response_status = "automation"
        billing_result = build_billing_automation_result(
            action=route,
            message=question,
            ticket_id=ticket_id,
            customer_email=str(request.customer_email or "").strip() or None,
            billing_ticket_id=billing_ticket_id,
        )
        customer_reply = billing_result.customer_reply
        missing_fields = list(billing_result.missing_fields)
        collected_fields = dict(billing_result.collected_fields)
        if billing_result.internal_email:
            billing_response_raw_token = generate_billing_response_token()
            billing_response_link = _build_billing_response_link(billing_response_raw_token)
            billing_result = build_billing_automation_result(
                action=route,
                message=question,
                ticket_id=ticket_id,
                customer_email=str(request.customer_email or "").strip() or None,
                billing_ticket_id=billing_ticket_id,
                response_link=billing_response_link,
            )
            customer_reply = billing_result.customer_reply
            missing_fields = list(billing_result.missing_fields)
            collected_fields = dict(billing_result.collected_fields)
            internal_email_to_send = dict(billing_result.internal_email)
            internal_email_payload = _redact_billing_response_token(
                internal_email_to_send,
                billing_response_raw_token,
            )
            billing_response_token_record = {
                "token_hash": hash_billing_response_token(billing_response_raw_token),
                "billing_ticket_id": billing_ticket_id,
                "created_at": now_iso(),
                "used_at": None,
            }
            internal_email_send_status = "pending"
        else:
            internal_email_send_status = "not_ready"
            internal_email_send_reason = "missing_required_fields"
        if customer_reply:
            ticket["messages"].append(
                {
                    "role": "assistant",
                    "content": customer_reply,
                    "created_at": timestamp,
                    "content_format": "plaintext",
                    "source": "billing_automation",
                }
            )

    await async_to_thread(ticket_repository.save_ticket, ticket, new_messages=ticket.get("messages", []))

    billing_ticket: dict[str, Any] = {
        "billing_ticket_id": billing_ticket_id,
        "client_ticket_id": ticket_id,
        "source": _serialize_billing_ticket_source(request.source, account_source),
        "external_id": str(request.external_id).strip() or None,
        "created_by": str(request.created_by).strip() or None,
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
        "customer_reply": customer_reply or None,
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
    }
    await async_to_thread(ticket_repository.save_billing_ticket, billing_ticket)

    if billing_response_token_record and internal_email_to_send:
        await async_to_thread(ticket_repository.save_billing_response_token, billing_response_token_record)
        email_send_result = await async_to_thread(send_billing_internal_email, internal_email_to_send)
        internal_email_send_status = str(email_send_result.get("status") or "failed")
        internal_email_send_reason = str(email_send_result.get("reason") or "")
        if internal_email_send_status != "sent":
            await async_to_thread(
                ticket_repository.mark_billing_response_token_used,
                billing_response_token_record["token_hash"],
                now_iso(),
            )
        billing_ticket["internal_email_send_status"] = internal_email_send_status
        billing_ticket["internal_email_send_reason"] = internal_email_send_reason
        billing_ticket["updated_at"] = now_iso()
        await async_to_thread(ticket_repository.save_billing_ticket, billing_ticket)

    event = {
        "event": "ticket_created",
        "ticket_id": ticket_id,
        "status": ticket["status"],
        "source": account_source,
        "message": question[:200],
        "created_at": now_iso(),
        "answer_route": resolution.answer_route if resolution is not None else None,
        "scope_label": decision.scope_label,
        "route_family": decision.route_family,
        "execution_action": route if is_billing_route else None,
        "tooling_profile": resolution.tooling_profile if resolution is not None else None,
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
        # Router audit fields
        "intent_router_attempted": decision.intent_router_attempted,
        "intent_router_confidence_threshold": decision.intent_router_confidence_threshold,
        "intent_router_model_confidence": decision.intent_router_model_confidence,
        "intent_router_fallback_reason": decision.intent_router_fallback_reason,
        "intent_router_failure_type": decision.intent_router_failure_type,
        "intent_router_failure_source": decision.intent_router_failure_source,
    }
    await async_to_thread(ticket_repository.record_event, ticket_id, event["event"], event)
    await dispatch_event(["engineer", "dashboard"], event)
    await dispatch_event(["client"], build_client_sync_event(ticket, event["event"], question[:200]))

    return {
        "status": response_status,
        "route": route or None,
        "ticket_id": ticket_id,
        "billing_ticket_id": billing_ticket_id,
        "customer_reply": customer_reply,
        "missing_fields": missing_fields,
        "collected_fields": collected_fields,
        "internal_email_send_status": internal_email_send_status,
        "internal_email_send_reason": internal_email_send_reason,
        # Semantic routing fields.
        "semantic_intent": decision.semantic_intent or None,
        "route_family": decision.route_family,
        "automation_eligibility": decision.automation_eligibility or None,
        "policy_decision": decision.policy_decision or None,
        "not_automated_reason": decision.not_automated_reason or None,
        "risk_flags": list(decision.risk_flags),
        "evidence_spans": list(decision.evidence_spans),
        "router_source": decision.router_source,
        # Router audit fields
        "intent_router_attempted": decision.intent_router_attempted,
        "intent_router_confidence_threshold": decision.intent_router_confidence_threshold,
        "intent_router_model_confidence": decision.intent_router_model_confidence,
        "intent_router_fallback_reason": decision.intent_router_fallback_reason,
        "intent_router_failure_type": decision.intent_router_failure_type,
        "intent_router_failure_source": decision.intent_router_failure_source,
    }


@app.get("/api/account/billing-tickets")
def list_billing_tickets(limit: int = 30) -> dict[str, Any]:
    safe_limit = max(1, min(limit, 100))
    tickets = ticket_repository.list_billing_tickets(limit=safe_limit)
    items = [
        {
            **_build_account_ticket_view_model(item),
            "billing_ticket_id": item.get("billing_ticket_id"),
            "client_ticket_id": item.get("client_ticket_id"),
        }
        for item in tickets
    ]
    return {"tickets": items, "billing_tickets": items, "count": len(items)}


@app.delete("/api/account/billing-tickets")
def delete_all_billing_tickets() -> dict[str, Any]:
    deleted = ticket_repository.delete_all_billing_tickets()
    return {"deleted": deleted}


@app.get("/api/account/billing-tickets/{billing_ticket_id}")
def get_billing_ticket(billing_ticket_id: str) -> dict[str, Any]:
    ticket = ticket_repository.get_billing_ticket(billing_ticket_id)
    if ticket is None and not str(billing_ticket_id).startswith("BT-"):
        ticket = ticket_repository.get_billing_ticket_by_client_ticket_id(billing_ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    view_model = _build_account_ticket_view_model(ticket)
    canonical_ticket_id = view_model.get("ticket_id")
    canonical_ticket = ticket_repository.get_ticket(canonical_ticket_id) if canonical_ticket_id else None
    if canonical_ticket:
        view_model["messages"] = canonical_ticket.get("messages", [])
        view_model["customer_id"] = canonical_ticket.get("customer_id")
        view_model["requester"] = canonical_ticket.get("requester")
        view_model["support_ticket_status"] = canonical_ticket.get("status")
    else:
        view_model["messages"] = []
        view_model["customer_id"] = ticket.get("client_ticket_id") or ""
        view_model["requester"] = ticket.get("client_ticket_id") or ""
        view_model["support_ticket_status"] = ""
    return {
        **ticket,
        **view_model,
    }


class BillingReplyRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)


@app.post("/api/account/billing-tickets/{billing_ticket_id}/reply")
async def reply_to_billing_ticket(
    billing_ticket_id: str,
    request: BillingReplyRequest,
) -> dict[str, Any]:
    billing_ticket = await async_to_thread(ticket_repository.get_billing_ticket, billing_ticket_id)
    if billing_ticket is None:
        raise HTTPException(status_code=404, detail="billing ticket not found")

    client_ticket_id = str(billing_ticket.get("client_ticket_id") or "").strip()
    if not client_ticket_id:
        raise HTTPException(status_code=400, detail="billing ticket has no linked support ticket")

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

    automation_status = str(
        billing_ticket.get("automation_status") or billing_ticket.get("status") or ""
    ).strip()

    if automation_status in {"automation", "automated"}:
        route = str(billing_ticket.get("route") or "").strip()
        # Build conversation text from all messages for field extraction.
        all_contents = [
            str(msg.get("content") or "")
            for msg in canonical_ticket.get("messages", [])
            if isinstance(msg, dict)
        ]
        conversation_text = "\n".join(all_contents)

        billing_result = build_billing_automation_result(
            action=route,
            message=conversation_text,
            ticket_id=client_ticket_id,
            customer_email=str(canonical_ticket.get("customer_id") or "").strip() or None,
        )

        assistant_reply = billing_result.customer_reply
        missing_fields = list(billing_result.missing_fields)
        collected_fields = dict(billing_result.collected_fields)

        # Update billing ticket with recomputed fields.
        billing_ticket["missing_fields"] = missing_fields
        billing_ticket["collected_fields"] = collected_fields
        billing_ticket["customer_reply"] = assistant_reply
        billing_ticket["internal_email_send_status"] = "not_sending"
        billing_ticket["internal_email_send_reason"] = "demo_mode"
        billing_ticket["updated_at"] = timestamp

        if assistant_reply:
            canonical_ticket["messages"].append(
                {
                    "role": "assistant",
                    "content": assistant_reply,
                    "created_at": timestamp,
                    "content_format": "plaintext",
                    "source": "billing_automation",
                }
            )

        new_messages = canonical_ticket.get("messages", [])[initial_message_count:]
        await async_to_thread(ticket_repository.save_ticket, canonical_ticket, new_messages=new_messages)
        await async_to_thread(ticket_repository.save_billing_ticket, billing_ticket)
    else:
        # Non-automated: just save the customer message (reply handled by /api/tickets/query on frontend).
        new_messages = canonical_ticket.get("messages", [])[initial_message_count:]
        await async_to_thread(ticket_repository.save_ticket, canonical_ticket, new_messages=new_messages)

    # Return refreshed detail view.
    view_model = _build_account_ticket_view_model(billing_ticket)
    view_model["messages"] = canonical_ticket.get("messages", [])
    view_model["customer_id"] = canonical_ticket.get("customer_id")
    view_model["requester"] = canonical_ticket.get("requester")
    view_model["support_ticket_status"] = canonical_ticket.get("status")
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
                now_iso_value = now_iso()
                engineer_plan = build_engineer_plan(
                    summary_packet=summary_packet,
                    mem0_context=None,
                    skill_inventory=None,
                    revise_context=None,
                    now_value=now_iso_value,
                )
                execution_packet = execute_engineer_plan(
                    active_plan=engineer_plan,
                    summary_packet=summary_packet,
                    engineer_agent_state=engineer_case.get("engineer_agent_state"),
                    execution_context=None,
                    now_value=now_iso_value,
                )
                merged_agent_state = {
                    **(engineer_case.get("engineer_agent_state") or {}),
                    "summary_packet_id": summary_packet["packet_id"],
                    "summary_agent_version": summary_packet["summary_agent_version"],
                    "summary_packet_version": summary_packet["packet_version"],
                    "issue_understanding": summary_packet["engineer_ticket_input"]["opening_summary"],
                    "missing_information": list(summary_packet.get("missing_information") or []),
                    "next_request_for_engineer": summary_packet["engineer_ticket_input"]["requested_action"],
                    "active_plan": engineer_plan,
                    "plan_id": engineer_plan["plan_id"],
                    "plan_version": engineer_plan["plan_version"],
                    "plan_agent_version": engineer_plan["plan_agent_version"],
                    "active_execution": execution_packet,
                    "execution_id": execution_packet["execution_id"],
                    "execution_version": execution_packet["execution_version"],
                    "execute_agent_version": execution_packet["execute_agent_version"],
                    "evidence_packet": execution_packet["evidence_packet"],
                    "task_results": execution_packet["task_results"],
                }
                review_packet = review_execution(
                    active_execution=execution_packet,
                    engineer_agent_state=merged_agent_state,
                    handoff_packet=summary_packet,
                    ticket=ticket,
                    now_value=now_iso_value,
                )
                merged_agent_state.update({
                    "active_review": review_packet,
                    "review_id": review_packet["review_id"],
                    "review_version": review_packet["review_version"],
                    "review_agent_version": review_packet["review_agent_version"],
                    "review_decision": review_packet["review_decision"],
                    "replan_count": review_packet["replan_count"],
                })
                engineer_case["engineer_agent_state"] = merged_agent_state
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
    result = append_engineer_investigation_message(
        case_context,
        engineer_message=request.message.strip(),
        now_value=timestamp,
        ai_turn_builder=generate_investigation_ai_turn,
    )
    engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
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


def _build_revise_context_for_engineer_replan(
    engineer_case: dict[str, Any],
    *,
    note: str,
    engineer_id: str,
    created_at: str,
) -> dict[str, Any]:
    """Collect current multi-agent state for deterministic replan.

    Reads from engineer_agent_state: active_plan, active_execution,
    active_review, evidence_packet, task_results, and current replan_count.
    Returns a revise_context dict suitable for build_engineer_plan().
    """
    agent_state = (
        engineer_case.get("engineer_agent_state")
        if isinstance(engineer_case.get("engineer_agent_state"), dict)
        else {}
    )

    active_plan = agent_state.get("active_plan") if isinstance(agent_state.get("active_plan"), dict) else {}
    active_execution = agent_state.get("active_execution") if isinstance(agent_state.get("active_execution"), dict) else {}
    active_review = agent_state.get("active_review") if isinstance(agent_state.get("active_review"), dict) else {}
    evidence_packet = agent_state.get("evidence_packet") if isinstance(agent_state.get("evidence_packet"), dict) else {}
    task_results = agent_state.get("task_results") if isinstance(agent_state.get("task_results"), list) else []

    current_replan_count = 0
    rc = agent_state.get("replan_count")
    if isinstance(rc, int):
        current_replan_count = rc
    elif isinstance(rc, str) and rc.isdigit():
        current_replan_count = int(rc)

    new_replan_count = current_replan_count + 1
    evidence_gaps = (
        active_review.get("evidence_gaps")
        if isinstance(active_review.get("evidence_gaps"), list)
        else []
    )

    return {
        "revise_note": note,
        "previous_plan_id": str(active_plan.get("plan_id") or "").strip(),
        "previous_execution_id": str(active_execution.get("execution_id") or "").strip(),
        "previous_review_id": str(active_review.get("review_id") or "").strip(),
        "previous_review_decision": str(active_review.get("review_decision") or "").strip(),
        "review_problem_statement": str(active_review.get("problem_statement") or "").strip(),
        "review_evidence_gaps": [copy.deepcopy(item) for item in evidence_gaps],
        "previous_evidence_packet": copy.deepcopy(evidence_packet),
        "previous_task_results": [copy.deepcopy(item) for item in task_results],
        "engineer_feedback": {
            "note": note,
            "engineer_id": engineer_id,
            "created_at": created_at,
        },
        "replan_count": new_replan_count,
    }


def _run_engineer_multi_agent_round(
    engineer_case: dict[str, Any],
    *,
    revise_context: dict[str, Any] | None,
    now_value: str,
) -> dict[str, Any]:
    """Run one Plan → Execute → Review cycle and merge results into agent state.

    Returns the updated engineer_case dict (mutated in place).
    Does NOT save to repository.
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
    review_agent_state = dict(agent_state)
    if isinstance(revise_context, dict) and isinstance(revise_context.get("replan_count"), int):
        review_agent_state["replan_count"] = revise_context["replan_count"]
    active_review = review_execution(
        active_execution=active_execution,
        engineer_agent_state=review_agent_state,
        handoff_packet=handoff_packet,
        ticket=engineer_case,
        now_value=now_value,
    )

    # 4. Merge back into agent_state
    new_replan_count = active_review.get("replan_count", 0)
    if isinstance(revise_context, dict):
        rc = revise_context.get("replan_count")
        if isinstance(rc, int):
            new_replan_count = rc

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
        "replan_count": new_replan_count,
        "last_revise_context": revise_context,
    })

    # Append to replan_history
    replan_history = list(agent_state.get("replan_history") or [])
    replan_history.append({
        "plan_id": active_plan.get("plan_id"),
        "execution_id": active_execution.get("execution_id"),
        "review_id": active_review.get("review_id"),
        "review_decision": active_review.get("review_decision"),
        "replan_count": new_replan_count,
        "created_at": now_value,
    })
    merged_state["replan_history"] = replan_history

    engineer_case["engineer_agent_state"] = merged_state
    return engineer_case


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
        # ---- Multi-agent replan path ----
        agent_state = (
            engineer_case.get("engineer_agent_state")
            if isinstance(engineer_case.get("engineer_agent_state"), dict)
            else {}
        )
        current_replan_count = 0
        rc = agent_state.get("replan_count")
        if isinstance(rc, int):
            current_replan_count = rc
        elif isinstance(rc, str) and rc.isdigit():
            current_replan_count = int(rc)
        max_replan = 2
        mrc = agent_state.get("max_replan_count")
        if isinstance(mrc, int):
            max_replan = mrc
        elif isinstance(mrc, str) and mrc.isdigit():
            max_replan = int(mrc)

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
            active_investigation["updated_at"] = timestamp
            new_internal_messages.append(revision_message)

        if current_replan_count >= max_replan:
            # Replan limit reached — add internal message, keep investigating
            limit_message = build_internal_message(
                str((case_context.get("active_investigation") or {}).get("id") or ""),
                "engineer_ai",
                (
                    f"Replan limit reached ({current_replan_count}/{max_replan}). "
                    "Automatic investigation cannot continue — manual engineer review is required."
                ),
                timestamp,
                sequence=len((case_context.get("active_investigation") or {}).get("messages", [])) + 1,
            )
            active_investigation = case_context.get("active_investigation")
            if isinstance(active_investigation, dict):
                active_investigation.setdefault("messages", []).append(limit_message)
                active_investigation["state"] = "active"
                active_investigation["updated_at"] = timestamp
            new_internal_messages.append(limit_message)
        else:
            # Build revise_context and run multi-agent round
            revise_context = _build_revise_context_for_engineer_replan(
                engineer_case,
                note=str(request.note or "").strip(),
                engineer_id=request.engineer_id,
                created_at=timestamp,
            )
            engineer_case = _run_engineer_multi_agent_round(
                engineer_case,
                revise_context=revise_context,
                now_value=timestamp,
            )
            # Add internal message with new review decision
            new_agent_state = engineer_case.get("engineer_agent_state") or {}
            new_review = new_agent_state.get("active_review") or {}
            review_decision = str(new_review.get("review_decision") or "unknown")
            decision_message = build_internal_message(
                str((case_context.get("active_investigation") or {}).get("id") or ""),
                "engineer_ai",
                (
                    f"Replan complete (round {current_replan_count + 1}/{max_replan}). "
                    f"New review decision: {review_decision}. "
                    f"Plan ID: {new_agent_state.get('plan_id', 'unknown')}. "
                    "Please review the updated investigation results."
                ),
                timestamp,
                sequence=len((case_context.get("active_investigation") or {}).get("messages", [])) + 1,
            )
            active_investigation = case_context.get("active_investigation")
            if isinstance(active_investigation, dict):
                active_investigation.setdefault("messages", []).append(decision_message)
                previous_state = str(active_investigation.get("state") or "").strip().lower()
                if (
                    previous_state == INVESTIGATION_STATE_AWAITING_FINAL_APPROVAL
                    or isinstance(new_agent_state.get("active_guardrail_final"), dict)
                ):
                    active_investigation["state"] = "active"
                    active_investigation["final_confirmation_requested_at"] = None
                active_investigation["updated_at"] = timestamp
            new_internal_messages.append(decision_message)

        # Sync agent_state back to case_context
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


@app.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket) -> None:
    await hub.connect("dashboard", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect("dashboard", websocket)
