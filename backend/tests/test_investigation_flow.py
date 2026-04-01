from __future__ import annotations

import os
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

from fastapi.testclient import TestClient

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.rag_service_client import RagTicketAnswerDetail
from backend.services.support_router import SupportResolution
from backend.services.ticket_orchestrator import SufficiencyAssessment


def _resolution(*, needs_engineer_guidance: bool) -> SupportResolution:
    return SupportResolution(
        answer="Need more evidence.",
        confidence=0.18,
        sources=[],
        citations=[],
        needs_engineer_guidance=needs_engineer_guidance,
        answer_route="rag",
        scope_label="agora_technical",
        route_reason="insufficient_evidence",
        route_confidence=0.91,
        search_used=False,
    )


def _rag_route_decision(*, reason: str = "technical_docs_match") -> main.SupportRouteDecision:
    return main.SupportRouteDecision(
        scope_label="agora_technical",
        route="rag",
        confidence=0.93,
        reason=reason,
        matched_signals=["token", "android 14"],
        response_language="zh",
        route_family="agora_docs_rag",
        execution_action="rag",
        tooling_profile="agora_docs_only",
    )


class InvestigationFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.original_repository = main.ticket_repository
        main.ticket_repository = self.repository
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.ticket_repository = self.original_repository

    def _seed_ticket(
        self,
        *,
        ticket_id: str = "TK-INV-001",
        status: str = "open",
        messages: list[dict[str, object]] | None = None,
        active_investigation: dict[str, object] | None = None,
        investigation_history: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        ticket = {
            "ticket_id": ticket_id,
            "customer_id": "C-001",
            "requester": "Customer",
            "subject": "Token renew callback missing",
            "status": status,
            "priority": "normal",
            "last_engineer_action": None,
            "created_at": "2026-03-29T09:00:00+00:00",
            "updated_at": "2026-03-29T09:00:00+00:00",
            "messages": messages
            or [
                {
                    "role": "customer",
                    "content": "token renew callback never fires",
                    "created_at": "2026-03-29T09:00:00+00:00",
                }
            ],
            "active_investigation": active_investigation,
            "investigation_history": investigation_history or [],
        }
        self.repository.save_ticket(ticket, new_messages=ticket["messages"])
        return ticket

    def test_repository_normalizes_legacy_waiting_for_engineer_status_to_investigating(self) -> None:
        ticket = self._seed_ticket(status="waiting_for_engineer")
        loaded = self.repository.get_ticket(str(ticket["ticket_id"]))
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["status"], "investigating")

    def test_ticket_query_escalation_starts_active_investigation_and_public_reply(self) -> None:
        with patch.object(
            main,
            "build_initial_ack",
            return_value=types.SimpleNamespace(
                text="收到，我先帮你看一下。",
                source="rule",
                intent="question",
            ),
        ), patch.object(
            main,
            "resolve_support_message",
            return_value=_resolution(needs_engineer_guidance=True),
        ), patch.object(
            main,
            "generate_investigation_ai_turn",
            return_value={
                "state": "active",
                "message": "Please confirm the SDK version and whether this reproduces on Android only.",
                "draft_customer_reply": None,
            },
        ), patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/tickets/query",
                json={
                    "ticket_id": "TK-INV-100",
                    "customer_id": "C-001",
                    "message": "为什么 token renew callback 一直没有回调？",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "investigating")
        self.assertEqual(payload["answer"], "收到，我先帮你看一下。")

        detail = self.client.get("/api/engineer/tickets/TK-INV-100")
        self.assertEqual(detail.status_code, 200, detail.text)
        ticket = detail.json()["ticket"]
        self.assertEqual(ticket["status"], "investigating")
        self.assertIsNotNone(ticket["active_investigation"])
        self.assertEqual(ticket["active_investigation"]["state"], "active")
        self.assertEqual(ticket["active_investigation"]["trigger_source"], "support_query")
        self.assertEqual(ticket["active_investigation"]["messages"][0]["role"], "engineer_ai")
        self.assertEqual(ticket["messages"][-1]["role"], "assistant")
        assistant_messages = [message["content"] for message in ticket["messages"] if message["role"] == "assistant"]
        self.assertIn("收到，我先帮你看一下。", assistant_messages)
        self.assertTrue(
            any("我已经为这个问题创建了工程师工单" in content for content in assistant_messages)
        )
        self.assertNotIn("engineer_ai", [message["role"] for message in ticket["messages"]])
        event_types = [item["event_type"] for item in self.repository.list_ticket_events("TK-INV-100")]
        self.assertIn("ticket_investigation_started", event_types)

    def test_customer_follow_up_during_investigation_keeps_same_thread_and_clears_confirmation(self) -> None:
        self._seed_ticket(
            ticket_id="TK-INV-101",
            status="investigating",
            active_investigation={
                "id": "INV-101",
                "state": "awaiting_confirmation",
                "trigger_reason": "rag_insufficient_evidence",
                "trigger_source": "support_query",
                "draft_customer_reply": "Current draft.",
                "final_confirmation_requested_at": "2026-03-29T09:02:00+00:00",
                "opened_at": "2026-03-29T09:00:00+00:00",
                "updated_at": "2026-03-29T09:02:00+00:00",
                "messages": [
                    {
                        "id": "INV-101-m1",
                        "role": "engineer_ai",
                        "content": "I have a draft ready for final confirmation.",
                        "created_at": "2026-03-29T09:02:00+00:00",
                    }
                ],
            },
        )

        with patch.object(
            main,
            "build_initial_ack",
            return_value=types.SimpleNamespace(
                text="收到，我先帮你看一下。",
                source="rule",
                intent="question",
            ),
        ), patch.object(
            main,
            "resolve_support_message",
            return_value=_resolution(needs_engineer_guidance=True),
        ), patch.object(
            main,
            "generate_investigation_ai_turn",
            return_value={
                "state": "active",
                "message": "The customer added new context. Please confirm whether token renew succeeds on iOS.",
                "draft_customer_reply": None,
            },
        ), patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/tickets/query",
                json={
                    "ticket_id": "TK-INV-101",
                    "customer_id": "C-001",
                    "message": "补充一下，这个问题只在 Android 14 上出现。",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        detail = self.client.get("/api/engineer/tickets/TK-INV-101")
        ticket = detail.json()["ticket"]
        self.assertEqual(ticket["active_investigation"]["id"], "INV-101")
        self.assertEqual(ticket["active_investigation"]["state"], "active")
        self.assertIsNone(ticket["active_investigation"]["final_confirmation_requested_at"])
        self.assertEqual(ticket["active_investigation"]["draft_customer_reply"], "")

    def test_negative_customer_message_no_longer_auto_escalates_or_sets_high_priority(self) -> None:
        resolution = SupportResolution(
            answer="Please verify the token server configuration.",
            confidence=0.89,
            sources=[],
            citations=[],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="docs_match",
            route_confidence=0.93,
            search_used=False,
        )
        enqueue_mock = AsyncMock(return_value=True)
        with patch.object(
            main,
            "build_initial_ack",
            return_value=types.SimpleNamespace(
                text="Got it, let me check this for you.",
                source="rule",
                intent="complaint",
            ),
        ), patch.object(
            main,
            "resolve_support_message",
            return_value=resolution,
        ), patch(
            "backend.services.ticket_orchestrator.assess_rag_answer_sufficiency",
            return_value=types.SimpleNamespace(
                decision="answer",
                reason="sufficient_grounding",
                confidence=0.93,
            ),
        ), patch.object(
            main.task_queue,
            "enqueue",
            enqueue_mock,
        ), patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/tickets/query",
                json={
                    "ticket_id": "TK-ACK-NEG-100",
                    "customer_id": "C-001",
                    "message": "My service is down and this is so frustrated!",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "communicating")
        self.assertEqual(payload["priority"], "normal")
        self.assertFalse(payload["needs_engineer_input"])
        self.assertEqual(payload["answer"], "Got it, let me check this for you.")
        self.assertNotIn("engineer_mode", payload)
        self.assertEqual(
            payload["sentiment"],
            {
                "label": None,
                "raw_label": None,
                "score": None,
                "is_alert": False,
                "provider": "deferred",
                "intent": "complaint",
            },
        )
        self.assertEqual(enqueue_mock.await_count, 1)
        task = enqueue_mock.await_args_list[0].args[0]
        self.assertEqual(task["task_type"], "ticket_message_sentiment")

    def test_black_screen_query_defaults_to_rag_and_investigates_when_rag_is_insufficient(self) -> None:
        with patch.object(
            main,
            "ASYNC_QUERY_ENABLED",
            False,
        ), patch.object(
            main,
            "build_initial_ack",
            return_value=types.SimpleNamespace(
                text="Got it, let me check this for you.",
                source="rule",
                intent="question",
            ),
        ), patch.object(
            main.rag_service_client,
            "query_answer_with_recovery_detail",
            return_value=RagTicketAnswerDetail(
                answer=main.INSUFFICIENT_EVIDENCE_REPLY,
                confidence=0.0,
                sources=[],
                citations=[],
                needs_engineer_guidance=True,
                reason="rag_insufficient_evidence",
                evidence_summary=None,
            ),
        ), patch.object(
            main,
            "generate_investigation_ai_turn",
            return_value={
                "state": "active",
                "message": "Please confirm whether the black screen affects local preview or remote video only.",
                "draft_customer_reply": None,
            },
        ), patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/tickets/query",
                json={
                    "ticket_id": "TK-INV-110",
                    "customer_id": "C-001",
                    "message": "i got black screen issue, what should i do?",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "investigating")
        self.assertEqual(payload["answer_route"], "rag")
        self.assertEqual(payload["scope_label"], "agora_technical")
        self.assertEqual(payload["route_reason"], "rag_insufficient_evidence")
        self.assertEqual(payload["answer"], "Got it, let me check this for you.")

    def test_customer_message_sentiment_falls_back_to_background_tagging_when_queue_is_unavailable(self) -> None:
        resolution = SupportResolution(
            answer="Please confirm whether the token server allows renewal.",
            confidence=0.84,
            sources=[],
            citations=[],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="docs_match",
            route_confidence=0.88,
            search_used=False,
        )
        enqueue_mock = AsyncMock(return_value=False)
        fallback_mock = AsyncMock()
        with patch.object(
            main,
            "build_initial_ack",
            return_value=types.SimpleNamespace(
                text="Got it, let me check this for you.",
                source="rule",
                intent="question",
            ),
        ), patch.object(
            main,
            "resolve_support_message",
            return_value=resolution,
        ), patch.object(
            main.task_queue,
            "enqueue",
            enqueue_mock,
        ), patch.object(
            main,
            "_apply_deferred_message_sentiment_tag",
            fallback_mock,
        ), patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/tickets/query",
                json={
                    "ticket_id": "TK-ACK-FALLBACK-100",
                    "customer_id": "C-001",
                    "message": "How can I debug token renewal on Android?",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(enqueue_mock.await_count, 1)
        self.assertEqual(fallback_mock.await_count, 1)

    def test_ticket_query_post_rag_check_rejection_starts_investigation(self) -> None:
        resolution = SupportResolution(
            answer="Please upgrade to SDK 4.2.2 and retry token renewal.",
            confidence=0.86,
            sources=["https://docs.agora.io/en/video-calling/token-authentication"],
            citations=[
                {
                    "chunk_id": "chunk-1",
                    "source_path": "official/token-authentication.md",
                    "heading": "Token authentication",
                    "source_url": "https://docs.agora.io/en/video-calling/token-authentication",
                }
            ],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="grounded_answer",
            route_confidence=0.93,
            search_used=False,
            evidence_summary={
                "quality_signals": {
                    "generation_mode": "structured_answer",
                    "selected_doc_count": 1,
                    "citation_coverage_ratio": 1.0,
                    "top1_similarity_score": 0.93,
                    "avg_selected_similarity_score": 0.93,
                    "handoff_reason": None,
                    "needs_human": False,
                },
                "selected_contexts": [
                    {
                        "chunk_id": "chunk-1",
                        "heading": "Token authentication",
                        "source_path": "official/token-authentication.md",
                        "source_url": "https://docs.agora.io/en/video-calling/token-authentication",
                        "text_excerpt": "Token renewal requires a valid token server response.",
                        "similarity": 0.93,
                        "cited_in_answer": True,
                    }
                ],
            },
        )
        with patch.object(
            main,
            "build_initial_ack",
            return_value=types.SimpleNamespace(
                text="收到，我先帮你看一下。",
                source="rule",
                intent="question",
            ),
        ), patch.object(
            main,
            "resolve_support_message",
            return_value=resolution,
        ), patch.object(
            main,
            "analyze_ticket_message",
            return_value=_rag_route_decision(),
        ), patch(
            "backend.services.ticket_orchestrator.assess_rag_answer_sufficiency",
            return_value=SufficiencyAssessment(
                decision="investigate",
                reason="missing_android_14_specific_evidence",
                confidence=0.89,
            ),
        ), patch.object(
            main,
            "generate_investigation_ai_turn",
            return_value={
                "state": "active",
                "message": "Please confirm whether Android 14 is the only affected platform.",
                "draft_customer_reply": None,
            },
        ), patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/tickets/query",
                json={
                    "ticket_id": "TK-INV-POSTCHECK-100",
                    "customer_id": "C-001",
                    "message": "Android 14 token renewal still fails after I upgraded the SDK.",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "investigating")
        detail = self.client.get("/api/engineer/tickets/TK-INV-POSTCHECK-100")
        ticket = detail.json()["ticket"]
        self.assertEqual(ticket["status"], "investigating")
        self.assertEqual(ticket["active_investigation"]["trigger_reason"], "rag_post_check_insufficient")
        assistant_messages = [message["content"] for message in ticket["messages"] if message["role"] == "assistant"]
        self.assertFalse(any("Please upgrade to SDK 4.2.2" in content for content in assistant_messages))
        self.assertTrue(
            any("I've opened an engineer ticket for this issue" in content for content in assistant_messages)
        )

    def test_ticket_query_post_rag_check_error_starts_investigation(self) -> None:
        resolution = SupportResolution(
            answer="Please upgrade to SDK 4.2.2 and retry token renewal.",
            confidence=0.86,
            sources=["https://docs.agora.io/en/video-calling/token-authentication"],
            citations=[],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="grounded_answer",
            route_confidence=0.93,
            search_used=False,
            evidence_summary={
                "quality_signals": {
                    "generation_mode": "structured_answer",
                    "selected_doc_count": 1,
                    "citation_coverage_ratio": 1.0,
                    "top1_similarity_score": 0.93,
                    "avg_selected_similarity_score": 0.93,
                    "handoff_reason": None,
                    "needs_human": False,
                },
                "selected_contexts": [],
            },
        )
        with patch.object(
            main,
            "build_initial_ack",
            return_value=types.SimpleNamespace(
                text="收到，我先帮你看一下。",
                source="rule",
                intent="question",
            ),
        ), patch.object(
            main,
            "resolve_support_message",
            return_value=resolution,
        ), patch.object(
            main,
            "analyze_ticket_message",
            return_value=_rag_route_decision(),
        ), patch(
            "backend.services.ticket_orchestrator.assess_rag_answer_sufficiency",
            side_effect=RuntimeError("judge unavailable"),
        ), patch.object(
            main,
            "generate_investigation_ai_turn",
            return_value={
                "state": "active",
                "message": "The post-RAG sufficiency check failed and needs engineer review.",
                "draft_customer_reply": None,
            },
        ), patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/tickets/query",
                json={
                    "ticket_id": "TK-INV-POSTCHECK-ERR-100",
                    "customer_id": "C-001",
                    "message": "Android 14 token renewal still fails after I upgraded the SDK.",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "investigating")
        detail = self.client.get("/api/engineer/tickets/TK-INV-POSTCHECK-ERR-100")
        ticket = detail.json()["ticket"]
        self.assertEqual(ticket["active_investigation"]["trigger_reason"], "rag_post_check_error")

    def test_engineer_internal_message_generates_next_ai_turn_and_draft(self) -> None:
        self._seed_ticket(
            ticket_id="TK-INV-102",
            status="investigating",
            active_investigation={
                "id": "INV-102",
                "state": "active",
                "trigger_reason": "sentiment_alert",
                "trigger_source": "support_query",
                "draft_customer_reply": None,
                "final_confirmation_requested_at": None,
                "opened_at": "2026-03-29T09:00:00+00:00",
                "updated_at": "2026-03-29T09:00:00+00:00",
                "messages": [
                    {
                        "id": "INV-102-m1",
                        "role": "engineer_ai",
                        "content": "Please share the SDK version first.",
                        "created_at": "2026-03-29T09:00:00+00:00",
                    }
                ],
            },
        )

        with patch.object(
            main,
            "generate_investigation_ai_turn",
            return_value={
                "state": "awaiting_confirmation",
                "message": "I have enough information now. Please confirm this draft before I reply to the customer.",
                "draft_customer_reply": "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
            },
        ), patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/engineer/tickets/TK-INV-102/investigation/messages",
                json={
                    "engineer_id": "eng",
                    "message": "Reproduces on Android 14 with SDK 4.2.1. Token renew succeeds after manual refresh.",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["active_investigation"]["state"], "awaiting_confirmation")
        self.assertIn("Please upgrade to SDK 4.2.2", payload["active_investigation"]["draft_customer_reply"])
        self.assertEqual(payload["active_investigation"]["messages"][-2]["role"], "engineer")
        self.assertEqual(payload["active_investigation"]["messages"][-1]["role"], "engineer_ai")

    def test_confirmation_approve_sends_customer_reply_and_closes_investigation(self) -> None:
        self._seed_ticket(
            ticket_id="TK-INV-103",
            status="investigating",
            active_investigation={
                "id": "INV-103",
                "state": "awaiting_confirmation",
                "trigger_reason": "rag_insufficient_evidence",
                "trigger_source": "worker_async_rag",
                "draft_customer_reply": "Please upgrade to SDK 4.2.2 and retry token renewal.",
                "final_confirmation_requested_at": "2026-03-29T09:03:00+00:00",
                "opened_at": "2026-03-29T09:00:00+00:00",
                "updated_at": "2026-03-29T09:03:00+00:00",
                "messages": [
                    {
                        "id": "INV-103-m1",
                        "role": "engineer_ai",
                        "content": "Draft ready for confirmation.",
                        "created_at": "2026-03-29T09:03:00+00:00",
                    }
                ],
            },
        )

        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/engineer/tickets/TK-INV-103/investigation/confirmation",
                json={
                    "engineer_id": "eng",
                    "decision": "approve",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "communicating")
        self.assertIsNone(payload["active_investigation"])

        detail = self.client.get("/api/engineer/tickets/TK-INV-103")
        ticket = detail.json()["ticket"]
        self.assertEqual(ticket["status"], "communicating")
        self.assertIsNone(ticket["active_investigation"])
        self.assertEqual(ticket["messages"][-1]["role"], "assistant")
        self.assertIn("Please upgrade to SDK 4.2.2", ticket["messages"][-1]["content"])
        self.assertEqual(ticket["investigation_history"][0]["state"], "closed")
        event_types = [item["event_type"] for item in self.repository.list_ticket_events("TK-INV-103")]
        self.assertIn("ticket_investigation_closed", event_types)
        self.assertIn("ticket_guidance_applied", event_types)

    def test_confirmation_revise_records_engineer_note_and_keeps_investigation_active(self) -> None:
        self._seed_ticket(
            ticket_id="TK-INV-104",
            status="investigating",
            active_investigation={
                "id": "INV-104",
                "state": "awaiting_confirmation",
                "trigger_reason": "rag_insufficient_evidence",
                "trigger_source": "support_query",
                "draft_customer_reply": "Upgrade to SDK 4.2.2.",
                "final_confirmation_requested_at": "2026-03-29T09:03:00+00:00",
                "opened_at": "2026-03-29T09:00:00+00:00",
                "updated_at": "2026-03-29T09:03:00+00:00",
                "messages": [
                    {
                        "id": "INV-104-m1",
                        "role": "engineer_ai",
                        "content": "Draft ready for confirmation.",
                        "created_at": "2026-03-29T09:03:00+00:00",
                    }
                ],
            },
        )

        with patch.object(
            main,
            "generate_investigation_ai_turn",
            return_value={
                "state": "awaiting_confirmation",
                "message": "Revised draft is ready. Please confirm whether this wording is safe to send.",
                "draft_customer_reply": "Please upgrade to SDK 4.2.2 and clear the local token cache before retrying.",
            },
        ), patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/engineer/tickets/TK-INV-104/investigation/confirmation",
                json={
                    "engineer_id": "eng",
                    "decision": "revise",
                    "note": "Add a cache-clear step before asking the customer to retry.",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "investigating")
        self.assertEqual(payload["active_investigation"]["state"], "awaiting_confirmation")
        self.assertEqual(payload["active_investigation"]["messages"][-2]["role"], "engineer")
        self.assertEqual(payload["active_investigation"]["messages"][-1]["role"], "engineer_ai")

    def test_storage_contract_defines_dedicated_investigation_tables(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("support_ticket_investigations", sql_source)
        self.assertIn("support_ticket_investigation_messages", sql_source)
        self.assertIn("def get_active_investigation", repo_source)
        self.assertIn("def list_ticket_investigations", repo_source)
        self.assertIn("def save_investigation", repo_source)

    def test_request_engineer_assistance_marks_ticket_escalated(self) -> None:
        self._seed_ticket(ticket_id="TK-INV-105", status="communicating")

        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/tickets/TK-INV-105/request-engineer-assistance",
                json={},
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "escalated")
        self.assertNotIn("engineer_mode", payload)

        detail = self.client.get("/api/engineer/tickets/TK-INV-105")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["ticket"]["status"], "escalated")

    def test_investigate_action_moves_escalated_ticket_into_investigating(self) -> None:
        self._seed_ticket(ticket_id="TK-INV-106", status="escalated")

        with patch.object(
            main,
            "generate_investigation_ai_turn",
            return_value={
                "state": "active",
                "message": "Please confirm the SDK version and reproduction scope.",
                "draft_customer_reply": None,
            },
        ), patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/tickets/TK-INV-106/action",
                json={
                    "action": "investigate",
                    "engineer_id": "eng",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "investigating")
        self.assertNotIn("engineer_mode", payload)

        detail = self.client.get("/api/engineer/tickets/TK-INV-106")
        ticket = detail.json()["ticket"]
        self.assertEqual(ticket["status"], "investigating")
        self.assertIsNotNone(ticket["active_investigation"])

    def test_legacy_mode_and_takeover_reply_endpoints_are_unavailable(self) -> None:
        self._seed_ticket(ticket_id="TK-INV-107", status="communicating")

        mode_response = self.client.post(
            "/api/engineer/tickets/TK-INV-107/mode",
            json={"mode": "takeover"},
        )
        takeover_reply_response = self.client.post(
            "/api/engineer/tickets/TK-INV-107/takeover-reply",
            json={"engineer_id": "eng", "message": "manual reply"},
        )

        self.assertEqual(mode_response.status_code, 404, mode_response.text)
        self.assertEqual(takeover_reply_response.status_code, 404, takeover_reply_response.text)


if __name__ == "__main__":
    unittest.main()
