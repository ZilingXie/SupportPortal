from __future__ import annotations

import copy
import importlib.util
import os
from pathlib import Path
import sys
import time
import types
import unittest
from unittest.mock import Mock, patch

if importlib.util.find_spec("psycopg") is None:
    raise unittest.SkipTest("psycopg is not installed in the local test environment")

import psycopg

from backend.services.rag_qa import INSUFFICIENT_EVIDENCE_REPLY

if importlib.util.find_spec("redis") is None:
    redis_module = types.ModuleType("redis")
    redis_asyncio_module = types.ModuleType("redis.asyncio")

    class _FakeRedis:
        @classmethod
        def from_url(cls, *_args: object, **_kwargs: object) -> "_FakeRedis":
            return cls()

        def publish(self, *_args: object, **_kwargs: object) -> int:
            return 1

        def blpop(self, *_args: object, **_kwargs: object) -> None:
            return None

        def rpush(self, *_args: object, **_kwargs: object) -> int:
            return 1

        def close(self) -> None:
            return None

        async def aclose(self) -> None:
            return None

    redis_module.Redis = _FakeRedis
    redis_asyncio_module.Redis = _FakeRedis
    sys.modules["redis"] = redis_module
    sys.modules["redis.asyncio"] = redis_asyncio_module


def _load_worker_module():
    module_path = Path(__file__).resolve().parents[1] / "worker.py"
    spec = importlib.util.spec_from_file_location(
        "backend.tests._worker_under_test",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load backend.worker for tests")

    fake_main = types.ModuleType("backend.main")
    fake_main.build_answer = lambda *_args, **_kwargs: ("", 0.0, [], [], False)
    fake_main.build_query_task = lambda ticket_id, customer_message, message_created_at, **kwargs: {
        "task_type": "ticket_query",
        "ticket_id": ticket_id,
        "customer_message": customer_message,
        "message_created_at": message_created_at,
        **kwargs,
    }
    fake_main.resolve_support_message = lambda *_args, **_kwargs: None
    fake_main.build_client_sync_event = lambda *_args, **_kwargs: {}
    fake_main.build_engineer_followup_request = lambda *_args, **_kwargs: "follow up"
    fake_main.ensure_ticket_defaults = lambda _ticket: None
    fake_main.now_iso = lambda: "2026-03-22T00:00:00+00:00"
    fake_main._run_client_ticket_review_agent = lambda *_args, **_kwargs: None
    fake_main._record_ticket_agent_runtime_events = lambda *_args, **_kwargs: None
    fake_main.ticket_repository = Mock()

    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"backend.main": fake_main}):
        spec.loader.exec_module(module)
    return module


worker = _load_worker_module()


def _route_decision(*, action: str, scope_label: str, reason: str) -> types.SimpleNamespace:
    route_family = "agora_docs_rag" if action == "rag" else "web_company_info" if action == "web_search" else "fallback_or_refuse"
    tooling_profile = "agora_docs_only" if action == "rag" else "official_web_search" if action == "web_search" else "no_agora_docs_refusal"
    return types.SimpleNamespace(
        scope_label=scope_label,
        route=action,
        confidence=0.93,
        reason=reason,
        matched_signals=["token"] if action == "rag" else ["agora"],
        response_language="en",
        route_family=route_family,
        execution_action=action,
        tooling_profile=tooling_profile,
    )


def _build_ticket(
    *,
    ticket_id: str = "T-RETRY",
    customer_message: str = "Need help with token generation",
    message_created_at: str = "2026-03-22T00:00:00+00:00",
    client_intake_state: dict[str, object] | None = None,
    product_selection_state: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "ticket_id": ticket_id,
        "customer_id": "C-123",
        "requester": "Customer",
        "subject": "Token question",
        "status": "communicating",
        "created_at": "2026-03-22T00:00:00+00:00",
        "updated_at": "2026-03-22T00:00:00+00:00",
        "messages": [
            {
                "role": "customer",
                "content": customer_message,
                "created_at": message_created_at,
            },
            {
                "role": "assistant",
                "content": "I am checking the knowledge base for you now.",
                "created_at": "2026-03-22T00:00:01+00:00",
            },
        ],
        "client_intake_state": client_intake_state,
        "product_selection_state": product_selection_state,
    }


class WorkerResilienceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = {
            "task_type": "ticket_query",
            "ticket_id": "T-RETRY",
            "customer_message": "Need help with token generation",
            "message_created_at": "2026-03-22T00:00:00+00:00",
            "created_at": "2026-03-22T00:00:01+00:00",
        }

    def test_worker_rag_executor_uses_extended_timeout_and_recovery_window(self) -> None:
        detail = worker.RagTicketAnswerDetail(
            answer="Use joinChannel with a token.",
            confidence=0.92,
            sources=["https://docs.agora.io/en/video-calling/get-started"],
            citations=[{"chunk_id": "chunk-1"}],
            needs_engineer_guidance=False,
            reason="grounded_answer",
            evidence_summary=None,
            packed_evidence=None,
        )

        with patch.dict(
            os.environ,
            {
                "TICKET_WORKER_RAG_SERVICE_TIMEOUT_SECONDS": "90",
                "TICKET_WORKER_RAG_MAX_WAIT_SECONDS": "300",
                "TICKET_WORKER_RAG_RECOVERY_POLL_INTERVAL_SECONDS": "2",
            },
            clear=False,
        ), patch.object(
            worker.rag_service_client,
            "query_answer_with_recovery_detail",
            return_value=detail,
        ) as rag_mock:
            result = worker._worker_rag_with_cancel_guard(
                request_id="rag-worker-timeout-1",
                message="how to join channel",
                ticket_id="T-WORKER-1",
                customer_id="C-123",
                ticket_context=[{"role": "customer", "content": "how to join channel"}],
                product="audio_video_calling",
            )

        self.assertEqual(result.answer, "Use joinChannel with a token.")
        rag_mock.assert_called_once_with(
            question="how to join channel",
            request_id="rag-worker-timeout-1",
            ticket_id="T-WORKER-1",
            customer_id="C-123",
            requester=None,
            ticket_context=[{"role": "customer", "content": "how to join channel"}],
            product="audio_video_calling",
            insufficient_reply=INSUFFICIENT_EVIDENCE_REPLY,
            timeout_seconds=90.0,
            recovery_window_seconds=210.0,
            recovery_poll_interval_seconds=2.0,
            query_policy="client_accuracy_first",
        )

    def test_worker_rag_executor_timeout_with_healthy_service_returns_processing_timeout(self) -> None:
        with patch.object(
            worker.rag_service_client,
            "query_answer_with_recovery_detail",
            side_effect=worker.RagServiceError(
                "RAG service request failed",
                failure_kind="timeout",
            ),
        ) as rag_mock, patch.object(
            worker.rag_service_client,
            "health",
            return_value={"status": "ok", "service": "rag-api"},
        ) as health_mock:
            result = worker._worker_rag_with_cancel_guard(
                request_id="rag-worker-timeout-2",
                message="how to join channel",
                ticket_id="T-WORKER-2",
                customer_id="C-123",
                ticket_context=[{"role": "customer", "content": "how to join channel"}],
                product="audio_video_calling",
            )

        self.assertEqual(result.reason, "rag_processing_timeout")
        self.assertTrue(result.needs_engineer_guidance)
        self.assertIsInstance(result.evidence_summary, dict)
        self.assertEqual(result.evidence_summary["diagnostics"]["rag_failure_kind"], "timeout")
        self.assertEqual(result.evidence_summary["diagnostics"]["rag_timeout_health_check_status"], "ok")
        rag_mock.assert_called_once()
        health_mock.assert_called_once()

    def test_worker_rag_executor_preserves_recovered_insufficient_evidence_reason(self) -> None:
        recovered = worker.RagTicketAnswerDetail(
            answer="RAG completed but could not verify a customer-safe grounded answer from the available schema evidence.",
            confidence=0.41,
            sources=[],
            citations=[],
            needs_engineer_guidance=True,
            reason="rag_completed_with_insufficient_evidence",
            evidence_summary={"diagnostics": {"rag_recovered_from_live_detail": True}},
            packed_evidence=None,
        )
        with patch.object(
            worker.rag_service_client,
            "query_answer_with_recovery_detail",
            return_value=recovered,
        ) as rag_mock, patch.object(
            worker.rag_service_client,
            "health",
        ) as health_mock:
            result = worker._worker_rag_with_cancel_guard(
                request_id="rag-worker-insufficient-1",
                message="Can you check this request body {\"clientRequest\":{\"layoutConfig\":[]}}?",
                ticket_id="T-WORKER-INSUFFICIENT",
                customer_id="C-123",
                ticket_context=[{"role": "customer", "content": "request body question"}],
                product="cloud_recording",
            )

        self.assertEqual(result.reason, "rag_completed_with_insufficient_evidence")
        self.assertTrue(result.needs_engineer_guidance)
        self.assertEqual(
            result.evidence_summary["diagnostics"]["rag_recovered_from_live_detail"],
            True,
        )
        self.assertNotEqual(result.reason, "rag_processing_timeout")
        rag_mock.assert_called_once()
        health_mock.assert_not_called()

    def test_worker_rag_executor_transport_failure_with_unhealthy_service_stays_unavailable(self) -> None:
        with patch.object(
            worker.rag_service_client,
            "query_answer_with_recovery_detail",
            side_effect=worker.RagServiceError(
                "RAG service request failed",
                failure_kind="transport",
            ),
        ) as rag_mock, patch.object(
            worker.rag_service_client,
            "health",
        ) as health_mock:
            result = worker._worker_rag_with_cancel_guard(
                request_id="rag-worker-timeout-3",
                message="how to join channel",
                ticket_id="T-WORKER-3",
                customer_id="C-123",
                ticket_context=[{"role": "customer", "content": "how to join channel"}],
                product="audio_video_calling",
            )

        self.assertEqual(result.reason, "rag_unavailable")
        self.assertTrue(result.needs_engineer_guidance)
        rag_mock.assert_called_once()
        health_mock.assert_not_called()

    def test_execute_parallel_ticket_query_skips_rag_when_route_is_non_rag(self) -> None:
        rag_detail = types.SimpleNamespace(
            answer="Use joinChannel with a valid token.",
            confidence=0.91,
            sources=["https://docs.agora.io/en/video-calling/get-started"],
            citations=[{"chunk_id": "chunk-1"}],
            needs_engineer_guidance=False,
            reason="grounded_answer",
            evidence_summary={},
            packed_evidence=None,
        )
        execution = types.SimpleNamespace(
            answer="Agora's latest investor information is on the official site.",
            confidence=0.82,
            sources=["https://investor.agora.io"],
            citations=[],
            needs_engineer_guidance=False,
            needs_investigating=False,
            next_status="communicating",
            answer_route="web_search",
            scope_label="agora_non_technical",
            route_reason="company_info",
            route_confidence=0.88,
            search_used=True,
            matched_signals=["ceo"],
            route_family="web_company_info",
            execution_action="web_search",
            tooling_profile="official_web_search",
            evidence_summary=None,
            packed_evidence=None,
        )

        def _slow_rag(*_args, **_kwargs):
            time.sleep(0.05)
            return rag_detail

        with patch.object(
            worker,
            "decide_support_route",
            return_value=_route_decision(
                action="web_search",
                scope_label="agora_non_technical",
                reason="company_info",
            ),
        ), patch.object(
            worker,
            "_worker_rag_with_cancel_guard",
            side_effect=_slow_rag,
        ) as rag_mock, patch.object(
            worker,
            "resolve_support_message",
            return_value=execution,
        ) as resolve_mock:
            result, diagnostics = worker._execute_parallel_ticket_query(
                "Who is Agora's CEO?",
                ticket_id="T-WEB-1",
                customer_id="C-123",
                ticket_subject="Investor question",
                ticket_context=[{"role": "customer", "content": "Who is Agora's CEO?"}],
                message_created_at="2026-03-22T00:00:00+00:00",
            )

        self.assertEqual(result.execution_action, "web_search")
        self.assertFalse(diagnostics["rag_cancelled"])
        self.assertIsNone(diagnostics["rag_cancel_stage"])
        self.assertEqual(diagnostics["route_final_action"], "web_search")
        self.assertEqual(diagnostics["route_result_source"], "route_first")
        self.assertEqual(result.workflow_action, "answer_customer")
        rag_mock.assert_not_called()
        self.assertEqual(
            resolve_mock.call_args.kwargs["decision"].execution_action,
            "web_search",
        )

    def test_execute_parallel_ticket_query_fails_open_to_rag_when_route_raises(self) -> None:
        rag_detail = types.SimpleNamespace(
            answer="Use joinChannel with the same channel name and token.",
            confidence=0.91,
            sources=["https://docs.agora.io/en/video-calling/get-started"],
            citations=[{"chunk_id": "chunk-1"}],
            needs_engineer_guidance=False,
            reason="grounded_answer",
            evidence_summary={},
            packed_evidence=None,
        )
        execution = types.SimpleNamespace(
            answer="Use joinChannel with the same channel name and token.",
            confidence=0.91,
            sources=["https://docs.agora.io/en/video-calling/get-started"],
            citations=[{"chunk_id": "chunk-1"}],
            needs_investigating=False,
            next_status="communicating",
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="grounded_answer",
            route_confidence=0.0,
            search_used=False,
            matched_signals=["optimistic_default"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            evidence_summary=None,
            packed_evidence=None,
        )

        with patch.object(
            worker,
            "decide_support_route",
            side_effect=RuntimeError("route timeout"),
        ), patch.object(
            worker,
            "_worker_rag_with_cancel_guard",
            return_value=rag_detail,
        ):
            result, diagnostics = worker._execute_parallel_ticket_query(
                "how to join channel",
                ticket_id="T-RAG-1",
                customer_id="C-123",
                ticket_subject="Join question",
                ticket_context=[{"role": "customer", "content": "how to join channel"}],
                message_created_at="2026-03-22T00:00:00+00:00",
            )

        self.assertEqual(result.execution_action, "rag")
        self.assertFalse(diagnostics["rag_cancelled"])
        self.assertEqual(diagnostics["route_final_action"], "rag")
        self.assertEqual(diagnostics["route_result_source"], "route_fail_open")
        self.assertTrue(diagnostics["route_fail_open"])
        self.assertEqual(diagnostics["route_timeout_seconds"], 8.0)
        self.assertGreaterEqual(float(diagnostics["route_latency_ms"]), 0.0)
        self.assertTrue(str(diagnostics["rag_started_at"] or "").strip())
        self.assertTrue(str(diagnostics["rag_finished_at"] or "").strip())
        self.assertEqual(result.workflow_action, "answer_customer")
        self.assertEqual(result.answer, "Use joinChannel with the same channel name and token.")

    def test_process_ticket_query_forwards_ticket_product_to_orchestrator(self) -> None:
        ticket = _build_ticket()
        ticket["product"] = "cloud_recording"
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(ticket),
            copy.deepcopy(ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use the Cloud Recording REST API start endpoint.",
            confidence=0.91,
            sources=["official/cloud-recording-start.md"],
            citations=[{"source": "official/cloud-recording-start.md", "label": "Start recording"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="docs_match",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["cloud recording"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ) as orchestrate_mock, patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:00+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task, product="cloud_recording"))

        self.assertEqual(orchestrate_mock.call_args.kwargs["product"], "cloud_recording")

    def test_process_ticket_query_starts_main_agent_from_task_snapshot_before_ticket_refresh(self) -> None:
        repository = Mock()
        repository.get_ticket.return_value = _build_ticket(
            ticket_id="T-SNAPSHOT",
            customer_message="how to join channel",
            message_created_at="2026-03-22T00:00:00+00:00",
        )
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use joinChannel with the same channel name and token.",
            confidence=0.91,
            sources=["https://docs.agora.io/en/video-calling/get-started"],
            citations=[{"chunk_id": "chunk-1"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="grounded_answer",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["join channel"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
            workflow_action="answer_customer",
            client_intake_state={"phase": "gather_customer_inputs"},
            evidence_summary=None,
            run_id="run-snapshot",
            client_agent_runtime_state={"status": "completed"},
        )

        def _orchestrate_side_effect(*args, **kwargs):
            self.assertEqual(repository.get_ticket.call_count, 0)
            self.assertEqual(kwargs["customer_id"], "C-123")
            self.assertEqual(kwargs["ticket_subject"], "Join question")
            self.assertEqual(kwargs["product"], "audio_video_calling")
            self.assertEqual(
                kwargs["ticket_context"],
                [
                    {"role": "customer", "content": "how to join channel"},
                    {"role": "assistant", "content": "I am checking the knowledge base for you now."},
                ],
            )
            self.assertEqual(kwargs["client_intake_state"], {"phase": "gather_customer_inputs"})
            self.assertEqual(
                kwargs["latest_assistant_message"],
                {
                    "role": "assistant",
                    "content": "Use joinChannel with the same channel name and token.",
                    "workflow_action": "answer_customer",
                    "answer_route": "rag",
                    "route_reason": "grounded_answer",
                },
            )
            self.assertEqual(kwargs["current_ticket_status"], "communicating")
            return execution

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            side_effect=_orchestrate_side_effect,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:00:01+00:00",
        ):
            worker._process_ticket_query(
                bus,
                {
                    "task_type": "ticket_query",
                    "ticket_id": "T-SNAPSHOT",
                    "customer_message": "how to join channel",
                    "message_created_at": "2026-03-22T00:00:00+00:00",
                    "created_at": "2026-03-22T00:00:00.100000+00:00",
                    "customer_id": "C-123",
                    "ticket_subject": "Join question",
                    "product": "audio_video_calling",
                    "route_context_tail": [
                        {"role": "customer", "content": "how to join channel"},
                        {"role": "assistant", "content": "I am checking the knowledge base for you now."},
                    ],
                    "client_intake_state": {"phase": "gather_customer_inputs"},
                    "latest_assistant_message": {
                        "role": "assistant",
                        "content": "Use joinChannel with the same channel name and token.",
                        "workflow_action": "answer_customer",
                        "answer_route": "rag",
                        "route_reason": "grounded_answer",
                    },
                    "current_ticket_status": "communicating",
                    "ticket_updated_at": "2026-03-22T00:00:00+00:00",
                },
            )

        self.assertEqual(repository.get_ticket.call_count, 1)

    def test_process_ticket_query_clarifies_customer_and_keeps_ticket_communicating(self) -> None:
        ticket = _build_ticket(
            ticket_id="T-INTAKE",
            customer_message="I got black screen issue.",
        )
        ticket["product"] = "audio_video_calling"
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(ticket),
            copy.deepcopy(ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        repository.save_engineer_case.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer=(
                "Known so far: the issue symptom is black screen. "
                "To investigate this Audio/Video Calling issue, please share the channel name, "
                "problematic uid, and issue timestamp."
            ),
            confidence=0.0,
            sources=[],
            citations=[],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="rag_insufficient_evidence",
            route_confidence=0.87,
            search_used=False,
            matched_signals=["black screen"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
            workflow_action="clarify_customer_for_intake",
            client_intake_state={
                "phase": "gather_customer_inputs",
                "product": "audio_video_calling",
                "issue_mode": "investigation",
                "known_information": {"issue_symptom": "black screen"},
                "missing_information": ["channel_name", "problematic_uid", "issue_timestamp"],
                "ready_for_engineer_ticket": False,
                "last_updated_at": "2026-04-04T10:00:00Z",
            },
            evidence_summary=None,
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "_record_ticket_agent_runtime_events",
            side_effect=lambda execution_arg: [
                repository.record_ticket_agent_event(
                    str(item.get("ticket_id") or ""),
                    str(item.get("message_id") or "").strip() or None,
                    str(item.get("run_id") or ""),
                    str(item.get("agent_name") or ""),
                    str(item.get("phase") or ""),
                    str(item.get("event_type") or ""),
                    dict(item.get("payload") or {}) if isinstance(item.get("payload"), dict) else {},
                )
                for item in getattr(execution_arg, "client_agent_runtime_events", [])
                if isinstance(item, dict)
            ],
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:00+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task, ticket_id="T-INTAKE", customer_message="I got black screen issue."))

        saved_ticket = repository.save_ticket.call_args_list[0].args[0]
        self.assertEqual(saved_ticket["status"], "communicating")
        self.assertEqual(saved_ticket["client_intake_state"]["phase"], "gather_customer_inputs")
        self.assertEqual(
            saved_ticket["messages"][-1]["content"],
            "Known so far: the issue symptom is black screen. To investigate this Audio/Video Calling issue, please share the channel name, problematic uid, and issue timestamp.",
        )
        self.assertFalse(repository.save_engineer_case.called)
        event_payload = repository.record_event.call_args_list[0].args[2]
        self.assertEqual(event_payload["workflow_action"], "clarify_customer_for_intake")

    def test_process_ticket_query_persists_client_agent_runtime_state_and_events(self) -> None:
        ticket = _build_ticket(ticket_id="T-RUNTIME", customer_message="how to join channel")
        ticket["product"] = "audio_video_calling"
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(ticket),
            copy.deepcopy(ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        repository.record_ticket_agent_event.return_value = None
        repository.save_engineer_case.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use joinChannel with the same channel name and token.",
            confidence=0.91,
            sources=["https://docs.agora.io/en/video-calling/get-started"],
            citations=[{"chunk_id": "chunk-1"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="grounded_answer",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["join channel"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
            workflow_action="answer_customer",
            client_intake_state=None,
            evidence_summary=None,
            run_id="run-123",
            client_agent_runtime_state={
                "runtime_version": "client_ticket_agents_v1",
                "active_run_id": "run-123",
                "status": "completed",
                "main_agent": {"phase": "completed", "status": "completed"},
                "route_agent": {"phase": "completed", "status": "completed", "decision": "rag"},
                "rag_agent": {"phase": "completed", "status": "completed", "decision": "grounded_answer"},
                "review_agent": {"phase": "skipped", "status": "skipped"},
            },
            client_agent_runtime_events=[
                {
                    "ticket_id": "T-RUNTIME",
                    "message_id": "2026-03-22T00:00:00+00:00",
                    "run_id": "run-123",
                    "agent_name": "main_agent",
                    "phase": "completed",
                    "event_type": "workflow_decided",
                    "payload": {"workflow_action": "answer_customer"},
                    "created_at": "2026-03-22T00:00:01+00:00",
                }
            ],
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "get_app_build_info",
            return_value={"ref": "execution-build-456", "built_at": "2026-03-22T00:01:00Z"},
        ), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "_record_ticket_agent_runtime_events",
            side_effect=lambda execution_arg: [
                repository.record_ticket_agent_event(
                    str(item.get("ticket_id") or ""),
                    str(item.get("message_id") or "").strip() or None,
                    str(item.get("run_id") or ""),
                    str(item.get("agent_name") or ""),
                    str(item.get("phase") or ""),
                    str(item.get("event_type") or ""),
                    dict(item.get("payload") or {}) if isinstance(item.get("payload"), dict) else {},
                )
                for item in getattr(execution_arg, "client_agent_runtime_events", [])
                if isinstance(item, dict)
            ],
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:00+00:00",
        ):
            worker._process_ticket_query(
                bus,
                dict(
                    self.task,
                    ticket_id="T-RUNTIME",
                    customer_message="how to join channel",
                    app_build_ref="admission-build-123",
                ),
            )

        saved_ticket = repository.save_ticket.call_args_list[0].args[0]
        self.assertEqual(saved_ticket["client_agent_runtime_state"]["active_run_id"], "run-123")
        self.assertEqual(
            saved_ticket["client_agent_runtime_state"]["build_provenance"],
            {
                "task_app_build_ref": "admission-build-123",
                "execution_app_build_ref": "execution-build-456",
            },
        )
        assistant_message = saved_ticket["messages"][-1]
        self.assertEqual(assistant_message["client_agent_run_id"], "run-123")
        self.assertEqual(assistant_message["client_agent_runtime_status"], "completed")
        repository.record_ticket_agent_event.assert_called_once()
        agent_event_args = repository.record_ticket_agent_event.call_args.args
        self.assertEqual(agent_event_args[2], "run-123")
        self.assertEqual(agent_event_args[3], "main_agent")

        response_ready_payload = repository.record_event.call_args_list[0].args[2]
        self.assertIn("message_to_task_dequeued_ms", response_ready_payload)
        self.assertIn("dequeued_to_main_agent_started_ms", response_ready_payload)
        self.assertIn("main_agent_total_ms", response_ready_payload)
        self.assertIn("main_agent_to_answer_saved_ms", response_ready_payload)
        self.assertIn("answer_saved_to_response_ready_ms", response_ready_payload)
        self.assertEqual(response_ready_payload["task_app_build_ref"], "admission-build-123")
        self.assertEqual(response_ready_payload["execution_app_build_ref"], "execution-build-456")
        record_event_index = next(
            index
            for index, call in enumerate(repository.mock_calls)
            if call[0] == "record_event" and call.args[:2] == ("T-RUNTIME", "ticket_ai_response_ready")
        )
        runtime_event_index = next(
            index
            for index, call in enumerate(repository.mock_calls)
            if call[0] == "record_ticket_agent_event"
        )
        self.assertLess(
            record_event_index,
            runtime_event_index,
        )

    def test_process_ticket_query_persists_message_level_retrieval_plan_snapshot(self) -> None:
        ticket = _build_ticket(
            ticket_id="T-RAG-SNAPSHOT",
            customer_message="how to join channel",
            message_created_at="2026-03-22T00:00:00+00:00",
        )
        ticket["product"] = "audio_video_calling"
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(ticket),
            copy.deepcopy(ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        repository.record_ticket_agent_event.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use joinChannel with a valid token and channel name.",
            confidence=0.94,
            sources=["https://docs.agora.io/en/video-calling/get-started"],
            citations=[{"chunk_id": "chunk-1", "source_path": "official/get-started-sdk_android.md"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="grounded_answer",
            route_confidence=0.94,
            search_used=False,
            matched_signals=["join channel"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
            workflow_action="answer_customer",
            client_intake_state=None,
            evidence_summary={
                "diagnostics": {
                    "retrieval_plan_snapshot": {
                        "request_id": "rag-snapshot-1",
                        "query_class": "how_to_faq",
                        "retrieval_strategy": "agentic_multi_tool_v1",
                        "light_path_used": False,
                        "evidence_goal": "how_to_usage_support",
                        "recovery_bias": "semantic",
                        "first_pass_tools": ["p_vec", "s_vec"],
                        "query_variants": [{"kind": "original", "query": "how to join channel"}],
                        "decomposition_targets": [],
                        "agent_iterations": [{"round_index": 1, "decision": "answer_now"}],
                        "judge_summary": {"decision": "answer_now", "reason": "sufficient_first_pass_support"},
                        "selected_contexts": [{"chunk_id": "chunk-1"}],
                        "query_understanding_summary": {"query_profile": "how_to_faq"},
                        "tool_timing_summary": {"total_latency_ms": 1200.0},
                        "open_diagnosis_target": "rag-snapshot-1",
                    }
                }
            },
            packed_evidence=None,
            client_agent_runtime_state={"status": "completed"},
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=(execution, {"parallel_mode": "main_agent"}),
        ), patch.object(
            worker,
            "_record_ticket_agent_runtime_events",
            side_effect=lambda execution_arg: [
                repository.record_ticket_agent_event(
                    str(item.get("ticket_id") or ""),
                    str(item.get("message_id") or "").strip() or None,
                    str(item.get("run_id") or ""),
                    str(item.get("agent_name") or ""),
                    str(item.get("phase") or ""),
                    str(item.get("event_type") or ""),
                    dict(item.get("payload") or {}) if isinstance(item.get("payload"), dict) else {},
                )
                for item in getattr(execution_arg, "client_agent_runtime_events", [])
                if isinstance(item, dict)
            ],
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:00+00:00",
        ):
            worker._process_ticket_query(
                bus,
                dict(self.task, ticket_id="T-RAG-SNAPSHOT", customer_message="how to join channel"),
            )

        saved_ticket = repository.save_ticket.call_args_list[0].args[0]
        assistant_message = saved_ticket["messages"][-1]
        self.assertEqual(assistant_message["answer_route"], "rag")
        self.assertIn("retrieval_plan_snapshot", assistant_message)
        self.assertEqual(assistant_message["retrieval_plan_snapshot"]["request_id"], "rag-snapshot-1")
        self.assertEqual(assistant_message["retrieval_plan_snapshot"]["query_class"], "how_to_faq")

    def test_process_ticket_query_records_queue_wait_and_main_agent_timing_fields(self) -> None:
        ticket = _build_ticket(
            ticket_id="T-TIMING",
            customer_message="how to join channel",
            message_created_at="2026-03-22T00:00:00+00:00",
        )
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(ticket),
            copy.deepcopy(ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use joinChannel with the same channel name and token.",
            confidence=0.91,
            sources=["https://docs.agora.io/en/video-calling/get-started"],
            citations=[{"chunk_id": "chunk-1"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="grounded_answer",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["join channel"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
            workflow_action="answer_customer",
            client_intake_state=None,
            evidence_summary=None,
            client_agent_runtime_state={"status": "completed"},
        )

        now_values = iter(
            [
                "2026-03-22T00:00:10+00:00",
                "2026-03-22T00:00:11+00:00",
                "2026-03-22T00:00:12+00:00",
                "2026-03-22T00:00:13+00:00",
                "2026-03-22T00:00:14+00:00",
                "2026-03-22T00:00:15+00:00",
                "2026-03-22T00:00:16+00:00",
            ]
        )
        task = dict(
            self.task,
            ticket_id="T-TIMING",
            customer_message="how to join channel",
            message_created_at="2026-03-22T00:00:00+00:00",
            created_at="2026-03-22T00:00:00+00:00",
            api_persist_latency_ms=120.5,
            api_return_latency_ms=180.25,
            load_ticket_ms=5.0,
            save_ticket_ms=8.0,
            record_ticket_created_event_ms=2.0,
            enqueue_ticket_query_ms=3.0,
            enqueue_sentiment_ms=1.5,
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            side_effect=lambda: next(now_values),
        ):
            worker._process_ticket_query(bus, task)

        event_payload = repository.record_event.call_args.args[2]
        self.assertEqual(event_payload["task_dequeued_at"], "2026-03-22T00:00:10+00:00")
        self.assertEqual(event_payload["main_agent_started_at"], "2026-03-22T00:00:11+00:00")
        self.assertEqual(event_payload["main_agent_completed_at"], "2026-03-22T00:00:12+00:00")
        self.assertEqual(event_payload["queue_wait_ms"], 10000.0)
        self.assertEqual(event_payload["response_ready_dispatch_ms"], 4000.0)
        self.assertEqual(event_payload["load_ticket_ms"], 5.0)
        self.assertEqual(event_payload["save_ticket_ms"], 8.0)
        self.assertEqual(event_payload["record_ticket_created_event_ms"], 2.0)
        self.assertEqual(event_payload["enqueue_ticket_query_ms"], 3.0)
        self.assertEqual(event_payload["enqueue_sentiment_ms"], 1.5)

    def test_process_ticket_query_retries_transient_save_ticket_failure(self) -> None:
        initial_ticket = _build_ticket()
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(initial_ticket),
            copy.deepcopy(initial_ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.side_effect = [
            psycopg.OperationalError("connection timeout expired"),
            None,
        ]
        repository.record_event.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use the Node.js token builder sample.",
            confidence=0.91,
            sources=["official/deploy-token-server.md"],
            citations=[{"source": "official/deploy-token-server.md", "label": "Deploy a token server"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="docs_match",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["token", "node.js"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:00+00:00",
        ), patch.object(
            worker,
            "TICKET_REPOSITORY_RETRY_MAX",
            1,
        ), patch.object(
            worker,
            "TICKET_REPOSITORY_RETRY_BASE_DELAY_SECONDS",
            0.05,
        ), patch.object(
            worker.time,
            "sleep",
        ) as sleep_mock:
            worker._process_ticket_query(bus, dict(self.task))

        self.assertEqual(repository.save_ticket.call_count, 2)
        saved_ticket = repository.save_ticket.call_args_list[-1].args[0]
        self.assertEqual(saved_ticket["messages"][-1]["content"], "Use the Node.js token builder sample.")
        self.assertEqual(repository.record_event.call_count, 1)
        sleep_mock.assert_any_call(0.05)

    def test_schedule_ticket_task_retry_reenqueues_retryable_db_failure(self) -> None:
        queue = Mock()
        queue.enqueue.return_value = True

        with patch.object(worker, "TICKET_TASK_RETRY_MAX", 2), patch.object(
            worker,
            "TICKET_TASK_RETRY_BASE_DELAY_SECONDS",
            0.5,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:02:00+00:00",
        ), patch.object(worker.time, "sleep") as sleep_mock:
            scheduled = worker._schedule_ticket_task_retry(
                queue,
                dict(self.task),
                psycopg.OperationalError("server closed the connection unexpectedly"),
            )

        self.assertTrue(scheduled)
        queue.enqueue.assert_called_once()
        retry_task = queue.enqueue.call_args.args[0]
        self.assertEqual(retry_task["worker_retry_count"], 1)
        self.assertEqual(retry_task["last_retry_at"], "2026-03-22T00:02:00+00:00")
        self.assertIn("server closed the connection unexpectedly", retry_task["last_error"])
        sleep_mock.assert_called_once_with(0.5)

    def test_recover_stale_ticket_query_tasks_on_worker_start_reenqueues_missing_async_turn(self) -> None:
        stuck_ticket = _build_ticket(
            ticket_id="TK-116",
            product_selection_state={
                "phase": "awaiting_product_confirmation",
                "pending_customer_message": "I got black screen, what should I do now?",
                "pending_message_created_at": "2026-03-22T00:03:00+00:00",
            },
        )
        stuck_ticket["messages"].append(
            {
                "role": "customer",
                "content": "i got black screen, what should i do now?",
                "created_at": "2026-03-22T00:03:00+00:00",
            }
        )
        stuck_ticket["updated_at"] = "2026-03-22T00:03:01+00:00"
        repository = Mock()
        repository.list_tickets.return_value = [copy.deepcopy(stuck_ticket)]
        repository.list_ticket_events.return_value = [
            {
                "ticket_id": "TK-116",
                "event_type": "ticket_ai_processing",
                "payload": {
                    "message_created_at": "2026-03-22T00:03:00+00:00",
                },
                "created_at": "2026-03-22T00:03:02+00:00",
            }
        ]
        queue = Mock()
        queue.list_pending_tasks.return_value = []
        queue.enqueue.return_value = True

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:05:00+00:00",
        ):
            recovered = worker._recover_stale_ticket_query_tasks_on_worker_start(
                queue,
                worker_started_at="2026-03-22T00:04:00+00:00",
            )

        self.assertEqual(recovered, 1)
        queue.enqueue.assert_called_once()
        recovery_task = queue.enqueue.call_args.args[0]
        self.assertEqual(recovery_task["ticket_id"], "TK-116")
        self.assertEqual(recovery_task["customer_message"], "i got black screen, what should i do now?")
        self.assertEqual(recovery_task["message_created_at"], "2026-03-22T00:03:00+00:00")
        self.assertEqual(recovery_task["processing_mode"], "worker_startup_recovery")
        self.assertEqual(recovery_task["requester"], "Customer")
        self.assertEqual(recovery_task["latest_assistant_message"]["content"], "I am checking the knowledge base for you now.")
        self.assertEqual(
            recovery_task["product_selection_state"]["phase"],
            "awaiting_product_confirmation",
        )
        repository.record_event.assert_called_once()
        self.assertEqual(repository.record_event.call_args.args[0], "TK-116")
        self.assertEqual(repository.record_event.call_args.args[1], "ticket_ai_recovery_queued")
        recovery_event = repository.record_event.call_args.args[2]
        self.assertEqual(recovery_event["message_created_at"], "2026-03-22T00:03:00+00:00")
        self.assertEqual(recovery_event["recovery_reason"], "missing_async_completion_after_worker_restart")

    def test_recover_stale_ticket_query_tasks_on_worker_start_skips_turn_still_in_queue(self) -> None:
        stuck_ticket = _build_ticket(ticket_id="TK-117")
        stuck_ticket["messages"].append(
            {
                "role": "customer",
                "content": "the video stays frozen",
                "created_at": "2026-03-22T00:03:00+00:00",
            }
        )
        repository = Mock()
        repository.list_tickets.return_value = [copy.deepcopy(stuck_ticket)]
        repository.list_ticket_events.return_value = [
            {
                "ticket_id": "TK-117",
                "event_type": "ticket_ai_processing",
                "payload": {
                    "message_created_at": "2026-03-22T00:03:00+00:00",
                },
                "created_at": "2026-03-22T00:03:02+00:00",
            }
        ]
        queue = Mock()
        queue.list_pending_tasks.return_value = [
            {
                "task_type": "ticket_query",
                "ticket_id": "TK-117",
                "message_created_at": "2026-03-22T00:03:00+00:00",
            }
        ]

        with patch.object(worker, "ticket_repository", repository):
            recovered = worker._recover_stale_ticket_query_tasks_on_worker_start(
                queue,
                worker_started_at="2026-03-22T00:04:00+00:00",
            )

        self.assertEqual(recovered, 0)
        queue.enqueue.assert_not_called()
        repository.record_event.assert_not_called()

    def test_process_ticket_query_skips_duplicate_final_response_after_requeue(self) -> None:
        initial_ticket = _build_ticket()
        refreshed_ticket = _build_ticket()
        refreshed_ticket["messages"].append(
            {
                "role": "assistant",
                "content": "Use the Node.js token builder sample.",
                "created_at": "2026-03-22T00:01:00+00:00",
                "sources": ["official/deploy-token-server.md"],
                "citations": [
                    {
                        "source": "official/deploy-token-server.md",
                        "label": "Deploy a token server",
                    }
                ],
            }
        )
        repository = Mock()
        repository.get_ticket.return_value = copy.deepcopy(refreshed_ticket)
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use the Node.js token builder sample.",
            confidence=0.91,
            sources=["official/deploy-token-server.md"],
            citations=[{"source": "official/deploy-token-server.md", "label": "Deploy a token server"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="docs_match",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["token", "node.js"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:05+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task))

        repository.save_ticket.assert_not_called()

    def test_find_existing_worker_response_returns_single_persisted_reply_for_customer_turn(self) -> None:
        ticket = _build_ticket()
        ticket["messages"] = [
            ticket["messages"][0],
            {
                "role": "assistant",
                "content": "Use the Node.js token builder sample.",
                "created_at": "2026-03-22T00:01:00+00:00",
                "answer_route": "rag",
                "route_reason": "docs_match",
            },
        ]

        existing = worker._find_existing_worker_response(
            ticket,
            self.task["customer_message"],
            self.task["message_created_at"],
        )

        self.assertIsNotNone(existing)
        assert existing is not None
        self.assertEqual(existing["content"], "Use the Node.js token builder sample.")

    def test_process_ticket_query_skips_duplicate_final_response_after_requeue_with_single_assistant_reply(self) -> None:
        refreshed_ticket = _build_ticket()
        refreshed_ticket["messages"] = [
            refreshed_ticket["messages"][0],
            {
                "role": "assistant",
                "content": "Use the Node.js token builder sample.",
                "created_at": "2026-03-22T00:01:00+00:00",
                "sources": ["official/deploy-token-server.md"],
                "citations": [
                    {
                        "source": "official/deploy-token-server.md",
                        "label": "Deploy a token server",
                    }
                ],
            },
        ]
        repository = Mock()
        repository.get_ticket.return_value = copy.deepcopy(refreshed_ticket)
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use the Node.js token builder sample.",
            confidence=0.91,
            sources=["official/deploy-token-server.md"],
            citations=[{"source": "official/deploy-token-server.md", "label": "Deploy a token server"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="docs_match",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["token", "node.js"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:05+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task))

        repository.save_ticket.assert_not_called()

    def test_process_ticket_query_persists_route_metadata_without_calling_legacy_build_answer(self) -> None:
        initial_ticket = _build_ticket()
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(initial_ticket),
            copy.deepcopy(initial_ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        bus = Mock()

        resolution = types.SimpleNamespace(
            answer="Agora's CEO is Tony Zhao.",
            confidence=0.93,
            sources=["https://www.agora.io/en/about-agora/"],
            citations=[
                {
                    "source_url": "https://www.agora.io/en/about-agora/",
                    "heading": "About Agora",
                    "source_path": "https://www.agora.io/en/about-agora/",
                }
            ],
            needs_engineer_guidance=False,
            answer_route="web_search",
            scope_label="agora_non_technical",
            route_reason="agora_public_info",
            route_confidence=0.93,
            search_used=True,
            matched_signals=["agora", "ceo"],
            route_family="web_company_info",
            execution_action="web_search",
            tooling_profile="official_web_search",
            needs_investigating=False,
            next_status="communicating",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=resolution,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:05+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task))

        saved_ticket = repository.save_ticket.call_args.args[0]
        assistant_message = saved_ticket["messages"][-1]
        self.assertEqual(saved_ticket["status"], "communicating")
        self.assertEqual(assistant_message["answer_route"], "web_search")
        self.assertEqual(assistant_message["scope_label"], "agora_non_technical")
        self.assertTrue(assistant_message["search_used"])
        event_payload = repository.record_event.call_args.args[2]
        self.assertEqual(event_payload["status"], "communicating")
        self.assertEqual(event_payload["answer_route"], "web_search")
        self.assertEqual(event_payload["scope_label"], "agora_non_technical")
        self.assertNotIn("engineer_mode", event_payload)
        self.assertNotIn("priority", event_payload)

    def test_process_ticket_query_post_check_rejection_starts_investigation(self) -> None:
        initial_ticket = _build_ticket()
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(initial_ticket),
            copy.deepcopy(initial_ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.save_investigation.return_value = None
        repository.record_event.return_value = None
        bus = Mock()

        execution = types.SimpleNamespace(
            answer="Please upgrade to SDK 4.2.2 and retry token renewal.",
            confidence=0.86,
            sources=["https://docs.agora.io/en/video-calling/token-authentication"],
            citations=[
                {
                    "chunk_id": "chunk-1",
                    "source_path": "official/token-authentication.md",
                    "heading": "Token authentication",
                    "source_url": "https://docs.agora.io/en/video-calling/token-authentication",
                }
            ],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="grounded_answer",
            route_confidence=0.93,
            search_used=False,
            matched_signals=["token", "android 14"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=True,
            next_status="investigating",
            investigation_reason="rag_post_check_insufficient",
            evidence_summary={
                "quality_signals": {
                    "generation_mode": "structured_answer",
                    "selected_doc_count": 1,
                },
                "selected_contexts": [],
            },
        )

        investigation_result = {
            "created": True,
            "public_reply": "This issue requires further internal investigation, which may take some time. Thank you for your patience. We expect to reply or update you here within 20 minutes.",
            "active_investigation": {
                "id": "INV-RETRY-1",
                "state": "active",
                "trigger_reason": "rag_post_check_insufficient",
                "trigger_source": "worker_async_rag",
                "messages": [
                    {
                        "id": "INV-RETRY-1-m1",
                        "role": "engineer_ai",
                        "content": "Please confirm whether Android 14 is the only affected platform.",
                        "created_at": "2026-03-22T00:01:05+00:00",
                    }
                ],
            },
            "new_internal_messages": [],
        }
        captured_opening_context = None

        def _start_or_refresh(ticket, **kwargs):
            nonlocal captured_opening_context
            captured_opening_context = copy.deepcopy(kwargs.get("opening_context"))
            ticket["status"] = "investigating"
            ticket["active_investigation"] = copy.deepcopy(investigation_result["active_investigation"])
            ticket["engineer_handoff_packet"] = {
                "source": "worker_async_rag",
                "conversation_summary": "Customer reports token renew callback never fires.",
                "latest_customer_message": "token renew callback never fires",
                "latest_client_ai_reply": "This issue requires further internal investigation, which may take some time. Thank you for your patience. We expect to reply or update you here within 20 minutes.",
                "route_summary": {
                    "answer_route": "rag",
                    "route_reason": "grounded_answer",
                },
                "rag_result": {
                    "candidate_answer": execution.answer,
                    "sources": list(execution.sources),
                    "citations": [dict(item) for item in execution.citations],
                    "evidence_summary": dict(execution.evidence_summary),
                },
                "unresolved_reason": "rag_post_check_insufficient",
                "customer_language_hint": "en",
                "created_at": "2026-03-22T00:01:05+00:00",
                "updated_at": "2026-03-22T00:01:05+00:00",
            }
            ticket["engineer_agent_state"] = {
                "phase": "gather_missing_inputs",
                "issue_understanding": "Token renew callback still fails after the upgrade attempt.",
                "knowledge_summary": "Client AI found generic token-authentication guidance but not enough Android 14-specific evidence.",
                "why_not_solved": "The current grounded answer is not enough to prove the Android-specific fix.",
                "goal": "Confirm Android 14 scope and exact SDK version before replying.",
                "known_facts": ["Customer confirmed the upgrade attempt already failed."],
                "missing_information": ["Exact SDK version", "Whether Android 14 is the only affected platform"],
                "next_request_for_engineer": "Please confirm Android 14 scope and exact SDK version.",
                "resolution_hypothesis": "The issue may be isolated to SDK 4.2.1 on Android 14.",
                "ready_to_reply": False,
                "last_refreshed_at": "2026-03-22T00:01:05+00:00",
            }
            return copy.deepcopy(investigation_result)

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "start_or_refresh_investigation",
            side_effect=_start_or_refresh,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:05+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task))

        saved_ticket = repository.save_ticket.call_args_list[0].args[0]
        saved_engineer_case = repository.save_engineer_case.call_args.kwargs["engineer_case"]
        self.assertEqual(saved_ticket["status"], "investigating")
        self.assertEqual(saved_ticket["messages"][-1]["content"], investigation_result["public_reply"])
        self.assertEqual(
            saved_engineer_case["engineer_handoff_packet"]["rag_result"]["candidate_answer"],
            "Please upgrade to SDK 4.2.2 and retry token renewal.",
        )
        self.assertEqual(saved_engineer_case["engineer_agent_state"]["phase"], "gather_missing_inputs")
        self.assertEqual(
            saved_engineer_case["engineer_agent_state"]["known_facts"],
            ["Customer confirmed the upgrade attempt already failed."],
        )
        self.assertEqual(saved_engineer_case["engineer_case_id"], "T-RETRY-1")
        self.assertEqual(saved_engineer_case["title"], "Token question")
        self.assertEqual(repository.save_engineer_case.call_count, 1)
        self.assertIsInstance(captured_opening_context, dict)
        self.assertIn("Need help with token generation", captured_opening_context["issue_summary"])
        self.assertIn(
            "Please upgrade to SDK 4.2.2 and retry token renewal.",
            captured_opening_context["rag_answer_summary"],
        )
        self.assertIn("Action Needed", f"Action Needed: {captured_opening_context['action_needed']}")
        self.assertEqual(
            captured_opening_context["sources"],
            ["https://docs.agora.io/en/video-calling/token-authentication"],
        )
        self.assertEqual(
            captured_opening_context["citations"][0]["source_url"],
            "https://docs.agora.io/en/video-calling/token-authentication",
        )
        first_event = repository.record_event.call_args_list[0].args[2]
        self.assertEqual(first_event["status"], "investigating")
        self.assertEqual(first_event["execution_action"], "rag")
        investigation_event = repository.record_event.call_args_list[1].args[2]
        self.assertEqual(investigation_event["agent_phase"], "gather_missing_inputs")

    def test_process_ticket_query_drops_stale_result_when_newer_customer_turn_exists(self) -> None:
        initial_ticket = _build_ticket(
            customer_message="First question",
            message_created_at="2026-03-22T00:00:00+00:00",
        )
        refreshed_ticket = copy.deepcopy(initial_ticket)
        refreshed_ticket["messages"].append(
            {
                "role": "customer",
                "content": "Second question",
                "created_at": "2026-03-22T00:01:00+00:00",
            }
        )
        refreshed_ticket["updated_at"] = "2026-03-22T00:01:00+00:00"

        repository = Mock()
        repository.get_ticket.return_value = copy.deepcopy(refreshed_ticket)
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        bus = Mock()

        execution = types.SimpleNamespace(
            answer="Old answer that should be dropped.",
            confidence=0.91,
            sources=["official/docs.md"],
            citations=[{"source": "official/docs.md", "label": "Docs"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="grounded_answer",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["token"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
        )

        task = dict(
            self.task,
            customer_message="First question",
            message_created_at="2026-03-22T00:00:00+00:00",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:05+00:00",
        ):
            worker._process_ticket_query(bus, task)

        repository.save_ticket.assert_not_called()
        repository.record_event.assert_not_called()

    def test_process_ticket_query_service_error_preserves_service_error_reason(self) -> None:
        initial_ticket = _build_ticket(
            ticket_id="T-SVCERR",
            customer_message="how to join channel",
        )
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(initial_ticket),
            copy.deepcopy(initial_ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.save_investigation.return_value = None
        repository.record_event.return_value = None
        bus = Mock()

        execution = types.SimpleNamespace(
            answer="I couldn't find enough information in the available support knowledge base to answer that question.",
            confidence=0.0,
            sources=[],
            citations=[],
            needs_engineer_guidance=True,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="rag_service_error",
            route_confidence=0.98,
            search_used=False,
            matched_signals=["join channel"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=True,
            next_status="investigating",
            investigation_reason="rag_service_error",
            evidence_summary=None,
        )

        captured_opening_context = None

        def _start_or_refresh(ticket, **kwargs):
            nonlocal captured_opening_context
            trigger_reason = kwargs["trigger_reason"]
            captured_opening_context = copy.deepcopy(kwargs.get("opening_context"))
            ticket["status"] = "investigating"
            ticket["active_investigation"] = {
                "id": "INV-SVCERR-1",
                "state": "active",
                "trigger_reason": trigger_reason,
                "trigger_source": "worker_async_rag",
                "messages": [
                    {
                        "id": "INV-SVCERR-1-m1",
                        "role": "engineer_ai",
                        "content": "RAG service failed before it returned a grounded answer.",
                        "created_at": "2026-03-22T00:01:05+00:00",
                    }
                ],
            }
            execution_context = kwargs.get("execution_context") or {}
            ticket["engineer_handoff_packet"] = {
                "source": "worker_async_rag",
                "conversation_summary": "Customer: how to join channel",
                "latest_customer_message": "how to join channel",
                "latest_client_ai_reply": "This issue requires further internal investigation, which may take some time. Thank you for your patience. We expect to reply or update you here within 20 minutes.",
                "route_summary": {
                    "answer_route": "rag",
                    "route_reason": execution_context.get("route_reason"),
                },
                "rag_result": {
                    "candidate_answer": "RAG service error prevented a grounded answer from being produced.",
                    "sources": [],
                    "citations": [],
                    "evidence_summary": {},
                },
                "unresolved_reason": trigger_reason,
                "customer_language_hint": "en",
                "created_at": "2026-03-22T00:01:05+00:00",
                "updated_at": "2026-03-22T00:01:05+00:00",
            }
            ticket["engineer_agent_state"] = {
                "phase": "gather_missing_inputs",
                "issue_understanding": "how to join channel",
                "knowledge_summary": "RAG service failed before a grounded answer was available.",
                "why_not_solved": "The RAG service failed before it could return a grounded answer, so client AI could not respond safely.",
                "goal": "Restore the RAG service path and rerun the customer query.",
                "known_facts": ["Customer reported: how to join channel"],
                "missing_information": ["Confirm the RAG service error type and the failing request trace."],
                "next_request_for_engineer": "Confirm the RAG service error type and the failing request trace.",
                "resolution_hypothesis": "",
                "ready_to_reply": False,
                "last_refreshed_at": "2026-03-22T00:01:05+00:00",
            }
            return {
                "created": True,
                "public_reply": "This issue requires further internal investigation, which may take some time. Thank you for your patience. We expect to reply or update you here within 20 minutes.",
                "active_investigation": copy.deepcopy(ticket["active_investigation"]),
                "new_internal_messages": [],
            }

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "start_or_refresh_investigation",
            side_effect=_start_or_refresh,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:05+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task, ticket_id="T-SVCERR", customer_message="how to join channel"))

        saved_ticket = repository.save_ticket.call_args_list[0].args[0]
        saved_engineer_case = repository.save_engineer_case.call_args.kwargs["engineer_case"]
        self.assertEqual(saved_ticket["status"], "investigating")
        self.assertEqual(saved_ticket["messages"][-1]["content"], "This issue requires further internal investigation, which may take some time. Thank you for your patience. We expect to reply or update you here within 20 minutes.")
        self.assertEqual(saved_engineer_case["trigger_reason"], "rag_service_error")
        self.assertEqual(saved_engineer_case["engineer_handoff_packet"]["route_summary"]["route_reason"], "rag_service_error")
        self.assertEqual(saved_engineer_case["engineer_handoff_packet"]["unresolved_reason"], "rag_service_error")
        self.assertEqual(
            saved_engineer_case["engineer_handoff_packet"]["rag_result"]["candidate_answer"],
            "RAG service error prevented a grounded answer from being produced.",
        )
        self.assertIsInstance(captured_opening_context, dict)
        self.assertIn("RAG service failed", captured_opening_context["rag_answer_summary"])
        first_event = repository.record_event.call_args_list[0].args[2]
        self.assertEqual(first_event["status"], "investigating")
        self.assertEqual(first_event["execution_action"], "rag")
        investigation_event = repository.record_event.call_args_list[1].args[2]
        self.assertEqual(investigation_event["agent_phase"], "gather_missing_inputs")
        self.assertFalse(investigation_event["agent_ready_to_reply"])
        self.assertEqual(
            investigation_event["agent_next_request_for_engineer"],
            "Confirm the RAG service error type and the failing request trace.",
        )

    def test_process_ticket_message_sentiment_persists_label_and_records_event(self) -> None:
        repository = Mock()
        repository.get_ticket.return_value = copy.deepcopy(_build_ticket())
        repository.update_message_sentiment_label.return_value = True
        repository.record_event.return_value = None
        bus = Mock()
        task = {
            "task_type": "ticket_message_sentiment",
            "ticket_id": "T-RETRY",
            "customer_message": "Need help with token generation",
            "message_created_at": "2026-03-22T00:00:00+00:00",
            "created_at": "2026-03-22T00:00:01+00:00",
        }

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "classify_sentiment",
            return_value=types.SimpleNamespace(
                bucket="negative",
                raw_label="anger",
                confidence=0.91,
                provider="test",
            ),
        ), patch.object(worker, "now_iso", return_value="2026-03-22T00:03:00+00:00"), patch.object(
            worker,
            "_publish",
        ) as publish_mock:
            worker._process_ticket_message_sentiment(bus, task)

        repository.update_message_sentiment_label.assert_called_once_with(
            ticket_id="T-RETRY",
            role="customer",
            content="Need help with token generation",
            created_at="2026-03-22T00:00:00+00:00",
            sentiment_label="bad",
        )
        event_payload = repository.record_event.call_args.args[2]
        self.assertEqual(event_payload["event"], "ticket_message_sentiment_tagged")
        self.assertEqual(event_payload["sentiment_label"], "bad")
        publish_mock.assert_called_once()

    def test_process_ticket_message_sentiment_skips_when_customer_message_cannot_be_updated(self) -> None:
        repository = Mock()
        repository.get_ticket.return_value = copy.deepcopy(_build_ticket())
        repository.update_message_sentiment_label.return_value = False
        repository.record_event.return_value = None
        bus = Mock()
        task = {
            "task_type": "ticket_message_sentiment",
            "ticket_id": "T-RETRY",
            "customer_message": "Need help with token generation",
            "message_created_at": "2026-03-22T00:00:00+00:00",
            "created_at": "2026-03-22T00:00:01+00:00",
        }

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "classify_sentiment",
            return_value=types.SimpleNamespace(
                bucket="neutral",
                raw_label="neutral",
                confidence=0.51,
                provider="test",
            ),
        ), patch.object(worker, "_publish") as publish_mock:
            worker._process_ticket_message_sentiment(bus, task)

        repository.record_event.assert_not_called()
        publish_mock.assert_not_called()

    def test_worker_task_types_from_env_filters_and_deduplicates_values(self) -> None:
        with patch.dict(
            os.environ,
            {"WORKER_TASK_TYPES": "ticket_query, ticket_message_sentiment, ticket_query,unknown"},
            clear=False,
        ):
            self.assertEqual(
                worker._worker_task_types_from_env(),
                ("ticket_query", "ticket_message_sentiment"),
            )

    def test_worker_task_types_from_env_defaults_to_all_supported_types(self) -> None:
        with patch.dict(os.environ, {"WORKER_TASK_TYPES": ""}, clear=False):
            self.assertEqual(
                worker._worker_task_types_from_env(),
                ("ticket_query", "ticket_message_sentiment"),
            )

    def test_handle_billing_request_reply_generates_customer_followup(self) -> None:
        repository = Mock()
        repository.get_billing_ticket_by_client_ticket_id.return_value = {
            "billing_ticket_id": "BT-TK-ACC-1",
            "client_ticket_id": "TK-ACC-1",
            "title": "Detailed invoice request",
            "question": "Please send the detailed invoice.",
            "automation_status": "automation",
        }
        repository.get_ticket.return_value = {
            "ticket_id": "TK-ACC-1",
            "subject": "Detailed invoice request",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please send the detailed invoice for transaction 123.",
                    "created_at": "2026-07-02T00:00:00+00:00",
                },
                {
                    "role": "assistant",
                    "content": "We have escalated this to billing.",
                    "created_at": "2026-07-02T00:00:01+00:00",
                },
            ],
        }
        reply = types.SimpleNamespace(
            message_id="msg-1",
            subject="Re: [Billing Request] Detailed invoice request - Ticket TK-ACC-1",
            sender="billing@example.com",
            body_text="Done. The detailed invoice was sent to the customer email.",
            received_at="2026-07-02T08:14:38Z",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "record_billing_request_reply",
        ) as record_mock:
            worker.handle_billing_request_reply(reply)

        record_mock.assert_called_once_with(reply)
        repository.get_billing_ticket_by_client_ticket_id.assert_called_once_with("TK-ACC-1")
        repository.get_ticket.assert_called_once_with("TK-ACC-1")
        saved_ticket = repository.save_ticket.call_args.args[0]
        new_messages = repository.save_ticket.call_args.kwargs["new_messages"]
        self.assertEqual(saved_ticket["messages"][-1]["role"], "assistant")
        self.assertEqual(saved_ticket["messages"][-1]["source"], "billing_reply_email")
        self.assertIn("detailed invoice", saved_ticket["messages"][-1]["content"].lower())
        self.assertEqual(new_messages, [saved_ticket["messages"][-1]])
        saved_billing_ticket = repository.save_billing_ticket.call_args.args[0]
        self.assertEqual(saved_billing_ticket["automation_status"], "customer_notified")
        self.assertIn("detailed invoice", saved_billing_ticket["customer_reply"].lower())
        self.assertEqual(repository.record_event.call_count, 2)
        self.assertEqual(repository.record_event.call_args_list[0].args[1], "billing_internal_resolution_submitted")
        self.assertEqual(repository.record_event.call_args_list[1].args[1], "billing_customer_followup_generated")

    def test_handle_billing_request_reply_rejects_empty_body_before_marking_read(self) -> None:
        repository = Mock()
        repository.get_billing_ticket_by_client_ticket_id.return_value = {
            "billing_ticket_id": "BT-TK-ACC-1",
            "client_ticket_id": "TK-ACC-1",
        }
        repository.get_ticket.return_value = {"ticket_id": "TK-ACC-1", "messages": []}
        reply = types.SimpleNamespace(
            message_id="msg-empty",
            subject="Re: [Billing Request] Detailed invoice request - Ticket TK-ACC-1",
            sender="billing@example.com",
            body_text="",
            received_at="2026-07-02T08:14:38Z",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "record_billing_request_reply",
        ) as record_mock:
            with self.assertRaisesRegex(ValueError, "body is empty"):
                worker.handle_billing_request_reply(reply)

        record_mock.assert_called_once_with(reply)
        repository.save_ticket.assert_not_called()
        repository.save_billing_ticket.assert_not_called()
        repository.record_event.assert_not_called()

    def test_billing_reply_poller_is_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {"BILLING_AUTOMATION_REPLY_POLL_ENABLED": ""}, clear=False):
            self.assertFalse(worker._billing_reply_poller_enabled_from_env())

    def test_billing_reply_poller_enabled_from_env(self) -> None:
        with patch.dict(os.environ, {"BILLING_AUTOMATION_REPLY_POLL_ENABLED": "true"}, clear=False):
            self.assertTrue(worker._billing_reply_poller_enabled_from_env())

    def test_start_billing_reply_poller_starts_daemon_thread_when_enabled(self) -> None:
        started_threads = []

        class _FakeThread:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.daemon = kwargs.get("daemon")
                self.name = kwargs.get("name")

            def start(self) -> None:
                started_threads.append(self)

        with patch.dict(
            os.environ,
            {
                "BILLING_AUTOMATION_REPLY_POLL_ENABLED": "true",
                "BILLING_AUTOMATION_REPLY_POLL_INTERVAL_SECONDS": "7",
            },
            clear=False,
        ), patch.object(worker.threading, "Thread", _FakeThread):
            thread = worker._start_billing_reply_poller_if_enabled()

        self.assertIsNotNone(thread)
        self.assertEqual(len(started_threads), 1)
        self.assertEqual(started_threads[0].name, "billing-reply-poller")
        self.assertTrue(started_threads[0].daemon)
        self.assertEqual(started_threads[0].kwargs["args"], (7.0,))


if __name__ == "__main__":
    unittest.main()
