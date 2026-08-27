"""Versioned contracts for the ECS Automation runtime."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


INTAKE_CONTRACT_VERSION = "automation-intake-v1"
ROUTE_CONTRACT_VERSION = "automation-route-v1"
PROCESSING_CONTRACT_VERSION = "automation-processing-v1"
EXECUTION_CONTRACT_VERSION = "automation-execution-v1"
HEARTBEAT_CONTRACT_VERSION = "automation-heartbeat-v1"
RELEASE_MANIFEST_VERSION = "automation-release-v1"
SCHEMA_REVISION = "automation-ecs-001"

_NUMERIC_ID_RE = re.compile(r"^\d{1,128}$")
_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,239}$")


class IntakeEventType(StrEnum):
    TICKET_CREATED = "ticket.created"
    TICKET_UPDATED = "ticket.updated"
    COMMENT_CREATED = "comment.created"


class ExecutionStatus(StrEnum):
    ROUTE_PENDING = "route_pending"
    ROUTING = "routing"
    PROCESSING_PENDING = "processing_pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    HUMAN_REVIEW = "human_review"
    OUTCOME_UNKNOWN = "outcome_unknown"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    OUTCOME_UNKNOWN = "outcome_unknown"


class JobKind(StrEnum):
    ROUTE = "route"
    PROCESSING = "processing"


class JobStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"
    HUMAN_REVIEW = "human_review"
    OUTCOME_UNKNOWN = "outcome_unknown"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class ZendeskIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, max_length=320)
    role: str | None = Field(default=None, max_length=64)
    is_agent: bool | None = None


class ZendeskComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    public: bool
    author: ZendeskIdentity = Field(default_factory=ZendeskIdentity)
    body: str = Field(default="", max_length=100_000)
    via_channel: str | None = Field(default=None, max_length=160)
    created_at: datetime

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        normalized = str(value).strip()
        if not _NUMERIC_ID_RE.fullmatch(normalized):
            raise ValueError("Zendesk comment id must be numeric")
        return normalized


class ZendeskCommentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_updated_at: datetime
    snapshot_complete: Literal[True]
    comments: list[ZendeskComment] = Field(max_length=10_000)
    trigger_comment_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_trigger(self) -> "ZendeskCommentSnapshot":
        ids = [comment.id for comment in self.comments]
        if len(ids) != len(set(ids)):
            raise ValueError("Zendesk comment ids must be unique")
        if self.trigger_comment_id not in set(ids):
            raise ValueError("trigger_comment_id must be present in comments")
        return self


class ZendeskTicketSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=32)
    subject: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=12_000)
    requester: ZendeskIdentity = Field(default_factory=ZendeskIdentity)
    organization: ZendeskIdentity | None = None
    tags: list[str] = Field(default_factory=list, max_length=200)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        normalized = str(value).strip()
        if not _NUMERIC_ID_RE.fullmatch(normalized):
            raise ValueError("Zendesk ticket id must be numeric")
        return normalized

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in {"new", "open", "pending", "hold", "solved", "closed"}:
            raise ValueError("Zendesk ticket status is invalid")
        return normalized

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            tag = str(value).strip()
            if not tag or len(tag) > 160:
                raise ValueError("Zendesk tags must be 1-160 characters")
            normalized.append(tag)
        return normalized


class AutomationIntakeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[INTAKE_CONTRACT_VERSION]
    event_id: str = Field(min_length=1, max_length=240)
    event_type: IntakeEventType
    occurred_at: datetime
    ticket: ZendeskTicketSnapshot
    comment_snapshot: ZendeskCommentSnapshot | None = None

    @field_validator("event_id")
    @classmethod
    def validate_event_id(cls, value: str) -> str:
        normalized = str(value).strip()
        if not _EVENT_ID_RE.fullmatch(normalized):
            raise ValueError("event_id contains unsupported characters")
        return normalized

    @model_validator(mode="after")
    def validate_event_shape(self) -> "AutomationIntakeEvent":
        if self.event_type == IntakeEventType.COMMENT_CREATED and self.comment_snapshot is None:
            raise ValueError("comment.created requires a complete comment_snapshot")
        if self.event_type != IntakeEventType.COMMENT_CREATED and self.comment_snapshot is not None:
            raise ValueError("comment_snapshot is only valid for comment.created")
        if self.event_type == IntakeEventType.TICKET_CREATED and not self.ticket.description.strip():
            raise ValueError("ticket.created requires ticket.description")
        return self

    def routing_text(self) -> str:
        if self.comment_snapshot is not None:
            trigger_id = self.comment_snapshot.trigger_comment_id
            trigger = next(comment for comment in self.comment_snapshot.comments if comment.id == trigger_id)
            return trigger.body.strip()
        return self.ticket.description.strip()


class IntakeReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[EXECUTION_CONTRACT_VERSION] = EXECUTION_CONTRACT_VERSION
    environment: Literal["preproduction", "production"]
    event_id: str
    zendesk_ticket_id: str
    execution_id: str
    status: ExecutionStatus
    idempotent_replay: bool = False


class RouteJobPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[ROUTE_CONTRACT_VERSION] = ROUTE_CONTRACT_VERSION
    execution_id: str
    event: AutomationIntakeEvent


class ProcessingJobPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[PROCESSING_CONTRACT_VERSION] = PROCESSING_CONTRACT_VERSION
    execution_id: str
    event: AutomationIntakeEvent
    route: dict[str, Any]
    persona: dict[str, Any] | None = None
    prompt_snapshots: dict[str, Any] = Field(default_factory=dict)


class RuntimeProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment: Literal["preproduction", "production"]
    service_role: Literal["api", "route", "worker", "bootstrap"]
    runtime_identity: str
    release_id: str
    git_commit: str
    image_digest: str
    build_time: str
    schema_revision: Literal[SCHEMA_REVISION] = SCHEMA_REVISION
    prompt_release_id: str
    db_resource_id: str
    db_schema: str
    job_namespace: str
    contracts: dict[str, str]


def canonical_payload_digest(payload: BaseModel | dict[str, Any]) -> str:
    value = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

