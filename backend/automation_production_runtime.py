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

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles

from backend.services.automation_contracts import (
    AutomationEnvironment,
    AutomationExecutionRequest,
    CommentVisibility,
    ExecutionReconcileRequest,
    environment_from_env,
    policy_for,
    validate_ticket_policy,
    runtime_resource_identity,
)
from backend.services.automation_execution_store import AutomationExecutionStore
from backend.services.automation_delivery_ledger import merge_delivery_ledger, pending_delivery_ledger
from backend.services.automation_delivery_reconciliation import (
    DeliveryReadbackNotConfirmed,
    readback_error_code,
    verify_delivery_operation,
)
from backend.services.automation_side_effects import SideEffectError, execute_side_effects
from backend.services.route_client import RouteServiceError, call_route


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_execution_token(authorization: str | None = Header(default=None)) -> None:
    expected = str(os.getenv("AUTOMATION_EXECUTION_TOKEN") or "").strip()
    if not expected or str(authorization or "") != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid automation execution token")


def create_app() -> FastAPI:
    environment = environment_from_env(AutomationEnvironment.PRODUCTION)
    if environment != AutomationEnvironment.PRODUCTION:
        raise RuntimeError("production runtime requires AUTOMATION_ENVIRONMENT=production")
    policy = policy_for(environment)
    resource_identity = runtime_resource_identity(environment)
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
            "capabilities": {"rerun": False, "reset": False, "zendesk": True, "ownership": True, "status": True, "resources": resource_identity},
            "app_build": {"ref": str(os.getenv("APP_BUILD_REF") or "unknown"), "time": str(os.getenv("APP_BUILD_TIME") or "")},
        }

    @app.get("/v1/capabilities")
    async def capabilities() -> dict[str, Any]:
        return {"environment": environment.value, "rerun": False, "reset": False, "comment_visibility": ["internal", "external"], "resources": resource_identity}

    @app.post("/v1/cases", dependencies=[Depends(_require_execution_token)])
    async def execute_case(request: AutomationExecutionRequest) -> dict[str, Any]:
        try:
            visibility = validate_ticket_policy(environment, request.zendesk_ticket_id, request.comment_visibility)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        existing = store.get_by_request_id(request.request_id)
        if existing is not None:
            if existing.get("status") == "completed":
                return {"status": "completed", "environment": environment.value, "execution": existing, "idempotent_replay": True}
            raise HTTPException(status_code=409, detail={"code": "execution_requires_reconcile", "execution": existing})
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
        except RouteServiceError as exc:
            failed = store.save({"request_id": request.request_id, "case_id": request.case_id, "status": "failed", "failure_code": exc.code, "policy": {"environment": environment.value, "comment_visibility": visibility.value if visibility else None}, "created_at": _now()})
            raise HTTPException(status_code=502, detail={"code": exc.code, "execution": failed}) from exc
        preparation_status = str(route_result.action_plan.get("preparation_status") or "").strip()
        route_eligible = bool(route_result.automation.get("eligible")) and preparation_status == "prepared"
        if not route_eligible:
            status = "human_review" if preparation_status == "human_review" else "failed"
            failure_code = None if status == "human_review" else "route_preparation_failed"
            record = store.save({"request_id": request.request_id, "case_id": request.case_id, "status": status, "failure_code": failure_code, "route_result": route_result.model_dump(mode="json"), "policy": {"environment": environment.value, "comment_visibility": visibility.value if visibility else None}, "side_effects": [], "created_at": _now()})
            if status == "failed":
                raise HTTPException(status_code=502, detail={"code": failure_code, "execution": record})
            return {"status": status, "environment": environment.value, "execution": record}
        delivery_ledger = pending_delivery_ledger(
            environment=environment.value,
            request_id=request.request_id,
            ticket_id=str(request.zendesk_ticket_id or "").strip(),
            visibility=visibility,
            target_status=str(os.getenv("AUTOMATION_TARGET_TICKET_STATUS") or "").strip().lower(),
        )
        store.save({
            "request_id": request.request_id,
            "case_id": request.case_id,
            "status": "pending",
            "route_result": route_result.model_dump(mode="json"),
            "policy": {"environment": environment.value, "comment_visibility": visibility.value if visibility else None},
            "side_effects": [],
            "delivery_ledger": delivery_ledger,
            "created_at": _now(),
        })
        try:
            side_effects = await asyncio.to_thread(
                execute_side_effects,
                environment=environment,
                case_id=request.case_id,
                ticket_id=str(request.zendesk_ticket_id or "").strip(),
                route=route_result.route,
                reply_body=str(route_result.action_plan.get("reply_body") or ""),
                visibility=visibility,
                delivery_ledger=delivery_ledger,
            )
        except RouteServiceError as exc:
            raise HTTPException(status_code=502, detail=exc.code) from exc
        except SideEffectError as exc:
            failure_status = "outcome_unknown" if exc.outcome_unknown else "failed"
            failed = store.save({"request_id": request.request_id, "case_id": request.case_id, "status": failure_status, "failure_code": exc.code, "route_result": route_result.model_dump(mode="json"), "policy": {"environment": environment.value, "comment_visibility": visibility.value if visibility else None}, "side_effects": exc.completed_operations, "delivery_ledger": merge_delivery_ledger(delivery_ledger, exc.completed_operations, outcome_unknown=exc.outcome_unknown), "created_at": _now()})
            raise HTTPException(status_code=502, detail={"code": exc.code, "execution": failed}) from exc
        record = store.save({
            "request_id": request.request_id,
            "case_id": request.case_id,
            "status": "completed",
            "route_result": route_result.model_dump(mode="json"),
            "policy": {"environment": environment.value, "comment_visibility": visibility.value if visibility else None, "ownership": policy.performs_ownership, "status": policy.performs_status, "zendesk_delivery": True},
            "side_effects": side_effects,
            "delivery_ledger": merge_delivery_ledger(delivery_ledger, side_effects),
            "created_at": _now(),
        })
        return {"status": "completed", "environment": environment.value, "execution": record}

    @app.post("/v1/executions/{execution_id}/reconcile", dependencies=[Depends(_require_execution_token)])
    async def reconcile_execution(execution_id: str, request: ExecutionReconcileRequest) -> dict[str, Any]:
        record = store.get(execution_id)
        if record is None:
            raise HTTPException(status_code=404, detail="execution not found")
        if record.get("status") != "outcome_unknown":
            raise HTTPException(status_code=409, detail="execution does not require reconcile")
        operations = {str(item.get("operation")): item for item in request.operations}
        ledger = list(record.get("delivery_ledger") or [])
        known_operations = {str(item.get("operation")) for item in ledger}
        if set(operations) - known_operations:
            raise HTTPException(status_code=422, detail="reconcile contains an unknown delivery operation")
        unknown_items = {
            str(item.get("operation")): item
            for item in ledger
            if str(item.get("status")) == "outcome_unknown"
        }
        if not ledger or set(unknown_items) - set(operations):
            raise HTTPException(status_code=422, detail="readback for every unknown operation is required")
        route_result = record.get("route_result") if isinstance(record.get("route_result"), dict) else {}
        action_plan = route_result.get("action_plan") if isinstance(route_result.get("action_plan"), dict) else {}
        reply_body = str(action_plan.get("reply_body") or "").strip()
        policy_record = record.get("policy") if isinstance(record.get("policy"), dict) else {}
        try:
            visibility = CommentVisibility(str(policy_record.get("comment_visibility") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="execution policy has no valid comment visibility") from exc
        ticket_id = str(next((item.get("ticket_id") for item in ledger if item.get("ticket_id")), "") or "").strip()
        if not ticket_id:
            raise HTTPException(status_code=422, detail="delivery ledger has no ticket_id for readback")
        verified_operations: list[dict[str, Any]] = []
        try:
            for operation, ledger_item in unknown_items.items():
                verified_operations.append(
                    verify_delivery_operation(
                        operation=operation,
                        ticket_id=ticket_id,
                        ledger_item=ledger_item,
                        reply_body=reply_body,
                        visibility=visibility,
                    )
                )
        except DeliveryReadbackNotConfirmed as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"code": readback_error_code(exc)}) from exc
        updated_ledger = []
        for item in ledger:
            observed = next(
                (value for value in verified_operations if value.get("operation") == item.get("operation")),
                None,
            )
            updated = dict(item)
            if observed is not None:
                updated.update(observed)
                updated["status"] = "completed"
            updated_ledger.append(updated)
        updated = store.save({**record, "status": "completed", "delivery_ledger": updated_ledger, "reconciled": True})
        return {"status": "completed", "environment": environment.value, "execution": updated, "reconciled": True}

    @app.api_route("/{path:path}", methods=["POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    async def unknown_write_path(path: str) -> dict[str, str]:
        raise HTTPException(status_code=404, detail="not found")

    ui_dir = Path(__file__).resolve().parent.parent / "ui" / "automation-production"
    if ui_dir.exists():
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="automation-production-ui")
    return app


app = create_app()
