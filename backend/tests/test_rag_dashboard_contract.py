from __future__ import annotations

import unittest
from pathlib import Path


class RagDashboardContractTests(unittest.TestCase):
    def test_repository_accepts_workbench_pages(self) -> None:
        source = Path("backend/repositories/knowledge_repository.py").read_text(encoding="utf-8")
        for page_name in [
            '"diagnosis"',
            '"knowledge-supply"',
            '"production-signals"',
            '"review"',
        ]:
            self.assertIn(page_name, source)

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


if __name__ == "__main__":
    unittest.main()
