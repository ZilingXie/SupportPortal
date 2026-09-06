from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.scripts.automation_ecs_release_pipeline import (
    DEFAULT_PREFLIGHT_TTL_SECONDS,
    PipelineState,
    assert_secret_free_argv,
    assert_evidence_secret_free,
    make_preflight_evidence,
    sanitized_aws_environment,
    database_identity_sha256,
    collect_prompt_target_state,
    deploy_mode_args,
    prompt_target_dsn,
    task_definition_sha256,
    validate_preflight_evidence,
    validate_release_source,
    write_preflight_evidence,
)
from backend.services.automation_ecs_contracts import RELEASE_MANIFEST_VERSION, SCHEMA_REVISION
from backend.services.automation_release_manifest import contract_versions


def _run(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def _commit(repo: Path, path: str, content: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run(repo, "add", path)
    _run(repo, "commit", "-m", path)
    return _run(repo, "rev-parse", "HEAD")


def _manifest(path: Path, commit: str) -> Path:
    value = {
        "schema_version": RELEASE_MANIFEST_VERSION,
        "release_id": "r20260906-test",
        "git_commit": commit,
        "build_time": "2026-09-06T00:00:00Z",
        "prompt_release_id": "prompt-test",
        "schema_revision": SCHEMA_REVISION,
        "platform": "linux/amd64",
        "contracts": contract_versions(),
        "components": {
            role: {
                "role": role,
                "tag": f"{role}-r20260906-test",
                "digest": "sha256:" + digit * 64,
                "platform": "linux/amd64",
                "oci_layout": f"{role}.oci.tar",
            }
            for role, digit in (("api", "1"), ("route", "2"), ("worker", "3"))
        },
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-b", "main")
    _run(repo, "config", "user.email", "test@example.com")
    _run(repo, "config", "user.name", "Test")
    release = _commit(repo, "backend/app.py", "release\n")
    _run(repo, "branch", "origin/main")
    return repo, release


def test_release_commit_may_trail_origin_main_by_docs_only(tmp_path: Path) -> None:
    repo, release = _repo(tmp_path)
    _commit(repo, "docs/release.md", "evidence\n")
    _run(repo, "branch", "-f", "origin/main", "HEAD")
    _run(repo, "checkout", "--detach", release)
    manifest = _manifest(tmp_path / "manifest.json", release)

    result = validate_release_source(repo=repo, release_commit=release, manifest_path=manifest)

    assert result["status"] == "passed"
    assert result["post_release_paths"] == ["docs/release.md"]


@pytest.mark.parametrize(
    "changed_path",
    ["backend/app.py", "ui/app.js", "deployment/x.sh", "infra/x.tf", "requirements.txt"],
)
def test_release_commit_rejects_post_release_runtime_change(tmp_path: Path, changed_path: str) -> None:
    repo, release = _repo(tmp_path)
    _commit(repo, changed_path, "runtime change\n")
    _run(repo, "branch", "-f", "origin/main", "HEAD")
    _run(repo, "checkout", "--detach", release)
    with pytest.raises(ValueError, match="post-release runtime changes"):
        validate_release_source(
            repo=repo,
            release_commit=release,
            manifest_path=_manifest(tmp_path / "manifest.json", release),
        )


def test_release_source_rejects_non_ancestor_dirty_and_manifest_mismatch(tmp_path: Path) -> None:
    repo, release = _repo(tmp_path)
    _run(repo, "checkout", "--orphan", "unrelated")
    unrelated = _commit(repo, "unrelated.txt", "x\n")
    with pytest.raises(ValueError, match="not reachable"):
        validate_release_source(
            repo=repo,
            release_commit=unrelated,
            manifest_path=_manifest(tmp_path / "manifest.json", unrelated),
        )
    _run(repo, "checkout", "--detach", release)
    (repo / "dirty.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(ValueError, match="clean"):
        validate_release_source(
            repo=repo,
            release_commit=release,
            manifest_path=_manifest(tmp_path / "manifest.json", release),
        )
    (repo / "dirty.txt").unlink()
    with pytest.raises(ValueError, match="Manifest Git commit"):
        validate_release_source(
            repo=repo,
            release_commit=release,
            manifest_path=_manifest(tmp_path / "manifest.json", "f" * 40),
        )


def _evidence_inputs(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    manifest = tmp_path / "manifest.json"
    record = tmp_path / "record.json"
    manifest.write_bytes(b'{"manifest":"one"}\n')
    record.write_bytes(b'{"record":"one"}\n')
    context: dict[str, object] = {
        "environment": "preproduction",
        "services": {"api": "api", "route": "route", "worker": "worker"},
        "terraform": {"lineage": "lineage", "serial": 7, "config_sha256": "sha256:config"},
        "prompt": {"release_id": "prompt-1", "content_fingerprint": "sha256:prompt"},
    }
    return manifest, record, context


def test_preflight_evidence_reuse_is_content_addressed_and_time_bounded(tmp_path: Path) -> None:
    manifest, record, context = _evidence_inputs(tmp_path)
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    evidence = make_preflight_evidence(
        manifest_path=manifest, record_path=record, context=context, reusable=True, now=now
    )
    path = write_preflight_evidence(evidence, tmp_path / "evidence")
    assert path.name == evidence["content_sha256"].removeprefix("sha256:") + ".json"
    assert datetime.fromisoformat(evidence["expires_at"].replace("Z", "+00:00")) == now + timedelta(
        seconds=DEFAULT_PREFLIGHT_TTL_SECONDS
    )
    assert validate_preflight_evidence(
        evidence,
        manifest_path=manifest,
        record_path=record,
        context=context,
        now=now + timedelta(minutes=14),
    )["status"] == "passed"
    with pytest.raises(ValueError, match="expired"):
        validate_preflight_evidence(
            evidence,
            manifest_path=manifest,
            record_path=record,
            context=context,
            now=now + timedelta(minutes=15),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest, record, context: manifest.write_bytes(b"changed"),
        lambda manifest, record, context: record.write_bytes(b"changed"),
        lambda manifest, record, context: context["terraform"].update(serial=8),  # type: ignore[union-attr]
        lambda manifest, record, context: context.update(environment="production"),
        lambda manifest, record, context: context["services"].update(api="other"),  # type: ignore[union-attr]
        lambda manifest, record, context: context["prompt"].update(release_id="prompt-2"),  # type: ignore[union-attr]
    ],
)
def test_preflight_evidence_rejects_every_bound_input_change(tmp_path: Path, mutation) -> None:
    manifest, record, context = _evidence_inputs(tmp_path)
    evidence = make_preflight_evidence(
        manifest_path=manifest, record_path=record, context=context, reusable=True
    )
    mutation(manifest, record, context)
    with pytest.raises(ValueError, match="mismatch"):
        validate_preflight_evidence(
            evidence, manifest_path=manifest, record_path=record, context=context
        )


def test_preflight_tamper_and_non_reusable_evidence_fail_closed(tmp_path: Path) -> None:
    manifest, record, context = _evidence_inputs(tmp_path)
    evidence = make_preflight_evidence(
        manifest_path=manifest, record_path=record, context=context, reusable=False
    )
    with pytest.raises(ValueError, match="not reusable"):
        validate_preflight_evidence(
            evidence, manifest_path=manifest, record_path=record, context=context
        )
    evidence["context"] = {"tampered": True}
    with pytest.raises(ValueError, match="content hash"):
        validate_preflight_evidence(
            evidence, manifest_path=manifest, record_path=record, context=context
        )


def test_aws_environment_rotates_without_static_credentials_or_secret_argv() -> None:
    source = {
        "PATH": os.environ["PATH"],
        "AWS_ACCESS_KEY_ID": "AKIA_SENTINEL",
        "AWS_SECRET_ACCESS_KEY": "secret-sentinel",
        "AWS_SESSION_TOKEN": "token-sentinel",
        "AUTOMATION_AWS_PROFILE": "zac-login",
        "PROMPT_RELEASE_TARGET_DSN": "postgresql://dsn-sentinel",
    }
    result = sanitized_aws_environment(source)
    assert result["AWS_PROFILE"] == "zac-login"
    assert result["AWS_REGION"] == "us-east-1"
    assert not ({"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"} & result.keys())
    with pytest.raises(ValueError, match="must not enter argv"):
        assert_secret_free_argv(["deploy", source["PROMPT_RELEASE_TARGET_DSN"]], source)
    with pytest.raises(ValueError, match="forbidden in evidence"):
        assert_evidence_secret_free({"target_dsn": source["PROMPT_RELEASE_TARGET_DSN"]})


def test_checkpoint_attempts_are_append_only_and_resume_keeps_failures(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "state")
    identity = {"release_commit": "a" * 40, "prompt_release_id": "prompt-1"}
    state.bind_identity(identity)
    state.bind_identity(identity)
    state.append({"stage": "preflight", "status": "failed", "duration_seconds": 1})
    state.append({"stage": "preflight", "status": "passed", "duration_seconds": 2})
    assert [item["status"] for item in state.attempts()] == ["failed", "passed"]
    assert state.completed("preflight") is True
    with pytest.raises(ValueError, match="checkpoint identity"):
        state.bind_identity({"release_commit": "a" * 40, "prompt_release_id": "prompt-2"})


def test_task_definition_hash_ignores_revision_metadata_but_not_configuration() -> None:
    first = {"taskDefinition": {"family": "api", "revision": 1, "containerDefinitions": []}}
    second = {"taskDefinition": {"family": "api", "revision": 9, "containerDefinitions": []}}
    assert task_definition_sha256(first) == task_definition_sha256(second)
    second["taskDefinition"]["cpu"] = "512"
    assert task_definition_sha256(first) != task_definition_sha256(second)
    unsafe = {
        "family": "api",
        "containerDefinitions": [
            {"name": "api", "environment": [{"name": "DATABASE_DSN", "value": "sentinel"}]}
        ],
    }
    with pytest.raises(ValueError, match="plaintext secret"):
        task_definition_sha256(unsafe)


def test_terraform_credential_process_is_private_absolute_and_secret_free(tmp_path: Path) -> None:
    deploy_script = Path(__file__).resolve().parents[2] / "deployment" / "deploy_automation_ecs_release.sh"
    env = {
        **os.environ,
        "AWS_ACCESS_KEY_ID": "AKIA_SENTINEL",
        "AWS_SECRET_ACCESS_KEY": "secret-sentinel",
        "AWS_SESSION_TOKEN": "token-sentinel",
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; TEMP_DIR="$2"; prepare_terraform_provider; '
            'printf "%s\\n" "$AWS_CONFIG_FILE"; cat "$AWS_CONFIG_FILE"; cat "$TEMP_DIR/aws-credential-process.sh"',
            "bash",
            str(deploy_script),
            str(tmp_path),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert str(tmp_path.resolve()) in result.stdout
    assert "credential_process = /" in result.stdout
    assert "configure export-credentials" in result.stdout
    assert "AKIA_SENTINEL" not in result.stdout + result.stderr
    assert "secret-sentinel" not in result.stdout + result.stderr
    assert "token-sentinel" not in result.stdout + result.stderr


def test_formal_pipeline_keeps_production_approval_before_promotion() -> None:
    source = Path(__file__).resolve().parents[1] / "scripts" / "automation_ecs_release_pipeline.py"
    text = source.read_text(encoding="utf-8")
    approval = text.index('env.get("DEPLOY_PRODUCTION_APPROVED") != "1"')
    promotion = text.index('"production_promotion"')
    assert approval < promotion
    assert "start_automation_codebuild_release.sh" in text
    assert "--release-worktree" in text
    assert "--through" in text
    assert text.index("validate_release_worktree(repo=release_worktree") < text.index(
        '"codebuild"'
    )


def test_formal_pipeline_wrapper_uses_repository_runtime() -> None:
    wrapper = Path(__file__).resolve().parents[2] / "deployment" / "release_automation_ecs_pipeline.sh"
    result = subprocess.run(
        [str(wrapper), "--help"],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--release-commit" in result.stdout


def test_pipeline_uses_identical_mode_arguments_for_preflight_and_deploy() -> None:
    args = argparse.Namespace(
        bootstrap_account_schema=True,
        hermes_case_workflow_mode="mock",
        hermes_persona_enabled=True,
    )
    assert deploy_mode_args(args) == [
        "--bootstrap-account-schema",
        "--hermes-case-workflow-mode",
        "mock",
        "--hermes-persona-enabled",
    ]


def test_pipeline_requires_environment_specific_production_prompt_target() -> None:
    env = {
        "PROMPT_RELEASE_TARGET_DSN": "preproduction-sentinel",
        "PREPRODUCTION_PROMPT_RELEASE_TARGET_DSN": "preproduction-specific",
        "PRODUCTION_PROMPT_RELEASE_TARGET_DSN": "production-specific",
    }
    assert prompt_target_dsn(env, "preproduction") == "preproduction-specific"
    assert prompt_target_dsn(env, "production") == "production-specific"
    assert prompt_target_dsn({"PROMPT_RELEASE_TARGET_DSN": "legacy"}, "preproduction") == "legacy"
    with pytest.raises(ValueError, match="PRODUCTION_PROMPT_RELEASE_TARGET_DSN"):
        prompt_target_dsn({"PROMPT_RELEASE_TARGET_DSN": "must-not-cross-environments"}, "production")


def test_prompt_target_identity_is_stable_without_persisting_connection_values() -> None:
    first = database_identity_sha256("supportportal", "deploy", 42, "170009")
    assert first == database_identity_sha256("supportportal", "deploy", 42, "170009")
    assert first != database_identity_sha256("other", "deploy", 42, "170009")
    assert "supportportal" not in first
    assert "deploy" not in first


def test_resume_prompt_state_readback_forces_read_only_connection() -> None:
    repository = MagicMock()
    repository.get_prompt_release.return_value = {"release_id": "prompt-1", "status": "active"}
    with (
        patch.dict(os.environ, {"PROMPT_RELEASE_TARGET_DSN": "postgresql://user:pass@db/app"}),
        patch(
            "backend.scripts.automation_ecs_release_pipeline.PostgresTicketRepository",
            return_value=repository,
        ) as constructor,
    ):
        result = collect_prompt_target_state("supportportal_preproduction", "prompt-1")
    assert result == {"release_id": "prompt-1", "status": "active"}
    assert "default_transaction_read_only=on" in constructor.call_args.kwargs["dsn"]
    repository.get_prompt_release.assert_called_once_with("prompt-1")
    repository.close.assert_called_once_with()
