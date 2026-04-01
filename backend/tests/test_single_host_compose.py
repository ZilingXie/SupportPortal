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


if __name__ == "__main__":
    unittest.main()
