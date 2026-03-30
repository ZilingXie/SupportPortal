from __future__ import annotations

import importlib.util
import io
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


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
        self.assertIn("Improvements since previous benchmark session:", output)
        self.assertIn("Eval run: EVAL-1", output)
        self.assertIn("Dataset: agora_rag_testset_100_standrad_en", output)
        self.assertIn("Eval run: EVAL-2", output)


if __name__ == "__main__":
    unittest.main()
