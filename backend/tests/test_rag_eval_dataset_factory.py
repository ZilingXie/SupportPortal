from __future__ import annotations

import unittest

from backend.services.rag_eval_dataset_factory import (
    DatasetSourceChunk,
    build_dataset_candidate,
    build_dataset_review_sample,
    evaluate_dataset_candidate_votes,
    run_generation_quality_checks,
)


class RagEvalDatasetFactoryTests(unittest.TestCase):
    def test_build_dataset_candidate_from_source_chunk(self) -> None:
        chunk = DatasetSourceChunk(
            chunk_id="chunk-1",
            document_id="official-doc-1",
            source_type="official_markdown_upload",
            source_path="official/setup.md",
            heading="Setup > Basic Authentication",
            chunk_type="procedure",
            section_path=["Setup", "Basic Authentication"],
            text="Use a token server on the backend. Do not embed secrets in the client.",
            language="en",
            product="video-calling",
            metadata={"chunk_type": "procedure"},
        )

        candidate = build_dataset_candidate(chunk)

        self.assertEqual(candidate["source_type"], "official_markdown_upload")
        self.assertEqual(candidate["query_type"], "configuration")
        self.assertEqual(candidate["language"], "en")
        self.assertEqual(candidate["expected_document_ids"], ["official-doc-1"])
        self.assertEqual(candidate["expected_evidence_refs"][0]["chunk_id"], "chunk-1")
        self.assertTrue(candidate["question"])
        self.assertTrue(candidate["reference_answer"])

    def test_quality_checks_reject_answer_leakage(self) -> None:
        candidate = {
            "question": "Do not embed secrets in the client.",
            "reference_answer": "Do not embed secrets in the client.",
            "expected_evidence_refs": [{"chunk_id": "chunk-1"}],
            "expected_citation_targets": [{"chunk_id": "chunk-1"}],
        }

        checked = run_generation_quality_checks(candidate)

        self.assertFalse(checked["passed"])
        self.assertIn("answer_leakage", checked["rejection_reasons"])

    def test_vote_aggregation_marks_disagreement_and_promotes_silver(self) -> None:
        aggregate = evaluate_dataset_candidate_votes(
            [
                {
                    "dataset_quality_score": 0.91,
                    "ambiguity_flag": False,
                    "answer_leakage_flag": False,
                    "citation_bindable_flag": True,
                    "logic_eval_applicable": True,
                },
                {
                    "dataset_quality_score": 0.84,
                    "ambiguity_flag": False,
                    "answer_leakage_flag": False,
                    "citation_bindable_flag": True,
                    "logic_eval_applicable": True,
                },
                {
                    "dataset_quality_score": 0.42,
                    "ambiguity_flag": True,
                    "answer_leakage_flag": False,
                    "citation_bindable_flag": True,
                    "logic_eval_applicable": True,
                },
            ]
        )

        self.assertEqual(aggregate["item_status"], "silver")
        self.assertTrue(aggregate["judge_disagreement_flag"])
        self.assertGreaterEqual(aggregate["dataset_quality_score"], 0.84)

    def test_build_dataset_review_sample_uses_dataset_candidate_source(self) -> None:
        sample = build_dataset_review_sample(
            dataset_item_id="DI-1",
            generation_run_id="GR-1",
            dataset_name="golden-set",
            candidate={
                "source_type": "technical_article_api",
                "query_type": "troubleshooting",
                "language": "en",
                "question": "Why does the stream stop after reconnect?",
                "reference_answer": "The article says to verify reconnection and queue ownership.",
                "expected_evidence_refs": [{"chunk_id": "chunk-77"}],
                "expected_citation_targets": [{"chunk_id": "chunk-77"}],
            },
            vote_summary={
                "dataset_quality_score": 0.62,
                "judge_disagreement_flag": True,
                "sampling_reasons": ["judge_disagreement"],
            },
        )

        self.assertEqual(sample["sample_source"], "dataset_candidate")
        self.assertEqual(sample["dataset_item_id"], "DI-1")
        self.assertEqual(sample["sample_payload"]["generation_run_id"], "GR-1")
        self.assertIn("judge_disagreement", sample["sampling_reasons"])


if __name__ == "__main__":
    unittest.main()
