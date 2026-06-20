from __future__ import annotations

import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_script_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "run_rag_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_rag_benchmark_script", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RunRagBenchmarkCliTests(unittest.TestCase):
    def test_cli_rejects_dataset_id_entrypoint(self) -> None:
        module = _load_script_module()
        with self.assertRaises(SystemExit) as exc:
            module.main(["--dataset-id", "DS-123"])
        self.assertNotEqual(exc.exception.code, 0)

    def test_cli_rejects_suite_entrypoint(self) -> None:
        module = _load_script_module()
        with self.assertRaises(SystemExit) as exc:
            module.main(["--suite", "agora_rag_testset_100_mixed_en"])
        self.assertNotEqual(exc.exception.code, 0)

    def test_cli_rag_vs_kg_mode_runs_two_benchmarks_and_writes_comparison(self) -> None:
        module = _load_script_module()
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            dataset_path = Path(tmpdir) / "dataset.jsonl"
            comparison_path = Path(tmpdir) / "comparison.json"
            dataset_path.write_text("{}", encoding="utf-8")
            summaries = [
                {
                    "eval_run_id": "EVAL-PURE",
                    "dataset_name": "dataset.jsonl",
                    "benchmark_version": "v1",
                    "judge_models": ["openai:gpt-5.4"],
                    "case_count": 10,
                    "metrics": {
                        "citation_correctness_score": 0.95,
                        "faithfulness_score": 0.94,
                        "total_latency_ms_p95": 1000.0,
                    },
                },
                {
                    "eval_run_id": "EVAL-KG",
                    "dataset_name": "dataset.jsonl",
                    "benchmark_version": "v1",
                    "judge_models": ["openai:gpt-5.4"],
                    "case_count": 10,
                    "metrics": {
                        "citation_correctness_score": 0.95,
                        "faithfulness_score": 0.95,
                        "total_latency_ms_p95": 1050.0,
                        "kg_degrade_rate": 0.0,
                    },
                },
            ]
            flag_values: list[str | None] = []

            def fake_run_benchmark(**_kwargs):
                flag_values.append(os.environ.get("RAG_KG_AUXILIARY_ENABLED"))
                return summaries.pop(0)

            with patch.dict(os.environ, {"RAG_KG_AUXILIARY_ENABLED": "true"}):
                with patch.object(module, "run_benchmark", side_effect=fake_run_benchmark) as run_mock:
                    exit_code = module.main(
                        [
                            "--dataset",
                            str(dataset_path),
                            "--mode",
                            "rag_vs_rag_plus_kg",
                            "--comparison-output",
                            str(comparison_path),
                        ]
                    )
                restored_flag = os.environ.get("RAG_KG_AUXILIARY_ENABLED")
            report = json.loads(comparison_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(run_mock.call_count, 2)
        self.assertEqual(run_mock.call_args_list[0].kwargs["experiment_id"], "pure_rag")
        self.assertEqual(run_mock.call_args_list[1].kwargs["experiment_id"], "rag_plus_kg")
        self.assertEqual(flag_values, ["false", "true"])
        self.assertEqual(restored_flag, "true")
        self.assertEqual(report["mode"], "rag_vs_rag_plus_kg")
        self.assertTrue(report["gate"]["passed"])
