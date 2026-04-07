from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def _load_script_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "profile_lexical_retrieval.py"
    spec = importlib.util.spec_from_file_location("profile_lexical_retrieval_script", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProfileLexicalRetrievalCliTests(unittest.TestCase):
    def test_parser_exposes_expected_defaults(self) -> None:
        module = _load_script_module()

        args = module.build_parser().parse_args([])

        self.assertEqual(args.query, "How to join channel")
        self.assertEqual(args.limit, 12)
        self.assertEqual(args.recent_hours, 24)
        self.assertEqual(args.containers, "deployment_rag_api_1,deployment_worker_query_1")

    def test_compute_prejoin_limit_uses_limit_times_eight_with_floor(self) -> None:
        module = _load_script_module()

        self.assertEqual(module.compute_prejoin_limit(1), 64)
        self.assertEqual(module.compute_prejoin_limit(12), 96)

    def test_render_markdown_report_includes_current_and_proposed_sections(self) -> None:
        module = _load_script_module()

        markdown = module.render_markdown_report(
            {
                "query": "How to join channel",
                "limit": 12,
                "recent_hours": 24,
                "host_timings": {
                    "bm25_cold_ms": 5710.31,
                    "bm25_warm_ms": 4384.01,
                    "fts_cold_ms": 3819.17,
                    "fts_warm_ms": 3219.80,
                },
                "container_timings": {
                    "deployment_rag_api_1": {
                        "bm25_cold_ms": 4384.01,
                        "bm25_warm_ms": 4269.91,
                    }
                },
                "recent_percentiles": {
                    "count": 27,
                    "p50_bm25_retrieval_latency_ms": 59137.73,
                    "p90_bm25_retrieval_latency_ms": 86188.21,
                    "p99_bm25_retrieval_latency_ms": 128548.13,
                },
                "current_bm25_explain": {
                    "execution_time_ms": 13479.66,
                    "summary": ["matched_postings: 19080 rows", "matched_docs: 15755 rows"],
                },
                "proposed_bm25_explain": {
                    "execution_time_ms": 8539.13,
                    "summary": ["top_scored limit before vector join", "12 rows joined back to vector table"],
                },
            }
        )

        self.assertIn("How to join channel", markdown)
        self.assertIn("Host Timings", markdown)
        self.assertIn("deployment_rag_api_1", markdown)
        self.assertIn("Recent 24h Percentiles", markdown)
        self.assertIn("Current BM25 Explain", markdown)
        self.assertIn("Proposed BM25 Explain", markdown)


if __name__ == "__main__":
    unittest.main()
