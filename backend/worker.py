from __future__ import annotations

import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any

import psycopg

from backend.main import (
    build_client_sync_event,
    ensure_ticket_defaults,
    now_iso,
    resolve_support_message,
    ticket_repository,
)
from backend.services.engineer_cases import (
    build_new_engineer_case,
    apply_case_context_to_engineer_case,
    build_engineer_case_context,
    derive_engineer_case_title,
)
from backend.services.event_bus import SyncRedisEventBus
from backend.services.investigation_flow import (
    COMMUNICATING_STATUS,
    INVESTIGATING_STATUS,
    build_investigation_opening_context,
    default_investigation_prompt as generate_investigation_ai_turn,
    normalize_ticket_status,
    start_or_refresh_investigation,
)
from backend.services.ticket_orchestrator import (
    TicketExecutionResult,
    build_execution_route_payload,
    orchestrate_ticket_execution,
    resolve_next_ticket_status,
)
from backend.services.sentiment_classifier import classify_sentiment
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


def _install_signal_handlers() -> None:
    def _handle_signal(signum: int, _frame: Any) -> None:
        global SHUTTING_DOWN
        SHUTTING_DOWN = True
        LOGGER.info("Worker received signal %s, shutting down...", signum)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)


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


def _orchestrate_worker_support_message(
    customer_message: str,
    *,
    ticket_id: str,
    customer_id: str | None,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]],
    product: str | None = None,
    client_intake_state: dict[str, object] | None = None,
) -> TicketExecutionResult:
    return orchestrate_ticket_execution(
        customer_message,
        ticket_id=ticket_id,
        customer_id=customer_id,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        product=product,
        client_intake_state=client_intake_state,
        resolution_builder=resolve_support_message,
    )


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
        assistant_messages.append(item)
    if len(assistant_messages) < 2:
        return None
    return assistant_messages[-1]


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
    if not ticket_id or not customer_message:
        return

    ticket, lookup_attempts, latest_message_found = _load_ticket_with_retry(
        ticket_id,
        customer_message,
        message_created_at,
    )
    if ticket is None:
        LOGGER.warning(
            "Worker skipped: ticket not found (%s) after %s retries",
            ticket_id,
            lookup_attempts,
        )
        return
    if not latest_message_found:
        LOGGER.info(
            "Worker skipped stale task for ticket %s after %s retries",
            ticket_id,
            lookup_attempts,
        )
        return
    if lookup_attempts > 0:
        LOGGER.info(
            "Worker recovered delayed ticket/message state for %s after %s retries",
            ticket_id,
            lookup_attempts,
        )
    ensure_ticket_defaults(ticket)

    if _is_task_cancelled(ticket_id, message_created_at):
        LOGGER.info("Worker skipped cancelled task for ticket %s", ticket_id)
        return

    route_context = [
        {
            "role": str(item.get("role", "system")).strip().lower() or "system",
            "content": " ".join(str(item.get("content", "")).split()).strip(),
        }
        for item in ticket.get("messages", [])
        if " ".join(str(item.get("content", "")).split()).strip()
    ]
    execution = _orchestrate_worker_support_message(
        customer_message,
        ticket_id=ticket_id,
        customer_id=str(ticket.get("customer_id") or "").strip() or None,
        ticket_subject=str(ticket.get("subject") or "").strip() or None,
        ticket_context=route_context[-6:],
        product=str(ticket.get("product") or "").strip() or None,
        client_intake_state=(
            dict(ticket.get("client_intake_state"))
            if isinstance(ticket.get("client_intake_state"), dict)
            else None
        ),
    )
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
        needs_engineer_input = (
            normalize_ticket_status(ticket.get("status")) == INVESTIGATING_STATUS
            or _active_engineer_case_payload(ticket) is not None
        )
    else:
        initial_message_count = len(ticket.get("messages", []))
        execution_client_intake_state = (
            dict(getattr(execution, "client_intake_state"))
            if isinstance(getattr(execution, "client_intake_state", None), dict)
            else None
        )
        execution_workflow_action = str(getattr(execution, "workflow_action", "") or "").strip()
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
            execution_route_payload = build_execution_route_payload(execution)
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

    event = {
        "event": "ticket_ai_response_ready",
        "ticket_id": ticket_id,
        "status": ticket["status"],
        "message": answer[:200],
        "message_created_at": message_created_at,
        "created_at": now_iso(),
        "answer_route": execution.answer_route,
        "scope_label": execution.scope_label,
        "route_family": execution.route_family,
        "execution_action": execution.execution_action,
        "tooling_profile": execution.tooling_profile,
        "route_reason": execution.route_reason,
        "route_confidence": round(float(execution.route_confidence), 4),
        "search_used": bool(execution.search_used),
        "matched_signals": list(execution.matched_signals),
        "workflow_action": str(getattr(execution, "workflow_action", "") or "").strip(),
    }
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
    _call_ticket_repository(
        "record_event",
        lambda: ticket_repository.record_event(ticket_id, event["event"], event),
    )
    _publish(bus, ["engineer", "dashboard"], event)
    _publish(bus, ["client"], build_client_sync_event(ticket, event["event"]))
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


def run_worker() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _install_signal_handlers()

    try:
        ticket_repository.initialize()
    except Exception as exc:
        LOGGER.error("Worker failed to initialize ticket repository: %s", exc)
        return 1

    queue = SyncRedisTaskQueue()
    bus = SyncRedisEventBus()
    if not queue.is_enabled():
        LOGGER.error("Worker requires REDIS_URL and TASK_QUEUE_NAME configuration.")
        return 1

    LOGGER.info("Worker started and waiting for tasks.")
    while not SHUTTING_DOWN:
        task = queue.dequeue(timeout_seconds=5)
        if not task:
            continue
        task_type = str(task.get("task_type", "")).strip().lower()
        if task_type == "ticket_query":
            try:
                _process_ticket_query(bus, task)
            except Exception as exc:
                if _schedule_ticket_task_retry(queue, task, exc):
                    continue
                LOGGER.exception("Worker failed to process ticket task: %s", exc)
            continue
        if task_type == "ticket_message_sentiment":
            try:
                _process_ticket_message_sentiment(bus, task)
            except Exception as exc:
                if _schedule_ticket_task_retry(queue, task, exc):
                    continue
                LOGGER.exception("Worker failed to process sentiment task: %s", exc)
            continue
        if task_type:
            LOGGER.warning("Worker ignored unknown task type: %s", task_type)
            continue
        LOGGER.warning("Worker ignored task without task_type")

    queue.close()
    bus.close()
    LOGGER.info("Worker stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(run_worker())
