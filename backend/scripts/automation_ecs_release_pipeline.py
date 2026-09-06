from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import psycopg
from psycopg.conninfo import make_conninfo

from backend.services.automation_release_manifest import (
    read_manifest,
    read_preproduction_publish_record,
    validate_preproduction_publish_record,
)
from backend.scripts.automation_ecs_deploy import _registrable_task_definition
from backend.repositories.ticket_repository import PostgresTicketRepository


ACCOUNT_ID = "891612554546"
AWS_REGION = "us-east-1"
AWS_USER_ARN = f"arn:aws:iam::{ACCOUNT_ID}:user/Zac"
PREFLIGHT_SCHEMA = "automation-ecs-preflight-evidence-v1"
PIPELINE_SCHEMA = "automation-ecs-release-pipeline-v1"
DEFAULT_PREFLIGHT_TTL_SECONDS = 15 * 60
ALLOWED_POST_RELEASE_PATHS = {"AGENTS.md", "CLAUDE.md", "REASONIX.md"}
STATIC_AWS_ENV = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_SECURITY_TOKEN",
    "AWS_CREDENTIAL_EXPIRATION",
}
SECRET_NAME_PATTERN = re.compile(r"(?:SECRET|TOKEN|PASSWORD|DSN|CREDENTIAL|API_KEY)", re.I)
SAFE_SECRET_NAMED_TASK_ENV = {
    "BILLING_AUTOMATION_GRAPH_TOKEN_CACHE": "/app/.msgraph/billing-automation-token.json",
}
SAFE_NUMERIC_TOKEN_CONFIG_SUFFIX = "_MAX_OUTPUT_TOKENS"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _is_secret_bearing_environment_value(name: str, value: str) -> bool:
    if not SECRET_NAME_PATTERN.search(name):
        return False
    if name.endswith(SAFE_NUMERIC_TOKEN_CONFIG_SUFFIX) and value.isdecimal():
        return False
    return SAFE_SECRET_NAMED_TASK_ENV.get(name) != value


def file_sha256(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def assert_evidence_secret_free(value: Any, path: str = "evidence") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if (
                SECRET_NAME_PATTERN.search(str(key))
                and key != "secret_metadata"
                and child not in (None, "")
                and not isinstance(child, bool)
            ):
                raise ValueError(f"secret-bearing field is forbidden in evidence: {child_path}")
            assert_evidence_secret_free(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_evidence_secret_free(child, f"{path}[{index}]")
    elif isinstance(value, str):
        lowered = value.lower()
        if "postgresql://" in lowered or "postgres://" in lowered:
            raise ValueError(f"DSN is forbidden in evidence: {path}")


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(canonical_json_bytes(value) + b"\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def _allowed_post_release_path(path: str) -> bool:
    return path.startswith("docs/") or path in ALLOWED_POST_RELEASE_PATHS


def validate_release_worktree(
    *,
    repo: str | Path,
    release_commit: str,
    main_ref: str = "origin/main",
) -> dict[str, Any]:
    repo_path = Path(repo).resolve()
    resolved_release = _git(repo_path, "rev-parse", f"{release_commit}^{{commit}}")
    resolved_main = _git(repo_path, "rev-parse", f"{main_ref}^{{commit}}")
    if not re.fullmatch(r"[0-9a-f]{40}", resolved_release):
        raise ValueError("release commit must resolve to a full SHA")
    ancestor = subprocess.run(
        ["git", "-C", str(repo_path), "merge-base", "--is-ancestor", resolved_release, resolved_main],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if ancestor.returncode != 0:
        raise ValueError("release commit is not reachable from origin/main")
    head = _git(repo_path, "rev-parse", "HEAD")
    if head != resolved_release:
        raise ValueError("release worktree HEAD does not match the requested release commit")
    if _git(repo_path, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("release worktree must be clean")
    changed = [
        path
        for path in _git(repo_path, "diff", "--name-only", f"{resolved_release}..{resolved_main}").splitlines()
        if path
    ]
    runtime_changes = [path for path in changed if not _allowed_post_release_path(path)]
    if runtime_changes:
        raise ValueError(
            "origin/main contains post-release runtime changes; build a new release: "
            + ", ".join(runtime_changes[:10])
        )
    return {
        "release_commit": resolved_release,
        "origin_main_commit": resolved_main,
        "post_release_paths": changed,
        "post_release_runtime_changes": [],
        "status": "passed",
    }


def validate_release_source(
    *,
    repo: str | Path,
    release_commit: str,
    manifest_path: str | Path,
    main_ref: str = "origin/main",
) -> dict[str, Any]:
    result = validate_release_worktree(
        repo=repo, release_commit=release_commit, main_ref=main_ref
    )
    manifest = read_manifest(Path(manifest_path))
    if manifest.git_commit != result["release_commit"]:
        raise ValueError("Manifest Git commit does not match release worktree HEAD")
    result["manifest_sha256"] = file_sha256(manifest_path)
    return result


def terraform_config_sha256(directory: str | Path) -> str:
    root = Path(directory).resolve()
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and (
            path.name == ".terraform.lock.hcl"
            or path.name.endswith((".tf", ".tf.json", ".tfvars", ".tfvars.json"))
        )
        and ".terraform" not in path.parts
    )
    payload = [
        {"path": path.relative_to(root).as_posix(), "sha256": file_sha256(path)} for path in files
    ]
    return canonical_sha256(payload)


def task_definition_sha256(value: Mapping[str, Any]) -> str:
    task_definition = value.get("taskDefinition") if "taskDefinition" in value else value
    if not isinstance(task_definition, dict):
        raise ValueError("task definition must be a JSON object")
    for container in task_definition.get("containerDefinitions") or []:
        for item in container.get("environment") or []:
            name = str(item.get("name") or "")
            item_value = str(item.get("value") or "")
            if item_value and _is_secret_bearing_environment_value(name, item_value):
                raise ValueError(f"task definition contains plaintext secret environment: {name}")
    return canonical_sha256(_registrable_task_definition(task_definition))


def database_identity_sha256(database: str, user: str, oid: int, server_version: str) -> str:
    return canonical_sha256(
        {"database": database, "user": user, "oid": oid, "server_version": server_version}
    )


def collect_prompt_target_identity(schema: str) -> dict[str, Any]:
    dsn = str(os.environ.get("PROMPT_RELEASE_TARGET_DSN") or "").strip()
    if not dsn:
        raise ValueError("PROMPT_RELEASE_TARGET_DSN is required")
    with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), current_user, oid, current_setting('server_version_num') "
                "FROM pg_database WHERE datname = current_database()"
            )
            row = cursor.fetchone()
    if row is None:
        raise ValueError("target Prompt database identity is unavailable")
    return {
        "schema": schema,
        "database_identity_sha256": database_identity_sha256(
            str(row[0]), str(row[1]), int(row[2]), str(row[3])
        ),
    }


def collect_prompt_target_state(schema: str, release_id: str) -> dict[str, Any]:
    dsn = str(os.environ.get("PROMPT_RELEASE_TARGET_DSN") or "").strip()
    if not dsn:
        raise ValueError("PROMPT_RELEASE_TARGET_DSN is required")
    readonly_dsn = make_conninfo(dsn, options="-c default_transaction_read_only=on")
    repository = PostgresTicketRepository(
        dsn=readonly_dsn,
        schema=schema,
        migration_dsn=readonly_dsn,
        application_name="supportportal-prompt-release-readback",
    )
    try:
        release = repository.get_prompt_release(release_id)
    finally:
        repository.close()
    if release is None:
        return {"release_id": release_id, "status": "missing"}
    return {"release_id": release_id, "status": str(release.get("status") or "")}


def make_preflight_evidence(
    *,
    manifest_path: str | Path,
    record_path: str | Path,
    context: Mapping[str, Any],
    reusable: bool,
    ttl_seconds: int = DEFAULT_PREFLIGHT_TTL_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    if ttl_seconds <= 0:
        raise ValueError("preflight TTL must be positive")
    generated = now or _utc_now()
    payload: dict[str, Any] = {
        "schema_version": PREFLIGHT_SCHEMA,
        "generated_at": _iso(generated),
        "expires_at": _iso(generated + timedelta(seconds=ttl_seconds)),
        "reusable_for_deploy": bool(reusable),
        "manifest_sha256": file_sha256(manifest_path),
        "deploy_record_sha256": file_sha256(record_path),
        "context": dict(context),
        "checks": {"terraform_zero_drift": "passed"},
    }
    assert_evidence_secret_free(payload)
    payload["content_sha256"] = canonical_sha256(payload)
    return payload


def validate_preflight_evidence(
    evidence: Mapping[str, Any],
    *,
    manifest_path: str | Path,
    record_path: str | Path,
    context: Mapping[str, Any],
    now: datetime | None = None,
    require_reusable: bool = True,
) -> dict[str, Any]:
    if evidence.get("schema_version") != PREFLIGHT_SCHEMA:
        raise ValueError("invalid Preflight Evidence schema")
    stored_hash = str(evidence.get("content_sha256") or "")
    unsigned = dict(evidence)
    unsigned.pop("content_sha256", None)
    if stored_hash != canonical_sha256(unsigned):
        raise ValueError("Preflight Evidence content hash mismatch")
    if require_reusable and evidence.get("reusable_for_deploy") is not True:
        raise ValueError("Preflight Evidence is not reusable for deploy")
    if evidence.get("checks") != {"terraform_zero_drift": "passed"}:
        raise ValueError("Preflight Evidence Terraform zero-drift check is incomplete")
    if _parse_time(str(evidence.get("expires_at") or "1970-01-01T00:00:00Z")) <= (now or _utc_now()):
        raise ValueError("Preflight Evidence has expired")
    expected = {
        "manifest_sha256": file_sha256(manifest_path),
        "deploy_record_sha256": file_sha256(record_path),
        "context": dict(context),
    }
    for key, value in expected.items():
        if evidence.get(key) != value:
            raise ValueError(f"Preflight Evidence {key} mismatch")
    return {"status": "passed", "content_sha256": stored_hash, "expires_at": evidence["expires_at"]}


def write_preflight_evidence(evidence: Mapping[str, Any], destination: str | Path) -> Path:
    requested = Path(destination)
    if requested.suffix == ".json":
        path = requested
    else:
        digest = str(evidence["content_sha256"]).removeprefix("sha256:")
        path = requested / f"{digest}.json"
    _write_json_atomic(path, evidence)
    return path


def sanitized_aws_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(source or os.environ)
    for name in STATIC_AWS_ENV:
        env.pop(name, None)
    profile = env.get("AUTOMATION_AWS_PROFILE") or env.get("AWS_PROFILE") or "default"
    env["AWS_PROFILE"] = profile
    env["AWS_REGION"] = AWS_REGION
    env["AWS_DEFAULT_REGION"] = AWS_REGION
    return env


def assert_secret_free_argv(argv: Sequence[str], env: Mapping[str, str]) -> None:
    rendered = "\0".join(argv)
    for name, value in env.items():
        if value and _is_secret_bearing_environment_value(name, value) and value in rendered:
            raise ValueError(f"secret-bearing environment value from {name} must not enter argv")


def verify_aws_identity(env: Mapping[str, str]) -> dict[str, str]:
    result = subprocess.run(
        ["aws", "sts", "get-caller-identity", "--region", AWS_REGION, "--output", "json"],
        env=dict(env),
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    identity = json.loads(result.stdout)
    account = str(identity.get("Account") or "")
    arn = str(identity.get("Arn") or "")
    if account != ACCOUNT_ID or arn != AWS_USER_ARN:
        raise ValueError("AWS identity must be account 891612554546 IAM user Zac")
    return {"account_id": account, "arn": arn, "region": AWS_REGION}


def deploy_mode_args(args: argparse.Namespace) -> list[str]:
    values: list[str] = []
    if args.bootstrap_account_schema:
        values.append("--bootstrap-account-schema")
    if args.hermes_case_workflow_mode:
        values.extend(["--hermes-case-workflow-mode", args.hermes_case_workflow_mode])
    if args.hermes_persona_enabled:
        values.append("--hermes-persona-enabled")
    return values


def prompt_target_dsn(env: Mapping[str, str], environment: str) -> str:
    specific_name = f"{environment.upper()}_PROMPT_RELEASE_TARGET_DSN"
    value = str(env.get(specific_name) or "").strip()
    if not value and environment == "preproduction":
        value = str(env.get("PROMPT_RELEASE_TARGET_DSN") or "").strip()
    if not value:
        raise ValueError(f"{specific_name} is required for {environment} deploy")
    return value


@dataclass
class PipelineState:
    path: Path

    def __post_init__(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path, 0o700)
        self.checkpoint = self.path / "checkpoint.json"
        if not self.checkpoint.exists():
            _write_json_atomic(self.checkpoint, {"schema_version": PIPELINE_SCHEMA, "attempts": []})

    def attempts(self) -> list[dict[str, Any]]:
        payload = _read_json(self.checkpoint)
        attempts = payload.get("attempts") or []
        return list(attempts) if isinstance(attempts, list) else []

    def completed(self, stage: str) -> bool:
        return any(item.get("stage") == stage and item.get("status") == "passed" for item in self.attempts())

    def bind_identity(self, identity: Mapping[str, Any]) -> None:
        payload = _read_json(self.checkpoint)
        expected = dict(identity)
        existing = payload.get("identity")
        if existing is not None and existing != expected:
            raise ValueError("pipeline checkpoint identity does not match requested release inputs")
        if existing is None:
            payload["identity"] = expected
            _write_json_atomic(self.checkpoint, payload)

    def append(self, attempt: Mapping[str, Any]) -> None:
        payload = _read_json(self.checkpoint)
        attempts = payload.setdefault("attempts", [])
        if not isinstance(attempts, list):
            raise ValueError("pipeline checkpoint attempts must be a list")
        attempts.append(dict(attempt))
        _write_json_atomic(self.checkpoint, payload)


def _run_stage(
    state: PipelineState,
    stage: str,
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    resume: bool,
    skip_if_complete: bool = True,
) -> None:
    if resume and skip_if_complete and state.completed(stage):
        return
    assert_secret_free_argv(argv, env)
    started = _utc_now()
    monotonic_start = time.monotonic()
    status = "passed"
    try:
        subprocess.run(list(argv), cwd=cwd, env=dict(env), check=True)
    except BaseException:
        status = "failed"
        raise
    finally:
        finished = _utc_now()
        state.append(
            {
                "stage": stage,
                "status": status,
                "started_at": _iso(started),
                "finished_at": _iso(finished),
                "duration_seconds": round(time.monotonic() - monotonic_start, 3),
            }
        )


def _pipeline_summary(state: PipelineState) -> dict[str, Any]:
    latest: dict[str, dict[str, Any]] = {}
    for attempt in state.attempts():
        latest[str(attempt.get("stage"))] = attempt
    stages = [latest[name] for name in latest]
    detailed: list[dict[str, Any]] = []
    for environment in ("preproduction", "production"):
        evidence_path = state.path / f"{environment}-deploy" / "evidence.json"
        if not evidence_path.exists():
            continue
        evidence = _read_json(evidence_path)
        for item in evidence.get("phase_timings") or []:
            detail = dict(item)
            detail["stage"] = f"{environment}.{detail.pop('phase', 'unknown')}"
            detailed.append(detail)
    total = round(sum(float(item.get("duration_seconds") or 0) for item in stages), 3)
    ecs_wait = round(
        sum(
            float(item.get("duration_seconds") or 0)
            for item in detailed
            if str(item.get("stage", "")).endswith(("route_worker_rollout", "api_rollout"))
        ),
        3,
    )
    return {
        "schema_version": PIPELINE_SCHEMA,
        "stages": stages,
        "deployment_stages": detailed,
        "total_seconds": total,
        "ecs_wait_seconds": ecs_wait,
        "controllable_seconds": round(max(0.0, total - ecs_wait), 3),
    }


def _recover_release_evidence(
    *,
    release_dir: Path,
    release_id: str,
    release_commit: str,
    prompt_release_id: str,
    env: Mapping[str, str],
) -> bool:
    bucket = str(env.get("AUTOMATION_RELEASE_EVIDENCE_BUCKET") or "").strip()
    if not bucket:
        return False
    release_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = release_dir / "release-manifest.json"
    record_path = release_dir / "publish-record.json"
    try:
        for name, path in (("release-manifest.json", manifest_path), ("publish-record.json", record_path)):
            subprocess.run(
                [
                    "aws",
                    "s3api",
                    "get-object",
                    "--region",
                    AWS_REGION,
                    "--bucket",
                    bucket,
                    "--key",
                    f"releases/{release_id}/{name}",
                    str(path),
                ],
                env=dict(env),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        manifest = read_manifest(manifest_path)
        record = read_preproduction_publish_record(record_path)
        validate_preproduction_publish_record(manifest, record)
        if manifest.git_commit != release_commit or manifest.prompt_release_id != prompt_release_id:
            raise ValueError("recovered release evidence identity mismatch")
        return True
    except (OSError, ValueError, subprocess.CalledProcessError):
        manifest_path.unlink(missing_ok=True)
        record_path.unlink(missing_ok=True)
        return False


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    project_root = Path(args.project_root).resolve()
    env = sanitized_aws_environment()
    env["AUTOMATION_RELEASE_PYTHON"] = sys.executable
    verify_aws_identity(env)
    release_commit = args.release_commit or _git(project_root, "rev-parse", "origin/main")
    release_commit = _git(project_root, "rev-parse", f"{release_commit}^{{commit}}")
    release_id = f"r{_utc_now():%Y%m%d}-{release_commit[:7]}"
    if args.resume:
        candidates = sorted(
            (project_root / ".deployments").glob(f"ecs-pipeline-r*-{release_commit[:7]}")
        )
        if len(candidates) > 1:
            raise ValueError("multiple pipeline checkpoints match this release commit")
        if not candidates:
            raise ValueError("pipeline checkpoint not found for --resume")
        release_id = candidates[0].name.removeprefix("ecs-pipeline-")
    state_path = project_root / ".deployments" / f"ecs-pipeline-{release_id}"
    if state_path.exists() and not args.resume:
        raise ValueError(f"pipeline state already exists; review it and use --resume: {state_path}")
    state = PipelineState(state_path)
    state.bind_identity(
        {
            "release_commit": release_commit,
            "prompt_release_id": args.prompt_release_id,
            "mode": {
                "schema_bootstrap": bool(args.bootstrap_account_schema),
                "hermes_case_workflow": args.hermes_case_workflow_mode or "",
                "hermes_persona_enabled": bool(args.hermes_persona_enabled),
            },
        }
    )
    release_worktree = state.path / "release-worktree"
    release_dir = state.path / "release"
    if not release_worktree.exists():
        subprocess.run(
            ["git", "-C", str(project_root), "worktree", "add", "--detach", str(release_worktree), release_commit],
            check=True,
        )
    validate_release_worktree(repo=release_worktree, release_commit=release_commit)
    manifest_path = release_dir / "release-manifest.json"
    record_path = release_dir / "publish-record.json"
    mode_args = deploy_mode_args(args)
    preproduction_env = dict(env)
    preproduction_env["PROMPT_RELEASE_TARGET_DSN"] = prompt_target_dsn(env, "preproduction")
    common_deploy = [
        str(project_root / "deployment" / "deploy_automation_ecs_release.sh"),
        "--environment",
        "preproduction",
        "--release-worktree",
        str(release_worktree),
        "--manifest",
        str(manifest_path),
        "--publish-record",
        str(record_path),
        *mode_args,
    ]
    try:
        codebuild_ready = bool(
            args.resume and state.completed("codebuild") and manifest_path.exists() and record_path.exists()
        )
        if args.resume and not codebuild_ready:
            codebuild_ready = _recover_release_evidence(
                release_dir=release_dir,
                release_id=release_id,
                release_commit=release_commit,
                prompt_release_id=args.prompt_release_id,
                env=env,
            )
            if codebuild_ready:
                state.append(
                    {
                        "stage": "codebuild",
                        "status": "passed",
                        "recovered_from": "versioned_s3_evidence",
                        "duration_seconds": 0,
                    }
                )
        if not codebuild_ready:
            _run_stage(
                state,
                "codebuild",
                [
                    str(project_root / "deployment" / "start_automation_codebuild_release.sh"),
                    "--git-commit",
                    release_commit,
                    "--prompt-release-id",
                    args.prompt_release_id,
                    "--release-id",
                    release_id,
                    "--output-dir",
                    str(release_dir),
                ],
                cwd=project_root,
                env=env,
                resume=args.resume,
            )
        validate_release_source(
            repo=release_worktree,
            release_commit=release_commit,
            manifest_path=manifest_path,
        )
        preflight_path = state.path / "preflight.json"
        _run_stage(
            state,
            "preflight",
            [*common_deploy, "--check-only", "--preflight-evidence-out", str(preflight_path)],
            cwd=project_root,
            env=preproduction_env,
            resume=args.resume,
            skip_if_complete=False,
        )
        deploy_env = dict(preproduction_env)
        deploy_env["AUTOMATION_ECS_DEPLOY_STATE_DIR"] = str(state.path / "preproduction-deploy")
        deploy_args = [*common_deploy, "--preflight-evidence", str(preflight_path)]
        if args.resume and (state.path / "preproduction-deploy" / "checkpoint.json").exists():
            deploy_args.append("--resume")
        _run_stage(
            state,
            "preproduction_deploy",
            deploy_args,
            cwd=project_root,
            env=deploy_env,
            resume=args.resume,
            skip_if_complete=False,
        )
        if args.through == "production":
            if env.get("DEPLOY_PRODUCTION_APPROVED") != "1":
                raise ValueError("DEPLOY_PRODUCTION_APPROVED=1 is required before Production promotion")
            production_target_dsn = prompt_target_dsn(env, "production")
            promotion = state.path / "promotion-record.json"
            preproduction_evidence = state.path / "preproduction-deploy" / "evidence.json"
            _run_stage(
                state,
                "production_promotion",
                [
                    str(project_root / "deployment" / "promote_automation_release.sh"),
                    "--manifest",
                    str(manifest_path),
                    "--publish-record",
                    str(record_path),
                    "--preproduction-evidence",
                    str(preproduction_evidence),
                    "--promotion-record",
                    str(promotion),
                    "--region",
                    AWS_REGION,
                ],
                cwd=project_root,
                env=env,
                resume=args.resume,
            )
            production_env = dict(env)
            production_env["PROMPT_RELEASE_TARGET_DSN"] = production_target_dsn
            production_env["AUTOMATION_ECS_DEPLOY_STATE_DIR"] = str(state.path / "production-deploy")
            _run_stage(
                state,
                "production_deploy",
                [
                    str(project_root / "deployment" / "deploy_automation_ecs_release.sh"),
                    "--environment",
                    "production",
                    "--release-worktree",
                    str(release_worktree),
                    "--manifest",
                    str(manifest_path),
                    "--promotion-record",
                    str(promotion),
                    *mode_args,
                ],
                cwd=project_root,
                env=production_env,
                resume=args.resume,
                skip_if_complete=False,
            )
        summary = _pipeline_summary(state)
        _write_json_atomic(state.path / "timings.json", summary)
        return summary
    finally:
        if release_worktree.exists() and not args.keep_release_worktree:
            subprocess.run(
                ["git", "-C", str(project_root), "worktree", "remove", "--force", str(release_worktree)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SupportPortal immutable ECS release pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    release = subparsers.add_parser("validate-release-source")
    release.add_argument("--repo", required=True)
    release.add_argument("--release-commit", required=True)
    release.add_argument("--manifest", required=True)
    release.add_argument("--main-ref", default="origin/main")
    release_tree = subparsers.add_parser("validate-release-worktree")
    release_tree.add_argument("--repo", required=True)
    release_tree.add_argument("--release-commit", required=True)
    release_tree.add_argument("--main-ref", default="origin/main")

    create = subparsers.add_parser("create-preflight-evidence")
    create.add_argument("--manifest", required=True)
    create.add_argument("--record", required=True)
    create.add_argument("--context", required=True)
    create.add_argument("--output", required=True)
    create.add_argument("--ttl-seconds", type=int, default=DEFAULT_PREFLIGHT_TTL_SECONDS)
    create.add_argument("--reusable", action="store_true")

    validate = subparsers.add_parser("validate-preflight-evidence")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--record", required=True)
    validate.add_argument("--context", required=True)
    validate.add_argument("--evidence", required=True)

    task_hash = subparsers.add_parser("task-definition-sha256")
    task_hash.add_argument("--task-definition", required=True)
    prompt_target = subparsers.add_parser("prompt-target-identity")
    prompt_target.add_argument("--schema", required=True)
    prompt_state = subparsers.add_parser("prompt-target-state")
    prompt_state.add_argument("--schema", required=True)
    prompt_state.add_argument("--release-id", required=True)

    pipeline = subparsers.add_parser("run")
    pipeline.add_argument("--project-root", default=str(Path(__file__).resolve().parents[2]))
    pipeline.add_argument("--release-commit")
    pipeline.add_argument("--prompt-release-id", required=True)
    pipeline.add_argument("--through", choices=("preproduction", "production"), default="preproduction")
    pipeline.add_argument("--bootstrap-account-schema", action="store_true")
    pipeline.add_argument("--hermes-case-workflow-mode", choices=("disabled", "mock", "real"))
    pipeline.add_argument("--hermes-persona-enabled", action="store_true")
    pipeline.add_argument("--resume", action="store_true")
    pipeline.add_argument("--keep-release-worktree", action="store_true", help=argparse.SUPPRESS)
    return parser


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    if args.command == "validate-release-source":
        return validate_release_source(
            repo=args.repo,
            release_commit=args.release_commit,
            manifest_path=args.manifest,
            main_ref=args.main_ref,
        )
    if args.command == "validate-release-worktree":
        return validate_release_worktree(
            repo=args.repo,
            release_commit=args.release_commit,
            main_ref=args.main_ref,
        )
    if args.command == "create-preflight-evidence":
        evidence = make_preflight_evidence(
            manifest_path=args.manifest,
            record_path=args.record,
            context=_read_json(args.context),
            reusable=args.reusable,
            ttl_seconds=args.ttl_seconds,
        )
        path = write_preflight_evidence(evidence, args.output)
        return {"path": str(path), **evidence}
    if args.command == "validate-preflight-evidence":
        return validate_preflight_evidence(
            _read_json(args.evidence),
            manifest_path=args.manifest,
            record_path=args.record,
            context=_read_json(args.context),
        )
    if args.command == "task-definition-sha256":
        return {"sha256": task_definition_sha256(_read_json(args.task_definition))}
    if args.command == "prompt-target-identity":
        return collect_prompt_target_identity(args.schema)
    if args.command == "prompt-target-state":
        return collect_prompt_target_state(args.schema, args.release_id)
    return run_pipeline(args)


def main(argv: list[str] | None = None) -> int:
    try:
        print(json.dumps(run(argv), sort_keys=True))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"automation ECS release pipeline failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
