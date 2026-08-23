from __future__ import annotations

import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.services.account_reply_rag_fallback import (  # noqa: E402
    INTERNAL_NOTE_HEADLINE,
    RagFallbackOutcome,
    escalate_unexpected_reply_to_human,
    rag_fallback_enabled,
    rag_fallback_timeout_seconds,
    should_run_reply_rag_fallback,
    try_rag_fallback_answer,
)
from backend.services.rag_service_client import RagServiceError  # noqa: E402
from backend.services.zendesk_comments import ZendeskCommentError  # noqa: E402


class _FakeRepository:
    def __init__(self) -> None:
        self.saved_cases: list[dict[str, Any]] = []
        self.cancelled: list[tuple[str, str]] = []
        self.audit_events: list[dict[str, Any]] = []

    def save_account_case(self, account_case: dict[str, Any]) -> None:
        self.saved_cases.append(dict(account_case))

    def cancel_pending_account_reply_jobs(self, ticket_id: str, updated_at: str) -> int:
        self.cancelled.append((ticket_id, updated_at))
        return 2

    def record_workspace_audit_event(self, event_type: str, **kwargs: Any) -> None:
        self.audit_events.append({"event_type": event_type, **kwargs})


class _FakeRagClient:
    def __init__(self, payload: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def query(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.payload is not None
        return self.payload


class RagFallbackGatingTest(unittest.TestCase):
    def test_enabled_by_default_and_disabled_via_env(self) -> None:
        self.assertTrue(rag_fallback_enabled())
        with patch.dict(os.environ, {"ACCOUNT_REPLY_RAG_FALLBACK_ENABLED": "false"}):
            self.assertFalse(rag_fallback_enabled())

    def test_timeout_defaults_to_120_and_honors_env(self) -> None:
        self.assertEqual(rag_fallback_timeout_seconds(), 120.0)
        with patch.dict(os.environ, {"ACCOUNT_REPLY_RAG_FALLBACK_TIMEOUT_SECONDS": "90"}):
            self.assertEqual(rag_fallback_timeout_seconds(), 90.0)
        with patch.dict(os.environ, {"ACCOUNT_REPLY_RAG_FALLBACK_TIMEOUT_SECONDS": "not-a-number"}):
            self.assertEqual(rag_fallback_timeout_seconds(), 120.0)

    def test_should_run_skips_human_review_and_released_cases(self) -> None:
        active = {"automation_status": "automation", "automation_context": {}}
        self.assertTrue(should_run_reply_rag_fallback(active))
        self.assertFalse(
            should_run_reply_rag_fallback({**active, "automation_status": "human_review_required"})
        )
        self.assertFalse(
            should_run_reply_rag_fallback(
                {
                    **active,
                    "automation_context": {
                        "zendesk_ownership": {"state": "released_to_queue"}
                    },
                }
            )
        )
        with patch.dict(os.environ, {"ACCOUNT_REPLY_RAG_FALLBACK_ENABLED": "false"}):
            self.assertFalse(should_run_reply_rag_fallback(active))


class RagFallbackAnswerTest(unittest.TestCase):
    def test_answer_decision_returns_the_answer(self) -> None:
        client = _FakeRagClient(
            payload={"decision": "answer", "answer": "An App ID identifies your Agora project."}
        )
        outcome = try_rag_fallback_answer(
            question="what is appid?",
            request_id="req-1",
            ticket_id="123",
            ticket_context=[{"role": "customer", "content": "what is appid?"}],
            client=client,
        )
        self.assertEqual(outcome.kind, "answer")
        self.assertEqual(outcome.answer, "An App ID identifies your Agora project.")

    def test_answer_trailing_signature_is_stripped(self) -> None:
        client = _FakeRagClient(
            payload={
                "decision": "answer",
                "answer": (
                    "You can find it on the Agora Console Projects page.\n\n"
                    "1. Open the Agora Console Projects page.\n2. Click the copy icon.\n\n"
                    "Best Regards,\nSid"
                ),
            }
        )
        outcome = try_rag_fallback_answer(question="where is the app id?", request_id="req-s", client=client)
        self.assertEqual(outcome.kind, "answer")
        self.assertFalse(outcome.answer.rstrip().endswith("Sid"))
        self.assertTrue(outcome.answer.startswith("You can find it on the Agora Console Projects page."))
        # A closing sentence is not a signature and must be preserved.
        keep_client = _FakeRagClient(
            payload={"decision": "answer", "answer": "Thanks for asking.\nThe App ID is on the Projects page."}
        )
        keep = try_rag_fallback_answer(question="q", request_id="req-k", client=keep_client)
        self.assertEqual(keep.answer, "Thanks for asking.\nThe App ID is on the Projects page.")

    def test_marketing_footer_after_signoff_is_stripped(self) -> None:
        # The real RAG answer template observed on ticket 12940.
        client = _FakeRagClient(
            payload={
                "decision": "answer",
                "answer": (
                    "App ID (`appid`) is the application identifier.\n\n"
                    "1. Provide a non-empty App ID.\n\n"
                    "Best regards,\n"
                    "May Collins\n"
                    "Agora Support Engineer\n"
                    "\n"
                    "We'd love to hear your thoughts on our assistance and products! "
                    "Feel free to drop us at feedback@agora.io. Your feedback means a lot to us!\n"
                    "Also, boost your experience with our premium support plan for priority "
                    "assistance and quicker response times. Just click this link to learn more "
                    "—  https://www.agora.io/en/support-plans/!\n"
                    "Join our official Discord Developers Community —  https://discord.gg/uhkxjDpJsN"
                ),
            }
        )
        outcome = try_rag_fallback_answer(question="what is appid", request_id="req-m", client=client)
        self.assertEqual(outcome.kind, "answer")
        self.assertNotIn("May Collins", outcome.answer)
        self.assertNotIn("discord.gg", outcome.answer)
        self.assertNotIn("support-plans", outcome.answer)
        self.assertTrue(outcome.answer.startswith("App ID (`appid`) is the application identifier."))
        self.assertTrue(outcome.answer.rstrip().endswith("Provide a non-empty App ID."))

    def test_citations_are_appended_as_references(self) -> None:
        client = _FakeRagClient(
            payload={
                "decision": "answer",
                "answer": "App ID is the application identifier.",
                "citations": [
                    {
                        "heading": "Quickstart > Initialize the engine",
                        "source_url": "https://docs.agora.io/en/voice-calling/get-started/get-started-sdk?platform=python",
                    },
                    {
                        "heading": "Quickstart > Initialize the engine",
                        "source_url": "https://docs.agora.io/en/voice-calling/get-started/get-started-sdk?platform=python",
                    },
                    {"heading": "", "source_url": "https://docs.agora.io/en/interactive-whiteboard/reference/uikit-sdk"},
                ],
            }
        )
        outcome = try_rag_fallback_answer(question="what is appid", request_id="req-c", client=client)
        self.assertIn("References:", outcome.answer)
        self.assertEqual(outcome.answer.count("get-started-sdk"), 1)  # dedup by URL
        self.assertIn(
            "Quickstart > Initialize the engine — https://docs.agora.io/en/voice-calling/get-started/get-started-sdk",
            outcome.answer,
        )
        self.assertIn(
            "- https://docs.agora.io/en/interactive-whiteboard/reference/uikit-sdk",
            outcome.answer,
        )

    def test_escalate_decision_and_rag_errors_map_to_escalate(self) -> None:
        escalate = try_rag_fallback_answer(
            question="q",
            request_id="req-2",
            client=_FakeRagClient(payload={"decision": "escalate", "reason": "insufficient_evidence"}),
        )
        self.assertEqual(escalate.kind, "escalate")
        self.assertEqual(escalate.reason, "insufficient_evidence")

        transport = try_rag_fallback_answer(
            question="q",
            request_id="req-3",
            client=_FakeRagClient(error=RagServiceError("boom", failure_kind="transport")),
        )
        self.assertEqual(transport.kind, "escalate")
        self.assertEqual(transport.reason, "rag_error_transport")

        unexpected = try_rag_fallback_answer(
            question="q",
            request_id="req-4",
            client=_FakeRagClient(error=RuntimeError("unexpected")),
        )
        self.assertEqual(unexpected.kind, "escalate")
        self.assertEqual(unexpected.reason, "rag_error_RuntimeError")


class RagFallbackEscalationTest(unittest.TestCase):
    def _production_case(self) -> dict[str, Any]:
        return {
            "account_case_id": "AC-1",
            "processing_profile": "production",
            "automation_status": "not_automated",
            "zendesk_ticket_id": "12895",
            "automation_context": {
                "zendesk_ownership": {
                    "state": "assigned",
                    "assignee_id": "1",
                    "group_id": "2",
                    "source_group_id": "3600123",
                }
            },
        }

    def test_staging_case_only_marks_human_review_locally(self) -> None:
        repository = _FakeRepository()
        account_case = {
            "account_case_id": "AC-2",
            "processing_profile": "staging",
            "automation_status": "not_automated",
            "automation_context": {},
        }
        with patch("backend.services.account_reply_rag_fallback.add_ticket_comment") as note, patch(
            "backend.services.account_reply_rag_fallback.route_ticket_back_to_queue"
        ) as route_back:
            result = escalate_unexpected_reply_to_human(
                account_case=account_case,
                ticket_id="T-2",
                zendesk_ticket_id="",
                customer_reply_text="what is appid?",
                reason="insufficient_evidence",
                repository=repository,
                timestamp="2026-08-23T00:00:00+00:00",
            )
        note.assert_not_called()
        route_back.assert_not_called()
        self.assertEqual(result["mode"], "staging")
        self.assertEqual(account_case["automation_status"], "human_review_required")
        self.assertIn("reply_rag_fallback_escalation", account_case["not_automated_reason"])
        self.assertEqual(repository.cancelled, [("T-2", "2026-08-23T00:00:00+00:00")])
        self.assertEqual(repository.audit_events[0]["event_type"], "account_reply_rag_fallback_escalation")

    def test_production_case_notes_and_routes_back_to_queue(self) -> None:
        repository = _FakeRepository()
        account_case = self._production_case()
        with patch(
            "backend.services.account_reply_rag_fallback.add_ticket_comment"
        ) as note, patch(
            "backend.services.account_reply_rag_fallback.route_ticket_back_to_queue",
            return_value=type("Result", (), {"status": "queued"})(),
        ) as route_back:
            result = escalate_unexpected_reply_to_human(
                account_case=account_case,
                ticket_id="T-1",
                zendesk_ticket_id="12895",
                customer_reply_text="what is appid?",
                reason="insufficient_evidence",
                repository=repository,
                timestamp="2026-08-23T00:00:00+00:00",
            )
        note.assert_called_once()
        note_kwargs = note.call_args.kwargs
        self.assertFalse(note_kwargs["public"])
        self.assertIn(INTERNAL_NOTE_HEADLINE, note_kwargs["body"])
        self.assertIn("insufficient_evidence", note_kwargs["body"])
        self.assertIn("what is appid?", note_kwargs["body"])
        route_back.assert_called_once_with(ticket_id="12895", source_group_id="3600123")
        self.assertEqual(result["internal_note_status"], "sent")
        self.assertEqual(result["route_back_status"], "queued")
        self.assertEqual(account_case["automation_status"], "human_review_required")
        ownership = account_case["automation_context"]["zendesk_ownership"]
        self.assertEqual(ownership["state"], "released_to_queue")
        self.assertEqual(ownership["handoff_status"], "queued")

    def test_production_route_back_failure_is_recorded_without_raising(self) -> None:
        repository = _FakeRepository()
        account_case = self._production_case()
        failure = ZendeskCommentError("zendesk_http_error")
        failure.category = "permanent"
        failure.error_code = "zendesk_ticket_closed"
        with patch(
            "backend.services.account_reply_rag_fallback.add_ticket_comment"
        ), patch(
            "backend.services.account_reply_rag_fallback.route_ticket_back_to_queue",
            side_effect=failure,
        ):
            result = escalate_unexpected_reply_to_human(
                account_case=account_case,
                ticket_id="T-1",
                zendesk_ticket_id="12895",
                customer_reply_text="what is appid?",
                reason="insufficient_evidence",
                repository=repository,
                timestamp="2026-08-23T00:00:00+00:00",
            )
        self.assertEqual(result["route_back_status"], "failed:zendesk_ticket_closed")
        ownership = account_case["automation_context"]["zendesk_ownership"]
        self.assertEqual(ownership["handoff_status"], "failed")
        self.assertEqual(account_case["automation_status"], "human_review_required")


if __name__ == "__main__":
    unittest.main()
