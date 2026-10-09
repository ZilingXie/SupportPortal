from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services.hermes_case_task import (
    CaseTask,
    build_case_task,
    parse_message_action,
)


def _result(**overrides):
    decision = {
        "route_family": "automated",
        "execution_action": "enablement",
        "route": "enablement",
        "route_target": "automation",
        "scope_label": "backend_operation",
        "confidence": 0.91,
        "reason": "registered_enablement",
    }
    decision.update(overrides)
    return SimpleNamespace(
        decision=SimpleNamespace(**decision),
        classification={"automation_handler": "enablement", "route_reason_code": "registered_enablement"},
        primary_label="Agora",
        secondary_label="Backend Operation / Enablement",
    )


def test_registered_automation_becomes_locked_hermes_task() -> None:
    task = build_case_task(_result(), source_event_id="zendesk:ticket:1:created", prompt_release_id="pr-1")

    assert isinstance(task, CaseTask)
    assert task.hermes_eligible is True
    assert task.direction == "automation"
    assert task.route == "enablement"
    assert task.locked is True


def test_human_review_is_classification_only() -> None:
    task = build_case_task(
        _result(route_family="human_review", execution_action="human_review", route=None, route_target="human_review"),
        source_event_id="zendesk:ticket:2:created",
    )

    assert task.hermes_eligible is False
    assert task.classification_only is True
    assert task.direction == "human"


def test_message_action_is_strict_and_independent_request_fails_closed() -> None:
    action = parse_message_action(
        '{"contract_version":"hermes-message-action-v1","action":"continue_task",'
        '"reason_code":"new_request","confidence":0.8,"message_role":"request",'
        '"independent_request":true}'
    )
    assert action.action == "handoff_human"

    with pytest.raises(ValueError):
        parse_message_action("model prose")
    with pytest.raises(ValueError):
        parse_message_action(
            {
                "contract_version": "hermes-message-action-v1",
                "action": "unknown",
                "reason_code": "x",
                "confidence": 0.5,
                "message_role": "x",
            }
        )
    with pytest.raises(ValueError):
        parse_message_action(
            {
                "contract_version": "old-contract",
                "action": "acknowledge",
                "reason_code": "ack",
                "confidence": 0.5,
                "message_role": "acknowledgement",
            }
        )
