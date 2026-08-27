"""Bootstrap or check the ECS Automation coordination and Account schemas."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from backend.repositories.ticket_repository import create_ticket_repository
from backend.services.automation_ecs_runtime import AutomationEcsSettings
from backend.services.automation_ecs_schema import check_account_runtime_schema
from backend.services.automation_ecs_store import create_automation_ecs_store
from backend.services.prompt_versioning import PromptVersionService


def bootstrap() -> dict[str, Any]:
    settings = AutomationEcsSettings.from_env("bootstrap")
    store = create_automation_ecs_store(settings)
    repository = create_ticket_repository()
    try:
        repository.initialize()
        PromptVersionService(repository).sync_catalog()
        store.migrate()
        store.check_schema()
        account = check_account_runtime_schema()
        return {
            "ok": True,
            "mode": "bootstrap",
            "environment": settings.environment,
            "coordination_schema": settings.db_schema,
            "account_schema": account["schema"],
            "schema_revision": settings.provenance().schema_revision,
            "prompt_catalog": "synced",
        }
    finally:
        close = getattr(repository, "close", None)
        if callable(close):
            close()


def check() -> dict[str, Any]:
    settings = AutomationEcsSettings.from_env("bootstrap")
    store = create_automation_ecs_store(settings)
    store.check_schema()
    account = check_account_runtime_schema()
    return {
        "ok": True,
        "mode": "check",
        "environment": settings.environment,
        "coordination_schema": settings.db_schema,
        "account_schema": account["schema"],
        "schema_revision": settings.provenance().schema_revision,
    }


def run(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Manage ECS Automation runtime schemas")
    parser.add_argument("command", choices=("bootstrap", "check"))
    command = parser.parse_args(argv).command
    return bootstrap() if command == "bootstrap" else check()


def main(argv: list[str] | None = None) -> int:
    try:
        payload = run(argv)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
