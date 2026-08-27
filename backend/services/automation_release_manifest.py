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


def digest_from_oci_layout(path: Path) -> str:
    with tarfile.open(path, "r:*") as archive:
        member = archive.extractfile("index.json")
        if member is None:
            raise ValueError("OCI layout has no index.json")
        index = json.load(member)
    manifests = index.get("manifests") if isinstance(index, dict) else None
    if not isinstance(manifests, list) or len(manifests) != 1:
        raise ValueError("OCI layout must contain exactly one platform manifest")
    digest = str(manifests[0].get("digest") or "")
    if not _DIGEST.fullmatch(digest):
        raise ValueError("OCI layout index has an invalid manifest digest")
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
