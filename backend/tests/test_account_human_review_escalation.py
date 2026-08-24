from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.services.account_human_review_escalation import (
    escalate_account_case_to_human_review,
)
from backend.services.zendesk_comments import ZendeskCommentError


class AccountHumanReviewEscalationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = Mock()
        self.repository.begin_idempotent_request.return_value = {
            "created": True,
            "response_payload": None,
        }
        self.case = {
            "account_case_id": "AC-123",
            "client_ticket_id": "123",
            "zendesk_ticket_id": "123",
            "route": "enablement",
            "execution_action": "enablement",
            "automation_handler": "enablement",
            "processing_profile": "production",
            "automation_context": {
                "zendesk_ownership": {
                    "source_group_id": "2721",
                    "assignee_id": "ai-1",
                    "group_id": "ai-group",
                }
            },
        }

    def _escalate(self, **overrides):
        case = {**self.case, **overrides}
        return escalate_account_case_to_human_review(
            account_case=case,
            ticket_id=str(case.get("client_ticket_id") or "123"),
            handler="enablement",
            failure_stage="reply_worker",
            failure_code="persona_render_failed",
            reason="persona unavailable",
            repository=self.repository,
            timestamp="2026-08-24T00:00:00+00:00",
        ), case

    @patch("backend.services.account_human_review_escalation.route_ticket_back_to_queue")
    @patch("backend.services.account_human_review_escalation.add_ticket_comment")
    @patch("backend.services.account_human_review_escalation.read_ticket_comment_audit")
    def test_production_writes_private_note_and_routes_back(
        self, audit, add_comment, route
    ) -> None:
        audit.return_value = (None, False)
        add_comment.return_value = SimpleNamespace(comment_id="comment-1")
        route.return_value = SimpleNamespace(status="queued")

        result, case = self._escalate()

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.internal_note_status, "sent")
        self.assertEqual(result.route_back_status, "queued")
        add_comment.assert_called_once()
        self.assertIn("Failure code: persona_render_failed", add_comment.call_args.kwargs["body"])
        route.assert_called_once_with(ticket_id="123", source_group_id="2721")
        self.assertEqual(case["automation_status"], "human_review_required")
        self.assertEqual(case["route_status"], "not_automated")
        self.repository.cancel_pending_account_reply_jobs.assert_called_once()

    @patch("backend.services.account_human_review_escalation.route_ticket_back_to_queue")
    @patch("backend.services.account_human_review_escalation.add_ticket_comment")
    def test_staging_has_no_zendesk_side_effect(self, add_comment, route) -> None:
        result, case = self._escalate(processing_profile="staging")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.internal_note_status, "skipped_not_production")
        self.assertEqual(result.route_back_status, "skipped_not_production")
        add_comment.assert_not_called()
        route.assert_not_called()
        self.assertEqual(case["automation_status"], "human_review_required")

    @patch("backend.services.account_human_review_escalation.route_ticket_back_to_queue")
    @patch("backend.services.account_human_review_escalation.add_ticket_comment")
    @patch("backend.services.account_human_review_escalation.read_ticket_comment_audit")
    def test_note_failure_does_not_prevent_route(self, audit, add_comment, route) -> None:
        audit.return_value = (None, False)
        add_comment.side_effect = ZendeskCommentError("permanent", error_code="note_failed")
        route.return_value = SimpleNamespace(status="queued")

        result, _ = self._escalate()

        self.assertEqual(result.internal_note_status, "failed:note_failed")
        self.assertEqual(result.route_back_status, "queued")
        route.assert_called_once()

    @patch("backend.services.account_human_review_escalation.route_ticket_back_to_queue")
    @patch("backend.services.account_human_review_escalation.add_ticket_comment")
    @patch("backend.services.account_human_review_escalation.read_ticket_comment_audit")
    def test_route_failure_does_not_prevent_note(self, audit, add_comment, route) -> None:
        audit.return_value = (None, False)
        add_comment.return_value = SimpleNamespace(comment_id="comment-1")
        route.side_effect = ZendeskCommentError("permanent", error_code="route_failed")

        result, _ = self._escalate()

        self.assertEqual(result.internal_note_status, "sent")
        self.assertEqual(result.route_back_status, "failed:route_failed")
        self.assertEqual(result.status, "degraded")

    @patch("backend.services.account_human_review_escalation.route_ticket_back_to_queue")
    @patch("backend.services.account_human_review_escalation.add_ticket_comment")
    @patch("backend.services.account_human_review_escalation.read_ticket_comment_audit")
    def test_idempotent_replay_reconciles_existing_note(self, audit, add_comment, route) -> None:
        audit.return_value = (SimpleNamespace(comment_id="existing"), False)
        route.return_value = SimpleNamespace(status="already_human_owned")

        result, _ = self._escalate()

        self.assertEqual(result.internal_note_status, "sent")
        self.assertEqual(result.note_comment_id, "existing")
        add_comment.assert_not_called()
        self.assertEqual(result.route_back_status, "already_human_owned")

    @patch("backend.services.account_human_review_escalation.route_ticket_back_to_queue")
    @patch("backend.services.account_human_review_escalation.add_ticket_comment")
    def test_nonnumeric_identity_skips_zendesk_but_marks_human_review(self, add_comment, route) -> None:
        result, case = self._escalate(
            client_ticket_id="ticket-opaque", zendesk_ticket_id="ticket-opaque"
        )

        self.assertEqual(result.route_back_status, "skipped_missing_zendesk_ticket")
        add_comment.assert_not_called()
        route.assert_not_called()
        self.assertEqual(case["automation_status"], "human_review_required")

    @patch("backend.services.account_human_review_escalation.route_ticket_back_to_queue")
    @patch("backend.services.account_human_review_escalation.add_ticket_comment")
    @patch("backend.services.account_human_review_escalation.read_ticket_comment_audit")
    def test_outcome_unknown_note_is_not_retried(self, audit, add_comment, route) -> None:
        audit.side_effect = ZendeskCommentError("outcome_unknown", error_code="audit_unknown")
        route.return_value = SimpleNamespace(status="queued")

        first, _ = self._escalate()
        second, _ = self._escalate()

        self.assertEqual(first.internal_note_status, "outcome_unknown")
        self.assertEqual(second.internal_note_status, "outcome_unknown")
        add_comment.assert_not_called()
        self.assertEqual(route.call_count, 2)


if __name__ == "__main__":
    unittest.main()
