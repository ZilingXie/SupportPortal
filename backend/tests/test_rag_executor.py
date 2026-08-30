from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from backend.services.rag_executor import (
    build_ragflow_worker_executor,
    build_sync_rag_executor,
    build_worker_rag_executor,
    normalize_rag_failure,
)
from backend.services.ragflow_docs_search_skill import RagflowDocsSearchError
from backend.services.rag_qa import INSUFFICIENT_EVIDENCE_REPLY
from backend.services.rag_service_client import (
    RagServiceError,
    RagTicketAnswerDetail,
    with_rag_detail_diagnostics,
)


class TestNormalizeRagFailure(unittest.TestCase):
    def test_transport_error_returns_rag_unavailable(self):
        error = RagServiceError("request failed", failure_kind="transport")
        result = normalize_rag_failure(error)
        self.assertEqual(result.reason, "rag_unavailable")
        self.assertTrue(result.needs_engineer_guidance)
        self.assertEqual(result.answer, INSUFFICIENT_EVIDENCE_REPLY)
        self.assertEqual(result.confidence, 0.0)

    def test_timeout_with_health_ok_returns_rag_processing_timeout(self):
        error = RagServiceError("timed out", failure_kind="timeout")
        result = normalize_rag_failure(error, timeout_health_status="ok")
        self.assertEqual(result.reason, "rag_processing_timeout")

    def test_timeout_with_health_unreachable_returns_rag_unavailable(self):
        error = RagServiceError("timed out", failure_kind="timeout")
        result = normalize_rag_failure(error, timeout_health_status="unreachable")
        self.assertEqual(result.reason, "rag_unavailable")

    def test_http_error_returns_rag_service_error(self):
        error = RagServiceError("server error", status_code=500)
        result = normalize_rag_failure(error)
        self.assertEqual(result.reason, "rag_service_error")

    def test_not_configured_message_returns_rag_unavailable(self):
        error = RagServiceError("RAG not configured")
        result = normalize_rag_failure(error)
        self.assertEqual(result.reason, "rag_unavailable")

    def test_diagnostics_are_attached(self):
        error = RagServiceError("request failed", failure_kind="transport")
        result = normalize_rag_failure(error, timeout_health_status="ok")
        evidence = result.evidence_summary or {}
        diagnostics = evidence.get("diagnostics") if isinstance(evidence, dict) else {}
        self.assertEqual(diagnostics.get("rag_failure_kind"), "transport")
        self.assertFalse(diagnostics.get("rag_recovered_from_live_detail"))


class TestBuildSyncRagExecutor(unittest.TestCase):
    def test_success_returns_detail(self):
        client = MagicMock()
        expected = RagTicketAnswerDetail(
            answer="Hello",
            confidence=0.9,
            sources=["src"],
            citations=[{"chunk_id": "c1", "source_path": "p", "heading": "h", "source_url": "u"}],
            needs_engineer_guidance=False,
            reason="grounded_answer",
        )
        client.query_answer_with_recovery_detail.return_value = expected
        executor = build_sync_rag_executor(client)
        result = executor(message="hi", ticket_id="T1")
        self.assertIs(result, expected)

    def test_sync_client_executor_forces_official_only_access(self):
        client = MagicMock()
        client.query_answer_with_recovery_detail.return_value = RagTicketAnswerDetail(
            answer="Hello",
            confidence=0.9,
            sources=["src"],
            citations=[],
            needs_engineer_guidance=False,
            reason="grounded_answer",
        )
        executor = build_sync_rag_executor(client)

        executor(message="hi", ticket_id="T1")

        self.assertEqual(
            client.query_answer_with_recovery_detail.call_args.kwargs["rag_access_mode"],
            "official_only",
        )

    def test_transport_error_returns_fallback(self):
        client = MagicMock()
        client.query_answer_with_recovery_detail.side_effect = RagServiceError(
            "request failed", failure_kind="transport"
        )
        executor = build_sync_rag_executor(client)
        result = executor(message="hi", ticket_id="T1")
        self.assertEqual(result.reason, "rag_unavailable")
        self.assertTrue(result.needs_engineer_guidance)

    def test_timeout_error_checks_health(self):
        client = MagicMock()
        client.query_answer_with_recovery_detail.side_effect = RagServiceError(
            "timed out", failure_kind="timeout"
        )
        client.health.return_value = {"status": "ok"}
        executor = build_sync_rag_executor(client)
        result = executor(message="hi", ticket_id="T1")
        self.assertEqual(result.reason, "rag_processing_timeout")
        client.health.assert_called_once()

    def test_timeout_error_health_fails_returns_rag_unavailable(self):
        client = MagicMock()
        client.query_answer_with_recovery_detail.side_effect = RagServiceError(
            "timed out", failure_kind="timeout"
        )
        client.health.side_effect = RagServiceError("unreachable")
        executor = build_sync_rag_executor(client)
        result = executor(message="hi", ticket_id="T1")
        self.assertEqual(result.reason, "rag_unavailable")


class TestBuildWorkerRagExecutor(unittest.TestCase):
    def _client(self):
        return MagicMock()

    def test_success_returns_detail_with_worker_diagnostics(self):
        client = self._client()
        expected = RagTicketAnswerDetail(
            answer="Hello",
            confidence=0.9,
            sources=["src"],
            citations=[{"chunk_id": "c1", "source_path": "p", "heading": "h", "source_url": "u"}],
            needs_engineer_guidance=False,
            reason="grounded_answer",
        )
        client.query_answer_with_recovery_detail.return_value = expected
        executor = build_worker_rag_executor(
            client,
            timeout_seconds=30.0,
            max_wait_seconds=60.0,
            recovery_window_seconds=15.0,
        )
        result = executor(message="hi", ticket_id="T1", request_id="r1")
        self.assertIsNot(result, expected)
        self.assertEqual(result.answer, expected.answer)
        evidence = result.evidence_summary or {}
        diagnostics = evidence.get("diagnostics") if isinstance(evidence, dict) else {}
        self.assertEqual(diagnostics.get("rag_timeout_seconds"), 30.0)
        self.assertEqual(diagnostics.get("rag_max_wait_seconds"), 60.0)

    def test_timeout_with_health_ok_returns_rag_processing_timeout(self):
        client = self._client()
        client.query_answer_with_recovery_detail.side_effect = RagServiceError(
            "timed out", failure_kind="timeout"
        )
        client.health.return_value = {"status": "ok"}
        executor = build_worker_rag_executor(client, timeout_seconds=30.0)
        result = executor(message="hi", ticket_id="T1", request_id="r1")
        self.assertEqual(result.reason, "rag_processing_timeout")

    def test_timeout_with_health_unreachable_returns_rag_unavailable(self):
        client = self._client()
        client.query_answer_with_recovery_detail.side_effect = RagServiceError(
            "timed out", failure_kind="timeout"
        )
        client.health.side_effect = RagServiceError("unreachable")
        executor = build_worker_rag_executor(client, timeout_seconds=30.0)
        result = executor(message="hi", ticket_id="T1", request_id="r1")
        self.assertEqual(result.reason, "rag_unavailable")

    def test_cancelled_by_route_flip_propagates(self):
        client = self._client()
        client.query_answer_with_recovery_detail.side_effect = RagServiceError(
            "cancelled",
            status_code=409,
            payload={"reason": "cancelled_by_route_flip", "stage": "rag_running"},
        )
        executor = build_worker_rag_executor(client, timeout_seconds=30.0)
        with self.assertRaises(RagServiceError) as ctx:
            executor(message="hi", ticket_id="T1", request_id="r1")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_http_error_returns_fallback_with_diagnostics(self):
        client = self._client()
        client.query_answer_with_recovery_detail.side_effect = RagServiceError(
            "server error", status_code=500
        )
        executor = build_worker_rag_executor(client, timeout_seconds=30.0)
        result = executor(message="hi", ticket_id="T1", request_id="r1")
        self.assertEqual(result.reason, "rag_service_error")
        evidence = result.evidence_summary or {}
        diagnostics = evidence.get("diagnostics") if isinstance(evidence, dict) else {}
        self.assertEqual(diagnostics.get("rag_failure_kind"), "http")


class TestBuildRagflowWorkerExecutor(unittest.TestCase):
    def test_grounded_answer_maps_sources_and_provider_diagnostics(self):
        client = MagicMock()
        client.query.return_value = {
            "decision": "answer",
            "answer": "Use a project App ID.",
            "citations": [
                {"source_url": "https://docs.agora.io/en/get-started/manage-agora-account"},
                {"source_url": "https://docs.agora.io/en/get-started/manage-agora-account"},
            ],
        }
        executor = build_ragflow_worker_executor(client, timeout_seconds=90.0)

        result = executor(
            message="What is an App ID?",
            request_id="ragflow-1",
            ticket_id="T1",
            customer_id="C1",
            ticket_context=[{"role": "customer", "content": "I am creating a project."}],
        )

        self.assertEqual(result.reason, "grounded_answer")
        self.assertFalse(result.needs_engineer_guidance)
        self.assertEqual(
            result.sources,
            ["https://docs.agora.io/en/get-started/manage-agora-account"],
        )
        self.assertEqual(len(result.citations), 2)
        self.assertEqual(result.evidence_summary["diagnostics"]["rag_provider"], "ragflow_docs_search")
        self.assertEqual(client.query.call_args.kwargs["timeout_seconds"], 90.0)

    def test_insufficient_evidence_preserves_escalation_reason(self):
        client = MagicMock()
        client.query.return_value = {
            "decision": "escalate",
            "answer": "",
            "reason": "insufficient_evidence",
        }
        result = build_ragflow_worker_executor(client, timeout_seconds=90.0)(message="unknown")

        self.assertEqual(result.reason, "insufficient_evidence")
        self.assertTrue(result.needs_engineer_guidance)
        self.assertEqual(result.answer, INSUFFICIENT_EVIDENCE_REPLY)

    def test_timeout_maps_to_processing_timeout_and_preserves_failure_kind(self):
        client = MagicMock()
        client.query.side_effect = RagflowDocsSearchError("timeout")
        result = build_ragflow_worker_executor(client, timeout_seconds=90.0)(message="question")

        self.assertEqual(result.reason, "rag_processing_timeout")
        self.assertTrue(result.needs_engineer_guidance)
        self.assertEqual(result.evidence_summary["diagnostics"]["rag_failure_kind"], "timeout")

    def test_other_failures_fail_closed_with_stable_diagnostics(self):
        for failure_kind in (
            "configuration",
            "authentication",
            "access",
            "execution",
            "search",
            "generation",
            "invalid_search_response",
            "invalid_generation_response",
        ):
            with self.subTest(failure_kind=failure_kind):
                client = MagicMock()
                client.query.side_effect = RagflowDocsSearchError(failure_kind)
                result = build_ragflow_worker_executor(client, timeout_seconds=90.0)(message="question")
                self.assertEqual(result.reason, "rag_unavailable")
                self.assertTrue(result.needs_engineer_guidance)
                self.assertEqual(
                    result.evidence_summary["diagnostics"]["rag_failure_kind"],
                    failure_kind,
                )

    def test_non_mapping_response_fails_closed(self):
        client = MagicMock()
        client.query.return_value = []
        result = build_ragflow_worker_executor(client, timeout_seconds=90.0)(message="question")

        self.assertEqual(result.reason, "rag_unavailable")
        self.assertEqual(result.evidence_summary["diagnostics"]["rag_failure_kind"], "invalid_response")

    def test_unexpected_client_failure_fails_closed_without_error_text(self):
        client = MagicMock()
        client.query.side_effect = RuntimeError("sensitive upstream detail")

        with self.assertLogs("backend.services.rag_executor", level="WARNING") as logs:
            result = build_ragflow_worker_executor(client, timeout_seconds=90.0)(message="question")

        self.assertEqual(result.reason, "rag_unavailable")
        self.assertEqual(
            result.evidence_summary["diagnostics"]["rag_failure_kind"],
            "unexpected_RuntimeError",
        )
        self.assertNotIn("sensitive upstream detail", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
