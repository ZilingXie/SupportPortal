from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from backend.repositories.ticket_repository import create_ticket_repository
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
    fail = subparsers.add_parser("fail", help="Mark a candidate Release as failed.")
    fail.add_argument("--release-id", required=True)
    fail.add_argument("--reason", required=True)
    return parser


def _execute(args: argparse.Namespace, *, repository: Any | None = None) -> dict[str, Any]:
    owned_repository = repository is None
    repo = repository or create_ticket_repository()
    try:
        repo.initialize()
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


def run(argv: list[str] | None = None, *, repository: Any | None = None) -> dict[str, Any]:
    return _execute(build_parser().parse_args(argv), repository=repository)


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
