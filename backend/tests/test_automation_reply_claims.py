from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from backend.repositories.ticket_repository import (
    ACCOUNT_RERUN_RESET_AI_ONLY,
    ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
    InMemoryTicketRepository,
)


class AutomationReplyClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.save_ticket({
            "ticket_id": "12555", "customer_id": "customer-1", "requester": "Customer",
            "subject": "Invoice", "status": "open", "messages": [],
            "created_at": "2026-08-05T00:00:00+00:00", "updated_at": "2026-08-05T00:00:00+00:00",
        })
        self.repository.save_account_case({
            "account_case_id": "AC-12555", "billing_ticket_id": "AC-12555",
            "client_ticket_id": "12555", "source": "zendesk", "title": "Invoice",
            "question": "Please check", "automation_status": "internal_processing",
        })

    def test_active_lease_blocks_second_owner_and_stale_owner_cannot_commit(self) -> None:
        now = datetime(2026, 8, 5, tzinfo=timezone.utc)
        first = self.repository.claim_automation_reply(
            "graph:message-1", client_ticket_id="12555", handler="billing", owner_token="owner-1",
            claimed_at=now.isoformat(), lease_expires_at=(now + timedelta(minutes=15)).isoformat(),
        )
        blocked = self.repository.claim_automation_reply(
            "graph:message-1", client_ticket_id="12555", handler="billing", owner_token="owner-2",
            claimed_at=(now + timedelta(minutes=1)).isoformat(),
            lease_expires_at=(now + timedelta(minutes=16)).isoformat(),
        )
        reclaimed = self.repository.claim_automation_reply(
            "graph:message-1", client_ticket_id="12555", handler="billing", owner_token="owner-3",
            claimed_at=(now + timedelta(minutes=16)).isoformat(),
            lease_expires_at=(now + timedelta(minutes=31)).isoformat(),
        )

        self.assertEqual(first["status"], "acquired")
        self.assertEqual(blocked["status"], "in_progress")
        self.assertEqual(reclaimed["status"], "acquired")
        self.assertEqual(reclaimed["attempt_count"], 2)
        self.assertFalse(self.repository.commit_automation_reply_result(
            "graph:message-1", owner_token="owner-1", ticket_id="12555",
            assistant_message={"role": "assistant", "content": "old", "created_at": now.isoformat()},
            account_case_updates={}, events=[], completed_at=now.isoformat(),
        ))

        completed_at = (now + timedelta(minutes=17)).isoformat()
        self.assertTrue(self.repository.commit_automation_reply_result(
            "graph:message-1", owner_token="owner-3", ticket_id="12555",
            assistant_message={"role": "assistant", "content": "one reply", "created_at": completed_at},
            account_case_updates={"automation_status": "customer_notified"},
            events=[{"event_type": "billing_customer_followup_generated", "payload": {"created_at": completed_at}}],
            completed_at=completed_at,
        ))
        self.assertEqual(len(self.repository.get_ticket("12555")["messages"]), 1)
        self.assertEqual(self.repository.claim_automation_reply(
            "graph:message-1", client_ticket_id="12555", handler="billing", owner_token="owner-4",
            claimed_at=(now + timedelta(hours=1)).isoformat(),
            lease_expires_at=(now + timedelta(hours=1, minutes=15)).isoformat(),
        )["status"], "already_completed")

    def test_failed_claim_is_reclaimable_and_error_is_sanitized(self) -> None:
        now = datetime(2026, 8, 5, tzinfo=timezone.utc).isoformat()
        self.repository.claim_automation_reply(
            "graph:message-2", client_ticket_id="12555", handler="enablement", owner_token="owner-1",
            claimed_at=now, lease_expires_at="2026-08-05T00:15:00+00:00",
        )
        self.assertTrue(self.repository.fail_automation_reply_claim(
            "graph:message-2", owner_token="owner-1", error_code="ValueError", failed_at=now,
        ))
        reclaimed = self.repository.claim_automation_reply(
            "graph:message-2", client_ticket_id="12555", handler="enablement", owner_token="owner-2",
            claimed_at=now, lease_expires_at="2026-08-05T00:15:00+00:00",
        )
        self.assertEqual(reclaimed["status"], "acquired")
        self.assertEqual(reclaimed["attempt_count"], 2)

    def test_full_reset_invalidates_processing_claim_before_stale_commit(self) -> None:
        now = "2026-08-05T00:00:00+00:00"
        self.repository.claim_automation_reply(
            "graph:message-reset",
            client_ticket_id="12555",
            handler="billing",
            owner_token="owner-reset",
            claimed_at=now,
            lease_expires_at="2026-08-05T00:15:00+00:00",
        )

        self.repository.reset_account_rerun_state(
            "12555",
            reset_at="2026-08-05T00:01:00+00:00",
            rerun_job_id="account-rerun-reset-claim",
            reset_mode=ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
            clear_persona_assignment=True,
        )
        committed = self.repository.commit_automation_reply_result(
            "graph:message-reset",
            owner_token="owner-reset",
            ticket_id="12555",
            assistant_message={
                "role": "assistant",
                "content": "stale reply",
                "created_at": "2026-08-05T00:02:00+00:00",
            },
            account_case_updates={"automation_status": "customer_notified"},
            events=[
                {
                    "event_type": "billing_customer_followup_generated",
                    "payload": {"created_at": "2026-08-05T00:02:00+00:00"},
                }
            ],
            completed_at="2026-08-05T00:02:00+00:00",
        )

        self.assertFalse(committed)
        self.assertEqual(self.repository.get_ticket("12555")["messages"], [])
        account_case = self.repository.get_account_case_by_ticket_id("12555")
        assert account_case is not None
        self.assertNotEqual(account_case.get("automation_status"), "customer_notified")
        self.assertEqual(self.repository.list_ticket_events("12555"), [])

    def test_clear_persona_ai_only_reset_invalidates_claim_and_legacy_token(self) -> None:
        self.repository.claim_automation_reply(
            "graph:message-ai-only-reset",
            client_ticket_id="12555",
            handler="billing",
            owner_token="owner-ai-only-reset",
            claimed_at="2026-08-05T00:00:00+00:00",
            lease_expires_at="2026-08-05T00:15:00+00:00",
        )
        self.repository.save_billing_response_token(
            {
                "token_hash": "legacy-token-ai-only-reset",
                "billing_ticket_id": "AC-12555",
                "created_at": "2026-08-05T00:00:00+00:00",
                "used_at": None,
            }
        )

        self.repository.reset_account_rerun_state(
            "12555",
            reset_at="2026-08-05T00:01:00+00:00",
            reset_mode=ACCOUNT_RERUN_RESET_AI_ONLY,
            clear_persona_assignment=True,
        )

        self.assertFalse(
            self.repository.commit_automation_reply_result(
                "graph:message-ai-only-reset",
                owner_token="owner-ai-only-reset",
                ticket_id="12555",
                assistant_message=None,
                account_case_updates={},
                events=[],
                completed_at="2026-08-05T00:02:00+00:00",
            )
        )
        self.assertFalse(
            self.repository.commit_billing_response_submission(
                "legacy-token-ai-only-reset",
                billing_ticket_id="AC-12555",
                ticket_id="12555",
                assistant_message=None,
                account_case_updates={},
                events=[],
                cancel_pending_reply_jobs=False,
                completed_at="2026-08-05T00:02:00+00:00",
            )
        )
        self.assertIsNone(
            self.repository.get_billing_response_token("legacy-token-ai-only-reset")
        )

    def test_reply_only_ai_reset_preserves_claim_and_legacy_token(self) -> None:
        self.repository.claim_automation_reply(
            "graph:message-reply-only-reset",
            client_ticket_id="12555",
            handler="billing",
            owner_token="owner-reply-only-reset",
            claimed_at="2026-08-05T00:00:00+00:00",
            lease_expires_at="2026-08-05T00:15:00+00:00",
        )
        self.repository.save_billing_response_token(
            {
                "token_hash": "legacy-token-reply-only-reset",
                "billing_ticket_id": "AC-12555",
                "created_at": "2026-08-05T00:00:00+00:00",
                "used_at": None,
            }
        )

        self.repository.reset_account_rerun_state(
            "12555",
            reset_at="2026-08-05T00:01:00+00:00",
            reset_mode=ACCOUNT_RERUN_RESET_AI_ONLY,
            clear_persona_assignment=False,
        )

        self.assertTrue(
            self.repository.commit_automation_reply_result(
                "graph:message-reply-only-reset",
                owner_token="owner-reply-only-reset",
                ticket_id="12555",
                assistant_message=None,
                account_case_updates={},
                events=[],
                completed_at="2026-08-05T00:02:00+00:00",
            )
        )
        self.assertTrue(
            self.repository.commit_billing_response_submission(
                "legacy-token-reply-only-reset",
                billing_ticket_id="AC-12555",
                ticket_id="12555",
                assistant_message=None,
                account_case_updates={},
                events=[],
                cancel_pending_reply_jobs=False,
                completed_at="2026-08-05T00:02:00+00:00",
            )
        )

    def test_in_memory_claim_waits_for_reset_atomic_section(self) -> None:
        reset_holds_lock = threading.Event()
        release_reset = threading.Event()
        original_get_case = self.repository.get_billing_ticket_by_client_ticket_id

        def get_case_while_reset_holds_lock(ticket_id: str):
            reset_holds_lock.set()
            if not release_reset.wait(timeout=5):
                raise TimeoutError("test did not release reset")
            return original_get_case(ticket_id)

        with ThreadPoolExecutor(max_workers=2) as executor, patch.object(
            self.repository,
            "get_billing_ticket_by_client_ticket_id",
            side_effect=get_case_while_reset_holds_lock,
        ):
            reset_future = executor.submit(
                self.repository.reset_account_rerun_state,
                "12555",
                reset_at="2026-08-05T00:01:00+00:00",
                reset_mode=ACCOUNT_RERUN_RESET_AI_ONLY,
                clear_persona_assignment=True,
            )
            self.assertTrue(reset_holds_lock.wait(timeout=5))
            claim_future = executor.submit(
                self.repository.claim_automation_reply,
                "graph:message-after-reset",
                client_ticket_id="12555",
                handler="billing",
                owner_token="owner-after-reset",
                claimed_at="2026-08-05T00:02:00+00:00",
                lease_expires_at="2026-08-05T00:17:00+00:00",
            )
            self.assertFalse(claim_future.done())
            release_reset.set()
            reset_future.result(timeout=5)
            claim = claim_future.result(timeout=5)

        self.assertEqual(claim["status"], "acquired")
        self.assertTrue(
            self.repository.commit_automation_reply_result(
                "graph:message-after-reset",
                owner_token="owner-after-reset",
                ticket_id="12555",
                assistant_message=None,
                account_case_updates={},
                events=[],
                completed_at="2026-08-05T00:03:00+00:00",
            )
        )

    def test_schema_documents_claim_backfill_and_unique_guards(self) -> None:
        schema = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        migration = Path("backend/sql/migrations/2026_08_05_automation_reply_claims.sql").read_text(encoding="utf-8")
        for text in (schema, migration):
            self.assertIn("support_automation_reply_claims", text)
            self.assertIn("idx_support_ticket_messages_automation_reply_key", text)
            self.assertIn("idx_support_ticket_events_automation_reply_key_event", text)
        self.assertIn("automation_reply_message_id", migration)
        self.assertIn("ON CONFLICT (automation_reply_key) DO NOTHING", migration)


if __name__ == "__main__":
    unittest.main()
