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
    MANAGED_MODE,
    TAKEOVER_MODE,
    build_answer,
    build_client_sync_event,
    build_engineer_followup_request,
    ensure_ticket_defaults,
    now_iso,
    resolve_support_message,
    ticket_repository,
)
from backend.services.event_bus import SyncRedisEventBus
from backend.services.task_queue import SyncRedisTaskQueue

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

    current_mode = str(ticket.get("engineer_mode") or MANAGED_MODE).strip().lower()
    if current_mode == TAKEOVER_MODE:
        attention_event = {
            "event": "engineer_attention_required",
            "ticket_id": ticket_id,
            "priority": ticket.get("priority", "normal"),
            "status": ticket.get("status", "open"),
            "engineer_mode": TAKEOVER_MODE,
            "message": "Customer sent a new message in takeover mode. Please contact the customer directly.",
            "message_created_at": message_created_at,
            "created_at": now_iso(),
        }
        _call_ticket_repository(
            "record_event",
            lambda: ticket_repository.record_event(ticket_id, attention_event["event"], attention_event),
        )
        _publish(bus, ["engineer", "dashboard"], attention_event)
        _publish(bus, ["client"], build_client_sync_event(ticket, attention_event["event"]))
        return

    route_context = [
        {
            "role": str(item.get("role", "system")).strip().lower() or "system",
            "content": " ".join(str(item.get("content", "")).split()).strip(),
        }
        for item in ticket.get("messages", [])
        if " ".join(str(item.get("content", "")).split()).strip()
    ]
    resolution = resolve_support_message(
        customer_message,
        ticket_id=ticket_id,
        customer_id=str(ticket.get("customer_id") or "").strip() or None,
        ticket_subject=str(ticket.get("subject") or "").strip() or None,
        ticket_context=route_context[-6:],
    )
    answer = resolution.answer
    sources = list(resolution.sources)
    citations = [dict(item) for item in resolution.citations]
    needs_engineer_guidance = resolution.needs_engineer_guidance
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
    refreshed_mode = str(refreshed_ticket.get("engineer_mode") or MANAGED_MODE).strip().lower()
    if refreshed_mode == TAKEOVER_MODE:
        LOGGER.info("Worker dropped AI result because ticket %s switched to takeover mode", ticket_id)
        return

    ticket = refreshed_ticket
    existing_response = _find_existing_worker_response(ticket, customer_message, message_created_at)
    needs_engineer_input = False
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
        needs_engineer_input = str(ticket.get("status") or "").strip().lower() == "waiting_for_engineer"
    else:
        initial_message_count = len(ticket.get("messages", []))
        if needs_engineer_guidance:
            engineer_request = build_engineer_followup_request(ticket, customer_message)
            answer = (
                "I could not find enough reliable information in the knowledge sources for this issue. "
                "I have contacted an engineer to continue investigation and will follow up shortly."
            )
            sources = []
            citations = []
            ticket["status"] = "waiting_for_engineer"
            ticket["pending_engineer_question"] = engineer_request
            needs_engineer_input = True
        else:
            ticket["status"] = "open"
            ticket["pending_engineer_question"] = None

        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": answer,
            "created_at": now_iso(),
        }
        assistant_message["answer_route"] = resolution.answer_route
        assistant_message["scope_label"] = resolution.scope_label
        assistant_message["route_reason"] = resolution.route_reason
        assistant_message["route_confidence"] = round(float(resolution.route_confidence), 4)
        assistant_message["search_used"] = bool(resolution.search_used)
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

    event = {
        "event": "ticket_ai_response_ready",
        "ticket_id": ticket_id,
        "priority": ticket.get("priority", "normal"),
        "status": ticket["status"],
        "engineer_mode": ticket["engineer_mode"],
        "message": answer[:200],
        "message_created_at": message_created_at,
        "created_at": now_iso(),
        "answer_route": resolution.answer_route,
        "scope_label": resolution.scope_label,
        "route_reason": resolution.route_reason,
        "route_confidence": round(float(resolution.route_confidence), 4),
        "search_used": bool(resolution.search_used),
    }
    _call_ticket_repository(
        "record_event",
        lambda: ticket_repository.record_event(ticket_id, event["event"], event),
    )
    _publish(bus, ["engineer", "dashboard"], event)
    _publish(bus, ["client"], build_client_sync_event(ticket, event["event"]))

    if needs_engineer_input:
        attention_event = {
            "event": "engineer_attention_required",
            "ticket_id": ticket_id,
            "priority": ticket.get("priority", "normal"),
            "status": ticket["status"],
            "engineer_mode": ticket["engineer_mode"],
            "message": ticket.get("pending_engineer_question") or "Engineer attention required",
            "message_created_at": message_created_at,
            "created_at": now_iso(),
        }
        _call_ticket_repository(
            "record_event",
            lambda: ticket_repository.record_event(ticket_id, attention_event["event"], attention_event),
        )
        _publish(bus, ["engineer", "dashboard"], attention_event)
        _publish(bus, ["client"], build_client_sync_event(ticket, attention_event["event"]))


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
