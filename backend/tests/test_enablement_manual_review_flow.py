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


def _ensure_ticket(repository, ticket_id: str) -> dict:
    ticket = repository.get_ticket(ticket_id)
    if not isinstance(ticket, dict):
        ticket = {"ticket_id": ticket_id, "status": "open", "messages": []}
        repository.save_ticket(ticket)
    return ticket


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
        confirmation_job_id = str(
            case["automation_context"]["enablement_manual_workflow"]["reply_job_id"]
        )
        ticket = _ensure_ticket(repository, case["client_ticket_id"])
        ticket.setdefault("messages", []).append(
            {
                "role": "assistant",
                "content": "We received your request.",
                "message_id": "assistant-msg-1",
                "meta": {"account_reply_job_id": confirmation_job_id},
            }
        )
        repository.save_ticket(ticket)
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
        self.assertEqual(
            refreshed["automation_context"]["enablement_manual_workflow"]["state"],
            "email_released",
        )

    def test_unrelated_public_reply_does_not_release_the_gate(self):
        repository = InMemoryTicketRepository()
        repository.save_account_case(_base_case())
        case, _job, _outcome = self._run(repository, _base_case(), _email_payload())
        account_case_id = case["account_case_id"]
        # A public reply that belongs to a DIFFERENT job (e.g. an older
        # follow-up) must not release the current application's gate.
        ticket = _ensure_ticket(repository, case["client_ticket_id"])
        ticket.setdefault("messages", []).append(
            {
                "role": "assistant",
                "content": "Unrelated update.",
                "message_id": "assistant-msg-other",
                "meta": {"account_reply_job_id": "job-some-other"},
            }
        )
        repository.save_ticket(ticket)
        repository.create_account_zendesk_comment_delivery(
            account_case_id=account_case_id,
            message_id="assistant-msg-other",
            zendesk_ticket_id=case["client_ticket_id"],
            idempotency_key=f"zd:{account_case_id}:assistant-msg-other",
            created_at="2026-09-10T01:01:30Z",
            is_public=True,
            target_status=None,
        )
        repository.begin_idempotent_request(
            "account_zendesk_internal_comment",
            f"zd:{account_case_id}:assistant-msg-other",
            created_at="2026-09-10T01:01:45Z",
        )
        repository.record_account_zendesk_internal_comment_result(
            account_case_id=account_case_id,
            ticket_id=case["client_ticket_id"],
            message_id="assistant-msg-other",
            idempotency_key=f"zd:{account_case_id}:assistant-msg-other",
            result_payload={"status": "added", "comment_id": "zc-other"},
            recorded_at="2026-09-10T01:02:00Z",
        )
        refreshed = repository.get_account_case(account_case_id)
        self.assertEqual(
            refreshed["internal_email_send_status"], "awaiting_public_reply"
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
            ticket = _ensure_ticket(repository, case["client_ticket_id"])
            ticket.setdefault("messages", []).append(
                {
                    "role": "assistant",
                    "content": "We received your request.",
                    "message_id": "assistant-msg-1",
                    "meta": {"account_reply_job_id": "job-1"},
                }
            )
            repository.save_ticket(ticket)
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

    def test_drain_keeps_gate_without_confirmation_linkage(self):
        # Fail-closed: a gated case whose workflow context lacks the
        # confirmation reply_job_id (e.g. an interrupted legacy write) must
        # never release, even when unrelated public deliveries exist.
        repository = InMemoryTicketRepository()
        case = _base_case()
        case["created_at"] = "2026-09-10T00:00:00Z"
        case["updated_at"] = "2026-09-01T00:00:00Z"
        repository.save_account_case(case)
        payload = _email_payload()
        payload["customer_confirmation_queued"] = True
        prepare_account_internal_email(
            repository,
            account_case_id=case["account_case_id"],
            payload=payload,
            prepared_at="2026-09-10T01:00:00Z",
            target_status=DELIVERY_AWAITING_PUBLIC_REPLY,
        )
        refreshed = dict(repository.get_account_case(case["account_case_id"]))
        refreshed["automation_context"] = {
            "enablement_manual_workflow": {
                "version": 1,
                "state": "awaiting_public_reply",
                "delivery_key": payload["delivery_key"],
            }
        }
        repository.save_account_case(refreshed)
        with patch.object(worker_module, "ticket_repository", repository):
            counts = worker_module._drain_enablement_manual_review_emails(
                limit=10, processing_profile="production"
            )
        self.assertEqual(counts["still_gated"], 1)
        self.assertEqual(counts["released"], 0)
        stored = repository.get_account_case(case["account_case_id"])
        self.assertEqual(stored["internal_email_send_status"], DELIVERY_AWAITING_PUBLIC_REPLY)


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

class OutlookQuoteBoundaryTests(unittest.TestCase):
    def test_reviewer_repro_keeps_only_thanks(self):
        body = (
            "Thanks.\n"
            "From: Engineer Name <engineer@example.com>\n"
            "Sent: Yesterday\n"
            "Subject: Old request\n"
            "Media Relay has been enabled."
        )
        segment = worker_module._unquoted_enablement_reply_segment(body)
        self.assertEqual(segment, "Thanks.")
        self.assertFalse(
            worker_module._enablement_reply_explicitly_confirms_completion(segment)
        )

    def test_outlook_header_run_is_boundary(self):
        body = "Checked, all good.\nSent: Monday 10:00\nTo: someone\nSubject: re\nenabled"
        self.assertEqual(
            worker_module._unquoted_enablement_reply_segment(body),
            "Checked, all good.",
        )

    def test_inline_from_header_cuts_tail(self):
        body = "Thanks. From: Engineer <engineer@example.com> Media Relay has been enabled."
        self.assertEqual(
            worker_module._unquoted_enablement_reply_segment(body),
            "Thanks.",
        )

    def test_valid_unquoted_completion_still_passes(self):
        body = "Media Relay has been enabled for the project.\n> old: enabled yesterday"
        segment = worker_module._unquoted_enablement_reply_segment(body)
        self.assertTrue(
            worker_module._enablement_reply_explicitly_confirms_completion(segment)
        )


class CompletionIdempotencyTests(unittest.TestCase):
    def _seed_sent_case(self, repository):
        case = _base_case()
        case["internal_email_send_status"] = "sent"
        case["internal_email_payload"] = {
            "delivery_key": "enablement:AC-MANUAL-1:v1",
            "to_addresses": [SENDER],
        }
        case["automation_context"] = {
            "enablement_manual_workflow": {
                "version": 1,
                "state": "awaiting_human_confirmation",
                "reply_job_id": "job-1",
                "delivery_key": "enablement:AC-MANUAL-1:v1",
            }
        }
        repository.save_ticket(
            {"ticket_id": case["client_ticket_id"], "status": "open", "messages": []}
        )
        repository.save_account_case(case)
        return case

    def _enabled_reply(self, message_id):
        return types.SimpleNamespace(
            message_id=message_id,
            sender=SENDER,
            subject="Re: [Enablement Request] Media Relay - Ticket TK-MANUAL-1",
            body_text="Media Relay has been enabled.",
        )

    def test_second_confirmation_creates_no_second_completion_job(self):
        repository = InMemoryTicketRepository()
        self._seed_sent_case(repository)
        with patch.object(worker_module, "ticket_repository", repository):
            first = worker_module.handle_enablement_request_reply(
                self._enabled_reply("enabled-msg-1")
            )
            second = worker_module.handle_enablement_request_reply(
                self._enabled_reply("enabled-msg-2")
            )
        self.assertEqual(first, "completed")
        self.assertEqual(second, "completed")
        completion_jobs = [
            job
            for job in repository._account_reply_jobs.values()
            if job["payload"].get("reply_intent") == "enablement_completed_and_close"
        ]
        self.assertEqual(len(completion_jobs), 1)
        events = repository.list_ticket_events("TK-MANUAL-1")
        stopped = [
            event
            for event in events
            if event["event_type"] == "enablement_reply_processing_stopped"
        ]
        self.assertTrue(
            any(
                event["payload"].get("reason") == "enablement_reply_already_completed"
                for event in stopped
            )
        )
        stored = repository.get_account_case("AC-MANUAL-1")
        self.assertEqual(
            stored["automation_context"]["enablement_manual_workflow"]["state"],
            "completed",
        )


class DrainScopeTests(unittest.TestCase):
    def test_legacy_pending_without_context_is_never_taken_over(self):
        repository = InMemoryTicketRepository()
        case = _base_case()
        case["created_at"] = "2026-09-10T00:00:00Z"
        case["updated_at"] = "2026-09-01T00:00:00Z"
        case["internal_email_send_status"] = "pending"
        case["internal_email_payload"] = _email_payload()
        repository.save_account_case(case)
        with patch.object(worker_module, "ticket_repository", repository), patch.object(
            worker_module,
            "_send_claimed_enablement_delivery",
            side_effect=AssertionError("legacy todo must not be taken over"),
        ) as send:
            counts = worker_module._drain_enablement_manual_review_emails(
                limit=10, processing_profile="production"
            )
        send.assert_not_called()
        self.assertEqual(counts, {"released": 0, "sent": 0, "send_retried": 0, "still_gated": 0})

    def test_released_state_required_before_send(self):
        repository = InMemoryTicketRepository()
        case = _base_case()
        case["created_at"] = "2026-09-10T00:00:00Z"
        case["updated_at"] = "2026-09-01T00:00:00Z"
        case["internal_email_send_status"] = "pending"
        case["internal_email_payload"] = _email_payload()
        case["automation_context"] = {
            "enablement_manual_workflow": {
                "version": 1,
                "state": "awaiting_human_confirmation",
                "reply_job_id": "job-1",
                "delivery_key": "enablement:AC-MANUAL-1:v1",
            }
        }
        repository.save_account_case(case)
        with patch.object(worker_module, "ticket_repository", repository), patch.object(
            worker_module,
            "_send_claimed_enablement_delivery",
            side_effect=AssertionError("only email_released may send"),
        ) as send:
            counts = worker_module._drain_enablement_manual_review_emails(
                limit=10, processing_profile="production"
            )
        send.assert_not_called()
        self.assertEqual(counts["sent"], 0)

    def test_filtered_query_is_not_starved_by_newer_unrelated_cases(self):
        repository = InMemoryTicketRepository()
        old_gated = _base_case(ticket_id="TK-OLD")
        old_gated["account_case_id"] = "AC-OLD"
        old_gated["billing_ticket_id"] = "AC-OLD"
        old_gated["created_at"] = "2026-09-01T00:00:00Z"
        old_gated["updated_at"] = "2026-09-01T00:00:00Z"
        old_gated["internal_email_send_status"] = "awaiting_public_reply"
        old_gated["internal_email_payload"] = _email_payload()
        old_gated["automation_context"] = {
            "enablement_manual_workflow": {
                "version": 1,
                "state": "awaiting_public_reply",
                "reply_job_id": "job-old",
                "delivery_key": "enablement:AC-OLD:v1",
            }
        }
        repository.save_account_case(old_gated)
        for index in range(30):
            newer = _base_case(ticket_id=f"TK-NEW-{index}")
            newer["account_case_id"] = f"AC-NEW-{index}"
            newer["billing_ticket_id"] = f"AC-NEW-{index}"
            newer["automation_handler"] = "billing"
            newer["route"] = "detailed_invoice"
            newer["execution_action"] = "detailed_invoice"
            newer["created_at"] = f"2026-09-02T00:{index:02d}:00Z"
            newer["updated_at"] = f"2026-09-02T00:{index:02d}:00Z"
            repository.save_account_case(newer)
        listed = repository.list_enablement_cases_by_email_status(
            ("awaiting_public_reply",), processing_profile="production", limit=5
        )
        self.assertEqual([case["account_case_id"] for case in listed], ["AC-OLD"])

class Round3ConcurrencyTests(unittest.TestCase):
    def _seed_sent_case(self, repository, *, with_workflow=True):
        case = _base_case()
        case["internal_email_send_status"] = "sent"
        case["internal_email_payload"] = {
            "delivery_key": "enablement:AC-MANUAL-1:v1",
            "to_addresses": [SENDER],
        }
        if with_workflow:
            case["automation_context"] = {
                "enablement_manual_workflow": {
                    "version": 1,
                    "state": "awaiting_human_confirmation",
                    "reply_job_id": "job-1",
                    "delivery_key": "enablement:AC-MANUAL-1:v1",
                }
            }
        repository.save_ticket(
            {"ticket_id": case["client_ticket_id"], "status": "open", "messages": []}
        )
        # a pending submission job that must be cancelled by the winner
        repository.save_account_reply_job(
            {
                "job_id": "submission-job-1",
                "ticket_id": case["client_ticket_id"],
                "trigger_message_created_at": "2026-09-10T00:00:00Z",
                "status": "persona_v8_queued",
                "scheduled_for": "2026-09-10T00:01:00Z",
                "payload": {"reply_intent": "submission_confirmation"},
                "attempt_count": 0,
                "claimed_at": None,
                "published_at": None,
                "created_at": "2026-09-10T00:00:00Z",
                "updated_at": "2026-09-10T00:00:00Z",
            }
        )
        repository.save_account_case(case)
        return case

    def _enabled_reply(self, message_id):
        return types.SimpleNamespace(
            message_id=message_id,
            sender=SENDER,
            subject="Re: [Enablement Request] Media Relay - Ticket TK-MANUAL-1",
            body_text="Media Relay has been enabled.",
        )

    def test_concurrent_confirmations_keep_the_one_completion_job_alive(self):
        import concurrent.futures
        import threading

        repository = InMemoryTicketRepository()
        self._seed_sent_case(repository)
        barrier = threading.Barrier(2)

        def confirm(message_id):
            def _run():
                barrier.wait()
                return worker_module.handle_enablement_request_reply(
                    self._enabled_reply(message_id)
                )

            return _run

        # ONE shared patch around the whole pool: per-thread patches race on
        # the module global (early exit restores it while the other thread is
        # still mid-handler, and a late exit can leave it polluted).
        with patch.object(worker_module, "ticket_repository", repository):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(confirm("enabled-a")), pool.submit(confirm("enabled-b"))]
                outcomes = [future.result() for future in futures]
        self.assertEqual(outcomes, ["completed", "completed"])
        self.assertIs(worker_module.ticket_repository.__class__, type(worker_module.ticket_repository))
        completion_jobs = [
            job
            for job in repository._account_reply_jobs.values()
            if job["payload"].get("reply_intent") == "enablement_completed_and_close"
        ]
        self.assertEqual(len(completion_jobs), 1)
        self.assertNotEqual(completion_jobs[0]["status"], "cancelled")
        submission = repository._account_reply_jobs.get("submission-job-1")
        self.assertEqual(submission["status"], "cancelled")

    def test_legacy_case_first_confirmation_persists_completed_marker(self):
        repository = InMemoryTicketRepository()
        self._seed_sent_case(repository, with_workflow=False)
        with patch.object(worker_module, "ticket_repository", repository):
            first = worker_module.handle_enablement_request_reply(
                self._enabled_reply("enabled-legacy-1")
            )
            second = worker_module.handle_enablement_request_reply(
                self._enabled_reply("enabled-legacy-2")
            )
        self.assertEqual(first, "completed")
        self.assertEqual(second, "completed")
        stored = repository.get_account_case("AC-MANUAL-1")
        workflow = stored["automation_context"]["enablement_manual_workflow"]
        self.assertEqual(workflow["state"], "completed")
        self.assertTrue(workflow.get("legacy"))
        completion_jobs = [
            job
            for job in repository._account_reply_jobs.values()
            if job["payload"].get("reply_intent") == "enablement_completed_and_close"
        ]
        self.assertEqual(len(completion_jobs), 1)


class Round3QuoteBoundaryTests(unittest.TestCase):
    def test_blockquote_html_never_satisfies_completion(self):
        html = (
            "<html><body>"
            "<div>Thanks.</div>"
            '<blockquote class="gmail_quote">'
            "<div>Media Relay has been enabled.</div>"
            "</blockquote>"
            "</body></html>"
        )
        from backend.services.billing_automation import _normalize_graph_message_body

        text = _normalize_graph_message_body(html, content_type="html")
        segment = worker_module._unquoted_enablement_reply_segment(text)
        self.assertEqual(segment, "Thanks.")
        self.assertFalse(
            worker_module._enablement_reply_explicitly_confirms_completion(segment)
        )

    def test_outlook_border_left_quote_div_is_boundary(self):
        html = (
            "<div>Checked, all good.</div>"
            '<div style="border-left:solid #B5C4DF 1.0pt;padding-left:5pt">'
            "<div>From: Engineer &lt;engineer@example.com&gt;</div>"
            "<div>Media Relay has been enabled.</div>"
            "</div>"
        )
        from backend.services.billing_automation import _normalize_graph_message_body

        text = _normalize_graph_message_body(html, content_type="html")
        segment = worker_module._unquoted_enablement_reply_segment(text)
        self.assertEqual(segment, "Checked, all good.")

    def test_chinese_outlook_headers_are_boundary(self):
        body = "Thanks.\n发件人：工程师 <engineer@example.com>\n发送时间：2026年9月10日\n主题：旧请求\nMedia Relay 已经开通。"
        segment = worker_module._unquoted_enablement_reply_segment(body)
        self.assertEqual(segment, "Thanks.")

    def test_chinese_original_message_marker_is_boundary(self):
        body = "好的，已处理。\n-----原始邮件-----\nFrom: a@b.c\nIt has been enabled."
        segment = worker_module._unquoted_enablement_reply_segment(body)
        self.assertEqual(segment, "好的，已处理。")

    def test_inline_chinese_from_header_cuts_tail(self):
        body = "谢谢。发件人：工程师 <engineer@example.com> Media Relay has been enabled."
        segment = worker_module._unquoted_enablement_reply_segment(body)
        self.assertEqual(segment, "谢谢。")


class Round3OwnershipTests(unittest.TestCase):
    def test_human_review_case_is_not_sent_by_drain(self):
        repository = InMemoryTicketRepository()
        case = _base_case()
        case["created_at"] = "2026-09-10T00:00:00Z"
        case["updated_at"] = "2026-09-01T00:00:00Z"
        case["automation_status"] = "human_review_required"
        case["internal_email_send_status"] = "pending"
        case["internal_email_payload"] = _email_payload()
        case["automation_context"] = {
            "enablement_manual_workflow": {
                "version": 1,
                "state": "email_released",
                "reply_job_id": "job-1",
                "delivery_key": "enablement:AC-MANUAL-1:v1",
            }
        }
        repository.save_account_case(case)
        with patch.object(worker_module, "ticket_repository", repository):
            listed = repository.list_enablement_cases_by_email_status(
                ("pending",),
                processing_profile="production",
                limit=10,
                workflow_states=("email_released",),
            )
        self.assertEqual(listed, [])
        result = worker_module._send_claimed_enablement_delivery(
            case, allow_rerun_owned=True
        )
        self.assertFalse(result.get("claimed"))
        self.assertEqual(result.get("reason"), "case_not_automation_owned")
        stored = repository.get_account_case("AC-MANUAL-1")
        self.assertEqual(stored["internal_email_send_status"], "pending")

    def test_hungry_legacy_pending_records_do_not_starve_released_case(self):
        repository = InMemoryTicketRepository()
        for index in range(25):
            legacy = _base_case(ticket_id=f"TK-LEGACY-{index}")
            legacy["account_case_id"] = f"AC-LEGACY-{index}"
            legacy["billing_ticket_id"] = f"AC-LEGACY-{index}"
            legacy["created_at"] = "2026-09-01T00:00:00Z"
            legacy["updated_at"] = f"2026-09-01T00:{index:02d}:00Z"
            legacy["internal_email_send_status"] = "pending"
            legacy["internal_email_payload"] = _email_payload()
            repository.save_account_case(legacy)
        released = _base_case(ticket_id="TK-REL")
        released["account_case_id"] = "AC-REL"
        released["billing_ticket_id"] = "AC-REL"
        released["created_at"] = "2026-08-31T00:00:00Z"
        released["updated_at"] = "2026-08-31T00:00:00Z"
        released["internal_email_send_status"] = "pending"
        released["internal_email_payload"] = _email_payload()
        released["automation_context"] = {
            "enablement_manual_workflow": {
                "version": 1,
                "state": "email_released",
                "reply_job_id": "job-rel",
                "delivery_key": "enablement:AC-REL:v1",
            }
        }
        repository.save_account_case(released)
        send_batch = repository.list_enablement_cases_by_email_status(
            ("pending", "retry", "failed", "skipped_config_missing"),
            processing_profile="production",
            limit=25,
            workflow_states=("email_released",),
        )
        self.assertEqual(
            [case["account_case_id"] for case in send_batch], ["AC-REL"]
        )

class Round4OwnershipRecoveryTests(unittest.TestCase):
    def _seed_human_review_case(self, repository, *, handler="billing"):
        case = _base_case()
        case["automation_handler"] = handler
        case["route"] = handler
        case["execution_action"] = handler
        case["automation_status"] = "human_review_required"
        case["internal_email_send_status"] = "retry"
        case["internal_email_payload"] = _email_payload()
        repository.save_account_case(case)
        return case

    def test_resume_path_claims_escalated_case_by_default(self):
        # The admin Resume flow (billing/quota/suspension) never resets
        # automation_status; the DEFAULT claim path must still work so the
        # explicitly authorized recovery can resend the email.
        from backend.services.account_automation_delivery import (
            deliver_account_internal_email,
        )

        repository = InMemoryTicketRepository()
        case = self._seed_human_review_case(repository)
        sent: list[str] = []

        def sender(payload):
            sent.append(str(payload.get("delivery_key") or ""))
            return {"status": "sent", "reason": ""}

        result = deliver_account_internal_email(
            repository,
            account_case_id=case["account_case_id"],
            payload=dict(case["internal_email_payload"]),
            sender=sender,
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(sent, ["enablement:AC-MANUAL-1:v1"])
        stored = repository.get_account_case(case["account_case_id"])
        self.assertEqual(stored["internal_email_send_status"], "sent")

    def test_background_claim_policy_rejection_keeps_known_not_sent(self):
        # require_automation_active=True (drain only): a human-review case is
        # NOT claimed, the sender never runs, and the result keeps the
        # persisted known-not-sent status instead of delivery_unknown.
        from backend.services.account_automation_delivery import (
            deliver_account_internal_email,
        )

        repository = InMemoryTicketRepository()
        case = self._seed_human_review_case(repository)
        sent: list[str] = []

        def sender(payload):
            sent.append("must-not-run")
            return {"status": "sent", "reason": ""}

        result = deliver_account_internal_email(
            repository,
            account_case_id=case["account_case_id"],
            payload=dict(case["internal_email_payload"]),
            sender=sender,
            require_automation_active=True,
        )
        self.assertFalse(result.claimed)
        self.assertFalse(result.persisted)
        self.assertEqual(sent, [])
        self.assertEqual(result.status, "retry")
        self.assertEqual(result.delivery_state, "known_not_sent")
        self.assertEqual(result.reason, "case_not_automation_owned")
        stored = repository.get_account_case(case["account_case_id"])
        self.assertEqual(stored["internal_email_send_status"], "retry")

    def test_release_window_not_starved_by_undeliverable_gated_cases(self):
        # 25 gated cases whose confirmation is NOT delivered must not block
        # the 26th (older) gated case whose confirmation IS delivered.
        repository = InMemoryTicketRepository()
        for index in range(25):
            stuck = _base_case(ticket_id=f"TK-STUCK-{index}")
            stuck["account_case_id"] = f"AC-STUCK-{index}"
            stuck["billing_ticket_id"] = f"AC-STUCK-{index}"
            stuck["created_at"] = "2026-09-01T00:00:00Z"
            stuck["updated_at"] = f"2026-09-01T00:{index:02d}:00Z"
            stuck["internal_email_send_status"] = "awaiting_public_reply"
            stuck["internal_email_payload"] = _email_payload()
            stuck["automation_context"] = {
                "enablement_manual_workflow": {
                    "version": 1,
                    "state": "awaiting_public_reply",
                    "reply_job_id": f"job-stuck-{index}",
                    "delivery_key": "enablement:AC-MANUAL-1:v1",
                }
            }
            repository.save_account_case(stuck)
        ready = _base_case(ticket_id="TK-READY")
        ready["account_case_id"] = "AC-READY"
        ready["billing_ticket_id"] = "AC-READY"
        ready["created_at"] = "2026-08-31T00:00:00Z"
        ready["updated_at"] = "2026-08-31T00:00:00Z"
        ready["internal_email_send_status"] = "awaiting_public_reply"
        ready["internal_email_payload"] = _email_payload()
        ready["automation_context"] = {
            "enablement_manual_workflow": {
                "version": 1,
                "state": "awaiting_public_reply",
                "reply_job_id": "job-ready",
                "delivery_key": "enablement:AC-MANUAL-1:v1",
            }
        }
        repository.save_ticket(
            {
                "ticket_id": "TK-READY",
                "status": "open",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "We received your request.",
                        "message_id": "assistant-ready",
                        "meta": {"account_reply_job_id": "job-ready"},
                    }
                ],
            }
        )
        repository.create_account_zendesk_comment_delivery(
            account_case_id="AC-READY",
            message_id="assistant-ready",
            zendesk_ticket_id="TK-READY",
            idempotency_key="zd-ready",
            created_at="2026-09-10T00:01:00Z",
            is_public=True,
        )
        repository.begin_idempotent_request(
            "account_zendesk_internal_comment",
            "zd-ready",
            created_at="2026-09-10T00:01:01Z",
        )
        repository.complete_account_zendesk_comment_delivery(
            account_case_id="AC-READY",
            message_id="assistant-ready",
            status="delivered",
            zendesk_comment_id="zc-ready",
            failure_code=None,
            completed_at="2026-09-10T00:01:30Z",
        )
        repository.save_account_case(ready)

        releasable = repository.list_enablement_cases_by_email_status(
            ("awaiting_public_reply",),
            processing_profile="production",
            limit=25,
            workflow_states=("awaiting_public_reply",),
            require_delivered_confirmation=True,
        )
        self.assertEqual([case["account_case_id"] for case in releasable], ["AC-READY"])
        with patch.object(worker_module, "ticket_repository", repository), patch.object(
            worker_module,
            "_send_claimed_enablement_delivery",
            return_value={"status": "skipped", "reason": "isolated", "claimed": False},
        ):
            counts = worker_module._drain_enablement_manual_review_emails(
                limit=25, processing_profile="production"
            )
        self.assertEqual(counts["released"], 1)
        self.assertEqual(counts["still_gated"], 25)
        stored = repository.get_account_case("AC-READY")
        self.assertEqual(stored["internal_email_send_status"], "pending")
