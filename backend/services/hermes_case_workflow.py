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
HERMES_SUMMARY_PACKET_VERSION = "v1"
HERMES_REVIEW_REPORT_VERSION = "v1"

# Knowledge-governance artifacts (summary packets, review reports) must never
# leak the same restricted identifiers as promotions: workspace credentials and
# agent-only URLs stay out of anything that travels to knowledge consumers.
_RESTRICTED_KNOWLEDGE_MARKERS = (
    "<restricted>", "authorization:", "x-hermes-callback-token",
    "slack.com/archives/", "zendesk.com/agent/tickets/",
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HermesLedgerDelta(_StrictModel):
    schema_version: Literal["v1"] = "v1"
    problem_description: str | None = None
    investigation_process: str | None = None
    misjudgment_corrections: str | None = None
    current_conclusion_next_steps: str | None = None
    references: str | None = None


class HermesTurnInput(_StrictModel):
    problem_description: str | None = None
    investigation_scope: str | None = None
    completion_criteria: tuple[str, ...] = ()
    message: str | None = None


class HermesHumanAuthority(_StrictModel):
    authority_event_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    action: Literal[
        "authorize_round", "accept_and_finish", "start_suggested_round", "stop_investigation"
    ]
    target_round_id: str = Field(min_length=1)
    target_version: int = Field(ge=1)
    target_digest: str = Field(min_length=1)
    created_at: str = Field(min_length=1)


class HermesOutputAction(_StrictModel):
    action: Literal[
        "authorize_round", "accept_and_finish", "start_suggested_round", "stop_investigation"
    ]
    target_round_id: str = Field(min_length=1)
    target_version: int = Field(ge=1)
    target_digest: str = Field(min_length=1)


class HermesTurnRequestDraft(_StrictModel):
    schema_version: Literal["v1"]
    request_id: str = Field(min_length=1)
    engineer_case_id: str = Field(min_length=1)
    client_ticket_id: str = Field(min_length=1)
    investigation_id: str = Field(min_length=1)
    hermes_conversation_key: str = Field(min_length=1)
    hermes_session_id: str = Field(min_length=1)
    episode: int = Field(ge=1)
    conversation_version: int = Field(ge=0)
    turn_kind: Literal["opening", "engineer_feedback", "round_authority", "reopen", "stop"]
    input: HermesTurnInput
    slack_channel_id: str | None
    slack_thread_ts: str | None
    session_binding_version: int = Field(ge=1)
    data_boundary: Literal["curated_case_context"]
    human_authority: HermesHumanAuthority | None
    created_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_authority(self) -> "HermesTurnRequestDraft":
        if self.turn_kind == "round_authority" and self.human_authority is None:
            raise ValueError("round_authority requires human_authority")
        if self.turn_kind != "round_authority" and self.human_authority is not None:
            raise ValueError("human_authority is only valid for round_authority")
        if self.turn_kind == "opening":
            if (
                not str(self.input.problem_description or "").strip()
                or not str(self.input.investigation_scope or "").strip()
                or not self.input.completion_criteria
                or any(not str(item).strip() for item in self.input.completion_criteria)
                or self.input.message is not None
            ):
                raise ValueError("opening requires problem, scope, and completion criteria only")
        elif not str(self.input.message or "").strip():
            raise ValueError(f"{self.turn_kind} requires input.message")
        return self


class HermesTurnRequest(HermesTurnRequestDraft):
    slack_channel_id: str = Field(min_length=1)
    slack_thread_ts: str = Field(min_length=1)


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


class PromotionVerdict(_StrictModel):
    verdict: Literal["pass"]
    reason: str = Field(min_length=1)


class SanitizedCaseKnowledge(_StrictModel):
    summary: str = Field(min_length=1)
    problem_pattern: str = ""
    root_cause: str = ""
    resolution: str = ""
    verification: str = ""
    references: tuple[str, ...] = ()


class CorrectionRecord(_StrictModel):
    incorrect_direction: str = Field(min_length=1)
    correction: str = Field(min_length=1)


class ClosedRevisionProof(_StrictModel):
    status: Literal["closed"]
    episode: int = Field(ge=1)
    ledger_revision: int = Field(ge=1)
    closed_at: str = Field(min_length=1)


class CaseKnowledgePromotion(_StrictModel):
    schema_version: Literal["v1"]
    promotion_id: str = Field(min_length=1)
    engineer_case_id: str = Field(min_length=1)
    client_ticket_id: str = Field(min_length=1)
    investigation_id: str = Field(min_length=1)
    episode: int = Field(ge=1)
    ledger_revision: int = Field(ge=1)
    status: Literal["awaiting_transport"]
    sanitized_knowledge: SanitizedCaseKnowledge
    evidence_categories: tuple[str, ...]
    applicability: tuple[str, ...]
    limitations: tuple[str, ...]
    corrections: tuple[CorrectionRecord, ...]
    review: PromotionVerdict
    guardrail: PromotionVerdict
    sanitization: PromotionVerdict
    closed_revision_proof: ClosedRevisionProof
    content_hash: str = Field(min_length=64, max_length=64)
    targets: tuple[Literal["tencentdb_knowledge", "skill_evolution"], ...]
    created_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_promotion(self) -> "CaseKnowledgePromotion":
        if tuple(self.targets) != ("tencentdb_knowledge", "skill_evolution"):
            raise ValueError("promotion targets must be fixed and ordered")
        if (
            self.closed_revision_proof.episode != self.episode
            or self.closed_revision_proof.ledger_revision != self.ledger_revision
        ):
            raise ValueError("closed revision proof does not match promotion lineage")
        promotable = {
            "sanitized_knowledge": self.sanitized_knowledge.model_dump(mode="json"),
            "evidence_categories": list(self.evidence_categories),
            "applicability": list(self.applicability),
            "limitations": list(self.limitations),
            "corrections": [item.model_dump(mode="json") for item in self.corrections],
        }
        actual = hashlib.sha256(
            json.dumps(promotable, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if not hmac.compare_digest(self.content_hash, actual):
            raise ValueError("content_hash does not match promotable knowledge")
        restricted = (
            "<restricted>", "authorization:", "x-hermes-callback-token",
            "slack.com/archives/", "zendesk.com/agent/tickets/",
        )
        serialized = json.dumps(promotable, sort_keys=True).lower()
        if any(marker in serialized for marker in restricted):
            raise ValueError("promotion contains a restricted identifier")
        return self


WEKNORA_CANDIDATE_TYPES = ("knowledge", "memory")
WEKNORA_CANDIDATE_DECISIONS = (
    "no_change",
    "new",
    "supplement",
    "replace",
    "merge",
    "human_review",
)


class WeKnoraPromotionCandidate(_StrictModel):
    """Structured promotion candidate from the Hermes Review output.

    Only the classification contract is enforced here: candidate_type and
    decision must be recognizable.  Content completeness (content, target,
    base version, merged content) is validated by the WeKnora adapter, which
    records incomplete review output as a failed write.  Skill proposals are
    intentionally not representable: they stay proposals and never enter the
    WeKnora adapter.
    """

    schema_version: Literal["v1"]
    candidate_type: Literal["knowledge", "memory"]
    decision: Literal[
        "no_change", "new", "supplement", "replace", "merge", "human_review"
    ]
    title: str = ""
    content: str = ""
    merged_content: str = ""
    target_object_id: str = ""
    base_version: str = ""
    # Official WeKnora memory items carry a kind and an importance; the review
    # forwards them when it can and the adapter passes them to the client,
    # whose body template decides whether they reach the wire.
    kind: str = ""
    importance: int | None = None
    note: str = ""


class HermesSummaryCandidate(_StrictModel):
    """One piece of case knowledge proposed for review, type-agnostic by design.

    The Summary role proposes candidates; only the Review role decides whether
    a candidate is knowledge, memory, or a skill change.
    """

    candidate_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    context: str = ""
    evidence_references: tuple[str, ...] = ()


def _summary_packet_content(payload: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        HermesSummaryCandidate.model_validate(item).model_dump(mode="json")
        for item in (payload.get("candidates") or [])
    ]
    return {
        "problem_description": str(payload.get("problem_description") or ""),
        "timeline": str(payload.get("timeline") or ""),
        "investigation_process": str(payload.get("investigation_process") or ""),
        "confirmed_facts": str(payload.get("confirmed_facts") or ""),
        "root_cause_and_solution": str(payload.get("root_cause_and_solution") or ""),
        "verification_results": str(payload.get("verification_results") or ""),
        "limitations_and_unconfirmed": str(payload.get("limitations_and_unconfirmed") or ""),
        "evidence_references": list(payload.get("evidence_references") or []),
        "candidates": candidates,
    }


def summary_packet_content_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(_summary_packet_content(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class HermesSummaryPacket(_StrictModel):
    """Structured close-of-case summary produced by the Summary role.

    Lineage fields bind the packet to one engineer-case episode/revision; the
    content hash covers only the narrative content so lineage bookkeeping can
    never forge a packet's substance.
    """

    schema_version: Literal["v1"]
    summary_id: str = Field(min_length=1)
    engineer_case_id: str = Field(min_length=1)
    client_ticket_id: str = Field(min_length=1)
    investigation_id: str = Field(min_length=1)
    episode: int = Field(ge=1)
    ledger_revision: int = Field(ge=0)
    conversation_version: int = Field(ge=0)
    hermes_session_id: str = Field(min_length=1)
    trigger: Literal["solved", "local_resolved", "closed"]
    problem_description: str = Field(min_length=1)
    timeline: str = ""
    investigation_process: str = Field(min_length=1)
    confirmed_facts: str = ""
    root_cause_and_solution: str = ""
    verification_results: str = ""
    limitations_and_unconfirmed: str = Field(min_length=1)
    evidence_references: tuple[str, ...] = ()
    candidates: tuple[HermesSummaryCandidate, ...] = ()
    content_hash: str = Field(min_length=64, max_length=64)
    created_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_packet(self) -> "HermesSummaryPacket":
        candidate_ids = [item.candidate_id for item in self.candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("summary candidates must have unique ids")
        payload = self.model_dump(mode="json")
        actual = summary_packet_content_hash(payload)
        if not hmac.compare_digest(self.content_hash, actual):
            raise ValueError("content_hash does not match summary content")
        serialized = json.dumps(_summary_packet_content(payload), sort_keys=True).lower()
        if any(marker in serialized for marker in _RESTRICTED_KNOWLEDGE_MARKERS):
            raise ValueError("summary packet contains a restricted identifier")
        return self


class HermesReviewDecision(_StrictModel):
    """One candidate's governance decision produced by the Review role."""

    candidate_id: str = Field(min_length=1)
    candidate_type: Literal["knowledge", "memory", "skill"]
    decision: Literal["no_change", "merge", "supplement", "replace", "new", "human_review"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    proposed_content: str = ""
    target_object: str | None = None
    target_version: str | None = None
    # Memory items in the official WeKnora API carry a kind and an integer
    # importance; the Review classifies memory candidates with both so the
    # write chain can emit the official shape without inventing values.
    kind: str = ""
    importance: int | None = Field(default=None, ge=0)
    source_references: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_decision(self) -> "HermesReviewDecision":
        has_target = bool(self.target_object) or bool(self.target_version)
        if self.decision == "human_review" and has_target:
            raise ValueError("human_review must not claim a resolved target")
        if self.decision in {"no_change", "merge", "supplement", "replace"}:
            if not self.target_object or not self.target_version:
                raise ValueError(f"{self.decision} requires target_object and target_version")
        if self.decision == "new" and has_target:
            raise ValueError("new must not reference an existing target")
        if self.candidate_type == "memory" and self.decision not in {"no_change", "human_review"}:
            if not self.kind.strip():
                raise ValueError("memory decisions must carry the target memory kind")
        return self


def _review_report_content(payload: dict[str, Any]) -> dict[str, Any]:
    decisions = [
        HermesReviewDecision.model_validate(item).model_dump(mode="json")
        for item in (payload.get("decisions") or [])
    ]
    return {"decisions": decisions}


def review_report_content_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(_review_report_content(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class HermesReviewReport(_StrictModel):
    """Validated review outcome returned to SupportPortal by the Review role."""

    schema_version: Literal["v1"]
    review_id: str = Field(min_length=1)
    summary_id: str = Field(min_length=1)
    engineer_case_id: str = Field(min_length=1)
    client_ticket_id: str = Field(min_length=1)
    investigation_id: str = Field(min_length=1)
    episode: int = Field(ge=1)
    ledger_revision: int = Field(ge=0)
    conversation_version: int = Field(ge=0)
    review_session_id: str = Field(min_length=1)
    weknora_available: bool
    decisions: tuple[HermesReviewDecision, ...]
    content_hash: str = Field(min_length=64, max_length=64)
    created_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_report(self) -> "HermesReviewReport":
        candidate_ids = [item.candidate_id for item in self.decisions]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("review decisions must cover each candidate at most once")
        payload = self.model_dump(mode="json")
        actual = review_report_content_hash(payload)
        if not hmac.compare_digest(self.content_hash, actual):
            raise ValueError("content_hash does not match review decisions")
        serialized = json.dumps(_review_report_content(payload), sort_keys=True).lower()
        if any(marker in serialized for marker in _RESTRICTED_KNOWLEDGE_MARKERS):
            raise ValueError("review report contains a restricted identifier")
        return self


HermesWorkflowConflict = HermesRepositoryConflict


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hermes_workflow_mode() -> Literal["disabled", "mock", "real"]:
    mode = str(os.getenv("HERMES_CASE_WORKFLOW_MODE") or "disabled").strip().lower()
    if mode not in {"disabled", "mock", "real"}:
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


def _session_id(case_id: str) -> str:
    return f"hermes-session:{uuid5(NAMESPACE_URL, _conversation_key(case_id))}"


def create_opening_turn(
    *, engineer_case_id: str, client_ticket_id: str, investigation_id: str,
    problem_description: str, investigation_scope: str,
    completion_criteria: tuple[str, ...], now_value: str | None = None,
) -> HermesTurnRequestDraft:
    now = now_value or _now_iso()
    return HermesTurnRequestDraft(
        schema_version="v1",
        request_id=_request_id(engineer_case_id, 1, 0, "opening"),
        engineer_case_id=engineer_case_id,
        client_ticket_id=client_ticket_id,
        investigation_id=investigation_id,
        hermes_conversation_key=_conversation_key(engineer_case_id),
        hermes_session_id=_session_id(engineer_case_id),
        episode=1,
        conversation_version=0,
        turn_kind="opening",
        input=HermesTurnInput(
            problem_description=problem_description,
            investigation_scope=investigation_scope,
            completion_criteria=completion_criteria,
        ),
        slack_channel_id=None,
        slack_thread_ts=None,
        session_binding_version=1,
        data_boundary="curated_case_context",
        human_authority=None,
        created_at=now,
    )


def start_hermes_case(repository: Any, *, request: HermesTurnRequestDraft) -> dict[str, Any]:
    return repository.start_hermes_case(request.model_dump(mode="json"))


def freeze_turn_request_for_delivery(
    repository: Any,
    request: dict[str, Any],
    *,
    slack_channel_id: str,
    slack_thread_ts: str,
) -> HermesTurnRequest:
    payload = {
        key: request[key]
        for key in HermesTurnRequestDraft.model_fields
        if key in request
    }
    payload.update(
        slack_channel_id=str(slack_channel_id or "").strip(),
        slack_thread_ts=str(slack_thread_ts or "").strip(),
    )
    frozen = HermesTurnRequest.model_validate(payload)
    persisted = repository.freeze_hermes_turn_request(
        frozen.request_id,
        payload=frozen.model_dump(mode="json"),
    )
    persisted_payload = {
        key: persisted[key]
        for key in HermesTurnRequest.model_fields
        if key in persisted
    }
    return HermesTurnRequest.model_validate(persisted_payload)


def build_mock_output(request: dict[str, Any], *, now_value: str | None = None) -> HermesInvestigationOutput:
    request_model = HermesTurnRequestDraft.model_validate(
        {key: request[key] for key in HermesTurnRequestDraft.model_fields}
    )
    session_id = request_model.hermes_session_id
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
) -> HermesTurnRequestDraft:
    binding = repository.get_hermes_case_binding(engineer_case_id)
    if not binding:
        raise HermesWorkflowConflict("unknown Hermes Case")
    version = int(binding["conversation_version"]) + 1
    request = HermesTurnRequestDraft(
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
        input=HermesTurnInput(message=input_text),
        slack_channel_id=None,
        slack_thread_ts=None,
        session_binding_version=int(binding["binding_version"]) + 1,
        data_boundary="curated_case_context",
        human_authority=None,
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
    target_round_id = target_output_id
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
        target_round_id = str((target_output or {}).get("round_id") or "")
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
    request = HermesTurnRequestDraft(
        schema_version="v1",
        request_id=(
            _request_id(engineer_case_id, event.episode, event.conversation_version, "round_authority")
            + f":{action}"
        ),
        engineer_case_id=engineer_case_id, client_ticket_id=str(binding["client_ticket_id"]),
        investigation_id=str(binding["investigation_id"]),
        hermes_conversation_key=str(binding["hermes_conversation_key"]),
        hermes_session_id=str(binding.get("hermes_session_id") or ""), episode=event.episode,
        conversation_version=event.conversation_version, turn_kind="round_authority",
        input=HermesTurnInput(message=action), slack_channel_id=None, slack_thread_ts=None,
        session_binding_version=int(binding["binding_version"]),
        data_boundary="curated_case_context",
        human_authority=HermesHumanAuthority(
            authority_event_id=event.authority_event_id,
            actor_id=event.actor_id,
            action=event.action,
            target_round_id=target_round_id,
            target_version=event.target_version,
            target_digest=event.target_digest,
            created_at=event.created_at,
        ),
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
        "sanitized_knowledge": {"summary": summary},
        "evidence_categories": ("synthetic_test",),
        "applicability": ("workflow contract test",),
        "limitations": ("Synthetic mock result only.",),
        "corrections": (),
        "sanitization": {"verdict": "pass", "reason": "canonical_mock_test"},
    }


def _promotion_content_hash(payload: dict[str, Any]) -> str:
    knowledge = SanitizedCaseKnowledge.model_validate(
        payload["sanitized_knowledge"]
    ).model_dump(mode="json")
    corrections = [
        CorrectionRecord.model_validate(item).model_dump(mode="json")
        for item in (payload.get("corrections") or [])
    ]
    promotable = {
        "sanitized_knowledge": knowledge,
        "evidence_categories": list(payload.get("evidence_categories") or []),
        "applicability": list(payload.get("applicability") or []),
        "limitations": list(payload.get("limitations") or []),
        "corrections": corrections,
    }
    return hashlib.sha256(
        json.dumps(promotable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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
) -> HermesTurnRequestDraft:
    binding = repository.get_hermes_case_binding(engineer_case_id)
    if not binding:
        raise HermesWorkflowConflict("unknown Hermes Case")
    episode = int(binding["episode"]) + 1
    version = int(binding["conversation_version"]) + 1
    request = HermesTurnRequestDraft(
        schema_version="v1",
        request_id=_request_id(engineer_case_id, episode, version, "reopen"),
        engineer_case_id=engineer_case_id,
        client_ticket_id=str(binding["client_ticket_id"]),
        investigation_id=str(binding["investigation_id"]),
        hermes_conversation_key=str(binding["hermes_conversation_key"]),
        hermes_session_id=str(binding.get("hermes_session_id") or ""),
        episode=episode,
        conversation_version=version,
        turn_kind="reopen",
        input=HermesTurnInput(message=input_text),
        slack_channel_id=None,
        slack_thread_ts=None,
        session_binding_version=int(binding["binding_version"]) + 1,
        data_boundary="curated_case_context",
        human_authority=None,
        created_at=now_value or _now_iso(),
    )
    repository.reopen_hermes_case(request.model_dump(mode="json"))
    return request


def build_weknora_promotion_tasks(
    *,
    sanitized_payload: dict[str, Any],
    binding: dict[str, Any],
    review_payload: dict[str, Any] | None = None,
    slack_channel_id: str | None = None,
    slack_thread_ts: str | None = None,
    summary_session_id: str | None = None,
    summary_run_id: str | None = None,
    review_session_id: str | None = None,
    review_run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Map Review output to WeKnora promotion tasks (knowledge/memory only).

    When the review provides ``weknora_candidates``, each entry is validated
    against :class:`WeKnoraPromotionCandidate`; structurally invalid entries are
    preserved as synthetic ``human_review`` tasks instead of being dropped, and
    incomplete-but-classifiable candidates pass through for the adapter to
    record as failed writes.  When the review provides no candidates, the
    sanitized close knowledge is promoted as a single ``new`` knowledge
    candidate (the current close-review contract has no memory/skill output).
    """
    source_id = f"{binding['engineer_case_id']}:{binding['episode']}"
    source_version = str(binding["current_ledger_revision"])
    lineage = {
        "engineer_case_id": str(binding["engineer_case_id"]),
        "client_ticket_id": str(binding["client_ticket_id"]),
        "investigation_id": str(binding.get("investigation_id") or "") or None,
        "slack_channel_id": str(slack_channel_id or "") or None,
        "slack_thread_ts": str(slack_thread_ts or "") or None,
        "summary_session_id": str(summary_session_id or "") or None,
        "summary_run_id": str(summary_run_id or "") or None,
        "review_session_id": str(review_session_id or "") or None,
        "review_run_id": str(review_run_id or "") or None,
        "source_type": "hermes_case_promotion",
        "source_id": source_id,
        "source_version": source_version,
    }
    raw_candidates: list[Any] = []
    if isinstance(review_payload, dict):
        value = review_payload.get("weknora_candidates")
        if isinstance(value, list):
            raw_candidates = value

    tasks: list[dict[str, Any]] = []
    for entry in raw_candidates:
        raw = dict(entry) if isinstance(entry, dict) else {"raw_value": entry}
        raw.setdefault("schema_version", "v1")
        try:
            candidate = WeKnoraPromotionCandidate.model_validate(raw)
        except ValueError as exc:
            tasks.append(
                {
                    **lineage,
                    "candidate_type": "knowledge",
                    "decision": "human_review",
                    "content_hash": hashlib.sha256(
                        json.dumps(entry, sort_keys=True, default=str).encode("utf-8")
                    ).hexdigest(),
                    "candidate_payload": {
                        "synthetic_invalid_candidate": True,
                        "validation_error": str(exc),
                        "raw_entry": entry,
                    },
                }
            )
            continue
        payload = candidate.model_dump(mode="json")
        tasks.append(
            {
                **lineage,
                "candidate_type": candidate.candidate_type,
                "decision": candidate.decision,
                "content_hash": _weknora_candidate_hash(payload),
                "candidate_payload": payload,
            }
        )
    if tasks:
        return tasks

    knowledge = SanitizedCaseKnowledge.model_validate(sanitized_payload["sanitized_knowledge"])
    content = json.dumps(
        knowledge.model_dump(mode="json"), sort_keys=True, ensure_ascii=False
    )
    return [
        {
            **lineage,
            "candidate_type": "knowledge",
            "decision": "new",
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "candidate_payload": {
                "schema_version": "v1",
                "candidate_type": "knowledge",
                "decision": "new",
                "title": (
                    f"Hermes case {binding['client_ticket_id']} "
                    f"episode {binding['episode']}"
                ),
                "content": content,
            },
        }
    ]


def _weknora_candidate_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def close_hermes_case(
    repository: Any, *, engineer_case_id: str, sanitized_payload: dict[str, Any],
    now_value: str | None = None,
    weknora_promotions: list[dict[str, Any]] | None = None,
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
            investigation_id=str(binding["investigation_id"]),
            episode=int(binding["episode"]),
            ledger_revision=int(binding["current_ledger_revision"]),
            status="awaiting_transport",
            sanitized_knowledge=sanitized_payload["sanitized_knowledge"],
            evidence_categories=tuple(sanitized_payload.get("evidence_categories") or ()),
            applicability=tuple(sanitized_payload.get("applicability") or ()),
            limitations=tuple(sanitized_payload.get("limitations") or ()),
            corrections=tuple(sanitized_payload.get("corrections") or ()),
            review={"verdict": "pass", "reason": "approved_close_review"},
            guardrail={"verdict": "pass", "reason": "passed_summary_guardrail"},
            sanitization=sanitized_payload["sanitization"],
            closed_revision_proof={
                "status": "closed",
                "episode": int(binding["episode"]),
                "ledger_revision": int(binding["current_ledger_revision"]),
                "closed_at": now,
            },
            content_hash=_promotion_content_hash(sanitized_payload),
            targets=("tencentdb_knowledge", "skill_evolution"),
            created_at=now,
        )
    except ValueError as exc:
        raise HermesWorkflowConflict("sanitized promotion payload is required") from exc
    repository.close_hermes_case(
        promotion.model_dump(mode="json"), now_value=now,
        weknora_promotions=weknora_promotions,
    )
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
