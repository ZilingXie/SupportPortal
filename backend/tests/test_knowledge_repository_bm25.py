from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.repositories.knowledge_repository import (
    PostgresKnowledgeRepository,
    _KNOWLEDGE_BOOTSTRAP_VERSION,
    create_knowledge_repository,
)


class _FakeCursor:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, *_args, **_kwargs) -> None:
        if _args:
            self.executed.append(str(_args[0]))
        return None

    def executemany(self, *_args, **_kwargs) -> None:
        return None

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeConnection:
    def __init__(self) -> None:
        self._cursor = _FakeCursor()

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        return None

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _SequenceCursor(_FakeCursor):
    def __init__(self, *, fetchone_results=None) -> None:
        super().__init__()
        self.fetchone_results = list(fetchone_results or [])
        self.fetchall_results = []

    def fetchone(self):
        if not self.fetchone_results:
            return None
        return self.fetchone_results.pop(0)

    def fetchall(self):
        return list(self.fetchall_results)


class KnowledgeRepositoryBm25HookTests(unittest.TestCase):
    def test_bootstrap_version_advances_for_live_query_usage_schema_fix(self) -> None:
        self.assertNotEqual(_KNOWLEDGE_BOOTSTRAP_VERSION, "2026-04-02-query-understanding-v1")

    def test_initialize_accepts_jsonb_defaults_in_bm25_telemetry_schema(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")

        with patch.object(repository, "_connect", return_value=_FakeConnection()):
            with patch("backend.repositories.knowledge_repository.validate_embedding_provider_dim", return_value=1024):
                with patch.object(repository, "_ensure_vector_table"):
                    with patch.object(repository, "_backfill_bm25_index_if_needed", return_value=0):
                        repository.initialize()

    def test_initialize_skips_bm25_backfill_when_disabled(self) -> None:
        repository = PostgresKnowledgeRepository(
            dsn="postgresql://example",
            schema="supportportal",
            bm25_backfill_on_init=False,
        )

        with patch.object(repository, "_connect", return_value=_FakeConnection()):
            with patch("backend.repositories.knowledge_repository.validate_embedding_provider_dim", return_value=1024):
                with patch.object(repository, "_ensure_vector_table"):
                    with patch.object(repository, "_backfill_bm25_index_if_needed", return_value=0) as backfill_mock:
                        repository.initialize()

        backfill_mock.assert_not_called()

    def test_initialize_can_opt_into_bm25_backfill_via_startup_alias(self) -> None:
        repository = PostgresKnowledgeRepository(
            dsn="postgresql://example",
            schema="supportportal",
            bm25_backfill_on_init=False,
            bootstrap_bm25_on_startup=True,
        )

        with patch.object(repository, "_connect", return_value=_FakeConnection()):
            with patch("backend.repositories.knowledge_repository.validate_embedding_provider_dim", return_value=1024):
                with patch.object(repository, "_ensure_vector_table"):
                    with patch.object(repository, "_backfill_bm25_index_if_needed", return_value=124) as backfill_mock:
                        repository.initialize()

        backfill_mock.assert_called_once()

    def test_create_repository_disables_startup_backfill_by_default(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PGVECTOR_DSN": "postgresql://example",
                "PGVECTOR_SCHEMA": "supportportal",
                "PGVECTOR_DIM": "1024",
            },
            clear=True,
        ):
            repository = create_knowledge_repository()

        self.assertFalse(repository._bootstrap_bm25_on_startup)

    def test_initialize_skips_full_bootstrap_when_schema_version_matches(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")

        with patch.object(repository, "_connect", return_value=_FakeConnection()):
            with patch.object(repository, "_ensure_bootstrap_version_table") as ensure_version_table_mock:
                with patch.object(repository, "_bootstrap_version_matches", return_value=True):
                    with patch("backend.repositories.knowledge_repository.validate_embedding_provider_dim") as validate_mock:
                        with patch.object(repository, "_ensure_vector_table") as ensure_vector_mock:
                            with patch.object(repository, "_backfill_bm25_index_if_needed") as backfill_mock:
                                repository.initialize()

        ensure_version_table_mock.assert_called_once()
        validate_mock.assert_not_called()
        ensure_vector_mock.assert_not_called()
        backfill_mock.assert_not_called()

    def test_initialize_records_bootstrap_version_after_full_bootstrap(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")

        with patch.object(repository, "_connect", return_value=_FakeConnection()):
            with patch.object(repository, "_ensure_bootstrap_version_table"):
                with patch.object(repository, "_bootstrap_version_matches", return_value=False):
                    with patch.object(repository, "_record_bootstrap_version") as record_version_mock:
                        with patch("backend.repositories.knowledge_repository.validate_embedding_provider_dim", return_value=1024):
                            with patch.object(repository, "_ensure_vector_table"):
                                with patch.object(repository, "_backfill_bm25_index_if_needed", return_value=0):
                                    repository.initialize()

        record_version_mock.assert_called_once()

    def test_initialize_replays_query_run_usage_column_alters_when_bootstrap_runs(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        fake_connection = _FakeConnection()

        with patch.object(repository, "_connect", return_value=fake_connection):
            with patch.object(repository, "_ensure_bootstrap_version_table"):
                with patch.object(repository, "_bootstrap_version_matches", return_value=False):
                    with patch.object(repository, "_record_bootstrap_version"):
                        with patch("backend.repositories.knowledge_repository.validate_embedding_provider_dim", return_value=1024):
                            with patch.object(repository, "_ensure_vector_table"):
                                with patch.object(repository, "_backfill_bm25_index_if_needed", return_value=0):
                                    repository.initialize()

        executed_sql = "\n".join(fake_connection._cursor.executed)
        self.assertIn("ADD COLUMN IF NOT EXISTS usage_ledger", executed_sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS usage_summary", executed_sql)

    def test_initialize_replays_query_run_usage_column_alters_when_schema_version_matches(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        fake_connection = _FakeConnection()

        with patch.object(repository, "_connect", return_value=fake_connection):
            with patch.object(repository, "_ensure_bootstrap_version_table"):
                with patch.object(repository, "_bootstrap_version_matches", return_value=True):
                    with patch("backend.repositories.knowledge_repository.validate_embedding_provider_dim") as validate_mock:
                        with patch.object(repository, "_ensure_vector_table") as ensure_vector_mock:
                            with patch.object(repository, "_record_bootstrap_version") as record_version_mock:
                                with patch.object(repository, "_backfill_bm25_index_if_needed") as backfill_mock:
                                    repository.initialize()

        executed_sql = "\n".join(fake_connection._cursor.executed)
        self.assertIn("ADD COLUMN IF NOT EXISTS usage_ledger", executed_sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS usage_summary", executed_sql)
        validate_mock.assert_not_called()
        ensure_vector_mock.assert_not_called()
        record_version_mock.assert_not_called()
        backfill_mock.assert_not_called()

    def test_backfill_bm25_index_runs_when_primary_chunks_exist_without_docs(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        cursor = _SequenceCursor(fetchone_results=[(0,), (124,)])

        with patch.object(repository, "_rebuild_bm25_index_from_vector_table", return_value=124) as rebuild_mock:
            rebuilt = repository._backfill_bm25_index_if_needed(cur=cursor)

        self.assertEqual(rebuilt, 124)
        rebuild_mock.assert_called_once()

    def test_backfill_bm25_index_skips_when_counts_match(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        cursor = _SequenceCursor(fetchone_results=[(124,), (124,)])

        with patch.object(repository, "_rebuild_bm25_index_from_vector_table") as rebuild_mock:
            rebuilt = repository._backfill_bm25_index_if_needed(cur=cursor)

        self.assertEqual(rebuilt, 0)
        rebuild_mock.assert_not_called()

    def test_replace_document_chunks_updates_bm25_index_for_primary_rows(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        rows = [
            {
                "id": "chunk-1",
                "doc_id": "doc-1",
                "source_path": "official/doc.md",
                "content": "Token generation details",
                "metadata": {},
                "embedding": [0.1, 0.2, 0.3],
            }
        ]

        with patch.object(repository, "_connect", return_value=_FakeConnection()):
            with patch.object(repository, "_ensure_vector_table"):
                with patch.object(repository, "_ensure_bm25_tables") as ensure_bm25_mock:
                    with patch.object(repository, "_replace_bm25_document_index") as bm25_mock:
                        repository.replace_document_chunks(
                            document_id="doc-1",
                            index_role="primary",
                            vector_dim=3,
                            rows=rows,
                        )

        bm25_mock.assert_called_once()
        ensure_bm25_mock.assert_not_called()

    def test_replace_document_chunks_updates_bm25_index_for_shadow_rows(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        rows = [
            {
                "id": "chunk-shadow",
                "doc_id": "doc-1",
                "source_path": "official/doc.md",
                "content": "Shadow retrieval text",
                "metadata": {},
                "embedding": [0.1, 0.2, 0.3],
            }
        ]

        with patch.object(repository, "_connect", return_value=_FakeConnection()):
            with patch.object(repository, "_ensure_vector_table"):
                with patch.object(repository, "_ensure_bm25_tables") as ensure_bm25_mock:
                    with patch.object(repository, "_replace_bm25_document_index") as bm25_mock:
                        repository.replace_document_chunks(
                            document_id="doc-1",
                            index_role="shadow",
                            vector_dim=3,
                            rows=rows,
                        )

        bm25_mock.assert_called_once()
        ensure_bm25_mock.assert_not_called()

    def test_replace_document_chunks_invalidates_active_vector_table_cache(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        rows = [
            {
                "id": "chunk-1",
                "doc_id": "doc-1",
                "source_path": "official/doc.md",
                "content": "Token generation details",
                "metadata": {},
                "embedding": [0.1, 0.2, 0.3],
            }
        ]

        with patch.object(repository, "_connect", return_value=_FakeConnection()):
            with patch.object(repository, "_ensure_vector_table"):
                with patch.object(repository, "_replace_bm25_document_index"):
                    with patch(
                        "backend.services.rag_qa.clear_active_vector_table_cache",
                        create=True,
                    ) as clear_cache_mock:
                        repository.replace_document_chunks(
                            document_id="doc-1",
                            index_role="primary",
                            vector_dim=3,
                            rows=rows,
                        )

        clear_cache_mock.assert_called_once()

    def test_replace_bm25_document_index_acquires_write_lock(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        cursor = _FakeCursor()
        order: list[str] = []

        with patch.object(repository, "_ensure_bm25_tables", side_effect=lambda **_: order.append("ensure")):
            with patch.object(
                repository,
                "_acquire_bm25_write_lock",
                side_effect=lambda **_: order.append("lock"),
            ) as lock_mock:
                with patch(
                    "backend.repositories.knowledge_repository.build_bm25_index_payload",
                    return_value={"docs": [], "postings": [], "terms": [], "stats": {"doc_count": 0, "avg_doc_length": 0.0}},
                ):
                    repository._replace_bm25_document_index(
                        cur=cursor,
                        document_id="doc-1",
                        index_role="primary",
                        rows=[],
                    )

        lock_mock.assert_called_once_with(cur=cursor, index_role="primary")
        self.assertEqual(order[:2], ["lock", "ensure"])

    def test_rebuild_bm25_index_acquires_write_lock(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        cursor = _SequenceCursor()
        order: list[str] = []

        with patch.object(repository, "_ensure_bm25_tables", side_effect=lambda **_: order.append("ensure")):
            with patch.object(
                repository,
                "_acquire_bm25_write_lock",
                side_effect=lambda **_: order.append("lock"),
            ) as lock_mock:
                with patch(
                    "backend.repositories.knowledge_repository.build_bm25_index_payload",
                    return_value={"docs": [], "postings": [], "terms": [], "stats": {"doc_count": 0, "avg_doc_length": 0.0}},
                ):
                    rebuilt = repository._rebuild_bm25_index_from_vector_table(cur=cursor, index_role="primary")

        self.assertEqual(rebuilt, 0)
        lock_mock.assert_called_once_with(cur=cursor, index_role="primary")
        self.assertEqual(order[:2], ["lock", "ensure"])

    def test_rebuild_bm25_index_supports_shadow_rows(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        cursor = _SequenceCursor()

        with patch(
            "backend.repositories.knowledge_repository.build_bm25_index_payload",
            return_value={"docs": [], "postings": [], "terms": [], "stats": {"doc_count": 0, "avg_doc_length": 0.0}},
        ):
            rebuilt = repository._rebuild_bm25_index_from_vector_table(cur=cursor, index_role="shadow")

        self.assertEqual(rebuilt, 0)

    def test_replace_bm25_document_index_supports_shadow_rows(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        cursor = _FakeCursor()

        with patch.object(repository, "_ensure_bm25_tables"):
            with patch.object(repository, "_acquire_bm25_write_lock") as lock_mock:
                with patch(
                    "backend.repositories.knowledge_repository.build_bm25_index_payload",
                    return_value={"docs": [], "postings": [], "terms": [], "stats": {"doc_count": 0, "avg_doc_length": 0.0}},
                ):
                    repository._replace_bm25_document_index(
                        cur=cursor,
                        document_id="doc-1",
                        index_role="shadow",
                        rows=[],
                    )

        lock_mock.assert_called_once_with(cur=cursor, index_role="shadow")


if __name__ == "__main__":
    unittest.main()
