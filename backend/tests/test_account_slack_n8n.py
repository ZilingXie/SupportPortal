from __future__ import annotations

import json
import os
import socket
import unittest
from unittest.mock import Mock, patch

from backend import main, worker
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_slack_n8n import (
    AccountSlackN8nError,
    account_slack_n8n_configured,
    build_account_slack_event,
    get_account_slack_event_status,
    post_account_slack_event,
)


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class AccountSlackN8nContractTests(unittest.TestCase):
    def test_configuration_requires_absolute_http_urls(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ACCOUNT_SLACK_N8N_WEBHOOK_URL": "not-a-url",
                "ACCOUNT_SLACK_N8N_STATUS_URL": "https://n8n.invalid/status",
                "ACCOUNT_SLACK_N8N_TOKEN": "token",
            },
            clear=False,
        ):
            self.assertFalse(account_slack_n8n_configured())

    def test_fraud_message_uses_exact_template_and_allowlist(self) -> None:
        event = build_account_slack_event(
            account_case={
                "account_case_id": "AC-1",
                "zendesk_ticket_id": "12838",
                "title": "  Suspicious   activity  ",
                "question": " Please   review this account. ",
                "execution_action": "fraud_account",
                "customer_name": "Must not leak",
                "collected_fields": {"payment": "Must not leak"},
            },
            message_id="42",
            reply_intent="fraud_handoff_confirmation",
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(
            event["message_text"],
            "[Fraud Account] Suspicious activity\n"
            "zendesk: https://agoraio.zendesk.com/agent/tickets/12838\n"
            "Please review this account.",
        )
        self.assertEqual(
            set(event),
            {
                "schema_version", "event_id", "event_type", "account_case_id",
                "message_id", "reply_intent", "case_type", "case_title",
                "zendesk_ticket_id", "zendesk_url", "ticket_summary", "message_text",
            },
        )

    def test_suspension_contact_confirmation_does_not_create_event(self) -> None:
        event = build_account_slack_event(
            account_case={
                "account_case_id": "AC-2",
                "execution_action": "account_suspension",
            },
            message_id="43",
            reply_intent="account_suspension_contact_confirmation_request",
        )
        self.assertIsNone(event)

    def test_reply_intent_must_match_case_action(self) -> None:
        event = build_account_slack_event(
            account_case={
                "account_case_id": "AC-MISMATCH",
                "zendesk_ticket_id": "12839",
                "title": "Enable feature",
                "question": "Please enable the feature.",
                "execution_action": "enablement",
            },
            message_id="43-mismatch",
            reply_intent="fraud_handoff_confirmation",
        )
        self.assertIsNone(event)

    def test_empty_question_falls_back_to_title(self) -> None:
        event = build_account_slack_event(
            account_case={
                "account_case_id": "AC-3",
                "zendesk_ticket_id": "99",
                "title": "Suspended account",
                "question": "   ",
                "execution_action": "account_suspension",
            },
            message_id="44",
            reply_intent="account_suspension_handoff_and_close",
        )
        assert event is not None
        self.assertEqual(event["ticket_summary"], "Suspended account")
        self.assertTrue(event["message_text"].startswith("[Account Suspension] Suspended account\n"))

    def test_post_timeout_is_outcome_unknown_and_does_not_retry(self) -> None:
        event = build_account_slack_event(
            account_case={
                "account_case_id": "AC-4",
                "zendesk_ticket_id": "100",
                "title": "Fraud",
                "question": "Review",
                "execution_action": "fraud_account",
            },
            message_id="45",
            reply_intent="fraud_handoff_confirmation",
        )
        assert event is not None
        env = {
            "ACCOUNT_SLACK_N8N_WEBHOOK_URL": "https://n8n.example.invalid/delivery",
            "ACCOUNT_SLACK_N8N_TOKEN": "secret-token",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "backend.services.account_slack_n8n.urllib.request.urlopen",
            side_effect=socket.timeout("timed out"),
        ) as urlopen:
            with self.assertRaises(AccountSlackN8nError) as raised:
                post_account_slack_event(event)
        self.assertTrue(raised.exception.outcome_unknown)
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(urlopen.call_args.args[0].get_header("X-supportportal-token"), "secret-token")

    def test_post_rejects_mismatched_event_id_as_outcome_unknown(self) -> None:
        event = build_account_slack_event(
            account_case={
                "account_case_id": "AC-5",
                "zendesk_ticket_id": "101",
                "title": "Fraud",
                "question": "Review",
                "execution_action": "fraud_account",
            },
            message_id="46",
            reply_intent="fraud_handoff_confirmation",
        )
        assert event is not None
        with patch.dict(
            os.environ,
            {"ACCOUNT_SLACK_N8N_WEBHOOK_URL": "https://n8n.invalid", "ACCOUNT_SLACK_N8N_TOKEN": "x"},
            clear=False,
        ), patch(
            "backend.services.account_slack_n8n.urllib.request.urlopen",
            return_value=_Response({"event_id": "other", "status": "delivered"}),
        ):
            with self.assertRaises(AccountSlackN8nError) as raised:
                post_account_slack_event(event)
        self.assertTrue(raised.exception.outcome_unknown)

    def test_status_request_is_read_only_and_accepts_missing(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ACCOUNT_SLACK_N8N_STATUS_URL": "https://n8n.invalid/status",
                "ACCOUNT_SLACK_N8N_TOKEN": "status-token",
            },
            clear=False,
        ), patch(
            "backend.services.account_slack_n8n.urllib.request.urlopen",
            return_value=_Response({"event_id": "event:1", "status": "missing"}),
        ) as urlopen:
            result = get_account_slack_event_status("event:1")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, "https://n8n.invalid/status?event_id=event%3A1")
        self.assertEqual(result, {"event_id": "event:1", "status": "missing", "failure_code": None})

    def test_health_warning_is_production_only(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ACCOUNT_DEFAULT_PROCESSING_PROFILE": "production",
                "ACCOUNT_SLACK_N8N_WEBHOOK_URL": "",
                "ACCOUNT_SLACK_N8N_STATUS_URL": "",
                "ACCOUNT_SLACK_N8N_TOKEN": "",
            },
            clear=False,
        ):
            self.assertIn("account_slack_n8n_config_incomplete", main._health_config_warnings())
        with patch.dict(
            os.environ,
            {
                "ACCOUNT_DEFAULT_PROCESSING_PROFILE": "staging",
                "ACCOUNT_SLACK_N8N_WEBHOOK_URL": "",
                "ACCOUNT_SLACK_N8N_STATUS_URL": "",
                "ACCOUNT_SLACK_N8N_TOKEN": "",
            },
            clear=False,
        ):
            self.assertNotIn("account_slack_n8n_config_incomplete", main._health_config_warnings())


class AccountSlackWorkerTests(unittest.TestCase):
    def _configured(self):
        return patch.dict(
            os.environ, {"ACCOUNT_DEFAULT_PROCESSING_PROFILE": "production"}, clear=False
        )

    def test_outcome_unknown_only_queries_status(self) -> None:
        repository = Mock()
        repository.list_account_slack_deliveries.return_value = [
            {"event_id": "event-1", "status": "outcome_unknown"}
        ]
        with self._configured(), patch.object(
            worker, "account_slack_n8n_configured", return_value=True
        ), patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "get_account_slack_event_status", return_value={"event_id": "event-1", "status": "pending"}
        ) as status, patch.object(worker, "post_account_slack_event") as post:
            worker._drain_account_slack_deliveries(limit=20)
        status.assert_called_once_with("event-1")
        post.assert_not_called()
        repository.requeue_account_slack_delivery.assert_not_called()
        repository.complete_account_slack_delivery.assert_called_once()

    def test_queued_post_timeout_becomes_outcome_unknown_without_status_call(self) -> None:
        repository = Mock()
        repository.list_account_slack_deliveries.return_value = [
            {"event_id": "event-timeout", "status": "queued"}
        ]
        repository.claim_account_slack_delivery.return_value = {
            "event_id": "event-timeout",
            "status": "pending",
            "claimed": True,
            "payload": {"event_id": "event-timeout"},
        }
        with self._configured(), patch.object(
            worker, "account_slack_n8n_configured", return_value=True
        ), patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "post_account_slack_event",
            side_effect=AccountSlackN8nError("account_slack_n8n_request_failed", True),
        ) as post, patch.object(worker, "get_account_slack_event_status") as status:
            worker._drain_account_slack_deliveries(limit=20)
        post.assert_called_once_with({"event_id": "event-timeout"})
        status.assert_not_called()
        repository.complete_account_slack_delivery.assert_called_once_with(
            event_id="event-timeout",
            status="outcome_unknown",
            failure_code="account_slack_n8n_request_failed",
            completed_at=unittest.mock.ANY,
        )

    def test_staging_worker_does_not_read_slack_outbox(self) -> None:
        repository = Mock()
        with patch.dict(
            os.environ, {"ACCOUNT_DEFAULT_PROCESSING_PROFILE": "staging"}, clear=False
        ), patch.object(worker, "ticket_repository", repository):
            worker._drain_account_slack_deliveries(limit=20)
        repository.list_account_slack_deliveries.assert_not_called()

    def test_missing_status_is_the_only_requeue_path(self) -> None:
        repository = Mock()
        repository.list_account_slack_deliveries.return_value = [
            {"event_id": "event-missing", "status": "pending"}
        ]
        with self._configured(), patch.object(
            worker, "account_slack_n8n_configured", return_value=True
        ), patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "get_account_slack_event_status",
            return_value={"event_id": "event-missing", "status": "missing"},
        ), patch.object(worker, "post_account_slack_event") as post:
            worker._drain_account_slack_deliveries(limit=20)
        post.assert_not_called()
        repository.requeue_account_slack_delivery.assert_called_once()
        repository.complete_account_slack_delivery.assert_not_called()

    def test_concurrent_replay_that_loses_claim_does_not_post(self) -> None:
        repository = Mock()
        repository.list_account_slack_deliveries.return_value = [
            {"event_id": "event-once", "status": "queued"}
        ]
        repository.claim_account_slack_delivery.return_value = {
            "event_id": "event-once", "status": "pending", "claimed": False
        }
        with self._configured(), patch.object(
            worker, "account_slack_n8n_configured", return_value=True
        ), patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "post_account_slack_event"
        ) as post:
            worker._drain_account_slack_deliveries(limit=20)
        post.assert_not_called()


class AccountSlackRepositoryTests(unittest.TestCase):
    def _repository_with_publishable_case(
        self, *, intent: str, handler: str, close_after_publish: bool = False
    ) -> tuple[InMemoryTicketRepository, dict[str, object]]:
        repository = InMemoryTicketRepository()
        now = "2026-08-20T00:00:00+00:00"
        repository.save_ticket(
            {
                "ticket_id": "PRD-SLACK-1", "customer_id": "customer-1",
                "requester": "Customer", "subject": "Account issue", "status": "open",
                "created_at": now, "updated_at": now,
                "messages": [{"role": "customer", "content": "Original question", "created_at": now}],
            }
        )
        repository.save_account_case(
            {
                "account_case_id": "AC-SLACK-1", "billing_ticket_id": "AC-SLACK-1",
                "client_ticket_id": "PRD-SLACK-1", "processing_profile": "production",
                "zendesk_ticket_id": "12838", "source": "zendesk", "title": "Account issue",
                "question": "Original question", "route_family": "automated", "route": handler,
                "execution_action": handler, "automation_handler": handler,
                "route_status": "automated", "automation_status": "automation",
                "created_at": now, "updated_at": now,
            }
        )
        job = repository.save_account_reply_job(
            {
                "job_id": "reply-slack-1", "ticket_id": "PRD-SLACK-1",
                "trigger_message_created_at": now, "status": "persona_publishing",
                "scheduled_for": now, "claimed_at": now, "attempt_count": 1,
                "payload": {"reply_intent": intent, "close_after_publish": close_after_publish},
                "created_at": now, "updated_at": now,
            }
        )
        return repository, job

    def _publish(self, repository, job, *, intent: str, close_after_publish: bool = False):
        return repository.publish_account_reply(
            job,
            content="The relevant team will contact you within 24 hours.",
            payload={"reply_intent": intent, "close_after_publish": close_after_publish},
            published_at="2026-08-20T00:01:00+00:00",
            reply_execution={"execution_id": "execution-slack-1", "ticket_id": "PRD-SLACK-1"},
        )

    def test_publish_creates_one_waiting_event_and_public_delivery_releases_it(self) -> None:
        intent = "fraud_handoff_confirmation"
        repository, job = self._repository_with_publishable_case(intent=intent, handler="fraud_account")
        published = self._publish(repository, job, intent=intent)
        waiting = repository.list_account_slack_deliveries(statuses=("waiting_zendesk",), limit=10)
        self.assertEqual(len(waiting), 1)
        self.assertEqual(
            waiting[0]["event_id"],
            f"account-automation-slack:AC-SLACK-1:{published['message_id']}",
        )
        repository.begin_idempotent_request(
            "account_zendesk_internal_comment",
            f"AC-SLACK-1:{published['message_id']}",
            created_at="2026-08-20T00:02:00+00:00",
        )
        repository.record_account_zendesk_internal_comment_result(
            account_case_id="AC-SLACK-1", ticket_id="PRD-SLACK-1",
            message_id=published["message_id"],
            idempotency_key=f"AC-SLACK-1:{published['message_id']}",
            result_payload={"status": "added", "comment_id": "comment-1"},
            recorded_at="2026-08-20T00:03:00+00:00",
        )
        self.assertEqual(len(repository.list_account_slack_deliveries(statuses=("queued",), limit=10)), 1)

    def test_contact_confirmation_does_not_create_slack_event(self) -> None:
        intent = "account_suspension_contact_confirmation_request"
        repository, job = self._repository_with_publishable_case(
            intent=intent, handler="account_suspension"
        )
        self._publish(repository, job, intent=intent)
        self.assertEqual(repository.list_account_slack_deliveries(
            statuses=("waiting_zendesk", "queued", "pending"), limit=10
        ), [])

    def test_private_or_unconfirmed_zendesk_result_does_not_release_event(self) -> None:
        intent = "fraud_handoff_confirmation"
        for result_status, is_public in (("added", False), ("failed", True), ("outcome_unknown", True)):
            with self.subTest(result_status=result_status, is_public=is_public):
                repository, job = self._repository_with_publishable_case(
                    intent=intent, handler="fraud_account"
                )
                published = self._publish(repository, job, intent=intent)
                delivery_key = ("AC-SLACK-1", published["message_id"])
                repository._account_zendesk_comment_deliveries[delivery_key]["is_public"] = is_public
                key = f"AC-SLACK-1:{published['message_id']}"
                repository.begin_idempotent_request(
                    "account_zendesk_internal_comment", key,
                    created_at="2026-08-20T00:02:00+00:00",
                )
                repository.record_account_zendesk_internal_comment_result(
                    account_case_id="AC-SLACK-1", ticket_id="PRD-SLACK-1",
                    message_id=published["message_id"], idempotency_key=key,
                    result_payload={"status": result_status, "error_code": "test_failure"},
                    recorded_at="2026-08-20T00:03:00+00:00",
                )
                self.assertEqual(len(repository.list_account_slack_deliveries(
                    statuses=("waiting_zendesk",), limit=10
                )), 1)

    def test_slack_failure_does_not_undo_zendesk_delivery_or_local_close(self) -> None:
        intent = "account_suspension_handoff_and_close"
        repository, job = self._repository_with_publishable_case(
            intent=intent, handler="account_suspension", close_after_publish=True
        )
        published = self._publish(repository, job, intent=intent, close_after_publish=True)
        key = f"AC-SLACK-1:{published['message_id']}"
        repository.begin_idempotent_request(
            "account_zendesk_internal_comment", key, created_at="2026-08-20T00:02:00+00:00"
        )
        repository.record_account_zendesk_internal_comment_result(
            account_case_id="AC-SLACK-1", ticket_id="PRD-SLACK-1",
            message_id=published["message_id"], idempotency_key=key,
            result_payload={"status": "added", "comment_id": "comment-close"},
            recorded_at="2026-08-20T00:03:00+00:00", close_local_ticket=True,
        )
        event = repository.list_account_slack_deliveries(statuses=("queued",), limit=10)[0]
        repository.complete_account_slack_delivery(
            event_id=event["event_id"], status="failed", failure_code="slack_rejected",
            completed_at="2026-08-20T00:04:00+00:00",
        )
        ticket = repository.get_ticket("PRD-SLACK-1")
        self.assertEqual(ticket["status"], "resolved")
        self.assertEqual(len(repository.list_account_zendesk_comment_deliveries(
            statuses=("delivered",), limit=10
        )), 1)


if __name__ == "__main__":
    unittest.main()
