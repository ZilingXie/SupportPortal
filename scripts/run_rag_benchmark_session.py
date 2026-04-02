#!/usr/bin/env python3

from __future__ import annotations

import argparse
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

from backend.services.rag_benchmark_session import run_local_benchmark_session

load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def create_knowledge_repository():
    from backend.repositories.knowledge_repository import create_knowledge_repository as _create_knowledge_repository

    return _create_knowledge_repository()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the 3-group local RAG benchmark session with changelog-driven improvement notes."
    )
    parser.add_argument(
        "--session-name",
        default=None,
        help="Optional human-readable session name used to group the 3 local benchmark runs.",
    )
    parser.add_argument(
        "--top-k",
        type=_positive_int,
        default=None,
        help="Optional retrieval top-k override applied to each benchmark run in the session.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repository = create_knowledge_repository()
    repository.initialize()

    summary = run_local_benchmark_session(
        repository=repository,
        session_name=args.session_name,
        top_k=args.top_k,
    )
    print(f"Benchmark session: {summary['benchmark_session_id']}")
    print(f"Session name: {summary['session_name']}")
    print(f"Previous session: {summary.get('previous_session_id') or '(none)'}")
    print("Improvements since previous benchmark session:")
    print(summary.get("improvement_summary") or "(none)")
    for run in summary.get("runs") or []:
        print()
        print(f"Eval run: {run.get('eval_run_id')}")
        print(f"Dataset: {run.get('dataset_name')}")
        print(f"Benchmark version: {run.get('benchmark_version')}")
        print(f"Status: {run.get('status')}")
        if run.get("case_count") is not None:
            print(f"Cases: {run.get('case_count')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
