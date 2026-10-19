"""14027: real native tool -> basis -> draft -> delivery -> next turn."""
import asyncio
import os
from types import SimpleNamespace as NS
from unittest.mock import patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

import pytest
import backend.tests.test_hermes_zendesk_agent as harness
from backend import worker
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_automation_ownership import OwnershipGateResult
from backend.services.automation_hermes_delivery import prepare_hermes_draft_delivery, _TranslationResult
from backend.services.automation_hermes_tools import tool_execute_automation_action, tool_save_reply_draft, publication_decision_for_turn
import backend.tests.test_hermes_tool_failure_handoff as tool_harness
from backend.tests.test_hermes_zendesk_agent_tools import _guardrail_pass
from backend.services.automation_ecs_contracts import IntakeEventType


def test_native_enablement_persona_prompt_naturalizes_missing_app_id_request() -> None:
    from backend.services.automation_hermes_agent import phase_instructions
    from backend.services.prompt_runtime import _code_snapshot, use_prompt_runtime_snapshot

    with use_prompt_runtime_snapshot(_code_snapshot()):
        instructions, key = phase_instructions(
            "persona",
            direction="automation",
            route="enablement",
            persona_style="Warm, concise senior support engineer voice.",
            persona_key="test-persona",
        )
    assert key == "hermes-persona-manual"
    assert "naturally connect the requested feature to the next step" in instructions
    assert "exactly [`app_id`]" in instructions
    assert "canonical name" in instructions


def test_native_enablement_prompt_keeps_missing_app_id_safety_boundaries() -> None:
    import re

    from backend.services.automation_hermes_agent import phase_instructions
    from backend.services.prompt_runtime import _code_snapshot, use_prompt_runtime_snapshot

    with use_prompt_runtime_snapshot(_code_snapshot()):
        instructions, _ = phase_instructions(
            "persona", direction="automation", route="enablement"
        )
    normalized = re.sub(r"\s+", " ", instructions)
    assert "never imply the request was submitted" in normalized
    assert "Never fabricate status, outcomes, causes, or timelines" in normalized
    assert "No internal system names" in normalized


@pytest.mark.parametrize("stale_workflow,completed,mode", [(False, False, "manual"), (True, False, "manual"), (False, True, "manual"), (False, True, "archer")])
def test_missing_app_id_delivery_preserves_ownership_and_next_turn(stale_workflow, completed, mode):
    store, repository = harness._store(), InMemoryTicketRepository()
    handoff, _, _ = tool_harness.ToolFailureHandoffTests()._seed(store)
    turn_id = handoff["turn_id"]
    store.start_hermes_agent_turn(turn_id, run_id="synthetic-work-run")
    repository.save_ticket({
        "ticket_id": "123", "customer_id": "cx@example.com", "subject": "Enable Media Relay",
        "status": "open", "created_at": "2026-09-08T10:00:00Z", "updated_at": "2026-09-08T10:00:00Z",
        "messages": [{"role": "customer", "content": "Please enable media relay.", "created_at": "2026-09-08T10:00:00Z"}],
    }, new_messages=[])
    repository.save_account_case({
        "account_case_id": "AC-123", "billing_ticket_id": "AC-123", "client_ticket_id": "123",
        "zendesk_ticket_id": "123", "processing_profile": "preproduction", "automation_status": "automation",
        "route": "enablement", "execution_action": "enablement", "route_family": "automated",
        "internal_email_send_status": "not_applicable",
        "automation_context": {"enablement_auto_workflow": {"reply_job_id": "stale-job"}} if stale_workflow else {},
    })
    extraction = NS(status="missing_fields", missing_fields=["app_id"], collected_fields={"requested_feature": "media_relay"},
                    requires_human_review=False, follow_up="Please share your App ID.", audit_payload=lambda: {"status": "missing_fields"})
    if completed:
        extraction.missing_fields = []
        extraction.collected_fields["app_id"] = "a" * 32
        extraction.collected_fields["requested_feature_label"] = "media relay"
    ownership = OwnershipGateResult(eligible=True, state="assigned", ticket_status="open")
    actual_save = repository.save_account_reply_job
    def save_and_schedule(job):
        persisted = actual_save(job)
        # Exercise the competing scheduler before the workflow returns to Hermes.
        assert persisted["payload"]["hermes_compatibility_only"] is True
        with patch.object(worker, "ticket_repository", repository), patch.object(worker, "render_automation_reply") as render:
            worker._process_claimed_account_reply_jobs(from_status=persisted["status"],
                to_status="persona_v8_preparing", due_only=False, limit=10)
        render.assert_not_called()
        assert repository.get_account_reply_job(persisted["job_id"])["status"] == persisted["status"]
        return persisted
    with patch.dict(os.environ, {"AUTOMATION_CASE_ENGINE": "hermes", "HERMES_CASE_WORKFLOW_MODE": "disabled", "ENABLEMENT_WORKFLOW_MODE": mode}), patch(
        "backend.services.automation_account_intake.extract_enablement_fields", return_value=extraction,
    ), patch("backend.services.account_automation_ownership.ensure_production_automation_ownership", return_value=ownership), patch.object(repository, "save_account_reply_job", side_effect=save_and_schedule):
        result = asyncio.run(tool_execute_automation_action(store, repository, turn_id=turn_id, route="enablement", environment="preproduction"))
    assert result["status"] == ("workflow_completed" if completed else "missing_fields")
    assert result["reply_basis"]["compatibility_relay_required"] is completed
    if completed:
        job_id = result["compatibility_account_reply_job_id"]
        job = repository.get_account_reply_job(job_id)
        assert job["payload"]["hermes_compatibility_only"] is True
        assert repository.get_account_case("AC-123")["automation_context"][f"enablement_{'manual' if mode == 'manual' else 'auto'}_workflow"]["reply_job_id"] == job_id
    store._hermes_turns[turn_id]["phase"] = "persona"
    with patch("backend.services.automation_hermes_tools.run_engineer_guardrail_final", side_effect=_guardrail_pass):
        saved = tool_save_reply_draft(store, repository, turn_id=turn_id, content=(
            "Your media relay request has been submitted for review." if completed
            else "To enable media relay, please share your App ID."))
    publication = publication_decision_for_turn(store, repository, turn_id=turn_id, environment="preproduction")
    assert publication["status"] == "preparing"
    with patch("backend.services.automation_hermes_delivery.translate_draft_for_delivery",
               return_value=_TranslationResult(reference_language="english", translated_text="unused")), patch(
        "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot", return_value=NS(comments_revision="rev-1"),
    ):
        prepared = prepare_hermes_draft_delivery(store, repository, draft_id=saved["draft_id"], environment="preproduction")
    assert prepared["status"] == "queued"
    delivery = repository.list_account_zendesk_comment_deliveries_for_case("AC-123")[0]
    assert {"_hermes_compatibility_relay_required": completed} in delivery["attachments"]
    with patch.object(worker, "ticket_repository", repository), patch(
        "backend.services.automation_ecs_store.create_automation_ecs_store", return_value=store,
    ), patch.object(worker, "ensure_production_automation_ownership", return_value=ownership), patch.object(
        worker, "read_ticket_ownership_snapshot", return_value=NS(comments_revision=delivery["comments_revision"], ticket_status="open"),
    ), patch.object(worker, "add_ticket_comment", return_value=NS(comment_id="public-1")) as send, patch.object(
        worker, "read_ticket_comment_audit", return_value=(NS(comment_id="public-1"), set()),
    ), patch.object(repository, "release_enablement_relay_requests_after_public_reply", wraps=repository.release_enablement_relay_requests_after_public_reply) as relay, patch.object(
        repository, "release_account_internal_email_after_public_reply", wraps=repository.release_account_internal_email_after_public_reply,
    ) as mail:
        worker._deliver_hermes_zendesk_comment(delivery)
        replay = repository.list_account_zendesk_comment_deliveries_for_case("AC-123")[0]
        assert replay["status"] == "delivered"
        worker._deliver_hermes_zendesk_comment(replay)
    send.assert_called_once()
    if completed:
        assert relay.call_count == 2 and mail.call_count == 2
        assert repository.get_account_case("AC-123")["internal_email_send_status"] == ("pending" if mode == "manual" else "not_applicable")
        assert len(repository.get_ticket("123")["messages"]) == 2
        return
    relay.assert_not_called()
    mail.assert_not_called()
    assert repository.get_account_case("AC-123")["automation_status"] == "automation"
    store.complete_hermes_agent_turn(turn_id, result={"status": "completed"})
    next_handoff = harness._accept_and_hand_off(store, harness._event(event_id="next-customer", event_type=IntakeEventType.COMMENT_CREATED))
    assert next_handoff["turn_id"] != turn_id
    next_id = next_handoff["turn_id"]
    store.record_hermes_turn_direction(next_id, direction="automation", route="enablement")
    store.record_hermes_case_direction(next_id, direction="automation", reason="customer follow-up")
    with patch("backend.services.automation_account_intake.extract_enablement_fields", return_value=extraction), patch(
        "backend.services.account_automation_ownership.ensure_production_automation_ownership", return_value=ownership,
    ):
        next_result = asyncio.run(tool_execute_automation_action(store, repository, turn_id=next_id, route="enablement", environment="preproduction"))
    assert next_result["status"] == "missing_fields"
    assert repository.get_account_case("AC-123")["automation_status"] == "automation"


def test_missing_app_id_real_draft_entry_rejects_extra_sensitive_field_request():
    """C1 negative control: the real draft boundary rejects an extra email ask."""
    from backend.services.automation_hermes_tools import HermesToolError

    store = harness._store()
    repository = InMemoryTicketRepository()
    handoff, _, _ = tool_harness.ToolFailureHandoffTests()._seed(store)
    turn_id = handoff["turn_id"]
    store.start_hermes_agent_turn(turn_id, run_id="synthetic-work-run")
    store.record_hermes_turn_direction(turn_id, direction="automation", route="enablement")
    store.record_hermes_case_direction(turn_id, direction="automation", reason="missing app id")
    store.record_hermes_turn_work(
        turn_id,
        work_result={
            "status": "missing_fields",
            "route": "enablement",
            "missing_fields": ["app_id"],
            "collected_fields": {"requested_feature": "media_relay"},
        },
    )
    with store._lock:
        store._hermes_turns[turn_id]["phase"] = "persona"
    with patch("backend.services.automation_hermes_tools.run_engineer_guardrail_final", side_effect=_guardrail_pass):
        with pytest.raises(HermesToolError, match="contact_email|unauthorized_field_request"):
            tool_save_reply_draft(
                store,
                repository,
                turn_id=turn_id,
                content="I can help enable Media Relay. Please also send your email address.",
            )
