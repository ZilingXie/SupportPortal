"""Contracts and policy boundaries for the split Route/Automation runtimes."""

from __future__ import annotations

import hmac
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


class ExecutionReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operations: list[dict[str, Any]] = Field(min_length=1)


class AutomationLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=512)


def verify_admin_login(email: str, password: str) -> bool:
    """Constant-time check of console admin credentials (default admin/admin).

    Mirrors the /workspace/admin bootstrap-admin experience: the value typed in
    the Email field is matched against ``AUTOMATION_ADMIN_USERNAME`` (default
    ``admin``) and the password against ``AUTOMATION_ADMIN_PASSWORD``.
    """
    expected_user = str(os.getenv("AUTOMATION_ADMIN_USERNAME") or "admin").strip() or "admin"
    expected_password = str(os.getenv("AUTOMATION_ADMIN_PASSWORD") or "admin")
    user_ok = hmac.compare_digest(str(email or "").strip().encode("utf-8"), expected_user.encode("utf-8"))
    password_ok = hmac.compare_digest(str(password or "").encode("utf-8"), expected_password.encode("utf-8"))
    return user_ok and password_ok


@dataclass(frozen=True)
class EnvironmentPolicy:
    environment: AutomationEnvironment
    writes_zendesk: bool
    performs_ownership: bool
    performs_status: bool
    forced_visibility: CommentVisibility | None = None
    requires_visibility: bool = False


POLICIES: dict[AutomationEnvironment, EnvironmentPolicy] = {
    AutomationEnvironment.STAGING: EnvironmentPolicy(
        environment=AutomationEnvironment.STAGING,
        writes_zendesk=False,
        performs_ownership=False,
        performs_status=False,
    ),
    AutomationEnvironment.PREPRODUCTION: EnvironmentPolicy(
        environment=AutomationEnvironment.PREPRODUCTION,
        writes_zendesk=True,
        performs_ownership=True,
        performs_status=True,
        forced_visibility=CommentVisibility.INTERNAL,
    ),
    AutomationEnvironment.PRODUCTION: EnvironmentPolicy(
        environment=AutomationEnvironment.PRODUCTION,
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


def runtime_resource_identity(environment: AutomationEnvironment) -> dict[str, str]:
    """Return and, when enabled, validate the environment resource binding."""
    identity = {
        "environment": environment.value,
        "resource_id": str(os.getenv("AUTOMATION_RESOURCE_ID") or "").strip(),
        "db_resource_id": str(os.getenv("AUTOMATION_DB_RESOURCE_ID") or "").strip(),
        "db_schema": str(os.getenv("AUTOMATION_DB_SCHEMA") or "").strip(),
        "db_table": str(os.getenv("AUTOMATION_DB_TABLE") or "").strip(),
        "redis_url": str(os.getenv("AUTOMATION_REDIS_URL") or "").strip(),
        "queue": str(os.getenv("AUTOMATION_QUEUE_NAME") or "").strip(),
        "event_channel": str(os.getenv("AUTOMATION_EVENT_CHANNEL") or "").strip(),
    }
    if os.getenv("AUTOMATION_RUNTIME_REQUIRE_RESOURCES") == "1":
        missing = [key for key, value in identity.items() if key != "environment" and not value]
        if missing:
            raise RuntimeError(f"automation resource identity missing: {', '.join(missing)}")
        if identity["resource_id"] != environment.value:
            raise RuntimeError("AUTOMATION_RESOURCE_ID does not match AUTOMATION_ENVIRONMENT")
        if identity["db_resource_id"] != environment.value:
            raise RuntimeError("AUTOMATION_DB_RESOURCE_ID does not match AUTOMATION_ENVIRONMENT")
        if environment.value not in identity["db_table"]:
            raise RuntimeError("automation database table is not environment-scoped")
        if environment.value not in identity["redis_url"]:
            raise RuntimeError("AUTOMATION_REDIS_URL is not environment-scoped")
        if environment.value not in identity["queue"] or environment.value not in identity["event_channel"]:
            raise RuntimeError("automation queue/channel is not environment-scoped")
    return identity
