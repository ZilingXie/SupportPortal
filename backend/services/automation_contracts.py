"""Contracts and policy boundaries for the split Route/Automation runtimes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


CONTRACT_VERSION = "route-automation-v1"


class AutomationEnvironment(StrEnum):
    STAGING = "staging"
    PREPRODUCTION = "preproduction"
    PRODUCTION = "production"


class CommentVisibility(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str = CONTRACT_VERSION
    request_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=240)
    expected_environment: AutomationEnvironment
    case_id: str = Field(min_length=1, max_length=160)
    subject: str = Field(default="", max_length=300)
    question: str = Field(min_length=1, max_length=12000)
    customer_email: str | None = Field(default=None, max_length=320)
    customer_name: str | None = Field(default=None, max_length=160)
    ticket_context: list[dict[str, str]] = Field(default_factory=list)
    source: str = Field(default="automation", max_length=80)
    build_ref: str = Field(default="unknown", max_length=160)


class RouteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str = CONTRACT_VERSION
    request_id: str
    idempotency_key: str
    environment: AutomationEnvironment
    case_id: str
    route: dict[str, Any]
    automation: dict[str, Any]
    action_plan: dict[str, Any] = Field(default_factory=dict)
    prompt_snapshots: dict[str, dict[str, str]] = Field(default_factory=dict)
    build_ref: str = "unknown"


class AutomationExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=160)
    case_id: str = Field(min_length=1, max_length=160)
    subject: str = Field(default="", max_length=300)
    question: str = Field(min_length=1, max_length=12000)
    customer_email: str | None = Field(default=None, max_length=320)
    customer_name: str | None = Field(default=None, max_length=160)
    zendesk_ticket_id: str | None = Field(default=None, max_length=128)
    comment_visibility: CommentVisibility | None = None
    ticket_context: list[dict[str, str]] = Field(default_factory=list)


@dataclass(frozen=True)
class EnvironmentPolicy:
    environment: AutomationEnvironment
    allow_rerun: bool
    allow_reset: bool
    writes_zendesk: bool
    performs_ownership: bool
    performs_status: bool
    forced_visibility: CommentVisibility | None = None
    requires_visibility: bool = False


POLICIES: dict[AutomationEnvironment, EnvironmentPolicy] = {
    AutomationEnvironment.STAGING: EnvironmentPolicy(
        environment=AutomationEnvironment.STAGING,
        allow_rerun=True,
        allow_reset=True,
        writes_zendesk=False,
        performs_ownership=False,
        performs_status=False,
    ),
    AutomationEnvironment.PREPRODUCTION: EnvironmentPolicy(
        environment=AutomationEnvironment.PREPRODUCTION,
        allow_rerun=True,
        allow_reset=False,
        writes_zendesk=True,
        performs_ownership=True,
        performs_status=True,
        forced_visibility=CommentVisibility.INTERNAL,
    ),
    AutomationEnvironment.PRODUCTION: EnvironmentPolicy(
        environment=AutomationEnvironment.PRODUCTION,
        allow_rerun=False,
        allow_reset=False,
        writes_zendesk=True,
        performs_ownership=True,
        performs_status=True,
        requires_visibility=True,
    ),
}


def environment_from_env(default: AutomationEnvironment = AutomationEnvironment.STAGING) -> AutomationEnvironment:
    raw = str(os.getenv("AUTOMATION_ENVIRONMENT") or default.value).strip().lower()
    try:
        return AutomationEnvironment(raw)
    except ValueError as exc:
        raise RuntimeError(f"invalid AUTOMATION_ENVIRONMENT: {raw!r}") from exc


def policy_for(environment: AutomationEnvironment) -> EnvironmentPolicy:
    return POLICIES[AutomationEnvironment(environment)]


def resolve_comment_visibility(
    environment: AutomationEnvironment,
    requested: CommentVisibility | None,
) -> CommentVisibility | None:
    policy = policy_for(environment)
    if policy.forced_visibility is not None:
        if requested is not None and requested != policy.forced_visibility:
            raise ValueError(
                f"{environment.value} only permits {policy.forced_visibility.value} comments"
            )
        return policy.forced_visibility
    if policy.requires_visibility and requested is None:
        raise ValueError("production execution requires comment_visibility")
    if not policy.writes_zendesk and requested is not None:
        raise ValueError(f"{environment.value} cannot select Zendesk comment visibility")
    return requested


def preproduction_ticket_allowlist() -> frozenset[str]:
    raw = str(os.getenv("PREPRODUCTION_ZENDESK_TICKET_ALLOWLIST") or "")
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def validate_ticket_policy(
    environment: AutomationEnvironment,
    zendesk_ticket_id: str | None,
    requested_visibility: CommentVisibility | None,
) -> CommentVisibility | None:
    visibility = resolve_comment_visibility(environment, requested_visibility)
    policy = policy_for(environment)
    normalized_ticket = str(zendesk_ticket_id or "").strip()
    if policy.writes_zendesk and not normalized_ticket:
        raise ValueError(f"{environment.value} requires zendesk_ticket_id")
    if environment == AutomationEnvironment.PREPRODUCTION:
        allowlist = preproduction_ticket_allowlist()
        if not allowlist or normalized_ticket not in allowlist:
            raise ValueError("preproduction ticket is not in the configured allowlist")
    return visibility
