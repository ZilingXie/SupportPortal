from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from backend.services.support_router import (
    SupportRouteDecision,
    decide_support_route,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class GoldenBillingRoutingTests(unittest.TestCase):
    """Golden regression set for billing semantic routing.

    These tests define the expected behaviour BEFORE implementation changes.
    They should initially fail and pass as the semantic router + policy gate
    are implemented in Tasks 2-6.
    """

    # ── TK-ACC-68BAC7 canonical case ──────────────────────────────────

    def test_semantic_first_billing_policy_gate_preserves_router_audit(self) -> None:
        """LLM-sourced billing decisions keep audit fields after policy gate rewrites."""
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "billing",
                    "semantic_intent": "billing.account_suspension",
                    "recommended_action": "human_review_required",
                    "automation_eligibility": "not_eligible",
                    "confidence": 0.93,
                    "reason": "Customer asks for account suspension review.",
                    "matched_signals": ["account suspension", "review"],
                    "evidence_spans": ["review our suspended account"],
                    "risk_flags": ["account_access_restore"],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route(
                "Please review our suspended account.",
                semantic_first=True,
            )

        self.assertEqual(decision.router_source, "llm_semantic")
        self.assertEqual(decision.execution_action, "human_review_required")
        self.assertTrue(decision.intent_router_attempted)
        self.assertEqual(decision.intent_router_model_confidence, 0.93)
        self.assertIsNotNone(decision.intent_router_confidence_threshold)
        self.assertIsNone(decision.intent_router_fallback_reason)

    def test_account_temporarily_suspended_routes_to_billing_not_web_search(self) -> None:
        """TK-ACC-68BAC7: 'Account temporarily suspended' must route to billing,
        not web_search."""
        message = (
            "Account temporarily suspended. Our account ExampleCo under "
            "support@example.com has been suspended due to insufficient balance. "
            "We’d like to restore access to our account as soon as possible."
        )
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "billing",
                    "semantic_intent": "billing.account_suspension",
                    "recommended_action": "human_review_required",
                    "automation_eligibility": "not_eligible",
                    "confidence": 0.94,
                    "reason": "Customer reports an Agora account suspension due to insufficient balance and asks to restore access.",
                    "matched_signals": ["account suspended", "insufficient balance", "restore access"],
                    "evidence_spans": [
                        "has been suspended due to insufficient balance",
                        "restore access to our account",
                    ],
                    "risk_flags": ["account_access_restore", "billing_terms_question"],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route(message)

        # Must be billing, not web_search
        self.assertEqual(decision.scope_label, "billing")
        self.assertNotEqual(decision.execution_action, "web_search")
        # Semantic intent should be populated
        self.assertEqual(decision.semantic_intent, "billing.account_suspension")
        # router_source may be 'deterministic' (expanded regex) or 'llm_semantic'
        self.assertIn(decision.router_source, {"deterministic", "llm_semantic"})
        # When LLM-sourced, automation_eligibility must be set
        if decision.router_source == "llm_semantic":
            self.assertEqual(decision.automation_eligibility, "not_eligible")
            self.assertIsNotNone(decision.not_automated_reason)

    def test_account_has_been_suspended_due_to_balance_routes_to_billing(self) -> None:
        """Var: 'account has been suspended due to insufficient balance' → billing."""
        message = (
            "Hi, our account has been suspended due to insufficient balance. "
            "Please help us restore access."
        )
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "billing",
                    "semantic_intent": "billing.account_suspension",
                    "recommended_action": "human_review_required",
                    "automation_eligibility": "not_eligible",
                    "confidence": 0.92,
                    "reason": "Account suspended due to balance.",
                    "matched_signals": ["account suspended", "insufficient balance"],
                    "evidence_spans": ["account has been suspended due to insufficient balance"],
                    "risk_flags": ["account_access_restore"],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route(message)

        self.assertEqual(decision.scope_label, "billing")
        self.assertEqual(decision.semantic_intent, "billing.account_suspension")
        self.assertNotEqual(decision.execution_action, "web_search")

    def test_account_disabled_due_to_balance_routes_to_billing(self) -> None:
        """Var: 'account disabled due to balance' → billing."""
        message = "My account was disabled due to balance. What do I need to do to restore it?"
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "billing",
                    "semantic_intent": "billing.account_suspension",
                    "recommended_action": "human_review_required",
                    "automation_eligibility": "not_eligible",
                    "confidence": 0.90,
                    "reason": "Account disabled due to balance.",
                    "matched_signals": ["account disabled", "balance"],
                    "evidence_spans": ["account was disabled due to balance"],
                    "risk_flags": ["account_access_restore"],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route(message)

        self.assertEqual(decision.scope_label, "billing")
        self.assertEqual(decision.semantic_intent, "billing.account_suspension")

    # ── Positive billing: detailed invoice ────────────────────────────

    def test_detailed_invoice_request_routes_to_billing(self) -> None:
        """Detailed invoice request should route to billing."""
        message = (
            "Please send detailed invoice for Transaction ID TXN-123, "
            "Issue date 1 June 2026, Amount USD 500."
        )
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "billing",
                    "semantic_intent": "billing.detailed_invoice",
                    "recommended_action": "automation_candidate",
                    "automation_eligibility": "eligible",
                    "confidence": 0.96,
                    "reason": "Customer requests a detailed invoice with complete field data.",
                    "matched_signals": ["detailed invoice", "transaction id", "amount"],
                    "evidence_spans": ["detailed invoice for Transaction ID TXN-123"],
                    "risk_flags": [],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route(message)

        self.assertEqual(decision.scope_label, "billing")
        self.assertEqual(decision.semantic_intent, "billing.detailed_invoice")

    def test_llm_detailed_invoice_automation_candidate_maps_to_billing_action(self) -> None:
        """LLM can use semantic wording that regex misses; policy gate must map it."""
        message = "Please provide an itemized billing statement for transaction TXN-123."
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "billing",
                    "semantic_intent": "billing.detailed_invoice",
                    "recommended_action": "automation_candidate",
                    "automation_eligibility": "eligible",
                    "confidence": 0.93,
                    "reason": "Customer requests itemized billing details for a transaction.",
                    "matched_signals": ["itemized billing statement", "transaction"],
                    "evidence_spans": ["itemized billing statement for transaction TXN-123"],
                    "risk_flags": [],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route(message)

        self.assertEqual(decision.scope_label, "billing")
        self.assertEqual(decision.semantic_intent, "billing.detailed_invoice")
        self.assertEqual(decision.route_family, "billing_automation")
        self.assertEqual(decision.execution_action, "detailed_invoice")
        self.assertEqual(decision.automation_eligibility, "eligible")
        self.assertEqual(decision.policy_decision, "policy_gate")

    # ── Negative billing: should NOT be automated ─────────────────────

    def test_invoice_amount_wrong_routes_to_billing_but_not_automated(self) -> None:
        """Invoice amount dispute → billing intent but human_review_required."""
        message = "The invoice amount is wrong. We were overcharged for last month."
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "billing",
                    "semantic_intent": "billing.refund_or_dispute",
                    "recommended_action": "human_review_required",
                    "automation_eligibility": "not_eligible",
                    "confidence": 0.88,
                    "reason": "Customer disputes invoice amount and reports overcharge.",
                    "matched_signals": ["invoice", "wrong amount", "overcharged"],
                    "evidence_spans": ["invoice amount is wrong", "overcharged for last month"],
                    "risk_flags": ["amount_dispute", "overcharge"],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route(message)

        self.assertEqual(decision.scope_label, "billing")
        self.assertIn("refund_or_dispute", decision.semantic_intent or "")
        self.assertEqual(decision.automation_eligibility, "not_eligible")
        self.assertEqual(decision.not_automated_reason, "human_review_required")

    def test_refund_request_routes_to_billing_not_automated(self) -> None:
        """Refund request → billing but not eligible for automation."""
        message = "I want a refund for my last payment. The service did not work as expected."
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "billing",
                    "semantic_intent": "billing.refund_or_dispute",
                    "recommended_action": "human_review_required",
                    "automation_eligibility": "not_eligible",
                    "confidence": 0.90,
                    "reason": "Customer requests a refund.",
                    "matched_signals": ["refund", "payment"],
                    "evidence_spans": ["want a refund for my last payment"],
                    "risk_flags": ["refund_request", "service_dissatisfaction"],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route(message)

        self.assertEqual(decision.scope_label, "billing")
        self.assertEqual(decision.automation_eligibility, "not_eligible")
        self.assertEqual(decision.not_automated_reason, "human_review_required")

    def test_legal_compensation_request_routes_to_billing_not_automated(self) -> None:
        """Legal/compensation → billing but definitely not automation."""
        message = "We are seeking legal compensation for the billing error that affected our service."
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "billing",
                    "semantic_intent": "billing.refund_or_dispute",
                    "recommended_action": "human_review_required",
                    "automation_eligibility": "not_eligible",
                    "confidence": 0.85,
                    "reason": "Customer seeks legal compensation for billing error.",
                    "matched_signals": ["legal", "compensation", "billing error"],
                    "evidence_spans": ["seeking legal compensation for the billing error"],
                    "risk_flags": ["legal_threat", "compensation"],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route(message)

        self.assertEqual(decision.scope_label, "billing")
        self.assertEqual(decision.automation_eligibility, "not_eligible")
        self.assertTrue(any("legal" in f.lower() for f in decision.risk_flags))

    # ── Non-billing cases ─────────────────────────────────────────────

    def test_black_screen_is_not_billing(self) -> None:
        """Black screen troubleshooting → agora_technical, not billing."""
        message = "I got black screen issue, what should I do?"
        # This should hit deterministic fast path, no LLM call needed
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen"
        ) as urlopen_mock:
            decision = decide_support_route(message)

        self.assertEqual(decision.scope_label, "agora_technical")
        self.assertIsNone(decision.semantic_intent)
        self.assertEqual(decision.router_source, "deterministic")
        urlopen_mock.assert_not_called()

    def test_product_overview_is_not_billing(self) -> None:
        """Product overview → agora_non_technical, not billing."""
        message = "What products does Agora provide for broadcasting?"
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen"
        ) as urlopen_mock:
            decision = decide_support_route(message)

        self.assertEqual(decision.scope_label, "agora_non_technical")
        self.assertIsNone(decision.semantic_intent)
        self.assertEqual(decision.execution_action, "web_search")
        self.assertEqual(decision.router_source, "deterministic")
        urlopen_mock.assert_not_called()

    def test_printer_issue_is_not_billing(self) -> None:
        """Printer issue → non_agora, not billing."""
        message = "My printer is not working. Can you help?"
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen"
        ) as urlopen_mock:
            decision = decide_support_route(message)

        self.assertEqual(decision.scope_label, "non_agora")
        self.assertIsNone(decision.semantic_intent)
        self.assertEqual(decision.router_source, "deterministic")
        urlopen_mock.assert_not_called()

    # ── Route contract compatibility ──────────────────────────────────

    def test_billing_not_automated_has_not_automated_reason(self) -> None:
        """Every not_automated billing case must carry a reason."""
        decision = SupportRouteDecision(
            scope_label="billing",
            route="human_review_required",
            confidence=0.90,
            reason="billing_account_suspension",
            matched_signals=["account suspended"],
            response_language="en",
            semantic_intent="billing.account_suspension",
            automation_eligibility="not_eligible",
            not_automated_reason="human_review_required",
            risk_flags=["account_access_restore"],
            evidence_spans=["account has been suspended"],
            router_source="llm_semantic",
        )

        self.assertEqual(decision.scope_label, "billing")
        self.assertEqual(decision.semantic_intent, "billing.account_suspension")
        self.assertEqual(decision.automation_eligibility, "not_eligible")
        self.assertEqual(decision.not_automated_reason, "human_review_required")
        # Existing route contract fields must still work
        self.assertIsNotNone(decision.route_family)
        self.assertIsNotNone(decision.execution_action)
        self.assertIsNotNone(decision.tooling_profile)

    # ── Account verification routing ───────────────────────────────────

    def test_account_verification_fraud_review_routes_to_billing_automation(self) -> None:
        """Fraud/suspicious activity verification → billing.account_verification → billing_automation."""
        message = (
            "Our Agora account has been flagged for suspicious activity. "
            "We need to complete company verification to restore access. "
            "Company: ExampleCorp, Country: Singapore, Address: 123 Orchard Road, "
            "Service URL: https://example.com, Email: admin@example.com, "
            "Phone: +65-1234-5678. "
            "[Use Case]\nWe use Agora for live streaming to 10k concurrent viewers.\n"
        )
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "billing",
                    "semantic_intent": "billing.account_verification",
                    "recommended_action": "automation_candidate",
                    "automation_eligibility": "eligible",
                    "confidence": 0.93,
                    "reason": "Customer reports suspicious activity flag and requests company verification.",
                    "matched_signals": ["suspicious activity", "company verification", "restore access"],
                    "evidence_spans": [
                        "account has been flagged for suspicious activity",
                        "complete company verification to restore access",
                    ],
                    "risk_flags": ["account_access_restore"],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route(message)

        self.assertEqual(decision.scope_label, "billing")
        self.assertEqual(decision.semantic_intent, "billing.account_verification")
        self.assertEqual(decision.route_family, "billing_automation")
        self.assertEqual(decision.execution_action, "account_verification")
        self.assertEqual(decision.automation_eligibility, "eligible")

    def test_account_verification_with_refund_flag_not_automated(self) -> None:
        """account_verification + refund risk → billing_review, not automation."""
        message = "My account was flagged for suspicious activity and I want a refund too."
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "billing",
                    "semantic_intent": "billing.account_verification",
                    "recommended_action": "human_review_required",
                    "automation_eligibility": "not_eligible",
                    "confidence": 0.88,
                    "reason": "Suspicious activity verification with refund request.",
                    "matched_signals": ["suspicious activity", "refund"],
                    "evidence_spans": ["flagged for suspicious activity", "want a refund"],
                    "risk_flags": ["account_access_restore", "refund_request"],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route(message)

        self.assertEqual(decision.scope_label, "billing")
        self.assertIn("account_verification", decision.semantic_intent or "")
        self.assertEqual(decision.route_family, "billing_review")
        self.assertEqual(decision.execution_action, "human_review_required")
        self.assertEqual(decision.automation_eligibility, "not_eligible")

    def test_account_verification_with_overcharge_flag_not_automated(self) -> None:
        """account_verification + billing abnormality risk → billing_review, not automation."""
        message = "My account was flagged for suspicious activity, and we were overcharged."
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "billing",
                    "semantic_intent": "billing.account_verification",
                    "recommended_action": "automation_candidate",
                    "automation_eligibility": "eligible",
                    "confidence": 0.89,
                    "reason": "Suspicious activity verification with overcharge complaint.",
                    "matched_signals": ["suspicious activity", "overcharged"],
                    "evidence_spans": ["flagged for suspicious activity", "we were overcharged"],
                    "risk_flags": ["account_access_restore", "overcharge"],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route(message)

        self.assertEqual(decision.scope_label, "billing")
        self.assertIn("account_verification", decision.semantic_intent or "")
        self.assertEqual(decision.route_family, "billing_review")
        self.assertEqual(decision.execution_action, "human_review_required")
        self.assertEqual(decision.automation_eligibility, "not_eligible")

    def test_account_verification_company_reactivation_intake(self) -> None:
        """Company verification / reactivation → billing.account_verification."""
        message = (
            "Our account needs company verification review after a fraud alert. "
            "We need to submit verification materials to reactivate. Company: MyCo, Location: USA."
        )
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "billing",
                    "semantic_intent": "billing.account_verification",
                    "recommended_action": "automation_candidate",
                    "automation_eligibility": "eligible",
                    "confidence": 0.91,
                    "reason": "Customer needs to submit company verification for reactivation.",
                    "matched_signals": ["account suspended", "company verification", "reactivate"],
                    "evidence_spans": ["account was suspended", "submit company verification materials to reactivate"],
                    "risk_flags": ["account_access_restore"],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            decision = decide_support_route(message)

        self.assertEqual(decision.scope_label, "billing")
        self.assertEqual(decision.semantic_intent, "billing.account_verification")
        self.assertEqual(decision.route_family, "billing_automation")

    # ── Gratitude downgrade: long messages with "thank you" ────────────

    def test_long_message_ending_thank_you_not_small_talk(self) -> None:
        """TK-ACC-C31612: long message ending with thank you must not be small_talk."""
        message = (
            "Our Agora account has been flagged for suspicious activity. "
            "We need to complete company verification to restore access. "
            "Company: ExampleCorp, Country: Singapore, Address: 123 Orchard Road, "
            "Service URL: https://example.com, Email: admin@example.com, "
            "Phone: +65-1234-5678. "
            "[Use Case]\nWe use Agora for live streaming to 10k concurrent viewers.\n"
            "Thank you."
        )
        # No LLM key -> falls back to deterministic
        with patch.dict(os.environ, {}, clear=True):
            decision = decide_support_route(message)

        # Must NOT be small_talk (gratitude regex should be blocked by billing keywords)
        self.assertNotEqual(decision.scope_label, "small_talk")
        self.assertNotEqual(decision.execution_action, "controlled_response")

    def test_short_pure_thank_you_still_small_talk(self) -> None:
        """Short pure gratitude still routes to small_talk."""
        decision = decide_support_route("Thank you!")
        self.assertEqual(decision.scope_label, "small_talk")
        self.assertEqual(decision.execution_action, "controlled_response")


if __name__ == "__main__":
    unittest.main()
