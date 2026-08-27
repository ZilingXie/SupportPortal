from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.scripts import automation_release
from backend.services.automation_release_manifest import read_manifest


def _layout(path: Path, digit: str) -> None:
    payload = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": "sha256:" + digit * 64,
                    "size": 123,
                    "platform": {"os": "linux", "architecture": "amd64"},
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
