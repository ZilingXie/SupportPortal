#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
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

load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)


def run_benchmark(**kwargs):
    from backend.services.rag_benchmark_runner import run_benchmark as _run_benchmark

    return _run_benchmark(**kwargs)


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
    parser.add_argument(
        "--mode",
        choices=["pure_rag", "rag_plus_kg", "rag_vs_rag_plus_kg"],
        default="pure_rag",
        help="Benchmark mode. rag_vs_rag_plus_kg runs pure RAG then RAG+KG and writes a comparison report.",
    )
    parser.add_argument(
        "--comparison-output",
        default=None,
        help="Path to write the rag_vs_rag_plus_kg comparison JSON report.",
    )
    return parser


def _write_json(path: str | None, payload: dict[str, object]) -> None:
    if not path:
        return
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Comparison report written: {output_path}")


def _run_benchmark_with_kg_flag(*, kg_enabled: bool, **kwargs):
    previous_flag = os.environ.get("RAG_KG_AUXILIARY_ENABLED")
    os.environ["RAG_KG_AUXILIARY_ENABLED"] = "true" if kg_enabled else "false"
    try:
        return run_benchmark(**kwargs)
    finally:
        if previous_flag is None:
            os.environ.pop("RAG_KG_AUXILIARY_ENABLED", None)
        else:
            os.environ["RAG_KG_AUXILIARY_ENABLED"] = previous_flag


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

    if args.mode == "rag_vs_rag_plus_kg":
        from backend.services.rag_kg_benchmark_compare import build_rag_vs_kg_comparison_report

        pure_summary = _run_benchmark_with_kg_flag(
            kg_enabled=False,
            dataset_path=dataset_path,
            experiment_id=args.experiment_id or "pure_rag",
            limit=args.limit,
            top_k=args.top_k,
            initialize_repository=True,
        )
        kg_summary = _run_benchmark_with_kg_flag(
            kg_enabled=True,
            dataset_path=dataset_path,
            experiment_id=args.experiment_id or "rag_plus_kg",
            limit=args.limit,
            top_k=args.top_k,
            initialize_repository=True,
        )
        summary = kg_summary
        comparison = build_rag_vs_kg_comparison_report(
            pure_rag_summary=pure_summary,
            rag_plus_kg_summary=kg_summary,
        )
        _write_json(args.comparison_output, comparison)
        print(f"Comparison gate: {'PASS' if comparison['gate']['passed'] else 'FAIL'}")
        for reason in comparison["gate"]["reasons"]:
            print(f"- gate_reason: {reason}")
    else:
        summary = _run_benchmark_with_kg_flag(
            kg_enabled=args.mode == "rag_plus_kg",
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
