from __future__ import annotations

import logging
import os
import threading
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

os.environ.setdefault("PGVECTOR_DSN", "postgresql://example.invalid/supportportal")
os.environ.setdefault("PGVECTOR_DIM", "1024")
os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/supportportal")
os.environ.setdefault("RAG_SERVICE_SHARED_TOKEN", "test-token")

import backend.rag_api as rag_api
from backend.repositories.event_repository import InMemoryEventRepository
from backend.services.rag_qa import RagAnswer, RagKnowledgeIndexReadiness, RagQueryResult, RagQueryTrace


class _TrackingKnowledgeRepository:
    def __init__(self, *, telemetry_error: BaseException | None = None) -> None:
        self.telemetry_error = telemetry_error
        self.initialize_calls = 0
        self.recorded_runs: list[dict[str, object]] = []

    def initialize(self) -> None:
        self.initialize_calls += 1

    def is_enabled(self) -> bool:
        return True

    def storage_mode(self) -> str:
        return "postgres"

    def record_rag_query_run(self, *, run: dict[str, object], candidates: list[dict[str, object]]) -> None:
        self.recorded_runs.append({"run": dict(run), "candidates": [dict(item) for item in candidates]})
        if self.telemetry_error is not None:
            raise self.telemetry_error


class _BlockingKnowledgeRepository(_TrackingKnowledgeRepository):
    def __init__(self, gate: threading.Event, *, telemetry_error: BaseException | None = None) -> None:
        super().__init__(telemetry_error=telemetry_error)
        self.gate = gate

    def record_rag_query_run(self, *, run: dict[str, object], candidates: list[dict[str, object]]) -> None:
        self.recorded_runs.append({"run": dict(run), "candidates": [dict(item) for item in candidates]})
        self.gate.wait(timeout=0.5)
        if self.telemetry_error is not None:
            raise self.telemetry_error


def _trace(
    *,
    needs_human: bool = False,
    handoff_reason: str | None = None,
    generation_mode: str = "structured_answer",
    extractive_fallback_used: bool = False,
) -> RagQueryTrace:
    return RagQueryTrace(
        query_type="how_to",
        retrieval_strategy="agentic_multi_tool_v1",
        vector_candidates_count=2,
        bm25_candidates_count=1,
        reranked_candidates_count=2,
        retrieved_chunk_ids=["chunk-1", "chunk-2"],
        selected_chunk_ids=["chunk-1"],
        vector_retrieval_latency_ms=12.0,
        bm25_retrieval_latency_ms=4.0,
        retrieval_latency_ms=20.0,
        rerank_latency_ms=8.0,
        generation_latency_ms=35.0,
        total_latency_ms=63.0,
        bm25_sql_latency_ms=11.0,
        fts_latency_ms=7.0,
        retrieval_round_wall_clock_ms=14.0,
        prompt_tokens=120,
        completion_tokens=40,
        embedding_tokens=12,
        embedding_provider="siliconflow",
        embedding_model="BAAI/bge-m3",
        embedding_dimensions=1024,
        embedding_request_meta=[],
        model_name="openai:gpt-5.4",
        answer_length=72,
        citation_count=1,
        cited_chunk_ids=["chunk-1"],
        needs_human=needs_human,
        handoff_reason=handoff_reason,
        confidence_score=0.92,
        primary_source_type="official",
        primary_chunk_strategy="markdown_header_v1",
        generation_mode=generation_mode,
        extractive_fallback_used=extractive_fallback_used,
        selected_doc_count=1,
        top1_similarity_score=0.98,
        avg_selected_similarity_score=0.98,
        citation_coverage_ratio=1.0,
        retrieval_candidates=[
            {
                "chunk_id": "chunk-1",
                "source_path": "official/get-started.md",
                "source_url": "https://docs.agora.io/en/video-calling/get-started",
            }
        ],
        retrieval_tool_timings=[
            {
                "tool_name": "p_bm25",
                "query_kind": "original",
                "round_index": 1,
                "index_role": "primary",
                "latency_ms": 11.0,
                "candidate_count": 3,
                "used_seed_tool": False,
            },
            {
                "tool_name": "p_fts",
                "query_kind": "original",
                "round_index": 1,
                "index_role": "primary",
                "latency_ms": 7.0,
                "candidate_count": 3,
                "used_seed_tool": False,
            },
        ],
        selected_contexts=[
            {
                "chunk_id": "chunk-1",
                "heading": "Join a channel",
                "source_path": "official/get-started.md",
                "source_url": "https://docs.agora.io/en/video-calling/get-started",
                "text_excerpt": "Call joinChannel with the same channel name and token.",
                "similarity": 0.98,
                "cited_in_answer": True,
            }
        ],
    )


def _answer_result() -> RagQueryResult:
    return RagQueryResult(
        answer=RagAnswer(
            answer="Call joinChannel with the same channel name and token on each client.",
            confidence=0.92,
            sources=["https://docs.agora.io/en/video-calling/get-started"],
            citations=[
                {
                    "chunk_id": "chunk-1",
                    "source_path": "official/get-started.md",
                    "heading": "Join a channel",
                    "source_url": "https://docs.agora.io/en/video-calling/get-started",
                }
            ],
        ),
        trace=_trace(),
    )


class RagApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_knowledge_repository = rag_api.knowledge_repository
        self.original_event_repository = rag_api.event_repository
        self.original_shared_token = os.environ.get("RAG_SERVICE_SHARED_TOKEN")
        os.environ["RAG_SERVICE_SHARED_TOKEN"] = "test-token"

    def tearDown(self) -> None:
        rag_api.knowledge_repository = self.original_knowledge_repository
        rag_api.event_repository = self.original_event_repository
        if self.original_shared_token is None:
            os.environ.pop("RAG_SERVICE_SHARED_TOKEN", None)
        else:
            os.environ["RAG_SERVICE_SHARED_TOKEN"] = self.original_shared_token

    def _client(self, repository: _TrackingKnowledgeRepository) -> TestClient:
        rag_api.knowledge_repository = repository
        event_repository = InMemoryEventRepository()
        event_repository.initialize()
        rag_api.event_repository = event_repository
        return TestClient(rag_api.app)

    def test_internal_rag_query_returns_answer_without_waiting_for_async_telemetry_persistence(self) -> None:
        gate = threading.Event()
        repository = _BlockingKnowledgeRepository(gate)

        with self._client(repository) as client, patch.object(
            rag_api,
            "probe_customer_rag_index_readiness",
            return_value=RagKnowledgeIndexReadiness(
                status="ready",
                configured_table="supportportal.docagent_chunks_bge_m3_1024",
                resolved_table="supportportal.docagent_chunks_bge_m3_1024",
                configured_primary_rows=123,
            ),
        ), patch.object(
            rag_api,
            "run_rag_query",
            return_value=_answer_result(),
        ):
            started_at = time.perf_counter()
            try:
                response = client.post(
                    "/internal/rag/query",
                    headers={"Authorization": "Bearer test-token"},
                    json={
                        "question": "how to join channel",
                        "request_id": "rag-api-telemetry-1",
                        "ticket_id": "TK-001",
                        "customer_id": "C-001",
                    },
                )
            finally:
                gate.set()
            elapsed_ms = (time.perf_counter() - started_at) * 1000

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["decision"], "answer")
        self.assertEqual(
            payload["evidence_summary"]["diagnostics"]["telemetry_mode"],
            "async_best_effort",
        )
        self.assertLess(elapsed_ms, 250.0, elapsed_ms)

    def test_trace_query_understanding_meta_exposes_lexical_retrieval_breakdown(self) -> None:
        meta = rag_api._trace_query_understanding_meta(_trace())

        self.assertEqual(meta["bm25_sql_latency_ms"], 11.0)
        self.assertEqual(meta["fts_latency_ms"], 7.0)
        self.assertEqual(meta["retrieval_round_wall_clock_ms"], 14.0)
        self.assertEqual(len(meta["retrieval_tool_timings"]), 2)
        self.assertEqual(meta["retrieval_tool_timings"][0]["tool_name"], "p_bm25")

    def test_record_rag_query_run_best_effort_returns_enqueue_failure_diagnostics_when_queue_full(self) -> None:
        repository = _TrackingKnowledgeRepository()

        with patch.object(rag_api, "knowledge_repository", repository), patch.object(
            rag_api,
            "_RAG_QUERY_TELEMETRY_SEMAPHORE",
            Mock(acquire=Mock(return_value=False)),
            create=True,
        ), patch.object(
            rag_api,
            "_RAG_QUERY_TELEMETRY_EXECUTOR",
            Mock(),
            create=True,
        ):
            diagnostics = rag_api._record_rag_query_run_best_effort(
                request_id="rag-api-telemetry-queue-full",
                ticket_id="TK-002",
                run={
                    "request_id": "rag-api-telemetry-queue-full",
                    "ticket_id": "TK-002",
                    "created_at": "2026-04-04T00:00:00Z",
                },
                candidates=[],
            )

        self.assertEqual(
            diagnostics,
            {
                "telemetry_mode": "async_best_effort",
                "telemetry_enqueue_failed": True,
            },
        )
        self.assertEqual(repository.recorded_runs, [])

    def test_internal_rag_cancel_marks_inflight_request_cancelled(self) -> None:
        repository = _TrackingKnowledgeRepository()
        with self._client(repository) as client:
            rag_api._register_inflight_rag_request("rag-cancel-api-1")
            try:
                response = client.post(
                    "/internal/rag/requests/rag-cancel-api-1/cancel",
                    headers={"Authorization": "Bearer test-token"},
                )
            finally:
                rag_api._cleanup_inflight_rag_request("rag-cancel-api-1")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["cancelled"])
        self.assertEqual(payload["request_id"], "rag-cancel-api-1")

    def test_internal_rag_query_forwards_selected_product_to_rag_engine(self) -> None:
        repository = _TrackingKnowledgeRepository()

        with self._client(repository) as client, patch.object(
            rag_api,
            "run_rag_query",
            return_value=_answer_result(),
        ) as run_mock:
            response = client.post(
                "/internal/rag/query",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "question": "how to join channel",
                    "request_id": "rag-api-product-1",
                    "ticket_id": "TK-003",
                    "customer_id": "C-003",
                    "product": "cloud_recording",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        run_mock.assert_called_once()
        args, kwargs = run_mock.call_args
        self.assertEqual(args, ("how to join channel",))
        self.assertEqual(kwargs["top_k"], 6)
        self.assertIsNone(kwargs["ticket_context"])
        self.assertEqual(kwargs["ticket_id"], "TK-003")
        self.assertEqual(kwargs["customer_id"], "C-003")
        self.assertEqual(kwargs["product"], "cloud_recording")
        self.assertTrue(callable(kwargs["should_cancel"]))
        self.assertTrue(callable(kwargs["record_cancel_stage"]))

    def test_internal_rag_query_fail_closes_extractive_fallback_result(self) -> None:
        repository = _TrackingKnowledgeRepository()
        fallback_result = RagQueryResult(
            answer=RagAnswer(
                answer="I found relevant support evidence, but I could not verify a complete grounded answer.",
                confidence=0.55,
                sources=[],
                citations=[],
            ),
            trace=_trace(
                needs_human=True,
                handoff_reason="insufficient_evidence",
                generation_mode="extractive_fallback",
                extractive_fallback_used=True,
            ),
        )

        with self._client(repository) as client, patch.object(
            rag_api,
            "probe_customer_rag_index_readiness",
            return_value=RagKnowledgeIndexReadiness(
                status="ready",
                configured_table="supportportal.docagent_chunks_bge_m3_1024",
                resolved_table="supportportal.docagent_chunks_bge_m3_1024",
                configured_primary_rows=123,
            ),
        ), patch.object(
            rag_api,
            "run_rag_query",
            return_value=fallback_result,
        ):
            response = client.post(
                "/internal/rag/query",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "question": "how to join channel",
                    "request_id": "rag-api-fallback-1",
                    "ticket_id": "TK-004",
                    "customer_id": "C-004",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["decision"], "escalate")
        self.assertEqual(payload["answer"], "")
        self.assertEqual(payload["reason"], "insufficient_evidence")
        self.assertEqual(payload["evidence_summary"]["quality_signals"]["generation_mode"], "extractive_fallback")
        self.assertTrue(payload["evidence_summary"]["quality_signals"]["needs_human"])

    def test_internal_rag_query_returns_rag_unavailable_when_knowledge_index_guard_trips(self) -> None:
        repository = _TrackingKnowledgeRepository()

        with self._client(repository) as client, patch.object(
            rag_api,
            "probe_customer_rag_index_readiness",
            return_value=RagKnowledgeIndexReadiness(
                status="configured_table_empty",
                configured_table="supportportal.docagent_chunks_bge_m3_1024",
                resolved_table="supportportal.docagent_chunks_bge_m3_1024",
                configured_primary_rows=0,
            ),
        ), patch.object(
            rag_api,
            "run_rag_query",
            side_effect=AssertionError("run_rag_query should not be called when the knowledge index is unavailable"),
        ):
            response = client.post(
                "/internal/rag/query",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "question": "how to join channel",
                    "request_id": "rag-api-guard-1",
                    "ticket_id": "TK-005",
                    "customer_id": "C-005",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["decision"], "escalate")
        self.assertEqual(payload["reason"], "rag_unavailable")
        self.assertEqual(
            payload["evidence_summary"]["diagnostics"]["knowledge_index_status"],
            "configured_table_empty",
        )
        self.assertEqual(
            payload["evidence_summary"]["diagnostics"]["configured_vector_table"],
            "supportportal.docagent_chunks_bge_m3_1024",
        )


if __name__ == "__main__":
    unittest.main()
