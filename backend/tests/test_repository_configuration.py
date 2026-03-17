from __future__ import annotations

import importlib.util
import os
import unittest
from unittest.mock import patch

if importlib.util.find_spec("psycopg") is None:
    raise unittest.SkipTest("psycopg is not installed in the local test environment")

from backend.repositories.event_repository import PostgresEventRepository, create_event_repository
from backend.repositories.knowledge_repository import (
    PostgresKnowledgeRepository,
    create_knowledge_repository,
)
from backend.repositories.ticket_repository import PostgresTicketRepository, create_ticket_repository


class RepositoryConfigurationTests(unittest.TestCase):
    def test_ticket_repository_requires_ticket_db_dsn(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                create_ticket_repository()

    def test_event_repository_requires_ticket_db_dsn(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                create_event_repository()

    def test_knowledge_repository_requires_pgvector_dsn(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                create_knowledge_repository()

    def test_ticket_repository_defaults_to_supportportal_schema(self) -> None:
        with patch.dict(os.environ, {"TICKET_DB_DSN": "postgresql://example"}, clear=True):
            repository = create_ticket_repository()
        self.assertIsInstance(repository, PostgresTicketRepository)
        self.assertEqual(repository._schema, "supportportal")

    def test_event_repository_defaults_to_supportportal_schema(self) -> None:
        with patch.dict(os.environ, {"TICKET_DB_DSN": "postgresql://example"}, clear=True):
            repository = create_event_repository()
        self.assertIsInstance(repository, PostgresEventRepository)
        self.assertEqual(repository._schema, "supportportal")

    def test_knowledge_repository_defaults_to_supportportal_vector_table(self) -> None:
        with patch.dict(os.environ, {"PGVECTOR_DSN": "postgresql://example"}, clear=True):
            repository = create_knowledge_repository()
        self.assertIsInstance(repository, PostgresKnowledgeRepository)
        self.assertEqual(repository._schema, "supportportal")
        self.assertEqual(repository._vector_schema, "supportportal")
        self.assertEqual(repository._vector_table_name, "docagent_chunks")


if __name__ == "__main__":
    unittest.main()
