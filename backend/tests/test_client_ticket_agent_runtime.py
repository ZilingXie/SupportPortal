from __future__ import annotations

import os
import types
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

    def test_rag_extractive_fallback_routes_into_answer_mode_clarification(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="How do I join channel?",
            ticket_id="TK-RAG-FAQ-1",
            customer_id="C-001",
            ticket_subject="Join channel",
            ticket_context=[{"role": "customer", "content": "How do I join channel?"}],
            product="audio_video_calling",
            message_id="2026-04-04T00:00:00+00:00",
            route_agent=lambda **_kwargs: SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.94,
                reason="technical_question",
                matched_signals=["join channel"],
                response_language="en",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
            ),
            route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
                answer="I found relevant support evidence, but I could not verify a complete grounded answer.",
                confidence=0.41,
                sources=[],
                citations=[],
                needs_engineer_guidance=True,
                reason="rag_insufficient_evidence",
                evidence_summary={
                    "quality_signals": {
                        "generation_mode": "extractive_fallback",
                        "extractive_fallback_used": True,
                        "needs_human": True,
                    }
                },
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: TroubleshootingIntakeResult(
                issue_mode="answer",
                known_information={},
                missing_information=["desired_outcome", "blocked_step_or_error"],
                ready_for_engineer_ticket=False,
                customer_reply=(
                    "I couldn't verify a grounded answer yet. What are you trying to achieve? "
                    "What error or blocker are you seeing?"
                ),
            ),
            rag_canceler=None,
        )

        self.assertEqual(execution.result.workflow_action, "clarify_customer_for_intake")
        self.assertEqual(execution.result.client_intake_state["issue_mode"], "answer")
        self.assertEqual(
            execution.result.client_intake_state["missing_information"],
            ["desired_outcome", "blocked_step_or_error"],
        )
        self.assertIn("What are you trying to achieve", execution.result.answer)
        self.assertEqual(execution.runtime_state.review_agent.get("decision"), "clarify_customer_for_intake")

    def test_grounded_answer_with_extractive_fallback_signal_never_skips_review(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="How do I join channel?",
            ticket_id="TK-RISK-FAQ-1",
            customer_id="C-001",
            ticket_subject="Join channel",
            ticket_context=[{"role": "customer", "content": "How do I join channel?"}],
            product="audio_video_calling",
            message_id="2026-04-04T00:00:00+00:00",
            route_agent=lambda **_kwargs: SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.94,
                reason="technical_question",
                matched_signals=["join channel"],
                response_language="en",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
            ),
            route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
                answer="I found relevant support evidence, but I could not verify a complete grounded answer.",
                confidence=0.91,
                sources=["https://docs.agora.io/en/video-calling/get-started"],
                citations=[{"chunk_id": "chunk-1"}],
                needs_engineer_guidance=False,
                reason="grounded_answer",
                evidence_summary={
                    "quality_signals": {
                        "generation_mode": "extractive_fallback",
                        "extractive_fallback_used": True,
                        "selected_doc_count": 1,
                        "top1_similarity_score": 0.93,
                        "needs_human": False,
                    }
                },
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: {"decision": "open_engineer_ticket", "reason": "review_insufficient", "confidence": 0.62},
            rag_canceler=None,
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.result.investigation_reason, "rag_post_check_insufficient")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "completed")
        self.assertEqual(execution.runtime_state.review_agent.get("decision"), "open_engineer_ticket")

    def test_troubleshooting_postcheck_rejection_routes_into_intake_clarification(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        review_modes: list[str] = []

        def _review_agent(**kwargs: object) -> object:
            mode = str(kwargs.get("mode") or "")
            review_modes.append(mode)
            if mode == "grounded_postcheck":
                return {"decision": "open_engineer_ticket", "reason": "review_insufficient", "confidence": 0.62}
            if mode == "pre_engineer_intake":
                return TroubleshootingIntakeResult(
                    issue_mode="investigation",
                    known_information={"issue_symptom": "black screen issue"},
                    missing_information=["channel_name", "problematic_uid", "issue_timestamp"],
                    ready_for_engineer_ticket=False,
                    customer_reply=(
                        "Known so far: the issue symptom is black screen issue. "
                        "To investigate this Audio/Video Calling issue, please share the channel name, "
                        "problematic uid, and issue timestamp."
                    ),
                )
            self.fail(f"unexpected review mode {mode!r}")

        execution = execute_client_ticket_agent_runtime(
            message="i got black screen!!! what should i do",
            ticket_id="TK-RISK-TRBL-1",
            customer_id="C-001",
            ticket_subject="Black screen",
            ticket_context=[{"role": "customer", "content": "i got black screen!!! what should i do"}],
            product="audio_video_calling",
            message_id="2026-04-04T00:00:00+00:00",
            route_agent=lambda **_kwargs: SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.94,
                reason="technical_question",
                matched_signals=["black screen"],
                response_language="en",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
            ),
            route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
                answer="Check whether the remote user is publishing video and the local render view is bound correctly.",
                confidence=0.88,
                sources=["https://docs.agora.io/en/video-calling/troubleshooting/black-screen"],
                citations=[{"chunk_id": "chunk-black-screen"}],
                needs_engineer_guidance=False,
                reason="grounded_answer",
                evidence_summary={
                    "quality_signals": {
                        "generation_mode": "structured_answer",
                        "selected_doc_count": 1,
                        "top1_similarity_score": 0.91,
                    }
                },
                packed_evidence=None,
            ),
            review_agent=_review_agent,
            rag_canceler=None,
        )

        self.assertEqual(review_modes, ["grounded_postcheck", "pre_engineer_intake"])
        self.assertEqual(execution.result.workflow_action, "clarify_customer_for_intake")
        self.assertFalse(execution.result.needs_investigating)
        self.assertEqual(execution.result.investigation_reason, None)
        self.assertEqual(
            execution.result.client_intake_state["missing_information"],
            ["channel_name", "problematic_uid", "issue_timestamp"],
        )
        self.assertEqual(
            execution.result.client_intake_state["pending_investigation_reason"],
            "rag_post_check_insufficient",
        )
        self.assertEqual(execution.runtime_state.review_agent.get("decision"), "clarify_customer_for_intake")

    def test_ready_intake_follow_up_reuses_pending_investigation_reason(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="channel name is demo-room, problematic uid is 42, timestamp is 2026-04-04T10:30:00Z",
            ticket_id="TK-RISK-TRBL-2",
            customer_id="C-001",
            ticket_subject="Black screen",
            ticket_context=[
                {"role": "customer", "content": "i got black screen issue"},
                {
                    "role": "assistant",
                    "content": (
                        "Known so far: the issue symptom is black screen issue. "
                        "To investigate this Audio/Video Calling issue, please share the channel name, "
                        "problematic uid, and issue timestamp."
                    ),
                },
            ],
            product="audio_video_calling",
            message_id="2026-04-04T10:30:00+00:00",
            client_intake_state={
                "phase": "gather_customer_inputs",
                "product": "audio_video_calling",
                "issue_mode": "investigation",
                "known_information": {"issue_symptom": "black screen issue"},
                "missing_information": ["channel_name", "problematic_uid", "issue_timestamp"],
                "ready_for_engineer_ticket": False,
                "pending_investigation_reason": "rag_post_check_insufficient",
                "last_updated_at": "2026-04-04T10:00:00Z",
            },
            route_agent=lambda **_kwargs: SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.94,
                reason="technical_question",
                matched_signals=["black screen", "uid"],
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
                known_information={
                    "issue_symptom": "black screen issue",
                    "channel_name": "demo-room",
                    "problematic_uid": "42",
                    "issue_timestamp": "2026-04-04T10:30:00Z",
                },
                missing_information=[],
                ready_for_engineer_ticket=True,
                customer_reply="",
            ),
            rag_canceler=None,
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertTrue(execution.result.needs_investigating)
        self.assertEqual(execution.result.investigation_reason, "rag_post_check_insufficient")
        self.assertEqual(
            execution.result.client_intake_state["pending_investigation_reason"],
            "rag_post_check_insufficient",
        )

    def test_rag_unavailable_from_knowledge_index_guard_skips_review_and_surfaces_diagnostics(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="How do I join channel?",
            ticket_id="TK-RAG-UNAVAIL-1",
            customer_id="C-001",
            ticket_subject="Join channel",
            ticket_context=[{"role": "customer", "content": "How do I join channel?"}],
            product="audio_video_calling",
            message_id="2026-04-04T00:00:00+00:00",
            route_agent=lambda **_kwargs: SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.94,
                reason="technical_question",
                matched_signals=["join channel"],
                response_language="en",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
            ),
            route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
                answer="",
                confidence=0.0,
                sources=[],
                citations=[],
                needs_engineer_guidance=True,
                reason="rag_unavailable",
                evidence_summary={
                    "quality_signals": {
                        "generation_mode": "insufficient_evidence",
                        "needs_human": True,
                    },
                    "diagnostics": {
                        "knowledge_index_status": "configured_table_empty",
                        "knowledge_index_reason": "configured_table_empty",
                        "configured_vector_table": "supportportal.docagent_chunks_bge_m3_1024",
                    },
                },
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: self.fail("review agent should not run for rag_unavailable"),
            rag_canceler=None,
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.result.investigation_reason, "rag_unavailable")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")
        self.assertEqual(execution.diagnostics.get("knowledge_index_status"), "configured_table_empty")
        self.assertEqual(execution.diagnostics.get("knowledge_index_reason"), "configured_table_empty")

    def test_troubleshooting_rag_unavailable_routes_into_intake_clarification(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        review_modes: list[str] = []

        def _review_agent(**kwargs: object) -> TroubleshootingIntakeResult:
            mode = str(kwargs.get("mode") or "")
            review_modes.append(mode)
            if mode != "rag_insufficient_evidence":
                self.fail(f"unexpected review mode {mode!r}")
            return TroubleshootingIntakeResult(
                issue_mode="investigation",
                known_information={"issue_symptom": "black screen issue"},
                missing_information=["channel_name", "problematic_uid", "issue_timestamp"],
                ready_for_engineer_ticket=False,
                customer_reply=(
                    "Known so far: the issue symptom is black screen issue. "
                    "To investigate this Audio/Video Calling issue, please share the channel name, "
                    "problematic uid, and issue timestamp."
                ),
            )

        execution = execute_client_ticket_agent_runtime(
            message="i got black screen, what should i do?",
            ticket_id="TK-RAG-UNAVAIL-TRBL-1",
            customer_id="C-001",
            ticket_subject="Black screen",
            ticket_context=[{"role": "customer", "content": "i got black screen, what should i do?"}],
            product="audio_video_calling",
            message_id="2026-04-04T00:00:00+00:00",
            route_agent=lambda **_kwargs: SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.94,
                reason="technical_question",
                matched_signals=["black screen"],
                response_language="en",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
            ),
            route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
                answer="",
                confidence=0.0,
                sources=[],
                citations=[],
                needs_engineer_guidance=True,
                reason="rag_unavailable",
                evidence_summary={
                    "quality_signals": {
                        "generation_mode": "insufficient_evidence",
                        "needs_human": True,
                    },
                    "diagnostics": {
                        "knowledge_index_status": "configured_table_empty",
                        "knowledge_index_reason": "configured_table_empty",
                        "configured_vector_table": "supportportal.docagent_chunks_bge_m3_1024",
                    },
                },
                packed_evidence=None,
            ),
            review_agent=_review_agent,
            rag_canceler=None,
        )

        self.assertEqual(review_modes, ["rag_insufficient_evidence"])
        self.assertEqual(execution.result.workflow_action, "clarify_customer_for_intake")
        self.assertFalse(execution.result.needs_investigating)
        self.assertEqual(
            execution.result.client_intake_state["missing_information"],
            ["channel_name", "problematic_uid", "issue_timestamp"],
        )
        self.assertEqual(
            execution.result.client_intake_state["pending_investigation_reason"],
            "rag_unavailable",
        )
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "completed")

    def test_troubleshooting_rag_unavailable_keeps_investigation_mode_when_intake_llm_prefers_answer(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime
        from backend.services.troubleshooting_intake import evaluate_troubleshooting_intake

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch(
            "backend.services.troubleshooting_intake.invoke_responses_text",
            return_value=types.SimpleNamespace(
                text=(
                    '{"issue_mode":"answer","known_information":{"symptom":"black screen"},'
                    '"missing_information":["desired_outcome","blocked_step_or_error"],'
                    '"ready_for_engineer_ticket":false,'
                    '"customer_reply":"What are you trying to achieve? What error or blocker are you seeing?"}'
                )
            ),
        ):
            execution = execute_client_ticket_agent_runtime(
                message="i got black screen, what should i do?",
                ticket_id="TK-RAG-UNAVAIL-TRBL-2",
                customer_id="C-001",
                ticket_subject="Black screen",
                ticket_context=[{"role": "customer", "content": "i got black screen, what should i do?"}],
                product="audio_video_calling",
                message_id="2026-04-04T00:00:00+00:00",
                route_agent=lambda **_kwargs: SupportRouteDecision(
                    scope_label="agora_technical",
                    route="rag",
                    confidence=0.94,
                    reason="technical_question",
                    matched_signals=["black screen"],
                    response_language="en",
                    route_family="agora_docs_rag",
                    execution_action="rag",
                    tooling_profile="agora_docs_only",
                ),
                route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
                rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
                    answer="",
                    confidence=0.0,
                    sources=[],
                    citations=[],
                    needs_engineer_guidance=True,
                    reason="rag_unavailable",
                    evidence_summary={
                        "quality_signals": {
                            "generation_mode": "insufficient_evidence",
                            "needs_human": True,
                        }
                    },
                    packed_evidence=None,
                ),
                review_agent=lambda **kwargs: evaluate_troubleshooting_intake(
                    message=str(kwargs.get("message") or ""),
                    product=kwargs.get("product"),
                    ticket_subject=kwargs.get("ticket_subject"),
                    ticket_context=kwargs.get("ticket_context"),
                    current_state=kwargs.get("current_state"),
                    rag_result=kwargs.get("rag_result"),
                ),
                rag_canceler=None,
            )

        self.assertEqual(execution.result.workflow_action, "clarify_customer_for_intake")
        self.assertEqual(execution.result.client_intake_state["issue_mode"], "investigation")
        self.assertEqual(
            execution.result.client_intake_state["missing_information"],
            ["channel_name", "problematic_uid", "issue_timestamp"],
        )
        self.assertEqual(execution.runtime_state.review_agent.get("decision"), "clarify_customer_for_intake")

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
