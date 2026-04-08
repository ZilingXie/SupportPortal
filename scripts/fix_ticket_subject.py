#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional in lightweight test environments
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False


REPO_ROOT = Path(__file__).resolve().parents[1]


def _shared_root_workspace() -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=1.0,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    common_dir = Path(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip() else None
    if common_dir is None or common_dir.name != ".git":
        return None
    return common_dir.parent


def _venv_python_candidates() -> list[Path]:
    candidates = [REPO_ROOT / ".venv" / "bin" / "python"]
    shared_root = _shared_root_workspace()
    if shared_root is not None:
        candidates.append(shared_root / ".venv" / "bin" / "python")
    ordered: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(candidate)
    return ordered


for candidate_python in _venv_python_candidates():
    if not candidate_python.exists():
        continue
    current_python = Path(sys.executable).resolve()
    if current_python != candidate_python.resolve():
        os.execv(str(candidate_python), [str(candidate_python), str(Path(__file__).resolve()), *sys.argv[1:]])
    break

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.repositories.ticket_repository import create_ticket_repository
from backend.services.ticket_title import derive_ticket_title

load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair a single ticket subject using the canonical title helper.")
    parser.add_argument("--ticket-id", required=True, help="Ticket id to inspect and optionally update.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the regenerated subject back to support_tickets. Defaults to dry-run.",
    )
    return parser


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _first_customer_message(ticket: dict[str, object]) -> str:
    messages = ticket.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        if _normalize_text(message.get("role")).lower() != "customer":
            continue
        content = _normalize_text(message.get("content"))
        if content:
            return content
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ticket_id = _normalize_text(args.ticket_id)
    repository = create_ticket_repository()
    repository.initialize()

    ticket = repository.get_ticket(ticket_id)
    if ticket is None:
        raise SystemExit(f"Ticket not found: {ticket_id}")

    customer_message = _first_customer_message(ticket)
    if not customer_message:
        raise SystemExit(f"No customer message found for ticket {ticket_id}")

    new_subject = _normalize_text(derive_ticket_title(customer_message))
    if not new_subject:
        raise SystemExit(f"Generated subject is empty for ticket {ticket_id}")

    old_subject = _normalize_text(ticket.get("subject"))
    print(f"ticket_id={ticket_id}")
    print(f"dry_run={not args.apply}")
    print(f"old_subject={old_subject}")
    print(f"new_subject={new_subject}")

    if args.apply:
        updated_ticket = dict(ticket)
        updated_ticket["subject"] = new_subject
        repository.save_ticket(updated_ticket, new_messages=[])
        print("applied=True")
    else:
        print("applied=False")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
