from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.workspace_auth import WorkspacePrincipal
from backend.services.zendesk_comments import ZendeskCommentError
from backend.services.zendesk_ticket_assignment import (
    ZendeskAssignmentResult,
    ZendeskRouteBackResult,
)


class AccountZendeskAssignmentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.original_repository = main.ticket_repository
        self.original_dependency_overrides = dict(main.app.dependency_overrides)
        main.ticket_repository = self.repository
        main.app.dependency_overrides[main.require_workspace_admin] = lambda: WorkspacePrincipal(
            account_id="assignment-test-admin",
            role="admin",
            display_name="Assignment Test Admin",
            expires_at=4_102_444_800,
        )
        self.client = TestClient(main.app)
        now = "2026-08-17T00:00:00+00:00"
        self.repository.save_ticket({
            "ticket_id": "12807",
            "customer_id": "customer-12807",
            "requester": "customer-12807",
            "subject": "Assignment validation",
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "messages": [{"role": "customer", "content": "Question", "created_at": now}],
        })
        self.repository.save_account_case({
            "account_case_id": "AC-12807",
            "billing_ticket_id": "AC-12807",
            "client_ticket_id": "12807",
            "source": "https://agoraio.zendesk.com/agent/tickets/12807",
            "title": "Assignment validation",
            "question": "Question",
            "processing_profile": "production",
            "zendesk_ticket_id": "12807",
            "automation_status": "automation",
            "automation_context": {
                "zendesk_ownership": {
                    "state": "assigned",
                    "assignee_id": "48557297720084",
                    "group_id": "29388501432596",
                    "source_assignee_id": "31116634341396",
                    "source_group_id": "27216253642772",
                }
            },
            "created_at": now,
            "updated_at": now,
        })

    def tearDown(self) -> None:
        self.client.close()
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(self.original_dependency_overrides)
        main.ticket_repository = self.original_repository

    def test_admin_can_assign_linked_case(self) -> None:
        result = ZendeskAssignmentResult(
            ticket_id="12807",
            assignee_id="48557297720084",
            assignee_email="ai-support-agent@agora.io",
            assignee_name="AI Support",
            group_id="27216254064148",
            previous_group_id="27216254064148",
            group_changed=False,
            status_code=200,
            already_assigned=False,
        )
        with patch("backend.main.assign_ticket_to_configured_ai", return_value=result) as assign:
            response = self.client.post("/api/account/cases/AC-12807/zendesk-ai-assignment")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["assignee_id"], "48557297720084")
        self.assertEqual(response.json()["group_id"], "27216254064148")
        self.assertFalse(response.json()["group_changed"])
        assign.assert_called_once_with(ticket_id="12807")

    def test_admin_response_exposes_zendesk_final_group_change(self) -> None:
        result = ZendeskAssignmentResult(
            ticket_id="12807",
            assignee_id="48557297720084",
            assignee_email="ai-support-agent@agora.io",
            assignee_name="AI Support",
            group_id="29388501432596",
            previous_group_id="27216254064148",
            group_changed=True,
            status_code=200,
            already_assigned=False,
        )
        with patch("backend.main.assign_ticket_to_configured_ai", return_value=result):
            response = self.client.post("/api/account/cases/AC-12807/zendesk-ai-assignment")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["group_id"], "29388501432596")
        self.assertEqual(response.json()["previous_group_id"], "27216254064148")
        self.assertTrue(response.json()["group_changed"])

    def test_unlinked_case_is_rejected(self) -> None:
        self.repository.save_account_case({
            "account_case_id": "AC-MANUAL",
            "billing_ticket_id": "AC-MANUAL",
            "client_ticket_id": "manual",
            "source": "account-ui",
            "title": "Manual",
            "question": "Question",
        })
        response = self.client.post("/api/account/cases/AC-MANUAL/zendesk-ai-assignment")
        self.assertEqual(response.status_code, 400, response.text)

    def test_non_admin_is_rejected(self) -> None:
        main.app.dependency_overrides.pop(main.require_workspace_admin, None)
        main.app.dependency_overrides[main.require_workspace_principal] = lambda: WorkspacePrincipal(
            account_id="assignment-test-engineer",
            role="engineer",
            display_name="Assignment Test Engineer",
            expires_at=4_102_444_800,
        )
        response = self.client.post("/api/account/cases/AC-12807/zendesk-ai-assignment")
        self.assertEqual(response.status_code, 403, response.text)

    def test_permission_error_is_not_reported_as_group_membership_error(self) -> None:
        with patch(
            "backend.main.assign_ticket_to_configured_ai",
            side_effect=ZendeskCommentError(
                "permanent",
                status_code=403,
                error_code="zendesk_http_error",
            ),
        ):
            response = self.client.post("/api/account/cases/AC-12807/zendesk-ai-assignment")

        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("lacks permission", response.json()["detail"])
        self.assertNotIn("ticket group", response.json()["detail"])

    def test_unprocessable_assignment_reports_group_membership_error(self) -> None:
        with patch(
            "backend.main.assign_ticket_to_configured_ai",
            side_effect=ZendeskCommentError(
                "permanent",
                status_code=422,
                error_code="zendesk_http_error",
            ),
        ):
            response = self.client.post("/api/account/cases/AC-12807/zendesk-ai-assignment")

        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("ownership transfer", response.json()["detail"])
        self.assertNotIn("ticket group", response.json()["detail"])

    def test_admin_can_route_production_case_back_and_stop_ai(self) -> None:
        result = ZendeskRouteBackResult(
            ticket_id="12807",
            status="queued",
            assignee_id=None,
            group_id="27216253642772",
            source_group_id="27216253642772",
            status_code=200,
            updated=True,
        )
        with patch(
            "backend.main.route_ticket_back_to_queue", return_value=result
        ) as route_back, patch.object(
            self.repository,
            "cancel_pending_account_reply_jobs",
            wraps=self.repository.cancel_pending_account_reply_jobs,
        ) as cancel_jobs:
            response = self.client.post(
                "/api/account/cases/AC-12807/zendesk-route-back-to-queue"
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "queued")
        route_back.assert_called_once_with(
            ticket_id="12807", source_group_id="27216253642772"
        )
        cancel_jobs.assert_called_once()
        self.assertEqual(cancel_jobs.call_args.args[0], "12807")
        saved = self.repository.get_account_case("AC-12807")
        self.assertEqual(saved["automation_status"], "human_review_required")
        self.assertEqual(saved["policy_decision"], "manual_zendesk_route_back_to_queue")
        ownership = saved["automation_context"]["zendesk_ownership"]
        self.assertEqual(ownership["state"], "released_to_queue")
        self.assertEqual(ownership["handoff_status"], "queued")
        events = self.repository.list_workspace_audit_events()
        self.assertEqual(events[0]["event_type"], "account_zendesk_route_back_to_queue")
        self.assertEqual(events[0]["payload"]["status"], "queued")

    def test_route_back_failure_keeps_ai_stopped_and_audits_failure(self) -> None:
        with patch(
            "backend.main.route_ticket_back_to_queue",
            side_effect=ZendeskCommentError(
                "permanent", error_code="zendesk_source_group_unavailable"
            ),
        ):
            response = self.client.post(
                "/api/account/cases/AC-12807/zendesk-route-back-to-queue"
            )

        self.assertEqual(response.status_code, 409, response.text)
        saved = self.repository.get_account_case("AC-12807")
        self.assertEqual(saved["automation_status"], "human_review_required")
        ownership = saved["automation_context"]["zendesk_ownership"]
        self.assertEqual(ownership["state"], "released_to_queue")
        self.assertEqual(ownership["handoff_status"], "failed")
        self.assertEqual(
            self.repository.list_workspace_audit_events()[0]["payload"]["failure_code"],
            "zendesk_source_group_unavailable",
        )

    def test_route_back_rejects_non_production_case_before_zendesk(self) -> None:
        case = self.repository.get_account_case("AC-12807")
        case["processing_profile"] = "staging"
        self.repository.save_account_case(case)
        with patch("backend.main.route_ticket_back_to_queue") as route_back:
            response = self.client.post(
                "/api/account/cases/AC-12807/zendesk-route-back-to-queue"
            )

        self.assertEqual(response.status_code, 409, response.text)
        route_back.assert_not_called()
        saved = self.repository.get_account_case("AC-12807")
        self.assertEqual(saved["automation_status"], "automation")

    def test_route_back_does_not_release_already_human_owned_ticket(self) -> None:
        result = ZendeskRouteBackResult(
            ticket_id="12807",
            status="already_human_owned",
            assignee_id="31116634341396",
            group_id="27216253642772",
            source_group_id="27216253642772",
            status_code=200,
            updated=False,
        )
        with patch("backend.main.route_ticket_back_to_queue", return_value=result):
            response = self.client.post(
                "/api/account/cases/AC-12807/zendesk-route-back-to-queue"
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "already_human_owned")
        self.assertFalse(response.json()["updated"])

    def test_route_back_outcome_unknown_cannot_be_blindly_retried(self) -> None:
        case = self.repository.get_account_case("AC-12807")
        case["automation_status"] = "human_review_required"
        case["automation_context"]["zendesk_ownership"].update(
            {"state": "released_to_queue", "handoff_status": "outcome_unknown"}
        )
        self.repository.save_account_case(case)
        with patch("backend.main.route_ticket_back_to_queue") as route_back:
            response = self.client.post(
                "/api/account/cases/AC-12807/zendesk-route-back-to-queue"
            )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("verify the ticket", response.json()["detail"])
        route_back.assert_not_called()

if __name__ == "__main__":
    unittest.main()
