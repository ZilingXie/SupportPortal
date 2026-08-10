from __future__ import annotations

import os
import unittest
from unittest.mock import patch

if __import__("importlib.util").util.find_spec("psycopg") is None:
    raise unittest.SkipTest("psycopg is not installed in the local test environment")

import psycopg

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("PGVECTOR_DSN", "postgresql://example.invalid/supportportal")
os.environ.setdefault("PGVECTOR_DIM", "1024")
os.environ.setdefault("SILICONFLOW_EMBEDDING_DIMENSIONS", "1024")

import backend.main as main
import backend.rag_api as rag_api
from backend.repositories.asset_repository import InMemoryAssetRepository
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


class _FlakyRepository:
    def __init__(self, failures: int, message: str = "connection timeout expired") -> None:
        self.failures = failures
        self.message = message
        self.initialize_calls = 0

    def initialize(self) -> None:
        self.initialize_calls += 1
        if self.initialize_calls <= self.failures:
            raise psycopg.OperationalError(self.message)

    def storage_mode(self) -> str:
        return "postgres"


class StartupRepositoryFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_ticket_repository = main.ticket_repository
        self.original_asset_repository = main.asset_repository
        self.original_event_repository = rag_api.event_repository
        self.original_knowledge_repository = rag_api.knowledge_repository
        self.prompt_catalog_patcher = patch.object(main.PromptVersionService, "sync_catalog")
        self.prompt_runtime_patcher = patch.object(main, "initialize_prompt_runtime")
        self.workspace_admin_patcher = patch.object(main, "_bootstrap_workspace_admin")
        self.prompt_catalog_patcher.start()
        self.prompt_runtime_patcher.start()
        self.workspace_admin_patcher.start()

    def tearDown(self) -> None:
        main._stop_account_reroute_dispatcher()
        self.workspace_admin_patcher.stop()
        self.prompt_runtime_patcher.stop()
        self.prompt_catalog_patcher.stop()
        main.ticket_repository = self.original_ticket_repository
        main.asset_repository = self.original_asset_repository
        rag_api.event_repository = self.original_event_repository
        rag_api.knowledge_repository = self.original_knowledge_repository

    def test_main_startup_falls_back_to_in_memory_ticket_repository_when_ticket_db_init_fails(self) -> None:
        main.ticket_repository = _FailingRepository("connection timeout expired")
        main.asset_repository = _FailingRepository("connection timeout expired")

        with (
            patch.dict(os.environ, {"TICKET_DB_STARTUP_INIT_RETRIES": "0"}, clear=False),
            patch("backend.main.time.sleep"),
            patch.object(main, "_start_account_reroute_dispatcher") as start_dispatcher,
        ):
            main.startup_event()

        self.assertIsInstance(main.ticket_repository, InMemoryTicketRepository)
        self.assertEqual(main.ticket_repository.storage_mode(), "memory")
        self.assertIsInstance(main.asset_repository, InMemoryAssetRepository)
        self.assertEqual(main.asset_repository.storage_mode(), "memory")
        start_dispatcher.assert_called_once_with()

    def test_main_startup_falls_back_to_in_memory_asset_repository_when_asset_db_init_fails(self) -> None:
        main.ticket_repository = _FailingRepository("connection timeout expired")
        main.asset_repository = _FailingRepository("connection timeout expired")

        with (
            patch.dict(os.environ, {"TICKET_DB_STARTUP_INIT_RETRIES": "0"}, clear=False),
            patch("backend.main.time.sleep"),
            patch.object(main, "_start_account_reroute_dispatcher") as start_dispatcher,
        ):
            main.startup_event()

        self.assertIsInstance(main.ticket_repository, InMemoryTicketRepository)
        self.assertEqual(main.ticket_repository.storage_mode(), "memory")
        self.assertIsInstance(main.asset_repository, InMemoryAssetRepository)
        self.assertEqual(main.asset_repository.storage_mode(), "memory")
        start_dispatcher.assert_called_once_with()

    def test_main_startup_retries_transient_ticket_db_init_failure_before_falling_back(self) -> None:
        repository = _FlakyRepository(failures=1)
        main.ticket_repository = repository
        main.asset_repository = InMemoryAssetRepository()

        with patch.dict(
            os.environ,
            {
                "TICKET_DB_STARTUP_INIT_RETRIES": "2",
                "TICKET_DB_STARTUP_INIT_RETRY_DELAY_SECONDS": "0.25",
            },
            clear=False,
        ):
            with (
                patch("backend.main.time.sleep") as sleep_mock,
                patch.object(main, "_start_account_reroute_dispatcher") as start_dispatcher,
            ):
                main.startup_event()

        self.assertIs(main.ticket_repository, repository)
        self.assertEqual(repository.storage_mode(), "postgres")
        self.assertEqual(repository.initialize_calls, 2)
        sleep_mock.assert_called_once_with(0.25)
        start_dispatcher.assert_called_once_with()

    def test_main_startup_keeps_ticket_repository_when_asset_repository_init_fails(self) -> None:
        ticket_repository = _TrackingKnowledgeRepository()
        main.ticket_repository = ticket_repository
        main.asset_repository = _FailingRepository("connection timeout expired")

        with patch.object(main, "_start_account_reroute_dispatcher") as start_dispatcher:
            main.startup_event()

        self.assertIs(main.ticket_repository, ticket_repository)
        self.assertEqual(ticket_repository.initialize_calls, 1)
        self.assertIsInstance(main.asset_repository, InMemoryAssetRepository)
        self.assertEqual(main.asset_repository.storage_mode(), "memory")
        start_dispatcher.assert_called_once_with()

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
