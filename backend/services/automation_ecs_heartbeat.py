"""Independent DB heartbeat loop for long-running ECS Automation workers."""

from __future__ import annotations

import logging
from threading import Event, Thread

from backend.services.automation_ecs_contracts import RuntimeProvenance
from backend.services.automation_ecs_store import AutomationEcsStore, ClaimedJob

LOGGER = logging.getLogger("supportportal.automation_ecs_heartbeat")


class WorkerHeartbeat:
    def __init__(
        self,
        store: AutomationEcsStore,
        *,
        worker_id: str,
        provenance: RuntimeProvenance,
        interval_seconds: float = 5.0,
    ) -> None:
        self.store = store
        self.worker_id = worker_id
        self.provenance = provenance
        self.interval_seconds = max(1.0, interval_seconds)
        self._stop = Event()
        self._thread = Thread(target=self._run, name=f"heartbeat-{worker_id}", daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.store.heartbeat(worker_id=self.worker_id, provenance=self.provenance)
            except Exception:
                LOGGER.exception("Worker heartbeat write failed worker_id=%s", self.worker_id)
            self._stop.wait(self.interval_seconds)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self.interval_seconds + 1.0)


class JobLeaseHeartbeat:
    """Keep one claimed job leased while its owner is actively processing it."""

    def __init__(
        self,
        store: AutomationEcsStore,
        *,
        job: ClaimedJob,
        lease_seconds: int,
    ) -> None:
        self.store = store
        self.job = job
        self.lease_seconds = max(1, lease_seconds)
        self.interval_seconds = max(1.0, self.lease_seconds / 3.0)
        self._stop = Event()
        self._thread = Thread(
            target=self._run,
            name=f"job-lease-{job.job_id}",
            daemon=True,
        )

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.store.renew_job_lease(self.job, lease_seconds=self.lease_seconds)
            except Exception:
                LOGGER.exception("Job lease renewal failed job_id=%s", self.job.job_id)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self.interval_seconds + 1.0)
