from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from backend.services.account_admin import route_execution_from_decision
from backend.services.account_billing_handlers import account_billing_metadata
from backend.services.account_route_pipeline import (
    ACCOUNT_ROUTE_PIPELINE_VERSION,
    AccountRouteResult,
    decide_account_route,
)
from backend.services.automation_routing import automation_metadata
from backend.services.support_router import decide_support_route


@dataclass(frozen=True)
class AccountCaseReroute:
    account_case: dict[str, Any]
    route_execution: dict[str, Any]
    previous_pipeline_version: str | None
    changed: bool


def reroute_account_case(
    account_case: dict[str, Any],
    *,
    canonical_ticket: dict[str, Any] | None = None,
    route_agent: Callable[..., AccountRouteResult] = decide_account_route,
    created_at: str | None = None,
) -> AccountCaseReroute:
    current = dict(account_case)
    title = str(current.get("title") or "").strip()
    question = str(current.get("question") or "").strip()
    ticket_messages = [
        dict(message)
        for message in list((canonical_ticket or {}).get("messages") or [])
        if isinstance(message, dict)
    ]
    customer_messages = [
        message
        for message in ticket_messages
        if str(message.get("role") or "").strip().lower() in {"customer", "user"}
        and str(message.get("content") or "").strip()
    ]
    latest_customer_message = str((customer_messages[-1] if customer_messages else {}).get("content") or "").strip()
    route_input = latest_customer_message or "\n\n".join(part for part in (title, question) if part)
    if not route_input:
        raise ValueError("account case has no title or question to reroute")

    result = route_agent(
        route_input,
        ticket_subject=title,
        ticket_context=ticket_messages or ([{"role": "customer", "content": question}] if question else []),
        latest_assistant_message=next(
            (
                message
                for message in reversed(ticket_messages)
                if str(message.get("role") or "").strip().lower() == "assistant"
            ),
            None,
        ),
        current_ticket_status=str((canonical_ticket or {}).get("status") or ""),
        legacy_router=decide_support_route,
        require_latest=True,
    )
    decision = result.decision
    classification = dict(result.classification)
    previous_classification = (
        dict(current.get("route_classification"))
        if isinstance(current.get("route_classification"), dict)
        else {}
    )
    previous_pipeline_version = str(previous_classification.get("pipeline_version") or "").strip() or None
    timestamp = created_at or datetime.now(timezone.utc).isoformat()
    action = str(decision.execution_action or decision.route or "").strip()
    account_billing_subcategory = str(
        classification.get("account_billing_subcategory") or ""
    ).strip()
    metadata = (
        account_billing_metadata(account_billing_subcategory)
        if classification.get("agora_route") == "account_billing"
        else automation_metadata(
            route_family=decision.route_family,
            execution_action=action,
        )
    )

    same_active_automation = (
        metadata["route_status"] == "automated"
        and str(current.get("route_status") or "").strip() == "automated"
        and str(current.get("subcategory") or current.get("execution_action") or "").strip() == action
        and str(current.get("automation_handler") or "").strip()
        == str(metadata.get("automation_handler") or "").strip()
    )
    if same_active_automation:
        classification["handler_binding_status"] = previous_classification.get(
            "handler_binding_status"
        ) or classification.get("handler_binding_status")
    elif metadata["route_status"] == "automated":
        classification["handler_binding_status"] = "classification_only"

    classification.update(
        classification_source="bulk_latest_reroute",
        previous_pipeline_version=previous_pipeline_version,
        rerouted_at=timestamp,
    )
    updated = dict(current)
    updated.update(
        route=action or None,
        scope_label=decision.scope_label,
        route_family=decision.route_family,
        execution_action=action or None,
        tooling_profile=decision.tooling_profile or None,
        route_reason=decision.reason,
        route_confidence=decision.confidence,
        matched_signals=list(decision.matched_signals),
        semantic_intent=decision.semantic_intent or None,
        automation_eligibility=decision.automation_eligibility or None,
        policy_decision=decision.policy_decision or None,
        not_automated_reason=decision.not_automated_reason or None,
        risk_flags=list(decision.risk_flags),
        evidence_spans=list(decision.evidence_spans),
        router_source=decision.router_source,
        route_classification=classification,
        updated_at=timestamp,
        **metadata,
    )
    if str(current.get("automation_status") or "").strip() in {"", "automation", "not_automated"}:
        updated["automation_status"] = (
            "automation" if metadata["route_status"] == "automated" else "not_automated"
        )
    compared_fields = {
        "route",
        "scope_label",
        "route_family",
        "execution_action",
        "category",
        "subcategory",
        "route_status",
        "automation_handler",
        "route_classification",
    }
    changed = any(current.get(field) != updated.get(field) for field in compared_fields)
    execution = route_execution_from_decision(
        ticket_id=str(current.get("client_ticket_id") or current.get("ticket_id") or ""),
        decision=decision,
        system_prompt=None,
        user_prompt=None,
        created_at=timestamp,
        classification=classification,
        prompt_snapshots=result.prompt_snapshots,
        stage_attempts=result.stage_attempts,
    )
    execution["trigger"] = "bulk_latest_reroute"
    execution["previous_pipeline_version"] = previous_pipeline_version
    execution["target_pipeline_version"] = ACCOUNT_ROUTE_PIPELINE_VERSION
    failed_stage = any(
        bool(attempt.failure_type)
        for attempt in result.stage_attempts.values()
    ) or bool(classification.get("stage_failure_types"))
    if failed_stage:
        execution["reroute_failed_closed"] = True
        execution["previous_valid_route"] = {
            "pipeline_version": previous_classification.get("pipeline_version"),
            "primary_label": previous_classification.get("primary_label"),
            "secondary_label": previous_classification.get("secondary_label"),
            "route_reason_code": previous_classification.get("route_reason_code"),
            "category": current.get("category"),
            "subcategory": current.get("subcategory"),
            "route_family": current.get("route_family"),
        }
    return AccountCaseReroute(
        account_case=updated,
        route_execution=execution,
        previous_pipeline_version=previous_pipeline_version,
        changed=changed,
    )
