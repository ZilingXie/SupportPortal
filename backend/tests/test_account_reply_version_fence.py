from __future__ import annotations

import unittest

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_reply_jobs import (
    AccountReplyContractError,
    ACCOUNT_REPLY_PERSONA_LEGACY_PIPELINE,
    ACCOUNT_REPLY_PERSONA_PIPELINE,
    ACCOUNT_REPLY_PERSONA_V8_PREPARING,
    ACCOUNT_REPLY_PERSONA_V8_QUEUED,
    ACCOUNT_REPLY_PERSONA_V8_SCHEDULED,
    account_reply_persona_pipeline_for_job,
    account_reply_persona_status_for_stage,
    create_account_reply_job,
    normalize_account_reply_contract,
)


class AccountReplyVersionFenceTests(unittest.TestCase):
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
        facts, intent, close_after_publish = normalize_account_reply_contract(
            {"reply_intent": "account_suspension_handoff_and_close"},
        )
        self.assertEqual(intent, facts["reply_intent"])
        self.assertTrue(close_after_publish)

        with self.assertRaisesRegex(AccountReplyContractError, "account_reply_close_intent_conflict"):
            normalize_account_reply_contract(
                {"reply_intent": "fraud_handoff_confirmation"},
                close_after_publish=True,
            )

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
        self.assertTrue(job["payload"]["close_after_publish"])

    def test_unpublished_legacy_fraud_close_is_rejected(self) -> None:
        with self.assertRaisesRegex(AccountReplyContractError, "legacy_fraud_handoff_close_intent"):
            normalize_account_reply_contract(
                {"reply_intent": "fraud_handoff_and_close"},
                reject_legacy_fraud_close=True,
            )


if __name__ == "__main__":
    unittest.main()
