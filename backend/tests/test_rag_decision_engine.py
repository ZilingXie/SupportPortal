from __future__ import annotations

import types
import unittest
from unittest.mock import patch, MagicMock

from backend.services.rag_service_client import RagTicketAnswerDetail
from backend.services.support_router import SupportResolution, SupportRouteDecision
from backend.services.troubleshooting_intake import TroubleshootingIntakeResult


def _make_rag_detail(
    *,
    answer: str = "Call joinChannel to join a channel.",
    confidence: float = 0.91,
    sources: list[str] | None = None,
    citations: list[dict[str, str]] | None = None,
    needs_engineer_guidance: bool = False,
    reason: str = "grounded_answer",
    evidence_summary: dict | None = None,
) -> RagTicketAnswerDetail:
    return RagTicketAnswerDetail(
        answer=answer,
        confidence=confidence,
        sources=sources or ["https://docs.agora.io/en/video-calling/get-started"],
        citations=citations if citations is not None else [{"chunk_id": "chunk-1"}],
        needs_engineer_guidance=needs_engineer_guidance,
        reason=reason,
        evidence_summary=evidence_summary or {},
    )


def _make_route_decision(
    *,
    scope_label: str = "agora_technical",
    route: str = "rag",
    confidence: float = 0.94,
    reason: str = "technical_question",
    execution_action: str = "rag",
    route_family: str = "agora_docs_rag",
    tooling_profile: str = "agora_docs_only",
) -> SupportRouteDecision:
    return SupportRouteDecision(
        scope_label=scope_label,
        route=route,
        confidence=confidence,
        reason=reason,
        matched_signals=["join channel"],
        response_language="en",
        route_family=route_family,
        execution_action=execution_action,
        tooling_profile=tooling_profile,
    )


def _noop_review_agent(**_kwargs) -> TroubleshootingIntakeResult:
    return TroubleshootingIntakeResult(
        issue_mode="answer",
        known_information={},
        missing_information=[],
        ready_for_engineer_ticket=False,
        customer_reply="",
    )


class RagDecisionEngineUnitTests(unittest.TestCase):

    def _call_evaluate(self, **overrides):
        from backend.services.rag_decision_engine import evaluate_rag_result

        defaults = dict(
            rag_detail=_make_rag_detail(),
            route_decision=_make_route_decision(),
            message="How do I join a channel?",
            client_intake_state=None,
            ticket_context=None,
            review_agent=None,
            product="audio_video_calling",
            ticket_subject="Join channel",
            requester=None,
            customer_id="C-001",
            message_id="2026-05-21T00:00:00+08:00",
            latest_assistant_message=None,
            run_id="run-test",
            ticket_id="TK-TEST-1",
        )
        defaults.update(overrides)
        return evaluate_rag_result(**defaults)

    def test_grounded_answer_low_risk_returns_answer_customer(self):
        decision = self._call_evaluate(
            rag_detail=_make_rag_detail(
                answer="Call joinChannel with the same channel name and token.",
                confidence=0.95,
                citations=[{"chunk_id": "chunk-1"}],
                reason="grounded_answer",
            ),
        )
        self.assertEqual(decision.execution_result.workflow_action, "answer_customer")
        self.assertFalse(decision.execution_result.needs_investigating)
        self.assertEqual(decision.review_summary.get("phase"), "skipped")
        self.assertEqual(decision.review_summary.get("reason"), "low_risk_grounded_answer")

    def test_grounded_answer_high_risk_with_review_approve_returns_answer_customer(self):
        def approve_review(**kwargs):
            self.assertEqual(kwargs.get("mode"), "grounded_postcheck")
            return {"decision": "approve_answer", "reason": "review_ok", "confidence": 0.88}

        decision = self._call_evaluate(
            rag_detail=_make_rag_detail(
                answer="Call joinChannel with the same channel name.",
                confidence=0.80,
                citations=[{"chunk_id": "chunk-1"}],
                reason="grounded_answer",
            ),
            client_intake_state={"phase": "investigating"},
            review_agent=approve_review,
        )
        self.assertEqual(decision.execution_result.workflow_action, "answer_customer")
        self.assertFalse(decision.execution_result.needs_investigating)
        self.assertEqual(decision.review_summary.get("phase"), "completed")
        self.assertEqual(decision.review_summary.get("decision"), "approve_answer")

    def test_no_citations_grounded_postcheck_fails_closed(self):
        decision = self._call_evaluate(
            rag_detail=_make_rag_detail(
                answer="Call joinChannel.",
                confidence=0.80,
                citations=[],
                reason="grounded_answer",
            ),
            client_intake_state={"phase": "investigating"},
            review_agent=lambda **kwargs: {"decision": "approve_answer", "reason": "review_ok", "confidence": 0.5},
        )
        self.assertNotEqual(decision.execution_result.workflow_action, "answer_customer")
        self.assertEqual(decision.review_summary.get("reason"), "rag_post_check_insufficient")

    def test_insufficient_answer_route_without_review_clarifies_customer(self):
        decision = self._call_evaluate(
            rag_detail=_make_rag_detail(
                answer="",
                confidence=0.0,
                citations=[],
                needs_engineer_guidance=True,
                reason="rag_insufficient_evidence",
            ),
        )
        self.assertEqual(decision.execution_result.workflow_action, "clarify_customer_for_intake")
        self.assertEqual(decision.execution_result.investigation_reason, "rag_insufficient_evidence")

    def test_needs_engineer_guidance_with_review_clarify_returns_clarify(self):
        def clarify_review(**kwargs):
            self.assertEqual(kwargs.get("mode"), "rag_insufficient_evidence")
            return TroubleshootingIntakeResult(
                issue_mode="answer",
                known_information={},
                missing_information=["desired_outcome"],
                ready_for_engineer_ticket=False,
                customer_reply="What are you trying to achieve?",
            )

        decision = self._call_evaluate(
            rag_detail=_make_rag_detail(
                answer="",
                confidence=0.0,
                citations=[],
                needs_engineer_guidance=True,
                reason="rag_insufficient_evidence",
            ),
            review_agent=clarify_review,
        )
        self.assertEqual(decision.execution_result.workflow_action, "clarify_customer_for_intake")

    def test_rag_service_error_without_troubleshooting_skips_review(self):
        decision = self._call_evaluate(
            rag_detail=_make_rag_detail(
                answer="",
                confidence=0.0,
                citations=[],
                needs_engineer_guidance=True,
                reason="rag_service_error",
            ),
        )
        self.assertEqual(decision.execution_result.workflow_action, "open_engineer_ticket")
        self.assertEqual(decision.review_summary.get("phase"), "skipped")

    def test_evaluate_rag_result_returns_valid_execution_result_structure(self):
        decision = self._call_evaluate()
        result = decision.execution_result
        self.assertIsInstance(result.answer, str)
        self.assertIsInstance(result.confidence, float)
        self.assertIsInstance(result.sources, list)
        self.assertIsInstance(result.citations, list)
        self.assertIsInstance(result.needs_investigating, bool)
        self.assertIsInstance(result.workflow_action, str)
        self.assertIn(result.workflow_action, {"answer_customer", "clarify_customer_for_intake", "open_engineer_ticket"})

    def test_grounded_postcheck_review_rejects_cited_answer_with_follow_up(self):
        def reject_review(**kwargs):
            return {"decision": "open_engineer_ticket", "reason": "high_risk_unverified", "confidence": 0.3}

        decision = self._call_evaluate(
            rag_detail=_make_rag_detail(
                answer="Call joinChannel.",
                confidence=0.80,
                citations=[{"chunk_id": "chunk-1"}],
                reason="grounded_answer",
            ),
            client_intake_state={"phase": "investigating"},
            review_agent=reject_review,
        )
        self.assertEqual(decision.execution_result.workflow_action, "answer_customer")
        self.assertEqual(decision.execution_result.investigation_reason, "rag_post_check_insufficient")

    def test_review_summary_has_expected_keys(self):
        decision = self._call_evaluate()
        summary = decision.review_summary
        for key in ("phase", "status", "decision", "reason"):
            self.assertIn(key, summary)
        self.assertIn(summary.get("phase"), {"skipped", "completed"})
