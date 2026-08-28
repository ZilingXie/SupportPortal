"""Shared pytest fixtures for the backend test suite.

Importing ``backend.main`` loads the repository-root ``.env`` (see
``load_dotenv`` in ``backend/main.py``), which both hands live Microsoft Graph
mail credentials to the test process and snapshots ``RAG_SERVICE_URL`` into
the module-level ``RagServiceClient`` instances — flipping tests that assert
the "RAG not configured" path. This fixture therefore never imports anything:
it only patches consumers that the collected test modules already imported.

The Account failure alert path must be stubbed centrally: without this
fixture, any failure-path test reaching ``notify_account_failure`` sends real
alert email to the operations inbox (observed live: twelve alerts in one
minute from a single ``test_account_intake.py`` run, plus the
``TK-INVALID-CONTRACT`` incident from ``test_worker.py``).

The patch mechanics are deliberate:

- Consumers call ``notify_account_failure`` through their own module globals
  (``from ... import notify_account_failure``), so patching the source module
  ``backend.services.account_failure_alerts`` has no effect.
- ``mail_sender=send_graph_mail`` is bound as a default argument at function
  definition time, so patching ``account_failure_alerts.send_graph_mail``
  cannot intercept already-imported references either.

Only patching each consumer namespace stops the outbound mail. Existing
per-test patches stack on top of this fixture and keep their semantics.

``test_worker.py`` loads ``backend/worker.py`` under the standalone name
``backend.tests._worker_under_test`` (registered in ``sys.modules`` by its
loader); that name is part of the consumer list below.
"""

from __future__ import annotations

import sys

import pytest


_CONSUMER_MODULE_NAMES = (
    "backend.main",
    "backend.worker",
    "backend.services.automation_account_intake",
    "backend.tests._worker_under_test",
)


@pytest.fixture(autouse=True)
def _stub_account_failure_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop Account failure alert emails from leaving the test process."""

    def _silent_notify(**kwargs):
        return {"status": "sent", "incident_id": kwargs.get("incident_id", "")}

    for name in _CONSUMER_MODULE_NAMES:
        consumer = sys.modules.get(name)
        if consumer is not None and hasattr(consumer, "notify_account_failure"):
            monkeypatch.setattr(consumer, "notify_account_failure", _silent_notify)
