from __future__ import annotations

from contextlib import contextmanager
import inspect
import os
import types
import unittest
from unittest.mock import patch

from backend.services.rag_service_client import RagTicketAnswerDetail
from backend.services.support_router import SupportResolution, SupportRouteDecision
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
            rag_agent=lambda **_kwargs: self.fail("rag agent should not run for resolved confirmation"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run for resolved confirmation"),
            rag_canceler=None,
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
        self.assertEqual(execution.runtime_state.rag_agent.get("status"), "cancelled")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")

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
            rag_agent=lambda **_kwargs: self.fail("rag agent should not run for resolved confirmation"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run for resolved confirmation"),
            rag_canceler=None,
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
            rag_agent=lambda **_kwargs: self.fail("rag agent should not run for resolved confirmation fallback"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run for resolved confirmation fallback"),
            rag_canceler=None,
        )

        self.assertEqual(execution.result.workflow_action, "resolve_ticket")
        self.assertEqual(execution.result.next_status, "resolved")
        self.assertEqual(execution.result.route_reason, "customer_confirmed_resolved")
        self.assertEqual(execution.runtime_state.route_agent.get("status"), "failed")
        self.assertEqual(execution.runtime_state.rag_agent.get("status"), "skipped")
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
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
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
            rag_canceler=None,
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
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
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
            rag_canceler=None,
        )

        self.assertTrue(route_called)
        self.assertNotEqual(execution.result.workflow_action, "resolve_ticket")
        self.assertEqual(execution.result.workflow_action, "clarify_customer_for_intake")

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
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
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
            rag_canceler=None,
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
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
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
            rag_canceler=None,
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
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
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
            rag_canceler=None,
        )

        self.assertEqual(execution.result.workflow_action, "answer_customer")
        self.assertFalse(execution.result.needs_investigating)
        self.assertIn("join-channel method", execution.result.answer)
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
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
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
            rag_canceler=None,
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
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
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
            rag_canceler=None,
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
        self.assertTrue(execution.result.answer.startswith("Hi there,"))
        self.assertIn("Thanks for the details.", execution.result.answer)
        self.assertIn("what you're trying to achieve", execution.result.answer.lower())
        self.assertIn("the exact error or blocker you're seeing", execution.result.answer.lower())
        self.assertNotIn("grounded answer", execution.result.answer.lower())
        self.assertEqual(execution.runtime_state.review_agent.get("decision"), "clarify_customer_for_intake")

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
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
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
            rag_canceler=None,
        )

        self.assertEqual(execution.result.workflow_action, "clarify_customer_for_intake")
        self.assertTrue(execution.result.answer.startswith("Hi there,"))
        self.assertIn("Thanks for the details.", execution.result.answer)
        self.assertIn("what you're trying to achieve", execution.result.answer.lower())
        self.assertIn("the exact error or blocker you're seeing", execution.result.answer.lower())
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

        self.assertEqual(execution.result.workflow_action, "answer_customer")
        self.assertEqual(execution.result.investigation_reason, "rag_post_check_insufficient")
        self.assertEqual(execution.result.route_reason, "grounded_answer")
        self.assertTrue(execution.result.citations)
        self.assertIn("please share what you're trying to achieve", execution.result.answer.lower())
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "completed")
        self.assertEqual(execution.runtime_state.review_agent.get("decision"), "answer_customer")

    def test_troubleshooting_postcheck_rejection_preserves_cited_answer_with_follow_up(self) -> None:
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
        self.assertEqual(execution.result.workflow_action, "answer_customer")
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
        self.assertEqual(execution.result.route_reason, "grounded_answer")
        self.assertEqual(execution.result.citations, [{"chunk_id": "chunk-black-screen"}])
        self.assertIn("Check whether the remote user is publishing video", execution.result.answer)
        self.assertIn("If the issue continues, please share", execution.result.answer)
        self.assertEqual(execution.runtime_state.review_agent.get("decision"), "answer_customer")
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
                rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
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
                rag_canceler=None,
            )

        self.assertEqual(review_modes, ["grounded_postcheck"])
        self.assertEqual(execution.result.workflow_action, "answer_customer")
        self.assertEqual(execution.result.route_reason, "grounded_answer")
        self.assertEqual(execution.result.citations, [{"chunk_id": "chunk-dual-stream"}])
        self.assertEqual(execution.result.client_intake_state["issue_mode"], "answer")
        self.assertEqual(
            execution.result.client_intake_state["missing_information"],
            ["desired_outcome", "blocked_step_or_error"],
        )
        self.assertIn("Call `client.enableDualStream()`", execution.result.answer)
        self.assertIn("please share what you're trying to achieve", execution.result.answer.lower())
        self.assertEqual(execution.runtime_state.review_agent.get("decision"), "answer_customer")
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
            rag_agent=lambda **_kwargs: self.fail("rag agent should not run once investigation intake is complete"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run once investigation intake is complete"),
            rag_canceler=None,
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
        self.assertEqual(execution.runtime_state.rag_agent.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.route_agent.get("reason"), "investigation_intake_complete")
        self.assertEqual(execution.runtime_state.rag_agent.get("reason"), "investigation_intake_complete")
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
            rag_agent=lambda **_kwargs: self.fail("rag agent should not run during deterministic second clarify"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run during deterministic second clarify"),
            rag_canceler=None,
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
        self.assertEqual(execution.runtime_state.rag_agent.get("status"), "skipped")
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
            rag_agent=lambda **_kwargs: self.fail("rag agent should not run during deterministic second clarify"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run during deterministic second clarify"),
            rag_canceler=None,
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
            rag_agent=lambda **_kwargs: self.fail("rag agent should not run while the legacy second clarify is inferred"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run while the legacy second clarify is inferred"),
            rag_canceler=None,
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
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
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
            rag_canceler=None,
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
            rag_agent=lambda **_kwargs: self.fail("rag agent should not run once two investigation clarify rounds are exhausted"),
            review_agent=lambda **_kwargs: self.fail("review agent should not run once two investigation clarify rounds are exhausted"),
            rag_canceler=None,
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
        self.assertEqual(execution.runtime_state.rag_agent.get("status"), "skipped")
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
            rag_agent=lambda **_kwargs: self.fail(
                "rag agent should not run once two investigation clarify rounds are exhausted"
            ),
            review_agent=lambda **_kwargs: self.fail(
                "review agent should not run once two investigation clarify rounds are exhausted"
            ),
            rag_canceler=None,
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.result.route_reason, "investigation_intake_round_exhausted")
        self.assertEqual(execution.result.client_intake_state["clarification_rounds_used"], 2)
        self.assertEqual(execution.result.client_intake_state["phase"], "clarification_limit_reached")
        self.assertEqual(execution.runtime_state.route_agent.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.rag_agent.get("status"), "skipped")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")

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
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
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
            rag_canceler=None,
        )

        self.assertEqual(execution.result.workflow_action, "open_engineer_ticket")
        self.assertEqual(execution.result.investigation_reason, "rag_processing_timeout")
        self.assertEqual(execution.runtime_state.review_agent.get("status"), "skipped")

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
            rag_agent=lambda **_kwargs: RagTicketAnswerDetail(
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
            rag_canceler=None,
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
