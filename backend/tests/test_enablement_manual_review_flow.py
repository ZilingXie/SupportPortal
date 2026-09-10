"""Manual enablement review flow tests (p2-149, Archer revert).

Covers the acceptance contract: zero Archer calls on every entry, the
awaiting_public_reply gate (unclaimable until the Zendesk public readback
releases it), the reply identity gate (sender snapshot, waiting state,
quoted-history exclusion) and the single-completion idempotency.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_automation_delivery import (
    DELIVERY_AWAITING_PUBLIC_REPLY,
    prepare_account_internal_email,
)
from backend.services.automation_account_intake import _start_enablement_manual_review


def _load_worker_module():
    module_path = Path(__file__).resolve().parents[1] / "worker.py"
    spec = importlib.util.spec_from_file_location(
        "backend.tests._manual_review_worker_under_test",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load backend.worker for tests")

    fake_main = types.ModuleType("backend.main")
    fake_main.build_answer = lambda *_args, **_kwargs: ("", 0.0, [], [], False)
    fake_main.build_query_task = lambda ticket_id, customer_message, message_created_at, **kwargs: {
        "task_type": "ticket_query",
        "ticket_id": ticket_id,
        "customer_message": customer_message,
        "message_created_at": message_created_at,
        **kwargs,
    }
    fake_main.resolve_support_message = lambda *_args, **_kwargs: None
    fake_main.build_client_sync_event = lambda *_args, **_kwargs: {}
    fake_main.build_engineer_followup_request = lambda *_args, **_kwargs: "follow up"
    fake_main.ensure_ticket_defaults = lambda _ticket: None
    fake_main.now_iso = lambda: "2026-03-22T00:00:00+00:00"
    fake_main._run_client_ticket_review_agent = lambda *_args, **_kwargs: None
    fake_main._record_ticket_agent_runtime_events = lambda *_args, **_kwargs: None
    fake_main.ticket_repository = Mock()
    fake_main.asset_repository = Mock()
    fake_main.asset_storage = Mock()

    module = importlib.util.module_from_spec(spec)
    sys.modules["backend.tests._manual_review_worker_under_test"] = module
    with patch.dict(sys.modules, {"backend.main": fake_main}):
        spec.loader.exec_module(module)
    return module


worker_module = _load_worker_module()


APP_ID = "0123456789abcdef0123456789abcdef"
SENDER = "reviewer@example.com"


def _base_case(*, ticket_id: str = "TK-MANUAL-1", app_id: str | None = APP_ID) -> dict:
    collected = {"requested_feature": "media_relay", "requested_feature_label": "Media Relay"}
    if app_id:
        collected["app_id"] = app_id
    return {
        "account_case_id": "AC-MANUAL-1",
        "billing_ticket_id": "AC-MANUAL-1",
        "client_ticket_id": ticket_id,
        "zendesk_ticket_id": ticket_id,
        "processing_profile": "production",
        "automation_status": "automation",
        "route": "enablement",
        "execution_action": "enablement",
        "automation_handler": "enablement",
        "route_classification": {"handler_binding_status": "active"},
        "customer_name": "Ziling",
        "collected_fields": collected,
        "missing_fields": [] if app_id else ["app_id"],
        "internal_email_payload": None,
        "internal_email_send_status": "not_ready",
        "created_at": "2026-09-10T00:00:00Z",
    }


def _email_payload() -> dict:
    return {
        "subject": "Enablement",
        "delivery_key": "enablement:AC-MANUAL-1:v1",
        "body": "Please enable manually and reply enabled.",
        "body_html": "<p>Please enable manually and reply enabled.</p>",
        "to_addresses": [SENDER],
        "cc_addresses": [],
    }


class ManualReviewWorkflowTests(unittest.TestCase):
    def _run(self, repository, case, payload):
        return _start_enablement_manual_review(
            repository=repository,
            account_case=dict(case),
            ticket_id=case["client_ticket_id"],
            email_payload=payload,
            persona_assignment=None,
            processing_profile="production",
            trigger_message_created_at="2026-09-10T01:00:00Z",
        )

    def test_valid_request_makes_zero_archer_calls(self):
        repository = InMemoryTicketRepository()
        repository.save_account_case(_base_case())
        with patch(
            "backend.services.enablement_archer_executor.execute_enablement_archer",
            side_effect=AssertionError("archer executor must not be called"),
        ), patch(
            "backend.services.archer_direct_client.DirectArcherClient.call",
            side_effect=AssertionError("archer client must not be called"),
        ):
            case, reply_job, outcome = self._run(
                repository, _base_case(), _email_payload()
            )
        self.assertEqual(outcome, "review_requested")
        self.assertIsNotNone(reply_job)
        self.assertEqual(case["internal_email_send_status"], DELIVERY_AWAITING_PUBLIC_REPLY)
        self.assertEqual(reply_job["payload"]["reply_intent"], "submission_confirmation")
        self.assertIs(reply_job["payload"].get("close_after_publish"), None)

    def test_invalid_appid_format_short_circuits_without_email(self):
        repository = InMemoryTicketRepository()
        case, reply_job, outcome = self._run(
            repository, _base_case(app_id="frhug123"), _email_payload()
        )
        self.assertEqual(outcome, "appid_invalid")
        self.assertEqual(reply_job["payload"]["reply_intent"], "enablement_appid_invalid")
        self.assertEqual(case["missing_fields"], ["app_id"])
        self.assertEqual(case["internal_email_send_status"], "not_applicable")
        self.assertNotIn("app_id", case["collected_fields"])

    def test_gated_email_is_not_claimable_until_released(self):
        repository = InMemoryTicketRepository()
        repository.save_account_case(_base_case())
        case, _job, _outcome = self._run(repository, _base_case(), _email_payload())
        account_case_id = case["account_case_id"]
        claimed = repository.claim_account_internal_email_delivery(
            account_case_id,
            delivery_key="enablement:AC-MANUAL-1:v1",
            claim_token="tok",
            claimed_at="2026-09-10T01:01:00Z",
            payload=case["internal_email_payload"],
        )
        self.assertFalse(claimed)

    def test_public_readback_hook_releases_the_gate(self):
        repository = InMemoryTicketRepository()
        repository.save_account_case(_base_case())
        case, _job, _outcome = self._run(repository, _base_case(), _email_payload())
        account_case_id = case["account_case_id"]
        repository.create_account_zendesk_comment_delivery(
            account_case_id=account_case_id,
            message_id="assistant-msg-1",
            zendesk_ticket_id=case["client_ticket_id"],
            idempotency_key=f"zd:{account_case_id}:assistant-msg-1",
            created_at="2026-09-10T01:01:30Z",
            is_public=True,
            target_status=None,
        )
        repository.begin_idempotent_request(
            "account_zendesk_internal_comment",
            f"zd:{account_case_id}:assistant-msg-1",
            created_at="2026-09-10T01:01:45Z",
        )
        repository.record_account_zendesk_internal_comment_result(
            account_case_id=account_case_id,
            ticket_id=case["client_ticket_id"],
            message_id="assistant-msg-1",
            idempotency_key=f"zd:{account_case_id}:assistant-msg-1",
            result_payload={"status": "added", "comment_id": "zc-1"},
            recorded_at="2026-09-10T01:02:00Z",
        )
        refreshed = repository.get_account_case(account_case_id)
        self.assertEqual(refreshed["internal_email_send_status"], "pending")
        self.assertEqual(
            refreshed["internal_email_send_reason"], "public_reply_confirmed"
        )


class DrainReleaseTests(unittest.TestCase):
    def _seed_gated_case(self, repository, *, prepared_at, delivered_at):
        case = _base_case()
        case["created_at"] = "2026-09-10T00:00:00Z"
        case["updated_at"] = "2026-09-01T00:00:00Z"
        repository.save_account_case(case)
        payload = _email_payload()
        payload["customer_confirmation_queued"] = True
        prepared = prepare_account_internal_email(
            repository,
            account_case_id=case["account_case_id"],
            payload=payload,
            prepared_at=prepared_at,
            target_status=DELIVERY_AWAITING_PUBLIC_REPLY,
        )
        assert prepared
        workflow = {
            "version": 1,
            "state": "awaiting_public_reply",
            "reply_job_id": "job-1",
            "delivery_key": payload["delivery_key"],
            "prepared_at": prepared_at,
            "updated_at": prepared_at,
        }
        if delivered_at:
            repository.create_account_zendesk_comment_delivery(
                account_case_id=case["account_case_id"],
                message_id="assistant-msg-1",
                zendesk_ticket_id=case["client_ticket_id"],
                idempotency_key=f"zd:{case['account_case_id']}:assistant-msg-1",
                created_at="2026-09-10T01:01:30Z",
                is_public=True,
                target_status=None,
            )
            repository.begin_idempotent_request(
                "account_zendesk_internal_comment",
                f"zd:{case['account_code_id'] if False else case['account_case_id']}:assistant-msg-1",
                created_at="2026-09-10T01:01:45Z",
            )
            repository.record_account_zendesk_internal_comment_result(
                account_case_id=case["account_case_id"],
                ticket_id=case["client_ticket_id"],
                message_id="assistant-msg-1",
                idempotency_key=f"zd:{case['account_case_id']}:assistant-msg-1",
                result_payload={"status": "added"},
                recorded_at=delivered_at,
            )
            # The readback hook released the gate during seeding; simulate the
            # missed-hook/crash path the drain belt exists for by restoring
            # the gated state while the delivered evidence stays.
            refreshed = dict(repository.get_account_case(case["account_case_id"]))
            refreshed["internal_email_send_status"] = DELIVERY_AWAITING_PUBLIC_REPLY
            refreshed["internal_email_payload"] = dict(payload)
            refreshed["automation_context"] = {
                "enablement_manual_workflow": dict(workflow)
            }
            refreshed["updated_at"] = "2026-09-01T00:00:00Z"
            repository.save_account_case(refreshed)
            return refreshed
        refreshed = dict(repository.get_account_case(case["account_case_id"]))
        refreshed["internal_email_send_status"] = DELIVERY_AWAITING_PUBLIC_REPLY
        refreshed["internal_email_payload"] = dict(payload)
        refreshed["automation_context"] = {
            "enablement_manual_workflow": dict(workflow)
        }
        repository.save_account_case(refreshed)
        return refreshed

    def test_drain_releases_only_after_confirmation_delivered(self):
        repository = InMemoryTicketRepository()
        self._seed_gated_case(
            repository,
            prepared_at="2026-09-10T01:00:00Z",
            delivered_at="2026-09-10T01:05:00Z",
        )
        with patch.object(worker_module, "ticket_repository", repository), patch.object(
            worker_module,
            "_send_claimed_enablement_delivery",
            wraps=worker_module._send_claimed_enablement_delivery,
        ) as send:
            counts = worker_module._drain_enablement_manual_review_emails(
                limit=10, processing_profile="production"
            )
        self.assertEqual(counts["released"], 1)
        refreshed = repository.get_account_case("AC-MANUAL-1")
        self.assertNotEqual(refreshed["internal_email_send_status"], DELIVERY_AWAITING_PUBLIC_REPLY)

    def test_drain_keeps_gate_when_reply_not_delivered(self):
        repository = InMemoryTicketRepository()
        self._seed_gated_case(
            repository, prepared_at="2026-09-10T01:00:00Z", delivered_at=None
        )
        with patch.object(worker_module, "ticket_repository", repository):
            counts = worker_module._drain_enablement_manual_review_emails(
                limit=10, processing_profile="production"
            )
        self.assertEqual(counts["still_gated"], 1)
        self.assertEqual(counts["released"], 0)
        refreshed = repository.get_account_case("AC-MANUAL-1")
        self.assertEqual(refreshed["internal_email_send_status"], DELIVERY_AWAITING_PUBLIC_REPLY)

    def test_drain_ignores_deliveries_from_before_the_gate(self):
        repository = InMemoryTicketRepository()
        self._seed_gated_case(
            repository,
            prepared_at="2026-09-10T02:00:00Z",
            delivered_at="2026-09-10T01:00:00Z",
        )
        with patch.object(worker_module, "ticket_repository", repository):
            counts = worker_module._drain_enablement_manual_review_emails(
                limit=10, processing_profile="production"
            )
        self.assertEqual(counts["still_gated"], 1)
        self.assertEqual(counts["released"], 0)


class ReplyIdentityTests(unittest.TestCase):
    def _case(self, *, status="sent", payload=None, workflow_state=None):
        case = _base_case()
        case["internal_email_send_status"] = status
        case["internal_email_payload"] = payload if payload is not None else {
            "delivery_key": "enablement:AC-MANUAL-1:v1",
            "to_addresses": [SENDER],
            "cc_addresses": [],
        }
        if workflow_state:
            case["automation_context"] = {
                "enablement_manual_workflow": {"state": workflow_state}
            }
        return case

    def _reply(self, *, sender=SENDER, body="Media Relay has been enabled."):
        return types.SimpleNamespace(
            message_id="msg-1",
            sender=sender,
            subject="Re: [Enablement Request] Media Relay - Ticket TK-MANUAL-1",
            body_text=body,
        )

    def test_allowed_recipient_passes_the_gate(self):
        self.assertIsNone(
            worker_module._enablement_reply_identity_gate(self._case(), self._reply())
        )

    def test_non_recipient_sender_is_stopped(self):
        self.assertEqual(
            worker_module._enablement_reply_identity_gate(
                self._case(), self._reply(sender="stranger@example.com")
            ),
            "enablement_reply_sender_unverified",
        )

    def test_missing_recipient_snapshot_is_stopped(self):
        case = self._case(payload={"delivery_key": "enablement:AC-MANUAL-1:v1"})
        self.assertEqual(
            worker_module._enablement_reply_identity_gate(case, self._reply()),
            "enablement_reply_recipients_unknown",
        )

    def test_not_waiting_state_is_stopped(self):
        case = self._case(status="awaiting_public_reply")
        self.assertEqual(
            worker_module._enablement_reply_identity_gate(case, self._reply()),
            "enablement_reply_not_awaiting_confirmation",
        )

    def test_already_completed_application_is_stopped(self):
        case = self._case(workflow_state="completed")
        self.assertEqual(
            worker_module._enablement_reply_identity_gate(case, self._reply()),
            "enablement_reply_already_completed",
        )

    def test_quoted_history_alone_is_excluded(self):
        body = (
            "Thanks.\n"
            "> -----Original Message-----\n"
            "> It has been enabled successfully.\n"
        )
        self.assertEqual(worker_module._unquoted_enablement_reply_segment(body), "Thanks.")

    def test_unquoted_segment_is_preserved(self):
        body = "It is enabled now.\n> old thread said enabled too"
        self.assertEqual(
            worker_module._unquoted_enablement_reply_segment(body),
            "It is enabled now.",
        )

    def test_handler_stops_quoted_only_reply_without_completion(self):
        repository = InMemoryTicketRepository()
        case = self._case()
        case["client_ticket_id"] = "TK-MANUAL-1"
        repository.save_ticket(
            {"ticket_id": "TK-MANUAL-1", "status": "open", "messages": []}
        )
        repository.save_account_case(case)
        reply = self._reply(body="> enabled in the quoted thread only")
        with patch.object(worker_module, "ticket_repository", repository):
            handled = worker_module.handle_enablement_request_reply(reply)
        self.assertEqual(handled, "completed")
        events = repository.list_ticket_events("TK-MANUAL-1")
        self.assertTrue(
            any(event["event_type"] == "enablement_reply_processing_stopped" for event in events)
        )
