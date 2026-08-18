"""Run the single-host database bootstrap once, or verify it without DDL."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from backend.repositories.asset_repository import create_asset_repository
from backend.repositories.event_repository import create_event_repository
from backend.repositories.knowledge_repository import create_knowledge_repository
from backend.repositories.ticket_repository import create_ticket_repository
from backend.services.prompt_versioning import PromptVersionService
from backend.services.runtime_schema import check_runtime_schema, required_tables


def _check_only() -> dict[str, Any]:
    try:
        result = check_runtime_schema()
    except RuntimeError as exc:
        missing = [item.strip() for item in str(exc).split(":", 1)[-1].split(",") if item.strip()]
        return {"mode": "check-only", "ok": False, "missing": missing, "error": str(exc)}
    return {"mode": "check-only", **result}


def _bootstrap() -> dict[str, Any]:
    repositories: list[tuple[str, Any]] = []
    initialized: list[str] = []
    try:
        for name, factory in (
            ("ticket", create_ticket_repository),
            ("event", create_event_repository),
            ("asset", create_asset_repository),
            ("knowledge", create_knowledge_repository),
        ):
            repository = factory()
            repositories.append((name, repository))
            repository.initialize()
            initialized.append(name)
        PromptVersionService(repositories[0][1]).sync_catalog()
    finally:
        for _, repository in repositories:
            close = getattr(repository, "close", None)
            if callable(close):
                close()
    return {"mode": "bootstrap", "ok": True, "initialized": initialized, "prompt_catalog": "synced"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bootstrap or verify SupportPortal runtime schemas.")
    parser.add_argument("command", choices=("bootstrap", "check-only"))
    return parser


def run(argv: list[str] | None = None) -> dict[str, Any]:
    command = build_parser().parse_args(argv).command
    return _bootstrap() if command == "bootstrap" else _check_only()


def main(argv: list[str] | None = None) -> int:
    try:
        payload = run(argv)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
