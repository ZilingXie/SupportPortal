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
    render_task_definition,
    validate_suspension_recipients,
    validate_promotion,
    verify_heartbeats,
)
from backend.services.automation_ecs_contracts import RELEASE_MANIFEST_VERSION, SCHEMA_REVISION
from backend.services.automation_release_manifest import contract_versions


ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = ROOT / "deployment/deploy_automation_ecs_release.sh"


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
    secrets = [{"name": "AUTOMATION_DB_DSN", "valueFrom": "arn:aws:ssm:::parameter/db"}]
    if role == "worker":
        secrets.append(
            {
                "name": "ACCOUNT_SUSPENSION_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON",
                "valueFrom": "arn:aws:ssm:::parameter/suspension",
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


def test_formal_deploy_script_enforces_order_rollback_and_secret_safe_prompt_sync() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    main_script = script[script.index("main() {") :]
    cleanup_script = script[script.index("cleanup() {") : script.index("run_terraform_zero_plan() {")]
    assert "DEPLOY_PRODUCTION_APPROVED" in script
    assert "--check-only" in script
    assert "run_terraform_zero_plan" in script
    assert "--defer-activation" in script
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
    assert "HEARTBEAT_WAIT_TIMEOUT_SECONDS=90" in script
    assert "HEARTBEAT_RETRY_INTERVAL_SECONDS=5" in script
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
    check_only = main_script.index('if [[ "${CHECK_ONLY}" = "1" ]]')
    prompt_sync = main_script.index("backend.scripts.prompt_release sync")
    register = script.index("aws ecs register-task-definition")
    optional_tags = script.index("register_args+=(--tags")
    assert 'jq \'length\' "${tags_path}"' in script
    assert optional_tags < register
    update_service = main_script.index("update_role_if_needed")
    assert check_only < prompt_sync
    assert check_only < register
    assert check_only < update_service
    assert '"${ACTIVATION_STARTED}" = "0"' in script
    assert "rollback_services" in cleanup_script
    assert "verify_aws_credential_lifetime" in script
    assert "aws configure export-credentials --format process" in script
    assert "run_parallel_post_deploy_checks" in script
    assert "automation-ecs-deploy-checkpoint-v1" in script
    assert "automation-ecs-deploy-evidence-v1" in script
    assert "taskDefinition.status" in script
    assert "--resume" in script
    assert "Release evidence:" in script
    assert 'ROLLBACK_STATUS="succeeded"' in script
    assert 'ROLLBACK_STATUS="failed"' in script
    assert 'write_evidence "rollback_incomplete"' in script


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
