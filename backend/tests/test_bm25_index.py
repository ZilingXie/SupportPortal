from __future__ import annotations

import unittest

from backend.services.bm25_index import build_bm25_index_payload


class Bm25IndexTests(unittest.TestCase):
    def test_build_bm25_index_payload_builds_docs_postings_terms_and_stats_for_primary(self) -> None:
        payload = build_bm25_index_payload(
            rows=[
                {
                    "id": "chunk-1",
                    "doc_id": "doc-1",
                    "h1": "Deploy a token server",
                    "h2": "Token generation code",
                    "h3": "BuildTokenWithUidAndPrivilege",
                    "content": "Use BuildTokenWithUidAndPrivilege to generate a token.",
                    "updated_at": "2026-03-22T00:00:00+00:00",
                }
            ],
            index_role="primary",
        )

        self.assertEqual(payload["stats"]["index_role"], "primary")
        self.assertEqual(payload["stats"]["doc_count"], 1)
        self.assertGreater(payload["stats"]["avg_doc_length"], 0.0)
        self.assertEqual(payload["docs"][0]["chunk_id"], "chunk-1")
        self.assertGreater(payload["docs"][0]["doc_length"], 0)
        self.assertTrue(any(item["term"] == "buildtokenwithuidandprivilege" for item in payload["terms"]))
        self.assertTrue(any(item["term"] == "token" for item in payload["postings"]))

    def test_build_bm25_index_payload_skips_non_primary_index_roles(self) -> None:
        payload = build_bm25_index_payload(
            rows=[
                {
                    "id": "chunk-shadow",
                    "doc_id": "doc-1",
                    "h1": "Shadow title",
                    "content": "Shadow text",
                }
            ],
            index_role="shadow",
        )

        self.assertEqual(payload["docs"], [])
        self.assertEqual(payload["postings"], [])
        self.assertEqual(payload["terms"], [])
        self.assertEqual(payload["stats"]["doc_count"], 0)


if __name__ == "__main__":
    unittest.main()
