"""Repository-independent manifest for one immutable ECS Automation release."""

from __future__ import annotations

import json
import re
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.services.automation_ecs_contracts import (
    EXECUTION_CONTRACT_VERSION,
    HEARTBEAT_CONTRACT_VERSION,
    INTAKE_CONTRACT_VERSION,
    PROCESSING_CONTRACT_VERSION,
    RELEASE_MANIFEST_VERSION,
    ROUTE_CONTRACT_VERSION,
    SCHEMA_REVISION,
)

_RELEASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class ReleaseComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["api", "route", "worker"]
    tag: str
    digest: str
    platform: Literal["linux/amd64"] = "linux/amd64"
    oci_layout: str

    @field_validator("digest")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if not _DIGEST.fullmatch(value):
            raise ValueError("component digest must be sha256:<64 lowercase hex>")
        return value

    @model_validator(mode="after")
    def validate_tag(self) -> "ReleaseComponent":
        if self.tag != f"{self.role}-{self.tag.split('-', 1)[-1]}":
            raise ValueError("component tag must start with its role")
        if "/" in self.tag or ":" in self.tag:
            raise ValueError("component tag must not contain a repository")
        return self


class AutomationReleaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[RELEASE_MANIFEST_VERSION] = RELEASE_MANIFEST_VERSION
    release_id: str
    git_commit: str = Field(min_length=7, max_length=64)
    build_time: datetime
    prompt_release_id: str = Field(min_length=1, max_length=160)
    schema_revision: Literal[SCHEMA_REVISION] = SCHEMA_REVISION
    platform: Literal["linux/amd64"] = "linux/amd64"
    contracts: dict[str, str]
    components: dict[str, ReleaseComponent]

    @field_validator("release_id")
    @classmethod
    def validate_release_id(cls, value: str) -> str:
        if not _RELEASE_ID.fullmatch(value):
            raise ValueError("invalid release_id")
        return value

    @model_validator(mode="after")
    def validate_components(self) -> "AutomationReleaseManifest":
        if set(self.components) != {"api", "route", "worker"}:
            raise ValueError("release must contain api, route, and worker")
        for role, component in self.components.items():
            if component.role != role:
                raise ValueError("component key and role must match")
            if component.tag != f"{role}-{self.release_id}":
                raise ValueError("component tag must be <role>-<release_id>")
        return self


def contract_versions() -> dict[str, str]:
    return {
        "intake": INTAKE_CONTRACT_VERSION,
        "route": ROUTE_CONTRACT_VERSION,
        "processing": PROCESSING_CONTRACT_VERSION,
        "execution": EXECUTION_CONTRACT_VERSION,
        "heartbeat": HEARTBEAT_CONTRACT_VERSION,
    }


def _read_archive_json(archive: tarfile.TarFile, member_name: str) -> dict[str, object]:
    member = archive.extractfile(member_name)
    if member is None:
        raise ValueError(f"OCI layout has no {member_name}")
    value = json.load(member)
    if not isinstance(value, dict):
        raise ValueError(f"OCI layout {member_name} must be a JSON object")
    return value


def _blob_member_name(digest: str) -> str:
    algorithm, separator, value = digest.partition(":")
    if algorithm != "sha256" or not separator or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("OCI layout contains an invalid blob digest")
    return f"blobs/{algorithm}/{value}"


def inspect_oci_layout(path: Path) -> tuple[str, str]:
    with tarfile.open(path, "r:*") as archive:
        index = _read_archive_json(archive, "index.json")
        manifests = index.get("manifests")
        if not isinstance(manifests, list) or len(manifests) != 1:
            raise ValueError("OCI layout must contain exactly one platform manifest")
        descriptor = manifests[0]
        if not isinstance(descriptor, dict):
            raise ValueError("OCI layout manifest descriptor is invalid")
        digest = str(descriptor.get("digest") or "")
        if not _DIGEST.fullmatch(digest):
            raise ValueError("OCI layout index has an invalid manifest digest")

        platform = descriptor.get("platform")
        os_name = str(platform.get("os") or "") if isinstance(platform, dict) else ""
        architecture = (
            str(platform.get("architecture") or "") if isinstance(platform, dict) else ""
        )
        if not os_name or not architecture:
            manifest = _read_archive_json(archive, _blob_member_name(digest))
            config = manifest.get("config")
            if not isinstance(config, dict):
                raise ValueError("OCI image manifest has no config descriptor")
            config_digest = str(config.get("digest") or "")
            image_config = _read_archive_json(archive, _blob_member_name(config_digest))
            os_name = str(image_config.get("os") or "")
            architecture = str(image_config.get("architecture") or "")

    observed_platform = f"{os_name}/{architecture}"
    if observed_platform != "linux/amd64":
        raise ValueError(f"OCI layout platform must be linux/amd64, got {observed_platform}")
    return digest, observed_platform


def digest_from_oci_layout(path: Path) -> str:
    digest, _ = inspect_oci_layout(path)
    return digest


def write_manifest(manifest: AutomationReleaseManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_manifest(path: Path) -> AutomationReleaseManifest:
    return AutomationReleaseManifest.model_validate_json(path.read_text(encoding="utf-8"))
