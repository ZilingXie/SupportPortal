from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.repositories.ticket_repository import create_ticket_repository
from backend.services.account_automation_state_repair import repair_account_automation_state


load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env", override=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Re-extract Fraud Account or Account Suspension state without sending mail or customer replies."
    )
    parser.add_argument("--case-id", action="append", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    return parser


def run(argv: list[str] | None = None, *, repository: Any | None = None) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    owned_repository = repository is None
    repo = repository or create_ticket_repository()
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    try:
        repo.initialize()
        for identifier in [str(value).strip() for value in args.case_id if str(value).strip()]:
            try:
                item = repo.get_account_case(identifier) or repo.get_account_case_by_ticket_id(identifier)
                if item is None:
                    raise ValueError("account case not found")
                ticket_id = str(item.get("client_ticket_id") or "").strip()
                ticket = repo.get_ticket(ticket_id) if ticket_id else None
                messages = list((ticket or {}).get("messages") or [])
                repaired = repair_account_automation_state(item, customer_messages=messages)
                if args.apply and repaired.changed:
                    repo.save_account_case(repaired.account_case)
                    if str(repaired.account_case.get("subcategory") or "") == "account_suspension":
                        repo.cancel_pending_account_reply_jobs(
                            ticket_id,
                            updated_at=str(repaired.account_case.get("updated_at") or ""),
                        )
                results.append(
                    {
                        "account_case_id": repaired.account_case.get("account_case_id"),
                        "subcategory": repaired.account_case.get("subcategory"),
                        "changed": repaired.changed,
                        "repair_status": repaired.repair_status,
                    }
                )
            except Exception as exc:
                failures.append({"case_id": identifier, "error": str(exc)})
                if not args.continue_on_error:
                    raise
        return {
            "mode": "apply" if args.apply else "dry_run",
            "processed": len(results),
            "failed": len(failures),
            "results": results,
            "failures": failures,
        }
    finally:
        if owned_repository:
            close = getattr(repo, "close", None)
            if callable(close):
                close()


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
