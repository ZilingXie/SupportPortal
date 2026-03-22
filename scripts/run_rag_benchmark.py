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
        default=str(REPO_ROOT / "benchmarks" / "supportportal_faq_v1.jsonl"),
        help="Path to the benchmark JSONL dataset.",
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
    args = build_parser().parse_args(argv)
    summary = run_benchmark(
        dataset_path=None if args.dataset_id else Path(args.dataset).expanduser().resolve(),
        dataset_id=args.dataset_id,
        dataset_tier=args.tier,
        experiment_id=args.experiment_id,
        limit=args.limit,
        top_k=args.top_k,
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
