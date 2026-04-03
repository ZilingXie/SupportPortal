from __future__ import annotations

import importlib.util
import io
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.services.rag_benchmark_readiness import BenchmarkReadinessError


def _load_script_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "run_rag_benchmark_session.py"
    spec = importlib.util.spec_from_file_location("run_rag_benchmark_session_script", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RunRagBenchmarkSessionCliTests(unittest.TestCase):
    def test_cli_prints_session_summary_and_run_results(self) -> None:
        module = _load_script_module()
        repository = Mock()

        with patch.object(module, "create_knowledge_repository", return_value=repository), patch.object(
            module,
            "run_local_benchmark_session",
            return_value={
                "benchmark_session_id": "BSESS-1",
                "session_name": "session-1",
                "previous_session_id": "BSESS-0",
                "improvement_summary": "- Retrieval tuning: improved evidence hit rate",
                "runs": [
                    {
                        "eval_run_id": "EVAL-1",
                        "dataset_name": "agora_rag_testset_100_standrad_en",
                        "benchmark_version": "agora_rag_testset_100_standrad_en",
                        "case_count": 100,
                        "status": "completed",
                    },
                    {
                        "eval_run_id": "EVAL-2",
                        "dataset_name": "agora_rag_testset_100_mixed_en",
                        "benchmark_version": "agora_rag_testset_100_mixed_en",
                        "case_count": 100,
                        "status": "completed",
                    },
                ],
            },
        ) as run_mock, patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = module.main(["--session-name", "session-1", "--top-k", "7"])

        self.assertEqual(exit_code, 0)
        repository.initialize.assert_called_once_with()
        run_mock.assert_called_once_with(
            repository=repository,
            session_name="session-1",
            top_k=7,
        )
        output = stdout.getvalue()
        self.assertIn("Benchmark session: BSESS-1", output)
        self.assertIn("Previous session: BSESS-0", output)
        self.assertIn(
            "Session protocol: Canonical + Mixed + Real User (content-hash benchmark versions)",
            output,
        )
        self.assertIn("Improvements since previous benchmark session:", output)
        self.assertIn("Eval run: EVAL-1", output)
        self.assertIn("Dataset: agora_rag_testset_100_standrad_en", output)
        self.assertIn("Eval run: EVAL-2", output)

    def test_cli_returns_error_when_session_is_not_ready(self) -> None:
        module = _load_script_module()
        repository = Mock()

        with patch.object(module, "create_knowledge_repository", return_value=repository), patch.object(
            module,
            "run_local_benchmark_session",
            side_effect=BenchmarkReadinessError(
                "Local benchmark session is not ready: benchmark expected_document_ids still miss 21 active knowledge docs",
                report={"ready_for_session": False},
            ),
        ), patch("sys.stderr", new_callable=io.StringIO) as stderr:
            exit_code = module.main(["--session-name", "session-2"])

        self.assertEqual(exit_code, 1)
        self.assertIn("not ready", stderr.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
