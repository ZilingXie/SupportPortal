from __future__ import annotations

import os
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.services.account_route_pipeline import (
    AccountRouteStageAttempt,
    ACCOUNT_ROUTE_PIPELINE_VERSION,
    account_case_labels,
    classification_for_corrected_route,
    decide_account_route,
)
from backend.services.prompts.account_routing import (
    build_account_agora_system_prompt,
    build_account_automation_system_prompt,
)
from backend.services.support_router import SupportRouteDecision


def _attempt(payload: dict[str, object]) -> AccountRouteStageAttempt:
    return AccountRouteStageAttempt(payload=payload, attempted=True)


class AccountRoutePipelineTests(unittest.TestCase):
    def test_agora_prompt_prioritizes_legal_compliance_complaints_over_evidence_commands(self) -> None:
        prompt = build_account_agora_system_prompt()

        self.assertIn("legal_compliance_request", prompt)
        self.assertIn("third-party fraud complaint", prompt)
        self.assertIn("extract logs, preserve evidence", prompt)

    def test_legal_compliance_agora_route_stops_before_automation_router(self) -> None:
        attempts = [
            _attempt({
                "intent_class": "agora",
                "intent_confidence": 0.99,
                "reason_code": "agora_case",
            }),
            _attempt({
                "agora_route": "uncategorized",
                "confidence": 0.98,
                "reason_code": "legal_compliance_request",
                "backend_operation": None,
                "evidence_spans": ["third-party fraud complaint", "extract server logs"],
            }),
        ]
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            side_effect=attempts,
        ) as invoke_stage:
            result = decide_account_route(
                "A third-party fraud complaint asks Agora and regulators to extract server logs as evidence."
            )

        self.assertEqual(result.secondary_label, "Agora / Uncategorized")
        self.assertEqual(result.decision.route_family, "human_review")
        self.assertEqual(result.decision.execution_action, "human_review_required")
        self.assertEqual(result.classification["route_reason_code"], "legal_compliance_request")
        self.assertEqual(invoke_stage.call_count, 2)

    def test_automation_prompt_treats_complete_suspension_review_template_as_fraud_evidence(self) -> None:
        prompt = build_account_automation_system_prompt()

        self.assertIn("Company Information, Contact Information", prompt)
        self.assertIn("Use Case, and Payment Information", prompt)
        self.assertIn("strong fraud-review workflow evidence", prompt)
        self.assertIn("Non-fraud suspension", prompt)
        self.assertNotIn("account_suspension:", prompt)

    def test_human_review_golden_fixture_covers_all_operator_labels(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "account_route_golden_cases.json"
        cases = json.loads(fixture_path.read_text(encoding="utf-8"))

        self.assertEqual(len(cases), 17)
        self.assertEqual({item["case_id"] for item in cases}, {
            "12515", "12512", "12511", "12506", "12505", "12502", "12500",
            "12497", "12496", "12486", "12484", "12480", "12479", "12476",
            "12460", "12458", "12456",
        })
        self.assertTrue(all(item["primary_label"] == "Agora" for item in cases))
        self.assertNotIn("Support Request", {item["primary_label"] for item in cases})
        self.assertNotIn("mixed", {item["reason_code"] for item in cases})
        by_case_id = {item["case_id"]: item for item in cases}
        self.assertEqual(by_case_id["12458"]["secondary_label"], "Agora Technical")
        self.assertEqual(by_case_id["12458"]["reason_code"], "technical_request")

    def test_route_correction_uses_layered_labels_without_activating_handler(self) -> None:
        conversation = classification_for_corrected_route(
            scope_label="conversation",
            route_family="conversation",
            execution_action="follow_up",
        )
        self.assertEqual(conversation["primary_label"], "Conversation")
        self.assertEqual(conversation["secondary_label"], "Follow-up")

        automation = classification_for_corrected_route(
            scope_label="billing",
            route_family="automated",
            execution_action="detailed_invoice",
        )
        self.assertEqual(automation["secondary_label"], "Automation / Detailed Invoice")
        self.assertEqual(automation["handler_binding_status"], "completed")

        account_billing = classification_for_corrected_route(
            scope_label="account_billing",
            route_family="human_review",
            execution_action="human_review_required",
            subcategory="account_suspension",
            previous={"automation_mode": "classification_only"},
        )
        self.assertEqual(
            account_billing["secondary_label"],
            "Account & Billing / Account Suspension",
        )
        self.assertEqual(account_billing["account_billing_subcategory"], "account_suspension")
        self.assertIsNone(account_billing["automation_mode"])

    def test_pipeline_is_scoped_to_account_entrypoints(self) -> None:
        main_source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertEqual(main_source.count("decide_account_route("), 3)
        self.assertEqual(main_source.count("require_latest=True"), 3)
        self.assertIn("route_agent=decide_support_route", main_source)

    def test_latest_account_route_ignores_legacy_mode(self) -> None:
        legacy_router = unittest.mock.Mock()
        with patch.dict(os.environ, {"ACCOUNT_ROUTER_MODE": "legacy"}), patch(
            "backend.services.account_route_pipeline._invoke_stage",
            return_value=_attempt(
                {
                    "intent_class": "conversation",
                    "conversation_action": "follow_up",
                    "intent_confidence": 0.97,
                    "action_confidence": 0.94,
                    "reason_code": "conversation_follow_up",
                }
            ),
        ):
            result = decide_account_route(
                "Thanks",
                legacy_router=legacy_router,
                require_latest=True,
            )

        self.assertEqual(result.secondary_label, "Follow-up")
        self.assertEqual(result.classification["pipeline_version"], ACCOUNT_ROUTE_PIPELINE_VERSION)
        legacy_router.assert_not_called()

    def test_latest_account_route_normalizes_missing_credentials_fallback(self) -> None:
        legacy_router = unittest.mock.Mock(
            return_value=SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                route_family="rag_product_support",
                execution_action="rag",
                confidence=0.88,
                reason="legacy technical fallback",
                router_source="llm_semantic",
            )
        )
        missing_credentials = AccountRouteStageAttempt(
            payload=None,
            attempted=False,
            failure_type="missing_credentials",
            failure_source="intent_classifier",
        )
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            return_value=missing_credentials,
        ):
            result = decide_account_route(
                "Please help with my Agora account.",
                legacy_router=legacy_router,
                require_latest=True,
            )

        self.assertEqual(result.primary_label, "Agora")
        self.assertEqual(result.secondary_label, "Agora Technical")
        self.assertEqual(result.classification["pipeline_version"], ACCOUNT_ROUTE_PIPELINE_VERSION)
        self.assertEqual(result.decision.router_source, "account_legacy_fallback")
        legacy_router.assert_called_once()

    def test_missing_credentials_suspension_fallback_uses_account_billing_contract(self) -> None:
        legacy_router = unittest.mock.Mock(
            return_value=SupportRouteDecision(
                scope_label="billing",
                route="account_verification",
                route_family="automated",
                execution_action="account_verification",
                confidence=0.94,
                reason="billing_account_suspension",
                semantic_intent="billing.account_suspension",
                router_source="deterministic_billing",
            )
        )
        missing_credentials = AccountRouteStageAttempt(
            payload=None,
            attempted=False,
            failure_type="missing_credentials",
            failure_source="intent_classifier",
        )
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            return_value=missing_credentials,
        ):
            result = decide_account_route(
                "Our balance ran out and the account was suspended.",
                legacy_router=legacy_router,
                require_latest=True,
            )

        self.assertEqual(result.secondary_label, "Account & Billing / Account Suspension")
        self.assertEqual(result.decision.execution_action, "human_review_required")
        self.assertEqual(result.decision.route_family, "human_review")
        self.assertEqual(result.decision.semantic_intent, "account_billing.account_suspension")

    def test_legacy_support_request_automation_uses_canonical_automation_labels(self) -> None:
        labels = account_case_labels(
            {
                "route_family": "automated",
                "execution_action": "account_suspension",
                "route_classification": {
                    "pipeline_version": "account-layered-router-v1",
                    "intent_class": "support_request",
                    "agora_route": "automation",
                    "automation_subcategory": "account_suspension",
                },
            }
        )

        self.assertEqual(labels, ("Agora", "Automation / Account Suspension"))

    def test_conversation_stops_after_intent_classifier(self) -> None:
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            return_value=_attempt(
                {
                    "intent_class": "conversation",
                    "conversation_action": "resolve",
                    "intent_confidence": 0.97,
                    "action_confidence": 0.94,
                    "reason_code": "conversation_resolution",
                }
            ),
        ) as invoke_stage:
            result = decide_account_route("It works now, thanks.")

        self.assertEqual(result.primary_label, "Conversation")
        self.assertEqual(result.secondary_label, "Resolve")
        self.assertEqual(result.classification["route_target"], "none")
        self.assertEqual(invoke_stage.call_count, 1)

    def test_agora_technical_uses_intent_then_agora_router(self) -> None:
        attempts = [
            _attempt(
                {
                    "intent_class": "agora",
                    "intent_confidence": 0.98,
                    "reason_code": "agora_case",
                }
            ),
            _attempt(
                {
                    "agora_route": "technical",
                    "confidence": 0.95,
                    "reason_code": "technical_request",
                }
            ),
        ]
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            side_effect=attempts,
        ) as invoke_stage:
            result = decide_account_route("How do I generate an RTC token?")

        self.assertEqual(result.primary_label, "Agora")
        self.assertEqual(result.secondary_label, "Agora Technical")
        self.assertEqual(result.classification["route_target"], "rag")
        self.assertEqual(invoke_stage.call_count, 2)

    def test_enablement_uses_all_three_stages(self) -> None:
        attempts = [
            _attempt(
                {
                    "intent_class": "agora",
                    "intent_confidence": 0.99,
                    "reason_code": "agora_case",
                }
            ),
            _attempt(
                {
                    "agora_route": "automation",
                    "confidence": 0.97,
                    "reason_code": "explicit_backend_operation",
                    "backend_operation": {
                        "action": "enable",
                        "target": "media_relay",
                        "evidence": "enable Media Relay from your end",
                    },
                }
            ),
            _attempt(
                {
                    "automation_subcategory": "enablement",
                    "confidence": 0.96,
                    "reason_code": "registered_enablement",
                }
            ),
        ]
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            side_effect=attempts,
        ) as invoke_stage:
            result = decide_account_route(
                "Please enable Media Relay from your end for App ID 7da36383d624411698e5c0bc1fda6324."
            )

        self.assertEqual(result.primary_label, "Agora")
        self.assertEqual(result.secondary_label, "Automation / Enablement")
        self.assertEqual(result.classification["route_target"], "automation")
        self.assertEqual(result.classification["handler_binding_status"], "active")
        self.assertEqual(result.decision.route_family, "automated")
        self.assertEqual(result.decision.execution_action, "enablement")
        self.assertEqual(invoke_stage.call_count, 3)

    def test_non_fraud_account_suspension_routes_to_account_billing(self) -> None:
        attempts = [
            _attempt(
                {
                    "intent_class": "agora",
                    "intent_confidence": 0.99,
                    "reason_code": "agora_case",
                }
            ),
            _attempt(
                {
                    "agora_route": "account_billing",
                    "confidence": 0.97,
                    "reason_code": "account_billing_request",
                    "backend_operation": None,
                }
            ),
            _attempt(
                {
                    "account_billing_subcategory": "account_suspension",
                    "confidence": 0.96,
                    "reason_code": "registered_account_suspension",
                    "additional_intents": ["refund"],
                }
            ),
        ]
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            side_effect=attempts,
        ) as invoke_stage:
            result = decide_account_route(
                "Our account is suspended. Please review it and restore our access."
            )

        self.assertEqual(result.primary_label, "Agora")
        self.assertEqual(result.secondary_label, "Account & Billing / Account Suspension")
        self.assertEqual(result.classification["account_billing_subcategory"], "account_suspension")
        self.assertEqual(result.classification["account_billing_additional_intents"], ["refund"])
        self.assertIsNone(result.classification["automation_subcategory"])
        self.assertEqual(result.decision.execution_action, "human_review_required")
        self.assertEqual(result.decision.route_family, "human_review")
        self.assertEqual(result.decision.semantic_intent, "account_billing.account_suspension")
        self.assertEqual(
            result.classification["stage_reason_codes"]["account_billing_router"],
            "registered_account_suspension",
        )
        self.assertEqual(invoke_stage.call_count, 3)
        self.assertEqual(
            [call.kwargs["prompt_key"] for call in invoke_stage.call_args_list],
            [
                "account-intent-classifier-system",
                "account-agora-router-system",
                "account-account-billing-router-system",
            ],
        )

    def test_invalid_account_billing_output_falls_back_to_other_with_reason(self) -> None:
        attempts = [
            _attempt({"intent_class": "agora", "intent_confidence": 0.99, "reason_code": "agora_case"}),
            _attempt({
                "agora_route": "account_billing",
                "confidence": 0.97,
                "reason_code": "account_billing_request",
            }),
            _attempt({
                "account_billing_subcategory": "refund",
                "confidence": 0.96,
                "reason_code": "account_billing_other",
            }),
        ]
        with patch("backend.services.account_route_pipeline._invoke_stage", side_effect=attempts):
            result = decide_account_route("Please refund our unused balance.")

        self.assertEqual(result.secondary_label, "Account & Billing / Other")
        self.assertEqual(result.classification["account_billing_subcategory"], "other")
        self.assertEqual(result.classification["route_reason_code"], "invalid_account_billing_output")
        self.assertEqual(result.decision.semantic_intent, "account_billing.other")

    def test_other_automation_without_backend_operation_fails_closed(self) -> None:
        attempts = [
            _attempt(
                {
                    "intent_class": "agora",
                    "intent_confidence": 0.99,
                    "reason_code": "agora_case",
                }
            ),
            _attempt(
                {
                    "agora_route": "automation",
                    "confidence": 0.97,
                    "reason_code": "explicit_backend_operation",
                    "backend_operation": None,
                }
            ),
        ]
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            side_effect=attempts,
        ) as invoke_stage:
            result = decide_account_route("Please change something on my account.")

        self.assertEqual(result.secondary_label, "Agora / Uncategorized")
        self.assertEqual(result.classification["route_target"], "human_review")
        self.assertEqual(
            result.classification["route_reason_code"],
            "insufficient_backend_operation_evidence",
        )
        self.assertEqual(invoke_stage.call_count, 2)

    def test_fraud_account_is_separate_from_non_fraud_suspension(self) -> None:
        attempts = [
            _attempt({"intent_class": "agora", "intent_confidence": 0.99, "reason_code": "agora_case"}),
            _attempt({
                "agora_route": "automation",
                "confidence": 0.98,
                "reason_code": "explicit_backend_operation",
                "backend_operation": {
                    "action": "review",
                    "target": "fraud_account_restriction",
                    "evidence": "blocked for suspicious activity",
                },
            }),
            _attempt({
                "automation_subcategory": "fraud_account",
                "confidence": 0.97,
                "reason_code": "registered_fraud_account",
            }),
        ]
        with patch("backend.services.account_route_pipeline._invoke_stage", side_effect=attempts):
            result = decide_account_route(
                "Our account was blocked for suspicious activity. Please review our company details."
            )

        self.assertEqual(result.secondary_label, "Automation / Fraud Account")
        self.assertEqual(result.decision.execution_action, "fraud_account")
        self.assertEqual(result.decision.semantic_intent, "automation.fraud_account_review")

    def test_independent_unrelated_request_is_uncertain_without_agora_router(self) -> None:
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            return_value=_attempt(
                {
                    "intent_class": "uncertain",
                    "intent_confidence": 0.95,
                    "reason_code": "out_of_scope_or_unknown",
                }
            ),
        ) as invoke_stage:
            result = decide_account_route("Fix my Agora token and reset my AWS password.")

        self.assertEqual(result.primary_label, "Uncertain")
        self.assertEqual(result.secondary_label, "Human Review")
        self.assertEqual(result.classification["route_target"], "human_review")
        self.assertEqual(result.classification["human_review_reason"], "out_of_scope_or_unknown")
        self.assertEqual(invoke_stage.call_count, 1)

    def test_low_intent_confidence_fails_closed(self) -> None:
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            return_value=_attempt(
                {
                    "intent_class": "agora",
                    "intent_confidence": 0.4,
                    "reason_code": "agora_case",
                }
            ),
        ) as invoke_stage:
            result = decide_account_route("Maybe something is wrong.")

        self.assertEqual(result.primary_label, "Uncertain")
        self.assertEqual(result.secondary_label, "Human Review")
        self.assertEqual(result.classification["human_review_reason"], "low_intent_confidence")
        self.assertEqual(result.classification["route_reason_code"], "low_intent_confidence")
        self.assertEqual(invoke_stage.call_count, 1)

    def test_account_billing_is_a_stable_agora_classification(self) -> None:
        attempts = [
            _attempt(
                {
                    "intent_class": "agora",
                    "intent_confidence": 0.98,
                    "reason_code": "agora_case",
                }
            ),
            _attempt(
                {
                    "agora_route": "account_billing",
                    "confidence": 0.96,
                    "reason_code": "account_billing_request",
                    "additional_intents": [],
                }
            ),
            _attempt(
                {
                    "account_billing_subcategory": "other",
                    "confidence": 0.95,
                    "reason_code": "account_billing_other",
                    "additional_intents": ["payment_method"],
                }
            ),
        ]
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            side_effect=attempts,
        ) as invoke_stage:
            result = decide_account_route("Please change our payment method to invoice billing.")

        self.assertEqual(result.primary_label, "Agora")
        self.assertEqual(result.secondary_label, "Account & Billing / Other")
        self.assertEqual(result.classification["agora_route"], "account_billing")
        self.assertEqual(result.classification["route_reason_code"], "account_billing_other")
        self.assertEqual(result.classification["account_billing_subcategory"], "other")
        self.assertEqual(invoke_stage.call_count, 3)

    def test_vague_backend_request_stays_uncategorized(self) -> None:
        attempts = [
            _attempt(
                {
                    "intent_class": "agora",
                    "intent_confidence": 0.97,
                    "reason_code": "agora_case",
                }
            ),
            _attempt(
                {
                    "agora_route": "automation",
                    "confidence": 0.94,
                    "reason_code": "explicit_backend_operation",
                    "backend_operation": None,
                }
            ),
        ]
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            side_effect=attempts,
        ) as invoke_stage:
            result = decide_account_route("Please change something on my account.")

        self.assertEqual(result.primary_label, "Agora")
        self.assertEqual(result.secondary_label, "Agora / Uncategorized")
        self.assertEqual(result.classification["agora_route"], "uncategorized")
        self.assertEqual(
            result.classification["route_reason_code"],
            "insufficient_backend_operation_evidence",
        )
        self.assertEqual(invoke_stage.call_count, 2)

    def test_quota_increase_uses_registered_quota_handler(self) -> None:
        attempts = [
            _attempt(
                {
                    "intent_class": "agora",
                    "intent_confidence": 0.99,
                    "reason_code": "agora_case",
                }
            ),
            _attempt(
                {
                    "agora_route": "automation",
                    "confidence": 0.97,
                    "reason_code": "explicit_backend_operation",
                    "backend_operation": {
                        "action": "increase",
                        "target": "rtc_concurrency_limit",
                        "evidence": "increase our RTC concurrency limit",
                    },
                }
            ),
            _attempt(
                {
                    "automation_subcategory": "quota",
                    "automation_candidate": None,
                    "confidence": 0.96,
                    "reason_code": "registered_quota",
                    "risk_flags": [],
                }
            ),
        ]
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            side_effect=attempts,
        ):
            result = decide_account_route("Please increase our RTC concurrency limit.")

        self.assertEqual(result.primary_label, "Agora")
        self.assertEqual(result.secondary_label, "Automation / Quota")
        self.assertEqual(result.classification["automation_subcategory"], "quota")
        self.assertIsNone(result.classification["automation_candidate"])
        self.assertEqual(result.classification["route_reason_code"], "registered_quota")
        self.assertEqual(result.decision.scope_label, "quota")
        self.assertEqual(result.decision.execution_action, "quota")
        self.assertEqual(result.decision.semantic_intent, "quota.capacity_request")
        self.assertEqual(
            result.classification["stage_reason_codes"],
            {
                "intent_classifier": "agora_case",
                "agora_router": "explicit_backend_operation",
                "automation_router": "registered_quota",
            },
        )

    def test_shadow_mode_preserves_legacy_route_for_non_automation_result(self) -> None:
        legacy_decision = SupportRouteDecision(
            scope_label="agora_non_technical",
            route="web_search",
            confidence=0.88,
            reason="legacy_company_info",
            route_family="web_company_info",
            execution_action="web_search",
            router_source="llm_semantic",
        )
        attempts = [
            _attempt(
                {
                    "intent_class": "agora",
                    "intent_confidence": 0.98,
                    "reason_code": "agora_case",
                }
            ),
            _attempt(
                {
                    "agora_route": "technical",
                    "confidence": 0.95,
                    "reason_code": "technical_request",
                }
            ),
        ]
        with patch.dict(os.environ, {"ACCOUNT_ROUTER_MODE": "shadow"}), patch(
            "backend.services.account_route_pipeline._invoke_stage",
            side_effect=attempts,
        ):
            result = decide_account_route(
                "How do I generate an RTC token?",
                legacy_router=lambda *_args, **_kwargs: legacy_decision,
            )

        self.assertEqual(result.decision.execution_action, "web_search")
        self.assertEqual(result.secondary_label, "Agora Non-technical")
        self.assertEqual(
            result.classification["shadow_classification"]["secondary_label"],
            "Agora Technical",
        )


if __name__ == "__main__":
    unittest.main()
