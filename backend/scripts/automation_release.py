"""Create or validate an ECS Automation Release Manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from backend.services.automation_release_manifest import (
    AutomationReleaseManifest,
    ReleaseComponent,
    contract_versions,
    digest_from_oci_layout,
    read_manifest,
    read_preproduction_publish_record,
    validate_preproduction_publish_record,
    write_manifest,
)
from backend.services.automation_ecs_contracts import REGISTRY_RELEASE_MANIFEST_VERSION


def _create(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output).resolve()
    components: dict[str, ReleaseComponent] = {}
    for role, layout_value in args.component:
        layout = Path(layout_value).resolve()
        components[role] = ReleaseComponent(
            role=role,
            tag=f"{role}-{args.release_id}",
            digest=digest_from_oci_layout(layout),
            oci_layout=str(layout.relative_to(output.parent)),
        )
    manifest = AutomationReleaseManifest(
        release_id=args.release_id,
        git_commit=args.git_commit,
        build_time=args.build_time,
        prompt_release_id=args.prompt_release_id,
        contracts=contract_versions(),
        components=components,
    )
    write_manifest(manifest, output)
    return {"ok": True, "mode": "create", "manifest": str(output)}


def _create_registry(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output).resolve()
    components = {
        role: ReleaseComponent(
            role=role,
            tag=f"{role}-{args.release_id}",
            digest=digest,
        )
        for role, digest in args.component
    }
    manifest = AutomationReleaseManifest(
        schema_version=REGISTRY_RELEASE_MANIFEST_VERSION,
        artifact_kind="registry",
        release_id=args.release_id,
        git_commit=args.git_commit,
        build_time=args.build_time,
        prompt_release_id=args.prompt_release_id,
        contracts=contract_versions(),
        components=components,
    )
    write_manifest(manifest, output)
    return {"ok": True, "mode": "create-registry", "manifest": str(output)}


def _validate(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest).resolve()
    manifest = read_manifest(manifest_path)
    if manifest.artifact_kind == "oci-layout":
        for component in manifest.components.values():
            if component.oci_layout is None:
                raise ValueError(f"{component.role} OCI layout is missing")
            observed = digest_from_oci_layout(manifest_path.parent / component.oci_layout)
            if observed != component.digest:
                raise ValueError(f"{component.role} OCI digest does not match the Release Manifest")
    return {
        "ok": True,
        "mode": "validate",
        "release_id": manifest.release_id,
        "digests": {role: item.digest for role, item in manifest.components.items()},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage ECS Automation Release Manifests")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--release-id", required=True)
    create.add_argument("--git-commit", required=True)
    create.add_argument("--build-time", required=True)
    create.add_argument("--prompt-release-id", required=True)
    create.add_argument("--output", required=True)
    create.add_argument(
        "--component",
        action="append",
        nargs=2,
        metavar=("ROLE", "OCI_LAYOUT"),
        required=True,
        choices=None,
    )
    create_registry = commands.add_parser("create-registry")
    create_registry.add_argument("--release-id", required=True)
    create_registry.add_argument("--git-commit", required=True)
    create_registry.add_argument("--build-time", required=True)
    create_registry.add_argument("--prompt-release-id", required=True)
    create_registry.add_argument("--output", required=True)
    create_registry.add_argument(
        "--component",
        action="append",
        nargs=2,
        metavar=("ROLE", "DIGEST"),
        required=True,
    )
    validate = commands.add_parser("validate")
    validate.add_argument("--manifest", required=True)
    validate_publish = commands.add_parser("validate-preproduction-publish")
    validate_publish.add_argument("--manifest", required=True)
    validate_publish.add_argument("--publish-record", required=True)
    return parser


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    if args.command == "create":
        return _create(args)
    if args.command == "create-registry":
        return _create_registry(args)
    if args.command == "validate-preproduction-publish":
        manifest = read_manifest(Path(args.manifest))
        record = read_preproduction_publish_record(Path(args.publish_record))
        validate_preproduction_publish_record(manifest, record)
        return {
            "ok": True,
            "mode": "validate-preproduction-publish",
            "release_id": manifest.release_id,
            "registry_id": record.registry_id,
            "region": record.region,
            "repository": record.target_repository,
        }
    return _validate(args)


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
