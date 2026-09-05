from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_reply_jobs import (
    AccountReplyContractError,
    ACCOUNT_REPLY_PERSONA_LEGACY_PIPELINE,
    ACCOUNT_REPLY_PERSONA_PIPELINE,
    ACCOUNT_REPLY_PERSONA_V8_PREPARING,
    ACCOUNT_REPLY_PERSONA_V8_QUEUED,
    ACCOUNT_REPLY_PERSONA_V8_SCHEDULED,
    ACCOUNT_REPLY_DELAY_MAX_SECONDS,
    ACCOUNT_REPLY_DELAY_MIN_SECONDS,
    account_reply_delay_seconds_for_profile,
    account_reply_persona_pipeline_for_job,
    account_reply_persona_status_for_stage,
    create_account_reply_job,
    normalize_account_reply_contract,
)


class AccountReplyVersionFenceTests(unittest.TestCase):
    def test_staging_reply_delay_is_zero_without_random_sampling(self) -> None:
        with patch(
            "backend.services.account_reply_jobs._ACCOUNT_REPLY_RANDOM.randint",
            side_effect=AssertionError("staging must not sample a reply delay"),
        ) as randint:
            self.assertEqual(account_reply_delay_seconds_for_profile("staging"), 0)
            self.assertEqual(account_reply_delay_seconds_for_profile(""), 0)

        randint.assert_not_called()

    def test_production_reply_delay_is_sampled_once_within_contract(self) -> None:
        with patch(
            "backend.services.account_reply_jobs._ACCOUNT_REPLY_RANDOM.randint",
            return_value=417,
        ) as randint:
            self.assertEqual(account_reply_delay_seconds_for_profile("production"), 417)

        randint.assert_called_once_with(
            ACCOUNT_REPLY_DELAY_MIN_SECONDS,
            ACCOUNT_REPLY_DELAY_MAX_SECONDS,
        )

    def test_unknown_processing_profile_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "staging, preproduction, or production"):
            account_reply_delay_seconds_for_profile("preview")

    @patch("backend.services.account_reply_jobs._ACCOUNT_REPLY_RANDOM.randint", return_value=420)
    def test_preproduction_uses_live_reply_delay(self, randint) -> None:
        self.assertEqual(account_reply_delay_seconds_for_profile("preproduction"), 420)
        randint.assert_called_once_with(
            ACCOUNT_REPLY_DELAY_MIN_SECONDS,
            ACCOUNT_REPLY_DELAY_MAX_SECONDS,
        )

    def test_new_persona_job_is_invisible_to_legacy_worker_statuses(self) -> None:
        repository = InMemoryTicketRepository()
        job = create_account_reply_job(
            repository,
            ticket_id="TK-V8-FENCE",
            trigger_message_created_at="2026-08-14T00:00:00+00:00",
            created_at="2026-08-14T00:00:01+00:00",
            delay_seconds=360,
            reply_facts={"resolution_status": "internal_review_in_progress"},
        )

        self.assertEqual(job["status"], ACCOUNT_REPLY_PERSONA_V8_QUEUED)
        self.assertEqual(
            job["payload"]["reply_pipeline"],
            ACCOUNT_REPLY_PERSONA_PIPELINE,
        )
        self.assertEqual(
            repository.claim_account_reply_jobs(
                from_status="persona_queued",
                to_status="persona_preparing",
                now_value="2026-08-14T00:10:00+00:00",
            ),
            [],
        )
        claimed = repository.claim_account_reply_jobs(
            from_status=ACCOUNT_REPLY_PERSONA_V8_QUEUED,
            to_status=ACCOUNT_REPLY_PERSONA_V8_PREPARING,
            now_value="2026-08-14T00:10:00+00:00",
        )
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["status"], ACCOUNT_REPLY_PERSONA_V8_PREPARING)

    def test_legacy_unfinished_job_remains_compatible_with_new_worker_helpers(self) -> None:
        legacy_job = {
            "status": "persona_preparing",
            "payload": {"reply_facts": {"resolution_status": "internal_review_in_progress"}},
        }

        self.assertEqual(
            account_reply_persona_pipeline_for_job(legacy_job),
            ACCOUNT_REPLY_PERSONA_LEGACY_PIPELINE,
        )
        self.assertEqual(
            account_reply_persona_status_for_stage(legacy_job, "scheduled"),
            "persona_scheduled",
        )

    def test_v8_status_transitions_keep_the_version_fence(self) -> None:
        job = {
            "status": ACCOUNT_REPLY_PERSONA_V8_PREPARING,
            "payload": {"reply_pipeline": ACCOUNT_REPLY_PERSONA_PIPELINE},
        }

        self.assertEqual(
            account_reply_persona_status_for_stage(job, "scheduled"),
            ACCOUNT_REPLY_PERSONA_V8_SCHEDULED,
        )

    def test_nested_and_top_level_intents_must_match(self) -> None:
        with self.assertRaisesRegex(AccountReplyContractError, "account_reply_intent_conflict"):
            normalize_account_reply_contract(
                {"reply_intent": "submission_confirmation"},
                reply_intent="resolution_update",
            )

    def test_close_is_derived_from_canonical_intent(self) -> None:
        # p2-138: the legacy "_and_close" suspension intent hands off to the
        # reviewer and no longer derives a close.
        facts, intent, close_after_publish = normalize_account_reply_contract(
            {"reply_intent": "account_suspension_handoff_and_close"},
        )
        self.assertEqual(intent, facts["reply_intent"])
        self.assertFalse(close_after_publish)

        with self.assertRaisesRegex(AccountReplyContractError, "account_reply_close_intent_conflict"):
            normalize_account_reply_contract(
                {"reply_intent": "account_suspension_handoff_and_close"},
                close_after_publish=True,
            )

        invoice_facts, _invoice_intent, invoice_close = normalize_account_reply_contract(
            {"reply_intent": "detailed_invoice_completed_and_close"},
        )
        self.assertTrue(invoice_close)

    def test_detailed_invoice_completion_intent_derives_close(self) -> None:
        facts, intent, close_after_publish = normalize_account_reply_contract(
            {"behavior": "detailed_invoice"},
            reply_intent="detailed_invoice_completed_and_close",
        )
        self.assertEqual(intent, "detailed_invoice_completed_and_close")
        self.assertTrue(close_after_publish)
        self.assertEqual(facts["reply_intent"], "detailed_invoice_completed_and_close")

    def test_job_payload_uses_one_intent_and_derived_close_flag(self) -> None:
        repository = InMemoryTicketRepository()
        job = create_account_reply_job(
            repository,
            ticket_id="TK-CONTRACT",
            trigger_message_created_at="2026-08-14T00:00:00+00:00",
            created_at="2026-08-14T00:00:01+00:00",
            delay_seconds=360,
            reply_facts={
                "behavior": "account_suspension",
                "reply_intent": "account_suspension_handoff_and_close",
            },
        )
        self.assertEqual(
            job["payload"]["reply_facts"]["reply_intent"],
            job["payload"]["reply_intent"],
        )
        # p2-138: suspension handoff keeps AI-owned publication without close.
        self.assertIsNot(job["payload"].get("close_after_publish"), True)

    def test_unpublished_legacy_fraud_close_is_rejected(self) -> None:
        with self.assertRaisesRegex(AccountReplyContractError, "legacy_fraud_handoff_close_intent"):
            normalize_account_reply_contract(
                {"reply_intent": "fraud_handoff_and_close"},
                reject_legacy_fraud_close=True,
            )


if __name__ == "__main__":
    unittest.main()
