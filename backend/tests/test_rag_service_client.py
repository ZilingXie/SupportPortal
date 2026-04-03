from __future__ import annotations

import io
import json
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch

from backend.services.rag_service_client import (
    RagServiceClient,
    RagServiceError,
    map_rag_payload_to_ticket_answer,
    map_rag_payload_to_ticket_answer_detail,
)


class RagServiceClientTests(unittest.TestCase):
    def test_map_answer_payload_to_ticket_answer_detail_preserves_evidence_summary(self) -> None:
        detail = map_rag_payload_to_ticket_answer_detail(
            {
                "decision": "answer",
                "answer": "Use the REST API endpoint.",
                "confidence": 0.88,
                "sources": ["https://docs.agora.io/en/example"],
                "citations": [
                    {
                        "chunk_id": "chunk-1",
                        "source_path": "official/agora.md",
                        "heading": "API",
                    }
                ],
                "evidence_summary": {
                    "quality_signals": {
                        "generation_mode": "structured_answer",
                        "selected_doc_count": 1,
                        "citation_coverage_ratio": 1.0,
                        "top1_similarity_score": 0.96,
                        "avg_selected_similarity_score": 0.96,
                        "handoff_reason": None,
                        "needs_human": False,
                    },
                    "selected_contexts": [
                        {
                            "chunk_id": "chunk-1",
                            "heading": "API",
                            "source_path": "official/agora.md",
                            "source_url": "https://docs.agora.io/en/example",
                            "text_excerpt": "Use the REST API endpoint.",
                            "similarity": 0.96,
                            "cited_in_answer": True,
                        }
                    ],
                },
            },
            insufficient_reply="INSUFFICIENT",
        )

        self.assertEqual(detail.answer, "Use the REST API endpoint.")
        self.assertFalse(detail.needs_engineer_guidance)
        self.assertEqual(detail.reason, "grounded_answer")
        self.assertEqual(
            detail.evidence_summary["selected_contexts"][0]["text_excerpt"],
            "Use the REST API endpoint.",
        )

    def test_map_answer_payload_to_ticket_answer(self) -> None:
        answer, confidence, sources, citations, needs_engineer = map_rag_payload_to_ticket_answer(
            {
                "decision": "answer",
                "answer": "Use the REST API endpoint.",
                "confidence": 0.88,
                "sources": ["https://docs.agora.io/en/example"],
                "citations": [
                    {
                        "chunk_id": "chunk-1",
                        "source_path": "official/agora.md",
                        "heading": "API",
                    }
                ],
            },
            insufficient_reply="INSUFFICIENT",
        )
        self.assertEqual(answer, "Use the REST API endpoint.")
        self.assertEqual(confidence, 0.88)
        self.assertEqual(sources, ["https://docs.agora.io/en/example"])
        self.assertEqual(citations[0]["chunk_id"], "chunk-1")
        self.assertFalse(needs_engineer)

    def test_legacy_tuple_mapping_ignores_evidence_summary(self) -> None:
        answer, confidence, sources, citations, needs_engineer = map_rag_payload_to_ticket_answer(
            {
                "decision": "answer",
                "answer": "Use the REST API endpoint.",
                "confidence": 0.88,
                "sources": ["https://docs.agora.io/en/example"],
                "citations": [{"chunk_id": "chunk-1"}],
                "evidence_summary": {
                    "quality_signals": {
                        "generation_mode": "structured_answer",
                        "selected_doc_count": 1,
                    },
                    "selected_contexts": [{"chunk_id": "chunk-1"}],
                },
            },
            insufficient_reply="INSUFFICIENT",
        )

        self.assertEqual(answer, "Use the REST API endpoint.")
        self.assertEqual(confidence, 0.88)
        self.assertEqual(sources, ["https://docs.agora.io/en/example"])
        self.assertEqual(citations, [{"chunk_id": "chunk-1"}])
        self.assertFalse(needs_engineer)

    def test_map_escalate_payload_to_ticket_answer(self) -> None:
        answer, confidence, sources, citations, needs_engineer = map_rag_payload_to_ticket_answer(
            {
                "decision": "escalate",
                "confidence": 0.31,
                "reason": "insufficient_evidence",
            },
            insufficient_reply="INSUFFICIENT",
        )
        self.assertEqual(answer, "INSUFFICIENT")
        self.assertEqual(confidence, 0.31)
        self.assertEqual(sources, [])
        self.assertEqual(citations, [])
        self.assertTrue(needs_engineer)

    def test_probe_health_returns_disabled_without_base_url(self) -> None:
        client = RagServiceClient(base_url="")
        payload = client.probe_health()
        self.assertEqual(payload["status"], "disabled")
        self.assertEqual(payload["knowledge_storage"], "disabled")

    def test_request_raises_rag_service_error_for_timeout(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
            with self.assertRaises(RagServiceError) as ctx:
                client.query(
                    question="How do I join a channel?",
                    request_id="rag-1",
                    ticket_id="T-001",
                    customer_id="C-001",
                )
        self.assertIsNone(ctx.exception.status_code)

    def test_request_raises_rag_service_error_for_http_500(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        error = urllib.error.HTTPError(
            url="http://rag-api.internal/internal/rag/query",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(b'{"detail":"boom"}'),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(RagServiceError) as ctx:
                client.query(
                    question="How do I join a channel?",
                    request_id="rag-2",
                    ticket_id="T-001",
                    customer_id="C-001",
                )
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.payload, {"detail": "boom"})

    def test_query_includes_ticket_context_in_json_payload(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        captured: dict[str, object] = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"decision":"answer","answer":"ok","confidence":0.8,"sources":[],"citations":[]}'

        def _fake_urlopen(request, timeout):
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            payload = client.query(
                question="What does error 109 mean?",
                request_id="rag-ctx-1",
                ticket_id="T-001",
                customer_id="C-001",
                ticket_context=[
                    {"role": "customer", "content": "We only see this on iOS 4.6.0"},
                    {"role": "assistant", "content": "Investigating."},
                ],
            )

        self.assertEqual(payload["decision"], "answer")
        self.assertEqual(
            captured["body"],
            {
                "question": "What does error 109 mean?",
                "request_id": "rag-ctx-1",
                "ticket_id": "T-001",
                "customer_id": "C-001",
                "ticket_context": [
                    {"role": "customer", "content": "We only see this on iOS 4.6.0"},
                    {"role": "assistant", "content": "Investigating."},
                ],
            },
        )

    def test_query_answer_with_recovery_detail_forwards_ticket_context(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")

        with patch.object(
            client,
            "query",
            return_value={"decision": "answer", "answer": "ok", "confidence": 0.8, "sources": [], "citations": []},
        ) as query_mock:
            detail = client.query_answer_with_recovery_detail(
                question="What does error 109 mean?",
                request_id="rag-ctx-2",
                ticket_id="T-001",
                customer_id="C-001",
                ticket_context=[{"role": "customer", "content": "We only see this on iOS 4.6.0"}],
                insufficient_reply="INSUFFICIENT",
            )

        self.assertEqual(detail.answer, "ok")
        query_mock.assert_called_once_with(
            question="What does error 109 mean?",
            request_id="rag-ctx-2",
            ticket_id="T-001",
            customer_id="C-001",
            ticket_context=[{"role": "customer", "content": "We only see this on iOS 4.6.0"}],
            top_k=None,
        )

    def test_get_ingestion_report_uses_report_endpoint(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        captured = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"summary":{"ingestion_id":"KI-REPORT"}}'

        def _fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["authorization"] = request.headers.get("Authorization")
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            payload = client.get_ingestion_report("KI-REPORT")

        self.assertEqual(
            captured["url"],
            "http://rag-api.internal/internal/knowledge/ingestions/KI-REPORT/report",
        )
        self.assertEqual(captured["authorization"], "Bearer token")
        self.assertEqual(payload["summary"]["ingestion_id"], "KI-REPORT")

    def test_rag_dashboard_page_uses_internal_dashboard_endpoint(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        captured = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"cards":{"doc_count_total":12}}'

        def _fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers.get("Authorization")
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            payload = client.rag_dashboard_page(
                "overview",
                range_value="30d",
                filters={"source_type": "technical_article_api", "limit": 25},
            )

        parsed = urllib.parse.urlparse(captured["url"])
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/internal/dashboard/rag/overview")
        self.assertEqual(query["range"], ["30d"])
        self.assertEqual(query["source_type"], ["technical_article_api"])
        self.assertEqual(query["limit"], ["25"])
        self.assertEqual(captured["authorization"], "Bearer token")
        self.assertEqual(payload["cards"]["doc_count_total"], 12)

    def test_rag_dashboard_page_supports_diagnosis_and_experiment_filters(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        captured = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"layout":"diagnosis","sections":{"summary":{}}}'

        def _fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers.get("Authorization")
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            payload = client.rag_dashboard_page(
                "diagnosis",
                range_value="30d",
                filters={
                    "sample_id": "RS-1",
                    "baseline_experiment_id": "exp-baseline",
                    "candidate_experiment_id": "exp-candidate",
                    "product": "video-calling",
                    "language": "en",
                    "experiment_id": "exp-candidate",
                    "limit": 10,
                },
            )

        parsed = urllib.parse.urlparse(captured["url"])
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/internal/dashboard/rag/diagnosis")
        self.assertEqual(query["range"], ["30d"])
        self.assertEqual(query["sample_id"], ["RS-1"])
        self.assertEqual(query["baseline_experiment_id"], ["exp-baseline"])
        self.assertEqual(query["candidate_experiment_id"], ["exp-candidate"])
        self.assertEqual(query["product"], ["video-calling"])
        self.assertEqual(query["language"], ["en"])
        self.assertEqual(query["experiment_id"], ["exp-candidate"])
        self.assertEqual(captured["authorization"], "Bearer token")
        self.assertEqual(payload["layout"], "diagnosis")

    def test_benchmark_case_detail_uses_internal_dashboard_endpoint(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        captured = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"primary":{"eval_run_id":"run-1","test_case_id":"case-1"},"baseline":null,"deltas":{}}'

        def _fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers.get("Authorization")
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            payload = client.rag_dashboard_benchmark_case_detail(
                "run-1",
                "case-1",
                baseline_eval_run_id="run-0",
            )

        parsed = urllib.parse.urlparse(captured["url"])
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/internal/dashboard/rag/cases/benchmark-detail")
        self.assertEqual(query["eval_run_id"], ["run-1"])
        self.assertEqual(query["test_case_id"], ["case-1"])
        self.assertEqual(query["baseline_eval_run_id"], ["run-0"])
        self.assertEqual(captured["authorization"], "Bearer token")
        self.assertEqual(payload["primary"]["test_case_id"], "case-1")

    def test_live_case_detail_uses_internal_dashboard_endpoint(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        captured = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"primary":{"request_id":"RQ-1"},"baseline":null,"deltas":null}'

        def _fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers.get("Authorization")
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            payload = client.rag_dashboard_live_case_detail("RQ-1")

        parsed = urllib.parse.urlparse(captured["url"])
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/internal/dashboard/rag/cases/live-detail")
        self.assertEqual(query["request_id"], ["RQ-1"])
        self.assertEqual(captured["authorization"], "Bearer token")
        self.assertEqual(payload["primary"]["request_id"], "RQ-1")

    def test_query_answer_with_recovery_returns_live_detail_answer_after_query_timeout(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        live_detail = {
            "primary": {
                "request_id": "rag-join-1",
                "needs_human": False,
                "answer": "Call joinChannel with a token and channel name.",
                "confidence_score": 0.95,
                "answer_sources": ["https://docs.agora.io/en/video-calling/get-started/get-started-sdk"],
                "answer_citations": [
                    {
                        "chunk_id": "chunk-1",
                        "source_path": "official/get-started-sdk_android.md",
                        "heading": "Quickstart > Join a channel",
                        "source_url": "https://docs.agora.io/en/video-calling/get-started/get-started-sdk",
                    }
                ],
            }
        }

        with patch.object(
            client,
            "query",
            side_effect=RagServiceError("RAG service request failed"),
        ), patch.object(
            client,
            "rag_dashboard_live_case_detail",
            return_value=live_detail,
        ) as live_detail_mock:
            answer, confidence, sources, citations, needs_engineer = client.query_answer_with_recovery(
                question="how to join channel",
                request_id="rag-join-1",
                ticket_id="TK-021",
                customer_id="C-001",
                insufficient_reply="INSUFFICIENT",
            )

        self.assertEqual(answer, "Call joinChannel with a token and channel name.")
        self.assertEqual(confidence, 0.95)
        self.assertEqual(sources, ["https://docs.agora.io/en/video-calling/get-started/get-started-sdk"])
        self.assertEqual(citations[0]["chunk_id"], "chunk-1")
        self.assertFalse(needs_engineer)
        live_detail_mock.assert_called_once_with("rag-join-1")

    def test_query_answer_with_recovery_retries_live_detail_until_answer_is_available(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        live_detail = {
            "primary": {
                "request_id": "rag-join-2",
                "needs_human": False,
                "answer": "Use the join method on the channel instance.",
                "confidence_score": 0.91,
                "answer_sources": ["https://docs.agora.io/en/signaling/core-functionality/stream-channel"],
                "answer_citations": [
                    {
                        "chunk_id": "chunk-2",
                        "source_path": "official/stream-channel_ios.md",
                        "heading": "Stream channels > Join a stream channel",
                        "source_url": "https://docs.agora.io/en/signaling/core-functionality/stream-channel",
                    }
                ],
            }
        }

        with patch.object(
            client,
            "query",
            side_effect=RagServiceError("RAG service request failed"),
        ), patch.object(
            client,
            "rag_dashboard_live_case_detail",
            side_effect=[
                RagServiceError(
                    "RAG service returned HTTP 404",
                    status_code=404,
                    payload={"detail": "Live query not found"},
                ),
                live_detail,
            ],
        ) as live_detail_mock, patch(
            "backend.services.rag_service_client.time.sleep",
        ) as sleep_mock:
            answer, confidence, sources, citations, needs_engineer = client.query_answer_with_recovery(
                question="how to join channel",
                request_id="rag-join-2",
                ticket_id="TK-021",
                customer_id="C-001",
                insufficient_reply="INSUFFICIENT",
                recovery_attempts=2,
                recovery_delay_seconds=0.25,
            )

        self.assertEqual(answer, "Use the join method on the channel instance.")
        self.assertEqual(confidence, 0.91)
        self.assertEqual(sources, ["https://docs.agora.io/en/signaling/core-functionality/stream-channel"])
        self.assertEqual(citations[0]["chunk_id"], "chunk-2")
        self.assertFalse(needs_engineer)
        self.assertEqual(live_detail_mock.call_count, 2)
        sleep_mock.assert_called_once_with(0.25)

    def test_update_review_sample_uses_internal_review_endpoint(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        captured = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"sample_id":"RS-1","updated":true}'

        def _fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers.get("Authorization")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            payload = client.update_review_sample(
                "RS-1",
                review_status="reviewed",
                retrieval_ok=True,
                answer_ok=False,
                citation_ok=True,
                logic_ok=False,
                hallucination_present=True,
                dataset_decision="needs_fix",
                corrected_reference_answer="Use the backend token server.",
                corrected_citation_targets=[{"chunk_id": "chunk-1"}],
                note="Needs retrieval follow-up.",
            )

        self.assertEqual(
            captured["url"],
            "http://rag-api.internal/internal/dashboard/rag/review-samples/RS-1",
        )
        self.assertEqual(captured["authorization"], "Bearer token")
        self.assertEqual(captured["body"]["review_status"], "reviewed")
        self.assertEqual(captured["body"]["retrieval_ok"], True)
        self.assertEqual(captured["body"]["logic_ok"], False)
        self.assertEqual(captured["body"]["hallucination_present"], True)
        self.assertEqual(captured["body"]["dataset_decision"], "needs_fix")
        self.assertEqual(captured["body"]["corrected_reference_answer"], "Use the backend token server.")
        self.assertEqual(payload["updated"], True)

    def test_create_dataset_generation_run_uses_internal_endpoint(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        captured = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"generation_run_id":"GR-1","queued":true}'

        def _fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers.get("Authorization")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            payload = client.create_dataset_generation_run(
                dataset_name="golden-set",
                source_types=["official_markdown_upload", "technical_article_api"],
                question_language="en",
            )

        self.assertEqual(
            captured["url"],
            "http://rag-api.internal/internal/dashboard/rag/datasets/generation-runs",
        )
        self.assertEqual(captured["body"]["dataset_name"], "golden-set")
        self.assertEqual(captured["body"]["question_language"], "en")
        self.assertEqual(payload["generation_run_id"], "GR-1")

    def test_create_dataset_benchmark_run_uses_internal_endpoint(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        captured = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"eval_run_id":"EVAL-1","queued":true}'

        def _fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers.get("Authorization")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            payload = client.create_dataset_benchmark_run(
                "DS-1",
                experiment_id="exp-dataset-1",
                top_k=6,
                tier="gold",
            )

        self.assertEqual(
            captured["url"],
            "http://rag-api.internal/internal/dashboard/rag/datasets/DS-1/benchmark-runs",
        )
        self.assertEqual(captured["body"]["experiment_id"], "exp-dataset-1")
        self.assertEqual(captured["body"]["top_k"], 6)
        self.assertEqual(captured["body"]["tier"], "gold")
        self.assertEqual(payload["eval_run_id"], "EVAL-1")

    def test_create_local_benchmark_session_run_uses_internal_endpoint(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        captured = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"benchmark_session_id":"BSESS-1","queued":true,"runs_expected":3}'

        def _fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers.get("Authorization")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            payload = client.create_local_benchmark_session_run(
                session_name="session-1",
                top_k=8,
            )

        self.assertEqual(
            captured["url"],
            "http://rag-api.internal/internal/dashboard/rag/benchmarks/sessions/local-run",
        )
        self.assertEqual(captured["body"]["session_name"], "session-1")
        self.assertEqual(captured["body"]["top_k"], 8)
        self.assertEqual(payload["benchmark_session_id"], "BSESS-1")

    def test_sync_local_benchmarks_uses_internal_endpoint(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        captured = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"synced_count":3,"source_of_truth":"local_benchmarks"}'

        def _fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers.get("Authorization")
            captured["body"] = request.data
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            payload = client.sync_local_benchmarks()

        self.assertEqual(
            captured["url"],
            "http://rag-api.internal/internal/dashboard/rag/benchmarks/local-sync",
        )
        self.assertEqual(captured["authorization"], "Bearer token")
        self.assertIsNone(captured["body"])
        self.assertEqual(payload["synced_count"], 3)

    def test_export_dataset_snapshot_uses_internal_endpoint(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        captured = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"test_case_id":"case-1"}\n'

        def _fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers.get("Authorization")
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            payload = client.export_dataset_snapshot("DS-1", tier="gold")

        parsed = urllib.parse.urlparse(captured["url"])
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/internal/dashboard/rag/datasets/DS-1/export")
        self.assertEqual(query["tier"], ["gold"])
        self.assertEqual(captured["authorization"], "Bearer token")
        self.assertEqual(payload, '{"test_case_id":"case-1"}\n')


if __name__ == "__main__":
    unittest.main()
