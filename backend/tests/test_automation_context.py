from copy import deepcopy
from unittest.mock import Mock

import pytest

from backend.services.automation_context import (
    build_automation_context, evidence_messages, persona_context, public_messages,
)
from backend.services.enablement_field_extractor import extract_enablement_fields


def context_fixture():
    messages = [
        {"message_id": "request", "role": "customer", "content": "Enable Media Relay"},
        {"message_id": "question", "role": "assistant", "content": "Please provide the App ID."},
        {"message_id": "current", "role": "customer", "content": "can you try: " + "b" * 32},
    ]
    fields = {"requested_feature": "media_relay", "requested_feature_label": "Media Relay"}
    context = build_automation_context(
        {"ticket_id": "synthetic", "messages": messages},
        {"collected_fields": fields, "automation_handler": "enablement"},
        current_message=messages[-1],
    )
    return messages, fields, context


@pytest.mark.parametrize("repeat_trusted", [False, True])
def test_current_app_id_with_omitted_or_repeated_trusted_feature(repeat_trusted):
    messages, fields, context = context_fixture()
    payload = {"status": "complete", "fields": {"app_id": {
        "value": "b" * 32, "source_message_id": "current",
        "source_quote": "b" * 32, "confidence": 0.99,
    }}}
    if repeat_trusted:
        payload["fields"]["requested_feature"] = {
            "value": "media_relay", "original_label": "Media Relay",
            "source_message_id": "old-unavailable-source",
            "source_quote": "Enable Media Relay", "confidence": 0.99,
        }
    invoke = Mock(side_effect=lambda **_: deepcopy(payload))
    result = extract_enablement_fields(
        ticket_subject="Enable Media Relay", customer_messages=messages,
        existing_fields=fields, automation_context=context, invoke=invoke,
    )
    assert result.status == "complete"
    assert result.collected_fields["app_id"] == "b" * 32
    assert result.collected_fields["requested_feature"] == "media_relay"
    assert result.source_message_ids["app_id"] == "current"
    # A fully grounded new App ID does not need the existing exceptional verifier.
    assert invoke.call_count == 1
    diagnostic = result.audit_payload()["field_diagnostics"]
    assert diagnostic[0]["source_allowed"] is True
    assert diagnostic[0]["quote_matches"] is True
    assert "b" * 32 not in str(diagnostic)
    if repeat_trusted:
        assert diagnostic[1]["trusted_unchanged"] is True
        assert diagnostic[1]["source_exists"] is False


def test_history_is_understanding_not_current_evidence():
    messages, _, context = context_fixture()
    assert [m["message_id"] for m in evidence_messages(messages, context)] == ["current"]
    assert [m["role"] for m in context["conversation"]] == ["customer", "assistant", "customer"]


def test_private_and_draft_messages_are_excluded():
    messages = [
        {"role": "assistant", "content": "private", "is_public": False},
        {"role": "assistant", "content": "draft", "status": "draft"},
        {"role": "system", "content": "internal"},
        {"role": "customer", "content": "visible", "message_id": "public"},
    ]
    assert [m["content"] for m in public_messages(messages)] == ["visible"]


def test_persona_context_redacts_without_mutating_source():
    _, _, context = context_fixture()
    original = deepcopy(context)
    sanitized = persona_context(context)
    assert "b" * 32 not in str(sanitized)
    assert "business_state" not in sanitized
    assert sanitized["current_message_id"] == "current"
    assert context == original


def test_context_excludes_comments_after_trigger():
    messages = [
        {"message_id": "later", "role": "customer", "created_at": "2026-09-07T00:04:00Z",
         "content": "Use a different project"},
        {"message_id": "current", "role": "customer", "created_at": "2026-09-07T00:03:00Z",
         "content": "can you try: " + "b" * 32},
        {"message_id": "question", "role": "assistant", "created_at": "2026-09-07T00:02:00Z",
         "content": "Please provide the App ID."},
        {"message_id": "request", "role": "customer", "created_at": "2026-09-07T00:01:00Z",
         "content": "Enable Media Relay"},
    ]
    fields = {"requested_feature": "media_relay"}
    trigger = messages[1]
    context = build_automation_context({"ticket_id": "synthetic", "messages": messages},
                                       {"collected_fields": fields}, current_message=trigger)
    assert [m["message_id"] for m in context["conversation"]] == ["request", "question", "current"]
    assert context["evidence_message_ids"] == ["current"]


def test_missing_trigger_fails_explicitly():
    messages, fields, _ = context_fixture()
    with pytest.raises(ValueError, match="automation_context_current_message_not_found"):
        build_automation_context({"ticket_id": "synthetic", "messages": messages},
                                 {"collected_fields": fields},
                                 current_message={"message_id": "absent"})


def test_context_rejects_case_from_another_ticket():
    with pytest.raises(ValueError, match="automation_context_ticket_mismatch"):
        build_automation_context(
            {"ticket_id": "ticket-a", "messages": []},
            {"client_ticket_id": "ticket-b"},
        )
