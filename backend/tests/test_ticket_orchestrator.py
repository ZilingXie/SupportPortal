from __future__ import annotations

from dataclasses import replace
import os
import types
import unittest
from unittest.mock import patch

from backend.services.support_router import SupportResolution, SupportRouteDecision
from backend.services.ticket_orchestrator import (
    COMMUNICATING_STATUS,
    INVESTIGATING_STATUS,
    SufficiencyAssessment,
    orchestrate_ticket_execution,
)


def _decision(action: str = "rag") -> SupportRouteDecision:
    return SupportRouteDecision(
        scope_label="agora_technical" if action == "rag" else "agora_non_technical",
        route=action,
        confidence=0.94,
        reason="route_match",
        matched_signals=["token"] if action == "rag" else ["ceo"],
        response_language="en",
    )


def _resolution(
    *,
    action: str = "rag",
    needs_engineer_guidance: bool = False,
    confidence: float = 0.88,
    query_class: str | None = None,
    top1_similarity_score: float = 0.95,
) -> SupportResolution:
    return SupportResolution(
        answer="Use the token server sample.",
        confidence=confidence,
        sources=["https://docs.agora.io/en/video-calling/token-authentication"],
        citations=[
            {
                "chunk_id": "chunk-1",
                "source_path": "official/token-authentication.md",
                "heading": "Token authentication",
                "source_url": "https://docs.agora.io/en/video-calling/token-authentication",
            }
        ],
        needs_engineer_guidance=needs_engineer_guidance,
        answer_route=action,
        scope_label="agora_technical" if action == "rag" else "agora_non_technical",
        route_family="agora_docs_rag" if action == "rag" else "web_company_info",
        execution_action=action,
        tooling_profile="agora_docs_only" if action == "rag" else "official_web_search",
        route_reason="grounded_answer",
        route_confidence=0.94,
        search_used=action == "web_search",
        matched_signals=["token"] if action == "rag" else ["ceo"],
        evidence_summary={
            "quality_signals": {
                "query_class": query_class,
                "generation_mode": "structured_answer",
                "selected_doc_count": 1,
                "citation_coverage_ratio": 1.0,
                "top1_similarity_score": top1_similarity_score,
                "avg_selected_similarity_score": top1_similarity_score,
                "handoff_reason": None,
                "needs_human": False,
            },
            "selected_contexts": [
                {
                    "chunk_id": "chunk-1",
                    "heading": "Token authentication",
                    "source_path": "official/token-authentication.md",
                    "source_url": "https://docs.agora.io/en/video-calling/token-authentication",
                    "text_excerpt": "Set a token server before joining the channel.",
                    "similarity": top1_similarity_score,
                    "cited_in_answer": True,
                }
            ],
        }
        if action == "rag"
        else None,
    )


class TicketOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_openai_api_key = os.environ.get("OPENAI_API_KEY")
        os.environ.pop("OPENAI_API_KEY", None)

    def tearDown(self) -> None:
        if self._original_openai_api_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self._original_openai_api_key

    def test_orchestrator_forwards_selected_product_to_router_and_resolution_builder(self) -> None:
        captured_resolution_kwargs: dict[str, object] = {}

        def _resolution_builder(message: str, **kwargs) -> SupportResolution:
            _ = message
            captured_resolution_kwargs.update(kwargs)
            return _resolution(action="rag")

        with patch(
            "backend.services.ticket_orchestrator.analyze_ticket_message",
            return_value=_decision("rag"),
        ) as analyze_mock, patch(
            "backend.services.ticket_orchestrator.assess_rag_answer_sufficiency",
            return_value=SufficiencyAssessment(
                decision="answer",
                reason="sufficient_grounded_answer",
                confidence=0.9,
            ),
        ):
            orchestrate_ticket_execution(
                "How do I join a channel?",
                ticket_id="TK-ORCH-1",
                customer_id="C-001",
                ticket_subject="Join a channel",
                ticket_context=[{"role": "customer", "content": "Need a Cloud Recording answer."}],
                product="cloud_recording",
                resolution_builder=_resolution_builder,
            )

        analyze_mock.assert_called_once_with(
            "How do I join a channel?",
            ticket_subject="Join a channel",
            ticket_context=[{"role": "customer", "content": "Need a Cloud Recording answer."}],
            product="cloud_recording",
        )
        self.assertEqual(captured_resolution_kwargs["product"], "cloud_recording")

    def test_rag_insufficiency_skips_post_check_and_marks_investigating(self) -> None:
        with patch(
            "backend.services.ticket_orchestrator.assess_rag_answer_sufficiency"
        ) as sufficiency_mock:
            execution = orchestrate_ticket_execution(
                "How do I debug token renewal on Android 14?",
                decision=_decision("rag"),
                resolution_builder=lambda *_args, **_kwargs: _resolution(
                    action="rag",
                    needs_engineer_guidance=True,
                ),
            )

        self.assertFalse(execution.needs_investigating)
        self.assertEqual(execution.next_status, COMMUNICATING_STATUS)
        self.assertEqual(execution.execution_action, "rag")
        self.assertEqual(execution.investigation_reason, "rag_insufficient_evidence")
        self.assertEqual(execution.workflow_action, "clarify_customer_for_intake")
        sufficiency_mock.assert_not_called()

    def test_rag_insufficiency_for_troubleshooting_missing_fields_clarifies_customer(self) -> None:
        with patch(
            "backend.services.ticket_orchestrator.evaluate_troubleshooting_intake",
            return_value=types.SimpleNamespace(
                issue_mode="investigation",
                known_information={"issue_symptom": "black screen"},
                missing_information=["channel_name", "problematic_uid", "issue_timestamp"],
                ready_for_engineer_ticket=False,
                customer_reply=(
                    "Known so far: symptom is black screen. "
                    "To investigate this Audio/Video Calling issue, please share the channel name, "
                    "problematic uid, and issue timestamp."
                ),
            ),
            create=True,
        ), patch(
            "backend.services.ticket_orchestrator.assess_rag_answer_sufficiency"
        ) as sufficiency_mock:
            execution = orchestrate_ticket_execution(
                "I got black screen issue.",
                product="audio_video_calling",
                decision=_decision("rag"),
                resolution_builder=lambda *_args, **_kwargs: _resolution(
                    action="rag",
                    needs_engineer_guidance=True,
                ),
            )

        self.assertFalse(execution.needs_investigating)
        self.assertEqual(execution.next_status, COMMUNICATING_STATUS)
        self.assertEqual(execution.workflow_action, "clarify_customer_for_intake")
        self.assertEqual(
            execution.client_intake_state["missing_information"],
            ["channel_name", "problematic_uid", "issue_timestamp"],
        )
        self.assertIn("Known so far", execution.answer)
        sufficiency_mock.assert_not_called()

    def test_rag_insufficiency_for_how_to_clarifies_customer_with_answer_mode_fields(self) -> None:
        with patch(
            "backend.services.ticket_orchestrator.evaluate_troubleshooting_intake",
            return_value=types.SimpleNamespace(
                issue_mode="answer",
                known_information={},
                missing_information=["desired_outcome", "blocked_step_or_error"],
                ready_for_engineer_ticket=False,
                customer_reply=(
                    "I couldn't verify a grounded answer yet. What are you trying to achieve? "
                    "What error or blocker are you seeing?"
                ),
            ),
            create=True,
        ), patch(
            "backend.services.ticket_orchestrator.assess_rag_answer_sufficiency"
        ) as sufficiency_mock:
            execution = orchestrate_ticket_execution(
                "How to join channel?",
                product="audio_video_calling",
                decision=_decision("rag"),
                resolution_builder=lambda *_args, **_kwargs: _resolution(
                    action="rag",
                    needs_engineer_guidance=True,
                ),
            )

        self.assertFalse(execution.needs_investigating)
        self.assertEqual(execution.next_status, COMMUNICATING_STATUS)
        self.assertEqual(execution.workflow_action, "clarify_customer_for_intake")
        self.assertEqual(execution.client_intake_state["issue_mode"], "answer")
        self.assertEqual(
            execution.client_intake_state["missing_information"],
            ["desired_outcome", "blocked_step_or_error"],
        )
        self.assertIn("What are you trying to achieve", execution.answer)
        sufficiency_mock.assert_not_called()

    def test_rag_insufficiency_for_troubleshooting_ready_inputs_opens_engineer_ticket(self) -> None:
        with patch(
            "backend.services.ticket_orchestrator.evaluate_troubleshooting_intake",
            return_value=types.SimpleNamespace(
                issue_mode="investigation",
                known_information={
                    "issue_symptom": "black screen",
                    "channel_name": "demo-room",
                    "problematic_uid": "42",
                    "issue_timestamp": "2026-04-04T10:30:00Z",
                },
                missing_information=[],
                ready_for_engineer_ticket=True,
                customer_reply="",
            ),
            create=True,
        ):
            execution = orchestrate_ticket_execution(
                "Channel name is demo-room. Problematic uid is 42. It happened at 2026-04-04T10:30:00Z.",
                product="audio_video_calling",
                client_intake_state={
                    "phase": "gather_customer_inputs",
                    "product": "audio_video_calling",
                    "issue_mode": "investigation",
                    "known_information": {"issue_symptom": "black screen"},
                    "missing_information": ["channel_name", "problematic_uid", "issue_timestamp"],
                    "ready_for_engineer_ticket": False,
                    "last_updated_at": "2026-04-04T10:00:00Z",
                },
                decision=_decision("rag"),
                resolution_builder=lambda *_args, **_kwargs: _resolution(
                    action="rag",
                    needs_engineer_guidance=True,
                ),
            )

        self.assertTrue(execution.needs_investigating)
        self.assertEqual(execution.next_status, INVESTIGATING_STATUS)
        self.assertEqual(execution.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.investigation_reason, "investigation_intake_complete")
        self.assertTrue(execution.client_intake_state["ready_for_engineer_ticket"])

    def test_rag_service_error_keeps_service_error_reason_for_investigation(self) -> None:
        execution = orchestrate_ticket_execution(
            "How do I join a channel?",
            decision=_decision("rag"),
            resolution_builder=lambda *_args, **_kwargs: SupportResolution(
                answer="I couldn't find enough information in the available support knowledge base to answer that question.",
                confidence=0.0,
                sources=[],
                citations=[],
                needs_engineer_guidance=True,
                answer_route="rag",
                scope_label="agora_technical",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
                route_reason="rag_service_error",
                route_confidence=0.94,
                search_used=False,
                matched_signals=["join channel"],
            ),
        )

        self.assertTrue(execution.needs_investigating)
        self.assertEqual(execution.next_status, INVESTIGATING_STATUS)
        self.assertEqual(execution.route_reason, "rag_service_error")
        self.assertEqual(execution.investigation_reason, "rag_service_error")

    def test_rag_answer_runs_post_check_and_stays_communicating_when_allowed(self) -> None:
        with patch(
            "backend.services.ticket_orchestrator.assess_rag_answer_sufficiency",
            return_value=SufficiencyAssessment(
                decision="answer",
                reason="sufficient_grounded_answer",
                confidence=0.91,
            ),
        ):
            execution = orchestrate_ticket_execution(
                "How do I debug token renewal on Android 14?",
                decision=_decision("rag"),
                resolution_builder=lambda *_args, **_kwargs: _resolution(action="rag"),
            )

        self.assertFalse(execution.needs_investigating)
        self.assertEqual(execution.next_status, COMMUNICATING_STATUS)
        self.assertEqual(execution.execution_action, "rag")
        self.assertIsNone(execution.investigation_reason)

    def test_generic_grounded_how_to_question_ignores_platform_gap_rejection(self) -> None:
        with patch(
            "backend.services.ticket_orchestrator.assess_rag_answer_sufficiency",
            return_value=SufficiencyAssessment(
                decision="investigate",
                reason="platform_specific_gap_without_explicit_platform",
                confidence=0.89,
            ),
        ):
            execution = orchestrate_ticket_execution(
                "How to join channel?",
                decision=_decision("rag"),
                resolution_builder=lambda *_args, **_kwargs: _resolution(action="rag"),
            )

        self.assertFalse(execution.needs_investigating)
        self.assertEqual(execution.next_status, COMMUNICATING_STATUS)
        self.assertIsNone(execution.investigation_reason)

    def test_generic_grounded_how_to_question_skips_post_check_entirely(self) -> None:
        with patch(
            "backend.services.ticket_orchestrator.assess_rag_answer_sufficiency"
        ) as sufficiency_mock:
            execution = orchestrate_ticket_execution(
                "how to join channel",
                decision=_decision("rag"),
                resolution_builder=lambda *_args, **_kwargs: _resolution(action="rag"),
            )

        self.assertFalse(execution.needs_investigating)
        self.assertEqual(execution.next_status, COMMUNICATING_STATUS)
        self.assertIsNone(execution.investigation_reason)
        self.assertEqual(execution.workflow_action, "answer_customer")
        sufficiency_mock.assert_not_called()

    def test_rag_answer_runs_post_check_and_investigates_when_rejected(self) -> None:
        with patch(
            "backend.services.ticket_orchestrator.assess_rag_answer_sufficiency",
            return_value=SufficiencyAssessment(
                decision="investigate",
                reason="missing_android_14_specific_evidence",
                confidence=0.89,
            ),
        ):
            execution = orchestrate_ticket_execution(
                "How do I debug token renewal on Android 14?",
                decision=_decision("rag"),
                resolution_builder=lambda *_args, **_kwargs: _resolution(action="rag"),
            )

        self.assertFalse(execution.needs_investigating)
        self.assertEqual(execution.next_status, COMMUNICATING_STATUS)
        self.assertEqual(execution.execution_action, "rag")
        self.assertEqual(execution.investigation_reason, "rag_post_check_insufficient")
        self.assertEqual(execution.workflow_action, "answer_customer")
        self.assertEqual(execution.route_reason, "grounded_answer")
        self.assertIn("please share what you're trying to achieve", execution.answer.lower())

    def test_rag_post_check_error_falls_back_to_investigating(self) -> None:
        with patch(
            "backend.services.ticket_orchestrator.assess_rag_answer_sufficiency",
            side_effect=RuntimeError("judge unavailable"),
        ):
            execution = orchestrate_ticket_execution(
                "How do I debug token renewal on Android 14?",
                decision=_decision("rag"),
                resolution_builder=lambda *_args, **_kwargs: _resolution(action="rag"),
            )

        self.assertFalse(execution.needs_investigating)
        self.assertEqual(execution.next_status, COMMUNICATING_STATUS)
        self.assertEqual(execution.investigation_reason, "rag_post_check_error")
        self.assertEqual(execution.workflow_action, "answer_customer")
        self.assertEqual(execution.route_reason, "grounded_answer")
        self.assertIn("please share what you're trying to achieve", execution.answer.lower())

    def test_generic_grounded_how_to_question_keeps_answer_when_post_check_errors(self) -> None:
        with patch(
            "backend.services.ticket_orchestrator.assess_rag_answer_sufficiency",
            side_effect=RuntimeError("judge unavailable"),
        ):
            execution = orchestrate_ticket_execution(
                "how to join channel",
                decision=_decision("rag"),
                resolution_builder=lambda *_args, **_kwargs: _resolution(action="rag"),
            )

        self.assertFalse(execution.needs_investigating)
        self.assertEqual(execution.next_status, COMMUNICATING_STATUS)
        self.assertIsNone(execution.investigation_reason)

    def test_short_how_to_faq_grounded_answer_skips_post_check_with_lexical_confidence(self) -> None:
        with patch(
            "backend.services.ticket_orchestrator.assess_rag_answer_sufficiency"
        ) as sufficiency_mock:
            execution = orchestrate_ticket_execution(
                "how to join channel",
                decision=_decision("rag"),
                resolution_builder=lambda *_args, **_kwargs: _resolution(
                    action="rag",
                    confidence=0.77,
                    query_class="how_to_faq",
                    top1_similarity_score=0.0,
                ),
            )

        self.assertFalse(execution.needs_investigating)
        self.assertEqual(execution.next_status, COMMUNICATING_STATUS)
        self.assertIsNone(execution.investigation_reason)
        self.assertEqual(execution.workflow_action, "answer_customer")
        sufficiency_mock.assert_not_called()

    def test_short_how_to_faq_grounded_answer_skips_post_check_without_citations(self) -> None:
        with patch(
            "backend.services.ticket_orchestrator.assess_rag_answer_sufficiency"
        ) as sufficiency_mock:
            def _resolution_without_citations(*_args, **_kwargs) -> SupportResolution:
                return replace(
                    _resolution(
                        action="rag",
                        confidence=0.77,
                        query_class="how_to_faq",
                        top1_similarity_score=0.0,
                    ),
                    citations=[],
                )

            execution = orchestrate_ticket_execution(
                "how to join channel",
                decision=_decision("rag"),
                resolution_builder=_resolution_without_citations,
            )

        self.assertFalse(execution.needs_investigating)
        self.assertEqual(execution.next_status, COMMUNICATING_STATUS)
        self.assertEqual(execution.investigation_reason, "rag_post_check_insufficient")
        self.assertEqual(execution.workflow_action, "clarify_customer_for_intake")
        sufficiency_mock.assert_not_called()

    def test_non_rag_action_skips_post_check(self) -> None:
        with patch(
            "backend.services.ticket_orchestrator.assess_rag_answer_sufficiency"
        ) as sufficiency_mock:
            execution = orchestrate_ticket_execution(
                "Who's the CEO of Agora?",
                decision=_decision("web_search"),
                resolution_builder=lambda *_args, **_kwargs: _resolution(action="web_search"),
            )

        self.assertFalse(execution.needs_investigating)
        self.assertEqual(execution.next_status, COMMUNICATING_STATUS)
        self.assertEqual(execution.execution_action, "web_search")
        sufficiency_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
