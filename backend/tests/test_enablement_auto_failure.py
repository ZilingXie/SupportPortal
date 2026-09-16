"""Enablement auto (relay) failure-chain tests (p2-163).

The auto workflow routes every failure into the unified automation failure
chain: reconcile -> human review escalation (internal note, ownership
release, route-back) -> idempotent owner alert email.  These tests run that
chain for real (only external boundaries are replaced) and pin the p2-163
contract additions: incidents are per-request, the alert is idempotent per
incident, and the failure path never prepares or sends a manual enablement
email.
"""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import backend.services.automation_account_intake as intake_module
import backend.tests.test_enablement_auto_relay as relay_helpers
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_failure_alerts import notify_account_failure

WORKER = relay_helpers.WORKER
RELAY_ENV = relay_helpers.RELAY_ENV


def _dispatched_request(repository: InMemoryTicketRepository) -> dict:
    case = relay_helpers._seed_auto_case(repository)
    request_id = relay_helpers._seed_gated_request(repository, case)
    repository._enablement_relay_requests[request_id]["status"] = "dispatch_pending"
    repository.claim_enablement_relay_dispatch(
        request_id=request_id,
        lease_token="lease-1",
        lease_seconds=120,
        now="2026-09-15T23:59:00+00:00",
    )
    repository.complete_enablement_relay_dispatch(
        request_id=request_id,
        relay_task_id="task-1",
        relay_task_expires_at="2026-09-30T00:00:00+00:00",
        now="2026-09-15T23:59:05+00:00",
    )
    return repository.get_enablement_relay_request(request_id)


class RelayFailureChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.request = _dispatched_request(self.repository)
        self.mail = Mock()
        self.note = Mock(return_value=("sent", "note-1", None))
        self.queue = Mock(return_value=type("NS", (), {"status": "queued"})())
        self.prepare = Mock(return_value=True)
        WORKER._ENABLEMENT_RELAY_LISTENER.update(
            {"instance_id": "", "epoch": 0, "published_at": 0.0}
        )

    def _patches(self):
        return [
            patch.dict("os.environ", RELAY_ENV, clear=False),
            patch.object(WORKER, "ticket_repository", self.repository),
            patch.object(
                intake_module,
                "notify_account_failure",
                lambda **kw: notify_account_failure(**kw, mail_sender=self.mail),
            ),
            patch(
                "backend.services.account_human_review_escalation._deliver_internal_note",
                self.note,
            ),
            patch(
                "backend.services.account_human_review_escalation.route_ticket_back_to_queue",
                self.queue,
            ),
            patch(
                "backend.services.account_automation_delivery.prepare_account_internal_email",
                self.prepare,
            ),
        ]

    def _run_failure(self, reason_code: str = "relay_config_mismatch"):
        request = self.repository.get_enablement_relay_request(self.request["request_id"])
        patches = self._patches()
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        WORKER._record_enablement_relay_failure(
            request,
            reason_code=reason_code,
            detail="synthetic relay failure detail",
        )

    def test_failure_alerts_owner_and_hands_off_without_manual_email(self):
        self._run_failure()
        saved = self.repository.get_account_case(self.request["account_case_id"])
        self.assertEqual(saved["automation_status"], "human_review_required")
        self.assertIn("relay_config_mismatch", str(saved["execution_reason_code"]))
        # One note, one route-back, one owner alert — and no manual email.
        self.assertEqual(self.note.call_count, 1)
        self.assertEqual(self.queue.call_count, 1)
        self.assertEqual(self.mail.call_count, 1)
        self.assertIn("relay", str(self.mail.call_args.kwargs.get("subject") or "").lower())
        self.prepare.assert_not_called()
        escalation = saved["automation_context"]["human_review_escalation"]
        self.assertEqual(escalation["internal_note_status"], "sent")
        self.assertEqual(escalation["handoff_status"], "queued")
        request = self.repository.get_enablement_relay_request(self.request["request_id"])
        self.assertEqual(request["status"], "failed")
        events = [item["event_type"] for item in self.repository._events]
        self.assertIn("enablement_relay_failure", events)

    def test_same_request_failure_never_realerts(self):
        self._run_failure()
        first_alerts = self.mail.call_count
        # A duplicate failure for the SAME request is terminal-skipped before
        # the chain runs, so neither note nor alert fires again.
        self.note.reset_mock()
        self._run_failure()
        self.assertEqual(self.mail.call_count, first_alerts)
        self.assertEqual(self.note.call_count, 0)

    def test_different_request_gets_distinct_incident(self):
        self._run_failure()
        first_alerts = self.mail.call_count
        # A second application on another case fails independently.
        other_case = relay_helpers._seed_auto_case(self.repository)
        other_case["account_case_id"] = "AC-RELAY-2"
        other_case["billing_ticket_id"] = "AC-RELAY-2"
        self.repository.save_account_case(other_case)
        other_request_id = relay_helpers._seed_gated_request(self.repository, other_case)
        self.repository._enablement_relay_requests[other_request_id]["status"] = (
            "dispatched"
        )
        other = self.repository.get_enablement_relay_request(other_request_id)
        WORKER._record_enablement_relay_failure(
            other,
            reason_code="relay_config_mismatch",
            detail="second synthetic failure",
        )
        self.assertEqual(self.mail.call_count, first_alerts + 1)

    def test_unknown_outcome_note_demands_verification_first(self):
        captured = {}

        def note_side_effect(*_args, **kwargs):
            captured["body"] = str(kwargs.get("body") or kwargs)
            return ("sent", "note-x", None)

        self.note.side_effect = note_side_effect
        self._run_failure(reason_code="relay_outcome_unknown")
        request = self.repository.get_enablement_relay_request(self.request["request_id"])
        self.assertEqual(request["status"], "failed")
        events = [item["event_type"] for item in self.repository._events]
        self.assertIn("enablement_relay_failure", events)


if __name__ == "__main__":
    unittest.main()
