from __future__ import annotations

import importlib.util
import os
import unittest
from unittest.mock import patch

HAS_PSYCOPG = importlib.util.find_spec("psycopg") is not None
if HAS_PSYCOPG:
    from backend.scripts import runtime_bootstrap
    from backend.services import runtime_schema
else:
    runtime_bootstrap = None  # type: ignore[assignment]
    runtime_schema = None  # type: ignore[assignment]


class _Repository:
    def __init__(self, name: str, calls: list[str], fail: bool = False) -> None:
        self.name = name
        self.calls = calls
        self.fail = fail

    def initialize(self) -> None:
        self.calls.append(f"init:{self.name}")
        if self.fail:
            raise RuntimeError(f"{self.name} failed")

    def close(self) -> None:
        self.calls.append(f"close:{self.name}")


@unittest.skipUnless(HAS_PSYCOPG, "psycopg is not installed in the local test environment")
class RuntimeBootstrapTests(unittest.TestCase):
    def test_bootstrap_initializes_repositories_in_order_and_closes_them(self) -> None:
        calls: list[str] = []
        repositories = [_Repository(name, calls) for name in ("ticket", "event", "asset", "knowledge")]
        factories = [patch.object(runtime_bootstrap, f"create_{name}_repository", return_value=repo) for name, repo in zip(("ticket", "event", "asset", "knowledge"), repositories)]
        with factories[0], factories[1], factories[2], factories[3], patch.object(
            runtime_bootstrap.PromptVersionService, "sync_catalog", side_effect=lambda: calls.append("prompt:sync")
        ):
            payload = runtime_bootstrap.run(["bootstrap"])

        self.assertEqual(payload["initialized"], ["ticket", "event", "asset", "knowledge"])
        self.assertEqual(calls, [
            "init:ticket", "init:event", "init:asset", "init:knowledge",
            "prompt:sync", "close:ticket", "close:event", "close:asset", "close:knowledge",
        ])

    def test_bootstrap_closes_created_repositories_when_a_later_stage_fails(self) -> None:
        calls: list[str] = []
        repositories = [_Repository("ticket", calls), _Repository("event", calls, fail=True)]
        with (
            patch.object(runtime_bootstrap, "create_ticket_repository", return_value=repositories[0]),
            patch.object(runtime_bootstrap, "create_event_repository", return_value=repositories[1]),
            patch.object(runtime_bootstrap, "create_asset_repository", side_effect=AssertionError("asset should not be created")),
        ):
            with self.assertRaisesRegex(RuntimeError, "event failed"):
                runtime_bootstrap.run(["bootstrap"])

        self.assertEqual(calls, ["init:ticket", "init:event", "close:ticket", "close:event"])

    def test_check_only_is_read_only_and_reports_missing_tables(self) -> None:
        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, params):
                self.query = query
                self.params = params

            def fetchall(self):
                return [("supportportal", "support_ticket_schema_meta")]

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def cursor(self):
                return Cursor()

        with patch.dict(os.environ, {"TICKET_DB_DSN": "ticket-dsn", "PGVECTOR_DSN": "vector-dsn"}, clear=False), patch.object(
            runtime_schema.psycopg, "connect", return_value=Connection()
        ) as connect, patch.object(runtime_bootstrap, "create_ticket_repository", side_effect=AssertionError("initialize must not run")):
            payload = runtime_bootstrap.run(["check-only"])

        self.assertFalse(payload["ok"])
        self.assertTrue(payload["missing"])
        self.assertEqual(connect.call_count, 2)

    def test_runtime_schema_covers_startup_core_tables(self) -> None:
        tables = runtime_schema.required_tables()

        self.assertTrue(
            {
                "support_ticket_schema_meta",
                "support_tickets",
                "support_ticket_events",
                "support_assets",
                "support_prompt_definitions",
                "support_prompt_releases",
            }.issubset(tables["ticket"])
        )
        self.assertTrue(
            {
                "support_knowledge_documents",
                "support_knowledge_ingestions",
                "support_rag_query_runs",
                "support_rag_query_candidates",
            }.issubset(tables["knowledge"])
        )


if __name__ == "__main__":
    unittest.main()
