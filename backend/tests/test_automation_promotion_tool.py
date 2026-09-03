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
if args[:2] == ["ecr", "get-login-password"]:
    print("password")
elif args[:2] == ["ecr", "describe-repositories"]:
    print(os.environ.get("PROMOTION_TEST_MUTABILITY", "IMMUTABLE"))
elif args[:2] == ["ecr", "batch-get-image"]:
    image_id = args[args.index("--image-ids") + 1]
    tag = image_id.split("=", 1)[1]
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


def test_promotion_copies_manifests_and_layers_and_verifies_identical_digests_without_building() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert 'SOURCE_REPOSITORY="supportportal/preproduction"' in script
    assert 'TARGET_REPOSITORY="supportportal/production"' in script
    assert "batch-get-image" in script
    assert "crane copy" in script
    assert "skopeo copy --preserve-digests" in script
    assert "put-image" not in script
    assert '[[ "${target_digest}" = "${expected}" ]]' in script
    assert "automation-promotion-v1" in script
    assert "docker build" not in script
    assert "buildx" not in script


def test_direct_production_publishes_local_archives_and_records_explicit_source(tmp_path: Path) -> None:
    result, state = _run_direct(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((tmp_path / "promotion-record.json").read_text(encoding="utf-8"))
    assert record["source_repository"] == "local-oci"
    assert record["target_repository"] == "supportportal/production"
    calls = [json.loads(line) for line in (state / "skopeo.jsonl").read_text().splitlines()]
    copies = [call for call in calls if call[:2] == ["copy", "--preserve-digests"]]
    assert len(copies) == 3
    assert all(call[2].startswith("oci-archive:") for call in copies)
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
