from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from backend.services.automation_release_manifest import read_manifest
from backend.services.account_internal_email_recipients import (
    resolve_account_internal_email_recipients,
)


PROVENANCE_ENV_NAMES = {
    "AUTOMATION_RELEASE_ID",
    "AUTOMATION_IMAGE_DIGEST",
    "APP_BUILD_REF",
    "APP_BUILD_TIME",
    "PROMPT_RELEASE_ID",
}
HERMES_CASE_WORKFLOW_MODES = {"disabled", "mock", "real"}
REGISTER_TASK_DEFINITION_FIELDS = {
    "family",
    "taskRoleArn",
    "executionRoleArn",
    "networkMode",
    "containerDefinitions",
    "volumes",
    "placementConstraints",
    "requiresCompatibilities",
    "cpu",
    "memory",
    "runtimePlatform",
    "ephemeralStorage",
    "proxyConfiguration",
    "inferenceAccelerators",
    "pidMode",
    "ipcMode",
}


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def validate_promotion(manifest_path: str | Path, record_path: str | Path) -> dict[str, Any]:
    manifest = read_manifest(Path(manifest_path))
    record = _read_json(record_path)
    if record.get("schema_version") != "automation-promotion-v1":
        raise ValueError("invalid Promotion Record schema")
    if record.get("release_id") != manifest.release_id:
        raise ValueError("Promotion Record release_id does not match manifest")
    if record.get("target_repository") != "supportportal/production":
        raise ValueError("Promotion Record target repository must be supportportal/production")
    if record.get("source_repository") not in {
        "local-oci",
        "supportportal/preproduction",
    }:
        raise ValueError("Promotion Record source repository is not approved")
    if set(record.get("components") or {}) != {"api", "route", "worker"}:
        raise ValueError("Promotion Record must contain api, route, and worker")
    for role, component in manifest.components.items():
        promoted = record["components"][role]
        if promoted.get("tag") != component.tag:
            raise ValueError(f"Promotion Record {role} tag mismatch")
        if promoted.get("source_digest") != component.digest:
            raise ValueError(f"Promotion Record {role} source digest mismatch")
        if promoted.get("target_digest") != component.digest:
            raise ValueError(f"Promotion Record {role} target digest mismatch")
    registry_id = str(record.get("registry_id") or "").strip()
    region = str(record.get("region") or "").strip()
    if len(registry_id) != 12 or not registry_id.isdigit() or not region:
        raise ValueError("Promotion Record registry_id/region is invalid")
    return {
        "release_id": manifest.release_id,
        "registry_id": registry_id,
        "region": region,
        "repository": record["target_repository"],
    }


def _container(task_definition: dict[str, Any], role: str) -> dict[str, Any]:
    matches = [
        item
        for item in task_definition.get("containerDefinitions") or []
        if str(item.get("name") or "") == role
    ]
    if len(matches) != 1:
        raise ValueError(f"task definition must contain exactly one {role} container")
    return matches[0]


def _environment_map(container: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("name") or ""): str(item.get("value") or "")
        for item in container.get("environment") or []
    }


def _secret_names(container: dict[str, Any]) -> set[str]:
    return {
        str(item.get("name") or "")
        for item in container.get("secrets") or []
    }


def _set_environment_value(container: dict[str, Any], name: str, value: str) -> None:
    environment = container.setdefault("environment", [])
    matches = [item for item in environment if str(item.get("name") or "") == name]
    if len(matches) > 1:
        raise ValueError(f"task definition contains duplicate environment: {name}")
    if matches:
        matches[0]["value"] = value
        return
    environment.append({"name": name, "value": value})


def _set_secret_reference(container: dict[str, Any], name: str, value_from: str) -> None:
    secrets = container.setdefault("secrets", [])
    matches = [item for item in secrets if str(item.get("name") or "") == name]
    if len(matches) > 1:
        raise ValueError(f"task definition contains duplicate secret: {name}")
    if matches:
        matches[0]["valueFrom"] = value_from
        return
    secrets.append({"name": name, "valueFrom": value_from})


def validate_worker_contract(task_definition: dict[str, Any]) -> None:
    container = _container(task_definition, "worker")
    environment = _environment_map(container)
    forbidden_names = {"PILOT_BIN", "XDG_CONFIG_HOME", "PILOT_HOME"}
    if forbidden_names & set(environment):
        raise ValueError("Worker task definition contains forbidden Pilot environment")
    if any("pilot" in str(value).lower() for value in environment.values()):
        raise ValueError("Worker task definition contains a Pilot environment value")
    if any("pilot" in str(item.get("sourceVolume") or "").lower() for item in container.get("mountPoints") or []):
        raise ValueError("Worker task definition contains a pilot-creds mount")
    if any("pilot" in str(item.get("name") or "").lower() for item in task_definition.get("volumes") or []):
        raise ValueError("Worker task definition contains a pilot-creds volume")
    if "ACCOUNT_SUSPENSION_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON" not in _secret_names(container):
        raise ValueError("Worker task definition is missing the Suspension recipients secret")


def render_task_definition(
    *,
    role: str,
    current_path: str | Path,
    manifest_path: str | Path,
    registry_id: str,
    region: str,
    hermes_case_workflow_mode: str | None = None,
) -> dict[str, Any]:
    if role not in {"api", "route", "worker"}:
        raise ValueError("role must be api, route, or worker")
    source = _read_json(current_path)
    task_definition = source.get("taskDefinition") if "taskDefinition" in source else source
    if not isinstance(task_definition, dict):
        raise ValueError("taskDefinition object is required")
    if role == "worker":
        validate_worker_contract(task_definition)
    if (
        hermes_case_workflow_mode is not None
        and hermes_case_workflow_mode not in HERMES_CASE_WORKFLOW_MODES
    ):
        raise ValueError("Hermes Case Workflow mode must be disabled, mock, or real")
    manifest = read_manifest(Path(manifest_path))
    component = manifest.components[role]
    rendered = {
        key: json.loads(json.dumps(value))
        for key, value in task_definition.items()
        if key in REGISTER_TASK_DEFINITION_FIELDS
    }
    container = _container(rendered, role)
    container["image"] = (
        f"{registry_id}.dkr.ecr.{region}.amazonaws.com/"
        f"supportportal/production@{component.digest}"
    )
    replacements = {
        "AUTOMATION_RELEASE_ID": manifest.release_id,
        "AUTOMATION_IMAGE_DIGEST": component.digest,
        "APP_BUILD_REF": manifest.git_commit,
        "APP_BUILD_TIME": manifest.build_time.isoformat().replace("+00:00", "Z"),
        "PROMPT_RELEASE_ID": manifest.prompt_release_id,
    }
    environment = container.get("environment") or []
    existing_names = {str(item.get("name") or "") for item in environment}
    if not PROVENANCE_ENV_NAMES <= existing_names:
        missing = sorted(PROVENANCE_ENV_NAMES - existing_names)
        raise ValueError(f"task definition is missing provenance fields: {missing}")
    for item in environment:
        name = str(item.get("name") or "")
        if name in replacements:
            item["value"] = replacements[name]
    if hermes_case_workflow_mode is not None and role in {"api", "worker"}:
        _set_environment_value(
            container,
            "HERMES_CASE_WORKFLOW_MODE",
            hermes_case_workflow_mode,
        )
    return rendered


def render_schema_bootstrap_task_definition(
    *,
    current_path: str | Path,
    manifest_path: str | Path,
    registry_id: str,
    region: str,
    migration_secret_reference: str,
) -> dict[str, Any]:
    if ":parameter/" not in migration_secret_reference:
        raise ValueError("schema bootstrap migration secret must be an SSM parameter ARN")
    rendered = render_task_definition(
        role="api",
        current_path=current_path,
        manifest_path=manifest_path,
        registry_id=registry_id,
        region=region,
    )
    rendered["family"] = "supportportal-production-schema-bootstrap"
    container = _container(rendered, "api")
    container["command"] = [
        "python",
        "-m",
        "backend.scripts.automation_ecs_bootstrap",
        "bootstrap",
    ]
    container.pop("healthCheck", None)
    container["portMappings"] = []
    for name in ("AUTOMATION_DB_MIGRATION_DSN", "TICKET_DB_MIGRATION_DSN"):
        _set_secret_reference(container, name, migration_secret_reference)
    return rendered


def verify_heartbeats(
    *,
    manifest_path: str | Path,
    task_definition_path: str | Path,
    max_age_seconds: float,
) -> dict[str, Any]:
    dsn = str(os.getenv("AUTOMATION_HEARTBEAT_DSN") or "").strip()
    if not dsn:
        raise ValueError("AUTOMATION_HEARTBEAT_DSN is required")
    source = _read_json(task_definition_path)
    task_definition = source.get("taskDefinition") if "taskDefinition" in source else source
    worker = _container(task_definition, "worker")
    environment = _environment_map(worker)
    schema = environment.get("AUTOMATION_DB_SCHEMA", "")
    namespace = environment.get("AUTOMATION_JOB_NAMESPACE", "")
    if not schema or not namespace:
        raise ValueError("Worker task definition is missing DB schema or job namespace")
    manifest = read_manifest(Path(manifest_path))
    expected_common = {
        "environment": "production",
        "release_id": manifest.release_id,
        "git_commit": manifest.git_commit,
        "build_time": manifest.build_time.isoformat().replace("+00:00", "Z"),
        "prompt_release_id": manifest.prompt_release_id,
        "db_schema": schema,
        "job_namespace": namespace,
    }
    newest: dict[str, dict[str, Any]] = {}
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT role,provenance,last_seen_at,clock_timestamp() FROM {} "
                "WHERE namespace=%s AND role IN ('route','worker')"
            ).format(sql.Identifier(schema, "automation_worker_heartbeats")),
            (namespace,),
        )
        for role, provenance, last_seen_at, observed_at in cursor.fetchall():
            normalized_role = str(role)
            current = newest.get(normalized_role)
            if current is None or last_seen_at > current["last_seen_at"]:
                newest[normalized_role] = {
                    "provenance": provenance,
                    "last_seen_at": last_seen_at,
                    "observed_at": observed_at,
                }
    for role in ("route", "worker"):
        row = newest.get(role)
        if row is None:
            raise ValueError(f"latest {role} heartbeat is missing")
        age = (row["observed_at"] - row["last_seen_at"]).total_seconds()
        if age < 0 or age > max_age_seconds:
            raise ValueError(f"latest {role} heartbeat is stale")
        expected = {
            **expected_common,
            "service_role": role,
            "image_digest": manifest.components[role].digest,
        }
        provenance = row["provenance"] if isinstance(row["provenance"], dict) else {}
        mismatches = sorted(
            key for key, value in expected.items() if provenance.get(key) != value
        )
        if mismatches:
            raise ValueError(f"latest {role} heartbeat provenance mismatch: {mismatches}")
    return {"status": "ok", "roles": ["route", "worker"]}


def validate_suspension_recipients() -> dict[str, Any]:
    recipients = resolve_account_internal_email_recipients(
        "account_suspension",
        require_json=True,
    )
    return {
        "status": "ok",
        "to_count": len(recipients.to),
        "cc_count": len(recipients.cc),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    promotion = subparsers.add_parser("validate-promotion")
    promotion.add_argument("--manifest", required=True)
    promotion.add_argument("--promotion-record", required=True)
    render = subparsers.add_parser("render-task-definition")
    render.add_argument("--role", choices=("api", "route", "worker"), required=True)
    render.add_argument("--current", required=True)
    render.add_argument("--manifest", required=True)
    render.add_argument("--registry-id", required=True)
    render.add_argument("--region", required=True)
    render.add_argument(
        "--hermes-case-workflow-mode",
        choices=sorted(HERMES_CASE_WORKFLOW_MODES),
    )
    render.add_argument("--output", required=True)
    bootstrap = subparsers.add_parser("render-schema-bootstrap-task-definition")
    bootstrap.add_argument("--current", required=True)
    bootstrap.add_argument("--manifest", required=True)
    bootstrap.add_argument("--registry-id", required=True)
    bootstrap.add_argument("--region", required=True)
    bootstrap.add_argument("--migration-secret-reference", required=True)
    bootstrap.add_argument("--output", required=True)
    heartbeat = subparsers.add_parser("verify-heartbeats")
    heartbeat.add_argument("--manifest", required=True)
    heartbeat.add_argument("--task-definition", required=True)
    heartbeat.add_argument("--max-age-seconds", type=float, default=90.0)
    subparsers.add_parser("validate-suspension-recipients")
    return parser


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    if args.command == "validate-promotion":
        return validate_promotion(args.manifest, args.promotion_record)
    if args.command == "render-task-definition":
        result = render_task_definition(
            role=args.role,
            current_path=args.current,
            manifest_path=args.manifest,
            registry_id=args.registry_id,
            region=args.region,
            hermes_case_workflow_mode=args.hermes_case_workflow_mode,
        )
        Path(args.output).write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {"status": "ok", "output": args.output}
    if args.command == "render-schema-bootstrap-task-definition":
        result = render_schema_bootstrap_task_definition(
            current_path=args.current,
            manifest_path=args.manifest,
            registry_id=args.registry_id,
            region=args.region,
            migration_secret_reference=args.migration_secret_reference,
        )
        Path(args.output).write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {"status": "ok", "output": args.output}
    if args.command == "verify-heartbeats":
        return verify_heartbeats(
            manifest_path=args.manifest,
            task_definition_path=args.task_definition,
            max_age_seconds=args.max_age_seconds,
        )
    if args.command == "validate-suspension-recipients":
        return validate_suspension_recipients()
    raise ValueError(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    try:
        print(json.dumps(run(argv), sort_keys=True))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
