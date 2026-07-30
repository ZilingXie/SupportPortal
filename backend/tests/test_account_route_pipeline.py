from __future__ import annotations

import os
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.services.account_route_pipeline import (
    AccountRouteStageAttempt,
    classification_for_corrected_route,
    decide_account_route,
)
from backend.services.support_router import SupportRouteDecision


def _attempt(payload: dict[str, object]) -> AccountRouteStageAttempt:
    return AccountRouteStageAttempt(payload=payload, attempted=True)


class AccountRoutePipelineTests(unittest.TestCase):
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

    def test_pipeline_is_scoped_to_account_entrypoints(self) -> None:
        main_source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertEqual(main_source.count("decide_account_route("), 3)
        self.assertIn("route_agent=decide_support_route", main_source)

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

    def test_account_suspension_remains_separate_from_account_verification(self) -> None:
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
                        "action": "restore",
                        "target": "account_access",
                        "evidence": "restore our access",
                    },
                }
            ),
            _attempt(
                {
                    "automation_subcategory": "account_suspension",
                    "confidence": 0.96,
                    "reason_code": "registered_account_suspension",
                }
            ),
        ]
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            side_effect=attempts,
        ):
            result = decide_account_route(
                "Our account is suspended. Please review it and restore our access."
            )

        self.assertEqual(result.primary_label, "Agora")
        self.assertEqual(result.secondary_label, "Automation / Account Suspension")
        self.assertEqual(result.classification["automation_subcategory"], "account_suspension")
        self.assertEqual(result.decision.execution_action, "account_suspension")
        self.assertEqual(result.decision.route_family, "automated")

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
        ]
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            side_effect=attempts,
        ) as invoke_stage:
            result = decide_account_route("Please change our payment method to invoice billing.")

        self.assertEqual(result.primary_label, "Agora")
        self.assertEqual(result.secondary_label, "Account & Billing")
        self.assertEqual(result.classification["agora_route"], "account_billing")
        self.assertEqual(result.classification["route_reason_code"], "account_billing_request")
        self.assertEqual(invoke_stage.call_count, 2)

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
