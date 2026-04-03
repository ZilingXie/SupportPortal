from __future__ import annotations

import unittest
from pathlib import Path


class RagDashboardContractTests(unittest.TestCase):
    def test_repository_dispatches_workbench_pages(self) -> None:
        source = Path("backend/repositories/knowledge_repository.py").read_text(encoding="utf-8")
        expected_dispatches = {
            'if normalized_page == "scorecard":': "_scorecard_workbench_page",
            'if normalized_page == "routing":': "_routing_workbench_page",
            'if normalized_page == "retrieval":': "_retrieval_workbench_page",
            'if normalized_page == "generation":': "_generation_workbench_page",
            'if normalized_page == "performance":': "_performance_workbench_page",
            'if normalized_page == "data-supply":': "_data_supply_workbench_page",
            'if normalized_page == "diagnosis":': "_diagnosis_workbench_page",
            'if normalized_page == "review":': "_review_workbench_page",
        }
        for branch, helper_name in expected_dispatches.items():
            self.assertIn(branch, source)
            self.assertIn(helper_name, source)
        for alias_branch in [
            'if normalized_page == "experiments":',
            'if normalized_page == "datasets":',
            'if normalized_page == "knowledge-supply":',
            'if normalized_page == "production-signals":',
        ]:
            self.assertIn(alias_branch, source)

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

    def test_case_detail_routes_are_exposed_publicly_and_internally(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        rag_api_source = Path("backend/rag_api.py").read_text(encoding="utf-8")

        self.assertIn("/api/dashboard/rag/cases/benchmark-detail", main_source)
        self.assertIn("/api/dashboard/rag/cases/live-detail", main_source)
        self.assertIn("/internal/dashboard/rag/cases/benchmark-detail", rag_api_source)
        self.assertIn("/internal/dashboard/rag/cases/live-detail", rag_api_source)

        for source in [main_source, rag_api_source]:
            self.assertIn("baseline_eval_run_id: str | None = Query(default=None)", source)
            self.assertIn("test_case_id: str = Query(...", source)
            self.assertIn("eval_run_id: str = Query(...", source)
            self.assertIn("request_id: str = Query(...", source)

    def test_workbench_pages_are_primary_public_page_set(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        rag_api_source = Path("backend/rag_api.py").read_text(encoding="utf-8")
        for source in [main_source, rag_api_source]:
            for page_name in [
                "scorecard",
                "routing",
                "retrieval",
                "generation",
                "performance",
                "data-supply",
                "diagnosis",
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
                "route_family_override",
                "execution_action_override",
                "tooling_profile_override",
                "failure_stage_override",
                "failure_bucket_override",
            ]:
                self.assertIn(field_name, source)
        self.assertIn("/api/dashboard/rag/datasets/generation-runs", main_source)
        self.assertIn("/api/dashboard/rag/benchmarks/local-sync", main_source)
        self.assertIn("/api/dashboard/rag/datasets/{dataset_id}/benchmark-runs", main_source)
        self.assertIn("/api/dashboard/rag/datasets/{dataset_id}/export", main_source)
        self.assertIn("/internal/dashboard/rag/datasets/generation-runs", rag_api_source)
        self.assertIn("/internal/dashboard/rag/benchmarks/local-sync", rag_api_source)
        self.assertIn("/internal/dashboard/rag/datasets/{dataset_id}/benchmark-runs", rag_api_source)
        self.assertIn("/internal/dashboard/rag/datasets/{dataset_id}/export", rag_api_source)

    def test_benchmark_session_routes_and_request_model_are_exposed(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        rag_api_source = Path("backend/rag_api.py").read_text(encoding="utf-8")
        repository_source = Path("backend/repositories/knowledge_repository.py").read_text(encoding="utf-8")
        service_client_source = Path("backend/services/rag_service_client.py").read_text(encoding="utf-8")

        for source in [main_source, rag_api_source]:
            self.assertIn("class BenchmarkSessionRunRequest(BaseModel):", source)
            self.assertIn("session_name: str | None = Field(default=None, max_length=160)", source)
            self.assertIn("top_k: int | None = Field(default=None, ge=1, le=20)", source)
        self.assertIn("/api/dashboard/rag/benchmarks/sessions/local-run", main_source)
        self.assertIn("/internal/dashboard/rag/benchmarks/sessions/local-run", rag_api_source)
        self.assertIn("create_local_benchmark_session_run", service_client_source)
        for marker in [
            "benchmark_session",
            "_benchmark_session_payload_for_eval_run",
            "improvement_summary",
            "improvement_entries",
            "gate_status",
            "gate_failure_dimensions",
            "session_gate",
        ]:
            self.assertIn(marker, repository_source)

    def test_dashboard_repository_exposes_case_results_and_route_fields(self) -> None:
        source = Path("backend/repositories/knowledge_repository.py").read_text(encoding="utf-8")
        for marker in [
            "case_results",
            "route_accuracy",
            "expected_answer_text",
            "actual_answer_text",
            "expected_route",
            "actual_route",
            "route_correct_flag",
        ]:
            self.assertIn(marker, source)

    def test_dashboard_repository_exposes_shared_benchmark_selector_metadata(self) -> None:
        source = Path("backend/repositories/knowledge_repository.py").read_text(encoding="utf-8")
        for marker in [
            "benchmark_selector",
            "current_experiment_id",
            "current_eval_run_id",
            "current_benchmark_version",
            "current_finished_at",
            "available_experiments",
        ]:
            self.assertIn(marker, source)

    def test_dashboard_repository_exposes_run_centric_benchmark_diagnostics(self) -> None:
        source = Path("backend/repositories/knowledge_repository.py").read_text(encoding="utf-8")
        for marker in [
            "failure_stage_distribution",
            "root_cause_distribution",
            "category_distribution",
            "query_type_distribution",
            "source_type_distribution",
            "query_understanding",
            "candidate_funnel",
            "judge_summary",
            "strategy_snapshot",
        ]:
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
