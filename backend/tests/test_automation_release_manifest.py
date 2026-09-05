from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.scripts import automation_release
from backend.services.automation_release_manifest import (
    read_manifest,
    read_preproduction_publish_record,
    validate_preproduction_publish_record,
)
from backend.services.automation_ecs_contracts import (
    PREPRODUCTION_PUBLISH_RECORD_VERSION,
    REGISTRY_RELEASE_MANIFEST_VERSION,
    SCHEMA_REVISION,
)


def _layout(path: Path, digit: str, *, architecture: str = "amd64") -> None:
    payload = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": "sha256:" + digit * 64,
                    "size": 123,
                    "platform": {"os": "linux", "architecture": architecture},
                }
            ],
        }
    ).encode()
    with tarfile.open(path, "w") as archive:
        info = tarfile.TarInfo("index.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))


def test_create_and_validate_repository_independent_manifest(tmp_path: Path) -> None:
    layouts = {}
    for role, digit in (("api", "a"), ("route", "b"), ("worker", "c")):
        path = tmp_path / f"{role}.oci.tar"
        _layout(path, digit)
        layouts[role] = path
    output = tmp_path / "release-manifest.json"
    args = [
        "create",
        "--release-id", "r20260827-54e8235",
        "--git-commit", "54e8235abcd",
        "--build-time", "2026-08-27T10:00:00Z",
        "--prompt-release-id", "prompt-42",
        "--output", str(output),
    ]
    for role, layout in layouts.items():
        args.extend(("--component", role, str(layout)))
    automation_release.run(args)

    manifest = read_manifest(output)
    assert manifest.components["api"].tag == "api-r20260827-54e8235"
    assert manifest.components["worker"].digest == "sha256:" + "c" * 64
    assert "repository" not in output.read_text(encoding="utf-8").lower()
    assert automation_release.run(["validate", "--manifest", str(output)])["ok"] is True


def test_manifest_rejects_missing_role_or_mutable_image_reference(tmp_path: Path) -> None:
    layout = tmp_path / "api.oci.tar"
    _layout(layout, "a")
    args = [
        "create", "--release-id", "r1", "--git-commit", "abcdef1",
        "--build-time", "2026-08-27T10:00:00Z", "--prompt-release-id", "p1",
        "--output", str(tmp_path / "manifest.json"), "--component", "api", str(layout),
    ]
    with pytest.raises(ValidationError, match="api, route, and worker"):
        automation_release.run(args)


def test_manifest_rejects_non_amd64_oci_layout(tmp_path: Path) -> None:
    layout = tmp_path / "api.oci.tar"
    _layout(layout, "a", architecture="arm64")
    args = [
        "create", "--release-id", "r1", "--git-commit", "abcdef1",
        "--build-time", "2026-08-27T10:00:00Z", "--prompt-release-id", "p1",
        "--output", str(tmp_path / "manifest.json"), "--component", "api", str(layout),
    ]

    with pytest.raises(ValueError, match="must be linux/amd64"):
        automation_release.run(args)


def test_create_and_validate_registry_manifest_and_publish_record(tmp_path: Path) -> None:
    commit = "a" * 40
    output = tmp_path / "release-manifest.json"
    args = [
        "create-registry",
        "--release-id", "release-42",
        "--git-commit", commit,
        "--build-time", "2026-09-06T10:00:00Z",
        "--prompt-release-id", "prompt-42",
        "--output", str(output),
    ]
    for role, digit in (("api", "1"), ("route", "2"), ("worker", "3")):
        args.extend(("--component", role, "sha256:" + digit * 64))
    automation_release.run(args)

    manifest = read_manifest(output)
    assert manifest.schema_version == REGISTRY_RELEASE_MANIFEST_VERSION
    assert manifest.artifact_kind == "registry"
    assert manifest.components["api"].oci_layout is None
    assert automation_release.run(["validate", "--manifest", str(output)])["ok"] is True

    record_path = tmp_path / "publish-record.json"
    record_path.write_text(
        json.dumps(
            {
                "schema_version": PREPRODUCTION_PUBLISH_RECORD_VERSION,
                "release_id": "release-42",
                "published_at": "2026-09-06T10:05:00Z",
                "source_git_commit": commit,
                "codebuild_build_arn": "arn:aws:codebuild:us-east-1:123456789012:build/example:1",
                "codebuild_build_number": 1,
                "registry_id": "123456789012",
                "region": "us-east-1",
                "target_repository": "supportportal/preproduction",
                "evidence_object_version": "version-1",
                "components": {
                    role: {
                        "tag": f"{role}-release-42",
                        "digest": "sha256:" + digit * 64,
                    }
                    for role, digit in (("api", "1"), ("route", "2"), ("worker", "3"))
                },
            }
        ),
        encoding="utf-8",
    )
    record = read_preproduction_publish_record(record_path)
    validate_preproduction_publish_record(manifest, record)


def test_registry_manifest_requires_full_commit_and_no_oci_layout(tmp_path: Path) -> None:
    args = [
        "create-registry",
        "--release-id", "release-42",
        "--git-commit", "abcdef1",
        "--build-time", "2026-09-06T10:00:00Z",
        "--prompt-release-id", "prompt-42",
        "--output", str(tmp_path / "release-manifest.json"),
    ]
    for role, digit in (("api", "1"), ("route", "2"), ("worker", "3")):
        args.extend(("--component", role, "sha256:" + digit * 64))

    with pytest.raises(ValidationError, match="full 40-character Git commit"):
        automation_release.run(args)


def test_publish_record_rejects_digest_mismatch(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    components = {
        role: {
            "role": role,
            "tag": f"{role}-release-42",
            "digest": "sha256:" + digit * 64,
            "platform": "linux/amd64",
        }
        for role, digit in (("api", "1"), ("route", "2"), ("worker", "3"))
    }
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": REGISTRY_RELEASE_MANIFEST_VERSION,
                "artifact_kind": "registry",
                "release_id": "release-42",
                "git_commit": "a" * 40,
                "build_time": "2026-09-06T10:00:00Z",
                "prompt_release_id": "prompt-42",
                "schema_revision": SCHEMA_REVISION,
                "platform": "linux/amd64",
                "contracts": {},
                "components": components,
            }
        ),
        encoding="utf-8",
    )
    record_path = tmp_path / "publish.json"
    published = {
        role: {"tag": value["tag"], "digest": value["digest"]}
        for role, value in components.items()
    }
    published["worker"]["digest"] = "sha256:" + "9" * 64
    record_path.write_text(
        json.dumps(
            {
                "schema_version": PREPRODUCTION_PUBLISH_RECORD_VERSION,
                "release_id": "release-42",
                "published_at": "2026-09-06T10:05:00Z",
                "source_git_commit": "a" * 40,
                "codebuild_build_arn": "arn:build",
                "codebuild_build_number": 1,
                "registry_id": "123456789012",
                "region": "us-east-1",
                "target_repository": "supportportal/preproduction",
                "evidence_object_version": "version-1",
                "components": published,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="worker component"):
        validate_preproduction_publish_record(
            read_manifest(manifest_path),
            read_preproduction_publish_record(record_path),
        )
