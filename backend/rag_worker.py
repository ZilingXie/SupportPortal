from __future__ import annotations

import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any

from backend.repositories.event_repository import create_event_repository
from backend.repositories.knowledge_repository import create_knowledge_repository
from backend.services.rag_benchmark_runner import run_benchmark
from backend.services.rag_eval_dataset_factory import process_dataset_generation
from backend.services.event_bus import SyncRedisEventBus
from backend.services.knowledge_ingestion import process_knowledge_ingestion
from backend.services.knowledge_monitoring import build_knowledge_event_payload
from backend.services.task_queue import SyncRedisTaskQueue

LOGGER = logging.getLogger(__name__)
SHUTTING_DOWN = False

knowledge_repository = create_knowledge_repository()
event_repository = create_event_repository()
task_queue = SyncRedisTaskQueue(queue_name=(os.getenv("RAG_QUEUE_NAME") or "support.rag.tasks").strip())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _process_dataset_generation(task: dict[str, Any]) -> None:
    generation_run_id = str(task.get("generation_run_id") or "").strip()
    if not generation_run_id:
        LOGGER.warning("RAG worker skipped dataset_generation task without generation_run_id")
        return
    if not knowledge_repository.is_enabled():
        LOGGER.warning("RAG worker skipped dataset_generation %s because repository is disabled", generation_run_id)
        return
    try:
        knowledge_repository.update_dataset_generation_run(
            generation_run_id,
            status="processing",
            error_message="",
            started_at=_utc_now(),
        )
        process_dataset_generation(knowledge_repository, generation_run_id)
    except Exception as exc:
        knowledge_repository.update_dataset_generation_run(
            generation_run_id,
            status="failed",
            error_message=str(exc),
            finished_at=_utc_now(),
        )
        raise


def _process_dataset_benchmark(task: dict[str, Any]) -> None:
    dataset_id = str(task.get("dataset_id") or "").strip()
    eval_run_id = str(task.get("eval_run_id") or "").strip()
    if not dataset_id or not eval_run_id:
        LOGGER.warning("RAG worker skipped dataset_benchmark task without dataset_id/eval_run_id")
        return
    if not knowledge_repository.is_enabled():
        LOGGER.warning("RAG worker skipped dataset_benchmark %s because repository is disabled", eval_run_id)
        return
    snapshot = knowledge_repository.get_dataset_snapshot(dataset_id)
    experiment_id = str(task.get("experiment_id") or "").strip() or eval_run_id
    if snapshot is not None:
        knowledge_repository.upsert_rag_eval_run(
            eval_run={
                "eval_run_id": eval_run_id,
                "dataset_name": snapshot.get("dataset_name"),
                "eval_type": "dataset_snapshot_benchmark",
                "experiment_id": experiment_id,
                "strategy_snapshot": {},
                "judge_models": [],
                "benchmark_version": snapshot.get("benchmark_version"),
                "status": "running",
                "started_at": _utc_now(),
                "finished_at": None,
            }
        )
    try:
        run_benchmark(
            dataset_id=dataset_id,
            dataset_tier=str(task.get("tier") or "gold").strip() or "gold",
            experiment_id=experiment_id,
            top_k=int(task.get("top_k")) if task.get("top_k") is not None else None,
            repository=knowledge_repository,
            eval_run_id=eval_run_id,
        )
    except Exception:
        if snapshot is not None:
            knowledge_repository.upsert_rag_eval_run(
                eval_run={
                    "eval_run_id": eval_run_id,
                    "dataset_name": snapshot.get("dataset_name"),
                    "eval_type": "dataset_snapshot_benchmark",
                    "experiment_id": experiment_id,
                    "strategy_snapshot": {},
                    "judge_models": [],
                    "benchmark_version": snapshot.get("benchmark_version"),
                    "status": "failed",
                    "started_at": _utc_now(),
                    "finished_at": _utc_now(),
                }
            )
        raise


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
        try:
            if task_type == "knowledge_ingest":
                _process_knowledge_ingest(bus, task)
            elif task_type == "dataset_generation":
                _process_dataset_generation(task)
            elif task_type == "dataset_benchmark":
                _process_dataset_benchmark(task)
            else:
                LOGGER.info("RAG worker skipped unsupported task type: %s", task_type or "(missing)")
        except Exception as exc:
            LOGGER.exception("RAG worker failed to process task type %s: %s", task_type or "(missing)", exc)
        time.sleep(0.05)

    task_queue.close()
    bus.close()
    LOGGER.info("RAG worker stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
