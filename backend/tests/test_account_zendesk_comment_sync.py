from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

import backend.main as main
from backend.repositories.ticket_repository import ACCOUNT_RERUN_RESET_AI_ONLY, InMemoryTicketRepository
from backend.services.account_zendesk_comments import (
    ZendeskCommentSnapshotError,
    author_is_agent,
    normalize_snapshot,
)
from backend.services.engineer_cases import build_new_engineer_case


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
        self.assertIs(author_is_agent(snapshot.comments[0].author_kind), False)
        self.assertIs(author_is_agent(snapshot.comments[1].author_kind), True)
        self.assertEqual(snapshot.comments[0].via_channel, "email")

    def test_author_is_agent_can_supply_identity_when_role_is_missing(self) -> None:
        snapshot = normalize_snapshot(
            snapshot_payload(
                {
                    "id": "agent-flag",
                    "public": True,
                    "author": {"id": "7", "name": "Support", "is_agent": True},
                    "body": "Agent reply",
                    "created_at": "2026-08-16T01:20:00Z",
                },
                {
                    "id": "customer-flag",
                    "public": True,
                    "author": {"id": "8", "name": "Customer", "is_staff": False},
                    "body": "Customer reply",
                    "created_at": "2026-08-16T01:21:00Z",
                },
            )
        )

        self.assertEqual([comment.author_kind for comment in snapshot.comments], ["agent", "customer"])

    def test_conflicting_author_role_and_is_agent_is_rejected(self) -> None:
        with self.assertRaisesRegex(ZendeskCommentSnapshotError, "author role conflicts"):
            normalize_snapshot(
                snapshot_payload(
                    {
                        "id": "conflict",
                        "public": True,
                        "author": {"role": "end-user", "is_agent": True},
                        "body": "Conflicting identity",
                        "created_at": "2026-08-16T01:20:00Z",
                    }
                )
            )

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
        comments = self.repository.get_account_case_comments(self.ticket_id)
        self.assertEqual([comment["is_agent"] for comment in comments], [False, True])

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
        self.assertNotIn("n8n_request_token", serialized)
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
        self.token_patcher = patch.dict(os.environ, {"n8n_request_token": "test-sync-token"}, clear=False)
        self.token_patcher.start()

    def tearDown(self) -> None:
        self.token_patcher.stop()
        main.ticket_repository = self.original_repository
        self.client.close()

    def test_membership_and_sync_require_token_and_accept_complete_snapshot(self) -> None:
        target = "/api/integrations/zendesk/account-cases/12620/comment-sync-target"
        self.assertEqual(self.client.get(target).status_code, 401)
        membership = self.client.get(target, headers={"X-N8n-Request-Token": "test-sync-token"})
        self.assertEqual(membership.status_code, 200, membership.text)
        self.assertTrue(membership.json()["is_account_case"])

        response = self.client.put(
            "/api/integrations/zendesk/account-cases/12620/comments",
            headers={"X-N8n-Request-Token": "test-sync-token"},
            json=snapshot_payload(
                {"id": "100", "public": True, "body": "Public", "created_at": "2026-08-16T01:10:00Z"}
            ),
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "synced")
        self.assertEqual(response.json()["comment_count"], 1)
        self.assertEqual(response.json()["unresolved_author_count"], 1)

    def test_non_account_ticket_returns_false_without_querying_comments(self) -> None:
        self.repository.save_ticket({"ticket_id": "12621", "messages": []})
        response = self.client.get(
            "/api/integrations/zendesk/account-cases/12621/comment-sync-target",
            headers={"X-N8n-Request-Token": "test-sync-token"},
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


class ZendeskCommentTriggerTests(unittest.TestCase):
    """trigger_comment_id turns a projection sync into an automation trigger."""

    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.ticket_id = "12838"
        self.case_id = "AC-12838"
        self.repository.save_ticket(
            {
                "ticket_id": self.ticket_id,
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please enable Media Relay.",
                        "created_at": "2026-08-19T08:00:00Z",
                    }
                ],
                "created_at": "2026-08-19T08:00:00Z",
                "updated_at": "2026-08-19T08:00:00Z",
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": self.case_id,
                "billing_ticket_id": self.case_id,
                "client_ticket_id": self.ticket_id,
                "title": "Enablement request",
                "question": "Please enable Media Relay.",
                "processing_profile": "production",
                "zendesk_ticket_id": "12838",
                "route_family": "automated",
                "execution_action": "enablement",
                "automation_handler": "enablement",
                "route_status": "automated",
                "automation_status": "automation",
                "collected_fields": {},
                "missing_fields": ["app_id"],
                "created_at": "2026-08-19T08:00:30Z",
            }
        )
        self.original_repository = main.ticket_repository
        main.ticket_repository = self.repository
        self.client = TestClient(main.app)
        self.token_patcher = patch.dict(
            os.environ,
            {"n8n_request_token": "test-sync-token"},
            clear=False,
        )
        self.token_patcher.start()
        self.ownership_patcher = patch.object(
            main,
            "_apply_production_ownership_gate",
            return_value=True,
        )
        self.ownership_patcher.start()
        self.sync_url = "/api/integrations/zendesk/account-cases/12838/comments"
        self.headers = {"X-N8n-Request-Token": "test-sync-token"}

    def tearDown(self) -> None:
        self.ownership_patcher.stop()
        self.token_patcher.stop()
        main.ticket_repository = self.original_repository
        self.client.close()

    def _initial_comment(self):
        return {
            "id": "52661000",
            "public": True,
            "body": "Please enable Media Relay.",
            "created_at": "2026-08-19T07:00:00Z",
            "author": {"id": "31116634341396", "name": "Customer", "role": "end-user"},
        }

    def _customer_comment(self, **overrides):
        comment = {
            "id": "52661001",
            "public": True,
            "body": "My app id is 4b7634a0d0f1418b8135918292f6a507.",
            "created_at": "2026-08-19T09:00:00Z",
            "author": {"id": "31116634341396", "name": "Customer", "role": "end-user"},
        }
        comment.update(overrides)
        return comment

    def _sync(self, comments, trigger_comment_id=None):
        payload = snapshot_payload(*comments)
        if trigger_comment_id is not None:
            payload["trigger_comment_id"] = trigger_comment_id
        return self.client.put(self.sync_url, headers=self.headers, json=payload)

    def test_customer_public_comment_triggers_reply_flow_once(self) -> None:
        async def consume_reply(**kwargs):
            ticket = self.repository.get_ticket(self.ticket_id)
            assert ticket is not None
            ticket["messages"].append(
                {
                    "role": "customer",
                    "content": kwargs["message"],
                    "external_id": kwargs["message_source_id"],
                    "created_at": "2026-08-19T09:00:00Z",
                }
            )
            self.repository.save_ticket(ticket)
            return {"status": "processed"}

        with patch.object(
            main,
            "_process_account_customer_reply",
            new_callable=AsyncMock,
            side_effect=consume_reply,
        ) as process:
            response = self._sync([self._initial_comment(), self._customer_comment()], trigger_comment_id="52661001")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["trigger_status"], "processed")

        messages = self.repository.get_ticket(self.ticket_id)["messages"]
        ingested = [
            message
            for message in messages
            if message.get("external_id") == "52661001"
        ]
        self.assertEqual(len(ingested), 1)

        with patch.object(
            main,
            "_process_account_customer_reply",
            new_callable=AsyncMock,
            side_effect=consume_reply,
        ) as replay_process:
            replay = self._sync([self._initial_comment(), self._customer_comment()], trigger_comment_id="52661001")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["trigger_status"], "processed")
        messages_after = self.repository.get_ticket(self.ticket_id)["messages"]
        ingested_after = [
            message
            for message in messages_after
            if message.get("external_id") == "52661001"
        ]
        self.assertEqual(len(ingested_after), 1)
        process.assert_awaited_once()
        replay_process.assert_not_awaited()

    def test_failed_trigger_outcome_is_replayable_after_handler_repair(self) -> None:
        # 13001 scenario: a suspension case whose persisted handler was
        # rewritten to billing fails the reply flow. The failed outcome must
        # stay replayable so the same comment re-runs once the handler is
        # repaired, with side effects exactly once.
        self.repository.save_account_case(
            {
                "account_case_id": self.case_id,
                "billing_ticket_id": self.case_id,
                "client_ticket_id": self.ticket_id,
                "title": "Suspend my account",
                "question": "Please suspend the account.",
                "processing_profile": "production",
                "zendesk_ticket_id": "12838",
                "route_family": "automated",
                "execution_action": "account_suspension",
                "automation_handler": "billing",
                "route_status": "automated",
                "automation_status": "automation",
                "collected_fields": {},
                "missing_fields": [],
                "created_at": "2026-08-19T08:00:30Z",
            }
        )

        with patch.object(
            main,
            "_process_account_customer_reply",
            new_callable=AsyncMock,
            side_effect=HTTPException(
                status_code=409,
                detail="account case has no registered automation handler",
            ),
        ) as failed_process:
            failed = self._sync(
                [self._initial_comment(), self._customer_comment()],
                trigger_comment_id="52661001",
            )
        self.assertEqual(failed.status_code, 200, failed.text)
        self.assertEqual(failed.json()["trigger_status"], "failed")
        failed_process.assert_awaited_once()

        async def consume_reply(**kwargs):
            ticket = self.repository.get_ticket(self.ticket_id)
            assert ticket is not None
            ticket["messages"].append(
                {
                    "role": "customer",
                    "content": kwargs["message"],
                    "external_id": kwargs["message_source_id"],
                    "created_at": "2026-08-19T09:00:00Z",
                }
            )
            self.repository.save_ticket(ticket)
            return {"status": "processed"}

        repaired_case = self.repository.get_account_case(self.case_id)
        assert repaired_case is not None
        repaired_case["automation_handler"] = "account_suspension"
        self.repository.save_account_case(repaired_case)
        with patch.object(
            main,
            "_process_account_customer_reply",
            new_callable=AsyncMock,
            side_effect=consume_reply,
        ) as replay_process:
            replay = self._sync(
                [self._initial_comment(), self._customer_comment()],
                trigger_comment_id="52661001",
            )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["trigger_status"], "processed")
        replay_process.assert_awaited_once()
        messages = self.repository.get_ticket(self.ticket_id)["messages"]
        ingested = [
            message
            for message in messages
            if message.get("external_id") == "52661001"
        ]
        self.assertEqual(len(ingested), 1)

    def test_not_automated_case_still_consumes_customer_comments(self) -> None:
        # After an unexpected reply re-routes the case (e.g. onto the rag
        # route), later customer comments must keep entering the reply loop so
        # the RAG fallback can keep serving them until an escalation.
        self.repository.save_account_case(
            {
                "account_case_id": self.case_id,
                "billing_ticket_id": self.case_id,
                "client_ticket_id": self.ticket_id,
                "title": "Enablement request",
                "question": "Please enable Media Relay.",
                "processing_profile": "production",
                "zendesk_ticket_id": "12838",
                "route_family": "rag_product_support",
                "execution_action": "rag",
                "automation_status": "not_automated",
                "collected_fields": {},
                "missing_fields": [],
                "created_at": "2026-08-19T08:00:30Z",
            }
        )
        with patch.object(main, "_process_account_customer_reply", new_callable=AsyncMock) as process:
            process.return_value = {"status": "ok", "automation_status": "not_automated"}
            response = self._sync(
                [self._initial_comment(), self._customer_comment()],
                trigger_comment_id="52661001",
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["trigger_status"], "processed")
        process.assert_awaited_once()

    def test_active_non_automated_engineer_case_records_customer_comment_and_only_notifies_slack(self) -> None:
        account_case = self.repository.get_account_case(self.case_id)
        assert account_case is not None
        account_case.update(
            route_family="rag_product_support",
            execution_action="rag",
            automation_status="not_automated",
            route_status="not_automated",
        )
        self.repository.save_account_case(account_case)
        ticket = self.repository.get_ticket(self.ticket_id)
        assert ticket is not None
        engineer_case = build_new_engineer_case(
            ticket,
            engineer_case_id="12838-1",
            case_sequence=1,
            title="Enablement request",
            status="investigating",
            trigger_source="account_not_automated",
            trigger_reason="not_automated",
            now_value="2026-08-19T08:00:30Z",
        )
        engineer_case.update(
            thread_id="12838-1-round-1",
            draft_customer_reply="Old draft that must be invalidated.",
            final_confirmation_requested_at="2026-08-19T08:01:00Z",
            investigation_state="awaiting_final_approval",
            engineer_agent_state={
                "conversation_version": 3,
                "draft_version": 2,
                "round_number": 1,
                "round_state": "awaiting_final_approval",
                "ready_to_reply": True,
                "reply_readiness": {"ready_for_customer_reply": True},
                "guided_reply_generation": {"source": "slack"},
                "active_guardrail_final": {"decision": "passed"},
                "guardrail_final_id": "guardrail-1",
                "guardrail_final_version": 1,
                "guardrail_final_decision": "passed",
                "final_approval_required": True,
                "final_approved_at": "2026-08-19T08:02:00Z",
            },
        )
        self.repository.save_engineer_case(engineer_case)
        with patch.object(main, "_process_account_customer_reply", new_callable=AsyncMock) as account_reply, patch.object(
            main, "_process_engineer_investigation_message", new_callable=AsyncMock
        ) as engineer_ai:
            response = self._sync(
                [self._initial_comment(), self._customer_comment()],
                trigger_comment_id="52661001",
            )
            replay = self._sync(
                [self._initial_comment(), self._customer_comment()],
                trigger_comment_id="52661001",
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["trigger_status"], "processed_engineer_notification")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["status"], "unchanged")
        self.assertEqual(
            {
                key: replay.json()[key]
                for key in (
                    "trigger_status",
                    "trigger_comment_id",
                    "engineer_case_id",
                    "conversation_version",
                    "draft_version",
                )
            },
            {
                key: response.json()[key]
                for key in (
                    "trigger_status",
                    "trigger_comment_id",
                    "engineer_case_id",
                    "conversation_version",
                    "draft_version",
                )
            },
        )
        self.assertEqual(response.json()["conversation_version"], 4)
        self.assertEqual(response.json()["draft_version"], 3)
        account_reply.assert_not_awaited()
        engineer_ai.assert_not_awaited()
        stored_case = self.repository.get_engineer_case("12838-1")
        assert stored_case is not None
        customer_message = stored_case["active_investigation"]["messages"][-1]
        self.assertEqual(len(stored_case["active_investigation"]["messages"]), 1)
        self.assertEqual(customer_message["role"], "customer")
        self.assertEqual(customer_message["meta"]["zendesk_comment_id"], "52661001")
        self.assertEqual(stored_case["active_investigation"]["draft_customer_reply"], "")
        self.assertEqual(stored_case["active_investigation"]["state"], "active")
        state = stored_case["engineer_agent_state"]
        self.assertEqual(state["phase"], "investigating")
        for stale_key in (
            "ready_to_reply",
            "reply_readiness",
            "guided_reply_generation",
            "active_guardrail_final",
            "guardrail_final_id",
            "guardrail_final_version",
            "guardrail_final_decision",
            "final_approval_required",
            "final_approved_at",
        ):
            self.assertNotIn(stale_key, state)
        slack_events = self.repository.list_engineer_slack_events(statuses=("queued",))
        self.assertEqual(len(slack_events), 1)
        self.assertEqual(slack_events[0]["event_type"], "zendesk_customer_comment")
        self.assertEqual(slack_events[0]["payload"]["message_text"], "Cx has added a new comment")
        self.assertNotIn("app id", str(slack_events[0]["payload"]).lower())

    def test_zendesk_solved_status_closes_active_engineer_case_and_thread(self) -> None:
        ticket = self.repository.get_ticket(self.ticket_id)
        assert ticket is not None
        engineer_case = build_new_engineer_case(
            ticket,
            engineer_case_id="12838-close-1",
            case_sequence=1,
            title="Enablement request",
            status="investigating",
            trigger_source="account_not_automated",
            trigger_reason="not_automated",
            now_value="2026-08-19T08:00:30Z",
        )
        engineer_case["thread_id"] = "12838-close-1-round-1"
        self.repository.save_engineer_case(engineer_case)

        response = self.client.put(
            "/api/integrations/zendesk/account-cases/12838/status",
            headers=self.headers,
            json={"zendesk_status": "solved", "updated_at": "2026-08-19T10:00:00Z"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["engineer_case_closed"])
        stored_case = self.repository.get_engineer_case("12838-close-1")
        assert stored_case is not None
        self.assertEqual(stored_case["status"], "resolved")
        self.assertEqual(stored_case["assignment_status"], "resolved")
        self.assertIsNone(stored_case["active_investigation"])
        slack_events = self.repository.list_engineer_slack_events(statuses=("queued",))
        self.assertEqual(slack_events[-1]["event_type"], "engineer_case_closed")

    def test_agent_comment_is_ignored_without_trigger(self) -> None:
        comment = self._customer_comment(
            body="Internal update from the AI agent.",
            author={"id": "48557297720084", "name": "AI Support Agent", "role": "agent"},
        )
        response = self._sync([comment], trigger_comment_id="52661001")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["trigger_status"], "ignored_agent_comment")
        messages = self.repository.get_ticket(self.ticket_id)["messages"]
        self.assertFalse(
            any(message.get("external_id") == "52661001" for message in messages)
        )

    def test_private_customer_comment_is_ignored(self) -> None:
        response = self._sync(
            [self._initial_comment(), self._customer_comment(public=False)],
            trigger_comment_id="52661001",
        )
        self.assertEqual(response.json()["trigger_status"], "ignored_private_comment")

    def test_initial_comment_is_ignored(self) -> None:
        response = self._sync(
            [self._customer_comment(is_initial=True)],
            trigger_comment_id="52661001",
        )
        self.assertEqual(response.json()["trigger_status"], "ignored_initial_comment")

    def test_pre_intake_comment_is_ignored(self) -> None:
        response = self._sync(
            [self._initial_comment(), self._customer_comment(created_at="2026-08-19T07:59:00Z")],
            trigger_comment_id="52661001",
        )
        self.assertEqual(response.json()["trigger_status"], "ignored_pre_intake_comment")

    def test_non_production_case_is_ignored(self) -> None:
        case = self.repository.get_account_case(self.case_id)
        case["processing_profile"] = "staging"
        self.repository.save_account_case(case)
        response = self._sync([self._initial_comment(), self._customer_comment()], trigger_comment_id="52661001")
        self.assertEqual(response.json()["trigger_status"], "ignored_non_production_case")

    def test_missing_trigger_comment_id_is_projection_only(self) -> None:
        response = self._sync([self._customer_comment()])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["trigger_status"], "ignored_no_trigger")
        messages = self.repository.get_ticket(self.ticket_id)["messages"]
        self.assertFalse(
            any(message.get("external_id") == "52661001" for message in messages)
        )

    def test_unknown_trigger_comment_id_is_rejected(self) -> None:
        response = self._sync([self._customer_comment()], trigger_comment_id="99999")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "trigger_comment_missing")
