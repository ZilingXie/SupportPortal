from __future__ import annotations

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "deployment" / "docker-compose.single-host.yml"


class SingleHostComposeTests(unittest.TestCase):
    def _worker_block(self) -> str:
        content = COMPOSE_PATH.read_text(encoding="utf-8")
        match = re.search(r"(?ms)^  worker:\n(.*?)(?=^  redis:)", content)
        self.assertIsNotNone(match, "worker service block should exist in single-host compose")
        assert match is not None
        return match.group(1)

    def test_worker_service_defaults_sentiment_provider_to_legacy(self) -> None:
        worker_block = self._worker_block()

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
        worker_block = self._worker_block()

        self.assertIn(
            "volumes:\n      - huggingface_cache:/root/.cache/huggingface",
            worker_block,
        )

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
            "CLIENT_ACK_TIMEOUT_SECONDS: ${CLIENT_ACK_TIMEOUT_SECONDS:-1.25}",
            content,
        )
        self.assertIn(
            "CLIENT_ACK_MAX_OUTPUT_TOKENS: ${CLIENT_ACK_MAX_OUTPUT_TOKENS:-32}",
            content,
        )
        self.assertIn(
            "CLIENT_ACK_SESSION_MODEL: ${CLIENT_ACK_SESSION_MODEL:-gpt-realtime-mini}",
            content,
        )
        self.assertIn(
            "CLIENT_ACK_SESSION_MAX_OUTPUT_TOKENS: ${CLIENT_ACK_SESSION_MAX_OUTPUT_TOKENS:-48}",
            content,
        )
        self.assertIn(
            "CLIENT_ACK_SESSION_TTL_SECONDS: ${CLIENT_ACK_SESSION_TTL_SECONDS:-60}",
            content,
        )
        self.assertIn(
            "OPTIMISTIC_PARALLEL_ROUTE_ENABLED: ${OPTIMISTIC_PARALLEL_ROUTE_ENABLED:-true}",
            content,
        )


if __name__ == "__main__":
    unittest.main()
