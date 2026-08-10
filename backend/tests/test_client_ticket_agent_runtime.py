from __future__ import annotations

from contextlib import contextmanager
import inspect
import os
import threading
import types
import unittest
from unittest.mock import patch

from backend.services.detailed_invoice_field_extractor import DetailedInvoiceFieldExtraction
from backend.services.rag_service_client import RagTicketAnswerDetail
from backend.services.support_router import SupportResolution, SupportRouteDecision, resolve_support_message
from backend.services.troubleshooting_intake import TroubleshootingIntakeResult


class _FakeOpenAiReviewTrace:
    def __init__(self, *, trace_id: str, group_id: str, workflow_name: str, mode: str) -> None:
        self._ref = {
            "trace_id": trace_id,
            "group_id": group_id,
            "workflow_name": workflow_name,
            "mode": mode,
        }
        self.function_calls: list[dict[str, object]] = []
        self.custom_spans: list[dict[str, object]] = []

    def __enter__(self) -> "_FakeOpenAiReviewTrace":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def as_trace_ref(self) -> dict[str, str]:
        return dict(self._ref)

    @contextmanager
    def function_span(
        self,
        name: str,
        *,
        input: str | None = None,
        output: str | None = None,
    ):
        self.function_calls.append({"name": name, "input": input, "output": output})
        yield

    def record_custom_span(self, name: str, data: dict[str, object] | None = None) -> None:
        self.custom_spans.append({"name": name, "data": dict(data or {})})


class ClientTicketAgentRuntimeContractTests(unittest.TestCase):
    _BAN_API_MISMATCH_MESSAGE = """Hello, Agora team.

We are using the Ban User Privileges API (POST /dev/v1/kicking-rule) to disband channels after a broadcast ends, but we have found some differences between the official documentation and the actual API behavior.

1. uid: 0 cannot be used
According to the documentation
(https://docs.agora.io/en/broadcast-streaming/channel-management-api/best-practices/ban-user-privileges#disband-a-channel), when targeting all users in a channel, it says to use uid: 0. However, in actual use:
"uid": 0 (number) -> Error: uid '0' must be a number, or set str_uid = true

2. Cannot create a permanent rule with time: 0
The documentation states that time: 0 means the rule is applied permanently. However, when we actually send time: 0, the API returns {"status":"success","id":0}, but no rule is created."""
    _TK_165_MESSAGE = (
        "Hi, We are implementing agora broadcasting and currently need some more info on products that "
        "agora provides. Would be great if we can connect with someone who can guide us on products "
        'that Agora has and could help us."'
    )

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
        self.assertEqual(AGENT_NAME_RAG, "rag_service")
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

    def test_runtime_route_timeout_default_is_eight_seconds(self) -> None:
        from backend.services import client_ticket_agent_runtime as runtime

        signature = inspect.signature(runtime.execute_client_ticket_agent_runtime)
        self.assertEqual(signature.parameters["route_timeout_seconds"].default, 8.0)

    def test_build_execution_route_payload_exposes_retrieval_plan_snapshot_for_rag_answers(self) -> None:
        from backend.services.client_ticket_agent_runtime import build_execution_route_payload

        execution = types.SimpleNamespace(
            answer_route="rag",
            scope_label="agora_technical",
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            route_reason="grounded_answer",
            workflow_action="answer_customer",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["join channel"],
            evidence_summary={
                "diagnostics": {
                    "retrieval_plan_snapshot": {
                        "request_id": "rag-abc123",
                        "query_class": "how_to_faq",
                        "retrieval_strategy": "agentic_multi_tool_v1",
                        "light_path_used": False,
                        "evidence_goal": "how_to_usage_support",
                        "recovery_bias": "semantic",
                        "first_pass_tools": ["p_vec", "s_vec"],
                        "query_variants": [{"kind": "original", "query": "How to join channel"}],
                        "decomposition_targets": [],
                        "agent_iterations": [{"round_index": 1, "decision": "answer_now"}],
                        "judge_summary": {"decision": "answer_now", "reason": "sufficient_first_pass_support"},
                        "selected_contexts": [{"chunk_id": "chunk-1"}],
                        "query_understanding_summary": {"query_profile": "how_to_faq"},
                        "tool_timing_summary": {"total_latency_ms": 1234.5},
                        "open_diagnosis_target": "rag-abc123",
                    }
                }
            },
        )

        payload = build_execution_route_payload(execution)

        self.assertIn("retrieval_plan_snapshot", payload)
        self.assertEqual(payload["retrieval_plan_snapshot"]["request_id"], "rag-abc123")
        self.assertEqual(payload["retrieval_plan_snapshot"]["query_class"], "how_to_faq")
        self.assertEqual(payload["retrieval_plan_snapshot"]["open_diagnosis_target"], "rag-abc123")

    def test_resolved_confirmation_routes_via_route_agent_and_marks_ticket_resolved(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        route_called: list[bool] = []
        route_executor_called: list[bool] = []

        execution = execute_client_ticket_agent_runtime(
            message="got it, thanks",
            ticket_id="TK-RESOLVE-1",
            customer_id="C-001",
            ticket_subject="Join channel",
            ticket_context=[
                {"role": "customer", "content": "how to join channel"},
                {
                    "role": "assistant",
                    "content": "Use joinChannel with the same channel name and token.",
                },
            ],
            product="audio_video_calling",
            message_id="2026-04-13T01:00:00+00:00",
            latest_assistant_message={
                "role": "assistant",
                "content": "Use joinChannel with the same channel name and token.",
                "workflow_action": "answer_customer",
                "answer_route": "rag",
                "route_reason": "grounded_answer",
                "execution_action": "rag",
            },
            current_ticket_status="communicating",
            route_agent=lambda **_kwargs: route_called.append(True) or SupportRouteDecision(
                scope_label="ticket_resolution",
                route="resolve_ticket",
                confidence=0.99,
                reason="customer_confirmed_resolved",
                matched_signals=["got it", "thanks"],
                response_language="en",
                route_family="ticket_resolution",
                execution_action="resolve_ticket",
                tooling_profile="deterministic_resolution",
            ),
            route_executor=lambda **_kwargs: route_executor_called.append(True) or SupportResolution(
                answer=(
                    "Thanks for your response. I'm glad to hear the information provided was helpful. "
                    "I'll mark this case as resolved. If you have any further questions, please create a new ticket."
                ),
                confidence=1.0,
                sources=[],
                citations=[],
                needs_engineer_guidance=False,
                answer_route="workflow",
                scope_label="ticket_resolution",
                route_reason="customer_confirmed_resolved",
                route_confidence=0.99,
                search_used=False,
                matched_signals=["got it", "thanks"],
                route_family="ticket_resolution",
                execution_action="resolve_ticket",
                tooling_profile="deterministic_resolution",
            ),
            rag_executor=lambda **_kwargs: self.fail("rag agent should not run for resolved confirmation"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run for resolved confirmation"),
        )

        self.assertTrue(route_called)
        self.assertTrue(route_executor_called)
        self.assertEqual(execution.result.workflow_action, "resolve_ticket")
        self.assertEqual(execution.result.next_status, "resolved")
        self.assertEqual(execution.result.answer_route, "workflow")
        self.assertEqual(execution.result.route_family, "ticket_resolution")
        self.assertEqual(execution.result.execution_action, "resolve_ticket")
        self.assertEqual(execution.result.tooling_profile, "deterministic_resolution")
        self.assertEqual(execution.result.route_reason, "customer_confirmed_resolved")
        self.assertFalse(execution.result.sources)
        self.assertFalse(execution.result.citations)
        self.assertFalse(execution.result.needs_investigating)
        self.assertIn("I'll mark this case as resolved", execution.result.answer)
        self.assertEqual(execution.runtime_state.route_agent.get("status"), "completed")
        self.assertEqual(execution.runtime_state.rag_service.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.rag_service.get("reason"), "non_rag_route")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")

    def test_runtime_surfaces_evidence_verdict_diagnostics_without_changing_workflow_action(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        evidence_verdict = {
            "decision": "answer",
            "risk_level": "low",
            "needs_human": False,
            "handoff_reason": None,
            "judge_decision": "answer_now",
            "judge_reason": "sufficient_first_pass_support",
            "confidence": 0.91,
            "citation_count": 1,
            "citation_coverage_ratio": 1.0,
            "selected_doc_count": 1,
            "generation_mode": "structured_answer",
            "deadline_exhausted": False,
            "timeout_stage": None,
            "judge_override": False,
        }

        execution = execute_client_ticket_agent_runtime(
            message="How do I join a channel?",
            ticket_id="TK-EVIDENCE-VERDICT-1",
            customer_id="C-001",
            ticket_subject="Join channel",
            ticket_context=[{"role": "customer", "content": "How do I join a channel?"}],
            product="audio_video_calling",
            message_id="2026-05-20T00:00:00+08:00",
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
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer="Call joinChannel with the same channel name and token on each client.",
                confidence=0.91,
                sources=["https://docs.agora.io/en/video-calling/get-started"],
                citations=[{"chunk_id": "chunk-1"}],
                needs_engineer_guidance=False,
                reason="grounded_answer",
                evidence_summary={
                    "quality_signals": {
                        "generation_mode": "structured_answer",
                        "selected_doc_count": 1,
                        "query_class": "how_to_faq",
                        "needs_human": False,
                    },
                    "diagnostics": {"evidence_verdict": evidence_verdict},
                },
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: self.fail("low risk answer should not wait for review"),
        )

        self.assertEqual(execution.result.workflow_action, "answer_customer")
        self.assertEqual(execution.diagnostics["rag_evidence_risk_level"], "low")
        self.assertEqual(execution.diagnostics["rag_evidence_judge_decision"], "answer_now")
        self.assertIsNone(execution.diagnostics["rag_evidence_handoff_reason"])
        self.assertEqual(execution.diagnostics["rag_evidence_citation_coverage_ratio"], 1.0)
        self.assertEqual(execution.runtime_state.rag_service["evidence_verdict"]["risk_level"], "low")
        self.assertEqual(execution.runtime_state.rag_service["evidence_verdict"]["judge_decision"], "answer_now")

    def test_runtime_blocks_needs_human_rag_candidate_even_when_review_approves(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="How do I join a channel?",
            ticket_id="TK-NEEDS-HUMAN-GATE-1",
            customer_id="C-001",
            ticket_subject="Join channel",
            ticket_context=[{"role": "customer", "content": "How do I join a channel?"}],
            product="audio_video_calling",
            message_id="2026-05-21T00:00:00+08:00",
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
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer="Call joinChannel with the same channel name and token.",
                confidence=0.95,
                sources=["https://docs.agora.io/en/video-calling/get-started"],
                citations=[{"chunk_id": "chunk-1"}],
                needs_engineer_guidance=False,
                reason="grounded_answer",
                evidence_summary={
                    "quality_signals": {
                        "generation_mode": "structured_answer",
                        "selected_doc_count": 1,
                        "needs_human": True,
                    }
                },
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: {
                "decision": "approve_answer",
                "reason": "review_passed",
                "confidence": 0.92,
            },
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.result.investigation_reason, "rag_post_check_insufficient")
        self.assertEqual(execution.runtime_state.review_agent.get("gate_block_reason"), "needs_human")

    def test_runtime_blocks_extractive_fallback_rag_candidate_even_when_review_approves(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        fallback_signals = [
            {"generation_mode": "extractive_fallback", "selected_doc_count": 1},
            {
                "generation_mode": "structured_answer",
                "selected_doc_count": 1,
                "extractive_fallback_used": True,
            },
        ]

        for quality_signals in fallback_signals:
            with self.subTest(quality_signals=quality_signals):
                execution = execute_client_ticket_agent_runtime(
                    message="How do I join a channel?",
                    ticket_id="TK-EXTRACTIVE-GATE-1",
                    customer_id="C-001",
                    ticket_subject="Join channel",
                    ticket_context=[{"role": "customer", "content": "How do I join a channel?"}],
                    product="audio_video_calling",
                    message_id="2026-05-21T00:00:00+08:00",
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
                    rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                        answer="Call joinChannel with the same channel name and token.",
                        confidence=0.95,
                        sources=["https://docs.agora.io/en/video-calling/get-started"],
                        citations=[{"chunk_id": "chunk-1"}],
                        needs_engineer_guidance=False,
                        reason="grounded_answer",
                        evidence_summary={"quality_signals": quality_signals},
                        packed_evidence=None,
                    ),
                    review_agent=lambda **_kwargs: {
                        "decision": "approve_answer",
                        "reason": "review_passed",
                        "confidence": 0.92,
                    },
                )

                self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
                self.assertEqual(execution.result.investigation_reason, "rag_post_check_insufficient")
                self.assertEqual(execution.runtime_state.review_agent.get("gate_block_reason"), "extractive_fallback")

    def test_runtime_review_agent_exception_during_grounded_postcheck_fails_closed(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="How do I join a channel?",
            ticket_id="TK-REVIEW-EXCEPTION-1",
            customer_id="C-001",
            ticket_subject="Join channel",
            ticket_context=[{"role": "customer", "content": "How do I join a channel?"}],
            product="audio_video_calling",
            message_id="2026-05-21T00:00:00+08:00",
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
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer="Call joinChannel with the same channel name and token.",
                confidence=0.80,
                sources=["https://docs.agora.io/en/video-calling/get-started"],
                citations=[{"chunk_id": "chunk-1"}],
                needs_engineer_guidance=False,
                reason="grounded_answer",
                evidence_summary={},
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("review unavailable")),
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.result.investigation_reason, "rag_post_check_error")
        self.assertEqual(execution.runtime_state.review_agent.get("reason"), "rag_post_check_error")

    def test_runtime_review_agent_invalid_output_during_grounded_postcheck_fails_closed(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="How do I join a channel?",
            ticket_id="TK-REVIEW-INVALID-1",
            customer_id="C-001",
            ticket_subject="Join channel",
            ticket_context=[{"role": "customer", "content": "How do I join a channel?"}],
            product="audio_video_calling",
            message_id="2026-05-21T00:00:00+08:00",
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
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer="Call joinChannel with the same channel name and token.",
                confidence=0.80,
                sources=["https://docs.agora.io/en/video-calling/get-started"],
                citations=[{"chunk_id": "chunk-1"}],
                needs_engineer_guidance=False,
                reason="grounded_answer",
                evidence_summary={},
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: {"reason": "missing_decision"},
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.result.investigation_reason, "rag_post_check_error")

    def test_resolved_confirmation_returns_chinese_resolution_message(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        route_called: list[bool] = []

        execution = execute_client_ticket_agent_runtime(
            message="明白了，谢谢",
            ticket_id="TK-RESOLVE-ZH-1",
            customer_id="C-001",
            ticket_subject="Join channel",
            ticket_context=[
                {"role": "customer", "content": "how to join channel"},
                {
                    "role": "assistant",
                    "content": "Use joinChannel with the same channel name and token.",
                },
            ],
            product="audio_video_calling",
            message_id="2026-04-13T01:05:00+00:00",
            latest_assistant_message={
                "role": "assistant",
                "content": "Use joinChannel with the same channel name and token.",
                "workflow_action": "answer_customer",
                "answer_route": "rag",
                "route_reason": "grounded_answer",
                "execution_action": "rag",
            },
            current_ticket_status="communicating",
            route_agent=lambda **_kwargs: route_called.append(True) or SupportRouteDecision(
                scope_label="ticket_resolution",
                route="resolve_ticket",
                confidence=0.99,
                reason="customer_confirmed_resolved",
                matched_signals=["明白了", "谢谢"],
                response_language="zh",
                route_family="ticket_resolution",
                execution_action="resolve_ticket",
                tooling_profile="deterministic_resolution",
            ),
            route_executor=lambda **_kwargs: SupportResolution(
                answer="感谢你的回复，很高兴这些信息对你有帮助。我会将这个工单标记为已解决。如果你后续还有其他问题，欢迎再创建一个新工单。",
                confidence=1.0,
                sources=[],
                citations=[],
                needs_engineer_guidance=False,
                answer_route="workflow",
                scope_label="ticket_resolution",
                route_reason="customer_confirmed_resolved",
                route_confidence=0.99,
                search_used=False,
                matched_signals=["明白了", "谢谢"],
                route_family="ticket_resolution",
                execution_action="resolve_ticket",
                tooling_profile="deterministic_resolution",
            ),
            rag_executor=lambda **_kwargs: self.fail("rag agent should not run for resolved confirmation"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run for resolved confirmation"),
        )

        self.assertTrue(route_called)
        self.assertEqual(execution.result.workflow_action, "resolve_ticket")
        self.assertEqual(execution.result.next_status, "resolved")
        self.assertIn("我会将这个工单标记为已解决", execution.result.answer)

    def test_resolved_confirmation_route_failure_falls_back_to_resolution_for_engineer_guidance_reply(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="it worked, thanks!",
            ticket_id="TK-RESOLVE-ENG-1",
            customer_id="C-001",
            ticket_subject="Black screen issue",
            ticket_context=[
                {"role": "customer", "content": "I got black screen issue."},
                {
                    "role": "assistant",
                    "content": "Please try switching to a different camera and test again.",
                },
            ],
            product="audio_video_calling",
            message_id="2026-04-15T01:00:00+00:00",
            latest_assistant_message={
                "role": "assistant",
                "content": "Please try switching to a different camera and test again.",
                "assistant_message_source": "engineer_guidance",
                "supports_customer_resolution": True,
            },
            current_ticket_status="communicating",
            route_agent=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("router unavailable")),
            route_executor=lambda **_kwargs: self.fail("route executor should not run when the route agent fails"),
            rag_executor=lambda **_kwargs: self.fail("rag agent should not run for resolved confirmation fallback"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run for resolved confirmation fallback"),
        )

        self.assertEqual(execution.result.workflow_action, "resolve_ticket")
        self.assertEqual(execution.result.next_status, "resolved")
        self.assertEqual(execution.result.route_reason, "customer_confirmed_resolved")
        self.assertEqual(execution.runtime_state.route_agent.get("status"), "failed")
        self.assertEqual(execution.runtime_state.rag_service.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")

    def test_gratitude_after_non_substantive_reply_returns_controlled_response_without_resolving(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        route_called: list[bool] = []

        execution = execute_client_ticket_agent_runtime(
            message="thanks",
            ticket_id="TK-RESOLVE-NO-ANSWER-1",
            customer_id="C-001",
            ticket_subject="Join channel",
            ticket_context=[
                {"role": "customer", "content": "how to join channel"},
                {
                    "role": "assistant",
                    "content": "What error or blocker are you seeing?",
                },
            ],
            product="audio_video_calling",
            message_id="2026-04-13T01:10:00+00:00",
            latest_assistant_message={
                "role": "assistant",
                "content": "What error or blocker are you seeing?",
                "workflow_action": "clarify_customer_for_intake",
                "answer_route": "rag",
                "route_reason": "rag_insufficient_evidence",
                "execution_action": "rag",
            },
            current_ticket_status="communicating",
            route_agent=lambda **_kwargs: route_called.append(True) or SupportRouteDecision(
                scope_label="small_talk",
                route="controlled_response",
                confidence=0.91,
                reason="gratitude_acknowledgement",
                matched_signals=["thanks"],
                response_language="en",
                route_family="general_chat",
                execution_action="controlled_response",
                tooling_profile="controlled_acknowledgement",
            ),
            route_executor=lambda **_kwargs: SupportResolution(
                answer="You're welcome. If you need anything else for this ticket, send the next detail here and I'll continue helping.",
                confidence=0.82,
                sources=[],
                citations=[],
                needs_engineer_guidance=False,
                answer_route="controlled_response",
                scope_label="small_talk",
                route_reason="gratitude_acknowledgement",
                route_confidence=0.91,
                search_used=False,
                matched_signals=["thanks"],
                route_family="general_chat",
                execution_action="controlled_response",
                tooling_profile="controlled_acknowledgement",
            ),
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer="",
                confidence=0.0,
                sources=[],
                citations=[],
                needs_engineer_guidance=True,
                reason="rag_unavailable",
                evidence_summary=None,
                packed_evidence=None,
            ),
            review_agent=None,
        )

        self.assertTrue(route_called)
        self.assertNotEqual(execution.result.workflow_action, "resolve_ticket")
        self.assertEqual(execution.result.answer_route, "controlled_response")
        self.assertEqual(execution.result.execution_action, "controlled_response")
        self.assertEqual(execution.result.route_reason, "gratitude_acknowledgement")
        self.assertEqual(execution.runtime_state.route_agent.get("decision"), "controlled_response")

    def test_resolved_confirmation_rejects_remaining_problem_signals(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        route_called: list[bool] = []

        execution = execute_client_ticket_agent_runtime(
            message="thanks, but still not working",
            ticket_id="TK-RESOLVE-STILL-1",
            customer_id="C-001",
            ticket_subject="Join channel",
            ticket_context=[
                {"role": "customer", "content": "how to join channel"},
                {
                    "role": "assistant",
                    "content": "Use joinChannel with the same channel name and token.",
                },
            ],
            product="audio_video_calling",
            message_id="2026-04-13T01:15:00+00:00",
            latest_assistant_message={
                "role": "assistant",
                "content": "Use joinChannel with the same channel name and token.",
                "workflow_action": "answer_customer",
                "answer_route": "rag",
                "route_reason": "grounded_answer",
                "execution_action": "rag",
            },
            current_ticket_status="communicating",
            route_agent=lambda **_kwargs: route_called.append(True) or SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.95,
                reason="technical_question",
                matched_signals=["still not working"],
                response_language="en",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
            ),
            route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer="Please share the exact error or blocker you're seeing.",
                confidence=0.41,
                sources=[],
                citations=[],
                needs_engineer_guidance=True,
                reason="rag_insufficient_evidence",
                evidence_summary={"quality_signals": {"needs_human": True}},
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: TroubleshootingIntakeResult(
                issue_mode="answer",
                known_information={},
                missing_information=["desired_outcome", "blocked_step_or_error"],
                ready_for_engineer_ticket=False,
                customer_reply="What are you trying to achieve? What error or blocker are you seeing?",
            ),
        )

        self.assertTrue(route_called)
        self.assertNotEqual(execution.result.workflow_action, "resolve_ticket")
        self.assertEqual(execution.result.workflow_action, "clarify_customer_for_intake")

    def test_non_rag_route_skips_review_without_starting_or_cancelling_rag(self) -> None:
        from backend.services.client_ticket_agent_runtime import (
            AGENT_NAME_RAG,
            AGENT_NAME_REVIEW,
            execute_client_ticket_agent_runtime,
        )

        rag_calls: list[bool] = []

        def _rag_agent(**_kwargs):
            rag_calls.append(True)
            raise AssertionError("rag agent should not run for non-rag route")

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
            rag_executor=_rag_agent,
            review_agent=lambda **_kwargs: self.fail("review agent should not run for non-rag route"),
        )

        self.assertEqual(execution.result.execution_action, "web_search")
        self.assertEqual(execution.result.workflow_action, "answer_customer")
        self.assertEqual(execution.runtime_state.route_agent.get("decision"), "web_search")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.rag_service.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.rag_service.get("reason"), "non_rag_route")
        self.assertTrue(str(execution.runtime_state.rag_service.get("request_id") or "").startswith("rag-"))
        self.assertFalse(rag_calls)
        self.assertFalse(
            any(
                event.get("agent_name") == AGENT_NAME_RAG and event.get("event_type") == "cancel_requested"
                for event in execution.agent_events
            )
        )
        self.assertFalse(
            any(
                event.get("agent_name") == AGENT_NAME_RAG and event.get("event_type") == "started"
                for event in execution.agent_events
            )
        )
        self.assertTrue(
            any(
                event.get("agent_name") == AGENT_NAME_RAG
                and event.get("event_type") == "skipped"
                and (event.get("payload") or {}).get("reason") == "non_rag_route"
                for event in execution.agent_events
            )
        )
        self.assertTrue(
            any(
                event.get("agent_name") == AGENT_NAME_REVIEW and event.get("event_type") == "skipped"
                for event in execution.agent_events
            )
        )

    @patch("backend.services.billing_automation.extract_detailed_invoice_fields")
    def test_billing_detailed_invoice_route_skips_rag_and_prepares_internal_email(
        self,
        extract_fields_mock,
    ) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime
        from backend.services.support_router import decide_support_route

        extract_fields_mock.return_value = DetailedInvoiceFieldExtraction(
            status="complete",
            collected_fields={
                "issue_date": "6 May 2026",
                "transaction_id": "1104245232004173824",
                "amount": "USD 705.97",
            },
        )

        execution = execute_client_ticket_agent_runtime(
            message=(
                "Please send the detailed invoice. Issue date: 6 May 2026. "
                "Transaction ID: 1104245232004173824. Amount: USD 705.97."
            ),
            ticket_id="TK-BILL-1",
            customer_id="C-001",
            ticket_subject="Detailed invoice request",
            ticket_context=[],
            product="audio_video_calling",
            message_id="2026-06-10T00:00:00+00:00",
            requester="Taylor",
            route_agent=decide_support_route,
            route_executor=lambda **kwargs: resolve_support_message(
                kwargs["message"],
                ticket_id=kwargs.get("ticket_id"),
                customer_id=kwargs.get("customer_id"),
                ticket_subject=kwargs.get("ticket_subject"),
                ticket_context=kwargs.get("ticket_context"),
                product=kwargs.get("product"),
                latest_assistant_message=kwargs.get("latest_assistant_message"),
                current_ticket_status=kwargs.get("current_ticket_status"),
                has_active_engineer_case=bool(kwargs.get("has_active_engineer_case")),
                decision=kwargs.get("decision"),
            ),
            rag_executor=lambda **_kwargs: self.fail("rag executor should not run for billing route"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run for billing route"),
        )

        self.assertEqual(execution.result.answer_route, "workflow")
        self.assertEqual(execution.result.route_family, "automated")
        self.assertEqual(execution.result.execution_action, "detailed_invoice")
        self.assertEqual(execution.result.tooling_profile, "deterministic_billing_intake")
        self.assertEqual(execution.result.workflow_action, "answer_customer")
        self.assertTrue(execution.result.answer.startswith("Hi Taylor,"))
        self.assertTrue(execution.result.answer.endswith("Best Regards,\nSid"))
        extract_fields_mock.assert_called_once()
        self.assertIsNotNone(execution.result.evidence_summary)
        assert execution.result.evidence_summary is not None
        internal_email = execution.result.evidence_summary["billing_internal_email"]
        self.assertEqual(
            internal_email["subject"],
            "[Billing Request] Detailed invoice request - Ticket TK-BILL-1",
        )
        self.assertIn("Customer email: C-001", internal_email["body"])
        self.assertIn("Transaction ID: 1104245232004173824", internal_email["body"])
        self.assertEqual(execution.runtime_state.route_agent.get("decision"), "detailed_invoice")
        self.assertEqual(execution.runtime_state.rag_service.get("reason"), "non_rag_route")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")

    def test_rag_route_starts_rag_agent_only_after_route_agent_returns(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        rag_started = threading.Event()
        route_returned = threading.Event()

        def _route_agent(**_kwargs):
            rag_started.wait(timeout=0.5)
            route_returned.set()
            return SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.94,
                reason="technical_question",
                matched_signals=["join channel"],
                response_language="en",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
            )

        def _rag_agent(**_kwargs):
            rag_started.set()
            self.assertTrue(route_returned.is_set(), "rag agent started before route agent returned")
            return RagTicketAnswerDetail(
                answer="Call joinChannel with the same channel name and token.",
                confidence=0.91,
                sources=["https://docs.agora.io/en/video-calling/get-started"],
                citations=[{"chunk_id": "chunk-1"}],
                needs_engineer_guidance=False,
                reason="grounded_answer",
                evidence_summary={},
                packed_evidence=None,
            )

        execution = execute_client_ticket_agent_runtime(
            message="How do I join a channel?",
            ticket_id="TK-ROUTE-FIRST-1",
            customer_id="C-001",
            ticket_subject="Join channel",
            ticket_context=[{"role": "customer", "content": "How do I join a channel?"}],
            product="audio_video_calling",
            message_id="2026-04-04T00:00:00+00:00",
            route_agent=_route_agent,
            route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
            rag_executor=_rag_agent,
            review_agent=lambda **_kwargs: self.fail("grounded rag answer should not run review"),
        )

        self.assertEqual(execution.result.execution_action, "rag")
        self.assertEqual(execution.runtime_state.route_agent.get("status"), "completed")
        self.assertEqual(execution.runtime_state.rag_service.get("status"), "completed")
        self.assertTrue(route_returned.is_set())
        self.assertTrue(rag_started.is_set())

    def test_product_portfolio_route_uses_real_web_search_resolution_builder(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        payload = {
            "output_text": (
                "For broadcasting, the best starting point is to separate one-to-many delivery from "
                "interactive live experiences.\n\n"
                "Core products:\n"
                "- **Broadcast Streaming** — Best for large-scale one-way broadcasting.\n"
                "- **Interactive Live Streaming** — Best when hosts and audiences need low-latency interaction.\n\n"
                "If you would like to speak with someone, please use Agora's official Talk to Us / Contact Sales path."
            ),
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {
                                "url": "https://www.agora.io/en/products/broadcast-streaming/",
                                "title": "Broadcast Streaming",
                            }
                        ]
                    },
                },
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": (
                                "For broadcasting, the best starting point is to separate one-to-many delivery from "
                                "interactive live experiences.\n\n"
                                "Core products:\n"
                                "- **Broadcast Streaming** — Best for large-scale one-way broadcasting.\n"
                                "- **Interactive Live Streaming** — Best when hosts and audiences need low-latency interaction.\n\n"
                                "If you would like to speak with someone, please use Agora's official Talk to Us / Contact Sales path."
                            ),
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://www.agora.io/en/products/broadcast-streaming/",
                                    "title": "Broadcast Streaming",
                                }
                            ],
                        }
                    ],
                },
            ],
        }

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                import json

                return json.dumps(payload).encode("utf-8")

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(),
        ):
            execution = execute_client_ticket_agent_runtime(
                message=self._TK_165_MESSAGE,
                ticket_id="TK-165",
                customer_id="C-001",
                ticket_subject="Agora products for broadcasting",
                ticket_context=[{"role": "customer", "content": self._TK_165_MESSAGE}],
                product="audio_video_calling",
                message_id="2026-04-22T00:00:00+00:00",
                requester="Zac",
                route_agent=lambda **_kwargs: SupportRouteDecision(
                    scope_label="agora_non_technical",
                    route="web_search",
                    confidence=0.97,
                    reason="agora_product_portfolio",
                    matched_signals=["broadcasting", "products that agora provides"],
                    response_language="en",
                    route_family="web_company_info",
                    execution_action="web_search",
                    tooling_profile="official_web_search",
                ),
                route_executor=lambda **kwargs: resolve_support_message(
                    kwargs["message"],
                    ticket_subject=kwargs.get("ticket_subject"),
                    ticket_context=kwargs.get("ticket_context"),
                    product=kwargs.get("product"),
                    latest_assistant_message=kwargs.get("latest_assistant_message"),
                    current_ticket_status=kwargs.get("current_ticket_status"),
                    has_active_engineer_case=bool(kwargs.get("has_active_engineer_case")),
                    decision=kwargs.get("decision"),
                ),
                rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                    answer="Use joinChannel with a valid token.",
                    confidence=0.91,
                    sources=["https://docs.agora.io/en/video-calling/get-started"],
                    citations=[{"chunk_id": "chunk-1"}],
                    needs_engineer_guidance=False,
                    reason="grounded_answer",
                    evidence_summary={},
                    packed_evidence=None,
                ),
                review_agent=lambda **_kwargs: self.fail("review agent should not run for product portfolio route"),
            )

        self.assertEqual(execution.result.answer_route, "web_search")
        self.assertEqual(execution.result.execution_action, "web_search")
        self.assertEqual(execution.result.route_reason, "agora_product_portfolio")
        self.assertTrue(execution.result.answer.startswith("Hi Zac,"))
        self.assertIn("Hope all is well. Thank you for reaching out!", execution.result.answer)
        self.assertIn("\n- **Broadcast Streaming**", execution.result.answer)
        self.assertIn("\n- **Interactive Live Streaming**", execution.result.answer)
        self.assertIn("Broadcast Streaming", execution.result.answer)
        self.assertTrue(execution.result.answer.endswith("Best Regards,\nSid"))
        self.assertEqual(execution.runtime_state.route_agent.get("decision"), "web_search")
        self.assertEqual(execution.runtime_state.rag_service.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.rag_service.get("reason"), "non_rag_route")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")
        self.assertTrue(
            all("agora.io" in citation["source_url"] for citation in execution.result.citations),
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
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
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
        )

        self.assertEqual(execution.result.workflow_action, "clarify_customer_for_intake")
        self.assertEqual(execution.runtime_state.review_agent.get("decision"), "clarify_customer_for_intake")
        self.assertEqual(execution.result.client_intake_state["missing_information"], ["channel_name", "problematic_uid", "issue_timestamp"])
        self.assertEqual(execution.runtime_state.status, "completed")

    def test_api_semantics_timeout_routes_to_docs_clarification_without_channel_timestamp(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime
        from backend.services.troubleshooting_intake import evaluate_troubleshooting_intake

        execution = execute_client_ticket_agent_runtime(
            message=self._BAN_API_MISMATCH_MESSAGE,
            ticket_id="TK-API-1",
            customer_id="C-001",
            ticket_subject="Ban User Privileges API mismatch",
            ticket_context=[{"role": "customer", "content": self._BAN_API_MISMATCH_MESSAGE}],
            product="audio_video_calling",
            message_id="2026-04-08T00:00:00+00:00",
            route_agent=lambda **_kwargs: SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.98,
                reason="docs_api_semantics_support",
                matched_signals=["docs_url", "endpoint_path"],
                response_language="en",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
            ),
            route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer="",
                confidence=0.0,
                sources=[],
                citations=[],
                needs_engineer_guidance=True,
                reason="deadline_exhausted",
                evidence_summary={
                    "diagnostics": {
                        "retrieval_plan_snapshot": {
                            "query_class": "api_semantics_mismatch",
                        }
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
        )

        self.assertEqual(execution.result.workflow_action, "clarify_customer_for_intake")
        self.assertEqual(execution.result.client_intake_state["issue_mode"], "answer")
        self.assertNotIn("channel_name", execution.result.client_intake_state["missing_information"])
        self.assertNotIn("issue_timestamp", execution.result.client_intake_state["missing_information"])
        self.assertIn("platform", execution.result.answer.lower())
        self.assertIn("sdk", execution.result.answer.lower())
        self.assertEqual(execution.result.investigation_reason, "deadline_exhausted")

    def test_api_semantics_grounded_answer_skips_post_check_and_answers_customer(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message=self._BAN_API_MISMATCH_MESSAGE,
            ticket_id="TK-API-2",
            customer_id="C-001",
            ticket_subject="Ban User Privileges API mismatch",
            ticket_context=[{"role": "customer", "content": self._BAN_API_MISMATCH_MESSAGE}],
            product="audio_video_calling",
            message_id="2026-04-08T00:00:00+00:00",
            route_agent=lambda **_kwargs: SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.98,
                reason="docs_api_semantics_support",
                matched_signals=["docs_url", "endpoint_path"],
                response_language="en",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
            ),
            route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer=(
                    "1. For disbanding a channel, the docs say to fill in `cname` and leave `uid` and `ip` blank. "
                    "The create-rule request parameters also say do not set `uid` to `0`, so you should omit `uid` "
                    "instead of sending `uid: 0`.\n\n"
                    "2. For `time` or `time_in_seconds`, a value of `0` does not create a persistent rule."
                ),
                confidence=0.94,
                sources=[
                    "https://docs.agora.io/en/broadcast-streaming/channel-management-api/best-practices/ban-user-privileges",
                    "https://docs.agora.io/en/broadcast-streaming/channel-management-api/endpoint/ban-user-privileges/create-rules",
                ],
                citations=[
                    {"chunk_id": "chunk-disband"},
                    {"chunk_id": "chunk-create-rule"},
                ],
                needs_engineer_guidance=False,
                reason="grounded_answer",
                evidence_summary={
                    "quality_signals": {
                        "query_class": "api_semantics_mismatch",
                        "generation_mode": "api_semantics_deterministic",
                        "selected_doc_count": 3,
                        "top1_similarity_score": 0.95,
                        "needs_human": False,
                    },
                    "diagnostics": {
                        "retrieval_plan_snapshot": {
                            "query_class": "api_semantics_mismatch",
                            "fanout_used": True,
                        }
                    },
                },
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: self.fail("review agent should not run for grounded api semantics answers"),
        )

        self.assertEqual(execution.result.workflow_action, "answer_customer")
        self.assertFalse(execution.result.needs_investigating)
        self.assertIn("omit `uid`", execution.result.answer)
        self.assertIn("does not create a persistent rule", execution.result.answer)
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.review_agent.get("reason"), "low_risk_grounded_answer")

    def test_short_how_to_faq_grounded_answer_skips_post_check_for_lexical_light_path(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="how to join channel",
            ticket_id="TK-FAQ-LOW-RISK",
            customer_id="C-001",
            ticket_subject="Join channel",
            ticket_context=[{"role": "customer", "content": "how to join channel"}],
            product="audio_video_calling",
            message_id="2026-04-09T00:00:00+00:00",
            route_agent=lambda **_kwargs: SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.98,
                reason="channel_joining_support",
                matched_signals=["join channel"],
                response_language="en",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
            ),
            route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer=(
                    "To join a channel, call the SDK join-channel method with your channel name, token, "
                    "user ID, and channel media options."
                ),
                confidence=0.77,
                sources=[
                    "https://docs.agora.io/en/voice-calling/get-started/get-started-sdk?platform=unity",
                    "https://docs.agora.io/en/video-calling/get-started/get-started-sdk?platform=windows",
                ],
                citations=[
                    {"chunk_id": "chunk-voice-join"},
                    {"chunk_id": "chunk-video-join"},
                ],
                needs_engineer_guidance=False,
                reason="grounded_answer",
                evidence_summary={
                    "quality_signals": {
                        "query_class": "how_to_faq",
                        "generation_mode": "structured_answer",
                        "selected_doc_count": 2,
                        "needs_human": False,
                    }
                },
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: self.fail("review agent should not run for low-risk how_to_faq answers"),
        )

        self.assertEqual(execution.result.workflow_action, "answer_customer")
        self.assertFalse(execution.result.needs_investigating)
        self.assertIn("join-channel method", execution.result.answer)
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.review_agent.get("reason"), "low_risk_grounded_answer")

    def test_follow_up_code_example_runtime_answers_customer_from_inherited_join_topic(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime
        from backend.services.rag_qa import run_rag_query

        class _FakeProvider:
            provider_name = "siliconflow"
            model_id = "BAAI/bge-m3"
            vector_dim = 1024

            def count_tokens(self, text: str) -> int:
                return max(1, len(str(text or "").split()))

            def drain_request_log(self) -> list[dict[str, object]]:
                return []

        def _bm25_chunk():
            from backend.services.rag_qa import RetrievedChunk

            return RetrievedChunk(
                chunk_id="bm25-join",
                text=(
                    "Call joinChannel with the same channel name on each client.\n\n"
                    "```cpp\nengine->joinChannel(token, channelName, uid, options);\n```"
                ),
                source_path="official/get-started.md",
                similarity=0.95,
            )

        def _fts_auth_chunk():
            from backend.services.rag_qa import RetrievedChunk

            return RetrievedChunk(
                chunk_id="fts-auth",
                text="Generate a token from your authentication server before calling joinChannel.",
                source_path="official/authentication-workflow.md",
                similarity=0.88,
            )

        def _rag_agent(**kwargs):
            rag_result = run_rag_query(
                str(kwargs.get("message") or ""),
                ticket_context=kwargs.get("ticket_context"),
                product=kwargs.get("product"),
            )
            self.assertIsNotNone(rag_result)
            assert rag_result is not None
            return RagTicketAnswerDetail(
                answer=rag_result.answer.answer,
                confidence=rag_result.answer.confidence,
                sources=list(rag_result.answer.sources),
                citations=list(rag_result.answer.citations),
                needs_engineer_guidance=bool(rag_result.trace.needs_human),
                reason=str(rag_result.trace.handoff_reason or "grounded_answer"),
                evidence_summary={
                    "quality_signals": {
                        "query_class": rag_result.trace.query_class,
                        "generation_mode": rag_result.trace.generation_mode,
                        "selected_doc_count": rag_result.trace.selected_doc_count,
                        "needs_human": rag_result.trace.needs_human,
                        "extractive_fallback_used": rag_result.trace.extractive_fallback_used,
                    }
                },
                packed_evidence=None,
            )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "fallback_models": (),
                "query_policy": "balanced",
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=_FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                side_effect=AssertionError("runtime follow-up example should stay on inherited lexical light path"),
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                side_effect=AssertionError("planner should not run for inherited generic join examples"),
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=AssertionError("vector retrieval should not run for inherited generic join examples"),
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                return_value=[_bm25_chunk()],
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[_fts_auth_chunk()],
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                side_effect=lambda *args, **kwargs: (
                    [_bm25_chunk(), _fts_auth_chunk()],
                    {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None},
                ),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=AssertionError("external rerank should be skipped for inherited lexical light path"),
            ), patch(
                "backend.services.rag_qa._fetch_generic_join_pinned_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                side_effect=AssertionError("generic join deterministic answer should satisfy inherited code-example follow-ups"),
            ):
                execution = execute_client_ticket_agent_runtime(
                    message="Can you share a code example?",
                    ticket_id="TK-171-RUNTIME",
                    customer_id="C-001",
                    ticket_subject="Join channel",
                    ticket_context=[
                        {"role": "customer", "content": "How to join channel?"},
                        {
                            "role": "assistant",
                            "content": (
                                "To join a channel, initialize the engine, prepare your token, "
                                "then call the SDK join method."
                            ),
                        },
                    ],
                    product="audio_video_calling",
                    message_id="2026-04-22T09:00:00+00:00",
                    route_agent=lambda **_kwargs: SupportRouteDecision(
                        scope_label="agora_technical",
                        route="rag",
                        confidence=0.75,
                        reason="conservative_agora_technical_fallback",
                        matched_signals=[],
                        response_language="en",
                        route_family="agora_docs_rag",
                        execution_action="rag",
                        tooling_profile="agora_docs_only",
                    ),
                    route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
                    rag_executor=_rag_agent,
                    review_agent=lambda **_kwargs: self.fail("review agent should not run for grounded inherited example answers"),
            )

        self.assertEqual(execution.result.workflow_action, "answer_customer")
        self.assertFalse(execution.result.needs_investigating)
        self.assertIn("reference example", execution.result.answer.lower())
        self.assertIn("joinchannel", execution.result.answer.lower())
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.review_agent.get("reason"), "low_risk_grounded_answer")

    def test_polite_onboarding_how_to_grounded_answer_skips_review(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        message = (
            "Hi Team, I am new to Agora and trying to integrate Agora SDK. However, I don't know "
            "how to join the channel as requested. Could you help explain to me and guide me to "
            "join the user into the channel?"
        )

        execution = execute_client_ticket_agent_runtime(
            message=message,
            ticket_id="TK-FAQ-ONBOARDING",
            customer_id="C-001",
            ticket_subject="Join channel",
            ticket_context=[{"role": "customer", "content": message}],
            product="audio_video_calling",
            message_id="2026-04-09T00:05:00+00:00",
            route_agent=lambda **_kwargs: SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.98,
                reason="channel_joining_support",
                matched_signals=["join channel"],
                response_language="en",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
            ),
            route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer=(
                    "To join a channel, call the SDK join-channel method with your channel name, token, "
                    "user ID, and channel media options."
                ),
                confidence=0.79,
                sources=[
                    "https://docs.agora.io/en/video-calling/get-started/get-started-sdk",
                ],
                citations=[
                    {"chunk_id": "chunk-video-join"},
                ],
                needs_engineer_guidance=False,
                reason="grounded_answer",
                evidence_summary={
                    "quality_signals": {
                        "query_class": "how_to_faq",
                        "generation_mode": "structured_answer",
                        "selected_doc_count": 1,
                        "needs_human": False,
                    }
                },
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: self.fail("review agent should not run for low-risk onboarding how_to answers"),
        )

        self.assertEqual(execution.result.workflow_action, "answer_customer")
        self.assertFalse(execution.result.needs_investigating)
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.review_agent.get("reason"), "low_risk_grounded_answer")

    def test_short_how_to_faq_grounded_answer_without_citations_does_not_answer_customer(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="how to join channel",
            ticket_id="TK-FAQ-LOW-RISK-NO-CITE",
            customer_id="C-001",
            ticket_subject="Join channel",
            ticket_context=[{"role": "customer", "content": "how to join channel"}],
            product="audio_video_calling",
            message_id="2026-04-09T00:00:00+00:00",
            route_agent=lambda **_kwargs: SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.98,
                reason="channel_joining_support",
                matched_signals=["join channel"],
                response_language="en",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
            ),
            route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer=(
                    "To join a channel, call the SDK join-channel method with your channel name, token, "
                    "user ID, and channel media options."
                ),
                confidence=0.77,
                sources=[
                    "https://docs.agora.io/en/voice-calling/get-started/get-started-sdk?platform=unity",
                ],
                citations=[],
                needs_engineer_guidance=False,
                reason="grounded_answer",
                evidence_summary={
                    "quality_signals": {
                        "query_class": "how_to_faq",
                        "generation_mode": "structured_answer",
                        "selected_doc_count": 1,
                        "needs_human": False,
                    }
                },
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: {
                "decision": "approve_answer",
                "reason": "review_passed",
                "confidence": 0.92,
            },
        )

        self.assertNotEqual(execution.result.workflow_action, "answer_customer")
        self.assertNotIn("join-channel method", execution.result.answer)
        self.assertFalse(execution.result.citations)
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "completed")
        self.assertNotEqual(execution.runtime_state.review_agent.get("reason"), "low_risk_grounded_answer")

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
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
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
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.result.client_intake_state["issue_mode"], "answer")
        self.assertEqual(execution.result.client_intake_state["missing_information"], [])
        self.assertEqual(execution.result.answer, "")
        self.assertNotIn("grounded answer", execution.result.answer.lower())
        self.assertEqual(execution.runtime_state.review_agent.get("decision"), "open_engineer_ticket")

    def test_rag_insufficient_evidence_discards_technical_clarify_reply_without_citations(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="How do I join channel?",
            ticket_id="TK-RAG-FAQ-NO-CITE-CLARIFY",
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
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer="I couldn't find enough information in the docs alone.",
                confidence=0.41,
                sources=[],
                citations=[],
                needs_engineer_guidance=True,
                reason="rag_insufficient_evidence",
                evidence_summary={
                    "quality_signals": {
                        "generation_mode": "insufficient_evidence",
                        "extractive_fallback_used": False,
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
                    "To join a channel in Agora Video Calling, call joinChannel with your token, "
                    "channel name, uid, and options."
                ),
            ),
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.result.answer, "")
        self.assertNotIn("grounded answer", execution.result.answer.lower())
        self.assertNotIn("call joinChannel", execution.result.answer)
        self.assertFalse(execution.result.citations)

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
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
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
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.result.investigation_reason, "rag_post_check_insufficient")
        self.assertEqual(execution.result.route_reason, "grounded_answer")
        self.assertEqual(execution.result.answer, "")
        self.assertFalse(execution.result.citations)
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "completed")
        self.assertEqual(execution.runtime_state.review_agent.get("decision"), "open_engineer_ticket")

    def test_troubleshooting_weak_evidence_postcheck_rejection_enters_intake(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        review_modes: list[str] = []
        trace_contexts: list[_FakeOpenAiReviewTrace] = []

        def _fake_start_review_trace(**kwargs: object) -> _FakeOpenAiReviewTrace:
            context = _FakeOpenAiReviewTrace(
                trace_id=f"trace-{kwargs['mode']}",
                group_id=str(kwargs["run_id"]),
                workflow_name=f"supportportal.review_agent.{kwargs['mode']}",
                mode=str(kwargs["mode"]),
            )
            trace_contexts.append(context)
            return context

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

        with patch(
            "backend.services.client_ticket_agent_runtime.openai_agent_tracing.start_review_trace",
            side_effect=_fake_start_review_trace,
        ):
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
                rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
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
            )

        self.assertEqual(review_modes, ["grounded_postcheck", "pre_engineer_intake"])
        self.assertEqual(execution.result.workflow_action, "clarify_customer_for_intake")
        self.assertFalse(execution.result.needs_investigating)
        self.assertEqual(execution.result.investigation_reason, "rag_post_check_insufficient")
        self.assertEqual(
            execution.result.client_intake_state["missing_information"],
            ["channel_name", "problematic_uid", "issue_timestamp"],
        )
        self.assertEqual(
            execution.result.client_intake_state["pending_investigation_reason"],
            "rag_post_check_insufficient",
        )
        self.assertEqual(execution.result.route_reason, "rag_post_check_insufficient")
        self.assertNotIn("Check whether the remote user is publishing video", execution.result.answer)
        self.assertEqual(execution.runtime_state.review_agent.get("decision"), "clarify_customer_for_intake")
        self.assertEqual(execution.runtime_state.review_agent.get("gate_block_reason"), "weak_troubleshooting_evidence")
        review_trace_state = execution.runtime_state.review_agent.get("openai_tracing") or {}
        self.assertEqual(review_trace_state.get("group_id"), execution.result.run_id)
        self.assertEqual(review_trace_state.get("latest_trace_id"), "trace-pre_engineer_intake")
        self.assertEqual(
            review_trace_state.get("traces"),
            [
                {
                    "mode": "grounded_postcheck",
                    "trace_id": "trace-grounded_postcheck",
                    "group_id": execution.result.run_id,
                    "workflow_name": "supportportal.review_agent.grounded_postcheck",
                },
                {
                    "mode": "pre_engineer_intake",
                    "trace_id": "trace-pre_engineer_intake",
                    "group_id": execution.result.run_id,
                    "workflow_name": "supportportal.review_agent.pre_engineer_intake",
                },
            ],
        )
        review_started = [
            event for event in execution.agent_events if event.get("agent_name") == "review_agent" and event.get("event_type") == "started"
        ]
        self.assertEqual(
            [event["payload"]["openai_tracing"]["mode"] for event in review_started],
            ["grounded_postcheck", "pre_engineer_intake"],
        )
        review_completed = next(
            event
            for event in execution.agent_events
            if event.get("agent_name") == "review_agent" and event.get("event_type") == "completed"
        )
        self.assertEqual(review_completed["payload"]["openai_tracing"]["mode"], "pre_engineer_intake")
        self.assertTrue(trace_contexts)
        self.assertEqual(trace_contexts[0].function_calls[0]["name"], "review_agent.grounded_postcheck")
        self.assertEqual(trace_contexts[1].function_calls[0]["name"], "review_agent.pre_engineer_intake")

    def test_cited_feature_enable_postcheck_rejection_answers_customer_before_follow_up(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        review_modes: list[str] = []
        trace_contexts: list[_FakeOpenAiReviewTrace] = []

        def _fake_start_review_trace(**kwargs: object) -> _FakeOpenAiReviewTrace:
            context = _FakeOpenAiReviewTrace(
                trace_id=f"trace-{kwargs['mode']}",
                group_id=str(kwargs["run_id"]),
                workflow_name=f"supportportal.review_agent.{kwargs['mode']}",
                mode=str(kwargs["mode"]),
            )
            trace_contexts.append(context)
            return context

        def _review_agent(**kwargs: object) -> object:
            mode = str(kwargs.get("mode") or "")
            review_modes.append(mode)
            if mode == "grounded_postcheck":
                return {"decision": "open_engineer_ticket", "reason": "review_insufficient", "confidence": 0.61}
            self.fail(f"unexpected review mode {mode!r}")

        with patch(
            "backend.services.client_ticket_agent_runtime.openai_agent_tracing.start_review_trace",
            side_effect=_fake_start_review_trace,
        ):
            execution = execute_client_ticket_agent_runtime(
                message="how to enable the dual stream",
                ticket_id="TK-RISK-DUAL-1",
                customer_id="C-001",
                ticket_subject="Dual stream",
                ticket_context=[{"role": "customer", "content": "how to enable the dual stream"}],
                product="audio_video_calling",
                message_id="2026-04-13T00:00:00+00:00",
                client_intake_state={
                    "phase": "gather_customer_inputs",
                    "product": "audio_video_calling",
                    "issue_mode": "answer",
                    "known_information": {},
                    "missing_information": ["desired_outcome", "blocked_step_or_error"],
                    "ready_for_engineer_ticket": False,
                    "last_updated_at": "2026-04-13T00:00:00+00:00",
                },
                route_agent=lambda **_kwargs: SupportRouteDecision(
                    scope_label="agora_technical",
                    route="rag",
                    confidence=0.95,
                    reason="technical_question",
                    matched_signals=["dual stream"],
                    response_language="en",
                    route_family="agora_docs_rag",
                    execution_action="rag",
                    tooling_profile="agora_docs_only",
                ),
                route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
                rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                    answer="Call `client.enableDualStream()` before remote users subscribe to the low stream.",
                    confidence=0.9,
                    sources=["https://docs.agora.io/en/video-calling/advanced-features/media-stream-fallback?platform=web"],
                    citations=[{"chunk_id": "chunk-dual-stream"}],
                    needs_engineer_guidance=False,
                    reason="grounded_answer",
                    evidence_summary={
                        "quality_signals": {
                            "query_class": "configuration",
                            "generation_mode": "structured_answer",
                            "selected_doc_count": 2,
                            "top1_similarity_score": 0.94,
                            "needs_human": False,
                        }
                    },
                    packed_evidence=None,
                ),
                review_agent=_review_agent,
            )

        self.assertEqual(review_modes, ["grounded_postcheck"])
        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.result.route_reason, "grounded_answer")
        self.assertEqual(execution.result.answer, "")
        self.assertFalse(execution.result.citations)
        self.assertEqual(execution.result.client_intake_state["issue_mode"], "answer")
        self.assertEqual(execution.result.client_intake_state["missing_information"], [])
        self.assertEqual(execution.runtime_state.review_agent.get("decision"), "open_engineer_ticket")
        review_trace_state = execution.runtime_state.review_agent.get("openai_tracing") or {}
        self.assertEqual(review_trace_state.get("group_id"), execution.result.run_id)
        self.assertEqual(review_trace_state.get("latest_trace_id"), "trace-grounded_postcheck")
        self.assertEqual(
            review_trace_state.get("traces"),
            [
                {
                    "mode": "grounded_postcheck",
                    "trace_id": "trace-grounded_postcheck",
                    "group_id": execution.result.run_id,
                    "workflow_name": "supportportal.review_agent.grounded_postcheck",
                }
            ],
        )
        review_started = next(
            event
            for event in execution.agent_events
            if event.get("agent_name") == "review_agent" and event.get("event_type") == "started"
        )
        self.assertEqual(review_started["payload"]["openai_tracing"]["trace_id"], "trace-grounded_postcheck")
        review_completed = next(
            event
            for event in execution.agent_events
            if event.get("agent_name") == "review_agent" and event.get("event_type") == "completed"
        )
        self.assertEqual(review_completed["payload"]["openai_tracing"]["mode"], "grounded_postcheck")
        self.assertEqual(trace_contexts[0].function_calls[0]["name"], "review_agent.grounded_postcheck")

    def test_ready_investigation_intake_follow_up_short_circuits_before_rag_and_uses_intake_complete_reason(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="it happened at 12:00pm utc+8",
            ticket_id="TK-RISK-TRBL-2",
            customer_id="C-001",
            ticket_subject="Black screen",
            ticket_context=[
                {"role": "customer", "content": "i got black screen issue"},
                {
                    "role": "customer",
                    "content": "channel name:zilingtest, uid 1, happened around 3/4 at 12pm",
                },
                {
                    "role": "assistant",
                    "content": (
                        "Known so far: issue symptom is black screen issue; channel name is zilingtest; "
                        "problematic uid is 1. Please share the issue timezone."
                    ),
                },
            ],
            product="audio_video_calling",
            message_id="2026-03-04T04:05:00+00:00",
            client_intake_state={
                "phase": "gather_customer_inputs",
                "product": "audio_video_calling",
                "issue_mode": "investigation",
                "known_information": {
                    "issue_symptom": "black screen issue",
                    "channel_name": "zilingtest",
                    "problematic_uid": "1",
                },
                "missing_information": ["issue_timestamp"],
                "ready_for_engineer_ticket": False,
                "pending_investigation_reason": "rag_post_check_insufficient",
                "issue_timestamp_parts": {"date": "2026-03-04", "time": "12:00pm"},
                "last_updated_at": "2026-03-04T04:00:00+00:00",
            },
            route_agent=lambda **_kwargs: self.fail("route agent should not run once investigation intake is complete"),
            route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
            rag_executor=lambda **_kwargs: self.fail("rag agent should not run once investigation intake is complete"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run once investigation intake is complete"),
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertTrue(execution.result.needs_investigating)
        self.assertEqual(execution.result.route_reason, "investigation_intake_complete")
        self.assertEqual(execution.result.investigation_reason, "investigation_intake_complete")
        self.assertIn("requires further internal investigation", execution.result.answer.lower())
        self.assertEqual(
            execution.result.client_intake_state["known_information"]["issue_timestamp"],
            "2026-03-04 12:00pm UTC+8",
        )
        self.assertEqual(
            execution.result.client_intake_state["pending_investigation_reason"],
            "investigation_intake_complete",
        )
        self.assertEqual(execution.runtime_state.route_agent.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.rag_service.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.route_agent.get("reason"), "investigation_intake_complete")
        self.assertEqual(execution.runtime_state.rag_service.get("reason"), "investigation_intake_complete")
        self.assertEqual(execution.runtime_state.review_agent.get("reason"), "investigation_intake_complete")

    def test_tk_122_follow_up_after_first_intake_round_requests_remaining_timestamp_details(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="channel name: zilingtest, uid is 2, and the issue happened on april 3rd",
            ticket_id="TK-122",
            customer_id="C-001",
            ticket_subject="Black screen",
            ticket_context=[
                {"role": "customer", "content": "i got black screen issue"},
                {
                    "role": "assistant",
                    "content": (
                        "If the issue continues, please share channel name, problematic uid, and issue timestamp "
                        "so I can narrow down the Audio/Video Calling investigation."
                    ),
                },
            ],
            product="audio_video_calling",
            message_id="2026-04-14T02:11:08.752498+00:00",
            client_intake_state={
                "phase": "gather_customer_inputs",
                "product": "audio_video_calling",
                "issue_mode": "investigation",
                "known_information": {"issue_symptom": "black screen issue"},
                "missing_information": ["channel_name", "problematic_uid", "issue_timestamp"],
                "ready_for_engineer_ticket": False,
                "pending_investigation_reason": "rag_post_check_insufficient",
                "clarification_rounds_used": 1,
                "last_updated_at": "2026-04-14T02:08:33.337732+00:00",
            },
            route_agent=lambda **_kwargs: self.fail("route agent should not run during deterministic second clarify"),
            route_executor=lambda **_kwargs: self.fail("route executor should not run during deterministic second clarify"),
            rag_executor=lambda **_kwargs: self.fail("rag agent should not run during deterministic second clarify"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run during deterministic second clarify"),
        )

        self.assertEqual(execution.result.workflow_action, "clarify_customer_for_intake")
        self.assertEqual(execution.result.client_intake_state["phase"], "gather_customer_inputs")
        self.assertFalse(execution.result.client_intake_state["ready_for_engineer_ticket"])
        self.assertEqual(execution.result.client_intake_state["missing_information"], ["issue_timestamp"])
        self.assertEqual(execution.result.client_intake_state["clarification_rounds_used"], 2)
        self.assertEqual(
            execution.result.client_intake_state["known_information"]["channel_name"],
            "zilingtest",
        )
        self.assertEqual(
            execution.result.client_intake_state["known_information"]["problematic_uid"],
            "2",
        )
        self.assertEqual(
            execution.result.client_intake_state["issue_timestamp_parts"],
            {"date": "2026-04-03"},
        )
        self.assertIn("issue time", execution.result.answer.lower())
        self.assertIn("timezone", execution.result.answer.lower())
        self.assertEqual(execution.runtime_state.route_agent.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.rag_service.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")

    def test_tk_123_follow_up_without_timestamp_after_first_intake_round_requests_issue_timestamp(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="channel name: zilingtest, uid 2",
            ticket_id="TK-123",
            customer_id="C-001",
            ticket_subject="Black screen",
            ticket_context=[
                {"role": "customer", "content": "i got black screen issue"},
                {
                    "role": "assistant",
                    "content": (
                        "If the issue continues, please share channel name, problematic uid, and issue timestamp "
                        "so I can narrow down the Audio/Video Calling investigation."
                    ),
                },
            ],
            product="audio_video_calling",
            message_id="2026-04-16T03:51:47.709113+00:00",
            client_intake_state={
                "phase": "gather_customer_inputs",
                "product": "audio_video_calling",
                "issue_mode": "investigation",
                "known_information": {"issue_symptom": "black screen issue"},
                "missing_information": ["channel_name", "problematic_uid", "issue_timestamp"],
                "ready_for_engineer_ticket": False,
                "pending_investigation_reason": "rag_post_check_insufficient",
                "clarification_rounds_used": 1,
                "last_updated_at": "2026-04-16T03:49:09.250383+00:00",
            },
            route_agent=lambda **_kwargs: self.fail("route agent should not run during deterministic second clarify"),
            route_executor=lambda **_kwargs: self.fail("route executor should not run during deterministic second clarify"),
            rag_executor=lambda **_kwargs: self.fail("rag agent should not run during deterministic second clarify"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run during deterministic second clarify"),
        )

        self.assertEqual(execution.result.workflow_action, "clarify_customer_for_intake")
        self.assertEqual(execution.result.client_intake_state["clarification_rounds_used"], 2)
        self.assertEqual(execution.result.client_intake_state["missing_information"], ["issue_timestamp"])
        self.assertIn("issue timestamp", execution.result.answer.lower())
        self.assertNotIn("timezone", execution.result.answer.lower())

    def test_two_round_investigation_intake_infers_prior_clarification_for_legacy_state(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="channel name: zilingtest, uid is 2, and the issue happened on april 3rd",
            ticket_id="TK-122-LEGACY",
            customer_id="C-001",
            ticket_subject="Black screen",
            ticket_context=[
                {"role": "customer", "content": "i got black screen issue"},
                {
                    "role": "assistant",
                    "content": (
                        "If the issue continues, please share channel name, problematic uid, and issue timestamp "
                        "so I can narrow down the Audio/Video Calling investigation."
                    ),
                },
            ],
            product="audio_video_calling",
            message_id="2026-04-14T02:11:08.752498+00:00",
            client_intake_state={
                "phase": "gather_customer_inputs",
                "product": "audio_video_calling",
                "issue_mode": "investigation",
                "known_information": {"issue_symptom": "black screen issue"},
                "missing_information": ["channel_name", "problematic_uid", "issue_timestamp"],
                "ready_for_engineer_ticket": False,
                "pending_investigation_reason": "rag_post_check_insufficient",
                "last_updated_at": "2026-04-14T02:08:33.337732+00:00",
            },
            route_agent=lambda **_kwargs: self.fail("route agent should not run while the legacy second clarify is inferred"),
            route_executor=lambda **_kwargs: self.fail("route executor should not run while the legacy second clarify is inferred"),
            rag_executor=lambda **_kwargs: self.fail("rag agent should not run while the legacy second clarify is inferred"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run while the legacy second clarify is inferred"),
        )

        self.assertEqual(execution.result.workflow_action, "clarify_customer_for_intake")
        self.assertEqual(execution.result.client_intake_state["clarification_rounds_used"], 2)
        self.assertIn("timezone", execution.result.answer.lower())

    def test_exhausted_investigation_clarification_budget_converts_third_clarify_to_open_engineer(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="still black screen",
            ticket_id="TK-122-REVIEW",
            customer_id="C-001",
            ticket_subject="Black screen",
            ticket_context=[
                {"role": "customer", "content": "i got black screen issue"},
                {
                    "role": "assistant",
                    "content": (
                        "If the issue continues, please share channel name, problematic uid, and issue timestamp "
                        "so I can narrow down the Audio/Video Calling investigation."
                    ),
                },
                {"role": "customer", "content": "channel name is zilingtest and uid is 2"},
            ],
            product="audio_video_calling",
            message_id="2026-04-14T02:15:00+00:00",
            client_intake_state={
                "phase": "gather_customer_inputs",
                "product": "audio_video_calling",
                "issue_mode": "investigation",
                "known_information": {
                    "issue_symptom": "black screen issue",
                    "channel_name": "zilingtest",
                    "problematic_uid": "2",
                },
                "missing_information": ["issue_timestamp"],
                "ready_for_engineer_ticket": False,
                "pending_investigation_reason": "rag_post_check_insufficient",
                "clarification_rounds_used": 2,
                "last_updated_at": "2026-04-14T02:11:08.752498+00:00",
            },
            route_agent=lambda **_kwargs: SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.92,
                reason="technical_troubleshooting_symptom",
                matched_signals=["black screen"],
                response_language="en",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
            ),
            route_executor=lambda **_kwargs: self.fail("route executor should not run when route=rag"),
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer="I couldn't find enough information in the available support knowledge base to answer that question.",
                confidence=0.38,
                sources=[],
                citations=[],
                needs_engineer_guidance=True,
                reason="rag_insufficient_evidence",
                evidence_summary={},
                packed_evidence={},
            ),
            review_agent=lambda **_kwargs: TroubleshootingIntakeResult(
                issue_mode="investigation",
                known_information={
                    "issue_symptom": "black screen issue",
                    "channel_name": "zilingtest",
                    "problematic_uid": "2",
                },
                missing_information=["issue_timestamp"],
                ready_for_engineer_ticket=False,
                customer_reply="Known so far: channel name is zilingtest; problematic uid is 2. Please share the issue time and timezone.",
                issue_timestamp_parts={},
            ),
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.result.route_reason, "investigation_intake_round_exhausted")
        self.assertEqual(execution.result.client_intake_state["phase"], "clarification_limit_reached")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "completed")

    def test_follow_up_after_second_investigation_clarify_opens_engineer_ticket_without_third_prompt(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="12pm utc+8",
            ticket_id="TK-123-TURN3",
            customer_id="C-001",
            ticket_subject="Black screen",
            ticket_context=[
                {"role": "customer", "content": "i got black screen issue"},
                {
                    "role": "assistant",
                    "content": (
                        "If the issue continues, please share channel name, problematic uid, and issue timestamp "
                        "so I can narrow down the Audio/Video Calling investigation."
                    ),
                },
                {"role": "customer", "content": "channel name: zilingtest, uid 2"},
                {
                    "role": "assistant",
                    "content": (
                        "Known so far: issue symptom is black screen issue; channel name is zilingtest; "
                        "problematic uid is 2. To investigate this Audio/Video Calling issue, please share "
                        "the issue timestamp."
                    ),
                    "workflow_action": "clarify_customer_for_intake",
                    "client_intake_missing_information": ["issue_timestamp"],
                },
            ],
            product="audio_video_calling",
            message_id="2026-04-16T03:52:10+00:00",
            client_intake_state={
                "phase": "gather_customer_inputs",
                "product": "audio_video_calling",
                "issue_mode": "investigation",
                "known_information": {
                    "issue_symptom": "black screen issue",
                    "channel_name": "zilingtest",
                    "problematic_uid": "2",
                },
                "missing_information": ["issue_timestamp"],
                "ready_for_engineer_ticket": False,
                "pending_investigation_reason": "rag_post_check_insufficient",
                "clarification_rounds_used": 2,
                "last_updated_at": "2026-04-16T03:51:47.709113+00:00",
            },
            route_agent=lambda **_kwargs: self.fail("route agent should not run once two investigation clarify rounds are exhausted"),
            route_executor=lambda **_kwargs: self.fail("route executor should not run once two investigation clarify rounds are exhausted"),
            rag_executor=lambda **_kwargs: self.fail("rag agent should not run once two investigation clarify rounds are exhausted"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run once two investigation clarify rounds are exhausted"),
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.result.route_reason, "investigation_intake_round_exhausted")
        self.assertEqual(execution.result.client_intake_state["clarification_rounds_used"], 2)
        self.assertEqual(execution.result.client_intake_state["phase"], "clarification_limit_reached")
        self.assertEqual(
            execution.result.client_intake_state["issue_timestamp_parts"],
            {"time": "12:00pm", "timezone": "UTC+8"},
        )
        self.assertEqual(execution.runtime_state.route_agent.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.rag_service.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")

    def test_follow_up_after_second_investigation_clarify_without_timestamp_opens_engineer_ticket(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="i dont have a timestamp",
            ticket_id="TK-136-TURN3",
            customer_id="C-001",
            requester="Zac",
            ticket_subject="Black screen",
            ticket_context=[
                {"role": "customer", "content": "i got black screen, what should i do?"},
                {
                    "role": "assistant",
                    "content": (
                        "If the issue continues, please share channel name, problematic uid, and issue timestamp "
                        "so I can narrow down the Audio/Video Calling investigation."
                    ),
                },
                {"role": "customer", "content": "channel name: zilingtes, uid 2"},
                {
                    "role": "assistant",
                    "content": (
                        "Hi Zac,\n\nThanks for sharing the additional info.\n\n"
                        "To help us investigate this Audio/Video Calling issue, could you also share issue timestamp?\n\n"
                        "Best Regards,\nSid"
                    ),
                    "workflow_action": "clarify_customer_for_intake",
                    "client_intake_missing_information": ["issue_timestamp"],
                },
            ],
            product="audio_video_calling",
            message_id="2026-04-17T03:51:45.928128+00:00",
            client_intake_state={
                "phase": "gather_customer_inputs",
                "product": "audio_video_calling",
                "issue_mode": "investigation",
                "known_information": {
                    "issue_symptom": "black screen issue",
                    "channel_name": "zilingtes",
                    "problematic_uid": "2",
                },
                "missing_information": ["issue_timestamp"],
                "ready_for_engineer_ticket": False,
                "pending_investigation_reason": "rag_post_check_insufficient",
                "clarification_rounds_used": 2,
                "last_updated_at": "2026-04-17T03:26:04.439828+00:00",
            },
            route_agent=lambda **_kwargs: self.fail(
                "route agent should not run once two investigation clarify rounds are exhausted"
            ),
            route_executor=lambda **_kwargs: self.fail(
                "route executor should not run once two investigation clarify rounds are exhausted"
            ),
            rag_executor=lambda **_kwargs: self.fail(
                "rag agent should not run once two investigation clarify rounds are exhausted"
            ),
            review_agent=lambda **_kwargs: self.fail(
                "review agent should not run once two investigation clarify rounds are exhausted"
            ),
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.result.route_reason, "investigation_intake_round_exhausted")
        self.assertEqual(execution.result.client_intake_state["clarification_rounds_used"], 2)
        self.assertEqual(execution.result.client_intake_state["phase"], "clarification_limit_reached")
        self.assertEqual(execution.runtime_state.route_agent.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.rag_service.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")

    def test_follow_up_with_channel_uid_and_unavailable_timestamp_opens_engineer_ticket(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="channel is zilingtest, uid is 2. i dont have a timestamp",
            ticket_id="TK-235-REGRESSION",
            customer_id="C-001",
            requester="Zac",
            ticket_subject="Black screen",
            ticket_context=[
                {"role": "customer", "content": "I got black screen, what should I do?"},
                {
                    "role": "assistant",
                    "content": (
                        "Hi Zac,\n\nThanks for the details.\n\n"
                        "To help us investigate this Audio/Video Calling issue, could you also share "
                        "channel name, problematic uid, and issue timestamp?\n\n"
                        "Best Regards,\nSid"
                    ),
                    "workflow_action": "clarify_customer_for_intake",
                    "client_intake_missing_information": [
                        "channel_name",
                        "problematic_uid",
                        "issue_timestamp",
                    ],
                },
            ],
            product="audio_video_calling",
            message_id="2026-06-29T02:53:53.530082+00:00",
            client_intake_state={
                "phase": "gather_customer_inputs",
                "product": "audio_video_calling",
                "issue_mode": "investigation",
                "known_information": {"issue_symptom": "black screen issue"},
                "missing_information": ["channel_name", "problematic_uid", "issue_timestamp"],
                "ready_for_engineer_ticket": False,
                "pending_investigation_reason": "rag_insufficient_evidence",
                "clarification_rounds_used": 1,
                "last_updated_at": "2026-06-29T02:52:42.727782+00:00",
            },
            route_agent=lambda **_kwargs: self.fail(
                "route agent should not run when unavailable timestamp exhausts intake"
            ),
            route_executor=lambda **_kwargs: self.fail(
                "route executor should not run when unavailable timestamp exhausts intake"
            ),
            rag_executor=lambda **_kwargs: self.fail(
                "rag agent should not run when unavailable timestamp exhausts intake"
            ),
            review_agent=lambda **_kwargs: self.fail(
                "review agent should not run when unavailable timestamp exhausts intake"
            ),
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.result.route_reason, "investigation_intake_round_exhausted")
        self.assertEqual(
            execution.result.client_intake_state["known_information"]["channel_name"],
            "zilingtest",
        )
        self.assertEqual(
            execution.result.client_intake_state["known_information"]["problematic_uid"],
            "2",
        )
        self.assertEqual(execution.result.client_intake_state["missing_information"], ["issue_timestamp"])
        self.assertNotIn("could you also share issue timestamp", execution.result.answer.lower())

    def test_grounded_answer_fallback_with_exhausted_investigation_budget_opens_engineer_ticket(self) -> None:
        from backend.services.client_ticket_agent_runtime import _build_cited_answer_execution_result

        resolution = SupportResolution(
            answer="Grounded answer that should not be sent as a third clarify.",
            confidence=0.93,
            sources=["https://docs.agora.io/en/video-calling/troubleshooting/black-screen"],
            citations=[
                {
                    "heading": "Black screen troubleshooting",
                    "chunk_id": "chunk-1",
                    "source_url": "https://docs.agora.io/en/video-calling/troubleshooting/black-screen",
                    "source_path": "official/black-screen.md",
                }
            ],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            route_reason="grounded_answer",
            route_confidence=0.92,
            search_used=False,
            matched_signals=["black screen"],
            evidence_summary={},
            packed_evidence={},
        )

        review_result = TroubleshootingIntakeResult(
            issue_mode="investigation",
            known_information={
                "issue_symptom": "black screen issue",
                "channel_name": "zilingtes",
                "problematic_uid": "2",
            },
            missing_information=["issue_timestamp"],
            ready_for_engineer_ticket=False,
            customer_reply=(
                "Hi Zac,\n\nThanks for sharing the additional info.\n\n"
                "To help us investigate this Audio/Video Calling issue, could you also share issue timestamp?\n\n"
                "Best Regards,\nSid"
            ),
            issue_timestamp_parts={},
        )

        result = _build_cited_answer_execution_result(
            review_result=review_result,
            resolution=resolution,
            message="i dont have a timestamp",
            product="audio_video_calling",
            investigation_reason="rag_post_check_insufficient",
            current_state={
                "phase": "gather_customer_inputs",
                "product": "audio_video_calling",
                "issue_mode": "investigation",
                "known_information": {
                    "issue_symptom": "black screen issue",
                    "channel_name": "zilingtes",
                    "problematic_uid": "2",
                },
                "missing_information": ["issue_timestamp"],
                "ready_for_engineer_ticket": False,
                "pending_investigation_reason": "rag_post_check_insufficient",
                "clarification_rounds_used": 2,
                "last_updated_at": "2026-04-17T03:26:04.439828+00:00",
            },
            requester="Zac",
            customer_id="C-001",
        )

        self.assertEqual(result.workflow_action, "open_engineer_ticket")
        self.assertEqual(result.route_reason, "investigation_intake_round_exhausted")
        self.assertEqual(result.client_intake_state["phase"], "clarification_limit_reached")
        self.assertTrue(result.needs_investigating)
        self.assertNotIn("could you also share issue timestamp", result.answer.lower())

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
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
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
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.result.investigation_reason, "rag_unavailable")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")
        self.assertEqual(execution.diagnostics.get("knowledge_index_status"), "configured_table_empty")
        self.assertEqual(execution.diagnostics.get("knowledge_index_reason"), "configured_table_empty")

    def test_troubleshooting_rag_unavailable_routes_into_intake_clarification(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        review_modes: list[str] = []
        trace_contexts: list[_FakeOpenAiReviewTrace] = []

        def _fake_start_review_trace(**kwargs: object) -> _FakeOpenAiReviewTrace:
            context = _FakeOpenAiReviewTrace(
                trace_id=f"trace-{kwargs['mode']}",
                group_id=str(kwargs["run_id"]),
                workflow_name=f"supportportal.review_agent.{kwargs['mode']}",
                mode=str(kwargs["mode"]),
            )
            trace_contexts.append(context)
            return context

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
                    "Thanks for the details. To help us investigate this Audio/Video Calling issue, "
                    "could you also share the channel name, problematic uid, and issue timestamp?"
                ),
            )

        with patch(
            "backend.services.client_ticket_agent_runtime.openai_agent_tracing.start_review_trace",
            side_effect=_fake_start_review_trace,
        ):
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
                rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
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
        self.assertTrue(execution.result.answer.startswith("Hi there,"))
        self.assertIn("Thanks for the details.", execution.result.answer)
        self.assertNotIn("known so far", execution.result.answer.lower())
        review_trace_state = execution.runtime_state.review_agent.get("openai_tracing") or {}
        self.assertEqual(review_trace_state.get("group_id"), execution.result.run_id)
        self.assertEqual(review_trace_state.get("latest_trace_id"), "trace-rag_insufficient_evidence")
        self.assertEqual(
            review_trace_state.get("traces"),
            [
                {
                    "mode": "rag_insufficient_evidence",
                    "trace_id": "trace-rag_insufficient_evidence",
                    "group_id": execution.result.run_id,
                    "workflow_name": "supportportal.review_agent.rag_insufficient_evidence",
                }
            ],
        )
        review_started = next(
            event
            for event in execution.agent_events
            if event.get("agent_name") == "review_agent" and event.get("event_type") == "started"
        )
        self.assertEqual(review_started["payload"]["openai_tracing"]["mode"], "rag_insufficient_evidence")
        review_completed = next(
            event
            for event in execution.agent_events
            if event.get("agent_name") == "review_agent" and event.get("event_type") == "completed"
        )
        self.assertEqual(review_completed["payload"]["openai_tracing"]["trace_id"], "trace-rag_insufficient_evidence")
        self.assertEqual(trace_contexts[0].function_calls[0]["name"], "review_agent.rag_insufficient_evidence")

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
                rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
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
            )

        self.assertEqual(execution.result.workflow_action, "clarify_customer_for_intake")
        self.assertEqual(execution.result.client_intake_state["issue_mode"], "investigation")
        self.assertEqual(
            execution.result.client_intake_state["missing_information"],
            ["channel_name", "problematic_uid", "issue_timestamp"],
        )

    def test_rag_processing_timeout_skips_review_for_non_troubleshooting_queries(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="How do I join channel?",
            ticket_id="TK-RAG-TIMEOUT-1",
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
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer="",
                confidence=0.0,
                sources=[],
                citations=[],
                needs_engineer_guidance=True,
                reason="rag_processing_timeout",
                evidence_summary={"diagnostics": {"rag_failure_kind": "timeout"}},
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: self.fail("review agent should not run for rag_processing_timeout"),
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.result.investigation_reason, "rag_processing_timeout")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")

    def test_rag_completed_with_insufficient_evidence_routes_into_review_agent_clarification(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        review_modes: list[str] = []

        def _review_agent(**kwargs: object) -> TroubleshootingIntakeResult:
            review_modes.append(str(kwargs.get("mode") or ""))
            rag_result = kwargs.get("rag_result")
            self.assertIsInstance(rag_result, dict)
            self.assertEqual(rag_result["reason"], "rag_completed_with_insufficient_evidence")
            return TroubleshootingIntakeResult(
                issue_mode="answer",
                known_information={"endpoint_hint": "updateLayout"},
                missing_information=["exact endpoint"],
                ready_for_engineer_ticket=False,
                customer_reply="Could you confirm the exact endpoint and whether this payload is for start or updateLayout?",
            )

        execution = execute_client_ticket_agent_runtime(
            message='Why does this request body fail? {"clientRequest":{"layoutConfig":[]}}',
            ticket_id="TK-RAG-COMPLETED-INSUFFICIENT-1",
            customer_id="C-001",
            ticket_subject="Cloud Recording request body",
            ticket_context=[
                {
                    "role": "customer",
                    "content": 'Why does this request body fail? {"clientRequest":{"layoutConfig":[]}}',
                }
            ],
            product="cloud_recording",
            message_id="2026-04-04T00:00:00+00:00",
            route_agent=lambda **_kwargs: SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.94,
                reason="technical_question",
                matched_signals=["request body"],
                response_language="en",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
            ),
            route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer="RAG completed but could not verify a customer-safe grounded answer from the available schema evidence.",
                confidence=0.42,
                sources=[],
                citations=[],
                needs_engineer_guidance=True,
                reason="rag_completed_with_insufficient_evidence",
                evidence_summary={
                    "quality_signals": {
                        "needs_human": True,
                        "handoff_reason": "insufficient_evidence",
                    },
                    "missing_evidence": ["exact layoutConfig schema not found"],
                },
                packed_evidence=None,
            ),
            review_agent=_review_agent,
        )

        self.assertEqual(review_modes, ["rag_insufficient_evidence"])
        self.assertEqual(execution.result.workflow_action, "clarify_customer_for_intake")
        self.assertEqual(
            execution.result.client_intake_state["pending_investigation_reason"],
            "rag_completed_with_insufficient_evidence",
        )
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "completed")
        self.assertNotEqual(execution.result.investigation_reason, "rag_processing_timeout")
        self.assertIn("confirm the exact endpoint", execution.result.answer.lower())

    def test_rag_completed_insufficient_review_cannot_send_non_missing_platform_clarification(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="How can I retrieve the recorded file URLs after Cloud Recording finishes?",
            ticket_id="TK-RAG-COMPLETED-INSUFFICIENT-PLATFORM-1",
            customer_id="C-001",
            ticket_subject="Cloud Recording file access",
            ticket_context=[
                {
                    "role": "customer",
                    "content": "How can I retrieve the recorded file URLs after Cloud Recording finishes?",
                }
            ],
            product="cloud_recording",
            message_id="2026-04-04T00:00:00+00:00",
            route_agent=lambda **_kwargs: SupportRouteDecision(
                scope_label="agora_technical",
                route="rag",
                confidence=0.94,
                reason="technical_question",
                matched_signals=["cloud recording"],
                response_language="en",
                route_family="agora_docs_rag",
                execution_action="rag",
                tooling_profile="agora_docs_only",
            ),
            route_executor=lambda **_kwargs: self.fail("route executor should not be used when route=rag"),
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer="RAG completed but could not verify a customer-safe grounded answer from the available evidence.",
                confidence=0.42,
                sources=[],
                citations=[],
                needs_engineer_guidance=True,
                reason="rag_completed_with_insufficient_evidence",
                evidence_summary={
                    "quality_signals": {
                        "needs_human": True,
                        "handoff_reason": "insufficient_evidence",
                    },
                },
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: TroubleshootingIntakeResult(
                issue_mode="answer",
                known_information={"desired_outcome": "retrieve Cloud Recording file URLs"},
                missing_information=[],
                ready_for_engineer_ticket=False,
                customer_reply="Which platform or SDK are you using?",
            ),
        )

        self.assertNotIn("platform", execution.result.answer.lower())
        self.assertNotIn("sdk", execution.result.answer.lower())

    def test_troubleshooting_rag_processing_timeout_routes_into_intake_clarification(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="i got black screen, what should i do?",
            ticket_id="TK-RAG-TIMEOUT-TRBL-1",
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
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer="",
                confidence=0.0,
                sources=[],
                citations=[],
                needs_engineer_guidance=True,
                reason="rag_processing_timeout",
                evidence_summary={"diagnostics": {"rag_failure_kind": "timeout"}},
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: TroubleshootingIntakeResult(
                issue_mode="investigation",
                known_information={"issue_symptom": "black screen issue"},
                missing_information=["channel_name", "problematic_uid", "issue_timestamp"],
                ready_for_engineer_ticket=False,
                customer_reply=(
                    "Thanks for the details. To help us investigate this Audio/Video Calling issue, "
                    "could you also share the channel name, problematic uid, and issue timestamp?"
                ),
            ),
        )

        self.assertEqual(execution.result.workflow_action, "clarify_customer_for_intake")
        self.assertEqual(
            execution.result.client_intake_state["pending_investigation_reason"],
            "rag_processing_timeout",
        )
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "completed")
        self.assertEqual(execution.runtime_state.review_agent.get("decision"), "clarify_customer_for_intake")
        self.assertTrue(execution.result.answer.startswith("Hi there,"))
        self.assertIn("Thanks for the details.", execution.result.answer)
        self.assertNotIn("known so far", execution.result.answer.lower())

    def test_troubleshooting_weak_evidence_review_approve_still_enters_intake(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        review_modes: list[str] = []

        def review_agent(**kwargs: object) -> object:
            mode = str(kwargs.get("mode") or "")
            review_modes.append(mode)
            if mode == "grounded_postcheck":
                return {"decision": "approve_answer", "reason": "postcheck_passed", "confidence": 0.86}
            if mode == "pre_engineer_intake":
                return TroubleshootingIntakeResult(
                    issue_mode="investigation",
                    known_information={"symptom": "token renewal fails"},
                    missing_information=[],
                    ready_for_engineer_ticket=True,
                    customer_reply="",
                )
            self.fail(f"unexpected review mode {mode!r}")

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
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer="Use onTokenPrivilegeWillExpire and renewToken before reconnect completes.",
                confidence=0.88,
                sources=["https://docs.agora.io/en/video-calling/token-authentication"],
                citations=[{"chunk_id": "chunk-1"}],
                needs_engineer_guidance=False,
                reason="grounded_answer",
                evidence_summary={"quality_signals": {"generation_mode": "structured_answer", "selected_doc_count": 1, "top1_similarity_score": 0.93}},
                packed_evidence=None,
            ),
            review_agent=review_agent,
        )

        self.assertEqual(review_modes, ["grounded_postcheck", "pre_engineer_intake"])
        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "completed")
        self.assertEqual(execution.runtime_state.review_agent.get("decision"), "open_engineer_ticket")
        self.assertEqual(execution.runtime_state.review_agent.get("gate_block_reason"), "weak_troubleshooting_evidence")

    def test_black_screen_runtime_uses_real_route_agent_fast_path_with_misleading_subject(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime
        from backend.services.support_router import decide_support_route

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("route llm should not run for black-screen symptom fast path"),
        ):
            execution = execute_client_ticket_agent_runtime(
                message="I got black screen, what should I do?",
                ticket_id="TK-176-RUNTIME",
                customer_id="C-001",
                ticket_subject="Black Screen After Startup",
                ticket_context=[
                    {"role": "customer", "content": "I got black screen, what should I do?"},
                ],
                product="audio_video_calling",
                message_id="2026-04-22T00:00:00+00:00",
                route_agent=decide_support_route,
                route_executor=lambda **_kwargs: self.fail("route executor should not run when route=rag"),
                rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                    answer="Check whether the remote user is publishing video and whether the local render view is bound correctly.",
                    confidence=0.9,
                    sources=["https://docs.agora.io/en/video-calling/troubleshooting/black-screen"],
                    citations=[{"chunk_id": "chunk-black-screen"}],
                    needs_engineer_guidance=False,
                    reason="grounded_answer",
                    evidence_summary={
                        "quality_signals": {
                            "generation_mode": "structured_answer",
                            "selected_doc_count": 1,
                            "top1_similarity_score": 0.93,
                        }
                    },
                    packed_evidence=None,
                ),
                review_agent=lambda **_kwargs: {
                    "decision": "approve_answer",
                    "reason": "postcheck_passed",
                    "confidence": 0.88,
                },
            )

        self.assertEqual(execution.result.answer_route, "rag")
        self.assertEqual(execution.result.route_reason, "grounded_answer")
        self.assertEqual(execution.result.workflow_action, "answer_customer")
        self.assertEqual(execution.runtime_state.route_agent.get("status"), "completed")
        self.assertEqual(execution.runtime_state.route_agent.get("decision"), "rag")
        self.assertEqual(execution.runtime_state.route_agent.get("reason"), "technical_troubleshooting_symptom")
        self.assertEqual(execution.runtime_state.rag_service.get("status"), "completed")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "completed")
        self.assertFalse(
            any(
                event.get("agent_name") == "rag_service" and event.get("event_type") == "cancel_requested"
                for event in execution.agent_events
            )
        )

    def test_runtime_state_uses_rag_service_phase_with_legacy_alias(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="How do I join a channel?",
            ticket_id="TK-PHASE-1",
            customer_id="C-001",
            ticket_subject="Join channel",
            ticket_context=[{"role": "customer", "content": "How do I join a channel?"}],
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
            rag_executor=lambda **_kwargs: RagTicketAnswerDetail(
                answer="Call joinChannel with the same channel name and token on each client.",
                confidence=0.91,
                sources=["https://docs.agora.io/en/video-calling/get-started"],
                citations=[{"chunk_id": "chunk-1"}],
                needs_engineer_guidance=False,
                reason="grounded_answer",
                evidence_summary={},
                packed_evidence=None,
            ),
            review_agent=lambda **_kwargs: self.fail("review should not run for low risk"),
        )

        route_payload = execution.result.route_payload()
        self.assertIn("rag_service_phase", route_payload)
        self.assertIn("rag_agent_phase", route_payload)
        self.assertEqual(route_payload["rag_service_phase"], "completed")
        self.assertEqual(route_payload["rag_agent_phase"], "completed")
        self.assertNotEqual(route_payload["rag_service_phase"], "")
        runtime_state_payload = execution.runtime_state.as_dict()
        self.assertIn("rag_service", runtime_state_payload)
        self.assertIn("rag_agent", runtime_state_payload)
        self.assertEqual(runtime_state_payload["rag_service"], runtime_state_payload["rag_agent"])

    def test_rag_canceler_contract_removed_runtime_no_longer_exposes_rag_canceler(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        execution = execute_client_ticket_agent_runtime(
            message="Who is Agora's CEO?",
            ticket_id="TK-CANCEL-CONTRACT-1",
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
            rag_executor=lambda **_kwargs: self.fail("rag executor should not run for non-rag route"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run for non-rag route"),
        )

        self.assertEqual(execution.result.execution_action, "web_search")
        self.assertNotIn("rag_canceler", inspect.signature(execute_client_ticket_agent_runtime).parameters)

    def test_runtime_signature_has_rag_executor_and_no_rag_agent(self) -> None:
        from backend.services.client_ticket_agent_runtime import execute_client_ticket_agent_runtime

        sig = inspect.signature(execute_client_ticket_agent_runtime)
        self.assertIn("rag_executor", sig.parameters)
        self.assertNotIn("rag_agent", sig.parameters)
        self.assertNotIn("rag_canceler", sig.parameters)


if __name__ == "__main__":
    unittest.main()
