from __future__ import annotations

import logging
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.setdefault("PGVECTOR_DSN", "postgresql://example.invalid/supportportal")
os.environ.setdefault("PGVECTOR_DIM", "1024")
os.environ.setdefault("RAG_SERVICE_SHARED_TOKEN", "test-token")

import backend.rag_api as rag_api
from backend.repositories.event_repository import InMemoryEventRepository
from backend.services.rag_qa import RagAnswer, RagQueryResult, RagQueryTrace


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


def _trace(*, needs_human: bool = False, handoff_reason: str | None = None) -> RagQueryTrace:
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
        generation_mode="structured_answer",
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

    def test_internal_rag_query_returns_answer_when_telemetry_persistence_fails(self) -> None:
        repository = _TrackingKnowledgeRepository(telemetry_error=RuntimeError("missing usage_ledger column"))

        with self._client(repository) as client, patch.object(
            rag_api,
            "run_rag_query",
            return_value=_answer_result(),
        ), self.assertLogs("backend.rag_api", level="WARNING") as logs:
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

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["decision"], "answer")
        self.assertEqual(
            payload["answer"],
            "Call joinChannel with the same channel name and token on each client.",
        )
        self.assertEqual(len(repository.recorded_runs), 1)
        self.assertTrue(
            any("RAG telemetry persistence failed" in message for message in logs.output),
            logs.output,
        )

    def test_internal_rag_query_returns_service_error_when_rag_execution_and_telemetry_both_fail(self) -> None:
        repository = _TrackingKnowledgeRepository(telemetry_error=RuntimeError("missing usage_summary column"))

        with self._client(repository) as client, patch.object(
            rag_api,
            "run_rag_query",
            side_effect=RuntimeError("rag engine crashed"),
        ), self.assertLogs("backend.rag_api", level="WARNING") as logs:
            response = client.post(
                "/internal/rag/query",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "question": "how to join channel",
                    "request_id": "rag-api-telemetry-2",
                    "ticket_id": "TK-002",
                    "customer_id": "C-002",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["decision"], "escalate")
        self.assertEqual(payload["reason"], "rag_service_error")
        self.assertEqual(len(repository.recorded_runs), 1)
        self.assertTrue(
            any("operation=record_rag_query_run" in message for message in logs.output),
            logs.output,
        )


if __name__ == "__main__":
    unittest.main()
