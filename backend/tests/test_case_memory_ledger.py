from __future__ import annotations

import unittest

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.case_memory_ledger import build_case_memory_ledger_record_from_feedback


class CaseMemoryLedgerTests(unittest.TestCase):
    def test_builds_ledger_record_from_any_hitl_feedback_with_retrieval_disabled(self) -> None:
        feedback = {
            "feedback_id": "hitl_reject_1",
            "engineer_case_id": "TK-LEDGER-1-1",
            "client_ticket_id": "TK-LEDGER-1",
            "feedback_type": "reject",
            "diagnosis_correctness": "incorrect",
            "root_cause_correctness": "incorrect",
            "evidence_quality": "wrong",
            "citation_quality": "wrong",
            "customer_reply_quality": "unsafe",
            "corrected_root_cause": "The root cause was not proven.",
            "corrected_solution": "Ask for more evidence before suggesting a fix.",
            "corrected_customer_reply": "Please share logs before we recommend a next step.",
            "missing_information": [{"field": "logs"}],
            "incorrect_claims": [{"claim": "The SDK version is definitely broken."}],
            "evidence_refs": [{"source_id": "msg-1"}],
            "memory_candidate": "no",
            "memory_safety": "do_not_store",
            "memory_notes": "Rejected feedback should stay in the ledger but never be auto-retrieved.",
            "prompt_version": "engineer-hitl-auto-review-v1",
            "workflow_version": "engineer-auto-hitl-review-v1",
            "tool_policy_version": "engineer-evidence-tools-v1",
            "rag_access_policy_version": "rag-access-routing-v1",
            "evidence_packet_version": "engineer-evidence-packet-v1",
            "created_by": "engineer_ai_auto_review",
            "created_at": "2026-06-10T09:30:00+00:00",
        }

        record = build_case_memory_ledger_record_from_feedback(feedback)

        self.assertEqual(record["memory_record_id"], "cm_hitl_reject_1")
        self.assertEqual(record["source_feedback_id"], "hitl_reject_1")
        self.assertEqual(record["ledger_status"], "ledger_only")
        self.assertFalse(record["retrieval_enabled"])
        self.assertEqual(record["active_memory_status"], "inactive")
        self.assertEqual(record["feedback_type"], "reject")
        self.assertEqual(record["quality_label"], "rejected_feedback")
        self.assertEqual(record["safety_label"], "do_not_store")
        self.assertEqual(record["symptom"], "The root cause was not proven.")
        self.assertEqual(record["root_cause"], "The root cause was not proven.")
        self.assertEqual(record["solution"], "Ask for more evidence before suggesting a fix.")
        self.assertEqual(
            record["customer_safe_summary"],
            "Please share logs before we recommend a next step.",
        )
        self.assertEqual(record["internal_only_summary"], "Rejected feedback should stay in the ledger but never be auto-retrieved.")
        self.assertEqual(record["metadata"]["memory_candidate"], "no")
        self.assertEqual(record["metadata"]["customer_reply_quality"], "unsafe")

    def test_repository_records_and_lists_case_memory_ledger_records(self) -> None:
        repository = InMemoryTicketRepository()
        repository.initialize()
        saved = repository.record_case_memory_ledger(
            {
                "memory_record_id": "cm_hitl_1",
                "source_feedback_id": "hitl_1",
                "client_ticket_id": "TK-LEDGER-2",
                "engineer_case_id": "TK-LEDGER-2-1",
                "feedback_type": "resolve",
                "ledger_status": "candidate",
                "retrieval_enabled": False,
                "active_memory_status": "inactive",
                "symptom": "Token renew callback fails.",
                "root_cause": "SDK 4.2.1 regression on Android 14.",
                "solution": "Upgrade to SDK 4.2.2.",
                "customer_safe_summary": "Please upgrade to SDK 4.2.2.",
                "internal_only_summary": "Internal repro logs confirmed Android 14 only.",
                "evidence_refs": [{"source_id": "msg-2"}],
                "safety_label": "internal_only",
                "quality_label": "candidate",
                "memory_schema_version": "case-memory-ledger-v1",
                "prompt_version": "engineer-hitl-auto-review-v1",
                "workflow_version": "engineer-auto-hitl-review-v1",
                "tool_policy_version": "engineer-evidence-tools-v1",
                "rag_access_policy_version": "rag-access-routing-v1",
                "evidence_packet_version": "engineer-evidence-packet-v1",
                "metadata": {"memory_candidate": "needs_review"},
                "created_at": "2026-06-10T09:31:00+00:00",
                "updated_at": "2026-06-10T09:31:00+00:00",
            }
        )

        rows = repository.list_case_memory_ledger("TK-LEDGER-2-1")

        self.assertEqual(saved["memory_record_id"], "cm_hitl_1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["memory_record_id"], "cm_hitl_1")
        self.assertFalse(rows[0]["retrieval_enabled"])
        self.assertEqual(rows[0]["active_memory_status"], "inactive")
        self.assertEqual(rows[0]["metadata"]["memory_candidate"], "needs_review")

    def test_candidate_memory_must_stay_retrieval_disabled_and_inactive(self) -> None:
        """Candidate ledger records must never auto-enable retrieval or active memory."""
        feedback = {
            "feedback_id": "hitl_candidate_safe",
            "engineer_case_id": "TK-CAND-1-1",
            "client_ticket_id": "TK-CAND-1",
            "feedback_type": "resolve",
            "diagnosis_correctness": "correct",
            "root_cause_correctness": "confirmed",
            "evidence_quality": "sufficient",
            "citation_quality": "correct",
            "customer_reply_quality": "sendable",
            "corrected_root_cause": "SDK 4.2.1 regression on Android 14.",
            "corrected_solution": "Upgrade to SDK 4.2.2.",
            "corrected_customer_reply": "Please upgrade to SDK 4.2.2.",
            "missing_information": [],
            "incorrect_claims": [],
            "evidence_refs": [{"source_id": "msg-cand"}],
            "memory_candidate": "yes",
            "memory_safety": "customer_safe",
            "memory_notes": "Verified fix for token renew callback.",
            "prompt_version": "engineer-hitl-auto-review-v1",
            "workflow_version": "engineer-auto-hitl-review-v1",
            "tool_policy_version": None,
            "rag_access_policy_version": None,
            "evidence_packet_version": None,
            "created_by": "engineer_ai_auto_review",
            "created_at": "2026-06-10T10:00:00+00:00",
        }

        record = build_case_memory_ledger_record_from_feedback(feedback)

        self.assertEqual(record["ledger_status"], "candidate")
        self.assertFalse(record["retrieval_enabled"])
        self.assertEqual(record["active_memory_status"], "inactive")
        self.assertEqual(record["quality_label"], "candidate")
        self.assertEqual(record["safety_label"], "customer_safe")


if __name__ == "__main__":
    unittest.main()
