from __future__ import annotations

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "deployment" / "docker-compose.single-host.yml"


class SingleHostComposeTests(unittest.TestCase):
    def _service_block(self, service_name: str) -> str:
        content = COMPOSE_PATH.read_text(encoding="utf-8")
        match = re.search(rf"(?ms)^  {re.escape(service_name)}:\n(.*?)(?=^  [a-zA-Z0-9_]+:|\Z)", content)
        self.assertIsNotNone(match, f"{service_name} service block should exist in single-host compose")
        assert match is not None
        return match.group(1)

    def test_api_service_defaults_to_two_workers(self) -> None:
        api_block = self._service_block("api")

        self.assertIn(
            '- "${API_WORKERS:-2}"',
            api_block,
        )
        self.assertIn(
            "TICKET_DB_POOL_MIN_SIZE: ${TICKET_DB_POOL_MIN_SIZE:-1}",
            api_block,
        )
        self.assertIn(
            "TICKET_DB_POOL_MAX_SIZE: ${TICKET_DB_POOL_MAX_SIZE:-8}",
            api_block,
        )
        self.assertIn(
            "TICKET_DB_POOL_TIMEOUT_SECONDS: ${TICKET_DB_POOL_TIMEOUT_SECONDS:-5}",
            api_block,
        )

    def test_worker_query_service_defaults_query_concurrency_and_queue(self) -> None:
        worker_block = self._service_block("worker_query")

        self.assertIn(
            "WORKER_TASK_TYPES: ${WORKER_QUERY_TASK_TYPES:-ticket_query}",
            worker_block,
        )
        self.assertIn(
            "WORKER_CONCURRENCY: ${WORKER_QUERY_CONCURRENCY:-2}",
            worker_block,
        )
        self.assertIn(
            "TICKET_QUERY_QUEUE_NAME: ${TICKET_QUERY_QUEUE_NAME:-support.ticket_queries}",
            worker_block,
        )
        self.assertIn(
            "TICKET_DB_POOL_MAX_LIFETIME_SECONDS: ${TICKET_DB_POOL_MAX_LIFETIME_SECONDS:-300}",
            worker_block,
        )
        self.assertIn(
            "TICKET_DB_POOL_MAX_IDLE_SECONDS: ${TICKET_DB_POOL_MAX_IDLE_SECONDS:-60}",
            worker_block,
        )

    def test_worker_aux_service_defaults_sentiment_provider_and_queue(self) -> None:
        worker_block = self._service_block("worker_aux")

        self.assertIn(
            "WORKER_TASK_TYPES: ${WORKER_AUX_TASK_TYPES:-ticket_message_sentiment}",
            worker_block,
        )
        self.assertIn(
            "WORKER_CONCURRENCY: ${WORKER_AUX_CONCURRENCY:-1}",
            worker_block,
        )
        self.assertIn(
            "TICKET_AUX_QUEUE_NAME: ${TICKET_AUX_QUEUE_NAME:-support.ticket_aux}",
            worker_block,
        )
        self.assertIn(
            "SENTIMENT_PROVIDER: ${WORKER_SENTIMENT_PROVIDER:-legacy}",
            worker_block,
        )
        self.assertIn(
            "SENTIMENT_MODEL_ID: ${SENTIMENT_MODEL_ID:-j-hartmann/emotion-english-distilroberta-base}",
            worker_block,
        )
        self.assertIn(
            "SENTIMENT_MIN_CONFIDENCE: ${SENTIMENT_MIN_CONFIDENCE:-0.45}",
            worker_block,
        )

    def test_worker_service_mounts_huggingface_cache(self) -> None:
        worker_block = self._service_block("worker_query")

        self.assertIn(
            "volumes:\n      - huggingface_cache:/root/.cache/huggingface",
            worker_block,
        )

    def test_legacy_single_worker_service_is_removed(self) -> None:
        content = COMPOSE_PATH.read_text(encoding="utf-8")
        self.assertNotRegex(content, r"(?m)^  worker:\n")

    def test_client_rag_recovery_defaults_are_present(self) -> None:
        content = COMPOSE_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "CLIENT_RAG_SERVICE_TIMEOUT_SECONDS: ${CLIENT_RAG_SERVICE_TIMEOUT_SECONDS:-40.0}",
            content,
        )
        self.assertIn(
            "CLIENT_RAG_RECOVERY_WINDOW_SECONDS: ${CLIENT_RAG_RECOVERY_WINDOW_SECONDS:-15.0}",
            content,
        )
        self.assertIn(
            "CLIENT_RAG_RECOVERY_POLL_INTERVAL_SECONDS: ${CLIENT_RAG_RECOVERY_POLL_INTERVAL_SECONDS:-1.0}",
            content,
        )

    def test_client_ack_and_parallel_route_defaults_are_present(self) -> None:
        content = COMPOSE_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "CLIENT_ACK_MODEL: ${CLIENT_ACK_MODEL:-gpt-5.4-nano}",
            content,
        )
        self.assertIn(
            "CLIENT_ACK_REASONING_EFFORT: ${CLIENT_ACK_REASONING_EFFORT:-none}",
            content,
        )
        self.assertIn(
            "CLIENT_ACK_TIMEOUT_SECONDS: ${CLIENT_ACK_TIMEOUT_SECONDS:-5.0}",
            content,
        )
        self.assertIn(
            "CLIENT_ACK_MAX_OUTPUT_TOKENS: ${CLIENT_ACK_MAX_OUTPUT_TOKENS:-32}",
            content,
        )
        self.assertIn(
            "OPTIMISTIC_PARALLEL_ROUTE_ENABLED: ${OPTIMISTIC_PARALLEL_ROUTE_ENABLED:-true}",
            content,
        )
        self.assertIn(
            "CLIENT_ACK_FALLBACK_TIMEOUT_MS: ${CLIENT_ACK_FALLBACK_TIMEOUT_MS:-5000}",
            content,
        )

    def test_engineer_investigation_reply_defaults_are_present_for_api_service(self) -> None:
        api_block = self._service_block("api")

        self.assertIn(
            "ENGINEER_INVESTIGATION_REPLY_MODEL: ${ENGINEER_INVESTIGATION_REPLY_MODEL:-gpt-5.4}",
            api_block,
        )
        self.assertIn(
            "ENGINEER_INVESTIGATION_REPLY_REASONING_EFFORT: ${ENGINEER_INVESTIGATION_REPLY_REASONING_EFFORT:-medium}",
            api_block,
        )
        self.assertIn(
            "ENGINEER_INVESTIGATION_REPLY_TIMEOUT_SECONDS: ${ENGINEER_INVESTIGATION_REPLY_TIMEOUT_SECONDS:-20.0}",
            api_block,
        )
        self.assertIn(
            "ENGINEER_INVESTIGATION_REPLY_MAX_RETRIES: ${ENGINEER_INVESTIGATION_REPLY_MAX_RETRIES:-1}",
            api_block,
        )


if __name__ == "__main__":
    unittest.main()
