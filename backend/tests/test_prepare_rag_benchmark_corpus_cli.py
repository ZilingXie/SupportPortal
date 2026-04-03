from __future__ import annotations

import importlib.util
import io
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


def _load_script_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "prepare_rag_benchmark_corpus.py"
    spec = importlib.util.spec_from_file_location("prepare_rag_benchmark_corpus_script", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PrepareRagBenchmarkCorpusCliTests(unittest.TestCase):
    def test_cli_restores_missing_docs_syncs_datasets_and_reports_ready(self) -> None:
        module = _load_script_module()
        repository = Mock()

        reports = [
            {
                "ready_for_session": False,
                "missing_expected_document_ids": ["official-doc-2"],
                "restorable_missing_document_ids": ["official-doc-2"],
                "unrestorable_missing_document_ids": [],
                "missing_dataset_mirrors": [{"dataset_name": "benchmark"}],
                "failures": ["benchmark expected_document_ids still miss 1 active knowledge doc"],
            },
            {
                "ready_for_session": False,
                "missing_expected_document_ids": [],
                "restorable_missing_document_ids": [],
                "unrestorable_missing_document_ids": [],
                "missing_dataset_mirrors": [{"dataset_name": "benchmark"}],
                "failures": ["local benchmark datasets are not synced into support_rag_datasets"],
            },
            {
                "ready_for_session": True,
                "missing_expected_document_ids": [],
                "restorable_missing_document_ids": [],
                "unrestorable_missing_document_ids": [],
                "missing_dataset_mirrors": [],
                "failures": [],
            },
        ]

        with patch.object(module, "create_knowledge_repository", return_value=repository), patch.object(
            module,
            "build_local_benchmark_readiness_report",
            side_effect=reports,
        ) as report_mock, patch.object(
            module,
            "ingest_missing_benchmark_documents_from_ag_docs",
            return_value=[{"expected_document_id": "official-doc-2", "status": "completed"}],
        ) as ingest_mock, patch.object(
            module,
            "sync_default_local_benchmarks",
            return_value=[{"dataset_name": "benchmark", "status": "gold_ready"}],
        ) as sync_mock, patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = module.main([])

        self.assertEqual(exit_code, 0)
        repository.prepare_rag_benchmark_run.assert_called_once_with()
        self.assertEqual(report_mock.call_count, 3)
        ingest_mock.assert_called_once_with(
            repository=repository,
            missing_document_ids=["official-doc-2"],
        )
        sync_mock.assert_called_once_with(repository)
        self.assertIn("ready_for_session=True", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
