from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from backend.repositories.ticket_repository import (
    PostgresTicketRepository,
    create_ticket_repository,
)
from backend.services.prompt_versioning import PromptVersionService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage deployment-bound Prompt Releases.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    current = subparsers.add_parser("current", help="Return the active Prompt Release.")
    current.add_argument("--output", choices=("json", "shell"), default="json")
    subparsers.add_parser("pending", help="Return Prompt versions scheduled for the next deployment.")
    prepare = subparsers.add_parser("prepare", help="Freeze scheduled Prompt versions into a candidate Release.")
    prepare.add_argument("--build-ref", required=True)
    prepare.add_argument("--output", choices=("json", "shell"), default="json")
    validate = subparsers.add_parser("validate", help="Validate a deployable Prompt Release against the code catalog.")
    validate.add_argument("--release-id", required=True)
    activate = subparsers.add_parser("activate", help="Activate a healthy candidate Release.")
    activate.add_argument("--release-id", required=True)
    sync = subparsers.add_parser(
        "sync",
        help="Replicate a deployable Prompt Release into another deployment database (same release id).",
    )
    sync.add_argument("--release-id", required=True)
    sync.add_argument("--target-dsn", required=True)
    fail = subparsers.add_parser("fail", help="Mark a candidate Release as failed.")
    fail.add_argument("--release-id", required=True)
    fail.add_argument("--reason", required=True)
    return parser


def _create_sync_target_repository(target_dsn: str) -> Any:
    normalized = str(target_dsn or "").strip()
    if not normalized:
        raise ValueError("--target-dsn is required")
    schema = (os.getenv("TICKET_DB_SCHEMA") or "supportportal").strip() or "supportportal"
    return PostgresTicketRepository(
        dsn=normalized,
        schema=schema,
        migration_dsn=normalized,
        application_name="supportportal-prompt-release-sync",
    )


def _collect_release_versions(repository: Any, release: dict[str, Any]) -> list[dict[str, Any]]:
    items = {str(key): int(version) for key, version in dict(release.get("items") or {}).items()}
    rows: list[dict[str, Any]] = []
    for prompt in repository.list_managed_prompts():
        key = str(prompt.get("prompt_key") or "").strip()
        selected_version = items.get(key)
        if selected_version is None:
            continue
        selected = next(
            (item for item in list(prompt.get("versions") or []) if int(item.get("version") or 0) == selected_version),
            None,
        )
        if selected is None:
            raise ValueError(f"source release is missing {key} v{selected_version}")
        rows.append(selected)
    if len(rows) != len(items):
        missing = sorted(set(items) - {str(row.get("prompt_key")) for row in rows})
        raise ValueError(f"source release references unknown prompt keys: {missing}")
    return rows


def _sync_release(
    repository: Any,
    *,
    release_id: str,
    target_repository: Any,
) -> dict[str, Any]:
    release = repository.get_prompt_release(release_id)
    if release is None:
        raise ValueError(f"prompt release not found: {release_id}")
    if release.get("status") not in {"candidate", "active"}:
        raise ValueError(f"prompt release is not deployable: {release_id}")

    target_repository.initialize()
    PromptVersionService(target_repository).sync_catalog()

    existing = target_repository.get_prompt_release(release_id)
    if existing is None:
        result = target_repository.sync_prompt_release(
            release,
            _collect_release_versions(repository, release),
        )
    else:
        result = {
            "release_id": release_id,
            "status": str(existing.get("status") or ""),
            "created": False,
            "versions_created": 0,
            "versions_matched": 0,
        }

    if release.get("status") == "active" and result.get("status") != "active":
        PromptVersionService(target_repository).activate_release(release_id)
        result["status"] = "active"

    validation = PromptVersionService(target_repository).validate_release(release_id)
    return {"sync": result, "validation": validation}


def _execute(args: argparse.Namespace, *, repository: Any | None = None, target_repository: Any | None = None) -> dict[str, Any]:
    owned_repository = repository is None
    repo = repository or create_ticket_repository()
    try:
        repo.initialize()
        if args.command == "sync":
            owned_target = target_repository is None
            target = target_repository or _create_sync_target_repository(args.target_dsn)
            try:
                return _sync_release(repo, release_id=args.release_id, target_repository=target)
            finally:
                if owned_target:
                    close = getattr(target, "close", None)
                    if callable(close):
                        close()
        service = PromptVersionService(repo)
        service.sync_catalog()
        if args.command == "current":
            return {"release": service.active_release()}
        if args.command == "pending":
            prompts = service.list_prompts()
            return {
                "scheduled": [
                    {
                        "prompt_key": item["prompt_key"],
                        "version": item["scheduled_version"]["version"],
                    }
                    for item in prompts
                    if item.get("scheduled_version")
                ]
            }
        if args.command == "prepare":
            return {"release": service.prepare_release(build_ref=args.build_ref)}
        if args.command == "validate":
            return {"validation": service.validate_release(args.release_id)}
        if args.command == "activate":
            return {"release": service.activate_release(args.release_id)}
        if args.command == "fail":
            return {"release": service.fail_release(args.release_id, failure_reason=args.reason)}
        raise ValueError(f"unsupported command: {args.command}")
    finally:
        if owned_repository:
            close = getattr(repo, "close", None)
            if callable(close):
                close()


def run(argv: list[str] | None = None, *, repository: Any | None = None, target_repository: Any | None = None) -> dict[str, Any]:
    return _execute(build_parser().parse_args(argv), repository=repository, target_repository=target_repository)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = _execute(args)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1
    if args.command == "current" and args.output == "shell":
        release = payload["release"]
        print(str((release or {}).get("release_id") or ""))
    elif args.command == "prepare" and args.output == "shell":
        release = payload["release"]
        print(f"{release['release_id']}\t{'true' if release.get('created') else 'false'}")
    else:
        print(json.dumps({"ok": True, **payload}, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
