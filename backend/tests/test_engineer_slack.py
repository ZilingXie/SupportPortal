from __future__ import annotations

import io
import json
import os
import socket
import unittest
import urllib.error
from unittest.mock import Mock, patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from backend import main, worker
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.engineer_cases import build_new_engineer_case
from backend.services.engineer_slack import (
    EngineerSlackDeliveryError,
    build_engineer_case_status_changed_event,
    build_engineer_case_opened_event,
    build_engineer_case_thread_event,
    engineer_slack_configured,
    post_engineer_slack_event,
)


DIRECT_ENV = {
    "ENGINEER_SLACK_ACCESS_TOKEN": "xoxp" + "-test-token",
    "ENGINEER_SLACK_TEAM_ID": "T-TEST",
    "ENGINEER_SLACK_CHANNEL_ID": "C-TEST",
    "ENGINEER_SLACK_TIMEOUT_SECONDS": "15",
}


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _ticket_and_case() -> tuple[dict[str, object], dict[str, object]]:
    ticket: dict[str, object] = {
        "ticket_id": "12874",
        "subject": "Token callback missing",
        "status": "open",
        "messages": [{"role": "customer", "content": "Callback never fires."}],
    }
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
    return ticket, engineer_case


def _root_event(engineer_case: dict[str, object]) -> dict[str, object]:
    return build_engineer_case_opened_event(
        account_case={
            "account_case_id": "AC-12874",
            "zendesk_ticket_id": "12874",
            "title": "Token callback missing",
            "question": "Callback never fires.",
            "not_automated_reason": "no registered automation",
        },
        engineer_case=engineer_case,
    )


class EngineerSlackContractTests(unittest.TestCase):
    def test_configuration_requires_token_team_and_channel(self) -> None:
        with patch.dict(os.environ, DIRECT_ENV, clear=False):
            self.assertTrue(engineer_slack_configured())
        for missing in (
            "ENGINEER_SLACK_ACCESS_TOKEN",
            "ENGINEER_SLACK_TEAM_ID",
            "ENGINEER_SLACK_CHANNEL_ID",
        ):
            with self.subTest(missing=missing), patch.dict(
                os.environ,
                {**DIRECT_ENV, missing: ""},
                clear=False,
            ):
                self.assertFalse(engineer_slack_configured())

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

    def test_status_changed_event_uses_exact_customer_facing_text(self) -> None:
        event = build_engineer_case_status_changed_event(
            event_id="engineer-slack:EC-1:status:open:pending:revision-1",
            engineer_case_id="EC-1",
            prior_status=" OPEN ",
            zendesk_status="PENDING",
            investigation_id="EC-1-round-1",
        )

        self.assertEqual(
            event["message_text"],
            "Ticket's Status has been changed from open to pending.",
        )
        self.assertEqual(event["prior_zendesk_status"], "open")
        self.assertEqual(event["zendesk_status"], "pending")

    def test_direct_post_uses_fixed_channel_and_returns_root_binding(self) -> None:
        _ticket, engineer_case = _ticket_and_case()
        event = _root_event(engineer_case)
        with patch.dict(os.environ, DIRECT_ENV, clear=False), patch(
            "backend.services.engineer_slack.urllib.request.urlopen",
            return_value=_Response({"ok": True, "channel": "C-TEST", "ts": "100.200"}),
        ) as urlopen:
            result = post_engineer_slack_event(event)

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://slack.com/api/chat.postMessage")
        self.assertEqual(
            request.headers["Authorization"],
            "Bearer " + DIRECT_ENV["ENGINEER_SLACK_ACCESS_TOKEN"],
        )
        self.assertEqual(payload["channel"], "C-TEST")
        self.assertNotIn("thread_ts", payload)
        self.assertTrue(payload["client_msg_id"])
        self.assertEqual(result["slack_message_ts"], "100.200")
        self.assertEqual(result["slack_thread_ts"], "100.200")

    def test_direct_post_sends_actions_in_bound_thread(self) -> None:
        event = build_engineer_case_thread_event(
            event_id="event-guardrail",
            event_type="engineer_ai_response",
            engineer_case_id="EC-1",
            message_text="Draft is ready.",
            investigation_id="EC-1-round-1",
            draft_version=2,
            action="guardrail",
        )
        with patch.dict(os.environ, DIRECT_ENV, clear=False), patch(
            "backend.services.engineer_slack.urllib.request.urlopen",
            return_value=_Response({"ok": True, "channel": "C-TEST", "ts": "100.201"}),
        ) as urlopen:
            result = post_engineer_slack_event(event, thread_ts="100.200")

        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["thread_ts"], "100.200")
        self.assertEqual(payload["blocks"][-1]["elements"][0]["action_id"], "guardrail")
        self.assertEqual(result["slack_thread_ts"], "100.200")

    def test_action_text_is_split_to_slack_section_limit(self) -> None:
        event = build_engineer_case_thread_event(
            event_id="event-long-action",
            event_type="engineer_ai_response",
            engineer_case_id="EC-1",
            message_text="x" * 3001,
            investigation_id="EC-1-round-1",
            draft_version=2,
            action="guardrail",
        )
        with patch.dict(os.environ, DIRECT_ENV, clear=False), patch(
            "backend.services.engineer_slack.urllib.request.urlopen",
            return_value=_Response({"ok": True, "channel": "C-TEST", "ts": "100.201"}),
        ) as urlopen:
            post_engineer_slack_event(event, thread_ts="100.200")

        blocks = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))["blocks"]
        self.assertEqual([len(block["text"]["text"]) for block in blocks[:-1]], [3000, 1])

    def test_timeout_is_outcome_unknown_and_api_rejection_is_known(self) -> None:
        event = build_engineer_case_thread_event(
            event_id="event-1",
            event_type="engineer_ai_response",
            engineer_case_id="EC-1",
            message_text="Done",
        )
        with patch.dict(os.environ, DIRECT_ENV, clear=False), patch(
            "backend.services.engineer_slack.urllib.request.urlopen",
            side_effect=socket.timeout("timed out"),
        ):
            with self.assertRaises(EngineerSlackDeliveryError) as timeout_error:
                post_engineer_slack_event(event, thread_ts="100.200")
        self.assertTrue(timeout_error.exception.outcome_unknown)

        with patch.dict(os.environ, DIRECT_ENV, clear=False), patch(
            "backend.services.engineer_slack.urllib.request.urlopen",
            return_value=_Response({"ok": False, "error": "not_in_channel"}),
        ):
            with self.assertRaises(EngineerSlackDeliveryError) as api_error:
                post_engineer_slack_event(event, thread_ts="100.200")
        self.assertFalse(api_error.exception.outcome_unknown)
        self.assertEqual(api_error.exception.code, "engineer_slack_api_not_in_channel")

        upstream_error = urllib.error.HTTPError(
            "https://slack.com/api/chat.postMessage",
            503,
            "Service unavailable",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"service_unavailable"}'),
        )
        with patch.dict(os.environ, DIRECT_ENV, clear=False), patch(
            "backend.services.engineer_slack.urllib.request.urlopen",
            side_effect=upstream_error,
        ):
            with self.assertRaises(EngineerSlackDeliveryError) as server_error:
                post_engineer_slack_event(event, thread_ts="100.200")
        self.assertTrue(server_error.exception.outcome_unknown)


class EngineerSlackOutboxRepositoryTests(unittest.TestCase):
    def test_root_delivery_persists_binding_and_active_resolver_closes(self) -> None:
        repository = InMemoryTicketRepository()
        repository.initialize()
        ticket, engineer_case = _ticket_and_case()
        repository.save_ticket(ticket, new_messages=ticket["messages"])
        event = _root_event(engineer_case)

        repository.save_engineer_case(engineer_case, slack_events=[event])
        repository.save_engineer_case(engineer_case, slack_events=[event])
        queued = repository.list_engineer_slack_events(statuses=("queued",))
        self.assertEqual(len(queued), 1)
        claim = repository.claim_engineer_slack_event(
            event_id=event["event_id"],
            claimed_at="2026-08-24T00:01:00+00:00",
        )
        assert claim is not None
        self.assertTrue(claim["claimed"])
        with self.assertRaisesRegex(ValueError, "requires Slack remote references"):
            repository.complete_engineer_slack_event(
                event_id=event["event_id"],
                status="delivered",
                failure_code=None,
                completed_at="2026-08-24T00:02:00+00:00",
            )
        repository.complete_engineer_slack_event(
            event_id=event["event_id"],
            status="delivered",
            failure_code=None,
            completed_at="2026-08-24T00:02:00+00:00",
            slack_channel_id="C-TEST",
            slack_message_ts="100.200",
            slack_thread_ts="100.200",
        )

        self.assertEqual(
            repository.resolve_engineer_slack_thread_binding(
                slack_channel_id="C-TEST",
                slack_thread_ts="100.200",
            )["engineer_case_id"],
            "12874-1",
        )
        engineer_case["investigation_state"] = "closed"
        engineer_case["closed_at"] = "2026-08-24T00:03:00+00:00"
        repository.save_engineer_case(engineer_case)
        self.assertIsNone(
            repository.resolve_engineer_slack_thread_binding(
                slack_channel_id="C-TEST",
                slack_thread_ts="100.200",
            )
        )
        historical = repository.get_engineer_slack_thread_binding(
            "12874-1",
            active_only=False,
        )
        assert historical is not None
        self.assertFalse(historical["active"])


class EngineerSlackWorkerTests(unittest.TestCase):
    def test_health_warning_is_production_only(self) -> None:
        missing = {key: "" for key in DIRECT_ENV}
        with patch.dict(
            os.environ,
            {**missing, "ACCOUNT_DEFAULT_PROCESSING_PROFILE": "production"},
            clear=False,
        ):
            self.assertIn("engineer_slack_config_incomplete", main._health_config_warnings())
        with patch.dict(
            os.environ,
            {**missing, "ACCOUNT_DEFAULT_PROCESSING_PROFILE": "staging"},
            clear=False,
        ):
            self.assertNotIn("engineer_slack_config_incomplete", main._health_config_warnings())

    def test_root_event_is_sent_and_remote_binding_is_completed(self) -> None:
        repository = Mock()
        event = {
            "event_id": "event-root",
            "event_type": "engineer_case_opened",
            "engineer_case_id": "EC-1",
            "message_text": "New case",
        }
        repository.list_engineer_slack_events.return_value = [
            {**event, "status": "queued", "payload": event}
        ]
        repository.claim_engineer_slack_event.return_value = {
            **event,
            "status": "pending",
            "payload": event,
            "claimed": True,
        }
        result = {
            "event_id": "event-root",
            "status": "delivered",
            "failure_code": None,
            "slack_channel_id": "C-TEST",
            "slack_message_ts": "100.200",
            "slack_thread_ts": "100.200",
        }
        with patch.dict(
            os.environ,
            {**DIRECT_ENV, "ACCOUNT_DEFAULT_PROCESSING_PROFILE": "production"},
            clear=False,
        ), patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "post_engineer_slack_event", return_value=result
        ) as post:
            worker._drain_engineer_slack_events(limit=20)

        repository.list_engineer_slack_events.assert_called_once_with(
            statuses=("queued",),
            limit=20,
        )
        post.assert_called_once_with(event, thread_ts=None)
        repository.complete_engineer_slack_event.assert_called_once_with(
            event_id="event-root",
            status="delivered",
            failure_code=None,
            completed_at=unittest.mock.ANY,
            slack_channel_id="C-TEST",
            slack_message_ts="100.200",
            slack_thread_ts="100.200",
        )

    def test_thread_event_waits_for_binding_before_claim(self) -> None:
        repository = Mock()
        event = {
            "event_id": "event-thread",
            "event_type": "engineer_ai_response",
            "engineer_case_id": "EC-1",
            "message_text": "Analysis",
        }
        repository.list_engineer_slack_events.return_value = [
            {**event, "status": "queued", "payload": event}
        ]
        repository.get_engineer_slack_thread_binding.return_value = None
        with patch.dict(
            os.environ,
            {**DIRECT_ENV, "ACCOUNT_DEFAULT_PROCESSING_PROFILE": "production"},
            clear=False,
        ), patch.object(worker, "ticket_repository", repository), patch.object(
            worker, "post_engineer_slack_event"
        ) as post:
            worker._drain_engineer_slack_events(limit=20)

        repository.get_engineer_slack_thread_binding.assert_called_once_with(
            "EC-1",
            active_only=False,
        )
        repository.claim_engineer_slack_event.assert_not_called()
        post.assert_not_called()

    def test_unknown_result_is_terminal_without_automatic_replay(self) -> None:
        repository = Mock()
        event = {
            "event_id": "event-unknown",
            "event_type": "engineer_case_opened",
            "engineer_case_id": "EC-1",
            "message_text": "New case",
        }
        repository.list_engineer_slack_events.return_value = [
            {**event, "status": "queued", "payload": event}
        ]
        repository.claim_engineer_slack_event.return_value = {
            **event,
            "status": "pending",
            "payload": event,
            "claimed": True,
        }
        with patch.dict(
            os.environ,
            {**DIRECT_ENV, "ACCOUNT_DEFAULT_PROCESSING_PROFILE": "production"},
            clear=False,
        ), patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "post_engineer_slack_event",
            side_effect=EngineerSlackDeliveryError(
                "engineer_slack_request_failed",
                outcome_unknown=True,
            ),
        ):
            worker._drain_engineer_slack_events(limit=20)

        repository.complete_engineer_slack_event.assert_called_once_with(
            event_id="event-unknown",
            status="outcome_unknown",
            failure_code="engineer_slack_request_failed",
            completed_at=unittest.mock.ANY,
        )

    def test_staging_does_not_read_outbox(self) -> None:
        repository = Mock()
        with patch.dict(
            os.environ,
            {**DIRECT_ENV, "ACCOUNT_DEFAULT_PROCESSING_PROFILE": "staging"},
            clear=False,
        ), patch.object(worker, "ticket_repository", repository):
            worker._drain_engineer_slack_events(limit=20)
        repository.list_engineer_slack_events.assert_not_called()


if __name__ == "__main__":
    unittest.main()
