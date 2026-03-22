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
    _rrf_merge,
    _split_table_name,
)


class RagQaHybridTests(unittest.TestCase):
    def test_split_table_name_supports_schema_prefix(self) -> None:
        self.assertEqual(_split_table_name("public.docagent"), ("public", "docagent"))
        self.assertEqual(
            _split_table_name("docagent_chunks_bge_large_en_v1_5_1024"),
            ("supportportal", "docagent_chunks_bge_large_en_v1_5_1024"),
        )

    def test_get_rag_config_uses_hybrid_candidate_windows(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = _get_rag_config(top_k=6)
        self.assertEqual(config["top_k"], 6)
        self.assertEqual(config["vector_candidate_k"], 24)
        self.assertEqual(config["keyword_candidate_k"], 24)
        self.assertEqual(config["fusion_candidate_k"], 30)
        self.assertEqual(config["table"], "supportportal.docagent_chunks_bge_large_en_v1_5_1024")
        self.assertEqual(config["embedding_provider"], "siliconflow")
        self.assertEqual(config["embedding_model"], "BAAI/bge-large-en-v1.5")

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

    def test_retrieval_queries_filter_primary_index_role(self) -> None:
        source = Path("backend/services/rag_qa.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("index_role = 'primary'"), 3)

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


if __name__ == "__main__":
    unittest.main()
