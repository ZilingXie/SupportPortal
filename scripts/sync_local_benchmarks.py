#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.repositories.knowledge_repository import create_knowledge_repository
from backend.services.local_benchmark_sync import sync_default_local_benchmarks

load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)


def main() -> int:
    repository = create_knowledge_repository()
    prepare = getattr(repository, "prepare_rag_benchmark_run", None)
    if callable(prepare):
        prepare()
    else:
        repository.initialize()
    results = sync_default_local_benchmarks(repository)
    for result in results:
        print(f"Dataset: {result.get('dataset_name')}")
        print(f"- Benchmark version: {result.get('benchmark_version')}")
        print(f"- Dataset id: {result.get('dataset_id')}")
        print(f"- Generation run id: {result.get('generation_run_id')}")
        print(f"- Cases: {result.get('case_count')}")
        print(f"- Status: {result.get('status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
