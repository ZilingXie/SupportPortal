#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.repositories.knowledge_repository import create_knowledge_repository
from backend.services.rag_benchmark_suite_importer import (
    SUPPORTED_BENCHMARK_SUITES,
    import_benchmark_suite,
)
from backend.services.rag_benchmark_runner import run_benchmark

load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the quality-first RAG offline benchmark.")
    parser.add_argument(
        "--dataset",
        default=None,
        help="Path to a benchmark JSONL dataset.",
    )
    parser.add_argument(
        "--dataset-id",
        default=None,
        help="Dataset snapshot id from support_rag_datasets. When provided, benchmark cases load from Postgres instead of JSONL.",
    )
    parser.add_argument(
        "--tier",
        choices=["gold", "silver"],
        default="gold",
        help="Dataset snapshot tier to benchmark when --dataset-id is used.",
    )
    parser.add_argument(
        "--experiment-id",
        default=None,
        help="Optional experiment id used to group this benchmark run in the dashboard.",
    )
    parser.add_argument(
        "--suite",
        choices=list(SUPPORTED_BENCHMARK_SUITES),
        default=None,
        help="Import one supported Agora benchmark suite into a gold dataset snapshot, then run the benchmark from that snapshot.",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="Optional maximum number of cases to execute from the dataset.",
    )
    parser.add_argument(
        "--top-k",
        type=_positive_int,
        default=None,
        help="Optional retrieval top-k override for the benchmark run.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    provided_sources = [bool(args.suite), bool(args.dataset_id), bool(args.dataset)]
    if sum(provided_sources) != 1:
        parser.error("Provide exactly one benchmark source: --suite, --dataset-id, or --dataset.")

    repository = None
    dataset_path = Path(args.dataset).expanduser().resolve() if args.dataset else None
    dataset_id = args.dataset_id
    dataset_tier = args.tier
    if args.suite:
        repository = create_knowledge_repository()
        imported = import_benchmark_suite(
            repository,
            suite_name=args.suite,
            question_language="en",
            initialize_repository=False,
        )
        dataset_id = imported["dataset_id"]
        dataset_path = None
        dataset_tier = "gold"
        print(f"Imported suite: {args.suite}")
        print(f"Imported dataset id: {imported['dataset_id']}")
        print(f"Imported benchmark version: {imported['benchmark_version']}")

    summary = run_benchmark(
        dataset_path=dataset_path,
        dataset_id=dataset_id,
        dataset_tier=dataset_tier,
        experiment_id=args.experiment_id,
        limit=args.limit,
        top_k=args.top_k,
        repository=repository,
        initialize_repository=False if repository is not None else True,
    )
    print(f"Eval run: {summary['eval_run_id']}")
    print(f"Dataset: {summary['dataset_name']}")
    print(f"Benchmark version: {summary['benchmark_version']}")
    print(f"Judge models: {', '.join(summary['judge_models'])}")
    print(f"Cases: {summary['case_count']}")
    for key, value in sorted((summary.get("metrics") or {}).items()):
        print(f"- {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
