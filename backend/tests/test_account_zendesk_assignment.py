from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.workspace_auth import WorkspacePrincipal
from backend.services.zendesk_comments import ZendeskCommentError
from backend.services.zendesk_ticket_assignment import ZendeskAssignmentResult


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
            status_code=200,
            already_assigned=False,
        )
        with patch("backend.main.assign_ticket_to_configured_ai", return_value=result) as assign:
            response = self.client.post("/api/account/cases/AC-12807/zendesk-ai-assignment")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["assignee_id"], "48557297720084")
        self.assertEqual(response.json()["group_id"], "27216254064148")
        assign.assert_called_once_with(ticket_id="12807")

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
        self.assertIn("ticket group", response.json()["detail"])

if __name__ == "__main__":
    unittest.main()
