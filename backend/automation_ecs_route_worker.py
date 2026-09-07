"""Durable Route/Persona worker for the ECS Automation runtime."""

from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass
from threading import Event
from typing import Any, Callable

from backend.repositories.ticket_repository import create_ticket_repository
from backend.services.account_route_pipeline import decide_account_route
from backend.services.automation_context import build_automation_context, public_messages, understanding_messages
from backend.services.automation_ecs_contracts import IntakeEventType, JobKind, RouteJobPayload
from backend.services.automation_ecs_heartbeat import JobLeaseHeartbeat, WorkerHeartbeat
from backend.services.automation_ecs_runtime import AutomationEcsSettings
from backend.services.automation_ecs_schema import check_account_runtime_schema
from backend.services.automation_ecs_store import AutomationEcsStore, create_automation_ecs_store
from backend.services.prompt_runtime import initialize_prompt_runtime

LOGGER = logging.getLogger("supportportal.automation_ecs_route_worker")


def _comment_role(comment: Any) -> str | None:
    role = str(comment.author.role or "").strip().lower()
    role_is_agent = None
    if role in {"agent", "staff", "admin", "support"}:
        role_is_agent = True
    elif role in {"end-user", "end_user", "customer", "requester", "user"}:
        role_is_agent = False
    if (
        role_is_agent is not None
        and comment.author.is_agent is not None
        and role_is_agent != comment.author.is_agent
    ):
        raise ValueError("automation_context_author_identity_conflict")
    is_agent = role_is_agent if role_is_agent is not None else comment.author.is_agent
    if is_agent is None:
        return None
    return "assistant" if is_agent else "user"


def _route_payload(result: Any) -> dict[str, Any]:
    decision = result.decision
    return {
        "scope_label": decision.scope_label,
        "route_family": decision.route_family,
        "execution_action": decision.execution_action or decision.route,
        "route": decision.route,
        "reason": decision.reason,
        "confidence": decision.confidence,
        "router_source": decision.router_source,
        "matched_signals": list(decision.matched_signals or []),
        "semantic_intent": decision.semantic_intent,
        "automation_eligibility": decision.automation_eligibility,
        "policy_decision": decision.policy_decision,
        "not_automated_reason": decision.not_automated_reason,
        "risk_flags": list(decision.risk_flags or []),
        "evidence_spans": list(decision.evidence_spans or []),
        "intent_router_attempted": decision.intent_router_attempted,
        "intent_router_confidence_threshold": decision.intent_router_confidence_threshold,
        "intent_router_fallback_reason": decision.intent_router_fallback_reason,
        "intent_router_failure_type": decision.intent_router_failure_type,
        "intent_router_failure_source": decision.intent_router_failure_source,
        "stage_attempts": list(getattr(result, "stage_attempts", None) or []),
        "classification": dict(result.classification),
    }


def _ticket_context(payload: RouteJobPayload) -> list[dict[str, str]]:
    snapshot = payload.event.comment_snapshot
    if snapshot is None:
        return public_messages([{"message_id": payload.event.event_id, "role": "customer",
                                 "created_at": payload.event.occurred_at.isoformat(),
                                 "content": payload.event.routing_text()}])
    comments = sorted(snapshot.comments, key=lambda comment: (comment.created_at, comment.id))
    if snapshot.trigger_comment_id:
        trigger_index = next((i for i, comment in enumerate(comments)
                              if str(comment.id) == str(snapshot.trigger_comment_id)), None)
        if trigger_index is None:
            raise ValueError("automation_context_current_message_not_found")
        comments = comments[:trigger_index + 1]
    context = []
    for comment in comments:
        if not comment.public or not comment.body.strip():
            continue
        role = _comment_role(comment)
        if role is None:
            continue
        context.append({
            "role": role,
            "content": comment.body,
            "message_id": comment.id,
            "created_at": comment.created_at.isoformat(),
        })
    return public_messages(context)


@dataclass
class RouteWorker:
    settings: AutomationEcsSettings
    store: AutomationEcsStore
    persona_resolver: Callable[[str], dict[str, Any] | None]
    route_decider: Callable[..., Any] = decide_account_route
    lease_seconds: int = 120
    case_loader: Callable[[str], dict[str, Any] | None] | None = None

    def process_once(self) -> bool:
        self.store.heartbeat(
            worker_id=self.settings.runtime_identity,
            provenance=self.settings.provenance(),
        )
        job = self.store.claim_job(
            JobKind.ROUTE,
            worker_id=self.settings.runtime_identity,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return False
        lease = JobLeaseHeartbeat(self.store, job=job, lease_seconds=self.lease_seconds)
        lease.start()
        try:
            payload = RouteJobPayload.model_validate(job.payload)
            event = payload.event
            context = _ticket_context(payload)
            case = self.case_loader(event.ticket.id) if self.case_loader else None
            context = understanding_messages(build_automation_context({"ticket_id": event.ticket.id,
                "status": event.ticket.status, "messages": context}, case if isinstance(case, dict) else {}))
            result = self.route_decider(
                event.routing_text(),
                ticket_subject=event.ticket.subject,
                ticket_context=context,
                current_ticket_status=event.ticket.status,
                require_latest=True,
            )
            persona = None
            if event.event_type != IntakeEventType.TICKET_CREATED:
                persona = self.persona_resolver(event.ticket.id)
            lease.stop()
            self.store.complete_route(
                job,
                route=_route_payload(result),
                persona=persona,
                prompt_snapshots=dict(result.prompt_snapshots),
                provenance=self.settings.provenance(),
            )
            return True
        except Exception as exc:
            lease.stop()
            LOGGER.exception("Route job failed execution_id=%s", job.execution_id)
            self.store.fail_job(
                job,
                failure_stage="route.classify",
                failure_code=f"route_{type(exc).__name__}",
                error_message=str(exc),
            )
            return True


def run_route_worker() -> int:
    logging.basicConfig(level=logging.INFO)
    settings = AutomationEcsSettings.from_env("route")
    store = create_automation_ecs_store(settings)
    repository = create_ticket_repository()
    store.check_schema()
    check_account_runtime_schema()
    initialize_prompt_runtime(repository, service_name="automation-route")
    worker = RouteWorker(
        settings=settings,
        store=store,
        persona_resolver=repository.resolve_account_persona,
        case_loader=repository.get_billing_ticket_by_client_ticket_id,
    )
    stopping = Event()
    heartbeat = WorkerHeartbeat(
        store,
        worker_id=settings.runtime_identity,
        provenance=settings.provenance(),
    )
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopping.set())
    heartbeat.start()
    try:
        while not stopping.is_set():
            if not worker.process_once():
                stopping.wait(1.0)
            else:
                time.sleep(0)
    finally:
        heartbeat.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_route_worker())
