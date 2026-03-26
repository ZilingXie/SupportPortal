from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


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
