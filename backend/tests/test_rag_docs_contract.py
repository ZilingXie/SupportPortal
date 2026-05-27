from __future__ import annotations

import unittest
from pathlib import Path


class RagDocsContractTests(unittest.TestCase):
    def test_retrieval_chain_documents_agentic_fts_and_shadow_contract(self) -> None:
        source = Path("docs/rag_retrieval_chain.md").read_text(encoding="utf-8")

        self.assertIn("agentic multi-tool", source)
        self.assertIn("PostgreSQL FTS", source)
        self.assertIn("shadow", source.lower())
        self.assertNotIn("Only `index_role='primary'` chunks participate in online retrieval.", source)
        self.assertNotIn("vector recall + true BM25 recall + RRF + metadata prune + external rerank", source)

    def test_retrieval_chain_documents_fts_as_agentic_supplemental_telemetry(self) -> None:
        source = Path("docs/rag_retrieval_chain.md").read_text(encoding="utf-8")

        self.assertIn("supplemental", source.lower())
        self.assertIn("agentic", source.lower())
        self.assertNotIn(
            "fts_latency_ms` and `fts_candidates_count` are PostgreSQL FTS metrics for legacy or diagnostic paths",
            source,
        )
        self.assertIn("fts_latency_ms", source)
        self.assertIn("fts_candidates_count", source)

