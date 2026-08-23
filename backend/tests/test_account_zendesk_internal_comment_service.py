from __future__ import annotations

import unittest
from unittest.mock import patch

from backend import main as backend_main
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_zendesk_internal_comment import (
    AccountZendeskInternalCommentError,
    deliver_account_ai_message_as_internal_comment,
    reconcile_account_ai_message_internal_comment,
)
from backend.services.zendesk_comments import ZendeskCommentResult


class AccountZendeskInternalCommentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        now = "2026-08-19T00:00:00+00:00"
        self.repository.save_ticket(
            {
                "ticket_id": "PRD-SERVICE-1",
                "customer_id": "customer-service-1",
                "requester": "Customer",
                "subject": "Shared service test",
                "status": "open",
                "created_at": now,
                "updated_at": now,
                "messages": [
                    {
                        "role": "customer",
                        "content": "Original question",
                        "created_at": now,
                    },
                    {
                        "role": "assistant",
                        "content": "The persisted Production answer.",
                        "created_at": now,
                        "meta": {"source": "account_ai", "visibility": "account_only"},
                    },
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-SERVICE-1",
                "billing_ticket_id": "AC-SERVICE-1",
                "client_ticket_id": "PRD-SERVICE-1",
                "zendesk_ticket_id": "12838",
                "source": "https://agoraio.zendesk.com/agent/tickets/12838",
                "processing_profile": "production",
                "route_family": "automated",
                "route": "enablement",
                "execution_action": "enablement",
                "route_status": "automated",
                "automation_status": "automation",
                "created_at": now,
                "updated_at": now,
            }
        )
        detail = self.repository.get_account_case_details(["AC-SERVICE-1"])["AC-SERVICE-1"]
        self.message_id = next(
            item["message_id"]
            for item in detail["ticket"]["messages"]
            if item.get("role") == "assistant"
        )

    def _create_delivery(self) -> None:
        self.repository.create_account_zendesk_comment_delivery(
            account_case_id="AC-SERVICE-1",
            message_id=self.message_id,
            zendesk_ticket_id="12838",
            idempotency_key=f"production-zendesk-comment:AC-SERVICE-1:{self.message_id}",
            created_at="2026-08-19T00:00:00+00:00",
        )

    def test_worker_and_account_admin_share_one_api_write_and_persisted_result(self) -> None:
        self._create_delivery()
        with patch(
            "backend.services.account_zendesk_internal_comment.add_ticket_comment",
            return_value=ZendeskCommentResult(comment_id="comment-shared", status_code=200),
        ) as add_comment:
            first = deliver_account_ai_message_as_internal_comment(
                repository=self.repository,
                account_case_id="AC-SERVICE-1",
                message_id=self.message_id,
                actor_id="system:production-account-reply",
                trigger="production_worker",
            )
            replay = deliver_account_ai_message_as_internal_comment(
                repository=self.repository,
                account_case_id="AC-SERVICE-1",
                message_id=self.message_id,
                actor_id="account-admin-1",
                trigger="account_admin",
                retry_failed=True,
            )

        self.assertEqual(first.status, "added")
        self.assertEqual(first.comment_id, "comment-shared")
        self.assertEqual(replay.status, "added")
        self.assertTrue(replay.idempotent_replay)
        add_comment.assert_called_once_with(
            ticket_id="12838",
            body="The persisted Production answer.",
            public=False,
            solve=False,
        )

        delivery = self.repository.list_account_zendesk_comment_deliveries(
            statuses=("delivered",),
            limit=10,
        )
        self.assertEqual(len(delivery), 1)
        self.assertEqual(delivery[0]["zendesk_comment_id"], "comment-shared")
        saved = self.repository.get_account_case_details(["AC-SERVICE-1"])["AC-SERVICE-1"]
        saved_message = next(
            item for item in saved["ticket"]["messages"] if item.get("message_id") == self.message_id
        )
        comment_state = saved_message["meta"]["zendesk_internal_comment"]
        self.assertEqual(comment_state["status"], "added")
        self.assertEqual(comment_state["trigger"], "production_worker")

    def test_reconciliation_updates_all_states_without_put(self) -> None:
        self._create_delivery()
        self.repository.claim_account_zendesk_comment_delivery(
            account_case_id="AC-SERVICE-1",
            message_id=self.message_id,
            claimed_at="2026-08-19T00:01:00+00:00",
        )
        with patch(
            "backend.services.account_zendesk_internal_comment.read_ticket_comment_audit",
            return_value=(ZendeskCommentResult(comment_id="comment-audited", status_code=200), False),
        ) as find_comment:
            result = reconcile_account_ai_message_internal_comment(
                repository=self.repository,
                account_case_id="AC-SERVICE-1",
                message_id=self.message_id,
                actor_id="system:production-account-reply",
                trigger="production_worker",
            )

        self.assertEqual(result.status, "added")
        find_comment.assert_called_once_with(
            ticket_id="12838",
            body="The persisted Production answer.",
            public=False,
        )
        delivery = self.repository.list_account_zendesk_comment_deliveries(
            statuses=("delivered",),
            limit=10,
        )
        self.assertEqual(delivery[0]["zendesk_comment_id"], "comment-audited")

    def test_outcome_unknown_blocks_a_later_put_until_audit_finds_comment(self) -> None:
        self._create_delivery()
        self.repository.claim_account_zendesk_comment_delivery(
            account_case_id="AC-SERVICE-1",
            message_id=self.message_id,
            claimed_at="2026-08-19T00:01:00+00:00",
        )
        with patch(
            "backend.services.account_zendesk_internal_comment.read_ticket_comment_audit",
            return_value=(None, False),
        ), patch(
            "backend.services.account_zendesk_internal_comment.add_ticket_comment"
        ) as add_comment:
            unknown = reconcile_account_ai_message_internal_comment(
                repository=self.repository,
                account_case_id="AC-SERVICE-1",
                message_id=self.message_id,
                actor_id="system:production-account-reply",
                trigger="production_worker",
            )
            replay = deliver_account_ai_message_as_internal_comment(
                repository=self.repository,
                account_case_id="AC-SERVICE-1",
                message_id=self.message_id,
                actor_id="account-admin-1",
                trigger="account_admin",
                retry_failed=True,
            )

        self.assertEqual(unknown.status, "outcome_unknown")
        self.assertEqual(replay.status, "outcome_unknown")
        add_comment.assert_not_called()

    def test_existing_failed_result_reconciles_local_state_without_put(self) -> None:
        self._create_delivery()
        idempotency_key = f"AC-SERVICE-1:{self.message_id}"
        self.repository.begin_idempotent_request(
            "account_zendesk_internal_comment",
            idempotency_key,
            created_at="2026-08-19T00:01:00+00:00",
        )
        self.repository.fail_idempotent_request(
            "account_zendesk_internal_comment",
            idempotency_key,
            response_payload={
                "status": "failed",
                "account_case_id": "AC-SERVICE-1",
                "message_id": self.message_id,
                "actor_id": "system:production-account-reply",
                "trigger": "production_worker",
                "retryable": True,
                "error_code": "zendesk_http_error",
            },
            updated_at="2026-08-19T00:01:01+00:00",
        )

        with patch(
            "backend.services.account_zendesk_internal_comment.add_ticket_comment"
        ) as add_comment:
            result = deliver_account_ai_message_as_internal_comment(
                repository=self.repository,
                account_case_id="AC-SERVICE-1",
                message_id=self.message_id,
                actor_id="system:production-account-reply",
                trigger="production_worker",
            )

        self.assertEqual(result.status, "failed")
        add_comment.assert_not_called()
        delivery = self.repository.list_account_zendesk_comment_deliveries(
            statuses=("failed",),
            limit=10,
        )
        self.assertEqual(len(delivery), 1)
        self.assertEqual(delivery[0]["failure_code"], "zendesk_http_error")

    def test_delivered_delivery_cannot_be_downgraded_by_stale_completion(self) -> None:
        self._create_delivery()
        self.repository.complete_account_zendesk_comment_delivery(
            account_case_id="AC-SERVICE-1",
            message_id=self.message_id,
            status="delivered",
            zendesk_comment_id="comment-final",
            failure_code=None,
            completed_at="2026-08-19T00:02:00+00:00",
        )
        delivery = self.repository.complete_account_zendesk_comment_delivery(
            account_case_id="AC-SERVICE-1",
            message_id=self.message_id,
            status="failed",
            zendesk_comment_id=None,
            failure_code="stale-worker",
            completed_at="2026-08-19T00:03:00+00:00",
        )

        self.assertIsNotNone(delivery)
        self.assertEqual(delivery["status"], "delivered")
        self.assertEqual(delivery["zendesk_comment_id"], "comment-final")

    def test_production_publication_persists_queued_delivery_when_email_is_not_ready(self) -> None:
        repository = InMemoryTicketRepository()
        repository.initialize()
        now = "2026-08-19T00:10:00+00:00"
        ticket_id = "PRD-PUBLISH-1"
        account_case_id = "AC-PUBLISH-1"
        repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "customer_id": "customer-publish-1",
                "requester": "Customer",
                "subject": "Enablement request",
                "status": "open",
                "created_at": now,
                "updated_at": now,
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please enable the feature.",
                        "created_at": now,
                    }
                ],
            }
        )
        repository.save_account_case(
            {
                "account_case_id": account_case_id,
                "billing_ticket_id": account_case_id,
                "client_ticket_id": ticket_id,
                "zendesk_ticket_id": "12838",
                "source": "https://agoraio.zendesk.com/agent/tickets/12838",
                "processing_profile": "production",
                "route_family": "automated",
                "route": "enablement",
                "execution_action": "enablement",
                "route_status": "automated",
                "automation_status": "automation",
                "internal_email_send_status": "not_ready",
                "missing_fields": ["app_id"],
                "created_at": now,
                "updated_at": now,
            }
        )
        job = repository.save_account_reply_job(
            {
                "job_id": "reply-publish-1",
                "ticket_id": ticket_id,
                "trigger_message_created_at": now,
                "status": "persona_publishing",
                "scheduled_for": now,
                "payload": {
                    "generated_content": "The feature is now enabled.",
                    "persona_key": "default-support",
                    "persona_version": 1,
                },
                "attempt_count": 1,
                "claimed_at": now,
                "created_at": now,
                "updated_at": now,
            }
        )

        published = repository.publish_account_reply(
            job,
            content="The feature is now enabled.",
            payload=dict(job["payload"]),
            published_at="2026-08-19T00:10:01+00:00",
            reply_execution={
                "execution_id": "reply-reply-publish-1",
                "ticket_id": ticket_id,
                "reply_kind": "enablement",
            },
        )

        self.assertEqual(published["delivery"]["status"], "queued")
        self.assertEqual(published["delivery"]["account_case_id"], account_case_id)
        self.assertEqual(published["delivery"]["zendesk_ticket_id"], "12838")
        self.assertEqual(
            repository.list_account_zendesk_comment_deliveries(
                statuses=("queued",),
                limit=10,
            )[0]["message_id"],
            published["message_id"],
        )

    def test_delivery_uploads_message_attachments_before_the_public_comment(self) -> None:
        self._create_delivery()
        attachment = {
            "asset_id": "ASSET-PDF0000000000000000000",
            "original_filename": "invoice-approval.pdf",
            "content_type": "application/pdf",
        }
        self.repository.update_ticket_message_meta(
            ticket_id="PRD-SERVICE-1",
            message_id=self.message_id,
            meta_updates={"attachments": [attachment]},
        )
        asset_repository = unittest.mock.Mock()
        asset_repository.get_asset.return_value = {
            "asset_id": attachment["asset_id"],
            "s3_key": "supportportal/assets/invoice-approval.pdf",
            "bucket": "supportportal-assets",
            "content_type": "application/pdf",
        }
        asset_storage = unittest.mock.Mock()
        asset_storage.fetch_bytes.return_value = b"%PDF-1.4\nfake invoice\n%%EOF"
        with patch.object(
            backend_main, "asset_repository", asset_repository
        ), patch.object(
            backend_main, "asset_storage", asset_storage
        ), patch(
            "backend.services.account_zendesk_internal_comment.upload_ticket_attachment",
            return_value="upload-token-1",
        ) as upload, patch(
            "backend.services.account_zendesk_internal_comment.add_ticket_comment",
            return_value=ZendeskCommentResult(comment_id="comment-attached", status_code=200),
        ) as add_comment:
            result = deliver_account_ai_message_as_internal_comment(
                repository=self.repository,
                account_case_id="AC-SERVICE-1",
                message_id=self.message_id,
                actor_id="system:production-account-reply",
                trigger="production_worker",
                public_comment=True,
                solve_ticket=True,
            )

        self.assertEqual(result.status, "added")
        upload.assert_called_once_with(
            filename="invoice-approval.pdf",
            content_type="application/pdf",
            data=b"%PDF-1.4\nfake invoice\n%%EOF",
        )
        add_comment.assert_called_once_with(
            ticket_id="12838",
            body="The persisted Production answer.",
            public=True,
            solve=True,
            uploads=("upload-token-1",),
        )

    def test_delivery_fails_permanently_when_attachment_asset_is_missing(self) -> None:
        self._create_delivery()
        attachment = {
            "asset_id": "ASSET-MISSING000000000000000",
            "original_filename": "invoice-approval.pdf",
            "content_type": "application/pdf",
        }
        self.repository.update_ticket_message_meta(
            ticket_id="PRD-SERVICE-1",
            message_id=self.message_id,
            meta_updates={"attachments": [attachment]},
        )
        asset_repository = unittest.mock.Mock()
        asset_repository.get_asset.return_value = None
        with patch.object(backend_main, "asset_repository", asset_repository), patch(
            "backend.services.account_zendesk_internal_comment.upload_ticket_attachment"
        ) as upload, patch(
            "backend.services.account_zendesk_internal_comment.add_ticket_comment"
        ) as add_comment:
            with self.assertRaises(AccountZendeskInternalCommentError) as raised:
                deliver_account_ai_message_as_internal_comment(
                    repository=self.repository,
                    account_case_id="AC-SERVICE-1",
                    message_id=self.message_id,
                    actor_id="system:production-account-reply",
                    trigger="production_worker",
                    public_comment=True,
                    solve_ticket=True,
                )

        self.assertEqual(raised.exception.code, "account_zendesk_comment_attachment_missing")
        self.assertFalse(raised.exception.outcome_unknown)
        upload.assert_not_called()
        add_comment.assert_not_called()


if __name__ == "__main__":
    unittest.main()
