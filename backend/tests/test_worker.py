from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import importlib.util
import os
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest.mock import Mock, call, patch

if importlib.util.find_spec("psycopg") is None:
    raise unittest.SkipTest("psycopg is not installed in the local test environment")

import psycopg

from backend.repositories.ticket_repository import (
    ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
    InMemoryTicketRepository,
)
from backend.services.rag_qa import INSUFFICIENT_EVIDENCE_REPLY
from backend.services.account_admin import AccountPersonaUnavailableError
from backend.services.account_zendesk_internal_comment import AccountZendeskCommentResult
from backend.services.zendesk_comments import ZendeskCommentError
from backend.services.zendesk_ticket_assignment import ZendeskAssignmentResult
from backend.services.enablement_completion_classifier import (
    EnablementCompletionClassification,
)


def _classifier_regex_fallback():
    return EnablementCompletionClassification(
        completed=False, source="regex_fallback", failure_reason="disabled"
    )

if importlib.util.find_spec("redis") is None:
    redis_module = types.ModuleType("redis")
    redis_asyncio_module = types.ModuleType("redis.asyncio")

    class _FakeRedis:
        @classmethod
        def from_url(cls, *_args: object, **_kwargs: object) -> "_FakeRedis":
            return cls()

        def publish(self, *_args: object, **_kwargs: object) -> int:
            return 1

        def blpop(self, *_args: object, **_kwargs: object) -> None:
            return None

        def rpush(self, *_args: object, **_kwargs: object) -> int:
            return 1

        def close(self) -> None:
            return None

        async def aclose(self) -> None:
            return None

    redis_module.Redis = _FakeRedis
    redis_asyncio_module.Redis = _FakeRedis
    sys.modules["redis"] = redis_module
    sys.modules["redis.asyncio"] = redis_asyncio_module


def _load_worker_module():
    module_path = Path(__file__).resolve().parents[1] / "worker.py"
    spec = importlib.util.spec_from_file_location(
        "backend.tests._worker_under_test",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load backend.worker for tests")

    fake_main = types.ModuleType("backend.main")
    fake_main.build_answer = lambda *_args, **_kwargs: ("", 0.0, [], [], False)
    fake_main.build_query_task = lambda ticket_id, customer_message, message_created_at, **kwargs: {
        "task_type": "ticket_query",
        "ticket_id": ticket_id,
        "customer_message": customer_message,
        "message_created_at": message_created_at,
        **kwargs,
    }
    fake_main.resolve_support_message = lambda *_args, **_kwargs: None
    fake_main.build_client_sync_event = lambda *_args, **_kwargs: {}
    fake_main.build_engineer_followup_request = lambda *_args, **_kwargs: "follow up"
    fake_main.ensure_ticket_defaults = lambda _ticket: None
    fake_main.now_iso = lambda: "2026-03-22T00:00:00+00:00"
    fake_main._run_client_ticket_review_agent = lambda *_args, **_kwargs: None
    fake_main._record_ticket_agent_runtime_events = lambda *_args, **_kwargs: None
    fake_main.ticket_repository = Mock()
    fake_main.asset_repository = Mock()
    fake_main.asset_storage = Mock()

    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"backend.main": fake_main}):
        spec.loader.exec_module(module)
    return module


worker = _load_worker_module()


def test_account_automation_cycle_is_public_and_does_not_start_redis_consumer():
    with patch.object(worker, "_process_claimed_account_reply_jobs") as replies, patch.object(
        worker, "_drain_production_zendesk_comment_deliveries"
    ) as zendesk, patch.object(worker, "_drain_account_slack_deliveries") as slack, patch.object(
        worker, "_drain_engineer_slack_events"
    ) as engineer_slack, patch.dict(
        os.environ,
        {"ACCOUNT_DEFAULT_PROCESSING_PROFILE": "preproduction"},
        clear=False,
    ):
        worker.process_account_automation_once()

    assert replies.call_count == 2
    zendesk.assert_called_once_with(limit=20)
    slack.assert_called_once_with(limit=20)
    engineer_slack.assert_called_once_with(limit=20)


def _route_decision(*, action: str, scope_label: str, reason: str) -> types.SimpleNamespace:
    route_family = "agora_docs_rag" if action == "rag" else "web_company_info" if action == "web_search" else "fallback_or_refuse"
    tooling_profile = "agora_docs_only" if action == "rag" else "official_web_search" if action == "web_search" else "no_agora_docs_refusal"
    return types.SimpleNamespace(
        scope_label=scope_label,
        route=action,
        confidence=0.93,
        reason=reason,
        matched_signals=["token"] if action == "rag" else ["agora"],
        response_language="en",
        route_family=route_family,
        execution_action=action,
        tooling_profile=tooling_profile,
        router_source="deterministic",
        intent_router_attempted=False,
        intent_router_confidence_threshold=None,
        intent_router_model_confidence=None,
        intent_router_fallback_reason=None,
        intent_router_failure_type=None,
        intent_router_failure_source=None,
    )


def _build_ticket(
    *,
    ticket_id: str = "T-RETRY",
    customer_message: str = "Need help with token generation",
    message_created_at: str = "2026-03-22T00:00:00+00:00",
    client_intake_state: dict[str, object] | None = None,
    product_selection_state: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "ticket_id": ticket_id,
        "customer_id": "C-123",
        "requester": "Customer",
        "subject": "Token question",
        "status": "communicating",
        "created_at": "2026-03-22T00:00:00+00:00",
        "updated_at": "2026-03-22T00:00:00+00:00",
        "messages": [
            {
                "role": "customer",
                "content": customer_message,
                "created_at": message_created_at,
            },
            {
                "role": "assistant",
                "content": "I am checking the knowledge base for you now.",
                "created_at": "2026-03-22T00:00:01+00:00",
            },
        ],
        "client_intake_state": client_intake_state,
        "product_selection_state": product_selection_state,
    }


def _ownership_assigned():
    from backend.services.account_automation_ownership import OwnershipGateResult

    return OwnershipGateResult(
        eligible=True,
        state="assigned",
        assignee_id="48557297720084",
        group_id="27216254064148",
        updated_at="2026-08-19T00:00:00+00:00",
    )


def _ownership_policy_blocked(*, state: str, failure_code: str, blocking_comment_id: str | None = None):
    from backend.services.account_automation_ownership import OwnershipGateResult

    return OwnershipGateResult(
        eligible=True,
        state=state,
        assignee_id="31116634341396",
        group_id="27216254064148",
        failure_code=failure_code,
        failure_category="policy",
        blocking_comment_id=blocking_comment_id,
        updated_at="2026-08-20T07:04:00Z",
    )


def _zendesk_result(
    *,
    status: str = "added",
    comment_id: str | None = "comment-1",
    retryable: bool = False,
    error_code: str | None = None,
) -> AccountZendeskCommentResult:
    return AccountZendeskCommentResult(
        status=status,
        account_case_id="AC-TEST",
        message_id="1372",
        actor_id="system:production-account-reply",
        trigger="production_worker",
        comment_id=comment_id,
        retryable=retryable,
        error_code=error_code,
    )


class FraudReviewHandoffTests(unittest.TestCase):
    def _fraud_case(self) -> dict:
        return {
            "account_case_id": "AC-FRAUD",
            "client_ticket_id": "12895",
            "processing_profile": "production",
            "zendesk_ticket_id": "12895",
            "route_family": "automated",
            "route_status": "automated",
            "execution_action": "fraud_account",
            "automation_handler": "billing",
            "automation_context": {
                "zendesk_ownership": {
                    "state": "assigned",
                    "assignee_id": "48557297720084",
                    "group_id": "29388501432596",
                    "source_group_id": "27216253642772",
                }
            },
        }

    def test_public_fraud_delivery_hands_off_to_reviewer(self) -> None:
        repository = Mock()
        repository.get_account_case_by_ticket_id.return_value = self._fraud_case()
        repository.claim_account_zendesk_comment_delivery.return_value = {
            "claimed": True,
            "status": "pending",
            "is_public": True,
            "target_status": None,
        }
        result = _zendesk_result(comment_id="comment-fraud")
        assignment = ZendeskAssignmentResult(
            ticket_id="12895",
            assignee_id="31116634341396",
            assignee_email="xieziling@agora.io",
            assignee_name="Xie Ziling",
            group_id="27216254064148",
            previous_group_id="29388501432596",
            group_changed=True,
            status_code=200,
            already_assigned=False,
        )
        with patch.dict(os.environ, {"ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID": "31116634341396"}, clear=False), patch.object(
            worker, "ticket_repository", repository
        ), patch.object(
            worker, "ensure_production_automation_ownership", return_value=_ownership_assigned()
        ), patch.object(
            worker, "deliver_account_ai_message_as_internal_comment", return_value=result
        ), patch.object(
            worker, "assign_ticket_to_reviewer", return_value=assignment
        ) as assign_reviewer:
            worker._deliver_production_account_reply_to_zendesk(
                ticket_id="PRD-12895",
                message_id="m-fraud",
                job_id="reply-fraud",
                reply_intent="fraud_handoff_confirmation",
            )

        assign_reviewer.assert_called_once_with(
            ticket_id="12895",
            reviewer_user_id="31116634341396",
        )
        event_calls = [
            call for call in repository.record_event.call_args_list
            if call.args[1] == "zendesk_fraud_review_handoff"
        ]
        self.assertEqual(len(event_calls), 1)
        payload = event_calls[0].args[2]
        self.assertEqual(payload["state"], "assigned")
        self.assertEqual(payload["assignee_id"], "31116634341396")
        self.assertEqual(payload["case_automation_status"], "human_review_required")
        repository.save_account_case.assert_called_once()
        saved_case = repository.save_account_case.call_args.args[0]
        self.assertEqual(saved_case["automation_status"], "human_review_required")
        self.assertEqual(saved_case["route_status"], "automated")
        self.assertEqual(
            saved_case["automation_context"]["zendesk_ownership"],
            {
                "state": "human_reassigned",
                "assignee_id": "31116634341396",
                "group_id": "27216254064148",
                "failure_code": None,
                "failure_category": None,
                "zendesk_status_code": None,
                "failure_detail": None,
                "blocking_comment_id": None,
                "source_assignee_id": None,
                "source_group_id": "27216253642772",
                "handoff_status": "assigned_to_reviewer",
                "confirmed_at": None,
                "updated_at": "2026-03-22T00:00:00+00:00",
            },
        )

        repository.list_account_cases.return_value = [saved_case]
        with patch(
            "backend.services.account_human_review_escalation.escalate_account_case_to_human_review"
        ) as escalate:
            reconciled = worker.reconcile_account_human_review_queue_mismatches(
                repository=repository,
                processing_profile="production",
                timestamp="2026-03-22T00:00:02+00:00",
            )

        self.assertEqual(reconciled, [])
        escalate.assert_not_called()

    def test_public_fraud_missing_information_delivery_defers_handoff(self) -> None:
        repository = Mock()
        repository.get_account_case_by_ticket_id.return_value = self._fraud_case()
        repository.claim_account_zendesk_comment_delivery.return_value = {
            "claimed": True,
            "status": "pending",
            "is_public": True,
            "target_status": None,
        }
        with patch.dict(os.environ, {"ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID": "31116634341396"}, clear=False), patch.object(
            worker, "ticket_repository", repository
        ), patch.object(
            worker, "ensure_production_automation_ownership", return_value=_ownership_assigned()
        ), patch.object(
            worker, "deliver_account_ai_message_as_internal_comment", return_value=_zendesk_result()
        ), patch.object(
            worker, "assign_ticket_to_reviewer"
        ) as assign_reviewer:
            worker._deliver_production_account_reply_to_zendesk(
                ticket_id="PRD-12895",
                message_id="m-fraud",
                job_id="reply-fraud",
                reply_intent="request_missing_information",
            )

        assign_reviewer.assert_not_called()
        repository.record_event.assert_not_called()
        repository.save_account_case.assert_not_called()

    def test_already_assigned_reviewer_records_terminal_ownership(self) -> None:
        repository = Mock()
        account_case = self._fraud_case()
        assignment = ZendeskAssignmentResult(
            ticket_id="12895",
            assignee_id="31116634341396",
            assignee_email="xieziling@agora.io",
            assignee_name="Xie Ziling",
            group_id="27216254064148",
            previous_group_id="27216254064148",
            group_changed=False,
            status_code=200,
            already_assigned=True,
        )

        with patch.dict(
            os.environ,
            {"ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID": "31116634341396"},
            clear=False,
        ), patch.object(
            worker, "ticket_repository", repository
        ), patch.object(
            worker, "assign_ticket_to_reviewer", return_value=assignment
        ):
            worker._hand_off_fraud_review_after_public_reply(
                account_case=account_case,
                ticket_id="PRD-12895",
                job_id="reply-fraud",
                message_id="m-fraud",
                reply_intent="fraud_handoff_confirmation",
            )

        event_payload = repository.record_event.call_args.args[2]
        self.assertEqual(event_payload["state"], "already_assigned")
        saved_case = repository.save_account_case.call_args.args[0]
        ownership = saved_case["automation_context"]["zendesk_ownership"]
        self.assertEqual(saved_case["automation_status"], "human_review_required")
        self.assertEqual(saved_case["route_status"], "automated")
        self.assertEqual(ownership["state"], "human_reassigned")
        self.assertEqual(ownership["handoff_status"], "assigned_to_reviewer")
        self.assertEqual(ownership["assignee_id"], "31116634341396")
        self.assertEqual(ownership["group_id"], "27216254064148")

    def test_internal_fraud_delivery_does_not_hand_off(self) -> None:
        repository = Mock()
        repository.get_account_case_by_ticket_id.return_value = self._fraud_case()
        repository.claim_account_zendesk_comment_delivery.return_value = {
            "claimed": True,
            "status": "pending",
            "is_public": False,
            "target_status": None,
        }
        with patch.dict(os.environ, {"ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID": "31116634341396"}, clear=False), patch.object(
            worker, "ticket_repository", repository
        ), patch.object(
            worker, "ensure_production_automation_ownership", return_value=_ownership_assigned()
        ), patch.object(
            worker, "deliver_account_ai_message_as_internal_comment", return_value=_zendesk_result()
        ), patch.object(
            worker, "assign_ticket_to_reviewer"
        ) as assign_reviewer:
            worker._deliver_production_account_reply_to_zendesk(
                ticket_id="PRD-12895", message_id="m-fraud", job_id="reply-fraud"
            )

        assign_reviewer.assert_not_called()

    def test_public_non_fraud_delivery_does_not_hand_off(self) -> None:
        repository = Mock()
        repository.get_account_case_by_ticket_id.return_value = {
            "account_case_id": "AC-EN",
            "processing_profile": "production",
            "zendesk_ticket_id": "12838",
            "route_family": "automated",
            "execution_action": "enablement",
        }
        repository.claim_account_zendesk_comment_delivery.return_value = {
            "claimed": True,
            "status": "pending",
            "is_public": True,
            "target_status": None,
        }
        with patch.dict(os.environ, {"ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID": "31116634341396"}, clear=False), patch.object(
            worker, "ticket_repository", repository
        ), patch.object(
            worker, "ensure_production_automation_ownership", return_value=_ownership_assigned()
        ), patch.object(
            worker, "deliver_account_ai_message_as_internal_comment", return_value=_zendesk_result()
        ), patch.object(
            worker, "assign_ticket_to_reviewer"
        ) as assign_reviewer:
            worker._deliver_production_account_reply_to_zendesk(
                ticket_id="PRD-12838", message_id="m-en", job_id="reply-en"
            )

        assign_reviewer.assert_not_called()

    def test_handoff_failure_records_failed_event_without_failing_delivery(self) -> None:
        repository = Mock()
        repository.get_account_case_by_ticket_id.return_value = self._fraud_case()
        repository.claim_account_zendesk_comment_delivery.return_value = {
            "claimed": True,
            "status": "pending",
            "is_public": True,
            "target_status": None,
        }
        with patch.dict(os.environ, {"ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID": "31116634341396"}, clear=False), patch.object(
            worker, "ticket_repository", repository
        ), patch.object(
            worker, "ensure_production_automation_ownership", return_value=_ownership_assigned()
        ), patch.object(
            worker, "deliver_account_ai_message_as_internal_comment", return_value=_zendesk_result()
        ), patch.object(
            worker,
            "assign_ticket_to_reviewer",
            side_effect=ZendeskCommentError("permanent", status_code=422, error_code="zendesk_http_error", detail="RecordInvalid | {...}"),
        ):
            worker._deliver_production_account_reply_to_zendesk(
                ticket_id="PRD-12895",
                message_id="m-fraud",
                job_id="reply-fraud",
                reply_intent="fraud_handoff_confirmation",
            )

        repository.complete_account_zendesk_comment_delivery.assert_not_called()
        repository.save_account_case.assert_not_called()
        event_calls = [
            call for call in repository.record_event.call_args_list
            if call.args[1] == "zendesk_fraud_review_handoff"
        ]
        self.assertEqual(len(event_calls), 1)
        payload = event_calls[0].args[2]
        self.assertEqual(payload["state"], "failed")
        self.assertEqual(payload["failure_code"], "zendesk_http_error")

    def test_missing_reviewer_config_records_skipped_event(self) -> None:
        repository = Mock()
        repository.get_account_case_by_ticket_id.return_value = self._fraud_case()
        repository.claim_account_zendesk_comment_delivery.return_value = {
            "claimed": True,
            "status": "pending",
            "is_public": True,
            "target_status": None,
        }
        with patch.dict(os.environ, {"ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID": ""}, clear=False), patch.object(
            worker, "ticket_repository", repository
        ), patch.object(
            worker, "ensure_production_automation_ownership", return_value=_ownership_assigned()
        ), patch.object(
            worker, "deliver_account_ai_message_as_internal_comment", return_value=_zendesk_result()
        ), patch.object(
            worker, "assign_ticket_to_reviewer"
        ) as assign_reviewer:
            worker._deliver_production_account_reply_to_zendesk(
                ticket_id="PRD-12895",
                message_id="m-fraud",
                job_id="reply-fraud",
                reply_intent="fraud_handoff_confirmation",
            )

        assign_reviewer.assert_not_called()
        event_calls = [
            call for call in repository.record_event.call_args_list
            if call.args[1] == "zendesk_fraud_review_handoff"
        ]
        self.assertEqual(len(event_calls), 1)
        self.assertEqual(event_calls[0].args[2]["state"], "skipped")


class WorkerResilienceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = {
            "task_type": "ticket_query",
            "ticket_id": "T-RETRY",
            "customer_message": "Need help with token generation",
            "message_created_at": "2026-03-22T00:00:00+00:00",
            "created_at": "2026-03-22T00:00:01+00:00",
        }

    def test_assignment_poller_reassigns_due_cases_before_dispatching_pending_cases(self) -> None:
        calls: list[str] = []

        class _AssignmentService:
            def resolve_closed_cases(self):
                calls.append("resolve_closed")
                return [{"engineer_case_id": "CASE-CLOSED"}]

            def reassign_off_schedule_cases(self):
                calls.append("reassign_off_schedule")
                return [{"engineer_case_id": "CASE-OFF-SCHEDULE"}]

            def reassign_due_cases(self):
                calls.append("reassign_due")
                return [{"engineer_case_id": "CASE-DUE"}]

            def dispatch_pending_cases(self):
                calls.append("dispatch_pending")
                worker.SHUTTING_DOWN = True
                return [{"engineer_case_id": "CASE-PENDING"}]

        original_shutting_down = worker.SHUTTING_DOWN
        worker.SHUTTING_DOWN = False
        try:
            with patch.object(worker, "EngineerAssignmentService", return_value=_AssignmentService()):
                worker._run_engineer_assignment_poller(60.0)
        finally:
            worker.SHUTTING_DOWN = original_shutting_down

        self.assertEqual(
            calls,
            ["resolve_closed", "reassign_off_schedule", "reassign_due", "dispatch_pending"],
        )

    def test_production_reply_delivery_skips_unregistered_automation(self) -> None:
        repository = Mock()
        repository.get_account_case_by_ticket_id.return_value = {
            "account_case_id": "AC-1",
            "processing_profile": "production",
            "zendesk_ticket_id": "12807",
            "route_family": "automated",
            "execution_action": "quota",
        }
        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "deliver_account_ai_message_as_internal_comment"
        ) as deliver:
            worker._deliver_production_account_reply_to_zendesk(
                ticket_id="PRD-12807", job_id="reply-1"
            )

        repository.claim_account_zendesk_comment_delivery.assert_not_called()
        deliver.assert_not_called()

    def test_existing_unknown_delivery_uses_readback_without_resending(self) -> None:
        repository = Mock()
        repository.get_account_case_by_ticket_id.return_value = {
            "account_case_id": "AC-1",
            "processing_profile": "production",
            "zendesk_ticket_id": "12807",
            "route_family": "automated",
            "execution_action": "fraud_account",
        }
        repository.claim_account_zendesk_comment_delivery.return_value = {
            "created": False,
            "status": "outcome_unknown",
        }
        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "reconcile_account_ai_message_internal_comment",
            return_value=_zendesk_result(),
        ) as reconcile:
            worker._deliver_production_account_reply_to_zendesk(
                ticket_id="PRD-12807", job_id="reply-1"
            )

        reconcile.assert_called_once_with(
            repository=repository,
            account_case_id="AC-1",
            message_id="reply-1",
            actor_id="system:production-account-reply",
            trigger="production_worker",
            public_comment=False,
            solve_ticket=False,
        )

    def test_queued_delivery_is_claimed_once_and_written_as_public_comment(self) -> None:
        repository = Mock()
        repository.get_account_case_by_ticket_id.return_value = {
            "account_case_id": "AC-QUEUED",
            "processing_profile": "production",
            "zendesk_ticket_id": "12838",
            "route_family": "automated",
            "execution_action": "enablement",
        }
        repository.claim_account_zendesk_comment_delivery.return_value = {
            "claimed": True,
            "status": "pending",
            "is_public": True,
            "target_status": None,
        }
        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "ensure_production_automation_ownership",
            return_value=_ownership_assigned(),
        ), patch.object(
            worker,
            "deliver_account_ai_message_as_internal_comment",
            return_value=_zendesk_result(comment_id="comment-queued"),
        ) as deliver:
            worker._deliver_production_account_reply_to_zendesk(
                ticket_id="PRD-12838",
                message_id="1372",
                job_id="reply-queued",
            )

        repository.claim_account_zendesk_comment_delivery.assert_called_once_with(
            account_case_id="AC-QUEUED",
            message_id="1372",
            claimed_at=unittest.mock.ANY,
        )
        deliver.assert_called_once_with(
            repository=repository,
            account_case_id="AC-QUEUED",
            message_id="1372",
            actor_id="system:production-account-reply",
            trigger="production_worker",
            retry_failed=False,
            public_comment=True,
            solve_ticket=False,
        )

    def test_policy_blocker_terminates_delivery_without_public_write(self) -> None:
        for ownership in (
            _ownership_policy_blocked(
                state="human_reassigned",
                failure_code="zendesk_ownership_human_reassigned",
            ),
            _ownership_policy_blocked(
                state="human_replied",
                failure_code="zendesk_human_reply_blocks_automation",
                blocking_comment_id="52708200000000",
            ),
            _ownership_policy_blocked(
                state="failed",
                failure_code="zendesk_comment_author_unresolved",
                blocking_comment_id="52708200000001",
            ),
        ):
            with self.subTest(state=ownership.state):
                repository = Mock()
                repository.get_account_case_by_ticket_id.return_value = {
                    "account_case_id": "AC-POLICY",
                    "processing_profile": "production",
                    "zendesk_ticket_id": "12875",
                    "route_family": "automated",
                    "execution_action": "enablement",
                }
                repository.claim_account_zendesk_comment_delivery.return_value = {
                    "claimed": True,
                    "status": "pending",
                    "is_public": True,
                    "target_status": None,
                }
                with patch.object(worker, "ticket_repository", repository), patch.object(
                    worker,
                    "ensure_production_automation_ownership",
                    return_value=ownership,
                ), patch.object(
                    worker,
                    "deliver_account_ai_message_as_internal_comment",
                ) as deliver:
                    worker._deliver_production_account_reply_to_zendesk(
                        ticket_id="PRD-12875",
                        message_id="1375",
                        job_id="reply-policy",
                    )

                repository.complete_account_zendesk_comment_delivery.assert_called_once_with(
                    account_case_id="AC-POLICY",
                    message_id="1375",
                    status="failed",
                    zendesk_comment_id=None,
                    failure_code=ownership.failure_code,
                    completed_at=unittest.mock.ANY,
                )
                repository.requeue_account_zendesk_comment_delivery.assert_not_called()
                deliver.assert_not_called()

    def test_pending_delivery_uses_audit_readback_without_put(self) -> None:
        repository = Mock()
        repository.get_account_case_by_ticket_id.return_value = {
            "account_case_id": "AC-PENDING",
            "processing_profile": "production",
            "zendesk_ticket_id": "12838",
            "route_family": "automated",
            "execution_action": "enablement",
        }
        repository.claim_account_zendesk_comment_delivery.return_value = {
            "claimed": False,
            "status": "pending",
        }
        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "reconcile_account_ai_message_internal_comment",
            return_value=_zendesk_result(comment_id="comment-pending"),
        ) as reconcile:
            worker._deliver_production_account_reply_to_zendesk(
                ticket_id="PRD-12838",
                message_id="1372",
                job_id="reply-pending",
            )

        reconcile.assert_called_once_with(
            repository=repository,
            account_case_id="AC-PENDING",
            message_id="1372",
            actor_id="system:production-account-reply",
            trigger="production_worker",
            public_comment=False,
            solve_ticket=False,
        )

    def test_staging_or_missing_external_ticket_never_creates_delivery(self) -> None:
        for case in (
            {
                "account_case_id": "AC-STAGING",
                "processing_profile": "staging",
                "zendesk_ticket_id": "12838",
                "route_family": "automated",
                "execution_action": "enablement",
            },
            {
                "account_case_id": "AC-MISSING-TICKET",
                "processing_profile": "production",
                "zendesk_ticket_id": "",
                "route_family": "automated",
                "execution_action": "enablement",
            },
        ):
            repository = Mock()
            repository.get_account_case_by_ticket_id.return_value = case
            with patch.object(worker, "ticket_repository", repository), patch.object(
                worker, "deliver_account_ai_message_as_internal_comment"
            ) as deliver:
                worker._deliver_production_account_reply_to_zendesk(
                    ticket_id="PRD-CASE",
                    message_id="1372",
                    job_id="reply-case",
                )

            repository.claim_account_zendesk_comment_delivery.assert_not_called()
            deliver.assert_not_called()

    def test_reply_poller_recovers_queued_delivery_after_publication_worker_stops(self) -> None:
        repository = Mock()
        repository.list_account_zendesk_comment_deliveries.return_value = [
            {
                "account_case_id": "AC-RECOVER",
                "message_id": "1372",
                "status": "queued",
            }
        ]
        repository.get_account_case.return_value = {
            "client_ticket_id": "PRD-RECOVER",
        }
        repository.get_ticket.return_value = {
            "ticket_id": "PRD-RECOVER",
            "messages": [
                {
                    "role": "assistant",
                    "message_id": "1372",
                    "content": "The feature is enabled.",
                    "meta": {"account_reply_job_id": "reply-recover"},
                }
            ],
        }
        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_deliver_production_account_reply_to_zendesk",
        ) as deliver:
            worker._drain_production_zendesk_comment_deliveries(limit=20)

        repository.list_account_zendesk_comment_deliveries.assert_called_once_with(
            statuses=("queued", "pending", "outcome_unknown"),
            limit=20,
        )
        deliver.assert_called_once_with(
            ticket_id="PRD-RECOVER",
            message_id="1372",
            job_id="reply-recover",
            reply_intent=None,
        )

    def test_engineer_delivery_rejects_stale_comments_revision_without_zendesk_write(self) -> None:
        repository = Mock()
        delivery = {
            "source": "engineer",
            "account_case_id": "AC-ENG-STALE",
            "message_id": "approval-1",
            "engineer_case_id": "EC-ENG-STALE",
            "investigation_id": "EC-ENG-STALE-round-1",
            "draft_version": 1,
            "comments_revision": "old-revision",
            "immutable_content": "Approved reply",
            "zendesk_ticket_id": "12890",
            "status": "queued",
        }
        repository.list_account_zendesk_comment_deliveries.return_value = [delivery]
        repository.get_account_case.return_value = {"client_ticket_id": "TK-ENG-STALE"}
        repository.get_account_case_comment_sync.return_value = {"comments_revision": "new-revision"}
        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "add_ticket_comment"
        ) as add_comment, patch.object(
            worker, "_record_engineer_delivery_slack_event"
        ) as slack_event:
            worker._drain_production_zendesk_comment_deliveries(limit=20)

        add_comment.assert_not_called()
        repository.claim_account_zendesk_comment_delivery.assert_not_called()
        repository.complete_account_zendesk_comment_delivery.assert_called_once_with(
            account_case_id="AC-ENG-STALE",
            message_id="approval-1",
            status="failed",
            zendesk_comment_id=None,
            failure_code="stale_comments_revision",
            completed_at="2026-03-22T00:00:00+00:00",
        )
        slack_event.assert_called_once()

    def test_engineer_delivery_success_keeps_case_active_and_does_not_solve(self) -> None:
        repository = InMemoryTicketRepository()
        repository.initialize()
        ticket = {
            "ticket_id": "TK-ENG-DELIVER",
            "customer_id": "C-1",
            "requester": "Customer",
            "subject": "Token callback",
            "status": "investigating",
            "messages": [{"role": "customer", "content": "Callback fails", "created_at": "2026-08-24T00:00:00Z"}],
            "created_at": "2026-08-24T00:00:00Z",
            "updated_at": "2026-08-24T00:00:00Z",
        }
        repository.save_ticket(ticket)
        repository.save_account_case(
            {
                "account_case_id": "AC-ENG-DELIVER",
                "billing_ticket_id": "AC-ENG-DELIVER",
                "client_ticket_id": "TK-ENG-DELIVER",
                "processing_profile": "production",
                "zendesk_ticket_id": "12891",
                "title": "Token callback",
                "question": "Callback fails",
                "route_status": "not_automated",
                "automation_status": "not_automated",
                "created_at": "2026-08-24T00:00:00Z",
            }
        )
        engineer_case = worker.build_new_engineer_case(
            ticket,
            engineer_case_id="EC-ENG-DELIVER",
            case_sequence=1,
            title="Token callback",
            status="investigating",
            trigger_source="account_not_automated",
            trigger_reason="not_automated",
            now_value="2026-08-24T00:00:00Z",
        )
        engineer_case.update(
            thread_id="EC-ENG-DELIVER-round-1",
            draft_customer_reply="Please upgrade and retry.",
            engineer_agent_state={"round_number": 1, "round_state": "publishing", "draft_version": 1},
        )
        repository.save_engineer_case(engineer_case)
        repository._account_case_comment_sync["TK-ENG-DELIVER"] = {"comments_revision": "rev-1"}
        repository.create_account_zendesk_comment_delivery(
            account_case_id="AC-ENG-DELIVER",
            message_id="approval-1",
            zendesk_ticket_id="12891",
            idempotency_key="engineer-zendesk-comment:1",
            created_at="2026-08-24T00:01:00Z",
            is_public=True,
            source="engineer",
            engineer_case_id="EC-ENG-DELIVER",
            investigation_id="EC-ENG-DELIVER-round-1",
            draft_version=1,
            comments_revision="rev-1",
            immutable_content="Please upgrade and retry.",
        )
        result = types.SimpleNamespace(comment_id="comment-1")
        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "add_ticket_comment", return_value=result
        ) as add_comment:
            worker._drain_production_zendesk_comment_deliveries(limit=20)

        add_comment.assert_called_once_with(
            ticket_id="12891",
            body="Please upgrade and retry.",
            public=True,
            solve=False,
        )
        stored_case = repository.get_engineer_case("EC-ENG-DELIVER")
        assert stored_case is not None
        self.assertEqual(stored_case["status"], "communicating")
        self.assertIsNotNone(stored_case["active_investigation"])
        self.assertEqual(stored_case["assignment_status"], "pending")
        stored_ticket = repository.get_ticket("TK-ENG-DELIVER")
        assert stored_ticket is not None
        self.assertEqual(stored_ticket["status"], "communicating")
        self.assertEqual(stored_ticket["messages"][-1]["content"], "Please upgrade and retry.")

    def test_engineer_delivery_reads_zendesk_revision_when_local_snapshot_is_missing(self) -> None:
        repository = Mock()
        delivery = {
            "source": "engineer",
            "account_case_id": "AC-ENG-LIVE-REV",
            "message_id": "approval-live-rev",
            "engineer_case_id": "EC-ENG-LIVE-REV",
            "investigation_id": "EC-ENG-LIVE-REV-round-1",
            "draft_version": 1,
            "comments_revision": "live-revision",
            "immutable_content": "Approved reply",
            "zendesk_ticket_id": "12892",
            "status": "queued",
        }
        repository.get_account_case.return_value = {"client_ticket_id": "TK-ENG-LIVE-REV"}
        repository.get_account_case_comment_sync.return_value = None
        repository.claim_account_zendesk_comment_delivery.return_value = {"claimed": True}
        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "read_ticket_ownership_snapshot",
            return_value=types.SimpleNamespace(comments_revision="live-revision"),
        ) as read_snapshot, patch.object(
            worker,
            "add_ticket_comment",
            return_value=types.SimpleNamespace(comment_id="comment-live-rev"),
        ) as add_comment, patch.object(worker, "_complete_engineer_delivery_round"):
            worker._deliver_engineer_approved_zendesk_comment(delivery)

        read_snapshot.assert_called_once_with(ticket_id="12892")
        add_comment.assert_called_once_with(
            ticket_id="12892",
            body="Approved reply",
            public=True,
            solve=False,
        )

    def test_engineer_delivery_marks_signed_public_reply_failed_and_notifies_slack(self) -> None:
        repository = Mock()
        delivery = {
            "source": "engineer",
            "account_case_id": "AC-ENG-SIGNED",
            "message_id": "approval-signed",
            "engineer_case_id": "EC-ENG-SIGNED",
            "investigation_id": "EC-ENG-SIGNED-round-1",
            "draft_version": 1,
            "comments_revision": "signed-revision",
            "immutable_content": "Hi, Ziling\n\nPlease retry.\n\nSid",
            "zendesk_ticket_id": "12893",
            "status": "queued",
        }
        repository.get_account_case.return_value = {"client_ticket_id": "TK-ENG-SIGNED"}
        repository.get_account_case_comment_sync.return_value = {
            "comments_revision": "signed-revision"
        }
        repository.claim_account_zendesk_comment_delivery.return_value = {"claimed": True}
        signature_error = ZendeskCommentError(
            "permanent",
            error_code="zendesk_public_comment_signature_forbidden",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "add_ticket_comment",
            side_effect=signature_error,
        ), patch.object(
            worker,
            "_record_engineer_delivery_slack_event",
        ) as slack_event:
            worker._deliver_engineer_approved_zendesk_comment(delivery)

        repository.complete_account_zendesk_comment_delivery.assert_called_once_with(
            account_case_id="AC-ENG-SIGNED",
            message_id="approval-signed",
            status="failed",
            zendesk_comment_id=None,
            failure_code="zendesk_public_comment_signature_forbidden",
            completed_at="2026-03-22T00:00:00+00:00",
        )
        slack_event.assert_called_once_with(
            delivery,
            event_type="zendesk_publish_failed",
            message_text="Zendesk public comment delivery failed.",
            failure_code="zendesk_public_comment_signature_forbidden",
        )

    def test_delivery_is_not_put_twice_after_first_completion(self) -> None:
        repository = Mock()
        repository.get_account_case_by_ticket_id.return_value = {
            "account_case_id": "AC-IDEMPOTENT",
            "processing_profile": "production",
            "zendesk_ticket_id": "12838",
            "route_family": "automated",
            "execution_action": "enablement",
        }
        repository.claim_account_zendesk_comment_delivery.side_effect = [
            {"claimed": True, "status": "pending"},
            {"claimed": False, "status": "delivered"},
        ]
        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "ensure_production_automation_ownership",
            return_value=_ownership_assigned(),
        ), patch.object(
            worker,
            "deliver_account_ai_message_as_internal_comment",
            return_value=_zendesk_result(comment_id="comment-once"),
        ) as deliver:
            for _ in range(2):
                worker._deliver_production_account_reply_to_zendesk(
                    ticket_id="PRD-12838",
                    message_id="1372",
                    job_id="reply-idempotent",
                )

        deliver.assert_called_once()

    def test_failed_service_result_is_logged_as_failed_delivery(self) -> None:
        repository = Mock()
        repository.get_account_case_by_ticket_id.return_value = {
            "account_case_id": "AC-FAILED-RESULT",
            "processing_profile": "production",
            "zendesk_ticket_id": "12838",
            "route_family": "automated",
            "execution_action": "enablement",
        }
        repository.claim_account_zendesk_comment_delivery.return_value = {
            "claimed": True,
            "status": "pending",
        }
        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "ensure_production_automation_ownership",
            return_value=_ownership_assigned(),
        ), patch.object(
            worker,
            "deliver_account_ai_message_as_internal_comment",
            return_value=_zendesk_result(
                status="failed",
                comment_id=None,
                retryable=True,
                error_code="zendesk_http_error",
            ),
        ), patch.object(worker.LOGGER, "warning") as warning:
            worker._deliver_production_account_reply_to_zendesk(
                ticket_id="PRD-FAILED-RESULT",
                message_id="1372",
                job_id="reply-failed-result",
            )

        self.assertTrue(warning.called)
        self.assertIn("production_zendesk_delivery_failed", warning.call_args.args[0])
        self.assertIn("delivery_status=failed", warning.call_args.args[0])

    def test_worker_rag_executor_uses_extended_timeout_and_recovery_window(self) -> None:
        detail = worker.RagTicketAnswerDetail(
            answer="Use joinChannel with a token.",
            confidence=0.92,
            sources=["https://docs.agora.io/en/video-calling/get-started"],
            citations=[{"chunk_id": "chunk-1"}],
            needs_engineer_guidance=False,
            reason="grounded_answer",
            evidence_summary=None,
            packed_evidence=None,
        )

        with patch.dict(
            os.environ,
            {
                "TICKET_WORKER_RAG_SERVICE_TIMEOUT_SECONDS": "90",
                "TICKET_WORKER_RAG_MAX_WAIT_SECONDS": "300",
                "TICKET_WORKER_RAG_RECOVERY_POLL_INTERVAL_SECONDS": "2",
            },
            clear=False,
        ), patch.object(
            worker.rag_service_client,
            "query_answer_with_recovery_detail",
            return_value=detail,
        ) as rag_mock:
            result = worker._worker_rag_with_cancel_guard(
                request_id="rag-worker-timeout-1",
                message="how to join channel",
                ticket_id="T-WORKER-1",
                customer_id="C-123",
                ticket_context=[{"role": "customer", "content": "how to join channel"}],
                product="audio_video_calling",
            )

        self.assertEqual(result.answer, "Use joinChannel with a token.")
        rag_mock.assert_called_once_with(
            question="how to join channel",
            request_id="rag-worker-timeout-1",
            ticket_id="T-WORKER-1",
            customer_id="C-123",
            requester=None,
            ticket_context=[{"role": "customer", "content": "how to join channel"}],
            product="audio_video_calling",
            insufficient_reply=INSUFFICIENT_EVIDENCE_REPLY,
            timeout_seconds=90.0,
            recovery_window_seconds=210.0,
            recovery_poll_interval_seconds=2.0,
            query_policy="client_accuracy_first",
            rag_access_mode="official_only",
        )

    def test_worker_rag_executor_timeout_with_healthy_service_returns_processing_timeout(self) -> None:
        with patch.object(
            worker.rag_service_client,
            "query_answer_with_recovery_detail",
            side_effect=worker.RagServiceError(
                "RAG service request failed",
                failure_kind="timeout",
            ),
        ) as rag_mock, patch.object(
            worker.rag_service_client,
            "health",
            return_value={"status": "ok", "service": "rag-api"},
        ) as health_mock:
            result = worker._worker_rag_with_cancel_guard(
                request_id="rag-worker-timeout-2",
                message="how to join channel",
                ticket_id="T-WORKER-2",
                customer_id="C-123",
                ticket_context=[{"role": "customer", "content": "how to join channel"}],
                product="audio_video_calling",
            )

        self.assertEqual(result.reason, "rag_processing_timeout")
        self.assertTrue(result.needs_engineer_guidance)
        self.assertIsInstance(result.evidence_summary, dict)
        self.assertEqual(result.evidence_summary["diagnostics"]["rag_failure_kind"], "timeout")
        self.assertEqual(result.evidence_summary["diagnostics"]["rag_timeout_health_check_status"], "ok")
        rag_mock.assert_called_once()
        health_mock.assert_called_once()

    def test_worker_rag_executor_preserves_recovered_insufficient_evidence_reason(self) -> None:
        recovered = worker.RagTicketAnswerDetail(
            answer="RAG completed but could not verify a customer-safe grounded answer from the available schema evidence.",
            confidence=0.41,
            sources=[],
            citations=[],
            needs_engineer_guidance=True,
            reason="rag_completed_with_insufficient_evidence",
            evidence_summary={"diagnostics": {"rag_recovered_from_live_detail": True}},
            packed_evidence=None,
        )
        with patch.object(
            worker.rag_service_client,
            "query_answer_with_recovery_detail",
            return_value=recovered,
        ) as rag_mock, patch.object(
            worker.rag_service_client,
            "health",
        ) as health_mock:
            result = worker._worker_rag_with_cancel_guard(
                request_id="rag-worker-insufficient-1",
                message="Can you check this request body {\"clientRequest\":{\"layoutConfig\":[]}}?",
                ticket_id="T-WORKER-INSUFFICIENT",
                customer_id="C-123",
                ticket_context=[{"role": "customer", "content": "request body question"}],
                product="cloud_recording",
            )

        self.assertEqual(result.reason, "rag_completed_with_insufficient_evidence")
        self.assertTrue(result.needs_engineer_guidance)
        self.assertEqual(
            result.evidence_summary["diagnostics"]["rag_recovered_from_live_detail"],
            True,
        )
        self.assertNotEqual(result.reason, "rag_processing_timeout")
        rag_mock.assert_called_once()
        health_mock.assert_not_called()

    def test_worker_rag_executor_transport_failure_with_unhealthy_service_stays_unavailable(self) -> None:
        with patch.object(
            worker.rag_service_client,
            "query_answer_with_recovery_detail",
            side_effect=worker.RagServiceError(
                "RAG service request failed",
                failure_kind="transport",
            ),
        ) as rag_mock, patch.object(
            worker.rag_service_client,
            "health",
        ) as health_mock:
            result = worker._worker_rag_with_cancel_guard(
                request_id="rag-worker-timeout-3",
                message="how to join channel",
                ticket_id="T-WORKER-3",
                customer_id="C-123",
                ticket_context=[{"role": "customer", "content": "how to join channel"}],
                product="audio_video_calling",
            )

        self.assertEqual(result.reason, "rag_unavailable")
        self.assertTrue(result.needs_engineer_guidance)
        rag_mock.assert_called_once()
        health_mock.assert_not_called()

    def test_execute_parallel_ticket_query_skips_rag_when_route_is_non_rag(self) -> None:
        rag_detail = types.SimpleNamespace(
            answer="Use joinChannel with a valid token.",
            confidence=0.91,
            sources=["https://docs.agora.io/en/video-calling/get-started"],
            citations=[{"chunk_id": "chunk-1"}],
            needs_engineer_guidance=False,
            reason="grounded_answer",
            evidence_summary={},
            packed_evidence=None,
        )
        execution = types.SimpleNamespace(
            answer="Agora's latest investor information is on the official site.",
            confidence=0.82,
            sources=["https://investor.agora.io"],
            citations=[],
            needs_engineer_guidance=False,
            needs_investigating=False,
            next_status="communicating",
            answer_route="web_search",
            scope_label="agora_non_technical",
            route_reason="company_info",
            route_confidence=0.88,
            search_used=True,
            matched_signals=["ceo"],
            route_family="web_company_info",
            execution_action="web_search",
            tooling_profile="official_web_search",
            evidence_summary=None,
            packed_evidence=None,
            router_source="deterministic",
            intent_router_attempted=False,
            intent_router_confidence_threshold=None,
            intent_router_model_confidence=None,
            intent_router_fallback_reason=None,
            intent_router_failure_type=None,
            intent_router_failure_source=None,
        )

        def _slow_rag(*_args, **_kwargs):
            time.sleep(0.05)
            return rag_detail

        with patch.object(
            worker,
            "decide_support_route",
            return_value=_route_decision(
                action="web_search",
                scope_label="agora_non_technical",
                reason="company_info",
            ),
        ), patch.object(
            worker,
            "_worker_rag_with_cancel_guard",
            side_effect=_slow_rag,
        ) as rag_mock, patch.object(
            worker,
            "resolve_support_message",
            return_value=execution,
        ) as resolve_mock:
            result, diagnostics = worker._execute_parallel_ticket_query(
                "Who is Agora's CEO?",
                ticket_id="T-WEB-1",
                customer_id="C-123",
                ticket_subject="Investor question",
                ticket_context=[{"role": "customer", "content": "Who is Agora's CEO?"}],
                message_created_at="2026-03-22T00:00:00+00:00",
            )

        self.assertEqual(result.execution_action, "web_search")
        self.assertFalse(diagnostics["rag_cancelled"])
        self.assertIsNone(diagnostics["rag_cancel_stage"])
        self.assertEqual(diagnostics["route_final_action"], "web_search")
        self.assertEqual(diagnostics["route_result_source"], "route_first")
        self.assertEqual(result.workflow_action, "answer_customer")
        rag_mock.assert_not_called()
        self.assertEqual(
            resolve_mock.call_args.kwargs["decision"].execution_action,
            "web_search",
        )

    def test_execute_parallel_ticket_query_fails_open_to_rag_when_route_raises(self) -> None:
        rag_detail = types.SimpleNamespace(
            answer="Use joinChannel with the same channel name and token.",
            confidence=0.91,
            sources=["https://docs.agora.io/en/video-calling/get-started"],
            citations=[{"chunk_id": "chunk-1"}],
            needs_engineer_guidance=False,
            reason="grounded_answer",
            evidence_summary={},
            packed_evidence=None,
        )
        execution = types.SimpleNamespace(
            answer="Use joinChannel with the same channel name and token.",
            confidence=0.91,
            sources=["https://docs.agora.io/en/video-calling/get-started"],
            citations=[{"chunk_id": "chunk-1"}],
            needs_investigating=False,
            next_status="communicating",
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="grounded_answer",
            route_confidence=0.0,
            search_used=False,
            matched_signals=["optimistic_default"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            evidence_summary=None,
            packed_evidence=None,
        )

        with patch.object(
            worker,
            "decide_support_route",
            side_effect=RuntimeError("route timeout"),
        ), patch.object(
            worker,
            "_worker_rag_with_cancel_guard",
            return_value=rag_detail,
        ):
            result, diagnostics = worker._execute_parallel_ticket_query(
                "how to join channel",
                ticket_id="T-RAG-1",
                customer_id="C-123",
                ticket_subject="Join question",
                ticket_context=[{"role": "customer", "content": "how to join channel"}],
                message_created_at="2026-03-22T00:00:00+00:00",
            )

        self.assertEqual(result.execution_action, "rag")
        self.assertFalse(diagnostics["rag_cancelled"])
        self.assertEqual(diagnostics["route_final_action"], "rag")
        self.assertEqual(diagnostics["route_result_source"], "route_fail_open")
        self.assertTrue(diagnostics["route_fail_open"])
        self.assertEqual(diagnostics["route_timeout_seconds"], 8.0)
        self.assertGreaterEqual(float(diagnostics["route_latency_ms"]), 0.0)
        self.assertTrue(str(diagnostics["rag_started_at"] or "").strip())
        self.assertTrue(str(diagnostics["rag_finished_at"] or "").strip())
        self.assertEqual(result.workflow_action, "answer_customer")
        self.assertEqual(result.answer, "Use joinChannel with the same channel name and token.")

    def test_process_ticket_query_forwards_ticket_product_to_orchestrator(self) -> None:
        ticket = _build_ticket()
        ticket["product"] = "cloud_recording"
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(ticket),
            copy.deepcopy(ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use the Cloud Recording REST API start endpoint.",
            confidence=0.91,
            sources=["official/cloud-recording-start.md"],
            citations=[{"source": "official/cloud-recording-start.md", "label": "Start recording"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="docs_match",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["cloud recording"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ) as orchestrate_mock, patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:00+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task, product="cloud_recording"))

        self.assertEqual(orchestrate_mock.call_args.kwargs["product"], "cloud_recording")

    def test_process_ticket_query_starts_main_agent_from_task_snapshot_before_ticket_refresh(self) -> None:
        repository = Mock()
        repository.get_ticket.return_value = _build_ticket(
            ticket_id="T-SNAPSHOT",
            customer_message="how to join channel",
            message_created_at="2026-03-22T00:00:00+00:00",
        )
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use joinChannel with the same channel name and token.",
            confidence=0.91,
            sources=["https://docs.agora.io/en/video-calling/get-started"],
            citations=[{"chunk_id": "chunk-1"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="grounded_answer",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["join channel"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
            workflow_action="answer_customer",
            client_intake_state={"phase": "gather_customer_inputs"},
            evidence_summary=None,
            run_id="run-snapshot",
            client_agent_runtime_state={"status": "completed"},
        )

        def _orchestrate_side_effect(*args, **kwargs):
            self.assertEqual(repository.get_ticket.call_count, 0)
            self.assertEqual(kwargs["customer_id"], "C-123")
            self.assertEqual(kwargs["ticket_subject"], "Join question")
            self.assertEqual(kwargs["product"], "audio_video_calling")
            self.assertEqual(
                kwargs["ticket_context"],
                [
                    {"role": "customer", "content": "how to join channel"},
                    {"role": "assistant", "content": "I am checking the knowledge base for you now."},
                ],
            )
            self.assertEqual(kwargs["client_intake_state"], {"phase": "gather_customer_inputs"})
            self.assertEqual(
                kwargs["latest_assistant_message"],
                {
                    "role": "assistant",
                    "content": "Use joinChannel with the same channel name and token.",
                    "workflow_action": "answer_customer",
                    "answer_route": "rag",
                    "route_reason": "grounded_answer",
                },
            )
            self.assertEqual(kwargs["current_ticket_status"], "communicating")
            return execution

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            side_effect=_orchestrate_side_effect,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:00:01+00:00",
        ):
            worker._process_ticket_query(
                bus,
                {
                    "task_type": "ticket_query",
                    "ticket_id": "T-SNAPSHOT",
                    "customer_message": "how to join channel",
                    "message_created_at": "2026-03-22T00:00:00+00:00",
                    "created_at": "2026-03-22T00:00:00.100000+00:00",
                    "customer_id": "C-123",
                    "ticket_subject": "Join question",
                    "product": "audio_video_calling",
                    "route_context_tail": [
                        {"role": "customer", "content": "how to join channel"},
                        {"role": "assistant", "content": "I am checking the knowledge base for you now."},
                    ],
                    "client_intake_state": {"phase": "gather_customer_inputs"},
                    "latest_assistant_message": {
                        "role": "assistant",
                        "content": "Use joinChannel with the same channel name and token.",
                        "workflow_action": "answer_customer",
                        "answer_route": "rag",
                        "route_reason": "grounded_answer",
                    },
                    "current_ticket_status": "communicating",
                    "ticket_updated_at": "2026-03-22T00:00:00+00:00",
                },
            )

        self.assertEqual(repository.get_ticket.call_count, 1)

    def test_process_ticket_query_clarifies_customer_and_keeps_ticket_communicating(self) -> None:
        ticket = _build_ticket(
            ticket_id="T-INTAKE",
            customer_message="I got black screen issue.",
        )
        ticket["product"] = "audio_video_calling"
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(ticket),
            copy.deepcopy(ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        repository.save_engineer_case.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer=(
                "Known so far: the issue symptom is black screen. "
                "To investigate this Audio/Video Calling issue, please share the channel name, "
                "problematic uid, and issue timestamp."
            ),
            confidence=0.0,
            sources=[],
            citations=[],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="rag_insufficient_evidence",
            route_confidence=0.87,
            search_used=False,
            matched_signals=["black screen"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
            workflow_action="clarify_customer_for_intake",
            client_intake_state={
                "phase": "gather_customer_inputs",
                "product": "audio_video_calling",
                "issue_mode": "investigation",
                "known_information": {"issue_symptom": "black screen"},
                "missing_information": ["channel_name", "problematic_uid", "issue_timestamp"],
                "ready_for_engineer_ticket": False,
                "last_updated_at": "2026-04-04T10:00:00Z",
            },
            evidence_summary=None,
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "_record_ticket_agent_runtime_events",
            side_effect=lambda execution_arg: [
                repository.record_ticket_agent_event(
                    str(item.get("ticket_id") or ""),
                    str(item.get("message_id") or "").strip() or None,
                    str(item.get("run_id") or ""),
                    str(item.get("agent_name") or ""),
                    str(item.get("phase") or ""),
                    str(item.get("event_type") or ""),
                    dict(item.get("payload") or {}) if isinstance(item.get("payload"), dict) else {},
                )
                for item in getattr(execution_arg, "client_agent_runtime_events", [])
                if isinstance(item, dict)
            ],
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:00+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task, ticket_id="T-INTAKE", customer_message="I got black screen issue."))

        saved_ticket = repository.save_ticket.call_args_list[0].args[0]
        self.assertEqual(saved_ticket["status"], "communicating")
        self.assertEqual(saved_ticket["client_intake_state"]["phase"], "gather_customer_inputs")
        self.assertEqual(
            saved_ticket["messages"][-1]["content"],
            "Known so far: the issue symptom is black screen. To investigate this Audio/Video Calling issue, please share the channel name, problematic uid, and issue timestamp.",
        )
        self.assertFalse(repository.save_engineer_case.called)
        event_payload = repository.record_event.call_args_list[0].args[2]
        self.assertEqual(event_payload["workflow_action"], "clarify_customer_for_intake")

    def test_process_ticket_query_persists_client_agent_runtime_state_and_events(self) -> None:
        ticket = _build_ticket(ticket_id="T-RUNTIME", customer_message="how to join channel")
        ticket["product"] = "audio_video_calling"
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(ticket),
            copy.deepcopy(ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        repository.record_ticket_agent_event.return_value = None
        repository.save_engineer_case.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use joinChannel with the same channel name and token.",
            confidence=0.91,
            sources=["https://docs.agora.io/en/video-calling/get-started"],
            citations=[{"chunk_id": "chunk-1"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="grounded_answer",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["join channel"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
            workflow_action="answer_customer",
            client_intake_state=None,
            evidence_summary=None,
            run_id="run-123",
            client_agent_runtime_state={
                "runtime_version": "client_ticket_agents_v1",
                "active_run_id": "run-123",
                "status": "completed",
                "main_agent": {"phase": "completed", "status": "completed"},
                "route_agent": {"phase": "completed", "status": "completed", "decision": "rag"},
                "rag_agent": {"phase": "completed", "status": "completed", "decision": "grounded_answer"},
                "review_agent": {"phase": "skipped", "status": "skipped"},
            },
            client_agent_runtime_events=[
                {
                    "ticket_id": "T-RUNTIME",
                    "message_id": "2026-03-22T00:00:00+00:00",
                    "run_id": "run-123",
                    "agent_name": "main_agent",
                    "phase": "completed",
                    "event_type": "workflow_decided",
                    "payload": {"workflow_action": "answer_customer"},
                    "created_at": "2026-03-22T00:00:01+00:00",
                }
            ],
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "get_app_build_info",
            return_value={"ref": "execution-build-456", "built_at": "2026-03-22T00:01:00Z"},
        ), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "_record_ticket_agent_runtime_events",
            side_effect=lambda execution_arg: [
                repository.record_ticket_agent_event(
                    str(item.get("ticket_id") or ""),
                    str(item.get("message_id") or "").strip() or None,
                    str(item.get("run_id") or ""),
                    str(item.get("agent_name") or ""),
                    str(item.get("phase") or ""),
                    str(item.get("event_type") or ""),
                    dict(item.get("payload") or {}) if isinstance(item.get("payload"), dict) else {},
                )
                for item in getattr(execution_arg, "client_agent_runtime_events", [])
                if isinstance(item, dict)
            ],
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:00+00:00",
        ):
            worker._process_ticket_query(
                bus,
                dict(
                    self.task,
                    ticket_id="T-RUNTIME",
                    customer_message="how to join channel",
                    app_build_ref="admission-build-123",
                ),
            )

        saved_ticket = repository.save_ticket.call_args_list[0].args[0]
        self.assertEqual(saved_ticket["client_agent_runtime_state"]["active_run_id"], "run-123")
        self.assertEqual(
            saved_ticket["client_agent_runtime_state"]["build_provenance"],
            {
                "task_app_build_ref": "admission-build-123",
                "execution_app_build_ref": "execution-build-456",
            },
        )
        assistant_message = saved_ticket["messages"][-1]
        self.assertEqual(assistant_message["client_agent_run_id"], "run-123")
        self.assertEqual(assistant_message["client_agent_runtime_status"], "completed")
        repository.record_ticket_agent_event.assert_called_once()
        agent_event_args = repository.record_ticket_agent_event.call_args.args
        self.assertEqual(agent_event_args[2], "run-123")
        self.assertEqual(agent_event_args[3], "main_agent")

        response_ready_payload = repository.record_event.call_args_list[0].args[2]
        self.assertIn("message_to_task_dequeued_ms", response_ready_payload)
        self.assertIn("dequeued_to_main_agent_started_ms", response_ready_payload)
        self.assertIn("main_agent_total_ms", response_ready_payload)
        self.assertIn("main_agent_to_answer_saved_ms", response_ready_payload)
        self.assertIn("answer_saved_to_response_ready_ms", response_ready_payload)
        self.assertEqual(response_ready_payload["task_app_build_ref"], "admission-build-123")
        self.assertEqual(response_ready_payload["execution_app_build_ref"], "execution-build-456")
        record_event_index = next(
            index
            for index, call in enumerate(repository.mock_calls)
            if call[0] == "record_event" and call.args[:2] == ("T-RUNTIME", "ticket_ai_response_ready")
        )
        runtime_event_index = next(
            index
            for index, call in enumerate(repository.mock_calls)
            if call[0] == "record_ticket_agent_event"
        )
        self.assertLess(
            record_event_index,
            runtime_event_index,
        )

    def test_process_ticket_query_persists_message_level_retrieval_plan_snapshot(self) -> None:
        ticket = _build_ticket(
            ticket_id="T-RAG-SNAPSHOT",
            customer_message="how to join channel",
            message_created_at="2026-03-22T00:00:00+00:00",
        )
        ticket["product"] = "audio_video_calling"
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(ticket),
            copy.deepcopy(ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        repository.record_ticket_agent_event.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use joinChannel with a valid token and channel name.",
            confidence=0.94,
            sources=["https://docs.agora.io/en/video-calling/get-started"],
            citations=[{"chunk_id": "chunk-1", "source_path": "official/get-started-sdk_android.md"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="grounded_answer",
            route_confidence=0.94,
            search_used=False,
            matched_signals=["join channel"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
            workflow_action="answer_customer",
            client_intake_state=None,
            evidence_summary={
                "diagnostics": {
                    "retrieval_plan_snapshot": {
                        "request_id": "rag-snapshot-1",
                        "query_class": "how_to_faq",
                        "retrieval_strategy": "agentic_multi_tool_v1",
                        "light_path_used": False,
                        "evidence_goal": "how_to_usage_support",
                        "recovery_bias": "semantic",
                        "first_pass_tools": ["p_vec", "s_vec"],
                        "query_variants": [{"kind": "original", "query": "how to join channel"}],
                        "decomposition_targets": [],
                        "agent_iterations": [{"round_index": 1, "decision": "answer_now"}],
                        "judge_summary": {"decision": "answer_now", "reason": "sufficient_first_pass_support"},
                        "selected_contexts": [{"chunk_id": "chunk-1"}],
                        "query_understanding_summary": {"query_profile": "how_to_faq"},
                        "tool_timing_summary": {"total_latency_ms": 1200.0},
                        "open_diagnosis_target": "rag-snapshot-1",
                    }
                }
            },
            packed_evidence=None,
            client_agent_runtime_state={"status": "completed"},
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=(execution, {"parallel_mode": "main_agent"}),
        ), patch.object(
            worker,
            "_record_ticket_agent_runtime_events",
            side_effect=lambda execution_arg: [
                repository.record_ticket_agent_event(
                    str(item.get("ticket_id") or ""),
                    str(item.get("message_id") or "").strip() or None,
                    str(item.get("run_id") or ""),
                    str(item.get("agent_name") or ""),
                    str(item.get("phase") or ""),
                    str(item.get("event_type") or ""),
                    dict(item.get("payload") or {}) if isinstance(item.get("payload"), dict) else {},
                )
                for item in getattr(execution_arg, "client_agent_runtime_events", [])
                if isinstance(item, dict)
            ],
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:00+00:00",
        ):
            worker._process_ticket_query(
                bus,
                dict(self.task, ticket_id="T-RAG-SNAPSHOT", customer_message="how to join channel"),
            )

        saved_ticket = repository.save_ticket.call_args_list[0].args[0]
        assistant_message = saved_ticket["messages"][-1]
        self.assertEqual(assistant_message["answer_route"], "rag")
        self.assertIn("retrieval_plan_snapshot", assistant_message)
        self.assertEqual(assistant_message["retrieval_plan_snapshot"]["request_id"], "rag-snapshot-1")
        self.assertEqual(assistant_message["retrieval_plan_snapshot"]["query_class"], "how_to_faq")

    def test_process_ticket_query_records_queue_wait_and_main_agent_timing_fields(self) -> None:
        ticket = _build_ticket(
            ticket_id="T-TIMING",
            customer_message="how to join channel",
            message_created_at="2026-03-22T00:00:00+00:00",
        )
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(ticket),
            copy.deepcopy(ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use joinChannel with the same channel name and token.",
            confidence=0.91,
            sources=["https://docs.agora.io/en/video-calling/get-started"],
            citations=[{"chunk_id": "chunk-1"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="grounded_answer",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["join channel"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
            workflow_action="answer_customer",
            client_intake_state=None,
            evidence_summary=None,
            client_agent_runtime_state={"status": "completed"},
        )

        now_values = iter(
            [
                "2026-03-22T00:00:10+00:00",
                "2026-03-22T00:00:11+00:00",
                "2026-03-22T00:00:12+00:00",
                "2026-03-22T00:00:13+00:00",
                "2026-03-22T00:00:14+00:00",
                "2026-03-22T00:00:15+00:00",
                "2026-03-22T00:00:16+00:00",
            ]
        )
        task = dict(
            self.task,
            ticket_id="T-TIMING",
            customer_message="how to join channel",
            message_created_at="2026-03-22T00:00:00+00:00",
            created_at="2026-03-22T00:00:00+00:00",
            api_persist_latency_ms=120.5,
            api_return_latency_ms=180.25,
            load_ticket_ms=5.0,
            save_ticket_ms=8.0,
            record_ticket_created_event_ms=2.0,
            enqueue_ticket_query_ms=3.0,
            enqueue_sentiment_ms=1.5,
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            side_effect=lambda: next(now_values),
        ):
            worker._process_ticket_query(bus, task)

        event_payload = repository.record_event.call_args.args[2]
        self.assertEqual(event_payload["task_dequeued_at"], "2026-03-22T00:00:10+00:00")
        self.assertEqual(event_payload["main_agent_started_at"], "2026-03-22T00:00:11+00:00")
        self.assertEqual(event_payload["main_agent_completed_at"], "2026-03-22T00:00:12+00:00")
        self.assertEqual(event_payload["queue_wait_ms"], 10000.0)
        self.assertEqual(event_payload["response_ready_dispatch_ms"], 4000.0)
        self.assertEqual(event_payload["load_ticket_ms"], 5.0)
        self.assertEqual(event_payload["save_ticket_ms"], 8.0)
        self.assertEqual(event_payload["record_ticket_created_event_ms"], 2.0)
        self.assertEqual(event_payload["enqueue_ticket_query_ms"], 3.0)
        self.assertEqual(event_payload["enqueue_sentiment_ms"], 1.5)

    def test_process_ticket_query_retries_transient_save_ticket_failure(self) -> None:
        initial_ticket = _build_ticket()
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(initial_ticket),
            copy.deepcopy(initial_ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.side_effect = [
            psycopg.OperationalError("connection timeout expired"),
            None,
        ]
        repository.record_event.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use the Node.js token builder sample.",
            confidence=0.91,
            sources=["official/deploy-token-server.md"],
            citations=[{"source": "official/deploy-token-server.md", "label": "Deploy a token server"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="docs_match",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["token", "node.js"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:00+00:00",
        ), patch.object(
            worker,
            "TICKET_REPOSITORY_RETRY_MAX",
            1,
        ), patch.object(
            worker,
            "TICKET_REPOSITORY_RETRY_BASE_DELAY_SECONDS",
            0.05,
        ), patch.object(
            worker.time,
            "sleep",
        ) as sleep_mock:
            worker._process_ticket_query(bus, dict(self.task))

        self.assertEqual(repository.save_ticket.call_count, 2)
        saved_ticket = repository.save_ticket.call_args_list[-1].args[0]
        self.assertEqual(saved_ticket["messages"][-1]["content"], "Use the Node.js token builder sample.")
        self.assertEqual(repository.record_event.call_count, 1)
        sleep_mock.assert_any_call(0.05)

    def test_schedule_ticket_task_retry_reenqueues_retryable_db_failure(self) -> None:
        queue = Mock()
        queue.enqueue.return_value = True

        with patch.object(worker, "TICKET_TASK_RETRY_MAX", 2), patch.object(
            worker,
            "TICKET_TASK_RETRY_BASE_DELAY_SECONDS",
            0.5,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:02:00+00:00",
        ), patch.object(worker.time, "sleep") as sleep_mock:
            scheduled = worker._schedule_ticket_task_retry(
                queue,
                dict(self.task),
                psycopg.OperationalError("server closed the connection unexpectedly"),
            )

        self.assertTrue(scheduled)
        queue.enqueue.assert_called_once()
        retry_task = queue.enqueue.call_args.args[0]
        self.assertEqual(retry_task["worker_retry_count"], 1)
        self.assertEqual(retry_task["last_retry_at"], "2026-03-22T00:02:00+00:00")
        self.assertIn("server closed the connection unexpectedly", retry_task["last_error"])
        sleep_mock.assert_called_once_with(0.5)

    def test_recover_stale_ticket_query_tasks_on_worker_start_reenqueues_missing_async_turn(self) -> None:
        stuck_ticket = _build_ticket(
            ticket_id="TK-116",
            product_selection_state={
                "phase": "awaiting_product_confirmation",
                "pending_customer_message": "I got black screen, what should I do now?",
                "pending_message_created_at": "2026-03-22T00:03:00+00:00",
            },
        )
        stuck_ticket["messages"].append(
            {
                "role": "customer",
                "content": "i got black screen, what should i do now?",
                "created_at": "2026-03-22T00:03:00+00:00",
            }
        )
        stuck_ticket["updated_at"] = "2026-03-22T00:03:01+00:00"
        repository = Mock()
        repository.list_tickets.return_value = [copy.deepcopy(stuck_ticket)]
        repository.list_ticket_events.return_value = [
            {
                "ticket_id": "TK-116",
                "event_type": "ticket_ai_processing",
                "payload": {
                    "message_created_at": "2026-03-22T00:03:00+00:00",
                },
                "created_at": "2026-03-22T00:03:02+00:00",
            }
        ]
        queue = Mock()
        queue.list_pending_tasks.return_value = []
        queue.enqueue.return_value = True

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:05:00+00:00",
        ):
            recovered = worker._recover_stale_ticket_query_tasks_on_worker_start(
                queue,
                worker_started_at="2026-03-22T00:04:00+00:00",
            )

        self.assertEqual(recovered, 1)
        queue.enqueue.assert_called_once()
        recovery_task = queue.enqueue.call_args.args[0]
        self.assertEqual(recovery_task["ticket_id"], "TK-116")
        self.assertEqual(recovery_task["customer_message"], "i got black screen, what should i do now?")
        self.assertEqual(recovery_task["message_created_at"], "2026-03-22T00:03:00+00:00")
        self.assertEqual(recovery_task["processing_mode"], "worker_startup_recovery")
        self.assertEqual(recovery_task["requester"], "Customer")
        self.assertEqual(recovery_task["latest_assistant_message"]["content"], "I am checking the knowledge base for you now.")
        self.assertEqual(
            recovery_task["product_selection_state"]["phase"],
            "awaiting_product_confirmation",
        )
        repository.record_event.assert_called_once()
        self.assertEqual(repository.record_event.call_args.args[0], "TK-116")
        self.assertEqual(repository.record_event.call_args.args[1], "ticket_ai_recovery_queued")
        recovery_event = repository.record_event.call_args.args[2]
        self.assertEqual(recovery_event["message_created_at"], "2026-03-22T00:03:00+00:00")
        self.assertEqual(recovery_event["recovery_reason"], "missing_async_completion_after_worker_restart")

    def test_recover_stale_ticket_query_tasks_on_worker_start_skips_turn_still_in_queue(self) -> None:
        stuck_ticket = _build_ticket(ticket_id="TK-117")
        stuck_ticket["messages"].append(
            {
                "role": "customer",
                "content": "the video stays frozen",
                "created_at": "2026-03-22T00:03:00+00:00",
            }
        )
        repository = Mock()
        repository.list_tickets.return_value = [copy.deepcopy(stuck_ticket)]
        repository.list_ticket_events.return_value = [
            {
                "ticket_id": "TK-117",
                "event_type": "ticket_ai_processing",
                "payload": {
                    "message_created_at": "2026-03-22T00:03:00+00:00",
                },
                "created_at": "2026-03-22T00:03:02+00:00",
            }
        ]
        queue = Mock()
        queue.list_pending_tasks.return_value = [
            {
                "task_type": "ticket_query",
                "ticket_id": "TK-117",
                "message_created_at": "2026-03-22T00:03:00+00:00",
            }
        ]

        with patch.object(worker, "ticket_repository", repository):
            recovered = worker._recover_stale_ticket_query_tasks_on_worker_start(
                queue,
                worker_started_at="2026-03-22T00:04:00+00:00",
            )

        self.assertEqual(recovered, 0)
        queue.enqueue.assert_not_called()
        repository.record_event.assert_not_called()

    def test_process_ticket_query_skips_duplicate_final_response_after_requeue(self) -> None:
        initial_ticket = _build_ticket()
        refreshed_ticket = _build_ticket()
        refreshed_ticket["messages"].append(
            {
                "role": "assistant",
                "content": "Use the Node.js token builder sample.",
                "created_at": "2026-03-22T00:01:00+00:00",
                "sources": ["official/deploy-token-server.md"],
                "citations": [
                    {
                        "source": "official/deploy-token-server.md",
                        "label": "Deploy a token server",
                    }
                ],
            }
        )
        repository = Mock()
        repository.get_ticket.return_value = copy.deepcopy(refreshed_ticket)
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use the Node.js token builder sample.",
            confidence=0.91,
            sources=["official/deploy-token-server.md"],
            citations=[{"source": "official/deploy-token-server.md", "label": "Deploy a token server"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="docs_match",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["token", "node.js"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:05+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task))

        repository.save_ticket.assert_not_called()

    def test_find_existing_worker_response_returns_single_persisted_reply_for_customer_turn(self) -> None:
        ticket = _build_ticket()
        ticket["messages"] = [
            ticket["messages"][0],
            {
                "role": "assistant",
                "content": "Use the Node.js token builder sample.",
                "created_at": "2026-03-22T00:01:00+00:00",
                "answer_route": "rag",
                "route_reason": "docs_match",
            },
        ]

        existing = worker._find_existing_worker_response(
            ticket,
            self.task["customer_message"],
            self.task["message_created_at"],
        )

        self.assertIsNotNone(existing)
        assert existing is not None
        self.assertEqual(existing["content"], "Use the Node.js token builder sample.")

    def test_process_ticket_query_skips_duplicate_final_response_after_requeue_with_single_assistant_reply(self) -> None:
        refreshed_ticket = _build_ticket()
        refreshed_ticket["messages"] = [
            refreshed_ticket["messages"][0],
            {
                "role": "assistant",
                "content": "Use the Node.js token builder sample.",
                "created_at": "2026-03-22T00:01:00+00:00",
                "sources": ["official/deploy-token-server.md"],
                "citations": [
                    {
                        "source": "official/deploy-token-server.md",
                        "label": "Deploy a token server",
                    }
                ],
            },
        ]
        repository = Mock()
        repository.get_ticket.return_value = copy.deepcopy(refreshed_ticket)
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use the Node.js token builder sample.",
            confidence=0.91,
            sources=["official/deploy-token-server.md"],
            citations=[{"source": "official/deploy-token-server.md", "label": "Deploy a token server"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="docs_match",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["token", "node.js"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:05+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task))

        repository.save_ticket.assert_not_called()

    def test_process_ticket_query_persists_route_metadata_without_calling_legacy_build_answer(self) -> None:
        initial_ticket = _build_ticket()
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(initial_ticket),
            copy.deepcopy(initial_ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        bus = Mock()

        resolution = types.SimpleNamespace(
            answer="Agora's CEO is Tony Zhao.",
            confidence=0.93,
            sources=["https://www.agora.io/en/about-agora/"],
            citations=[
                {
                    "source_url": "https://www.agora.io/en/about-agora/",
                    "heading": "About Agora",
                    "source_path": "https://www.agora.io/en/about-agora/",
                }
            ],
            needs_engineer_guidance=False,
            answer_route="web_search",
            scope_label="agora_non_technical",
            route_reason="agora_public_info",
            route_confidence=0.93,
            search_used=True,
            matched_signals=["agora", "ceo"],
            route_family="web_company_info",
            execution_action="web_search",
            tooling_profile="official_web_search",
            needs_investigating=False,
            next_status="communicating",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=resolution,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:05+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task))

        saved_ticket = repository.save_ticket.call_args.args[0]
        assistant_message = saved_ticket["messages"][-1]
        self.assertEqual(saved_ticket["status"], "communicating")
        self.assertEqual(assistant_message["answer_route"], "web_search")
        self.assertEqual(assistant_message["scope_label"], "agora_non_technical")
        self.assertTrue(assistant_message["search_used"])
        event_payload = repository.record_event.call_args.args[2]
        self.assertEqual(event_payload["status"], "communicating")
        self.assertEqual(event_payload["answer_route"], "web_search")
        self.assertEqual(event_payload["scope_label"], "agora_non_technical")
        self.assertNotIn("engineer_mode", event_payload)
        self.assertNotIn("priority", event_payload)

    def test_process_ticket_query_post_check_rejection_starts_investigation(self) -> None:
        initial_ticket = _build_ticket()
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(initial_ticket),
            copy.deepcopy(initial_ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.save_investigation.return_value = None
        repository.record_event.return_value = None
        bus = Mock()

        execution = types.SimpleNamespace(
            answer="Please upgrade to SDK 4.2.2 and retry token renewal.",
            confidence=0.86,
            sources=["https://docs.agora.io/en/video-calling/token-authentication"],
            citations=[
                {
                    "chunk_id": "chunk-1",
                    "source_path": "official/token-authentication.md",
                    "heading": "Token authentication",
                    "source_url": "https://docs.agora.io/en/video-calling/token-authentication",
                }
            ],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="grounded_answer",
            route_confidence=0.93,
            search_used=False,
            matched_signals=["token", "android 14"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=True,
            next_status="investigating",
            investigation_reason="rag_post_check_insufficient",
            evidence_summary={
                "quality_signals": {
                    "generation_mode": "structured_answer",
                    "selected_doc_count": 1,
                },
                "selected_contexts": [],
            },
        )

        investigation_result = {
            "created": True,
            "public_reply": "This issue requires further internal investigation, which may take some time. Thank you for your patience. We expect to reply or update you here within 20 minutes.",
            "active_investigation": {
                "id": "INV-RETRY-1",
                "state": "active",
                "trigger_reason": "rag_post_check_insufficient",
                "trigger_source": "worker_async_rag",
                "messages": [
                    {
                        "id": "INV-RETRY-1-m1",
                        "role": "engineer_ai",
                        "content": "Please confirm whether Android 14 is the only affected platform.",
                        "created_at": "2026-03-22T00:01:05+00:00",
                    }
                ],
            },
            "new_internal_messages": [],
        }
        captured_opening_context = None

        def _start_or_refresh(ticket, **kwargs):
            nonlocal captured_opening_context
            captured_opening_context = copy.deepcopy(kwargs.get("opening_context"))
            ticket["status"] = "investigating"
            ticket["active_investigation"] = copy.deepcopy(investigation_result["active_investigation"])
            ticket["engineer_handoff_packet"] = {
                "source": "worker_async_rag",
                "conversation_summary": "Customer reports token renew callback never fires.",
                "latest_customer_message": "token renew callback never fires",
                "latest_client_ai_reply": "This issue requires further internal investigation, which may take some time. Thank you for your patience. We expect to reply or update you here within 20 minutes.",
                "route_summary": {
                    "answer_route": "rag",
                    "route_reason": "grounded_answer",
                },
                "rag_result": {
                    "candidate_answer": execution.answer,
                    "sources": list(execution.sources),
                    "citations": [dict(item) for item in execution.citations],
                    "evidence_summary": dict(execution.evidence_summary),
                },
                "unresolved_reason": "rag_post_check_insufficient",
                "customer_language_hint": "en",
                "created_at": "2026-03-22T00:01:05+00:00",
                "updated_at": "2026-03-22T00:01:05+00:00",
            }
            ticket["engineer_agent_state"] = {
                "phase": "gather_missing_inputs",
                "issue_understanding": "Token renew callback still fails after the upgrade attempt.",
                "knowledge_summary": "Client AI found generic token-authentication guidance but not enough Android 14-specific evidence.",
                "why_not_solved": "The current grounded answer is not enough to prove the Android-specific fix.",
                "goal": "Confirm Android 14 scope and exact SDK version before replying.",
                "known_facts": ["Customer confirmed the upgrade attempt already failed."],
                "missing_information": ["Exact SDK version", "Whether Android 14 is the only affected platform"],
                "next_request_for_engineer": "Please confirm Android 14 scope and exact SDK version.",
                "resolution_hypothesis": "The issue may be isolated to SDK 4.2.1 on Android 14.",
                "ready_to_reply": False,
                "last_refreshed_at": "2026-03-22T00:01:05+00:00",
            }
            return copy.deepcopy(investigation_result)

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "start_or_refresh_investigation",
            side_effect=_start_or_refresh,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:05+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task))

        saved_ticket = repository.save_ticket.call_args_list[0].args[0]
        saved_engineer_case = repository.save_engineer_case.call_args.kwargs["engineer_case"]
        self.assertEqual(saved_ticket["status"], "investigating")
        self.assertEqual(saved_ticket["messages"][-1]["content"], investigation_result["public_reply"])
        self.assertEqual(
            saved_engineer_case["engineer_handoff_packet"]["rag_result"]["candidate_answer"],
            "Please upgrade to SDK 4.2.2 and retry token renewal.",
        )
        self.assertEqual(saved_engineer_case["engineer_agent_state"]["phase"], "gather_missing_inputs")
        self.assertEqual(
            saved_engineer_case["engineer_agent_state"]["known_facts"],
            ["Customer confirmed the upgrade attempt already failed."],
        )
        self.assertEqual(saved_engineer_case["engineer_case_id"], "T-RETRY-1")
        self.assertEqual(saved_engineer_case["title"], "Token question")
        self.assertEqual(repository.save_engineer_case.call_count, 1)
        self.assertIsInstance(captured_opening_context, dict)
        self.assertIn("Need help with token generation", captured_opening_context["issue_summary"])
        self.assertIn(
            "Please upgrade to SDK 4.2.2 and retry token renewal.",
            captured_opening_context["rag_answer_summary"],
        )
        self.assertIn("Action Needed", f"Action Needed: {captured_opening_context['action_needed']}")
        self.assertEqual(
            captured_opening_context["sources"],
            ["https://docs.agora.io/en/video-calling/token-authentication"],
        )
        self.assertEqual(
            captured_opening_context["citations"][0]["source_url"],
            "https://docs.agora.io/en/video-calling/token-authentication",
        )
        first_event = repository.record_event.call_args_list[0].args[2]
        self.assertEqual(first_event["status"], "investigating")
        self.assertEqual(first_event["execution_action"], "rag")
        investigation_event = repository.record_event.call_args_list[1].args[2]
        self.assertEqual(investigation_event["agent_phase"], "gather_missing_inputs")

    def test_process_ticket_query_drops_stale_result_when_newer_customer_turn_exists(self) -> None:
        initial_ticket = _build_ticket(
            customer_message="First question",
            message_created_at="2026-03-22T00:00:00+00:00",
        )
        refreshed_ticket = copy.deepcopy(initial_ticket)
        refreshed_ticket["messages"].append(
            {
                "role": "customer",
                "content": "Second question",
                "created_at": "2026-03-22T00:01:00+00:00",
            }
        )
        refreshed_ticket["updated_at"] = "2026-03-22T00:01:00+00:00"

        repository = Mock()
        repository.get_ticket.return_value = copy.deepcopy(refreshed_ticket)
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        bus = Mock()

        execution = types.SimpleNamespace(
            answer="Old answer that should be dropped.",
            confidence=0.91,
            sources=["official/docs.md"],
            citations=[{"source": "official/docs.md", "label": "Docs"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="grounded_answer",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["token"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
        )

        task = dict(
            self.task,
            customer_message="First question",
            message_created_at="2026-03-22T00:00:00+00:00",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:05+00:00",
        ):
            worker._process_ticket_query(bus, task)

        repository.save_ticket.assert_not_called()
        repository.record_event.assert_not_called()

    def test_process_ticket_query_service_error_preserves_service_error_reason(self) -> None:
        initial_ticket = _build_ticket(
            ticket_id="T-SVCERR",
            customer_message="how to join channel",
        )
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(initial_ticket),
            copy.deepcopy(initial_ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.save_investigation.return_value = None
        repository.record_event.return_value = None
        bus = Mock()

        execution = types.SimpleNamespace(
            answer="I couldn't find enough information in the available support knowledge base to answer that question.",
            confidence=0.0,
            sources=[],
            citations=[],
            needs_engineer_guidance=True,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="rag_service_error",
            route_confidence=0.98,
            search_used=False,
            matched_signals=["join channel"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=True,
            next_status="investigating",
            investigation_reason="rag_service_error",
            evidence_summary=None,
        )

        captured_opening_context = None

        def _start_or_refresh(ticket, **kwargs):
            nonlocal captured_opening_context
            trigger_reason = kwargs["trigger_reason"]
            captured_opening_context = copy.deepcopy(kwargs.get("opening_context"))
            ticket["status"] = "investigating"
            ticket["active_investigation"] = {
                "id": "INV-SVCERR-1",
                "state": "active",
                "trigger_reason": trigger_reason,
                "trigger_source": "worker_async_rag",
                "messages": [
                    {
                        "id": "INV-SVCERR-1-m1",
                        "role": "engineer_ai",
                        "content": "RAG service failed before it returned a grounded answer.",
                        "created_at": "2026-03-22T00:01:05+00:00",
                    }
                ],
            }
            execution_context = kwargs.get("execution_context") or {}
            ticket["engineer_handoff_packet"] = {
                "source": "worker_async_rag",
                "conversation_summary": "Customer: how to join channel",
                "latest_customer_message": "how to join channel",
                "latest_client_ai_reply": "This issue requires further internal investigation, which may take some time. Thank you for your patience. We expect to reply or update you here within 20 minutes.",
                "route_summary": {
                    "answer_route": "rag",
                    "route_reason": execution_context.get("route_reason"),
                },
                "rag_result": {
                    "candidate_answer": "RAG service error prevented a grounded answer from being produced.",
                    "sources": [],
                    "citations": [],
                    "evidence_summary": {},
                },
                "unresolved_reason": trigger_reason,
                "customer_language_hint": "en",
                "created_at": "2026-03-22T00:01:05+00:00",
                "updated_at": "2026-03-22T00:01:05+00:00",
            }
            ticket["engineer_agent_state"] = {
                "phase": "gather_missing_inputs",
                "issue_understanding": "how to join channel",
                "knowledge_summary": "RAG service failed before a grounded answer was available.",
                "why_not_solved": "The RAG service failed before it could return a grounded answer, so client AI could not respond safely.",
                "goal": "Restore the RAG service path and rerun the customer query.",
                "known_facts": ["Customer reported: how to join channel"],
                "missing_information": ["Confirm the RAG service error type and the failing request trace."],
                "next_request_for_engineer": "Confirm the RAG service error type and the failing request trace.",
                "resolution_hypothesis": "",
                "ready_to_reply": False,
                "last_refreshed_at": "2026-03-22T00:01:05+00:00",
            }
            return {
                "created": True,
                "public_reply": "This issue requires further internal investigation, which may take some time. Thank you for your patience. We expect to reply or update you here within 20 minutes.",
                "active_investigation": copy.deepcopy(ticket["active_investigation"]),
                "new_internal_messages": [],
            }

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "start_or_refresh_investigation",
            side_effect=_start_or_refresh,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:05+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task, ticket_id="T-SVCERR", customer_message="how to join channel"))

        saved_ticket = repository.save_ticket.call_args_list[0].args[0]
        saved_engineer_case = repository.save_engineer_case.call_args.kwargs["engineer_case"]
        self.assertEqual(saved_ticket["status"], "investigating")
        self.assertEqual(saved_ticket["messages"][-1]["content"], "This issue requires further internal investigation, which may take some time. Thank you for your patience. We expect to reply or update you here within 20 minutes.")
        self.assertEqual(saved_engineer_case["trigger_reason"], "rag_service_error")
        self.assertEqual(saved_engineer_case["engineer_handoff_packet"]["route_summary"]["route_reason"], "rag_service_error")
        self.assertEqual(saved_engineer_case["engineer_handoff_packet"]["unresolved_reason"], "rag_service_error")
        self.assertEqual(
            saved_engineer_case["engineer_handoff_packet"]["rag_result"]["candidate_answer"],
            "RAG service error prevented a grounded answer from being produced.",
        )
        self.assertIsInstance(captured_opening_context, dict)
        self.assertIn("RAG service failed", captured_opening_context["rag_answer_summary"])
        first_event = repository.record_event.call_args_list[0].args[2]
        self.assertEqual(first_event["status"], "investigating")
        self.assertEqual(first_event["execution_action"], "rag")
        investigation_event = repository.record_event.call_args_list[1].args[2]
        self.assertEqual(investigation_event["agent_phase"], "gather_missing_inputs")
        self.assertFalse(investigation_event["agent_ready_to_reply"])
        self.assertEqual(
            investigation_event["agent_next_request_for_engineer"],
            "Confirm the RAG service error type and the failing request trace.",
        )

    def test_process_ticket_message_sentiment_persists_label_and_records_event(self) -> None:
        repository = Mock()
        repository.get_ticket.return_value = copy.deepcopy(_build_ticket())
        repository.update_message_sentiment_label.return_value = True
        repository.record_event.return_value = None
        bus = Mock()
        task = {
            "task_type": "ticket_message_sentiment",
            "ticket_id": "T-RETRY",
            "customer_message": "Need help with token generation",
            "message_created_at": "2026-03-22T00:00:00+00:00",
            "created_at": "2026-03-22T00:00:01+00:00",
        }

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "classify_sentiment",
            return_value=types.SimpleNamespace(
                bucket="negative",
                raw_label="anger",
                confidence=0.91,
                provider="test",
            ),
        ), patch.object(worker, "now_iso", return_value="2026-03-22T00:03:00+00:00"), patch.object(
            worker,
            "_publish",
        ) as publish_mock:
            worker._process_ticket_message_sentiment(bus, task)

        repository.update_message_sentiment_label.assert_called_once_with(
            ticket_id="T-RETRY",
            role="customer",
            content="Need help with token generation",
            created_at="2026-03-22T00:00:00+00:00",
            sentiment_label="bad",
        )
        event_payload = repository.record_event.call_args.args[2]
        self.assertEqual(event_payload["event"], "ticket_message_sentiment_tagged")
        self.assertEqual(event_payload["sentiment_label"], "bad")
        publish_mock.assert_called_once()

    def test_process_ticket_message_sentiment_skips_when_customer_message_cannot_be_updated(self) -> None:
        repository = Mock()
        repository.get_ticket.return_value = copy.deepcopy(_build_ticket())
        repository.update_message_sentiment_label.return_value = False
        repository.record_event.return_value = None
        bus = Mock()
        task = {
            "task_type": "ticket_message_sentiment",
            "ticket_id": "T-RETRY",
            "customer_message": "Need help with token generation",
            "message_created_at": "2026-03-22T00:00:00+00:00",
            "created_at": "2026-03-22T00:00:01+00:00",
        }

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "classify_sentiment",
            return_value=types.SimpleNamespace(
                bucket="neutral",
                raw_label="neutral",
                confidence=0.51,
                provider="test",
            ),
        ), patch.object(worker, "_publish") as publish_mock:
            worker._process_ticket_message_sentiment(bus, task)

        repository.record_event.assert_not_called()
        publish_mock.assert_not_called()

    def test_worker_task_types_from_env_filters_and_deduplicates_values(self) -> None:
        with patch.dict(
            os.environ,
            {"WORKER_TASK_TYPES": "ticket_query, ticket_message_sentiment, ticket_query,unknown"},
            clear=False,
        ):
            self.assertEqual(
                worker._worker_task_types_from_env(),
                ("ticket_query", "ticket_message_sentiment"),
            )

    def test_worker_task_types_from_env_defaults_to_all_supported_types(self) -> None:
        with patch.dict(os.environ, {"WORKER_TASK_TYPES": ""}, clear=False):
            self.assertEqual(
                worker._worker_task_types_from_env(),
                ("ticket_query", "ticket_message_sentiment"),
            )

    def test_handle_billing_request_reply_generates_customer_followup(self) -> None:
        repository = Mock()
        repository.claim_automation_reply.return_value = {"status": "acquired"}
        repository.commit_automation_reply_result.return_value = True
        repository.get_billing_ticket_by_client_ticket_id.return_value = {
            "billing_ticket_id": "BT-TK-ACC-1",
            "client_ticket_id": "TK-ACC-1",
            "title": "Detailed invoice request",
            "question": "Please send the detailed invoice.",
            "automation_status": "automation",
        }
        repository.get_ticket.return_value = {
            "ticket_id": "TK-ACC-1",
            "subject": "Detailed invoice request",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please send the detailed invoice for transaction 123.",
                    "created_at": "2026-07-02T00:00:00+00:00",
                },
                {
                    "role": "assistant",
                    "content": "We have escalated this to billing.",
                    "created_at": "2026-07-02T00:00:01+00:00",
                },
            ],
        }
        reply = types.SimpleNamespace(
            message_id="msg-1",
            subject="Re: [Billing Request] Detailed invoice request - Ticket TK-ACC-1",
            sender="billing@example.com",
            body_text="Done. The detailed invoice was sent to the customer email.",
            received_at="2026-07-02T08:14:38Z",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "record_billing_request_reply",
        ) as record_mock, patch.object(
            worker, "is_registered_automation", return_value=True,
        ), patch.object(
            worker, "_render_case_persona_reply", return_value="Hi Customer,\n\nThe detailed invoice is ready.\n\nBest,\nSid"
        ):
            handled = worker.handle_billing_request_reply(reply)

        self.assertTrue(handled)
        record_mock.assert_called_once_with(reply)
        repository.get_billing_ticket_by_client_ticket_id.assert_called_once_with("TK-ACC-1")
        repository.get_ticket.assert_called_once_with("TK-ACC-1")
        commit = repository.commit_automation_reply_result.call_args.kwargs
        self.assertEqual(commit["assistant_message"]["source"], "billing_reply_email")
        self.assertIn("detailed invoice", commit["assistant_message"]["content"].lower())
        self.assertEqual(commit["account_case_updates"]["automation_status"], "customer_notified")
        self.assertEqual([event["event_type"] for event in commit["events"]],
                         ["billing_internal_resolution_submitted", "billing_customer_followup_generated"])

    def test_handle_billing_request_reply_skips_duplicate_graph_message(self) -> None:
        repository = Mock()
        repository.claim_automation_reply.return_value = {"status": "already_completed"}
        reply = types.SimpleNamespace(
            message_id="msg-1",
            subject="Re: [Billing Request] Detailed invoice request - Ticket TK-ACC-1",
            sender="billing@example.com",
            body_text="Done. The detailed invoice was sent to the customer email.",
            received_at="2026-07-02T08:14:38Z",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "record_billing_request_reply",
        ) as record_mock:
            handled = worker.handle_billing_request_reply(reply)

        self.assertEqual(handled, "already_completed")
        record_mock.assert_not_called()
        repository.claim_automation_reply.assert_called_once()
        repository.get_billing_ticket_by_client_ticket_id.assert_not_called()
        repository.get_ticket.assert_not_called()
        repository.save_ticket.assert_not_called()
        repository.save_billing_ticket.assert_not_called()
        repository.record_event.assert_not_called()

    def test_inactive_detailed_invoice_reply_is_dismissed_before_side_effects(self) -> None:
        repository = Mock()
        repository.claim_automation_reply.return_value = {"status": "acquired"}
        repository.dismiss_automation_reply_claim.return_value = True
        repository.get_billing_ticket_by_client_ticket_id.return_value = {
            "billing_ticket_id": "BT-INACTIVE-INVOICE",
            "client_ticket_id": "TK-INACTIVE-INVOICE",
            "route_family": "automated",
            "execution_action": "detailed_invoice",
        }
        reply = types.SimpleNamespace(
            message_id="inactive-invoice-reply",
            subject="Re: [Billing Request] Detailed invoice - Ticket TK-INACTIVE-INVOICE",
            body_text="The detailed invoice is attached.",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "record_billing_request_reply"
        ), patch.object(
            worker, "_store_billing_reply_pdf_attachments", return_value=([], [])
        ) as store_attachments, patch.object(
            worker, "_queue_billing_completion_reply_job", return_value="completed"
        ) as queue_reply_job:
            handled = worker.handle_billing_request_reply(reply)

        self.assertEqual(handled, "completed")
        repository.dismiss_automation_reply_claim.assert_called_once()
        self.assertEqual(
            repository.dismiss_automation_reply_claim.call_args.kwargs["reason"],
            "inactive_automation",
        )
        repository.get_ticket.assert_not_called()
        store_attachments.assert_not_called()
        queue_reply_job.assert_not_called()
        repository.commit_automation_reply_result.assert_not_called()

    def test_enablement_resolution_reply_uses_canonical_feature_key(self) -> None:
        repository = Mock()
        repository.claim_automation_reply.return_value = {"status": "acquired"}
        repository.commit_automation_reply_result.return_value = True
        repository.get_billing_ticket_by_client_ticket_id.return_value = {
            "account_case_id": "AC-TK-ACC-2",
            "billing_ticket_id": "AC-TK-ACC-2",
            "client_ticket_id": "TK-ACC-2",
            "automation_handler": "enablement",
            "collected_fields": {
                "app_id": "7da36383d624411698e5c0bc1fda6324",
                "requested_feature": "media_relay",
                "requested_feature_label": "channel media rele",
            },
        }
        repository.get_ticket.return_value = {
            "ticket_id": "TK-ACC-2",
            "messages": [{"role": "customer", "content": "Please enable Media Relay."}],
        }
        reply = types.SimpleNamespace(
            message_id="enablement-msg-canonical",
            subject="Re: [Enablement Request] Media Relay - Ticket TK-ACC-2",
            body_text="The request is complete.",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_render_case_persona_reply",
            return_value="Hi Customer,\n\nThe request is complete.",
        ) as render, patch.object(
            worker,
            "classify_enablement_completion",
            return_value=_classifier_regex_fallback(),
        ):
            self.assertTrue(worker.handle_enablement_request_reply(reply))

        self.assertEqual(render.call_args.kwargs["known_information"]["requested_feature"], "media_relay")
        self.assertEqual(render.call_args.kwargs["known_information"]["requested_feature_label"], "channel media rele")

    def test_handle_enablement_request_reply_rejects_signed_generated_content(self) -> None:
        repository = Mock()
        repository.claim_automation_reply.return_value = {"status": "acquired"}
        repository.commit_automation_reply_result.return_value = True
        repository.get_billing_ticket_by_client_ticket_id.return_value = {
            "account_case_id": "AC-TK-ACC-2",
            "billing_ticket_id": "AC-TK-ACC-2",
            "client_ticket_id": "TK-ACC-2",
            "automation_handler": "enablement",
            "automation_status": "automation",
            "route_status": "automated",
            "collected_fields": {
                "app_id": "7da36383d624411698e5c0bc1fda6324",
                "requested_feature": "media_relay",
                "requested_feature_label": "Media Relay",
            },
        }
        repository.get_ticket.return_value = {
            "ticket_id": "TK-ACC-2",
            "messages": [{"role": "customer", "content": "Please enable Media Relay."}],
        }
        reply = types.SimpleNamespace(
            message_id="enablement-msg-1",
            subject="Re: [Enablement Request] Media Relay - Ticket TK-ACC-2",
            sender="enablement@example.com",
            body_text="Please ask the customer to add a payment method before activation.",
            received_at="2026-07-24T00:00:00Z",
        )

        generated_reply = "Hi there,\n\nPlease add a payment method before activation.\n\nBest Regards,\nSid"
        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "_render_case_persona_reply", return_value=generated_reply,
        ), patch.object(
            worker,
            "classify_enablement_completion",
            return_value=_classifier_regex_fallback(),
        ):
            handled = worker.handle_automation_request_reply(reply)

        self.assertTrue(handled)
        commit = repository.commit_automation_reply_result.call_args.kwargs
        self.assertIsNone(commit["assistant_message"])
        self.assertEqual(commit["account_case_updates"]["automation_status"], "human_review_required")
        self.assertEqual(commit["account_case_updates"]["policy_decision"], "automation_persona_human_review")
        self.assertEqual(len(commit["events"]), 1)
        event_payload = commit["events"][0]["payload"]
        self.assertEqual(event_payload["automation_reply_message_id"], "enablement-msg-1")

    def test_reply_with_missing_ticket_is_dismissed_at_claim(self) -> None:
        repository = Mock()
        repository.claim_automation_reply.side_effect = ValueError(
            "linked support ticket not found for 12998"
        )
        repository.record_dismissed_automation_reply.return_value = True
        reply = types.SimpleNamespace(
            message_id="cross-env-msg-2",
            subject="Re: [Enablement Request] Media Relay - Ticket 12998",
            body_text="It's enabled.",
        )
        with patch.object(worker, "ticket_repository", repository):
            handled = worker.handle_automation_request_reply(reply)

        self.assertEqual(handled, "already_completed")
        repository.record_dismissed_automation_reply.assert_called_once()
        kwargs = repository.record_dismissed_automation_reply.call_args.kwargs
        self.assertEqual(kwargs["reason"], "linked_ticket_not_found_at_claim")
        self.assertEqual(kwargs["client_ticket_id"], "12998")

    def test_billing_reply_without_case_is_dismissed_terminally(self) -> None:
        repository = Mock()
        repository.claim_automation_reply.return_value = {"status": "acquired"}
        repository.dismiss_automation_reply_claim.return_value = True
        repository.get_billing_ticket_by_client_ticket_id.return_value = None
        reply = types.SimpleNamespace(
            message_id="cross-env-msg-1",
            subject="Re: [Enablement Request] Media Relay - Ticket 12999",
            body_text="It's enabled.",
        )
        with patch.object(worker, "ticket_repository", repository):
            handled = worker.handle_automation_request_reply(reply)

        self.assertEqual(handled, "completed")
        repository.dismiss_automation_reply_claim.assert_called_once()
        kwargs = repository.dismiss_automation_reply_claim.call_args.kwargs
        self.assertEqual(kwargs["reason"], "account_case_not_found")
        repository.commit_automation_reply_result.assert_not_called()

    def test_enablement_explicit_completion_queues_closing_reply_job(self) -> None:
        repository = Mock()
        repository.claim_automation_reply.return_value = {"status": "acquired"}
        repository.commit_automation_reply_result.return_value = True
        repository.resolve_account_persona.return_value = None
        repository.get_billing_ticket_by_client_ticket_id.return_value = {
            "account_case_id": "AC-TK-ENABLEMENT-DONE",
            "billing_ticket_id": "AC-TK-ENABLEMENT-DONE",
            "client_ticket_id": "TK-ENABLEMENT-DONE",
            "automation_handler": "enablement",
            "automation_status": "automation",
            "processing_profile": "production",
            "collected_fields": {"requested_feature": "media_relay"},
            "internal_email_payload": {"delivery_key": "enablement-delivery-1"},
        }
        repository.get_ticket.return_value = {
            "ticket_id": "TK-ENABLEMENT-DONE",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable Media Relay.",
                    "created_at": "2026-08-19T08:00:00+00:00",
                }
            ],
        }
        repository.save_account_reply_job.side_effect = lambda job: job
        reply = types.SimpleNamespace(
            message_id="enablement-msg-done",
            subject="Re: [Enablement Request] Media Relay - Ticket TK-ENABLEMENT-DONE",
            body_text="Media Relay has been enabled successfully.",
        )
        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "classify_enablement_completion",
        ) as classifier:
            handled = worker.handle_enablement_request_reply(reply)

        classifier.assert_not_called()
        self.assertEqual(handled, "completed")
        repository.cancel_pending_account_reply_jobs.assert_called_once_with(
            "TK-ENABLEMENT-DONE", updated_at=unittest.mock.ANY
        )
        saved_job = repository.save_account_reply_job.call_args.args[0]
        payload = saved_job["payload"]
        self.assertEqual(payload["reply_intent"], "enablement_completed_and_close")
        self.assertTrue(payload["close_after_publish"])
        # The completion is internal-triggered: it must carry the marker that
        # exempts it from the customer-currency gate and its own trigger
        # timestamp so it never collides with the submission job for the same
        # customer message.
        self.assertTrue(payload["internal_resolution"])
        self.assertEqual(payload["automation_delivery_key"], "enablement-delivery-1")
        self.assertEqual(payload["reply_facts"]["reply_intent"], "enablement_completed_and_close")
        self.assertEqual(payload["reply_facts"]["completion_acknowledgement"], "patience")
        commit = repository.commit_automation_reply_result.call_args.kwargs
        self.assertIsNone(commit["assistant_message"])
        self.assertNotIn("close_after_publish", commit)
        self.assertEqual(commit["account_case_updates"]["automation_status"], "automation")

    def test_enablement_completion_acknowledges_customer_information_after_missing_request(self) -> None:
        repository = Mock()
        repository.claim_automation_reply.return_value = {"status": "acquired"}
        repository.commit_automation_reply_result.return_value = True
        repository.resolve_account_persona.return_value = None
        repository.get_billing_ticket_by_client_ticket_id.return_value = {
            "account_case_id": "AC-TK-ENABLEMENT-FOLLOWUP",
            "billing_ticket_id": "AC-TK-ENABLEMENT-FOLLOWUP",
            "client_ticket_id": "TK-ENABLEMENT-FOLLOWUP",
            "automation_handler": "enablement",
            "automation_status": "automation",
            "processing_profile": "production",
            "collected_fields": {"requested_feature": "media_relay"},
            "internal_email_payload": {"delivery_key": "enablement-delivery-followup"},
        }
        repository.get_ticket.return_value = {
            "ticket_id": "TK-ENABLEMENT-FOLLOWUP",
            "messages": [
                {"role": "customer", "content": "Please enable Media Relay."},
                {
                    "role": "assistant",
                    "content": "Could you please provide your App ID?",
                    "reply_intent": "request_missing_information",
                },
                {"role": "customer", "content": "Here is the App ID."},
            ],
        }
        repository.save_account_reply_job.side_effect = lambda job: job
        reply = types.SimpleNamespace(
            message_id="enablement-msg-followup",
            subject="Re: [Enablement Request] Media Relay - Ticket TK-ENABLEMENT-FOLLOWUP",
            body_text="Media Relay has been enabled successfully.",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "classify_enablement_completion"
        ) as classifier:
            handled = worker.handle_enablement_request_reply(reply)

        classifier.assert_not_called()
        self.assertEqual(handled, "completed")
        saved_job = repository.save_account_reply_job.call_args.args[0]
        self.assertEqual(
            saved_job["payload"]["reply_facts"]["completion_acknowledgement"],
            "additional_information",
        )
        repository.cancel_pending_account_reply_jobs.assert_called_once_with(
            "TK-ENABLEMENT-FOLLOWUP", updated_at=unittest.mock.ANY
        )

    def test_enablement_non_completion_reply_does_not_close(self) -> None:
        repository = Mock()
        repository.claim_automation_reply.return_value = {"status": "acquired"}
        repository.commit_automation_reply_result.return_value = True
        repository.get_billing_ticket_by_client_ticket_id.return_value = {
            "account_case_id": "AC-TK-ENABLEMENT-NOT-DONE",
            "billing_ticket_id": "AC-TK-ENABLEMENT-NOT-DONE",
            "client_ticket_id": "TK-ENABLEMENT-NOT-DONE",
            "automation_handler": "enablement",
            "automation_status": "automation",
            "collected_fields": {"requested_feature": "media_relay"},
        }
        repository.get_ticket.return_value = {
            "ticket_id": "TK-ENABLEMENT-NOT-DONE",
            "messages": [{"role": "customer", "content": "Please enable Media Relay."}],
        }
        reply = types.SimpleNamespace(
            message_id="enablement-msg-not-done",
            subject="Re: [Enablement Request] Media Relay - Ticket TK-ENABLEMENT-NOT-DONE",
            body_text="We are unable to enable Media Relay at this time.",
        )
        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_render_case_persona_reply",
            return_value="Hi Customer,\n\nWe are still investigating this request.",
        ), patch.object(
            worker,
            "classify_enablement_completion",
            return_value=EnablementCompletionClassification(
                completed=False, source="regex_fallback", failure_reason="invocation_failed"
            ),
        ):
            handled = worker.handle_enablement_request_reply(reply)

        self.assertEqual(handled, "completed")
        commit = repository.commit_automation_reply_result.call_args.kwargs
        self.assertNotIn("close_after_publish", commit)
        self.assertEqual(commit["account_case_updates"]["automation_status"], "customer_notified")

    def test_enablement_completion_llm_classifier_upgrades_non_english_reply(self) -> None:
        repository = Mock()
        repository.claim_automation_reply.return_value = {"status": "acquired"}
        repository.commit_automation_reply_result.return_value = True
        repository.save_account_reply_job.side_effect = lambda job: job
        repository.resolve_account_persona.return_value = {
            "persona_key": "sid_precise",
            "version": "test",
            "content": {},
        }
        repository.get_billing_ticket_by_client_ticket_id.return_value = {
            "account_case_id": "AC-TK-ENABLEMENT-CN",
            "billing_ticket_id": "AC-TK-ENABLEMENT-CN",
            "client_ticket_id": "TK-ENABLEMENT-CN",
            "automation_handler": "enablement",
            "automation_status": "automation",
            "processing_profile": "production",
            "collected_fields": {"requested_feature": "media_relay", "requested_feature_label": "Media Relay"},
        }
        repository.get_ticket.return_value = {
            "ticket_id": "TK-ENABLEMENT-CN",
            "messages": [{"role": "customer", "content": "Please enable Media Relay."}],
        }
        reply = types.SimpleNamespace(
            message_id="enablement-msg-cn",
            subject="Re: [Enablement Request] Media Relay - Ticket TK-ENABLEMENT-CN",
            body_text="已开通",
        )
        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "classify_enablement_completion",
            return_value=EnablementCompletionClassification(
                completed=True, source="llm", failure_reason=None
            ),
        ) as classifier:
            handled = worker.handle_enablement_request_reply(reply)

        classifier.assert_called_once()
        self.assertEqual(classifier.call_args.args[0], "已开通")
        self.assertEqual(classifier.call_args.kwargs["feature_label"], "Media Relay")
        self.assertEqual(handled, "completed")
        repository.cancel_pending_account_reply_jobs.assert_called_once_with(
            "TK-ENABLEMENT-CN", updated_at=unittest.mock.ANY
        )
        saved_job = repository.save_account_reply_job.call_args.args[0]
        payload = saved_job["payload"]
        self.assertEqual(payload["reply_intent"], "enablement_completed_and_close")
        self.assertTrue(payload["close_after_publish"])
        resolution_event = next(
            event["payload"]
            for event in repository.commit_automation_reply_result.call_args.kwargs["events"]
            if event["event_type"] == "enablement_internal_resolution_received"
        )
        self.assertEqual(resolution_event["completion_source"], "llm")

    def test_enablement_completion_detection_requires_current_positive_state(self) -> None:
        invalid_notes = (
            "Is Media Relay enabled?",
            "Please enable Media Relay.",
            "Media Relay will be enabled tomorrow.",
            "Media Relay was enabled but is now disabled.",
            "Media Relay was enabled. It has since been turned off.",
        )
        for note in invalid_notes:
            with self.subTest(note=note):
                self.assertFalse(worker._enablement_reply_explicitly_confirms_completion(note))

        for note in (
            "Media Relay is enabled.",
            "We have activated Media Relay.",
        ):
            with self.subTest(note=note):
                self.assertTrue(worker._enablement_reply_explicitly_confirms_completion(note))

    def test_enablement_reply_subject_accepts_numeric_zendesk_ticket_id(self) -> None:
        self.assertEqual(
            worker._ticket_id_from_billing_reply_subject(
                "Re: [Enablement Request] Media Relay - Ticket 12488"
            ),
            "12488",
        )

    def test_handle_quota_request_reply_notifies_customer_and_keeps_automated_route(self) -> None:
        repository = Mock()
        repository.claim_automation_reply.return_value = {"status": "acquired"}
        repository.commit_automation_reply_result.return_value = True
        repository.get_billing_ticket_by_client_ticket_id.return_value = {
            "account_case_id": "AC-12512",
            "billing_ticket_id": "AC-12512",
            "client_ticket_id": "12512",
            "automation_handler": "quota",
            "automation_status": "internal_processing",
            "route_status": "automated",
            "collected_fields": {"app_ids": ["app-prod"], "products": ["rtc", "rtm", "chat"]},
        }
        repository.get_ticket.return_value = {
            "ticket_id": "12512",
            "messages": [{"role": "customer", "content": "Please increase our concurrency limits."}],
            "customer_id": "customer@example.com",
        }
        reply = types.SimpleNamespace(
            message_id="quota-msg-1",
            subject="Re: [Quota Request] RTC, RTM, Chat - Ticket 12512",
            body_text="The requested limits are approved for the event window.",
        )
        generated_reply = "Hi there,\n\nThe requested limits are approved for the event window."

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "_render_case_persona_reply", return_value=generated_reply,
        ):
            handled = worker.handle_automation_request_reply(reply)

        self.assertTrue(handled)
        commit = repository.commit_automation_reply_result.call_args.kwargs
        self.assertEqual(commit["account_case_updates"]["automation_status"], "customer_notified")
        self.assertEqual(commit["assistant_message"]["source"], "quota_reply_email")
        self.assertEqual(len(commit["events"]), 2)

    def test_handle_enablement_request_reply_is_idempotent(self) -> None:
        repository = Mock()
        repository.claim_automation_reply.return_value = {"status": "already_completed"}
        reply = types.SimpleNamespace(
            message_id="enablement-msg-1",
            subject="Re: [Enablement Request] Media Relay - Ticket TK-ACC-2",
            body_text="Enabled.",
        )

        with patch.object(worker, "ticket_repository", repository):
            handled = worker.handle_enablement_request_reply(reply)

        self.assertEqual(handled, "already_completed")
        repository.get_billing_ticket_by_client_ticket_id.assert_not_called()
        repository.save_ticket.assert_not_called()
        repository.save_account_case.assert_not_called()

    def test_enablement_delivery_retry_sends_once_and_queues_confirmation(self) -> None:
        account_case = {
            "account_case_id": "AC-12495",
            "client_ticket_id": "12495",
            "processing_profile": "staging",
            "automation_handler": "enablement",
            "missing_fields": [],
            "collected_fields": {
                "app_id": "project-one",
                "requested_feature": "media_relay",
                "requested_feature_label": "Media Relay",
            },
            "internal_email_payload": {
                "delivery_key": "enablement:AC-12495:v1",
                "subject": "[Enablement Request] Media Relay - Ticket 12495",
                "body": "Internal request",
            },
            "internal_email_send_status": "retry",
            "updated_at": "2026-07-24T00:00:00+00:00",
        }
        repository = Mock()
        repository.list_billing_tickets.return_value = [account_case]
        repository.get_ticket.return_value = {
            "ticket_id": "12495",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable Media Relay.",
                    "created_at": "2026-07-23T23:59:00+00:00",
                }
            ],
        }
        repository.get_latest_account_reply_job.return_value = None
        repository.resolve_account_persona.return_value = None

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "send_enablement_internal_email",
            return_value={
                "status": "sent",
                "reason": "",
                "resolved_to": "enablement@example.com",
            },
        ) as send_mail:
            first = worker.retry_enablement_internal_deliveries_once()
            second = worker.retry_enablement_internal_deliveries_once()

        self.assertEqual(first["sent"], 1)
        self.assertEqual(first["confirmations"], 1)
        self.assertEqual(second["sent"], 0)
        send_mail.assert_called_once()
        sent_payload = send_mail.call_args.args[0]
        self.assertEqual(sent_payload["body_content_type"], "HTML")
        self.assertIn("body_html", sent_payload)
        self.assertNotEqual(sent_payload["body"], "Internal request")
        repository.save_account_reply_job.assert_called_once()
        reply_job = repository.save_account_reply_job.call_args.args[0]
        self.assertEqual(
            reply_job["payload"]["automation_delivery_key"],
            "enablement:AC-12495:v1",
        )
        self.assertEqual(reply_job["trigger_message_created_at"], "2026-07-23T23:59:00+00:00")
        self.assertEqual(reply_job["scheduled_for"], reply_job["created_at"])
        self.assertNotIn("it enablement", reply_job["payload"]["draft_content"])
        self.assertEqual(account_case["internal_email_send_status"], "sent")
        self.assertTrue(account_case["internal_email_payload"]["customer_confirmation_queued"])

    def test_enablement_confirmation_keeps_production_reply_delay(self) -> None:
        account_case = {
            "account_case_id": "AC-PRODUCTION-CONFIRMATION",
            "client_ticket_id": "99887770",
            "processing_profile": "production",
            "collected_fields": {"requested_feature": "media_relay"},
            "internal_email_payload": {"delivery_key": "enablement:AC-PRODUCTION-CONFIRMATION:v1"},
        }
        repository = Mock()
        repository.get_ticket.return_value = {
            "ticket_id": "99887770",
            "messages": [{
                "role": "customer",
                "content": "Please enable Media Relay.",
                "created_at": "2026-08-19T00:00:00+00:00",
            }],
        }
        repository.get_latest_account_reply_job.return_value = None
        repository.resolve_account_persona.return_value = None

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "account_reply_delay_seconds_for_profile", return_value=417
        ) as delay:
            created = worker._queue_enablement_submission_confirmation(account_case)

        self.assertTrue(created)
        reply_job = repository.save_account_reply_job.call_args.args[0]
        created_at = datetime.fromisoformat(reply_job["created_at"])
        scheduled_for = datetime.fromisoformat(reply_job["scheduled_for"])
        self.assertEqual((scheduled_for - created_at).total_seconds(), 417)
        delay.assert_called_once_with("production")

    def test_enablement_delivery_retry_replaces_malformed_cancelled_confirmation(self) -> None:
        account_case = {
            "account_case_id": "AC-12513",
            "client_ticket_id": "12513",
            "customer_name": "Jack Gold",
            "automation_handler": "enablement",
            "missing_fields": [],
            "collected_fields": {
                "requested_feature": "media_relay",
                "requested_feature_label": "Media Relay",
            },
            "internal_email_payload": {
                "delivery_key": "enablement:AC-12513:v1",
                "customer_confirmation_queued": True,
            },
            "internal_email_send_status": "sent",
        }
        repository = Mock()
        repository.list_billing_tickets.return_value = [account_case]
        repository.get_ticket.return_value = {
            "ticket_id": "12513",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable Media Relay.",
                    "created_at": "2026-07-29T19:50:01+00:00",
                }
            ],
        }
        repository.get_latest_account_reply_job.return_value = {
            "status": "cancelled",
            "trigger_message_created_at": "2026-07-30T02:09:35+00:00",
            "payload": {"automation_delivery_key": "enablement:AC-12513:v1"},
        }
        repository.resolve_account_persona.return_value = None

        with patch.object(worker, "ticket_repository", repository):
            created = worker._queue_enablement_submission_confirmation(
                account_case,
                repair_malformed_cancelled=True,
            )

        self.assertTrue(created)
        replacement = repository.save_account_reply_job.call_args.args[0]
        self.assertEqual(replacement["trigger_message_created_at"], "2026-07-29T19:50:01+00:00")
        self.assertEqual(replacement["payload"]["reply_facts"]["customer_first_name"], "Jack")

    def test_enablement_delivery_retry_does_not_duplicate_existing_rerun_confirmation(self) -> None:
        account_case = {
            "account_case_id": "AC-12570",
            "client_ticket_id": "12570",
            "automation_handler": "enablement",
            "missing_fields": [],
            "collected_fields": {"requested_feature": "media_relay"},
            "internal_email_payload": {
                "delivery_key": "enablement:AC-12570:v1",
            },
            "internal_email_send_status": "sent",
        }
        repository = Mock()
        repository.get_ticket.return_value = {
            "ticket_id": "12570",
            "messages": [
                {
                    "role": "customer",
                    "created_at": "2026-08-02T14:04:29.472437+00:00",
                }
            ],
        }
        repository.get_latest_account_reply_job.return_value = {
            "status": "cancelled",
            "trigger_message_created_at": "2026-08-02T14:04:29.472437+00:00",
            "payload": {
                "rerun_job_id": "account-rerun-1:recovery",
                "automation_delivery_key": "enablement:AC-12570:v1:rerun:account-rerun-1",
            },
        }

        with patch.object(worker, "ticket_repository", repository):
            created = worker._queue_enablement_submission_confirmation(account_case)

        self.assertFalse(created)
        repository.save_account_reply_job.assert_not_called()

    def test_enablement_delivery_retry_skips_rerun_owned_pending_delivery(self) -> None:
        account_case = {
            "account_case_id": "AC-RERUN-FENCE",
            "client_ticket_id": "12570",
            "automation_handler": "enablement",
            "missing_fields": [],
            "internal_email_payload": {
                "delivery_key": "enablement:AC-RERUN-FENCE:v1:rerun:account-rerun-failed",
                "to": "enablement@example.com",
            },
            "internal_email_send_status": "retry",
            "updated_at": "2026-07-24T00:00:00+00:00",
        }
        repository = Mock()
        repository.list_billing_tickets.return_value = [account_case]

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "send_enablement_internal_email",
        ) as send_mail:
            result = worker.retry_enablement_internal_deliveries_once()

        self.assertEqual(result["rerun_owned_skipped"], 1)
        self.assertEqual(result["examined"], 0)
        self.assertEqual(result["confirmations"], 0)
        send_mail.assert_not_called()
        repository.save_account_case.assert_not_called()
        repository.save_account_reply_job.assert_not_called()

    def test_enablement_delivery_retry_does_not_revive_confirmation_after_customer_reply(self) -> None:
        account_case = {
            "account_case_id": "AC-12513",
            "client_ticket_id": "12513",
            "automation_handler": "enablement",
            "missing_fields": [],
            "collected_fields": {"requested_feature": "media_relay"},
            "internal_email_payload": {
                "delivery_key": "enablement:AC-12513:v1",
                "customer_confirmation_queued": True,
            },
            "internal_email_send_status": "sent",
        }
        repository = Mock()
        repository.get_ticket.return_value = {
            "ticket_id": "12513",
            "messages": [
                {"role": "customer", "created_at": "2026-07-29T19:50:01+00:00"},
                {"role": "customer", "created_at": "2026-07-30T03:00:00+00:00"},
            ],
        }
        repository.get_latest_account_reply_job.return_value = {
            "status": "cancelled",
            "trigger_message_created_at": "2026-07-29T19:50:01+00:00",
            "payload": {"automation_delivery_key": "enablement:AC-12513:v1"},
        }

        with patch.object(worker, "ticket_repository", repository):
            created = worker._queue_enablement_submission_confirmation(
                account_case,
                repair_malformed_cancelled=True,
            )

        self.assertFalse(created)
        repository.save_account_reply_job.assert_not_called()

    def test_enablement_delivery_retry_ignores_legacy_sent_case_without_delivery_key(self) -> None:
        repository = Mock()
        repository.list_billing_tickets.return_value = [
            {
                "account_case_id": "AC-legacy",
                "client_ticket_id": "legacy",
                "automation_handler": "enablement",
                "missing_fields": [],
                "internal_email_payload": {"subject": "Legacy request", "body": "Body"},
                "internal_email_send_status": "sent",
            }
        ]

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "send_enablement_internal_email",
        ) as send_mail:
            result = worker.retry_enablement_internal_deliveries_once()

        self.assertEqual(result["examined"], 0)
        self.assertEqual(result["confirmations"], 0)
        send_mail.assert_not_called()
        repository.save_account_reply_job.assert_not_called()

    def test_enablement_delivery_retry_marks_unreconstructable_payload_for_manual_attention(self) -> None:
        account_case = {
            "account_case_id": "AC-unrecoverable",
            "client_ticket_id": "unrecoverable",
            "automation_handler": "enablement",
            "missing_fields": [],
            "collected_fields": {},
            "internal_email_payload": {
                "delivery_key": "enablement:AC-unrecoverable:v1",
                "body": "legacy body",
            },
            "internal_email_send_status": "retry",
            "updated_at": "2026-07-24T00:00:00+00:00",
        }
        repository = Mock()
        repository.list_billing_tickets.return_value = [account_case]
        repository.get_ticket.return_value = {"ticket_id": "unrecoverable", "messages": []}
        repository.claim_account_internal_email_delivery.return_value = True
        repository.complete_account_internal_email_delivery.return_value = True

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "send_enablement_internal_email",
        ) as send_mail:
            result = worker.retry_enablement_internal_deliveries_once()

        self.assertEqual(result["sent"], 0)
        self.assertEqual(account_case["internal_email_send_status"], "manual_attention")
        send_mail.assert_not_called()
        repository.complete_account_internal_email_delivery.assert_called_once()

    def test_handle_billing_request_reply_rejects_empty_body_before_marking_read(self) -> None:
        repository = Mock()
        repository.claim_automation_reply.return_value = {"status": "acquired"}
        repository.get_billing_ticket_by_client_ticket_id.return_value = {
            "billing_ticket_id": "BT-TK-ACC-1",
            "client_ticket_id": "TK-ACC-1",
        }
        repository.get_ticket.return_value = {"ticket_id": "TK-ACC-1", "messages": []}
        reply = types.SimpleNamespace(
            message_id="msg-empty",
            subject="Re: [Billing Request] Detailed invoice request - Ticket TK-ACC-1",
            sender="billing@example.com",
            body_text="",
            received_at="2026-07-02T08:14:38Z",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "record_billing_request_reply",
        ) as record_mock, patch.object(
            worker, "is_registered_automation", return_value=True,
        ):
            with self.assertRaisesRegex(ValueError, "body is empty"):
                worker.handle_billing_request_reply(reply)

        record_mock.assert_called_once_with(reply)
        repository.save_ticket.assert_not_called()
        repository.save_billing_ticket.assert_not_called()
        repository.record_event.assert_not_called()
        repository.fail_automation_reply_claim.assert_called_once()

    def test_handle_billing_request_reply_uses_pdf_ocr_text_when_body_is_empty(self) -> None:
        repository = Mock()
        repository.claim_automation_reply.return_value = {"status": "acquired"}
        repository.commit_automation_reply_result.return_value = True
        repository.get_billing_ticket_by_client_ticket_id.return_value = {
            "billing_ticket_id": "BT-TK-ACC-1",
            "client_ticket_id": "TK-ACC-1",
            "title": "Detailed invoice request",
            "question": "Please send the detailed invoice.",
            "automation_status": "automation",
        }
        repository.get_ticket.return_value = {
            "ticket_id": "TK-ACC-1",
            "subject": "Detailed invoice request",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please send the detailed invoice for transaction 123.",
                    "created_at": "2026-07-02T00:00:00+00:00",
                }
            ],
        }
        reply = types.SimpleNamespace(
            message_id="msg-pdf-only",
            subject="Re: [Billing Request] Detailed invoice request - Ticket TK-ACC-1",
            sender="billing@example.com",
            body_text="",
            attachment_names=("invoice-approval.pdf",),
            attachment_text="Invoice total: USD 705.97\nApproved by finance.",
            received_at="2026-07-02T08:14:38Z",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "record_billing_request_reply",
        ) as record_mock, patch.object(
            worker, "is_registered_automation", return_value=True,
        ), patch.object(
            worker, "_render_case_persona_reply", return_value="Hi Customer,\n\nThe detailed invoice is ready.\n\nBest,\nSid"
        ):
            worker.handle_billing_request_reply(reply)

        record_mock.assert_called_once_with(reply)
        commit = repository.commit_automation_reply_result.call_args.kwargs
        self.assertEqual(commit["assistant_message"]["source"], "billing_reply_email")
        self.assertIn("detailed invoice", commit["assistant_message"]["content"].lower())
        resolution_payload = commit["events"][0]["payload"]
        self.assertIn("[PDF attachment: invoice-approval.pdf]", resolution_payload["note"])
        self.assertIn("Invoice total: USD 705.97", resolution_payload["note"])

    def test_handle_billing_request_reply_attaches_pdf_to_customer_message_without_ocr(self) -> None:
        repository = Mock()
        publication_order: list[str] = []
        repository.claim_automation_reply.return_value = {"status": "acquired"}
        repository.commit_automation_reply_result.side_effect = (
            lambda *_args, **_kwargs: publication_order.append("commit") or True
        )
        repository.get_billing_ticket_by_client_ticket_id.return_value = {
            "billing_ticket_id": "BT-TK-ACC-1",
            "client_ticket_id": "TK-ACC-1",
            "title": "Detailed invoice request",
            "question": "Please send the detailed invoice.",
            "automation_status": "automation",
        }
        repository.get_ticket.return_value = {
            "ticket_id": "TK-ACC-1",
            "customer_id": "C-001",
            "subject": "Detailed invoice request",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please send the detailed invoice for transaction 123.",
                    "created_at": "2026-07-02T00:00:00+00:00",
                }
            ],
        }
        asset_repository = Mock()
        asset_repository.get_asset.return_value = None
        asset_repository.create_asset.side_effect = lambda asset: {
            **asset,
            "status": "uploaded",
            "created_at": "2026-07-02T08:14:38Z",
            "uploaded_at": "2026-07-02T08:14:38Z",
            "attached_at": None,
        }
        asset_repository.mark_attached.side_effect = (
            lambda *_args, **_kwargs: publication_order.append("attach") or []
        )
        asset_storage = Mock()
        asset_storage.bucket = "supportportal-assets"
        asset_storage.store_bytes.return_value = {"etag": "etag-1", "checksum": "checksum-1"}
        reply = types.SimpleNamespace(
            message_id="msg-pdf",
            subject="Re: [Billing Request] Detailed invoice request - Ticket TK-ACC-1",
            sender="billing@example.com",
            body_text="Approved. Please send the attached invoice to the customer.",
            attachment_names=("invoice-approval.pdf",),
            attachments=(
                types.SimpleNamespace(
                    name="invoice-approval.pdf",
                    content_type="application/pdf",
                    content=b"%PDF-1.4\nfake billing approval\n%%EOF",
                    size_bytes=32,
                ),
            ),
            received_at="2026-07-02T08:14:38Z",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "record_billing_request_reply",
        ) as record_mock, patch.object(
            worker, "is_registered_automation", return_value=True,
        ), patch.object(
            worker, "_render_case_persona_reply",
            return_value="Hi Customer,\n\nThe attached invoice is ready.\n\nBest,\nSid",
        ), patch.object(
            worker,
            "asset_repository",
            asset_repository,
            create=True,
        ), patch.object(
            worker,
            "asset_storage",
            asset_storage,
            create=True,
        ), patch.object(worker.hashlib, "sha256") as digest:
            digest.return_value.hexdigest.return_value = "abcdef1234560000000000000000000000000000000000000000000000000000"
            worker.handle_billing_request_reply(reply)

        record_mock.assert_called_once_with(reply)
        asset_storage.store_bytes.assert_called_once()
        stored_asset = asset_repository.create_asset.call_args.args[0]
        self.assertEqual(stored_asset["asset_id"], "ASSET-ABCDEF123456000000000000")
        self.assertEqual(stored_asset["ticket_id"], "TK-ACC-1")
        self.assertEqual(stored_asset["customer_id"], "C-001")
        self.assertEqual(stored_asset["original_filename"], "invoice-approval.pdf")
        self.assertEqual(stored_asset["content_type"], "application/pdf")
        self.assertEqual(stored_asset["extension"], ".pdf")
        self.assertEqual(stored_asset["status"], "uploaded")
        asset_repository.mark_attached.assert_called_once_with(["ASSET-ABCDEF123456000000000000"])
        self.assertEqual(publication_order, ["commit", "attach"])
        assistant_message = repository.commit_automation_reply_result.call_args.kwargs["assistant_message"]
        self.assertEqual(assistant_message["source"], "billing_reply_email")
        self.assertEqual(assistant_message["attachments"][0]["asset_id"], "ASSET-ABCDEF123456000000000000")
        self.assertEqual(assistant_message["attachments"][0]["original_filename"], "invoice-approval.pdf")
        self.assertIn("attached", assistant_message["content"].lower())
        self.assertNotIn("sent to your email", assistant_message["content"].lower())

    def test_detailed_invoice_reply_queues_closing_reply_job_with_pdf_attachments(self) -> None:
        repository = Mock()
        publication_order: list[str] = []
        repository.claim_automation_reply.return_value = {"status": "acquired"}
        repository.commit_automation_reply_result.side_effect = (
            lambda *_args, **_kwargs: publication_order.append("commit") or True
        )
        repository.resolve_account_persona.return_value = None
        repository.save_account_reply_job.side_effect = lambda job: job
        repository.get_billing_ticket_by_client_ticket_id.return_value = {
            "account_case_id": "AC-TK-DI-DONE",
            "billing_ticket_id": "AC-TK-DI-DONE",
            "client_ticket_id": "TK-DI-DONE",
            "title": "Detailed invoice request",
            "execution_action": "detailed_invoice",
            "automation_handler": "billing",
            "automation_status": "automation",
            "processing_profile": "production",
            "internal_email_payload": {"delivery_key": "billing-delivery-1"},
        }
        repository.get_ticket.return_value = {
            "ticket_id": "TK-DI-DONE",
            "customer_id": "C-001",
            "subject": "Detailed invoice request",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please send the detailed invoice for transaction 123.",
                    "created_at": "2026-08-23T00:00:00+00:00",
                }
            ],
        }
        asset_repository = Mock()
        asset_repository.get_asset.return_value = None
        asset_repository.create_asset.side_effect = lambda asset: {
            **asset,
            "status": "uploaded",
            "created_at": "2026-08-23T08:14:38Z",
            "uploaded_at": "2026-08-23T08:14:38Z",
            "attached_at": None,
        }
        asset_repository.mark_attached.side_effect = (
            lambda *_args, **_kwargs: publication_order.append("attach") or []
        )
        asset_storage = Mock()
        asset_storage.bucket = "supportportal-assets"
        asset_storage.store_bytes.return_value = {"etag": "etag-1", "checksum": "checksum-1"}
        reply = types.SimpleNamespace(
            message_id="msg-di-pdf",
            subject="Re: [Billing Request] Detailed invoice request - Ticket TK-DI-DONE",
            sender="billing@example.com",
            body_text="The detailed invoice is attached.",
            attachment_names=("invoice-approval.pdf",),
            attachments=(
                types.SimpleNamespace(
                    name="invoice-approval.pdf",
                    content_type="application/pdf",
                    content=b"%PDF-1.4\nfake invoice\n%%EOF",
                    size_bytes=28,
                ),
            ),
            received_at="2026-08-23T08:14:38Z",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "record_billing_request_reply"
        ), patch.object(
            worker, "is_registered_automation", return_value=True,
        ), patch.object(
            worker, "_render_case_persona_reply"
        ) as render_inline, patch.object(
            worker,
            "asset_repository",
            asset_repository,
            create=True,
        ), patch.object(
            worker,
            "asset_storage",
            asset_storage,
            create=True,
        ), patch.object(worker.hashlib, "sha256") as digest:
            digest.return_value.hexdigest.return_value = "abcdef1234560000000000000000000000000000000000000000000000000000"
            handled = worker.handle_billing_request_reply(reply)

        # The detailed-invoice completion no longer renders inline: the reply
        # job pipeline owns the persona render and the Zendesk delivery.
        render_inline.assert_not_called()
        self.assertEqual(handled, "completed")
        asset_storage.store_bytes.assert_called_once()
        asset_repository.mark_attached.assert_called_once_with(["ASSET-ABCDEF123456000000000000"])
        self.assertEqual(publication_order, ["commit", "attach"])
        repository.cancel_pending_account_reply_jobs.assert_called_once_with(
            "TK-DI-DONE", updated_at=unittest.mock.ANY
        )
        saved_job = repository.save_account_reply_job.call_args.args[0]
        payload = saved_job["payload"]
        self.assertEqual(payload["reply_intent"], "detailed_invoice_completed_and_close")
        self.assertTrue(payload["close_after_publish"])
        self.assertTrue(payload["internal_resolution"])
        self.assertEqual(payload["automation_delivery_key"], "billing-delivery-1")
        self.assertEqual(payload["reply_facts"]["reply_intent"], "detailed_invoice_completed_and_close")
        self.assertTrue(payload["reply_facts"]["attachments_included"])
        self.assertEqual(
            payload["attachments"][0]["asset_id"], "ASSET-ABCDEF123456000000000000"
        )
        self.assertEqual(payload["attachments"][0]["original_filename"], "invoice-approval.pdf")
        commit = repository.commit_automation_reply_result.call_args.kwargs
        self.assertIsNone(commit["assistant_message"])
        self.assertEqual(commit["account_case_updates"]["automation_status"], "automation")
        queued_event_types = [event["event_type"] for event in commit["events"]]
        self.assertIn("billing_internal_resolution_submitted", queued_event_types)
        self.assertIn("detailed_invoice_completion_reply_job_queued", queued_event_types)

    def test_billing_reply_poller_is_disabled_by_default(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTOMATION_REPLY_POLL_ENABLED": "",
                "BILLING_AUTOMATION_REPLY_POLL_ENABLED": "",
            },
            clear=False,
        ):
            self.assertFalse(worker._billing_reply_poller_enabled_from_env())

    def test_billing_reply_poller_enabled_from_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTOMATION_REPLY_POLL_ENABLED": "",
                "BILLING_AUTOMATION_REPLY_POLL_ENABLED": "true",
            },
            clear=False,
        ):
            self.assertTrue(worker._billing_reply_poller_enabled_from_env())

    def test_automation_reply_poller_config_takes_precedence(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTOMATION_REPLY_POLL_ENABLED": "true",
                "AUTOMATION_REPLY_POLL_INTERVAL_SECONDS": "11",
                "AUTOMATION_REPLY_POLL_MAX_MESSAGES": "9",
                "BILLING_AUTOMATION_REPLY_POLL_ENABLED": "false",
            },
            clear=False,
        ):
            self.assertTrue(worker._billing_reply_poller_enabled_from_env())
            self.assertEqual(worker._billing_reply_poll_interval_from_env(), 11.0)
            self.assertEqual(worker._billing_reply_poll_max_messages_from_env(), 9)

    def test_start_billing_reply_poller_starts_daemon_thread_when_enabled(self) -> None:
        started_threads = []

        class _FakeThread:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.daemon = kwargs.get("daemon")
                self.name = kwargs.get("name")

            def start(self) -> None:
                started_threads.append(self)

        with patch.dict(
            os.environ,
            {
                "BILLING_AUTOMATION_REPLY_POLL_ENABLED": "true",
                "BILLING_AUTOMATION_REPLY_POLL_INTERVAL_SECONDS": "7",
            },
            clear=False,
        ), patch.object(worker.threading, "Thread", _FakeThread):
            thread = worker._start_billing_reply_poller_if_enabled()

        self.assertIsNotNone(thread)
        self.assertEqual(len(started_threads), 1)
        self.assertEqual(started_threads[0].name, "automation-reply-poller")
        self.assertTrue(started_threads[0].daemon)
        self.assertEqual(started_threads[0].kwargs["args"], (7.0,))

    def test_account_reply_poller_is_disabled_without_explicit_owner_flag(self) -> None:
        with patch.dict(os.environ, {"ACCOUNT_REPLY_POLLER_ENABLED": "false"}, clear=False):
            self.assertFalse(worker._account_reply_poller_enabled_from_env())
            self.assertIsNone(worker._start_account_reply_poller())

    def test_account_reply_poller_owner_flag_starts_only_the_configured_worker(self) -> None:
        started_threads = []

        class _FakeThread:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def start(self) -> None:
                started_threads.append(self)

        with patch.dict(
            os.environ,
            {
                "ACCOUNT_REPLY_POLLER_ENABLED": "true",
                "ACCOUNT_REPLY_POLL_INTERVAL_SECONDS": "3",
            },
            clear=False,
        ), patch.object(worker.threading, "Thread", _FakeThread):
            thread = worker._start_account_reply_poller()

        self.assertIsNotNone(thread)
        self.assertEqual(len(started_threads), 1)
        self.assertEqual(started_threads[0].kwargs["args"], (3.0,))
        self.assertEqual(started_threads[0].kwargs["name"], "account-reply-poller")

    def test_reply_facts_persona_unavailable_moves_delayed_reply_to_human_review(self) -> None:
        job = {
            "job_id": "account-reply-persona-unavailable",
            "ticket_id": "TK-PERSONA-UNAVAILABLE",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "preparing",
            "payload": {
                "reply_facts": {
                    "behavior": "enablement",
                    "reply_intent": "request_missing_information",
                }
            },
        }
        ticket = {
            "ticket_id": "TK-PERSONA-UNAVAILABLE",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable the feature.",
                    "created_at": "2026-03-22T00:00:00+00:00",
                }
            ],
        }
        account_case = {
            "account_case_id": "AC-TK-PERSONA-UNAVAILABLE",
            "route": "enablement",
            "route_family": "automated",
            "execution_action": "enablement",
            "category": "automation",
            "subcategory": "enablement",
            "route_status": "automated",
            "automation_handler": "enablement",
            "tooling_profile": "deterministic_enablement_intake",
            "automation_status": "automation",
            "route_classification": {
                "route_target": "automation",
                "automation_subcategory": "enablement",
                "handler_binding_status": "active",
            },
        }
        repository = Mock()
        repository.get_account_reply_job.return_value = job
        repository.get_ticket.return_value = ticket
        repository.get_billing_ticket_by_client_ticket_id.return_value = account_case
        repository.resolve_account_persona_for_claimed_reply.side_effect = AccountPersonaUnavailableError(
            "no enabled published persona"
        )
        transitioned_job = copy.deepcopy(job)
        transitioned_job.update(
            status="manual_attention",
            payload={
                **copy.deepcopy(job["payload"]),
                "error": "no enabled published persona",
                "persona_render_status": "human_review",
            },
            updated_at="2026-03-22T00:01:00+00:00",
        )
        repository.transition_claimed_account_reply_to_human_review.return_value = transitioned_job

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "render_automation_reply"
        ) as render:
            worker._prepare_account_reply_job(job)

        self.assertEqual(job["status"], "manual_attention")
        self.assertEqual(job["payload"]["persona_render_status"], "human_review")
        self.assertEqual(job["payload"]["error"], "no enabled published persona")
        repository.transition_claimed_account_reply_to_human_review.assert_called_once()
        transition_call = repository.transition_claimed_account_reply_to_human_review.call_args
        self.assertIs(transition_call.args[0], job)
        self.assertEqual(transition_call.kwargs["expected_status"], "preparing")
        self.assertIsNone(transition_call.kwargs["expected_claimed_at"])
        self.assertEqual(transition_call.kwargs["expected_attempt_count"], 0)
        self.assertEqual(transition_call.kwargs["reason"], "no enabled published persona")
        self.assertEqual(
            transition_call.kwargs["policy_decision"],
            "account_persona_unavailable_human_review",
        )
        self.assertTrue(transition_call.kwargs["transitioned_at"])
        repository.save_account_reply_job.assert_not_called()

    def test_rag_fallback_job_renders_through_persona(self) -> None:
        job = {
            "job_id": "account-reply-rag-fallback",
            "ticket_id": "TK-RAG-FALLBACK",
            "trigger_message_created_at": "2026-08-23T00:00:00+00:00",
            "status": "preparing",
            "claimed_at": "2026-08-23T00:00:01+00:00",
            "attempt_count": 0,
            "payload": {
                "reply_intent": "rag_fallback_answer",
                "reply_pipeline": "automation_persona_v8",
                "reply_facts": {
                    "behavior": "rag_fallback_answer",
                    "reply_intent": "rag_fallback_answer",
                    "provided_answer": "An App ID identifies your Agora project.",
                    "references": ["https://docs.agora.io/en/get-started"],
                },
            },
        }
        ticket = {
            "ticket_id": "TK-RAG-FALLBACK",
            "messages": [
                {
                    "role": "customer",
                    "content": "what is appid?",
                    "created_at": "2026-08-23T00:00:00+00:00",
                }
            ],
        }
        repository = Mock()
        repository.get_account_reply_job.return_value = job
        repository.get_ticket.return_value = ticket
        repository.resolve_account_persona_for_claimed_reply.return_value = {
            "persona_key": "default-support",
            "version": 1,
            "content": {"instruction": "Answer warmly and precisely."},
        }
        rendered = Mock(
            content="Hi Customer,\n\nAn App ID identifies your Agora project.",
            model="gpt-5.4-mini",
            prompt_version=worker.AUTOMATION_PERSONA_PROMPT_VERSION,
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "resolve_support_message"
        ) as resolve, patch.object(
            worker, "render_automation_reply", return_value=rendered
        ) as render:
            worker._prepare_account_reply_job(job)

        # The RAGFlow answer enters the persona pipeline as provided_answer
        # facts; the legacy regeneration path must not run.
        resolve.assert_not_called()
        render.assert_called_once()
        self.assertEqual(
            render.call_args.kwargs["reply_facts"]["provided_answer"],
            "An App ID identifies your Agora project.",
        )
        self.assertEqual(job["status"], "persona_v8_scheduled")
        self.assertEqual(
            job["payload"]["generated_content"],
            "Hi Customer,\n\nAn App ID identifies your Agora project.",
        )
        self.assertEqual(job["payload"]["reply_intent"], "rag_fallback_answer")
        repository.publish_account_reply.assert_not_called()

    def test_rag_fallback_publish_appends_references_after_persona_content(self) -> None:
        job = {
            "job_id": "account-reply-rag-fallback-publish",
            "ticket_id": "TK-RAG-FALLBACK-PUB",
            "trigger_message_created_at": "2026-08-23T00:00:00+00:00",
            "status": "publishing",
            "payload": {
                "reply_intent": "rag_fallback_answer",
                "reply_pipeline": "automation_persona_v8",
                "generated_content": "Hi Customer,\n\nYou can find the App ID on the Projects page in Agora Console.",
                "persona_render_status": "generated",
                "persona_prompt_version": worker.AUTOMATION_PERSONA_PROMPT_VERSION,
                "reply_facts": {
                    "behavior": "rag_fallback_answer",
                    "reply_intent": "rag_fallback_answer",
                    "provided_answer": "You can find the App ID on the Projects page in Agora Console.",
                    "references": [
                        "https://docs.agora.io/en/get-started/manage-agora-account",
                        "https://api-ref.agora.io/cpp",
                    ],
                },
            },
        }
        ticket = {
            "ticket_id": "TK-RAG-FALLBACK-PUB",
            "messages": [
                {
                    "role": "customer",
                    "content": "where can I find the App ID?",
                    "created_at": "2026-08-23T00:00:00+00:00",
                }
            ],
        }
        repository = Mock()
        repository.get_account_reply_job.return_value = job
        repository.get_ticket.return_value = ticket
        repository.publish_account_reply.side_effect = (
            lambda current_job, content, **kwargs: {"content": content, "message_id": "msg-rag-1"}
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "render_automation_reply"
        ) as render, patch.object(
            worker, "_deliver_production_account_reply_to_zendesk"
        ) as deliver:
            worker._publish_account_reply_job(job)

        # The persona body publishes with the reference links appended
        # deterministically; no re-render for a current-version payload.
        render.assert_not_called()
        repository.publish_account_reply.assert_called_once()
        publish_call = repository.publish_account_reply.call_args
        self.assertEqual(
            publish_call.kwargs["content"],
            "Hi Customer,\n\nYou can find the App ID on the Projects page in Agora Console."
            "\n\nReferences:\n- https://docs.agora.io/en/get-started/manage-agora-account"
            "\n- https://api-ref.agora.io/cpp",
        )
        deliver.assert_called_once()
        self.assertEqual(
            deliver.call_args.kwargs.get("reply_intent"),
            "rag_fallback_answer",
        )

    def test_rag_fallback_delivery_bypasses_unregistered_route_gate(self) -> None:
        # After an unexpected reply the case is re-routed (e.g. rag_product_support)
        # and no longer carries a registered automation route; the RAG answer
        # must still be delivered to Zendesk.
        account_case = {
            "account_case_id": "AC-UNREGISTERED",
            "processing_profile": "production",
            "route_family": "rag_product_support",
            "execution_action": "rag",
            "zendesk_ticket_id": "12999",
        }
        repository = Mock()
        repository.get_account_case_by_ticket_id.return_value = account_case
        repository.claim_account_zendesk_comment_delivery.return_value = {
            "claimed": False,
            "status": "missing",
        }
        with patch.object(worker, "ticket_repository", repository):
            worker._deliver_production_account_reply_to_zendesk(
                ticket_id="TK-UNREGISTERED",
                message_id="msg-1",
                job_id="job-1",
                reply_intent="rag_fallback_answer",
            )
        # The unregistered-automation gate must NOT have returned early.
        repository.claim_account_zendesk_comment_delivery.assert_called_once()

    def test_legacy_delayed_reply_persona_unavailable_moves_to_human_review(self) -> None:
        job = {
            "job_id": "account-reply-legacy-persona-unavailable",
            "ticket_id": "TK-LEGACY-PERSONA-UNAVAILABLE",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "preparing",
            "payload": {},
        }
        ticket = {
            "ticket_id": "TK-LEGACY-PERSONA-UNAVAILABLE",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable the feature.",
                    "created_at": "2026-03-22T00:00:00+00:00",
                }
            ],
        }
        account_case = {
            "account_case_id": "AC-TK-LEGACY-PERSONA-UNAVAILABLE",
            "route": "enablement",
            "route_family": "automated",
            "execution_action": "enablement",
            "category": "automation",
            "subcategory": "enablement",
            "route_status": "automated",
            "automation_handler": "enablement",
            "tooling_profile": "deterministic_enablement_intake",
            "automation_status": "automation",
            "route_classification": {
                "route_target": "automation",
                "automation_subcategory": "enablement",
                "handler_binding_status": "active",
            },
        }
        repository = Mock()
        repository.get_account_reply_job.return_value = job
        repository.get_ticket.return_value = ticket
        repository.get_billing_ticket_by_client_ticket_id.return_value = account_case
        repository.resolve_account_persona_for_claimed_reply.side_effect = AccountPersonaUnavailableError(
            "no enabled published persona"
        )
        transitioned_job = copy.deepcopy(job)
        transitioned_job.update(
            status="manual_attention",
            payload={
                **copy.deepcopy(job["payload"]),
                "error": "no enabled published persona",
                "persona_render_status": "human_review",
            },
            updated_at="2026-03-22T00:01:00+00:00",
        )
        repository.transition_claimed_account_reply_to_human_review.return_value = transitioned_job
        resolution = types.SimpleNamespace(
            answer="Please share the App ID.",
            evidence_summary=None,
            answer_route="enablement",
            route_reason="registered_enablement",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "resolve_support_message", return_value=resolution
        ), patch.object(worker, "apply_persona_to_customer_reply") as apply_persona:
            worker._prepare_account_reply_job(job)

        self.assertEqual(job["status"], "manual_attention")
        self.assertEqual(job["payload"]["persona_render_status"], "human_review")
        repository.transition_claimed_account_reply_to_human_review.assert_called_once()
        transition_call = repository.transition_claimed_account_reply_to_human_review.call_args
        self.assertIs(transition_call.args[0], job)
        self.assertEqual(transition_call.kwargs["expected_status"], "preparing")
        self.assertIsNone(transition_call.kwargs["expected_claimed_at"])
        self.assertEqual(transition_call.kwargs["expected_attempt_count"], 0)
        self.assertEqual(transition_call.kwargs["reason"], "no enabled published persona")
        self.assertEqual(
            transition_call.kwargs["policy_decision"],
            "account_persona_unavailable_human_review",
        )
        repository.save_account_reply_job.assert_not_called()
        repository.save_billing_ticket.assert_not_called()
        repository.record_event.assert_not_called()
        apply_persona.assert_not_called()
        repository.publish_account_reply.assert_not_called()

    def test_legacy_human_review_from_unavailable_persona_uses_unavailable_policy(self) -> None:
        job = {
            "job_id": "account-reply-legacy-indirect-persona-unavailable",
            "ticket_id": "TK-LEGACY-INDIRECT-PERSONA-UNAVAILABLE",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "preparing",
            "payload": {},
        }
        ticket = {
            "ticket_id": "TK-LEGACY-INDIRECT-PERSONA-UNAVAILABLE",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable the feature.",
                    "created_at": "2026-03-22T00:00:00+00:00",
                }
            ],
        }
        account_case = {
            "account_case_id": "AC-TK-LEGACY-INDIRECT-PERSONA-UNAVAILABLE",
            "route": "enablement",
            "route_family": "automated",
            "execution_action": "enablement",
            "category": "automation",
            "subcategory": "enablement",
            "route_status": "automated",
            "automation_handler": "enablement",
            "tooling_profile": "deterministic_enablement_intake",
            "automation_status": "automation",
            "route_classification": {
                "route_target": "automation",
                "automation_subcategory": "enablement",
                "handler_binding_status": "active",
            },
        }
        repository = Mock()
        repository.get_account_reply_job.return_value = job
        repository.get_ticket.return_value = ticket
        repository.get_billing_ticket_by_client_ticket_id.return_value = account_case
        transitioned_job = copy.deepcopy(job)
        transitioned_job.update(
            status="manual_attention",
            payload={
                **copy.deepcopy(job["payload"]),
                "error": "no enabled published persona",
                "persona_render_status": "human_review",
            },
            updated_at="2026-03-22T00:01:00+00:00",
        )
        repository.transition_claimed_account_reply_to_human_review.return_value = transitioned_job
        resolution = types.SimpleNamespace(
            answer="",
            route_family="human_review",
            route_reason="no enabled published persona",
            evidence_summary={"account_persona_unavailable": True},
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "resolve_support_message", return_value=resolution
        ):
            worker._prepare_account_reply_job(job)

        self.assertEqual(job["status"], "manual_attention")
        self.assertEqual(job["payload"]["persona_render_status"], "human_review")
        repository.transition_claimed_account_reply_to_human_review.assert_called_once()
        transition_call = repository.transition_claimed_account_reply_to_human_review.call_args
        self.assertIs(transition_call.args[0], job)
        self.assertEqual(transition_call.kwargs["expected_status"], "preparing")
        self.assertEqual(transition_call.kwargs["reason"], "no enabled published persona")
        self.assertEqual(
            transition_call.kwargs["policy_decision"],
            "account_persona_unavailable_human_review",
        )
        repository.save_account_reply_job.assert_not_called()
        repository.save_billing_ticket.assert_not_called()
        repository.record_event.assert_not_called()
        repository.resolve_account_persona.assert_not_called()
        repository.publish_account_reply.assert_not_called()

    def test_legacy_human_review_without_boolean_unavailable_marker_uses_generic_policy(self) -> None:
        base_job = {
            "job_id": "account-reply-legacy-marker-negative",
            "ticket_id": "TK-LEGACY-MARKER-NEGATIVE",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "preparing",
            "payload": {},
        }
        ticket = {
            "ticket_id": "TK-LEGACY-MARKER-NEGATIVE",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable the feature.",
                    "created_at": "2026-03-22T00:00:00+00:00",
                }
            ],
        }
        base_case = {
            "account_case_id": "AC-TK-LEGACY-MARKER-NEGATIVE",
            "route": "enablement",
            "route_family": "automated",
            "execution_action": "enablement",
            "category": "automation",
            "subcategory": "enablement",
            "route_status": "automated",
            "automation_handler": "enablement",
            "tooling_profile": "deterministic_enablement_intake",
            "automation_status": "automation",
            "route_classification": {
                "route_target": "automation",
                "automation_subcategory": "enablement",
                "handler_binding_status": "active",
            },
        }
        for marker in ({}, {"account_persona_unavailable": "true"}, {"account_persona_unavailable": 1}):
            with self.subTest(marker=marker):
                job = copy.deepcopy(base_job)
                account_case = copy.deepcopy(base_case)
                repository = Mock()
                repository.get_account_reply_job.return_value = job
                repository.get_ticket.return_value = ticket
                repository.get_billing_ticket_by_client_ticket_id.return_value = account_case
                transitioned_job = copy.deepcopy(job)
                transitioned_job.update(
                    status="manual_attention",
                    payload={
                        **copy.deepcopy(job["payload"]),
                        "error": "no enabled published persona",
                        "persona_render_status": "human_review",
                    },
                    updated_at="2026-03-22T00:01:00+00:00",
                )
                repository.transition_claimed_account_reply_to_human_review.return_value = (
                    transitioned_job
                )
                resolution = types.SimpleNamespace(
                    answer="",
                    route_family="human_review",
                    route_reason="no enabled published persona",
                    evidence_summary=marker,
                )

                with patch.object(worker, "ticket_repository", repository), patch.object(
                    worker, "resolve_support_message", return_value=resolution
                ):
                    worker._prepare_account_reply_job(job)

                self.assertEqual(job["status"], "manual_attention")
                repository.transition_claimed_account_reply_to_human_review.assert_called_once()
                transition_call = (
                    repository.transition_claimed_account_reply_to_human_review.call_args
                )
                self.assertIs(transition_call.args[0], job)
                self.assertEqual(transition_call.kwargs["expected_status"], "preparing")
                self.assertEqual(
                    transition_call.kwargs["policy_decision"],
                    "automation_persona_human_review",
                )
                repository.save_account_reply_job.assert_not_called()
                repository.save_billing_ticket.assert_not_called()
                repository.record_event.assert_not_called()
                repository.resolve_account_persona.assert_not_called()

    def test_internal_followups_persona_unavailable_do_not_publish_customer_copy(self) -> None:
        cases = (
            (
                "billing",
                worker.handle_billing_request_reply,
                "Re: [Billing Request] Detailed invoice - Ticket TK-PERSONA-BILLING",
                {
                    "billing_ticket_id": "AC-TK-PERSONA-BILLING",
                    "account_case_id": "AC-TK-PERSONA-BILLING",
                    "client_ticket_id": "TK-PERSONA-BILLING",
                    "route": "detailed_invoice",
                    "route_family": "automated",
                    "execution_action": "detailed_invoice",
                    "automation_status": "automation",
                },
            ),
            (
                "enablement",
                worker.handle_enablement_request_reply,
                "Re: [Enablement Request] Media Relay - Ticket TK-PERSONA-ENABLEMENT",
                {
                    "billing_ticket_id": "AC-TK-PERSONA-ENABLEMENT",
                    "account_case_id": "AC-TK-PERSONA-ENABLEMENT",
                    "client_ticket_id": "TK-PERSONA-ENABLEMENT",
                    "automation_handler": "enablement",
                    "route": "enablement",
                    "route_family": "automated",
                    "execution_action": "enablement",
                    "automation_status": "automation",
                    "collected_fields": {"app_id": "alpha"},
                },
            ),
            (
                "quota",
                worker.handle_quota_request_reply,
                "Re: [Quota Request] RTC - Ticket TK-PERSONA-QUOTA",
                {
                    "billing_ticket_id": "AC-TK-PERSONA-QUOTA",
                    "account_case_id": "AC-TK-PERSONA-QUOTA",
                    "client_ticket_id": "TK-PERSONA-QUOTA",
                    "automation_handler": "quota",
                    "route": "quota",
                    "route_family": "automated",
                    "execution_action": "quota",
                    "automation_status": "automation",
                    "collected_fields": {"products": ["rtc"]},
                },
            ),
        )
        for handler, handle_reply, subject, account_case in cases:
            with self.subTest(handler=handler):
                account_case.update(
                    {
                        "category": "automation",
                        "subcategory": str(account_case["execution_action"]),
                        "route_status": "automated",
                        "automation_handler": handler,
                        "tooling_profile": f"deterministic_{handler}_intake",
                        "route_classification": {
                            "route_target": "automation",
                            "automation_subcategory": str(account_case["execution_action"]),
                            "handler_binding_status": "active",
                        },
                    }
                )
                repository = Mock()
                repository.claim_automation_reply.return_value = {"status": "acquired"}
                repository.commit_automation_reply_result.return_value = True
                repository.get_billing_ticket_by_client_ticket_id.return_value = account_case
                repository.get_ticket.return_value = {
                    "ticket_id": account_case["client_ticket_id"],
                    "subject": "Automation request",
                    "messages": [{"role": "customer", "content": "Please help."}],
                }
                repository.resolve_account_persona.side_effect = AccountPersonaUnavailableError(
                    "no enabled published persona"
                )
                reply = types.SimpleNamespace(
                    message_id=f"{handler}-persona-unavailable",
                    subject=subject,
                    body_text="The internal team completed the request.",
                )

                with patch.object(worker, "ticket_repository", repository), patch.object(
                    worker, "record_billing_request_reply"
                ), patch.object(
                    worker, "is_registered_automation", return_value=True,
                ) as is_registered, patch.object(
                    worker, "render_automation_reply"
                ) as render, patch.object(
                    worker,
                    "classify_enablement_completion",
                    return_value=_classifier_regex_fallback(),
                ):
                    handled = handle_reply(reply)

                self.assertEqual(handled, "completed")
                commit = repository.commit_automation_reply_result.call_args.kwargs
                self.assertIsNone(commit["assistant_message"])
                updates = commit["account_case_updates"]
                self.assertEqual(updates["route"], account_case["execution_action"])
                self.assertEqual(updates["automation_status"], "human_review_required")
                self.assertEqual(updates["category"], "automation")
                self.assertEqual(updates["subcategory"], account_case["execution_action"])
                if handler != "quota":
                    self.assertEqual(updates["route_status"], "not_automated")
                self.assertEqual(updates["automation_handler"], handler)
                self.assertEqual(updates["tooling_profile"], f"deterministic_{handler}_intake")
                self.assertEqual(updates["execution_reason_code"], f"{handler}_persona_unavailable")
                self.assertEqual(
                    updates["route_classification"]["route_target"],
                    "automation",
                )
                self.assertEqual(updates["route_classification"]["handler_binding_status"], "human_review")
                self.assertEqual(updates["route_classification"]["automation_subcategory"], account_case["execution_action"])
                if handler == "billing":
                    is_registered.assert_called_once()
                else:
                    is_registered.assert_not_called()
                render.assert_not_called()

    def test_internal_followups_persona_render_failure_persist_generic_human_review(self) -> None:
        cases = (
            (
                "billing",
                worker.handle_billing_request_reply,
                "Re: [Billing Request] Fraud review - Ticket TK-PERSONA-RENDER-BILLING",
                {
                    "billing_ticket_id": "AC-TK-PERSONA-RENDER-BILLING",
                    "account_case_id": "AC-TK-PERSONA-RENDER-BILLING",
                    "client_ticket_id": "TK-PERSONA-RENDER-BILLING",
                    "route": "fraud_account",
                    "route_family": "automated",
                    "execution_action": "fraud_account",
                },
            ),
            (
                "enablement",
                worker.handle_enablement_request_reply,
                "Re: [Enablement Request] Media Relay - Ticket TK-PERSONA-RENDER-ENABLEMENT",
                {
                    "billing_ticket_id": "AC-TK-PERSONA-RENDER-ENABLEMENT",
                    "account_case_id": "AC-TK-PERSONA-RENDER-ENABLEMENT",
                    "client_ticket_id": "TK-PERSONA-RENDER-ENABLEMENT",
                    "route": "enablement",
                    "route_family": "automated",
                    "execution_action": "enablement",
                    "collected_fields": {"app_id": "alpha"},
                },
            ),
            (
                "quota",
                worker.handle_quota_request_reply,
                "Re: [Quota Request] RTC - Ticket TK-PERSONA-RENDER-QUOTA",
                {
                    "billing_ticket_id": "AC-TK-PERSONA-RENDER-QUOTA",
                    "account_case_id": "AC-TK-PERSONA-RENDER-QUOTA",
                    "client_ticket_id": "TK-PERSONA-RENDER-QUOTA",
                    "route": "quota",
                    "route_family": "automated",
                    "execution_action": "quota",
                    "collected_fields": {"products": ["rtc"]},
                },
            ),
        )
        for handler, handle_reply, subject, account_case in cases:
            with self.subTest(handler=handler):
                account_case.update(
                    {
                        "category": "automation",
                        "subcategory": str(account_case["execution_action"]),
                        "route_status": "automated",
                        "automation_handler": handler,
                        "tooling_profile": f"deterministic_{handler}_intake",
                        "automation_status": "automation",
                        "route_classification": {
                            "intent_class": "agora",
                            "agora_route": "automation",
                            "route_target": "automation",
                            "automation_subcategory": str(account_case["execution_action"]),
                            "handler_binding_status": "active",
                            "primary_label": "Agora",
                            "secondary_label": (
                                f"Automation / {str(account_case['execution_action']).replace('_', ' ').title()}"
                            ),
                        },
                    }
                )
                repository = Mock()
                repository.claim_automation_reply.return_value = {"status": "acquired"}
                repository.commit_automation_reply_result.return_value = True
                repository.get_billing_ticket_by_client_ticket_id.return_value = account_case
                repository.get_ticket.return_value = {
                    "ticket_id": account_case["client_ticket_id"],
                    "subject": "Automation request",
                    "messages": [{"role": "customer", "content": "Please help."}],
                }
                repository.resolve_account_persona.return_value = {
                    "persona_key": "sid-warm",
                    "version": 1,
                    "content": {},
                }
                reply = types.SimpleNamespace(
                    message_id=f"{handler}-persona-render-failure",
                    subject=subject,
                    body_text="The internal team completed the request.",
                )

                with patch.object(worker, "ticket_repository", repository), patch.object(
                    worker, "record_billing_request_reply"
                ), patch.object(
                    worker,
                    "classify_enablement_completion",
                    return_value=_classifier_regex_fallback(),
                ), patch.object(
                    worker,
                    "extract_automation_resolution_facts",
                    return_value={
                        "customer_shareable_facts": ["The internal team completed the request."],
                        "customer_action": None,
                        "next_step": None,
                        "status": "completed",
                    },
                ), patch.object(
                    worker,
                    "render_automation_reply",
                    side_effect=worker.AutomationPersonaError("persona render failed"),
                ) as render:
                    handled = handle_reply(reply)

                self.assertEqual(handled, "completed")
                commit = repository.commit_automation_reply_result.call_args.kwargs
                self.assertIsNone(commit["assistant_message"])
                updates = commit["account_case_updates"]
                self.assertEqual(updates["policy_decision"], "automation_persona_human_review")
                self.assertEqual(updates["automation_status"], "human_review_required")
                self.assertEqual(updates["route_family"], "automated")
                if handler != "quota":
                    self.assertEqual(updates["route_status"], "not_automated")
                self.assertEqual(updates["automation_handler"], handler)
                self.assertEqual(updates["execution_reason_code"], f"{handler}_persona_render_failed")
                self.assertEqual(updates["route_classification"]["route_target"], "automation")
                self.assertEqual(updates["route_classification"]["handler_binding_status"], "human_review")
                render.assert_called_once()

    def test_case_persona_extraction_failure_persists_generic_human_review(self) -> None:
        account_case = {
            "account_case_id": "AC-TK-PERSONA-EXTRACTION-FAILURE",
            "client_ticket_id": "TK-PERSONA-EXTRACTION-FAILURE",
            "route": "enablement",
            "route_family": "automated",
            "execution_action": "enablement",
            "category": "automation",
            "subcategory": "enablement",
            "route_status": "automated",
            "automation_handler": "enablement",
            "tooling_profile": "deterministic_enablement_intake",
            "automation_status": "automation",
            "route_classification": {
                "intent_class": "agora",
                "agora_route": "backend_operation",
                "route_target": "automation",
                "account_billing_subcategory": None,
                "backend_operation_subcategory": "enablement",
                "automation_subcategory": None,
                "handler_binding_status": "active",
                "primary_label": "Agora",
                "secondary_label": "Automation / Enablement",
            },
        }
        repository = Mock()
        repository.resolve_account_persona.return_value = {
            "persona_key": "sid-warm",
            "version": 1,
            "content": {},
        }
        save_case = Mock()

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "extract_automation_resolution_facts",
            side_effect=worker.AutomationPersonaError("persona extraction failed"),
        ), patch.object(worker, "render_automation_reply") as render:
            reply = worker._render_case_persona_reply(
                ticket_id="TK-PERSONA-EXTRACTION-FAILURE",
                case=account_case,
                behavior="enablement",
                reply_intent="resolution_update",
                source_facts=["The internal team completed the request."],
                save_case=save_case,
            )

        self.assertEqual(reply, "")
        save_case.assert_called_once_with(account_case)
        self.assertEqual(account_case["policy_decision"], "automation_persona_human_review")
        self.assertEqual(account_case["route"], "enablement")
        self.assertEqual(account_case["automation_status"], "human_review_required")
        self.assertEqual(account_case["route_status"], "not_automated")
        self.assertEqual(account_case["automation_handler"], "enablement")
        self.assertEqual(account_case["execution_reason_code"], "enablement_persona_render_failed")
        self.assertEqual(account_case["route_classification"]["route_target"], "automation")
        self.assertEqual(account_case["route_classification"]["handler_binding_status"], "human_review")
        self.assertEqual(account_case["route_classification"]["primary_label"], "Agora")
        self.assertEqual(account_case["route_classification"]["secondary_label"], "Automation / Enablement")
        render.assert_not_called()

    def test_enablement_confirmation_persona_unavailable_does_not_create_reply_job(self) -> None:
        account_case = {
            "account_case_id": "AC-PERSONA-CONFIRMATION",
            "client_ticket_id": "TK-PERSONA-CONFIRMATION",
            "route": "enablement",
            "route_family": "automated",
            "execution_action": "enablement",
            "category": "automation",
            "subcategory": "enablement",
            "route_status": "automated",
            "automation_handler": "enablement",
            "tooling_profile": "deterministic_enablement_intake",
            "automation_status": "automation",
            "route_classification": {
                "route_target": "automation",
                "automation_subcategory": "enablement",
                "handler_binding_status": "active",
            },
            "internal_email_payload": {"delivery_key": "enablement:AC-PERSONA-CONFIRMATION:v1"},
            "collected_fields": {"app_id": "alpha", "requested_feature": "media_relay"},
        }
        repository = Mock()
        repository.get_ticket.return_value = {
            "ticket_id": "TK-PERSONA-CONFIRMATION",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable Media Relay.",
                    "created_at": "2026-03-22T00:00:00+00:00",
                }
            ],
        }
        repository.get_latest_account_reply_job.return_value = None
        repository.resolve_account_persona.side_effect = AccountPersonaUnavailableError(
            "no enabled published persona"
        )

        with patch.object(worker, "ticket_repository", repository):
            created = worker._queue_enablement_submission_confirmation(account_case)

        self.assertFalse(created)
        self.assertEqual(account_case["route"], "enablement")
        self.assertEqual(account_case["automation_status"], "human_review_required")
        self.assertEqual(account_case["category"], "automation")
        self.assertEqual(account_case["subcategory"], "enablement")
        self.assertEqual(account_case["route_status"], "not_automated")
        self.assertEqual(account_case["automation_handler"], "enablement")
        self.assertEqual(account_case["execution_reason_code"], "enablement_persona_unavailable")
        self.assertEqual(account_case["route_classification"]["route_target"], "automation")
        self.assertEqual(account_case["route_classification"]["handler_binding_status"], "human_review")
        repository.cancel_pending_account_reply_jobs.assert_called_once_with(
            "TK-PERSONA-CONFIRMATION", updated_at=account_case["updated_at"]
        )
        repository.save_account_reply_job.assert_not_called()
        self.assertGreaterEqual(repository.save_account_case.call_count, 1)
        self.assertEqual(repository.save_account_case.call_args.args[0], account_case)

    def test_fraud_missing_information_prepare_persists_deterministic_bullets(self) -> None:
        job = {
            "job_id": "account-reply-fraud-deterministic-missing",
            "ticket_id": "TK-FRAUD-DETERMINISTIC-MISSING",
            "trigger_message_created_at": "2026-08-25T10:20:14+00:00",
            "status": worker.ACCOUNT_REPLY_PERSONA_V8_PREPARING,
            "claimed_at": "2026-08-25T10:22:05+00:00",
            "attempt_count": 0,
            "payload": {
                "reply_pipeline": worker.ACCOUNT_REPLY_PERSONA_PIPELINE,
                "reply_facts": worker.build_account_automation_reply_facts(
                    handler="fraud_account",
                    action="fraud_account",
                    missing_fields=[
                        "office_address",
                        "contact_number",
                        "console_configuration",
                    ],
                    collected_fields={
                        "account_type": "Individual Developer",
                        "name": "Test Customer",
                        "contact_email": "customer@example.invalid",
                        "use_case_description": "Independent developer evaluation",
                    },
                    customer_name="Taylor",
                ),
            },
        }
        repository = Mock()
        repository.get_account_reply_job.return_value = job
        repository.get_ticket.return_value = {
            "ticket_id": job["ticket_id"],
            "messages": [
                {
                    "role": "customer",
                    "content": "I need help with account verification.",
                    "created_at": job["trigger_message_created_at"],
                }
            ],
        }
        repository.resolve_account_persona_for_claimed_reply.return_value = {
            "persona_key": "sid-warm",
            "version": 2,
            "content": {"instruction": "Use a warm, concise support voice."},
        }
        repository.get_billing_ticket_by_client_ticket_id.return_value = {
            "billing_ticket_id": "AC-FRAUD-DETERMINISTIC-MISSING"
        }
        profile = types.SimpleNamespace(
            has_invocation_credentials=lambda: True,
            api_key="test-key",
            model="persona-model",
        )
        response = types.SimpleNamespace(
            text="Thank you for sharing the information you have so far.",
            model_name="persona-model",
            provider_name="openai",
            prompt_tokens=10,
            completion_tokens=8,
            cached_input_tokens=0,
            reasoning_tokens=0,
        )

        with patch.object(worker, "ticket_repository", repository), patch.dict(
            worker.render_automation_reply.__globals__,
            {"resolve_model_profile": Mock(return_value=profile)},
        ), patch(
            "backend.services.account_ai_execution.invoke_responses_text", return_value=response
        ):
            worker._prepare_account_reply_job(job)

        self.assertEqual(job["status"], worker.ACCOUNT_REPLY_PERSONA_V8_SCHEDULED)
        self.assertEqual(
            job["payload"]["persona_prompt_version"],
            worker.AUTOMATION_PERSONA_PROMPT_VERSION,
        )
        self.assertEqual(job["payload"]["persona_render_status"], "generated")
        content = job["payload"]["generated_content"]
        self.assertIn("- Office address", content)
        self.assertIn("- Official contact number", content)
        self.assertIn("- Last known console configuration", content)
        self.assertNotIn("1. Office address", content)
        repository.transition_claimed_account_reply_to_human_review.assert_not_called()

    def test_reply_facts_prepare_pins_persisted_persona_assignment(self) -> None:
        job = {
            "job_id": "account-reply-persona-pin",
            "ticket_id": "TK-PERSONA-PIN",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "preparing",
            "payload": {
                "reply_facts": {
                    "behavior": "enablement",
                    "reply_intent": "request_missing_information",
                }
            },
        }
        assignment = {
            "persona_key": "sid-bright",
            "version": 2,
            "content": {"instruction": "Bright", "signature": "Best,\nSid"},
        }
        repository = Mock()
        repository.get_account_reply_job.return_value = job
        repository.get_ticket.return_value = {
            "ticket_id": "TK-PERSONA-PIN",
            "messages": [{"role": "customer", "content": "Please enable the feature."}],
        }
        repository.resolve_account_persona_for_claimed_reply.return_value = assignment
        rendered = types.SimpleNamespace(
            content="Please share the App ID.",
            model="persona-model",
            prompt_version="automation-persona-v4",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "render_automation_reply", return_value=rendered
        ) as render:
            worker._prepare_account_reply_job(job)

        repository.resolve_account_persona_for_claimed_reply.assert_called_once_with(
            job,
            expected_status="preparing",
            expected_claimed_at=None,
            expected_attempt_count=0,
        )
        repository.resolve_account_persona.assert_not_called()
        self.assertEqual(job["payload"]["persona_key"], "sid-bright")
        self.assertEqual(job["payload"]["persona_version"], 2)
        self.assertEqual(
            job["payload"]["effective_prompt"],
            {"instruction": "Bright", "opener": ""},
        )
        self.assertEqual(render.call_args.kwargs["persona_assignment"]["persona_key"], "sid-bright")
        self.assertEqual(render.call_args.kwargs["persona_assignment"]["version"], 2)
        self.assertEqual(
            render.call_args.kwargs["persona_assignment"]["content"],
            {"instruction": "Bright", "opener": ""},
        )

    def test_legacy_delayed_reply_pins_persisted_persona_assignment(self) -> None:
        job = {
            "job_id": "account-reply-legacy-pin",
            "ticket_id": "TK-LEGACY-PIN",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "preparing",
            "payload": {},
        }
        assignment = {
            "persona_key": "sid-precise",
            "version": 4,
            "content": {"instruction": "Precise"},
        }
        repository = Mock()
        repository.get_account_reply_job.side_effect = [job, copy.deepcopy(job)]
        repository.get_ticket.return_value = {
            "ticket_id": "TK-LEGACY-PIN",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable the feature.",
                    "created_at": "2026-03-22T00:00:00+00:00",
                }
            ],
        }
        repository.resolve_account_persona_for_claimed_reply.return_value = assignment
        resolution = types.SimpleNamespace(
            answer="Please share the App ID.",
            evidence_summary=None,
            answer_route="enablement",
            route_reason="registered_enablement",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "resolve_support_message", return_value=resolution
        ), patch.object(worker, "apply_persona_to_customer_reply", return_value="Pinned draft") as apply_persona:
            worker._prepare_account_reply_job(job)

        repository.resolve_account_persona_for_claimed_reply.assert_called_once_with(
            job,
            expected_status="preparing",
            expected_claimed_at=None,
            expected_attempt_count=0,
        )
        repository.resolve_account_persona.assert_not_called()
        apply_persona.assert_called_once_with("Please share the App ID.", assignment)
        self.assertEqual(job["payload"]["persona_key"], "sid-precise")
        self.assertEqual(job["payload"]["persona_version"], 4)
        self.assertEqual(job["payload"]["effective_prompt"], assignment["content"])

    def test_reply_facts_prepare_stops_silently_when_persona_claim_is_lost(self) -> None:
        job = {
            "job_id": "account-reply-persona-lost-claim",
            "ticket_id": "TK-PERSONA-LOST-CLAIM",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "persona_preparing",
            "claimed_at": "2026-03-22T00:01:00+00:00",
            "attempt_count": 2,
            "payload": {
                "reply_facts": {
                    "behavior": "enablement",
                    "reply_intent": "request_missing_information",
                }
            },
        }
        original_job = copy.deepcopy(job)
        repository = Mock()
        repository.get_account_reply_job.return_value = copy.deepcopy(job)
        repository.get_ticket.return_value = {
            "ticket_id": job["ticket_id"],
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable the feature.",
                    "created_at": job["trigger_message_created_at"],
                }
            ],
        }
        repository.resolve_account_persona_for_claimed_reply.return_value = None

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "render_automation_reply"
        ) as render, patch.object(
            worker, "resolve_support_message"
        ) as legacy_resolver, patch.object(
            worker, "_move_automation_reply_to_human_review"
        ) as move_to_human_review:
            worker._prepare_account_reply_job(job)

        repository.resolve_account_persona_for_claimed_reply.assert_called_once_with(
            job,
            expected_status="persona_preparing",
            expected_claimed_at="2026-03-22T00:01:00+00:00",
            expected_attempt_count=2,
        )
        self.assertEqual(job, original_job)
        repository.resolve_account_persona.assert_not_called()
        repository.update_claimed_account_reply_job.assert_not_called()
        repository.get_billing_ticket_by_client_ticket_id.assert_not_called()
        repository.save_billing_ticket.assert_not_called()
        repository.record_event.assert_not_called()
        repository.publish_account_reply.assert_not_called()
        move_to_human_review.assert_not_called()
        render.assert_not_called()
        legacy_resolver.assert_not_called()

    def test_legacy_prepare_stops_silently_when_persona_claim_is_lost(self) -> None:
        job = {
            "job_id": "account-reply-legacy-lost-claim",
            "ticket_id": "TK-LEGACY-LOST-CLAIM",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "preparing",
            "claimed_at": "2026-03-22T00:01:00+00:00",
            "attempt_count": 1,
            "payload": {},
        }
        original_job = copy.deepcopy(job)
        repository = Mock()
        repository.get_account_reply_job.return_value = copy.deepcopy(job)
        repository.get_ticket.return_value = {
            "ticket_id": job["ticket_id"],
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable the feature.",
                    "created_at": job["trigger_message_created_at"],
                }
            ],
        }
        repository.resolve_account_persona_for_claimed_reply.return_value = None
        repository.resolve_account_persona.return_value = {
            "persona_key": "sid-bright",
            "version": 1,
            "content": {"instruction": "Bright"},
        }
        resolution = types.SimpleNamespace(
            answer="Please share the App ID.",
            evidence_summary=None,
            answer_route="enablement",
            route_reason="registered_enablement",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "resolve_support_message", return_value=resolution
        ), patch.object(
            worker, "apply_persona_to_customer_reply"
        ) as apply_persona, patch.object(
            worker, "_move_automation_reply_to_human_review"
        ) as move_to_human_review:
            worker._prepare_account_reply_job(job)

        repository.resolve_account_persona_for_claimed_reply.assert_called_once_with(
            job,
            expected_status="preparing",
            expected_claimed_at="2026-03-22T00:01:00+00:00",
            expected_attempt_count=1,
        )
        self.assertEqual(job, original_job)
        repository.resolve_account_persona.assert_not_called()
        repository.update_claimed_account_reply_job.assert_not_called()
        repository.get_billing_ticket_by_client_ticket_id.assert_not_called()
        repository.save_billing_ticket.assert_not_called()
        repository.record_event.assert_not_called()
        repository.publish_account_reply.assert_not_called()
        move_to_human_review.assert_not_called()
        apply_persona.assert_not_called()

    def test_human_review_transition_stops_after_reset_wins_in_memory_fence(self) -> None:
        ticket_id = "TK-HUMAN-REVIEW-RESET-FENCE"
        job_id = "account-reply-human-review-reset-fence"
        trigger_created_at = "2026-03-22T00:00:00+00:00"
        repository = InMemoryTicketRepository()
        repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please enable the feature.",
                        "created_at": trigger_created_at,
                    },
                    {
                        "role": "assistant",
                        "content": "Old Account reply",
                        "source": "account_ai",
                        "meta": {"account_reply_job_id": job_id},
                        "created_at": "2026-03-22T00:00:30+00:00",
                    },
                ],
            }
        )
        repository.save_billing_ticket(
            {
                "billing_ticket_id": "AC-HUMAN-REVIEW-RESET-FENCE",
                "account_case_id": "AC-HUMAN-REVIEW-RESET-FENCE",
                "client_ticket_id": ticket_id,
                "title": "Enablement request",
                "question": "Please enable this feature.",
                "route": "enablement",
                "scope_label": "automation",
                "route_family": "automated",
                "execution_action": "enablement",
                "automation_status": "automation",
                "customer_reply": "Old Account reply",
                "route_classification": {
                    "intent_class": "agora",
                    "agora_route": "automation",
                    "automation_subcategory": "enablement",
                },
            }
        )
        repository.resolve_account_persona(ticket_id)
        repository.save_account_reply_execution(
            {"execution_id": f"reply-{job_id}", "ticket_id": ticket_id}
        )
        repository.save_account_reply_job(
            {
                "job_id": job_id,
                "ticket_id": ticket_id,
                "trigger_message_created_at": trigger_created_at,
                "status": "persona_queued",
                "scheduled_for": "2026-03-22T00:01:00+00:00",
                "payload": {"reply_facts": {"behavior": "enablement"}},
                "created_at": "2026-03-22T00:00:45+00:00",
            }
        )
        claimed = repository.claim_account_reply_jobs(
            from_status="persona_queued",
            to_status="persona_preparing",
            now_value="2026-03-22T00:01:30+00:00",
        )[0]
        ticket = repository.get_ticket(ticket_id)
        assert ticket is not None
        original_transition = repository.transition_claimed_account_reply_to_human_review
        transition_started = threading.Event()
        release_transition = threading.Event()

        def delayed_transition(*args, **kwargs):
            transition_started.set()
            if not release_transition.wait(timeout=5):
                raise TimeoutError("test did not release claimed human-review transition")
            return original_transition(*args, **kwargs)

        executor = ThreadPoolExecutor(max_workers=1)
        transition_future = None
        try:
            with patch.object(
                repository,
                "transition_claimed_account_reply_to_human_review",
                side_effect=delayed_transition,
            ), patch.object(worker, "ticket_repository", repository):
                transition_future = executor.submit(
                    worker._move_automation_reply_to_human_review,
                    claimed,
                    ticket,
                    "no enabled published persona",
                    policy_decision="account_persona_unavailable_human_review",
                )
                self.assertTrue(transition_started.wait(timeout=5))
                reset_result = repository.reset_account_rerun_state(
                    ticket_id,
                    reset_at="2026-03-22T00:02:00+00:00",
                    rerun_job_id="account-rerun-human-review-reset-fence",
                    clear_persona_assignment=True,
                )
                release_transition.set()
                transition_future.result(timeout=5)
        finally:
            release_transition.set()
            executor.shutdown(wait=True, cancel_futures=True)

        self.assertEqual(reset_result["reply_jobs_deleted"], 1)
        self.assertEqual(reset_result["reply_executions_deleted"], 1)
        self.assertEqual(reset_result["persona_assignments_deleted"], 1)
        self.assertEqual(reset_result["ai_messages_deleted"], 1)
        self.assertEqual(reset_result["customer_replies_cleared"], 1)
        self.assertIsNone(repository.get_account_reply_job(job_id))
        self.assertIsNone(repository.get_account_persona_assignment(ticket_id))
        self.assertEqual(repository.list_account_reply_executions(ticket_id), [])
        stored_ticket = repository.get_ticket(ticket_id)
        assert stored_ticket is not None
        self.assertEqual([message["role"] for message in stored_ticket["messages"]], ["customer"])
        stored_case = repository.get_billing_ticket("AC-HUMAN-REVIEW-RESET-FENCE")
        assert stored_case is not None
        self.assertEqual(stored_case["route"], "enablement")
        self.assertIsNone(stored_case["customer_reply"])
        self.assertNotEqual(stored_case.get("policy_decision"), "account_persona_unavailable_human_review")
        self.assertNotIn(
            "automation_persona_human_review",
            [event["event_type"] for event in repository.list_ticket_events(ticket_id)],
        )

    def test_outlook_reply_commit_stops_after_full_reset_wins_in_memory_fence(self) -> None:
        ticket_id = "TK-OUTLOOK-RESET-FENCE"
        repository = InMemoryTicketRepository()
        repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please enable the feature.",
                        "created_at": "2026-03-22T00:00:00+00:00",
                    }
                ],
            }
        )
        repository.save_account_case(
            {
                "account_case_id": "AC-OUTLOOK-RESET-FENCE",
                "billing_ticket_id": "AC-OUTLOOK-RESET-FENCE",
                "client_ticket_id": ticket_id,
                "title": "Enablement request",
                "question": "Please enable the feature.",
                "automation_handler": "enablement",
                "automation_status": "internal_processing",
                "route_status": "automated",
            }
        )
        reply = types.SimpleNamespace(
            message_id="outlook-reset-fence",
            subject=(
                "Re: [Enablement Request] Feature - "
                f"Ticket {ticket_id}"
            ),
            body_text="The feature is ready.",
        )

        def reset_after_render(**_kwargs):
            repository.reset_account_rerun_state(
                ticket_id,
                reset_at="2026-03-22T00:02:00+00:00",
                rerun_job_id="account-rerun-outlook-reset-fence",
                reset_mode=ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
                clear_persona_assignment=True,
            )
            return "Hi Customer,\n\nThe feature is ready.\n\nBest Regards,\nSid"

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_render_case_persona_reply",
            side_effect=reset_after_render,
        ), patch.object(
            worker,
            "classify_enablement_completion",
            return_value=_classifier_regex_fallback(),
        ):
            outcome = worker.handle_enablement_request_reply(reply)

        self.assertEqual(outcome, "in_progress")
        stored_ticket = repository.get_ticket(ticket_id)
        assert stored_ticket is not None
        self.assertEqual([message["role"] for message in stored_ticket["messages"]], ["customer"])
        stored_case = repository.get_account_case_by_ticket_id(ticket_id)
        assert stored_case is not None
        self.assertFalse(stored_case.get("customer_reply"))
        self.assertNotEqual(stored_case.get("automation_status"), "customer_notified")
        self.assertEqual(repository.list_ticket_events(ticket_id), [])

    def test_internal_reply_renders_with_persisted_persona_assignment(self) -> None:
        account_case = {
            "account_case_id": "AC-PERSONA-INTERNAL",
            "customer_name": "Alice",
        }
        assignment = {
            "persona_key": "sid-warm",
            "version": 5,
            "content": {"instruction": "Warm"},
        }
        repository = Mock()
        repository.resolve_account_persona.return_value = assignment
        rendered = types.SimpleNamespace(content="The request is complete.")
        save_case = Mock()

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "render_automation_reply", return_value=rendered
        ) as render:
            reply = worker._render_case_persona_reply(
                ticket_id="TK-PERSONA-INTERNAL",
                case=account_case,
                behavior="quota",
                reply_intent="resolution_update",
                save_case=save_case,
            )

        self.assertEqual(reply, "The request is complete.")
        repository.resolve_account_persona.assert_called_once_with("TK-PERSONA-INTERNAL")
        self.assertEqual(render.call_args.kwargs["persona_assignment"], assignment)
        save_case.assert_not_called()

    def test_enablement_confirmation_pins_persisted_persona_assignment(self) -> None:
        account_case = {
            "account_case_id": "AC-PERSONA-CONFIRMATION-PIN",
            "client_ticket_id": "TK-PERSONA-CONFIRMATION-PIN",
            "internal_email_payload": {"delivery_key": "enablement:AC-PERSONA-CONFIRMATION-PIN:v1"},
            "collected_fields": {"app_id": "alpha", "requested_feature": "media_relay"},
        }
        assignment = {
            "persona_key": "sid-bright",
            "version": 2,
            "content": {"instruction": "Bright"},
        }
        repository = Mock()
        repository.get_ticket.return_value = {
            "ticket_id": "TK-PERSONA-CONFIRMATION-PIN",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable Media Relay.",
                    "created_at": "2026-03-22T00:00:00+00:00",
                }
            ],
        }
        repository.get_latest_account_reply_job.return_value = None
        repository.resolve_account_persona.return_value = assignment

        with patch.object(worker, "ticket_repository", repository):
            created = worker._queue_enablement_submission_confirmation(account_case)

        self.assertTrue(created)
        repository.resolve_account_persona.assert_called_once_with("TK-PERSONA-CONFIRMATION-PIN")
        reply_job = repository.save_account_reply_job.call_args.args[0]
        self.assertEqual(reply_job["payload"]["persona_key"], "sid-bright")
        self.assertEqual(reply_job["payload"]["persona_version"], 2)
        self.assertEqual(reply_job["payload"]["effective_prompt"], assignment["content"])

    def test_published_persona_content_is_reused_on_retry(self) -> None:
        job = {
            "job_id": "account-reply-persona-retry",
            "ticket_id": "TK-PERSONA-RETRY",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "publishing",
            "scheduled_for": "2026-03-22T00:01:00+00:00",
            "payload": {
                "reply_facts": {"behavior": "quota", "reply_intent": "submission_confirmation"},
                "generated_content": "The request has been submitted.",
                "persona_prompt_version": worker.AUTOMATION_PERSONA_PROMPT_VERSION,
                "persona_key": "default-support",
                "persona_version": 1,
                "effective_prompt": {"instruction": "Warm"},
                "replace_existing_reply": True,
                "rerun_job_id": "account-rerun-1",
            },
        }
        ticket = {
            "ticket_id": "TK-PERSONA-RETRY",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please increase quota",
                    "created_at": "2026-03-22T00:00:00+00:00",
                },
                {
                    "role": "assistant",
                    "content": "Old reply",
                    "created_at": "2026-03-22T00:01:00+00:00",
                    "source": "account_ai",
                    "meta": {"account_reply_job_id": "account-reply-old", "source": "account_ai"},
                },
            ],
        }
        repository = Mock()
        repository.get_account_reply_job.return_value = job
        repository.get_ticket.return_value = ticket
        repository.get_billing_ticket_by_client_ticket_id.return_value = None
        repository.publish_account_reply.side_effect = lambda current_job, **kwargs: (
            current_job.update({"status": "published", "published_at": "2026-03-22T00:02:00+00:00"})
            or ticket["messages"].append(
                {
                    "role": "assistant",
                    "content": kwargs["content"],
                    "created_at": "2026-03-22T00:02:00+00:00",
                }
            )
            or {"content": kwargs["content"], "published_at": "2026-03-22T00:02:00+00:00"}
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "render_automation_reply"
        ) as render:
            worker._publish_account_reply_job(job)

        render.assert_not_called()
        self.assertEqual(job["status"], "published")
        self.assertEqual(ticket["messages"][-1]["content"], "The request has been submitted.")
        repository.publish_account_reply.assert_called_once()

    def test_unpublished_old_persona_content_is_regenerated_with_current_policy(self) -> None:
        job = {
            "job_id": "account-reply-persona-v6",
            "ticket_id": "TK-PERSONA-V6",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "publishing",
            "payload": {
                "reply_facts": {"behavior": "quota", "reply_intent": "submission_confirmation"},
                "generated_content": "The request has been submitted.",
                "persona_prompt_version": "automation-persona-v6",
                "persona_key": "default-support",
                "persona_version": 1,
                "effective_prompt": {"instruction": "Warm"},
            },
        }
        ticket = {
            "ticket_id": "TK-PERSONA-V6",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please increase quota",
                    "created_at": "2026-03-22T00:00:00+00:00",
                }
            ],
        }
        repository = Mock()
        repository.get_account_reply_job.return_value = job
        repository.get_ticket.return_value = ticket
        repository.get_billing_ticket_by_client_ticket_id.return_value = None
        repository.update_claimed_account_reply_job.return_value = job
        repository.publish_account_reply.return_value = {"status": "published"}
        rendered = types.SimpleNamespace(
            content="I am coordinating this request and will keep you updated.",
            model="persona-model",
            prompt_version=worker.AUTOMATION_PERSONA_PROMPT_VERSION,
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "render_automation_reply", return_value=rendered
        ) as render:
            worker._publish_account_reply_job(job)

        render.assert_called_once()
        self.assertEqual(job["payload"]["generated_content"], rendered.content)
        self.assertEqual(
            job["payload"]["persona_prompt_version"],
            worker.AUTOMATION_PERSONA_PROMPT_VERSION,
        )
        repository.publish_account_reply.assert_called_once()

    def test_unpublished_enablement_completion_v15_needs_v16_persona_render(self) -> None:
        job = {
            "job_id": "account-reply-enablement-completion-v15",
            "ticket_id": "TK-ENABLEMENT-COMPLETION-V15",
            "trigger_message_created_at": "2026-08-27T00:00:00+00:00",
            "status": worker.ACCOUNT_REPLY_PERSONA_V8_PUBLISHING,
            "claimed_at": "2026-08-27T00:03:00+00:00",
            "attempt_count": 0,
            "payload": {
                "reply_facts": {
                    "behavior": "enablement",
                    "reply_intent": "enablement_completed_and_close",
                },
                "reply_intent": "enablement_completed_and_close",
                "close_after_publish": True,
                "internal_resolution": True,
                "generated_content": "Media Relay is enabled. This ticket is closing.",
                "persona_prompt_version": "automation-persona-v15",
                "persona_key": "sid-bright",
                "persona_version": 1,
                "effective_prompt": {"instruction": "Warm and precise."},
            },
        }
        ticket = {
            "ticket_id": job["ticket_id"],
            "messages": [
                {"role": "customer", "content": "Please enable Media Relay."},
                {
                    "role": "assistant",
                    "content": "Please provide your App ID.",
                    "reply_intent": "request_missing_information",
                },
                {"role": "customer", "content": "Here is the App ID."},
            ],
        }
        repository = Mock()
        repository.get_account_reply_job.return_value = job
        repository.get_ticket.return_value = ticket
        repository.update_claimed_account_reply_job.return_value = job
        repository.publish_account_reply.return_value = {"status": "published"}
        rendered = types.SimpleNamespace(
            content=(
                "Hi, Customer\n\nThanks for providing the additional information. Media Relay is now enabled. "
                "We are archiving this case now. If you have further questions, you can open a new ticket."
            ),
            model="persona-model",
            prompt_version="automation-persona-v16",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "render_automation_reply", return_value=rendered
        ) as render:
            worker._publish_account_reply_job(job)

        self.assertEqual(worker.AUTOMATION_PERSONA_PROMPT_VERSION, "automation-persona-v16")
        self.assertEqual(
            render.call_args.kwargs["reply_facts"]["completion_acknowledgement"],
            "additional_information",
        )
        self.assertEqual(job["payload"]["persona_prompt_version"], "automation-persona-v16")
        repository.publish_account_reply.assert_called_once()

    def test_invalid_account_content_moves_to_human_review_before_publish(self) -> None:
        job = {
            "job_id": "account-reply-invalid-contract",
            "ticket_id": "TK-INVALID-CONTRACT",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "publishing",
            "scheduled_for": "2026-03-22T00:01:00+00:00",
            "payload": {
                "reply_facts": {
                    "behavior": "enablement",
                    "reply_intent": "submission_confirmation",
                },
                "generated_content": "We are reviewing the request.",
                "persona_prompt_version": worker.AUTOMATION_PERSONA_PROMPT_VERSION,
                "persona_key": "default-support",
                "persona_version": 1,
                "effective_prompt": {"instruction": "Warm"},
            },
        }
        ticket = {
            "ticket_id": "TK-INVALID-CONTRACT",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable the feature.",
                    "created_at": "2026-03-22T00:00:00+00:00",
                }
            ],
        }
        repository = Mock()
        repository.get_account_reply_job.return_value = job
        repository.get_ticket.return_value = ticket
        repository.get_account_case_by_ticket_id.return_value = None
        repository.transition_claimed_account_reply_to_human_review.return_value = {
            **job,
            "status": "human_review",
        }

        with patch.object(worker, "ticket_repository", repository):
            worker._publish_account_reply_job(job)

        repository.publish_account_reply.assert_not_called()
        repository.transition_claimed_account_reply_to_human_review.assert_called_once()
        self.assertEqual(
            repository.transition_claimed_account_reply_to_human_review.call_args.kwargs["policy_decision"],
            "account_reply_contract_human_review",
        )

    def test_signed_account_content_moves_to_human_review_before_publish(self) -> None:
        job = {
            "job_id": "account-reply-signed-content",
            "ticket_id": "TK-SIGNED-CONTENT",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "publishing",
            "scheduled_for": "2026-03-22T00:01:00+00:00",
            "payload": {
                "reply_facts": {
                    "behavior": "fraud_account",
                    "reply_intent": "fraud_handoff_confirmation",
                },
                "generated_content": (
                    "The relevant team will contact you within 24 hours.\n\n"
                    "Best,\nSid\nSupport Engineer 2"
                ),
                "persona_prompt_version": worker.AUTOMATION_PERSONA_PROMPT_VERSION,
                "persona_key": "default-support",
                "persona_version": 1,
                "effective_prompt": {"instruction": "Warm"},
            },
        }
        ticket = {
            "ticket_id": "TK-SIGNED-CONTENT",
            "messages": [
                {
                    "role": "customer",
                    "content": "I need help with a fraudulent account.",
                    "created_at": "2026-03-22T00:00:00+00:00",
                }
            ],
        }
        repository = Mock()
        repository.get_account_reply_job.return_value = job
        repository.get_ticket.return_value = ticket
        repository.get_account_case_by_ticket_id.return_value = None
        repository.transition_claimed_account_reply_to_human_review.return_value = {
            **job,
            "status": "human_review",
        }

        with patch.object(worker, "ticket_repository", repository):
            worker._publish_account_reply_job(job)

        repository.publish_account_reply.assert_not_called()
        repository.transition_claimed_account_reply_to_human_review.assert_called_once()
        self.assertEqual(
            repository.transition_claimed_account_reply_to_human_review.call_args.kwargs["policy_decision"],
            "account_reply_contract_human_review",
        )

    def test_persona_reply_facts_are_rendered_before_scheduling(self) -> None:
        job = {
            "job_id": "account-reply-persona-prepare",
            "ticket_id": "TK-PERSONA-PREPARE",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "preparing",
            "scheduled_for": "2026-03-22T00:07:00+00:00",
            "payload": {
                "reply_facts": {"behavior": "enablement", "reply_intent": "request_missing_information"},
                "persona_key": "default-support",
                "persona_version": 3,
                "effective_prompt": {"instruction": "Warm", "signature": "Best,\\nSid"},
            },
        }
        ticket = {
            "ticket_id": "TK-PERSONA-PREPARE",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable the feature.",
                    "created_at": "2026-03-22T00:00:00+00:00",
                }
            ],
        }
        repository = Mock()
        repository.get_account_reply_job.return_value = job
        repository.get_ticket.return_value = ticket
        repository.update_claimed_account_reply_job.return_value = job
        rendered = types.SimpleNamespace(
            content="Hi Customer,\\n\\nPlease share the App ID.\\n\\nBest,\\nSid",
            model="persona-model",
            prompt_version="automation-persona-v4",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "render_automation_reply", return_value=rendered
        ) as render, patch.object(worker, "resolve_support_message") as legacy_resolver:
            worker._prepare_account_reply_job(job)

        render.assert_called_once()
        legacy_resolver.assert_not_called()
        self.assertEqual(job["status"], worker.ACCOUNT_REPLY_PERSONA_SCHEDULED)
        self.assertEqual(job["payload"]["generated_content"], rendered.content)
        self.assertEqual(job["payload"]["persona_render_status"], "generated")
        repository.update_claimed_account_reply_job.assert_called_once_with(
            job,
            expected_status="preparing",
            expected_claimed_at=None,
            expected_attempt_count=0,
        )
        repository.save_account_reply_job.assert_not_called()

    def test_publishing_persona_reply_cancels_stale_customer_revision_with_reason(self) -> None:
        job = {
            "job_id": "account-reply-stale-customer",
            "ticket_id": "TK-STALE-CUSTOMER",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "publishing",
            "scheduled_for": "2026-03-22T00:07:00+00:00",
            "payload": {
                "reply_facts": {"behavior": "quota", "reply_intent": "submission_confirmation"},
                "generated_content": "The request has been submitted.",
                "persona_key": "default-support",
                "persona_version": 1,
                "effective_prompt": {"instruction": "Warm"},
            },
        }
        repository = Mock()
        repository.get_account_reply_job.return_value = job
        repository.get_ticket.return_value = {
            "ticket_id": "TK-STALE-CUSTOMER",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please increase quota",
                    "created_at": "2026-03-22T00:00:00+00:00",
                },
                {
                    "role": "customer",
                    "content": "I have one more detail.",
                    "created_at": "2026-03-22T00:01:00+00:00",
                },
            ],
        }
        repository.update_claimed_account_reply_job.return_value = job

        with patch.object(worker, "ticket_repository", repository):
            worker._publish_account_reply_job(job)

        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(job["payload"]["cancel_reason"], "stale_customer_revision")
        repository.publish_account_reply.assert_not_called()
        repository.update_claimed_account_reply_job.assert_called_once()

    def test_deleted_account_reply_job_is_not_recreated_by_stale_worker(self) -> None:
        job = {
            "job_id": "account-reply-deleted",
            "ticket_id": "TK-DELETED-REPLY",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "preparing",
            "scheduled_for": "2026-03-22T00:07:00+00:00",
            "payload": {
                "reply_facts": {"behavior": "quota", "reply_intent": "submission_confirmation"},
                "persona_key": "default-support",
                "persona_version": 1,
                "effective_prompt": {"instruction": "Warm"},
            },
        }
        repository = Mock()
        repository.get_account_reply_job.return_value = job
        repository.get_ticket.return_value = {
            "ticket_id": "TK-DELETED-REPLY",
            "messages": [{"role": "customer", "content": "Please increase quota"}],
        }
        repository.update_claimed_account_reply_job.return_value = None

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "render_automation_reply",
            return_value=types.SimpleNamespace(
                content="The request has been submitted.",
                model="persona-model",
                prompt_version="automation-persona-v4",
            ),
        ):
            worker._prepare_account_reply_job(job)

        repository.save_account_reply_job.assert_not_called()
        repository.update_claimed_account_reply_job.assert_called_once_with(
            job,
            expected_status="preparing",
            expected_claimed_at=None,
            expected_attempt_count=0,
        )
        repository.publish_account_reply.assert_not_called()

    def test_persona_failure_moves_reply_job_to_human_review_without_sending(self) -> None:
        job = {
            "job_id": "account-reply-persona-human-review",
            "ticket_id": "TK-PERSONA-HR",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "publishing",
            "scheduled_for": "2026-03-22T00:01:00+00:00",
            "payload": {
                "reply_facts": {"behavior": "quota", "reply_intent": "submission_confirmation"},
                "persona_key": "default-support",
                "persona_version": 1,
                "effective_prompt": {"instruction": "Warm"},
            },
        }
        ticket = {
            "ticket_id": "TK-PERSONA-HR",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please increase quota",
                    "created_at": "2026-03-22T00:00:00+00:00",
                }
            ],
        }
        billing_ticket = {
            "account_case_id": "AC-TK-PERSONA-HR",
            "route": "quota",
            "route_family": "automated",
            "execution_action": "quota",
            "automation_status": "automation",
            "internal_email_send_status": "sent",
        }
        repository = Mock()
        repository.get_account_reply_job.return_value = job
        repository.get_ticket.return_value = ticket
        repository.get_billing_ticket_by_client_ticket_id.return_value = billing_ticket
        transitioned_job = copy.deepcopy(job)
        transitioned_job.update(
            status="manual_attention",
            payload={
                **copy.deepcopy(job["payload"]),
                "error": "automation_persona_missing_credentials",
                "persona_render_status": "human_review",
            },
            updated_at="2026-03-22T00:02:00+00:00",
        )
        repository.transition_claimed_account_reply_to_human_review.return_value = transitioned_job

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "render_automation_reply",
            side_effect=worker.AutomationPersonaError("automation_persona_missing_credentials"),
        ):
            worker._publish_account_reply_job(job)

        self.assertEqual(job["status"], "manual_attention")
        self.assertEqual(job["payload"]["persona_render_status"], "human_review")
        self.assertEqual(job["payload"]["error"], "automation_persona_missing_credentials")
        repository.transition_claimed_account_reply_to_human_review.assert_called_once()
        transition_call = repository.transition_claimed_account_reply_to_human_review.call_args
        self.assertIs(transition_call.args[0], job)
        self.assertEqual(transition_call.kwargs["expected_status"], "publishing")
        self.assertIsNone(transition_call.kwargs["expected_claimed_at"])
        self.assertEqual(transition_call.kwargs["expected_attempt_count"], 0)
        self.assertEqual(
            transition_call.kwargs["policy_decision"],
            "automation_persona_human_review",
        )
        self.assertEqual(
            transition_call.kwargs["reason"],
            "automation_persona_missing_credentials",
        )
        self.assertEqual(len(ticket["messages"]), 1)
        repository.save_account_reply_job.assert_not_called()
        repository.save_billing_ticket.assert_not_called()
        repository.record_event.assert_not_called()
        repository.publish_account_reply.assert_not_called()

    def test_persona_failure_transitions_job_before_case_failure_cancels_pending_jobs(self) -> None:
        repository = InMemoryTicketRepository()
        repository.initialize()
        ticket_id = "TK-PERSONA-ORDER"
        case_id = "AC-TK-PERSONA-ORDER"
        repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "customer_id": "customer@example.invalid",
                "subject": "Enable media relay",
                "status": "open",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please enable media relay.",
                        "created_at": "2026-03-22T00:00:00+00:00",
                    }
                ],
            }
        )
        email_payload = {
            "delivery_key": f"enablement:{case_id}:v1",
            "subject": "[Enablement Request] Media Relay",
        }
        repository.save_account_case(
            {
                "account_case_id": case_id,
                "billing_ticket_id": case_id,
                "client_ticket_id": ticket_id,
                "route": "enablement",
                "category": "automation",
                "subcategory": "enablement",
                "route_family": "automated",
                "route_status": "automated",
                "automation_handler": "enablement",
                "automation_status": "in_progress",
                "internal_email_send_status": "sent",
                "internal_email_send_reason": "sent",
                "internal_email_payload": email_payload,
                "route_classification": {
                    "primary_label": "Agora",
                    "secondary_label": "Backend Operation / Enablement",
                    "handler_binding_status": "active",
                },
            }
        )
        job = {
            "job_id": "account-reply-persona-order",
            "ticket_id": ticket_id,
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "persona_publishing",
            "payload": {
                "reply_facts": {
                    "behavior": "enablement",
                    "reply_intent": "submission_confirmation",
                },
                "persona_key": "default-support",
                "persona_version": 1,
                "effective_prompt": {"instruction": "Warm"},
            },
        }
        repository.save_account_reply_job(job)

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "render_automation_reply",
            side_effect=worker.AutomationPersonaError(
                "automation_persona_fraud_handoff_contract_failed",
                attempt_count=4,
            ),
        ), patch.object(worker, "notify_account_failure") as notify:
            worker._publish_account_reply_job(job)

        saved_job = repository.get_account_reply_job(job["job_id"])
        saved_case = repository.get_account_case(case_id)
        assert saved_job is not None
        assert saved_case is not None
        self.assertEqual(saved_job["status"], "manual_attention")
        self.assertEqual(saved_job["payload"]["failure_stage"], "automation_persona")
        self.assertEqual(
            saved_job["payload"]["failure_code"],
            "automation_persona_fraud_handoff_contract_failed",
        )
        self.assertEqual(saved_case["failure_attempt_count"], 4)
        self.assertEqual(saved_case["automation_status"], "human_review_required")
        self.assertEqual(saved_case["internal_email_send_status"], "sent")
        self.assertEqual(saved_case["internal_email_payload"], email_payload)
        incident_id = notify.call_args.kwargs["incident_id"]
        self.assertIn(job["job_id"], incident_id)

    def test_reply_worker_exhaustion_persists_stable_operation_stage_and_code(self) -> None:
        job = {
            "job_id": "account-reply-exhausted",
            "ticket_id": "TK-REPLY-EXHAUSTED",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "persona_preparing",
            "attempt_count": 4,
            "payload": {},
        }
        repository = Mock()
        repository.claim_account_reply_jobs.return_value = [job]
        repository.get_account_reply_job.return_value = copy.deepcopy(job)
        repository.update_claimed_account_reply_job.side_effect = lambda saved, **_kwargs: saved

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_prepare_account_reply_job",
            side_effect=RuntimeError("preparation exploded"),
        ), patch.object(worker, "_record_account_worker_failure") as record_failure:
            worker._process_claimed_account_reply_jobs(
                from_status="persona_queued",
                to_status="persona_preparing",
                due_only=False,
                limit=1,
            )

        saved_job = repository.update_claimed_account_reply_job.call_args.args[0]
        self.assertEqual(saved_job["status"], "failed")
        self.assertEqual(saved_job["payload"]["failure_stage"], "reply_prepare")
        self.assertEqual(
            saved_job["payload"]["failure_code"],
            "account_reply_preparation_failed",
        )
        record_failure.assert_called_once()


if __name__ == "__main__":
    unittest.main()
