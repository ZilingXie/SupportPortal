from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.repositories.hermes_case_repository import HermesRepositoryConflict
from backend.services.engineer_slack import build_engineer_case_thread_event


CANONICAL_TEST_INVESTIGATION_RESULT = "Investigation result: test"
HERMES_TURN_REQUEST_VERSION = "v1"
HERMES_OUTPUT_VERSION = "v1"
HERMES_LEDGER_DELTA_VERSION = "v1"
HUMAN_AUTHORITY_VERSION = "v1"
CASE_KNOWLEDGE_PROMOTION_VERSION = "v1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HermesLedgerDelta(_StrictModel):
    schema_version: Literal["v1"] = "v1"
    problem_description: str | None = None
    investigation_process: str | None = None
    misjudgment_corrections: str | None = None
    current_conclusion_next_steps: str | None = None
    references: str | None = None


class HermesOutputAction(_StrictModel):
    action: Literal[
        "authorize_round", "accept_and_finish", "start_suggested_round", "stop_investigation"
    ]
    target_version: int = Field(ge=1)
    target_digest: str = Field(min_length=1)


class HermesTurnRequest(_StrictModel):
    schema_version: Literal["v1"]
    request_id: str = Field(min_length=1)
    engineer_case_id: str = Field(min_length=1)
    client_ticket_id: str = Field(min_length=1)
    investigation_id: str = Field(min_length=1)
    hermes_conversation_key: str = Field(min_length=1)
    hermes_session_id: str | None
    episode: int = Field(ge=1)
    conversation_version: int = Field(ge=0)
    turn_kind: Literal["opening", "engineer_feedback", "round_authority", "reopen", "stop"]
    input_text: str = Field(min_length=1)
    slack_channel_id: str | None
    slack_thread_ts: str | None
    session_binding_version: int = Field(ge=0)
    data_boundary: Literal["curated_case_context"]
    human_authority_event_ref: str | None
    approved_round_plan_digest: str | None
    created_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_authority(self) -> "HermesTurnRequest":
        authority_fields = (
            self.human_authority_event_ref,
            self.approved_round_plan_digest,
        )
        if self.turn_kind == "round_authority" and not all(authority_fields):
            raise ValueError(
                "round_authority requires human authority reference and approved plan digest"
            )
        if self.turn_kind != "round_authority" and any(authority_fields):
            raise ValueError("human authority fields are only valid for round_authority")
        return self


class HermesInvestigationOutput(_StrictModel):
    schema_version: Literal["v1"]
    output_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    engineer_case_id: str = Field(min_length=1)
    investigation_id: str = Field(min_length=1)
    hermes_conversation_key: str = Field(min_length=1)
    hermes_session_id: str = Field(min_length=1)
    episode: int = Field(ge=1)
    conversation_version: int = Field(ge=0)
    output_version: int = Field(ge=1)
    output_kind: Literal[
        "investigation_result", "round_plan", "review_packet", "investigation_status"
    ]
    round_id: str | None
    text: str = Field(min_length=1)
    ledger_delta: HermesLedgerDelta
    available_actions: tuple[HermesOutputAction, ...]
    producer_contract_version: Literal["v1"]
    created_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_available_actions(self) -> "HermesInvestigationOutput":
        actions = {item.action for item in self.available_actions}
        if len(actions) != len(self.available_actions):
            raise ValueError("available actions must be unique")
        if self.output_kind == "round_plan" and actions != {"authorize_round"}:
            raise ValueError("round_plan requires only authorize_round")
        if self.output_kind == "review_packet" and actions - {
            "accept_and_finish", "start_suggested_round", "stop_investigation"
        }:
            raise ValueError("review_packet contains an invalid authority action")
        if self.output_kind in {"investigation_result", "investigation_status"} and actions:
            raise ValueError(f"{self.output_kind} does not support round authority actions")
        if self.output_kind in {"round_plan", "review_packet"} and not self.round_id:
            raise ValueError(f"{self.output_kind} requires round_id")
        return self


class HumanAuthorityEvent(_StrictModel):
    schema_version: Literal["v1"]
    authority_event_id: str = Field(min_length=1)
    engineer_case_id: str = Field(min_length=1)
    episode: int = Field(ge=1)
    conversation_version: int = Field(ge=0)
    action: Literal[
        "authorize_round", "accept_and_finish", "start_suggested_round", "stop_investigation"
    ]
    target_output_id: str = Field(min_length=1)
    target_version: int = Field(ge=1)
    target_digest: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    created_at: str = Field(min_length=1)


class SanitizationReport(_StrictModel):
    decision: Literal["passed"]
    reason: str = Field(min_length=1)


class SanitizedCaseKnowledge(_StrictModel):
    summary: str = Field(min_length=1)
    references: str = ""
    safety_label: Literal["sanitized"]
    sanitization_report: SanitizationReport


class CaseKnowledgePromotion(_StrictModel):
    schema_version: Literal["v1"]
    promotion_id: str = Field(min_length=1)
    engineer_case_id: str = Field(min_length=1)
    client_ticket_id: str = Field(min_length=1)
    episode: int = Field(ge=1)
    ledger_revision: int = Field(ge=1)
    status: Literal["awaiting_transport"]
    sanitized_payload: SanitizedCaseKnowledge
    created_at: str = Field(min_length=1)


HermesWorkflowConflict = HermesRepositoryConflict


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hermes_workflow_mode() -> Literal["disabled", "mock"]:
    mode = str(os.getenv("HERMES_CASE_WORKFLOW_MODE") or "disabled").strip().lower()
    if mode not in {"disabled", "mock"}:
        raise RuntimeError("invalid HERMES_CASE_WORKFLOW_MODE")
    return mode  # type: ignore[return-value]


def normalize_summary(value: str) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def evaluate_summary_guardrail(summary: str) -> dict[str, str]:
    normalized = normalize_summary(summary)
    if normalized == CANONICAL_TEST_INVESTIGATION_RESULT:
        return {"decision": "passed", "reason": "test", "normalized_summary": normalized}
    return {
        "decision": "needs_review",
        "reason": "summary_requires_review",
        "normalized_summary": normalized,
    }


def _conversation_key(case_id: str) -> str:
    return f"supportportal:engineer-case:{case_id}"


def _request_id(case_id: str, episode: int, version: int, turn_kind: str) -> str:
    return f"hermes-request:{case_id}:{episode}:{version}:{turn_kind}"


def create_opening_turn(
    *, engineer_case_id: str, client_ticket_id: str, investigation_id: str,
    problem_description: str, now_value: str | None = None,
) -> HermesTurnRequest:
    now = now_value or _now_iso()
    return HermesTurnRequest(
        schema_version="v1",
        request_id=_request_id(engineer_case_id, 1, 0, "opening"),
        engineer_case_id=engineer_case_id,
        client_ticket_id=client_ticket_id,
        investigation_id=investigation_id,
        hermes_conversation_key=_conversation_key(engineer_case_id),
        hermes_session_id=None,
        episode=1,
        conversation_version=0,
        turn_kind="opening",
        input_text=problem_description,
        slack_channel_id=None,
        slack_thread_ts=None,
        session_binding_version=1,
        data_boundary="curated_case_context",
        human_authority_event_ref=None,
        approved_round_plan_digest=None,
        created_at=now,
    )


def start_hermes_case(repository: Any, *, request: HermesTurnRequest) -> dict[str, Any]:
    return repository.start_hermes_case(request.model_dump(mode="json"))


def build_mock_output(request: dict[str, Any], *, now_value: str | None = None) -> HermesInvestigationOutput:
    request_model = HermesTurnRequest.model_validate(
        {key: request[key] for key in HermesTurnRequest.model_fields}
    )
    session_id = request_model.hermes_session_id or f"mock-session:{request_model.hermes_conversation_key}"
    output_id = f"hermes-output:{uuid5(NAMESPACE_URL, request_model.request_id)}"
    return HermesInvestigationOutput(
        schema_version="v1",
        output_id=output_id,
        request_id=request_model.request_id,
        engineer_case_id=request_model.engineer_case_id,
        investigation_id=request_model.investigation_id,
        hermes_conversation_key=request_model.hermes_conversation_key,
        hermes_session_id=session_id,
        episode=request_model.episode,
        conversation_version=request_model.conversation_version,
        output_version=request_model.conversation_version + 1,
        output_kind="investigation_result",
        round_id=None,
        text=CANONICAL_TEST_INVESTIGATION_RESULT,
        ledger_delta=HermesLedgerDelta(
            investigation_process=CANONICAL_TEST_INVESTIGATION_RESULT,
            current_conclusion_next_steps=CANONICAL_TEST_INVESTIGATION_RESULT,
        ),
        available_actions=(),
        producer_contract_version="v1",
        created_at=now_value or _now_iso(),
    )


def apply_hermes_output(repository: Any, output: HermesInvestigationOutput) -> dict[str, Any]:
    payload = output.model_dump(mode="json")
    digest = hashlib.sha256(output.text.encode("utf-8")).hexdigest()
    event = build_engineer_case_thread_event(
        event_id=f"engineer-slack:{output.engineer_case_id}:hermes:{output.output_id}",
        event_type="hermes_investigation_output",
        engineer_case_id=output.engineer_case_id,
        message_text=output.text,
        investigation_id=output.investigation_id,
        conversation_version=output.conversation_version,
        action="summarize",
    )
    event.update(
        output_id=output.output_id,
        episode=output.episode,
        output_digest=digest,
        ledger_delta_version=HERMES_LEDGER_DELTA_VERSION,
        output_version=output.output_version,
        output_kind=output.output_kind,
        round_id=output.round_id,
        authority_actions=[item.model_dump(mode="json") for item in output.available_actions],
        producer_contract_version=output.producer_contract_version,
    )
    return repository.apply_hermes_output(payload, event)


def freeze_summary(repository: Any, *, engineer_case_id: str, now_value: str | None = None) -> dict[str, Any]:
    binding = repository.get_hermes_case_binding(engineer_case_id)
    if not binding:
        raise HermesWorkflowConflict("unknown Hermes Case")
    seed = f"{engineer_case_id}:{binding['episode']}:{binding['conversation_version']}:{binding['current_output_id']}:{binding['current_ledger_revision']}"
    snapshot_id = f"hermes-summary:{uuid5(NAMESPACE_URL, seed)}"
    return repository.freeze_hermes_summary(
        engineer_case_id, snapshot_id=snapshot_id, frozen_at=now_value or _now_iso()
    )


def queue_feedback_turn(
    repository: Any, *, engineer_case_id: str, input_text: str, now_value: str | None = None
) -> HermesTurnRequest:
    binding = repository.get_hermes_case_binding(engineer_case_id)
    if not binding:
        raise HermesWorkflowConflict("unknown Hermes Case")
    version = int(binding["conversation_version"]) + 1
    request = HermesTurnRequest(
        schema_version="v1",
        request_id=_request_id(
            engineer_case_id, int(binding["episode"]), version, "engineer_feedback"
        ),
        engineer_case_id=engineer_case_id,
        client_ticket_id=str(binding["client_ticket_id"]),
        investigation_id=str(binding["investigation_id"]),
        hermes_conversation_key=str(binding["hermes_conversation_key"]),
        hermes_session_id=binding.get("hermes_session_id"),
        episode=int(binding["episode"]),
        conversation_version=version,
        turn_kind="engineer_feedback",
        input_text=input_text,
        slack_channel_id=None,
        slack_thread_ts=None,
        session_binding_version=int(binding["binding_version"]) + 1,
        data_boundary="curated_case_context",
        human_authority_event_ref=None,
        approved_round_plan_digest=None,
        created_at=now_value or _now_iso(),
    )
    repository.queue_hermes_feedback_turn(request.model_dump(mode="json"))
    return request


def record_human_authority(
    repository: Any, *, engineer_case_id: str, action: str, actor_id: str,
    target_output_id: str,
    target_version: int,
    target_digest: str,
    now_value: str | None = None,
) -> HumanAuthorityEvent:
    if action == "summarize":
        raise ValueError("Summarize is not round authority")
    binding = repository.get_hermes_case_binding(engineer_case_id)
    if not binding:
        raise HermesWorkflowConflict("unknown Hermes Case")
    if target_output_id.startswith("hermes-close-review:"):
        review = repository.get_hermes_close_review(target_output_id)
        actual_digest = hashlib.sha256(
            json.dumps(
                (review or {}).get("review_payload") or {},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if (
            action != "accept_and_finish"
            or not isinstance(review, dict)
            or str(review.get("status") or "") != "awaiting_closed"
            or int(review.get("episode") or 0) != int(binding["episode"])
            or int(review.get("ledger_revision") or 0) != target_version
            or not hmac.compare_digest(target_digest, actual_digest)
        ):
            raise HermesWorkflowConflict("stale Hermes close review")
    else:
        target_output = repository.get_hermes_output(target_output_id)
        matching_action = next(
            (
                item
                for item in (target_output or {}).get("available_actions") or []
                if isinstance(item, dict) and str(item.get("action") or "") == action
            ),
            None,
        )
        if (
            not isinstance(target_output, dict)
            or not bool(target_output.get("accepted"))
            or target_output_id != str(binding.get("current_output_id") or "")
            or int(target_output.get("episode") or 0) != int(binding["episode"])
            or int(target_output.get("conversation_version") or 0)
            != int(binding["conversation_version"])
            or not isinstance(matching_action, dict)
            or int(matching_action.get("target_version") or 0) != target_version
            or not hmac.compare_digest(
                target_digest, str(matching_action.get("target_digest") or "")
            )
        ):
            raise HermesWorkflowConflict("stale Hermes authority target")
    now = now_value or _now_iso()
    event_id = (
        f"hermes-authority:{engineer_case_id}:{binding['episode']}:"
        f"{binding['conversation_version']}:{action}:{target_output_id}:"
        f"{target_version}:{target_digest}"
    )
    event = HumanAuthorityEvent(
        schema_version="v1", authority_event_id=event_id, engineer_case_id=engineer_case_id,
        episode=int(binding["episode"]), conversation_version=int(binding["conversation_version"]),
        action=action, target_output_id=target_output_id, target_version=target_version,
        target_digest=target_digest, actor_id=actor_id, created_at=now,
    )
    request = HermesTurnRequest(
        schema_version="v1",
        request_id=(
            _request_id(engineer_case_id, event.episode, event.conversation_version, "round_authority")
            + f":{action}"
        ),
        engineer_case_id=engineer_case_id, client_ticket_id=str(binding["client_ticket_id"]),
        investigation_id=str(binding["investigation_id"]),
        hermes_conversation_key=str(binding["hermes_conversation_key"]),
        hermes_session_id=binding.get("hermes_session_id"), episode=event.episode,
        conversation_version=event.conversation_version, turn_kind="round_authority",
        input_text=action, slack_channel_id=None, slack_thread_ts=None,
        session_binding_version=int(binding["binding_version"]),
        data_boundary="curated_case_context",
        human_authority_event_ref=event.authority_event_id,
        approved_round_plan_digest=event.target_digest,
        created_at=now,
    )
    repository.record_hermes_authority_event(
        event.model_dump(mode="json"), request.model_dump(mode="json")
    )
    return event


def build_mock_sanitized_case_knowledge(ledger: dict[str, Any]) -> dict[str, Any]:
    summary = normalize_summary(str(ledger.get("current_conclusion_next_steps") or ""))
    references = normalize_summary(str(ledger.get("references") or ""))
    if summary != CANONICAL_TEST_INVESTIGATION_RESULT or references:
        raise HermesWorkflowConflict("mock close payload failed sanitization")
    return {
        "summary": summary,
        "references": "",
        "safety_label": "sanitized",
        "sanitization_report": {
            "decision": "passed",
            "reason": "canonical_mock_test",
        },
    }


def record_case_solved(
    repository: Any, *, engineer_case_id: str, now_value: str | None = None
) -> dict[str, Any]:
    binding = repository.get_hermes_case_binding(engineer_case_id)
    if not binding:
        raise HermesWorkflowConflict("unknown Hermes Case")
    review_id = (
        f"hermes-close-review:{engineer_case_id}:"
        f"{binding['episode']}:{binding['current_ledger_revision']}"
    )
    return repository.record_hermes_case_solved(
        engineer_case_id,
        review_id=review_id,
        now_value=now_value or _now_iso(),
    )


def approve_close_review(
    repository: Any, *, review_id: str, reviewer_id: str, now_value: str | None = None
) -> dict[str, Any]:
    return repository.approve_hermes_close_review(
        review_id,
        reviewer_id=reviewer_id,
        now_value=now_value or _now_iso(),
    )


def reopen_hermes_case(
    repository: Any, *, engineer_case_id: str, input_text: str,
    now_value: str | None = None,
) -> HermesTurnRequest:
    binding = repository.get_hermes_case_binding(engineer_case_id)
    if not binding:
        raise HermesWorkflowConflict("unknown Hermes Case")
    episode = int(binding["episode"]) + 1
    version = int(binding["conversation_version"]) + 1
    request = HermesTurnRequest(
        schema_version="v1",
        request_id=_request_id(engineer_case_id, episode, version, "reopen"),
        engineer_case_id=engineer_case_id,
        client_ticket_id=str(binding["client_ticket_id"]),
        investigation_id=str(binding["investigation_id"]),
        hermes_conversation_key=str(binding["hermes_conversation_key"]),
        hermes_session_id=binding.get("hermes_session_id"),
        episode=episode,
        conversation_version=version,
        turn_kind="reopen",
        input_text=input_text,
        slack_channel_id=None,
        slack_thread_ts=None,
        session_binding_version=int(binding["binding_version"]) + 1,
        data_boundary="curated_case_context",
        human_authority_event_ref=None,
        approved_round_plan_digest=None,
        created_at=now_value or _now_iso(),
    )
    repository.reopen_hermes_case(request.model_dump(mode="json"))
    return request


def close_hermes_case(
    repository: Any, *, engineer_case_id: str, sanitized_payload: dict[str, Any],
    now_value: str | None = None,
) -> CaseKnowledgePromotion:
    binding = repository.get_hermes_case_binding(engineer_case_id)
    if not binding:
        raise HermesWorkflowConflict("unknown Hermes Case")
    now = now_value or _now_iso()
    try:
        promotion = CaseKnowledgePromotion(
            schema_version="v1",
            promotion_id=(
                f"hermes-promotion:{engineer_case_id}:"
                f"{binding['episode']}:{binding['current_ledger_revision']}"
            ),
            engineer_case_id=engineer_case_id,
            client_ticket_id=str(binding["client_ticket_id"]),
            episode=int(binding["episode"]),
            ledger_revision=int(binding["current_ledger_revision"]),
            status="awaiting_transport",
            sanitized_payload=sanitized_payload,
            created_at=now,
        )
    except ValueError as exc:
        raise HermesWorkflowConflict("sanitized promotion payload is required") from exc
    repository.close_hermes_case(promotion.model_dump(mode="json"), now_value=now)
    return promotion


def render_case_ledger_markdown(ledger: dict[str, Any]) -> str:
    metadata_fields = (
        ("engineer_case_id", ledger.get("engineer_case_id", "")),
        ("case_title", ledger.get("case_title", "")),
        ("customer_name", ledger.get("customer_name", "")),
        ("vid", ledger.get("vid", "")),
        ("zendesk_ticket_id", ledger.get("zendesk_ticket_id", "")),
        ("client_ticket_id", ledger.get("client_ticket_id", "")),
        ("slack_channel_id", ledger.get("slack_channel_id", "")),
        ("slack_thread_ts", ledger.get("slack_thread_ts", "")),
        ("hermes_conversation_key", ledger.get("hermes_conversation_key", "")),
        ("hermes_session_id", ledger.get("hermes_session_id", "")),
        ("investigation_id", ledger.get("investigation_id", "")),
        ("episode", int(ledger.get("episode") or 0)),
        ("revision", int(ledger.get("revision") or 0)),
        ("status", ledger.get("status", "")),
    )
    metadata = "---\n" + "\n".join(
        f"{key}: {value if isinstance(value, int) else json.dumps(str(value), ensure_ascii=False)}"
        for key, value in metadata_fields
    ) + "\n---"
    sections = (
        ("Problem description", "problem_description"),
        ("Investigation process", "investigation_process"),
        ("Misjudgment corrections", "misjudgment_corrections"),
        ("Current conclusion and next steps", "current_conclusion_next_steps"),
        ("References", "references"),
    )
    body = "\n\n".join(
        f"# {title}\n{str(ledger.get(field) or '').strip()}" for title, field in sections
    )
    return f"{metadata}\n\n{body}\n"


def render_persisted_case_ledger_markdown(
    repository: Any, *, engineer_case_id: str
) -> str:
    ledger = repository.get_hermes_case_ledger(engineer_case_id)
    binding = repository.get_hermes_case_binding(engineer_case_id)
    if not isinstance(ledger, dict) or not isinstance(binding, dict):
        raise HermesWorkflowConflict("unknown Hermes Case")
    client_ticket_id = str(binding.get("client_ticket_id") or "")
    engineer_case = repository.get_engineer_case(
        engineer_case_id, include_client_messages=False
    ) or {}
    ticket = repository.get_ticket(client_ticket_id) or {}
    account_case = repository.get_account_case_by_ticket_id(client_ticket_id) or {}
    slack_binding = repository.get_engineer_slack_thread_binding(
        engineer_case_id, active_only=False
    ) or {}
    view = {
        **ledger,
        "case_title": str(
            account_case.get("title")
            or engineer_case.get("subject")
            or ticket.get("subject")
            or ""
        ).strip(),
        "customer_name": str(
            account_case.get("customer_name") or ticket.get("requester") or ""
        ).strip(),
        "vid": str(
            account_case.get("vid")
            or account_case.get("customer_vid")
            or ticket.get("vid")
            or ""
        ).strip(),
        "zendesk_ticket_id": str(
            account_case.get("zendesk_ticket_id") or client_ticket_id
        ).strip(),
        "slack_channel_id": str(slack_binding.get("slack_channel_id") or "").strip(),
        "slack_thread_ts": str(slack_binding.get("slack_thread_ts") or "").strip(),
        "hermes_conversation_key": str(binding.get("hermes_conversation_key") or "").strip(),
        "hermes_session_id": str(binding.get("hermes_session_id") or "").strip(),
        "investigation_id": str(binding.get("investigation_id") or "").strip(),
    }
    return render_case_ledger_markdown(view)
