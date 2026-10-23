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


def test_native_enablement_eval_semantics_fail_closed_on_extra_fields_and_claims() -> None:
    from scripts.testing.hermes_native_enablement_eval import _semantic_verdict

    good = _semantic_verdict(
        "Hi Taylor, I can help enable Media Relay. Once you share the App ID, I can continue.",
        greeting_name="Taylor",
    )
    assert all(good.values())
    extra = _semantic_verdict(
        "Hi Taylor, please share your App ID and office address.", greeting_name="Taylor"
    )
    assert extra["c1_only_app_id_request"] is False
    claim = _semantic_verdict(
        "Hi Taylor, Media Relay is already enabled and will be reviewed within 24 hours.",
        greeting_name="Taylor",
    )
    assert claim["c3_no_unsupported_claim"] is False


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
        # p2-211: the completed submission draft is the confirmation — the
        # archer (auto) mode must satisfy the natural contract (receipt +
        # review + business days + update commitment); the manual mode keeps
        # its shorter receipt-only shape.
        if completed:
            confirmation_content = (
                "Your media relay request has been submitted for internal review, and "
                "we will keep you posted as soon as there is an update."
                + (
                    " Reviews are handled on business days, Monday through Friday."
                    if mode == "archer"
                    else ""
                )
            )
        else:
            confirmation_content = "To enable media relay, please share your App ID."
        saved = tool_save_reply_draft(store, repository, turn_id=turn_id, content=confirmation_content)
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


class PersonaDraftContractGateTests:
    """p2-211 persona-draft-contract-gate: the enablement review-requested
    draft is gated on the natural submission-confirmation contract at the
    REAL tool_save_reply_draft entry — the 14202/14225 shape can no longer
    reach the delivery pipeline through the Hermes native draft path."""

    _14225_CONTENT = (
        "Hi Ziling, Thanks for your request. I've requested a review to enable "
        "Media Relay for your project. Enablement hasn't been confirmed yet."
    )
    NATURAL_AUTO = (
        "Thanks for sending your Media Relay request. I've submitted it for "
        "internal review, and I'll keep you posted as soon as I have an "
        "update. Reviews are handled on business days, Monday through Friday."
    )
    NATURAL_MANUAL = (
        "Your media relay request has been submitted for internal review, and "
        "we will keep you posted as soon as there is an update."
    )

    @staticmethod
    def _seed_review_requested_turn(store, repository, *, reason):
        handoff, _, _ = tool_harness.ToolFailureHandoffTests()._seed(store)
        turn_id = handoff["turn_id"]
        store.start_hermes_agent_turn(turn_id, run_id="synthetic-work-run")
        store.record_hermes_turn_direction(turn_id, direction="automation", route="enablement")
        store.record_hermes_case_direction(turn_id, direction="automation", reason="valid app id submitted")
        store.record_hermes_turn_work(
            turn_id,
            work_result={
                "status": "workflow_completed",
                "route": "enablement",
                "outcome": "review_requested",
                "internal_email_send_status": "awaiting_public_reply",
                "internal_email_send_reason": reason,
                "collected_fields": {
                    "app_id": "a" * 32,
                    "requested_feature": "media_relay",
                    "requested_feature_label": "media relay",
                },
                "compatibility_relay_required": reason == "enablement_auto_review",
                "compatibility_account_reply_job_id": "account-reply-synthetic",
            },
        )
        with store._lock:
            store._hermes_turns[turn_id]["phase"] = "persona"
        return turn_id


def _gate_case(reason, content, expect_error=None):
    store = harness._store()
    repository = InMemoryTicketRepository()
    turn_id = PersonaDraftContractGateTests._seed_review_requested_turn(store, repository, reason=reason)
    from backend.services.automation_hermes_tools import HermesToolError

    def draft_count():
        with store._lock:
            return len(store._hermes_drafts)

    with patch("backend.services.automation_hermes_tools.run_engineer_guardrail_final", side_effect=_guardrail_pass):
        if expect_error:
            with pytest.raises(HermesToolError, match=expect_error):
                tool_save_reply_draft(store, repository, turn_id=turn_id, content=content)
            return None, draft_count()
        saved = tool_save_reply_draft(store, repository, turn_id=turn_id, content=content)
        return saved, draft_count()


@pytest.mark.parametrize("reason", ["enablement_auto_review", "enablement_manual_review"])
def test_draft_gate_rejects_14225_shape_on_both_paths(reason):
    saved, drafts = _gate_case(reason, PersonaDraftContractGateTests._14225_CONTENT, expect_error="submission_confirmation_stiff_negative_status")
    assert saved is None and drafts == 0


def test_draft_gate_auto_path_requires_full_natural_contract():
    missing_bizday = (
        "Thanks for your message. I've submitted your request for internal "
        "review and will update you as soon as there is news."
    )
    _gate_case("enablement_auto_review", missing_bizday, expect_error="submission_confirmation_business_day_missing")
    missing_update = (
        "Thanks for your Media Relay request. It is submitted for internal "
        "review. Reviews are handled on business days, Monday through Friday."
    )
    _gate_case("enablement_auto_review", missing_update, expect_error="submission_confirmation_update_commitment_missing")
    _gate_case("enablement_auto_review", "Thanks!", expect_error="submission_confirmation_receipt_missing")
    hours24 = PersonaDraftContractGateTests.NATURAL_AUTO.replace(
        "Reviews are handled on business days, Monday through Friday.",
        "It should complete within 24 hours.",
    )
    _gate_case("enablement_auto_review", hours24, expect_error="submission_confirmation_24h_promise")
    premature = PersonaDraftContractGateTests.NATURAL_AUTO.replace(
        "Reviews are handled on business days, Monday through Friday.",
        "Media Relay is enabled on your project.",
    )
    _gate_case("enablement_auto_review", premature, expect_error="submission_confirmation_premature_enablement")


def test_draft_gate_manual_path_keeps_shorter_contract():
    # The manual draft needs only receipt + no stiff negative; 24-hour
    # wording on the draft path is governed by the pre-existing basis
    # time-commitment guard (unsupported_time_commitment), not this gate.
    _gate_case("enablement_manual_review", PersonaDraftContractGateTests.NATURAL_MANUAL, expect_error=None)


@pytest.mark.parametrize("reason,content", [
    ("enablement_auto_review", PersonaDraftContractGateTests.NATURAL_AUTO),
    ("enablement_manual_review", PersonaDraftContractGateTests.NATURAL_MANUAL),
])
def test_draft_gate_accepts_compliant_confirmations(reason, content):
    saved, drafts = _gate_case(reason, content)
    assert saved and saved.get("draft_id") and drafts == 1


def test_draft_gate_regeneration_succeeds_after_rejection():
    """The in-run model rewrite: after a rejected attempt, the next
    tool_save_reply_draft call with compliant content saves the draft."""
    store = harness._store()
    repository = InMemoryTicketRepository()
    turn_id = PersonaDraftContractGateTests._seed_review_requested_turn(store, repository, reason="enablement_auto_review")
    from backend.services.automation_hermes_tools import HermesToolError

    with patch("backend.services.automation_hermes_tools.run_engineer_guardrail_final", side_effect=_guardrail_pass):
        with pytest.raises(HermesToolError, match="stiff_negative_status"):
            tool_save_reply_draft(store, repository, turn_id=turn_id, content=PersonaDraftContractGateTests._14225_CONTENT)
        saved = tool_save_reply_draft(store, repository, turn_id=turn_id, content=PersonaDraftContractGateTests.NATURAL_AUTO)
    assert saved["draft_id"]
    with store._lock:
        assert len(store._hermes_drafts) == 1


def test_draft_gate_leaves_other_outcomes_untouched():
    store = harness._store()
    repository = InMemoryTicketRepository()
    handoff, _, _ = tool_harness.ToolFailureHandoffTests()._seed(store)
    turn_id = handoff["turn_id"]
    store.start_hermes_agent_turn(turn_id, run_id="synthetic-run")
    store.record_hermes_turn_direction(turn_id, direction="automation", route="enablement")
    store.record_hermes_case_direction(turn_id, direction="automation", reason="invalid app id")
    store.record_hermes_turn_work(
        turn_id,
        work_result={
            "status": "workflow_completed",
            "route": "enablement",
            "outcome": "appid_invalid",
            "internal_email_send_reason": "appid_invalid_format",
            "collected_fields": {"requested_feature": "media_relay"},
        },
    )
    with store._lock:
        store._hermes_turns[turn_id]["phase"] = "persona"
    with patch("backend.services.automation_hermes_tools.run_engineer_guardrail_final", side_effect=_guardrail_pass):
        saved = tool_save_reply_draft(
            store, repository, turn_id=turn_id,
            content="Thanks for sending the App ID. Its format is not valid, so nothing was submitted; please send the App ID copied from the console.",
        )
    assert saved["draft_id"]
