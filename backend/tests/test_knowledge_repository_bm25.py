from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.repositories.knowledge_repository import PostgresKnowledgeRepository


class _FakeCursor:
    def execute(self, *_args, **_kwargs) -> None:
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
    def cursor(self) -> _FakeCursor:
        return _FakeCursor()

    def commit(self) -> None:
        return None

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _SequenceCursor(_FakeCursor):
    def __init__(self, *, fetchone_results=None) -> None:
        self.fetchone_results = list(fetchone_results or [])

    def fetchone(self):
        if not self.fetchone_results:
            return None
        return self.fetchone_results.pop(0)


class KnowledgeRepositoryBm25HookTests(unittest.TestCase):
    def test_initialize_accepts_jsonb_defaults_in_bm25_telemetry_schema(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")

        with patch.object(repository, "_connect", return_value=_FakeConnection()):
            with patch("backend.repositories.knowledge_repository.validate_embedding_provider_dim", return_value=1024):
                with patch.object(repository, "_ensure_vector_table"):
                    with patch.object(repository, "_backfill_bm25_index_if_needed", return_value=0):
                        repository.initialize()

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
                with patch.object(repository, "_replace_bm25_document_index") as bm25_mock:
                    repository.replace_document_chunks(
                        document_id="doc-1",
                        index_role="primary",
                        vector_dim=3,
                        rows=rows,
                    )

        bm25_mock.assert_called_once()

    def test_replace_document_chunks_does_not_update_bm25_index_for_shadow_rows(self) -> None:
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
                with patch.object(repository, "_replace_bm25_document_index") as bm25_mock:
                    repository.replace_document_chunks(
                        document_id="doc-1",
                        index_role="shadow",
                        vector_dim=3,
                        rows=rows,
                    )

        bm25_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
