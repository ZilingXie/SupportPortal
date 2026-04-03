from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


def _load_repository_module():
    if "psycopg" not in sys.modules:
        psycopg_stub = types.ModuleType("psycopg")
        psycopg_stub.sql = types.SimpleNamespace(
            SQL=lambda value: value,
            Identifier=lambda *args, **kwargs: ("identifier", args, kwargs),
            Literal=lambda value: value,
        )
        json_module = types.ModuleType("psycopg.types.json")

        class Json:  # noqa: N801 - mirrors external dependency name
            def __init__(self, value):
                self.value = value

        json_module.Json = Json
        sys.modules["psycopg"] = psycopg_stub
        sys.modules["psycopg.types"] = types.ModuleType("psycopg.types")
        sys.modules["psycopg.types.json"] = json_module

    module_path = Path(__file__).resolve().parents[1] / "repositories" / "knowledge_repository.py"
    spec = importlib.util.spec_from_file_location("knowledge_repository_for_tests", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


knowledge_repository_module = _load_repository_module()
PostgresKnowledgeRepository = knowledge_repository_module.PostgresKnowledgeRepository


class RagScorecardRepositoryTests(unittest.TestCase):
    def test_read_queries_reuse_cached_connection_between_scalar_and_rows(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        connections: list[object] = []

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, query, params):
                self.query = query
                self.params = params

            def fetchall(self):
                return [("row",)]

            def fetchone(self):
                return (1,)

        class FakeConnection:
            def __init__(self) -> None:
                self.closed = False
                self.broken = False
                self.autocommit = False

            def cursor(self):
                return FakeCursor()

            def close(self):
                self.closed = True

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                self.close()
                return False

        def fake_connect():
            connection = FakeConnection()
            connections.append(connection)
            return connection

        with patch.object(repository, "_connect", side_effect=fake_connect):
            self.assertEqual(repository._query_rows("SELECT 1"), [("row",)])
            self.assertEqual(repository._query_scalar("SELECT 1"), 1)

        self.assertEqual(len(connections), 1)
        self.assertTrue(connections[0].autocommit)

    def test_selected_benchmark_context_uses_summary_case_rows_instead_of_full_trace_rows(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        experiments = [
            {
                "experiment_id": "mixed-candidate",
                "eval_run_id": "run-mixed-candidate",
                "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
            },
            {
                "experiment_id": "mixed-baseline",
                "eval_run_id": "run-mixed-baseline",
                "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
            },
        ]
        baseline = experiments[1]
        candidate = experiments[0]
        baseline_cases = {"case-1": {"test_case_id": "case-1"}}
        candidate_cases = {"case-1": {"test_case_id": "case-1"}}

        with patch.object(repository, "_experiment_rows", return_value=experiments), patch.object(
            repository,
            "_select_experiment_rows",
            return_value=(baseline, candidate),
        ), patch.object(
            repository,
            "_experiment_case_rows",
            side_effect=AssertionError("full trace rows should not be loaded for scorecard or routing context"),
        ), patch.object(
            repository,
            "_benchmark_case_summary_rows",
            create=True,
            return_value={
                "run-mixed-baseline": baseline_cases,
                "run-mixed-candidate": candidate_cases,
            },
        ) as summary_rows_mock, patch.object(
            repository,
            "_sample_deltas_from_cases",
            return_value=(["win"], ["regression"]),
        ):
            result = repository._selected_benchmark_context(days=7, filters={"limit": 20})

        self.assertEqual(result[3], baseline_cases)
        self.assertEqual(result[4], candidate_cases)
        self.assertEqual(result[5], ["win"])
        self.assertEqual(result[6], ["regression"])
        summary_rows_mock.assert_called_once_with(["run-mixed-baseline", "run-mixed-candidate"])

    def test_select_experiment_rows_prefers_same_benchmark_version_as_candidate(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        experiments = [
            {
                "experiment_id": "canonical-baseline",
                "eval_run_id": "run-canonical",
                "benchmark_version": "agora_rag_testset_100_canonical_en",
            },
            {
                "experiment_id": "mixed-candidate",
                "eval_run_id": "run-mixed-candidate",
                "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
            },
            {
                "experiment_id": "mixed-baseline",
                "eval_run_id": "run-mixed-baseline",
                "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
            },
        ]

        baseline, candidate = repository._select_experiment_rows(
            experiments,
            {
                "candidate_experiment_id": "mixed-candidate",
                "baseline_experiment_id": "canonical-baseline",
            },
        )

        self.assertEqual(candidate["experiment_id"], "mixed-candidate")
        self.assertEqual(baseline["experiment_id"], "mixed-baseline")

    def test_select_experiment_rows_defaults_candidate_to_latest_completed_run(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        experiments = [
            {
                "experiment_id": "mixed-older",
                "eval_run_id": "run-mixed-older",
                "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                "finished_at": "2026-03-23T12:00:00+00:00",
                "created_at": "2026-03-23T11:00:00+00:00",
            },
            {
                "experiment_id": "canonical-latest",
                "eval_run_id": "run-canonical-latest",
                "benchmark_version": "agora_rag_testset_100_canonical_en",
                "finished_at": "2026-03-25T12:00:00+00:00",
                "created_at": "2026-03-25T11:00:00+00:00",
            },
            {
                "experiment_id": "mixed-created-only",
                "eval_run_id": "run-mixed-created-only",
                "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                "finished_at": None,
                "created_at": "2026-03-24T11:00:00+00:00",
            },
        ]

        baseline, candidate = repository._select_experiment_rows(experiments, {})

        self.assertEqual(candidate["experiment_id"], "canonical-latest")
        self.assertEqual(baseline["experiment_id"], "canonical-latest")

    def test_select_scorecard_experiment_rows_pins_baseline_to_current_run_and_defaults_candidate_to_different_run(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        experiments = [
            {
                "experiment_id": "mixed-older",
                "eval_run_id": "run-mixed-older",
                "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                "finished_at": "2026-03-23T12:00:00+00:00",
                "created_at": "2026-03-23T11:00:00+00:00",
            },
            {
                "experiment_id": "canonical-latest",
                "eval_run_id": "run-canonical-latest",
                "benchmark_version": "agora_rag_testset_100_canonical_en",
                "finished_at": "2026-03-25T12:00:00+00:00",
                "created_at": "2026-03-25T11:00:00+00:00",
            },
        ]

        baseline, candidate = repository._select_scorecard_experiment_rows(experiments, {})

        self.assertEqual(baseline["experiment_id"], "canonical-latest")
        self.assertEqual(candidate["experiment_id"], "mixed-older")

    def test_select_scorecard_experiment_rows_respects_explicit_candidate_when_different_from_current_run(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        experiments = [
            {
                "experiment_id": "mixed-older",
                "eval_run_id": "run-mixed-older",
                "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                "finished_at": "2026-03-23T12:00:00+00:00",
                "created_at": "2026-03-23T11:00:00+00:00",
            },
            {
                "experiment_id": "canonical-latest",
                "eval_run_id": "run-canonical-latest",
                "benchmark_version": "agora_rag_testset_100_canonical_en",
                "finished_at": "2026-03-25T12:00:00+00:00",
                "created_at": "2026-03-25T11:00:00+00:00",
            },
        ]

        baseline, candidate = repository._select_scorecard_experiment_rows(
            experiments,
            {
                "candidate_experiment_id": "canonical-latest",
                "baseline_experiment_id": "mixed-older",
            },
        )

        self.assertEqual(baseline["experiment_id"], "canonical-latest")
        self.assertEqual(candidate["experiment_id"], "mixed-older")

    def test_benchmark_run_comparison_returns_none_when_no_dataset_aligned_baseline_exists(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        session_row = {
            "runs": [
                {
                    "eval_run_id": "run-canonical-current",
                    "dataset_name": "Canonical",
                    "benchmark_version": "canonical-v2",
                    "is_current": True,
                },
                {
                    "eval_run_id": "run-real-user-old",
                    "dataset_name": "Real User",
                    "benchmark_version": "real-user-v1",
                    "is_current": False,
                },
            ]
        }

        comparison = knowledge_repository_module._benchmark_run_comparison(session_row["runs"])

        self.assertIsNone(comparison)

    def test_scorecard_page_populates_baseline_and_delta_for_layers(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        candidate_cases = {
            "case-1": {
                "category": "fact",
                "route_family_correct": 1.0,
                "evidence_hit_at_5": 0.9,
                "answer_accuracy_score": 0.8,
                "response_policy_followed": True,
            }
        }
        baseline_cases = {
            "case-1": {
                "category": "fact",
                "route_family_correct": 0.5,
                "evidence_hit_at_5": 0.4,
                "answer_accuracy_score": 0.2,
                "response_policy_followed": False,
            }
        }

        experiments = [
            {
                "experiment_id": "mixed-baseline",
                "eval_run_id": "run-mixed-baseline",
                "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
            },
            {
                "experiment_id": "mixed-candidate",
                "eval_run_id": "run-mixed-candidate",
                "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
            },
        ]
        baseline = experiments[0]
        candidate = experiments[1]

        with patch.object(repository, "_experiment_rows", return_value=experiments), patch.object(
            repository,
            "_select_scorecard_experiment_rows",
            side_effect=[(baseline, candidate), (baseline, candidate)],
        ), patch.object(
            repository,
            "_benchmark_case_summary_rows",
            return_value={
                "run-mixed-baseline": baseline_cases,
                "run-mixed-candidate": candidate_cases,
            },
        ), patch.object(
            repository,
            "_sample_deltas_from_cases",
            return_value=([], []),
        ):
            payload = repository._scorecard_workbench_page("7d", 7, {"limit": 20})

        rows = payload["sections"]["layer_scorecard"]["rows"]
        self.assertEqual(rows[0]["candidate"], 1.0)
        self.assertEqual(rows[0]["baseline"], 0.5)
        self.assertEqual(rows[0]["delta"], 0.5)
        self.assertEqual(rows[1]["candidate"], 0.9)
        self.assertEqual(rows[1]["baseline"], 0.4)
        self.assertEqual(rows[1]["delta"], 0.5)
        self.assertEqual(rows[2]["candidate"], 0.8)
        self.assertEqual(rows[2]["baseline"], 0.2)
        self.assertEqual(rows[2]["delta"], 0.6)
        self.assertEqual(rows[3]["candidate"], 1.0)
        self.assertEqual(rows[3]["baseline"], 0.0)
        self.assertEqual(rows[3]["delta"], 1.0)

    def test_scorecard_page_exposes_retrieval_generation_and_performance_sections(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        candidate_cases = {
            "case-1": {
                "category": "fact",
                "route_family_correct": 1.0,
                "evidence_precision_at_5": 0.9,
                "evidence_recall_at_5": 0.8,
                "evidence_ndcg_at_5": 0.88,
                "mrr": 0.75,
                "context_relevance_score": 0.92,
                "answer_relevance_score": 0.87,
                "faithfulness_score": 0.95,
                "citation_correctness_score": 0.9,
                "response_completeness_score": 0.86,
                "benchmark_p95_total_latency_ms": 2200.0,
                "benchmark_throughput_cases_per_sec": 0.42,
                "case_execution_error_rate": 0.0,
                "judge_error_rate": 0.0,
            }
        }
        baseline_cases = {
            "case-1": {
                "category": "fact",
                "route_family_correct": 1.0,
                "evidence_precision_at_5": 0.7,
                "evidence_recall_at_5": 0.6,
                "evidence_ndcg_at_5": 0.72,
                "mrr": 0.5,
                "context_relevance_score": 0.8,
                "answer_relevance_score": 0.75,
                "faithfulness_score": 0.84,
                "citation_correctness_score": 0.82,
                "response_completeness_score": 0.78,
                "benchmark_p95_total_latency_ms": 3100.0,
                "benchmark_throughput_cases_per_sec": 0.31,
                "case_execution_error_rate": 0.0,
                "judge_error_rate": 0.05,
            }
        }
        experiments = [
            {
                "experiment_id": "baseline",
                "eval_run_id": "run-baseline",
                "benchmark_version": "agora_rag_testset_100_standrad_en",
            },
            {
                "experiment_id": "candidate",
                "eval_run_id": "run-candidate",
                "benchmark_version": "agora_rag_testset_100_standrad_en",
            },
        ]
        baseline = experiments[0]
        candidate = experiments[1]

        with patch.object(repository, "_experiment_rows", return_value=experiments), patch.object(
            repository,
            "_select_scorecard_experiment_rows",
            side_effect=[(baseline, candidate), (baseline, candidate)],
        ), patch.object(
            repository,
            "_benchmark_case_summary_rows",
            return_value={
                "run-baseline": baseline_cases,
                "run-candidate": candidate_cases,
            },
        ), patch.object(
            repository,
            "_sample_deltas_from_cases",
            return_value=([], []),
        ):
            payload = repository._scorecard_workbench_page("7d", 7, {"limit": 20})

        self.assertEqual(payload["sections"]["retrieval_summary"]["cards"]["evidence_precision_at_5"], 0.9)
        self.assertEqual(payload["sections"]["generation_summary"]["cards"]["context_relevance_score"], 0.92)
        self.assertEqual(payload["sections"]["performance_summary"]["cards"]["benchmark_p95_total_latency_ms"], 2200.0)
        self.assertEqual(payload["sections"]["performance_summary"]["cards"]["benchmark_throughput_cases_per_sec"], 0.42)

    def test_scorecard_page_pins_benchmark_selector_to_baseline_and_defaults_candidate_to_alternate_run(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")

        with patch.object(
            repository,
            "_experiment_rows",
            return_value=[
                {
                    "experiment_id": "mixed-older",
                    "eval_run_id": "run-mixed-older",
                    "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                    "finished_at": "2026-03-23T12:00:00+00:00",
                    "created_at": "2026-03-23T11:00:00+00:00",
                },
                {
                    "experiment_id": "canonical-latest",
                    "eval_run_id": "run-canonical-latest",
                    "benchmark_version": "agora_rag_testset_100_canonical_en",
                    "finished_at": "2026-03-25T12:00:00+00:00",
                    "created_at": "2026-03-25T11:00:00+00:00",
                },
            ],
        ), patch.object(
            repository,
            "_benchmark_case_summary_rows",
            return_value={
                "run-canonical-latest": {},
                "run-mixed-older": {},
            },
        ), patch.object(
            repository,
            "_sample_deltas_from_cases",
            return_value=([], []),
        ):
            payload = repository._scorecard_workbench_page("7d", 7, {"limit": 20})

        self.assertEqual(payload["benchmark_selector"]["current_experiment_id"], "canonical-latest")
        self.assertEqual(payload["sections"]["summary"]["baseline_experiment_id"], "canonical-latest")
        self.assertEqual(payload["sections"]["summary"]["candidate_experiment_id"], "mixed-older")

    def test_scorecard_summary_exposes_benchmark_version_per_experiment_for_baseline_filtering(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")

        experiments = [
            {
                "experiment_id": "mixed-candidate",
                "eval_run_id": "run-mixed-candidate",
                "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                "finished_at": "2026-03-23T12:00:00+00:00",
            },
            {
                "experiment_id": "canonical-baseline",
                "eval_run_id": "run-canonical",
                "benchmark_version": "agora_rag_testset_100_canonical_en",
                "finished_at": "2026-03-22T12:00:00+00:00",
            },
        ]
        baseline = experiments[1]
        candidate = experiments[0]

        with patch.object(repository, "_experiment_rows", return_value=experiments), patch.object(
            repository,
            "_select_scorecard_experiment_rows",
            side_effect=[(baseline, candidate), (baseline, candidate)],
        ), patch.object(
            repository,
            "_benchmark_case_summary_rows",
            return_value={},
        ), patch.object(
            repository,
            "_sample_deltas_from_cases",
            return_value=([], []),
        ):
            payload = repository._scorecard_workbench_page("7d", 7, {"limit": 20})

        available = payload["sections"]["summary"]["available_experiments"]
        self.assertEqual(available[0]["benchmark_version"], "agora_rag_testset_100_mixed_en_v2")
        self.assertEqual(available[1]["benchmark_version"], "agora_rag_testset_100_canonical_en")

    def test_scorecard_page_exposes_top_level_benchmark_selector_sorted_by_recency(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")

        experiments = [
            {
                "experiment_id": "mixed-older",
                "eval_run_id": "run-mixed-older",
                "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                "finished_at": "2026-03-23T12:00:00+00:00",
                "created_at": "2026-03-23T11:00:00+00:00",
            },
            {
                "experiment_id": "canonical-latest",
                "eval_run_id": "run-canonical-latest",
                "benchmark_version": "agora_rag_testset_100_canonical_en",
                "finished_at": "2026-03-25T12:00:00+00:00",
                "created_at": "2026-03-25T11:00:00+00:00",
            },
        ]
        baseline = experiments[1]
        candidate = experiments[0]

        with patch.object(repository, "_experiment_rows", return_value=experiments), patch.object(
            repository,
            "_select_scorecard_experiment_rows",
            side_effect=[(baseline, candidate), (baseline, candidate)],
        ), patch.object(
            repository,
            "_benchmark_case_summary_rows",
            return_value={},
        ), patch.object(
            repository,
            "_sample_deltas_from_cases",
            return_value=([], []),
        ):
            payload = repository._scorecard_workbench_page("7d", 7, {"limit": 20})

        selector = payload["benchmark_selector"]
        self.assertEqual(selector["current_experiment_id"], "canonical-latest")
        self.assertEqual(selector["current_benchmark_version"], "agora_rag_testset_100_canonical_en")
        self.assertEqual(selector["available_experiments"][0]["experiment_id"], "canonical-latest")
        self.assertEqual(selector["available_experiments"][1]["experiment_id"], "mixed-older")

    def test_scorecard_page_includes_benchmark_session_for_current_run(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")

        baseline = {
            "experiment_id": "canonical-latest",
            "eval_run_id": "run-canonical-latest",
            "benchmark_version": "agora_rag_testset_100_canonical_en",
            "finished_at": "2026-03-25T12:00:00+00:00",
            "created_at": "2026-03-25T11:00:00+00:00",
        }
        candidate = {
            "experiment_id": "mixed-older",
            "eval_run_id": "run-mixed-older",
            "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
            "finished_at": "2026-03-23T12:00:00+00:00",
            "created_at": "2026-03-23T11:00:00+00:00",
        }
        experiments = [candidate, baseline]

        with patch.object(repository, "_experiment_rows", return_value=experiments), patch.object(
            repository,
            "_select_scorecard_experiment_rows",
            side_effect=[(baseline, candidate), (baseline, candidate)],
        ), patch.object(
            repository,
            "_benchmark_case_summary_rows",
            return_value={
                "run-canonical-latest": {},
                "run-mixed-older": {},
            },
        ), patch.object(
            repository,
            "_sample_deltas_from_cases",
            return_value=([], []),
        ), patch.object(
            repository,
            "_benchmark_session_payload_for_eval_run",
            return_value={
                "benchmark_session_id": "BSESS-1",
                "session_name": "session-1",
            },
        ) as session_mock:
            payload = repository._scorecard_workbench_page("7d", 7, {"limit": 20})

        self.assertEqual(payload["benchmark_session"]["benchmark_session_id"], "BSESS-1")
        session_mock.assert_called_once_with("run-canonical-latest")

    def test_scorecard_page_exposes_overview_usage_summary(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        baseline = {
            "experiment_id": "baseline",
            "eval_run_id": "run-baseline",
            "benchmark_version": "agora_rag_testset_100_standrad_en",
        }
        candidate = {
            "experiment_id": "candidate",
            "eval_run_id": "run-candidate",
            "benchmark_version": "agora_rag_testset_100_standrad_en",
        }

        with patch.object(repository, "_experiment_rows", return_value=[baseline, candidate]), patch.object(
            repository,
            "_select_scorecard_experiment_rows",
            side_effect=[(baseline, candidate), (baseline, candidate)],
        ), patch.object(
            repository,
            "_benchmark_case_summary_rows",
            return_value={
                "run-baseline": {},
                "run-candidate": {
                    "case-1": {
                        "avg_cost_per_query": 0.12,
                        "usage_summary": {
                            "total_input_tokens": 1200,
                            "total_output_tokens": 300,
                            "total_embedding_tokens": 100,
                            "token_by_model": [
                                {"provider": "openai", "model": "gpt-5.4", "input_tokens": 1000, "output_tokens": 250},
                                {"provider": "openai", "model": "gpt-5.4-mini", "input_tokens": 200, "output_tokens": 50},
                            ],
                        },
                    }
                },
            },
        ), patch.object(
            repository,
            "_sample_deltas_from_cases",
            return_value=([], []),
        ):
            payload = repository._scorecard_workbench_page("7d", 7, {"limit": 20})

        summary = payload["sections"]["overview_usage_summary"]["cards"]
        self.assertEqual(summary["total_input_tokens"], 1200)
        self.assertEqual(summary["total_output_tokens"], 300)
        self.assertEqual(summary["total_embedding_tokens"], 100)
        self.assertEqual(len(summary["token_by_model"]), 2)

    def test_benchmark_session_payload_for_eval_run_includes_gate_status(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        started_at = datetime(2026, 4, 2, 1, 0, tzinfo=timezone.utc)
        finished_at = datetime(2026, 4, 2, 1, 30, tzinfo=timezone.utc)
        session_row = (
            "BSESS-1",
            "session-1",
            "completed",
            None,
            [
                {
                    "dataset_name": "agora_rag_testset_100_standrad_en",
                    "label": "Canonical",
                    "benchmark_version": "agora_rag_testset_100_standrad_en",
                }
            ],
            "- Metrics refactor: unified scorecard",
            [{"entry_index": 5, "title": "Metrics refactor", "summary": "unified scorecard"}],
            5,
            "",
            started_at,
            finished_at,
        )
        run_rows = [
            (
                "EVAL-1",
                "agora_rag_testset_100_standrad_en",
                "offline_benchmark",
                "session-1::agora_rag_testset_100_standrad_en",
                "agora_rag_testset_100_standrad_en",
                "mixed_route_v2",
                "completed",
                started_at,
                finished_at,
            )
        ]

        with patch.object(repository, "_query_rows", side_effect=[[session_row], run_rows]), patch.object(
            repository,
            "_benchmark_case_summary_rows",
            return_value={
                "EVAL-1": {
                    "case-1": {
                        "evidence_precision_at_5": 0.91,
                        "evidence_recall_at_5": 0.9,
                        "evidence_ndcg_at_5": 0.92,
                        "context_relevance_score": 0.91,
                        "answer_relevance_score": 0.9,
                        "faithfulness_score": 0.94,
                        "citation_correctness_score": 0.91,
                        "response_completeness_score": 0.88,
                        "benchmark_p95_total_latency_ms": 1800.0,
                        "benchmark_throughput_cases_per_sec": 0.41,
                        "judge_error_rate": 0.0,
                        "case_execution_error_rate": 0.0,
                    }
                }
            },
        ):
            payload = repository._benchmark_session_payload_for_eval_run("EVAL-1")

        assert payload is not None
        self.assertEqual(payload["gate_status"], "pass")
        self.assertEqual(payload["gate_failure_dimensions"], [])
        self.assertIn("session_gate", payload)

    def test_benchmark_session_payload_includes_run_level_diagnostics_and_slices(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        started_at = datetime(2026, 4, 2, 1, 0, tzinfo=timezone.utc)
        finished_at = datetime(2026, 4, 2, 1, 30, tzinfo=timezone.utc)
        session_row = (
            "BSESS-2",
            "session-2",
            "completed",
            None,
            [{"dataset_name": "mixed", "label": "Mixed", "benchmark_version": "mixed-v1"}],
            None,
            [],
            None,
            "",
            started_at,
            finished_at,
        )
        run_rows = [
            (
                "EVAL-2",
                "mixed",
                "offline_benchmark",
                "session-2::mixed",
                "mixed-v1",
                "mixed_route_v2",
                "completed",
                started_at,
                finished_at,
            )
        ]

        with patch.object(repository, "_query_rows", side_effect=[[session_row], run_rows]), patch.object(
            repository,
            "_benchmark_case_summary_rows",
            return_value={
                "EVAL-2": {
                    "case-1": {
                        "failure_stage": "retrieval",
                        "root_cause_label": "missing_expected_doc",
                        "execution_mode": "agentic",
                        "agent_fallback_used": False,
                        "category": "scenario",
                        "query_type": "how_to",
                        "source_type": "official_markdown_upload",
                        "evidence_precision_at_5": 0.7,
                        "evidence_recall_at_5": 0.6,
                        "evidence_ndcg_at_5": 0.65,
                        "context_relevance_score": 0.71,
                        "answer_relevance_score": 0.68,
                        "faithfulness_score": 0.74,
                        "citation_correctness_score": 0.8,
                        "response_completeness_score": 0.61,
                    },
                    "case-2": {
                        "failure_stage": "generation",
                        "root_cause_label": "unused_selected_evidence",
                        "execution_mode": "legacy",
                        "agent_fallback_used": True,
                        "category": "fact",
                        "query_type": "faq",
                        "source_type": "technical_article",
                        "evidence_precision_at_5": 0.9,
                        "evidence_recall_at_5": 0.88,
                        "evidence_ndcg_at_5": 0.89,
                        "context_relevance_score": 0.9,
                        "answer_relevance_score": 0.84,
                        "faithfulness_score": 0.81,
                        "citation_correctness_score": 0.86,
                        "response_completeness_score": 0.77,
                    },
                }
            },
        ):
            payload = repository._benchmark_session_payload_for_eval_run("EVAL-2")

        assert payload is not None
        diagnostics = payload["runs"][0]["diagnostics"]
        self.assertEqual(diagnostics["failure_stage_distribution"][0]["label"], "generation")
        self.assertEqual(diagnostics["root_cause_distribution"][0]["label"], "missing_expected_doc")
        self.assertEqual(diagnostics["category_distribution"][0]["label"], "fact")
        self.assertEqual(diagnostics["query_type_distribution"][0]["label"], "faq")
        self.assertEqual(diagnostics["source_type_distribution"][0]["label"], "official_markdown_upload")
        self.assertEqual(diagnostics["execution_mode_distribution"][0]["label"], "agentic")
        self.assertEqual(diagnostics["agent_fallback_distribution"][0]["label"], "false")

    def test_benchmark_session_payload_for_eval_run_uses_dataset_name_keys_for_gate_status(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        started_at = datetime(2026, 4, 2, 1, 0, tzinfo=timezone.utc)
        finished_at = datetime(2026, 4, 2, 1, 30, tzinfo=timezone.utc)
        session_row = (
            "BSESS-FAIL",
            "session-fail",
            "completed",
            None,
            [
                {
                    "dataset_name": "Canonical",
                    "label": "Canonical",
                    "benchmark_version": "canonical-v1",
                }
            ],
            None,
            [],
            None,
            "",
            started_at,
            finished_at,
        )
        run_rows = [
            (
                "EVAL-CANONICAL",
                "Canonical",
                "offline_benchmark",
                "session-fail::Canonical",
                "canonical-v1",
                "mixed_route_v2",
                "completed",
                started_at,
                finished_at,
            )
        ]

        with patch.object(repository, "_query_rows", side_effect=[[session_row], run_rows]), patch.object(
            repository,
            "_benchmark_case_summary_rows",
            return_value={
                "EVAL-CANONICAL": {
                    "case-1": {
                        "evidence_precision_at_5": 0.1,
                        "evidence_recall_at_5": 0.1,
                        "evidence_ndcg_at_5": 0.1,
                        "context_relevance_score": 0.1,
                        "answer_relevance_score": 0.1,
                        "faithfulness_score": 0.1,
                        "citation_correctness_score": 0.1,
                        "response_completeness_score": 0.1,
                        "benchmark_p95_total_latency_ms": 1800.0,
                        "benchmark_throughput_cases_per_sec": 0.41,
                        "judge_error_rate": 0.0,
                        "case_execution_error_rate": 0.0,
                    }
                }
            },
        ):
            payload = repository._benchmark_session_payload_for_eval_run("EVAL-CANONICAL")

        assert payload is not None
        self.assertEqual(payload["gate_status"], "fail")
        self.assertEqual(payload["failed_dataset_names"], ["Canonical"])
        self.assertEqual(payload["per_run_gate_status"]["Canonical"]["overall_status"], "fail")

    def test_benchmark_run_comparison_is_token_only(self) -> None:
        session_row = {
            "runs": [
                {
                    "eval_run_id": "run-current",
                    "dataset_name": "Canonical",
                    "benchmark_version": "canonical-v2",
                    "is_current": True,
                    "metrics": {"evidence_precision_at_5": 0.8},
                    "usage_summary": {"total_input_tokens": 100, "total_output_tokens": 25},
                },
                {
                    "eval_run_id": "run-baseline",
                    "dataset_name": "Canonical",
                    "benchmark_version": "canonical-v2",
                    "is_current": False,
                    "metrics": {"evidence_precision_at_5": 0.7},
                    "usage_summary": {"total_input_tokens": 90, "total_output_tokens": 20},
                },
            ]
        }

        comparison = knowledge_repository_module._benchmark_run_comparison(session_row["runs"])

        assert comparison is not None
        metric_names = [row["metric"] for row in comparison["rows"]]
        self.assertIn("total_input_tokens", metric_names)
        self.assertIn("total_output_tokens", metric_names)
        self.assertNotIn("known_cost_total", metric_names)

    def test_benchmark_trace_detail_exposes_query_understanding_and_candidate_funnel(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        row = {
            "eval_run_id": "EVAL-3",
            "experiment_id": "exp-3",
            "test_case_id": "case-123",
            "question": "How do I join a channel in Node.js?",
            "query_type": "how_to",
            "source_type": "official_markdown_upload",
            "trace_payload": {
                "actual_answer_text": "Use joinChannel.",
                "actual_route": "rag",
                "selected_contexts": [
                    {"chunk_id": "chunk-1", "doc_id": "doc-1", "heading": "Join Channel", "text": "Use joinChannel."}
                ],
                "retrieval_candidates": [
                    {
                        "chunk_id": "chunk-1",
                        "doc_id": "doc-1",
                        "rank_before_rerank": 1,
                        "rank_after_rerank": 1,
                        "used_in_final_answer": True,
                    }
                ],
                "dictionary_hits": [{"canonical_term": "Channel"}],
                "rule_expansions": ["agora channel"],
                "llm_expansions": ["join an Agora RTC channel"],
                "prf_expansions": ["joinchannel"],
                "hard_filter_sources": {"language": "rule"},
                "applied_hard_filters": {"language": "nodejs"},
                "applied_soft_signals": {"topic": ["join"]},
                "first_pass_candidate_count": 14,
                "second_pass_candidate_count": 6,
                "judge_votes": [{"judge_model": "openai:gpt-5.4", "answer_relevance_score": 0.9}],
                "judge_disagreement_flag": True,
            },
            "failure_stage": "generation",
            "failure_bucket": "retrieved_useful_context_but_answer_missed_it",
            "judge_error_rate": 0.33,
        }

        with patch.object(repository, "_chunk_details", return_value={"chunk-1": {"chunk_id": "chunk-1", "heading": "Join Channel"}}):
            payload = repository._benchmark_trace_detail(
                row,
                run_meta={
                    "benchmark_version": "mixed-v1",
                    "judge_models": ["openai:gpt-5.4"],
                    "strategy_snapshot": {"query_understanding_enabled": True, "answer_model": "gpt-5.4"},
                },
            )

        self.assertEqual(payload["query_understanding"]["dictionary_hits"][0]["canonical_term"], "Channel")
        self.assertEqual(payload["query_understanding"]["hard_filter_sources"]["language"], "rule")
        self.assertEqual(payload["candidate_funnel"]["first_pass_candidate_count"], 14)
        self.assertEqual(payload["candidate_funnel"]["second_pass_candidate_count"], 6)
        self.assertTrue(payload["judge_summary"]["judge_disagreement_flag"])
        self.assertEqual(payload["strategy_snapshot"]["answer_model"], "gpt-5.4")

    def test_benchmark_session_payload_for_eval_run_orders_runs_by_catalog_snapshot(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        started_at = datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc)
        finished_at = datetime(2026, 3, 30, 1, 30, tzinfo=timezone.utc)
        session_row = (
            "BSESS-1",
            "session-1",
            "completed",
            "BSESS-0",
            [
                {
                    "dataset_name": "agora_rag_testset_100_standrad_en",
                    "label": "Canonical",
                    "benchmark_version": "agora_rag_testset_100_standrad_en",
                },
                {
                    "dataset_name": "agora_rag_testset_100_mixed_en",
                    "label": "Mixed",
                    "benchmark_version": "agora_rag_testset_100_mixed_en",
                },
            ],
            "- Canonical cleanup: improved retrieval coverage",
            [{"entry_index": 4, "title": "Canonical cleanup", "summary": "improved retrieval coverage"}],
            4,
            "",
            started_at,
            finished_at,
        )
        run_rows = [
            (
                "EVAL-MIXED",
                "agora_rag_testset_100_mixed_en",
                "offline_benchmark",
                "session-1::agora_rag_testset_100_mixed_en",
                "agora_rag_testset_100_mixed_en",
                "canonical_v1",
                "completed",
                started_at,
                finished_at,
            ),
            (
                "EVAL-STANDRAD",
                "agora_rag_testset_100_standrad_en",
                "offline_benchmark",
                "session-1::agora_rag_testset_100_standrad_en",
                "agora_rag_testset_100_standrad_en",
                "canonical_v1",
                "completed",
                started_at,
                finished_at,
            ),
        ]

        with patch.object(repository, "_query_rows", side_effect=[[session_row], run_rows]):
            payload = repository._benchmark_session_payload_for_eval_run("EVAL-MIXED")

        assert payload is not None
        self.assertEqual(payload["benchmark_session_id"], "BSESS-1")
        self.assertEqual(
            [row["benchmark_version"] for row in payload["runs"]],
            [
                "agora_rag_testset_100_standrad_en",
                "agora_rag_testset_100_mixed_en",
            ],
        )
        self.assertEqual(payload["runs"][0]["label"], "Canonical")
        self.assertFalse(payload["runs"][0]["is_current"])
        self.assertTrue(payload["runs"][1]["is_current"])

    def test_scorecard_page_uses_eval_run_selector_rows_when_metric_experiments_are_empty(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")

        selector_rows = [
            {
                "experiment_id": "run-latest",
                "eval_run_id": "EVAL-LATEST",
                "benchmark_version": "agora_rag_testset_100_mixed_en",
                "created_at": "2026-03-26T07:47:08+00:00",
                "finished_at": None,
            },
            {
                "experiment_id": "run-older",
                "eval_run_id": "EVAL-OLDER",
                "benchmark_version": "agora_rag_testset_100_standrad_en",
                "created_at": "2026-03-26T07:17:48+00:00",
                "finished_at": None,
            },
        ]

        with patch.object(repository, "_experiment_rows", return_value=[]), patch.object(
            repository,
            "_benchmark_selector_rows",
            return_value=selector_rows,
        ), patch.object(
            repository,
            "_benchmark_case_summary_rows",
            return_value={},
        ), patch.object(
            repository,
            "_sample_deltas_from_cases",
            return_value=([], []),
        ):
            payload = repository._scorecard_workbench_page("7d", 7, {"limit": 20})

        selector = payload["benchmark_selector"]
        self.assertEqual(selector["current_experiment_id"], "run-latest")
        self.assertEqual(selector["available_experiments"][0]["eval_run_id"], "EVAL-LATEST")
        self.assertEqual(selector["available_experiments"][1]["eval_run_id"], "EVAL-OLDER")

    def test_data_supply_page_filters_benchmark_tables_to_current_run_version(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        candidate = {
            "experiment_id": "mixed-candidate",
            "eval_run_id": "run-mixed-candidate",
            "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
            "finished_at": "2026-03-25T12:00:00+00:00",
        }
        experiments = [
            candidate,
            {
                "experiment_id": "canonical-baseline",
                "eval_run_id": "run-canonical",
                "benchmark_version": "agora_rag_testset_100_canonical_en",
                "finished_at": "2026-03-24T12:00:00+00:00",
            },
        ]

        with patch.object(repository, "_experiment_rows", return_value=experiments), patch.object(
            repository,
            "_select_experiment_rows",
            return_value=(candidate, candidate),
        ), patch.object(
            repository,
            "_datasets_workbench_page",
            return_value={
                "sections": {
                    "summary": {
                        "cards": {
                            "dataset_version_count": 1,
                            "gold_item_count": 99,
                            "coverage_row_count": 12,
                        }
                    }
                },
                "has_eval_data": True,
            },
        ) as datasets_page_mock, patch.object(
            repository,
            "_knowledge_supply_workbench_page",
            return_value={
                "sections": {
                    "summary": {
                        "cards": {
                            "ingestion_job_count_24h": 3,
                            "avg_chunk_tokens": 420,
                            "index_freshness_minutes": 18,
                        }
                    }
                },
                "has_eval_data": True,
            },
        ):
            payload = repository._data_supply_workbench_page("7d", 7, {"limit": 20})

        datasets_filters = datasets_page_mock.call_args.args[2]
        self.assertEqual(datasets_filters["benchmark_version"], "agora_rag_testset_100_mixed_en_v2")
        self.assertEqual(
            payload["benchmark_selector"]["current_benchmark_version"],
            "agora_rag_testset_100_mixed_en_v2",
        )

    def test_routing_page_groups_cases_by_route_family_correct_in_test_case_order(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        candidate_cases = {
            "agora-mixed-002": {
                "eval_run_id": "run-mixed-candidate",
                "test_case_id": "agora-mixed-002",
                "question": "Second case",
                "category": "fact",
                "expected_route_family": "agora_docs_rag",
                "actual_route_family": "fallback_or_refuse",
                "expected_execution_action": "rag",
                "actual_execution_action": "refuse",
                "expected_tooling_profile": "agora_docs_only",
                "actual_tooling_profile": "no_agora_docs_refusal",
                "route_family_correct": 0.0,
                "failure_stage": "routing",
                "failure_bucket": "route_to_wrong_system",
            },
            "agora-mixed-001": {
                "eval_run_id": "run-mixed-candidate",
                "test_case_id": "agora-mixed-001",
                "question": "First case",
                "category": "fact",
                "expected_route_family": "agora_docs_rag",
                "actual_route_family": "agora_docs_rag",
                "expected_execution_action": "rag",
                "actual_execution_action": "rag",
                "expected_tooling_profile": "agora_docs_only",
                "actual_tooling_profile": "agora_docs_only",
                "route_family_correct": 1.0,
                "failure_stage": None,
                "failure_bucket": None,
            },
            "agora-mixed-003": {
                "eval_run_id": "run-mixed-candidate",
                "test_case_id": "agora-mixed-003",
                "question": "Third case",
                "category": "small_talk",
                "expected_route_family": "general_chat",
                "actual_route_family": "fallback_or_refuse",
                "expected_execution_action": "refuse",
                "actual_execution_action": "refuse",
                "expected_tooling_profile": "no_agora_docs_refusal",
                "actual_tooling_profile": "no_agora_docs_refusal",
                "route_family_correct": 0.0,
                "failure_stage": "routing",
                "failure_bucket": "route_to_wrong_system",
            },
        }

        with patch.object(
            repository,
            "_selected_benchmark_context",
            return_value=(
                [
                    {
                        "experiment_id": "mixed-candidate",
                        "eval_run_id": "run-mixed-candidate",
                        "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                    }
                ],
                {
                    "experiment_id": "mixed-candidate",
                    "eval_run_id": "run-mixed-candidate",
                    "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                },
                {
                    "experiment_id": "mixed-candidate",
                    "eval_run_id": "run-mixed-candidate",
                    "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                },
                {},
                candidate_cases,
                [{"test_case_id": "agora-mixed-003"}],
                [{"test_case_id": "agora-mixed-002"}],
            ),
        ):
            payload = repository._routing_workbench_page("7d", 7, {"limit": 20})

        routing_cases = payload["sections"]["routing_cases"]
        incorrect_rows = routing_cases["incorrect"]["rows"]
        correct_rows = routing_cases["correct"]["rows"]

        self.assertEqual([row["test_case_id"] for row in incorrect_rows], ["agora-mixed-002", "agora-mixed-003"])
        self.assertEqual([row["test_case_id"] for row in correct_rows], ["agora-mixed-001"])
        self.assertEqual(incorrect_rows[0]["expected_route_family"], "agora_docs_rag")
        self.assertEqual(incorrect_rows[0]["actual_route_family"], "fallback_or_refuse")
        self.assertEqual(correct_rows[0]["route_family_correct"], 1.0)
        self.assertEqual(payload["sections"]["summary"]["candidate_eval_run_id"], "run-mixed-candidate")

    def test_retrieval_page_groups_retrieval_eligible_cases_by_failure_stage(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        candidate_cases = {
            "agora-mixed-002": {
                "eval_run_id": "run-mixed-candidate",
                "test_case_id": "agora-mixed-002",
                "question": "Second case",
                "category": "scenario",
                "expected_route_family": "agora_docs_rag",
                "failure_stage": "retrieval",
                "failure_bucket": "retrieved_nothing_useful",
                "evidence_hit_at_5": 0.0,
                "evidence_coverage": 0.0,
                "noise_rate": 0.8,
            },
            "agora-mixed-001": {
                "eval_run_id": "run-mixed-candidate",
                "test_case_id": "agora-mixed-001",
                "question": "First case",
                "category": "fact",
                "expected_route_family": "agora_docs_rag",
                "failure_stage": None,
                "failure_bucket": None,
                "evidence_hit_at_5": 1.0,
                "evidence_coverage": 1.0,
                "noise_rate": 0.1,
            },
            "agora-mixed-003": {
                "eval_run_id": "run-mixed-candidate",
                "test_case_id": "agora-mixed-003",
                "question": "Route failed first",
                "category": "fact",
                "expected_route_family": "agora_docs_rag",
                "failure_stage": "routing",
                "failure_bucket": "route_to_wrong_system",
                "evidence_hit_at_5": 0.0,
                "evidence_coverage": 0.0,
                "noise_rate": 1.0,
            },
            "agora-mixed-004": {
                "eval_run_id": "run-mixed-candidate",
                "test_case_id": "agora-mixed-004",
                "question": "Non-rag case",
                "category": "small_talk",
                "expected_route_family": "general_chat",
                "failure_stage": None,
                "failure_bucket": None,
                "evidence_hit_at_5": None,
                "evidence_coverage": None,
                "noise_rate": None,
            },
        }

        with patch.object(
            repository,
            "_selected_benchmark_context",
            return_value=(
                [
                    {
                        "experiment_id": "mixed-candidate",
                        "eval_run_id": "run-mixed-candidate",
                        "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                    }
                ],
                {
                    "experiment_id": "mixed-candidate",
                    "eval_run_id": "run-mixed-candidate",
                    "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                },
                {
                    "experiment_id": "mixed-candidate",
                    "eval_run_id": "run-mixed-candidate",
                    "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                },
                {},
                candidate_cases,
                [{"test_case_id": "agora-mixed-001"}],
                [{"test_case_id": "agora-mixed-002"}],
            ),
        ):
            payload = repository._retrieval_workbench_page("7d", 7, {"limit": 20})

        retrieval_cases = payload["sections"]["retrieval_cases"]
        incorrect_rows = retrieval_cases["incorrect"]["rows"]
        correct_rows = retrieval_cases["correct"]["rows"]

        self.assertEqual([row["test_case_id"] for row in incorrect_rows], ["agora-mixed-002"])
        self.assertEqual([row["test_case_id"] for row in correct_rows], ["agora-mixed-001"])
        self.assertEqual(incorrect_rows[0]["failure_stage"], "retrieval")
        self.assertEqual(correct_rows[0]["evidence_hit_at_5"], 1.0)
        self.assertEqual(payload["sections"]["summary"]["candidate_eval_run_id"], "run-mixed-candidate")

    def test_generation_page_groups_generation_eligible_cases_and_includes_business_failures(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        candidate_cases = {
            "agora-mixed-003": {
                "eval_run_id": "run-mixed-candidate",
                "test_case_id": "agora-mixed-003",
                "question": "Policy miss",
                "category": "small_talk",
                "failure_stage": "business",
                "failure_bucket": "answer_should_not_have_used_agora_docs",
                "answer_accuracy_score": 0.4,
                "faithfulness_score": 0.7,
                "response_policy_followed": False,
            },
            "agora-mixed-001": {
                "eval_run_id": "run-mixed-candidate",
                "test_case_id": "agora-mixed-001",
                "question": "First case",
                "category": "fact",
                "failure_stage": None,
                "failure_bucket": None,
                "answer_accuracy_score": 0.9,
                "faithfulness_score": 0.95,
                "response_policy_followed": True,
            },
            "agora-mixed-002": {
                "eval_run_id": "run-mixed-candidate",
                "test_case_id": "agora-mixed-002",
                "question": "Retrieval miss only",
                "category": "fact",
                "failure_stage": "retrieval",
                "failure_bucket": "retrieved_nothing_useful",
                "answer_accuracy_score": 0.2,
                "faithfulness_score": 0.2,
                "response_policy_followed": True,
            },
            "agora-mixed-004": {
                "eval_run_id": "run-mixed-candidate",
                "test_case_id": "agora-mixed-004",
                "question": "Route failed first",
                "category": "fact",
                "failure_stage": "routing",
                "failure_bucket": "route_to_wrong_system",
                "answer_accuracy_score": 0.0,
                "faithfulness_score": 0.0,
                "response_policy_followed": False,
            },
        }

        with patch.object(
            repository,
            "_selected_benchmark_context",
            return_value=(
                [
                    {
                        "experiment_id": "mixed-candidate",
                        "eval_run_id": "run-mixed-candidate",
                        "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                    }
                ],
                {
                    "experiment_id": "mixed-candidate",
                    "eval_run_id": "run-mixed-candidate",
                    "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                },
                {
                    "experiment_id": "mixed-candidate",
                    "eval_run_id": "run-mixed-candidate",
                    "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                },
                {},
                candidate_cases,
                [{"test_case_id": "agora-mixed-001"}],
                [{"test_case_id": "agora-mixed-003"}],
            ),
        ):
            payload = repository._generation_workbench_page("7d", 7, {"limit": 20})

        generation_cases = payload["sections"]["generation_cases"]
        incorrect_rows = generation_cases["incorrect"]["rows"]
        correct_rows = generation_cases["correct"]["rows"]

        self.assertEqual([row["test_case_id"] for row in incorrect_rows], ["agora-mixed-003"])
        self.assertEqual([row["test_case_id"] for row in correct_rows], ["agora-mixed-001", "agora-mixed-002"])
        self.assertEqual(incorrect_rows[0]["failure_stage"], "business")
        self.assertEqual(correct_rows[1]["failure_stage"], "retrieval")
        self.assertEqual(payload["sections"]["summary"]["candidate_eval_run_id"], "run-mixed-candidate")

    def test_benchmark_case_detail_includes_route_contract_policy_and_deltas(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        primary_row = {
            "eval_run_id": "run-mixed-candidate",
            "test_case_id": "agora-mixed-001",
            "question": "What is a token?",
            "query_type": "fact",
            "source_type": "official_markdown_upload",
            "product": "rtc",
            "language": "en",
            "chunk_strategy": "markdown_header_v1",
            "retrieval_strategy": "hybrid_rrf",
            "answer_preview": "Candidate answer",
            "expected_route_family": "agora_docs_rag",
            "actual_route_family": "agora_docs_rag",
            "expected_execution_action": "rag",
            "actual_execution_action": "rag",
            "expected_tooling_profile": "agora_docs_only",
            "actual_tooling_profile": "agora_docs_only",
            "expected_document_ids": ["doc-1"],
            "expected_heading_paths": ["Guide > Token"],
            "expected_evidence_refs": [{"chunk_id": "chunk-1"}],
            "trace_payload": {
                "answer_text": "Candidate answer",
                "retrieval_candidates": [],
                "selected_contexts": [],
                "generation_mode": "structured_answer",
                "answer_sources": ["https://investor.agora.io/example"],
                "answer_citations": [{"source_url": "https://investor.agora.io/example", "title": "Example"}],
            },
            "evidence_hit_at_1": 0.0,
            "evidence_hit_at_3": 1.0,
            "evidence_hit_at_5": 1.0,
            "document_relevance_score": 0.7,
            "faithfulness_score": 0.8,
            "groundedness_score": 0.7,
            "response_relevance_score": 0.6,
            "response_completeness_score": 0.5,
            "citation_correctness_score": 0.9,
            "answer_accuracy_score": 0.8,
            "answer_logic_score": 0.75,
            "hallucination_flag": False,
            "failure_type": "grounded_answer",
            "failure_stage": "generation",
            "failure_bucket": "answer_correct_but_too_vague",
            "matched_expected_execution_action": True,
            "used_prohibited_agora_docs": False,
            "abstained_or_deflected_properly": True,
            "no_unsupported_claims": True,
            "response_policy_followed": True,
            "authoritative_source_used": True,
            "citation_present": True,
            "unsupported_claim_avoidance": True,
            "judge_votes": [],
            "needs_human": False,
            "retrieval_latency_ms": 10.0,
            "generation_latency_ms": 20.0,
            "total_latency_ms": 30.0,
            "selected_doc_count": 2,
            "top1_similarity_score": 0.88,
            "avg_selected_similarity_score": 0.84,
        }
        baseline_row = dict(primary_row)
        baseline_row["eval_run_id"] = "run-mixed-baseline"
        baseline_row["answer_accuracy_score"] = 0.5
        baseline_row["response_policy_followed"] = False

        with patch.object(
            repository,
            "_benchmark_case_detail_rows",
            return_value={
                "run-mixed-candidate": {"agora-mixed-001": primary_row},
                "run-mixed-baseline": {"agora-mixed-001": baseline_row},
            },
        ), patch.object(
            repository,
            "_eval_run_meta_map",
            return_value={
                "run-mixed-candidate": {
                    "eval_run_id": "run-mixed-candidate",
                    "experiment_id": "mixed-candidate",
                    "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                    "judge_models": [],
                    "finished_at": "2026-03-23T12:00:00+00:00",
                },
                "run-mixed-baseline": {
                    "eval_run_id": "run-mixed-baseline",
                    "experiment_id": "mixed-baseline",
                    "benchmark_version": "agora_rag_testset_100_mixed_en_v2",
                    "judge_models": [],
                    "finished_at": "2026-03-22T12:00:00+00:00",
                },
            },
        ), patch.object(repository, "_chunk_details", return_value={}):
            payload = repository.rag_dashboard_benchmark_case_detail(
                "run-mixed-candidate",
                "agora-mixed-001",
                baseline_eval_run_id="run-mixed-baseline",
            )

        self.assertEqual(payload["primary"]["expected_route_family"], "agora_docs_rag")
        self.assertEqual(payload["primary"]["actual_execution_action"], "rag")
        self.assertEqual(payload["primary"]["failure_bucket"], "answer_correct_but_too_vague")
        self.assertTrue(payload["primary"]["response_policy_followed"])
        self.assertEqual(payload["primary"]["answer_sources"][0], "https://investor.agora.io/example")
        self.assertEqual(payload["baseline"]["eval_run_id"], "run-mixed-baseline")
        self.assertEqual(payload["deltas"]["answer_accuracy_score"], 0.3)
        self.assertEqual(payload["deltas"]["response_policy_followed"], 1.0)

    def test_benchmark_case_detail_uses_single_case_detail_rows_instead_of_full_run_scan(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        primary_row = {
            "eval_run_id": "run-mixed-candidate",
            "test_case_id": "agora-mixed-001",
            "question": "What is a token?",
            "route_family_correct": 1.0,
        }
        baseline_row = {
            "eval_run_id": "run-mixed-baseline",
            "test_case_id": "agora-mixed-001",
            "question": "What is a token?",
            "route_family_correct": 0.0,
        }

        with patch.object(
            repository,
            "_experiment_case_rows",
            side_effect=AssertionError("full run scan should not be used for single case detail"),
        ), patch.object(
            repository,
            "_benchmark_case_detail_rows",
            create=True,
            return_value={
                "run-mixed-candidate": {"agora-mixed-001": primary_row},
                "run-mixed-baseline": {"agora-mixed-001": baseline_row},
            },
        ) as detail_rows_mock, patch.object(
            repository,
            "_eval_run_meta_map",
            return_value={},
        ), patch.object(
            repository,
            "_benchmark_trace_detail",
            side_effect=lambda row, run_meta=None: {
                "eval_run_id": row["eval_run_id"],
                "question": row["question"],
                "route_family_correct": row["route_family_correct"],
            },
        ):
            payload = repository.rag_dashboard_benchmark_case_detail(
                "run-mixed-candidate",
                "agora-mixed-001",
                baseline_eval_run_id="run-mixed-baseline",
            )

        self.assertEqual(payload["primary"]["eval_run_id"], "run-mixed-candidate")
        self.assertEqual(payload["baseline"]["eval_run_id"], "run-mixed-baseline")
        detail_rows_mock.assert_called_once_with(
            ["run-mixed-candidate", "run-mixed-baseline"],
            test_case_id="agora-mixed-001",
        )


if __name__ == "__main__":
    unittest.main()
