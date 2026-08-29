"""ASGI entrypoint for the asynchronous ECS Automation intake API."""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from backend.services.automation_ecs_contracts import AutomationIntakeEvent, IntakeReceipt
from backend.services.automation_ecs_runtime import AutomationEcsSettings
from backend.services.automation_ecs_store import (
    AutomationEcsStore,
    IntakeConflictError,
    create_automation_ecs_store,
)


class IntakeTokenMiddleware:
    """Authenticate protected paths without reading or buffering the request body."""

    def __init__(self, app: Any, *, base_path: str, token: str) -> None:
        self.app = app
        self.base_path = base_path.rstrip("/")
        self.expected = f"Bearer {token}".encode("utf-8")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = str(scope.get("path") or "")
        protected = scope.get("type") == "http" and path.startswith(f"{self.base_path}/v1/")
        if protected:
            headers = {key.lower(): value for key, value in scope.get("headers") or []}
            supplied = headers.get(b"authorization", b"")
            if not hmac.compare_digest(supplied, self.expected):
                response = JSONResponse(status_code=401, content={"detail": "invalid intake token"})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def create_app(
    *,
    settings: AutomationEcsSettings | None = None,
    store: AutomationEcsStore | None = None,
) -> FastAPI:
    runtime = settings or AutomationEcsSettings.from_env("api")
    coordination_store = store or create_automation_ecs_store(runtime)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        coordination_store.check_schema()
        yield

    app = FastAPI(
        title="SupportPortal Automation ECS API",
        version="automation-intake-v1",
        lifespan=lifespan,
    )
    app.add_middleware(
        IntakeTokenMiddleware,
        base_path=runtime.base_path,
        token=runtime.intake_shared_token,
    )
    base = runtime.base_path

    @app.get(f"{base}/health/live")
    async def live() -> dict[str, Any]:
        return {"status": "ok", "service": "automation-api", "environment": runtime.environment}

    @app.get(f"{base}/health/release")
    async def release() -> dict[str, Any]:
        return {"status": "ok", "provenance": runtime.provenance().model_dump(mode="json")}

    @app.get(f"{base}/health/ready")
    async def ready() -> dict[str, Any]:
        try:
            coordination_store.check_schema()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="coordination schema unavailable") from exc
        now = datetime.now(timezone.utc)
        heartbeats = jsonable_encoder(coordination_store.list_heartbeats())
        try:
            max_age_seconds = max(
                1.0,
                float(os.getenv("AUTOMATION_WORKER_HEARTBEAT_MAX_AGE_SECONDS") or "30"),
            )
        except ValueError:
            max_age_seconds = 30.0
        fresh_roles: set[str] = set()
        expected_provenance = runtime.provenance().model_dump(mode="json")
        identity_fields = (
            "environment",
            "release_id",
            "git_commit",
            "build_time",
            "schema_revision",
            "prompt_release_id",
            "db_resource_id",
            "db_schema",
            "job_namespace",
        )
        for heartbeat in heartbeats:
            seen = _parse_timestamp(heartbeat.get("last_seen_at"))
            heartbeat["age_seconds"] = max(0.0, (now - seen).total_seconds()) if seen else None
            role = str(heartbeat.get("role") or "")
            observed_provenance = heartbeat.get("provenance")
            mismatched_fields = (
                [
                    field
                    for field in identity_fields
                    if not isinstance(observed_provenance, dict)
                    or observed_provenance.get(field) != expected_provenance.get(field)
                ]
            )
            heartbeat["provenance_mismatches"] = mismatched_fields
            if (
                heartbeat["age_seconds"] is not None
                and heartbeat["age_seconds"] <= max_age_seconds
                and not mismatched_fields
            ):
                fresh_roles.add(role)
        missing_roles = sorted({"route", "worker"} - fresh_roles)
        if missing_roles:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "reason": "worker_heartbeat_missing_or_stale",
                    "missing_roles": missing_roles,
                    "provenance": runtime.provenance().model_dump(mode="json"),
                    "worker_heartbeats": heartbeats,
                },
            )
        return {
            "status": "ok",
            "provenance": runtime.provenance().model_dump(mode="json"),
            "worker_heartbeats": heartbeats,
        }

    @app.post(f"{base}/v1/intake", response_model=IntakeReceipt, status_code=202)
    async def intake(event: AutomationIntakeEvent) -> IntakeReceipt:
        try:
            return coordination_store.accept_intake(event, runtime.provenance())
        except IntakeConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "event_payload_conflict", "execution_id": exc.execution_id},
            ) from exc

    @app.get(f"{base}/v1/executions/{{execution_id}}")
    async def execution(execution_id: str) -> dict[str, Any]:
        value = coordination_store.get_execution(execution_id)
        if value is None:
            raise HTTPException(status_code=404, detail="execution not found")
        return value

    @app.get(f"{base}/v1/cases/{{zendesk_ticket_id}}/executions")
    async def case_executions(zendesk_ticket_id: str) -> dict[str, Any]:
        if not zendesk_ticket_id.isdigit():
            raise HTTPException(status_code=422, detail="Zendesk ticket id must be numeric")
        return {
            "zendesk_ticket_id": zendesk_ticket_id,
            "executions": coordination_store.list_case_executions(zendesk_ticket_id),
        }

    return app
