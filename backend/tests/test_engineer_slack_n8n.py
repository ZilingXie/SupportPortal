from __future__ import annotations

import json
import os
import socket
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from backend import main, worker
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.engineer_cases import build_new_engineer_case
from backend.services.engineer_slack_n8n import (
    EngineerSlackN8nError,
    build_engineer_case_opened_event,
    build_engineer_case_thread_event,
    engineer_slack_n8n_configured,
    get_engineer_slack_event_status,
    post_engineer_slack_event,
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


class EngineerSlackN8nContractTests(unittest.TestCase):
    def test_configuration_requires_post_status_and_token(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ENGINEER_SLACK_N8N_WEBHOOK_URL": "https://n8n.invalid/post",
                "ENGINEER_SLACK_N8N_STATUS_URL": "https://n8n.invalid/status",
                "n8n_request_token": "token",
            },
            clear=False,
        ):
            self.assertTrue(engineer_slack_n8n_configured())

    def test_root_event_uses_allowlist_and_excludes_customer_identity(self) -> None:
        event = build_engineer_case_opened_event(
            account_case={
                "account_case_id": "AC-10",
                "zendesk_ticket_id": "12874",
                "title": "  Token callback   missing ",
                "question": " Callback never fires. ",
                "not_automated_reason": "no registered automation",
                "customer_email": "must-not-leak@example.com",
                "customer_name": "Must Not Leak",
            },
            engineer_case={"engineer_case_id": "12874-1"},
        )
        self.assertEqual(
            set(event),
            {
                "schema_version",
                "event_id",
                "event_type",
                "engineer_case_id",
                "account_case_id",
                "case_title",
                "problem",
                "route_reason",
                "zendesk_ticket_id",
                "zendesk_url",
                "message_text",
            },
        )
        serialized = json.dumps(event)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("channel", serialized.lower())
        self.assertNotIn("team", serialized.lower())
        self.assertNotIn("thread_ts", serialized.lower())

    def test_thread_event_never_accepts_destination_fields(self) -> None:
        event = build_engineer_case_thread_event(
            event_id="engineer-slack:12874-1:ai:2",
            event_type="engineer_ai_response",
            engineer_case_id="12874-1",
            message_text="Analysis complete.",
            investigation_id="12874-1-round-1",
            conversation_version=2,
            draft_version=1,
            customer_draft="Please upgrade and retry.",
            action="guardrail",
        )
        self.assertEqual(event["conversation_version"], 2)
        self.assertEqual(event["draft_version"], 1)
        self.assertFalse({"team_id", "channel_id", "thread_ts"} & set(event))

    def test_post_timeout_is_outcome_unknown(self) -> None:
        event = build_engineer_case_thread_event(
            event_id="event-1",
            event_type="engineer_ai_response",
            engineer_case_id="EC-1",
            message_text="Done",
        )
        with patch.dict(
            os.environ,
            {
                "ENGINEER_SLACK_N8N_WEBHOOK_URL": "https://n8n.invalid/post",
                "n8n_request_token": "secret",
            },
            clear=False,
        ), patch(
            "backend.services.engineer_slack_n8n.urllib.request.urlopen",
            side_effect=socket.timeout("timed out"),
        ):
            with self.assertRaises(EngineerSlackN8nError) as raised:
                post_engineer_slack_event(event)
        self.assertTrue(raised.exception.outcome_unknown)

    def test_status_is_read_only_and_accepts_missing(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ENGINEER_SLACK_N8N_STATUS_URL": "https://n8n.invalid/status",
                "n8n_request_token": "secret",
            },
            clear=False,
        ), patch(
            "backend.services.engineer_slack_n8n.urllib.request.urlopen",
            return_value=_Response({"event_id": "event:2", "status": "missing"}),
        ) as urlopen:
            result = get_engineer_slack_event_status("event:2")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(result["status"], "missing")


class EngineerSlackOutboxRepositoryTests(unittest.TestCase):
    def test_case_save_and_root_event_are_idempotent(self) -> None:
        repository = InMemoryTicketRepository()
        repository.initialize()
        ticket = {
            "ticket_id": "12874",
            "subject": "Token callback missing",
            "status": "open",
            "messages": [{"role": "customer", "content": "Callback never fires."}],
        }
        repository.save_ticket(ticket, new_messages=ticket["messages"])
        engineer_case = build_new_engineer_case(
            ticket,
            engineer_case_id="12874-1",
            case_sequence=1,
            title="Token callback missing",
            status="investigating",
            trigger_source="account_not_automated",
            trigger_reason="no registered automation",
            now_value="2026-08-24T00:00:00+00:00",
        )
        event = build_engineer_case_opened_event(
            account_case={
                "account_case_id": "AC-12874",
                "zendesk_ticket_id": "12874",
                "title": "Token callback missing",
                "question": "Callback never fires.",
                "not_automated_reason": "no registered automation",
            },
            engineer_case=engineer_case,
        )

        repository.save_engineer_case(engineer_case, slack_events=[event])
        repository.save_engineer_case(engineer_case, slack_events=[event])

        queued = repository.list_engineer_slack_events(statuses=("queued",))
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["payload"], event)
        claim = repository.claim_engineer_slack_event(
            event_id=event["event_id"], claimed_at="2026-08-24T00:01:00+00:00"
        )
        assert claim is not None
        self.assertTrue(claim["claimed"])
        second_claim = repository.claim_engineer_slack_event(
            event_id=event["event_id"], claimed_at="2026-08-24T00:02:00+00:00"
        )
        assert second_claim is not None
        self.assertFalse(second_claim["claimed"])

    def test_only_pending_or_unknown_event_can_be_requeued(self) -> None:
        repository = InMemoryTicketRepository()
        repository.initialize()
        repository._engineer_slack_events["event-1"] = {
            "event_id": "event-1",
            "engineer_case_id": "EC-1",
            "event_type": "engineer_ai_response",
            "payload": {"event_id": "event-1"},
            "status": "delivered",
            "failure_code": None,
            "confirmed_at": "2026-08-24T00:00:00+00:00",
            "created_at": "2026-08-24T00:00:00+00:00",
            "updated_at": "2026-08-24T00:00:00+00:00",
        }
        result = repository.requeue_engineer_slack_event(
            event_id="event-1", requeued_at="2026-08-24T00:02:00+00:00"
        )
        assert result is not None
        self.assertEqual(result["status"], "delivered")


class EngineerSlackWorkerTests(unittest.TestCase):
    def test_health_warning_is_production_only(self) -> None:
        missing = {
            "ENGINEER_SLACK_N8N_WEBHOOK_URL": "",
            "ENGINEER_SLACK_N8N_STATUS_URL": "",
            "n8n_request_token": "",
        }
        with patch.dict(
            os.environ,
            {**missing, "ACCOUNT_DEFAULT_PROCESSING_PROFILE": "production"},
            clear=False,
        ):
            self.assertIn("engineer_slack_n8n_config_incomplete", main._health_config_warnings())
        with patch.dict(
            os.environ,
            {**missing, "ACCOUNT_DEFAULT_PROCESSING_PROFILE": "staging"},
            clear=False,
        ):
            self.assertNotIn("engineer_slack_n8n_config_incomplete", main._health_config_warnings())

    def test_outcome_unknown_queries_status_without_posting(self) -> None:
        repository = Mock()
        repository.list_engineer_slack_events.return_value = [
            {"event_id": "event-unknown", "status": "outcome_unknown"}
        ]
        with patch.dict(
            os.environ, {"ACCOUNT_DEFAULT_PROCESSING_PROFILE": "production"}, clear=False
        ), patch.object(worker, "engineer_slack_n8n_configured", return_value=True), patch.object(
            worker, "ticket_repository", repository
        ), patch.object(
            worker,
            "get_engineer_slack_event_status",
            return_value={"event_id": "event-unknown", "status": "pending", "failure_code": None},
        ) as status, patch.object(worker, "post_engineer_slack_event") as post:
            worker._drain_engineer_slack_events(limit=20)
        status.assert_called_once_with("event-unknown")
        post.assert_not_called()
        repository.requeue_engineer_slack_event.assert_not_called()

    def test_only_missing_remote_status_requeues(self) -> None:
        repository = Mock()
        repository.list_engineer_slack_events.return_value = [
            {"event_id": "event-missing", "status": "pending"}
        ]
        with patch.dict(
            os.environ, {"ACCOUNT_DEFAULT_PROCESSING_PROFILE": "production"}, clear=False
        ), patch.object(worker, "engineer_slack_n8n_configured", return_value=True), patch.object(
            worker, "ticket_repository", repository
        ), patch.object(
            worker,
            "get_engineer_slack_event_status",
            return_value={"event_id": "event-missing", "status": "missing", "failure_code": None},
        ):
            worker._drain_engineer_slack_events(limit=20)
        repository.requeue_engineer_slack_event.assert_called_once_with(
            event_id="event-missing", requeued_at=unittest.mock.ANY
        )
        repository.complete_engineer_slack_event.assert_not_called()

    def test_staging_does_not_read_outbox(self) -> None:
        repository = Mock()
        with patch.dict(
            os.environ, {"ACCOUNT_DEFAULT_PROCESSING_PROFILE": "staging"}, clear=False
        ), patch.object(worker, "ticket_repository", repository):
            worker._drain_engineer_slack_events(limit=20)
        repository.list_engineer_slack_events.assert_not_called()


if __name__ == "__main__":
    unittest.main()
