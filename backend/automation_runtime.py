"""Environment-specific Automation runtime entrypoint.

This app owns environment policy and the execution ledger. AI routing is
delegated to the matching Route service. Zendesk delivery remains behind an
explicit adapter boundary so staging cannot accidentally acquire credentials.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles

from backend.services.automation_contracts import (
    AutomationEnvironment,
    AutomationExecutionRequest,
    RouteRequest,
    environment_from_env,
    policy_for,
    resolve_comment_visibility,
    validate_ticket_policy,
)
from backend.services.automation_rerun_contracts import RerunRequest
from backend.services.automation_execution_store import AutomationExecutionStore
from backend.services.automation_side_effects import SideEffectError, execute_side_effects
from backend.services.route_client import RouteServiceError, call_route


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_app() -> FastAPI:
    environment = environment_from_env()
    policy = policy_for(environment)
    store = AutomationExecutionStore(environment=environment.value)
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        store.ensure_schema()
        yield

    app = FastAPI(
        title=f"SupportPortal Automation {environment.value}",
        version=str(os.getenv("APP_BUILD_REF") or "unknown"),
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "automation",
            "environment": environment.value,
            "capabilities": {
                "rerun": policy.allow_rerun,
                "reset": policy.allow_reset,
                "zendesk": policy.writes_zendesk,
                "ownership": policy.performs_ownership,
                "status": policy.performs_status,
            },
            "app_build": {
                "ref": str(os.getenv("APP_BUILD_REF") or "unknown"),
                "time": str(os.getenv("APP_BUILD_TIME") or ""),
            },
        }

    @app.get("/v1/capabilities")
    async def capabilities() -> dict[str, Any]:
        return {
            "environment": environment.value,
            "rerun": policy.allow_rerun,
            "reset": policy.allow_reset,
            "comment_visibility": (
                [policy.forced_visibility.value]
                if policy.forced_visibility is not None
                else (["internal", "external"] if policy.requires_visibility else [])
            ),
        }

    @app.post("/v1/cases")
    async def execute_case(request: AutomationExecutionRequest) -> dict[str, Any]:
        try:
            visibility = validate_ticket_policy(
                environment,
                request.zendesk_ticket_id,
                request.comment_visibility,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        route_request = RouteRequest(
            request_id=request.request_id,
            idempotency_key=f"{environment.value}:route:{request.request_id}",
            expected_environment=environment,
            case_id=request.case_id,
            subject=request.subject,
            question=request.question,
            customer_email=request.customer_email,
            customer_name=request.customer_name,
            ticket_context=request.ticket_context,
            source=f"automation-{environment.value}",
            build_ref=str(os.getenv("APP_BUILD_REF") or "unknown"),
        )
        try:
            route_result = await call_route(route_request)
        except RouteServiceError as exc:
            raise HTTPException(status_code=502, detail=exc.code) from exc
        side_effects: list[dict[str, Any]] = []
        execution_status = "prepared"
        if policy.writes_zendesk:
            try:
                side_effects = await asyncio.to_thread(
                    execute_side_effects,
                    environment=environment,
                    case_id=request.case_id,
                    ticket_id=str(request.zendesk_ticket_id or "").strip(),
                    route=route_result.route,
                    reply_body=str(route_result.action_plan.get("reply_body") or ""),
                    visibility=visibility,
                )
                execution_status = "completed"
            except SideEffectError as exc:
                failed = store.save(
                    {
                        "request_id": request.request_id,
                        "case_id": request.case_id,
                        "status": "failed",
                        "failure_code": exc.code,
                        "route_result": route_result.model_dump(mode="json"),
                        "policy": {"environment": environment.value, "comment_visibility": visibility.value if visibility else None},
                        "side_effects": [],
                        "created_at": _now(),
                    }
                )
                raise HTTPException(status_code=502, detail={"code": exc.code, "execution": failed}) from exc
        record = store.save(
            {
                "request_id": request.request_id,
                "case_id": request.case_id,
                "status": execution_status,
                "route_result": route_result.model_dump(mode="json"),
                "policy": {
                    "environment": environment.value,
                    "comment_visibility": visibility.value if visibility else None,
                    "ownership": policy.performs_ownership,
                    "status": policy.performs_status,
                    "zendesk_delivery": policy.writes_zendesk,
                },
                "side_effects": side_effects,
                "created_at": _now(),
            }
        )
        return {
            "status": execution_status,
            "environment": environment.value,
            "execution": record,
        }

    @app.post("/v1/reruns")
    async def rerun_case(request: RerunRequest) -> dict[str, Any]:
        if not policy.allow_rerun:
            raise HTTPException(status_code=404, detail="rerun is not available in production")
        record = store.get(request.rerun_of_execution_id)
        if record is None or record.get("case_id") != request.case_id:
            raise HTTPException(status_code=404, detail="execution not found")
        return {
            "status": "accepted",
            "environment": environment.value,
            "request_id": request.request_id,
            "rerun_of_execution_id": request.rerun_of_execution_id,
        }

    @app.post("/v1/reset")
    async def reset_case() -> dict[str, Any]:
        if not policy.allow_reset:
            raise HTTPException(status_code=404, detail="reset is not available in this environment")
        return {"status": "accepted", "environment": environment.value}

    ui_dir = Path(__file__).resolve().parent.parent / "ui" / f"automation-{environment.value}"
    if ui_dir.exists():
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name=f"automation-{environment.value}-ui")

    return app


app = create_app()
