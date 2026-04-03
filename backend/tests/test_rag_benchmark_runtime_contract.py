from __future__ import annotations

import unittest
from pathlib import Path


class RagBenchmarkRuntimeContractTests(unittest.TestCase):
    def test_backend_container_copies_docs_and_benchmarks_for_local_benchmark_sessions(self) -> None:
        dockerfile = Path("backend/Dockerfile").read_text(encoding="utf-8")

        self.assertIn("COPY benchmarks /app/benchmarks", dockerfile)
        self.assertIn("COPY docs /app/docs", dockerfile)


if __name__ == "__main__":
    unittest.main()
