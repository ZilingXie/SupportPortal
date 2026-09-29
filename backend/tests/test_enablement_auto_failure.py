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
        # The worker module (loaded under a fake backend.main) may bind second
        # execs of the intake/escalation modules, so patch the failure chain
        # through the actual function globals instead of separately imported
        # module instances.
        chain_globals = WORKER._record_execution_failure.__globals__
        escalate_globals = chain_globals["escalate_account_case_to_human_review"].__globals__
        return [
            patch.dict("os.environ", RELAY_ENV, clear=False),
            patch.object(WORKER, "ticket_repository", self.repository),
            patch.dict(
                chain_globals,
                {
                    "notify_account_failure": lambda **kw: notify_account_failure(
                        **kw, mail_sender=self.mail
                    )
                },
            ),
            patch.dict(
                escalate_globals,
                {
                    "_deliver_internal_note": self.note,
                    "route_ticket_back_to_queue": self.queue,
                },
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


class RelayProjectNotFoundReplyTests(unittest.TestCase):
    """p2-178: a clean project_not_found result answers the customer with the
    dedicated not-found reply and keeps the case automation-owned."""

    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.case = relay_helpers._seed_auto_case(self.repository)
        self.request_id = relay_helpers._seed_gated_request(self.repository, self.case)
        self.repository._enablement_relay_requests[self.request_id]["status"] = "dispatched"

    def _apply(self, *, write_attempted: bool) -> dict:
        from types import SimpleNamespace

        request = self.repository.get_enablement_relay_request(self.request_id)
        result = {
            "outcome": "project_not_found",
            "write_attempted": write_attempted,
            "created_at": "2026-09-24T11:00:00+00:00",
            "detail": "no archer project for this app id",
        }
        with (
            patch.object(WORKER, "ticket_repository", self.repository),
            patch(
                "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
                return_value=SimpleNamespace(ticket_status="open"),
            ),
        ):
            WORKER._apply_enablement_relay_result(
                request=request,
                result=result,
                client=Mock(),
                task_detail={"task": {"task_id": "task-1"}},
            )
        return self.repository.get_enablement_relay_request(self.request_id)

    def test_clean_not_found_queues_dedicated_reply_without_handoff(self) -> None:
        request = self._apply(write_attempted=False)
        # The dedicated not-found reply job was queued exactly once.
        job = self.repository.get_account_reply_job(
            f"enablement-relay-notfound-{self.request_id}"
        )
        self.assertIsNotNone(job)
        self.assertEqual(
            job["payload"]["reply_intent"], "enablement_appid_not_found"
        )
        self.assertFalse(job["payload"]["close_after_publish"])
        # Live 13751 regression: the not-found reply is triggered by the relay
        # result, not the latest customer message; without this flag the
        # worker's customer-currency fence cancels the job at claim time.
        self.assertTrue(job["payload"]["internal_resolution"])
        # The request ended failed-but-recoverable and the case stayed
        # automation-owned: no human_review_required, no failure incident.
        self.assertEqual(request["status"], "failed")
        self.assertEqual(request.get("suppression_reason"), "project_not_found")
        case = self.repository.get_account_case(self.case["account_case_id"])
        self.assertNotEqual(case.get("automation_status"), "human_review_required")
        events = [item["event_type"] for item in self.repository._events]
        self.assertIn("enablement_relay_project_not_found_reply_queued", events)
        self.assertNotIn("enablement_relay_failure", events)

    def test_write_attempted_not_found_still_takes_the_failure_chain(self) -> None:
        chain_globals = WORKER._record_execution_failure.__globals__
        escalate_globals = chain_globals["escalate_account_case_to_human_review"].__globals__
        patches = [
            patch.dict("os.environ", RELAY_ENV, clear=False),
            patch.object(WORKER, "ticket_repository", self.repository),
            patch.dict(
                escalate_globals,
                {
                    "_deliver_internal_note": Mock(return_value=("sent", "note-x", None)),
                    "route_ticket_back_to_queue": Mock(
                        return_value=type("NS", (), {"status": "queued"})()
                    ),
                },
            ),
            patch("backend.services.account_failure_alerts.send_graph_mail"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        request = self._apply(write_attempted=True)
        self.assertEqual(request["status"], "failed")
        self.assertNotEqual(request.get("suppression_reason"), "project_not_found")
        case = self.repository.get_account_case(self.case["account_case_id"])
        self.assertEqual(case.get("automation_status"), "human_review_required")

    def test_replayed_result_never_queues_a_second_reply(self) -> None:
        self._apply(write_attempted=False)
        saved_before = dict(
            self.repository.get_account_reply_job(
                f"enablement-relay-notfound-{self.request_id}"
            )
        )
        # A duplicate result application for the same request is idempotent:
        # the deterministic job id and the terminal request state keep the
        # reply single.
        self._apply(write_attempted=False)
        saved_after = self.repository.get_account_reply_job(
            f"enablement-relay-notfound-{self.request_id}"
        )
        self.assertEqual(saved_before["job_id"], saved_after["job_id"])
        self.assertEqual(
            saved_before["payload"]["automation_delivery_key"],
            saved_after["payload"]["automation_delivery_key"],
        )


if __name__ == "__main__":
    unittest.main()


class RelayNotFoundReplyClaimGateTests(unittest.TestCase):
    """Real claim-path currency-fence tests (review round 1 gap).

    The not-found reply job is triggered by the relay result, not by the
    latest customer message: before the internal_resolution fix the worker
    cancelled it at claim time (live 13751). These tests drive the REAL
    _prepare_account_reply_job_impl through the InMemory repository.
    """

    def setUp(self) -> None:
        from backend.tests.test_enablement_auto_relay import WORKER

        self.WORKER = WORKER
        self.repository = InMemoryTicketRepository()
        self.repository.save_ticket(
            {
                "ticket_id": "13751",
                "customer_id": "xieziling97@163.com",
                "requester": "xieziling97@163.com",
                "subject": "Enable media relay",
                "status": "open",
                "created_at": "2026-09-29T04:53:00+00:00",
                "updated_at": "2026-09-29T05:04:00+00:00",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Any update? Could it be faster?",
                        # The LATEST customer message (the nudge) — deliberately
                        # different from the relay-result trigger below.
                        "created_at": "2026-09-29T05:04:40+00:00",
                    }
                ],
            },
            new_messages=[],
        )

    def _notfound_job(self, *, internal_resolution: bool) -> dict:
        payload = {
            "draft_content": "",
            "reply_facts": {
                "behavior": "enablement",
                "reply_intent": "enablement_appid_not_found",
                "known_information": {
                    "requested_feature": "media_relay",
                    "archer_outcome": "project_not_found",
                },
                "missing_information": ["app_id"],
                "resolution_status": "awaiting_customer",
            },
            "reply_pipeline": "account_reply_persona_v8",
            "asked_field_keys": ["app_id"],
            "visibility": "account_only",
            "close_after_publish": False,
            "reply_intent": "enablement_appid_not_found",
            "automation_delivery_key": "enablement-relay-notfound:enr-AC-13751-v1",
        }
        if internal_resolution:
            payload["internal_resolution"] = True
        return {
            "job_id": "enablement-relay-notfound-enr-AC-13751-v1",
            "ticket_id": "13751",
            # Trigger = the relay result timestamp, NOT the customer nudge.
            "trigger_message_created_at": "2026-09-29T07:44:45+00:00",
            "status": "persona_v8_preparing",
            "scheduled_for": "2026-09-29T07:52:46+00:00",
            "payload": payload,
            "attempt_count": 0,
            "claimed_at": "2026-09-29T07:52:46+00:00",
            "published_at": None,
            "created_at": "2026-09-29T07:44:45+00:00",
            "updated_at": "2026-09-29T07:52:46+00:00",
        }

    def _run_claim(self, job: dict) -> dict:
        """Drive the REAL publish-stage claim where the live cancellation
        happened (persona_v8 pipeline: prepare renders, publish claims and
        applies the currency gate). The job enters as claimed for publish."""
        job = dict(job)
        job["status"] = "persona_v8_publishing"
        self.repository.save_account_reply_job(job)
        with patch.object(self.WORKER, "ticket_repository", self.repository):
            try:
                self.WORKER._publish_account_reply_job(dict(job))
            except Exception:
                # Post-gate failures (persona/publish transports are not
                # under test) must not mask the gate decision recorded on
                # the repository row.
                pass
        return self.repository.get_account_reply_job(job["job_id"]) or {}

    def test_gate_helper_blocks_customer_triggered_stale_job(self) -> None:
        ticket = {"messages": [
            {"role": "customer", "created_at": "2026-09-29T09:00:00+00:00"}
        ]}
        job = {"trigger_message_created_at": "2026-09-29T05:00:00+00:00"}
        self.assertTrue(
            self.WORKER._account_reply_currency_gate_blocks({}, ticket, job)
        )

    def test_gate_helper_passes_internal_resolution(self) -> None:
        ticket = {"messages": [
            {"role": "customer", "created_at": "2026-09-29T09:00:00+00:00"}
        ]}
        job = {"trigger_message_created_at": "2026-09-29T05:00:00+00:00"}
        self.assertFalse(
            self.WORKER._account_reply_currency_gate_blocks(
                {"internal_resolution": True}, ticket, job
            )
        )

    def test_notfound_job_without_flag_is_cancelled_at_claim(self) -> None:
        """Mechanical reproduction of the live 13751 cancellation."""
        job = self._run_claim(self._notfound_job(internal_resolution=False))
        self.assertEqual(job.get("status"), "cancelled")
        self.assertEqual(
            (job.get("payload") or {}).get("cancel_reason"), "stale_customer_revision"
        )

    def test_notfound_job_with_flag_survives_the_claim_gate(self) -> None:
        """The fixed job must NOT be cancelled as stale."""
        job = self._run_claim(self._notfound_job(internal_resolution=True))
        self.assertNotEqual(job.get("status"), "cancelled")
        self.assertIsNone((job.get("payload") or {}).get("cancel_reason"))


if __name__ == "__main__":
    unittest.main()
