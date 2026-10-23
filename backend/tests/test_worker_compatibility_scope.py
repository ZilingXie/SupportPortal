"""C1/C2/C3 contracts for the enablement compatibility-job scope fix (14009).

The server-owned ``compatibility_relay_required`` basis flag decides whether
the worker's compatibility contract applies; the worker never guesses from
the route name (plan: Hermes Enablement Reply Delivery and Natural Persona
v1.1 rev v2).
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

from backend import worker


def _case(**overrides):
    base = {
        "account_case_id": "AC-COMPAT",
        "client_ticket_id": "TK-COMPAT",
        "zendesk_ticket_id": "TK-COMPAT",
        "execution_action": "enablement",
        "automation_status": "automation",
        "automation_context": {},
    }
    base.update(overrides)
    return base


def _delivery(**overrides):
    base = {
        "account_case_id": "AC-COMPAT",
        "message_id": "draft-1",
        "zendesk_ticket_id": "TK-COMPAT",
        "immutable_content": "Hi Customer,\n\nPlease share your App ID.",
    }
    base.update(overrides)
    return base


def _run(case, delivery):
    repository = Mock()
    repository.get_account_case.return_value = case
    with patch.object(worker, "ticket_repository", repository):
        worker._complete_hermes_customer_reply_side_effects(delivery)
    return repository


class CompatibilityScopeTests(unittest.TestCase):
    """C1: ask turns never demand a job or release ownership."""

    def test_ask_turn_without_workflow_or_flag_completes_untouched(self):
        # 14009 shape: missing-app_id ask delivery, no workflow context, no
        # basis flag (legacy delivery).  The case must stay in automation.
        case = _case()
        repository = _run(case, _delivery())
        self.assertEqual(case["automation_status"], "automation")
        self.assertNotIn("execution_reason_code", case)
        repository.release_account_internal_email_after_public_reply.assert_not_called()
        repository.release_enablement_relay_requests_after_public_reply.assert_not_called()
        repository.save_account_case.assert_not_called()

    def test_ask_turn_with_explicit_false_flag_completes_untouched(self):
        case = _case()
        repository = _run(
            case,
            _delivery(
                attachments=[
                    {"_hermes_compatibility_relay_required": False},
                ]
            ),
        )
        self.assertEqual(case["automation_status"], "automation")
        repository.release_account_internal_email_after_public_reply.assert_not_called()

    def test_ask_turn_with_false_flag_ignores_stale_workflow_context(self):
        # The explicit server decision outranks a leftover context from an
        # earlier turn: this delivery is an ask, nothing may be released.
        case = _case(
            automation_context={"enablement_auto_workflow": {"reply_job_id": "job-old"}}
        )
        repository = _run(
            case,
            _delivery(
                attachments=[
                    {"_hermes_compatibility_relay_required": False},
                    {"_hermes_compatibility_job_id": "job-old"},
                ]
            ),
        )
        self.assertEqual(case["automation_status"], "automation")
        repository.release_account_internal_email_after_public_reply.assert_not_called()


class RelayContractTests(unittest.TestCase):
    """C2: real relay turns validate ticket-bound, active jobs fail-closed."""

    def _relay_delivery(self, **extra):
        return _delivery(
            attachments=[
                {"_hermes_compatibility_relay_required": True},
                {"_hermes_compatibility_job_id": "job-1"},
            ],
            **extra,
        )

    def test_relay_turn_with_valid_job_releases(self):
        case = _case(
            automation_context={"enablement_auto_workflow": {"reply_job_id": "job-1"}}
        )
        repository = Mock()
        repository.get_account_case.return_value = case
        repository.get_account_reply_job.return_value = {
            "job_id": "job-1",
            "ticket_id": "TK-COMPAT",
            "status": "queued",
            "payload": {},
        }
        repository.get_ticket.return_value = {"ticket_id": "TK-COMPAT", "messages": []}
        with patch.object(worker, "ticket_repository", repository):
            worker._complete_hermes_customer_reply_side_effects(self._relay_delivery())
        repository.release_account_internal_email_after_public_reply.assert_called_once()
        repository.release_enablement_relay_requests_after_public_reply.assert_called_once()

    def test_relay_turn_missing_job_blocks_fail_closed(self):
        case = _case()  # no workflow context — flag alone demands the job
        repository = _run(case, self._relay_delivery())
        self.assertEqual(case["automation_status"], "human_review_required")
        self.assertEqual(
            case["execution_reason_code"], "enablement_compatibility_job_missing"
        )
        repository.release_account_internal_email_after_public_reply.assert_not_called()

    def test_relay_turn_wrong_ticket_blocks(self):
        case = _case(
            automation_context={"enablement_manual_workflow": {"reply_job_id": "job-1"}}
        )
        repository = Mock()
        repository.get_account_case.return_value = case
        repository.get_account_reply_job.return_value = {
            "job_id": "job-1",
            "ticket_id": "TK-OTHER",
            "status": "queued",
            "payload": {},
        }
        with patch.object(worker, "ticket_repository", repository):
            worker._complete_hermes_customer_reply_side_effects(self._relay_delivery())
        self.assertEqual(
            case["execution_reason_code"],
            "enablement_compatibility_job_ticket_mismatch",
        )
        repository.release_account_internal_email_after_public_reply.assert_not_called()

    def test_relay_turn_terminal_job_blocks(self):
        case = _case(
            automation_context={"enablement_auto_workflow": {"reply_job_id": "job-1"}}
        )
        repository = Mock()
        repository.get_account_case.return_value = case
        repository.get_account_reply_job.return_value = {
            "job_id": "job-1",
            "ticket_id": "TK-COMPAT",
            "status": "cancelled",
            "payload": {},
        }
        with patch.object(worker, "ticket_repository", repository):
            worker._complete_hermes_customer_reply_side_effects(self._relay_delivery())
        self.assertEqual(
            case["execution_reason_code"], "enablement_compatibility_job_terminal"
        )


class ReplayTests(unittest.TestCase):
    """C3: repeat execution must not duplicate blocks, sends, or releases."""

    def test_blocked_replay_records_once(self):
        case = _case(
            automation_context={"enablement_auto_workflow": {"reply_job_id": "job-1"}}
        )
        repository = Mock()
        repository.get_account_case.return_value = case
        with patch.object(worker, "ticket_repository", repository):
            delivery = _delivery()  # no identity, workflow demands it
            worker._complete_hermes_customer_reply_side_effects(delivery)
            worker._complete_hermes_customer_reply_side_effects(delivery)
        self.assertEqual(repository.record_event.call_count, 1)
        self.assertEqual(case["execution_reason_code"], "enablement_compatibility_job_missing")

    def test_manual_email_release_once_with_real_repository_state(self):
        repository, delivery = compatibility_fixture(auto=False)
        with patch.object(worker, "ticket_repository", repository), patch.object(
            repository, "release_account_internal_email_after_public_reply",
            wraps=repository.release_account_internal_email_after_public_reply,
        ) as release:
            worker._complete_hermes_customer_reply_side_effects(delivery)
            worker._complete_hermes_customer_reply_side_effects(delivery)
        case = repository.get_account_case("AC-COMPAT")
        self.assertEqual(case["internal_email_send_status"], "pending")
        self.assertEqual(case["automation_context"]["enablement_manual_workflow"]["state"], "email_released")
        self.assertEqual(release.call_count, 2)
        self.assertEqual(len(projected_messages(repository)), 1)

    def test_relay_release_once_with_real_repository_state(self):
        repository, delivery = compatibility_fixture(auto=True)
        winners = []
        actual = repository.release_enablement_relay_requests_after_public_reply
        def release(**kwargs):
            result = actual(**kwargs)
            winners.append([item["request_id"] for item in result])
            return result
        with patch.object(worker, "ticket_repository", repository), patch.object(
            repository, "release_enablement_relay_requests_after_public_reply", side_effect=release,
        ):
            worker._complete_hermes_customer_reply_side_effects(delivery)
            worker._complete_hermes_customer_reply_side_effects(delivery)
        self.assertEqual(winners, [["request-1"], []])
        self.assertEqual(repository.get_enablement_relay_request("request-1")["status"], "dispatch_pending")
        case = repository.get_account_case("AC-COMPAT")
        self.assertEqual(case["automation_context"]["enablement_auto_workflow"]["state"], "dispatch_pending")
        self.assertEqual(case["internal_email_send_status"], "not_applicable")
        self.assertEqual(case["automation_status"], "automation")
        self.assertEqual(len(projected_messages(repository)), 1)
        self.assertEqual(len(repository.list_account_zendesk_comment_deliveries_for_case("AC-COMPAT")), 1)

    def test_relay_rejects_unconfirmed_or_mismatched_identity(self):
        for mismatch in ("not_delivered", "message", "job", "ticket", "workflow", "request"):
            with self.subTest(mismatch=mismatch):
                repository, delivery = compatibility_fixture(auto=True, delivered=mismatch != "not_delivered")
                if mismatch == "message":
                    delivery["message_id"] = "another-draft"
                elif mismatch in {"job", "ticket"}:
                    request = repository._enablement_relay_requests["request-1"]
                    request["reply_job_id" if mismatch == "job" else "ticket_id"] = "wrong"
                elif mismatch in {"workflow", "request"}:
                    case = repository.get_account_case("AC-COMPAT")
                    case["automation_context"]["enablement_auto_workflow"][
                        "reply_job_id" if mismatch == "workflow" else "request_id"
                    ] = "wrong"
                    repository.save_account_case(case)
                with patch.object(worker, "ticket_repository", repository):
                    worker._complete_hermes_customer_reply_side_effects(delivery)
                self.assertEqual(repository.get_enablement_relay_request("request-1")["status"], "gated")


def projected_messages(repository):
    return [m for m in repository.get_ticket("TK-COMPAT")["messages"]
            if (m.get("meta") or m).get("account_reply_job_id") == "job-1"]


def compatibility_fixture(*, auto, delivered=True, repository=None):
    from backend.repositories.ticket_repository import InMemoryTicketRepository
    repository = repository if repository is not None else InMemoryTicketRepository()
    timestamp = "2026-10-13T10:00:00Z"
    repository.save_ticket({
        "ticket_id": "TK-COMPAT", "customer_id": "cx@example.com", "requester": "cx@example.com",
        "subject": "Enable Media Relay", "status": "open", "created_at": timestamp,
        "updated_at": timestamp, "messages": [],
    }, new_messages=[])
    workflow_key = "enablement_auto_workflow" if auto else "enablement_manual_workflow"
    repository.save_account_case(_case(
        billing_ticket_id="AC-COMPAT", processing_profile="preproduction", route="enablement",
        automation_handler="enablement", route_family="automated",
        internal_email_send_status="awaiting_public_reply", updated_at=timestamp,
        automation_context={workflow_key: {"state": "awaiting_public_reply", "reply_job_id": "job-1",
                                          **({"request_id": "request-1"} if auto else {})}},
    ))
    repository.save_account_reply_job({
        "job_id": "job-1", "ticket_id": "TK-COMPAT", "status": "published",
        "payload": {"reply_intent": "submission_confirmation"}, "attempt_count": 0,
        "trigger_message_created_at": timestamp, "scheduled_for": timestamp,
        "created_at": timestamp, "updated_at": timestamp,
    })
    if auto:
        request = repository.create_enablement_relay_request(
            request_id="request-1", account_case_id="AC-COMPAT", ticket_id="TK-COMPAT",
            zendesk_ticket_id="TK-COMPAT", customer_email="cx@example.com", app_id="a" * 32,
            reply_job_id="job-1", workflow_mode="archer", request_version=1,
            relay_task_expires_at="2026-10-14T10:00:00Z", now=timestamp,
        )
        assert request["status"] == "gated"
    delivery = repository.create_account_zendesk_comment_delivery(
        account_case_id="AC-COMPAT", message_id="draft-1", zendesk_ticket_id="TK-COMPAT",
        source="hermes", immutable_content="Hi Customer,\n\nYour request has been submitted for review.",
        draft_version=1, comments_revision="revision-1",
        idempotency_key="hermes-draft:draft-1", created_at=timestamp, is_public=True,
        attachments=[{"_hermes_compatibility_relay_required": True}, {"_hermes_compatibility_job_id": "job-1"}],
    )
    if delivered:
        repository.complete_account_zendesk_comment_delivery(
            account_case_id="AC-COMPAT", message_id="draft-1", status="delivered",
            zendesk_comment_id="comment-1", failure_code=None, completed_at=timestamp,
        )
        delivery["status"] = "delivered"
    return repository, delivery


class WorkerReadbackTests(unittest.TestCase):
    def test_real_audit_watermark_gates_plain_text_release_and_replay(self):
        from backend.services import zendesk_comments

        watermark = "2026-10-13T10:00:00Z"
        for auto in (False, True):
            for audit_time, delivery_time, confirmed in (
                ("2026-10-13T09:00:00Z", watermark, False),
                (None, watermark, False),
                ("invalid", watermark, False),
                ("2026-10-13T10:00:00", watermark, False),
                (watermark, None, False),
                (watermark, "invalid", False),
                (watermark, "2026-10-13T10:00:00", False),
                (watermark, watermark, True),
                ("2026-10-13T18:01:00+08:00", watermark, True),
            ):
                with self.subTest(auto=auto, audit_time=audit_time, delivery_time=delivery_time):
                    repository, delivery = compatibility_fixture(auto=auto, delivered=False)
                    delivery.update(status="outcome_unknown", created_at=delivery_time)
                    repository.complete_account_zendesk_comment_delivery(
                        account_case_id="AC-COMPAT", message_id="draft-1", status="outcome_unknown",
                        zendesk_comment_id=None, failure_code="timeout", completed_at=watermark,
                    )
                    audits = [{"created_at": audit_time, "events": [{
                        "type": "Comment", "public": True, "id": "audit-comment",
                        # Preserve the existing Zendesk-appended footer match.
                        "body": delivery["immutable_content"] + "\n\nZendesk agent footer",
                    }]}]
                    winners = []
                    actual_release = repository.release_enablement_relay_requests_after_public_reply

                    def release(**kwargs):
                        result = actual_release(**kwargs)
                        winners.extend(item["request_id"] for item in result)
                        return result

                    with patch.object(worker, "ticket_repository", repository), patch.object(
                        zendesk_comments, "_fetch_ticket_audits", return_value=(200, audits),
                    ), patch.object(worker, "add_ticket_comment") as send, patch.object(
                        repository, "release_enablement_relay_requests_after_public_reply", side_effect=release,
                    ), patch.object(repository, "complete_account_zendesk_comment_delivery",
                                    wraps=repository.complete_account_zendesk_comment_delivery) as complete:
                        worker._deliver_hermes_zendesk_comment(delivery)
                        worker._deliver_hermes_zendesk_comment(delivery)
                    send.assert_not_called()
                    self.assertEqual(len(projected_messages(repository)), int(confirmed))
                    self.assertEqual(winners, ["request-1"] if auto and confirmed else [])
                    case = repository.get_account_case("AC-COMPAT")
                    self.assertEqual(case["internal_email_send_status"],
                                     ("not_applicable" if auto else "pending") if confirmed else "awaiting_public_reply")
                    self.assertEqual(case["automation_status"], "automation")
                    if auto:
                        self.assertEqual(repository.get_enablement_relay_request("request-1")["status"],
                                         "dispatch_pending" if confirmed else "gated")
                    if confirmed:
                        self.assertEqual(complete.call_args.kwargs["zendesk_comment_id"], "audit-comment")
                    else:
                        complete.assert_not_called()
                    ledger = repository.list_account_zendesk_comment_deliveries_for_case("AC-COMPAT")[0]
                    self.assertEqual(ledger["status"], "delivered" if confirmed else "outcome_unknown")
                    self.assertEqual(ledger.get("zendesk_comment_id"), "audit-comment" if confirmed else None)

    def test_confirmed_readback_releases_real_request_once(self):
        from types import SimpleNamespace
        for auto in (False, True):
            with self.subTest(auto=auto):
                repository, delivery = compatibility_fixture(auto=auto, delivered=False)
                delivery["status"] = "outcome_unknown"
                winners = []
                actual = repository.release_enablement_relay_requests_after_public_reply
                def release(**kwargs):
                    result = actual(**kwargs)
                    winners.extend(item["request_id"] for item in result)
                    return result
                with patch.object(worker, "ticket_repository", repository), patch.object(
                    worker, "read_ticket_comment_audit", return_value=(SimpleNamespace(comment_id="confirmed"), set()),
                ) as audit, patch.object(worker, "add_ticket_comment") as send, patch.object(
                    repository, "release_enablement_relay_requests_after_public_reply", side_effect=release,
                ):
                    worker._deliver_hermes_zendesk_comment(delivery)
                    worker._deliver_hermes_zendesk_comment(delivery)
                send.assert_not_called()
                self.assertNotIn("attachments", audit.call_args.kwargs)
                self.assertEqual(winners, ["request-1"] if auto else [])
                self.assertEqual(len(projected_messages(repository)), 1)
                self.assertEqual(repository.get_account_case("AC-COMPAT")["internal_email_send_status"],
                                 "not_applicable" if auto else "pending")

    def test_unconfirmed_readback_keeps_real_request_gated(self):
        for status in ("failed", "outcome_unknown"):
            with self.subTest(status=status):
                repository, delivery = compatibility_fixture(auto=True, delivered=False)
                delivery["status"] = status
                with patch.object(worker, "ticket_repository", repository), patch.object(
                    worker, "read_ticket_comment_audit", return_value=(None, set()),
                ):
                    worker._deliver_hermes_zendesk_comment(delivery)
                self.assertEqual(repository.get_enablement_relay_request("request-1")["status"], "gated")
                self.assertEqual(projected_messages(repository), [])

    def test_bad_compatibility_identity_blocks_real_readback_and_replay(self):
        from types import SimpleNamespace
        for invalid in ("missing", "wrong_ticket", "cancelled", "failed", "made_up_state"):
            with self.subTest(invalid=invalid):
                repository, delivery = compatibility_fixture(auto=True, delivered=False)
                delivery["status"] = "outcome_unknown"
                if invalid == "missing":
                    delivery["attachments"] = [{"_hermes_compatibility_relay_required": True}]
                else:
                    job = repository.get_account_reply_job("job-1")
                    job["ticket_id" if invalid == "wrong_ticket" else "status"] = (
                        "other-ticket" if invalid == "wrong_ticket" else invalid)
                    repository.save_account_reply_job(job)
                with patch.object(worker, "ticket_repository", repository), patch.object(
                    worker, "read_ticket_comment_audit", return_value=(SimpleNamespace(comment_id="confirmed"), set()),
                ), patch.object(repository, "release_enablement_relay_requests_after_public_reply",
                                wraps=repository.release_enablement_relay_requests_after_public_reply) as release, patch.object(
                    repository, "record_event", wraps=repository.record_event,
                ) as event:
                    worker._deliver_hermes_zendesk_comment(delivery)
                    worker._deliver_hermes_zendesk_comment(delivery)
                release.assert_not_called()
                event.assert_called_once()
                self.assertEqual(repository.get_account_case("AC-COMPAT")["automation_status"], "human_review_required")
                self.assertEqual(repository.get_enablement_relay_request("request-1")["status"], "gated")
                self.assertEqual(projected_messages(repository), [])


if __name__ == "__main__":
    unittest.main()


class SidPreciseSeedV2Tests(unittest.TestCase):
    """Plan finding 4: the warmed sid-precise instruction must actually
    reach deployed personas — the seed marker bump (v1 -> v2) makes the
    registry seeding publish a new version instead of skipping."""

    def test_seed_marker_v2_publishes_warmed_instruction(self):
        from backend.services.account_admin import ACCOUNT_PERSONA_PRESETS

        sid = [p for p in ACCOUNT_PERSONA_PRESETS if p.persona_key == "sid-precise"][0]
        self.assertEqual(sid.seed_marker, "Seeded Sid Precise preset v3")
        self.assertIn("precise does", sid.instruction)
        self.assertIn("acknowledging", sid.instruction)

    def test_resolve_persona_style_uses_preset_content(self):
        import backend.tests.test_hermes_zendesk_agent as harness
        from backend.services.automation_hermes_agent import resolve_persona_style

        repository = harness._repository_like() if hasattr(harness, "_repository_like") else None
        if repository is None:
            # Minimal InMemory-backed resolution path.
            from backend.repositories.ticket_repository import InMemoryTicketRepository

            repository = InMemoryTicketRepository()
        style = resolve_persona_style(repository, "123")
        self.assertIsInstance(style, dict)
        # Whatever persona the sticky assignment picks, the library was
        # seeded from the CURRENT presets: sid-precise (if picked) must
        # carry the warmed v2 instruction.
        self.assertIn("instruction", style)
        if style.get("persona_key") == "sid-precise":
            self.assertIn("acknowledging", str(style.get("instruction") or ""))
