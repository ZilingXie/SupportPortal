"""Engineer Case Slack collaboration for /automation/production (p2-113 Phase E).

Pure port of the old /production engineer-loop surface: the investigation AI
round (`_process_engineer_investigation_message`, guardrail-only path — both
split callers submit with multi_agent_enabled=False) and the three n8n inbound
endpoints (thread binding resolve, Slack thread message, Slack interaction
action incl. guardrail + final approve with engineer Zendesk delivery queue).
Divergences vs main.py, both deliberate and recorded in p2-113: the
multi-agent Plan/Execute/Review refresh branch is omitted (never enabled on
these paths) and `_normalize_engineer_case_payload_for_read` is skipped
(repository payloads are already payload-shaped for these reads).
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from backend.services.customer_reply_composer import detect_customer_reply_language
from backend.services.engineer_cases import (
    apply_case_context_to_engineer_case,
    build_engineer_case_context,
)
from backend.services.engineer_guardrail_agent import run_engineer_guardrail_final
from backend.services.engineer_slack import build_engineer_case_thread_event
from backend.services.investigation_flow import (
    INVESTIGATION_STATE_AWAITING_FINAL_APPROVAL,
    append_engineer_investigation_message,
    build_internal_message,
    default_investigation_prompt,
    latest_customer_message,
)

from backend.services.automation_account_reply_sync import (
    ReplySyncError,
    _engineer_case_payload_to_record,
)

SLACK_ENGINEER_MESSAGE_IDEMPOTENCY_SCOPE = "slack_engineer_case_message"
SLACK_ENGINEER_ACTION_IDEMPOTENCY_SCOPE = "slack_engineer_case_action"


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _resolve_engineer_case_payload(repository: Any, reference_id: str) -> dict[str, Any] | None:
    normalized_reference = str(reference_id or "").strip()
    if not normalized_reference:
        return None
    case_payload = repository.get_engineer_case(normalized_reference, include_client_messages=True)
    if case_payload is not None:
        return case_payload
    client_ticket = repository.get_ticket(normalized_reference)
    if client_ticket is None:
        return None
    active_cases = repository.list_ticket_engineer_cases(
        normalized_reference, include_client_messages=True
    )
    open_cases = [
        item
        for item in active_cases
        if isinstance(item, dict)
        and (
            isinstance(item.get("active_investigation"), dict)
            or (
                isinstance(item.get("investigation_history"), list)
                and item.get("investigation_history")
                and str((item["investigation_history"][0] or {}).get("state") or "").strip().lower() != "closed"
            )
        )
    ]
    if len(open_cases) == 1:
        return open_cases[0]
    return None


async def process_engineer_investigation_message(
    repository: Any,
    ticket_id: str,
    *,
    engineer_id: str,
    message: str,
    message_role: str = "engineer",
    message_meta: dict[str, Any] | None = None,
    slack_event_id: str | None = None,
) -> dict[str, Any]:
    import asyncio
    import copy

    def _sync(call, *args, **kwargs):
        return asyncio.get_running_loop().run_in_executor(None, lambda: call(*args, **kwargs))

    engineer_case_payload = _resolve_engineer_case_payload(repository, ticket_id)
    if engineer_case_payload is None:
        raise ReplySyncError(404, "Ticket not found")
    ticket = repository.get_ticket(
        str((engineer_case_payload.get("client_ticket_ref") or {}).get("ticket_id") or "")
    )
    if ticket is None:
        raise ReplySyncError(404, "Ticket not found")
    if not isinstance(engineer_case_payload.get("active_investigation"), dict):
        raise ReplySyncError(400, "No active investigation exists")

    engineer_case = _engineer_case_payload_to_record(engineer_case_payload)
    case_context = build_engineer_case_context(ticket, engineer_case)
    timestamp = _now_iso()
    preserved_state = copy.deepcopy(
        engineer_case.get("engineer_agent_state")
        if isinstance(engineer_case.get("engineer_agent_state"), dict)
        else {}
    )
    conversation_version = int(preserved_state.get("conversation_version") or 0) + 1
    previous_draft_version = int(preserved_state.get("draft_version") or 0)
    for guardrail_key in (
        "active_guardrail_final",
        "guardrail_final_id",
        "guardrail_final_version",
        "guardrail_final_decision",
        "final_approval_required",
        "final_approved_at",
    ):
        preserved_state.pop(guardrail_key, None)
    preserved_state["conversation_version"] = conversation_version
    preserved_state["round_state"] = "active"
    active_investigation = case_context.get("active_investigation")
    if isinstance(active_investigation, dict):
        active_investigation["state"] = "active"
        active_investigation["final_confirmation_requested_at"] = None
    if preserved_state:
        case_context["engineer_agent_state"] = copy.deepcopy(preserved_state)
    result = append_engineer_investigation_message(
        case_context,
        engineer_message=message.strip(),
        now_value=timestamp,
        ai_turn_builder=default_investigation_prompt,
        message_role=message_role,
        message_meta=message_meta,
    )
    # When the investigation turn concludes (awaiting_confirmation), assemble the
    # customer-facing reply with the automation persona from the verified findings;
    # the investigation agent itself never writes the customer draft.
    persona_meta: dict[str, Any] | None = None
    if str((result.get("active_investigation") or {}).get("state") or "").strip().lower() == "awaiting_confirmation":
        from backend.services.automation_persona import (
            ENGINEER_INVESTIGATION_REPLY_INTENT,
            AutomationPersonaError,
            render_automation_reply,
            resolve_customer_greeting_name,
        )

        agent_state_snapshot = dict(
            case_context.get("engineer_agent_state")
            if isinstance(case_context.get("engineer_agent_state"), dict)
            else {}
        )
        readiness_snapshot = dict(agent_state_snapshot.get("reply_readiness") or {})
        findings: list[str] = []
        conclusion = str(readiness_snapshot.get("conclusion_summary") or "").strip()
        proof = str(readiness_snapshot.get("proof_summary") or "").strip()
        solution = str(readiness_snapshot.get("solution_or_next_step") or "").strip()
        if conclusion:
            findings.append(f"Conclusion: {conclusion}")
        if proof:
            findings.append(f"Evidence: {proof}")
        for known_fact in [str(item or "").strip() for item in agent_state_snapshot.get("known_facts") or [] if str(item or "").strip()][:8]:
            findings.append(f"Verified fact: {known_fact}")
        if solution:
            findings.append(f"Suggested resolution: {solution}")
        provided_answer = "\n".join(findings).strip()
        client_ticket_id = str((engineer_case_payload.get("client_ticket_ref") or {}).get("ticket_id") or "")
        if provided_answer and client_ticket_id:
            account_case_snapshot = repository.get_account_case_by_ticket_id(client_ticket_id) or {}
            latest_customer = latest_customer_message(ticket)
            public_messages = [
                str(item.get("content") or "").strip()
                for item in list(ticket.get("messages") or [])[-12:]
                if str(item.get("role") or "").strip().lower() in {"customer", "assistant"}
                and str(item.get("content") or "").strip()
            ]
            facts = {
                "behavior": "engineer_support",
                "reply_intent": ENGINEER_INVESTIGATION_REPLY_INTENT,
                "provided_answer": provided_answer,
                "latest_customer_message": latest_customer,
                "recent_public_conversation": "\n".join(public_messages[-6:]),
                "subject": str(account_case_snapshot.get("title") or ticket.get("subject") or "").strip(),
                "customer_language": detect_customer_reply_language(latest_customer, provided_answer),
                "customer_first_name": resolve_customer_greeting_name(
                    latest_customer_author_name=next(
                        (
                            str(
                                (item.get("meta") or {}).get("author_name") or ""
                                if isinstance(item.get("meta"), dict)
                                else ""
                            ).strip()
                            for item in reversed(list(ticket.get("messages") or []))
                            if isinstance(item, dict)
                            and str(item.get("role") or "").strip().lower() in {"customer", "user"}
                        ),
                        "",
                    ),
                    case_customer_name=account_case_snapshot.get("customer_name"),
                    requester_name=ticket.get("requester"),
                ),
            }
            try:
                persona_assignment = await _sync(
                    repository.resolve_account_persona,
                    client_ticket_id,
                )
                rendered = await _sync(
                    render_automation_reply,
                    reply_facts=facts,
                    persona_assignment=persona_assignment,
                    account_scope=False,
                )
            except AutomationPersonaError as exc:
                failure_code = str(getattr(exc, "code", "") or "").strip() or "automation_persona_failed"
                failed_event = build_engineer_case_thread_event(
                    event_id=f"{slack_event_id or engineer_case.get('engineer_case_id')}:draft-failed",
                    event_type="engineer_ai_response_failed",
                    engineer_case_id=str(engineer_case.get("engineer_case_id") or ticket_id),
                    message_text=f"Unable to assemble the customer draft. Failure: {failure_code}",
                    investigation_id=str((result.get("active_investigation") or {}).get("id") or "") or None,
                    failure_code=failure_code,
                )
                repository.save_engineer_case(engineer_case, new_messages=[], slack_events=[failed_event])
                raise ReplySyncError(502, failure_code) from exc
            persona_meta = {
                "source": "automation_persona",
                "persona_key": str((persona_assignment or {}).get("persona_key") or ""),
                "persona_version": str((persona_assignment or {}).get("version") or ""),
                "model": rendered.model,
                "prompt_version": rendered.prompt_version,
            }
            active_investigation = result.get("active_investigation") or {}
            active_investigation["draft_customer_reply"] = rendered.content
            active_investigation["state"] = "awaiting_confirmation"
            active_investigation["final_confirmation_requested_at"] = timestamp
            active_investigation["updated_at"] = timestamp
            result["active_investigation"] = active_investigation
            case_context["active_investigation"] = active_investigation
            agent_state_snapshot["reply_readiness"] = {
                "has_conclusion": bool(readiness_snapshot.get("has_conclusion")),
                "has_proof": bool(readiness_snapshot.get("has_proof")),
                "has_solution_or_next_step": bool(solution),
                "reply_scope": str(readiness_snapshot.get("reply_scope") or ""),
                "conclusion_summary": conclusion,
                "proof_summary": proof,
                "proof_anchors": [str(item) for item in readiness_snapshot.get("proof_anchors") or []],
                "solution_or_next_step": solution,
                "blockers": [],
                "advisory_followups": [],
                "critique": "",
                "source_mode": "persona_assembled",
                "ready_for_customer_reply": True,
            }
            agent_state_snapshot["guided_reply_generation"] = persona_meta
            agent_state_snapshot["ready_to_reply"] = True
            case_context["engineer_agent_state"] = agent_state_snapshot
    engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
    if preserved_state:
        merged_state = dict(
            engineer_case.get("engineer_agent_state")
            if isinstance(engineer_case.get("engineer_agent_state"), dict)
            else {}
        )
        for key in (
            "active_plan",
            "plan_id",
            "plan_version",
            "plan_agent_version",
            "active_execution",
            "execution_id",
            "execution_version",
            "execute_agent_version",
            "evidence_packet",
            "task_results",
            "active_review",
            "review_id",
            "review_version",
            "review_agent_version",
            "review_decision",
            "replan_count",
            "last_revise_context",
            "multi_agent_last_run",
        ):
            if key in preserved_state:
                merged_state[key] = preserved_state[key]
        engineer_case["engineer_agent_state"] = merged_state
        case_context["engineer_agent_state"] = copy.deepcopy(merged_state)
    current_state = dict(
        engineer_case.get("engineer_agent_state")
        if isinstance(engineer_case.get("engineer_agent_state"), dict)
        else {}
    )
    for guardrail_key in (
        "active_guardrail_final",
        "guardrail_final_id",
        "guardrail_final_version",
        "guardrail_final_decision",
        "final_approval_required",
        "final_approved_at",
    ):
        current_state.pop(guardrail_key, None)
    current_draft = str(
        (result.get("active_investigation") or {}).get("draft_customer_reply") or ""
    ).strip()
    draft_version = previous_draft_version + 1 if current_draft else previous_draft_version
    current_state.update(
        conversation_version=conversation_version,
        draft_version=draft_version,
        round_state="active",
    )
    engineer_case["engineer_agent_state"] = current_state
    case_context["engineer_agent_state"] = copy.deepcopy(current_state)
    ticket["updated_at"] = timestamp
    ticket["last_engineer_action"] = {
        "action": "investigation_message",
        "engineer_id": engineer_id,
        "note": message.strip(),
        "created_at": timestamp,
    }
    slack_events: list[dict[str, Any]] = []
    if slack_event_id:
        ai_messages = [
            str(item.get("content") or "").strip()
            for item in result.get("new_internal_messages") or []
            if str(item.get("role") or "").strip().lower() in {"engineer_ai", "assistant", "ai"}
            and str(item.get("content") or "").strip()
        ]
        analysis = ai_messages[-1] if ai_messages else "Engineer AI updated the investigation."
        message_text = analysis
        if persona_meta:
            message_text = (
                f"{analysis}\n\nPersona: {persona_meta.get('persona_key')} v{persona_meta.get('persona_version')}"
                f"\n\nCustomer draft:\n{current_draft}"
            )
        elif current_draft:
            message_text = f"{analysis}\n\nCustomer draft:\n{current_draft}"
        slack_events.append(
            build_engineer_case_thread_event(
                event_id=slack_event_id,
                event_type="engineer_ai_response",
                engineer_case_id=str(engineer_case.get("engineer_case_id") or ticket_id),
                message_text=message_text,
                investigation_id=str((result.get("active_investigation") or {}).get("id") or "") or None,
                conversation_version=conversation_version,
                draft_version=draft_version,
                customer_draft=current_draft or None,
                action="guardrail" if current_draft else None,
            )
        )
    repository.save_ticket(ticket, new_messages=[])
    repository.save_engineer_case(
        engineer_case,
        new_messages=result.get("new_internal_messages"),
        slack_events=slack_events,
    )
    return {
        "ticket_id": str(engineer_case.get("engineer_case_id") or ticket_id),
        "status": ticket["status"],
        "active_investigation": result.get("active_investigation"),
        "engineer_agent_state": (
            case_context.get("engineer_agent_state")
            if isinstance(case_context.get("engineer_agent_state"), dict)
            else None
        ),
        "updated_at": ticket["updated_at"],
    }


def resolve_slack_engineer_thread_binding(
    repository: Any,
    *,
    team_id: str,
    channel_id: str,
    thread_ts: str,
) -> dict[str, Any]:
    configured_team_id = str(os.getenv("ENGINEER_SLACK_TEAM_ID") or "").strip()
    configured_channel_id = str(os.getenv("ENGINEER_SLACK_CHANNEL_ID") or "").strip()
    if not configured_team_id or not configured_channel_id:
        raise ReplySyncError(503, "Engineer Slack destination is not configured")
    if not (
        hmac.compare_digest(team_id.strip(), configured_team_id)
        and hmac.compare_digest(channel_id.strip(), configured_channel_id)
    ):
        return {"status": "ignored_unbound"}
    binding = repository.resolve_engineer_slack_thread_binding(
        slack_channel_id=configured_channel_id,
        slack_thread_ts=thread_ts.strip(),
    )
    if not isinstance(binding, dict):
        return {"status": "ignored_unbound"}
    return {
        "status": "bound",
        "engineer_case_id": str(binding.get("engineer_case_id") or "").strip(),
        "team_id": configured_team_id,
        "channel_id": configured_channel_id,
        "thread_ts": str(binding.get("slack_thread_ts") or "").strip(),
    }


async def handle_slack_engineer_message(repository: Any, payload: dict[str, Any]) -> dict[str, Any]:
    import asyncio

    def _sync(call, *args, **kwargs):
        return asyncio.get_running_loop().run_in_executor(None, lambda: call(*args, **kwargs))

    event_id = str(payload.get("event_id") or "").strip()
    engineer_case_id = str(payload.get("engineer_case_id") or "").strip()
    text = str(payload.get("text") or "").strip()
    if payload.get("schema_version") != 1 or not event_id or not engineer_case_id or not text:
        raise ReplySyncError(422, "schema_version/event_id/engineer_case_id/text are required")
    claim = await _sync(
        repository.begin_idempotent_request,
        SLACK_ENGINEER_MESSAGE_IDEMPOTENCY_SCOPE,
        event_id,
        created_at=_now_iso(),
        retry_failed=True,
    )
    if not bool(claim.get("created")):
        existing = claim.get("response_payload")
        if str(claim.get("state") or "").strip().lower() == "completed" and isinstance(existing, dict):
            return {**existing, "idempotent_replay": True}
        raise ReplySyncError(409, "Slack message event is already processing")

    engineer_case_payload = _resolve_engineer_case_payload(repository, engineer_case_id)
    if engineer_case_payload is None:
        await _sync(
            repository.fail_idempotent_request,
            SLACK_ENGINEER_MESSAGE_IDEMPOTENCY_SCOPE,
            event_id,
            response_payload={"status": "error", "code": "engineer_case_not_found"},
            updated_at=_now_iso(),
        )
        raise ReplySyncError(404, "Engineer Case not found")
    if not isinstance(engineer_case_payload.get("active_investigation"), dict):
        ignored = {
            "status": "ignored_closed_case",
            "event_id": event_id,
            "engineer_case_id": engineer_case_id,
        }
        await _sync(
            repository.complete_idempotent_request,
            SLACK_ENGINEER_MESSAGE_IDEMPOTENCY_SCOPE,
            event_id,
            response_payload=ignored,
            updated_at=_now_iso(),
        )
        return ignored

    try:
        result = await process_engineer_investigation_message(
            repository,
            engineer_case_id,
            engineer_id=f"slack:{str(payload.get('slack_user_id') or '').strip()}",
            message=text,
            message_meta={
                "source": "slack",
                "slack_user_id": str(payload.get("slack_user_id") or ""),
                "slack_event_id": event_id,
                "occurred_at": str(payload.get("occurred_at") or ""),
            },
            slack_event_id=f"{event_id}:ai-response",
        )
    except Exception as exc:
        await _sync(
            repository.fail_idempotent_request,
            SLACK_ENGINEER_MESSAGE_IDEMPOTENCY_SCOPE,
            event_id,
            response_payload={"status": "failed", "code": type(exc).__name__},
            updated_at=_now_iso(),
        )
        raise

    response = {
        "status": "processed",
        "event_id": event_id,
        "engineer_case_id": engineer_case_id,
        "conversation_version": int((result.get("engineer_agent_state") or {}).get("conversation_version") or 0),
        "draft_version": int((result.get("engineer_agent_state") or {}).get("draft_version") or 0),
    }
    await _sync(
        repository.complete_idempotent_request,
        SLACK_ENGINEER_MESSAGE_IDEMPOTENCY_SCOPE,
        event_id,
        response_payload=response,
        updated_at=_now_iso(),
    )
    return response


async def handle_slack_engineer_action(repository: Any, payload: dict[str, Any]) -> dict[str, Any]:
    import asyncio

    from backend.services.zendesk_comments import ZendeskCommentError
    from backend.services.zendesk_ticket_assignment import read_ticket_ownership_snapshot

    def _sync(call, *args, **kwargs):
        return asyncio.get_running_loop().run_in_executor(None, lambda: call(*args, **kwargs))

    interaction_id = str(payload.get("interaction_id") or "").strip()
    engineer_case_id = str(payload.get("engineer_case_id") or "").strip()
    action = str(payload.get("action") or "").strip()
    investigation_id = str(payload.get("investigation_id") or "").strip()
    if not interaction_id or not engineer_case_id or action not in {"guardrail", "final_approve"} or not investigation_id:
        raise ReplySyncError(422, "interaction_id/engineer_case_id/action/investigation_id are required")
    try:
        draft_version = int(payload.get("draft_version") or 0)
    except (TypeError, ValueError):
        raise ReplySyncError(422, "draft_version must be an integer") from None
    if draft_version < 1:
        raise ReplySyncError(422, "draft_version must be >= 1")

    claim = await _sync(
        repository.begin_idempotent_request,
        SLACK_ENGINEER_ACTION_IDEMPOTENCY_SCOPE,
        interaction_id,
        created_at=_now_iso(),
        retry_failed=True,
    )
    if not bool(claim.get("created")):
        existing = claim.get("response_payload")
        if str(claim.get("state") or "").strip().lower() == "completed" and isinstance(existing, dict):
            return {**existing, "idempotent_replay": True}
        raise ReplySyncError(409, "Slack action is already processing")

    async def _complete(result_payload: dict[str, Any]) -> dict[str, Any]:
        await _sync(
            repository.complete_idempotent_request,
            SLACK_ENGINEER_ACTION_IDEMPOTENCY_SCOPE,
            interaction_id,
            response_payload=result_payload,
            updated_at=_now_iso(),
        )
        return result_payload

    try:
        engineer_case_payload = _resolve_engineer_case_payload(repository, engineer_case_id)
        if engineer_case_payload is None:
            raise ReplySyncError(404, "Engineer Case not found")
        active_payload = engineer_case_payload.get("active_investigation")
        if not isinstance(active_payload, dict):
            return await _complete(
                {
                    "status": "ignored_closed_case",
                    "interaction_id": interaction_id,
                    "engineer_case_id": engineer_case_id,
                }
            )
        if str(active_payload.get("id") or "").strip() != investigation_id:
            raise ReplySyncError(409, "stale investigation")

        ticket = repository.get_ticket(
            str((engineer_case_payload.get("client_ticket_ref") or {}).get("ticket_id") or "")
        )
        if ticket is None:
            raise ReplySyncError(404, "Client ticket not found")
        engineer_case = _engineer_case_payload_to_record(engineer_case_payload)
        case_context = build_engineer_case_context(ticket, engineer_case)
        active = case_context.get("active_investigation")
        if not isinstance(active, dict):
            raise ReplySyncError(409, "stale investigation")
        agent_state = dict(
            engineer_case.get("engineer_agent_state")
            if isinstance(engineer_case.get("engineer_agent_state"), dict)
            else {}
        )
        current_draft_version = int(agent_state.get("draft_version") or 0)
        conversation_version = int(agent_state.get("conversation_version") or 0)
        if current_draft_version != draft_version:
            raise ReplySyncError(409, "stale draft version")
        timestamp = _now_iso()
        draft_reply = str(active.get("draft_customer_reply") or "").strip()
        if not draft_reply:
            raise ReplySyncError(400, "A draft customer reply is required")
        slack_user_id = str(payload.get("slack_user_id") or "")

        if action == "guardrail":
            reply_readiness = (
                agent_state.get("reply_readiness")
                if isinstance(agent_state.get("reply_readiness"), dict)
                else None
            )
            active_review = agent_state.get("active_review") if isinstance(agent_state.get("active_review"), dict) else None
            evidence_packet = agent_state.get("evidence_packet") if isinstance(agent_state.get("evidence_packet"), dict) else None
            task_results = agent_state.get("task_results") if isinstance(agent_state.get("task_results"), list) else None
            handoff_packet = ticket.get("engineer_handoff_packet") if isinstance(ticket.get("engineer_handoff_packet"), dict) else None
            guardrail_packet = run_engineer_guardrail_final(
                draft_customer_reply=draft_reply,
                reply_readiness=reply_readiness,
                active_review=active_review,
                evidence_packet=evidence_packet,
                task_results=task_results,
                engineer_handoff_packet=handoff_packet,
                requester=str(ticket.get("requester") or "").strip() or None,
                customer_id=str(ticket.get("customer_id") or "").strip() or None,
                language_hint=detect_customer_reply_language(
                    (handoff_packet or {}).get("latest_customer_message", "") if isinstance(handoff_packet, dict) else "",
                    draft_reply,
                ),
            )
            guardrail_packet.update(
                created_at=timestamp,
                conversation_version=conversation_version,
                draft_version=current_draft_version,
            )
            decision = str(guardrail_packet.get("decision") or "blocked")
            approved = decision == "approved_for_final_engineer_review"
            blockers = [str(item).strip() for item in guardrail_packet.get("blockers") or [] if str(item).strip()]
            summary = (
                "Guardrail passed. Approve and publish this draft."
                if approved
                else "Guardrail blocked: " + ("; ".join(blockers) or "unknown reason")
            )
            guardrail_message = build_internal_message(
                investigation_id,
                "engineer_ai",
                summary,
                timestamp,
                sequence=len(active.get("messages") or []) + 1,
                meta={"source": "slack", "slack_user_id": slack_user_id, "interaction_id": interaction_id},
            )
            active.setdefault("messages", []).append(guardrail_message)
            active["state"] = INVESTIGATION_STATE_AWAITING_FINAL_APPROVAL if approved else "active"
            active["updated_at"] = timestamp
            agent_state.update(
                phase="awaiting_final_approval" if approved else "guardrail_blocked",
                active_guardrail_final=guardrail_packet,
                guardrail_final_id=str(guardrail_packet.get("guardrail_id") or ""),
                guardrail_final_version=str(guardrail_packet.get("guardrail_version") or ""),
                guardrail_final_decision=decision,
                final_approval_required=approved,
            )
            case_context["engineer_agent_state"] = agent_state
            engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
            slack_event = build_engineer_case_thread_event(
                event_id=f"{interaction_id}:guardrail-result",
                event_type="engineer_guardrail_result",
                engineer_case_id=engineer_case_id,
                message_text=summary,
                investigation_id=investigation_id,
                conversation_version=conversation_version,
                draft_version=current_draft_version,
                customer_draft=str(guardrail_packet.get("customer_reply") or draft_reply),
                guardrail_id=str(guardrail_packet.get("guardrail_id") or ""),
                guardrail_version=str(guardrail_packet.get("guardrail_version") or ""),
                action="final_approve" if approved else None,
                blockers=blockers,
            )
            repository.save_engineer_case(
                engineer_case,
                new_messages=[guardrail_message],
                slack_events=[slack_event],
            )
            return await _complete(
                {
                    "status": "guardrail_passed" if approved else "guardrail_blocked",
                    "interaction_id": interaction_id,
                    "engineer_case_id": engineer_case_id,
                    "guardrail_id": str(guardrail_packet.get("guardrail_id") or ""),
                    "guardrail_version": str(guardrail_packet.get("guardrail_version") or ""),
                    "draft_version": current_draft_version,
                }
            )

        guardrail = agent_state.get("active_guardrail_final")
        if not isinstance(guardrail, dict):
            raise ReplySyncError(409, "Guardrail approval is missing or stale")
        if (
            str(agent_state.get("round_state") or "").strip().lower() == "publishing"
            or bool(agent_state.get("final_approved_at"))
        ):
            raise ReplySyncError(409, "Zendesk delivery is already queued")
        if str(active.get("state") or "").strip().lower() != INVESTIGATION_STATE_AWAITING_FINAL_APPROVAL:
            raise ReplySyncError(409, "Guardrail approval is missing or stale")
        if (
            str(guardrail.get("guardrail_id") or "") != str(payload.get("guardrail_id") or "")
            or str(guardrail.get("guardrail_version") or "") != str(payload.get("guardrail_version") or "")
            or int(guardrail.get("draft_version") or 0) != current_draft_version
            or int(guardrail.get("conversation_version") or 0) != conversation_version
            or str(guardrail.get("decision") or "") != "approved_for_final_engineer_review"
        ):
            raise ReplySyncError(409, "Guardrail approval is missing or stale")
        approved_content = str(guardrail.get("customer_reply") or "").strip()
        account_case = repository.get_account_case_by_ticket_id(str(ticket.get("ticket_id") or ""))
        if not isinstance(account_case, dict) or str(account_case.get("processing_profile") or "").lower() != "production":
            raise ReplySyncError(409, "Production Account Case is required")
        account_case_id = str(account_case.get("account_case_id") or account_case.get("billing_ticket_id") or "").strip()
        zendesk_ticket_id = str(account_case.get("zendesk_ticket_id") or "").strip()
        if not account_case_id or not zendesk_ticket_id or not approved_content:
            raise ReplySyncError(409, "Zendesk delivery target is incomplete")
        sync_state = repository.get_account_case_comment_sync(str(ticket.get("ticket_id") or ""))
        comments_revision = str((sync_state or {}).get("comments_revision") or "").strip()
        if not comments_revision:
            # Mirror the legacy /production approval path: fall back to a live
            # Zendesk ownership snapshot when no comment sync baseline exists.
            try:
                zendesk_snapshot = await _sync(
                    read_ticket_ownership_snapshot,
                    ticket_id=zendesk_ticket_id,
                )
            except ZendeskCommentError as exc:
                raise ReplySyncError(503, "Unable to verify Zendesk comments before approval") from exc
            comments_revision = str(getattr(zendesk_snapshot, "comments_revision", "") or "").strip()
        if not comments_revision:
            raise ReplySyncError(409, "Zendesk comments snapshot is required before approval")

        approval_message = build_internal_message(
            investigation_id,
            "engineer",
            "Approved for Zendesk public comment delivery.",
            timestamp,
            sequence=len(active.get("messages") or []) + 1,
            meta={"source": "slack", "slack_user_id": slack_user_id, "interaction_id": interaction_id, "action": "final_approve"},
        )
        active.setdefault("messages", []).append(approval_message)
        active["updated_at"] = timestamp
        agent_state.update(
            phase="delivery_pending",
            round_state="publishing",
            final_approved_at=timestamp,
            approved_message_id=str(approval_message.get("id") or ""),
        )
        case_context["engineer_agent_state"] = agent_state
        engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
        slack_event = build_engineer_case_thread_event(
            event_id=f"{interaction_id}:publish-queued",
            event_type="zendesk_publish_queued",
            engineer_case_id=engineer_case_id,
            message_text="Approved. Zendesk public comment delivery is queued.",
            investigation_id=investigation_id,
            conversation_version=conversation_version,
            draft_version=current_draft_version,
        )
        delivery = {
            "account_case_id": account_case_id,
            "message_id": str(approval_message.get("id") or ""),
            "zendesk_ticket_id": zendesk_ticket_id,
            "idempotency_key": f"engineer-zendesk-comment:{engineer_case_id}:{investigation_id}:{current_draft_version}",
            "created_at": timestamp,
            "is_public": True,
            "target_status": None,
            "source": "engineer",
            "engineer_case_id": engineer_case_id,
            "investigation_id": investigation_id,
            "draft_version": current_draft_version,
            "comments_revision": comments_revision,
            "immutable_content": approved_content,
        }
        repository.save_engineer_case(
            engineer_case,
            new_messages=[approval_message],
            slack_events=[slack_event],
            zendesk_delivery=delivery,
        )
        return await _complete(
            {
                "status": "delivery_queued",
                "interaction_id": interaction_id,
                "engineer_case_id": engineer_case_id,
                "draft_version": current_draft_version,
            }
        )
    except Exception as exc:
        await _sync(
            repository.fail_idempotent_request,
            SLACK_ENGINEER_ACTION_IDEMPOTENCY_SCOPE,
            interaction_id,
            response_payload={"status": "failed", "code": type(exc).__name__},
            updated_at=_now_iso(),
        )
        raise
