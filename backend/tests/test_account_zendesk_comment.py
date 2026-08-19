from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.workspace_auth import WorkspacePrincipal
from backend.services.zendesk_comments import ZendeskCommentError, ZendeskCommentResult


class AccountZendeskCommentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.original_repository = main.ticket_repository
        self.original_dependency_overrides = dict(main.app.dependency_overrides)
        main.ticket_repository = self.repository
        main.app.dependency_overrides[main.require_workspace_admin] = lambda: WorkspacePrincipal(
            account_id="account-comment-test-admin",
            role="admin",
            display_name="Account Comment Test Admin",
            expires_at=4_102_444_800,
        )
        self.client = TestClient(main.app)
        self.marker = "SupportPortal /account internal comment integration test marker"
        now = "2026-08-16T00:00:00+00:00"
        self.repository.save_ticket(
            {
                "ticket_id": "12807",
                "customer_id": "customer-12807",
                "requester": "customer-12807",
                "subject": "Account comment validation",
                "status": "open",
                "created_at": now,
                "updated_at": now,
                "messages": [
                    {
                        "role": "customer",
                        "content": "Original customer question",
                        "created_at": now,
                    },
                    {
                        "role": "assistant",
                        "content": self.marker,
                        "created_at": now,
                        "meta": {"source": "account_ai", "visibility": "account_only"},
                    },
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-12807",
                "billing_ticket_id": "AC-12807",
                "client_ticket_id": "12807",
                "source": "https://agoraio.zendesk.com/agent/tickets/12807",
                "title": "Account comment validation",
                "question": "Original customer question",
                "route_family": "automated",
                "route": "fraud_account",
                "execution_action": "fraud_account",
                "route_status": "automated",
                "automation_status": "automation",
                "created_at": now,
                "updated_at": now,
            }
        )

    def tearDown(self) -> None:
        self.client.close()
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(self.original_dependency_overrides)
        main.ticket_repository = self.original_repository

    def test_server_reads_ai_message_and_idempotently_writes_one_internal_comment(self) -> None:
        detail = self.client.get("/api/account/cases/AC-12807")
        self.assertEqual(detail.status_code, 200, detail.text)
        message = next(item for item in detail.json()["messages"] if item["role"] == "assistant")
        message_id = message["message_id"]

        with patch(
            "backend.services.account_zendesk_internal_comment.add_internal_comment",
            return_value=ZendeskCommentResult(comment_id="comment-12807", status_code=200),
        ) as add_comment:
            first = self.client.post(
                f"/api/account/cases/AC-12807/messages/{message_id}/zendesk-internal-comment",
                json={"body": "browser supplied body must be ignored", "public": True},
            )
            replay = self.client.post(
                f"/api/account/cases/AC-12807/messages/{message_id}/zendesk-internal-comment",
                json={"body": "different browser supplied body", "public": True},
            )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["status"], "added")
        self.assertEqual(first.json()["comment_id"], "comment-12807")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["idempotent_replay"])
        add_comment.assert_called_once_with(ticket_id="12807", body=self.marker)

        saved_detail = self.repository.get_account_case_details(["AC-12807"])["AC-12807"]
        saved_message = next(
            item for item in saved_detail["ticket"]["messages"] if item.get("message_id") == message_id
        )
        self.assertEqual(saved_message["meta"]["zendesk_internal_comment"]["status"], "added")
        self.assertEqual(
            saved_message["meta"]["zendesk_internal_comment"]["actor_id"],
            "account-comment-test-admin",
        )

    def test_only_ai_messages_can_be_written(self) -> None:
        detail = self.client.get("/api/account/cases/AC-12807")
        customer_message = next(item for item in detail.json()["messages"] if item["role"] == "customer")
        response = self.client.post(
            f"/api/account/cases/AC-12807/messages/{customer_message['message_id']}/zendesk-internal-comment"
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_outcome_unknown_is_not_retried_automatically(self) -> None:
        detail = self.client.get("/api/account/cases/AC-12807")
        message = next(item for item in detail.json()["messages"] if item["role"] == "assistant")
        message_id = message["message_id"]

        with patch(
            "backend.services.account_zendesk_internal_comment.add_internal_comment",
            side_effect=ZendeskCommentError(
                "outcome_unknown",
                error_code="zendesk_comment_visibility_unverified",
            ),
        ) as add_comment:
            first = self.client.post(
                f"/api/account/cases/AC-12807/messages/{message_id}/zendesk-internal-comment"
            )
            second = self.client.post(
                f"/api/account/cases/AC-12807/messages/{message_id}/zendesk-internal-comment"
            )

        self.assertEqual(first.status_code, 409, first.text)
        self.assertEqual(second.status_code, 409, second.text)
        self.assertIn("result is unknown", first.text)
        add_comment.assert_called_once_with(ticket_id="12807", body=self.marker)

        saved_detail = self.repository.get_account_case_details(["AC-12807"])["AC-12807"]
        saved_message = next(
            item for item in saved_detail["ticket"]["messages"] if item.get("message_id") == message_id
        )
        failure = saved_message["meta"]["zendesk_internal_comment"]
        self.assertEqual(failure["status"], "outcome_unknown")
        self.assertEqual(failure["error_code"], "zendesk_comment_visibility_unverified")


if __name__ == "__main__":
    unittest.main()
