from __future__ import annotations

import unittest
from pathlib import Path

from backend.services.query_understanding import (
    DEFAULT_QUERY_PROFILE,
    GLOSSARY_HIT_LIMIT,
    load_query_profile,
    understand_rag_query,
    validate_retrieval_plan,
)


class QueryUnderstandingTests(unittest.TestCase):
    def test_load_query_profile_uses_default_english_profile_and_repo_glossary_snapshot(self) -> None:
        profile = load_query_profile()

        self.assertEqual(profile.profile_id, DEFAULT_QUERY_PROFILE)
        self.assertEqual(profile.prompt_profile, "default_en")
        self.assertTrue(profile.glossary_entries)
        self.assertEqual(Path(profile.glossary_path).name, "video-calling_glossary (1).md")

    def test_understand_rag_query_normalizes_glossary_terms_and_caps_hits(self) -> None:
        result = understand_rag_query(
            "How do Cloud Recording, jitter, packet loss, channel profile, App ID, "
            "and Interactive Live Streaming work together?"
        )

        self.assertEqual(result.query_profile, "en")
        self.assertLessEqual(len(result.glossary_hits), GLOSSARY_HIT_LIMIT)
        self.assertIn("Cloud Recording", result.canonical_terms)
        self.assertIn("Jitter", result.canonical_terms)
        self.assertIn("Packet loss", result.canonical_terms)
        self.assertEqual(result.fallback_mode, "none")

    def test_validate_retrieval_plan_drops_unsupported_or_invalid_filter_values(self) -> None:
        plan = validate_retrieval_plan(
            {
                "semantic_query": "How do I troubleshoot token expiry?",
                "hard_filters": {
                    "language": "Node.js",
                    "protocol": "ftp",
                    "priority": "high",
                    "doc_subtype": "troubleshooting_case",
                },
                "soft_signals": {
                    "keywords": ["token expired", "renew"],
                    "topic": "authentication",
                    "unknown": ["ignore-me"],
                },
            }
        )

        self.assertEqual(plan.semantic_query, "How do I troubleshoot token expiry?")
        self.assertEqual(plan.hard_filters["language"], "nodejs")
        self.assertEqual(plan.hard_filters["doc_subtype"], "troubleshooting_case")
        self.assertNotIn("protocol", plan.hard_filters)
        self.assertNotIn("priority", plan.hard_filters)
        self.assertEqual(plan.soft_signals["keywords"], ["token expired", "renew"])
        self.assertEqual(plan.soft_signals["topic"], ["authentication"])
        self.assertNotIn("unknown", plan.soft_signals)

    def test_understand_rag_query_only_decomposes_complex_queries_and_caps_subqueries(self) -> None:
        result = understand_rag_query(
            "Compare BuildTokenWithUid vs BuildTokenWithUidAndPrivilege for Node.js, "
            "explain which one I should use, and how wildcard tokens fit in."
        )

        self.assertLessEqual(len(result.decomposition_subqueries), 3)
        self.assertGreaterEqual(len(result.decomposition_subqueries), 2)
        self.assertTrue(result.rewritten_queries)
        flattened = " ".join(result.decomposition_subqueries)
        self.assertIn("BuildTokenWithUid", flattened)
        self.assertIn("BuildTokenWithUidAndPrivilege", flattened)


if __name__ == "__main__":
    unittest.main()
