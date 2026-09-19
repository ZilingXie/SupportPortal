"""Hermes tool failure-handoff contract tests (p2-163, tickets 13567/13595).

The business tool must NEVER fake success when it cannot complete, and the
turn must never reach persona/publication afterwards.  Covers:

- side effects disabled -> unified handoff (internal note + queue + owner
  email), hermes binding parked, tool returns human_review_required;
- enablement workflow raising -> same handoff;
- a work_result of human_review_required parks the turn before persona, so
  no customer-facing failure narrative can ever be drafted or published;
- the ownership gate claims the Zendesk ticket before business execution even
  when the hermes-created case has no route_family yet (13595: eligibility
  resolved an empty route_family and silently skipped the claim), and a
  fail-closed claim runs the same unified handoff.
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


    def _seed_case_without_route_family(self, store: InMemoryAutomationEcsStore):
        """The production shape from ticket 13595: the hermes-created account
        case has route/execution_action but no route_family (the legacy intake
        writes it at construction; the hermes path only wrote it deep inside
        the business execution, after the ownership gate had already run)."""
        from backend.repositories.ticket_repository import InMemoryTicketRepository

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
        handoff, agent_job, event = self._seed(store)
        return repository, handoff, agent_job, event

    def test_ownership_gate_claims_case_without_route_family(self) -> None:
        """13595 regression: eligibility must see route_family='automated' and
        the gate must claim the ticket BEFORE business execution."""
        from backend.services.account_automation_ownership import (
            OWNERSHIP_STATE_ASSIGNED,
            OwnershipGateResult,
        )

        store = _store()
        repository, handoff, _agent_job, _event = self._seed_case_without_route_family(store)

        gate_result = OwnershipGateResult(
            eligible=True,
            state=OWNERSHIP_STATE_ASSIGNED,
            assignee_id="48557297720084",
            group_id="29388501432596",
        )
        escalation: list[dict] = []
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

        async def fake_workflow(**kwargs):
            return kwargs["account_case"], NS(job_id="job-1"), "review_requested"

        import asyncio

        with patch(
            "backend.services.account_automation_ownership."
            "ensure_production_automation_ownership",
            return_value=gate_result,
        ) as ownership_mock, patch(
            "backend.services.automation_account_intake.extract_enablement_fields",
            return_value=complete,
        ), patch(
            "backend.services.automation_account_intake._run_enablement_workflow",
            side_effect=fake_workflow,
        ), patch(
            "backend.services.account_human_review_escalation."
            "escalate_account_case_to_human_review",
            side_effect=lambda **kw: escalation.append(kw) or NS(status="escalated"),
        ) as escalate_mock, patch(
            "backend.services.account_failure_alerts.notify_account_failure",
            side_effect=lambda **kw: notified.append(kw) or {"status": "sent"},
        ) as notify_mock:
            result = asyncio.run(
                tool_execute_automation_action(
                    store,
                    repository,
                    turn_id=handoff["turn_id"],
                    route="enablement",
                    environment="preproduction",
                    zendesk_side_effects_enabled=True,
                )
            )

        # The gate ran (pre-fix it was skipped: eligibility saw an empty
        # route_family) and saw the normalized route family on the case.
        ownership_mock.assert_called_once()
        # PR-B: the worker claims (gate) before the work run; the tool only
        # re-verifies read-only (the 60s ALB idle timeout kills longer calls).
        self.assertEqual(ownership_mock.call_args.kwargs.get("mode"), "verify")
        gate_case = ownership_mock.call_args.args[0]
        self.assertEqual(gate_case.get("route_family"), "automated")
        # The claim result is journaled and the case saved with the family.
        events = repository.list_ticket_events("123")
        ownership_events = [e for e in events if e.get("event_type") == "zendesk_ai_ownership"]
        self.assertEqual(len(ownership_events), 1)
        self.assertEqual(ownership_events[0]["payload"]["state"], "assigned")
        saved = repository.get_account_case("AC-123")
        self.assertEqual(saved["route_family"], "automated")
        # Business execution completed through the enablement workflow.
        self.assertEqual(result["status"], "workflow_completed")
        self.assertTrue(result["skip_persona"])
        escalate_mock.assert_not_called()
        self.assertEqual(notified, [])

    def test_ownership_gate_fail_closed_runs_unified_handoff(self) -> None:
        """A fail-closed claim (e.g. routed human already replied) must park the
        turn through the same unified handoff as any other failure."""
        from backend.services.account_automation_ownership import (
            OWNERSHIP_STATE_HUMAN_REASSIGNED,
            OwnershipGateResult,
        )

        store = _store()
        repository, handoff, _agent_job, _event = self._seed_case_without_route_family(store)

        gate_result = OwnershipGateResult(
            eligible=True,
            state=OWNERSHIP_STATE_HUMAN_REASSIGNED,
            assignee_id="31116509485716",
            failure_code="zendesk_ownership_human_reassigned",
            failure_category="policy",
        )
        escalate = NS(status="escalated")
        notified: list[dict] = []

        import asyncio

        with patch(
            "backend.services.account_automation_ownership."
            "ensure_production_automation_ownership",
            return_value=gate_result,
        ) as ownership_mock, patch(
            "backend.services.account_human_review_escalation."
            "escalate_account_case_to_human_review",
            return_value=escalate,
        ) as escalate_mock, patch(
            "backend.services.account_failure_alerts.notify_account_failure",
            side_effect=lambda **kw: notified.append(kw) or {"status": "sent"},
        ) as notify_mock:
            result = asyncio.run(
                tool_execute_automation_action(
                    store,
                    repository,
                    turn_id=handoff["turn_id"],
                    route="enablement",
                    environment="preproduction",
                    zendesk_side_effects_enabled=True,
                )
            )

        ownership_mock.assert_called_once()
        self.assertEqual(result["status"], "human_review_required")
        self.assertIn(
            "ownership_verify_zendesk_ownership_human_reassigned", result["reason"]
        )
        escalate_mock.assert_called_once()
        notify_mock.assert_called_once()
        # The fail path still journals the ownership event (legacy parity).
        events = repository.list_ticket_events("123")
        ownership_events = [e for e in events if e.get("event_type") == "zendesk_ai_ownership"]
        self.assertEqual(len(ownership_events), 1)
        self.assertEqual(
            ownership_events[0]["payload"]["state"], "human_reassigned"
        )
        # Binding parked before persona; persisted case is human-review.
        binding = store.get_hermes_case_binding("123")
        self.assertEqual(str(binding.get("direction") or ""), "human")
        self.assertEqual(str(binding.get("status") or ""), "paused")
        saved = repository.get_account_case("AC-123")
        self.assertEqual(saved["automation_status"], "human_review_required")
        turn = store.get_hermes_turn(handoff["turn_id"])
        work_result = turn.get("work_result")
        self.assertIsInstance(work_result, dict)
        self.assertEqual(work_result["status"], "human_review_required")


    def test_turn_superseded_during_extraction_stops_before_workflow(self) -> None:
        """Acceptance gap #3: a turn that dies during field extraction must
        not reach the business-write boundary (no workflow, no relay request,
        no reply job)."""
        from backend.services.account_automation_ownership import (
            OWNERSHIP_STATE_ASSIGNED,
            OwnershipGateResult,
        )

        store = _store()
        repository, handoff, _agent_job, _event = self._seed_case_without_route_family(store)
        gate_result = OwnershipGateResult(
            eligible=True,
            state=OWNERSHIP_STATE_ASSIGNED,
            assignee_id="48557297720084",
            group_id="29388501432596",
        )

        def supersede_mid_extraction(**kwargs):
            # The turn is superseded while the (slow) extraction runs.
            store._hermes_turns[handoff["turn_id"]]["status"] = "superseded"
            return {
                "customer_reply": "",
                "missing_fields": [],
                "collected_fields": {
                    "app_id": "0123456789abcdef0123456789abcdef",
                    "requested_feature": "media_relay",
                    "requested_feature_label": "media relay",
                },
                "internal_email_payload": {"to": ["ops@example.com"], "body": "x"},
                "internal_email_to_send": {"to": ["ops@example.com"], "body": "x"},
                "internal_email_send_status": "pending",
                "internal_email_send_reason": "",
                "requires_human_review": False,
            }

        import asyncio

        with patch(
            "backend.services.account_automation_ownership."
            "ensure_production_automation_ownership",
            return_value=gate_result,
        ), patch(
            "backend.services.automation_account_intake._build_enablement_attempt",
            side_effect=supersede_mid_extraction,
        ), patch(
            "backend.services.automation_account_intake._run_enablement_workflow"
        ) as workflow_mock:
            result = asyncio.run(
                tool_execute_automation_action(
                    store,
                    repository,
                    turn_id=handoff["turn_id"],
                    route="enablement",
                    environment="preproduction",
                    zendesk_side_effects_enabled=True,
                )
            )
        self.assertEqual(result["status"], "human_review_required")
        self.assertEqual(result["reason"], "turn_cancelled_before_execution")
        workflow_mock.assert_not_called()
        events = repository.list_ticket_events("123")
        dispatched = [
            e for e in events if e.get("event_type") == "enablement_auto_dispatch"
        ]
        self.assertEqual(dispatched, [])

    def test_tool_replay_returns_recorded_result_without_reexecution(self) -> None:
        """PR-B: a second invocation on the same turn replays the recorded
        business result; external actions never run twice."""
        from backend.services.account_automation_ownership import (
            OWNERSHIP_STATE_ASSIGNED,
            OwnershipGateResult,
        )

        store = _store()
        repository, handoff, _agent_job, _event = self._seed_case_without_route_family(store)

        gate_result = OwnershipGateResult(
            eligible=True,
            state=OWNERSHIP_STATE_ASSIGNED,
            assignee_id="48557297720084",
            group_id="29388501432596",
        )
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
        workflow_calls: list[dict] = []

        async def fake_workflow(**kwargs):
            workflow_calls.append(kwargs)
            return kwargs["account_case"], NS(job_id="job-1"), "review_requested"

        import asyncio

        with patch(
            "backend.services.account_automation_ownership."
            "ensure_production_automation_ownership",
            return_value=gate_result,
        ), patch(
            "backend.services.automation_account_intake.extract_enablement_fields",
            return_value=complete,
        ), patch(
            "backend.services.automation_account_intake._run_enablement_workflow",
            side_effect=fake_workflow,
        ) as workflow_mock, patch(
            "backend.services.account_human_review_escalation."
            "escalate_account_case_to_human_review",
            return_value=NS(status="escalated"),
        ), patch(
            "backend.services.account_failure_alerts.notify_account_failure",
            return_value={"status": "sent"},
        ):
            first = asyncio.run(
                tool_execute_automation_action(
                    store,
                    repository,
                    turn_id=handoff["turn_id"],
                    route="enablement",
                    environment="preproduction",
                    zendesk_side_effects_enabled=True,
                )
            )
            second = asyncio.run(
                tool_execute_automation_action(
                    store,
                    repository,
                    turn_id=handoff["turn_id"],
                    route="enablement",
                    environment="preproduction",
                    zendesk_side_effects_enabled=True,
                )
            )

        self.assertEqual(first["status"], "workflow_completed")
        self.assertEqual(second, first)
        workflow_mock.assert_called_once()
        self.assertEqual(len(workflow_calls), 1)


    def test_enablement_success_parks_neutrally_with_direction_kept(self) -> None:
        """PR-C: the review_requested wait parks the binding neutrally —
        status paused, direction still automation, no escalation trace."""
        from backend.services.account_automation_ownership import (
            OWNERSHIP_STATE_ASSIGNED,
            OwnershipGateResult,
        )

        store = _store()
        repository, handoff, _agent_job, _event = self._seed_case_without_route_family(store)
        gate_result = OwnershipGateResult(
            eligible=True,
            state=OWNERSHIP_STATE_ASSIGNED,
            assignee_id="48557297720084",
            group_id="29388501432596",
        )
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

        async def fake_workflow(**kwargs):
            return kwargs["account_case"], NS(job_id="job-1"), "review_requested"

        import asyncio

        with patch(
            "backend.services.account_automation_ownership."
            "ensure_production_automation_ownership",
            return_value=gate_result,
        ), patch(
            "backend.services.automation_account_intake.extract_enablement_fields",
            return_value=complete,
        ), patch(
            "backend.services.automation_account_intake._run_enablement_workflow",
            side_effect=fake_workflow,
        ):
            result = asyncio.run(
                tool_execute_automation_action(
                    store,
                    repository,
                    turn_id=handoff["turn_id"],
                    route="enablement",
                    environment="preproduction",
                    zendesk_side_effects_enabled=True,
                )
            )
        self.assertEqual(result["status"], "workflow_completed")
        binding = store.get_hermes_case_binding("123")
        self.assertEqual(str(binding.get("status") or ""), "paused")
        self.assertEqual(str(binding.get("direction") or ""), "automation")
        self.assertIsNone(binding.get("escalation"))

    def test_failure_handoff_records_each_step_outcome(self) -> None:
        """PR-C: the note/queue step failing alone must not skip the email or
        the park, and every step outcome is recorded visibly."""
        from backend.services.account_automation_ownership import (
            OWNERSHIP_STATE_FAILED,
            OwnershipGateResult,
        )

        store = _store()
        repository, handoff, _agent_job, _event = self._seed_case_without_route_family(store)
        gate_result = OwnershipGateResult(
            eligible=True,
            state=OWNERSHIP_STATE_FAILED,
            failure_code="zendesk_assignment_unverified",
            failure_category="policy",
        )
        notified: list[dict] = []

        import asyncio

        with patch(
            "backend.services.account_automation_ownership."
            "ensure_production_automation_ownership",
            return_value=gate_result,
        ), patch(
            "backend.services.account_human_review_escalation."
            "escalate_account_case_to_human_review",
            side_effect=RuntimeError("note endpoint down"),
        ), patch(
            "backend.services.account_failure_alerts.notify_account_failure",
            side_effect=lambda **kw: notified.append(kw) or {"status": "sent"},
        ):
            result = asyncio.run(
                tool_execute_automation_action(
                    store,
                    repository,
                    turn_id=handoff["turn_id"],
                    route="enablement",
                    environment="preproduction",
                    zendesk_side_effects_enabled=True,
                )
            )

        self.assertEqual(result["status"], "human_review_required")
        # The email still ran despite the note failing; both outcomes recorded.
        self.assertEqual(len(notified), 1)
        steps = result.get("handoff_steps") or {}
        self.assertEqual(steps.get("internal_note_queue_ownership"), "failed:RuntimeError")
        self.assertEqual(steps.get("owner_email"), "ok")
        self.assertEqual(steps.get("binding_park"), "ok")
        # The journaled work_result carries the same step outcomes.
        turn = store.get_hermes_turn(handoff["turn_id"])
        self.assertEqual(
            turn["work_result"].get("handoff_steps", {}).get("owner_email"), "ok"
        )
        # And a dedicated visibility event exists with the step map.
        events = repository.list_ticket_events("123")
        handoff_events = [
            e for e in events if e.get("event_type") == "automation_failure_handoff"
        ]
        self.assertEqual(len(handoff_events), 1)
        self.assertIn("steps", handoff_events[0]["payload"])


if __name__ == "__main__":
    unittest.main()
