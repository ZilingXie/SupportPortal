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
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import psycopg

from backend.repositories.ticket_repository import (
    InMemoryTicketRepository,
    TicketRepository,
    create_ticket_repository,
)
from backend.services.embedding_provider import (
    DEFAULT_PGVECTOR_TABLE,
    embedding_model_id,
    embedding_provider_name,
)
from backend.services.emotion_reply import build_initial_ack
from backend.services.engineer_agent import build_engineer_agent_brief
from backend.services.engineer_cases import (
    apply_case_context_to_engineer_case,
    build_engineer_case_context,
    build_new_engineer_case,
    derive_engineer_case_title,
)
from backend.services.investigation_flow import (
    COMMUNICATING_STATUS,
    ESCALATED_STATUS,
    INVESTIGATING_STATUS,
    OPEN_STATUS,
    RESOLVED_STATUS,
    append_engineer_investigation_message,
    apply_investigation_confirmation,
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
from backend.services.rag_qa import INSUFFICIENT_EVIDENCE_REPLY
from backend.services.rag_sufficiency_judge import judge_rag_answer_sufficiency
from backend.services.rag_service_client import (
    RagTicketAnswerDetail,
    RagServiceClient,
    RagServiceError,
    async_to_thread,
    classify_rag_service_failure_kind,
    with_rag_detail_diagnostics,
)
from backend.services.support_router import (
    SupportResolution,
    SupportRouteDecision,
    decide_support_route,
    resolve_support_message as resolve_support_route_message,
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
ENGINEER_DIR = UI_DIR / "engineer-ui"
DASHBOARD_DIR = UI_DIR / "dashboard-ui"

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
OPTIMISTIC_PARALLEL_ROUTE_ENABLED = _env_flag("OPTIMISTIC_PARALLEL_ROUTE_ENABLED", default=True)
KNOWLEDGE_OFFICIAL_MAX_BYTES = _safe_int_env("KNOWLEDGE_OFFICIAL_MAX_BYTES", 5 * 1024 * 1024)
KNOWLEDGE_ARTICLE_MAX_CHARS = _safe_int_env("KNOWLEDGE_ARTICLE_MAX_CHARS", 120000)
CLIENT_ACK_MAX_OUTPUT_TOKENS = _safe_int_env("CLIENT_ACK_MAX_OUTPUT_TOKENS", 32)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _main_agent_async_enabled() -> bool:
    return bool(ASYNC_QUERY_ENABLED and OPTIMISTIC_PARALLEL_ROUTE_ENABLED)


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
    message: str = Field(min_length=1)


class ClientAckRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    ticket_id: str | None = Field(default=None, max_length=128)
    customer_id: str | None = Field(default=None, max_length=128)


class TicketActionRequest(BaseModel):
    action: str = Field(pattern="^(processing|resolved|investigate|reopen)$")
    engineer_id: str = Field(default="eng")
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
    engineer_id: str = Field(default="eng")
    message: str = Field(min_length=1, max_length=4000)


class InvestigationConfirmationRequest(BaseModel):
    engineer_id: str = Field(default="eng")
    decision: str = Field(pattern="^(approve|revise)$")
    note: str | None = Field(default=None, max_length=4000)


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
if ENGINEER_DIR.exists():
    app.mount("/engineer", StaticFiles(directory=ENGINEER_DIR, html=True), name="engineer-ui")
if DASHBOARD_DIR.exists():
    app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard-ui")


ticket_repository: TicketRepository = create_ticket_repository()
hub = ConnectionHub()
event_bus = AsyncRedisEventBus()
task_queue = AsyncRedisTaskQueue()
rag_service_client = RagServiceClient()


def derive_subject(message: str) -> str:
    return derive_ticket_title(message)


def latest_customer_message(ticket: dict[str, Any]) -> str:
    messages = ticket.get("messages", [])
    for message in reversed(messages):
        if message.get("role") == "customer":
            return str(message.get("content", "")).strip()
    return ""


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
    route_decision: SupportRouteDecision,
    resolution: SupportResolution,
    rag_result: dict[str, Any] | None,
) -> Any:
    if mode in {"rag_insufficient_evidence", "pre_engineer_intake"}:
        return evaluate_troubleshooting_intake(
            message=message,
            product=product,
            ticket_subject=ticket_subject,
            ticket_context=ticket_context,
            current_state=current_state,
            rag_result=rag_result,
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


def _managed_followup_fallback(solution: str) -> str:
    clean_solution = solution.strip()
    return (
        "Thanks for waiting. I reviewed this with an engineer.\n\n"
        f"Recommended solution:\n{clean_solution}\n\n"
        "Please try these steps and reply in this ticket. I will continue to follow up until this is resolved."
    )


def build_ai_followup(ticket: dict[str, Any], solution: str) -> str:
    fallback = _managed_followup_fallback(solution)
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
            return answer
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


def _build_rag_answer_detail(
    message: str,
    *,
    ticket_id: str | None = None,
    customer_id: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    product: str | None = None,
) -> RagTicketAnswerDetail:
    def _rag_failure_reason(
        error: RagServiceError,
        *,
        timeout_health_status: str | None = None,
    ) -> str:
        failure_kind = classify_rag_service_failure_kind(error)
        if failure_kind == "timeout":
            return "rag_processing_timeout" if str(timeout_health_status or "").strip().lower() == "ok" else "rag_unavailable"
        if failure_kind == "transport":
            return "rag_unavailable"
        if failure_kind == "http":
            return "rag_service_error"
        if error.status_code is not None:
            return "rag_service_error"
        normalized_message = str(error).strip().lower()
        if (
            "not configured" in normalized_message
            or "request failed" in normalized_message
        ):
            return "rag_unavailable"
        return "rag_service_error"

    request_id = f"rag-{uuid4().hex[:12]}"
    try:
        answer_detail = rag_service_client.query_answer_with_recovery_detail(
            question=message,
            request_id=request_id,
            ticket_id=ticket_id,
            customer_id=customer_id,
            ticket_context=ticket_context,
            product=product,
            insufficient_reply=INSUFFICIENT_EVIDENCE_REPLY,
        )
    except RagServiceError as exc:
        failure_kind = classify_rag_service_failure_kind(exc)
        timeout_health_status: str | None = None
        if failure_kind == "timeout":
            try:
                health_payload = rag_service_client.health(timeout_seconds=2.0)
                timeout_health_status = str((health_payload or {}).get("status") or "").strip().lower() or "unknown"
            except RagServiceError:
                timeout_health_status = "unreachable"
        failure_reason = _rag_failure_reason(exc, timeout_health_status=timeout_health_status)
        LOGGER.warning(
            "RAG service call failed request_id=%s ticket_id=%s reason=%s failure_kind=%s status_code=%s error=%s",
            request_id,
            ticket_id,
            failure_reason,
            failure_kind,
            exc.status_code,
            exc,
        )
        return with_rag_detail_diagnostics(
            RagTicketAnswerDetail(
                answer=INSUFFICIENT_EVIDENCE_REPLY,
                confidence=0.0,
                sources=[],
                citations=[],
                needs_engineer_guidance=True,
                reason=failure_reason,
                evidence_summary=None,
                packed_evidence=None,
            ),
            {
                "rag_failure_kind": failure_kind,
                "rag_timeout_health_check_status": timeout_health_status,
                "rag_recovered_from_live_detail": False,
            },
        )

    if answer_detail.needs_engineer_guidance:
        LOGGER.info(
            "RAG service escalated request_id=%s ticket_id=%s reason=%s",
            request_id,
            ticket_id,
            answer_detail.reason,
        )
    return answer_detail
def resolve_support_message(
    message: str,
    *,
    ticket_id: str | None = None,
    customer_id: str | None = None,
    ticket_subject: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    product: str | None = None,
    decision: SupportRouteDecision | None = None,
) -> SupportResolution:
    return resolve_support_route_message(
        message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        product=product,
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


def build_emotion_context(ticket: dict[str, Any], limit: int = 6, max_chars: int = 240) -> list[dict[str, str]]:
    messages = ticket.get("messages", [])
    context: list[dict[str, str]] = []
    for item in messages[-max(1, int(limit)) :]:
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
    customer_id: str | None = None,
    ticket_subject: str | None = None,
    product: str | None = None,
    route_context_tail: list[dict[str, str]] | None = None,
    client_intake_state: dict[str, Any] | None = None,
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
    if customer_id:
        task["customer_id"] = str(customer_id).strip()
    if ticket_subject:
        task["ticket_subject"] = str(ticket_subject).strip()
    if product:
        task["product"] = str(product).strip()
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
    active_investigation = ticket.get("active_investigation")
    if not isinstance(active_investigation, dict):
        return None, []

    appended_messages: list[dict[str, Any]] = []
    if system_note:
        next_sequence = len(active_investigation.get("messages", [])) + 1
        system_message = {
            "id": f"{active_investigation.get('id')}-m-{next_sequence}",
            "role": "system",
            "content": str(system_note).strip(),
            "created_at": now_value,
        }
        active_investigation.setdefault("messages", []).append(system_message)
        appended_messages.append(system_message)

    active_investigation["state"] = "closed"
    active_investigation["draft_customer_reply"] = str(
        active_investigation.get("draft_customer_reply") or ""
    ).strip()
    active_investigation["final_confirmation_requested_at"] = None
    active_investigation["updated_at"] = now_value
    active_investigation["closed_at"] = now_value

    history = ticket.get("investigation_history")
    if not isinstance(history, list):
        history = []
        ticket["investigation_history"] = history
    history.insert(0, active_investigation)
    ticket["active_investigation"] = None
    return active_investigation, appended_messages


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
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").strip().lower() != "assistant":
            continue
        if not " ".join(str(message.get("content") or "").split()).strip():
            continue
        return copy.deepcopy(message)
    return None


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
        return case_payload

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
        return open_cases[0]
    return None


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/client")


@app.get("/login")
def login_entry() -> RedirectResponse:
    return RedirectResponse(url="/engineer")


@app.post("/api/v1/auth/logout")
def logout() -> dict[str, Any]:
    return {"ok": True, "logged_out_at": now_iso()}


@app.on_event("startup")
def startup_event() -> None:
    global ticket_repository
    try:
        ticket_repository.initialize()
        LOGGER.info("Ticket repository initialized: %s", ticket_repository.storage_mode())
    except (psycopg.OperationalError, psycopg.Error, OSError, TimeoutError) as exc:
        LOGGER.error("Ticket repository initialization failed: %s", exc)
        fallback_repository = InMemoryTicketRepository()
        fallback_repository.initialize()
        ticket_repository = fallback_repository
        LOGGER.warning("Falling back to in-memory ticket repository for this process.")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await event_bus.close()
    await task_queue.close()
    close_ticket_repository = getattr(ticket_repository, "close", None)
    if callable(close_ticket_repository):
        close_ticket_repository()


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

    ticket["customer_id"] = request.customer_id
    ticket["requester"] = (
        request.requester.strip()
        if request.requester and request.requester.strip()
        else ticket.get("requester") or request.customer_id
    )
    existing_subject = str(ticket.get("subject") or "").strip()
    if request.subject and request.subject.strip():
        ticket["subject"] = request.subject.strip()
    elif is_new_ticket or not existing_subject or existing_subject == "General support request":
        ticket["subject"] = derive_subject(request.message)

    if initial_message_count == 0:
        selected_product = _validated_new_session_product(request.product) or normalize_support_product(
            ticket.get("product")
        )
        if selected_product is None:
            raise HTTPException(status_code=400, detail="product is required for a new session")
        ticket["product"] = selected_product
    else:
        ticket["product"] = normalize_support_product(ticket.get("product"))

    if normalize_ticket_status(ticket.get("status")) == RESOLVED_STATUS:
        ticket["status"] = COMMUNICATING_STATUS

    timestamp = now_iso()
    customer_message = request.message.strip()
    ticket["messages"].append(
        {
            "role": "customer",
            "content": customer_message,
            "created_at": timestamp,
        }
    )

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
    route_context = build_emotion_context(ticket, limit=6, max_chars=400)
    investigation_result: dict[str, Any] | None = None
    engineer_case: dict[str, Any] | None = None
    engineer_case_created = False
    execution: TicketExecutionResult | None = None

    active_engineer_case_payload = _active_engineer_case_payload(ticket)
    main_agent_async_eligible = active_engineer_case_payload is None and _main_agent_async_enabled()
    if not main_agent_async_eligible:
        initial_ack = build_initial_ack(customer_message)
        ack_source = str(getattr(initial_ack, "source", "") or "server_ack").strip() or "server_ack"
        processing_mode = "main_agent_sync"
    if isinstance(active_engineer_case_payload, dict):
        engineer_case = _engineer_case_payload_to_record(active_engineer_case_payload)
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
    else:
        if main_agent_async_eligible:
            ticket["status"] = resolve_next_ticket_status(ticket.get("status"), COMMUNICATING_STATUS)
            processing_mode = "main_agent_async"
        else:
            processing_mode = "main_agent_sync"
            runtime_execution = execute_client_ticket_agent_runtime(
                customer_message,
                ticket_id=ticket_id,
                customer_id=request.customer_id,
                ticket_subject=str(ticket.get("subject") or "").strip() or None,
                ticket_context=route_context,
                product=ticket.get("product"),
                message_id=timestamp,
                client_intake_state=ticket.get("client_intake_state"),
                route_agent=decide_support_route,
                route_executor=resolve_support_message,
                rag_agent=lambda **kwargs: _build_rag_answer_detail(
                    kwargs["message"],
                    ticket_id=kwargs.get("ticket_id"),
                    customer_id=kwargs.get("customer_id"),
                    ticket_context=kwargs.get("ticket_context"),
                    product=kwargs.get("product"),
                ),
                review_agent=_run_client_ticket_review_agent,
                rag_canceler=None,
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
                customer_id=str(ticket.get("customer_id") or "").strip() or None,
                ticket_subject=str(ticket.get("subject") or "").strip() or None,
                product=str(ticket.get("product") or "").strip() or None,
                route_context_tail=route_context,
                client_intake_state=(
                    dict(ticket.get("client_intake_state"))
                    if isinstance(ticket.get("client_intake_state"), dict)
                    else None
                ),
                ticket_updated_at=str(ticket.get("updated_at") or "").strip() or None,
                load_ticket_ms=load_ticket_ms,
                save_ticket_ms=save_ticket_ms,
                api_persist_latency_ms=api_persist_latency_ms,
                processing_mode="main_agent_async",
            ),
        )
        enqueue_ticket_query_ms = round((time.perf_counter() - enqueue_started_at) * 1000, 2)

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
        "message": customer_message,
        "created_at": now_iso(),
        "parallel_mode": processing_mode,
        **admission_timing_payload,
    }
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
        build_client_sync_event(ticket, event["event"], customer_message[:200]),
    )
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
        "sources": [],
        "citations": [],
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
) -> dict[str, Any]:
    ticket = ticket_repository.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    runtime_state = (
        copy.deepcopy(ticket.get("client_agent_runtime_state"))
        if isinstance(ticket.get("client_agent_runtime_state"), dict)
        else {}
    )
    return {
        "ticket": ticket,
        "runtime_state": runtime_state,
        "final_assistant": _latest_assistant_message_for_ticket(ticket),
        "ticket_events": ticket_repository.list_ticket_events(ticket_id, limit=event_limit),
        "agent_events": ticket_repository.list_ticket_agent_events(ticket_id, limit=event_limit),
    }


@app.get("/api/dashboard/tickets")
def list_dashboard_tickets(
    status: str = Query(
        default=INVESTIGATING_STATUS,
        pattern="^(resolved|communicating|escalated|investigating)$",
    ),
) -> dict[str, Any]:
    normalized_filter = normalize_ticket_status(status)
    all_tickets = ticket_repository.list_tickets(include_messages=True)
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
    all_cases = ticket_repository.list_engineer_cases(include_client_messages=True)
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
def get_ticket_detail(ticket_id: str) -> dict[str, Any]:
    engineer_case = _resolve_engineer_case_payload(ticket_id)
    if engineer_case is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    client_ref = engineer_case.get("client_ticket_ref") if isinstance(engineer_case.get("client_ticket_ref"), dict) else {}
    client_ticket_id = str(client_ref.get("ticket_id") or "").strip() or None
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
        engineer_case["token_usage"] = {
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
        "updated_at": ticket["updated_at"],
    }


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
    if request.decision == "approve":
        draft_customer_reply = str(
            (engineer_case_payload.get("active_investigation") or {}).get("draft_customer_reply") or ""
        ).strip()
        if not draft_customer_reply:
            raise HTTPException(status_code=400, detail="A draft customer reply is required before approval.")

    engineer_case = _engineer_case_payload_to_record(engineer_case_payload)
    case_context = build_engineer_case_context(ticket, engineer_case)
    timestamp = now_iso()
    result = apply_investigation_confirmation(
        case_context,
        decision=request.decision,
        note=str(request.note or "").strip(),
        now_value=timestamp,
        ai_turn_builder=generate_investigation_ai_turn,
    )
    engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
    if request.decision == "approve":
        engineer_case["status"] = RESOLVED_STATUS
        ticket["status"] = COMMUNICATING_STATUS
    else:
        engineer_case["status"] = INVESTIGATING_STATUS
        ticket["status"] = INVESTIGATING_STATUS

    initial_message_count = len(ticket.get("messages", []))
    customer_reply = str(result.get("customer_reply") or "").strip()
    if request.decision == "approve" and customer_reply:
        ticket["messages"].append(
            {
                "role": "assistant",
                "content": customer_reply,
                "created_at": timestamp,
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
        None if request.decision == "approve" else str(engineer_case.get("engineer_case_id") or "").strip() or None
    )
    ticket_repository.save_ticket(ticket, new_messages=[])
    ticket_repository.save_engineer_case(
        engineer_case,
        new_messages=result.get("new_internal_messages"),
    )
    await _record_and_dispatch_investigation_event(ticket, engineer_case)

    if request.decision == "approve" and customer_reply:
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

    return {
        "ticket_id": str(engineer_case.get("engineer_case_id") or ticket_id),
        "status": engineer_case["status"],
        "active_investigation": result.get("active_investigation"),
        "closed_investigation": result.get("closed_investigation"),
        "updated_at": ticket["updated_at"],
    }


@app.get("/api/dashboard/metrics")
def dashboard_metrics() -> dict[str, Any]:
    tickets = ticket_repository.list_tickets(include_messages=True)
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
