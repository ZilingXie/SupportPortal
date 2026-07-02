from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import logging
import os
import re
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import psycopg

from backend.main import (
    _record_ticket_agent_runtime_events,
    _run_client_ticket_review_agent,
    build_query_task,
    build_client_sync_event,
    ensure_ticket_defaults,
    now_iso,
    resolve_support_message,
    ticket_repository,
)
from backend.services.app_build import get_app_build_info
from backend.services.engineer_cases import (
    build_new_engineer_case,
    apply_case_context_to_engineer_case,
    build_engineer_case_context,
    close_case_context_active_investigation,
    derive_engineer_case_title,
)
from backend.services.event_bus import SyncRedisEventBus
from backend.services.investigation_flow import (
    COMMUNICATING_STATUS,
    INVESTIGATING_STATUS,
    RESOLVED_STATUS,
    build_investigation_opening_context,
    default_investigation_prompt as generate_investigation_ai_turn,
    normalize_ticket_status,
    start_or_refresh_investigation,
)
from backend.services.billing_automation import poll_billing_request_replies, record_billing_request_reply
from backend.services.client_ticket_agent_runtime import (
    TicketExecutionResult,
    build_execution_route_payload,
    execute_client_ticket_agent_runtime,
    resolve_next_ticket_status,
)
from backend.services.product_selection import resolve_support_product_context
from backend.services.rag_executor import build_worker_rag_executor
from backend.services.rag_service_client import (
    RagServiceClient,
    RagServiceError,
    RagTicketAnswerDetail,
)
from backend.services.sentiment_classifier import classify_sentiment
from backend.services.support_router import SupportResolution, SupportRouteDecision, decide_support_route
from backend.services.task_queue import SyncRedisTaskQueue
from backend.services.ticket_message_sentiment import (
    build_ticket_message_sentiment_event,
    classify_customer_message_sentiment,
)

LOGGER = logging.getLogger(__name__)
SHUTTING_DOWN = False
TICKET_LOOKUP_RETRY_MAX = 6
TICKET_LOOKUP_RETRY_BASE_DELAY_SECONDS = 0.12
MESSAGE_TIMESTAMP_TOLERANCE_SECONDS = 1.0
SUPPORTED_WORKER_TASK_TYPES = ("ticket_query", "ticket_message_sentiment")
BILLING_REPLY_POLL_ENABLED_ENV = "BILLING_AUTOMATION_REPLY_POLL_ENABLED"
BILLING_REPLY_POLL_INTERVAL_ENV = "BILLING_AUTOMATION_REPLY_POLL_INTERVAL_SECONDS"
BILLING_REPLY_POLL_MAX_MESSAGES_ENV = "BILLING_AUTOMATION_REPLY_POLL_MAX_MESSAGES"


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
    return str(os.getenv(BILLING_REPLY_POLL_ENABLED_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


def _billing_reply_poll_interval_from_env() -> float:
    return _safe_positive_float(os.getenv(BILLING_REPLY_POLL_INTERVAL_ENV), 300.0)


def _billing_reply_poll_max_messages_from_env() -> int:
    return _safe_positive_int(os.getenv(BILLING_REPLY_POLL_MAX_MESSAGES_ENV), 25)


def _install_signal_handlers() -> None:
    def _handle_signal(signum: int, _frame: Any) -> None:
        global SHUTTING_DOWN
        SHUTTING_DOWN = True
        LOGGER.info("Worker received signal %s, shutting down...", signum)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)


def _run_billing_reply_poller(interval_seconds: float) -> None:
    LOGGER.info("Billing reply poller started with interval_seconds=%s.", interval_seconds)
    while not SHUTTING_DOWN:
        try:
            replies = poll_billing_request_replies(
                handler=record_billing_request_reply,
                max_messages=_billing_reply_poll_max_messages_from_env(),
            )
            if replies:
                LOGGER.info("Billing reply poller handled %s reply message(s).", len(replies))
        except Exception as exc:
            LOGGER.warning("Billing reply poller failed: %s", exc)
        sleep_until = time.time() + max(interval_seconds, 1.0)
        while not SHUTTING_DOWN and time.time() < sleep_until:
            time.sleep(min(1.0, max(0.0, sleep_until - time.time())))
    LOGGER.info("Billing reply poller stopped.")


def _start_billing_reply_poller_if_enabled() -> threading.Thread | None:
    if not _billing_reply_poller_enabled_from_env():
        return None
    interval_seconds = _billing_reply_poll_interval_from_env()
    thread = threading.Thread(
        target=_run_billing_reply_poller,
        args=(interval_seconds,),
        name="billing-reply-poller",
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
        ticket_repository.initialize()
    except Exception as exc:
        LOGGER.error("Worker failed to initialize ticket repository: %s", exc)
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
