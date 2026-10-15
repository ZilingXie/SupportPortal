from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.scripts import automation_ecs_release_pipeline as pipeline
from backend.scripts.automation_ecs_release_pipeline import (
    DEFAULT_PREFLIGHT_TTL_SECONDS,
    PipelineState,
    assert_secret_free_argv,
    assert_evidence_secret_free,
    make_preflight_evidence,
    validate_release_source,
    validate_release_worktree,
    _pipeline_summary,
    sanitized_aws_environment,
    database_identity_sha256,
    collect_prompt_target_state,
    deploy_mode_args,
    prompt_target_dsn,
    read_preproduction_prompt_target_dsn,
    task_definition_sha256,
    validate_preflight_evidence,
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


def _published_release(release_dir: Path, commit: str, release_id: str) -> Path:
    path = _manifest(release_dir / "release-manifest.json", commit)
    manifest = json.loads(path.read_text())
    manifest["schema_version"] = "automation-release-v2"
    manifest["artifact_kind"] = "registry"
    manifest["release_id"] = release_id
    for role, item in manifest["components"].items():
        item.pop("oci_layout")
        item["tag"] = f"{role}-{release_id}"
    path.write_text(json.dumps(manifest))
    record = {
        "schema_version": "automation-preproduction-publish-v1",
        "release_id": release_id, "published_at": manifest["build_time"],
        "source_git_commit": commit,
        "codebuild_build_arn": "arn:aws:codebuild:us-east-1:123456789012:build/test:1",
        "codebuild_build_number": 1, "registry_id": "123456789012", "region": "us-east-1",
        "target_repository": "supportportal/preproduction", "evidence_object_version": "v1",
        "components": {role: {"tag": item["tag"], "digest": item["digest"]} for role, item in manifest["components"].items()},
    }
    (release_dir / "publish-record.json").write_text(json.dumps(record))
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


def _hotfix_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A repo whose origin/main does NOT contain the hotfix lineage."""
    repo = tmp_path / "hotfix-repo"
    repo.mkdir()
    _run(repo, "init", "-b", "main")
    _run(repo, "config", "user.email", "test@example.com")
    _run(repo, "config", "user.name", "Test")
    baseline = _commit(repo, "backend/app.py", "baseline\n")
    hotfix = _commit(repo, "backend/worker.py", "hotfix\n")
    _run(repo, "branch", "origin/main", baseline)  # origin/main == baseline, WITHOUT the hotfix
    _run(repo, "checkout", "--detach", hotfix)
    return repo, baseline, hotfix


def test_hotfix_baseline_passes_without_main_reachability(tmp_path: Path, monkeypatch) -> None:
    repo, baseline, hotfix = _hotfix_repo(tmp_path)
    manifest = _manifest(tmp_path / "manifest.json", hotfix)
    monkeypatch.setenv("AUTOMATION_RELEASE_HOTFIX_AUTHORIZED", hotfix)

    result = validate_release_source(
        repo=repo,
        release_commit=hotfix,
        manifest_path=manifest,
        hotfix_baseline=baseline,
    )

    assert result["status"] == "passed"
    assert result["hotfix_source"] is True
    assert result["hotfix_baseline_commit"] == baseline
    assert result["origin_main_commit"] is None
    # And the same commit is rejected by the DEFAULT (main) gate:
    _run(repo, "checkout", "--detach", hotfix)
    with pytest.raises(ValueError, match="not reachable"):
        validate_release_source(
            repo=repo,
            release_commit=hotfix,
            manifest_path=_manifest(tmp_path / "manifest2.json", hotfix),
        )


def test_hotfix_baseline_requires_matching_authorization_env(tmp_path: Path, monkeypatch) -> None:
    repo, baseline, hotfix = _hotfix_repo(tmp_path)
    manifest = _manifest(tmp_path / "manifest.json", hotfix)
    monkeypatch.delenv("AUTOMATION_RELEASE_HOTFIX_AUTHORIZED", raising=False)
    with pytest.raises(ValueError, match="AUTOMATION_RELEASE_HOTFIX_AUTHORIZED is required"):
        validate_release_source(
            repo=repo, release_commit=hotfix, manifest_path=manifest, hotfix_baseline=baseline
        )
    monkeypatch.setenv("AUTOMATION_RELEASE_HOTFIX_AUTHORIZED", baseline)
    with pytest.raises(ValueError, match="does not match the release commit"):
        validate_release_source(
            repo=repo, release_commit=hotfix, manifest_path=manifest, hotfix_baseline=baseline
        )


def test_hotfix_baseline_rejects_commit_not_descended_from_baseline(tmp_path: Path, monkeypatch) -> None:
    repo, baseline, hotfix = _hotfix_repo(tmp_path)
    _run(repo, "checkout", "--orphan", "unrelated")
    unrelated = _commit(repo, "unrelated.txt", "x\n")
    _run(repo, "checkout", "--detach", unrelated)
    monkeypatch.setenv("AUTOMATION_RELEASE_HOTFIX_AUTHORIZED", unrelated)
    with pytest.raises(ValueError, match="not descended from the pinned baseline"):
        validate_release_source(
            repo=repo,
            release_commit=unrelated,
            manifest_path=_manifest(tmp_path / "manifest.json", unrelated),
            hotfix_baseline=baseline,
        )


def test_hotfix_baseline_rejects_dirty_worktree(tmp_path: Path, monkeypatch) -> None:
    repo, baseline, hotfix = _hotfix_repo(tmp_path)
    (repo / "dirty.txt").write_text("dirty", encoding="utf-8")
    monkeypatch.setenv("AUTOMATION_RELEASE_HOTFIX_AUTHORIZED", hotfix)
    with pytest.raises(ValueError, match="clean"):
        validate_release_worktree(repo=repo, release_commit=hotfix, hotfix_baseline=baseline)


def test_hotfix_baseline_rejects_manifest_commit_mismatch(tmp_path: Path, monkeypatch) -> None:
    repo, baseline, hotfix = _hotfix_repo(tmp_path)
    monkeypatch.setenv("AUTOMATION_RELEASE_HOTFIX_AUTHORIZED", hotfix)
    with pytest.raises(ValueError, match="Manifest Git commit"):
        validate_release_source(
            repo=repo,
            release_commit=hotfix,
            manifest_path=_manifest(tmp_path / "manifest.json", baseline),
            hotfix_baseline=baseline,
        )


@pytest.mark.parametrize("hotfix_mode", [False, True])
def test_pipeline_runs_and_resumes_with_source_identity_at_every_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hotfix_mode: bool
) -> None:
    repo, baseline, release = _hotfix_repo(tmp_path)
    if not hotfix_mode:
        _run(repo, "update-ref", "refs/heads/origin/main", release)
    arguments = [
        "run", "--automation-case-engine", "legacy", "--project-root", str(repo), "--release-commit", release,
        "--prompt-release-id", "prompt-test", "--through", "production",
        "--codebuild-direct-production", "--keep-release-worktree",
    ]
    if hotfix_mode:
        arguments.extend(["--hotfix-baseline", baseline])
    monkeypatch.setenv("AUTOMATION_RELEASE_HOTFIX_AUTHORIZED", release)
    environment = {
        "DEPLOY_PRODUCTION_APPROVED": "1",
        "AUTOMATION_RELEASE_HOTFIX_AUTHORIZED": release,
        "AUTOMATION_ECS_HOTFIX_BASELINE": "f" * 40,
        "PRODUCTION_PROMPT_RELEASE_TARGET_DSN": "isolated-test-target",
    }
    monkeypatch.setattr(pipeline, "sanitized_aws_environment", lambda: dict(environment))
    monkeypatch.setattr(pipeline, "verify_aws_identity", lambda env: None)
    monkeypatch.setattr(pipeline, "check_live_case_engine", lambda engine, env: None)
    stages = []

    def stage(state, name, command, *, env, **kwargs):
        stages.append((name, command, dict(env)))
        if name == "codebuild":
            release_dir = Path(command[command.index("--output-dir") + 1])
            release_dir.mkdir(parents=True)
            manifest_path = _published_release(release_dir, release, state.path.name.removeprefix("ecs-pipeline-"))
            if hotfix_mode:
                manifest = json.loads(manifest_path.read_text())
                manifest["schema_revision"] = "automation-ecs-002"
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        state.append({"stage": name, "status": "passed", "duration_seconds": 0})

    # Git, source validation, checkpoint creation, and resume run for real;
    # only external release stages and AWS identity are replaced.
    monkeypatch.setattr(pipeline, "_run_stage", stage)
    args = pipeline.build_parser().parse_args(arguments)
    pipeline.run_pipeline(args)
    assert [item[0] for item in stages] == [
        "codebuild", "production_promotion", "production_preflight", "production_deploy",
    ]
    codebuild_command = stages[0][1]
    if hotfix_mode:
        assert codebuild_command[codebuild_command.index("--hotfix-baseline") + 1] == baseline
        assert Path(
            codebuild_command[codebuild_command.index("--prompt-code-root") + 1]
        ) == next((repo / ".deployments").glob("ecs-pipeline-*/release-worktree"))
    else:
        assert "--hotfix-baseline" not in codebuild_command
        assert "--prompt-code-root" not in codebuild_command
    for name, command, env in stages:
        if name in {"production_preflight", "production_deploy"}:
            assert env.get("AUTOMATION_ECS_HOTFIX_BASELINE") == (baseline if hotfix_mode else None)
            assert env["AUTOMATION_RELEASE_HOTFIX_AUTHORIZED"] == release
    checkpoint = next((repo / ".deployments").glob("ecs-pipeline-*/checkpoint.json"))
    identity = json.loads(checkpoint.read_text())["identity"]
    assert identity["release_commit"] == release
    assert identity["prompt_release_id"] == "prompt-test"
    assert identity.get("hotfix_baseline") == (baseline if hotfix_mode else None)
    assert identity["mode"]["codebuild_direct_production"] is True

    stages.clear()
    args.resume = True
    pipeline.run_pipeline(args)
    assert [item[0] for item in stages] == [
        "production_promotion", "production_preflight", "production_deploy",
    ]
    if hotfix_mode:
        stages.clear()
        args.hotfix_baseline = release
        with pytest.raises(ValueError, match="checkpoint identity"):
            pipeline.run_pipeline(args)
        assert stages == []


@pytest.mark.parametrize("authorized", [None, "b" * 40])
def test_pipeline_rejects_hotfix_authorization_before_creating_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorized: str | None
) -> None:
    repo, baseline, release = _hotfix_repo(tmp_path)
    args = pipeline.build_parser().parse_args([
        "run", "--automation-case-engine", "legacy", "--project-root", str(repo), "--release-commit", release,
        "--prompt-release-id", "prompt-test", "--through", "production",
        "--codebuild-direct-production", "--hotfix-baseline", baseline,
    ])
    if authorized is None:
        monkeypatch.delenv("AUTOMATION_RELEASE_HOTFIX_AUTHORIZED", raising=False)
    else:
        monkeypatch.setenv("AUTOMATION_RELEASE_HOTFIX_AUTHORIZED", authorized)
    monkeypatch.setattr(pipeline, "sanitized_aws_environment", lambda: {})
    monkeypatch.setattr(pipeline, "verify_aws_identity", lambda env: None)
    monkeypatch.setattr(pipeline, "check_live_case_engine", lambda engine, env: None)
    with pytest.raises(ValueError, match="must equal the reviewed hotfix SHA"):
        pipeline.run_pipeline(args)
    assert not (repo / ".deployments").exists()


@pytest.mark.parametrize("authorized", [None, "b" * 40])
def test_old_schema_manifest_requires_exact_hotfix_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorized: str | None
) -> None:
    manifest_path = _manifest(tmp_path / "manifest.json", "a" * 40)
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_revision"] = "automation-ecs-002"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    if authorized is None:
        monkeypatch.delenv("AUTOMATION_RELEASE_HOTFIX_AUTHORIZED", raising=False)
    else:
        monkeypatch.setenv("AUTOMATION_RELEASE_HOTFIX_AUTHORIZED", authorized)
    with pytest.raises(ValueError, match="schema 002 requires authorization"):
        pipeline.read_manifest(manifest_path)


def test_hotfix_authorization_does_not_allow_unknown_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _manifest(tmp_path / "manifest.json", "a" * 40)
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_revision"] = "automation-ecs-999"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("AUTOMATION_RELEASE_HOTFIX_AUTHORIZED", "a" * 40)
    with pytest.raises(ValueError):
        pipeline.read_manifest(manifest_path)


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

    assert_secret_free_argv(
        ["deploy", "r20260906-32"],
        {"CLIENT_ACK_MAX_OUTPUT_TOKENS": "32"},
    )
    with pytest.raises(ValueError, match="must not enter argv"):
        assert_secret_free_argv(
            ["deploy", "not-a-number"],
            {"CLIENT_ACK_MAX_OUTPUT_TOKENS": "not-a-number"},
        )


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

    safe_cache_path = {
        "family": "worker",
        "containerDefinitions": [
            {
                "name": "worker",
                "environment": [
                    {
                        "name": "BILLING_AUTOMATION_GRAPH_TOKEN_CACHE",
                        "value": "/app/.msgraph/billing-automation-token.json",
                    }
                ],
            }
        ],
    }
    assert task_definition_sha256(safe_cache_path).startswith("sha256:")
    safe_cache_path["containerDefinitions"][0]["environment"][0]["value"] = "token-value"
    with pytest.raises(ValueError, match="plaintext secret"):
        task_definition_sha256(safe_cache_path)

    safe_token_limit = {
        "family": "api",
        "containerDefinitions": [
            {
                "name": "api",
                "environment": [
                    {"name": "CLIENT_ACK_MAX_OUTPUT_TOKENS", "value": "32"},
                ],
            }
        ],
    }
    assert task_definition_sha256(safe_token_limit).startswith("sha256:")
    safe_token_limit["containerDefinitions"][0]["environment"][0]["value"] = "secret-value"
    with pytest.raises(ValueError, match="plaintext secret"):
        task_definition_sha256(safe_token_limit)


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
    assert 'promotion_args.append("--codebuild-direct-production")' in text
    assert '"production_preflight"' in text
    assert '"--preflight-evidence"' in text
    assert '_write_json_atomic(state.path / "timings.json", _pipeline_summary(state))' in text
    assert text.index("validate_release_worktree(") < text.index(
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
    assert "--codebuild-direct-production" in result.stdout


def test_direct_production_parser_keeps_default_preproduction_target() -> None:
    parser = __import__(
        "backend.scripts.automation_ecs_release_pipeline",
        fromlist=["build_parser"],
    ).build_parser()
    args = parser.parse_args(
        ["run", "--prompt-release-id", "prompt-1", "--codebuild-direct-production"]
    )
    assert args.through == "preproduction"
    assert args.codebuild_direct_production is True


@pytest.mark.parametrize(
    ("direct_production", "message"),
    [
        (False, "--through production keeps Hermes Persona disabled"),
        (True, "CodeBuild direct Production keeps Production Hermes disabled"),
    ],
)
def test_pipeline_rejects_production_persona_before_release_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, direct_production: bool, message: str
) -> None:
    arguments = [
        "run", "--automation-case-engine", "legacy", "--project-root", str(tmp_path), "--prompt-release-id", "prompt-1",
        "--through", "production", "--hermes-persona-enabled",
    ]
    if direct_production:
        arguments.append("--codebuild-direct-production")
    args = pipeline.build_parser().parse_args(arguments)
    monkeypatch.setattr(pipeline, "sanitized_aws_environment", lambda: {})
    monkeypatch.setattr(pipeline, "verify_aws_identity", lambda env: None)
    monkeypatch.setattr(pipeline, "check_live_case_engine", lambda engine, env: None)
    git = MagicMock(side_effect=AssertionError("release source must not be resolved"))
    state = MagicMock(side_effect=AssertionError("checkpoint must not be created"))
    command = MagicMock(side_effect=AssertionError("release command must not run"))
    stage = MagicMock(side_effect=AssertionError("release stage must not run"))
    monkeypatch.setattr(pipeline, "_git", git)
    monkeypatch.setattr(pipeline, "PipelineState", state)
    monkeypatch.setattr(pipeline.subprocess, "run", command)
    monkeypatch.setattr(pipeline, "_run_stage", stage)

    with pytest.raises(ValueError, match=message):
        pipeline.run_pipeline(args)

    for operation in (git, state, command, stage):
        operation.assert_not_called()
    assert not (tmp_path / ".deployments").exists()


@pytest.mark.parametrize(
    "mode_arguments",
    [
        ["--through", "preproduction", "--hermes-persona-enabled"],
        ["--through", "production", "--hermes-case-workflow-mode", "mock"],
    ],
)
def test_pipeline_preserves_allowed_persona_and_mock_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode_arguments: list[str]
) -> None:
    args = pipeline.build_parser().parse_args(
        ["run", "--automation-case-engine", "legacy", "--project-root", str(tmp_path), "--prompt-release-id", "prompt-1", *mode_arguments]
    )
    monkeypatch.setattr(pipeline, "sanitized_aws_environment", lambda: {})
    monkeypatch.setattr(pipeline, "verify_aws_identity", lambda env: None)
    monkeypatch.setattr(pipeline, "check_live_case_engine", lambda engine, env: None)
    git = MagicMock(side_effect=RuntimeError("release source reached"))
    monkeypatch.setattr(pipeline, "_git", git)

    with pytest.raises(RuntimeError, match="release source reached"):
        pipeline.run_pipeline(args)

    git.assert_called_once()


def test_direct_production_branch_skips_preproduction_ecs_and_reuses_production_preflight() -> None:
    source = Path(__file__).resolve().parents[1] / "scripts" / "automation_ecs_release_pipeline.py"
    text = source.read_text(encoding="utf-8")
    # Slice from the preproduction branch onward so earlier top-of-function
    # guards (e.g. the knowledge stage config Preproduction-only check) do
    # not shift the anchor.
    tail = text[text.index("if not direct_production:") :]
    preproduction_branch = tail[: tail.index('if args.through == "production":')]
    production_branch = tail[tail.index('if args.through == "production":') :]
    assert '"preproduction_preflight"' in preproduction_branch
    assert '"preproduction_deploy"' in preproduction_branch
    assert 'promotion_args.append("--codebuild-direct-production")' in production_branch
    assert '"production_preflight"' in production_branch
    assert 'production_deploy.extend(["--preflight-evidence", str(production_preflight)])' in production_branch


def test_pipeline_summary_records_release_slo_and_breach(tmp_path: Path) -> None:
    state = PipelineState(tmp_path / "state")
    state.bind_identity({"release_commit": "a" * 40, "prompt_release_id": "prompt-1"})
    state.append({"stage": "codebuild", "status": "passed", "duration_seconds": 600})
    state.append({"stage": "production_deploy", "status": "passed", "duration_seconds": 301})
    summary = _pipeline_summary(state)
    assert summary["normal_release_slo_seconds"] == 900
    assert summary["target_range_seconds"] == {"min": 600, "max": 900}
    assert summary["slo_breach"] is True


def test_pipeline_uses_identical_mode_arguments_for_preflight_and_deploy() -> None:
    args = argparse.Namespace(
        bootstrap_account_schema=True,
        automation_case_engine="legacy",
        hermes_case_workflow_mode="mock",
        hermes_persona_enabled=True,
        investigation_case_files_enabled=True,
    )
    assert deploy_mode_args(args) == [
        "--bootstrap-account-schema",
        "--hermes-case-workflow-mode",
        "mock",
        "--hermes-persona-enabled",
        "--automation-case-engine",
        "legacy",
        "--investigation-case-files-enabled",
        "--enablement-workflow-mode",
        "manual",
    ]
    assert deploy_mode_args(
        argparse.Namespace(
            bootstrap_account_schema=False,
            hermes_case_workflow_mode="",
            hermes_persona_enabled=False,
            automation_case_engine="hermes",
            hermes_agent_enabled=True,
            enablement_workflow_mode="archer",
            investigation_case_files_enabled=False,
        )
    ) == [
        "--automation-case-engine",
        "hermes",
        "--hermes-agent-enabled",
        "--enablement-workflow-mode",
        "archer",
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


def test_preproduction_prompt_target_is_read_from_ssm_without_persisting_dsn() -> None:
    sentinel = "postgresql://ssm-only/sentinel"
    calls = []

    def run(argv, **kwargs):
        calls.append((list(argv), dict(kwargs["env"])))
        return subprocess.CompletedProcess(argv, 0, stdout=sentinel + "\n", stderr="")

    with patch("backend.scripts.automation_ecs_release_pipeline.subprocess.run", side_effect=run):
        value = read_preproduction_prompt_target_dsn({"AWS_PROFILE": "default"})

    assert value == sentinel
    assert calls[0][0][0:5] == ["aws", "ssm", "get-parameter", "--name", pipeline.PREPRODUCTION_PROMPT_TARGET_PARAMETER]
    assert sentinel not in calls[0][0]
    assert "--with-decryption" in calls[0][0]


def test_preproduction_prompt_target_rejects_empty_ssm_value() -> None:
    result = subprocess.CompletedProcess([], 0, stdout="None\n", stderr="")
    with patch(
        "backend.scripts.automation_ecs_release_pipeline.subprocess.run",
        return_value=result,
    ):
        with pytest.raises(ValueError, match="parameter is empty"):
            read_preproduction_prompt_target_dsn({"AWS_PROFILE": "default"})


def test_preproduction_prompt_target_propagates_ssm_denial_without_fallback() -> None:
    denial = subprocess.CalledProcessError(1, ["aws", "ssm", "get-parameter"])
    with patch(
        "backend.scripts.automation_ecs_release_pipeline.subprocess.run",
        side_effect=denial,
    ):
        with pytest.raises(subprocess.CalledProcessError):
            read_preproduction_prompt_target_dsn({"AWS_PROFILE": "default"})


@pytest.mark.parametrize("provider_type,expected", [("login", True), ("shared-credentials-file", False)])
def test_release_identity_requires_refreshable_login_provider(provider_type: str, expected: bool) -> None:
    identity = json.dumps({"Account": pipeline.ACCOUNT_ID, "Arn": pipeline.AWS_USER_ARN})
    provider = f"access_key : ****************ABCD : {provider_type} :\n"
    responses = [
        subprocess.CompletedProcess([], 0, stdout=identity, stderr=""),
        subprocess.CompletedProcess([], 0, stdout=provider, stderr=""),
    ]
    with patch(
        "backend.scripts.automation_ecs_release_pipeline.subprocess.run",
        side_effect=responses,
    ):
        if expected:
            assert pipeline.verify_aws_identity({"AWS_PROFILE": "default"})["account_id"] == pipeline.ACCOUNT_ID
        else:
            with pytest.raises(ValueError, match="refreshable login profile"):
                pipeline.verify_aws_identity({"AWS_PROFILE": "default"})


def test_pipeline_rejects_case_files_in_production() -> None:
    with pytest.raises(ValueError, match="only be enabled in Preproduction"):
        deploy_mode_args(argparse.Namespace(
            bootstrap_account_schema=False,
            hermes_case_workflow_mode="disabled",
            hermes_persona_enabled=False,
            automation_case_engine="legacy",
            hermes_agent_enabled=False,
            enablement_workflow_mode="manual",
            through="production",
            investigation_case_files_enabled=True,
        ))


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


# ---------------------------------------------------------------------------
# Stage-6 knowledge staged enablement (p2-197 step 2)
# ---------------------------------------------------------------------------


def _knowledge_stage_config_file(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "knowledge-stage-config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_pipeline_rejects_knowledge_stage_config_for_production(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stage config is a Preproduction-only entry point; a
    Production-through run fails before any release work starts."""
    stage = _knowledge_stage_config_file(tmp_path, {"master": "0"})
    args = pipeline.build_parser().parse_args(
        [
            "run", "--automation-case-engine", "legacy", "--project-root", str(tmp_path), "--prompt-release-id", "prompt-1",
            "--through", "production", "--knowledge-stage-config", str(stage),
        ]
    )
    monkeypatch.setattr(pipeline, "sanitized_aws_environment", lambda: {})
    monkeypatch.setattr(pipeline, "verify_aws_identity", lambda env: None)
    monkeypatch.setattr(pipeline, "check_live_case_engine", lambda engine, env: None)
    git = MagicMock(side_effect=AssertionError("release source must not be resolved"))
    state = MagicMock(side_effect=AssertionError("checkpoint must not be created"))
    monkeypatch.setattr(pipeline, "_git", git)
    monkeypatch.setattr(pipeline, "PipelineState", state)

    with pytest.raises(ValueError, match="Preproduction-only"):
        pipeline.run_pipeline(args)

    git.assert_not_called()
    state.assert_not_called()
    assert not (tmp_path / ".deployments").exists()


def test_pipeline_rejects_invalid_knowledge_stage_config_before_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An illegal stage config (a switch on while the master is off) fails
    before any release work or checkpoint state is created."""
    repo, release = _repo(tmp_path)
    stage = _knowledge_stage_config_file(
        tmp_path, {"switches": {"KNOWLEDGE_SOURCE_INTAKE_ENABLED": "1"}}
    )
    args = pipeline.build_parser().parse_args(
        [
            "run", "--automation-case-engine", "legacy", "--project-root", str(repo), "--release-commit", release,
            "--prompt-release-id", "prompt-1", "--knowledge-stage-config", str(stage),
        ]
    )
    monkeypatch.setattr(pipeline, "sanitized_aws_environment", lambda: {})
    monkeypatch.setattr(pipeline, "verify_aws_identity", lambda env: None)
    monkeypatch.setattr(pipeline, "check_live_case_engine", lambda engine, env: None)
    state = MagicMock(side_effect=AssertionError("checkpoint must not be created"))
    monkeypatch.setattr(pipeline, "PipelineState", state)

    with pytest.raises(ValueError, match="layered under"):
        pipeline.run_pipeline(args)

    state.assert_not_called()


def test_pipeline_binds_knowledge_stage_config_into_checkpoint_and_deploy_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid stage config enters the checkpoint identity (a switch change
    is a NEW release, never a silent resume mutation) and rides the
    Preproduction deploy argv via a self-contained state-dir copy."""
    repo, release = _repo(tmp_path)
    stage = _knowledge_stage_config_file(
        tmp_path,
        {
            "master": "1",
            "switches": {"KNOWLEDGE_SOURCE_INTAKE_ENABLED": "1"},
            "source_allowlist": "csd_issue:CSD-TEST-1",
        },
    )
    arguments = [
        "run", "--automation-case-engine", "legacy", "--project-root", str(repo), "--release-commit", release,
        "--prompt-release-id", "prompt-test", "--through", "preproduction",
        "--knowledge-stage-config", str(stage), "--keep-release-worktree",
    ]
    monkeypatch.setattr(
        pipeline,
        "sanitized_aws_environment",
        lambda: {"PROMPT_RELEASE_TARGET_DSN": "isolated-test-target"},
    )
    monkeypatch.setattr(pipeline, "read_preproduction_prompt_target_dsn", lambda env: "isolated-test-target")
    monkeypatch.setattr(pipeline, "verify_aws_identity", lambda env: None)
    monkeypatch.setattr(pipeline, "check_live_case_engine", lambda engine, env: None)
    stages = []
    stage_envs = {}

    def record_stage(state, name, command, *, env, **kwargs):
        stages.append((name, list(command)))
        stage_envs[name] = dict(env)
        if name == "codebuild":
            release_dir = Path(command[command.index("--output-dir") + 1])
            release_dir.mkdir(parents=True)
            _published_release(release_dir, release, state.path.name.removeprefix("ecs-pipeline-"))
        state.append({"stage": name, "status": "passed", "duration_seconds": 0})

    monkeypatch.setattr(pipeline, "_run_stage", record_stage)
    args = pipeline.build_parser().parse_args(arguments)
    pipeline.run_pipeline(args)

    assert [item[0] for item in stages] == [
        "codebuild", "preproduction_preflight", "preproduction_deploy",
    ]
    assert stage_envs["preproduction_preflight"]["PROMPT_RELEASE_TARGET_DSN"] == "isolated-test-target"
    assert stage_envs["preproduction_deploy"]["PROMPT_RELEASE_TARGET_DSN"] == "isolated-test-target"
    state_dir = next((repo / ".deployments").glob("ecs-pipeline-*"))
    assert "isolated-test-target" not in (state_dir / "checkpoint.json").read_text(encoding="utf-8")
    expected_config = {
        "master": "1",
        "switches": {
            "KNOWLEDGE_SOURCE_INTAKE_ENABLED": "1",
            "KNOWLEDGE_SUMMARY_REVIEW_ENABLED": "0",
            "KNOWLEDGE_AI_EXECUTION_ENABLED": "0",
            "KNOWLEDGE_DUALWRITE_WORKER_ENABLED": "0",
            "KNOWLEDGE_AGENT_MEMORY_DELIVERY_ENABLED": "0",
            "KNOWLEDGE_WEKNORA_DELIVERY_ENABLED": "0",
            "KNOWLEDGE_SLACK_NOTIFY_ENABLED": "0",
        },
        "source_allowlist": "csd_issue:CSD-TEST-1",
    }
    checkpoint = json.loads((state_dir / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["identity"]["mode"]["knowledge_stage"] == expected_config
    state_copy = state_dir / "knowledge-stage-config.json"
    assert json.loads(state_copy.read_text(encoding="utf-8")) == expected_config
    for name, command in stages:
        if name in {"preproduction_preflight", "preproduction_deploy"}:
            assert command[command.index("--knowledge-stage-config") + 1] == str(state_copy)
    assert not any("--knowledge-stage-config" in command for command in [stages[0][1]])

    # Resume with the same config reuses the checkpoint; a changed config is
    # an identity mismatch, never a silent resume mutation.
    stages.clear()
    args.resume = True
    pipeline.run_pipeline(args)
    assert [item[0] for item in stages] == ["preproduction_preflight", "preproduction_deploy"]

    stage.write_text(json.dumps({"master": "0"}), encoding="utf-8")
    stages.clear()
    args.resume = True
    with pytest.raises(ValueError, match="checkpoint identity"):
        pipeline.run_pipeline(args)
    assert stages == []

    # A changed stage gets a NEW immutable image tag, not an archived checkpoint.
    args.resume = False
    args.release_id = state_dir.name.removeprefix("ecs-pipeline-") + "-layer3"
    pipeline.run_pipeline(args)
    assert stages[0][0] == "codebuild"
    assert stages[0][1][stages[0][1].index("--release-id") + 1] == args.release_id
    stages.clear()
    with pytest.raises(ValueError, match="state already exists"):
        pipeline.run_pipeline(args)
    assert stages == []
    args.resume = True
    new_state = repo / ".deployments" / f"ecs-pipeline-{args.release_id}"
    (new_state / "release/publish-record.json").write_text("{}")
    with pytest.raises(ValueError):
        pipeline.run_pipeline(args)
    assert stages == []
