from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from backend.services.automation_release_manifest import (
    read_manifest,
    read_preproduction_publish_record,
    validate_preproduction_publish_record,
)
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
HERMES_OUTBOUND_SECRET_NAMES = {
    "ENGINEER_INVESTIGATION_REPLY_BASE_URL",
    "ENGINEER_INVESTIGATION_REPLY_API_KEY",
}
HERMES_SECRET_NAMES = HERMES_OUTBOUND_SECRET_NAMES | {"HERMES_CALLBACK_TOKEN"}
HERMES_SECRET_SUFFIXES = {
    "ENGINEER_INVESTIGATION_REPLY_BASE_URL": "hermes-base-url",
    "ENGINEER_INVESTIGATION_REPLY_API_KEY": "hermes-api-server-key",
    "HERMES_CALLBACK_TOKEN": "hermes-callback-token",
}
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
    if record.get("source_repository") == "supportportal/preproduction":
        for field in (
            "source_publish_record_sha256",
            "preproduction_deploy_evidence_sha256",
        ):
            value = str(record.get(field) or "")
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
                raise ValueError(f"Promotion Record {field} is invalid")
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


def validate_preproduction_publish(
    manifest_path: str | Path,
    record_path: str | Path,
) -> dict[str, Any]:
    manifest = read_manifest(Path(manifest_path))
    record = read_preproduction_publish_record(Path(record_path))
    validate_preproduction_publish_record(manifest, record)
    return {
        "release_id": manifest.release_id,
        "registry_id": record.registry_id,
        "region": record.region,
        "repository": record.target_repository,
    }


def _sha256(path: str | Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_preproduction_acceptance(
    manifest_path: str | Path,
    publish_record_path: str | Path,
    evidence_path: str | Path,
) -> dict[str, Any]:
    publish = validate_preproduction_publish(manifest_path, publish_record_path)
    manifest = read_manifest(Path(manifest_path))
    evidence = _read_json(evidence_path)
    if evidence.get("schema_version") != "automation-ecs-deploy-evidence-v1":
        raise ValueError("invalid Preproduction deploy evidence schema")
    if evidence.get("status") != "complete" or evidence.get("environment") != "preproduction":
        raise ValueError("Preproduction deploy evidence is not complete")
    if evidence.get("release_id") != manifest.release_id or evidence.get("git_commit") != manifest.git_commit:
        raise ValueError("Preproduction deploy evidence release identity mismatch")
    prompt = evidence.get("prompt_release") or {}
    if prompt.get("release_id") != manifest.prompt_release_id:
        raise ValueError("Preproduction deploy evidence Prompt Release mismatch")
    registry = evidence.get("registry") or {}
    if (
        str(registry.get("id") or "") != publish["registry_id"]
        or registry.get("repository") != "supportportal/preproduction"
    ):
        raise ValueError("Preproduction deploy evidence registry mismatch")
    components = evidence.get("components") or {}
    if set(components) != {"api", "route", "worker"}:
        raise ValueError("Preproduction deploy evidence must contain all roles")
    for role, component in manifest.components.items():
        observed = components[role]
        if observed.get("expected_digest") != component.digest or observed.get("runtime_verified") is not True:
            raise ValueError(f"Preproduction {role} runtime evidence is incomplete")
    checks = evidence.get("checks") or {}
    required_passed = {
        "terraform_zero_drift",
        "source_prompt",
        "ecr",
        "suspension_recipients",
        "heartbeats",
        "public_health",
        "cloudwatch",
        "ec2_backup",
    }
    failed = sorted(name for name in required_passed if checks.get(name) != "passed")
    if failed:
        raise ValueError(f"Preproduction deploy evidence checks failed: {failed}")
    if checks.get("schema_bootstrap") not in {"passed", "skipped_current"}:
        raise ValueError("Preproduction schema bootstrap evidence is incomplete")
    if checks.get("prompt_sync") != "active" or checks.get("prompt_activation") != "active":
        raise ValueError("Preproduction Prompt activation evidence is incomplete")
    return {
        **publish,
        "publish_record_sha256": _sha256(publish_record_path),
        "deploy_evidence_sha256": _sha256(evidence_path),
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


def _remove_secret_references(container: dict[str, Any], names: set[str]) -> None:
    container["secrets"] = [
        item
        for item in container.get("secrets") or []
        if str(item.get("name") or "") not in names
    ]


def _remove_environment_values(container: dict[str, Any], names: set[str]) -> None:
    container["environment"] = [
        item
        for item in container.get("environment") or []
        if str(item.get("name") or "") not in names
    ]


def _registrable_task_definition(task_definition: dict[str, Any]) -> dict[str, Any]:
    return {
        key: json.loads(json.dumps(value))
        for key, value in task_definition.items()
        if key in REGISTER_TASK_DEFINITION_FIELDS
    }


def _parameter_arn(prefix_arn: str, name: str) -> str:
    return f"{prefix_arn.rstrip('/')}/{name}"


def _parameter_prefix_arn(container: dict[str, Any], *, environment: str) -> str:
    references = [
        str(item.get("valueFrom") or "")
        for item in container.get("secrets") or []
        if str(item.get("name") or "") == "AUTOMATION_DB_DSN"
    ]
    if len(references) != 1:
        raise ValueError("task definition requires one AUTOMATION_DB_DSN secret")
    reference = references[0]
    suffix = f"/supportportal/{environment}/automation-db-dsn"
    if ":parameter/" not in reference or not reference.endswith(suffix):
        raise ValueError("AUTOMATION_DB_DSN must use the environment SSM parameter prefix")
    return reference[: -len("/automation-db-dsn")]


def _base_environment(
    *,
    manifest: Any,
    role: str,
    environment: str,
    hermes_case_workflow_mode: str,
) -> list[dict[str, str]]:
    values = {
        "AUTOMATION_RUNTIME_ALLOW_MEMORY": "0",
        "RUNTIME_SCHEMA_MODE": "check",
        "TICKET_DB_SCHEMA": f"supportportal_{environment}",
        "AUTOMATION_BASE_PATH": f"/automation/{environment}",
        "AUTOMATION_DB_RESOURCE_ID": f"supportportal-{environment}",
        "APP_BUILD_REF": manifest.git_commit,
        "AUTOMATION_ENVIRONMENT": environment,
        "AUTOMATION_JOB_NAMESPACE": f"supportportal-{environment}",
        "AUTOMATION_RELEASE_ID": manifest.release_id,
        "AUTOMATION_DB_SCHEMA": f"supportportal_{environment}",
        "PROMPT_RELEASE_REQUIRED": "true",
        "APP_BUILD_TIME": manifest.build_time.isoformat().replace("+00:00", "Z"),
        "AUTOMATION_IMAGE_DIGEST": manifest.components[role].digest,
        "PROMPT_RELEASE_ID": manifest.prompt_release_id,
    }
    if role in {"api", "worker"}:
        values.update(
            {
                "ENGINEER_SLACK_CHANNEL_ID": "C0BS0N61D1R",
                "ENGINEER_SLACK_TEAM_ID": "T1CBEDLJY",
                "ENGINEER_INVESTIGATION_REPLY_TIMEOUT_SECONDS": "300",
                "HERMES_CASE_WORKFLOW_MODE": hermes_case_workflow_mode,
            }
        )
    if role in {"route", "worker"}:
        values.update(
            {
                "TICKET_DB_APPLICATION_NAME": f"supportportal-automation-ecs-{role}-{environment}",
                "PROMPT_RUNTIME_SERVICE": f"automation-ecs-{role}-{environment}",
            }
        )
    if role == "worker":
        values.update(
            {
                "ACCOUNT_REPLY_POLLER_ENABLED": "false",
                "ACCOUNT_REPLY_LEGACY_POLLER_ENABLED": "true",
                "AUTOMATION_ECS_ACCOUNT_ONLY": "1",
                "AUTOMATION_REPLY_POLL_ENABLED": "true",
                "AUTOMATION_REPLY_POLL_INTERVAL_SECONDS": "300",
                "AUTOMATION_REPLY_POLL_MAX_MESSAGES": "25",
                "AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED": "1",
                "ACCOUNT_DEFAULT_PROCESSING_PROFILE": environment,
                "INTERNAL_EMAIL_SUBJECT_NAMESPACE": f"[{environment}-automation]",
                "CLIENT_RAG_SERVICE_TIMEOUT_SECONDS": "180",
                "BILLING_AUTOMATION_GRAPH_TOKEN_CACHE": "/app/.msgraph/billing-automation-token.json",
                "BILLING_AUTOMATION_REPLY_RECORD_PATH": "/app/.msgraph/billing-request-replies.jsonl",
                "BILLING_AUTOMATION_GRAPH_TENANT_ID": "60275374-3eaa-49c2-83c3-cc189d126981",
                "BILLING_AUTOMATION_GRAPH_CLIENT_ID": "cb5aaefe-2ee2-4ac9-a3ee-5490ddf70d80",
                "BILLING_AUTOMATION_GRAPH_USERNAME": "ai-support-agent@agora.io",
            }
        )
    return [{"name": name, "value": values[name]} for name in sorted(values)]


def render_initial_task_definition(
    *,
    role: str,
    manifest_path: str | Path,
    registry_id: str,
    region: str,
    environment: str,
    repository: str,
    execution_role_arn: str,
    task_role_arn: str,
    log_group_name: str,
    parameter_prefix_arn: str,
    hermes_case_workflow_mode: str = "disabled",
    hermes_persona_enabled: bool = False,
    graph_efs_file_system_id: str | None = None,
    graph_efs_access_point_id: str | None = None,
) -> dict[str, Any]:
    if role not in {"api", "route", "worker"}:
        raise ValueError("role must be api, route, or worker")
    if environment != "preproduction":
        raise ValueError("initial task definitions may only target preproduction")
    if repository != "supportportal/preproduction":
        raise ValueError("initial Preproduction repository must be supportportal/preproduction")
    if hermes_case_workflow_mode not in HERMES_CASE_WORKFLOW_MODES:
        raise ValueError("Hermes Case Workflow mode must be disabled, mock, or real")
    if role == "worker" and not (graph_efs_file_system_id and graph_efs_access_point_id):
        raise ValueError("Worker initial task definition requires Graph EFS inputs")

    manifest = read_manifest(Path(manifest_path))
    component = manifest.components[role]
    commands = {
        "api": [
            "python",
            "-m",
            "uvicorn",
            "backend.automation_ecs_api:create_app",
            "--factory",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ],
        "route": ["python", "-m", "backend.automation_ecs_route_worker"],
        "worker": ["python", "-m", "backend.automation_ecs_worker"],
    }
    secret_names = {
        "api": {
            "AUTOMATION_DB_DSN": "automation-db-dsn",
            "AUTOMATION_INTAKE_SHARED_TOKEN": "automation-intake-shared-token",
            "AUTOMATION_DASHBOARD_SESSION_SECRET": "dashboard-session-secret",
            "TICKET_DB_DSN": "automation-db-dsn",
            "n8n_request_token": "n8n-request-token",
        },
        "route": {
            "AUTOMATION_DB_DSN": "automation-db-dsn",
            "OPENAI_API_KEY": "openai-api-key",
            "TICKET_DB_DSN": "automation-db-dsn",
        },
        "worker": {
            "AUTOMATION_DB_DSN": "automation-db-dsn",
            "TICKET_DB_DSN": "automation-db-dsn",
            "zendesk_basic_auth": "zendesk-basic-auth",
            "OPENAI_API_KEY": "openai-api-key",
            "n8n_request_token": "n8n-request-token",
            "RAGFLOW_BASE_URL": "rag-service-url",
            "RAGFLOW_API_KEY": "rag-service-shared-token",
            "ZENDESK_AI_ASSIGNEE_EMAIL": "zendesk-ai-assignee-email",
            "ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID": "zendesk-fraud-review-assignee-id",
            "ACCOUNT_SLACK_N8N_WEBHOOK_URL": "account-slack-n8n-webhook-url",
            "ACCOUNT_SLACK_N8N_STATUS_URL": "account-slack-n8n-status-url",
            "ENGINEER_SLACK_ACCESS_TOKEN": "engineer-slack-access-token",
            "BILLING_AUTOMATION_GRAPH_CLIENT_SECRET": "billing-graph-client-secret",
            "ENABLEMENT_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON": "enablement-internal-email-recipients",
            "FRAUD_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON": "fraud-internal-email-recipients",
            "ACCOUNT_SUSPENSION_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON": "account-suspension-internal-email-recipients",
            "ARCHER_OAUTH_COOKIE": "archer-oauth-cookie",
        },
    }
    if role in {"api", "worker"} and (
        hermes_persona_enabled or hermes_case_workflow_mode != "disabled"
    ):
        secret_names[role].update(
            {
                "ENGINEER_INVESTIGATION_REPLY_BASE_URL": "hermes-base-url",
                "ENGINEER_INVESTIGATION_REPLY_API_KEY": "hermes-api-server-key",
            }
        )
    if role == "api" and hermes_case_workflow_mode != "disabled":
        secret_names[role]["HERMES_CALLBACK_TOKEN"] = "hermes-callback-token"
    container: dict[str, Any] = {
        "name": role,
        "image": (
            f"{registry_id}.dkr.ecr.{region}.amazonaws.com/"
            f"{repository}@{component.digest}"
        ),
        "cpu": {"api": 512, "route": 256, "worker": 512}[role],
        "essential": True,
        "command": commands[role],
        "environment": _base_environment(
            manifest=manifest,
            role=role,
            environment=environment,
            hermes_case_workflow_mode=hermes_case_workflow_mode,
        ),
        "secrets": [
            {"name": name, "valueFrom": _parameter_arn(parameter_prefix_arn, suffix)}
            for name, suffix in sorted(secret_names[role].items())
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": log_group_name,
                "awslogs-region": region,
                "awslogs-stream-prefix": role,
            },
        },
    }
    if role == "api":
        container["portMappings"] = [
            {
                "name": "api-8000-tcp",
                "containerPort": 8000,
                "hostPort": 8000,
                "protocol": "tcp",
                "appProtocol": "http",
            }
        ]
        container["healthCheck"] = {
            "command": [
                "CMD-SHELL",
                "python -c \"import urllib.request; "
                "urllib.request.urlopen('http://127.0.0.1:8000/automation/preproduction/health/live', timeout=3)\" "
                "|| exit 1",
            ],
            "interval": 30,
            "timeout": 5,
            "retries": 3,
            "startPeriod": 60,
        }
    volumes: list[dict[str, Any]] = []
    if role == "worker":
        container["mountPoints"] = [
            {
                "sourceVolume": "graph-token-cache",
                "containerPath": "/app/.msgraph",
                "readOnly": False,
            }
        ]
        volumes = [
            {
                "name": "graph-token-cache",
                "efsVolumeConfiguration": {
                    "fileSystemId": graph_efs_file_system_id,
                    "rootDirectory": "/",
                    "transitEncryption": "ENABLED",
                    "authorizationConfig": {
                        "accessPointId": graph_efs_access_point_id,
                        "iam": "ENABLED",
                    },
                },
            }
        ]
    rendered = {
        "family": f"supportportal-{environment}-{role}",
        "taskRoleArn": task_role_arn,
        "executionRoleArn": execution_role_arn,
        "networkMode": "awsvpc",
        "containerDefinitions": [container],
        "volumes": volumes,
        "requiresCompatibilities": ["FARGATE"],
        "cpu": {"api": "512", "route": "256", "worker": "512"}[role],
        "memory": {"api": "1024", "route": "512", "worker": "1024"}[role],
        "runtimePlatform": {
            "cpuArchitecture": "X86_64",
            "operatingSystemFamily": "LINUX",
        },
    }
    if role == "worker":
        validate_worker_contract(rendered)
    return rendered


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
    environment: str = "production",
    repository: str = "supportportal/production",
    hermes_case_workflow_mode: str | None = None,
    hermes_persona_enabled: bool | None = None,
) -> dict[str, Any]:
    if role not in {"api", "route", "worker"}:
        raise ValueError("role must be api, route, or worker")
    if environment not in {"preproduction", "production"}:
        raise ValueError("environment must be preproduction or production")
    expected_repository = f"supportportal/{environment}"
    if repository != expected_repository:
        raise ValueError(f"repository must be {expected_repository}")
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
    rendered = _registrable_task_definition(task_definition)
    container = _container(rendered, role)
    environment_values = _environment_map(container)
    if environment_values.get("AUTOMATION_ENVIRONMENT") != environment:
        raise ValueError("task definition environment does not match deployment target")
    if environment not in environment_values.get("AUTOMATION_DB_SCHEMA", "").lower():
        raise ValueError("task definition DB schema does not match deployment target")
    if environment not in environment_values.get("AUTOMATION_JOB_NAMESPACE", "").lower():
        raise ValueError("task definition job namespace does not match deployment target")
    container["image"] = (
        f"{registry_id}.dkr.ecr.{region}.amazonaws.com/"
        f"{repository}@{component.digest}"
    )
    replacements = {
        "AUTOMATION_RELEASE_ID": manifest.release_id,
        "AUTOMATION_IMAGE_DIGEST": component.digest,
        "APP_BUILD_REF": manifest.git_commit,
        "APP_BUILD_TIME": manifest.build_time.isoformat().replace("+00:00", "Z"),
        "PROMPT_RELEASE_ID": manifest.prompt_release_id,
    }
    environment_items = container.get("environment") or []
    existing_names = {str(item.get("name") or "") for item in environment_items}
    if not PROVENANCE_ENV_NAMES <= existing_names:
        missing = sorted(PROVENANCE_ENV_NAMES - existing_names)
        raise ValueError(f"task definition is missing provenance fields: {missing}")
    for item in environment_items:
        name = str(item.get("name") or "")
        if name in replacements:
            item["value"] = replacements[name]
    if role in {"api", "worker"} and (
        hermes_case_workflow_mode is not None or hermes_persona_enabled is not None
    ):
        effective_mode = (
            hermes_case_workflow_mode
            if hermes_case_workflow_mode is not None
            else environment_values.get("HERMES_CASE_WORKFLOW_MODE", "disabled")
        )
        if hermes_case_workflow_mode is not None:
            _set_environment_value(
                container,
                "HERMES_CASE_WORKFLOW_MODE",
                hermes_case_workflow_mode,
            )
        required_names: set[str] = set()
        if hermes_persona_enabled is True or effective_mode != "disabled":
            required_names.update(HERMES_OUTBOUND_SECRET_NAMES)
        if role == "api" and effective_mode != "disabled":
            required_names.add("HERMES_CALLBACK_TOKEN")
        _remove_environment_values(container, HERMES_SECRET_NAMES)
        _remove_secret_references(container, HERMES_SECRET_NAMES)
        if required_names:
            prefix_arn = _parameter_prefix_arn(container, environment=environment)
            for name in sorted(required_names):
                _set_secret_reference(
                    container,
                    name,
                    _parameter_arn(prefix_arn, HERMES_SECRET_SUFFIXES[name]),
                )
    return rendered


def render_production_hermes_disabled_task_definition(
    *,
    role: str,
    current_path: str | Path,
) -> tuple[dict[str, Any], bool]:
    if role not in {"api", "worker"}:
        raise ValueError("Production Hermes disable supports only api or worker")
    source = _read_json(current_path)
    task_definition = source.get("taskDefinition") if "taskDefinition" in source else source
    if not isinstance(task_definition, dict):
        raise ValueError("taskDefinition object is required")
    rendered = _registrable_task_definition(task_definition)
    container = _container(rendered, role)
    environment = _environment_map(container)
    if environment.get("AUTOMATION_ENVIRONMENT") != "production":
        raise ValueError("Hermes disable requires a Production task definition")
    if "production" not in environment.get("AUTOMATION_DB_SCHEMA", "").lower():
        raise ValueError("Production DB schema marker is missing")
    if "production" not in environment.get("AUTOMATION_JOB_NAMESPACE", "").lower():
        raise ValueError("Production job namespace marker is missing")
    image = str(container.get("image") or "")
    if "/supportportal/production@sha256:" not in image:
        raise ValueError("Production task definition must use a digest-pinned Production image")

    _remove_environment_values(container, HERMES_SECRET_NAMES)
    _remove_secret_references(container, HERMES_SECRET_NAMES)
    _set_environment_value(container, "HERMES_CASE_WORKFLOW_MODE", "disabled")
    if role == "worker":
        validate_worker_contract(rendered)
    return rendered, rendered != _registrable_task_definition(task_definition)


def render_schema_bootstrap_task_definition(
    *,
    current_path: str | Path,
    manifest_path: str | Path,
    registry_id: str,
    region: str,
    migration_secret_reference: str,
    environment: str = "production",
    repository: str = "supportportal/production",
) -> dict[str, Any]:
    if ":parameter/" not in migration_secret_reference:
        raise ValueError("schema bootstrap migration secret must be an SSM parameter ARN")
    rendered = render_task_definition(
        role="api",
        current_path=current_path,
        manifest_path=manifest_path,
        registry_id=registry_id,
        region=region,
        environment=environment,
        repository=repository,
    )
    rendered["family"] = f"supportportal-{environment}-schema-bootstrap"
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
    environment: str = "production",
) -> dict[str, Any]:
    dsn = str(os.getenv("AUTOMATION_HEARTBEAT_DSN") or "").strip()
    if not dsn:
        raise ValueError("AUTOMATION_HEARTBEAT_DSN is required")
    source = _read_json(task_definition_path)
    task_definition = source.get("taskDefinition") if "taskDefinition" in source else source
    worker = _container(task_definition, "worker")
    environment_values = _environment_map(worker)
    schema = environment_values.get("AUTOMATION_DB_SCHEMA", "")
    namespace = environment_values.get("AUTOMATION_JOB_NAMESPACE", "")
    if not schema or not namespace:
        raise ValueError("Worker task definition is missing DB schema or job namespace")
    manifest = read_manifest(Path(manifest_path))
    expected_common = {
        "environment": environment,
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
    publish = subparsers.add_parser("validate-preproduction-publish")
    publish.add_argument("--manifest", required=True)
    publish.add_argument("--publish-record", required=True)
    acceptance = subparsers.add_parser("validate-preproduction-acceptance")
    acceptance.add_argument("--manifest", required=True)
    acceptance.add_argument("--publish-record", required=True)
    acceptance.add_argument("--deploy-evidence", required=True)
    render = subparsers.add_parser("render-task-definition")
    render.add_argument("--role", choices=("api", "route", "worker"), required=True)
    render.add_argument("--current", required=True)
    render.add_argument("--manifest", required=True)
    render.add_argument("--registry-id", required=True)
    render.add_argument("--region", required=True)
    render.add_argument("--environment", choices=("preproduction", "production"), default="production")
    render.add_argument("--repository", default="supportportal/production")
    render.add_argument(
        "--hermes-case-workflow-mode",
        choices=sorted(HERMES_CASE_WORKFLOW_MODES),
    )
    render.add_argument(
        "--hermes-persona-enabled",
        action="store_const",
        const=True,
        default=None,
    )
    render.add_argument("--output", required=True)
    disable_hermes = subparsers.add_parser(
        "render-production-hermes-disabled-task-definition"
    )
    disable_hermes.add_argument("--role", choices=("api", "worker"), required=True)
    disable_hermes.add_argument("--current", required=True)
    disable_hermes.add_argument("--output", required=True)
    initial = subparsers.add_parser("render-initial-task-definition")
    initial.add_argument("--role", choices=("api", "route", "worker"), required=True)
    initial.add_argument("--manifest", required=True)
    initial.add_argument("--registry-id", required=True)
    initial.add_argument("--region", required=True)
    initial.add_argument("--environment", choices=("preproduction",), required=True)
    initial.add_argument("--repository", required=True)
    initial.add_argument("--execution-role-arn", required=True)
    initial.add_argument("--task-role-arn", required=True)
    initial.add_argument("--log-group-name", required=True)
    initial.add_argument("--parameter-prefix-arn", required=True)
    initial.add_argument(
        "--hermes-case-workflow-mode",
        choices=sorted(HERMES_CASE_WORKFLOW_MODES),
        default="disabled",
    )
    initial.add_argument("--hermes-persona-enabled", action="store_true")
    initial.add_argument("--graph-efs-file-system-id")
    initial.add_argument("--graph-efs-access-point-id")
    initial.add_argument("--output", required=True)
    bootstrap = subparsers.add_parser("render-schema-bootstrap-task-definition")
    bootstrap.add_argument("--current", required=True)
    bootstrap.add_argument("--manifest", required=True)
    bootstrap.add_argument("--registry-id", required=True)
    bootstrap.add_argument("--region", required=True)
    bootstrap.add_argument("--migration-secret-reference", required=True)
    bootstrap.add_argument("--environment", choices=("preproduction", "production"), default="production")
    bootstrap.add_argument("--repository", default="supportportal/production")
    bootstrap.add_argument("--output", required=True)
    heartbeat = subparsers.add_parser("verify-heartbeats")
    heartbeat.add_argument("--manifest", required=True)
    heartbeat.add_argument("--task-definition", required=True)
    heartbeat.add_argument("--max-age-seconds", type=float, default=90.0)
    heartbeat.add_argument("--environment", choices=("preproduction", "production"), default="production")
    subparsers.add_parser("validate-suspension-recipients")
    return parser


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    if args.command == "validate-promotion":
        return validate_promotion(args.manifest, args.promotion_record)
    if args.command == "validate-preproduction-publish":
        return validate_preproduction_publish(args.manifest, args.publish_record)
    if args.command == "validate-preproduction-acceptance":
        return validate_preproduction_acceptance(
            args.manifest,
            args.publish_record,
            args.deploy_evidence,
        )
    if args.command == "render-task-definition":
        result = render_task_definition(
            role=args.role,
            current_path=args.current,
            manifest_path=args.manifest,
            registry_id=args.registry_id,
            region=args.region,
            environment=args.environment,
            repository=args.repository,
            hermes_case_workflow_mode=args.hermes_case_workflow_mode,
            hermes_persona_enabled=args.hermes_persona_enabled,
        )
        Path(args.output).write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {"status": "ok", "output": args.output}
    if args.command == "render-production-hermes-disabled-task-definition":
        result, changed = render_production_hermes_disabled_task_definition(
            role=args.role,
            current_path=args.current,
        )
        Path(args.output).write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {"status": "ok", "output": args.output, "changed": changed}
    if args.command == "render-initial-task-definition":
        result = render_initial_task_definition(
            role=args.role,
            manifest_path=args.manifest,
            registry_id=args.registry_id,
            region=args.region,
            environment=args.environment,
            repository=args.repository,
            execution_role_arn=args.execution_role_arn,
            task_role_arn=args.task_role_arn,
            log_group_name=args.log_group_name,
            parameter_prefix_arn=args.parameter_prefix_arn,
            hermes_case_workflow_mode=args.hermes_case_workflow_mode,
            hermes_persona_enabled=args.hermes_persona_enabled,
            graph_efs_file_system_id=args.graph_efs_file_system_id,
            graph_efs_access_point_id=args.graph_efs_access_point_id,
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
            environment=args.environment,
            repository=args.repository,
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
            environment=args.environment,
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
