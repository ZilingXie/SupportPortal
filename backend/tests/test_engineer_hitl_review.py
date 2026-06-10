from __future__ import annotations

import types
import unittest
from unittest.mock import patch

from backend.services.engineer_hitl_review import build_engineer_auto_hitl_feedback


class EngineerHitlReviewTests(unittest.TestCase):
    def test_auto_review_falls_back_to_safe_candidate_without_model_credentials(self) -> None:
        with patch(
            "backend.services.engineer_hitl_review.resolve_model_profile",
            return_value=types.SimpleNamespace(api_key="", fallback_profiles=()),
        ):
            feedback = build_engineer_auto_hitl_feedback(
                client_ticket={
                    "ticket_id": "TK-AUTO-HITL-1",
                    "messages": [
                        {
                            "role": "customer",
                            "content": "Token renewal callback does not fire on Android 14.",
                        }
                    ],
                },
                engineer_case={
                    "engineer_case_id": "TK-AUTO-HITL-1-1",
                    "client_ticket_id": "TK-AUTO-HITL-1",
                    "engineer_agent_state": {
                        "run_id": "run-auto-hitl",
                        "evidence_packet_id": "packet-auto-hitl",
                        "issue_understanding": "Token renewal callback does not fire.",
                        "reply_readiness": {
                            "ready_for_customer_reply": True,
                            "has_conclusion": True,
                            "has_proof": True,
                            "conclusion_summary": "Android 14 with SDK 4.2.1 reproduces the token renew failure.",
                            "solution_or_next_step": "Upgrade to SDK 4.2.2.",
                        },
                    },
                },
                closed_investigation={
                    "id": "INV-AUTO-HITL",
                    "state": "closed",
                    "messages": [
                        {
                            "id": "msg-auto-hitl",
                            "role": "engineer_ai",
                            "content": "Draft ready for confirmation.",
                        }
                    ],
                },
                engineer_id="eng",
                customer_reply="Please upgrade to SDK 4.2.2 and retry token renewal.",
                created_at="2026-06-10T09:30:00+00:00",
            )

        self.assertEqual(feedback["feedback_id"], "hitl_auto_TK-AUTO-HITL-1-1")
        self.assertEqual(feedback["feedback_type"], "resolve")
        self.assertEqual(feedback["created_by"], "engineer_ai_auto_review")
        self.assertEqual(feedback["customer_reply_quality"], "sendable")
        self.assertEqual(feedback["memory_candidate"], "needs_review")
        self.assertEqual(feedback["memory_safety"], "internal_only")
        self.assertEqual(feedback["run_id"], "run-auto-hitl")
        self.assertEqual(feedback["message_id"], "msg-auto-hitl")
        self.assertEqual(feedback["evidence_packet_id"], "packet-auto-hitl")
        self.assertEqual(
            feedback["corrected_customer_reply"],
            "Please upgrade to SDK 4.2.2 and retry token renewal.",
        )


if __name__ == "__main__":
    unittest.main()
