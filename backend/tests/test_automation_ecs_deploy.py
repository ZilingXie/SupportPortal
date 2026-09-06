from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.scripts.automation_ecs_deploy import (
    HERMES_OUTBOUND_SECRET_NAMES,
    render_initial_task_definition,
    render_production_hermes_disabled_task_definition,
    render_schema_bootstrap_task_definition,
    render_task_definition,
    validate_preproduction_acceptance,
    validate_suspension_recipients,
    validate_promotion,
    verify_heartbeats,
)
from backend.services.automation_ecs_contracts import RELEASE_MANIFEST_VERSION, SCHEMA_REVISION
from backend.services.automation_release_manifest import contract_versions


ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = ROOT / "deployment/deploy_automation_ecs_release.sh"
INITIAL_TASK_DEFINITIONS_SCRIPT = (
    ROOT / "deployment/register_automation_ecs_initial_task_definitions.sh"
)
PRODUCTION_HERMES_DISABLE_SCRIPT = ROOT / "deployment/disable_production_hermes.sh"


def _manifest(tmp_path: Path) -> Path:
    path = tmp_path / "release-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": RELEASE_MANIFEST_VERSION,
                "release_id": "release-42",
                "git_commit": "a" * 40,
                "build_time": "2026-09-03T01:02:03Z",
                "prompt_release_id": "prompt-42",
                "schema_revision": SCHEMA_REVISION,
                "platform": "linux/amd64",
                "contracts": contract_versions(),
                "components": {
                    role: {
                        "role": role,
                        "tag": f"{role}-release-42",
                        "digest": f"sha256:{digit * 64}",
                        "platform": "linux/amd64",
                        "oci_layout": f"{role}.oci.tar",
                    }
                    for role, digit in (("api", "1"), ("route", "2"), ("worker", "3"))
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _task_definition(tmp_path: Path, role: str, *, pilot: bool = False) -> Path:
    environment = [
        {"name": "AUTOMATION_ENVIRONMENT", "value": "production"},
        {"name": "AUTOMATION_RELEASE_ID", "value": "old-release"},
        {"name": "AUTOMATION_IMAGE_DIGEST", "value": "sha256:" + "0" * 64},
        {"name": "APP_BUILD_REF", "value": "old-commit"},
        {"name": "APP_BUILD_TIME", "value": "2026-09-01T00:00:00Z"},
        {"name": "PROMPT_RELEASE_ID", "value": "old-prompt"},
        {"name": "AUTOMATION_DB_SCHEMA", "value": "supportportal_production"},
        {"name": "AUTOMATION_JOB_NAMESPACE", "value": "supportportal-production"},
    ]
    if pilot:
        environment.append({"name": "PILOT_BIN", "value": "/app/bin/pilot"})
    secrets = [
        {
            "name": "AUTOMATION_DB_DSN",
            "valueFrom": (
                "arn:aws:ssm:us-east-1:123456789012:"
                "parameter/supportportal/production/automation-db-dsn"
            ),
        }
    ]
    if role == "worker":
        secrets.append(
            {
                "name": "ACCOUNT_SUSPENSION_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON",
                "valueFrom": "arn:aws:ssm:::parameter/suspension",
            }
        )
    if role in {"api", "worker"}:
        secrets.extend(
            [
                {
                    "name": "ENGINEER_INVESTIGATION_REPLY_BASE_URL",
                    "valueFrom": "arn:aws:ssm:::parameter/hermes-url",
                },
                {
                    "name": "ENGINEER_INVESTIGATION_REPLY_API_KEY",
                    "valueFrom": "arn:aws:ssm:::parameter/hermes-key",
                },
            ]
        )
    if role == "api":
        secrets.append(
            {
                "name": "HERMES_CALLBACK_TOKEN",
                "valueFrom": "arn:aws:ssm:::parameter/hermes-callback-token",
            }
        )
    payload = {
        "taskDefinition": {
            "taskDefinitionArn": f"arn:old:{role}:7",
            "revision": 7,
            "status": "ACTIVE",
            "registeredAt": "ignored",
            "family": f"supportportal-production-{role}",
            "taskRoleArn": "arn:task-role",
            "executionRoleArn": "arn:execution-role",
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "cpu": "512",
            "memory": "1024",
            "runtimePlatform": {"cpuArchitecture": "X86_64", "operatingSystemFamily": "LINUX"},
            "containerDefinitions": [
                {
                    "name": role,
                    "image": "old-image@sha256:" + "0" * 64,
                    "command": ["python", "-m", f"backend.automation_ecs_{role}"],
                    "environment": environment,
                    "secrets": secrets,
                    "logConfiguration": {
                        "logDriver": "awslogs",
                        "options": {"awslogs-group": "/supportportal/production"},
                    },
                    "mountPoints": (
                        [{"sourceVolume": "graph-token-cache", "containerPath": "/app/.msgraph"}]
                        if role == "worker"
                        else []
                    ),
                }
            ],
            "volumes": (
                [{"name": "graph-token-cache", "efsVolumeConfiguration": {"fileSystemId": "fs-1"}}]
                if role == "worker"
                else []
            ),
        },
        "tags": [{"key": "Environment", "value": "production"}],
    }
    path = tmp_path / f"{role}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_render_task_definition_only_changes_image_and_provenance(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    current = _task_definition(tmp_path, "worker")

    rendered = render_task_definition(
        role="worker",
        current_path=current,
        manifest_path=manifest,
        registry_id="123456789012",
        region="us-east-1",
    )

    container = rendered["containerDefinitions"][0]
    assert container["image"] == (
        "123456789012.dkr.ecr.us-east-1.amazonaws.com/"
        "supportportal/production@sha256:" + "3" * 64
    )
    environment = {item["name"]: item["value"] for item in container["environment"]}
    assert environment["AUTOMATION_RELEASE_ID"] == "release-42"
    assert environment["PROMPT_RELEASE_ID"] == "prompt-42"
    assert environment["APP_BUILD_REF"] == "a" * 40
    assert rendered["taskRoleArn"] == "arn:task-role"
    assert rendered["executionRoleArn"] == "arn:execution-role"
    assert rendered["volumes"][0]["name"] == "graph-token-cache"
    assert "taskDefinitionArn" not in rendered
    assert "revision" not in rendered


def test_render_task_definition_explicitly_sets_hermes_mode_on_api_and_worker(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    for role in ("api", "worker"):
        rendered = render_task_definition(
            role=role,
            current_path=_task_definition(tmp_path, role),
            manifest_path=manifest,
            registry_id="123456789012",
            region="us-east-1",
            hermes_case_workflow_mode="mock",
        )
        environment = {
            item["name"]: item["value"]
            for item in rendered["containerDefinitions"][0]["environment"]
        }
        assert environment["HERMES_CASE_WORKFLOW_MODE"] == "mock"

    route = render_task_definition(
        role="route",
        current_path=_task_definition(tmp_path, "route"),
        manifest_path=manifest,
        registry_id="123456789012",
        region="us-east-1",
        hermes_case_workflow_mode="mock",
    )
    route_environment = {
        item["name"]: item["value"]
        for item in route["containerDefinitions"][0]["environment"]
    }
    assert "HERMES_CASE_WORKFLOW_MODE" not in route_environment


def test_render_task_definition_disabled_removes_hermes_secret_references(
    tmp_path: Path,
) -> None:
    rendered = render_task_definition(
        role="worker",
        current_path=_task_definition(tmp_path, "worker"),
        manifest_path=_manifest(tmp_path),
        registry_id="123456789012",
        region="us-east-1",
        hermes_case_workflow_mode="disabled",
    )
    container = rendered["containerDefinitions"][0]
    secret_names = {item["name"] for item in container["secrets"]}
    assert "ENGINEER_INVESTIGATION_REPLY_BASE_URL" not in secret_names
    assert "ENGINEER_INVESTIGATION_REPLY_API_KEY" not in secret_names
    assert "HERMES_CALLBACK_TOKEN" not in secret_names
    assert {
        item["name"]: item["value"] for item in container["environment"]
    }["HERMES_CASE_WORKFLOW_MODE"] == "disabled"


@pytest.mark.parametrize("role", ["api", "worker"])
def test_disabled_case_workflow_can_keep_persona_endpoint(
    tmp_path: Path,
    role: str,
) -> None:
    current = _task_definition(tmp_path, role)
    payload = json.loads(current.read_text(encoding="utf-8"))
    container = payload["taskDefinition"]["containerDefinitions"][0]
    environment = {item["name"]: item for item in container["environment"]}
    environment["AUTOMATION_ENVIRONMENT"]["value"] = "preproduction"
    environment["AUTOMATION_DB_SCHEMA"]["value"] = "supportportal_preproduction"
    environment["AUTOMATION_JOB_NAMESPACE"]["value"] = "supportportal-preproduction"
    next(
        item for item in container["secrets"] if item["name"] == "AUTOMATION_DB_DSN"
    )["valueFrom"] = (
        "arn:aws:ssm:us-east-1:123456789012:"
        "parameter/supportportal/preproduction/automation-db-dsn"
    )
    current.write_text(json.dumps(payload), encoding="utf-8")

    rendered = render_task_definition(
        role=role,
        current_path=current,
        manifest_path=_manifest(tmp_path),
        registry_id="123456789012",
        region="us-east-1",
        environment="preproduction",
        repository="supportportal/preproduction",
        hermes_case_workflow_mode="disabled",
        hermes_persona_enabled=True,
    )
    rendered_container = rendered["containerDefinitions"][0]
    secret_names = {item["name"] for item in rendered_container["secrets"]}
    assert HERMES_OUTBOUND_SECRET_NAMES <= secret_names
    assert "HERMES_CALLBACK_TOKEN" not in secret_names
    assert {
        item["name"]: item["value"] for item in rendered_container["environment"]
    }["HERMES_CASE_WORKFLOW_MODE"] == "disabled"


@pytest.mark.parametrize("role", ["api", "worker"])
def test_render_task_definition_can_activate_hermes_from_disabled_preproduction(
    tmp_path: Path,
    role: str,
) -> None:
    current = _task_definition(tmp_path, role)
    payload = json.loads(current.read_text(encoding="utf-8"))
    container = payload["taskDefinition"]["containerDefinitions"][0]
    environment = {item["name"]: item for item in container["environment"]}
    environment["AUTOMATION_ENVIRONMENT"]["value"] = "preproduction"
    environment["AUTOMATION_DB_SCHEMA"]["value"] = "supportportal_preproduction"
    environment["AUTOMATION_JOB_NAMESPACE"]["value"] = "supportportal-preproduction"
    container["secrets"] = [
        item
        for item in container["secrets"]
        if item["name"] not in {
            "ENGINEER_INVESTIGATION_REPLY_BASE_URL",
            "ENGINEER_INVESTIGATION_REPLY_API_KEY",
            "HERMES_CALLBACK_TOKEN",
        }
    ]
    next(
        item for item in container["secrets"] if item["name"] == "AUTOMATION_DB_DSN"
    )["valueFrom"] = (
        "arn:aws:ssm:us-east-1:123456789012:"
        "parameter/supportportal/preproduction/automation-db-dsn"
    )
    current.write_text(json.dumps(payload), encoding="utf-8")

    rendered = render_task_definition(
        role=role,
        current_path=current,
        manifest_path=_manifest(tmp_path),
        registry_id="123456789012",
        region="us-east-1",
        environment="preproduction",
        repository="supportportal/preproduction",
        hermes_case_workflow_mode="real",
    )

    secret_references = {
        item["name"]: item["valueFrom"]
        for item in rendered["containerDefinitions"][0]["secrets"]
    }
    assert secret_references["ENGINEER_INVESTIGATION_REPLY_BASE_URL"].endswith(
        "/supportportal/preproduction/hermes-base-url"
    )
    assert secret_references["ENGINEER_INVESTIGATION_REPLY_API_KEY"].endswith(
        "/supportportal/preproduction/hermes-api-server-key"
    )
    if role == "api":
        assert secret_references["HERMES_CALLBACK_TOKEN"].endswith(
            "/supportportal/preproduction/hermes-callback-token"
        )
    else:
        assert "HERMES_CALLBACK_TOKEN" not in secret_references


def test_render_task_definition_rejects_cross_environment_ssm_prefix(
    tmp_path: Path,
) -> None:
    current = _task_definition(tmp_path, "api")
    payload = json.loads(current.read_text(encoding="utf-8"))
    container = payload["taskDefinition"]["containerDefinitions"][0]
    environment = {item["name"]: item for item in container["environment"]}
    environment["AUTOMATION_ENVIRONMENT"]["value"] = "preproduction"
    environment["AUTOMATION_DB_SCHEMA"]["value"] = "supportportal_preproduction"
    environment["AUTOMATION_JOB_NAMESPACE"]["value"] = "supportportal-preproduction"
    current.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="environment SSM parameter prefix"):
        render_task_definition(
            role="api",
            current_path=current,
            manifest_path=_manifest(tmp_path),
            registry_id="123456789012",
            region="us-east-1",
            environment="preproduction",
            repository="supportportal/preproduction",
            hermes_case_workflow_mode="real",
        )


def test_configuration_only_hermes_disable_preserves_image_and_provenance(
    tmp_path: Path,
) -> None:
    current = _task_definition(tmp_path, "worker")
    payload = json.loads(current.read_text(encoding="utf-8"))
    container = payload["taskDefinition"]["containerDefinitions"][0]
    container["image"] = (
        "123456789012.dkr.ecr.us-east-1.amazonaws.com/"
        "supportportal/production@sha256:" + "0" * 64
    )
    container["environment"].extend(
        [
            {"name": "HERMES_CASE_WORKFLOW_MODE", "value": "mock"},
            {"name": "ENGINEER_INVESTIGATION_REPLY_BASE_URL", "value": "https://example.invalid"},
        ]
    )
    current.write_text(json.dumps(payload), encoding="utf-8")

    rendered, changed = render_production_hermes_disabled_task_definition(
        role="worker",
        current_path=current,
    )

    assert changed is True
    rendered_container = rendered["containerDefinitions"][0]
    assert rendered_container["image"] == container["image"]
    environment = {item["name"]: item["value"] for item in rendered_container["environment"]}
    assert environment["APP_BUILD_REF"] == "old-commit"
    assert environment["AUTOMATION_RELEASE_ID"] == "old-release"
    assert environment["HERMES_CASE_WORKFLOW_MODE"] == "disabled"
    assert "ENGINEER_INVESTIGATION_REPLY_BASE_URL" not in environment
    assert not (
        {
            "ENGINEER_INVESTIGATION_REPLY_BASE_URL",
            "ENGINEER_INVESTIGATION_REPLY_API_KEY",
            "HERMES_CALLBACK_TOKEN",
        }
        & {item["name"] for item in rendered_container["secrets"]}
    )

    rendered_path = tmp_path / "rendered.json"
    rendered_path.write_text(json.dumps(rendered), encoding="utf-8")
    second, second_changed = render_production_hermes_disabled_task_definition(
        role="worker",
        current_path=rendered_path,
    )
    assert second_changed is False
    assert second == rendered


def test_production_hermes_disable_command_is_guarded_and_configuration_only() -> None:
    script = PRODUCTION_HERMES_DISABLE_SCRIPT.read_text(encoding="utf-8")
    assert "PRODUCTION_HERMES_DISABLE_APPROVED=1" in script
    assert "render-production-hermes-disabled-task-definition" in script
    assert "services-stable" in script
    assert "Restoring captured Production task definitions" in script
    assert "supportportal-production-route" not in script
    assert "PROMPT_RELEASE_TARGET_DSN" not in script
    assert "supportportal/production@" not in script


def test_render_initial_preproduction_worker_is_environment_isolated(
    tmp_path: Path,
) -> None:
    rendered = render_initial_task_definition(
        role="worker",
        manifest_path=_manifest(tmp_path),
        registry_id="123456789012",
        region="us-east-1",
        environment="preproduction",
        repository="supportportal/preproduction",
        execution_role_arn="arn:aws:iam::123456789012:role/preproduction-execution",
        task_role_arn="arn:aws:iam::123456789012:role/preproduction-task",
        log_group_name="/ecs/supportportal/preproduction",
        parameter_prefix_arn=(
            "arn:aws:ssm:us-east-1:123456789012:"
            "parameter/supportportal/preproduction"
        ),
        hermes_case_workflow_mode="real",
        graph_efs_file_system_id="fs-preproduction",
        graph_efs_access_point_id="fsap-preproduction",
    )
    serialized = json.dumps(rendered, sort_keys=True)
    assert "supportportal/production" not in serialized
    assert "supportportal_production" not in serialized
    assert "supportportal-production" not in serialized
    container = rendered["containerDefinitions"][0]
    environment = {item["name"]: item["value"] for item in container["environment"]}
    assert environment["AUTOMATION_ENVIRONMENT"] == "preproduction"
    assert environment["ACCOUNT_DEFAULT_PROCESSING_PROFILE"] == "preproduction"
    assert environment["HERMES_CASE_WORKFLOW_MODE"] == "real"
    secret_names = {item["name"] for item in container["secrets"]}
    assert {
        "ENGINEER_INVESTIGATION_REPLY_BASE_URL",
        "ENGINEER_INVESTIGATION_REPLY_API_KEY",
        "ACCOUNT_SUSPENSION_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON",
    } <= secret_names

    api = render_initial_task_definition(
        role="api",
        manifest_path=_manifest(tmp_path),
        registry_id="123456789012",
        region="us-east-1",
        environment="preproduction",
        repository="supportportal/preproduction",
        execution_role_arn="arn:aws:iam::123456789012:role/preproduction-execution",
        task_role_arn="arn:aws:iam::123456789012:role/preproduction-task",
        log_group_name="/ecs/supportportal/preproduction",
        parameter_prefix_arn=(
            "arn:aws:ssm:us-east-1:123456789012:"
            "parameter/supportportal/preproduction"
        ),
        hermes_case_workflow_mode="real",
    )
    assert "HERMES_CALLBACK_TOKEN" in {
        item["name"] for item in api["containerDefinitions"][0]["secrets"]
    }
    health_check = api["containerDefinitions"][0]["healthCheck"]
    health_command = health_check["command"][1]
    assert "python -c" in health_command
    assert "urllib.request.urlopen" in health_command
    assert "curl" not in health_command
    assert health_check["startPeriod"] == 60


def test_initial_disabled_case_workflow_can_enable_persona_endpoint(tmp_path: Path) -> None:
    rendered = render_initial_task_definition(
        role="api",
        manifest_path=_manifest(tmp_path),
        registry_id="123456789012",
        region="us-east-1",
        environment="preproduction",
        repository="supportportal/preproduction",
        execution_role_arn="arn:aws:iam::123456789012:role/preproduction-execution",
        task_role_arn="arn:aws:iam::123456789012:role/preproduction-task",
        log_group_name="/ecs/supportportal/preproduction",
        parameter_prefix_arn=(
            "arn:aws:ssm:us-east-1:123456789012:"
            "parameter/supportportal/preproduction"
        ),
        hermes_case_workflow_mode="disabled",
        hermes_persona_enabled=True,
    )
    container = rendered["containerDefinitions"][0]
    secret_names = {item["name"] for item in container["secrets"]}
    assert HERMES_OUTBOUND_SECRET_NAMES <= secret_names
    assert "HERMES_CALLBACK_TOKEN" not in secret_names
    assert {
        item["name"]: item["value"] for item in container["environment"]
    }["HERMES_CASE_WORKFLOW_MODE"] == "disabled"


def test_render_schema_bootstrap_uses_api_image_and_secret_references(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    current = _task_definition(tmp_path, "api")
    payload = json.loads(current.read_text(encoding="utf-8"))
    payload["taskDefinition"]["containerDefinitions"][0]["healthCheck"] = {
        "command": ["CMD-SHELL", "curl -f http://localhost/health"]
    }
    current.write_text(json.dumps(payload), encoding="utf-8")
    reference = (
        "arn:aws:ssm:us-east-1:123456789012:"
        "parameter/supportportal/production/automation-db-migration-dsn"
    )

    rendered = render_schema_bootstrap_task_definition(
        current_path=current,
        manifest_path=manifest,
        registry_id="123456789012",
        region="us-east-1",
        migration_secret_reference=reference,
    )

    assert rendered["family"] == "supportportal-production-schema-bootstrap"
    container = rendered["containerDefinitions"][0]
    assert container["image"].endswith("@sha256:" + "1" * 64)
    assert container["command"] == [
        "python",
        "-m",
        "backend.scripts.automation_ecs_bootstrap",
        "bootstrap",
    ]
    assert container["portMappings"] == []
    assert "healthCheck" not in container
    secrets = {item["name"]: item["valueFrom"] for item in container["secrets"]}
    assert secrets["AUTOMATION_DB_MIGRATION_DSN"] == reference
    assert secrets["TICKET_DB_MIGRATION_DSN"] == reference

    with pytest.raises(ValueError, match="SSM parameter ARN"):
        render_schema_bootstrap_task_definition(
            current_path=current,
            manifest_path=manifest,
            registry_id="123456789012",
            region="us-east-1",
            migration_secret_reference="postgresql://must-not-enter-task-definition",
        )


def test_render_schema_bootstrap_rejects_duplicate_migration_secret(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    current = _task_definition(tmp_path, "api")
    payload = json.loads(current.read_text(encoding="utf-8"))
    container = payload["taskDefinition"]["containerDefinitions"][0]
    duplicate = {
        "name": "AUTOMATION_DB_MIGRATION_DSN",
        "valueFrom": "arn:aws:ssm:us-east-1:123456789012:parameter/old",
    }
    container["secrets"] = [duplicate, dict(duplicate)]
    current.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate secret"):
        render_schema_bootstrap_task_definition(
            current_path=current,
            manifest_path=manifest,
            registry_id="123456789012",
            region="us-east-1",
            migration_secret_reference=(
                "arn:aws:ssm:us-east-1:123456789012:"
                "parameter/supportportal/production/automation-db-migration-dsn"
            ),
        )


def test_render_task_definition_rejects_duplicate_hermes_mode(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    current = _task_definition(tmp_path, "api")
    payload = json.loads(current.read_text(encoding="utf-8"))
    container = payload["taskDefinition"]["containerDefinitions"][0]
    duplicate = {"name": "HERMES_CASE_WORKFLOW_MODE", "value": "disabled"}
    container["environment"].extend([duplicate, dict(duplicate)])
    current.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate environment"):
        render_task_definition(
            role="api",
            current_path=current,
            manifest_path=manifest,
            registry_id="123456789012",
            region="us-east-1",
            hermes_case_workflow_mode="mock",
        )


def test_render_worker_rejects_pilot_and_missing_suspension_secret(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    with pytest.raises(ValueError, match="forbidden Pilot"):
        render_task_definition(
            role="worker",
            current_path=_task_definition(tmp_path, "worker", pilot=True),
            manifest_path=manifest,
            registry_id="123456789012",
            region="us-east-1",
        )

    current = _task_definition(tmp_path, "worker")
    payload = json.loads(current.read_text(encoding="utf-8"))
    payload["taskDefinition"]["containerDefinitions"][0]["secrets"] = []
    current.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Suspension recipients"):
        render_task_definition(
            role="worker",
            current_path=current,
            manifest_path=manifest,
            registry_id="123456789012",
            region="us-east-1",
        )


def test_promotion_record_must_match_all_manifest_digests(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    record = tmp_path / "promotion-record.json"
    record.write_text(
        json.dumps(
            {
                "schema_version": "automation-promotion-v1",
                "release_id": "release-42",
                "source_repository": "local-oci",
                "target_repository": "supportportal/production",
                "registry_id": "123456789012",
                "region": "us-east-1",
                "components": {
                    role: {
                        "tag": f"{role}-release-42",
                        "source_digest": f"sha256:{digit * 64}",
                        "target_digest": f"sha256:{digit * 64}",
                    }
                    for role, digit in (("api", "1"), ("route", "2"), ("worker", "3"))
                },
            }
        ),
        encoding="utf-8",
    )
    assert validate_promotion(manifest, record)["registry_id"] == "123456789012"

    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["components"]["worker"]["target_digest"] = "sha256:" + "9" * 64
    record.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="worker target digest mismatch"):
        validate_promotion(manifest, record)

    payload["components"]["worker"]["target_digest"] = "sha256:" + "3" * 64
    payload["source_repository"] = "unverified-source"
    record.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="source repository is not approved"):
        validate_promotion(manifest, record)


def test_preproduction_acceptance_binds_publish_and_runtime_evidence(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_payload["schema_version"] = "automation-release-v2"
    manifest_payload["artifact_kind"] = "registry"
    for component in manifest_payload["components"].values():
        component.pop("oci_layout")
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
    publish = tmp_path / "publish-record.json"
    publish.write_text(
        json.dumps(
            {
                "schema_version": "automation-preproduction-publish-v1",
                "release_id": "release-42",
                "published_at": "2026-09-06T00:00:00Z",
                "source_git_commit": "a" * 40,
                "codebuild_build_arn": "arn:aws:codebuild:us-east-1:123456789012:build/example:1",
                "codebuild_build_number": 1,
                "registry_id": "123456789012",
                "region": "us-east-1",
                "target_repository": "supportportal/preproduction",
                "evidence_object_version": "version-1",
                "components": {
                    role: {"tag": component["tag"], "digest": component["digest"]}
                    for role, component in manifest_payload["components"].items()
                },
            }
        ),
        encoding="utf-8",
    )
    evidence = tmp_path / "preproduction-evidence.json"
    evidence_payload = {
        "schema_version": "automation-ecs-deploy-evidence-v1",
        "status": "complete",
        "environment": "preproduction",
        "release_id": "release-42",
        "git_commit": "a" * 40,
        "prompt_release": {"release_id": "prompt-42"},
        "registry": {"id": "123456789012", "repository": "supportportal/preproduction"},
        "components": {
            role: {"expected_digest": component["digest"], "runtime_verified": True}
            for role, component in manifest_payload["components"].items()
        },
        "checks": {
            "terraform_zero_drift": "passed",
            "source_prompt": "passed",
            "ecr": "passed",
            "suspension_recipients": "passed",
            "schema_bootstrap": "passed",
            "prompt_sync": "active",
            "heartbeats": "passed",
            "public_health": "passed",
            "cloudwatch": "passed",
            "ec2_backup": "passed",
            "prompt_activation": "active",
        },
    }
    evidence.write_text(json.dumps(evidence_payload), encoding="utf-8")

    result = validate_preproduction_acceptance(manifest, publish, evidence)
    assert result["repository"] == "supportportal/preproduction"
    assert result["publish_record_sha256"].startswith("sha256:")
    assert result["deploy_evidence_sha256"].startswith("sha256:")

    evidence_payload["components"]["worker"]["runtime_verified"] = False
    evidence.write_text(json.dumps(evidence_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="worker runtime evidence"):
        validate_preproduction_acceptance(manifest, publish, evidence)


def test_preproduction_promotion_record_requires_acceptance_hashes(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    record = tmp_path / "promotion-record.json"
    record.write_text(
        json.dumps(
            {
                "schema_version": "automation-promotion-v1",
                "release_id": "release-42",
                "source_repository": "supportportal/preproduction",
                "target_repository": "supportportal/production",
                "registry_id": "123456789012",
                "region": "us-east-1",
                "components": {
                    role: {
                        "tag": f"{role}-release-42",
                        "source_digest": f"sha256:{digit * 64}",
                        "target_digest": f"sha256:{digit * 64}",
                    }
                    for role, digit in (("api", "1"), ("route", "2"), ("worker", "3"))
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source_publish_record_sha256"):
        validate_promotion(manifest, record)
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["source_publish_record_sha256"] = "sha256:" + "4" * 64
    payload["preproduction_deploy_evidence_sha256"] = "sha256:" + "5" * 64
    record.write_text(json.dumps(payload), encoding="utf-8")
    assert validate_promotion(manifest, record)["repository"] == "supportportal/production"


def test_codebuild_direct_promotion_requires_only_publish_record_hash(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    record = tmp_path / "promotion-record.json"
    payload = {
        "schema_version": "automation-promotion-v1",
        "release_id": "release-42",
        "promotion_mode": "codebuild-direct-production",
        "source_repository": "supportportal/preproduction",
        "target_repository": "supportportal/production",
        "source_publish_record_sha256": "sha256:" + "4" * 64,
        "registry_id": "123456789012",
        "region": "us-east-1",
        "components": {
            role: {
                "tag": f"{role}-release-42",
                "source_digest": f"sha256:{digit * 64}",
                "target_digest": f"sha256:{digit * 64}",
            }
            for role, digit in (("api", "1"), ("route", "2"), ("worker", "3"))
        },
    }
    record.write_text(json.dumps(payload), encoding="utf-8")

    assert validate_promotion(manifest, record)["repository"] == "supportportal/production"

    payload["preproduction_deploy_evidence_sha256"] = None
    record.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="not allowed"):
        validate_promotion(manifest, record)

    payload.pop("preproduction_deploy_evidence_sha256")
    payload["promotion_mode"] = "unknown"
    record.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="promotion_mode is not approved"):
        validate_promotion(manifest, record)


def test_suspension_recipient_readback_validates_without_returning_addresses() -> None:
    env_name = "ACCOUNT_SUSPENSION_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON"
    with patch.dict(
        os.environ,
        {
            env_name: json.dumps(
                {"to": ["reviewer@example.com"], "cc": ["owner@example.com"]}
            )
        },
        clear=False,
    ):
        result = validate_suspension_recipients()

    assert result == {"status": "ok", "to_count": 1, "cc_count": 1}
    assert "example.com" not in json.dumps(result)

    with patch.dict(os.environ, {env_name: "not-json"}, clear=False):
        with pytest.raises(ValueError, match="value is not valid JSON"):
            validate_suspension_recipients()


def _heartbeat_provenance(role: str) -> dict[str, str]:
    digest = "2" if role == "route" else "3"
    return {
        "environment": "production",
        "service_role": role,
        "release_id": "release-42",
        "git_commit": "a" * 40,
        "image_digest": f"sha256:{digest * 64}",
        "build_time": "2026-09-03T01:02:03Z",
        "prompt_release_id": "prompt-42",
        "db_schema": "supportportal_production",
        "job_namespace": "supportportal-production",
    }


def _heartbeat_connection(rows: list[tuple[object, ...]]) -> MagicMock:
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchall.return_value = rows
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    return connection


def test_verify_heartbeats_uses_database_observation_time(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    task_definition = _task_definition(tmp_path, "worker")
    last_seen_at = datetime.now(timezone.utc) + timedelta(days=1)
    observed_at = last_seen_at + timedelta(seconds=1)
    rows = [
        (role, _heartbeat_provenance(role), last_seen_at, observed_at)
        for role in ("route", "worker")
    ]

    with (
        patch.dict(os.environ, {"AUTOMATION_HEARTBEAT_DSN": "postgresql://unused"}),
        patch(
            "backend.scripts.automation_ecs_deploy.psycopg.connect",
            return_value=_heartbeat_connection(rows),
        ),
    ):
        result = verify_heartbeats(
            manifest_path=manifest,
            task_definition_path=task_definition,
            max_age_seconds=90,
        )

    assert result == {"status": "ok", "roles": ["route", "worker"]}


def test_verify_heartbeats_rejects_database_stale_record(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    task_definition = _task_definition(tmp_path, "worker")
    last_seen_at = datetime.now(timezone.utc)
    observed_at = last_seen_at + timedelta(seconds=91)
    rows = [
        (role, _heartbeat_provenance(role), last_seen_at, observed_at)
        for role in ("route", "worker")
    ]

    with (
        patch.dict(os.environ, {"AUTOMATION_HEARTBEAT_DSN": "postgresql://unused"}),
        patch(
            "backend.scripts.automation_ecs_deploy.psycopg.connect",
            return_value=_heartbeat_connection(rows),
        ),
    ):
        with pytest.raises(ValueError, match="latest route heartbeat is stale"):
            verify_heartbeats(
                manifest_path=manifest,
                task_definition_path=task_definition,
                max_age_seconds=90,
            )


def test_verify_preproduction_heartbeats_require_preproduction_identity(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    task_definition = _task_definition(tmp_path, "worker")
    payload = json.loads(task_definition.read_text(encoding="utf-8"))
    container = payload["taskDefinition"]["containerDefinitions"][0]
    values = {item["name"]: item for item in container["environment"]}
    values["AUTOMATION_ENVIRONMENT"]["value"] = "preproduction"
    values["AUTOMATION_DB_SCHEMA"]["value"] = "supportportal_preproduction"
    values["AUTOMATION_JOB_NAMESPACE"]["value"] = "supportportal-preproduction"
    task_definition.write_text(json.dumps(payload), encoding="utf-8")
    now = datetime.now(timezone.utc)
    rows = []
    for role in ("route", "worker"):
        provenance = _heartbeat_provenance(role)
        provenance.update(
            {
                "environment": "preproduction",
                "db_schema": "supportportal_preproduction",
                "job_namespace": "supportportal-preproduction",
            }
        )
        rows.append((role, provenance, now, now))
    with (
        patch.dict(os.environ, {"AUTOMATION_HEARTBEAT_DSN": "postgresql://unused"}),
        patch(
            "backend.scripts.automation_ecs_deploy.psycopg.connect",
            return_value=_heartbeat_connection(rows),
        ),
    ):
        result = verify_heartbeats(
            manifest_path=manifest,
            task_definition_path=task_definition,
            max_age_seconds=90,
            environment="preproduction",
        )
    assert result["status"] == "ok"


def test_formal_deploy_script_enforces_order_rollback_and_secret_safe_prompt_sync() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    main_script = script[script.index("main() {") :]
    cleanup_script = script[script.index("cleanup() {") : script.index("run_terraform_zero_plan() {")]
    assert "DEPLOY_PRODUCTION_APPROVED" in script
    assert "--check-only" in script
    assert "run_terraform_zero_plan" in script
    assert "--defer-activation" in script
    assert "--bootstrap-account-schema" in script
    assert "--hermes-case-workflow-mode" in script
    assert "Hermes mock activation requires --bootstrap-account-schema" in script
    assert "render-schema-bootstrap-task-definition" in script
    assert "--migration-dsn" not in script
    assert "TICKET_DB_MIGRATION_DSN" not in script
    assert "--target-dsn" not in script
    assert "PROMPT_RELEASE_TARGET_DSN=" in script
    assert '[[ -n "${TICKET_DB_DSN:-}" ]] || fail "TICKET_DB_DSN is required"' in script
    assert 'mkdir -p -- "${PROJECT_ROOT}/.deployments"' in script
    assert "validate-suspension-recipients" in script
    assert main_script.index("validate-suspension-recipients") < main_script.index(
        'if [[ "${CHECK_ONLY}" = "1" ]]'
    )
    route_worker = main_script.index("for role in route worker")
    heartbeat = main_script.index('AUTOMATION_HEARTBEAT_DSN="${heartbeat_dsn}" wait_for_heartbeats')
    api_update = main_script.index("update_role_if_needed api")
    activation = main_script.rindex("ACTIVATION_STARTED=1")
    assert route_worker < heartbeat < api_update < activation
    route_worker_wait = main_script.index("(wait_and_verify_role route)")
    api_wait = main_script.index("wait_for_service_revision api", api_update)
    assert route_worker_wait < heartbeat
    assert 'route_wait_pid=$!' in main_script
    assert 'worker_wait_pid=$!' in main_script
    assert api_update < api_wait < activation
    assert "HEARTBEAT_WAIT_TIMEOUT_SECONDS=90" in script
    assert "HEARTBEAT_RETRY_INTERVAL_SECONDS=5" in script
    assert "SERVICE_ROLLOUT_WAIT_TIMEOUT_SECONDS=900" in script
    assert "SERVICE_ROLLOUT_RETRY_INTERVAL_SECONDS=5" in script
    assert "wait_for_service_revision" in script
    assert '.services[0].taskDefinition == $expected' in script
    assert '.services[0].deployments[0].taskDefinition == $expected' in script
    assert '.services[0].deployments[0].rolloutState == "COMPLETED"' in script
    assert '[[ "${rollout_state}" != "FAILED" ]]' in script
    assert 'wait_for_service_revision "${role}" "${service}" "${old_arn}"' in script
    assert "while true" in script
    assert "heartbeat provenance did not converge" in script
    assert "--max-age-seconds 90" in script
    assert "last_error" not in script
    assert "rollback_services" in script
    assert "reconciliation" in script
    assert "https://support.stellarix.space/health" in script
    assert "AUTOMATION_TERRAFORM_BIN" in script
    assert "-lock-timeout=60s" in script
    assert "-lock=false" not in script
    assert (
        "'.taskDefinition.containerDefinitions[] | select(.name == $role) "
        '| .logConfiguration.options["awslogs-group"]\''
    ) in script
    assert '--log-stream-name-prefix "${role}/${role}/"' in script
    check_only = main_script.index('if [[ "${CHECK_ONLY}" = "1" ]]')
    prompt_sync = main_script.index("backend.scripts.prompt_release sync")
    schema_bootstrap = main_script.index("run_schema_bootstrap")
    assert "schema_is_current" in script
    assert 'SCHEMA_BOOTSTRAP_STATUS="skipped_current"' in script
    assert "backend.scripts.automation_ecs_bootstrap check" in script
    assert '"${observed_revision}" = "${expected_revision}"' in script
    service_registration = main_script.index("for role in api route worker", schema_bootstrap)
    reconcile_bootstrap = script.index("reconcile_schema_bootstrap_checkpoint")
    run_bootstrap = script.index("run_schema_bootstrap()")
    register = script.index("aws ecs register-task-definition")
    optional_tags = script.index("register_args+=(--tags")
    assert 'jq \'length\' "${tags_path}"' in script
    assert optional_tags < register
    update_service = main_script.index("update_role_if_needed")
    assert check_only < schema_bootstrap
    assert schema_bootstrap < prompt_sync
    assert schema_bootstrap < service_registration
    assert reconcile_bootstrap < run_bootstrap < register
    assert "Schema bootstrap checkpoint task definition family mismatch" in script
    assert "Schema bootstrap checkpoint task definition mismatch" in script
    assert "schema bootstrap superseded by validated deploy resume" in script
    assert check_only < register
    assert check_only < update_service
    assert '"${ACTIVATION_STARTED}" = "0"' in script
    assert "rollback_services" in cleanup_script
    assert "verify_aws_credential_lifetime" in script
    assert "aws configure export-credentials --format process" in script
    assert "run_parallel_post_deploy_checks" in script
    assert "automation-ecs-deploy-checkpoint-v1" in script
    assert "automation-ecs-deploy-evidence-v1" in script
    assert 'schema-bootstrap.task-arn' in script
    assert 'schema-bootstrap.task-definition-arn' in script
    assert 'schema_bootstrap_task_arn:$schema_task_arn' in script
    assert 'schema_bootstrap_task_definition:$schema_task_definition' in script
    assert "taskDefinition.status" in script
    assert "--resume" in script
    assert "Release evidence:" in script
    assert 'ROLLBACK_STATUS="succeeded"' in script
    assert 'ROLLBACK_STATUS="failed"' in script
    assert 'write_evidence "rollback_incomplete"' in script
    already_active = script.split(
        'if [[ "${PROMPT_SYNC_STATUS}" = "active" ]]; then', 1
    )[1].split("fi", 1)[0]
    assert "ACTIVATION_STARTED" not in already_active
    assert "pre-activation failures remain rollback-safe" in already_active


def test_formal_deploy_waits_for_the_registered_ecs_revision() -> None:
    expected = "arn:aws:ecs:us-east-1:123456789012:task-definition/service:4"

    def run_wait(task_definition: str, deployment_task_definition: str) -> subprocess.CompletedProcess[str]:
        service = {
            "failures": [],
            "services": [
                {
                    "status": "ACTIVE",
                    "taskDefinition": task_definition,
                    "desiredCount": 1,
                    "runningCount": 1,
                    "pendingCount": 0,
                    "deployments": [
                        {
                            "status": "PRIMARY",
                            "rolloutState": "COMPLETED",
                            "taskDefinition": deployment_task_definition,
                            "desiredCount": 1,
                            "runningCount": 1,
                            "pendingCount": 0,
                        }
                    ],
                }
            ],
        }
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; '
                'aws() { printf "%s\\n" "$FAKE_SERVICE_JSON"; }; '
                'date() { printf "100\\n"; }; '
                'sleep() { return 99; }; '
                'REGION=us-east-1; CLUSTER=cluster; '
                'SERVICE_ROLLOUT_WAIT_TIMEOUT_SECONDS=0; '
                'wait_for_service_revision route service "$EXPECTED_TASK_DEFINITION"',
                "bash",
                str(DEPLOY_SCRIPT),
            ],
            env={
                **os.environ,
                "FAKE_SERVICE_JSON": json.dumps(service),
                "EXPECTED_TASK_DEFINITION": expected,
            },
            text=True,
            capture_output=True,
            check=False,
        )

    converged = run_wait(expected, expected)
    assert converged.returncode == 0, converged.stderr

    old = "arn:aws:ecs:us-east-1:123456789012:task-definition/service:3"
    stale = run_wait(old, old)
    assert stale.returncode != 0
    assert "revision did not converge before timeout" in stale.stderr


def test_formal_deploy_script_has_strict_preproduction_record_and_approval_boundary() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "--environment" in script
    assert "--publish-record" in script
    assert "validate-preproduction-publish" in script
    assert "DEPLOY_PREPRODUCTION_APPROVED=1 is required" in script
    assert "supportportal-preproduction-api" in script
    assert "supportportal/preproduction" in script
    assert "supportportal_preproduction" in script
    assert '--environment "${ENVIRONMENT}"' in script
    assert '--repository "${PROMOTION_REPOSITORY}"' in script

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; parse_args --environment preproduction --publish-record record.json; '
            'printf "%s|%s|%s|%s|%s" "$CLUSTER" "$API_SERVICE" "$BASE_URL" '
            '"$TERRAFORM_DIR" "$PROMPT_TARGET_SCHEMA"',
            "bash",
            str(DEPLOY_SCRIPT),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "supportportal-preproduction|supportportal-preproduction-api|"
        "https://supportcenter.stellarix.space/automation/preproduction|"
        f"{ROOT}/infra/terraform/preproduction|supportportal_preproduction"
    )


def test_initial_task_definition_bootstrap_never_updates_or_creates_service() -> None:
    script = INITIAL_TASK_DEFINITIONS_SCRIPT.read_text(encoding="utf-8")
    assert "render-initial-task-definition" in script
    assert "validate-preproduction-publish" in script
    assert "AUTOMATION_INITIAL_TASK_DEFINITIONS_APPROVED=1" in script
    assert "aws ecs register-task-definition" in script
    assert "aws ecs update-service" not in script
    assert "aws ecs create-service" not in script
    assert "supportportal/production" not in script
    assert "supportportal_production" not in script


def test_hermes_mock_cli_requires_explicit_schema_bootstrap() -> None:
    rejected = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; parse_args --hermes-case-workflow-mode mock',
            "bash",
            str(DEPLOY_SCRIPT),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert rejected.returncode != 0
    assert "requires --bootstrap-account-schema" in rejected.stderr

    accepted = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; parse_args --bootstrap-account-schema --hermes-case-workflow-mode mock',
            "bash",
            str(DEPLOY_SCRIPT),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert accepted.returncode == 0, accepted.stderr


def test_hermes_persona_cli_is_independent_of_case_workflow_mode() -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; parse_args --hermes-case-workflow-mode disabled '
            '--hermes-persona-enabled; printf "%s|%s" "$HERMES_CASE_WORKFLOW_MODE" '
            '"$HERMES_PERSONA_ENABLED"',
            "bash",
            str(DEPLOY_SCRIPT),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "disabled|1"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_credential_lifetime_preflight_fails_closed_without_leaking_credentials(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    expiration = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    _write_executable(
        fake_bin / "aws",
        """#!/usr/bin/env python3
import json, os, sys
if sys.argv[1:3] == ["sts", "get-caller-identity"]:
    print("{}")
elif sys.argv[1:4] == ["configure", "export-credentials", "--format"]:
    payload = {"AccessKeyId":"AKIA_TEST_SECRET","SecretAccessKey":"DO_NOT_PRINT","SessionToken":"TOKEN_DO_NOT_PRINT"}
    if os.environ.get("TEST_EXPIRATION"):
        payload["Expiration"] = os.environ["TEST_EXPIRATION"]
    print(json.dumps(payload))
elif sys.argv[1:3] == ["configure", "list"]:
    provider = os.environ.get("TEST_PROVIDER_TYPE", "env")
    print("NAME : VALUE : TYPE : LOCATION")
    print(f"access_key : ****************TEST : {provider} :")
else:
    raise SystemExit(2)
""",
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "TEST_EXPIRATION": expiration,
        }
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; REGION=us-east-1; PYTHON_BIN="$2"; AWS_MIN_CREDENTIAL_TTL_SECONDS=1800; verify_aws_credential_lifetime',
            "bash",
            str(DEPLOY_SCRIPT),
            sys.executable,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "expire too soon" in result.stderr
    assert "AKIA_TEST_SECRET" not in result.stdout + result.stderr
    assert "DO_NOT_PRINT" not in result.stdout + result.stderr

    login_env = {**env, "TEST_PROVIDER_TYPE": "login"}
    login = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; REGION=us-east-1; PYTHON_BIN="$2"; AWS_MIN_CREDENTIAL_TTL_SECONDS=1800; verify_aws_credential_lifetime',
            "bash",
            str(DEPLOY_SCRIPT),
            sys.executable,
        ],
        cwd=ROOT,
        env=login_env,
        text=True,
        capture_output=True,
    )
    assert login.returncode == 0, login.stderr
    assert "refreshable login credential preflight passed" in login.stdout
    assert "AKIA_TEST_SECRET" not in login.stdout + login.stderr

    unknown_env = {**env, "AWS_SESSION_TOKEN": "EXPORTED_TEMPORARY_TOKEN"}
    unknown_env.pop("TEST_EXPIRATION")
    unknown = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; REGION=us-east-1; PYTHON_BIN="$2"; AWS_MIN_CREDENTIAL_TTL_SECONDS=1800; verify_aws_credential_lifetime',
            "bash",
            str(DEPLOY_SCRIPT),
            sys.executable,
        ],
        cwd=ROOT,
        env=unknown_env,
        text=True,
        capture_output=True,
    )
    assert unknown.returncode != 0
    assert "expiration is unavailable" in unknown.stderr
    assert "EXPORTED_TEMPORARY_TOKEN" not in unknown.stdout + unknown.stderr

    valid_env = {
        **env,
        "TEST_EXPIRATION": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    }
    valid = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; REGION=us-east-1; PYTHON_BIN="$2"; AWS_MIN_CREDENTIAL_TTL_SECONDS=1800; verify_aws_credential_lifetime',
            "bash",
            str(DEPLOY_SCRIPT),
            sys.executable,
        ],
        cwd=ROOT,
        env=valid_env,
        text=True,
        capture_output=True,
    )
    assert valid.returncode == 0, valid.stderr
    assert "lifetime preflight passed" in valid.stdout
    assert "AKIA_TEST_SECRET" not in valid.stdout + valid.stderr


def test_resume_checkpoint_revalidates_identity_and_never_persists_dsn(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    promotion = tmp_path / "promotion.json"
    manifest.write_text('{"release_id":"release-42"}', encoding="utf-8")
    promotion.write_text('{"source_repository":"local-oci"}', encoding="utf-8")
    state_dir = tmp_path / "deploy-state"
    shell = """
source "$1"
PYTHON_BIN="$2"
MANIFEST_PATH="$3"
PROMOTION_RECORD="$4"
AUTOMATION_ECS_DEPLOY_STATE_DIR="$5"
RELEASE_ID=release-42
GIT_COMMIT=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
PROMPT_RELEASE_ID=prompt-42
REGION=us-east-1
CLUSTER=supportportal-production
API_SERVICE=api
ROUTE_SERVICE=route
WORKER_SERVICE=worker
CHECK_ONLY=0
RESUME="$6"
prepare_deploy_workspace
"""
    args = [
        "bash",
        "-c",
        shell,
        "bash",
        str(DEPLOY_SCRIPT),
        sys.executable,
        str(manifest),
        str(promotion),
        str(state_dir),
    ]
    env = {**os.environ, "PROMPT_RELEASE_TARGET_DSN": "postgresql://must-not-persist"}

    created = subprocess.run([*args, "0"], cwd=ROOT, env=env, text=True, capture_output=True)
    assert created.returncode == 0, created.stderr
    assert state_dir.stat().st_mode & 0o777 == 0o700
    assert (state_dir / "checkpoint.json").stat().st_mode & 0o777 == 0o600
    resumed = subprocess.run([*args, "1"], cwd=ROOT, env=env, text=True, capture_output=True)
    assert resumed.returncode == 0, resumed.stderr
    assert "Validated deploy checkpoint" in resumed.stdout
    assert "must-not-persist" not in (state_dir / "checkpoint.json").read_text(encoding="utf-8")

    promotion.write_text('{"source_repository":"changed"}', encoding="utf-8")
    mismatch = subprocess.run([*args, "1"], cwd=ROOT, env=env, text=True, capture_output=True)
    assert mismatch.returncode != 0
    assert "checkpoint identity does not match" in mismatch.stderr


def test_default_checkpoint_path_is_scoped_by_environment_and_hermes_mode() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "ecs-deploy-${ENVIRONMENT}-${RELEASE_ID}${operation_suffix}" in script
    assert 'operation_suffix="-${HERMES_CASE_WORKFLOW_MODE}"' in script


def test_resume_reconciles_only_matching_schema_bootstrap_checkpoint(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log = tmp_path / "aws-calls.log"
    definition_arn = (
        "arn:aws:ecs:us-east-1:123456789012:task-definition/"
        "supportportal-production-schema-bootstrap:1"
    )
    task_arn = (
        "arn:aws:ecs:us-east-1:123456789012:task/"
        "supportportal-production/bootstrap-task"
    )
    _write_executable(
        fake_bin / "aws",
        """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["AWS_CALL_LOG"], "a", encoding="utf-8") as handle:
    handle.write(" ".join(args) + "\\n")
if args[:2] == ["ecs", "describe-task-definition"]:
    print(os.environ["TEST_BOOTSTRAP_FAMILY"])
elif args[:2] == ["ecs", "describe-tasks"]:
    print(json.dumps({"tasks": [{"lastStatus": "RUNNING", "taskDefinitionArn": os.environ["TEST_BOOTSTRAP_DEFINITION"]}]}))
elif args[:2] == ["sts", "get-caller-identity"]:
    print(json.dumps({"Account":"891612554546","Arn":"arn:aws:iam::891612554546:user/Zac"}))
elif args[:3] == ["configure", "export-credentials", "--format"]:
    print("{}")
elif args[:2] == ["configure", "list"]:
    print("access_key : ****************TEST : login :")
else:
    print("{}")
""",
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    task_path = state_dir / "schema-bootstrap.task-arn"
    definition_path = state_dir / "schema-bootstrap.task-definition-arn"
    task_path.write_text(task_arn + "\n", encoding="utf-8")
    definition_path.write_text(definition_arn + "\n", encoding="utf-8")
    shell = """
source "$1"
RESUME=1
TEMP_DIR="$2"
REGION=us-east-1
CLUSTER=supportportal-production
PYTHON_BIN="$3"
AWS_MIN_CREDENTIAL_TTL_SECONDS=0
reconcile_schema_bootstrap_checkpoint
"""
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "AWS_CALL_LOG": str(call_log),
        "TEST_BOOTSTRAP_FAMILY": "supportportal-production-schema-bootstrap",
        "TEST_BOOTSTRAP_DEFINITION": definition_arn,
    }
    env.pop("AWS_SESSION_TOKEN", None)
    result = subprocess.run(
        ["bash", "-c", shell, "bash", str(DEPLOY_SCRIPT), str(state_dir), sys.executable],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert not task_path.exists()
    assert not definition_path.exists()
    calls = call_log.read_text(encoding="utf-8")
    assert "ecs stop-task" in calls
    assert "ecs wait tasks-stopped" in calls
    assert "ecs deregister-task-definition" in calls

    task_path.write_text(task_arn + "\n", encoding="utf-8")
    definition_path.write_text(definition_arn + "\n", encoding="utf-8")
    call_log.write_text("", encoding="utf-8")
    rejected = subprocess.run(
        ["bash", "-c", shell, "bash", str(DEPLOY_SCRIPT), str(state_dir), sys.executable],
        cwd=ROOT,
        env={**env, "TEST_BOOTSTRAP_FAMILY": "unrelated-family"},
        text=True,
        capture_output=True,
    )
    assert rejected.returncode != 0
    assert "family mismatch" in rejected.stderr
    rejected_calls = call_log.read_text(encoding="utf-8")
    assert "ecs stop-task" not in rejected_calls
    assert "ecs deregister-task-definition" not in rejected_calls
