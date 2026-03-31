from __future__ import annotations

import os
import unittest

if __import__("importlib.util").util.find_spec("psycopg") is None:
    raise unittest.SkipTest("psycopg is not installed in the local test environment")

import psycopg

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("PGVECTOR_DSN", "postgresql://example.invalid/supportportal")
os.environ.setdefault("PGVECTOR_DIM", "1024")
os.environ.setdefault("SILICONFLOW_EMBEDDING_DIMENSIONS", "1024")

import backend.main as main
import backend.rag_api as rag_api
from backend.repositories.event_repository import InMemoryEventRepository
from backend.repositories.ticket_repository import InMemoryTicketRepository


class _FailingRepository:
    def __init__(self, message: str) -> None:
        self.message = message

    def initialize(self) -> None:
        raise psycopg.OperationalError(self.message)

    def storage_mode(self) -> str:
        return "postgres"


class _TrackingKnowledgeRepository:
    def __init__(self) -> None:
        self.initialize_calls = 0

    def initialize(self) -> None:
        self.initialize_calls += 1

    def storage_mode(self) -> str:
        return "postgres"


class StartupRepositoryFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_ticket_repository = main.ticket_repository
        self.original_event_repository = rag_api.event_repository
        self.original_knowledge_repository = rag_api.knowledge_repository

    def tearDown(self) -> None:
        main.ticket_repository = self.original_ticket_repository
        rag_api.event_repository = self.original_event_repository
        rag_api.knowledge_repository = self.original_knowledge_repository

    def test_main_startup_falls_back_to_in_memory_ticket_repository_when_ticket_db_init_fails(self) -> None:
        main.ticket_repository = _FailingRepository("connection timeout expired")

        main.startup_event()

        self.assertIsInstance(main.ticket_repository, InMemoryTicketRepository)
        self.assertEqual(main.ticket_repository.storage_mode(), "memory")

    def test_rag_api_startup_falls_back_to_in_memory_event_repository_when_event_db_init_fails(self) -> None:
        rag_api.event_repository = _FailingRepository("connection timeout expired")
        knowledge_repository = _TrackingKnowledgeRepository()
        rag_api.knowledge_repository = knowledge_repository

        rag_api.startup_event()

        self.assertIsInstance(rag_api.event_repository, InMemoryEventRepository)
        self.assertEqual(rag_api.event_repository.storage_mode(), "memory")
        self.assertEqual(knowledge_repository.initialize_calls, 1)

    def test_rag_api_startup_still_raises_when_knowledge_repository_init_fails(self) -> None:
        rag_api.event_repository = InMemoryEventRepository()
        rag_api.knowledge_repository = _FailingRepository("pgvector unavailable")

        with self.assertRaises(psycopg.OperationalError):
            rag_api.startup_event()
