#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional in lightweight test environments
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False

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
        default=None,
        help="Path to a local benchmark NDJSON or JSON array dataset under benchmarks/.",
    )
    parser.add_argument(
        "--dataset-id",
        default=None,
        help="Deprecated. Benchmark runs now accept only local --dataset files.",
    )
    parser.add_argument(
        "--tier",
        choices=["gold", "silver"],
        default="gold",
        help="Deprecated. Kept only for compatibility while benchmark runs are local-file only.",
    )
    parser.add_argument(
        "--experiment-id",
        default=None,
        help="Optional experiment id used to group this benchmark run in the dashboard.",
    )
    parser.add_argument(
        "--suite",
        default=None,
        help="Deprecated. Benchmark runs now accept only local --dataset files.",
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
    if args.dataset_id:
        parser.error("--dataset-id is disabled. Run benchmarks from a local --dataset file in benchmarks/ instead.")
    if args.suite:
        parser.error("--suite is disabled. Run benchmarks from a local --dataset file in benchmarks/ instead.")
    dataset_path = (
        Path(args.dataset).expanduser().resolve()
        if args.dataset
        else REPO_ROOT / "benchmarks" / "agora_rag_testset_100_mixed_en.json"
    )

    summary = run_benchmark(
        dataset_path=dataset_path,
        experiment_id=args.experiment_id,
        limit=args.limit,
        top_k=args.top_k,
        initialize_repository=True,
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
