from __future__ import annotations

import unittest
from pathlib import Path


class RagDashboardContractTests(unittest.TestCase):
    def test_repository_dispatches_workbench_pages(self) -> None:
        source = Path("backend/repositories/knowledge_repository.py").read_text(encoding="utf-8")
        expected_dispatches = {
            'if normalized_page == "experiments":': "_experiments_workbench_page",
            'if normalized_page == "datasets":': "_datasets_workbench_page",
            'if normalized_page == "diagnosis":': "_diagnosis_workbench_page",
            'if normalized_page == "knowledge-supply":': "_knowledge_supply_workbench_page",
            'if normalized_page == "production-signals":': "_production_signals_workbench_page",
            'if normalized_page == "review":': "_review_workbench_page",
        }
        for branch, helper_name in expected_dispatches.items():
            self.assertIn(branch, source)
            self.assertIn(helper_name, source)

    def test_repository_normalizes_diagnosis_and_compare_filters(self) -> None:
        source = Path("backend/repositories/knowledge_repository.py").read_text(encoding="utf-8")
        for filter_name in [
            '"sample_id"',
            '"request_id"',
            '"eval_run_id"',
            '"test_case_id"',
            '"baseline_experiment_id"',
            '"candidate_experiment_id"',
        ]:
            self.assertIn(filter_name, source)

    def test_public_and_internal_dashboard_routes_accept_workbench_filters(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        rag_api_source = Path("backend/rag_api.py").read_text(encoding="utf-8")
        for source in [main_source, rag_api_source]:
            for filter_name in [
                "sample_id",
                "request_id",
                "eval_run_id",
                "test_case_id",
                "baseline_experiment_id",
                "candidate_experiment_id",
            ]:
                self.assertIn(f"{filter_name}: str | None = Query(default=None)", source)

    def test_workbench_pages_are_primary_public_page_set(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        rag_api_source = Path("backend/rag_api.py").read_text(encoding="utf-8")
        for source in [main_source, rag_api_source]:
            for page_name in [
                "experiments",
                "datasets",
                "diagnosis",
                "knowledge-supply",
                "production-signals",
                "review",
            ]:
                self.assertIn(page_name, source)

    def test_review_payload_and_dataset_routes_are_exposed(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        rag_api_source = Path("backend/rag_api.py").read_text(encoding="utf-8")
        for source in [main_source, rag_api_source]:
            for field_name in [
                "logic_ok",
                "hallucination_present",
                "dataset_decision",
                "corrected_reference_answer",
                "corrected_citation_targets",
            ]:
                self.assertIn(field_name, source)
        self.assertIn("/api/dashboard/rag/datasets/generation-runs", main_source)
        self.assertIn("/api/dashboard/rag/datasets/{dataset_id}/benchmark-runs", main_source)
        self.assertIn("/api/dashboard/rag/datasets/{dataset_id}/export", main_source)
        self.assertIn("/internal/dashboard/rag/datasets/generation-runs", rag_api_source)
        self.assertIn("/internal/dashboard/rag/datasets/{dataset_id}/benchmark-runs", rag_api_source)
        self.assertIn("/internal/dashboard/rag/datasets/{dataset_id}/export", rag_api_source)


if __name__ == "__main__":
    unittest.main()
