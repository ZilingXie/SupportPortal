from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

from backend.services.automation_account_reply_sync import ReplySyncError
from backend.services.automation_engineer_collab import handle_slack_engineer_message

_TICKET = {
    "ticket_id": "13240",
    "status": "investigating",
    "updated_at": "2026-09-02T09:04:00+00:00",
    "subject": "Native SDK crash on Android 14",
    "requester": "customer@example.com",
    "messages": [
        {"role": "customer", "content": "The app crashes when joining a channel.", "created_at": "2026-09-02T09:00:00+00:00"},
        {"role": "assistant", "content": "We are investigating.", "created_at": "2026-09-02T09:01:00+00:00"},
    ],
    "engineer_agent_state": {},
}

_CASE_PAYLOAD = {
    "engineer_case_id": "13240-1",
    "client_ticket_ref": {"ticket_id": "13240"},
    "active_investigation": {
        "id": "INV-13240-1",
        "state": "active",
        "messages": [],
    },
    "engineer_agent_state": {},
}


class _Repo:
    def __init__(self) -> None:
        self.saved_cases: list[dict] = []
        self.saved_case_saves: list[tuple] = []
        self.persona_assignment = {"persona_key": "default-support", "version": 7}

    def get_engineer_case(self, case_id, include_client_messages=False):
        return dict(_CASE_PAYLOAD) if case_id == "13240-1" else None

    def get_ticket(self, ticket_id):
        return dict(_TICKET) if ticket_id == "13240" else None

    def list_ticket_engineer_cases(self, ticket_id, include_client_messages=False):
        return []

    def get_account_case_by_ticket_id(self, ticket_id):
        return {"account_case_id": "AC-13240", "customer_name": "Ziling Xie", "title": "Native SDK crash"}

    def get_hermes_case_binding(self, engineer_case_id):
        return None

    def resolve_account_persona(self, ticket_id):
        return dict(self.persona_assignment)

    def begin_idempotent_request(self, scope, event_id, **kwargs):
        return {"created": True}

    def complete_idempotent_request(self, scope, event_id, **kwargs):
        return None

    def fail_idempotent_request(self, scope, event_id, **kwargs):
        return None

    def save_ticket(self, ticket, new_messages=None):
        return None

    def save_engineer_case(self, engineer_case, new_messages=None, slack_events=None):
        self.saved_cases.append(engineer_case)
        self.saved_case_saves.append((list(new_messages or []), list(slack_events or [])))


def _awaiting_result() -> dict[str, Any]:
    return {
        "active_investigation": {
            "id": "INV-13240-1",
            "state": "awaiting_confirmation",
            "draft_customer_reply": "",
            "final_confirmation_requested_at": None,
            "messages": [],
        },
        "new_internal_messages": [
            {
                "id": "INV-13240-1-m1",
                "role": "engineer_ai",
                "content": "Investigation concluded: native library packaging issue confirmed.",
                "created_at": "2026-09-02T09:05:00+00:00",
            }
        ],
    }


def _active_result() -> dict[str, Any]:
    return {
        "active_investigation": {
            "id": "INV-13240-1",
            "state": "active",
            "draft_customer_reply": "",
            "messages": [],
        },
        "new_internal_messages": [
            {
                "id": "INV-13240-1-m2",
                "role": "engineer_ai",
                "content": "Please share the crash stack trace to continue.",
                "created_at": "2026-09-02T09:05:00+00:00",
            }
        ],
    }


def _payload(event_id: str = "evt-1") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "engineer_case_id": "13240-1",
        "slack_user_id": "U1",
        "text": "investigation update: confirmed missing native library",
        "occurred_at": "2026-09-02T09:04:00+00:00",
    }


def _case_context_after_append(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticket_id": "13240",
        "subject": _TICKET["subject"],
        "requester": _TICKET["requester"],
        "messages": _TICKET["messages"],
        "active_investigation": result["active_investigation"],
        "engineer_agent_state": {
            "conversation_version": 1,
            "draft_version": 0,
            "known_facts": ["SDK 4.5.1", "Pixel 8 Android 14"],
            "reply_readiness": {
                "has_conclusion": True,
                "has_proof": True,
                "reply_scope": "root_cause_confirmed",
                "conclusion_summary": "APK is missing the Agora arm64-v8a native libraries.",
                "proof_summary": "UnsatisfiedLinkError for libagora-ffmpeg.so on first joinChannel.",
                "solution_or_next_step": "Add abiFilters arm64-v8a in build.gradle.",
                "proof_anchors": ["libagora-ffmpeg.so"],
            },
        },
    }


class _FakeResult:
    def __init__(self, content: str, prompt_version: str = "engineer-investigation-persona-v1") -> None:
        self.content = content
        self.model = "gpt-5.6-luna"
        self.prompt_version = prompt_version


def test_awaiting_investigation_assembles_persona_draft() -> None:
    repo = _Repo()
    rendered_calls: list[dict] = []

    def _append(case_context, *, engineer_message, now_value, ai_turn_builder, message_role, message_meta):
        result = _awaiting_result()
        case_context["active_investigation"] = result["active_investigation"]
        case_context["engineer_agent_state"] = _case_context_after_append(result)["engineer_agent_state"]
        return result

    def _render(*, reply_facts, persona_assignment, account_scope):
        rendered_calls.append({"facts": reply_facts, "account_scope": account_scope})
        return _FakeResult("Hi Ziling\n\nPlease add the arm64-v8a ABI filter and rebuild.")

    with patch(
        "backend.services.automation_engineer_collab.append_engineer_investigation_message", _append
    ), patch(
        "backend.services.automation_persona.render_automation_reply", _render
    ):
        response = asyncio.run(handle_slack_engineer_message(repo, _payload()))

    assert response["status"] == "processed"
    assert len(rendered_calls) == 1
    facts = rendered_calls[0]["facts"]
    assert facts["reply_intent"] == "engineer_investigation_reply"
    assert facts["customer_first_name"] == "Ziling"
    assert "APK is missing the Agora arm64-v8a native libraries." in facts["provided_answer"]
    assert facts["provided_answer"].index("Conclusion:") < facts["provided_answer"].index("Suggested resolution:")
    assert rendered_calls[0]["account_scope"] is False

    engineer_case = repo.saved_cases[-1]
    new_messages, slack_events = repo.saved_case_saves[-1]
    assert engineer_case["draft_customer_reply"] == "Hi Ziling\n\nPlease add the arm64-v8a ABI filter and rebuild."
    readiness = engineer_case["engineer_agent_state"]["reply_readiness"]
    assert readiness["source_mode"] == "persona_assembled"
    assert readiness["ready_for_customer_reply"] is True
    assert engineer_case["engineer_agent_state"]["guided_reply_generation"]["persona_key"] == "default-support"
    assert new_messages, "investigation messages must persist"
    assert slack_events, "slack thread event must persist"
    thread_event = slack_events[-1]
    assert thread_event["action"] == "guardrail"
    assert "Persona: default-support v7" in thread_event["message_text"]
    assert "Customer draft:" in thread_event["message_text"]


def test_persona_failure_persists_failed_event_and_raises() -> None:
    from backend.services.automation_persona import AutomationPersonaError

    repo = _Repo()

    def _append(case_context, *, engineer_message, now_value, ai_turn_builder, message_role, message_meta):
        result = _awaiting_result()
        case_context["active_investigation"] = result["active_investigation"]
        case_context["engineer_agent_state"] = _case_context_after_append(result)["engineer_agent_state"]
        return result

    def _render(*, reply_facts, persona_assignment, account_scope):
        raise AutomationPersonaError("automation_persona_guided_customer_name_missing")

    with patch(
        "backend.services.automation_engineer_collab.append_engineer_investigation_message", _append
    ), patch(
        "backend.services.automation_persona.render_automation_reply", _render
    ):
        try:
            asyncio.run(handle_slack_engineer_message(repo, _payload()))
            raise AssertionError("expected ReplySyncError")
        except ReplySyncError as exc:
            assert exc.status_code == 502

    _, slack_events = repo.saved_case_saves[-1]
    assert slack_events and slack_events[-1]["event_type"] == "engineer_ai_response_failed"
    assert "automation_persona_guided_customer_name_missing" in slack_events[-1]["message_text"]


def test_active_investigation_skips_persona() -> None:
    repo = _Repo()

    def _append(case_context, *, engineer_message, now_value, ai_turn_builder, message_role, message_meta):
        result = _active_result()
        case_context["active_investigation"] = result["active_investigation"]
        case_context["engineer_agent_state"] = _case_context_after_append(result)["engineer_agent_state"]
        return result

    def _render(*, reply_facts, persona_assignment, account_scope):
        raise AssertionError("persona must not be called for active investigations")

    with patch(
        "backend.services.automation_engineer_collab.append_engineer_investigation_message", _append
    ), patch(
        "backend.services.automation_persona.render_automation_reply", _render
    ):
        response = asyncio.run(handle_slack_engineer_message(repo, _payload()))

    assert response["status"] == "processed"
    _, slack_events = repo.saved_case_saves[-1]
    assert slack_events and slack_events[-1].get("action") is None
    assert "Customer draft:" not in slack_events[-1]["message_text"]
