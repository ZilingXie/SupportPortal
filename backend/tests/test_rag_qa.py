from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services.rag_qa import RetrievedChunk, _get_rag_config, _rrf_merge, _split_table_name


class RagQaHybridTests(unittest.TestCase):
    def test_split_table_name_supports_schema_prefix(self) -> None:
        self.assertEqual(_split_table_name("public.docagent"), ("public", "docagent"))
        self.assertEqual(_split_table_name("docagent_chunks_qwen3_1024"), ("supportportal", "docagent_chunks_qwen3_1024"))

    def test_get_rag_config_uses_hybrid_candidate_windows(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = _get_rag_config(top_k=6)
        self.assertEqual(config["top_k"], 6)
        self.assertEqual(config["vector_candidate_k"], 24)
        self.assertEqual(config["keyword_candidate_k"], 24)
        self.assertEqual(config["fusion_candidate_k"], 30)
        self.assertEqual(config["table"], "supportportal.docagent_chunks_qwen3_1024")
        self.assertEqual(config["embedding_provider"], "siliconflow_qwen3")
        self.assertEqual(config["embedding_model"], "Qwen/Qwen3-Embedding-8B")

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


if __name__ == "__main__":
    unittest.main()
