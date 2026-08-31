"""ASGI entrypoint for the asynchronous ECS Automation intake API."""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path as FilePath
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.services.automation_ecs_contracts import (
    AutomationIntakeEvent,
    ExecutionStatus,
    IntakeEventType,
    IntakeReceipt,
)
from backend.services.automation_ecs_dashboard_auth import DashboardAuthConfig
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


class DashboardLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=512)


DASHBOARD_COOKIE_NAME = "supportportal_automation_dashboard"
_ROUTE_FIELDS = (
    "route_family",
    "execution_action",
    "category",
    "subcategory",
    "classification",
    "decision",
    "automation_supported",
)
_PERSONA_FIELDS = ("persona_key", "version")
_PROVENANCE_FIELDS = (
    "service_role",
    "environment",
    "release_id",
    "git_commit",
    "build_time",
    "image_digest",
    "schema_revision",
    "prompt_release_id",
    "db_resource_id",
    "db_schema",
    "job_namespace",
)


def _selected_fields(value: Any, fields: tuple[str, ...]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {field: value[field] for field in fields if field in value}


def _safe_route(value: Any) -> dict[str, Any] | None:
    selected = _selected_fields(value, _ROUTE_FIELDS)
    if selected is None:
        return None
    return {
        key: item
        for key, item in selected.items()
        if item is None or isinstance(item, (str, bool, int, float))
    }


def _execution_summary(value: dict[str, Any]) -> dict[str, Any]:
    return {
        field: value.get(field)
        for field in (
            "execution_id",
            "zendesk_ticket_id",
            "event_id",
            "event_type",
            "status",
            "current_stage",
            "failure_stage",
            "failure_code",
            "requires_human_review",
            "created_at",
            "updated_at",
        )
    }


def _safe_intake(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    ticket = value.get("ticket") if isinstance(value.get("ticket"), dict) else {}
    comments = value.get("comment_snapshot")
    comment_summary = None
    if isinstance(comments, dict):
        comment_summary = {
            "source_updated_at": comments.get("source_updated_at"),
            "snapshot_complete": comments.get("snapshot_complete"),
            "trigger_comment_id": comments.get("trigger_comment_id"),
            "comment_count": len(comments.get("comments") or []),
        }
    return {
        "schema_version": value.get("schema_version"),
        "event_id": value.get("event_id"),
        "event_type": value.get("event_type"),
        "occurred_at": value.get("occurred_at"),
        "ticket": {
            "id": ticket.get("id"),
            "status": ticket.get("status"),
            "updated_at": ticket.get("updated_at"),
        },
        "comment_snapshot": comment_summary,
    }


def _safe_execution_detail(value: dict[str, Any]) -> dict[str, Any]:
    result = _execution_summary(value)
    result.update(
        intake=_safe_intake(value.get("intake")),
        route=_safe_route(value.get("route")),
        persona=_selected_fields(value.get("persona"), _PERSONA_FIELDS),
        outcome_present=isinstance(value.get("outcome"), dict),
        provenance=_selected_fields(value.get("provenance"), _PROVENANCE_FIELDS),
        route_provenance=_selected_fields(value.get("route_provenance"), _PROVENANCE_FIELDS),
        steps=[
            {
                field: item.get(field)
                for field in (
                    "step_id",
                    "step_name",
                    "attempt",
                    "status",
                    "worker_identity",
                    "error_code",
                    "started_at",
                    "finished_at",
                    "updated_at",
                )
            }
            for item in value.get("steps") or []
            if isinstance(item, dict)
        ],
        events=[
            {
                field: item.get(field)
                for field in ("timeline_event_id", "event_type", "created_at")
            }
            for item in value.get("events") or []
            if isinstance(item, dict)
        ],
        jobs=[
            {
                field: item.get(field)
                for field in (
                    "job_id",
                    "kind",
                    "status",
                    "attempt",
                    "claimed_by",
                    "lease_expires_at",
                    "external_started_at",
                    "created_at",
                    "updated_at",
                )
            }
            for item in value.get("jobs") or []
            if isinstance(item, dict)
        ],
        deliveries=[
            {
                field: item.get(field)
                for field in (
                    "action_id",
                    "action_type",
                    "status",
                    "attempt",
                    "error_code",
                    "created_at",
                    "updated_at",
                )
            }
            for item in value.get("deliveries") or []
            if isinstance(item, dict)
        ],
    )
    return result


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
    dashboard_auth: DashboardAuthConfig | None = None,
) -> FastAPI:
    runtime = settings or AutomationEcsSettings.from_env("api")
    coordination_store = store or create_automation_ecs_store(runtime)
    auth = dashboard_auth or DashboardAuthConfig.from_env()
    if hmac.compare_digest(auth.password, runtime.intake_shared_token) or hmac.compare_digest(
        auth.session_secret, runtime.intake_shared_token
    ):
        raise RuntimeError("dashboard credentials and intake token must be independent")

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

    def require_dashboard_session(request: Request) -> None:
        token = str(request.cookies.get(DASHBOARD_COOKIE_NAME) or "")
        if not auth.verify_session(token):
            raise HTTPException(status_code=401, detail="dashboard authentication required")

    def heartbeat_payload() -> tuple[dict[str, Any], list[str]]:
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
            mismatched_fields = [
                field
                for field in identity_fields
                if not isinstance(observed_provenance, dict)
                or observed_provenance.get(field) != expected_provenance.get(field)
            ]
            heartbeat["provenance_mismatches"] = mismatched_fields
            if (
                heartbeat["age_seconds"] is not None
                and heartbeat["age_seconds"] <= max_age_seconds
                and not mismatched_fields
            ):
                fresh_roles.add(role)
        missing_roles = sorted({"route", "worker"} - fresh_roles)
        active_workers = []
        for role in ("route", "worker"):
            candidates = [item for item in heartbeats if item.get("role") == role]
            if candidates:
                active_workers.append(
                    max(
                        candidates,
                        key=lambda item: _parse_timestamp(item.get("last_seen_at"))
                        or datetime.min.replace(tzinfo=timezone.utc),
                    )
                )
        return {
            "api": {
                "role": "api",
                "last_seen_at": now.isoformat(),
                "age_seconds": 0,
                "provenance_mismatches": [],
                "provenance": expected_provenance,
            },
            "workers": heartbeats,
            "active_workers": active_workers,
            "max_age_seconds": max_age_seconds,
        }, missing_roles

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
        runtime_status, missing_roles = heartbeat_payload()
        if missing_roles:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "reason": "worker_heartbeat_missing_or_stale",
                    "missing_roles": missing_roles,
                    "provenance": runtime_status["api"]["provenance"],
                    "worker_heartbeats": runtime_status["workers"],
                },
            )
        return {
            "status": "ok",
            "provenance": runtime_status["api"]["provenance"],
            "worker_heartbeats": runtime_status["workers"],
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

    @app.post(f"{base}/dashboard/auth/login")
    async def dashboard_login(credentials: DashboardLoginRequest) -> JSONResponse:
        if not auth.verify_credentials(credentials.username, credentials.password):
            raise HTTPException(status_code=401, detail="invalid dashboard credentials")
        token, expires_at = auth.create_session()
        response = JSONResponse(
            content={"authenticated": True, "expires_at": expires_at},
            headers={"Cache-Control": "no-store"},
        )
        response.set_cookie(
            DASHBOARD_COOKIE_NAME,
            token,
            max_age=auth.session_ttl_seconds,
            httponly=True,
            secure=True,
            samesite="strict",
            path=f"{base}/",
        )
        return response

    @app.get(f"{base}/dashboard/auth/session", dependencies=[Depends(require_dashboard_session)])
    async def dashboard_session() -> JSONResponse:
        return JSONResponse(
            content={"authenticated": True},
            headers={"Cache-Control": "no-store"},
        )

    @app.post(f"{base}/dashboard/auth/logout")
    async def dashboard_logout() -> JSONResponse:
        response = JSONResponse(
            content={"authenticated": False},
            headers={"Cache-Control": "no-store"},
        )
        response.delete_cookie(
            DASHBOARD_COOKIE_NAME,
            path=f"{base}/",
            secure=True,
            httponly=True,
            samesite="strict",
        )
        return response

    @app.get(
        f"{base}/dashboard/api/executions",
        dependencies=[Depends(require_dashboard_session)],
    )
    async def dashboard_executions(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=25, ge=1, le=100),
        zendesk_ticket_id: str | None = Query(default=None, pattern=r"^\d{1,128}$"),
        execution_id: str | None = Query(default=None, min_length=1, max_length=160),
        status: ExecutionStatus | None = None,
        event_type: IntakeEventType | None = None,
    ) -> JSONResponse:
        rows, total = coordination_store.list_executions(
            offset=(page - 1) * page_size,
            limit=page_size,
            zendesk_ticket_id=zendesk_ticket_id,
            execution_id=execution_id,
            status=status.value if status else None,
            event_type=event_type.value if event_type else None,
        )
        return JSONResponse(
            content=jsonable_encoder(
                {
                    "items": [_execution_summary(item) for item in rows],
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "pages": (total + page_size - 1) // page_size,
                }
            ),
            headers={"Cache-Control": "no-store"},
        )

    @app.get(
        f"{base}/dashboard/api/executions/{{execution_id}}",
        dependencies=[Depends(require_dashboard_session)],
    )
    async def dashboard_execution(execution_id: str) -> JSONResponse:
        value = coordination_store.get_execution(execution_id)
        if value is None:
            raise HTTPException(status_code=404, detail="execution not found")
        return JSONResponse(
            content=jsonable_encoder(_safe_execution_detail(value)),
            headers={"Cache-Control": "no-store"},
        )

    @app.get(
        f"{base}/dashboard/api/runtime",
        dependencies=[Depends(require_dashboard_session)],
    )
    async def dashboard_runtime() -> JSONResponse:
        runtime_status, missing_roles = heartbeat_payload()
        runtime_status["ready"] = not missing_roles
        runtime_status["missing_roles"] = missing_roles
        return JSONResponse(
            content=jsonable_encoder(runtime_status),
            headers={"Cache-Control": "no-store"},
        )

    dashboard_dir = FilePath(__file__).resolve().parents[1] / "ui" / "automation-ecs-production"
    if not dashboard_dir.is_dir():
        raise RuntimeError(f"ECS dashboard assets are missing: {dashboard_dir}")
    app.mount(
        f"{base}/",
        StaticFiles(directory=dashboard_dir, html=True),
        name="automation-ecs-production-ui",
    )

    return app
