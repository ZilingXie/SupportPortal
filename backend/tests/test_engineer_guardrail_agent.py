from __future__ import annotations

import unittest

from backend.services.engineer_guardrail_agent import (
    GUARDRAIL_VERSION,
    run_engineer_guardrail_final,
)


def _reply_readiness(**overrides):
    defaults = {
        "has_conclusion": True,
        "has_proof": True,
        "has_solution_or_next_step": True,
        "reply_scope": "symptom_and_workaround_only",
        "conclusion_summary": "The issue is caused by SDK 4.2.1 on Android 14.",
        "proof_summary": "Reproduced on Android 14 with SDK 4.2.1 only.",
        "proof_anchors": ["Android 14", "SDK 4.2.1"],
        "solution_or_next_step": "Upgrade to SDK 4.2.2 and clear the token cache.",
        "blockers": [],
        "advisory_followups": [],
        "critique": "The evidence supports a customer-safe reply.",
        "ready_for_customer_reply": True,
    }
    defaults.update(overrides)
    return defaults


class EngineerGuardrailAgentTests(unittest.TestCase):
    def test_guardrail_blocks_when_no_draft(self):
        packet = run_engineer_guardrail_final(
            draft_customer_reply="",
            reply_readiness=_reply_readiness(),
        )
        self.assertEqual(packet["decision"], "blocked")
        self.assertIn("No draft customer reply provided.", packet["blockers"])
        self.assertEqual(packet["guardrail_version"], GUARDRAIL_VERSION)

    def test_guardrail_blocks_when_not_ready_for_customer_reply(self):
        packet = run_engineer_guardrail_final(
            draft_customer_reply="Please upgrade to SDK 4.2.2.",
            reply_readiness=_reply_readiness(ready_for_customer_reply=False),
        )
        self.assertEqual(packet["decision"], "blocked")
        self.assertTrue(
            any("ready_for_customer_reply" in b.lower() for b in packet["blockers"]),
            f"blockers should mention ready_for_customer_reply: {packet['blockers']}",
        )

    def test_guardrail_approves_when_all_checks_pass(self):
        packet = run_engineer_guardrail_final(
            draft_customer_reply="Hi, Taylor\n\nPlease upgrade to SDK 4.2.2 and retry token renewal.",
            reply_readiness=_reply_readiness(),
            requester="Taylor",
            customer_id="C-001",
            language_hint="en",
        )
        self.assertEqual(packet["decision"], "approved_for_final_engineer_review")
        self.assertEqual(len(packet["blockers"]), 0)
        self.assertTrue(packet["checks"]["proof"]["passed"])
        self.assertTrue(packet["checks"]["no_internal_leakage"]["passed"])
        self.assertTrue(packet["checks"]["no_unsupported_claims"]["passed"])
        self.assertTrue(packet["checks"]["no_application_signature"]["passed"])

    def test_guardrail_detects_internal_leakage(self):
        packet = run_engineer_guardrail_final(
            draft_customer_reply="Hi, Taylor\n\nThis is engineer-only internal use only. Please upgrade.",
            reply_readiness=_reply_readiness(),
            requester="Taylor",
            customer_id="C-001",
            language_hint="en",
        )
        self.assertEqual(packet["decision"], "blocked")
        self.assertFalse(packet["checks"]["no_internal_leakage"]["passed"])

    def test_guardrail_detects_unsupported_claims(self):
        packet = run_engineer_guardrail_final(
            draft_customer_reply="Hi, Taylor\n\nWe guarantee this will 100% fix your issue.",
            reply_readiness=_reply_readiness(),
            requester="Taylor",
            customer_id="C-001",
            language_hint="en",
        )
        self.assertEqual(packet["decision"], "blocked")
        self.assertFalse(packet["checks"]["no_unsupported_claims"]["passed"])

    def test_guardrail_applies_email_style_normalization(self):
        packet = run_engineer_guardrail_final(
            draft_customer_reply="please upgrade to SDK 4.2.2",
            reply_readiness=_reply_readiness(),
            requester="Taylor",
            customer_id="C-001",
            language_hint="en",
        )
        self.assertEqual(packet["decision"], "approved_for_final_engineer_review")
        self.assertIn("Hi, Taylor", packet["customer_reply"])
        self.assertNotIn("Best Regards,", packet["customer_reply"])

    def test_guardrail_blocks_application_side_signature(self):
        packet = run_engineer_guardrail_final(
            draft_customer_reply="Hi, Taylor\n\nPlease retry.\n\nBest Regards,\nSid",
            reply_readiness=_reply_readiness(),
            requester="Taylor",
            customer_id="C-001",
            language_hint="en",
        )

        self.assertEqual(packet["decision"], "blocked")
        self.assertFalse(packet["checks"]["no_application_signature"]["passed"])

    def test_guardrail_blocks_legacy_standalone_sid_signature(self):
        packet = run_engineer_guardrail_final(
            draft_customer_reply="Hi, Taylor\n\nPlease retry.\n\nSid",
            reply_readiness=_reply_readiness(),
            requester="Taylor",
            customer_id="C-001",
            language_hint="en",
        )

        self.assertEqual(packet["decision"], "blocked")
        self.assertFalse(packet["checks"]["no_application_signature"]["passed"])

    def test_guardrail_includes_evidence_refs(self):
        packet = run_engineer_guardrail_final(
            draft_customer_reply="Hi, Taylor\n\nPlease upgrade to SDK 4.2.2.",
            reply_readiness=_reply_readiness(),
            active_review={"review_id": "REV-001", "review_decision": "approved"},
            evidence_packet={"evidence_packet_id": "EP-001", "answer_summary": "Test evidence"},
            task_results=[{"task_id": "TASK-001"}],
            requester="Taylor",
            customer_id="C-001",
            language_hint="en",
        )
        self.assertGreater(len(packet["evidence_refs"]), 0)
        ref_sources = [ref["source"] for ref in packet["evidence_refs"]]
        self.assertIn("evidence_packet", ref_sources)
        self.assertIn("active_review", ref_sources)
        self.assertIn("task_result", ref_sources)

    def test_guardrail_blocks_when_proof_missing(self):
        packet = run_engineer_guardrail_final(
            draft_customer_reply="Hi, Taylor\n\nPlease try again.",
            reply_readiness=_reply_readiness(
                has_proof=False,
                proof_summary="",
                ready_for_customer_reply=False,
            ),
            requester="Taylor",
            customer_id="C-001",
            language_hint="en",
        )
        self.assertEqual(packet["decision"], "blocked")
        self.assertFalse(packet["checks"]["proof"]["passed"])

    def test_guardrail_accepts_agent_self_reported_readiness_without_derived_proof(self):
        packet = run_engineer_guardrail_final(
            draft_customer_reply="Hi, Taylor\n\nPlease try again after the packaging fix.",
            reply_readiness=_reply_readiness(
                has_proof=False,
                proof_summary="",
                ready_for_customer_reply=True,
            ),
            requester="Taylor",
            customer_id="C-001",
            language_hint="en",
        )
        self.assertEqual(packet["decision"], "approved_for_final_engineer_review")
        self.assertTrue(packet["checks"]["proof"]["passed"])

    def test_guardrail_accepts_persisted_human_guidance_without_investigation_proof(self):
        packet = run_engineer_guardrail_final(
            draft_customer_reply="Please upgrade to SDK 4.2.2 and retry token renewal.",
            reply_readiness=_reply_readiness(
                source_mode="human_guided_reply",
                human_source_message_id="INV-1-m-3",
                human_source_slack_event_id="Ev-Slack-1",
                has_proof=False,
                proof_summary="",
            ),
            requester="Taylor",
            customer_id="C-001",
            language_hint="en",
        )
        self.assertEqual(packet["decision"], "approved_for_final_engineer_review")
        self.assertTrue(packet["checks"]["proof"]["passed"])
        self.assertIn(
            {"source": "human_guidance", "ref": "INV-1-m-3"},
            packet["evidence_refs"],
        )

    def test_guardrail_blocks_human_guidance_without_persisted_source(self):
        packet = run_engineer_guardrail_final(
            draft_customer_reply="Please retry.",
            reply_readiness=_reply_readiness(
                source_mode="human_guided_reply",
                human_source_message_id="",
                human_source_slack_event_id="Ev-Slack-2",
                has_proof=False,
                proof_summary="",
            ),
            requester="Taylor",
            customer_id="C-001",
            language_hint="en",
        )
        self.assertEqual(packet["decision"], "blocked")
        self.assertFalse(packet["checks"]["proof"]["passed"])
        self.assertTrue(any("persisted source" in item for item in packet["blockers"]))


if __name__ == "__main__":
    unittest.main()
