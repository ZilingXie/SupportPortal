from __future__ import annotations

import time
from unittest.mock import Mock

from backend.services.automation_ecs_heartbeat import WorkerHeartbeat
from backend.tests.test_automation_ecs_store import _settings


def test_heartbeat_remains_independent_from_job_processing() -> None:
    store = Mock()
    heartbeat = WorkerHeartbeat(
        store,
        worker_id="route-1",
        provenance=_settings("route").provenance(),
        interval_seconds=1.0,
    )
    heartbeat.start()
    time.sleep(1.1)
    heartbeat.stop()
    assert store.heartbeat.call_count >= 2
