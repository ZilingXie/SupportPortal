"""Production-only Automation runtime.

The production image intentionally uses this entrypoint instead of the
staging/preproduction runtime.  It has no rerun/reset routes or request
models; production executions always require an explicit comment visibility.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from backend.services.automation_contracts import (
    AutomationEnvironment,
    AutomationExecutionRequest,
    environment_from_env,
    policy_for,
    validate_ticket_policy,
)
from backend.services.automation_execution_store import AutomationExecutionStore
from backend.services.automation_side_effects import SideEffectError, execute_side_effects
from backend.services.route_client import RouteServiceError, call_route


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_app() -> FastAPI:
    environment = environment_from_env(AutomationEnvironment.PRODUCTION)
    if environment != AutomationEnvironment.PRODUCTION:
        raise RuntimeError("production runtime requires AUTOMATION_ENVIRONMENT=production")
    policy = policy_for(environment)
    store = AutomationExecutionStore(environment=environment.value)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        store.ensure_schema()
        yield

    app = FastAPI(
        title="SupportPortal Automation production",
        version=str(os.getenv("APP_BUILD_REF") or "unknown"),
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "automation",
            "environment": environment.value,
            "capabilities": {"rerun": False, "reset": False, "zendesk": True, "ownership": True, "status": True},
            "app_build": {"ref": str(os.getenv("APP_BUILD_REF") or "unknown"), "time": str(os.getenv("APP_BUILD_TIME") or "")},
        }

    @app.get("/v1/capabilities")
    async def capabilities() -> dict[str, Any]:
        return {"environment": environment.value, "rerun": False, "reset": False, "comment_visibility": ["internal", "external"]}

    @app.post("/v1/cases")
    async def execute_case(request: AutomationExecutionRequest) -> dict[str, Any]:
        try:
            visibility = validate_ticket_policy(environment, request.zendesk_ticket_id, request.comment_visibility)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        from backend.services.automation_contracts import RouteRequest

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
            source="automation-production",
            build_ref=str(os.getenv("APP_BUILD_REF") or "unknown"),
        )
        try:
            route_result = await call_route(route_request)
            side_effects = await asyncio.to_thread(
                execute_side_effects,
                environment=environment,
                case_id=request.case_id,
                ticket_id=str(request.zendesk_ticket_id or "").strip(),
                route=route_result.route,
                reply_body=str(route_result.action_plan.get("reply_body") or ""),
                visibility=visibility,
            )
        except RouteServiceError as exc:
            raise HTTPException(status_code=502, detail=exc.code) from exc
        except SideEffectError as exc:
            raise HTTPException(status_code=502, detail=exc.code) from exc
        record = store.save({
            "request_id": request.request_id,
            "case_id": request.case_id,
            "status": "completed",
            "route_result": route_result.model_dump(mode="json"),
            "policy": {"environment": environment.value, "comment_visibility": visibility.value if visibility else None, "ownership": policy.performs_ownership, "status": policy.performs_status, "zendesk_delivery": True},
            "side_effects": side_effects,
            "created_at": _now(),
        })
        return {"status": "completed", "environment": environment.value, "execution": record}

    ui_dir = Path(__file__).resolve().parent.parent / "ui" / "automation-production"
    if ui_dir.exists():
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="automation-production-ui")
    return app


app = create_app()
