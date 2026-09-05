from __future__ import annotations

import argparse
import hashlib
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
    sync.add_argument("--target-dsn")
    sync.add_argument("--target-schema")
    sync.add_argument(
        "--defer-activation",
        action="store_true",
        help="Keep the target release as candidate until an explicit activate command.",
    )
    fail = subparsers.add_parser("fail", help="Mark a candidate Release as failed.")
    fail.add_argument("--release-id", required=True)
    fail.add_argument("--reason", required=True)
    return parser


def _create_sync_target_repository(
    target_dsn: str | None,
    target_schema: str | None,
) -> Any:
    normalized = str(target_dsn or os.getenv("PROMPT_RELEASE_TARGET_DSN") or "").strip()
    if not normalized:
        raise ValueError("target DSN is required")
    schema = str(
        target_schema
        or os.getenv("PROMPT_RELEASE_TARGET_SCHEMA")
        or os.getenv("TICKET_DB_SCHEMA")
        or "supportportal"
    ).strip() or "supportportal"
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


def _release_content_fingerprint(versions: list[dict[str, Any]]) -> str:
    identity = "\n".join(
        f"{str(row.get('prompt_key') or '').strip()}:{str(row.get('content_sha256') or '').strip()}"
        for row in sorted(versions, key=lambda item: str(item.get("prompt_key") or ""))
    )
    return "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _sync_release(
    repository: Any,
    *,
    release_id: str,
    target_repository: Any,
    defer_activation: bool = False,
) -> dict[str, Any]:
    release = repository.get_prompt_release(release_id)
    if release is None:
        raise ValueError(f"prompt release not found: {release_id}")
    if release.get("status") not in {"candidate", "active"}:
        raise ValueError(f"prompt release is not deployable: {release_id}")
    source_service = PromptVersionService(repository)
    source_validation = source_service.validate_release(release_id)
    source_versions = _collect_release_versions(repository, release)
    source_fingerprint = _release_content_fingerprint(source_versions)

    PromptVersionService(target_repository).sync_catalog()
    replicated_release = dict(release)
    if defer_activation and replicated_release.get("status") == "active":
        replicated_release["status"] = "candidate"
        replicated_release["activated_at"] = None
    result = target_repository.sync_prompt_release(replicated_release, source_versions)

    if (
        release.get("status") == "active"
        and not defer_activation
        and result.get("status") == "candidate"
    ):
        PromptVersionService(target_repository).activate_release(release_id)
        result["status"] = "active"
    elif result.get("status") not in {"candidate", "active"}:
        raise ValueError(f"target prompt release is not deployable: {release_id}")

    validation = PromptVersionService(target_repository).validate_release(release_id)
    target_release = target_repository.get_prompt_release(release_id)
    if target_release is None:
        raise ValueError(f"target prompt release not found after sync: {release_id}")
    target_versions = _collect_release_versions(target_repository, target_release)
    target_fingerprint = _release_content_fingerprint(target_versions)
    if str(target_release.get("build_ref") or "") != str(release.get("build_ref") or ""):
        raise ValueError(f"prompt release build_ref mismatch: {release_id}")
    if target_fingerprint != source_fingerprint:
        raise ValueError(f"prompt release content fingerprint mismatch: {release_id}")
    result["status"] = str(target_release.get("status") or result.get("status") or "")
    return {
        "sync": result,
        "source_validation": source_validation,
        "validation": validation,
        "identity": {
            "build_ref": str(release.get("build_ref") or ""),
            "content_fingerprint": source_fingerprint,
        },
    }


def _execute(args: argparse.Namespace, *, repository: Any | None = None, target_repository: Any | None = None) -> dict[str, Any]:
    owned_repository = repository is None
    repo = repository or create_ticket_repository()
    try:
        if args.command not in {"sync", "validate", "activate"}:
            repo.initialize()
        if args.command == "sync":
            owned_target = target_repository is None
            target = target_repository or _create_sync_target_repository(
                args.target_dsn,
                args.target_schema,
            )
            try:
                return _sync_release(
                    repo,
                    release_id=args.release_id,
                    target_repository=target,
                    defer_activation=bool(args.defer_activation),
                )
            finally:
                if owned_target:
                    close = getattr(target, "close", None)
                    if callable(close):
                        close()
        service = PromptVersionService(repo)
        if args.command == "current":
            service.sync_catalog()
            return {"release": service.active_release()}
        if args.command == "pending":
            service.sync_catalog()
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
            validation = service.validate_release(args.release_id)
            release = repo.get_prompt_release(args.release_id)
            if release is None:
                raise ValueError(f"prompt release not found: {args.release_id}")
            versions = _collect_release_versions(repo, release)
            return {
                "validation": validation,
                "identity": {
                    "build_ref": str(release.get("build_ref") or ""),
                    "content_fingerprint": _release_content_fingerprint(versions),
                },
            }
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
