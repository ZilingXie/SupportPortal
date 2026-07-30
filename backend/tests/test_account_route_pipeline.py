from __future__ import annotations

import os
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
                    "reason_code": "explicit_resolution",
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
                    "intent_class": "support_request",
                    "support_scope": "agora",
                    "intent_confidence": 0.98,
                    "scope_confidence": 0.96,
                    "reason_code": "agora_support_request",
                }
            ),
            _attempt(
                {
                    "agora_route": "technical",
                    "confidence": 0.95,
                    "reason_code": "sdk_integration",
                }
            ),
        ]
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            side_effect=attempts,
        ) as invoke_stage:
            result = decide_account_route("How do I generate an RTC token?")

        self.assertEqual(result.primary_label, "Support Request")
        self.assertEqual(result.secondary_label, "Agora Technical")
        self.assertEqual(result.classification["route_target"], "rag")
        self.assertEqual(invoke_stage.call_count, 2)

    def test_enablement_uses_all_three_stages(self) -> None:
        attempts = [
            _attempt(
                {
                    "intent_class": "support_request",
                    "support_scope": "agora",
                    "intent_confidence": 0.99,
                    "scope_confidence": 0.98,
                    "reason_code": "agora_support_request",
                }
            ),
            _attempt(
                {
                    "agora_route": "automation",
                    "confidence": 0.97,
                    "reason_code": "backend_operation",
                }
            ),
            _attempt(
                {
                    "automation_subcategory": "enablement",
                    "confidence": 0.96,
                    "reason_code": "feature_activation",
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

        self.assertEqual(result.primary_label, "Support Request")
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
                    "intent_class": "support_request",
                    "support_scope": "agora",
                    "intent_confidence": 0.99,
                    "scope_confidence": 0.98,
                    "reason_code": "agora_support_request",
                }
            ),
            _attempt(
                {
                    "agora_route": "automation",
                    "confidence": 0.97,
                    "reason_code": "backend_operation",
                }
            ),
            _attempt(
                {
                    "automation_subcategory": "account_suspension",
                    "confidence": 0.96,
                    "reason_code": "account_suspension_review",
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

        self.assertEqual(result.primary_label, "Support Request")
        self.assertEqual(result.secondary_label, "Automation / Account Suspension")
        self.assertEqual(result.classification["automation_subcategory"], "account_suspension")
        self.assertEqual(result.decision.execution_action, "account_suspension")
        self.assertEqual(result.decision.route_family, "automated")

    def test_mixed_scope_fails_closed_without_agora_router(self) -> None:
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            return_value=_attempt(
                {
                    "intent_class": "support_request",
                    "support_scope": "mixed",
                    "intent_confidence": 0.95,
                    "scope_confidence": 0.91,
                    "reason_code": "mixed_scope",
                }
            ),
        ) as invoke_stage:
            result = decide_account_route("Fix my Agora token and reset my AWS password.")

        self.assertEqual(result.primary_label, "Support Request")
        self.assertEqual(result.secondary_label, "Human Review")
        self.assertEqual(result.classification["route_target"], "human_review")
        self.assertEqual(result.classification["human_review_reason"], "mixed_scope")
        self.assertEqual(invoke_stage.call_count, 1)

    def test_low_intent_confidence_fails_closed(self) -> None:
        with patch(
            "backend.services.account_route_pipeline._invoke_stage",
            return_value=_attempt(
                {
                    "intent_class": "support_request",
                    "support_scope": "agora",
                    "intent_confidence": 0.4,
                    "scope_confidence": 0.99,
                    "reason_code": "weak_intent",
                }
            ),
        ) as invoke_stage:
            result = decide_account_route("Maybe something is wrong.")

        self.assertEqual(result.primary_label, "Unclear")
        self.assertEqual(result.secondary_label, "Human Review")
        self.assertEqual(result.classification["human_review_reason"], "low_intent_confidence")
        self.assertEqual(invoke_stage.call_count, 1)

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
                    "intent_class": "support_request",
                    "support_scope": "agora",
                    "intent_confidence": 0.98,
                    "scope_confidence": 0.97,
                    "reason_code": "agora_support_request",
                }
            ),
            _attempt(
                {
                    "agora_route": "technical",
                    "confidence": 0.95,
                    "reason_code": "sdk_integration",
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
