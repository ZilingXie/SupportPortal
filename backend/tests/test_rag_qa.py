from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services.rag_qa import (
    RetrievedChunk,
    _extract_metadata_hints,
    _get_rag_config,
    _metadata_rerank,
    _resolve_active_vector_table,
    _rrf_merge,
    _select_diverse_chunks,
    _select_bm25_query_terms,
    _split_table_name,
    run_rag_query,
)


class RagQaHybridTests(unittest.TestCase):
    class _FakeProvider:
        provider_name = "siliconflow"
        model_id = "BAAI/bge-m3"
        vector_dim = 1024

        def count_tokens(self, text: str) -> int:
            return max(1, len(str(text or "").split()))

        def drain_request_log(self) -> list[dict[str, object]]:
            return []

    def test_split_table_name_supports_schema_prefix(self) -> None:
        self.assertEqual(_split_table_name("public.docagent"), ("public", "docagent"))
        self.assertEqual(
            _split_table_name("docagent_chunks_bge_m3_1024"),
            ("supportportal", "docagent_chunks_bge_m3_1024"),
        )

    def test_get_rag_config_uses_hybrid_candidate_windows(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = _get_rag_config(top_k=6)
        self.assertEqual(config["top_k"], 6)
        self.assertEqual(config["vector_candidate_k"], 60)
        self.assertEqual(config["bm25_candidate_k"], 60)
        self.assertEqual(config["fusion_candidate_k"], 48)
        self.assertEqual(config["rerank_top_n"], 24)
        self.assertEqual(config["bm25_k1"], 1.2)
        self.assertEqual(config["bm25_b"], 0.75)
        self.assertEqual(config["rerank_provider"], "siliconflow")
        self.assertEqual(config["rerank_model"], "BAAI/bge-reranker-v2-m3")
        self.assertEqual(config["table"], "supportportal.docagent_chunks_bge_m3_1024")
        self.assertEqual(config["embedding_provider"], "siliconflow")
        self.assertEqual(config["embedding_model"], "BAAI/bge-m3")
        self.assertEqual(config["bm25_max_query_terms"], 6)
        self.assertEqual(config["bm25_max_term_doc_freq_ratio"], 0.08)

    def test_get_rag_config_reads_lowercase_silliconflow_key_for_reranker(self) -> None:
        with patch.dict(os.environ, {"silliconflow_key": "test-rerank-key"}, clear=True):
            config = _get_rag_config(top_k=6)
        self.assertEqual(config["rerank_api_key"], "test-rerank-key")

    def test_select_bm25_query_terms_filters_overly_common_terms(self) -> None:
        selected = _select_bm25_query_terms(
            terms=["agora", "token", "recommended", "app", "id"],
            term_doc_freqs={
                "agora": 29285,
                "token": 4497,
                "recommended": 684,
                "app": 10033,
                "id": 6528,
            },
            doc_count=65890,
            max_term_doc_freq_ratio=0.08,
            max_query_terms=6,
        )

        self.assertEqual(selected, ["recommended", "token"])

    def test_select_bm25_query_terms_falls_back_to_rarest_terms_when_all_are_common(self) -> None:
        selected = _select_bm25_query_terms(
            terms=["agora", "app", "id"],
            term_doc_freqs={
                "agora": 29285,
                "app": 10033,
                "id": 6528,
            },
            doc_count=65890,
            max_term_doc_freq_ratio=0.05,
            max_query_terms=2,
        )

        self.assertEqual(selected, ["id", "app"])

    def test_select_bm25_query_terms_discards_conversational_noise(self) -> None:
        selected = _select_bm25_query_terms(
            terms=["i", "m", "getting", "error", "109", "users", "join", "mean", "token", "expired"],
            term_doc_freqs={
                "i": 1,
                "m": 2,
                "getting": 12,
                "error": 4500,
                "109": 40,
                "users": 9000,
                "join": 8000,
                "mean": 15,
                "token": 4497,
                "expired": 120,
            },
            doc_count=65890,
            max_term_doc_freq_ratio=0.08,
            max_query_terms=6,
        )

        self.assertNotIn("i", selected)
        self.assertNotIn("m", selected)
        self.assertNotIn("getting", selected)
        self.assertNotIn("mean", selected)
        self.assertIn("109", selected)
        self.assertIn("error", selected)
        self.assertIn("token", selected)
        self.assertIn("expired", selected)

    def test_rrf_merge_dedupes_and_limits_results(self) -> None:
        shared = RetrievedChunk(
            chunk_id="shared",
            text="Shared answer chunk",
            source_path="official/shared.md",
            similarity=0.91,
        )
        vector_chunks = [
            shared,
            RetrievedChunk(
                chunk_id="vector-only",
                text="Vector result",
                source_path="official/vector.md",
                similarity=0.88,
            ),
        ]
        keyword_chunks = [
            RetrievedChunk(
                chunk_id="keyword-only",
                text="Keyword result",
                source_path="technical/keyword.md",
                similarity=0.74,
            ),
            shared,
        ]

        merged = _rrf_merge(vector_chunks, keyword_chunks, limit=2)

        self.assertEqual(len(merged), 2)
        merged_ids = [chunk.chunk_id for chunk in merged]
        self.assertIn("shared", merged_ids)
        self.assertEqual(len(set(merged_ids)), 2)

    def test_select_diverse_chunks_prefers_unique_family_before_backfill(self) -> None:
        chunks = [
            RetrievedChunk(
                chunk_id="auth-android",
                text="Android authentication workflow",
                source_path="en/android/authentication-workflow.md",
                similarity=0.99,
                metadata={"product": "video-calling"},
            ),
            RetrievedChunk(
                chunk_id="auth-ios",
                text="iOS authentication workflow",
                source_path="en/ios/authentication-workflow.md",
                similarity=0.98,
                metadata={"product": "video-calling"},
            ),
            RetrievedChunk(
                chunk_id="error-codes",
                text="Common SDK error codes",
                source_path="en/android/error-codes.md",
                similarity=0.97,
                metadata={"product": "video-calling"},
            ),
        ]

        selected = _select_diverse_chunks(chunks, limit=2)

        self.assertEqual([chunk.chunk_id for chunk in selected], ["auth-android", "error-codes"])

    def test_select_diverse_chunks_backfills_original_order_when_unique_families_run_out(self) -> None:
        chunks = [
            RetrievedChunk(
                chunk_id="auth-android",
                text="Android authentication workflow",
                source_path="en/android/authentication-workflow.md",
                similarity=0.99,
                metadata={"product": "video-calling"},
            ),
            RetrievedChunk(
                chunk_id="auth-ios",
                text="iOS authentication workflow",
                source_path="en/ios/authentication-workflow.md",
                similarity=0.98,
                metadata={"product": "video-calling"},
            ),
            RetrievedChunk(
                chunk_id="error-codes",
                text="Common SDK error codes",
                source_path="en/android/error-codes.md",
                similarity=0.97,
                metadata={"product": "video-calling"},
            ),
        ]

        selected = _select_diverse_chunks(chunks, limit=3)

        self.assertEqual([chunk.chunk_id for chunk in selected], ["auth-android", "error-codes", "auth-ios"])

    def test_retrieval_queries_filter_primary_index_role(self) -> None:
        source = Path("backend/services/rag_qa.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("index_role = 'primary'"), 3)

    def test_bm25_query_uses_double_precision_score_constants(self) -> None:
        source = Path("backend/services/rag_qa.py").read_text(encoding="utf-8")
        self.assertIn("0.5::double precision", source)
        self.assertIn("1.0::double precision", source)

    def test_bm25_query_materializes_matched_postings_and_docs_before_scoring(self) -> None:
        source = Path("backend/services/rag_qa.py").read_text(encoding="utf-8")
        self.assertIn("matched_postings AS MATERIALIZED", source)
        self.assertIn("matched_docs AS MATERIALIZED", source)
        self.assertIn("SELECT DISTINCT chunk_id FROM matched_postings", source)

    def test_extract_metadata_hints_recognizes_language_method_and_structure_intent(self) -> None:
        hints = _extract_metadata_hints("Node.js 的 BuildTokenWithUidAndPrivilege Docker parameter 是什么")

        self.assertEqual(hints.language, "nodejs")
        self.assertEqual(hints.method_name, "BuildTokenWithUidAndPrivilege")
        self.assertIn("docker", hints.intent_terms)
        self.assertIn("parameter", hints.intent_terms)

    def test_extract_metadata_hints_recognizes_technical_case_intents(self) -> None:
        hints = _extract_metadata_hints("怎么判断延迟发生在 Agora 还是客户自己的 queue？")

        self.assertIsNone(hints.language)
        self.assertIsNone(hints.method_name)
        self.assertIn("decision_logic", hints.intent_terms)

    def test_metadata_rerank_filters_exact_method_and_boosts_language_matches(self) -> None:
        node_chunk = RetrievedChunk(
            chunk_id="node-method",
            text="Node.js code sample for BuildTokenWithUidAndPrivilege",
            source_path="official/deploy-token-server.md",
            similarity=0.82,
            h1="Deploy a token server",
            h2="Token generation code",
            h3="Basic authentication",
            metadata={
                "language": "nodejs",
                "method_name": "BuildTokenWithUidAndPrivilege",
                "chunk_type": "code",
                "section_path": ["Token generation code", "Basic authentication"],
                "topic": ["token", "authentication"],
                "use_case": "basic_authentication",
            },
        )
        java_chunk = RetrievedChunk(
            chunk_id="java-method",
            text="Java code sample for BuildTokenWithUid",
            source_path="official/deploy-token-server.md",
            similarity=0.91,
            h1="Deploy a token server",
            h2="Token generation code",
            h3="Basic authentication",
            metadata={
                "language": "java",
                "method_name": "BuildTokenWithUid",
                "chunk_type": "code",
                "section_path": ["Token generation code", "Basic authentication"],
                "topic": ["token", "authentication"],
                "use_case": "basic_authentication",
            },
        )
        docker_chunk = RetrievedChunk(
            chunk_id="docker-guide",
            text="Docker deployment guide for the token server",
            source_path="official/deploy-token-server.md",
            similarity=0.77,
            h1="Deploy a token server",
            h2="Deploy a token server",
            h3="Deploy with Docker",
            metadata={
                "language": None,
                "method_name": None,
                "chunk_type": "howto",
                "section_path": ["Deploy a token server", "Deploy with Docker"],
                "topic": ["docker", "deployment", "token"],
                "use_case": "docker_deployment",
            },
        )
        node_params_chunk = RetrievedChunk(
            chunk_id="node-method-params",
            text="Parameters for BuildTokenWithUidAndPrivilege in Node.js",
            source_path="official/deploy-token-server.md",
            similarity=0.79,
            h1="Deploy a token server",
            h2="Reference",
            h3="`BuildTokenWithUidAndPrivilege`",
            metadata={
                "language": "nodejs",
                "method_name": "BuildTokenWithUidAndPrivilege",
                "chunk_type": "api_params",
                "section_path": ["Reference", "API Reference", "`BuildTokenWithUidAndPrivilege`"],
                "topic": ["token", "permissions", "parameter"],
                "use_case": "advanced_permissions",
            },
        )

        hints = _extract_metadata_hints("Node.js 怎么用 BuildTokenWithUidAndPrivilege 生成 token")
        reranked, info = _metadata_rerank(
            query="Node.js 怎么用 BuildTokenWithUidAndPrivilege 生成 token",
            chunks=[java_chunk, docker_chunk, node_chunk, node_params_chunk],
            top_k=3,
            hints=hints,
        )

        self.assertEqual([chunk.chunk_id for chunk in reranked[:2]], ["node-method", "node-method-params"])
        self.assertTrue(info["applied_filter"])
        self.assertEqual(info["filter_type"], "language+method")
        self.assertEqual(info["post_rerank_count"], 2)
        self.assertIn("language:nodejs", info["candidate_reasons"]["node-method"])
        self.assertIn("method_name:BuildTokenWithUidAndPrivilege", info["candidate_reasons"]["node-method"])
        self.assertEqual(reranked[0].candidate_trace.get("metadata_rank"), 1)
        self.assertEqual(reranked[1].candidate_trace.get("metadata_rank"), 2)

    def test_metadata_rerank_filters_technical_case_chunks_by_strong_intent(self) -> None:
        issue_chunk = RetrievedChunk(
            chunk_id="issue-summary",
            text="A livestream archive was missing the first 64 seconds after the Cloud Transcoder create request.",
            source_path="technical/stream-start-delay.md",
            similarity=0.88,
            h1="Livestream archive missing first 64 seconds",
            h2="Issue Summary",
            metadata={
                "doc_subtype": "troubleshooting_case",
                "source_type": "technical_article_api",
                "chunk_type": "issue_summary",
                "issue_category": "startup_delay",
                "symptoms": [
                    "missing initial content",
                    "first frame delayed",
                ],
                "keywords": ["cloud transcoder", "create request", "aws ivs", "queue delay"],
                "external_service": "AWS IVS",
                "protocol": "RTMP",
            },
        )
        procedure_chunk = RetrievedChunk(
            chunk_id="procedure",
            text="Check Agora logs, find acquire/create timestamps, locate transcoder initialization, then compare the first RTMP frame arrival time at AWS IVS.",
            source_path="technical/stream-start-delay.md",
            similarity=0.81,
            h1="Livestream archive missing first 64 seconds",
            h2="Troubleshooting Procedure",
            metadata={
                "doc_subtype": "troubleshooting_case",
                "source_type": "technical_article_api",
                "chunk_type": "troubleshooting_procedure",
                "issue_category": "startup_delay",
                "symptoms": ["first frame delayed"],
                "keywords": ["cloud transcoder", "aws ivs", "rtmp", "create request"],
                "external_service": "AWS IVS",
                "protocol": "RTMP",
            },
        )
        decision_chunk = RetrievedChunk(
            chunk_id="decision",
            text="If the delay occurs before Agora receives create, investigate the customer queue. If Agora receives create quickly but RTMP starts late, investigate transcoder initialization.",
            source_path="technical/stream-start-delay.md",
            similarity=0.79,
            h1="Livestream archive missing first 64 seconds",
            h2="Decision Logic",
            metadata={
                "doc_subtype": "troubleshooting_case",
                "source_type": "technical_article_api",
                "chunk_type": "decision_logic",
                "issue_category": "startup_delay",
                "symptoms": ["first frame delayed", "stream start timestamp mismatch"],
                "keywords": ["queue delay", "cloud transcoder", "create", "rtmp output start"],
                "external_service": "AWS IVS",
                "protocol": "RTMP",
            },
        )
        best_practice_chunk = RetrievedChunk(
            chunk_id="best-practice",
            text="Log request dispatch timestamps, monitor RTMP output start, and minimize queue scheduling latency.",
            source_path="technical/stream-start-delay.md",
            similarity=0.74,
            h1="Livestream archive missing first 64 seconds",
            h2="Best Practice",
            metadata={
                "doc_subtype": "troubleshooting_case",
                "source_type": "technical_article_api",
                "chunk_type": "best_practice",
                "issue_category": "startup_delay",
                "symptoms": ["first frame delayed"],
                "keywords": ["logging", "queue latency", "monitoring"],
                "external_service": "AWS IVS",
                "protocol": "RTMP",
            },
        )

        hints = _extract_metadata_hints("怎么判断延迟发生在 Agora 还是客户自己的 queue？")
        reranked, info = _metadata_rerank(
            query="怎么判断延迟发生在 Agora 还是客户自己的 queue？",
            chunks=[issue_chunk, procedure_chunk, decision_chunk, best_practice_chunk],
            top_k=3,
            hints=hints,
        )

        self.assertEqual(reranked[0].chunk_id, "decision")
        self.assertTrue(info["applied_filter"])
        self.assertEqual(info["filter_type"], "technical_intent")
        self.assertIn("intent:decision_logic", info["candidate_reasons"]["decision"])
        self.assertGreaterEqual(info["post_rerank_count"], 1)

    def test_run_rag_query_uses_bm25_pipeline_and_skips_fts(self) -> None:
        vector_chunk = RetrievedChunk(
            chunk_id="vector-1",
            text="Vector chunk",
            source_path="official/vector.md",
            similarity=0.91,
        )
        bm25_chunk = RetrievedChunk(
            chunk_id="bm25-1",
            text="BM25 chunk",
            source_path="technical/bm25.md",
            similarity=0.66,
        )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-4.1",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[vector_chunk]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[bm25_chunk]):
                        with patch("backend.services.rag_qa._retrieve_fts_chunks", side_effect=AssertionError("fts should not run")):
                            with patch("backend.services.rag_qa._metadata_rerank", return_value=([vector_chunk, bm25_chunk], {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None})):
                                with patch("backend.services.rag_qa._rerank_chunks", return_value=[bm25_chunk, vector_chunk]):
                                    with patch("backend.services.rag_qa._invoke_llm_payload_with_trace", return_value=({"answer": "Use the BM25 chunk.", "key_steps": [], "citations": ["bm25-1"], "insufficient_evidence": False}, 10, 5, "gpt-4.1")):
                                        result = run_rag_query("how do I use BM25?")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.retrieval_strategy, "hybrid_rrf_bm25")
        self.assertEqual(result.trace.bm25_candidates_count, 1)
        self.assertEqual(result.trace.selected_chunk_ids[0], "bm25-1")
        self.assertEqual(result.trace.reranker_provider, "siliconflow")
        self.assertEqual(result.trace.reranker_model, "BAAI/bge-reranker-v2-m3")
        self.assertTrue(result.trace.retrieval_candidates)
        self.assertTrue(
            all(
                isinstance(candidate.get("candidate_trace"), dict)
                for candidate in result.trace.retrieval_candidates
            )
        )
        self.assertIn(
            ["bm25"],
            [
                candidate["candidate_trace"].get("retrieval_sources")
                for candidate in result.trace.retrieval_candidates
            ],
        )

    def test_run_rag_query_keeps_keyword_fallback_out_of_bm25_telemetry(self) -> None:
        vector_chunk = RetrievedChunk(
            chunk_id="vector-1",
            text="Vector chunk",
            source_path="official/vector.md",
            similarity=0.91,
        )
        keyword_chunk = RetrievedChunk(
            chunk_id="keyword-1",
            text="Keyword fallback chunk",
            source_path="technical/keyword.md",
            similarity=0.52,
            retrieval_sources=["keyword_fallback"],
            candidate_trace={"keyword_fallback_hits": 2},
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
                "chat_model": "gpt-4.1",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[vector_chunk]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", side_effect=RuntimeError("bm25 offline")):
                        with patch("backend.services.rag_qa._retrieve_keyword_chunks", return_value=[keyword_chunk]):
                            with patch("backend.services.rag_qa._metadata_rerank", return_value=([vector_chunk, keyword_chunk], {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None})):
                                with patch("backend.services.rag_qa._rerank_chunks", return_value=[vector_chunk, keyword_chunk]):
                                    with patch("backend.services.rag_qa._invoke_llm_payload_with_trace", return_value=({"answer": "Use the vector chunk.", "key_steps": [], "citations": ["vector-1"], "insufficient_evidence": False}, 10, 5, "gpt-4.1")):
                                        result = run_rag_query("bm25 is down, use fallback")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.retrieval_strategy, "vector_keyword_fallback")
        self.assertEqual(result.trace.bm25_candidates_count, 0)
        self.assertEqual(
            result.trace.retrieval_candidates[1]["candidate_trace"].get("retrieval_sources"),
            ["keyword_fallback"],
        )

    def test_run_rag_query_diversifies_final_chunks_before_generation(self) -> None:
        auth_android = RetrievedChunk(
            chunk_id="auth-android",
            text="Android authentication workflow",
            source_path="en/android/authentication-workflow.md",
            similarity=0.99,
            metadata={"product": "video-calling"},
        )
        auth_ios = RetrievedChunk(
            chunk_id="auth-ios",
            text="iOS authentication workflow",
            source_path="en/ios/authentication-workflow.md",
            similarity=0.98,
            metadata={"product": "video-calling"},
        )
        error_codes = RetrievedChunk(
            chunk_id="error-codes",
            text="Common SDK error codes",
            source_path="en/android/error-codes.md",
            similarity=0.97,
            metadata={"product": "video-calling"},
        )
        captured_final_chunk_ids: list[list[str]] = []

        def _capture_payload(
            message: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            strict_retry: bool = False,
        ) -> tuple[dict[str, object], int, int, str]:
            _ = message
            _ = config
            _ = strict_retry
            captured_final_chunk_ids.append([chunk.chunk_id for chunk in chunks])
            return (
                {
                    "answer": "Use the selected chunks.",
                    "key_steps": [],
                    "citations": ["auth-android"],
                    "insufficient_evidence": False,
                },
                10,
                5,
                "gpt-4.1",
            )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-4.1",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[auth_android]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[auth_ios, error_codes]):
                        with patch(
                            "backend.services.rag_qa._metadata_rerank",
                            return_value=([auth_android, auth_ios, error_codes], {"post_rerank_count": 3, "hints": {}, "applied_filter": False, "filter_type": None}),
                        ):
                            with patch("backend.services.rag_qa._rerank_chunks", return_value=[auth_android, auth_ios, error_codes]):
                                with patch("backend.services.rag_qa._invoke_llm_payload_with_trace", side_effect=_capture_payload):
                                    result = run_rag_query("how do I handle token authentication errors?")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(captured_final_chunk_ids[0], ["auth-android", "error-codes"])
        self.assertEqual(result.trace.selected_chunk_ids, ["auth-android", "error-codes"])

    def test_resolve_active_vector_table_prefers_populated_fallback_when_configured_table_empty(self) -> None:
        config = {
            "dsn": "postgresql://example",
            "table": "supportportal.docagent_chunks_bge_m3_1024",
        }

        with patch("backend.services.rag_qa._list_vector_tables_with_primary_counts") as list_mock:
            list_mock.return_value = [
                ("supportportal.docagent_chunks_bge_m3_1024", 0),
                ("supportportal.docagent_chunks_ag_docs_test_1024", 1907),
                ("supportportal.docagent_chunks", 16),
            ]

            resolved = _resolve_active_vector_table(config)

        self.assertEqual(resolved, "supportportal.docagent_chunks_ag_docs_test_1024")

    def test_resolve_active_vector_table_returns_configured_table_without_full_enumeration_when_populated(self) -> None:
        config = {
            "dsn": "postgresql://example",
            "table": "supportportal.docagent_chunks_bge_m3_1024",
        }

        with patch("backend.services.rag_qa._count_primary_rows_in_table", return_value=65890):
            with patch("backend.services.rag_qa._list_vector_tables_with_primary_counts") as list_mock:
                resolved = _resolve_active_vector_table(config)

        self.assertEqual(resolved, "supportportal.docagent_chunks_bge_m3_1024")
        list_mock.assert_not_called()

    def test_run_rag_query_uses_resolved_vector_table_for_all_retrieval_paths(self) -> None:
        captured_tables: list[str] = []

        def _capture_vector(message: str, config: dict[str, object], *, limit: int | None = None) -> list[RetrievedChunk]:
            _ = message
            _ = limit
            captured_tables.append(str(config["table"]))
            return []

        def _capture_bm25(message: str, config: dict[str, object], *, limit: int | None = None) -> list[RetrievedChunk]:
            _ = message
            _ = limit
            captured_tables.append(str(config["table"]))
            return []

        def _capture_keyword(message: str, config: dict[str, object], *, limit: int | None = None) -> list[RetrievedChunk]:
            _ = message
            _ = limit
            captured_tables.append(str(config["table"]))
            return []

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
                "chat_model": "gpt-4.1",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
            }
            with patch("backend.services.rag_qa._resolve_active_vector_table", return_value="supportportal.docagent_chunks_ag_docs_test_1024"):
                with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                    with patch("backend.services.rag_qa._retrieve_chunks", side_effect=_capture_vector):
                        with patch("backend.services.rag_qa._retrieve_bm25_chunks", side_effect=_capture_bm25):
                            with patch("backend.services.rag_qa._retrieve_keyword_chunks", side_effect=_capture_keyword):
                                result = run_rag_query("how to join channel")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            set(captured_tables),
            {"supportportal.docagent_chunks_ag_docs_test_1024"},
        )
        self.assertTrue(result.trace.needs_human)


if __name__ == "__main__":
    unittest.main()
