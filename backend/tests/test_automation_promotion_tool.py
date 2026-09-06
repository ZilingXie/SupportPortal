from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

from backend.services.automation_ecs_contracts import RELEASE_MANIFEST_VERSION, SCHEMA_REVISION
from backend.services.automation_release_manifest import contract_versions


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deployment/promote_automation_release.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _release_bundle(tmp_path: Path) -> Path:
    components = {}
    for role, digit in (("api", "1"), ("route", "2"), ("worker", "3")):
        archive_path = tmp_path / f"{role}.oci.tar"
        index = {
            "schemaVersion": 2,
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": f"sha256:{digit * 64}",
                    "size": 123,
                    "platform": {"os": "linux", "architecture": "amd64"},
                }
            ],
        }
        payload = json.dumps(index).encode()
        with tarfile.open(archive_path, "w") as archive:
            member = tarfile.TarInfo("index.json")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        components[role] = {
            "role": role,
            "tag": f"{role}-release-42",
            "digest": f"sha256:{digit * 64}",
            "platform": "linux/amd64",
            "oci_layout": archive_path.name,
        }
    manifest = tmp_path / "release-manifest.json"
    manifest.write_text(
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
                "components": components,
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _registry_release_bundle(tmp_path: Path) -> tuple[Path, Path, Path]:
    manifest = _release_bundle(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["schema_version"] = "automation-release-v2"
    payload["artifact_kind"] = "registry"
    for component in payload["components"].values():
        component.pop("oci_layout")
    manifest.write_text(json.dumps(payload), encoding="utf-8")
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
                    role: {"tag": item["tag"], "digest": item["digest"]}
                    for role, item in payload["components"].items()
                },
            }
        ),
        encoding="utf-8",
    )
    evidence = tmp_path / "preproduction-evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": "automation-ecs-deploy-evidence-v1",
                "status": "complete",
                "environment": "preproduction",
                "release_id": "release-42",
                "git_commit": "a" * 40,
                "prompt_release": {"release_id": "prompt-42"},
                "registry": {
                    "id": "123456789012",
                    "repository": "supportportal/preproduction",
                },
                "components": {
                    role: {"expected_digest": item["digest"], "runtime_verified": True}
                    for role, item in payload["components"].items()
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
        ),
        encoding="utf-8",
    )
    return manifest, publish, evidence


def _fake_tools(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    _write_executable(
        fake_bin / "aws",
        """#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
state = pathlib.Path(os.environ["PROMOTION_TEST_STATE"])
with (state / "aws.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")
if args[:2] == ["sts", "get-caller-identity"]:
    print(json.dumps({"Account":"891612554546","Arn":"arn:aws:iam::891612554546:user/Zac"}))
elif args[:2] == ["configure", "list"]:
    print("access_key : ****************TEST : login :")
elif args[:3] == ["configure", "export-credentials", "--format"]:
    print("{}")
elif args[:2] == ["ecr", "get-login-password"]:
    print("password")
elif args[:2] == ["ecr", "describe-repositories"]:
    print(os.environ.get("PROMOTION_TEST_MUTABILITY", "IMMUTABLE"))
elif args[:2] == ["ecr", "batch-get-image"]:
    image_id = args[args.index("--image-ids") + 1]
    kind, value = image_id.split("=", 1)
    if kind == "imageDigest":
        if os.environ.get("PROMOTION_TEST_SOURCE_MISMATCH") == value:
            print("sha256:" + "9" * 64)
        else:
            print(value)
        sys.exit(0)
    tag = value
    digit = {"api": "1", "route": "2", "worker": "3"}[tag.split("-", 1)[0]]
    if os.environ.get("PROMOTION_TEST_MISMATCH") == tag:
        print("sha256:" + "9" * 64)
    elif (state / tag).exists():
        print("sha256:" + digit * 64)
    else:
        print("None")
else:
    print("unexpected aws invocation", args, file=sys.stderr)
    sys.exit(1)
""",
    )
    _write_executable(
        fake_bin / "skopeo",
        """#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
state = pathlib.Path(os.environ["PROMOTION_TEST_STATE"])
with (state / "skopeo.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")
if args[:1] == ["login"]:
    sys.stdin.read()
elif args[:2] == ["copy", "--preserve-digests"]:
    tag = args[-1].rsplit(":", 1)[1]
    (state / tag).touch()
else:
    print("unexpected skopeo invocation", args, file=sys.stderr)
    sys.exit(1)
""",
    )
    _write_executable(
        fake_bin / "crane",
        """#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
state = pathlib.Path(os.environ["PROMOTION_TEST_STATE"])
with (state / "crane.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")
if args[:2] == ["auth", "login"]:
    sys.stdin.read()
elif args[:1] == ["copy"]:
    tag = args[-1].rsplit(":", 1)[1]
    (state / tag).touch()
else:
    print("unexpected crane invocation", args, file=sys.stderr)
    sys.exit(1)
""",
    )
    return fake_bin, state


def _run_direct(
    tmp_path: Path,
    *,
    existing: bool = False,
    mismatch: str = "",
    mutability: str = "IMMUTABLE",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    manifest = _release_bundle(tmp_path)
    fake_bin, state = _fake_tools(tmp_path)
    if existing:
        for role in ("api", "route", "worker"):
            (state / f"{role}-release-42").touch()
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "AUTOMATION_RELEASE_PYTHON": sys.executable,
            "PROMOTION_TEST_STATE": str(state),
            "PROMOTION_TEST_MISMATCH": mismatch,
            "PROMOTION_TEST_MUTABILITY": mutability,
            "DEPLOY_PRODUCTION_APPROVED": "1",
        }
    )
    result = subprocess.run(
        [
            str(SCRIPT),
            "--manifest",
            str(manifest),
            "--region",
            "us-east-1",
            "--registry-id",
            "123456789012",
            "--direct-production",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=environment,
    )
    return result, state


def _run_codebuild_direct(
    tmp_path: Path,
    *,
    extra_args: list[str] | None = None,
    source_mismatch: str = "",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    manifest, publish, _ = _registry_release_bundle(tmp_path)
    fake_bin, state = _fake_tools(tmp_path)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "AUTOMATION_RELEASE_PYTHON": sys.executable,
            "PROMOTION_TEST_STATE": str(state),
            "PROMOTION_TEST_SOURCE_MISMATCH": source_mismatch,
        }
    )
    args = [
        str(SCRIPT),
        "--manifest",
        str(manifest),
        "--publish-record",
        str(publish),
        "--region",
        "us-east-1",
        "--registry-id",
        "123456789012",
        "--codebuild-direct-production",
    ]
    args.extend(extra_args or [])
    result = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=environment,
    )
    return result, state


def test_promotion_copies_manifests_and_layers_and_verifies_identical_digests_without_building() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert 'SOURCE_REPOSITORY="supportportal/preproduction"' in script
    assert 'TARGET_REPOSITORY="supportportal/production"' in script
    assert "batch-get-image" in script
    assert "crane copy" in script
    assert "skopeo copy --preserve-digests" in script
    assert "--retry-times 3" in script
    assert "--retry-delay 5s" in script
    assert "--dest-precompute-digests" in script
    assert "put-image" not in script
    assert '[[ "${target_digest}" = "${expected}" ]]' in script
    assert "automation-promotion-v1" in script
    assert "--publish-record" in script
    assert "--preproduction-evidence" in script
    assert "validate-preproduction-acceptance" in script
    assert "source_publish_record_sha256" in script
    assert "preproduction_deploy_evidence_sha256" in script
    assert "docker build" not in script
    assert "buildx" not in script
    assert "normal promotion target must be supportportal/production" in script
    assert "--accepted-media-types" not in script


def test_registry_promotion_requires_and_records_preproduction_evidence(tmp_path: Path) -> None:
    manifest, publish, evidence = _registry_release_bundle(tmp_path)
    fake_bin, state = _fake_tools(tmp_path)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "AUTOMATION_RELEASE_PYTHON": sys.executable,
            "PROMOTION_TEST_STATE": str(state),
            "DEPLOY_PRODUCTION_APPROVED": "1",
        }
    )
    result = subprocess.run(
        [
            str(SCRIPT),
            "--manifest",
            str(manifest),
            "--publish-record",
            str(publish),
            "--preproduction-evidence",
            str(evidence),
            "--region",
            "us-east-1",
            "--registry-id",
            "123456789012",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=environment,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((tmp_path / "promotion-record.json").read_text(encoding="utf-8"))
    assert record["source_repository"] == "supportportal/preproduction"
    assert record["source_publish_record_sha256"].startswith("sha256:")
    assert record["preproduction_deploy_evidence_sha256"].startswith("sha256:")
    calls = [json.loads(line) for line in (state / "crane.jsonl").read_text().splitlines()]
    assert len([call for call in calls if call[:1] == ["copy"]]) == 3


def test_codebuild_direct_production_records_publish_provenance_without_acceptance(
    tmp_path: Path,
) -> None:
    result, state = _run_codebuild_direct(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((tmp_path / "promotion-record.json").read_text(encoding="utf-8"))
    assert record["promotion_mode"] == "codebuild-direct-production"
    assert record["source_repository"] == "supportportal/preproduction"
    assert record["source_publish_record_sha256"].startswith("sha256:")
    assert "preproduction_deploy_evidence_sha256" not in record
    calls = [json.loads(line) for line in (state / "crane.jsonl").read_text().splitlines()]
    assert len([call for call in calls if call[:1] == ["copy"]]) == 3


def test_codebuild_direct_production_rejects_conflicting_modes_and_deploy_evidence(
    tmp_path: Path,
) -> None:
    result, _ = _run_codebuild_direct(tmp_path, extra_args=["--direct-production"])
    assert result.returncode != 0
    assert "mutually exclusive" in result.stderr

    evidence = tmp_path / "unexpected-evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    result, _ = _run_codebuild_direct(
        tmp_path / "with-evidence",
        extra_args=["--preproduction-evidence", str(evidence)],
    )
    assert result.returncode != 0
    assert "does not accept Preproduction deploy evidence" in result.stderr


def test_codebuild_direct_production_validates_publish_record_before_registry_copy(
    tmp_path: Path,
) -> None:
    manifest, publish, _ = _registry_release_bundle(tmp_path)
    payload = json.loads(publish.read_text(encoding="utf-8"))
    payload["source_git_commit"] = "b" * 40
    publish.write_text(json.dumps(payload), encoding="utf-8")
    fake_bin, state = _fake_tools(tmp_path)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "AUTOMATION_RELEASE_PYTHON": sys.executable,
            "PROMOTION_TEST_STATE": str(state),
        }
    )
    result = subprocess.run(
        [
            str(SCRIPT),
            "--manifest",
            str(manifest),
            "--publish-record",
            str(publish),
            "--region",
            "us-east-1",
            "--registry-id",
            "123456789012",
            "--codebuild-direct-production",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=environment,
    )

    assert result.returncode != 0
    assert "Publish Record validation failed" in result.stderr
    assert not (state / "crane.jsonl").exists()


def test_codebuild_direct_production_rejects_source_digest_mismatch(tmp_path: Path) -> None:
    result, state = _run_codebuild_direct(
        tmp_path,
        source_mismatch="sha256:" + "3" * 64,
    )

    assert result.returncode != 0
    assert "worker source digest mismatch" in result.stderr
    calls_path = state / "crane.jsonl"
    copies = [] if not calls_path.exists() else [
        json.loads(line)
        for line in calls_path.read_text().splitlines()
        if json.loads(line)[:1] == ["copy"]
    ]
    assert copies == []


def test_direct_production_publishes_local_archives_and_records_explicit_source(tmp_path: Path) -> None:
    result, state = _run_direct(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((tmp_path / "promotion-record.json").read_text(encoding="utf-8"))
    assert record["source_repository"] == "local-oci"
    assert record["target_repository"] == "supportportal/production"
    calls = [json.loads(line) for line in (state / "skopeo.jsonl").read_text().splitlines()]
    copies = [call for call in calls if call[:2] == ["copy", "--preserve-digests"]]
    assert len(copies) == 3
    assert all(call[-2].startswith("oci-archive:") for call in copies)
    assert all(call[call.index("--retry-times") + 1] == "3" for call in copies)
    assert all(call[call.index("--retry-delay") + 1] == "5s" for call in copies)
    assert all("--dest-precompute-digests" in call for call in copies)
    assert all("supportportal/preproduction" not in json.dumps(call) for call in calls)
    aws_calls = [json.loads(line) for line in (state / "aws.jsonl").read_text().splitlines()]
    scoped_calls = [call for call in aws_calls if call[:1] == ["ecr"] and call[1] != "get-login-password"]
    assert scoped_calls
    assert all(call[call.index("--region") + 1] == "us-east-1" for call in scoped_calls)
    assert all(call[call.index("--registry-id") + 1] == "123456789012" for call in scoped_calls)


def test_direct_production_existing_identical_tags_are_idempotent(tmp_path: Path) -> None:
    result, state = _run_direct(tmp_path, existing=True)

    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in (state / "skopeo.jsonl").read_text().splitlines()]
    assert not any(call[:1] == ["copy"] for call in calls)


def test_direct_production_rejects_immutable_tag_digest_conflict(tmp_path: Path) -> None:
    result, _ = _run_direct(tmp_path, mismatch="worker-release-42")

    assert result.returncode != 0
    assert "immutable target tag worker-release-42" in result.stderr


def test_direct_production_rejects_mutable_target_repository(tmp_path: Path) -> None:
    result, state = _run_direct(tmp_path, mutability="MUTABLE")

    assert result.returncode != 0
    assert "Target ECR repository must use immutable tags" in result.stderr
    assert not (state / "skopeo.jsonl").exists()
