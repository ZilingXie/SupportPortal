from __future__ import annotations

import os
import unittest

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.route_correction import (
    RouteCorrectionValidationError,
    VALID_ROUTE_TUPLES,
    validate_route_correction,
)


class RouteCorrectionValidationTests(unittest.TestCase):
    def test_valid_billing_detailed_invoice_derives_full_tuple(self) -> None:
        result = validate_route_correction(scope_label="billing", execution_action="detailed_invoice")
        self.assertEqual(result["scope_label"], "billing")
        self.assertEqual(result["execution_action"], "detailed_invoice")
        self.assertEqual(result["route_family"], "billing_automation")
        self.assertEqual(result["tooling_profile"], "deterministic_billing_intake")

    def test_valid_agora_technical_rag(self) -> None:
        result = validate_route_correction(scope_label="agora_technical", execution_action="rag")
        self.assertEqual(result["route_family"], "agora_docs_rag")
        self.assertEqual(result["tooling_profile"], "agora_docs_only")

    def test_invalid_scope_rejected(self) -> None:
        with self.assertRaises(RouteCorrectionValidationError):
            validate_route_correction(scope_label="unknown_scope", execution_action="rag")

    def test_invalid_action_for_scope_rejected(self) -> None:
        with self.assertRaises(RouteCorrectionValidationError):
            validate_route_correction(scope_label="billing", execution_action="rag")

    def test_whitespace_and_case_normalized(self) -> None:
        result = validate_route_correction(scope_label="  Billing  ", execution_action="DETAILED_INVOICE")
        self.assertEqual(result["scope_label"], "billing")
        self.assertEqual(result["execution_action"], "detailed_invoice")

    def test_note_normalized_and_optional(self) -> None:
        result = validate_route_correction(
            scope_label="billing",
            execution_action="human_review_required",
            note="  refund dispute  ",
        )
        self.assertEqual(result["note"], "refund dispute")
        empty = validate_route_correction(
            scope_label="billing",
            execution_action="human_review_required",
        )
        self.assertEqual(empty["note"], "")

    def test_valid_tuple_dictionary_matches_contract(self) -> None:
        expected_pairs = {
            ("ticket_resolution", "resolve_ticket", "ticket_resolution", "deterministic_resolution"),
            ("billing", "account_suspension", "billing_automation", "deterministic_billing_intake"),
            ("billing", "detailed_invoice", "billing_automation", "deterministic_billing_intake"),
            ("billing", "account_verification", "billing_automation", "deterministic_billing_intake"),
            ("billing", "human_review_required", "billing_review", "deterministic_billing_intake"),
            ("billing", "refuse", "fallback_or_refuse", "no_agora_docs_refusal"),
            ("agora_technical", "rag", "agora_docs_rag", "agora_docs_only"),
            ("agora_non_technical", "web_search", "web_company_info", "official_web_search"),
            ("agora_non_technical", "refuse", "web_company_info", "no_agora_docs_refusal"),
            ("small_talk", "controlled_response", "general_chat", "controlled_acknowledgement"),
            ("small_talk", "refuse", "general_chat", "no_agora_docs_refusal"),
            ("non_agora", "refuse", "fallback_or_refuse", "no_agora_docs_refusal"),
        }
        actual_pairs = {
            (
                item["scope_label"],
                item["execution_action"],
                item["route_family"],
                item["tooling_profile"],
            )
            for item in VALID_ROUTE_TUPLES
        }
        self.assertEqual(actual_pairs, expected_pairs)


class BillingRouteCorrectionRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.billing_ticket_id = "BT-TK-ACC-123456"
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": self.billing_ticket_id,
                "client_ticket_id": "TK-ACC-123456",
                "source": "manual",
                "title": "t",
                "question": "q",
                "automation_status": "automation",
                "route": "detailed_invoice",
                "scope_label": "billing",
                "route_family": "billing_automation",
                "execution_action": "detailed_invoice",
                "tooling_profile": "deterministic_billing_intake",
                "route_reason": "billing_invoice_request",
                "route_confidence": 0.42,
            }
        )

    def _correction(self) -> dict[str, object]:
        return {
            "billing_ticket_id": self.billing_ticket_id,
            "client_ticket_id": "TK-ACC-123456",
            "original_scope_label": "billing",
            "original_route_family": "billing_automation",
            "original_execution_action": "detailed_invoice",
            "original_tooling_profile": "deterministic_billing_intake",
            "original_route_reason": "billing_invoice_request",
            "original_route_confidence": 0.42,
            "corrected_scope_label": "billing",
            "corrected_route_family": "billing_review",
            "corrected_execution_action": "human_review_required",
            "corrected_tooling_profile": "deterministic_billing_intake",
            "first_corrected_scope_label": "billing",
            "first_corrected_route_family": "billing_review",
            "first_corrected_execution_action": "human_review_required",
            "first_corrected_tooling_profile": "deterministic_billing_intake",
            "corrector": "operator",
            "note": "refund dispute",
            "created_at": "2026-06-19T00:00:00+00:00",
            "updated_at": "2026-06-19T00:00:00+00:00",
            "correction_count": 1,
        }

    def test_save_get_and_list_correction(self) -> None:
        correction = self._correction()
        self.repository.save_billing_route_correction(correction)
        fetched = self.repository.get_billing_route_correction(self.billing_ticket_id)
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched["corrected_execution_action"], "human_review_required")
        listed = self.repository.list_billing_route_corrections(limit=10)
        self.assertEqual(len(listed), 1)

    def test_resave_overwrites_but_preserves_original_and_first_corrected(self) -> None:
        self.repository.save_billing_route_correction(self._correction())
        updated = self._correction()
        updated["original_execution_action"] = "human_review_required"
        updated["corrected_execution_action"] = "refuse"
        updated["corrected_route_family"] = "fallback_or_refuse"
        updated["corrected_scope_label"] = "billing"
        updated["corrected_tooling_profile"] = "no_agora_docs_refusal"
        updated["first_corrected_execution_action"] = "refuse"
        updated["note"] = "actually refuse"
        updated["correction_count"] = 99
        self.repository.save_billing_route_correction(updated)
        fetched = self.repository.get_billing_route_correction(self.billing_ticket_id)
        assert fetched is not None
        self.assertEqual(fetched["corrected_execution_action"], "refuse")
        self.assertEqual(fetched["original_execution_action"], "detailed_invoice")
        self.assertEqual(fetched["first_corrected_execution_action"], "human_review_required")
        self.assertEqual(fetched["correction_count"], 2)

    def test_get_missing_returns_none(self) -> None:
        self.assertIsNone(self.repository.get_billing_route_correction("BT-MISSING"))

    def test_apply_route_correction_updates_ticket_and_returns_persisted_count(self) -> None:
        correction = self._correction()
        correction["correction_count"] = 99
        saved = self.repository.apply_billing_route_correction(
            billing_ticket_id=self.billing_ticket_id,
            active_route={
                "route": "human_review_required",
                "scope_label": "billing",
                "route_family": "billing_review",
                "execution_action": "human_review_required",
                "tooling_profile": "deterministic_billing_intake",
                "updated_at": "2026-06-19T00:00:00+00:00",
            },
            correction=correction,
        )
        self.assertEqual(saved["correction_count"], 1)
        ticket = self.repository.get_billing_ticket(self.billing_ticket_id)
        assert ticket is not None
        self.assertEqual(ticket["route"], "human_review_required")
        self.assertEqual(ticket["route_family"], "billing_review")

        updated = self._correction()
        updated["corrected_execution_action"] = "refuse"
        updated["corrected_route_family"] = "fallback_or_refuse"
        updated["corrected_tooling_profile"] = "no_agora_docs_refusal"
        updated["correction_count"] = 1
        resaved = self.repository.apply_billing_route_correction(
            billing_ticket_id=self.billing_ticket_id,
            active_route={
                "route": "refuse",
                "scope_label": "billing",
                "route_family": "fallback_or_refuse",
                "execution_action": "refuse",
                "tooling_profile": "no_agora_docs_refusal",
                "updated_at": "2026-06-19T00:01:00+00:00",
            },
            correction=updated,
        )
        self.assertEqual(resaved["correction_count"], 2)
        self.assertEqual(resaved["original_execution_action"], "detailed_invoice")
        self.assertEqual(resaved["first_corrected_execution_action"], "human_review_required")
