from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from backend.services.rag_reset import TableRef, select_rag_reset_targets


class RagResetTests(unittest.TestCase):
    def test_select_rag_reset_targets_only_includes_rag_tables_and_vector_table(self) -> None:
        tables = [
            TableRef(schema="supportportal", name="support_knowledge_ingestions"),
            TableRef(schema="supportportal", name="support_knowledge_documents"),
            TableRef(schema="supportportal", name="support_rag_query_runs"),
            TableRef(schema="supportportal", name="docagent_chunks_ag_docs_test_1024"),
            TableRef(schema="supportportal", name="tickets"),
            TableRef(schema="supportportal", name="events"),
        ]

        targets = select_rag_reset_targets(
            tables,
            app_schema="supportportal",
            vector_table="supportportal.docagent_chunks_ag_docs_test_1024",
        )

        self.assertEqual(
            [target.qualified_name for target in targets],
            [
                "supportportal.docagent_chunks_ag_docs_test_1024",
                "supportportal.support_knowledge_documents",
                "supportportal.support_knowledge_ingestions",
                "supportportal.support_rag_query_runs",
            ],
        )

    def test_select_rag_reset_targets_supports_vector_table_in_different_schema(self) -> None:
        tables = [
            TableRef(schema="supportportal", name="support_knowledge_ingestions"),
            TableRef(schema="supportportal", name="support_rag_query_candidates"),
            TableRef(schema="ragvectors", name="docagent_chunks_rebuild_1024"),
            TableRef(schema="ragvectors", name="other_vectors"),
            TableRef(schema="supportportal", name="tickets"),
        ]

        targets = select_rag_reset_targets(
            tables,
            app_schema="supportportal",
            vector_table="ragvectors.docagent_chunks_rebuild_1024",
        )

        self.assertEqual(
            [target.qualified_name for target in targets],
            [
                "ragvectors.docagent_chunks_rebuild_1024",
                "supportportal.support_knowledge_ingestions",
                "supportportal.support_rag_query_candidates",
            ],
        )

    def test_reset_rag_database_script_help_runs(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [sys.executable, "scripts/reset_rag_database.py", "--help"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Dry-run or reset RAG-related tables", result.stdout)


if __name__ == "__main__":
    unittest.main()
