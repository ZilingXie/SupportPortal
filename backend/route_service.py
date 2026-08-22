"""Standalone Route container entrypoint.

The service owns classification and AI preparation only. It deliberately has
no Zendesk client, Automation database, rerun API, or delivery side effects.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from backend.services.account_route_pipeline import decide_account_route
from backend.services.automation_contracts import (
    CONTRACT_VERSION,
    RouteRequest,
    RouteResult,
    environment_from_env,
)
from backend.services.route_preparation import prepare_action_plan


def _expected_token() -> str:
    return str(os.getenv("ROUTE_SERVICE_TOKEN") or "").strip()


def _authorize(token_header: str | None, expected_environment: str) -> None:
    token = str(token_header or "")
    expected = _expected_token()
    if not expected or token != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid route service token")
    configured = environment_from_env()
    if expected_environment != configured.value:
        raise HTTPException(status_code=409, detail="route environment mismatch")


def create_app() -> FastAPI:
    app = FastAPI(title="SupportPortal Route Service", version=CONTRACT_VERSION)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "route",
            "environment": environment_from_env().value,
            "app_build": {
                "ref": str(os.getenv("APP_BUILD_REF") or "unknown"),
                "time": str(os.getenv("APP_BUILD_TIME") or ""),
            },
        }

    @app.post("/v1/route", response_model=RouteResult)
    async def route(
        request: RouteRequest,
        authorization: str | None = Header(default=None),
    ) -> RouteResult:
        _authorize(authorization, request.expected_environment.value)
        try:
            result = decide_account_route(
                f"{request.subject}\n\n{request.question}".strip(),
                ticket_subject=request.subject,
                ticket_context=request.ticket_context,
                require_latest=True,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail="route preparation failed") from exc
        decision = result.decision
        classification = dict(result.classification)
        route_payload = {
            "scope_label": decision.scope_label,
            "route_family": decision.route_family,
            "execution_action": decision.execution_action or decision.route,
            "reason": decision.reason,
            "confidence": decision.confidence,
            "router_source": decision.router_source,
            "classification": classification,
        }
        automation_payload = {
            "eligible": bool(classification.get("handler_binding_status") in {"active", "completed"}),
            "handler": classification.get("automation_handler"),
            "subcategory": classification.get("automation_subcategory"),
        }
        action_plan = prepare_action_plan(
            subject=request.subject,
            question=request.question,
            ticket_context=request.ticket_context,
            customer_email=request.customer_email,
            customer_name=request.customer_name,
            case_id=request.case_id,
            route=route_payload,
        )
        return RouteResult(
            request_id=request.request_id,
            idempotency_key=request.idempotency_key,
            environment=request.expected_environment,
            case_id=request.case_id,
            route=route_payload,
            automation=automation_payload,
            action_plan=action_plan,
            prompt_snapshots=dict(result.prompt_snapshots),
            build_ref=str(os.getenv("APP_BUILD_REF") or "unknown"),
        )

    return app


app = create_app()
