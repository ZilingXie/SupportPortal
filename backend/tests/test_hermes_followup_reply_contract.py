"""AppID follow-up reply contracts (p2-178, plan: AppID追问RAG修复计划).

Pins the fixed behavior for mid-session customer follow-ups inside an
AI-held enablement conversation:

- the snapshot author-role read counts only PUBLIC ASSISTANT replies that
  predate the trigger comment, so a follow-up is no longer mis-normalized as
  a forbidden new-ticket follow-up (13733 turn 2 defect);
- knowledge questions and progress inquiries route to the server-controlled
  ``conversation_followup`` reply-only path (trusted RAG answer / bound
  relay status), rendered by Persona and delivered exactly once;
- RAG-unanswerable, reply failures, and ``direction=human`` complete a REAL
  human handoff (note, queue return, ownership release, pending-reply
  cancellation, owner notification) instead of silently parking the turn;
- a completed human handoff is never auto-revived by a later customer
  comment;
- a ``project_not_found`` relay result produces the dedicated customer
  reply and keeps the case automation-owned so a corrected App ID can open
  a new request version.

These tests reproduce the pre-fix defects (they fail on the unpatched
code) and pin the new contracts with mocked external boundaries.
"""

from __future__ import annotations

import json
import os
from typing import Any
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest

from backend.services.automation_ecs_contracts import (
    INTAKE_CONTRACT_VERSION,
    IntakeEventType,
    JobKind,
)
from backend.services.automation_ecs_runtime import AutomationEcsSettings
from backend.services.automation_ecs_store import InMemoryAutomationEcsStore
from backend.services.hermes_route_classifier import (
    HermesRouteClassificationError,
    normalize_hermes_route_classification,
)
from backend.services.automation_hermes_tools import tool_record_direction


def _settings(role: str = "worker") -> AutomationEcsSettings:
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


def _comment_event(
    event_id: str,
    ticket_id: str,
    comments: list[dict[str, Any]],
    trigger_comment_id: str,
) -> Any:
    from backend.services.automation_ecs_contracts import AutomationIntakeEvent

    payload: dict[str, Any] = {
        "schema_version": INTAKE_CONTRACT_VERSION,
        "event_id": event_id,
        "event_type": "comment.created",
        "occurred_at": comments[-1]["created_at"] if comments else "2026-09-24T10:05:00Z",
        "ticket": {
            "id": ticket_id,
            "status": "open",
            "subject": "Enable media relay for our project",
            "description": "I want to enable media relay.",
            "requester": {"email": "cx@example.com", "name": "Ziling"},
        },
        "comment_snapshot": {
            "source_updated_at": comments[-1]["created_at"] if comments else "2026-09-24T10:05:00Z",
            "snapshot_complete": True,
            "trigger_comment_id": trigger_comment_id,
            "comments": comments,
        },
    }
    return AutomationIntakeEvent.model_validate(payload)


def _customer_comment(comment_id: str, body: str, created_at: str) -> dict[str, Any]:
    return {
        "id": comment_id,
        "public": True,
        "author": {"email": "cx@example.com", "role": "end-user", "is_agent": False},
        "body": body,
        "created_at": created_at,
    }


def _assistant_comment(comment_id: str, body: str, created_at: str) -> dict[str, Any]:
    return {
        "id": comment_id,
        "public": True,
        "author": {"email": "agent@agora.io", "role": "agent", "is_agent": True},
        "body": body,
        "created_at": created_at,
    }


def _store() -> InMemoryAutomationEcsStore:
    store = InMemoryAutomationEcsStore(_settings())
    store.migrate()
    return store


def _open_turn(store: InMemoryAutomationEcsStore, event: Any) -> tuple[dict[str, Any], Any]:
    receipt = store.accept_intake(event, _settings().provenance())
    job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
    assert job is not None and job.execution_id == receipt.execution_id
    handoff = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")
    agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
    assert agent_job is not None
    return handoff, agent_job


def _attach_snapshot(store: InMemoryAutomationEcsStore, repository: Any, event: Any, turn_id: str) -> None:
    """Mirror the processor's pending-branch snapshot build for direct tool tests."""
    from backend.services.automation_hermes_snapshot import build_case_snapshot

    snapshot = build_case_snapshot(
        store,
        repository,
        zendesk_ticket_id=event.ticket.id,
        case_revision=1,
        current_event={
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "occurred_at": event.occurred_at.isoformat(),
            "ticket": event.ticket.model_dump(mode="json"),
            "trigger_comment_id": (
                event.comment_snapshot.trigger_comment_id
                if event.comment_snapshot is not None
                else ""
            ),
        },
    )
    store.set_hermes_turn_snapshot(turn_id, snapshot=snapshot)


def _seed_account_case(
    repository: Any,
    ticket_id: str,
    *,
    automation_status: str = "automation",
    ownership_state: str = "assigned",
    relay_request_id: str | None = None,
    relay_status: str | None = None,
) -> dict[str, Any]:
    case = {
        "account_case_id": f"AC-{ticket_id}",
        "billing_ticket_id": f"AC-{ticket_id}",
        "client_ticket_id": ticket_id,
        "processing_profile": "preproduction",
        "zendesk_ticket_id": ticket_id,
        "route": "enablement",
        "execution_action": "enablement",
        "automation_handler": "enablement",
        "automation_status": automation_status,
        "collected_fields": {"app_id": "4b7634a0d0f1418b8135918292f6a507"},
        "missing_fields": [],
        "automation_context": {
            "zendesk_ownership": {
                "state": ownership_state,
                "source_group_id": "360000000001",
                "assignee_id": "380000000001",
            },
        },
        "created_at": "2026-09-24T09:00:00Z",
        "updated_at": "2026-09-24T09:00:00Z",
    }
    if relay_request_id:
        case["automation_context"]["enablement_auto_workflow"] = {
            "state": "awaiting_public_reply",
            "request_id": relay_request_id,
            "request_version": 1,
            "app_id": "8cb7aea984c4457daad802e6960e2475",
        }
        repository.create_enablement_relay_request(
            request_id=relay_request_id,
            account_case_id=f"AC-{ticket_id}",
            ticket_id=ticket_id,
            zendesk_ticket_id=ticket_id,
            customer_email="cx@example.com",
            app_id="8cb7aea984c4457daad802e6960e2475",
            request_version=1,
            workflow_mode="archer",
            reply_job_id="job-1",
            target_params={},
            relay_task_expires_at="2026-10-01T00:00:00Z",
            now="2026-09-24T09:30:00Z",
        )
        if relay_status:
            repository._enablement_relay_requests[relay_request_id]["status"] = relay_status
    repository.save_account_case(dict(case))
    return case


def _knowledge_question_classification(**extra: Any) -> dict[str, Any]:
    payload = {
        "intent_class": "conversation",
        "conversation_action": "follow_up",
        "conversation_subcategory": "knowledge_question",
        "intent_confidence": 0.95,
        "action_confidence": 0.95,
        "agora_confidence": 0.95,
        "confidence": 0.95,
        "agora_route": None,
        "reason_code": "conversation_follow_up",
    }
    payload.update(extra)
    return payload


def _progress_inquiry_classification(**extra: Any) -> dict[str, Any]:
    return _knowledge_question_classification(conversation_subcategory="progress_inquiry")


# ---------------------------------------------------------------------------
# Phase 1: classification normalization
# ---------------------------------------------------------------------------


class TestFollowupClassificationNormalization:
    def test_knowledge_question_followup_routes_to_reply_only_path(self) -> None:
        result = normalize_hermes_route_classification(
            _knowledge_question_classification(),
            latest_assistant_message_present=True,
        )
        assert result["direction"] == "automation"
        assert result["route"] == "conversation_followup"
        assert result["conversation_subcategory"] == "knowledge_question"
        assert result["route_reason_code"] == "conversation_followup_knowledge_question"
        assert result["automation_eligibility"] == "eligible"

    def test_progress_inquiry_followup_routes_to_reply_only_path(self) -> None:
        result = normalize_hermes_route_classification(
            _progress_inquiry_classification(),
            latest_assistant_message_present=True,
        )
        assert result["direction"] == "automation"
        assert result["route"] == "conversation_followup"
        assert result["conversation_subcategory"] == "progress_inquiry"
        assert result["route_reason_code"] == "conversation_followup_progress_inquiry"

    def test_priority_request_stays_human(self) -> None:
        result = normalize_hermes_route_classification(
            _knowledge_question_classification(conversation_subcategory="priority_request"),
            latest_assistant_message_present=True,
        )
        assert result["direction"] == "human"
        assert result["route_reason_code"] == "conversation_priority_request"

    def test_untagged_followup_stays_human(self) -> None:
        result = normalize_hermes_route_classification(
            _knowledge_question_classification(conversation_subcategory=None),
            latest_assistant_message_present=True,
        )
        assert result["direction"] == "human"
        assert result["route"] == "follow_up"

    def test_followup_without_assistant_history_still_forbidden(self) -> None:
        result = normalize_hermes_route_classification(
            _knowledge_question_classification(),
            latest_assistant_message_present=False,
        )
        assert result["direction"] == "human"
        assert result["route_reason_code"] == "new_ticket_conversation_follow_up_forbidden"

    def test_invalid_conversation_subcategory_rejected(self) -> None:
        with pytest.raises(HermesRouteClassificationError) as exc_info:
            normalize_hermes_route_classification(
                _knowledge_question_classification(conversation_subcategory="chitchat"),
                latest_assistant_message_present=True,
            )
        assert exc_info.value.code == "invalid_conversation_subcategory"

    def test_backend_operation_query_verb_is_not_a_new_enablement(self) -> None:
        result = normalize_hermes_route_classification(
            {
                "intent_class": "agora",
                "agora_route": "backend_operation",
                "backend_operation_subcategory": "enablement",
                "backend_operation": {
                    "action": "check",
                    "target": "media_relay",
                    "evidence": "any update on the enablement? could it be faster?",
                },
                "intent_confidence": 0.95,
                "agora_confidence": 0.95,
                "confidence": 0.95,
                "reason_code": "registered_enablement",
            }
        )
        assert result["direction"] == "human"
        assert result["route_reason_code"] == "backend_operation_non_execution_verb"

    def test_backend_operation_execution_verb_still_eligible(self) -> None:
        result = normalize_hermes_route_classification(
            {
                "intent_class": "agora",
                "agora_route": "backend_operation",
                "backend_operation_subcategory": "enablement",
                "backend_operation": {
                    "action": "enable",
                    "target": "media_relay",
                    "evidence": "Please enable media relay for our project.",
                },
                "intent_confidence": 0.95,
                "agora_confidence": 0.95,
                "confidence": 0.95,
                "reason_code": "registered_enablement",
            }
        )
        assert result["direction"] == "automation"
        assert result["route"] == "enablement"


# ---------------------------------------------------------------------------
# Phase 1: snapshot author-role read + state gates in tool_record_direction
# ---------------------------------------------------------------------------


class TestRecordDirectionAuthorRoleAndGates:
    def _turn_with_snapshot(self, store: InMemoryAutomationEcsStore, repository: Any, ticket_id: str) -> dict[str, Any]:
        comments = [
            _customer_comment("90", "I want to enable media relay.", "2026-09-24T10:00:00Z"),
            _assistant_comment(
                "91",
                "Hi Ziling, could you share the project's App ID so I can proceed?",
                "2026-09-24T10:01:00Z",
            ),
            _customer_comment(
                "92", "What is the App ID? I am not sure where to find it.", "2026-09-24T10:05:00Z"
            ),
        ]
        event = _comment_event(
            f"zendesk:ticket:{ticket_id}:comment:92", ticket_id, comments, "92"
        )
        handoff, _job = _open_turn(store, event)
        _attach_snapshot(store, repository, event, handoff["turn_id"])
        return handoff

    def test_author_role_read_counts_prior_public_assistant_reply(self) -> None:
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        store = _store()
        repository = InMemoryTicketRepository()
        _seed_account_case(repository, "501")
        handoff = self._turn_with_snapshot(store, repository, "501")
        result = tool_record_direction(
            store,
            repository,
            turn_id=handoff["turn_id"],
            direction="automation",
            reason="conversation follow-up",
            classification=_knowledge_question_classification(),
        )
        assert result["direction"] == "automation"
        assert result["route"] == "conversation_followup"
        turn = store.get_hermes_turn(handoff["turn_id"])
        assert turn["direction"] == "automation"
        assert turn["route"] == "conversation_followup"

    def test_assistant_reply_after_trigger_comment_does_not_count(self) -> None:
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        store = _store()
        repository = InMemoryTicketRepository()
        _seed_account_case(repository, "502")
        comments = [
            _customer_comment("90", "I want to enable media relay.", "2026-09-24T10:00:00Z"),
            _customer_comment(
                "92", "What is the App ID? I am not sure where to find it.", "2026-09-24T10:05:00Z"
            ),
            _assistant_comment("93", "later agent reply", "2026-09-24T10:06:00Z"),
        ]
        event = _comment_event(
            "zendesk:ticket:502:comment:92", "502", comments, "92"
        )
        handoff, _job = _open_turn(store, event)
        _attach_snapshot(store, repository, event, handoff["turn_id"])
        result = tool_record_direction(
            store,
            repository,
            turn_id=handoff["turn_id"],
            direction="human",
            reason="conversation follow-up",
            classification=_knowledge_question_classification(),
        )
        # No assistant reply BEFORE the trigger comment: the follow-up is
        # still forbidden (new-ticket rule) and must not reach the reply path.
        assert result["direction"] == "human"
        assert (
            result["classification"]["route_reason_code"]
            == "new_ticket_conversation_follow_up_forbidden"
        )

    def _comment_turn(self, store, repository, ticket_id, comments, trigger):
        event = _comment_event(
            f"zendesk:ticket:{ticket_id}:comment:{trigger}", ticket_id, comments, trigger
        )
        handoff, _job = _open_turn(store, event)
        _attach_snapshot(store, repository, event, handoff["turn_id"])
        return handoff

    def _gated_turn_comments(self):
        return [
            _customer_comment("90", "I want to enable media relay.", "2026-09-24T10:00:00Z"),
            _assistant_comment("91", "please share your App ID", "2026-09-24T10:01:00Z"),
            _customer_comment("92", "What is the App ID?", "2026-09-24T10:05:00Z"),
        ]

    def test_state_gate_failure_corrects_to_human_with_reason(self) -> None:
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        store = _store()
        repository = InMemoryTicketRepository()
        # Case already handed to a human: the reply-only path must not run.
        _seed_account_case(repository, "503", automation_status="human_review_required",
                           ownership_state="released_to_queue")
        handoff = self._comment_turn(store, repository, "503", self._gated_turn_comments(), "92")
        result = tool_record_direction(
            store,
            repository,
            turn_id=handoff["turn_id"],
            direction="automation",
            reason="conversation follow-up",
            classification=_knowledge_question_classification(),
        )
        assert result["direction"] == "human"
        turn = store.get_hermes_turn(handoff["turn_id"])
        assert turn["direction"] == "human"
        assert turn["route"] is None
        classification = result["classification"]
        assert classification["server_correction_reason"]
        assert classification["hermes_proposed_direction"] == "automation"

    def test_progress_inquiry_requires_bound_active_relay_request(self) -> None:
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        store = _store()
        repository = InMemoryTicketRepository()
        _seed_account_case(repository, "504", relay_request_id="enr-AC-504-v1",
                           relay_status="dispatched")
        handoff = self._comment_turn(store, repository, "504", self._gated_turn_comments(), "92")
        result = tool_record_direction(
            store,
            repository,
            turn_id=handoff["turn_id"],
            direction="automation",
            reason="progress inquiry",
            classification=_progress_inquiry_classification(),
        )
        assert result["direction"] == "automation"
        assert result["route"] == "conversation_followup"

    def test_progress_inquiry_with_terminal_relay_request_corrects_to_human(self) -> None:
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        store = _store()
        repository = InMemoryTicketRepository()
        _seed_account_case(repository, "505", relay_request_id="enr-AC-505-v1",
                           relay_status="failed")
        handoff = self._comment_turn(store, repository, "505", self._gated_turn_comments(), "92")
        result = tool_record_direction(
            store,
            repository,
            turn_id=handoff["turn_id"],
            direction="automation",
            reason="progress inquiry",
            classification=_progress_inquiry_classification(),
        )
        assert result["direction"] == "human"
        assert "followup_progress_state_unavailable" in result["classification"][
            "server_correction_reason"
        ]

    def test_stale_trigger_comment_fails_the_gate(self) -> None:
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        store = _store()
        repository = InMemoryTicketRepository()
        _seed_account_case(repository, "506")
        comments = [
            _customer_comment("90", "I want to enable media relay.", "2026-09-24T10:00:00Z"),
            _assistant_comment("91", "please share your App ID", "2026-09-24T10:01:00Z"),
            _customer_comment("92", "What is the App ID?", "2026-09-24T10:05:00Z"),
            # A newer customer comment already exists: turn for 92 is stale.
            _customer_comment("93", "Also my colleague asked about pricing.", "2026-09-24T10:07:00Z"),
        ]
        handoff = self._comment_turn(store, repository, "506", comments, "92")
        result = tool_record_direction(
            store,
            repository,
            turn_id=handoff["turn_id"],
            direction="automation",
            reason="conversation follow-up",
            classification=_knowledge_question_classification(),
        )
        assert result["direction"] == "human"
        assert "followup_trigger_not_latest" in result["classification"]["server_correction_reason"]

    def test_replay_direction_keeps_registered_case_route_intact(self) -> None:
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        store = _store()
        repository = InMemoryTicketRepository()
        _seed_account_case(repository, "507", relay_request_id="enr-AC-507-v1",
                           relay_status="gated")
        handoff = self._comment_turn(store, repository, "507", self._gated_turn_comments(), "92")
        tool_record_direction(
            store,
            repository,
            turn_id=handoff["turn_id"],
            direction="automation",
            reason="progress inquiry",
            classification=_progress_inquiry_classification(),
        )
        case = repository.get_account_case_by_ticket_id("507")
        # The reply-only turn must not rewrite the case's enablement route.
        assert case["route"] == "enablement"
        assert case["execution_action"] == "enablement"
        assert case["automation_status"] == "automation"

    def test_enablement_direction_on_human_review_case_is_corrected(self) -> None:
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        store = _store()
        repository = InMemoryTicketRepository()
        _seed_account_case(repository, "508", automation_status="human_review_required",
                           ownership_state="released_to_queue")
        comments = [
            _customer_comment("90", "I want to enable media relay.", "2026-09-24T10:00:00Z"),
            _assistant_comment("91", "please share your App ID", "2026-09-24T10:01:00Z"),
            _customer_comment(
                "92",
                "My App ID is 4b7634a0d0f1418b8135918292f6a507.",
                "2026-09-24T10:05:00Z",
            ),
        ]
        handoff = self._comment_turn(store, repository, "508", comments, "92")
        result = tool_record_direction(
            store,
            repository,
            turn_id=handoff["turn_id"],
            direction="automation",
            reason="enablement",
            route="enablement",
            classification={
                "intent_class": "agora",
                "agora_route": "backend_operation",
                "backend_operation_subcategory": "enablement",
                "backend_operation": {
                    "action": "enable",
                    "target": "media_relay",
                    "evidence": "My App ID is ...",
                },
                "intent_confidence": 0.95,
                "agora_confidence": 0.95,
                "confidence": 0.95,
                "reason_code": "registered_enablement",
            },
        )
        assert result["direction"] == "human"
        assert "case_human_review_active" in result["classification"]["server_correction_reason"]


# ---------------------------------------------------------------------------
# Phase 2: restricted reply path (processor level)
# ---------------------------------------------------------------------------


class FakeRagClient:
    def __init__(self, payload: dict[str, Any] | None = None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def query(self, *, question, request_id, ticket_id=None, customer_id=None,
              ticket_context=None, timeout_seconds=None):
        self.calls.append(
            {
                "question": question,
                "request_id": request_id,
                "ticket_id": ticket_id,
                "ticket_context": ticket_context,
            }
        )
        if self.error is not None:
            raise self.error
        return self.payload


_RAG_ANSWER_PAYLOAD = {
    "decision": "answer",
    "answer": (
        "The App ID is the 32-character project identifier shown on the "
        "Project Management page of the Agora Console."
    ),
    "citations": [
        {
            "source_url": "https://docs.agora.io/en/help/general-use/app-id",
            "heading": "Get the App ID",
        }
    ],
}


class ScriptedHermesClient:
    """Client that drives the route tool on the route run and the draft tool
    on the first persona poll — the production timing of engine tool calls."""

    def __init__(self, store, repository, handoff, *, route_call, draft_content=None,
                 after_draft=None):
        self.store = store
        self.repository = repository
        self.handoff = handoff
        self.route_call = route_call
        self.draft_content = draft_content
        self.after_draft = after_draft
        self.run_counter = 0
        self.submissions: list[dict[str, Any]] = []

    def start_run(self, *, session_id, instructions, input_text, idempotency_key,
                  workspace_key=None, enabled_toolsets=None):
        self.run_counter += 1
        run_id = f"run-{self.run_counter}"
        self.submissions.append(
            {
                "run_id": run_id,
                "instructions": instructions,
                "input_text": input_text,
                "idempotency_key": idempotency_key,
                "toolsets": list(enabled_toolsets or []),
                "phase": idempotency_key.rsplit(":", 1)[-1],
            }
        )
        phase = idempotency_key.rsplit(":", 1)[-1]
        if phase == "route":
            self.route_call()
        return {"run_id": run_id, "status": "started", "replayed": False}

    def get_run(self, run_id):
        phase = "route" if run_id == "run-1" else "persona"
        if phase == "persona" and self.draft_content is not None and not getattr(self, "_drafted", False):
            self._drafted = True
            from backend.services.automation_hermes_tools import tool_save_reply_draft

            tool_save_reply_draft(
                self.store,
                self.repository,
                turn_id=self.handoff["turn_id"],
                content=self.draft_content,
            )
            if self.after_draft is not None:
                self.after_draft()
        return {"run_id": run_id, "status": "completed", "output": "ok"}

    def stop_run(self, run_id):
        return {"run_id": run_id, "status": "stopping"}


def _seed_ticket_mirror(repository: Any, ticket_id: str) -> None:
    repository.save_ticket(
        {
            "ticket_id": ticket_id,
            "customer_id": "cx@example.com",
            "requester": "cx@example.com",
            "subject": "Enable media relay for our project",
            "status": "open",
            "created_at": "2026-09-24T10:00:00Z",
            "updated_at": "2026-09-24T10:00:00Z",
            "messages": [
                {
                    "role": "customer",
                    "content": "I want to enable media relay.",
                    "created_at": "2026-09-24T10:00:00Z",
                }
            ],
        },
        new_messages=[],
    )


def _question_turn(store, repository, ticket_id: str):
    comments = [
        _customer_comment("90", "I want to enable media relay.", "2026-09-24T10:00:00Z"),
        _assistant_comment("91", "please share your App ID", "2026-09-24T10:01:00Z"),
        _customer_comment(
            "92", "What is the App ID? I am not sure where to find it.", "2026-09-24T10:05:00Z"
        ),
    ]
    event = _comment_event(f"zendesk:ticket:{ticket_id}:comment:92", ticket_id, comments, "92")
    handoff, _job = _open_turn(store, event)
    return handoff, _job, event


def _processor(store, repository, client, rag_client=None):
    from backend.services.automation_hermes_agent import HermesAgentTurnProcessor

    return HermesAgentTurnProcessor(
        store,
        client=client,
        environment="preproduction",
        repository=repository,
        poll_interval_seconds=0.01,
        rag_client=rag_client,
    )


@contextmanager
def _handoff_patches():
    from types import SimpleNamespace

    with (
        patch(
            "backend.services.account_human_review_escalation.read_ticket_comment_audit",
            return_value=(None, set()),
        ) as _audit_unused,
        patch(
            "backend.services.account_human_review_escalation.add_ticket_comment",
            return_value=SimpleNamespace(comment_id="53700871961876"),
        ) as note,
        patch(
            "backend.services.account_human_review_escalation.route_ticket_back_to_queue",
            return_value=SimpleNamespace(
                status="queued",
                assignee_id=None,
                group_id="360000000001",
                source_group_id="360000000001",
                status_code=200,
                updated=True,
            ),
        ) as route_back,
        patch("backend.services.account_failure_alerts.send_graph_mail") as mail,
    ):
        yield (_audit_unused, note, route_back, mail)


class TestRestrictedReplyPath:
    def test_knowledge_question_answered_once_via_persona(self) -> None:
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        store = _store()
        repository = InMemoryTicketRepository()
        _seed_account_case(repository, "601")
        _seed_ticket_mirror(repository, "601")
        handoff, agent_job, _event_payload = _question_turn(store, repository, "601")

        def route_call():
            tool_record_direction(
                store,
                repository,
                turn_id=handoff["turn_id"],
                direction="automation",
                reason="conversation follow-up",
                classification=_knowledge_question_classification(),
            )

        client = ScriptedHermesClient(
            store,
            repository,
            handoff,
            route_call=route_call,
            draft_content=(
                "You can find the App ID on the Project Management page of "
                "the Agora Console."
            ),
        )
        rag = FakeRagClient(payload=_RAG_ANSWER_PAYLOAD)
        processor = _processor(store, repository, client, rag_client=rag)
        result = processor.process(agent_job)
        assert result["status"] == "completed"
        # Work phase never reached the engine: only route + persona runs.
        phases = [s["phase"] for s in client.submissions]
        assert phases == ["route", "persona"]
        persona_input = client.submissions[1]["input_text"]
        assert "REPLY BASIS FOR THIS TURN" in persona_input
        assert "Project Management page" in persona_input
        # RAG queried exactly once, with the trigger comment as the question.
        assert len(rag.calls) == 1
        assert "What is the App ID?" in rag.calls[0]["question"]
        # Exactly one draft; the trusted references were appended.
        review = store.get_hermes_case_review("601")
        drafts = [d for d in review["drafts"] if d["turn_id"] == handoff["turn_id"]]
        assert len(drafts) == 1
        assert "docs.agora.io" in drafts[0]["content"]
        assert drafts[0]["status"] == "approved"
        assert result["publication"]["status"] == "approved"
        # No legacy reply job was created for this turn.
        assert not repository._account_reply_jobs
        # Automation ownership and the enablement route are untouched.
        case = repository.get_account_case_by_ticket_id("601")
        assert case["automation_status"] == "automation"
        assert case["route"] == "enablement"

    def test_same_comment_replay_delivers_exactly_once(self) -> None:
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        store = _store()
        repository = InMemoryTicketRepository()
        _seed_account_case(repository, "602")
        _seed_ticket_mirror(repository, "602")
        handoff, agent_job, _ = _question_turn(store, repository, "602")

        def route_call():
            tool_record_direction(
                store,
                repository,
                turn_id=handoff["turn_id"],
                direction="automation",
                reason="conversation follow-up",
                classification=_knowledge_question_classification(),
            )

        client = ScriptedHermesClient(
            store,
            repository,
            handoff,
            route_call=route_call,
            draft_content="The App ID is on the console's project page.",
        )
        rag = FakeRagClient(payload=_RAG_ANSWER_PAYLOAD)
        processor = _processor(store, repository, client, rag_client=rag)
        job = agent_job
        first = processor.process(job)
        assert first["status"] == "completed"
        replay = processor.process(job)
        assert replay.get("idempotent_replay") is True
        assert len(rag.calls) == 1
        review = store.get_hermes_case_review("602")
        assert len([d for d in review["drafts"] if d["turn_id"] == handoff["turn_id"]]) == 1

    def test_rag_unanswerable_completes_real_handoff(self) -> None:
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        store = _store()
        repository = InMemoryTicketRepository()
        _seed_account_case(repository, "603")
        _seed_ticket_mirror(repository, "603")
        handoff, agent_job, _ = _question_turn(store, repository, "603")

        def route_call():
            tool_record_direction(
                store,
                repository,
                turn_id=handoff["turn_id"],
                direction="automation",
                reason="conversation follow-up",
                classification=_knowledge_question_classification(),
            )

        client = ScriptedHermesClient(
            store, repository, handoff, route_call=route_call, draft_content=None
        )
        rag = FakeRagClient(payload={"decision": "escalate", "reason": "no_answer"})
        processor = _processor(store, repository, client, rag_client=rag)
        with _handoff_patches() as (_audit, note, route_back, mail):
            result = processor.process(agent_job)
        assert result["status"] == "human_review"
        assert "followup_rag_unanswerable" in str(result.get("reason") or "")
        # The handoff chain ran with verifiable evidence.
        note.assert_called_once()
        route_back.assert_called_once()
        assert mail.call_args.kwargs["subject"].startswith("[SupportPortal][Human takeover]")
        turn = store.get_hermes_turn(handoff["turn_id"])
        evidence = turn["work_result"].get("handoff_evidence", {})
        assert evidence.get("note_comment_id") == "53700871961876"
        assert evidence.get("route_back_status") == "queued"
        case = repository.get_account_case_by_ticket_id("603")
        assert case["automation_status"] == "human_review_required"
        ownership = case["automation_context"]["zendesk_ownership"]
        assert ownership["state"] == "released_to_queue"
        # No draft was ever produced for the customer.
        review = store.get_hermes_case_review("603")
        assert not [d for d in review["drafts"] if d["turn_id"] == handoff["turn_id"]]
        # The persona phase never ran.
        assert [s["phase"] for s in client.submissions] == ["route"]

    def test_progress_inquiry_answers_bound_relay_state_only(self) -> None:
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        store = _store()
        repository = InMemoryTicketRepository()
        _seed_account_case(repository, "604", relay_request_id="enr-AC-604-v1",
                           relay_status="dispatched")
        _seed_ticket_mirror(repository, "604")
        comments = [
            _customer_comment("90", "I want to enable media relay.", "2026-09-24T10:00:00Z"),
            _assistant_comment("91", "thanks, your request is in review", "2026-09-24T10:01:00Z"),
            _customer_comment(
                "92",
                "Hi, thanks for the update. If there is any possibility of getting "
                "this completed sooner we would really appreciate it.",
                "2026-09-24T10:05:00Z",
            ),
        ]
        event = _comment_event("zendesk:ticket:604:comment:92", "604", comments, "92")
        handoff, agent_job = _open_turn(store, event)

        def route_call():
            tool_record_direction(
                store,
                repository,
                turn_id=handoff["turn_id"],
                direction="automation",
                reason="progress inquiry",
                classification=_progress_inquiry_classification(),
            )

        client = ScriptedHermesClient(
            store,
            repository,
            handoff,
            route_call=route_call,
            draft_content=(
                "Thanks for checking in — your Media Relay request is with our "
                "reviewing engineer and we will follow up as soon as it completes."
            ),
        )
        processor = _processor(store, repository, client, rag_client=FakeRagClient())
        result = processor.process(agent_job)
        assert result["status"] == "completed"
        turn = store.get_hermes_turn(handoff["turn_id"])
        work = turn["work_result"]
        assert work["followup_kind"] == "progress_inquiry"
        assert work["progress"]["request_id"] == "enr-AC-604-v1"
        assert work["progress"]["raw_status"] == "dispatched"
        # No new relay request, no release, state untouched.
        request = repository.get_enablement_relay_request("enr-AC-604-v1")
        assert request["status"] == "dispatched"
        assert len(repository._enablement_relay_requests) == 1
        # The persona input carried the recorded state basis.
        persona_input = client.submissions[1]["input_text"]
        assert "recorded_state" in persona_input
        assert not repository._account_reply_jobs

    def test_stale_draft_never_publishes(self) -> None:
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        store = _store()
        repository = InMemoryTicketRepository()
        _seed_account_case(repository, "605")
        _seed_ticket_mirror(repository, "605")
        handoff, agent_job, _ = _question_turn(store, repository, "605")

        def route_call():
            tool_record_direction(
                store,
                repository,
                turn_id=handoff["turn_id"],
                direction="automation",
                reason="conversation follow-up",
                classification=_knowledge_question_classification(),
            )

        def after_draft():
            # A later turn completed (newer customer input) between the
            # draft save and the publication decision. The store getter
            # returns copies, so bump the stored binding directly.
            binding = store._hermes_bindings[(store.settings.job_namespace, "605")]
            binding["conversation_version"] = int(binding["conversation_version"]) + 2

        client = ScriptedHermesClient(
            store,
            repository,
            handoff,
            route_call=route_call,
            draft_content="The App ID is on the project page.",
            after_draft=after_draft,
        )
        rag = FakeRagClient(payload=_RAG_ANSWER_PAYLOAD)
        processor = _processor(store, repository, client, rag_client=rag)
        result = processor.process(agent_job)
        assert result["status"] == "human_review"
        assert result["reason"] == "draft_stale_before_publish"

    def test_ownership_lost_draft_never_publishes(self) -> None:
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        store = _store()
        repository = InMemoryTicketRepository()
        _seed_account_case(repository, "606")
        _seed_ticket_mirror(repository, "606")
        handoff, agent_job, _ = _question_turn(store, repository, "606")

        def route_call():
            tool_record_direction(
                store,
                repository,
                turn_id=handoff["turn_id"],
                direction="automation",
                reason="conversation follow-up",
                classification=_knowledge_question_classification(),
            )

        def after_draft():
            # A human took the ticket between the direction gate and
            # publication.
            case = repository.get_account_case_by_ticket_id("606")
            case["automation_context"]["zendesk_ownership"]["state"] = "released_to_queue"
            repository.save_account_case(case)

        client = ScriptedHermesClient(
            store,
            repository,
            handoff,
            route_call=route_call,
            draft_content="The App ID is on the project page.",
            after_draft=after_draft,
        )
        processor = _processor(store, repository, client, rag_client=FakeRagClient(payload=_RAG_ANSWER_PAYLOAD))
        result = processor.process(agent_job)
        assert result["status"] == "human_review"
        assert result["reason"] == "ownership_lost_before_publish"


def _claimed_agent_job(store):
    job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
    assert job is not None
    return job


# ---------------------------------------------------------------------------
# Phase 3: direction=human completes a real handoff; no auto-revival
# ---------------------------------------------------------------------------


class TestHumanDirectionHandoff:
    def test_priority_request_turn_completes_real_handoff(self) -> None:
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        store = _store()
        repository = InMemoryTicketRepository()
        _seed_account_case(repository, "701")
        _seed_ticket_mirror(repository, "701")
        comments = [
            _customer_comment("90", "I want to enable media relay.", "2026-09-24T10:00:00Z"),
            _assistant_comment("91", "please share your App ID", "2026-09-24T10:01:00Z"),
            _customer_comment(
                "92",
                "This is blocking our launch; please have a supervisor decide the priority.",
                "2026-09-24T10:05:00Z",
            ),
        ]
        event = _comment_event("zendesk:ticket:701:comment:92", "701", comments, "92")
        handoff, agent_job = _open_turn(store, event)

        def route_call():
            tool_record_direction(
                store,
                repository,
                turn_id=handoff["turn_id"],
                direction="human",
                reason="priority request",
                classification=_knowledge_question_classification(
                    conversation_subcategory="priority_request"
                ),
            )

        client = ScriptedHermesClient(
            store, repository, handoff, route_call=route_call, draft_content=None
        )
        processor = _processor(store, repository, client)
        with _handoff_patches() as (_audit, note, route_back, mail):
            result = processor.process(agent_job)
        assert result["status"] == "human_review"
        assert result["reason"] == "conversation_priority_request"
        handoff_result = result.get("handoff") or {}
        evidence = handoff_result.get("handoff_evidence") or {}
        assert evidence.get("note_comment_id") == "53700871961876"
        assert evidence.get("route_back_status") == "queued"
        assert evidence.get("owner_email_status") == "ok"
        assert mail.call_args.kwargs["subject"].startswith("[SupportPortal][Human takeover]")
        binding = store.get_hermes_case_binding("701")
        assert binding["status"] == "paused"
        assert binding["direction"] == "human"
        case = repository.get_account_case_by_ticket_id("701")
        assert case["automation_status"] == "human_review_required"
        # No public reply was drafted for the customer.
        review = store.get_hermes_case_review("701")
        assert not [d for d in review["drafts"] if d["turn_id"] == handoff["turn_id"]]

    def test_completed_handoff_is_not_revived_by_later_appid(self) -> None:
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        store = _store()
        repository = InMemoryTicketRepository()
        _seed_account_case(repository, "702")
        _seed_ticket_mirror(repository, "702")
        comments = [
            _customer_comment("90", "I want to enable media relay.", "2026-09-24T10:00:00Z"),
            _assistant_comment("91", "please share your App ID", "2026-09-24T10:01:00Z"),
            _customer_comment(
                "92", "What is the App ID? I am not sure where to find it.", "2026-09-24T10:05:00Z"
            ),
        ]
        event = _comment_event("zendesk:ticket:702:comment:92", "702", comments, "92")
        handoff, agent_job = _open_turn(store, event)

        def route_call():
            tool_record_direction(
                store,
                repository,
                turn_id=handoff["turn_id"],
                direction="human",
                reason="off topic",
                classification=_knowledge_question_classification(conversation_subcategory=None),
            )

        client = ScriptedHermesClient(
            store, repository, handoff, route_call=route_call, draft_content=None
        )
        processor = _processor(store, repository, client)
        with _handoff_patches() as (_audit, note, route_back, _mail):
            result = processor.process(agent_job)
            assert result["status"] == "human_review"
            notes_after_first = note.call_count

            # The customer now supplies the App ID on the same (handed-off)
            # case. The model proposes enablement automation again.
            later = [
                _customer_comment("90", "I want to enable media relay.", "2026-09-24T10:00:00Z"),
                _assistant_comment("91", "please share your App ID", "2026-09-24T10:01:00Z"),
                _customer_comment("92", "What is the App ID?", "2026-09-24T10:05:00Z"),
                _customer_comment(
                    "93",
                    "My App ID is 4b7634a0d0f1418b8135918292f6a507.",
                    "2026-09-24T10:20:00Z",
                ),
            ]
            event2 = _comment_event("zendesk:ticket:702:comment:93", "702", later, "93")
            handoff2, agent_job2 = _open_turn(store, event2)

            def route_call2():
                corrected = tool_record_direction(
                    store,
                    repository,
                    turn_id=handoff2["turn_id"],
                    direction="automation",
                    reason="enablement",
                    route="enablement",
                    classification={
                        "intent_class": "agora",
                        "agora_route": "backend_operation",
                        "backend_operation_subcategory": "enablement",
                        "backend_operation": {
                            "action": "enable",
                            "target": "media_relay",
                            "evidence": "My App ID is ...",
                        },
                        "intent_confidence": 0.95,
                        "agora_confidence": 0.95,
                        "confidence": 0.95,
                        "reason_code": "registered_enablement",
                    },
                )
                assert corrected["direction"] == "human"

            client2 = ScriptedHermesClient(
                store, repository, handoff2, route_call=route_call2, draft_content=None
            )
            processor2 = _processor(store, repository, client2)
            result2 = processor2.process(agent_job2)
            assert result2["status"] == "human_review"
            assert "case_human_review_active" in str(result2.get("reason") or "")
            # The ticket was never re-claimed and the note did not duplicate:
            # the recorded handoff short-circuits a replay.
            assert note.call_count == notes_after_first
            case = repository.get_account_case_by_ticket_id("702")
            ownership = case["automation_context"]["zendesk_ownership"]
            assert ownership["state"] == "released_to_queue"


# ---------------------------------------------------------------------------
# Isolated-PostgreSQL verification of the persistent turn/reply state
# ---------------------------------------------------------------------------

_PG_DSN = str(os.getenv("TICKET_DB_DSN") or "").strip() or "postgresql://localhost:5432/postgres"


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL followup-reply tests",
)
class TestFollowupReplyPostgres:
    @pytest.fixture()
    def pg_store(self) -> Any:
        import psycopg
        from uuid import uuid4

        from backend.services.automation_ecs_store import PostgresAutomationEcsStore

        schema = f"test_hermes_followup_preproduction_{uuid4().hex[:12]}"
        with patch.dict(
            os.environ,
            {
                "AUTOMATION_ENVIRONMENT": "preproduction",
                "AUTOMATION_DB_SCHEMA": schema,
                "AUTOMATION_DB_RESOURCE_ID": "rds-preproduction",
                "AUTOMATION_JOB_NAMESPACE": f"automation.{schema}",
                "AUTOMATION_INTAKE_SHARED_TOKEN": "secret",
                "AUTOMATION_RUNTIME_ALLOW_MEMORY": "0",
                "AUTOMATION_RELEASE_ID": "r1",
                "AUTOMATION_IMAGE_DIGEST": "sha256:" + "a" * 64,
                "APP_BUILD_REF": "abc123",
                "PROMPT_RELEASE_ID": "prompt-1",
                "AUTOMATION_DB_MIGRATION_DSN": _PG_DSN,
                "AUTOMATION_DB_DSN": _PG_DSN,
            },
            clear=False,
        ):
            settings = AutomationEcsSettings.from_env("worker")  # type: ignore[arg-type]
        with psycopg.connect(_PG_DSN, autocommit=True) as connection:
            connection.execute(f'CREATE SCHEMA "{schema}"')
        postgres_store = PostgresAutomationEcsStore(settings)
        try:
            postgres_store.migrate()
            yield postgres_store
        finally:
            with psycopg.connect(_PG_DSN, autocommit=True) as connection:
                connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    def _pg_case(self, repository: Any, ticket_id: str) -> None:
        # The PG contract enforces the ticket -> account case foreign key.
        _seed_ticket_mirror(repository, ticket_id)
        _seed_account_case(repository, ticket_id)

    @pytest.fixture()
    def pg_repository(self) -> Any:
        import psycopg
        from uuid import uuid4

        from backend.repositories.ticket_repository import PostgresTicketRepository

        schema = f"account_followup_{uuid4().hex[:12]}"
        repository = PostgresTicketRepository(dsn=_PG_DSN, schema=schema, migration_dsn=_PG_DSN)
        repository.initialize()
        try:
            yield repository
        finally:
            with psycopg.connect(_PG_DSN, autocommit=True) as connection:
                connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    def test_knowledge_question_turn_persists_and_replays_once(self, pg_store: Any, pg_repository: Any) -> None:
        repository = pg_repository
        self._pg_case(repository, "901")
        handoff, agent_job, event = _question_turn(pg_store, repository, "901")

        def route_call() -> None:
            result = tool_record_direction(
                pg_store,
                repository,
                turn_id=handoff["turn_id"],
                direction="automation",
                reason="conversation follow-up",
                classification=_knowledge_question_classification(),
            )
            assert result["route"] == "conversation_followup"

        client = ScriptedHermesClient(
            pg_store,
            repository,
            handoff,
            route_call=route_call,
            draft_content="The App ID is on the console's Project Management page.",
        )
        rag = FakeRagClient(payload=_RAG_ANSWER_PAYLOAD)
        processor = _processor(pg_store, repository, client, rag_client=rag)
        result = processor.process(agent_job)
        assert result["status"] == "completed"
        review = pg_store.get_hermes_case_review("901")
        drafts = [d for d in review["drafts"] if d["turn_id"] == handoff["turn_id"]]
        assert len(drafts) == 1
        assert "docs.agora.io" in drafts[0]["content"]
        turn = pg_store.get_hermes_turn(handoff["turn_id"])
        assert turn["work_result"]["followup_kind"] == "knowledge_question"
        # Same-comment replay: the persisted terminal turn replays exactly
        # once with no second RAG query or draft.
        replay = processor.process(agent_job)
        assert replay.get("idempotent_replay") is True
        assert len(rag.calls) == 1
        review = pg_store.get_hermes_case_review("901")
        assert len([d for d in review["drafts"] if d["turn_id"] == handoff["turn_id"]]) == 1

    def test_rag_escalation_persists_handoff_state(self, pg_store: Any, pg_repository: Any) -> None:
        repository = pg_repository
        self._pg_case(repository, "902")
        handoff, agent_job, _event = _question_turn(pg_store, repository, "902")

        def route_call() -> None:
            tool_record_direction(
                pg_store,
                repository,
                turn_id=handoff["turn_id"],
                direction="automation",
                reason="conversation follow-up",
                classification=_knowledge_question_classification(),
            )

        client = ScriptedHermesClient(
            pg_store, repository, handoff, route_call=route_call, draft_content=None
        )
        rag = FakeRagClient(payload={"decision": "escalate", "reason": "no_answer"})
        processor = _processor(pg_store, repository, client, rag_client=rag)
        with _handoff_patches() as (_audit, note, route_back, mail):
            result = processor.process(agent_job)
        assert result["status"] == "human_review"
        turn = pg_store.get_hermes_turn(handoff["turn_id"])
        evidence = turn["work_result"]["handoff_evidence"]
        assert evidence["note_comment_id"] == "53700871961876"
        assert evidence["route_back_status"] == "queued"
        binding = pg_store.get_hermes_case_binding("902")
        assert binding["status"] == "paused"
        assert binding["direction"] == "human"
        case = repository.get_account_case_by_ticket_id("902")
        assert case["automation_status"] == "human_review_required"
