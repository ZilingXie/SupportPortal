from __future__ import annotations

import unittest

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_reply_jobs import (
    ACCOUNT_REPLY_PERSONA_LEGACY_PIPELINE,
    ACCOUNT_REPLY_PERSONA_PIPELINE,
    ACCOUNT_REPLY_PERSONA_V8_PREPARING,
    ACCOUNT_REPLY_PERSONA_V8_QUEUED,
    ACCOUNT_REPLY_PERSONA_V8_SCHEDULED,
    account_reply_persona_pipeline_for_job,
    account_reply_persona_status_for_stage,
    create_account_reply_job,
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


if __name__ == "__main__":
    unittest.main()
