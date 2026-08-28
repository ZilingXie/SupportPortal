"""Shared pytest fixtures for the backend test suite.

Importing ``backend.main`` loads the repository-root ``.env`` (see
``load_dotenv`` in ``backend/main.py``), so every test process that touches
``main`` or ``worker`` runs with live Microsoft Graph mail credentials. The
Account failure alert path must therefore be stubbed centrally: without this
fixture, any failure-path test reaching ``notify_account_failure`` sends real
alert email to the operations inbox (observed live: twelve alerts in one
minute from a single ``test_account_intake.py`` run).

The patch targets are deliberate:

- Consumers call ``notify_account_failure`` through their own module globals
  (``from ... import notify_account_failure``), so patching the source module
  ``backend.services.account_failure_alerts`` has no effect.
- ``mail_sender=send_graph_mail`` is bound as a default argument at function
  definition time, so patching ``account_failure_alerts.send_graph_mail``
  cannot intercept already-imported references either.

Only patching each consumer namespace stops the outbound mail. Existing
per-test patches stack on top of this fixture and keep their semantics.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _stub_account_failure_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop Account failure alert emails from leaving the test process."""

    def _silent_notify(**kwargs):
        return {"status": "sent", "incident_id": kwargs.get("incident_id", "")}

    import backend.main as main_module
    import backend.services.automation_account_intake as intake_module
    import backend.worker as worker_module

    for consumer in (main_module, worker_module, intake_module):
        if hasattr(consumer, "notify_account_failure"):
            monkeypatch.setattr(consumer, "notify_account_failure", _silent_notify)
