"""Immutable Hermes case-task and customer-message action contracts.

The Account Router remains the source of the initial classification.  This
module only turns that result into a locked task and validates the small
SupportPortal-internal action contract used for later customer comments.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.services.automation_routing import is_registered_automation


CASE_TASK_SCHEMA_VERSION = "hermes-case-task-v1"
MESSAGE_ACTION_CONTRACT_VERSION = "hermes-message-action-v1"
MESSAGE_ACTIONS = frozenset(
    {
        "continue_task",
        "answer_related_question",
        "report_progress",
        "acknowledge",
        "request_clarification",
        "handoff_human",
    }
)


class CaseTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = CASE_TASK_SCHEMA_VERSION
    source: str = "account_router"
    source_event_id: str = Field(min_length=1, max_length=240)
    route_family: str = Field(min_length=1, max_length=120)
    route_target: str = Field(min_length=1, max_length=120)
    execution_action: str = Field(min_length=1, max_length=120)
    route: str | None = Field(default=None, max_length=120)
    direction: str = Field(min_length=1, max_length=32)
    primary_label: str = Field(default="", max_length=200)
    secondary_label: str = Field(default="", max_length=300)
    reason_code: str = Field(min_length=1, max_length=160)
    automation_handler: str | None = Field(default=None, max_length=120)
    confidence: float = Field(ge=0, le=1)
    prompt_release_id: str | None = Field(default=None, max_length=240)
    locked: bool = True
    hermes_eligible: bool = True
    classification_only: bool = False

    @model_validator(mode="after")
    def validate_direction(self) -> "CaseTask":
        if self.direction not in {"automation", "investigation", "human"}:
            raise ValueError("case task direction is invalid")
        if self.direction == "automation" and not self.automation_handler:
            raise ValueError("automation case task requires automation_handler")
        if self.classification_only and self.hermes_eligible:
            raise ValueError("classification_only task cannot be Hermes eligible")
        return self


class MessageAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: str = MESSAGE_ACTION_CONTRACT_VERSION
    action: str
    reason_code: str = Field(min_length=1, max_length=160)
    confidence: float = Field(ge=0, le=1)
    message_role: str = Field(min_length=1, max_length=80)
    independent_request: bool = False

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        value = str(value).strip()
        if value not in MESSAGE_ACTIONS:
            raise ValueError("message action is unknown")
        return value

    @model_validator(mode="after")
    def normalize_independent_request(self) -> "MessageAction":
        if self.independent_request and self.action != "handoff_human":
            return self.model_copy(
                update={"action": "handoff_human", "reason_code": "independent_request"}
            )
        return self


def _decision_fields(result: Any) -> tuple[Any, dict[str, Any]]:
    decision = getattr(result, "decision", None)
    classification = getattr(result, "classification", None)
    if decision is None and isinstance(result, dict):
        decision = result.get("decision") or result
        classification = result.get("classification") or {}
    if decision is None:
        raise ValueError("account route result is missing decision")
    if not isinstance(classification, dict):
        classification = {}
    return decision, classification


def _value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def build_case_task(
    result: Any,
    *,
    source_event_id: str,
    prompt_release_id: str | None = None,
) -> CaseTask:
    """Normalize one Account Router result without changing its raw payload."""

    decision, classification = _decision_fields(result)
    route_family = str(_value(decision, "route_family", "") or "").strip().lower()
    action = str(
        _value(decision, "execution_action", None)
        or _value(decision, "route", None)
        or classification.get("execution_action")
        or ""
    ).strip().lower()
    route_target = str(_value(decision, "route_target", "") or "").strip().lower()
    scope_label = str(_value(decision, "scope_label", "") or "").strip().lower()
    reason_code = str(
        classification.get("route_reason_code")
        or _value(decision, "reason_code", None)
        or _value(decision, "reason", None)
        or "route_unavailable"
    ).strip().lower().replace(" ", "_")
    confidence = float(_value(decision, "confidence", classification.get("confidence", 0)) or 0)
    confidence = max(0.0, min(1.0, confidence))
    handler = str(
        classification.get("automation_handler")
        or _value(decision, "automation_handler", None)
        or action
        or ""
    ).strip().lower() or None
    primary = str(_value(result, "primary_label", None) or classification.get("primary_label") or "")
    secondary = str(_value(result, "secondary_label", None) or classification.get("secondary_label") or "")

    if is_registered_automation(route_family=route_family, execution_action=action):
        return CaseTask(
            source_event_id=source_event_id,
            route_family=route_family or "automated",
            route_target="automation",
            execution_action=action,
            route=action,
            direction="automation",
            primary_label=primary,
            secondary_label=secondary,
            reason_code=reason_code or "registered_automation",
            automation_handler=handler or action,
            confidence=confidence,
            prompt_release_id=prompt_release_id,
        )

    investigation = route_target in {"rag", "technical", "investigation"} or route_family in {
        "technical",
        "investigation",
        "rag",
    } or scope_label in {"agora_technical", "technical", "rag", "investigation"}
    if investigation:
        return CaseTask(
            source_event_id=source_event_id,
            route_family=route_family or "technical",
            route_target=route_target or "rag",
            execution_action=action or "investigation",
            route="investigation",
            direction="investigation",
            primary_label=primary,
            secondary_label=secondary,
            reason_code=reason_code or "technical_request",
            confidence=confidence,
            prompt_release_id=prompt_release_id,
        )

    return CaseTask(
        source_event_id=source_event_id,
        route_family=route_family or "human_review",
        route_target=route_target or "human_review",
        execution_action=action or "human_review",
        route=action or None,
        direction="human",
        primary_label=primary,
        secondary_label=secondary,
        reason_code=reason_code or "classification_only",
        automation_handler=None,
        confidence=confidence,
        prompt_release_id=prompt_release_id,
        hermes_eligible=False,
        classification_only=True,
    )


def parse_message_action(value: Any) -> MessageAction:
    """Strictly parse a model/server payload; prose and unknown fields fail closed."""

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("message action must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("message action must be a JSON object")
    return MessageAction.model_validate(value)


def classify_customer_message(body: str, case_task: dict[str, Any] | CaseTask) -> MessageAction:
    """Conservative fail-closed fallback used before a model action is available.

    It deliberately recognizes only unambiguous reply intents.  Everything
    else is handed to a human, so the fallback can never execute a new task.
    """

    text = " ".join(str(body or "").split()).strip().lower()
    if not text:
        return MessageAction(
            action="handoff_human", reason_code="empty_comment", confidence=1.0,
            message_role="unknown",
        )
    if re.search(r"(另外|另一个|新的需求|独立|unrelated|new request|separate request)", text):
        return MessageAction(
            action="handoff_human", reason_code="independent_request", confidence=0.99,
            message_role="independent_request", independent_request=True,
        )
    if re.search(r"(app\s*id|应用.?id|在哪里找|what is the app|where.*app)", text):
        return MessageAction(
            action="answer_related_question", reason_code="related_app_id_question", confidence=0.96,
            message_role="related_question",
        )
    if re.search(r"(进度|状态|多久|什么时候|status|progress|when|how long)", text):
        return MessageAction(
            action="report_progress", reason_code="progress_inquiry", confidence=0.94,
            message_role="progress_inquiry",
        )
    if re.search(r"(谢谢|收到|好的|ok|okay|thanks|thank you|了解)", text):
        return MessageAction(
            action="acknowledge", reason_code="customer_acknowledgement", confidence=0.9,
            message_role="acknowledgement",
        )
    if re.search(r"(还需要|需要提供|缺少什么|what.*need|which.*provide)", text):
        return MessageAction(
            action="request_clarification", reason_code="required_information", confidence=0.88,
            message_role="clarification",
        )
    task = case_task if isinstance(case_task, dict) else case_task.model_dump()
    if str(task.get("direction") or "") == "automation" and str(task.get("route") or ""):
        return MessageAction(
            action="continue_task", reason_code="task_continuation", confidence=0.72,
            message_role="task_update",
        )
    return MessageAction(
        action="handoff_human", reason_code="message_action_uncertain", confidence=0.0,
        message_role="unknown",
    )

