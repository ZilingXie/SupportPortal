#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.repositories.ticket_repository import create_ticket_repository
from backend.services.enablement_repair import repair_enablement_case
from backend.services.prompt_runtime import initialize_prompt_runtime


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Re-run AI field extraction for incomplete Enablement Account Cases.")
    parser.add_argument("--account-case-id", action="append", default=[], help="Limit repair to one Account Case ID.")
    parser.add_argument("--apply", action="store_true", help="Persist grounded fields. Default is dry-run.")
    parser.add_argument("--send-email", action="store_true", help="Send one internal email after applying a complete repair.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.send_email and not args.apply:
        raise SystemExit("--send-email requires --apply")
    repository = create_ticket_repository()
    try:
        repository.initialize()
        initialize_prompt_runtime(repository, service_name="enablement-field-repair")
        case_ids = [str(item).strip() for item in args.account_case_id if str(item).strip()]
        if not case_ids:
            case_ids = [
                str(case.get("account_case_id") or case.get("billing_ticket_id") or "").strip()
                for case in repository.list_account_cases(limit=10000, route_status="automated")
                if str(case.get("subcategory") or case.get("execution_action") or "").strip() == "enablement"
                and "app_id" in list(case.get("missing_fields") or [])
            ]
        results = [
            repair_enablement_case(
                repository,
                account_case_id=case_id,
                apply=bool(args.apply),
                send_email=bool(args.send_email),
            )
            for case_id in case_ids
            if case_id
        ]
        summary = {
            "mode": "apply" if args.apply else "dry_run",
            "send_email": bool(args.send_email),
            "total": len(results),
            "complete": sum(item.get("status") in {"complete", "already_complete"} for item in results),
            "human_review": sum(bool(item.get("requires_human_review")) for item in results),
            "applied": sum(bool(item.get("applied")) for item in results),
            "results": results,
        }
        print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True))
        return 0 if not any(item.get("status") == "not_found" for item in results) else 1
    finally:
        close = getattr(repository, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    raise SystemExit(main())
