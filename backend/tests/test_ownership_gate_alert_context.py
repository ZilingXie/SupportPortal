"""Ownership gate to persisted handoff and actual alert body (R2-F2)."""
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import backend.tests.test_hermes_zendesk_agent as harness
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_automation_ownership import OwnershipGateResult
from backend.services.automation_hermes_agent import HermesAgentTurnProcessor
from backend.services.automation_ecs_contracts import AgentTurnJobPayload, JobKind


def setup_gate():
    store = harness._store()
    event = harness._event()
    handoff = harness._accept_and_hand_off(store, event)
    turn_id = handoff["turn_id"]
    store.record_hermes_turn_direction(turn_id, direction="automation", route="enablement")
    store.record_hermes_case_direction(turn_id, direction="automation", reason="test")
    job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker", lease_seconds=300)
    repository = InMemoryTicketRepository()
    repository.save_account_case({
        "account_case_id": "AC-123", "billing_ticket_id": "AC-123", "client_ticket_id": "123",
        "zendesk_ticket_id": "123", "processing_profile": "preproduction",
        "automation_status": "automation", "route": "enablement", "execution_action": "enablement",
        "route_family": "automated", "automation_context": {"ownership": {"state": "released_to_queue"}},
    })
    processor = HermesAgentTurnProcessor.__new__(HermesAgentTurnProcessor)
    processor.store, processor.repository, processor.environment = store, repository, "preproduction"
    processor._active_job_info = {"job_id": job.job_id, "attempt": 2}
    return processor, AgentTurnJobPayload.model_validate(job.payload), job


@pytest.mark.parametrize("existing_run", [False, True])
def test_gate_failure_reaches_real_alert_and_parks(existing_run):
    processor, payload, job = setup_gate()
    if existing_run:
        processor.store.get_or_create_hermes_turn_run(payload.turn_id, "work", prompt_version="test")
        processor.store.start_hermes_turn_run(payload.turn_id, "work", run_id="real-work-run")
    gate = OwnershipGateResult(
        eligible=True, state="released_to_queue", failure_code="released_to_queue",
        failure_category="policy", failure_detail="Ownership was released to the human queue",
        assignee_id="human-1", group_id="group-1",
    )
    with patch.dict(os.environ, {"AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED": "1"}), patch(
        "backend.services.account_automation_ownership.ensure_production_automation_ownership", return_value=gate,
    ) as query, patch(
        "backend.services.account_human_review_escalation.add_ticket_comment", return_value=SimpleNamespace(comment_id="note-1"),
    ), patch(
        "backend.services.account_human_review_escalation.route_ticket_back_to_queue", return_value=SimpleNamespace(status="queued"),
    ), patch("backend.services.account_failure_alerts.send_graph_mail") as mail:
        assert not processor._claim_automation_ownership_before_work(payload, processor.store.get_hermes_turn(payload.turn_id))
    query.assert_called_once()
    mail.assert_called_once()
    body = mail.call_args.kwargs["body"]
    for line in (
        "Failed phase: ownership_gate", "Code: ownership_gate_released_to_queue",
        "Failure reason: Ownership was released to the human queue", "Ticket: 123", "Account Case: AC-123",
        f"Job: {job.job_id}", "Attempts: 2", f"Turn: {payload.turn_id}",
        "Run: real-work-run" if existing_run else "Run: not_started",
    ):
        assert line in body
    assert processor.repository.get_account_case("AC-123")["automation_status"] == "human_review_required"
    binding = processor.store.get_hermes_case_binding("123")
    assert binding["status"] == "paused" and binding["direction"] == "human"
    assert processor.store.get_hermes_turn(payload.turn_id)["work_result"]["status"] == "human_review_required"
    assert not processor.repository.list_account_zendesk_comment_deliveries_for_case("AC-123")
    assert not processor.store.get_hermes_case_review("123")["drafts"]


def test_confirmed_gate_can_continue_without_alert():
    processor, payload, _ = setup_gate()
    with patch.dict(os.environ, {"AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED": "1"}), patch(
        "backend.services.account_automation_ownership.ensure_production_automation_ownership",
        return_value=OwnershipGateResult(eligible=True, state="assigned"),
    ), patch("backend.services.account_failure_alerts.send_graph_mail") as mail:
        assert processor._claim_automation_ownership_before_work(payload, processor.store.get_hermes_turn(payload.turn_id))
    mail.assert_not_called()
