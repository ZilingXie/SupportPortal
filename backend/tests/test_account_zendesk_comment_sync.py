from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

import backend.main as main
from backend.repositories.ticket_repository import ACCOUNT_RERUN_RESET_AI_ONLY, InMemoryTicketRepository
from backend.services.account_zendesk_comments import (
    ZendeskCommentSnapshotError,
    normalize_snapshot,
)


SOURCE_UPDATED_AT = "2026-08-16T02:00:00Z"


def snapshot_payload(*comments: dict[str, object], source_updated_at: str = SOURCE_UPDATED_AT) -> dict[str, object]:
    return {
        "source_updated_at": source_updated_at,
        "snapshot_complete": True,
        "comments": list(comments),
    }


class AccountZendeskCommentSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.ticket_id = "12620"
        self.case_id = "AC-12620"
        self.repository.save_ticket(
            {
                "ticket_id": self.ticket_id,
                "updated_at": SOURCE_UPDATED_AT,
                "messages": [
                    {
                        "role": "customer",
                        "content": "Original request",
                        "created_at": "2026-08-16T01:00:00Z",
                    },
                    {
                        "role": "assistant",
                        "content": "AI reply",
                        "created_at": "2026-08-16T01:05:00Z",
                        "source": "account_ai",
                    },
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": self.case_id,
                "billing_ticket_id": self.case_id,
                "client_ticket_id": self.ticket_id,
                "title": "Account request",
                "question": "Original request",
                "route_family": "automated",
                "route_status": "automated",
                "automation_status": "automation",
            }
        )

    def test_normalizes_public_internal_comments_and_marks_initial(self) -> None:
        snapshot = normalize_snapshot(
            snapshot_payload(
                {
                    "id": 20,
                    "public": False,
                    "author": {"id": 7, "name": "Agent Note", "role": "agent"},
                    "body": "Private handoff",
                    "via": {"channel": "web"},
                    "created_at": "2026-08-16T01:20:00Z",
                },
                {
                    "id": 19,
                    "public": True,
                    "author": {"id": 8, "name": "Customer Name", "role": "end-user"},
                    "body": "Public follow-up",
                    "via": {"channel": "email"},
                    "created_at": "2026-08-16T01:10:00Z",
                },
            )
        )

        self.assertEqual([comment.zendesk_comment_id for comment in snapshot.comments], ["19", "20"])
        self.assertTrue(snapshot.comments[0].is_initial)
        self.assertTrue(snapshot.comments[0].is_public)
        self.assertFalse(snapshot.comments[1].is_public)
        self.assertEqual(snapshot.comments[0].author_kind, "customer")
        self.assertEqual(snapshot.comments[1].author_kind, "agent")
        self.assertEqual(snapshot.comments[0].via_channel, "email")

    def test_duplicate_comment_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(ZendeskCommentSnapshotError, "duplicate Zendesk comment id"):
            normalize_snapshot(
                snapshot_payload(
                    {"id": "same", "public": True, "body": "one", "created_at": "2026-08-16T01:00:00Z"},
                    {"id": "same", "public": True, "body": "two", "created_at": "2026-08-16T01:01:00Z"},
                )
            )

    def test_sync_is_idempotent_and_preserves_comments_after_rerun_reset(self) -> None:
        snapshot = normalize_snapshot(
            snapshot_payload(
                {
                    "id": "100",
                    "public": True,
                    "author": {"name": "Customer", "role": "end-user"},
                    "body": "Public comment",
                    "created_at": "2026-08-16T01:10:00Z",
                },
                {
                    "id": "101",
                    "public": False,
                    "author": {"name": "Agent", "role": "agent"},
                    "body": "Internal note",
                    "created_at": "2026-08-16T01:20:00Z",
                },
            )
        )
        first = self.repository.sync_account_case_comments(
            ticket_id=self.ticket_id,
            account_case_id=self.case_id,
            snapshot=snapshot,
            synced_at="2026-08-16T02:01:00+00:00",
        )
        second = self.repository.sync_account_case_comments(
            ticket_id=self.ticket_id,
            account_case_id=self.case_id,
            snapshot=snapshot,
            synced_at="2026-08-16T02:02:00+00:00",
        )

        self.assertEqual(first["status"], "synced")
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(len(self.repository.get_account_case_comments(self.ticket_id)), 2)

        self.repository.reset_account_rerun_state(
            self.ticket_id,
            reset_at="2026-08-16T02:03:00+00:00",
            rerun_job_id="rerun-12620",
            reset_mode=ACCOUNT_RERUN_RESET_AI_ONLY,
            clear_persona_assignment=True,
        )
        self.assertEqual(len(self.repository.get_account_case_comments(self.ticket_id)), 2)
        details = self.repository.get_account_case_details([self.case_id])[self.case_id]
        self.assertEqual(len(details["ticket"]["messages"]), 1)
        self.assertEqual(len(details["zendesk_comments"]), 2)

    def test_incomplete_snapshot_cannot_remove_existing_comment(self) -> None:
        initial = normalize_snapshot(
            snapshot_payload(
                {"id": "100", "public": True, "body": "Existing", "created_at": "2026-08-16T01:10:00Z"}
            )
        )
        self.repository.sync_account_case_comments(
            ticket_id=self.ticket_id,
            account_case_id=self.case_id,
            snapshot=initial,
            synced_at="2026-08-16T02:01:00+00:00",
        )
        newer = normalize_snapshot(
            snapshot_payload(
                {"id": "101", "public": True, "body": "New", "created_at": "2026-08-16T01:20:00Z"},
                source_updated_at="2026-08-16T02:02:00Z",
            )
        )
        result = self.repository.sync_account_case_comments(
            ticket_id=self.ticket_id,
            account_case_id=self.case_id,
            snapshot=newer,
            synced_at="2026-08-16T02:03:00+00:00",
        )
        self.assertEqual(result["status"], "incomplete_snapshot")
        self.assertEqual(result["missing_comment_ids"], ["100"])
        self.assertEqual(len(self.repository.get_account_case_comments(self.ticket_id)), 1)

    def test_audit_does_not_contain_comment_body_author_or_token(self) -> None:
        snapshot = normalize_snapshot(
            snapshot_payload(
                {
                    "id": "100",
                    "public": True,
                    "author": {"name": "Sensitive Customer", "role": "end-user"},
                    "body": "Sensitive comment body",
                    "created_at": "2026-08-16T01:10:00Z",
                }
            )
        )
        self.repository.sync_account_case_comments(
            ticket_id=self.ticket_id,
            account_case_id=self.case_id,
            snapshot=snapshot,
            synced_at="2026-08-16T02:01:00+00:00",
        )
        audit = self.repository.list_workspace_audit_events()[0]
        serialized = str(audit)
        self.assertNotIn("Sensitive Customer", serialized)
        self.assertNotIn("Sensitive comment body", serialized)
        self.assertNotIn("ZENDESK_ACCOUNT_SYNC_TOKEN", serialized)
        self.assertEqual(audit["payload"].keys(), {"status", "client_ticket_id", "source_updated_at", "comment_count", "new_comment_count"})


class AccountZendeskCommentIntegrationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.repository.save_ticket({"ticket_id": "12620", "messages": []})
        self.repository.save_account_case(
            {
                "account_case_id": "AC-12620",
                "billing_ticket_id": "AC-12620",
                "client_ticket_id": "12620",
                "title": "Account request",
                "question": "Question",
            }
        )
        self.admin_account = self.repository.save_workspace_account(
            {
                "account_id": "comment-sync-admin",
                "email": "comment-sync-admin@example.com",
                "display_name": "Comment Sync Admin",
                "role": "admin",
                "password_hash": main.hash_workspace_password("comment-sync-admin-password"),
                "active": True,
            }
        )
        self.engineer_account = self.repository.save_workspace_account(
            {
                "account_id": "comment-sync-engineer",
                "email": "comment-sync-engineer@example.com",
                "display_name": "Comment Sync Engineer",
                "role": "engineer",
                "password_hash": main.hash_workspace_password("comment-sync-engineer-password"),
                "active": True,
            }
        )
        self.admin_access_token = main.create_workspace_access_token(self.admin_account)
        self.engineer_access_token = main.create_workspace_access_token(self.engineer_account)
        self.original_repository = main.ticket_repository
        main.ticket_repository = self.repository
        self.client = TestClient(main.app)
        self.token_patcher = patch.dict(os.environ, {"ZENDESK_ACCOUNT_SYNC_TOKEN": "test-sync-token"}, clear=False)
        self.token_patcher.start()

    def tearDown(self) -> None:
        self.token_patcher.stop()
        main.ticket_repository = self.original_repository
        self.client.close()

    def test_membership_and_sync_require_token_and_accept_complete_snapshot(self) -> None:
        target = "/api/integrations/zendesk/account-cases/12620/comment-sync-target"
        self.assertEqual(self.client.get(target).status_code, 401)
        membership = self.client.get(target, headers={"X-Zendesk-Account-Sync-Token": "test-sync-token"})
        self.assertEqual(membership.status_code, 200, membership.text)
        self.assertTrue(membership.json()["is_account_case"])

        response = self.client.put(
            "/api/integrations/zendesk/account-cases/12620/comments",
            headers={"X-Zendesk-Account-Sync-Token": "test-sync-token"},
            json=snapshot_payload(
                {"id": "100", "public": True, "body": "Public", "created_at": "2026-08-16T01:10:00Z"}
            ),
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "synced")
        self.assertEqual(response.json()["comment_count"], 1)

    def test_non_account_ticket_returns_false_without_querying_comments(self) -> None:
        self.repository.save_ticket({"ticket_id": "12621", "messages": []})
        response = self.client.get(
            "/api/integrations/zendesk/account-cases/12621/comment-sync-target",
            headers={"X-Zendesk-Account-Sync-Token": "test-sync-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["is_account_case"])

    def test_account_reads_require_admin_and_workspace_token_is_not_sync_token(self) -> None:
        account_cases = "/api/account/cases"
        self.assertEqual(self.client.get(account_cases).status_code, 401)
        self.assertEqual(
            self.client.get(
                account_cases,
                headers={"Authorization": f"Bearer {self.engineer_access_token}"},
            ).status_code,
            403,
        )
        admin_response = self.client.get(
            account_cases,
            headers={"Authorization": f"Bearer {self.admin_access_token}"},
        )
        self.assertEqual(admin_response.status_code, 200, admin_response.text)

        membership = self.client.get(
            "/api/integrations/zendesk/account-cases/12620/comment-sync-target",
            headers={"Authorization": f"Bearer {self.admin_access_token}"},
        )
        self.assertEqual(membership.status_code, 401)


if __name__ == "__main__":
    unittest.main()
