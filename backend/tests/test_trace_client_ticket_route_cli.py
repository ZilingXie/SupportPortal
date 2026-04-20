from __future__ import annotations

import importlib.util
from unittest import mock
import unittest
from pathlib import Path


def _load_script_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "trace_client_ticket_route.py"
    spec = importlib.util.spec_from_file_location("trace_client_ticket_route_script", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TraceClientTicketRouteCliTests(unittest.TestCase):
    def test_parser_exposes_split_timeout_budgets(self) -> None:
        module = _load_script_module()

        args = module.build_parser().parse_args([])

        self.assertEqual(args.query_timeout_seconds, 45.0)
        self.assertEqual(args.completion_timeout_seconds, 90.0)
        self.assertEqual(args.direct_probe_timeout_seconds, 30.0)
        self.assertEqual(args.post_answer_artifact_timeout_seconds, 15.0)
        self.assertEqual(args.poll_interval_seconds, 2.0)
        self.assertEqual(args.event_limit, 80)

    def test_preflight_requires_healthy_service(self) -> None:
        module = _load_script_module()

        with self.assertRaisesRegex(RuntimeError, "health"):
            module.run_preflight_checks(
                base_url="http://127.0.0.1:8080",
                http_get_json=lambda _url: {"status": "degraded"},
            )

    def test_preflight_no_longer_requires_runtime_mode_env(self) -> None:
        module = _load_script_module()

        payload = module.run_preflight_checks(
            base_url="http://127.0.0.1:8080",
            http_get_json=lambda _url: {"status": "ok"},
        )

        self.assertEqual(payload["health"]["status"], "ok")
        self.assertNotIn("containers", payload)

    def test_build_trace_summary_reports_agent_timeline_and_rag_internal_timings(self) -> None:
        module = _load_script_module()

        summary = module.build_trace_summary(
            ticket={
                "ticket_id": "TK-TRACE-001",
                "customer_id": "C-TRACE-001",
                "product": "audio_video_calling",
                "client_agent_runtime_state": {
                    "active_run_id": "run-123",
                    "workflow_action": "answer_customer",
                    "status": "completed",
                    "build_provenance": {
                        "task_app_build_ref": "build-123",
                        "execution_app_build_ref": "build-123",
                    },
                },
                "messages": [
                    {
                        "role": "customer",
                        "content": "how to join channel",
                        "created_at": "2026-04-04T00:00:01+00:00",
                    },
                    {
                        "role": "assistant",
                        "content": "Use joinChannel with a valid token, channel name, and uid.",
                        "created_at": "2026-04-04T00:00:04+00:00",
                        "answer_route": "rag",
                        "route_reason": "grounded_answer",
                        "workflow_action": "answer_customer",
                    },
                ],
            },
            request_context={
                "ticket_id": "TK-TRACE-001",
                "customer_id": "C-TRACE-001",
                "product": "audio_video_calling",
                "message": "how to join channel",
                "message_created_at": "2026-04-04T00:00:01+00:00",
                "question_started_at": "2026-04-04T00:00:00+00:00",
                "ack_received_at": "2026-04-04T00:00:00.300000+00:00",
            },
            ack_payload={
                "ack_text": "Got it, let me check this for you.",
                "model": "gpt-5.4-nano",
                "latency_ms": 300.0,
            },
            query_payload={
                "processing_mode": "main_agent_async",
                "queued_for_ai": True,
                "api_persist_latency_ms": 42.5,
                "api_return_latency_ms": 49.2,
                "queued_message_created_at": "2026-04-04T00:00:01+00:00",
            },
            ticket_events=[
                {
                    "event_type": "ticket_ai_response_ready",
                    "payload": {
                        "workflow_action": "answer_customer",
                        "answer_route": "rag",
                        "route_reason": "grounded_answer",
                        "load_ticket_ms": 11.0,
                        "save_ticket_ms": 18.0,
                        "record_ticket_created_event_ms": 4.0,
                        "enqueue_ticket_query_ms": 6.0,
                        "enqueue_sentiment_ms": 3.0,
                        "task_dequeued_at": "2026-04-04T00:00:01.090000+00:00",
                        "message_to_task_dequeued_ms": 90.0,
                        "queue_wait_ms": 90.0,
                        "main_agent_started_at": "2026-04-04T00:00:01.120000+00:00",
                        "dequeued_to_main_agent_started_ms": 30.0,
                        "main_agent_completed_at": "2026-04-04T00:00:03.720000+00:00",
                        "main_agent_total_ms": 2600.0,
                        "main_agent_to_answer_saved_ms": 358.0,
                        "response_ready_dispatch_ms": 22.0,
                        "answer_saved_to_response_ready_ms": 22.0,
                        "task_app_build_ref": "build-123",
                        "execution_app_build_ref": "build-123",
                    },
                    "created_at": "2026-04-04T00:00:04.100000+00:00",
                }
            ],
            agent_events=[
                {
                    "ticket_id": "TK-TRACE-001",
                    "message_id": "2026-04-04T00:00:01+00:00",
                    "run_id": "run-123",
                    "agent_name": "main_agent",
                    "phase": "created",
                    "event_type": "run_created",
                    "payload": {"product": "audio_video_calling", "created_at": "2026-04-04T00:00:01.010000+00:00"},
                    "created_at": "2026-04-04T00:00:05+00:00",
                },
                {
                    "ticket_id": "TK-TRACE-001",
                    "message_id": "2026-04-04T00:00:01+00:00",
                    "run_id": "run-123",
                    "agent_name": "main_agent",
                    "phase": "running",
                    "event_type": "started",
                    "payload": {"created_at": "2026-04-04T00:00:01.020000+00:00"},
                    "created_at": "2026-04-04T00:00:05+00:00",
                },
                {
                    "ticket_id": "TK-TRACE-001",
                    "message_id": "2026-04-04T00:00:01+00:00",
                    "run_id": "run-123",
                    "agent_name": "route_agent",
                    "phase": "running",
                    "event_type": "started",
                    "payload": {"created_at": "2026-04-04T00:00:01.025000+00:00"},
                    "created_at": "2026-04-04T00:00:05+00:00",
                },
                {
                    "ticket_id": "TK-TRACE-001",
                    "message_id": "2026-04-04T00:00:01+00:00",
                    "run_id": "run-123",
                    "agent_name": "rag_agent",
                    "phase": "running",
                    "event_type": "started",
                    "payload": {
                        "request_id": "rag-123",
                        "created_at": "2026-04-04T00:00:01.030000+00:00",
                    },
                    "created_at": "2026-04-04T00:00:05+00:00",
                },
                {
                    "ticket_id": "TK-TRACE-001",
                    "message_id": "2026-04-04T00:00:01+00:00",
                    "run_id": "run-123",
                    "agent_name": "route_agent",
                    "phase": "completed",
                    "event_type": "completed",
                    "payload": {
                        "decision": "rag",
                        "reason": "technical_question",
                        "created_at": "2026-04-04T00:00:01.180000+00:00",
                    },
                    "created_at": "2026-04-04T00:00:05+00:00",
                },
                {
                    "ticket_id": "TK-TRACE-001",
                    "message_id": "2026-04-04T00:00:01+00:00",
                    "run_id": "run-123",
                    "agent_name": "rag_agent",
                    "phase": "completed",
                    "event_type": "completed",
                    "payload": {
                        "decision": "grounded_answer",
                        "confidence": 0.96,
                        "created_at": "2026-04-04T00:00:03.700000+00:00",
                    },
                    "created_at": "2026-04-04T00:00:05+00:00",
                },
                {
                    "ticket_id": "TK-TRACE-001",
                    "message_id": "2026-04-04T00:00:01+00:00",
                    "run_id": "run-123",
                    "agent_name": "review_agent",
                    "phase": "skipped",
                    "event_type": "skipped",
                    "payload": {
                        "reason": "low_risk_grounded_answer",
                        "created_at": "2026-04-04T00:00:03.710000+00:00",
                    },
                    "created_at": "2026-04-04T00:00:05+00:00",
                },
                {
                    "ticket_id": "TK-TRACE-001",
                    "message_id": "2026-04-04T00:00:01+00:00",
                    "run_id": "run-123",
                    "agent_name": "main_agent",
                    "phase": "completed",
                    "event_type": "workflow_decided",
                    "payload": {
                        "workflow_action": "answer_customer",
                        "route_reason": "grounded_answer",
                        "created_at": "2026-04-04T00:00:03.720000+00:00",
                    },
                    "created_at": "2026-04-04T00:00:05+00:00",
                },
            ],
            rag_run={
                "request_id": "rag-123",
                "intent_latency_ms": 25.0,
                "rewrite_latency_ms": 18.0,
                "vector_retrieval_latency_ms": 140.0,
                "bm25_retrieval_latency_ms": 32.0,
                "bm25_sql_latency_ms": 22.0,
                "fts_latency_ms": 10.0,
                "retrieval_round_wall_clock_ms": 145.0,
                "retrieval_tool_timings": [
                    {
                        "tool_name": "p_bm25",
                        "query_kind": "original",
                        "round_index": 1,
                        "index_role": "primary",
                        "latency_ms": 22.0,
                        "candidate_count": 12,
                        "used_seed_tool": False,
                    },
                    {
                        "tool_name": "p_fts",
                        "query_kind": "original",
                        "round_index": 1,
                        "index_role": "primary",
                        "latency_ms": 10.0,
                        "candidate_count": 12,
                        "used_seed_tool": False,
                    },
                ],
                "retrieval_latency_ms": 172.0,
                "rerank_latency_ms": 65.0,
                "generation_latency_ms": 1830.0,
                "total_latency_ms": 2345.0,
                "query_class": "how_to_faq",
                "light_path_used": False,
                "vector_setup_skipped": False,
                "answer_profile_used": "gpt-5.4",
                "answer_profile_fallback_used": False,
            },
        )

        self.assertEqual(summary["raw_ids"]["run_id"], "run-123")
        self.assertEqual(summary["raw_ids"]["rag_request_id"], "rag-123")
        self.assertEqual(summary["raw_ids"]["request_id"], "rag-123")
        self.assertEqual(summary["main_agent"]["workflow_action"], "answer_customer")
        self.assertEqual(summary["route_agent"]["decision"], "rag")
        self.assertEqual(summary["rag_agent"]["decision"], "grounded_answer")
        self.assertEqual(summary["review_agent"]["status"], "skipped")
        self.assertEqual(summary["rag_internal_telemetry"]["status"], "available")
        self.assertEqual(summary["rag_internal_telemetry"]["query_class"], "how_to_faq")
        self.assertFalse(summary["rag_internal_telemetry"]["light_path_used"])
        self.assertFalse(summary["rag_internal_telemetry"]["vector_setup_skipped"])
        self.assertEqual(summary["rag_internal_telemetry"]["answer_profile_used"], "gpt-5.4")
        self.assertFalse(summary["rag_internal_telemetry"]["answer_profile_fallback_used"])
        self.assertEqual(summary["rag_internal_telemetry"]["bm25_sql_latency_ms"], 22.0)
        self.assertEqual(summary["rag_internal_telemetry"]["fts_latency_ms"], 10.0)
        self.assertEqual(summary["rag_internal_telemetry"]["retrieval_round_wall_clock_ms"], 145.0)
        self.assertEqual(len(summary["rag_internal_telemetry"]["retrieval_tool_timings"]), 2)
        self.assertEqual(summary["final_result"]["answer_route"], "rag")
        self.assertFalse(summary["post_answer_artifacts_incomplete"])
        self.assertEqual(summary["admission"]["load_ticket_ms"], 11.0)
        self.assertEqual(summary["worker_queue"]["queue_wait_ms"], 90.0)
        self.assertEqual(summary["worker_queue"]["message_to_task_dequeued_ms"], 90.0)
        self.assertEqual(summary["worker_queue"]["dequeued_to_main_agent_started_ms"], 30.0)
        self.assertEqual(summary["worker_queue"]["main_agent_total_ms"], 2600.0)
        self.assertEqual(summary["worker_queue"]["main_agent_to_answer_saved_ms"], 358.0)
        self.assertEqual(summary["worker_queue"]["answer_saved_to_response_ready_ms"], 22.0)
        self.assertEqual(summary["worker_queue"]["response_ready_dispatch_ms"], 22.0)
        self.assertEqual(summary["build_provenance"]["task_app_build_ref"], "build-123")
        self.assertEqual(summary["build_provenance"]["execution_app_build_ref"], "build-123")
        self.assertEqual(summary["build_provenance"]["status"], "matched")
        self.assertIn("joinChannel", summary["final_result"]["answer"])
        self.assertGreater(summary["metrics"]["question_to_final_answer_ms"], 0)
        self.assertGreater(summary["metrics"]["ack_to_final_answer_ms"], 0)

        markdown = module.render_markdown_report(summary)
        self.assertIn("how to join channel", markdown)
        self.assertIn("RAG 内部分段", markdown)
        self.assertIn("query_class", markdown)
        self.assertIn("answer_profile_used", markdown)
        self.assertIn("bm25_sql_latency_ms", markdown)
        self.assertIn("fts_latency_ms", markdown)
        self.assertIn("retrieval_round_wall_clock_ms", markdown)
        self.assertIn("retrieval_tool_timings", markdown)
        self.assertIn("grounded_answer", markdown)
        self.assertIn("Admission 分段", markdown)
        self.assertIn("Queue / Dispatch", markdown)
        self.assertIn("Build Provenance", markdown)
        self.assertIn("message_to_task_dequeued_ms", markdown)
        self.assertIn("answer_saved_to_response_ready_ms", markdown)

    def test_build_trace_artifact_marks_timeout_partial_and_keeps_direct_probe(self) -> None:
        module = _load_script_module()

        artifact = module.build_trace_artifact(
            preflight={"health": {"status": "ok", "rag_service": "ok"}},
            request_context={"ticket_id": "TK-TRACE-TP", "message": "how to join channel"},
            ack_payload={"ack_text": "Got it"},
            query_payload={"ticket_id": "TK-TRACE-TP", "queued_for_ai": True},
            ticket={"ticket_id": "TK-TRACE-TP", "messages": []},
            final_assistant=None,
            ticket_events=[],
            agent_events=[],
            rag_run=None,
            summary={"request": {"ticket_id": "TK-TRACE-TP"}, "final_result": {"answer": ""}},
            trace_status="timeout_partial",
            trace_completed=False,
            direct_probe={"status": "request_error", "error": "timed out"},
            query_error=None,
        )

        self.assertEqual(artifact["skill_runtime"]["trace_status"], "timeout_partial")
        self.assertFalse(artifact["skill_runtime"]["trace_completed"])
        self.assertEqual(artifact["direct_probe"]["status"], "request_error")

    def test_build_trace_artifact_marks_query_timeout_without_final_answer(self) -> None:
        module = _load_script_module()

        artifact = module.build_trace_artifact(
            preflight={"health": {"status": "ok", "rag_service": "ok"}},
            request_context={"ticket_id": "TK-TRACE-QT", "message": "black screen"},
            ack_payload={"ack_text": "Got it"},
            query_payload=None,
            ticket=None,
            final_assistant=None,
            ticket_events=[],
            agent_events=[],
            rag_run=None,
            summary={"request": {"ticket_id": "TK-TRACE-QT"}, "final_result": {"answer": ""}},
            trace_status="query_timeout",
            trace_completed=False,
            direct_probe={"status": "probe_timeout", "error": "timed out"},
            query_error="POST /api/tickets/query timed out",
        )

        self.assertEqual(artifact["skill_runtime"]["trace_status"], "query_timeout")
        self.assertEqual(artifact["query_error"], "POST /api/tickets/query timed out")
        self.assertEqual(artifact["direct_probe"]["status"], "probe_timeout")

    def test_wait_for_trace_completion_tolerates_snapshot_timeout_and_returns_partial(self) -> None:
        module = _load_script_module()

        fetch_calls = {"count": 0}

        def _fake_fetch(*_args, **_kwargs):
            fetch_calls["count"] += 1
            raise TimeoutError("timed out")

        with mock.patch.object(module, "fetch_trace_snapshot", side_effect=_fake_fetch):
            snapshot, final_assistant, completed = module.wait_for_trace_completion(
                base_url="http://127.0.0.1:8080",
                ticket_id="TK-TRACE-TIMEOUT",
                message="how to join channel",
                message_created_at=None,
                completion_timeout_seconds=0.3,
                poll_interval_seconds=0.05,
                event_limit=20,
            )

        self.assertIsNone(snapshot)
        self.assertIsNone(final_assistant)
        self.assertFalse(completed)
        self.assertGreater(fetch_calls["count"], 0)

    def test_wait_for_trace_artifacts_tolerates_snapshot_timeout_and_keeps_latest_snapshot(self) -> None:
        module = _load_script_module()

        latest_snapshot = {
            "ticket": {"ticket_id": "TK-TRACE-TIMEOUT", "messages": []},
            "ticket_events": [],
            "agent_events": [],
        }

        with mock.patch.object(module, "fetch_trace_snapshot", side_effect=TimeoutError("timed out")):
            snapshot = module.wait_for_trace_artifacts(
                base_url="http://127.0.0.1:8080",
                ticket_id="TK-TRACE-TIMEOUT",
                message_created_at=None,
                event_limit=20,
                timeout_seconds=0.2,
                poll_interval_seconds=0.05,
                latest_snapshot=latest_snapshot,
            )

        self.assertEqual(snapshot, latest_snapshot)

    def test_build_trace_summary_handles_missing_rag_telemetry(self) -> None:
        module = _load_script_module()

        summary = module.build_trace_summary(
            ticket={
                "ticket_id": "TK-TRACE-002",
                "customer_id": "C-TRACE-002",
                "product": "audio_video_calling",
                "client_agent_runtime_state": {
                    "active_run_id": "run-456",
                    "workflow_action": "answer_customer",
                    "status": "completed",
                },
                "messages": [
                    {
                        "role": "customer",
                        "content": "how to join channel",
                        "created_at": "2026-04-04T00:00:01+00:00",
                    },
                    {
                        "role": "assistant",
                        "content": "Use joinChannel with a valid token, channel name, and uid.",
                        "created_at": "2026-04-04T00:00:04+00:00",
                        "answer_route": "rag",
                        "route_reason": "grounded_answer",
                        "workflow_action": "answer_customer",
                    },
                ],
            },
            request_context={
                "ticket_id": "TK-TRACE-002",
                "customer_id": "C-TRACE-002",
                "product": "audio_video_calling",
                "message": "how to join channel",
                "message_created_at": "2026-04-04T00:00:01+00:00",
                "question_started_at": "2026-04-04T00:00:00+00:00",
                "ack_received_at": "2026-04-04T00:00:00.300000+00:00",
            },
            ack_payload={"ack_text": "Let me check this for you.", "latency_ms": 300.0},
            query_payload={"processing_mode": "main_agent_async", "queued_for_ai": True},
            ticket_events=[],
            agent_events=[
                {
                    "ticket_id": "TK-TRACE-002",
                    "message_id": "2026-04-04T00:00:01+00:00",
                    "run_id": "run-456",
                    "agent_name": "rag_agent",
                    "phase": "running",
                    "event_type": "started",
                    "payload": {
                        "request_id": "rag-456",
                        "created_at": "2026-04-04T00:00:01.030000+00:00",
                    },
                    "created_at": "2026-04-04T00:00:05+00:00",
                }
            ],
            rag_run=None,
        )

        self.assertEqual(summary["rag_internal_telemetry"]["status"], "missing")
        self.assertEqual(summary["raw_ids"]["request_id"], "rag-456")
        markdown = module.render_markdown_report(summary)
        self.assertIn("rag_internal_telemetry=missing", markdown)

    def test_build_trace_summary_uses_explicit_final_assistant_when_ticket_messages_are_omitted(self) -> None:
        module = _load_script_module()

        summary = module.build_trace_summary(
            ticket={
                "ticket_id": "TK-TRACE-EXPLICIT",
                "customer_id": "C-TRACE-EXPLICIT",
                "product": "audio_video_calling",
                "client_agent_runtime_state": {
                    "active_run_id": "run-explicit",
                    "workflow_action": "answer_customer",
                    "status": "completed",
                },
                "messages": [],
            },
            request_context={
                "ticket_id": "TK-TRACE-EXPLICIT",
                "customer_id": "C-TRACE-EXPLICIT",
                "product": "audio_video_calling",
                "message": "how to join channel",
                "message_created_at": "2026-04-04T00:00:01+00:00",
                "question_started_at": "2026-04-04T00:00:00+00:00",
                "ack_received_at": "2026-04-04T00:00:00.300000+00:00",
            },
            ack_payload={"ack_text": "Got it", "latency_ms": 300.0},
            query_payload={"processing_mode": "main_agent_async", "queued_for_ai": True},
            ticket_events=[],
            agent_events=[],
            rag_run=None,
            final_assistant={
                "role": "assistant",
                "content": "Use joinChannel with a valid token.",
                "created_at": "2026-04-04T00:00:04+00:00",
                "answer_route": "rag",
                "route_reason": "grounded_answer",
                "workflow_action": "answer_customer",
            },
        )

        self.assertEqual(summary["final_result"]["answer_route"], "rag")
        self.assertEqual(summary["final_result"]["route_reason"], "grounded_answer")
        self.assertIn("joinChannel", summary["final_result"]["answer"])

    def test_build_trace_summary_flags_incomplete_post_answer_artifacts_when_response_ready_is_missing(self) -> None:
        module = _load_script_module()

        summary = module.build_trace_summary(
            ticket={
                "ticket_id": "TK-TRACE-004",
                "customer_id": "C-TRACE-004",
                "product": "audio_video_calling",
                "client_agent_runtime_state": {
                    "active_run_id": "run-999",
                    "workflow_action": "answer_customer",
                    "status": "completed",
                    "main_agent": {
                        "status": "completed",
                        "phase": "completed",
                        "decision": "answer_customer",
                        "reason": "grounded_answer",
                        "started_at": "2026-04-04T00:00:01.010000+00:00",
                        "completed_at": "2026-04-04T00:00:05.010000+00:00",
                    },
                },
                "messages": [
                    {
                        "role": "customer",
                        "content": "how to join channel",
                        "created_at": "2026-04-04T00:00:01+00:00",
                    },
                    {
                        "role": "assistant",
                        "content": "Use joinChannel with a valid token, channel name, and uid.",
                        "created_at": "2026-04-04T00:00:05.100000+00:00",
                        "answer_route": "rag",
                        "route_reason": "grounded_answer",
                        "workflow_action": "answer_customer",
                    },
                ],
            },
            request_context={
                "ticket_id": "TK-TRACE-004",
                "customer_id": "C-TRACE-004",
                "product": "audio_video_calling",
                "message": "how to join channel",
                "message_created_at": "2026-04-04T00:00:01+00:00",
                "question_started_at": "2026-04-04T00:00:00+00:00",
                "ack_received_at": "2026-04-04T00:00:00.300000+00:00",
            },
            ack_payload={"ack_text": "Let me check this for you.", "latency_ms": 300.0},
            query_payload={"processing_mode": "main_agent_async", "queued_for_ai": True},
            ticket_events=[],
            agent_events=[],
            rag_run={
                "request_id": "rag-999",
                "query_class": "how_to_faq",
                "light_path_used": False,
                "vector_setup_skipped": False,
                "answer_profile_used": "gpt-5.4",
                "answer_profile_fallback_used": False,
            },
        )

        self.assertTrue(summary["post_answer_artifacts_incomplete"])
        self.assertEqual(summary["final_result"]["answer_route"], "rag")
        self.assertEqual(summary["final_result"]["route_reason"], "grounded_answer")
        self.assertIsNone(summary["worker_queue"]["queue_wait_ms"])
        markdown = module.render_markdown_report(summary)
        self.assertIn("post_answer_artifacts_incomplete", markdown)
        self.assertIn("true", markdown)

    def test_resolve_customer_message_created_at_falls_back_to_ticket_messages(self) -> None:
        module = _load_script_module()

        created_at = module._resolve_customer_message_created_at(
            {
                "messages": [
                    {
                        "role": "customer",
                        "content": "how to join channel",
                        "created_at": "2026-04-04T00:00:01+00:00",
                    }
                ]
            },
            message_created_at=None,
            message="how to join channel",
        )

        self.assertEqual(created_at, "2026-04-04T00:00:01+00:00")

    def test_build_trace_summary_prefers_runtime_snapshot_when_agent_events_are_partial(self) -> None:
        module = _load_script_module()

        summary = module.build_trace_summary(
            ticket={
                "ticket_id": "TK-TRACE-003",
                "customer_id": "C-TRACE-003",
                "product": "audio_video_calling",
                "client_agent_runtime_state": {
                    "active_run_id": "run-789",
                    "workflow_action": "answer_customer",
                    "status": "completed",
                    "main_agent": {
                        "status": "completed",
                        "phase": "completed",
                        "decision": "answer_customer",
                        "reason": "grounded_answer",
                        "started_at": "2026-04-04T00:00:01.010000+00:00",
                        "completed_at": "2026-04-04T00:00:10.010000+00:00",
                    },
                    "rag_agent": {
                        "status": "completed",
                        "phase": "completed",
                        "decision": "grounded_answer",
                        "reason": "grounded_answer",
                        "request_id": "rag-789",
                        "started_at": "2026-04-04T00:00:01.020000+00:00",
                        "completed_at": "2026-04-04T00:00:09.000000+00:00",
                    },
                },
                "messages": [
                    {
                        "role": "customer",
                        "content": "how to join channel",
                        "created_at": "2026-04-04T00:00:01+00:00",
                    },
                    {
                        "role": "assistant",
                        "content": "Use joinChannel with a valid token, channel name, and uid.",
                        "created_at": "2026-04-04T00:00:10.100000+00:00",
                        "answer_route": "rag",
                        "route_reason": "grounded_answer",
                        "workflow_action": "answer_customer",
                    },
                ],
            },
            request_context={
                "ticket_id": "TK-TRACE-003",
                "customer_id": "C-TRACE-003",
                "product": "audio_video_calling",
                "message": "how to join channel",
                "message_created_at": "2026-04-04T00:00:01+00:00",
                "question_started_at": "2026-04-04T00:00:00+00:00",
                "ack_received_at": "2026-04-04T00:00:00.300000+00:00",
            },
            ack_payload={"ack_text": "Let me check this for you.", "latency_ms": 300.0},
            query_payload={"processing_mode": "main_agent_async", "queued_for_ai": True},
            ticket_events=[],
            agent_events=[
                {
                    "ticket_id": "TK-TRACE-003",
                    "message_id": "2026-04-04T00:00:01+00:00",
                    "run_id": "run-789",
                    "agent_name": "main_agent",
                    "phase": "running",
                    "event_type": "started",
                    "payload": {"created_at": "2026-04-04T00:00:01.015000+00:00"},
                    "created_at": "2026-04-04T00:00:11+00:00",
                }
            ],
            rag_run=None,
        )

        self.assertEqual(summary["raw_ids"]["request_id"], "rag-789")
        self.assertEqual(summary["main_agent"]["ended_at"], "2026-04-04T00:00:10.010000+00:00")
        self.assertGreater(summary["main_agent"]["duration_ms"], 8000)


if __name__ == "__main__":
    unittest.main()
