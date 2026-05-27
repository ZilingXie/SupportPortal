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

