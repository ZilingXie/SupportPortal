"""Production-only Automation runtime.

The production image intentionally uses this entrypoint instead of the
staging/preproduction runtime.  It has no rerun/reset routes or request
models; production executions always require an explicit comment visibility.
"""

from __future__ import annotations

import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles

from backend.services.automation_contracts import (
    AutomationEnvironment,
    AutomationLoginRequest,
    CommentVisibility,
    ExecutionReconcileRequest,
    environment_from_env,
    policy_for,
    validate_ticket_policy,
    runtime_resource_identity,
    verify_admin_login,
)
from backend.services.automation_intake_compat import parse_automation_execution_request
from backend.services.automation_execution_store import AutomationExecutionStore
from backend.services.automation_delivery_ledger import merge_delivery_ledger, pending_delivery_ledger
from backend.services.automation_delivery_reconciliation import (
    DeliveryReadbackNotConfirmed,
    readback_error_code,
    verify_delivery_operation,
)
from backend.services.automation_side_effects import SideEffectError, execute_side_effects
from backend.services.route_client import RouteServiceError, call_route


_TICKET_REPOSITORY: Any = None


def _ticket_repository() -> Any:
    """Lazy account-case repository bound to the split production schema."""
    global _TICKET_REPOSITORY
    if _TICKET_REPOSITORY is None:
        from backend.repositories.ticket_repository import PostgresTicketRepository

        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        if not dsn:
            raise RuntimeError("TICKET_DB_DSN is required for the parity pipeline")
        _TICKET_REPOSITORY = PostgresTicketRepository(
            dsn=dsn,
            schema=(os.getenv("TICKET_DB_SCHEMA") or "supportportal_production").strip()
            or "supportportal_production",
            application_name="supportportal-automation-production",
        )
    return _TICKET_REPOSITORY


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_execution_token(
    request_token: str | None = Header(default=None, alias="X-N8n-Request-Token"),
) -> None:
    expected = str(os.getenv("n8n_request_token") or "").strip()
    if not expected or not hmac.compare_digest(str(request_token or ""), expected):
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

    @app.post("/v1/auth/login")
    async def admin_login(request: AutomationLoginRequest) -> dict[str, Any]:
        if not verify_admin_login(request.email, request.password):
            raise HTTPException(status_code=401, detail="invalid admin credentials")
        token = str(os.getenv("n8n_request_token") or "").strip()
        if not token:
            raise HTTPException(status_code=503, detail="automation execution token is not configured")
        return {"environment": environment.value, "execution_token": token}

    @app.post("/v1/cases", dependencies=[Depends(_require_execution_token)])
    async def execute_case(http_request: Request) -> dict[str, Any]:
        request = await parse_automation_execution_request(http_request)
        request_record = request.model_dump(mode="json")
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
            failed = store.save({"request_id": request.request_id, "case_id": request.case_id, "status": "failed", "failure_code": exc.code, "policy": {"environment": environment.value, "comment_visibility": visibility.value if visibility else None}, "request": request_record, "created_at": _now()})
            raise HTTPException(status_code=502, detail={"code": exc.code, "execution": failed}) from exc
        # Old-stack /production semantics (p2-109 Phase B): classification from
        # the bound Route service, then the account-intake pipeline (field
        # extraction, internal email handoff, reply jobs, ownership gate,
        # human-queue escalation, Engineer Case) runs against the split
        # production schema. No immediate Zendesk comment/status side effects.
        from backend.services.automation_account_intake import run_production_account_intake

        route_payload = dict(route_result.route or {})
        route_decision = {
            "scope_label": route_payload.get("scope_label"),
            "route_family": route_payload.get("route_family"),
            "execution_action": route_payload.get("execution_action") or route_payload.get("route"),
            "reason": route_payload.get("reason"),
            "confidence": route_payload.get("confidence"),
            "matched_signals": route_payload.get("matched_signals") or [],
            "semantic_intent": route_payload.get("semantic_intent"),
            "automation_eligibility": route_payload.get("automation_eligibility"),
            "policy_decision": route_payload.get("policy_decision"),
            "not_automated_reason": route_payload.get("not_automated_reason"),
            "risk_flags": route_payload.get("risk_flags") or [],
            "evidence_spans": route_payload.get("evidence_spans") or [],
            "router_source": route_payload.get("router_source"),
            "stage_attempts": route_payload.get("stage_attempts"),
        }
        repository = _ticket_repository()
        store.save({
            "request_id": request.request_id,
            "case_id": request.case_id,
            "request": request_record,
            "status": "pending",
            "route_result": route_result.model_dump(mode="json"),
            "policy": {"environment": environment.value, "comment_visibility": visibility.value if visibility else None},
            "side_effects": [],
            "created_at": _now(),
        })
        try:
            outcome = await run_production_account_intake(
                repository=repository,
                subject=request.subject,
                question=request.question,
                ticket_id=str(request.zendesk_ticket_id or request.case_id or "").strip(),
                zendesk_ticket_id=request.zendesk_ticket_id,
                customer_email=request.customer_email,
                customer_name=request.customer_name,
                source=None,
                route_decision=route_decision,
                route_classification=dict(route_payload.get("classification") or {}),
                route_prompt_snapshots=dict(route_result.prompt_snapshots or {}),
                zendesk_side_effects_enabled=str(os.getenv("AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED") or "").strip() == "1",
                case_id=request.case_id,
            )
        except Exception as exc:
            failed = store.save({"request_id": request.request_id, "case_id": request.case_id, "status": "failed", "failure_code": "automation_pipeline_error", "route_result": route_result.model_dump(mode="json"), "policy": {"environment": environment.value, "comment_visibility": visibility.value if visibility else None}, "request": request_record, "side_effects": [], "created_at": _now()})
            raise HTTPException(status_code=502, detail={"code": "automation_pipeline_error", "detail": str(exc), "execution": failed}) from exc
        response_status = str(outcome.get("response_status") or "")
        status = "human_review" if response_status == "human_review_required" else "completed"
        record = store.save({
            "request_id": request.request_id,
            "case_id": request.case_id,
            "request": request_record,
            "status": status,
            "failure_code": outcome.get("execution_reason_code"),
            "route_result": route_result.model_dump(mode="json"),
            "policy": {
                "environment": environment.value,
                "comment_visibility": visibility.value if visibility else None,
                "ownership": policy.performs_ownership,
                "zendesk_delivery": True,
                "pipeline": "account_intake_parity",
            },
            "side_effects": [],
            "intake_outcome": {
                key: outcome.get(key)
                for key in (
                    "response_status",
                    "route",
                    "automation_handler",
                    "execution_reason_code",
                    "engineer_case_id",
                    "internal_email_send_status",
                    "internal_email_send_reason",
                    "route_status",
                )
            },
            "reply_job": outcome.get("reply_job"),
            "created_at": _now(),
        })
        return {"status": status, "environment": environment.value, "execution": record}

    @app.get("/v1/executions", dependencies=[Depends(_require_execution_token)])
    async def list_executions(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=10, ge=1, le=50),
        status: str | None = None,
        case_id: str | None = None,
        route_category: str | None = None,
        route_subcategory: str | None = None,
    ) -> dict[str, Any]:
        result = store.list_executions(
            status=status,
            case_id=case_id,
            route_category=route_category,
            route_subcategory=route_subcategory,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return {
            "environment": environment.value,
            "executions": result["items"],
            "page": page,
            "page_size": page_size,
            "total": result["total"],
            "status_counts": result["status_counts"],
            "route_counts": result["route_counts"],
            "route_subcategory_counts": result["route_subcategory_counts"],
        }

    @app.get("/v1/executions/{execution_id}", dependencies=[Depends(_require_execution_token)])
    async def get_execution(execution_id: str) -> dict[str, Any]:
        record = store.get(execution_id)
        if record is None:
            raise HTTPException(status_code=404, detail="execution not found")
        return {"environment": environment.value, "execution": record}

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
