from __future__ import annotations

import unittest

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_rerun_recovery import (
    DELIVERY_NOT_SENT,
    DELIVERY_SENT,
    DELIVERY_UNKNOWN,
    build_recovery_manifest,
    classify_internal_email_delivery,
    delivery_readiness_for_cases,
    recovery_readiness,
)


class AccountRerunRecoveryTests(unittest.TestCase):
    def test_delivery_evidence_is_fail_closed(self) -> None:
        sent = classify_internal_email_delivery({
            "internal_email_send_status": "sent",
            "internal_email_payload": {"delivery_key": "enablement:AC-1:v1"},
        })
        legacy_sent = classify_internal_email_delivery({"internal_email_send_status": "sent"})
        not_sent = classify_internal_email_delivery({"internal_email_send_status": "failed"})
        not_ready = classify_internal_email_delivery({"internal_email_send_status": "not_ready"})
        missing = classify_internal_email_delivery({})
        unknown = classify_internal_email_delivery({
            "internal_email_send_status": "sending",
            "internal_email_payload": {"delivery_key": "enablement:AC-4:v1"},
        })
        self.assertEqual(sent["status"], DELIVERY_SENT)
        self.assertEqual(legacy_sent["status"], DELIVERY_UNKNOWN)
        self.assertEqual(not_sent["status"], DELIVERY_UNKNOWN)
        self.assertEqual(not_ready["status"], DELIVERY_NOT_SENT)
        self.assertEqual(missing["status"], DELIVERY_UNKNOWN)
        self.assertEqual(unknown["status"], DELIVERY_UNKNOWN)
        self.assertEqual(unknown["reason_code"], "manual_confirmation_required")

    def test_readiness_blocks_unknown_without_mutation(self) -> None:
        cases = [
            {
                "account_case_id": "AC-SENT",
                "internal_email_send_status": "sent",
                "internal_email_payload": {"delivery_key": "billing:AC-SENT:v1"},
            },
            {"account_case_id": "AC-NOT", "internal_email_send_status": "not_ready"},
            {
                "account_case_id": "AC-UNKNOWN",
                "route_status": "automated",
                "internal_email_send_status": "sending",
            },
            {
                "account_case_id": "AC-LEGACY-UNKNOWN",
                "route_family": "billing_automation",
                "internal_email_send_status": "sending",
            },
        ]
        result = delivery_readiness_for_cases(cases)
        self.assertFalse(result["ready"])
        self.assertEqual(result["unknown_case_ids"], ["AC-UNKNOWN", "AC-LEGACY-UNKNOWN"])
        self.assertTrue(result["manual_confirmation_required"])
        self.assertEqual(recovery_readiness({"unknown_case_ids": ["AC-UNKNOWN"]})["status"], "blocked")

        self.assertTrue(
            delivery_readiness_for_cases([
                {"account_case_id": "AC-HUMAN", "route_status": "not_automated"},
            ])["ready"]
        )

    def test_manifest_is_redacted_and_preserves_reply_associations(self) -> None:
        repository = InMemoryTicketRepository()
        job_id = "account-rerun-473db061e4db42bdaaae0a27241dcd87"
        repository.save_ticket({
            "ticket_id": "12513",
            "customer_id": "customer@example.com",
            "messages": [],
        })
        repository.save_account_case({
            "account_case_id": "AC-12513",
            "billing_ticket_id": "AC-12513",
            "client_ticket_id": "12513",
            "route_status": "automated",
            "route_family": "automated",
            "execution_action": "enablement",
            "automation_handler": "enablement",
            "automation_status": "customer_notified",
            "category": "backend_operation",
            "subcategory": "enablement",
            "route_classification": {
                "primary_label": "Agora",
                "secondary_label": "Backend Operation / Enablement",
            },
            "automation_context": {"rerun_job_id": job_id},
            "internal_email_send_status": "sent",
            "internal_email_payload": {
                "delivery_key": "enablement:AC-12513:v1",
                "customer_email": "customer@example.com",
                "app_id": "0123456789abcdef0123456789abcdef",
            },
        })
        repository.save_account_reply_job({
            "job_id": "reply-12513",
            "ticket_id": "12513",
            "status": "published",
            "payload": {"automation_delivery_key": "enablement:AC-12513:v1"},
        })
        repository.claim_automation_reply(
            "graph:outlook-12513",
            client_ticket_id="12513",
            handler="enablement",
            owner_token="owner",
            claimed_at="2026-08-12T00:00:00+00:00",
            lease_expires_at="2026-08-12T00:30:00+00:00",
        )
        repository.commit_automation_reply_result(
            "graph:outlook-12513",
            owner_token="owner",
            ticket_id="12513",
            assistant_message=None,
            account_case_updates={},
            events=[{"event_type": "automation_reply_completed", "payload": {}}],
            completed_at="2026-08-12T00:01:00+00:00",
        )
        repository.claim_account_case_rerun(
            {
                "job_id": job_id,
                "status": "failed",
                "scope": "all_cases",
                "frozen_case_ids": ["AC-12513"],
                "processed": 1,
                "succeeded": 0,
                "failed": 1,
                "remaining": 0,
            },
            active_after="2026-08-11T00:00:00+00:00",
            request_scope="test",
        )
        manifest = build_recovery_manifest(job_id, repository=repository)
        rendered = str(manifest)
        self.assertEqual(manifest["case_count"], 1)
        self.assertEqual(manifest["cases"][0]["internal_email_delivery"]["status"], DELIVERY_SENT)
        self.assertEqual(manifest["cases"][0]["reply_association"]["status"], "completed")
        self.assertEqual(manifest["cases"][0]["claim_association"]["status"], "completed")
        self.assertNotIn("customer@example.com", rendered)
        self.assertNotIn("0123456789abcdef0123456789abcdef", rendered)
        self.assertNotIn("0123456789abcdef0123456789abcdef", rendered)
        self.assertNotIn("raw-response-token", rendered)

    def test_manifest_marks_missing_frozen_case_as_unknown(self) -> None:
        repository = InMemoryTicketRepository()
        job_id = "account-rerun-missing-case"
        repository.claim_account_case_rerun(
            {
                "job_id": job_id,
                "status": "failed",
                "scope": "all_cases",
                "frozen_case_ids": ["AC-MISSING"],
            },
            active_after="2026-08-11T00:00:00+00:00",
            request_scope="test",
        )
        manifest = build_recovery_manifest(job_id, repository=repository)
        self.assertEqual(manifest["unknown_case_ids"], ["AC-MISSING"])
        self.assertEqual(
            manifest["cases"][0]["internal_email_delivery"]["reason_code"],
            "case_missing_from_storage",
        )

    def test_legacy_all_cases_job_uses_current_inventory_when_count_matches(self) -> None:
        repository = InMemoryTicketRepository()
        job_id = "legacy-all-cases"
        for number in (1, 2):
            repository.save_account_case({
                "account_case_id": f"AC-{number}",
                "client_ticket_id": str(number),
                "route_status": "not_automated",
            })
        repository.claim_account_case_rerun(
            {
                "job_id": job_id,
                "status": "completed_with_errors",
                "scope": "all_cases",
                "processed": 2,
                "failed": 2,
                "reply_jobs_deleted": 1,
            },
            active_after="2026-08-11T00:00:00+00:00",
            request_scope="test",
        )

        manifest = build_recovery_manifest(job_id, repository=repository)

        self.assertEqual(manifest["case_count"], 2)
        self.assertEqual(
            manifest["impact_inventory"]["source"],
            "legacy_all_cases_current_inventory",
        )
        self.assertTrue(manifest["impact_inventory"]["matches_expected_count"])
        self.assertTrue(recovery_readiness(manifest)["ready"])

    def test_legacy_all_cases_job_with_inventory_mismatch_fails_closed(self) -> None:
        repository = InMemoryTicketRepository()
        job_id = "legacy-all-cases-mismatch"
        repository.save_account_case({
            "account_case_id": "AC-ONLY",
            "client_ticket_id": "ONLY",
            "route_status": "not_automated",
        })
        repository.claim_account_case_rerun(
            {
                "job_id": job_id,
                "status": "completed_with_errors",
                "scope": "all_cases",
                "processed": 2,
                "failed": 2,
                "reply_jobs_deleted": 1,
            },
            active_after="2026-08-11T00:00:00+00:00",
            request_scope="test",
        )

        manifest = build_recovery_manifest(job_id, repository=repository)
        readiness = recovery_readiness(manifest)

        self.assertTrue(manifest["impact_inventory"]["unresolved"])
        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["reason_code"], "impact_inventory_unresolved")


if __name__ == "__main__":
    unittest.main()
