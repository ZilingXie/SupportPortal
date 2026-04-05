from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.services.rag_service_client import RagTicketAnswerDetail
from backend.services.support_router import SupportResolution, SupportRouteDecision
from backend.services.troubleshooting_intake import TroubleshootingIntakeResult


class ClientTicketAgentRuntimeContractTests(unittest.TestCase):
    def test_runtime_contract_exposes_explicit_agents_and_run_state(self) -> None:
        from backend.services.client_ticket_agent_runtime import (
            AGENT_NAME_MAIN,
            AGENT_NAME_RAG,
            AGENT_NAME_REVIEW,
            AGENT_NAME_ROUTE,
            ClientTicketAgentRuntimeExecution,
            ClientTicketAgentRuntimeState,
            execute_client_ticket_agent_runtime,
        )

        self.assertEqual(AGENT_NAME_MAIN, "main_agent")
        self.assertEqual(AGENT_NAME_ROUTE, "route_agent")
        self.assertEqual(AGENT_NAME_RAG, "rag_agent")
        self.assertEqual(AGENT_NAME_REVIEW, "review_agent")
        self.assertTrue(callable(execute_client_ticket_agent_runtime))
        self.assertTrue(hasattr(ClientTicketAgentRuntimeState, "__dataclass_fields__"))
        self.assertTrue(hasattr(ClientTicketAgentRuntimeExecution, "__dataclass_fields__"))

    def test_runtime_is_single_path_and_does_not_expose_mode_switch(self) -> None:
        from backend.services import client_ticket_agent_runtime as runtime

        self.assertFalse(hasattr(runtime, "CLIENT_TICKET_AGENT_RUNTIME_MODE_ENV"))
        self.assertFalse(hasattr(runtime, "CLIENT_TICKET_AGENT_RUNTIME_MODE_LEGACY"))
        self.assertFalse(hasattr(runtime, "CLIENT_TICKET_AGENT_RUNTIME_MODE_AGENT"))
        self.assertFalse(hasattr(runtime, "current_client_ticket_agent_runtime_mode"))

    def test_non_rag_route_skips_review_and_cancels_rag(self) -> None:
        from backend.services.client_ticket_agent_runtime import (
            AGENT_NAME_RAG,
            AGENT_NAME_REVIEW,
            execute_client_ticket_agent_runtime,
        )

        cancelled: list[str] = []

        execution = execute_client_ticket_agent_runtime(
            message="Who is Agora's CEO?",
            ticket_id="TK-ROUTE-1",
            customer_id="C-001",
            ticket_subject="Investor question",
            ticket_context=[{"role": "customer", "content": "Who is Agora's CEO?"}],
            product="audio_video_calling",
            message_id="2026-04-04T00:00:00+00:00",
            route_agent=lambda **_kwargs: SupportRouteDecision(
                scope_label="agora_non_technical",
                route="web_search",
                confidence=0.91,
                reason="company_info",
                matched_signals=["agora"],
                response_language="en",
                route_family="web_company_info",
                execution_action="web_search",
                tooling_profile="official_web_search",
            ),
            route_executor=lambda **_kwargs: SupportResolution(
                answer="Agora's latest investor information is on the official site.",
                confidence=0.82,
                sources=["https://investor.agora.io"],
                citations=[],
                needs_engineer_guidance=False,
                answer_route="web_search",
                scope_label="agora_non_technical",
                route_reason="company_info",
                route_confidence=0.91,
                search_used=True,
                matched_signals=["agora"],
                route_family="web_company_info",
                execution_action="web_search",
                tooling_profile="official_web_search",
            ),
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
                answer="Use joinChannel with a valid token.",
                confidence=0.91,
                sources=["https://docs.agora.io/en/video-calling/get-started"],
                citations=[{"chunk_id": "chunk-1"}],
                needs_engineer_guidance=False,
                reason="grounded_answer",
                evidence_summary={},
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: self.fail("review agent should not run for non-rag route"),
            rag_canceler=lambda request_id: cancelled.append(request_id) or {"cancelled": True, "stage": "route_flip"},
        )

        self.assertEqual(execution.result.execution_action, "web_search")
        self.assertEqual(execution.result.workflow_action, "answer_customer")
        self.assertEqual(execution.runtime_state.route_agent.get("decision"), "web_search")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")
        self.assertIn(execution.runtime_state.rag_agent.get("status"), {"cancelled", "completed"})
        self.assertTrue(cancelled, "rag cancel should be requested when route flips non-rag")
        self.assertTrue(
            any(
                event.get("agent_name") == AGENT_NAME_RAG and event.get("event_type") == "cancel_requested"
                for event in execution.agent_events
            )
        )
        self.assertTrue(
            any(
                event.get("agent_name") == AGENT_NAME_REVIEW and event.get("event_type") == "skipped"
                for event in execution.agent_events
            )
        )

    def test_rag_insufficient_routes_into_review_agent_clarification(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="I got black screen issue",
            ticket_id="TK-RAG-1",
            customer_id="C-001",
            ticket_subject="Black screen",
            ticket_context=[{"role": "customer", "content": "I got black screen issue"}],
            product="audio_video_calling",
            message_id="2026-04-04T00:00:00+00:00",
            route_agent=lambda **_kwargs: SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.94,
                reason="technical_question",
                matched_signals=["rtc"],
                response_language="en",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
            ),
            route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
                answer="I couldn't find enough information in the docs alone.",
                confidence=0.43,
                sources=[],
                citations=[],
                needs_engineer_guidance=True,
                reason="rag_insufficient_evidence",
                evidence_summary={"quality_signals": {"needs_human": True}},
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: TroubleshootingIntakeResult(
                issue_mode="investigation",
                known_information={"issue_symptom": "black screen issue"},
                missing_information=["channel_name", "problematic_uid", "issue_timestamp"],
                ready_for_engineer_ticket=False,
                customer_reply="I understand you are seeing a black screen issue. Please share the channel name, problematic uid, and issue timestamp.",
            ),
            rag_canceler=None,
        )

        self.assertEqual(execution.result.workflow_action, "clarify_customer_for_intake")
        self.assertEqual(execution.runtime_state.review_agent.get("decision"), "clarify_customer_for_intake")
        self.assertEqual(execution.result.client_intake_state["missing_information"], ["channel_name", "problematic_uid", "issue_timestamp"])
        self.assertEqual(execution.runtime_state.status, "completed")

    def test_grounded_answer_high_risk_waits_for_review(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="Android 14 token renewal keeps failing after reconnect",
            ticket_id="TK-RISK-1",
            customer_id="C-001",
            ticket_subject="Reconnect failure",
            ticket_context=[{"role": "customer", "content": "Android 14 token renewal keeps failing after reconnect"}],
            product="audio_video_calling",
            message_id="2026-04-04T00:00:00+00:00",
            route_agent=lambda **_kwargs: SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.94,
                reason="technical_question",
                matched_signals=["token"],
                response_language="en",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
            ),
            route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
                answer="Use onTokenPrivilegeWillExpire and renewToken before reconnect completes.",
                confidence=0.88,
                sources=["https://docs.agora.io/en/video-calling/token-authentication"],
                citations=[{"chunk_id": "chunk-1"}],
                needs_engineer_guidance=False,
                reason="grounded_answer",
                evidence_summary={"quality_signals": {"generation_mode": "structured_answer", "selected_doc_count": 1, "top1_similarity_score": 0.93}},
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: {"decision": "approve_answer", "reason": "postcheck_passed", "confidence": 0.86},
            rag_canceler=None,
        )

        self.assertEqual(execution.result.workflow_action, "answer_customer")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "completed")
        self.assertEqual(execution.runtime_state.review_agent.get("decision"), "approve_answer")


if __name__ == "__main__":
    unittest.main()
