"""Environment and provenance settings shared by ECS Automation roles."""

from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass
from typing import Literal

from backend.services.automation_ecs_contracts import (
    EXECUTION_CONTRACT_VERSION,
    HEARTBEAT_CONTRACT_VERSION,
    INTAKE_CONTRACT_VERSION,
    PROCESSING_CONTRACT_VERSION,
    ROUTE_CONTRACT_VERSION,
    RuntimeProvenance,
)


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


@dataclass(frozen=True)
class AutomationEcsSettings:
    environment: Literal["preproduction", "production"]
    service_role: Literal["api", "route", "worker", "bootstrap"]
    base_path: str
    intake_shared_token: str
    db_dsn: str
    migration_dsn: str
    db_resource_id: str
    db_schema: str
    job_namespace: str
    runtime_identity: str
    release_id: str
    git_commit: str
    image_digest: str
    build_time: str
    prompt_release_id: str
    allow_memory: bool

    @classmethod
    def from_env(
        cls,
        service_role: Literal["api", "route", "worker", "bootstrap"],
    ) -> "AutomationEcsSettings":
        environment = str(os.getenv("AUTOMATION_ENVIRONMENT") or "").strip().lower()
        if environment not in {"preproduction", "production"}:
            raise RuntimeError("ECS Automation requires preproduction or production environment")
        schema = str(os.getenv("AUTOMATION_DB_SCHEMA") or "").strip()
        if not _IDENTIFIER_RE.fullmatch(schema):
            raise RuntimeError("AUTOMATION_DB_SCHEMA must be a PostgreSQL identifier")
        allow_memory = str(os.getenv("AUTOMATION_RUNTIME_ALLOW_MEMORY") or "").strip() == "1"
        dsn = str(os.getenv("AUTOMATION_DB_DSN") or "").strip()
        if not dsn and not allow_memory:
            raise RuntimeError("AUTOMATION_DB_DSN is required")
        migration_dsn = str(os.getenv("AUTOMATION_DB_MIGRATION_DSN") or "").strip()
        if service_role == "bootstrap" and not migration_dsn and not allow_memory:
            raise RuntimeError("AUTOMATION_DB_MIGRATION_DSN is required for bootstrap")
        token = str(os.getenv("AUTOMATION_INTAKE_SHARED_TOKEN") or "").strip()
        if service_role == "api" and not token:
            raise RuntimeError("AUTOMATION_INTAKE_SHARED_TOKEN is required")
        expected_base_path = f"/automation/{environment}"
        configured_base_path = str(os.getenv("AUTOMATION_BASE_PATH") or expected_base_path).strip()
        if configured_base_path != expected_base_path:
            raise RuntimeError("AUTOMATION_BASE_PATH must match AUTOMATION_ENVIRONMENT")
        db_resource_id = str(os.getenv("AUTOMATION_DB_RESOURCE_ID") or "").strip()
        job_namespace = str(os.getenv("AUTOMATION_JOB_NAMESPACE") or "").strip()
        if not db_resource_id or not job_namespace:
            raise RuntimeError("AUTOMATION_DB_RESOURCE_ID and AUTOMATION_JOB_NAMESPACE are required")
        if environment not in schema.lower():
            raise RuntimeError("AUTOMATION_DB_SCHEMA must identify the configured environment")
        if environment not in job_namespace.lower():
            raise RuntimeError("AUTOMATION_JOB_NAMESPACE must identify the configured environment")
        return cls(
            environment=environment,  # type: ignore[arg-type]
            service_role=service_role,
            base_path=expected_base_path,
            intake_shared_token=token,
            db_dsn=dsn,
            migration_dsn=migration_dsn,
            db_resource_id=db_resource_id,
            db_schema=schema,
            job_namespace=job_namespace,
            runtime_identity=str(os.getenv("AUTOMATION_RUNTIME_IDENTITY") or socket.gethostname()).strip(),
            release_id=str(os.getenv("AUTOMATION_RELEASE_ID") or "unknown").strip(),
            git_commit=str(os.getenv("APP_BUILD_REF") or "unknown").strip(),
            image_digest=str(os.getenv("AUTOMATION_IMAGE_DIGEST") or "unknown").strip(),
            build_time=str(os.getenv("APP_BUILD_TIME") or "").strip(),
            prompt_release_id=str(os.getenv("PROMPT_RELEASE_ID") or "unknown").strip(),
            allow_memory=allow_memory,
        )

    def provenance(self) -> RuntimeProvenance:
        return RuntimeProvenance(
            environment=self.environment,
            service_role=self.service_role,
            runtime_identity=self.runtime_identity,
            release_id=self.release_id,
            git_commit=self.git_commit,
            image_digest=self.image_digest,
            build_time=self.build_time,
            prompt_release_id=self.prompt_release_id,
            db_resource_id=self.db_resource_id,
            db_schema=self.db_schema,
            job_namespace=self.job_namespace,
            contracts={
                "intake": INTAKE_CONTRACT_VERSION,
                "route": ROUTE_CONTRACT_VERSION,
                "processing": PROCESSING_CONTRACT_VERSION,
                "execution": EXECUTION_CONTRACT_VERSION,
                "heartbeat": HEARTBEAT_CONTRACT_VERSION,
            },
        )
