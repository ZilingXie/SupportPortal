from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from backend.services.automation_ecs_contracts import (
    AutomationIntakeEvent,
    INTAKE_CONTRACT_VERSION,
    canonical_payload_digest,
)
from backend.services.automation_ecs_runtime import AutomationEcsSettings


def _ticket() -> dict[str, object]:
    return {
        "id": "12345",
        "status": "open",
        "subject": "Enable Media Relay",
        "description": "Please enable Media Relay for app abc.",
        "requester": {"id": "88", "name": "Customer", "email": "cx@example.com"},
        "tags": ["automation"],
        "custom_fields": {"product": "Media Relay"},
    }


def _created_event() -> dict[str, object]:
    return {
        "schema_version": INTAKE_CONTRACT_VERSION,
        "event_id": "zendesk:ticket:12345:created",
        "event_type": "ticket.created",
        "occurred_at": "2026-08-27T10:00:00Z",
        "ticket": _ticket(),
    }


def test_ticket_created_uses_zendesk_ticket_id_without_internal_case_id() -> None:
    event = AutomationIntakeEvent.model_validate(_created_event())

    assert event.ticket.id == "12345"
    assert event.routing_text() == "Please enable Media Relay for app abc."
    assert "case_id" not in event.model_dump()


def test_comment_event_requires_complete_snapshot_and_trigger() -> None:
    payload = _created_event()
    payload.update(
        event_id="zendesk:comment:9001",
        event_type="comment.created",
        comment_snapshot={
            "source_updated_at": "2026-08-27T10:01:00Z",
            "snapshot_complete": True,
            "trigger_comment_id": "9001",
            "comments": [
                {
                    "id": "9001",
                    "public": True,
                    "author": {"id": "88", "role": "end-user", "is_agent": False},
                    "body": "The app id is abc.",
                    "created_at": "2026-08-27T10:01:00Z",
                }
            ],
        },
    )
    event = AutomationIntakeEvent.model_validate(payload)
    assert event.routing_text() == "The app id is abc."

    payload["comment_snapshot"] = {
        **payload["comment_snapshot"],  # type: ignore[arg-type]
        "trigger_comment_id": "missing",
    }
    with pytest.raises(ValidationError, match="trigger_comment_id"):
        AutomationIntakeEvent.model_validate(payload)


def test_event_and_ticket_ids_fail_closed() -> None:
    payload = _created_event()
    payload["event_id"] = "contains spaces"
    with pytest.raises(ValidationError, match="event_id"):
        AutomationIntakeEvent.model_validate(payload)

    payload = _created_event()
    payload["ticket"] = {**_ticket(), "id": "AC-12345"}
    with pytest.raises(ValidationError, match="ticket id must be numeric"):
        AutomationIntakeEvent.model_validate(payload)


def test_payload_digest_is_canonical() -> None:
    event = AutomationIntakeEvent.model_validate(_created_event())
    assert canonical_payload_digest(event) == canonical_payload_digest(event.model_dump(mode="json"))


def test_runtime_settings_generate_environment_path_and_provenance() -> None:
    env = {
        "AUTOMATION_ENVIRONMENT": "preproduction",
        "AUTOMATION_DB_SCHEMA": "supportportal_preproduction",
        "AUTOMATION_DB_RESOURCE_ID": "rds-preproduction",
        "AUTOMATION_JOB_NAMESPACE": "automation.preproduction",
        "AUTOMATION_INTAKE_SHARED_TOKEN": "secret",
        "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1",
        "AUTOMATION_RELEASE_ID": "r20260827-54e8235",
        "AUTOMATION_IMAGE_DIGEST": "sha256:" + "a" * 64,
        "APP_BUILD_REF": "54e8235",
        "PROMPT_RELEASE_ID": "prompt-42",
    }
    with patch.dict(os.environ, env, clear=True):
        settings = AutomationEcsSettings.from_env("api")

    assert settings.base_path == "/automation/preproduction"
    provenance = settings.provenance()
    assert provenance.environment == "preproduction"
    assert provenance.service_role == "api"
    assert provenance.release_id == "r20260827-54e8235"
    assert provenance.contracts["intake"] == INTAKE_CONTRACT_VERSION


def test_runtime_rejects_staging_and_mismatched_base_path() -> None:
    common = {
        "AUTOMATION_DB_SCHEMA": "supportportal_staging",
        "AUTOMATION_DB_RESOURCE_ID": "rds-staging",
        "AUTOMATION_JOB_NAMESPACE": "automation.staging",
        "AUTOMATION_INTAKE_SHARED_TOKEN": "secret",
        "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1",
    }
    with patch.dict(os.environ, {**common, "AUTOMATION_ENVIRONMENT": "staging"}, clear=True):
        with pytest.raises(RuntimeError, match="preproduction or production"):
            AutomationEcsSettings.from_env("api")

    with patch.dict(
        os.environ,
        {
            **common,
            "AUTOMATION_ENVIRONMENT": "production",
            "AUTOMATION_BASE_PATH": "/production/automation",
        },
        clear=True,
    ):
        with pytest.raises(RuntimeError, match="must match"):
            AutomationEcsSettings.from_env("api")


def test_runtime_rejects_cross_environment_schema_or_job_namespace() -> None:
    common = {
        "AUTOMATION_ENVIRONMENT": "preproduction",
        "AUTOMATION_DB_RESOURCE_ID": "shared-rds",
        "AUTOMATION_INTAKE_SHARED_TOKEN": "secret",
        "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1",
    }
    with patch.dict(
        os.environ,
        {
            **common,
            "AUTOMATION_DB_SCHEMA": "supportportal_production",
            "AUTOMATION_JOB_NAMESPACE": "automation.preproduction",
        },
        clear=True,
    ):
        with pytest.raises(RuntimeError, match="DB_SCHEMA"):
            AutomationEcsSettings.from_env("api")
    with patch.dict(
        os.environ,
        {
            **common,
            "AUTOMATION_DB_SCHEMA": "supportportal_preproduction",
            "AUTOMATION_JOB_NAMESPACE": "automation.production",
        },
        clear=True,
    ):
        with pytest.raises(RuntimeError, match="JOB_NAMESPACE"):
            AutomationEcsSettings.from_env("api")
