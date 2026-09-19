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
    def test_save_investigation_sets_direction(self) -> None:
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
        assert binding["direction"] == "investigation"

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
        assert result["queued"] is True
        assert len(repository.deliveries) == 1
        delivery = repository.deliveries[0]
        assert delivery["source"] == "hermes"
        assert delivery["is_public"] is True
        assert delivery["immutable_content"].startswith("Hi Customer,")
        assert delivery["comments_revision"] == "rev-1"
        assert store.get_hermes_draft(draft["draft_id"])["status"] == "queued"

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
        assert result["approved"]["status"] == "approved"
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

        with patch(
            "backend.services.automation_hermes_delivery.translate_draft_for_delivery",
            return_value="Ziling，您好。\n\n我们查过了。",
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
        assert "model down" in result["error"]
        assert repository.deliveries == []
        # A retry approve re-enqueues prep (prepare_failed → preparing).
        retry = store.create_hermes_delivery_prep_job(draft["draft_id"], base_event={"provenance": {}})
        assert retry.get("status") == "preparing"
        with patch(
            "backend.services.automation_hermes_delivery.translate_draft_for_delivery",
            return_value="Ziling，您好。",
        ):
            result2 = prepare_hermes_draft_delivery(
                store, repository, draft_id=draft["draft_id"], environment="preproduction"
            )
        assert result2["status"] == "queued"
        assert repository.deliveries[0]["immutable_content"] == "Ziling，您好。"

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
