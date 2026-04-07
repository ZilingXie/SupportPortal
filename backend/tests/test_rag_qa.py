from __future__ import annotations

import io
import os
import threading
import urllib.error
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import backend.services.rag_qa as rag_qa
import psycopg

from backend.services.llm_factory import LlmTextResult
from backend.services.rag_context_budget import ContextBudget, PackedEvidence
from backend.services.rag_qa import (
    INSUFFICIENT_EVIDENCE_REPLY,
    RagExecutionCancelled,
    RetrievedChunk,
    _chunk_family_key,
    _extract_metadata_hints,
    _get_rag_config,
    _raise_if_cancelled,
    _metadata_rerank,
    probe_customer_rag_index_readiness,
    _retrieve_bm25_chunks,
    _rerank_chunks,
    _resolve_active_vector_table,
    _rrf_merge,
    _select_diverse_chunks,
    _select_bm25_query_terms,
    _split_table_name,
    run_rag_query,
)
from backend.services.query_understanding import QueryUnderstandingResult, RetrievalPlan, downpush_hard_filters


class RagQaHybridTests(unittest.TestCase):
    class _FakeProvider:
        provider_name = "siliconflow"
        model_id = "BAAI/bge-m3"
        vector_dim = 1024

        def count_tokens(self, text: str) -> int:
            return max(1, len(str(text or "").split()))

        def drain_request_log(self) -> list[dict[str, object]]:
            return []

    def setUp(self) -> None:
        rag_qa._RUNTIME_CAPABILITY_UNAVAILABLE_UNTIL.clear()
        rag_qa.clear_active_vector_table_cache()
        self._env_backup = {
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
            "SILICONFLOW_API_KEY": os.environ.get("SILICONFLOW_API_KEY"),
            "SILLICONFLOW_KEY": os.environ.get("SILLICONFLOW_KEY"),
            "RAG_RERANK_API_KEY": os.environ.get("RAG_RERANK_API_KEY"),
        }
        for name in self._env_backup:
            os.environ.pop(name, None)

    def tearDown(self) -> None:
        rag_qa.clear_active_vector_table_cache()
        for name, value in self._env_backup.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

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

    def test_get_rag_config_disables_vector_and_rerank_without_provider_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {
                "EMBEDDING_PROVIDER": "siliconflow",
                "RAG_RERANK_PROVIDER": "siliconflow",
            },
            clear=True,
        ):
            config = _get_rag_config(top_k=6)

        self.assertFalse(config["vector_enabled"])
        self.assertFalse(config["rerank_enabled"])

    def test_raise_if_cancelled_uses_stage_name(self) -> None:
        with self.assertRaises(RagExecutionCancelled) as ctx:
            _raise_if_cancelled("answer_generation", should_cancel=lambda: True)

        self.assertEqual(ctx.exception.stage, "answer_generation")

    def test_run_rag_query_propagates_agentic_cancellation_without_legacy_fallback(self) -> None:
        with patch.object(
            rag_qa,
            "_run_rag_query_agentic",
            side_effect=RagExecutionCancelled("answer_generation"),
        ), patch.object(
            rag_qa,
            "_run_rag_query_legacy",
            side_effect=AssertionError("legacy fallback should not run for cancellations"),
        ):
            with self.assertRaises(RagExecutionCancelled) as ctx:
                run_rag_query("how to join channel")

        self.assertEqual(ctx.exception.stage, "answer_generation")

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

    def test_chunk_family_key_prefers_metadata_source_family_before_source_path_heuristic(self) -> None:
        chunk = RetrievedChunk(
            chunk_id="auth-ios",
            text="iOS authentication workflow",
            source_path="en/ios/authentication-workflow_ios.md",
            similarity=0.98,
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/authentication-workflow",
            },
        )

        self.assertEqual(
            _chunk_family_key(chunk),
            "video-calling::video-calling/get-started/authentication-workflow",
        )

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

    def test_select_diverse_chunks_dedupes_repeated_sections_before_backfill(self) -> None:
        chunks = [
            RetrievedChunk(
                chunk_id="wildcard-precautions-a",
                text="Set uid to 0 when you generate a wildcard token.",
                source_path="official/deploy-token-server.md",
                similarity=0.99,
                h1="Deploy a token server",
                h2="Generate wildcard tokens",
                h3="Precautions",
                metadata={
                    "product": "video-calling",
                    "section_path": ["Deploy a token server", "Generate wildcard tokens", "Precautions"],
                    "use_case": "wildcard_tokens",
                },
            ),
            RetrievedChunk(
                chunk_id="wildcard-precautions-b",
                text="Wildcard tokens should still be generated on the app server.",
                source_path="official/deploy-token-server.md",
                similarity=0.98,
                h1="Deploy a token server",
                h2="Generate wildcard tokens",
                h3="Precautions",
                metadata={
                    "product": "video-calling",
                    "section_path": ["Deploy a token server", "Generate wildcard tokens", "Precautions"],
                    "use_case": "wildcard_tokens",
                },
            ),
            RetrievedChunk(
                chunk_id="wildcard-main",
                text="Generate wildcard tokens only when you need a token that works for all users.",
                source_path="official/deploy-token-server.md",
                similarity=0.97,
                h1="Deploy a token server",
                h2="Generate wildcard tokens",
                metadata={
                    "product": "video-calling",
                    "section_path": ["Deploy a token server", "Generate wildcard tokens"],
                    "use_case": "wildcard_tokens",
                },
            ),
        ]

        selected = _select_diverse_chunks(chunks, limit=2)

        self.assertEqual([chunk.chunk_id for chunk in selected], ["wildcard-precautions-a", "wildcard-main"])

    def test_retrieval_queries_parameterize_index_role(self) -> None:
        source = Path("backend/services/rag_qa.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count('index_role: str = "primary"'), 4)
        self.assertGreaterEqual(source.count("index_role = %s"), 3)

    def test_bm25_query_uses_double_precision_score_constants(self) -> None:
        source = Path("backend/services/rag_qa.py").read_text(encoding="utf-8")
        self.assertIn("0.5::double precision", source)
        self.assertIn("1.0::double precision", source)

    def test_bm25_query_materializes_matched_postings_and_docs_before_scoring(self) -> None:
        source = Path("backend/services/rag_qa.py").read_text(encoding="utf-8")
        self.assertIn("matched_postings AS MATERIALIZED", source)
        self.assertIn("matched_docs AS MATERIALIZED", source)
        self.assertIn("SELECT DISTINCT chunk_id FROM matched_postings", source)

    def test_retrieve_bm25_chunks_binds_index_role_after_bm25_constants(self) -> None:
        class _FakeCursor:
            def __init__(self) -> None:
                self.calls: list[tuple[object, tuple[object, ...] | None]] = []

            def execute(self, query: object, params: tuple[object, ...] | None = None) -> None:
                self.calls.append((query, params))

            def fetchall(self) -> list[tuple[object, ...]]:
                if len(self.calls) == 1:
                    return [("token", 10)]
                if len(self.calls) == 3:
                    return []
                return []

            def fetchone(self) -> tuple[object, ...]:
                return (100,)

            def __enter__(self) -> "_FakeCursor":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

        class _FakeConnection:
            def __init__(self, cursor: _FakeCursor) -> None:
                self._cursor = cursor

            def cursor(self) -> _FakeCursor:
                return self._cursor

            def __enter__(self) -> "_FakeConnection":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

        fake_cursor = _FakeCursor()
        fake_psycopg = SimpleNamespace(
            sql=psycopg.sql,
            connect=lambda *_args, **_kwargs: _FakeConnection(fake_cursor),
        )
        config = {
            "dsn": "postgresql://example",
            "table": "supportportal.docagent_chunks_bge_m3_1024",
            "app_schema": "supportportal",
            "bm25_candidate_k": 12,
            "bm25_k1": 1.2,
            "bm25_b": 0.75,
            "bm25_max_term_doc_freq_ratio": 0.08,
            "bm25_max_query_terms": 6,
        }

        with patch("backend.services.rag_qa._import_psycopg", return_value=fake_psycopg), patch(
            "backend.services.rag_qa.tokenize_bm25_query",
            return_value=["token"],
        ), patch(
            "backend.services.rag_qa._select_bm25_query_terms",
            return_value=["token"],
        ):
            _retrieve_bm25_chunks("token question", config, index_role="shadow")

        _, params = fake_cursor.calls[2]
        assert params is not None
        self.assertEqual(params[5], 1.2)
        self.assertEqual(params[6], 1.2)
        self.assertEqual(params[7], 0.75)
        self.assertEqual(params[8], 0.75)
        self.assertEqual(params[9], "shadow")

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

    def test_metadata_rerank_prefers_basic_auth_for_generic_token_generation_query(self) -> None:
        advanced_chunk = RetrievedChunk(
            chunk_id="advanced-node",
            text="Node.js sample for generating a token with advanced privileges.",
            source_path="official/deploy-token-server.md",
            similarity=0.91,
            h1="Deploy a token server",
            h2="Token generation code",
            h3="Generate a token with advanced permissions",
            metadata={
                "language": "nodejs",
                "chunk_type": "code",
                "section_path": [
                    "Deploy a token server",
                    "Token generation code",
                    "Generate a token with advanced permissions",
                ],
                "topic": ["token", "permissions"],
                "use_case": "advanced_permissions",
            },
        )
        basic_chunk = RetrievedChunk(
            chunk_id="basic-node",
            text="Node.js sample for generating a token with basic authentication.",
            source_path="official/deploy-token-server.md",
            similarity=0.84,
            h1="Deploy a token server",
            h2="Token generation code",
            h3="Basic authentication",
            metadata={
                "language": "nodejs",
                "chunk_type": "code",
                "section_path": ["Deploy a token server", "Token generation code", "Basic authentication"],
                "topic": ["token", "authentication"],
                "use_case": "basic_authentication",
            },
        )

        hints = _extract_metadata_hints("Node.js 怎么生成 token")
        reranked, _ = _metadata_rerank(
            query="Node.js 怎么生成 token",
            chunks=[advanced_chunk, basic_chunk],
            top_k=2,
            hints=hints,
        )

        self.assertEqual([chunk.chunk_id for chunk in reranked[:2]], ["basic-node", "advanced-node"])

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

    def test_run_rag_query_uses_agentic_hybrid_pipeline(self) -> None:
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
                        with patch("backend.services.rag_qa._retrieve_fts_chunks", return_value=[]):
                            with patch("backend.services.rag_qa._metadata_rerank", return_value=([vector_chunk, bm25_chunk], {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None})):
                                with patch("backend.services.rag_qa._rerank_chunks", return_value=[bm25_chunk, vector_chunk]):
                                    with patch("backend.services.rag_qa._invoke_llm_payload_with_trace", return_value=({"answer": "Use the BM25 chunk.", "key_steps": [], "citations": ["bm25-1"], "insufficient_evidence": False}, 10, 5, "gpt-4.1")):
                                        result = run_rag_query("How do I use BM25 for channel join retrieval?")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.retrieval_strategy, "agentic_multi_tool_v1")
        self.assertGreaterEqual(result.trace.bm25_candidates_count, 1)
        self.assertEqual(result.trace.selected_chunk_ids[0], "bm25-1")
        self.assertTrue(result.trace.agent_enabled)
        self.assertEqual(result.trace.reranker_provider, "siliconflow")
        self.assertEqual(result.trace.reranker_model, "BAAI/bge-reranker-v2-m3")
        self.assertTrue(result.trace.retrieval_candidates)
        self.assertTrue(
            all(
                isinstance(candidate.get("candidate_trace"), dict)
                for candidate in result.trace.retrieval_candidates
            )
        )
        self.assertTrue(
            any(
                "p_bm25" in (candidate["candidate_trace"].get("retrieval_sources") or [])
                for candidate in result.trace.retrieval_candidates
            )
        )

    def test_run_rag_query_short_how_to_faq_uses_vector_first_pass_without_light_path(self) -> None:
        def _vector_chunk() -> RetrievedChunk:
            return RetrievedChunk(
                chunk_id="vec-join",
                text="Call joinChannel with the same channel name on each client.",
                source_path="official/get-started.md",
                similarity=0.95,
            )

        query_understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="how to join channel",
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[{"canonical_term": "Channel"}],
            retrieval_plan=RetrievalPlan(
                semantic_query="how to join channel",
                soft_signals={"topic": ["channel lifecycle"], "use_case": ["join_channel"]},
                rule_expansions=["channel name"],
            ),
            rewritten_queries=["join channel with token"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=2.0,
            rewrite_latency_ms=1.0,
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
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch("backend.services.rag_qa._resolve_active_vector_table", return_value="supportportal.docagent_chunks_bge_m3_1024"), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=query_understanding,
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                return_value=None,
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=lambda *args, **kwargs: [_vector_chunk()],
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                side_effect=AssertionError("bm25 warmup should be skipped for short how-to FAQ first pass"),
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                side_effect=AssertionError("fts retrieval should not run when vector first pass is sufficient"),
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                side_effect=lambda *args, **kwargs: ([_vector_chunk()], {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None}),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=lambda *args, **kwargs: [_vector_chunk()],
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                return_value=(
                    {
                        "answer": "Call joinChannel with the same channel name on each client.",
                        "key_steps": [],
                        "citations": ["vec-join"],
                        "insufficient_evidence": False,
                    },
                    10,
                    5,
                    "gpt-5.4",
                ),
            ):
                result = run_rag_query("how to join channel")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.answer.answer, "Call joinChannel with the same channel name on each client.")
        self.assertEqual(result.trace.selected_chunk_ids[0], "vec-join")
        self.assertEqual(result.trace.query_class, "how_to_faq")
        self.assertFalse(result.trace.light_path_used)
        self.assertFalse(result.trace.vector_setup_skipped)
        self.assertEqual(result.trace.answer_profile_used, "gpt-5.4")
        self.assertFalse(result.trace.answer_profile_fallback_used)

    def test_run_rag_query_exact_error_lookup_uses_light_path_fast_answer_profile_then_falls_back_to_main_model(self) -> None:
        bm25_chunk = RetrievedChunk(
            chunk_id="bm25-error-109",
            text="Error 109 means the token is expired.",
            source_path="official/error-codes.md",
            similarity=0.95,
        )
        captured_models: list[tuple[str, str]] = []

        def _capture_answer_call(*, profile, system_prompt: str, user_prompt: str, extra_payload=None):
            _ = system_prompt
            _ = user_prompt
            _ = extra_payload
            captured_models.append((str(profile.model), str(profile.reasoning_effort)))
            if len(captured_models) == 1:
                return LlmTextResult(
                    text=(
                        '{"answer":"Error 109 means the token is expired.",'
                        '"key_steps":[],"citations":[],"insufficient_evidence":false}'
                    ),
                    model_name=str(profile.model),
                    prompt_tokens=10,
                    completion_tokens=5,
                )
            return LlmTextResult(
                text=(
                    '{"answer":"Error 109 means the token is expired.",'
                    '"key_steps":[],"citations":["bm25-error-109"],"insufficient_evidence":false}'
                ),
                model_name=str(profile.model),
                prompt_tokens=12,
                completion_tokens=6,
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
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch("backend.services.rag_qa._resolve_active_vector_table", return_value=None):
                with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                    with patch(
                        "backend.services.rag_qa._retrieve_bm25_chunks",
                        return_value=[bm25_chunk],
                    ), patch(
                        "backend.services.rag_qa._retrieve_fts_chunks",
                        return_value=[],
                    ), patch(
                        "backend.services.rag_qa._metadata_rerank",
                        return_value=([bm25_chunk], {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None}),
                    ), patch(
                        "backend.services.rag_qa._rerank_chunks",
                        side_effect=AssertionError("external rerank should be skipped for simple lexical queries"),
                    ), patch(
                        "backend.services.rag_qa.invoke_responses_text",
                        side_effect=_capture_answer_call,
                    ):
                        result = run_rag_query("what does error 109 mean")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            captured_models,
            [("gpt-5.4-mini", "low"), ("gpt-5.4", "high")],
        )
        self.assertEqual(result.trace.query_class, "lexical_exact")
        self.assertTrue(result.trace.light_path_used)
        self.assertTrue(result.trace.vector_setup_skipped)
        self.assertEqual(result.trace.answer_profile_used, "gpt-5.4")
        self.assertTrue(result.trace.answer_profile_fallback_used)

    def test_run_rag_query_generic_join_channel_prefers_rtc_join_and_token_contexts_for_audio_video_calling(self) -> None:
        stream_chunk = RetrievedChunk(
            chunk_id="stream-join",
            text="Use a random user ID to join a stream channel.",
            source_path="official/stream-channel_macos.md",
            similarity=0.96,
            h1="Stream channels",
            h2="Implement communication in a stream channel",
            h3="Join a stream channel",
            metadata={
                "product": "signaling",
                "source_family": "signaling/stream-channel",
            },
        )
        multi_chunk = RetrievedChunk(
            chunk_id="multi-join",
            text="Call joinChannelEx to join multiple channels at the same time.",
            source_path="official/join-multiple-channels_android.md",
            similarity=0.94,
            h1="Video Calling",
            h2="Join multiple channels",
            h3="Android",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/advanced/join-multiple-channels",
            },
        )
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.87,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        auth_chunk = RetrievedChunk(
            chunk_id="auth-android",
            text="Request a token from your app server for the channel name and user ID before joining.",
            source_path="official/authentication-workflow_android.md",
            similarity=0.86,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        captured_final_chunk_ids: list[list[str]] = []

        def _capture_payload(
            message: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            strict_retry: bool = False,
            packed_evidence=None,
            product: str | None = None,
            **_: object,
        ) -> tuple[dict[str, object], int, int, str]:
            _ = message
            _ = config
            _ = strict_retry
            _ = packed_evidence
            _ = product
            captured_final_chunk_ids.append([chunk.chunk_id for chunk in chunks])
            return (
                {
                    "answer": "Request a token, keep the channel name and user ID ready, then call joinChannel.",
                    "key_steps": [],
                    "citations": [chunk.chunk_id for chunk in chunks[:2]],
                    "insufficient_evidence": False,
                },
                10,
                5,
                "gpt-5.4",
            )

        query_understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="how to join channel",
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[{"canonical_term": "Channel"}],
            retrieval_plan=RetrievalPlan(
                semantic_query="how to join channel",
                soft_signals={"topic": ["channel lifecycle"], "use_case": ["join_channel"]},
                rule_expansions=["joinChannel token uid"],
            ),
            rewritten_queries=["join channel token uid"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=2.0,
            rewrite_latency_ms=1.0,
        )

        def _vector_chunks(*args: object, **kwargs: object) -> list[RetrievedChunk]:
            _ = args
            _ = kwargs
            return [
                rag_qa._copy_chunk(stream_chunk),
                rag_qa._copy_chunk(multi_chunk),
                rag_qa._copy_chunk(auth_chunk),
                rag_qa._copy_chunk(join_chunk),
            ]

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
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
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=query_understanding,
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                return_value=None,
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=_vector_chunks,
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=lambda query, chunks, config, *, limit=None: chunks,
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                side_effect=_capture_payload,
            ):
                result = run_rag_query("how to join channel", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(set(captured_final_chunk_ids[0][:2]), {"join-android", "auth-android"})
        self.assertNotIn("stream-join", captured_final_chunk_ids[0][:2])
        self.assertNotIn("multi-join", captured_final_chunk_ids[0][:2])
        self.assertEqual(set(result.trace.cited_chunk_ids), {"join-android", "auth-android"})
        self.assertEqual(result.trace.query_class, "how_to_faq")
        self.assertFalse(result.trace.light_path_used)
        self.assertFalse(result.trace.vector_setup_skipped)

    def test_run_rag_query_generic_join_channel_retries_for_second_supporting_citation(self) -> None:
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.91,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        auth_chunk = RetrievedChunk(
            chunk_id="auth-android",
            text="Request a token from your app server for the channel name and user ID before joining.",
            source_path="official/authentication-workflow_android.md",
            similarity=0.9,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        call_records: list[dict[str, object]] = []

        def _capture_payload(
            message: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            strict_retry: bool = False,
            packed_evidence=None,
            product: str | None = None,
            citation_retry: bool = False,
            **_: object,
        ) -> tuple[dict[str, object], int, int, str]:
            _ = message
            _ = config
            _ = packed_evidence
            _ = product
            call_records.append(
                {
                    "strict_retry": strict_retry,
                    "citation_retry": citation_retry,
                    "chunk_ids": [chunk.chunk_id for chunk in chunks],
                }
            )
            if len(call_records) == 1:
                return (
                    {
                        "answer": "Request a token, then call joinChannel to join the channel.",
                        "key_steps": [],
                        "citations": ["join-android"],
                        "insufficient_evidence": False,
                    },
                    10,
                    5,
                    "gpt-5.4",
                )
            return (
                {
                    "answer": "Request a token with the channel name and user ID, then call joinChannel.",
                    "key_steps": [],
                    "citations": ["join-android", "auth-android"],
                    "insufficient_evidence": False,
                },
                12,
                6,
                "gpt-5.4",
            )

        query_understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="how to join channel",
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[{"canonical_term": "Channel"}],
            retrieval_plan=RetrievalPlan(
                semantic_query="how to join channel",
                soft_signals={"topic": ["channel lifecycle"], "use_case": ["join_channel"]},
                rule_expansions=["joinChannel token uid"],
            ),
            rewritten_queries=["join channel token uid"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=2.0,
            rewrite_latency_ms=1.0,
        )

        def _vector_chunks(*args: object, **kwargs: object) -> list[RetrievedChunk]:
            _ = args
            _ = kwargs
            return [
                rag_qa._copy_chunk(join_chunk),
                rag_qa._copy_chunk(auth_chunk),
            ]

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
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
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=query_understanding,
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                return_value=None,
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=_vector_chunks,
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=lambda query, chunks, config, *, limit=None: chunks,
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                side_effect=_capture_payload,
            ):
                result = run_rag_query("how to join channel", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(len(call_records), 2)
        self.assertFalse(bool(call_records[0]["citation_retry"]))
        self.assertTrue(bool(call_records[1]["citation_retry"]))
        self.assertEqual(set(result.trace.cited_chunk_ids), {"join-android", "auth-android"})
        self.assertEqual(result.trace.citation_count, 2)

    def test_run_rag_query_generic_join_channel_light_path_recovers_from_wrong_family_mix(self) -> None:
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.91,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        auth_chunk = RetrievedChunk(
            chunk_id="auth-android",
            text="Request a token from your app server for the channel name and user ID before joining.",
            source_path="official/authentication-workflow_android.md",
            similarity=0.9,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        broadcast_auth_chunk = RetrievedChunk(
            chunk_id="auth-broadcast",
            text="Use a token to join a channel in the Web implementation.",
            source_path="official/authentication-workflow_web.md",
            similarity=0.92,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "broadcast-streaming",
                "source_family": "broadcast-streaming/token-authentication/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        stream_chunk = RetrievedChunk(
            chunk_id="stream-join",
            text="Use a random user ID to join a stream channel.",
            source_path="official/stream-channel_macos.md",
            similarity=0.88,
            h1="Stream channels",
            h2="Implement communication in a stream channel",
            h3="Join a stream channel",
            metadata={
                "product": "signaling",
                "source_family": "signaling/core-functionality/stream-channel",
            },
        )
        multi_chunk = RetrievedChunk(
            chunk_id="multi-join",
            text="Join the channel using a random user ID.",
            source_path="official/join-multiple-channels_android.md",
            similarity=0.87,
            h1="Join multiple channels",
            h2="Implementation",
            h3="Android",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/advanced-features/join-multiple-channels",
            },
        )
        captured_calls: list[list[str]] = []

        def _bm25_side_effect(query: str, config: dict[str, object], *, limit: int, index_role: str = "primary") -> list[RetrievedChunk]:
            _ = config
            _ = limit
            _ = index_role
            normalized = " ".join(str(query or "").split()).strip().lower()
            if "basic authentication" in normalized:
                return [join_chunk, auth_chunk]
            return [multi_chunk, stream_chunk]

        def _fts_side_effect(query: str, config: dict[str, object], *, limit: int, index_role: str = "primary") -> list[RetrievedChunk]:
            _ = config
            _ = limit
            _ = index_role
            normalized = " ".join(str(query or "").split()).strip().lower()
            if "basic authentication" in normalized:
                return [auth_chunk, join_chunk]
            return [broadcast_auth_chunk]

        def _capture_payload(
            message: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            strict_retry: bool = False,
            packed_evidence=None,
            product: str | None = None,
            citation_retry: bool = False,
            **_: object,
        ) -> tuple[dict[str, object], int, int, str]:
            _ = message
            _ = config
            _ = strict_retry
            _ = packed_evidence
            _ = product
            _ = citation_retry
            captured_calls.append([chunk.chunk_id for chunk in chunks])
            return (
                {
                    "answer": "Request a token for the channel name and user ID, then call joinChannel(token, channelName, uid, options).",
                    "key_steps": [],
                    "citations": ["join-android", "auth-android"],
                    "insufficient_evidence": False,
                },
                10,
                5,
                "gpt-5.4-mini",
            )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
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
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                side_effect=AssertionError("vector table resolution should be skipped for simple lexical queries"),
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                side_effect=AssertionError("embedding provider should be skipped for simple lexical queries"),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                side_effect=AssertionError("query understanding should be skipped for simple lexical queries"),
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                side_effect=AssertionError("agent planner should be skipped for simple lexical queries"),
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=AssertionError("vector retrieval should be skipped for simple lexical queries"),
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                side_effect=_bm25_side_effect,
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                side_effect=_fts_side_effect,
            ), patch(
                "backend.services.rag_qa._retrieve_keyword_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                side_effect=_capture_payload,
            ):
                result = run_rag_query("how to join channel", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(captured_calls[0][:2], ["join-android", "auth-android"])
        self.assertEqual(set(result.trace.cited_chunk_ids), {"join-android", "auth-android"})
        self.assertEqual(result.trace.selected_chunk_ids[:2], ["join-android", "auth-android"])
        self.assertEqual(result.trace.citation_count, 2)

    def test_run_rag_query_join_multiple_channels_keeps_multi_channel_context(self) -> None:
        multi_chunk = RetrievedChunk(
            chunk_id="multi-join",
            text="Call joinChannelEx to join multiple channels at the same time.",
            source_path="official/join-multiple-channels_android.md",
            similarity=0.95,
            h1="Video Calling",
            h2="Join multiple channels",
            h3="Android",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/advanced/join-multiple-channels",
            },
        )
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.9,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        captured_final_chunk_ids: list[list[str]] = []

        def _capture_payload(
            message: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            strict_retry: bool = False,
            packed_evidence=None,
            product: str | None = None,
            **_: object,
        ) -> tuple[dict[str, object], int, int, str]:
            _ = message
            _ = config
            _ = strict_retry
            _ = packed_evidence
            _ = product
            captured_final_chunk_ids.append([chunk.chunk_id for chunk in chunks])
            return (
                {
                    "answer": "Use joinChannelEx for multiple channels.",
                    "key_steps": [],
                    "citations": ["multi-join"],
                    "insufficient_evidence": False,
                },
                10,
                5,
                "gpt-5.4",
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
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[multi_chunk]), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[join_chunk],
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                side_effect=_capture_payload,
            ):
                result = run_rag_query("how to join multiple channels", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(captured_final_chunk_ids[0][0], "multi-join")
        self.assertEqual(result.trace.selected_chunk_ids[0], "multi-join")

    def test_run_rag_query_join_stream_channel_keeps_stream_channel_context(self) -> None:
        stream_chunk = RetrievedChunk(
            chunk_id="stream-join",
            text="Use a random user ID to join a stream channel.",
            source_path="official/stream-channel_macos.md",
            similarity=0.95,
            h1="Stream channels",
            h2="Implement communication in a stream channel",
            h3="Join a stream channel",
            metadata={
                "product": "signaling",
                "source_family": "signaling/stream-channel",
            },
        )
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.9,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        captured_final_chunk_ids: list[list[str]] = []

        def _capture_payload(
            message: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            strict_retry: bool = False,
            packed_evidence=None,
            product: str | None = None,
            **_: object,
        ) -> tuple[dict[str, object], int, int, str]:
            _ = message
            _ = config
            _ = strict_retry
            _ = packed_evidence
            _ = product
            captured_final_chunk_ids.append([chunk.chunk_id for chunk in chunks])
            return (
                {
                    "answer": "Join the stream channel with the stream-channel flow.",
                    "key_steps": [],
                    "citations": ["stream-join"],
                    "insufficient_evidence": False,
                },
                10,
                5,
                "gpt-5.4",
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
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[stream_chunk]), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[join_chunk],
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                side_effect=_capture_payload,
            ):
                result = run_rag_query("how to join a stream channel", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(captured_final_chunk_ids[0][0], "stream-join")
        self.assertEqual(result.trace.selected_chunk_ids[0], "stream-join")

    def test_rerank_quota_failure_enters_process_cooldown(self) -> None:
        chunk = RetrievedChunk(
            chunk_id="chunk-rerank",
            text="Call joinChannel with a token.",
            source_path="official/get-started.md",
            similarity=0.91,
        )
        config = {
            "rerank_provider": "siliconflow",
            "rerank_api_key": "test-rerank-key",
            "rerank_base_url": "https://api.siliconflow.cn/v1",
            "rerank_model": "BAAI/bge-reranker-v2-m3",
            "rerank_timeout_seconds": 10.0,
            "rerank_max_retries": 0,
            "rerank_top_n": 1,
        }

        def _http_403() -> urllib.error.HTTPError:
            return urllib.error.HTTPError(
                url="https://api.siliconflow.cn/v1/rerank",
                code=403,
                msg="Forbidden",
                hdrs=None,
                fp=io.BytesIO(b'{"message":"insufficient balance"}'),
            )

        with patch.dict(rag_qa.__dict__, {"_RUNTIME_CAPABILITY_UNAVAILABLE_UNTIL": {}}, clear=False), patch(
            "backend.services.rag_qa.time.time",
            return_value=100.0,
        ), patch(
            "urllib.request.urlopen",
            side_effect=[_http_403(), _http_403()],
        ) as urlopen_mock:
            first = _rerank_chunks("how to join channel", [chunk], dict(config), limit=1)
            second = _rerank_chunks("how to join channel", [chunk], dict(config), limit=1)

        self.assertEqual(urlopen_mock.call_count, 1)
        self.assertEqual([item.chunk_id for item in first], ["chunk-rerank"])
        self.assertEqual([item.chunk_id for item in second], ["chunk-rerank"])

    def test_run_rag_query_records_keyword_fallback_as_agentic_tool(self) -> None:
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
        self.assertEqual(result.trace.retrieval_strategy, "agentic_multi_tool_v1")
        self.assertTrue(result.trace.agent_enabled)
        self.assertTrue(
            any(
                "p_keyword" in (candidate["candidate_trace"].get("retrieval_sources") or [])
                for candidate in result.trace.retrieval_candidates
            )
        )

    def test_run_rag_query_diversifies_final_chunks_before_generation(self) -> None:
        query = "how do I handle token authentication errors?"
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
        understanding = QueryUnderstandingResult(
            query_profile="test-hybrid",
            query_understanding_version="query-understanding-test",
            glossary_version="glossary-test",
            self_query_version="self-query-test",
            normalized_query=query,
            canonical_terms=[],
            glossary_hits=[],
            dictionary_hits=[],
            rewritten_queries=[],
            decomposition_subqueries=[],
            retrieval_plan=RetrievalPlan(semantic_query=query),
            fallback_mode="none",
        )

        def _capture_payload(
            message: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            strict_retry: bool = False,
            packed_evidence=None,
            product: str | None = None,
            **_: object,
        ) -> tuple[dict[str, object], int, int, str]:
            _ = message
            _ = config
            _ = strict_retry
            _ = packed_evidence
            _ = product
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
                                with patch("backend.services.rag_qa.understand_rag_query", return_value=understanding):
                                    with patch("backend.services.rag_qa._invoke_llm_payload_with_trace", side_effect=_capture_payload):
                                        result = run_rag_query(query)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(captured_final_chunk_ids[0], ["auth-android", "error-codes"])
        self.assertEqual(result.trace.selected_chunk_ids, ["auth-android", "error-codes"])

    def test_run_rag_query_diversifies_rerank_candidates_before_external_rerank(self) -> None:
        query = "how do I handle token authentication errors?"
        auth_android = RetrievedChunk(
            chunk_id="auth-android",
            text="Android authentication workflow",
            source_path="en/android/authentication-workflow.md",
            similarity=0.99,
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/authentication-workflow",
            },
        )
        auth_ios = RetrievedChunk(
            chunk_id="auth-ios",
            text="iOS authentication workflow",
            source_path="en/ios/authentication-workflow.md",
            similarity=0.98,
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/authentication-workflow",
            },
        )
        error_codes = RetrievedChunk(
            chunk_id="error-codes",
            text="Common SDK error codes",
            source_path="en/android/error-codes.md",
            similarity=0.97,
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/reference/error-codes",
            },
        )
        captured_rerank_inputs: list[list[str]] = []
        understanding = QueryUnderstandingResult(
            query_profile="test-hybrid",
            query_understanding_version="query-understanding-test",
            glossary_version="glossary-test",
            self_query_version="self-query-test",
            normalized_query=query,
            canonical_terms=[],
            glossary_hits=[],
            dictionary_hits=[],
            rewritten_queries=[],
            decomposition_subqueries=[],
            retrieval_plan=RetrievalPlan(semantic_query=query),
            fallback_mode="none",
        )

        def _capture_rerank(
            query: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            limit: int,
        ) -> list[RetrievedChunk]:
            _ = query
            _ = config
            _ = limit
            captured_rerank_inputs.append([chunk.chunk_id for chunk in chunks])
            return list(chunks)

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
                "rerank_top_n": 3,
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
                            return_value=(
                                [auth_android, auth_ios, error_codes],
                                {"post_rerank_count": 3, "hints": {}, "applied_filter": False, "filter_type": None},
                            ),
                        ):
                            with patch("backend.services.rag_qa._rerank_chunks", side_effect=_capture_rerank):
                                with patch("backend.services.rag_qa.understand_rag_query", return_value=understanding):
                                    with patch(
                                        "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                        return_value=(
                                            {
                                                "answer": "Use the selected chunks.",
                                                "key_steps": [],
                                                "citations": ["auth-android"],
                                                "insufficient_evidence": False,
                                            },
                                            10,
                                            5,
                                            "gpt-4.1",
                                        ),
                                    ):
                                        result = run_rag_query(query)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(captured_rerank_inputs[0], ["auth-android", "error-codes", "auth-ios"])

    def test_run_rag_query_preserves_method_coverage_for_comparison_queries(self) -> None:
        wildcard_chunk = RetrievedChunk(
            chunk_id="wildcard-token",
            text="Wildcard tokens allow all users to join the same channel.",
            source_path="official/deploy-token-server.md",
            similarity=0.96,
            h1="Deploy a token server",
            h2="Generate wildcard tokens",
            metadata={
                "product": "video-calling",
                "section_path": ["Deploy a token server", "Generate wildcard tokens"],
                "use_case": "wildcard_tokens",
            },
        )
        build_token_chunk = RetrievedChunk(
            chunk_id="build-token-with-uid",
            text="BuildTokenWithUid generates a token with appId, appCertificate, channelName, uid, role, and expiration.",
            source_path="official/deploy-token-server.md",
            similarity=0.93,
            h1="Deploy a token server",
            h2="Reference",
            h3="BuildTokenWithUid",
            metadata={
                "product": "video-calling",
                "language": "nodejs",
                "method_name": "BuildTokenWithUid",
                "section_path": ["Reference", "API Reference", "BuildTokenWithUid"],
                "use_case": "basic_authentication",
            },
        )
        privilege_chunk = RetrievedChunk(
            chunk_id="build-token-with-uid-privilege",
            text="BuildTokenWithUidAndPrivilege adds per-privilege expirations to the token payload.",
            source_path="official/deploy-token-server.md",
            similarity=0.92,
            h1="Deploy a token server",
            h2="Reference",
            h3="BuildTokenWithUidAndPrivilege",
            metadata={
                "product": "video-calling",
                "language": "nodejs",
                "method_name": "BuildTokenWithUidAndPrivilege",
                "section_path": ["Reference", "API Reference", "BuildTokenWithUidAndPrivilege"],
                "use_case": "advanced_permissions",
            },
        )
        captured_final_chunk_ids: list[list[str]] = []

        def _capture_payload(
            message: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            strict_retry: bool = False,
            packed_evidence=None,
            product: str | None = None,
            **_: object,
        ) -> tuple[dict[str, object], int, int, str]:
            _ = message
            _ = config
            _ = strict_retry
            _ = packed_evidence
            _ = product
            captured_final_chunk_ids.append([chunk.chunk_id for chunk in chunks])
            return (
                {
                    "answer": "The privilege variant supports privilege-level expirations.",
                    "key_steps": [],
                    "citations": [chunk.chunk_id for chunk in chunks[:2]],
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
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[wildcard_chunk]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[build_token_chunk, privilege_chunk]):
                        with patch(
                            "backend.services.rag_qa._metadata_rerank",
                            return_value=(
                                [wildcard_chunk, build_token_chunk, privilege_chunk],
                                {"post_rerank_count": 3, "hints": {}, "applied_filter": False, "filter_type": None},
                            ),
                        ):
                            with patch(
                                "backend.services.rag_qa._rerank_chunks",
                                return_value=[wildcard_chunk, build_token_chunk, privilege_chunk],
                            ):
                                with patch("backend.services.rag_qa._invoke_llm_payload_with_trace", side_effect=_capture_payload):
                                    result = run_rag_query(
                                        "BuildTokenWithUid 和 BuildTokenWithUidAndPrivilege 有什么区别？"
                                    )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            captured_final_chunk_ids[0],
            ["build-token-with-uid", "build-token-with-uid-privilege"],
        )
        self.assertEqual(
            result.trace.selected_chunk_ids,
            ["build-token-with-uid", "build-token-with-uid-privilege"],
        )

    def test_run_rag_query_preserves_method_coverage_in_rerank_candidate_window_for_comparison_queries(self) -> None:
        wildcard_chunk = RetrievedChunk(
            chunk_id="wildcard-token",
            text="Wildcard tokens allow all users to join the same channel.",
            source_path="official/deploy-token-server.md",
            similarity=0.96,
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/wildcard-tokens",
                "section_path": ["Deploy a token server", "Generate wildcard tokens"],
                "use_case": "wildcard_tokens",
            },
        )
        build_token_chunk = RetrievedChunk(
            chunk_id="build-token-with-uid",
            text="BuildTokenWithUid generates a token with appId, appCertificate, channelName, uid, role, and expiration.",
            source_path="official/deploy-token-server.md",
            similarity=0.93,
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/reference/deploy-token-server",
                "language": "nodejs",
                "method_name": "BuildTokenWithUid",
                "section_path": ["Reference", "API Reference", "BuildTokenWithUid"],
                "use_case": "basic_authentication",
            },
        )
        privilege_chunk = RetrievedChunk(
            chunk_id="build-token-with-uid-privilege",
            text="BuildTokenWithUidAndPrivilege adds per-privilege expirations to the token payload.",
            source_path="official/deploy-token-server.md",
            similarity=0.92,
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/reference/deploy-token-server",
                "language": "nodejs",
                "method_name": "BuildTokenWithUidAndPrivilege",
                "section_path": ["Reference", "API Reference", "BuildTokenWithUidAndPrivilege"],
                "use_case": "advanced_permissions",
            },
        )
        captured_rerank_inputs: list[list[str]] = []

        def _capture_rerank(
            query: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            limit: int,
        ) -> list[RetrievedChunk]:
            _ = query
            _ = config
            _ = limit
            captured_rerank_inputs.append([chunk.chunk_id for chunk in chunks])
            return list(chunks)

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
                "rerank_top_n": 3,
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
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[wildcard_chunk]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[build_token_chunk, privilege_chunk]):
                        with patch(
                            "backend.services.rag_qa._metadata_rerank",
                            return_value=(
                                [wildcard_chunk, build_token_chunk, privilege_chunk],
                                {"post_rerank_count": 3, "hints": {}, "applied_filter": False, "filter_type": None},
                            ),
                        ):
                            with patch("backend.services.rag_qa._rerank_chunks", side_effect=_capture_rerank):
                                with patch(
                                    "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                    return_value=(
                                        {
                                            "answer": "The privilege variant supports privilege-level expirations.",
                                            "key_steps": [],
                                            "citations": ["build-token-with-uid", "build-token-with-uid-privilege"],
                                            "insufficient_evidence": False,
                                        },
                                        10,
                                        5,
                                        "gpt-4.1",
                                    ),
                                ):
                                    result = run_rag_query(
                                        "BuildTokenWithUid 和 BuildTokenWithUidAndPrivilege 有什么区别？"
                                    )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            captured_rerank_inputs[0],
            ["build-token-with-uid", "build-token-with-uid-privilege", "wildcard-token"],
        )

    def test_run_rag_query_repairs_false_insufficient_evidence_before_fallback(self) -> None:
        token_server_chunk = RetrievedChunk(
            chunk_id="token-server",
            text="In production, your app server should generate the Agora token instead of the mobile client.",
            source_path="official/deploy-token-server.md",
            similarity=0.95,
            h1="Deploy a token server",
            h2="Token generation code",
            h3="Basic authentication",
            metadata={
                "product": "video-calling",
                "section_path": ["Deploy a token server", "Token generation code", "Basic authentication"],
                "use_case": "basic_authentication",
            },
        )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 1,
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
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[token_server_chunk]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[]):
                        with patch(
                            "backend.services.rag_qa._metadata_rerank",
                            return_value=([token_server_chunk], {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None}),
                        ):
                            with patch("backend.services.rag_qa._rerank_chunks", return_value=[token_server_chunk]):
                                with patch("backend.services.rag_qa._has_grounded_keyword_overlap", return_value=True):
                                    with patch(
                                        "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                        side_effect=[
                                            (
                                                {
                                                    "answer": INSUFFICIENT_EVIDENCE_REPLY,
                                                    "key_steps": [],
                                                    "citations": [],
                                                    "insufficient_evidence": True,
                                                },
                                                10,
                                                5,
                                                "gpt-4.1",
                                            ),
                                            (
                                                {
                                                    "answer": "Generate the Agora token on your app server in production.",
                                                    "key_steps": [],
                                                    "citations": ["token-server"],
                                                    "insufficient_evidence": False,
                                                },
                                                12,
                                                6,
                                                "gpt-4.1",
                                            ),
                                        ],
                                    ):
                                        result = run_rag_query(
                                            "Should I generate the Agora token on the mobile app or on my backend?"
                                        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            result.answer.answer,
            "Generate the Agora token on your app server in production.",
        )
        self.assertTrue(result.trace.structured_retry_used)
        self.assertEqual(result.trace.generation_mode, "structured_answer")
        self.assertFalse(result.trace.extractive_fallback_used)

    def test_run_rag_query_repairs_invalid_structured_payload_before_returning_answer(self) -> None:
        token_server_chunk = RetrievedChunk(
            chunk_id="token-server",
            text="In production, your app server should generate the Agora token instead of the mobile client.",
            source_path="official/deploy-token-server.md",
            similarity=0.95,
            h1="Deploy a token server",
            h2="Token generation code",
            h3="Basic authentication",
            metadata={
                "product": "video-calling",
                "section_path": ["Deploy a token server", "Token generation code", "Basic authentication"],
                "use_case": "basic_authentication",
            },
        )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 1,
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
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[token_server_chunk]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[]):
                        with patch(
                            "backend.services.rag_qa._metadata_rerank",
                            return_value=([token_server_chunk], {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None}),
                        ):
                            with patch("backend.services.rag_qa._rerank_chunks", return_value=[token_server_chunk]):
                                with patch(
                                    "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                    side_effect=[
                                        (
                                            {
                                                "answer": "Generate the Agora token on your app server in production.",
                                                "key_steps": [],
                                                "citations": [],
                                                "insufficient_evidence": False,
                                            },
                                            10,
                                            5,
                                            "gpt-4.1",
                                        ),
                                        (
                                            {
                                                "answer": "Generate the Agora token on your app server in production.",
                                                "key_steps": [],
                                                "citations": ["token-server"],
                                                "insufficient_evidence": False,
                                            },
                                            12,
                                            6,
                                            "gpt-4.1",
                                        ),
                                    ],
                                ):
                                    result = run_rag_query(
                                        "Should I generate the Agora token on the mobile app or on my backend?"
                                    )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            result.answer.answer,
            "Generate the Agora token on your app server in production.",
        )
        self.assertTrue(result.trace.structured_retry_used)
        self.assertEqual(result.trace.cited_chunk_ids, ["token-server"])
        self.assertEqual(result.trace.generation_mode, "structured_answer")

    def test_run_rag_query_uses_evidence_oriented_extractive_fallback_as_last_resort(self) -> None:
        token_server_chunk = RetrievedChunk(
            chunk_id="token-server",
            text="In production, your app server should generate the Agora token instead of the mobile client.",
            source_path="official/deploy-token-server.md",
            similarity=0.95,
            h1="Deploy a token server",
            h2="Token generation code",
            h3="Basic authentication",
            metadata={
                "product": "video-calling",
                "section_path": ["Deploy a token server", "Token generation code", "Basic authentication"],
                "use_case": "basic_authentication",
            },
        )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 1,
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
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[token_server_chunk]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[]):
                        with patch(
                            "backend.services.rag_qa._metadata_rerank",
                            return_value=([token_server_chunk], {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None}),
                        ):
                            with patch("backend.services.rag_qa._rerank_chunks", return_value=[token_server_chunk]):
                                with patch(
                                    "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                    side_effect=[
                                        (None, 10, 5, "gpt-4.1"),
                                        (None, 11, 5, "gpt-4.1"),
                                    ],
                                ):
                                    result = run_rag_query("How do I generate a token for users joining a channel?")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.generation_mode, "extractive_fallback")
        self.assertTrue(result.trace.extractive_fallback_used)
        self.assertTrue(result.trace.needs_human)
        self.assertEqual(result.trace.handoff_reason, "insufficient_evidence")
        self.assertIn("Evidence:", result.answer.answer)
        self.assertIn("Token generation code", result.answer.answer)
        self.assertNotIn("Key Steps:", result.answer.answer)

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

    def test_probe_customer_rag_index_readiness_reports_configured_table_empty(self) -> None:
        with patch("backend.services.rag_qa._get_rag_config", return_value={
            "dsn": "postgresql://example",
            "api_key": "test-key",
            "table": "supportportal.docagent_chunks_bge_m3_1024",
            "vector_enabled": True,
        }):
            with patch("backend.services.rag_qa._count_primary_rows_in_table", return_value=0):
                with patch(
                    "backend.services.rag_qa._resolve_active_vector_table",
                    return_value="supportportal.docagent_chunks_bge_m3_1024",
                ):
                    readiness = probe_customer_rag_index_readiness()

        self.assertEqual(readiness.status, "configured_table_empty")
        self.assertEqual(readiness.configured_table, "supportportal.docagent_chunks_bge_m3_1024")
        self.assertEqual(readiness.resolved_table, "supportportal.docagent_chunks_bge_m3_1024")
        self.assertEqual(readiness.configured_primary_rows, 0)

    def test_probe_customer_rag_index_readiness_reports_fallback_table_selected(self) -> None:
        with patch("backend.services.rag_qa._get_rag_config", return_value={
            "dsn": "postgresql://example",
            "api_key": "test-key",
            "table": "supportportal.docagent_chunks_bge_m3_1024",
            "vector_enabled": True,
        }):
            with patch("backend.services.rag_qa._count_primary_rows_in_table", return_value=0):
                with patch(
                    "backend.services.rag_qa._resolve_active_vector_table",
                    return_value="supportportal.docagent_chunks_ag_docs_test_1024",
                ):
                    readiness = probe_customer_rag_index_readiness()

        self.assertEqual(readiness.status, "fallback_table_selected")
        self.assertEqual(readiness.configured_table, "supportportal.docagent_chunks_bge_m3_1024")
        self.assertEqual(readiness.resolved_table, "supportportal.docagent_chunks_ag_docs_test_1024")
        self.assertEqual(readiness.configured_primary_rows, 0)

    def test_resolve_active_vector_table_uses_ttl_cache_until_expiry(self) -> None:
        config = {
            "dsn": "postgresql://example",
            "table": "supportportal.docagent_chunks_bge_m3_1024",
        }

        rag_qa.clear_active_vector_table_cache()
        with patch("backend.services.rag_qa._count_primary_rows_in_table", return_value=0) as count_mock:
            with patch("backend.services.rag_qa._list_vector_tables_with_primary_counts") as list_mock:
                list_mock.return_value = [
                    ("supportportal.docagent_chunks_bge_m3_1024", 0),
                    ("supportportal.docagent_chunks_ag_docs_test_1024", 1907),
                ]
                with patch("backend.services.rag_qa.time.time", return_value=100.0):
                    first = _resolve_active_vector_table(config)
                    second = _resolve_active_vector_table(config)
                with patch("backend.services.rag_qa.time.time", return_value=161.0):
                    third = _resolve_active_vector_table(config)

        self.assertEqual(first, "supportportal.docagent_chunks_ag_docs_test_1024")
        self.assertEqual(second, "supportportal.docagent_chunks_ag_docs_test_1024")
        self.assertEqual(third, "supportportal.docagent_chunks_ag_docs_test_1024")
        self.assertEqual(count_mock.call_count, 2)
        self.assertEqual(list_mock.call_count, 2)

    def test_run_rag_query_uses_resolved_vector_table_for_all_retrieval_paths(self) -> None:
        captured_tables: list[str] = []

        def _capture_vector(
            message: str,
            config: dict[str, object],
            *,
            limit: int | None = None,
            index_role: str = "primary",
        ) -> list[RetrievedChunk]:
            _ = message
            _ = limit
            _ = index_role
            captured_tables.append(str(config["table"]))
            return []

        def _capture_bm25(
            message: str,
            config: dict[str, object],
            *,
            limit: int | None = None,
            index_role: str = "primary",
        ) -> list[RetrievedChunk]:
            _ = message
            _ = limit
            _ = index_role
            captured_tables.append(str(config["table"]))
            return []

        def _capture_fts(
            message: str,
            config: dict[str, object],
            *,
            limit: int | None = None,
            index_role: str = "primary",
        ) -> list[RetrievedChunk]:
            _ = message
            _ = limit
            _ = index_role
            captured_tables.append(str(config["table"]))
            return []

        def _capture_keyword(
            message: str,
            config: dict[str, object],
            *,
            limit: int | None = None,
            index_role: str = "primary",
        ) -> list[RetrievedChunk]:
            _ = message
            _ = limit
            _ = index_role
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
                            with patch("backend.services.rag_qa._retrieve_fts_chunks", side_effect=_capture_fts):
                                with patch("backend.services.rag_qa._retrieve_keyword_chunks", side_effect=_capture_keyword):
                                    result = run_rag_query("why does audio fail when joining a channel")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            set(captured_tables),
            {"supportportal.docagent_chunks_ag_docs_test_1024"},
        )
        self.assertTrue(result.trace.needs_human)

    def test_run_rag_query_records_query_understanding_metadata_in_trace(self) -> None:
        vector_chunk = RetrievedChunk(
            chunk_id="chunk-1",
            text="Cloud Recording troubleshooting",
            source_path="official/cloud-recording.md",
            similarity=0.96,
            metadata={"product": "video-calling", "chunk_type": "troubleshooting_procedure"},
        )

        understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v1",
            glossary_version="video-calling_glossary_en_v1",
            self_query_version="v1",
            normalized_query="How do I troubleshoot Cloud Recording jitter?",
            canonical_terms=["Cloud Recording", "Jitter"],
            glossary_hits=[
                {
                    "canonical_term": "Cloud Recording",
                    "matched_text": "Cloud Recording",
                    "definition": "Cloud Recording is a component provided by Agora.",
                },
                {
                    "canonical_term": "Jitter",
                    "matched_text": "jitter",
                    "definition": "Jitter is the variation in delay of data packets.",
                },
            ],
            dictionary_hits=[
                {
                    "source": "glossary",
                    "canonical_term": "Cloud Recording",
                    "matched_text": "Cloud Recording",
                    "definition": "Cloud Recording is a component provided by Agora.",
                },
                {
                    "source": "glossary",
                    "canonical_term": "Jitter",
                    "matched_text": "jitter",
                    "definition": "Jitter is the variation in delay of data packets.",
                },
            ],
            retrieval_plan=RetrievalPlan(
                semantic_query="cloud recording jitter troubleshooting",
                hard_filters={"product": "video-calling"},
                soft_signals={"keywords": ["jitter"], "chunk_type": ["troubleshooting_procedure"]},
                rewritten_queries=["cloud recording jitter troubleshooting"],
                decomposition_subqueries=[],
                fallback_mode="none",
            ),
            rewritten_queries=["cloud recording jitter troubleshooting"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=4.5,
            rewrite_latency_ms=3.2,
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
                with patch("backend.services.rag_qa.understand_rag_query", return_value=understanding):
                    with patch("backend.services.rag_qa._retrieve_chunks", return_value=[vector_chunk]):
                        with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[]):
                            with patch(
                                "backend.services.rag_qa._metadata_rerank",
                                return_value=(
                                    [vector_chunk],
                                    {
                                        "post_rerank_count": 1,
                                        "hints": {},
                                        "applied_filter": True,
                                        "filter_type": "product",
                                        "query_understanding": {
                                            "query_profile": "en",
                                            "glossary_hit_terms": ["Cloud Recording", "Jitter"],
                                            "applied_hard_filters": {"product": "video-calling"},
                                            "applied_soft_signals": {
                                                "keywords": ["jitter"],
                                                "chunk_type": ["troubleshooting_procedure"],
                                            },
                                            "fallback_mode": "none",
                                        },
                                    },
                                ),
                            ):
                                with patch("backend.services.rag_qa._rerank_chunks", return_value=[vector_chunk]):
                                    with patch(
                                        "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                        return_value=(
                                            {
                                                "answer": "Use Cloud Recording diagnostics.",
                                                "key_steps": [],
                                                "citations": ["chunk-1"],
                                                "insufficient_evidence": False,
                                            },
                                            10,
                                            5,
                                            "gpt-4.1",
                                        ),
                                    ):
                                        result = run_rag_query("How do I troubleshoot Cloud Recording jitter?")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.trace.query_understanding_enabled)
        self.assertEqual(result.trace.query_profile, "en")
        self.assertEqual(result.trace.query_understanding_version, "v1")
        self.assertEqual(result.trace.glossary_version, "video-calling_glossary_en_v1")
        self.assertEqual(result.trace.glossary_hit_terms, ["Cloud Recording", "Jitter"])
        self.assertEqual(result.trace.applied_hard_filters, {"product": "video-calling"})
        self.assertEqual(
            result.trace.applied_soft_signals["chunk_type"],
            ["troubleshooting_procedure"],
        )
        self.assertEqual(result.trace.rewritten_queries, ["cloud recording jitter troubleshooting"])
        self.assertEqual(result.trace.rewrite_latency_ms, 3.2)

    def test_run_rag_query_uses_prf_expansion_and_only_downpushes_rule_backed_filters(self) -> None:
        seed_chunk = RetrievedChunk(
            chunk_id="chunk-seed",
            text="Use the RTC engine to connect users.",
            source_path="official/channel.md",
            h1="Join flow",
            h2=None,
            similarity=0.92,
            metadata={
                "language": "nodejs",
                "keywords": ["channel name"],
                "topic": ["channel lifecycle"],
            },
        )
        prf_chunk = RetrievedChunk(
            chunk_id="chunk-prf",
            text="Users who join the same channel name can communicate with each other.",
            source_path="official/channel.md",
            h1="Channel",
            h2="Join by channel name",
            similarity=0.95,
            metadata={
                "language": "nodejs",
                "keywords": ["channel name"],
                "topic": ["channel lifecycle"],
            },
        )
        understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="How do I join a channel in Node.js?",
            canonical_terms=["Channel"],
            glossary_hits=[
                {
                    "source": "glossary",
                    "canonical_term": "Channel",
                    "matched_text": "channel",
                    "definition": "A channel groups users under the same channel name.",
                }
            ],
            dictionary_hits=[
                {
                    "source": "glossary",
                    "canonical_term": "Channel",
                    "matched_text": "channel",
                    "definition": "A channel groups users under the same channel name.",
                }
            ],
            retrieval_plan=RetrievalPlan(
                semantic_query="How do I join a channel in Node.js?",
                hard_filters={"language": "nodejs", "product": "video-calling"},
                soft_signals={"topic": ["channel lifecycle"]},
                rewritten_queries=[],
                decomposition_subqueries=[],
                fallback_mode="none",
                rule_expansions=[],
                llm_expansions=[],
                prf_expansions=[],
                hard_filter_sources={"language": "rule+llm", "product": "llm_only"},
                soft_signal_sources={"topic": ["rule"]},
            ),
            rewritten_queries=[],
            decomposition_subqueries=[],
            fallback_mode="none",
        )
        captured_queries: list[str] = []
        captured_downpush: list[dict[str, str]] = []

        def _capture_vector(
            query: str,
            config: dict[str, object],
            *,
            limit: int | None = None,
            index_role: str = "primary",
        ):
            _ = limit
            _ = index_role
            captured_queries.append(query)
            plan = config.get("_retrieval_plan")
            captured_downpush.append(downpush_hard_filters(plan) if isinstance(plan, RetrievalPlan) else {})
            if query == "channel name":
                return [prf_chunk]
            return [seed_chunk]

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
                "chat_model": "gpt-5.4",
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
                "app_schema": "supportportal",
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa.understand_rag_query", return_value=understanding):
                    with patch("backend.services.rag_qa._retrieve_chunks", side_effect=_capture_vector):
                        with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[]):
                            with patch(
                                "backend.services.rag_qa._metadata_rerank",
                                return_value=(
                                    [prf_chunk],
                                    {
                                        "post_rerank_count": 1,
                                        "hints": {},
                                        "applied_filter": True,
                                        "filter_type": "language",
                                        "query_understanding": {
                                            "query_profile": "en",
                                            "glossary_hit_terms": ["Channel"],
                                            "applied_hard_filters": {"language": "nodejs", "product": "video-calling"},
                                            "applied_soft_signals": {"topic": ["channel lifecycle"]},
                                            "fallback_mode": "none",
                                            "dictionary_hits": understanding.dictionary_hits,
                                            "rule_expansions": [],
                                            "llm_expansions": [],
                                            "prf_expansions": ["channel name"],
                                            "hard_filter_sources": {"language": "rule+llm", "product": "llm_only"},
                                            "cache_hit": False,
                                            "prf_used": True,
                                        },
                                    },
                                ),
                            ):
                                with patch("backend.services.rag_qa._rerank_chunks", return_value=[prf_chunk]):
                                    with patch(
                                        "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                        return_value=(
                                            {
                                                "answer": "Join the same channel by using the same channel name.",
                                                "key_steps": [],
                                                "citations": ["chunk-prf"],
                                                "insufficient_evidence": False,
                                            },
                                            10,
                                            5,
                                            "gpt-5.4",
                                        ),
                                    ):
                                        with patch.dict(os.environ, {"RAG_AGENT_ENABLED": "0"}, clear=False):
                                            result = run_rag_query("How do I join a channel in Node.js?")

        self.assertGreaterEqual(len(captured_queries), 2)
        self.assertEqual(captured_queries[0], "How do I join a channel in Node.js?")
        self.assertEqual(captured_downpush[0], {})
        self.assertIn("channel name", captured_queries)
        self.assertIn({"language": "nodejs"}, captured_downpush)
        self.assertTrue(result.trace.prf_used)
        self.assertEqual(result.trace.prf_expansions, ["channel name"])
        self.assertEqual(result.trace.hard_filter_sources["language"], "rule+llm")
        self.assertTrue(all("product" not in downpush for downpush in captured_downpush))

    def test_run_rag_query_uses_shared_packed_evidence_for_answer_and_trace(self) -> None:
        long_chunk = RetrievedChunk(
            chunk_id="chunk-1",
            text=(
                "Use joinChannel with the same channel name to enter the same communication session. "
                "The first user creates the channel and the last user leaving closes it. "
            )
            * 10,
            source_path="official/channel.md",
            similarity=0.97,
            h1="Channel",
            h2="Join a channel",
            metadata={"product": "video-calling"},
        )
        captured_prompts: list[str] = []

        def _capture_answer_call(*, profile, system_prompt: str, user_prompt: str, extra_payload=None):
            _ = system_prompt
            _ = extra_payload
            if getattr(profile, "scenario", "") == "rag_agent_planner":
                return LlmTextResult(
                    text=(
                        '{"query_class":"configuration","first_pass_tools":["p_bm25","p_fts","p_vec"],'
                        '"decomposition_targets":[],"evidence_goal":"join channel flow",'
                        '"recovery_bias":"lexical_recovery","ticket_context_used":false}'
                    ),
                    model_name="gpt-5.4-mini",
                    prompt_tokens=10,
                    completion_tokens=5,
                )
            captured_prompts.append(user_prompt)
            return LlmTextResult(
                text=(
                    '{"answer":"Use joinChannel with the same channel name.",'
                    '"key_steps":[],"citations":["chunk-1"],"insufficient_evidence":false}'
                ),
                model_name="gpt-5.4",
                prompt_tokens=10,
                completion_tokens=5,
            )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 1,
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
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_window": 900,
                "context_budget_enabled": True,
                "reserved_output_tokens": 120,
                "buffer_tokens": 80,
                "context_compression_enabled": True,
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[long_chunk]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[]):
                        with patch(
                            "backend.services.rag_qa._metadata_rerank",
                            return_value=(
                                [long_chunk],
                                {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None},
                            ),
                        ):
                            with patch("backend.services.rag_qa._rerank_chunks", return_value=[long_chunk]):
                                with patch(
                                    "backend.services.rag_qa.build_packed_evidence",
                                    return_value=PackedEvidence(
                                        budget=ContextBudget(
                                            context_window=900,
                                            system_prompt_tokens=120,
                                            history_tokens=0,
                                            prompt_tokens=90,
                                            tool_tokens=0,
                                            reserved_output_tokens=120,
                                            buffer_tokens=80,
                                            available_context_tokens=490,
                                        ),
                                        chunk_ids=["chunk-1"],
                                        prompt_context=(
                                            "[chunk-1] official/channel.md | Channel > Join a channel\n"
                                            "Use joinChannel with the same channel name to join the same channel."
                                        ),
                                        selected_contexts=[
                                            {
                                                "chunk_id": "chunk-1",
                                                "doc_id": None,
                                                "source_path": "official/channel.md",
                                                "heading": "Channel > Join a channel",
                                                "source_url": None,
                                                "source_type": None,
                                                "chunk_strategy": None,
                                                "similarity": 0.97,
                                                "metadata": {"product": "video-calling"},
                                                "rerank_score": None,
                                                "rerank_reasons": [],
                                                "text": "Use joinChannel with the same channel name to join the same channel.",
                                                "text_excerpt": "Use joinChannel with the same channel name to join the same channel.",
                                                "packing_mode": "compressive",
                                            }
                                        ],
                                        raw_context_token_estimate=480,
                                        packed_context_token_estimate=120,
                                        compression_triggered=True,
                                        compression_trigger_reason="token_budget",
                                        compression_mode="compressive",
                                        compression_model="gpt-5.4-mini",
                                        extractive_segment_count=1,
                                        packed_evidence_count=1,
                                    ),
                                ):
                                    with patch(
                                        "backend.services.rag_qa.invoke_responses_text",
                                        side_effect=_capture_answer_call,
                                    ):
                                        result = run_rag_query("How do I join a channel in Node.js with a token?")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.trace.compression_triggered)
        self.assertEqual(result.trace.compression_mode, "compressive")
        self.assertIn(
            "Use joinChannel with the same channel name to join the same channel.",
            captured_prompts[0],
        )
        self.assertEqual(
            result.trace.selected_contexts[0]["text"],
            "Use joinChannel with the same channel name to join the same channel.",
        )
        self.assertGreater(
            result.trace.raw_context_token_estimate,
            result.trace.packed_context_token_estimate,
        )

    def test_run_rag_query_agentic_starts_original_retrieval_before_query_understanding_finishes(self) -> None:
        vector_chunk = RetrievedChunk(
            chunk_id="vector-1",
            text="Vector chunk",
            source_path="official/vector.md",
            similarity=0.91,
        )
        understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="query-understanding-v1",
            glossary_version="glossary-v1",
            self_query_version="self-query-v1",
            normalized_query="How do I join a channel in Node.js with a token?",
            canonical_terms=[],
            glossary_hits=[],
            dictionary_hits=[],
            rewritten_queries=[],
            decomposition_subqueries=[],
            retrieval_plan=RetrievalPlan(semantic_query="How do I join a channel in Node.js with a token?"),
            fallback_mode="none",
        )
        retrieval_started = threading.Event()
        understanding_observed_parallel_retrieval: list[bool] = []

        def fake_understand(_: str):
            understanding_observed_parallel_retrieval.append(retrieval_started.wait(timeout=0.2))
            return understanding

        def fake_retrieve_chunks(*args, **kwargs):
            _ = args
            _ = kwargs
            return [vector_chunk]

        def fake_retrieve_bm25_chunks(*args, **kwargs):
            _ = args
            _ = kwargs
            retrieval_started.set()
            return []

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
                "chat_model": "gpt-5.4",
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
                with patch("backend.services.rag_qa.understand_rag_query", side_effect=fake_understand):
                    with patch("backend.services.rag_qa._retrieve_chunks", side_effect=fake_retrieve_chunks):
                        with patch("backend.services.rag_qa._retrieve_bm25_chunks", side_effect=fake_retrieve_bm25_chunks):
                            with patch("backend.services.rag_qa._retrieve_fts_chunks", return_value=[]):
                                with patch(
                                    "backend.services.rag_qa._metadata_rerank",
                                    return_value=([vector_chunk], {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None}),
                                ):
                                    with patch("backend.services.rag_qa._rerank_chunks", return_value=[vector_chunk]):
                                        with patch(
                                            "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                            return_value=(
                                                {
                                                    "answer": "Use joinChannel.",
                                                    "key_steps": [],
                                                    "citations": ["vector-1"],
                                                    "insufficient_evidence": False,
                                                },
                                                10,
                                                5,
                                                "gpt-5.4",
                                            ),
                                        ):
                                            with patch.dict(os.environ, {"RAG_AGENT_ENABLED": "1"}, clear=False):
                                                run_rag_query("How do I join a channel in Node.js with a token?")

        self.assertEqual(understanding_observed_parallel_retrieval, [True])


if __name__ == "__main__":
    unittest.main()
