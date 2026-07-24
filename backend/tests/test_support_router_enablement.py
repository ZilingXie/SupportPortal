from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from backend.services.support_router import decide_support_route


SAMPLE = (
    "Dear team, my app id: 7da36383d624411698e5c0bc1fda6324. "
    "We enable co host authentication token but pk view not show, so please enable medial relay feature from your end."
)


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class EnablementRoutingTests(unittest.TestCase):
    def test_sample_uses_canonical_automated_route(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            decision = decide_support_route(SAMPLE, semantic_first=True)

        self.assertEqual(decision.scope_label, "enablement")
        self.assertEqual(decision.execution_action, "enablement")
        self.assertEqual(decision.route_family, "automated")
        self.assertEqual(decision.tooling_profile, "deterministic_enablement_intake")
        self.assertEqual(decision.semantic_intent, "enablement.feature_activation")
        self.assertEqual(decision.automation_eligibility, "eligible")

    def test_how_to_question_stays_technical(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            decision = decide_support_route("How do I enable and configure Media Relay in the SDK?")

        self.assertEqual(decision.scope_label, "agora_technical")
        self.assertEqual(decision.execution_action, "rag")
        self.assertNotEqual(decision.route_family, "automated")

    def test_llm_enablement_for_vague_request_fails_policy_gate(self) -> None:
        payload = {
            "output_text": json.dumps(
                {
                    "scope_label": "enablement",
                    "semantic_intent": "enablement.feature_activation",
                    "recommended_action": "enablement",
                    "automation_eligibility": "eligible",
                    "confidence": 0.98,
                    "reason": "Feature activation request.",
                    "matched_signals": ["enable feature"],
                    "evidence_spans": ["enable a feature"],
                    "risk_flags": [],
                }
            )
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch(
            "urllib.request.urlopen", return_value=_FakeResponse(payload)
        ):
            decision = decide_support_route("Please enable a feature for us.", semantic_first=True)

        self.assertEqual(decision.scope_label, "agora_technical")
        self.assertEqual(decision.execution_action, "rag")
        self.assertEqual(decision.not_automated_reason, "explicit_enablement_request_required")


if __name__ == "__main__":
    unittest.main()
