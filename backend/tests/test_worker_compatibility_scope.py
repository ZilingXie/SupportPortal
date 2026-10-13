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

    def test_relay_release_once_with_real_repository_state(self):
        # Real InMemory state (plan finding 3): two replays must leave
        # exactly ONE projected message, the internal email released once
        # (terminal status absorbs the second call), and the case never
        # blocked.
        from backend.repositories.ticket_repository import InMemoryTicketRepository
        from backend.services.account_automation_delivery import (
            prepare_account_internal_email,
        )

        repository = InMemoryTicketRepository()
        repository.save_ticket(
            {
                "ticket_id": "TK-COMPAT",
                "customer_id": "cx@example.com",
                "requester": "cx@example.com",
                "subject": "Enable media relay",
                "status": "open",
                "created_at": "2026-10-13T10:00:00Z",
                "updated_at": "2026-10-13T10:00:00Z",
                "messages": [],
            },
            new_messages=[],
        )
        repository.save_account_case(
            {
                "account_case_id": "AC-COMPAT",
                "billing_ticket_id": "AC-COMPAT",
                "client_ticket_id": "TK-COMPAT",
                "zendesk_ticket_id": "TK-COMPAT",
                "processing_profile": "preproduction",
                "automation_status": "automation",
                "route": "enablement",
                "execution_action": "enablement",
                "automation_handler": "enablement",
                "automation_context": {
                    "enablement_manual_workflow": {
                        "version": 1,
                        "state": "awaiting_public_reply",
                        "reply_job_id": "job-1",
                    }
                },
                "internal_email_payload": {
                    "delivery_key": "enablement:AC-COMPAT:v1",
                    "action": "enablement",
                },
                "internal_email_send_status": "awaiting_public_reply",
                "collected_fields": {"app_id": "8cb7aea984c4457daad802e6960e2475"},
            }
        )
        repository.save_account_reply_job(
            {
                "job_id": "job-1",
                "ticket_id": "TK-COMPAT",
                "trigger_message_created_at": "2026-10-13T10:00:00Z",
                "status": "published",
                "scheduled_for": "2026-10-13T10:00:30Z",
                "payload": {"reply_intent": "submission_confirmation"},
                "attempt_count": 0,
                "created_at": "2026-10-13T10:00:05Z",
                "updated_at": "2026-10-13T10:00:05Z",
            }
        )
        # The release twin requires a delivered public comment row bound to
        # the projected message — create one for the job's message.
        repository.create_account_zendesk_comment_delivery(
            account_case_id="AC-COMPAT",
            message_id="draft-1",
            zendesk_ticket_id="TK-COMPAT",
            idempotency_key="zd-compat-1",
            created_at="2026-10-13T10:05:00Z",
            is_public=True,
        )
        repository.complete_account_zendesk_comment_delivery(
            account_case_id="AC-COMPAT",
            message_id="draft-1",
            status="delivered",
            zendesk_comment_id="544000000000001",
            failure_code=None,
            completed_at="2026-10-13T10:05:30Z",
        )
        delivery = _delivery(
            message_id="draft-1",
            attachments=[
                {"_hermes_compatibility_relay_required": True},
                {"_hermes_compatibility_job_id": "job-1"},
            ],
        )
        with patch.object(worker, "ticket_repository", repository):
            worker._complete_hermes_customer_reply_side_effects(delivery)
            worker._complete_hermes_customer_reply_side_effects(delivery)

        saved_case = repository.get_account_case("AC-COMPAT")
        self.assertNotEqual(
            saved_case.get("automation_status"), "human_review_required"
        )
        # Exactly one projected compatibility message.
        ticket = repository.get_ticket("TK-COMPAT")
        projected = [
            m
            for m in ticket.get("messages") or []
            if (m.get("meta") or {}).get("account_reply_job_id") == "job-1"
        ]
        self.assertEqual(len(projected), 1)
        # The internal email release is terminal: status moved off the
        # awaiting gate and stays there after the second replay.
        self.assertNotEqual(
            saved_case.get("internal_email_send_status"), "awaiting_public_reply"
        )


if __name__ == "__main__":
    unittest.main()


class SidPreciseSeedV2Tests(unittest.TestCase):
    """Plan finding 4: the warmed sid-precise instruction must actually
    reach deployed personas — the seed marker bump (v1 -> v2) makes the
    registry seeding publish a new version instead of skipping."""

    def test_seed_marker_v2_publishes_warmed_instruction(self):
        from backend.services.account_admin import ACCOUNT_PERSONA_PRESETS

        sid = [p for p in ACCOUNT_PERSONA_PRESETS if p.persona_key == "sid-precise"][0]
        self.assertEqual(sid.seed_marker, "Seeded Sid Precise preset v2")
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
