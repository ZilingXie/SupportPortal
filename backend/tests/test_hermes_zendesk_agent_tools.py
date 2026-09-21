"""Hermes agent business-tool behavior tests (context, direction, drafts, publish)."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from backend.services.automation_ecs_contracts import INTAKE_CONTRACT_VERSION
from backend.services.automation_ecs_runtime import AutomationEcsSettings
from backend.services.automation_ecs_store import InMemoryAutomationEcsStore
from backend.services.automation_hermes_delivery import (
    approve_and_queue_hermes_draft,
    queue_hermes_draft_delivery,
)
from backend.services.automation_hermes_tools import (
    HermesToolError,
    apply_greeting_projection,
    derive_publish_policy,
    publication_decision_for_turn,
    tool_escalate_human,
    tool_get_case_context,
    tool_record_direction,
    tool_save_investigation_progress,
    tool_save_reply_draft,
)


def _settings(role: str = "api") -> AutomationEcsSettings:
    env = {
        "AUTOMATION_ENVIRONMENT": "preproduction",
        "AUTOMATION_DB_SCHEMA": "supportportal_preproduction",
        "AUTOMATION_DB_RESOURCE_ID": "rds-preproduction",
        "AUTOMATION_JOB_NAMESPACE": "automation.preproduction",
        "AUTOMATION_INTAKE_SHARED_TOKEN": "secret",
        "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1",
        "AUTOMATION_RELEASE_ID": "r1",
        "AUTOMATION_IMAGE_DIGEST": "sha256:" + "a" * 64,
        "APP_BUILD_REF": "abc123",
        "PROMPT_RELEASE_ID": "prompt-1",
    }
    with patch.dict(os.environ, env, clear=True):
        return AutomationEcsSettings.from_env(role)  # type: ignore[arg-type]


class FakeRepository:
    def __init__(self) -> None:
        self.account_cases: dict[str, dict[str, Any]] = {}
        self.tickets: dict[str, dict[str, Any]] = {}
        self.deliveries: list[dict[str, Any]] = []
        self.comment_revisions: dict[str, str] = {}

    def get_account_case_by_ticket_id(self, ticket_id: str) -> dict[str, Any] | None:
        return self.account_cases.get(ticket_id)

    def save_account_case(self, case: dict[str, Any]) -> None:
        self.account_cases[str(case.get("client_ticket_id"))] = case

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        return self.tickets.get(ticket_id)

    def get_account_case_comment_sync(self, client_ticket_id: str) -> dict[str, Any]:
        return {"comments_revision": self.comment_revisions.get(client_ticket_id, "rev-1")}

    def create_account_zendesk_comment_delivery(
        self, *, created_at: str, **kwargs: Any
    ) -> dict[str, Any]:
        # created_at is keyword-only required on the Postgres repository; the
        # fake mirrors that contract so callers cannot omit it silently.
        self.deliveries.append({"created_at": created_at, **kwargs})
        return {"created": True, "created_at": created_at, **kwargs}


def _setup_case() -> tuple[InMemoryAutomationEcsStore, FakeRepository, str]:
    from backend.services.automation_ecs_contracts import AutomationIntakeEvent

    store = InMemoryAutomationEcsStore(_settings())
    store.migrate()
    event = AutomationIntakeEvent.model_validate(
        {
            "schema_version": INTAKE_CONTRACT_VERSION,
            "event_id": "zendesk:ticket:123:created",
            "event_type": "ticket.created",
            "occurred_at": "2026-09-08T10:00:00Z",
            "ticket": {
                "id": "123",
                "status": "open",
                "subject": "Enable Media Relay",
                "description": "Please enable Media Relay.",
                "requester": {"email": "cx@example.com", "name": "Customer"},
            },
        }
    )
    store.accept_intake(event, _settings().provenance())
    job = store.claim_job(
        __import__("backend.services.automation_ecs_contracts", fromlist=["JobKind"]).JobKind.ROUTE,
        worker_id="route-1",
        lease_seconds=60,
    )
    assert job is not None
    handoff = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")
    repository = FakeRepository()
    repository.tickets["123"] = {
        "ticket_id": "123",
        "subject": "Enable Media Relay",
        "status": "open",
        "customer_id": "cx@example.com",
        "messages": [{"role": "customer", "content": "Please enable Media Relay.", "created_at": "2026-09-08T10:00:00Z"}],
    }
    repository.account_cases["123"] = {
        "account_case_id": "AC-123",
        "client_ticket_id": "123",
        "zendesk_ticket_id": "123",
        "collected_fields": {},
        "missing_fields": [],
        "automation_status": "automation",
        "route": None,
        "automation_context": {},
    }
    return store, repository, handoff["turn_id"]


def _guardrail_pass(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {"decision": "approved_for_final_engineer_review", "blockers": []}


class TestCaseContext:
    def test_get_case_context_returns_binding_and_mirror(self) -> None:
        store, repository, turn_id = _setup_case()
        context = tool_get_case_context(store, repository, turn_id=turn_id)
        assert context["zendesk_ticket_id"] == "123"
        assert context["conversation_version"] == 0
        assert context["direction"] == "pending"
        assert context["recent_messages"][0]["content"] == "Please enable Media Relay."

    def test_inactive_turn_rejects_tools(self) -> None:
        store, repository, turn_id = _setup_case()
        store.start_hermes_agent_turn(turn_id, run_id="run-1")
        store.fail_hermes_agent_turn(turn_id, status="failed", error_code="x", error_message="x")
        with pytest.raises(HermesToolError) as excinfo:
            tool_get_case_context(store, repository, turn_id=turn_id)
        assert excinfo.value.code == "turn_not_active"


class TestDirectionTools:
    def test_record_direction_updates_binding_and_case_route(self) -> None:
        store, repository, turn_id = _setup_case()
        result = tool_record_direction(
            store, repository, turn_id=turn_id, direction="automation", reason="registered route", route="enablement"
        )
        assert result["direction"] == "automation"
        assert repository.account_cases["123"]["route"] == "enablement"

    def test_record_direction_persists_nested_classification(self) -> None:
        store, repository, turn_id = _setup_case()
        result = tool_record_direction(
            store,
            repository,
            turn_id=turn_id,
            direction="automation",
            reason="registered_enablement",
            route="enablement",
            classification={
                "intent_class": "agora",
                "intent_confidence": 0.95,
                "agora_confidence": 0.95,
                "agora_route": "backend_operation",
                "backend_operation_subcategory": "enablement",
                "backend_operation": {
                    "action": "enable",
                    "target": "media_relay",
                    "evidence": "explicit customer request",
                },
                "additional_intents": ["technical"],
                "reason_code": "registered_enablement",
            },
        )

        saved = repository.account_cases["123"]
        assert result["classification"]["backend_operation"]["target"] == "media_relay"
        assert saved["route_classification"]["additional_intents"] == ["technical"]
        assert saved["automation_status"] == "automation"

    def test_record_direction_rejects_unknown_direction(self) -> None:
        store, repository, turn_id = _setup_case()
        with pytest.raises(HermesToolError):
            tool_record_direction(store, repository, turn_id=turn_id, direction="sideways", reason="x")

    def test_escalate_human_pauses_binding(self) -> None:
        store, repository, turn_id = _setup_case()
        result = tool_escalate_human(store, repository, turn_id=turn_id, reason="quota requires human")
        assert result["status"] == "paused" and result["direction"] == "human"
        assert repository.account_cases["123"]["automation_status"] == "human_review_required"


class TestInvestigationTool:
    def test_save_investigation_does_not_flip_direction(self) -> None:
        # PR-C (13601): saving investigation progress must never implicitly
        # change the case direction; direction changes are explicit only.
        store, repository, turn_id = _setup_case()
        result = tool_save_investigation_progress(
            store,
            repository,
            turn_id=turn_id,
            summary="Reproduced the relay enablement failure.",
            evidence=[{"source": "case", "note": "app id missing"}],
            blockers=["missing app id"],
            next_steps=["ask customer for app id"],
        )
        assert result["saved"] is True
        binding = store.get_hermes_case_binding("123")
        assert binding["investigation"]["summary"].startswith("Reproduced")
        assert binding["direction"] != "investigation"

    def test_empty_summary_rejected(self) -> None:
        store, repository, turn_id = _setup_case()
        with pytest.raises(HermesToolError):
            tool_save_investigation_progress(store, repository, turn_id=turn_id, summary="  ")


class TestDraftTools:
    def test_auto_policy_requires_automation_direction(self) -> None:
        store, repository, turn_id = _setup_case()
        with patch(
            "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
            side_effect=_guardrail_pass,
        ):
            store._hermes_turns[turn_id]["phase"] = "persona"
            draft = tool_save_reply_draft(
                store, repository, turn_id=turn_id, content="Hello", basis={}
            )
        assert draft["publish_policy"] == "manual"  # server derives from direction

    def test_manual_draft_saves_guardrail_result(self) -> None:
        store, repository, turn_id = _setup_case()
        store._hermes_turns[turn_id]["phase"] = "persona"
        with patch(
            "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
            side_effect=_guardrail_pass,
        ):
            draft = tool_save_reply_draft(
                store,
                repository,
                turn_id=turn_id,
                content="We are looking into this and will reply within 24 hours.",
                basis={"summary": "investigating"},
            )
        assert draft["publish_policy"] == "manual" and draft["guardrail_decision"] == "approved_for_final_engineer_review"

    def test_draft_readiness_derives_from_recorded_work(self) -> None:
        captured: dict[str, Any] = {}

        def capture_guardrail(*, draft_customer_reply, reply_readiness, **kwargs):
            captured.update(reply_readiness)
            return _guardrail_pass(draft_customer_reply)

        store, repository, turn_id = _setup_case()
        tool_save_investigation_progress(
            store,
            repository,
            turn_id=turn_id,
            summary="Reproduced the relay enablement failure.",
            evidence=[],
            blockers=[],
            next_steps=["ask for app id"],
        )
        store._hermes_turns[turn_id]["phase"] = "persona"
        with patch(
            "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
            side_effect=capture_guardrail,
        ):
            tool_save_reply_draft(store, repository, turn_id=turn_id, content="Draft", basis={})
        assert captured["ready_for_customer_reply"] is True
        assert captured["summary"].startswith("Reproduced")

        # no recorded work (fresh turn, no investigation, no work_result) stays blocked
        fresh_store, fresh_repository, fresh_turn = _setup_case()
        fresh_store._hermes_turns[fresh_turn]["phase"] = "persona"
        with patch(
            "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
            side_effect=capture_guardrail,
        ):
            tool_save_reply_draft(fresh_store, fresh_repository, turn_id=fresh_turn, content="Draft", basis={})
        assert captured["ready_for_customer_reply"] is False

    def _persona_draft(self, store, repository, turn_id, *, content="Draft", guardrail=None):
        store._hermes_turns[turn_id]["phase"] = "persona"
        with patch(
            "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
            side_effect=guardrail or _guardrail_pass,
        ):
            return tool_save_reply_draft(
                store, repository, turn_id=turn_id, content=content, basis={}
            )

    def test_publication_gate_blocks_auto_when_guardrail_fails(self) -> None:
        store, repository, turn_id = _setup_case()
        tool_record_direction(store, repository, turn_id=turn_id, direction="automation", reason="enablement")
        blocked = {"decision": "blocked", "blockers": ["No draft customer reply provided."]}
        draft = self._persona_draft(store, repository, turn_id, guardrail=lambda *a, **k: blocked)
        assert draft["guardrail_decision"] == "blocked"
        result = publication_decision_for_turn(
            store, repository, turn_id=turn_id, environment="preproduction"
        )
        assert result["status"] == "human_review" and result["reason"] == "guardrail_blocked"
        assert store.get_hermes_turn(turn_id)["status"] == "failed"

    def test_publication_gate_auto_queues_ledger_delivery(self) -> None:
        store, repository, turn_id = _setup_case()
        tool_record_direction(
            store, repository, turn_id=turn_id, direction="automation", reason="enablement", route="enablement"
        )
        draft = self._persona_draft(store, repository, turn_id, content="Please share your App ID.")
        result = publication_decision_for_turn(
            store, repository, turn_id=turn_id, environment="preproduction"
        )
        # Auto-publish now enters async delivery preparation (p2-173 fix #3):
        # the worker translates before the ledger entry.
        assert result["status"] == "preparing"
        assert store.get_hermes_draft(draft["draft_id"])["status"] == "preparing"

    def test_publication_gate_manual_waits_for_approval(self) -> None:
        store, repository, turn_id = _setup_case()
        self._persona_draft(store, repository, turn_id)
        result = publication_decision_for_turn(
            store, repository, turn_id=turn_id, environment="preproduction"
        )
        assert result["status"] == "awaiting_approval" and result["queued"] is False
        assert repository.deliveries == []

    def test_approve_and_queue_binds_version(self) -> None:
        store, repository, turn_id = _setup_case()
        draft = self._persona_draft(store, repository, turn_id)
        store.request_hermes_draft_publish(draft["draft_id"])
        result = approve_and_queue_hermes_draft(
            store, repository, draft_id=draft["draft_id"], approver="admin", environment="preproduction"
        )
        # Post schema-009: approval starts async delivery preparation; the
        # ledger row is written by the worker after translation. With no
        # customer-language reference available here the prep stops safely.
        assert result["approved"]["status"] == "preparing"  # atomically approved→preparing
        prep = result["prep"]
        assert prep.get("status") == "preparing"
        draft_row = store.get_hermes_draft(draft["draft_id"])
        assert draft_row["status"] == "preparing"


class TestDeliveryQueue:
    def test_queue_requires_approved_draft(self) -> None:
        store, repository, turn_id = _setup_case()
        store._hermes_turns[turn_id]["phase"] = "persona"
        with patch(
            "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
            side_effect=_guardrail_pass,
        ):
            draft = tool_save_reply_draft(
                store, repository, turn_id=turn_id, content="Draft", basis={}
            )
        with pytest.raises(Exception):
            queue_hermes_draft_delivery(store, repository, draft_id=draft["draft_id"], environment="preproduction")

    def test_queue_missing_mirror_fails_closed(self) -> None:
        store, repository, turn_id = _setup_case()
        store._hermes_turns[turn_id]["phase"] = "persona"
        with patch(
            "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
            side_effect=_guardrail_pass,
        ):
            draft = tool_save_reply_draft(
                store, repository, turn_id=turn_id, content="Draft", basis={}
            )
        store.request_hermes_draft_publish(draft["draft_id"])
        repository.account_cases.pop("123")
        # The prep worker's queue step (after translation) still fails closed
        # on the missing account mirror; approval itself only enqueues the
        # prep job, so drive the preparation path directly.
        from backend.services.automation_hermes_delivery import (
            prepare_hermes_draft_delivery,
        )

        approve_and_queue_hermes_draft(
            store, repository, draft_id=draft["draft_id"], approver="admin", environment="preproduction"
        )
        store.complete_hermes_draft_prep(
            draft["draft_id"],
            delivery_content="Translated",
            delivery_language_ref="参考",
            source_revision=1,
            prompt_version=None,
        )
        with pytest.raises(Exception):
            prepare_hermes_draft_delivery(
                store, repository, draft_id=draft["draft_id"], environment="preproduction"
            )
        assert repository.deliveries == []

    def test_queue_carries_case_revision_for_continuation_turns(self) -> None:
        # Continuation turns (investigation_reply / investigation_feedback)
        # draft at conversation_version == case_revision because no intake
        # bump precedes them; the queued draft_version must still equal the
        # mirror case_revision or the sender falsely rejects the send as
        # stale. Legacy normal turns keep the identical value (their
        # conversation_version + 1 == case_revision).
        store, repository, turn_id = _setup_case()
        store._hermes_turns[turn_id]["phase"] = "persona"
        with patch(
            "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
            side_effect=_guardrail_pass,
        ):
            draft = tool_save_reply_draft(
                store, repository, turn_id=turn_id, content="Draft", basis={}
            )
        # simulate the continuation-turn shape: conversation_version caught up
        # with the case revision
        draft_row = store.get_hermes_draft(draft["draft_id"])
        store._hermes_drafts[draft["draft_id"]]["conversation_version"] = int(
            draft_row["case_revision"]
        )
        store.request_hermes_draft_publish(draft["draft_id"])
        approve_and_queue_hermes_draft(
            store, repository, draft_id=draft["draft_id"], approver="admin", environment="preproduction"
        )
        # Simulate the prep completion path (translation already done).
        store.complete_hermes_draft_prep(
            draft["draft_id"],
            delivery_content="Translated",
            delivery_language_ref="参考",
            source_revision=int(draft_row["case_revision"]),
            prompt_version=None,
        )
        queue_hermes_draft_delivery(
            store, repository, draft_id=draft["draft_id"], environment="preproduction"
        )
        delivery = repository.deliveries[0]
        mirror_revision = int(store.get_case_mirror("123")["case_revision"])
        assert delivery["draft_version"] == mirror_revision
        assert delivery["draft_version"] == int(draft_row["case_revision"])
        assert delivery["immutable_content"] == "Translated"


class TestDeliveryPreparation:
    """p2-173: post-approval translation preparation for Hermes drafts."""

    def _approved_draft(self, store, repository, turn_id, content="Hi Ziling,\n\nWe checked."):
        store._hermes_turns[turn_id]["phase"] = "persona"
        with patch(
            "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
            side_effect=_guardrail_pass,
        ):
            draft = tool_save_reply_draft(store, repository, turn_id=turn_id, content=content, basis={})
        store.request_hermes_draft_publish(draft["draft_id"])
        return draft

    def test_chinese_customer_prep_translates_and_queues(self) -> None:
        from backend.services.automation_hermes_delivery import (
            determine_delivery_language_reference,
            prepare_hermes_draft_delivery,
        )

        store, repository, turn_id = _setup_case()
        draft = self._approved_draft(store, repository, turn_id)
        # Add a Chinese customer comment → the language reference comes from it.
        from backend.services.automation_ecs_contracts import AutomationIntakeEvent

        store._comments[("123", "88")] = {
            "zendesk_ticket_id": "123",
            "zendesk_comment_id": "88",
            "comment": {
                "id": "88",
                "public": True,
                "author": {"email": "cx@example.com", "name": "客户", "role": "end-user"},
                "body": "请帮我看一下这个问题",
                "created_at": "2026-09-19T10:00:00Z",
            },
            "updated_at": "2026-09-19T10:00:00Z",
        }
        ref = determine_delivery_language_reference(store, "123")
        assert ref == "请帮我看一下这个问题"

        approve_and_queue_hermes_draft(
            store, repository, draft_id=draft["draft_id"], approver="slack-engineer", environment="preproduction"
        )
        assert store.get_hermes_draft(draft["draft_id"])["status"] == "preparing"

        from backend.services.automation_hermes_delivery import _TranslationResult

        with patch(
            "backend.services.automation_hermes_delivery.translate_draft_for_delivery",
            return_value=_TranslationResult(reference_language="non_english", translated_text="Ziling，您好。\n\n我们查过了。"),
        ) as mock_translate:
            result = prepare_hermes_draft_delivery(
                store, repository, draft_id=draft["draft_id"], environment="preproduction"
            )
        assert result["status"] == "queued"
        mock_translate.assert_called_once()
        call = mock_translate.call_args.kwargs
        assert call["language_reference"] == "请帮我看一下这个问题"
        assert "Hi " in call["english_content"] and "We checked" in call["english_content"]

        delivery = repository.deliveries[0]
        assert delivery["immutable_content"] == "Ziling，您好。\n\n我们查过了。"
        final = store.get_hermes_draft(draft["draft_id"])
        assert final["status"] == "queued"
        assert final["delivery_language_ref"] == "请帮我看一下这个问题"

    def test_no_language_reference_parks_safely(self) -> None:
        from backend.services.automation_hermes_delivery import prepare_hermes_draft_delivery

        store, repository, turn_id = _setup_case()
        draft = self._approved_draft(store, repository, turn_id)
        # Remove the ticket description so no reference is available.
        store._cases["123"]["ticket"]["description"] = ""
        approve_and_queue_hermes_draft(
            store, repository, draft_id=draft["draft_id"], approver="slack-engineer", environment="preproduction"
        )
        with patch(
            "backend.services.automation_hermes_delivery.translate_draft_for_delivery"
        ) as mock_translate:
            result = prepare_hermes_draft_delivery(
                store, repository, draft_id=draft["draft_id"], environment="preproduction"
            )
        assert result["status"] == "prepare_failed"
        assert "no customer language reference" in result["error"]
        mock_translate.assert_not_called()
        assert repository.deliveries == []
        failed = store.get_hermes_draft(draft["draft_id"])
        assert failed["status"] == "prepare_failed"
        assert failed["prep_error"]

    def test_description_fallback_when_no_customer_comments(self) -> None:
        from backend.services.automation_hermes_delivery import (
            determine_delivery_language_reference,
        )

        store, repository, turn_id = _setup_case()
        description = str(store.get_case_mirror("123")["ticket"].get("description") or "")
        ref = determine_delivery_language_reference(store, "123")
        assert ref == description

    def test_translation_failure_parks_and_retry_works(self) -> None:
        from backend.services.automation_hermes_delivery import prepare_hermes_draft_delivery

        store, repository, turn_id = _setup_case()
        draft = self._approved_draft(store, repository, turn_id)
        approve_and_queue_hermes_draft(
            store, repository, draft_id=draft["draft_id"], approver="slack-engineer", environment="preproduction"
        )
        with patch(
            "backend.services.automation_hermes_delivery.translate_draft_for_delivery",
            side_effect=RuntimeError("model down"),
        ):
            result = prepare_hermes_draft_delivery(
                store, repository, draft_id=draft["draft_id"], environment="preproduction"
            )
        assert result["status"] == "prepare_failed"
        assert "unexpected_RuntimeError" in result["error"]
        assert repository.deliveries == []
        # A retry approve re-enqueues prep (prepare_failed → preparing).
        retry = store.create_hermes_delivery_prep_job(draft["draft_id"], base_event={"provenance": {}})
        assert retry.get("status") == "preparing"
        from backend.services.automation_hermes_delivery import _TranslationResult

        with patch(
            "backend.services.automation_hermes_delivery.translate_draft_for_delivery",
            return_value=_TranslationResult(reference_language="english", translated_text="Hello Ziling, we checked."),
        ):
            result2 = prepare_hermes_draft_delivery(
                store, repository, draft_id=draft["draft_id"], environment="preproduction"
            )
        assert result2["status"] == "queued"
        # English reference: delivery content is the approved English original
        assert "Hi Customer," in repository.deliveries[0]["immutable_content"]

    def test_new_customer_input_stops_prep_before_translation(self) -> None:
        from backend.services.automation_hermes_delivery import prepare_hermes_draft_delivery

        store, repository, turn_id = _setup_case()
        draft = self._approved_draft(store, repository, turn_id)
        approve_and_queue_hermes_draft(
            store, repository, draft_id=draft["draft_id"], approver="slack-engineer", environment="preproduction"
        )
        # Bump the case revision (a new customer comment arrived).
        store._cases["123"]["case_revision"] = int(store.get_case_mirror("123")["case_revision"]) + 1
        with patch(
            "backend.services.automation_hermes_delivery.translate_draft_for_delivery"
        ) as mock_translate:
            result = prepare_hermes_draft_delivery(
                store, repository, draft_id=draft["draft_id"], environment="preproduction"
            )
        assert result["status"] == "prepare_failed"
        assert "stale_case_revision" in result["error"]
        mock_translate.assert_not_called()
        assert repository.deliveries == []

    def test_preformatted_guardrail_accepts_app_greeting(self) -> None:
        # The guardrail no longer rewrites a Hermes draft: the app-projected
        # English greeting is validated as-is (case 13602 root fix).
        from backend.services.engineer_guardrail_agent import run_engineer_guardrail_final

        packet = run_engineer_guardrail_final(
            draft_customer_reply="Hi Ziling,\n\n中文正文也应通过，因为校验原样进行。",
            reply_readiness={"ready_for_customer_reply": True},
            preformatted=True,
        )
        assert packet["decision"] == "approved_for_final_engineer_review"
        assert packet["normalized_customer_reply"].startswith("Hi Ziling,")
        # Missing greeting still blocks.
        packet2 = run_engineer_guardrail_final(
            draft_customer_reply="No greeting here.",
            reply_readiness={"ready_for_customer_reply": True},
            preformatted=True,
        )
        assert packet2["decision"] == "blocked"


class TestDeliveryPreparationFixes:
    """p2-173 review fixes: customer identity, safety validation, recovery."""

    def test_is_agent_false_end_user_is_customer(self) -> None:
        from backend.services.automation_hermes_delivery import _is_customer_author
        assert _is_customer_author({"role": "end-user", "is_agent": False}) is True
        assert _is_customer_author({"role": "end-user", "is_agent": True}) is False
        assert _is_customer_author({"role": "end-user"}) is True
        assert _is_customer_author({"role": "agent", "is_agent": False}) is False

    def test_safety_validation_blocks_unsafe_translations(self) -> None:
        from backend.services.automation_hermes_delivery import _validate_translated_content
        assert _validate_translated_content("", "original") is not None
        assert _validate_translated_content("internal use only", "normal") is not None
        assert _validate_translated_content("We guarantee 100% fix", "normal") is not None
        assert _validate_translated_content("A" * 5000, "N" * 200) is not None
        assert _validate_translated_content("Normal.", "Normal original.") is None

    def test_queued_draft_recovery_does_not_requeue(self) -> None:
        from backend.services.automation_hermes_delivery import prepare_hermes_draft_delivery
        store, repository, turn_id = _setup_case()
        store._hermes_turns[turn_id]["phase"] = "persona"
        with patch(
            "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
            side_effect=_guardrail_pass,
        ):
            draft = tool_save_reply_draft(store, repository, turn_id=turn_id, content="D", basis={})
        store.request_hermes_draft_publish(draft["draft_id"])
        store.approve_hermes_case_draft(draft["draft_id"], approver="a")
        store.create_hermes_delivery_prep_job(draft["draft_id"], base_event={})
        store.complete_hermes_draft_prep(
            draft["draft_id"], delivery_content="T", delivery_language_ref="ref",
            source_revision=1, prompt_version=None,
        )
        # Manually set to queued (simulate post-ledger crash)
        store._hermes_drafts[draft["draft_id"]]["status"] = "queued"
        result = prepare_hermes_draft_delivery(
            store, repository, draft_id=draft["draft_id"], environment="preproduction"
        )
        assert result["status"] == "queued"
        assert result.get("reused_ledger") is True

    def test_review_includes_preparing_and_prepare_failed(self) -> None:
        store, repository, turn_id = _setup_case()
        store._hermes_turns[turn_id]["phase"] = "persona"
        with patch(
            "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
            side_effect=_guardrail_pass,
        ):
            draft = tool_save_reply_draft(store, repository, turn_id=turn_id, content="D", basis={})
        store._hermes_drafts[draft["draft_id"]]["status"] = "preparing"
        review = store.get_hermes_case_review("123")
        assert any(d["status"] == "preparing" for d in review["drafts"])
        store._hermes_drafts[draft["draft_id"]]["status"] = "prepare_failed"
        review2 = store.get_hermes_case_review("123")
        assert any(d["status"] == "prepare_failed" for d in review2["drafts"])

    def test_auto_publish_draft_creates_prep_job(self) -> None:
        """Auto-approve path enters the same prep flow (issue #3)."""
        from backend.services.automation_hermes_tools import publication_decision_for_turn
        store, repository, turn_id = _setup_case()
        store._hermes_turns[turn_id]["phase"] = "persona"
        with patch(
            "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
            side_effect=_guardrail_pass,
        ):
            draft = tool_save_reply_draft(store, repository, turn_id=turn_id, content="D", basis={})
        store._hermes_drafts[draft["draft_id"]]["publish_policy"] = "auto"
        # Simulate the publication gate on a completed turn
        store.start_hermes_agent_turn(turn_id, run_id="r1")
        result = publication_decision_for_turn(
            store, repository, turn_id=turn_id, environment="preproduction",
            zendesk_side_effects_enabled=True,
        )
        assert result["status"] == "preparing", result
        assert store.get_hermes_draft(draft["draft_id"])["status"] == "preparing"


class TestTranslationValidationRound4:
    """p2-175: URL trailing punctuation + Latin diacritics detection."""

    def test_url_trailing_period_not_mismatched(self) -> None:
        from backend.services.automation_hermes_delivery import _validate_translated_content
        # English original: URL followed by period
        english = "See https://example.com/help. for more details about session 123456."
        # Chinese translation: URL followed by Chinese full stop (。)
        chinese = "请查看 https://example.com/help 。关于会话 123456 的更多详情。"
        assert _validate_translated_content(chinese, english, "中文参考") is None

    def test_url_trailing_comma_not_mismatched(self) -> None:
        from backend.services.automation_hermes_delivery import _validate_translated_content
        english = "Check https://example.com/doc, then retry."
        chinese = "请检查 https://example.com/doc，然后重试。"
        assert _validate_translated_content(chinese, english, "中文") is None

    def test_actual_missing_url_still_fails(self) -> None:
        from backend.services.automation_hermes_delivery import _validate_translated_content
        english = "See https://example.com/help for details."
        chinese = "请查看详情。"  # URL completely dropped
        result = _validate_translated_content(chinese, english, "中文")
        assert result is not None
        assert "example.com" in result

    def test_french_reference_untranslated_english_fails(self) -> None:
        from backend.services.automation_hermes_delivery import _validate_translated_content
        french_ref = "Bonjour, mon téléphone ne fonctionne pas avec l'application à distance."
        untranslated_english = "Hello, we have checked the session and found no issues."
        result = _validate_translated_content(untranslated_english, untranslated_english, french_ref, reference_language="non_english")
        assert result is not None
        assert "identical" in result.lower() or "untranslated" in result.lower()

    def test_french_reference_french_translation_passes(self) -> None:
        from backend.services.automation_hermes_delivery import _validate_translated_content
        french_ref = "Bonjour, mon téléphone ne fonctionne pas avec l'application à distance."
        french_translation = "Bonjour, nous avons vérifié la séance et n'avons trouvé aucun problème."
        english_orig = "Hello, we have checked the session and found no issues."
        assert _validate_translated_content(french_translation, english_orig, french_ref) is None

    def test_english_reference_pure_ascii_translation_passes(self) -> None:
        from backend.services.automation_hermes_delivery import _validate_translated_content
        english_ref = "Hello, my call is not working."
        english_original = "We have checked and found no issues."
        english_translation = "Hi Customer, we have checked and found no issues."
        assert _validate_translated_content(english_translation, english_original, english_ref) is None


class TestTranslationValidationRound5:
    """p2-175 round 5: identity check replaces diacritics; URL balanced-paren strip."""

    def test_identical_to_english_fails(self) -> None:
        from backend.services.automation_hermes_delivery import _validate_translated_content
        # French customer + untranslated English that happens to contain "André"
        english = "Hello André, we have checked and found no issues."
        result = _validate_translated_content(english, english, "Bonjour André", reference_language="non_english")
        assert result is not None
        assert "identical" in result.lower()

    def test_french_without_accents_translated_passes(self) -> None:
        from backend.services.automation_hermes_delivery import _validate_translated_content
        english = "Hello, we have checked and found no issues."
        french = "Bonjour, nous avons vérifié et n'avons trouvé aucun problème."
        assert _validate_translated_content(french, english, "Bonjour, mon appel ne fonctionne pas.") is None

    def test_english_customer_with_cafe_passes(self) -> None:
        from backend.services.automation_hermes_delivery import _validate_translated_content
        # English reference contains "café" — old diacritics check would block
        # because ref has é but translation (being English) has no other é.
        translated = "Hi Customer, we checked the café order and found no issues."
        english = "We checked the café order and found no issues."
        ref = "Hello, my café order has an issue."
        assert _validate_translated_content(translated, english, ref) is None

    def test_url_with_parentheses_preserved(self) -> None:
        from backend.services.automation_hermes_delivery import _validate_translated_content
        english = "See https://example.com/Guide_(RTC) for details."
        # Translation drops the closing paren — should FAIL
        chinese_bad = "请查看 https://example.com/Guide_(RTC 的详情。"
        result = _validate_translated_content(chinese_bad, english, "中文")
        assert result is not None, "dropped ) should fail"

    def test_url_with_parentheses_intact_passes(self) -> None:
        from backend.services.automation_hermes_delivery import _validate_translated_content
        english = "See https://example.com/Guide_(RTC) for details."
        chinese_good = "请查看 https://example.com/Guide_(RTC) 的详情。"
        assert _validate_translated_content(chinese_good, english, "中文") is None

    def test_url_sentence_period_still_handled(self) -> None:
        from backend.services.automation_hermes_delivery import _validate_translated_content
        english = "See https://example.com/help. for details."
        chinese = "请查看 https://example.com/help 。详情。"
        assert _validate_translated_content(chinese, english, "中文") is None

    def test_slightly_different_from_english_passes(self) -> None:
        from backend.services.automation_hermes_delivery import _validate_translated_content
        english = "Hello, we checked the session."
        translated = "Hello, we have checked the session."  # Slightly different (greeting removed)
        assert _validate_translated_content(translated, english, "Bonjour") is None


class TestDeliveryPrepBaselineRegressions:
    """p2-176 round 6 baseline regressions: must FAIL on 54ec242, pass after fix.

    These drive the real approval entry point and the prep flow end-to-end
    with an isolated store, verifying persisted state, delivery content,
    and ledger count — not just the validation function's return value.
    """

    def _approved_draft_with_ref(self, store, repository, turn_id, content, customer_comment=None):
        from backend.services.automation_hermes_tools import tool_save_reply_draft

        store._hermes_turns[turn_id]["phase"] = "persona"
        with patch(
            "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
            side_effect=_guardrail_pass,
        ):
            draft = tool_save_reply_draft(store, repository, turn_id=turn_id, content=content, basis={})
        store.request_hermes_draft_publish(draft["draft_id"])
        if customer_comment:
            store._comments[("123", "ref-comment")] = {
                "zendesk_ticket_id": "123",
                "zendesk_comment_id": "ref-comment",
                "comment": {
                    "id": "ref-comment",
                    "public": True,
                    "author": {"email": "cx@example.com", "name": "CX", "role": "end-user", "is_agent": False},
                    "body": customer_comment,
                    "created_at": "2026-09-20T10:00:00Z",
                },
                "updated_at": "2026-09-20T10:00:00Z",
            }
        else:
            # Use ticket description as language reference (English default)
            pass
        return draft

    def test_english_customer_translated_identical_queues(self) -> None:
        """English customer: model returns English original → should queue."""
        from backend.services.automation_hermes_delivery import (
            approve_and_queue_hermes_draft,
            prepare_hermes_draft_delivery,
        )

        store, repository, turn_id = _setup_case()
        english_content = "Hi Customer,\n\nWe have checked the session and found no issues."
        draft = self._approved_draft_with_ref(store, repository, turn_id, english_content)

        approve_and_queue_hermes_draft(
            store, repository, draft_id=draft["draft_id"], approver="test", environment="preproduction"
        )
        from backend.services.automation_hermes_delivery import _TranslationResult

        # Use the actual approved draft content — the save tool may have applied
        # greeting projection, so read from the store to get the persisted text.
        approved_draft = store.get_hermes_draft(draft["draft_id"])
        actual_content = str(approved_draft["content"])
        with patch(
            "backend.services.automation_hermes_delivery.translate_draft_for_delivery",
            return_value=_TranslationResult(reference_language="english", translated_text=actual_content),
        ):
            result = prepare_hermes_draft_delivery(
                store, repository, draft_id=draft["draft_id"], environment="preproduction"
            )
        assert result["status"] == "queued", f"English customer should queue, got {result}"
        assert len(repository.deliveries) == 1
        # Delivery content must be exactly the approved English original
        assert repository.deliveries[0]["immutable_content"] == actual_content

    def test_url_with_parens_and_period_queues(self) -> None:
        """URL Guide_(RTC). in English + Chinese translation with 。 → should queue."""
        from backend.services.automation_hermes_delivery import (
            approve_and_queue_hermes_draft,
            prepare_hermes_draft_delivery,
        )

        store, repository, turn_id = _setup_case()
        english_content = (
            "Hi Customer,\n\nPlease see https://example.com/Guide_(RTC). for details about session 123456."
        )
        draft = self._approved_draft_with_ref(
            store, repository, turn_id, english_content,
            customer_comment="请帮我看一下这个问题",
        )
        chinese_translation = "您好，请查看 https://example.com/Guide_(RTC)。\n关于会话 123456 的详情。"

        approve_and_queue_hermes_draft(
            store, repository, draft_id=draft["draft_id"], approver="test", environment="preproduction"
        )
        from backend.services.automation_hermes_delivery import _TranslationResult

        with patch(
            "backend.services.automation_hermes_delivery.translate_draft_for_delivery",
            return_value=_TranslationResult(reference_language="non_english", translated_text=chinese_translation),
        ):
            result = prepare_hermes_draft_delivery(
                store, repository, draft_id=draft["draft_id"], environment="preproduction"
            )
        assert result["status"] == "queued", f"Valid translation with URL should queue, got {result}"
        assert len(repository.deliveries) == 1
        assert repository.deliveries[0]["immutable_content"] == chinese_translation


class TestRound6ReviewFixes:
    """p2-176 round 6 review fixes: undetermined stop, URL peripheral punct, HTTP-level API param."""

    def _prep_with_mock(self, store, repository, turn_id, content, structured_result, customer_comment=None):
        from backend.services.automation_hermes_delivery import (
            approve_and_queue_hermes_draft,
            prepare_hermes_draft_delivery,
        )
        from backend.services.automation_hermes_tools import tool_save_reply_draft

        store._hermes_turns[turn_id]["phase"] = "persona"
        with patch(
            "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
            side_effect=_guardrail_pass,
        ):
            draft = tool_save_reply_draft(store, repository, turn_id=turn_id, content=content, basis={})
        store.request_hermes_draft_publish(draft["draft_id"])
        if customer_comment:
            store._comments[("123", "ref-c")] = {
                "zendesk_ticket_id": "123", "zendesk_comment_id": "ref-c",
                "comment": {"id": "ref-c", "public": True,
                    "author": {"email": "cx@e.com", "name": "CX", "role": "end-user", "is_agent": False},
                    "body": customer_comment, "created_at": "2026-09-20T10:00:00Z"},
                "updated_at": "2026-09-20T10:00:00Z",
            }
        approve_and_queue_hermes_draft(store, repository, draft_id=draft["draft_id"], approver="t", environment="preproduction")
        with patch(
            "backend.services.automation_hermes_delivery.translate_draft_for_delivery",
            return_value=structured_result,
        ):
            return prepare_hermes_draft_delivery(store, repository, draft_id=draft["draft_id"], environment="preproduction")

    def test_undetermined_language_parks_safely(self) -> None:
        from backend.services.automation_hermes_delivery import _TranslationResult
        store, repository, turn_id = _setup_case()
        result = self._prep_with_mock(
            store, repository, turn_id, "Hi Customer,\n\nChecked.",
            _TranslationResult(reference_language="undetermined", translated_text="Hi Customer,\n\nChecked."),
        )
        assert result["status"] == "prepare_failed"
        assert "undetermined" in result["error"]
        assert len(repository.deliveries) == 0

    def test_url_angle_bracket_peripheral_passes(self) -> None:
        from backend.services.automation_hermes_delivery import _validate_translated_content
        english = "See <https://example.com/help> for details."
        chinese = "请查看 <https://example.com/help> 的详情。"
        assert _validate_translated_content(chinese, english, "中文", reference_language="non_english") is None

    def test_url_fullwidth_paren_peripheral_passes(self) -> None:
        from backend.services.automation_hermes_delivery import _validate_translated_content
        english = "See （https://example.com/help） for details."
        chinese = "请查看 （https://example.com/help） 的详情。"
        assert _validate_translated_content(chinese, english, "中文", reference_language="non_english") is None

    def test_url_angle_sentence_end_unwrapped_translation_passes(self) -> None:
        # English autolink at sentence end; the translation drops the angle
        # brackets — the trailing ">." must be stripped for containment.
        from backend.services.automation_hermes_delivery import _validate_translated_content
        english = "See <https://example.com/help>."
        chinese = "详见 https://example.com/help。"
        assert _validate_translated_content(chinese, english, "中文", reference_language="non_english") is None

    def test_url_fullwidth_paren_sentence_end_stripped(self) -> None:
        # Full-width ） wrapping the URL is sentence punctuation, not part of
        # the URL (the pre-round-6 _URL_TRAILING_PUNCT_CJK had it; restore it).
        from backend.services.automation_hermes_delivery import _validate_translated_content
        english = "See the guide（https://example.com/help）."
        chinese = "详见 https://example.com/help。"
        assert _validate_translated_content(chinese, english, "中文", reference_language="non_english") is None

    def test_strip_trailing_punct_fullwidth_pair_protection(self) -> None:
        from backend.services.automation_hermes_delivery import _strip_trailing_punct
        # Extra full-width closer is stripped; a matched pair stays intact.
        assert _strip_trailing_punct("https://example.com/help）") == "https://example.com/help"
        assert (
            _strip_trailing_punct("https://example.com/wiki/腾讯（游戏）")
            == "https://example.com/wiki/腾讯（游戏）"
        )

    def test_translation_api_uses_text_format_not_response_format(self) -> None:
        """HTTP-level check: the request payload must use text.format, not response_format."""
        from backend.services.automation_hermes_delivery import translate_draft_for_delivery

        captured_payload = {}
        class FakeResult:
            text = '{"reference_language": "english", "translated_text": "Hello."}'
        def fake_invoke(*, profile, system_prompt, user_prompt, extra_payload=None):
            captured_payload.update(extra_payload or {})
            return FakeResult()

        with patch("backend.services.llm_factory.invoke_responses_text", side_effect=fake_invoke):
            result = translate_draft_for_delivery(english_content="Hello.", language_reference="Hello")
        assert result.reference_language == "english"
        # The correct Responses API parameter is text.format, NOT response_format
        assert "text" in captured_payload, f"text.format not in payload: {captured_payload}"
        assert captured_payload["text"]["format"]["type"] == "json_object"
        assert "response_format" not in captured_payload, f"response_format should NOT be in payload: {captured_payload}"

    def test_slack_chinese_title_converts_to_english(self) -> None:
        """Slack display: Chinese title must be converted to English via translated_text."""
        from backend.services.engineer_slack import _to_english_display
        from backend.services.automation_hermes_delivery import _TranslationResult

        with patch(
            "backend.services.automation_hermes_delivery.translate_draft_for_delivery",
            return_value=_TranslationResult(reference_language="non_english", translated_text="Zac Test"),
        ):
            result = _to_english_display("客户测试标题")
        assert result == "Zac Test"
        assert isinstance(result, str)

    def test_slack_english_title_unchanged(self) -> None:
        from backend.services.engineer_slack import _to_english_display
        from backend.services.automation_hermes_delivery import _TranslationResult

        with patch(
            "backend.services.automation_hermes_delivery.translate_draft_for_delivery",
            return_value=_TranslationResult(reference_language="english", translated_text="Zac Test"),
        ):
            result = _to_english_display("Zac Test")
        assert result == "Zac Test"
        assert isinstance(result, str)
