from __future__ import annotations

import unittest

from backend.services.rag_evidence_summary import build_rag_evidence_summary


class RagEvidenceSummaryTests(unittest.TestCase):
    def test_build_rag_evidence_summary_limits_contexts_and_truncates_excerpt(self) -> None:
        long_excerpt = "A" * 400
        summary = build_rag_evidence_summary(
            quality_signals={
                "generation_mode": "structured_answer",
                "selected_doc_count": 4,
                "citation_coverage_ratio": 0.5,
                "top1_similarity_score": 0.93,
                "avg_selected_similarity_score": 0.88,
                "handoff_reason": None,
                "needs_human": False,
            },
            selected_contexts=[
                {
                    "chunk_id": f"chunk-{index}",
                    "heading": f"Heading {index}",
                    "source_path": f"official/doc-{index}.md",
                    "source_url": f"https://docs.agora.io/doc-{index}",
                    "text": long_excerpt,
                    "similarity": 0.9 - (index * 0.01),
                }
                for index in range(4)
            ],
            cited_chunk_ids={"chunk-1", "chunk-3"},
            max_contexts=3,
            max_excerpt_chars=120,
        )

        self.assertEqual(summary["quality_signals"]["selected_doc_count"], 4)
        self.assertEqual(len(summary["selected_contexts"]), 3)
        self.assertTrue(summary["selected_contexts"][0]["text_excerpt"].endswith("..."))
        self.assertLessEqual(len(summary["selected_contexts"][0]["text_excerpt"]), 123)
        self.assertTrue(summary["selected_contexts"][1]["cited_in_answer"])
        self.assertFalse(summary["selected_contexts"][2]["cited_in_answer"])


if __name__ == "__main__":
    unittest.main()
