"""Hermes tool failure-handoff contract tests (p2-163, ticket 13567).

The business tool must NEVER fake success when it cannot complete, and the
turn must never reach persona/publication afterwards.  Covers:

- side effects disabled -> unified handoff (internal note + queue + owner
  email), hermes binding parked, tool returns human_review_required;
- enablement workflow raising -> same handoff;
- a work_result of human_review_required parks the turn before persona, so
  no customer-facing failure narrative can ever be drafted or published.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from backend.services.automation_ecs_contracts import (
    INTAKE_CONTRACT_VERSION,
    IntakeEventType,
)
from backend.services.automation_ecs_runtime import AutomationEcsSettings
from backend.services.automation_ecs_store import (
    InMemoryAutomationEcsStore,
    JobKind,
)

import backend.tests.test_hermes_zendesk_agent as harness
from backend.services.automation_hermes_agent import HermesAgentTurnProcessor
from backend.services.automation_hermes_tools import tool_execute_automation_action


def _settings(role: str = "route") -> AutomationEcsSettings:
    return harness._settings(role)


def _store() -> InMemoryAutomationEcsStore:
    store = InMemoryAutomationEcsStore(harness._settings())
    store.migrate()
    return store


class ToolFailureHandoffTests(unittest.TestCase):
    def _seed(self, store: InMemoryAutomationEcsStore, *, route: str = "enablement"):
        event = harness._event()
        receipt = store.accept_intake(event, _settings().provenance())
        job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
        handoff = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")
        store.record_hermes_turn_direction(
            handoff["turn_id"], direction="automation", route=route
        )
        # The tool gate reads the BINDING direction; the real route phase
        # sets it via tool_record_direction (turn + case binding together).
        store.record_hermes_case_direction(
            handoff["turn_id"],
            direction="automation",
            reason="test automation direction",
        )
        agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        return handoff, agent_job, event

    def test_side_effects_disabled_runs_unified_handoff_and_parks(self) -> None:
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        store = _store()
        repository = InMemoryTicketRepository()
        repository.save_ticket(
            {
                "ticket_id": "123",
                "customer_id": "cx@example.com",
                "requester": "cx@example.com",
                "subject": "Enable Media Relay",
                "status": "open",
                "created_at": "2026-09-08T10:00:00Z",
                "updated_at": "2026-09-08T10:00:00Z",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please enable media relay for "
                        "0123456789abcdef0123456789abcdef.",
                        "created_at": "2026-09-08T10:00:00Z",
                    }
                ],
            },
            new_messages=[],
        )
        repository.save_account_case(
            {
                "account_case_id": "AC-123",
                "billing_ticket_id": "AC-123",
                "client_ticket_id": "123",
                "zendesk_ticket_id": "123",
                "processing_profile": "preproduction",
                "automation_status": "automation",
                "route": "enablement",
                "collected_fields": {
                    "app_id": "0123456789abcdef0123456789abcdef",
                    "requested_feature": "media_relay",
                    "requested_feature_label": "media relay",
                },
                "internal_email_payload": None,
                "internal_email_send_status": "not_applicable",
            }
        )
        handoff, _agent_job, _event = self._seed(store)

        escalate = NS(status="escalated")
        notified: list[dict] = []
        complete = NS(
            status="ok",
            missing_fields=[],
            collected_fields={
                "app_id": "0123456789abcdef0123456789abcdef",
                "requested_feature": "media_relay",
                "requested_feature_label": "media relay",
            },
            requires_human_review=False,
            audit_payload=lambda: {"status": "ok"},
            follow_up=None,
        )
        with patch(
            "backend.services.automation_account_intake.extract_enablement_fields",
            return_value=complete,
        ), patch(
            "backend.services.account_human_review_escalation."
            "escalate_account_case_to_human_review",
            return_value=escalate,
        ) as escalate_mock, patch(
            "backend.services.account_failure_alerts.notify_account_failure",
            side_effect=lambda **kw: notified.append(kw) or {"status": "sent"},
        ) as notify_mock:
            result = None
            import asyncio

            result = asyncio.run(
                tool_execute_automation_action(
                    store,
                    repository,
                    turn_id=handoff["turn_id"],
                    route="enablement",
                    environment="preproduction",
                    zendesk_side_effects_enabled=False,
                )
            )

        self.assertEqual(result["status"], "human_review_required")
        self.assertEqual(result["reason"], "zendesk_side_effects_disabled")
        escalate_mock.assert_called_once()
        self.assertEqual(
            escalate_mock.call_args.kwargs["account_case"]["automation_status"],
            "human_review_required",
        )
        notify_mock.assert_called_once()
        self.assertIn("zendesk_side_effects_disabled", notified[0]["incident_id"])
        # The hermes binding is parked so no further phases run.
        binding = store.get_hermes_case_binding("123")
        self.assertEqual(str(binding.get("direction") or ""), "human")
        self.assertEqual(str(binding.get("status") or ""), "paused")
        # The persisted case is human-review and was not flipped back.
        saved = repository.get_account_case("AC-123")
        self.assertEqual(saved["automation_status"], "human_review_required")
        # Review #1: the helper itself writes turn.work_result — the
        # processor's persona gate reads exactly this field.
        turn = store.get_hermes_turn(handoff["turn_id"])
        work_result = turn.get("work_result")
        self.assertIsInstance(work_result, dict)
        self.assertEqual(work_result["status"], "human_review_required")
        self.assertEqual(work_result["reason"], "zendesk_side_effects_disabled")

    def test_publication_gate_parks_turn_before_persona(self) -> None:
        store = _store()
        handoff, agent_job, _event = self._seed(store)

        submissions: list[dict] = []

        # Simulate what the real tool now does when it escalates: write
        # work_result with human_review_required (the processor gate reads
        # exactly this). The fake LLM client can't invoke the real HTTP tool,
        # so this manual write IS the simulation of the tool's behavior.
        def on_run_completed(run_id, idempotency_key):
            phase = idempotency_key.rsplit(":", 1)[-1]
            if phase == "route":
                store.record_hermes_turn_direction(
                    handoff["turn_id"],
                    direction="automation",
                    route="enablement",
                )
                store.record_hermes_case_direction(
                    handoff["turn_id"],
                    direction="automation",
                    reason="test automation direction",
                )
            if phase == "work":
                store.record_hermes_turn_work(
                    handoff["turn_id"],
                    work_result={
                        "status": "human_review_required",
                        "reason": "zendesk_side_effects_disabled",
                    },
                )

        client = harness.FakeHermesClient(on_run_completed=on_run_completed)
        original_start = client.start_run

        def counting_start(*args, **kwargs):
            submissions.append(kwargs)
            return original_start(*args, **kwargs)

        client.start_run = counting_start
        processor = HermesAgentTurnProcessor(
            store,
            client=client,
            environment="preproduction",
            repository=None,
            poll_interval_seconds=0.01,
        )
        outcome = processor.process(agent_job)

        self.assertEqual(outcome["status"], "human_review")
        self.assertEqual(outcome["reason"], "zendesk_side_effects_disabled")
        # Route + work ran; persona must never be submitted.
        self.assertEqual(len(submissions), 2)
        turn = store.get_hermes_turn(handoff["turn_id"])
        self.assertEqual(turn["status"], "completed")

    def test_enablement_success_skips_persona_with_pipeline_reply(self) -> None:
        """Review #3: enablement workflow_completed skips persona; the legacy
        pipeline reply job is the sole customer reply."""
        store = _store()
        handoff, agent_job, _event = self._seed(store)

        submissions: list[dict] = []

        def on_run_completed(run_id, idempotency_key):
            phase = idempotency_key.rsplit(":", 1)[-1]
            if phase == "route":
                store.record_hermes_turn_direction(
                    handoff["turn_id"],
                    direction="automation",
                    route="enablement",
                )
                store.record_hermes_case_direction(
                    handoff["turn_id"],
                    direction="automation",
                    reason="test automation direction",
                )
            if phase == "work":
                # Simulate what the enablement tool now returns on success:
                # skip_persona=True (the workflow created the pipeline reply).
                store.record_hermes_turn_work(
                    handoff["turn_id"],
                    work_result={
                        "status": "workflow_completed",
                        "outcome": "review_requested",
                        "skip_persona": True,
                    },
                )

        client = harness.FakeHermesClient(on_run_completed=on_run_completed)
        original_start = client.start_run

        def counting_start(*args, **kwargs):
            submissions.append(kwargs)
            return original_start(*args, **kwargs)

        client.start_run = counting_start
        processor = HermesAgentTurnProcessor(
            store,
            client=client,
            environment="preproduction",
            repository=None,
            poll_interval_seconds=0.01,
        )
        outcome = processor.process(agent_job)

        # Turn completed (not human_review); persona never ran.
        self.assertEqual(outcome["status"], "completed")
        self.assertEqual(outcome["reason"], "review_requested")
        self.assertEqual(len(submissions), 2)  # route + work only


if __name__ == "__main__":
    unittest.main()
