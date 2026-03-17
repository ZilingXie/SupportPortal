from __future__ import annotations

import logging
import os
import signal
import sys
import time
from typing import Any

from backend.repositories.event_repository import create_event_repository
from backend.repositories.knowledge_repository import create_knowledge_repository
from backend.services.event_bus import SyncRedisEventBus
from backend.services.knowledge_ingestion import process_knowledge_ingestion
from backend.services.knowledge_monitoring import build_knowledge_event_payload
from backend.services.task_queue import SyncRedisTaskQueue

LOGGER = logging.getLogger(__name__)
SHUTTING_DOWN = False

knowledge_repository = create_knowledge_repository()
event_repository = create_event_repository()
task_queue = SyncRedisTaskQueue(queue_name=(os.getenv("RAG_QUEUE_NAME") or "support.rag.tasks").strip())


def _install_signal_handlers() -> None:
    def _handle_signal(signum: int, _frame: Any) -> None:
        global SHUTTING_DOWN
        SHUTTING_DOWN = True
        LOGGER.info("RAG worker received signal %s, shutting down...", signum)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)


def _publish(bus: SyncRedisEventBus, channels: list[str], payload: dict[str, Any]) -> None:
    bus_payload = dict(payload)
    bus_payload["targets"] = channels
    bus.publish(bus_payload)


def _process_knowledge_ingest(bus: SyncRedisEventBus, task: dict[str, Any]) -> None:
    ingestion_id = str(task.get("ingestion_id") or "").strip()
    if not ingestion_id:
        LOGGER.warning("RAG worker skipped knowledge_ingest task without ingestion_id")
        return
    if not knowledge_repository.is_enabled():
        LOGGER.warning("RAG worker skipped knowledge_ingest task %s because repository is disabled", ingestion_id)
        return

    queued_record = knowledge_repository.get_ingestion(ingestion_id, include_content=False)
    if queued_record is not None:
        processing_payload = build_knowledge_event_payload(
            "knowledge_ingestion_processing",
            queued_record,
            status_override="processing",
        )
        event_repository.record_event(None, processing_payload["event"], processing_payload)
        _publish(bus, ["dashboard"], processing_payload)

    try:
        completed_record = process_knowledge_ingestion(knowledge_repository, ingestion_id)
    except Exception:
        failed_record = knowledge_repository.get_ingestion(ingestion_id, include_content=False)
        if failed_record is not None:
            failed_payload = build_knowledge_event_payload(
                "knowledge_ingestion_failed",
                failed_record,
                status_override="failed",
            )
            event_repository.record_event(None, failed_payload["event"], failed_payload)
            _publish(bus, ["dashboard"], failed_payload)
        raise

    if completed_record is None:
        completed_record = knowledge_repository.get_ingestion(ingestion_id, include_content=False)
    if completed_record is None:
        LOGGER.warning("RAG worker finished knowledge_ingest %s without a record", ingestion_id)
        return

    completed_payload = build_knowledge_event_payload(
        "knowledge_ingestion_completed",
        completed_record,
        status_override="completed",
    )
    event_repository.record_event(None, completed_payload["event"], completed_payload)
    _publish(bus, ["dashboard"], completed_payload)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _install_signal_handlers()

    try:
        event_repository.initialize()
        LOGGER.info("RAG worker event repository initialized: %s", event_repository.storage_mode())
    except Exception as exc:
        LOGGER.error("RAG worker failed to initialize event repository: %s", exc)
        return 1

    try:
        knowledge_repository.initialize()
        LOGGER.info("RAG worker knowledge repository initialized: %s", knowledge_repository.storage_mode())
    except Exception as exc:
        LOGGER.error("RAG worker failed to initialize knowledge repository: %s", exc)
        return 1

    bus = SyncRedisEventBus()
    LOGGER.info("RAG worker started. waiting for tasks...")

    while not SHUTTING_DOWN:
        task = task_queue.dequeue(timeout_seconds=5)
        if task is None:
            continue
        task_type = str(task.get("task_type") or "").strip()
        if task_type != "knowledge_ingest":
            LOGGER.info("RAG worker skipped unsupported task type: %s", task_type or "(missing)")
            continue
        try:
            _process_knowledge_ingest(bus, task)
        except Exception as exc:
            LOGGER.exception("RAG worker failed to process knowledge ingestion task: %s", exc)
        time.sleep(0.05)

    task_queue.close()
    bus.close()
    LOGGER.info("RAG worker stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
