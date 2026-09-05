from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = ROOT / "deployment/codebuild_build_automation_release.sh"
START_SCRIPT = ROOT / "deployment/start_automation_codebuild_release.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _prepare_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    for relative in (
        "deployment/codebuild_build_automation_release.sh",
        "backend/Dockerfile.automation",
        "backend/__init__.py",
        "backend/scripts/__init__.py",
        "backend/scripts/automation_release.py",
        "backend/services/__init__.py",
        "backend/services/automation_ecs_contracts.py",
        "backend/services/automation_release_manifest.py",
    ):
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "CodeBuild Test"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "codebuild@example.com"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, check=True, capture_output=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return repo, commit


def _install_fake_tools(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    _write_executable(
        fake_bin / "docker",
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json, os, pathlib, subprocess, sys
            args = sys.argv[1:]
            state = pathlib.Path(os.environ["CODEBUILD_TEST_STATE"])
            with (state / "docker.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(args) + "\\n")
            if args[:2] == ["login", "--username"]:
                sys.stdin.read()
            elif args[:2] == ["buildx", "build"]:
                print("noisy docker stdout after build")
            elif args[:2] in (["buildx", "create"], ["buildx", "use"], ["buildx", "inspect"]):
                pass
            elif args and args[0] == "run":
                module_index = args.index("-m")
                subprocess.run(
                    [os.environ["CODEBUILD_TEST_PYTHON"], *args[module_index:]],
                    cwd=os.getcwd(),
                    check=True,
                )
            else:
                raise SystemExit(2)
            """
        ),
    )
    _write_executable(
        fake_bin / "aws",
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json, os, pathlib, sys
            args = sys.argv[1:]
            state = pathlib.Path(os.environ["CODEBUILD_TEST_STATE"])
            with (state / "aws.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(args) + "\\n")
            if args[:2] == ["s3api", "get-object"]:
                destination = pathlib.Path(args[-1])
                destination.write_text(json.dumps({
                    "schema_version": "automation-codebuild-request-v1",
                    "release_id": os.environ["AUTOMATION_RELEASE_ID"],
                    "git_commit": os.environ["AUTOMATION_RELEASE_GIT_COMMIT"],
                    "prompt_release_id": os.environ["PROMPT_RELEASE_ID"],
                    "prompt_build_ref": "prompt-build-ref",
                    "prompt_content_fingerprint": "sha256:" + "f" * 64,
                }), encoding="utf-8")
            elif args[:2] == ["sts", "get-caller-identity"]:
                print("123456789012")
            elif args[:2] == ["ecr", "get-login-password"]:
                print("password")
            elif args[:2] == ["ecr", "describe-images"]:
                tag = args[args.index("--image-ids") + 1].split("=", 1)[1]
                digit = {"api": "1", "route": "2", "worker": "3"}[tag.split("-", 1)[0]]
                print("sha256:" + digit * 64)
            elif args[:2] == ["s3api", "put-object"]:
                print("version-1")
            else:
                print("unexpected aws invocation", args, file=sys.stderr)
                raise SystemExit(2)
            """
        ),
    )
    return fake_bin, state


def test_codebuild_build_emits_registry_manifest_and_publish_record(tmp_path: Path) -> None:
    repo, commit = _prepare_repo(tmp_path)
    fake_bin, state = _install_fake_tools(tmp_path)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CODEBUILD_TEST_STATE": str(state),
        "CODEBUILD_TEST_PYTHON": sys.executable,
        "CODEBUILD_SRC_DIR": str(state),
        "AUTOMATION_RELEASE_PYTHON": sys.executable,
        "AUTOMATION_RELEASE_ID": "release-42",
        "AUTOMATION_RELEASE_GIT_COMMIT": commit,
        "PROMPT_RELEASE_ID": "prompt-42",
        "AUTOMATION_RELEASE_EVIDENCE_BUCKET": "evidence-bucket",
        "AUTOMATION_RELEASE_REQUEST_BUCKET": "evidence-bucket",
        "AUTOMATION_RELEASE_REQUEST_KEY": "requests/release-42/request.json",
        "AUTOMATION_RELEASE_REQUEST_VERSION": "request-version-1",
        "CODEBUILD_BUILD_ARN": "arn:aws:codebuild:us-east-1:123456789012:build/example:1",
        "CODEBUILD_BUILD_NUMBER": "1",
    }
    result = subprocess.run(
        [str(repo / "deployment/codebuild_build_automation_release.sh")],
        cwd=repo,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = state / "release-evidence"
    manifest = json.loads((output / "release-manifest.json").read_text(encoding="utf-8"))
    record = json.loads((output / "publish-record.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "automation-release-v2"
    assert manifest["git_commit"] == commit
    assert "oci_layout" not in json.dumps(manifest)
    assert record["source_git_commit"] == commit
    assert record["target_repository"] == "supportportal/preproduction"
    assert record["components"]["worker"]["digest"] == "sha256:" + "3" * 64
    docker_calls = [
        json.loads(line)
        for line in (state / "docker.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    builds = [call for call in docker_calls if call[:2] == ["buildx", "build"]]
    assert len(builds) == 3
    assert all("linux/amd64" in call for call in builds)
    assert all("--push" in call for call in builds)
    assert all(any(value.startswith("type=registry") for value in call) for call in builds)
    release_tool_calls = [call for call in docker_calls if call and call[0] == "run"]
    assert len(release_tool_calls) == 3
    assert all("--entrypoint" in call and "python" in call for call in release_tool_calls)


def test_codebuild_trigger_is_fixed_sha_secret_free_and_does_not_deploy() -> None:
    script = START_SCRIPT.read_text(encoding="utf-8")
    assert "full 40-character SHA" in script
    assert "merge-base --is-ancestor" in script
    assert "automation-codebuild-request-v1" in script
    assert "prompt_content_fingerprint" in script
    assert "start-build" in script
    assert "batch-get-builds" in script
    assert "validate-preproduction-publish" in script
    assert 'TICKET_DB_DSN is required for source Prompt validation' in script
    assert "update-service" not in script
    assert "register-task-definition" not in script
    assert "TICKET_DB_DSN" not in script.split("environment-variables-override", 1)[1]
    assert "PROMPT_RELEASE_TARGET_DSN" not in script
