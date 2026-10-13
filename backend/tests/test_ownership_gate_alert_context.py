"""C7 regression: the ownership-gate failure escalation must carry the real
gate failure code and phase — never <unknown> (plan finding 1)."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

import backend.tests.test_hermes_zendesk_agent as harness


class OwnershipGateAlertContextTests(unittest.TestCase):
    def test_gate_failure_escalation_carries_phase_and_code(self):
        from backend.services.automation_hermes_agent import (
            HermesAgentTurnProcessor,
        )

        store = harness._store()
        event = harness._event()
        store.accept_intake(event, harness._settings().provenance())
        from backend.services.automation_ecs_store import JobKind

        job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
        handoff = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")
        store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)

        account_case = {
            "account_case_id": "AC-GATE",
            "billing_ticket_id": "AC-GATE",
            "client_ticket_id": "123",
            "zendesk_ticket_id": "123",
            "processing_profile": "preproduction",
            "automation_status": "automation",
            "route": "fraud_account",
            "execution_action": "fraud_account",
            "route_family": "automated",
            "collected_fields": {},
        }
        processor = HermesAgentTurnProcessor.__new__(HermesAgentTurnProcessor)
        processor.store = store
        processor.repository = MagicMock()
        processor.environment = "preproduction"

        payload = MagicMock()
        payload.turn_id = handoff["turn_id"]
        payload.event.ticket.id = "123"

        with patch(
            "backend.services.automation_hermes_tools._escalate_uncompleted_automation",
            return_value={"status": "human_review_required"},
        ) as escalate_mock:
            processor._escalate_automation_failure(
                payload,
                account_case,
                reason_code="ownership_gate_routed_human_replied",
                detail="Zendesk ownership gate failed: assignee already replied",
                failed_phase="ownership_gate",
                failure_reason="assignee already replied",
            )
        escalate_mock.assert_called_once()
        kwargs = escalate_mock.call_args.kwargs
        self.assertEqual(kwargs.get("failed_phase"), "ownership_gate")
        self.assertEqual(kwargs.get("failure_reason"), "assignee already replied")
        self.assertIn("routed_human_replied", kwargs.get("reason_code", ""))


if __name__ == "__main__":
    unittest.main()
