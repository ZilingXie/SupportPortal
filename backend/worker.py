from __future__ import annotations

import logging
import signal
import sys
from typing import Any

from backend.main import (
    MANAGED_MODE,
    TAKEOVER_MODE,
    build_answer,
    build_client_sync_event,
    build_engineer_followup_request,
    ensure_ticket_defaults,
    now_iso,
    ticket_repository,
)
from backend.services.event_bus import SyncRedisEventBus
from backend.services.task_queue import SyncRedisTaskQueue

LOGGER = logging.getLogger(__name__)
SHUTTING_DOWN = False


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


def _is_latest_customer_message(ticket: dict[str, Any], message: str, created_at: str) -> bool:
    expected_content = str(message).strip()
    expected_created_at = str(created_at).strip()
    for item in reversed(ticket.get("messages", [])):
        if str(item.get("role", "")).strip().lower() != "customer":
            continue
        content = str(item.get("content", "")).strip()
        ts = str(item.get("created_at", "")).strip()
        if expected_content and content != expected_content:
            return False
        if expected_created_at and ts and ts != expected_created_at:
            return False
        return True
    return False


def _process_ticket_query(bus: SyncRedisEventBus, task: dict[str, Any]) -> None:
    ticket_id = str(task.get("ticket_id", "")).strip()
    customer_message = str(task.get("customer_message", "")).strip()
    message_created_at = str(task.get("message_created_at", "")).strip()
    if not ticket_id or not customer_message:
        return

    ticket = ticket_repository.get_ticket(ticket_id)
    if ticket is None:
        LOGGER.warning("Worker skipped: ticket not found (%s)", ticket_id)
        return
    ensure_ticket_defaults(ticket)

    if not _is_latest_customer_message(ticket, customer_message, message_created_at):
        LOGGER.info("Worker skipped stale task for ticket %s", ticket_id)
        return

    initial_message_count = len(ticket.get("messages", []))
    current_mode = str(ticket.get("engineer_mode") or MANAGED_MODE).strip().lower()
    if current_mode == TAKEOVER_MODE:
        attention_event = {
            "event": "engineer_attention_required",
            "ticket_id": ticket_id,
            "priority": ticket.get("priority", "normal"),
            "status": ticket.get("status", "open"),
            "engineer_mode": TAKEOVER_MODE,
            "message": "Customer sent a new message in takeover mode. Please contact the customer directly.",
            "created_at": now_iso(),
        }
        ticket_repository.record_event(ticket_id, attention_event["event"], attention_event)
        _publish(bus, ["engineer", "dashboard"], attention_event)
        _publish(bus, ["client"], build_client_sync_event(ticket, attention_event["event"]))
        return

    answer, confidence, sources, citations, needs_engineer_guidance = build_answer(customer_message)
    _ = confidence  # Confidence is returned to API responses; worker only persists messages/events.
    needs_engineer_input = False
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
    if sources:
        assistant_message["sources"] = sources
    if citations:
        assistant_message["citations"] = citations
    ticket["messages"].append(assistant_message)

    ticket["updated_at"] = now_iso()
    new_messages = ticket.get("messages", [])[initial_message_count:]
    ticket_repository.save_ticket(ticket, new_messages=new_messages)

    event = {
        "event": "ticket_ai_response_ready",
        "ticket_id": ticket_id,
        "priority": ticket.get("priority", "normal"),
        "status": ticket["status"],
        "engineer_mode": ticket["engineer_mode"],
        "message": answer[:200],
        "created_at": now_iso(),
    }
    ticket_repository.record_event(ticket_id, event["event"], event)
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
            "created_at": now_iso(),
        }
        ticket_repository.record_event(ticket_id, attention_event["event"], attention_event)
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
        if task_type != "ticket_query":
            LOGGER.warning("Worker ignored unknown task type: %s", task_type)
            continue
        try:
            _process_ticket_query(bus, task)
        except Exception as exc:
            LOGGER.exception("Worker failed to process task: %s", exc)

    queue.close()
    bus.close()
    LOGGER.info("Worker stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(run_worker())

