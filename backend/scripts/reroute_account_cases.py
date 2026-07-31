from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.repositories.ticket_repository import create_ticket_repository
from backend.services.account_case_reroute import reroute_account_case
from backend.services.account_route_pipeline import ACCOUNT_ROUTE_PIPELINE_VERSION, account_case_labels


load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env", override=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reclassify Account Cases with the latest layered route pipeline without replaying handlers."
    )
    parser.add_argument("--apply", action="store_true", help="Persist route fields and audit executions.")
    parser.add_argument("--case-id", action="append", default=[], help="Only reroute a specific Account Case or ticket ID.")
    parser.add_argument("--limit", type=int, default=100000, help="Maximum cases to inspect.")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue after an individual case fails.")
    return parser


def _matches_case_id(item: dict[str, Any], selected: set[str]) -> bool:
    if not selected:
        return True
    identifiers = {
        str(item.get("account_case_id") or "").strip(),
        str(item.get("billing_ticket_id") or "").strip(),
        str(item.get("client_ticket_id") or item.get("ticket_id") or "").strip(),
    }
    return bool(identifiers & selected)


def _execute(args: argparse.Namespace, *, repository: Any | None = None) -> dict[str, Any]:
    owned_repository = repository is None
    repo = repository or create_ticket_repository()
    try:
        repo.initialize()
        selected = {str(value).strip() for value in args.case_id if str(value).strip()}
        cases = [
            item
            for item in repo.list_account_cases(limit=max(1, int(args.limit)), offset=0)
            if _matches_case_id(item, selected)
        ]
        route_counts: Counter[str] = Counter()
        previous_versions: Counter[str] = Counter()
        failures: list[dict[str, str]] = []
        changed = 0
        for item in cases:
            account_case_id = str(item.get("account_case_id") or item.get("billing_ticket_id") or "")
            try:
                result = reroute_account_case(item)
                _, secondary_label = account_case_labels(result.account_case)
                route_counts[secondary_label] += 1
                previous_versions[result.previous_pipeline_version or "missing"] += 1
                changed += int(result.changed)
                if args.apply:
                    repo.save_account_case(result.account_case)
                    repo.save_account_route_execution(result.route_execution)
            except Exception as exc:
                failures.append({"account_case_id": account_case_id, "error": str(exc)})
                if not args.continue_on_error:
                    raise
        return {
            "mode": "apply" if args.apply else "dry_run",
            "pipeline_version": ACCOUNT_ROUTE_PIPELINE_VERSION,
            "processed": len(cases) - len(failures),
            "changed": changed,
            "failed": len(failures),
            "route_counts": dict(sorted(route_counts.items())),
            "previous_pipeline_versions": dict(sorted(previous_versions.items())),
            "failures": failures,
        }
    finally:
        if owned_repository:
            close = getattr(repo, "close", None)
            if callable(close):
                close()


def run(argv: list[str] | None = None, *, repository: Any | None = None) -> dict[str, Any]:
    return _execute(build_parser().parse_args(argv), repository=repository)


def main(argv: list[str] | None = None) -> int:
    try:
        payload = run(argv)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1
    print(json.dumps({"ok": not payload["failed"], **payload}, ensure_ascii=True, sort_keys=True))
    return 0 if not payload["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
