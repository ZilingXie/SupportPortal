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
        subject: str = "Token renew callback missing",
        status: str = "open",
        messages: list[dict[str, object]] | None = None,
        active_investigation: dict[str, object] | None = None,
        investigation_history: list[dict[str, object]] | None = None,
        engineer_handoff_packet: dict[str, object] | None = None,
        engineer_agent_state: dict[str, object] | None = None,
    ) -> dict[str, object]:
        ticket = {
            "ticket_id": ticket_id,
            "customer_id": "C-001",
            "requester": "Customer",
            "subject": subject,
            "status": status,
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
            "engineer_handoff_packet": engineer_handoff_packet,
            "engineer_agent_state": engineer_agent_state,
        }
        self.repository.save_ticket(ticket, new_messages=ticket["messages"])
        return ticket

    def test_ticket_query_escalation_creates_linked_engineer_case_with_snapshot_title(self) -> None:
        self._seed_ticket(
            ticket_id="TK-040",
            subject="how to join channel",
            status="communicating",
            messages=[
                {
                    "role": "customer",
                    "content": "how to join channel",
                    "created_at": "2026-03-29T09:00:00+00:00",
                }
            ],
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
        ), patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/tickets/query",
                json={
                    "ticket_id": "TK-040",
                    "customer_id": "C-001",
                    "message": "i got black screen issue",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        detail = self.client.get("/api/engineer/tickets/TK-040-1")
        self.assertEqual(detail.status_code, 200, detail.text)
        payload = detail.json()["ticket"]
        self.assertEqual(payload["engineer_case_id"], "TK-040-1")
        self.assertEqual(payload["client_ticket_ref"]["ticket_id"], "TK-040")
        self.assertEqual(payload["client_ticket_ref"]["subject"], "how to join channel")
        self.assertEqual(payload["title"], "black screen issue")
        self.assertEqual(payload["status"], "investigating")

    def test_client_ticket_list_keeps_client_ticket_identity_after_engineer_case_is_created(self) -> None:
        self._seed_ticket(
            ticket_id="TK-040",
            subject="how to join channel",
            status="investigating",
            active_investigation={
                "id": "INV-040",
                "state": "active",
                "trigger_reason": "rag_insufficient_evidence",
                "trigger_source": "support_query",
                "opened_at": "2026-03-29T09:00:00+00:00",
                "updated_at": "2026-03-29T09:01:00+00:00",
                "messages": [
                    {
                        "id": "INV-040-m1",
                        "role": "engineer_ai",
                        "content": "Please confirm whether the black screen issue reproduces on all devices.",
                        "created_at": "2026-03-29T09:01:00+00:00",
                    }
                ],
            },
        )

        response = self.client.get("/api/tickets?customer_id=C-001&status=all")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["customer_id"], "C-001")
        self.assertEqual(len(payload["tickets"]), 1)
        ticket = payload["tickets"][0]
        self.assertEqual(ticket["ticket_id"], "TK-040")
        self.assertEqual(ticket["subject"], "how to join channel")
        self.assertEqual(ticket["active_engineer_case_id"], "TK-040-1")
        self.assertEqual(ticket["engineer_case_count"], 1)

    def test_engineer_ticket_detail_includes_canonical_ticket_family_token_summary(self) -> None:
        self._seed_ticket(
            ticket_id="TK-040",
            subject="how to join channel",
            status="investigating",
            active_investigation={
                "id": "TK-040-1",
                "state": "active",
                "trigger_reason": "rag_insufficient_evidence",
                "trigger_source": "support_query",
                "opened_at": "2026-03-29T09:00:00+00:00",
                "updated_at": "2026-03-29T09:00:00+00:00",
                "messages": [],
            },
        )

        with patch.object(
            main.rag_service_client,
            "get_ticket_family_token_summary",
            return_value={
                "canonical_ticket_id": "TK-040",
                "related_ticket_ids": ["TK-040-1"],
                "total_input_tokens": 1200,
                "total_output_tokens": 300,
                "total_embedding_tokens": 100,
                "known_cost_total": 0.12,
                "unknown_cost_present": False,
                "cost_by_model": [{"provider": "openai", "model": "gpt-5.4", "known_cost": 0.12}],
            },
        ):
            detail = self.client.get("/api/engineer/tickets/TK-040-1")

        self.assertEqual(detail.status_code, 200, detail.text)
        payload = detail.json()["ticket"]
        self.assertEqual(payload["token_usage"]["canonical_ticket_id"], "TK-040")
        self.assertEqual(payload["token_usage"]["related_ticket_ids"], ["TK-040-1"])
        self.assertEqual(payload["token_usage"]["total_input_tokens"], 1200)

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

        detail = self.client.get("/api/engineer/tickets/TK-INV-100-1")
        self.assertEqual(detail.status_code, 200, detail.text)
        ticket = detail.json()["ticket"]
        self.assertEqual(ticket["status"], "investigating")
        self.assertIsNotNone(ticket["active_investigation"])
        self.assertEqual(ticket["active_investigation"]["state"], "active")
        self.assertEqual(ticket["active_investigation"]["trigger_source"], "support_query")
        self.assertEqual(ticket["active_investigation"]["messages"][0]["role"], "engineer_ai")
        opening_message = ticket["active_investigation"]["messages"][0]
        self.assertIn("Engineer Request:", opening_message["content"])
        self.assertIn("Issue:", opening_message["content"])
        self.assertIn("AI could not find enough grounded doc evidence to answer safely.", opening_message["content"])
        self.assertIn("Action Needed:", opening_message["content"])
        self.assertFalse(opening_message.get("sources"))
        self.assertFalse(opening_message.get("citations"))
        self.assertEqual(ticket["messages"][-1]["role"], "assistant")
        assistant_messages = [message["content"] for message in ticket["messages"] if message["role"] == "assistant"]
        self.assertIn("收到，我先帮你看一下。", assistant_messages)
        self.assertTrue(
            any("我已经为这个问题创建了工程师工单" in content for content in assistant_messages)
        )
        self.assertNotIn("engineer_ai", [message["role"] for message in ticket["messages"]])
        event_types = [item["event_type"] for item in self.repository.list_ticket_events("TK-INV-100")]
        self.assertIn("ticket_investigation_started", event_types)

    def test_ticket_query_escalation_persists_ticket_level_handoff_and_agent_state(self) -> None:
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
                "message": "I understand the Android 14 token renewal regression and need the exact SDK version plus reproduction scope before I can draft a safe customer reply.",
                "draft_customer_reply": None,
                "engineer_agent_state": {
                    "phase": "gather_missing_inputs",
                    "issue_understanding": "Android 14 token renewal still fails after the customer upgraded the SDK.",
                    "knowledge_summary": "Client AI found generic token-authentication guidance and one cited chunk about valid token server responses.",
                    "why_not_solved": "The grounded answer does not prove the Android 14-specific callback behavior or the exact SDK regression boundary.",
                    "goal": "Confirm the exact SDK version and whether Android 14 is the only affected platform before replying to the customer.",
                    "known_facts": [
                        "Customer reports token renewal still fails on Android 14.",
                        "The current candidate answer recommends upgrading to SDK 4.2.2.",
                    ],
                    "missing_information": [
                        "Exact SDK version in the failing build",
                        "Whether the issue reproduces on non-Android 14 platforms",
                    ],
                    "next_request_for_engineer": "Please confirm the exact SDK version and whether Android 14 is the only affected platform.",
                    "resolution_hypothesis": "The issue may be limited to SDK 4.2.1 on Android 14.",
                    "ready_to_reply": False,
                    "last_refreshed_at": "2026-03-29T09:00:00+00:00",
                },
            },
        ), patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/tickets/query",
                json={
                    "ticket_id": "TK-INV-HANDOFF-100",
                    "customer_id": "C-001",
                    "message": "Android 14 token renewal still fails after I upgraded the SDK.",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        detail = self.client.get("/api/engineer/tickets/TK-INV-HANDOFF-100-1")
        self.assertEqual(detail.status_code, 200, detail.text)
        ticket = detail.json()["ticket"]
        handoff = ticket["engineer_handoff_packet"]
        agent_state = ticket["engineer_agent_state"]
        self.assertIsInstance(handoff, dict)
        self.assertIsInstance(agent_state, dict)
        self.assertEqual(handoff["latest_customer_message"], "Android 14 token renewal still fails after I upgraded the SDK.")
        self.assertEqual(handoff["rag_result"]["candidate_answer"], resolution.answer)
        self.assertEqual(
            handoff["rag_result"]["sources"],
            ["https://docs.agora.io/en/video-calling/token-authentication"],
        )
        self.assertEqual(handoff["route_summary"]["route_reason"], "grounded_answer")
        self.assertEqual(handoff["unresolved_reason"], "rag_post_check_insufficient")
        self.assertIn("Android 14 token renewal still fails", handoff["conversation_summary"])
        self.assertEqual(agent_state["phase"], "gather_missing_inputs")
        self.assertTrue(agent_state["goal"].startswith("Confirm the exact SDK version"))
        self.assertEqual(
            agent_state["next_request_for_engineer"],
            "Please confirm the exact SDK version and whether Android 14 is the only affected platform.",
        )

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
            engineer_handoff_packet={
                "source": "support_query",
                "conversation_summary": "Customer reports token renew callback never fires.",
                "latest_customer_message": "token renew callback never fires",
                "latest_client_ai_reply": "I've opened an engineer ticket for this issue and we're investigating further. I'll reply here as soon as the engineer review is confirmed.",
                "route_summary": {
                    "answer_route": "rag",
                    "route_reason": "rag_insufficient_evidence",
                },
                "rag_result": {
                    "candidate_answer": "Please upgrade to SDK 4.2.2 and retry token renewal.",
                    "sources": ["https://docs.agora.io/en/video-calling/token-authentication"],
                    "citations": [],
                    "evidence_summary": {
                        "quality_signals": {
                            "generation_mode": "structured_answer",
                        },
                        "selected_contexts": [],
                    },
                },
                "unresolved_reason": "rag_insufficient_evidence",
                "customer_language_hint": "zh",
                "created_at": "2026-03-29T09:00:00+00:00",
                "updated_at": "2026-03-29T09:02:00+00:00",
            },
            engineer_agent_state={
                "phase": "awaiting_confirmation",
                "issue_understanding": "Token renew callback is missing on the customer build.",
                "knowledge_summary": "Client AI found a generic token-renewal recommendation.",
                "why_not_solved": "The draft does not yet account for platform scope.",
                "goal": "Confirm whether Android is the only affected platform before replying.",
                "known_facts": ["Customer initially reported a missing callback."],
                "missing_information": ["Platform scope"],
                "next_request_for_engineer": "Confirm whether the issue is Android-only.",
                "resolution_hypothesis": "",
                "ready_to_reply": True,
                "last_refreshed_at": "2026-03-29T09:02:00+00:00",
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
                "engineer_agent_state": {
                    "phase": "gather_missing_inputs",
                    "issue_understanding": "Token renew callback is missing only on Android 14 so far.",
                    "knowledge_summary": "Client AI found generic token-renewal guidance but nothing Android 14-specific.",
                    "why_not_solved": "The issue scope changed and the old draft is no longer safe.",
                    "goal": "Validate whether Android 14 is the only affected platform and whether iOS reproduces.",
                    "known_facts": [
                        "Customer says the issue only appears on Android 14."
                    ],
                    "missing_information": [
                        "Whether iOS also reproduces"
                    ],
                    "next_request_for_engineer": "Please confirm whether token renew succeeds on iOS.",
                    "resolution_hypothesis": "The regression may be Android 14-specific.",
                    "ready_to_reply": False,
                    "last_refreshed_at": "2026-03-29T09:03:00+00:00",
                },
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
        detail = self.client.get("/api/engineer/tickets/TK-INV-101-1")
        ticket = detail.json()["ticket"]
        self.assertEqual(ticket["active_investigation"]["id"], "TK-INV-101-1")
        self.assertEqual(ticket["active_investigation"]["state"], "active")
        self.assertIsNone(ticket["active_investigation"]["final_confirmation_requested_at"])
        self.assertEqual(ticket["active_investigation"]["draft_customer_reply"], "")
        self.assertEqual(
            ticket["engineer_handoff_packet"]["latest_customer_message"],
            "补充一下，这个问题只在 Android 14 上出现。",
        )
        self.assertIn("Android 14", ticket["engineer_handoff_packet"]["conversation_summary"])
        self.assertEqual(
            ticket["engineer_handoff_packet"]["rag_result"]["candidate_answer"],
            "Please upgrade to SDK 4.2.2 and retry token renewal.",
        )
        self.assertEqual(ticket["engineer_agent_state"]["phase"], "gather_missing_inputs")
        self.assertEqual(
            ticket["engineer_agent_state"]["next_request_for_engineer"],
            "Please confirm whether token renew succeeds on iOS.",
        )

    def test_negative_customer_message_no_longer_auto_escalates_or_returns_priority_fields(self) -> None:
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
        self.assertFalse(payload["needs_engineer_input"])
        self.assertEqual(payload["answer"], "Got it, let me check this for you.")
        self.assertNotIn("engineer_mode", payload)
        self.assertNotIn("priority", payload)
        self.assertNotIn("eta_minutes", payload)
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
            "analyze_ticket_message",
            return_value=_rag_route_decision(reason="conservative_agora_technical_fallback"),
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
        detail = self.client.get("/api/engineer/tickets/TK-INV-POSTCHECK-100-1")
        ticket = detail.json()["ticket"]
        self.assertEqual(ticket["status"], "investigating")
        self.assertEqual(ticket["active_investigation"]["trigger_reason"], "rag_post_check_insufficient")
        opening_message = ticket["active_investigation"]["messages"][0]
        self.assertEqual(opening_message["role"], "engineer_ai")
        self.assertIn("Engineer Request:", opening_message["content"])
        self.assertIn("Issue:", opening_message["content"])
        self.assertIn(
            "AI found a tentative docs-backed answer but could not safely send it without engineer review.",
            opening_message["content"],
        )
        self.assertIn(
            "Please upgrade to SDK 4.2.2 and retry token renewal.",
            opening_message["content"],
        )
        self.assertIn("Action Needed:", opening_message["content"])
        self.assertEqual(
            opening_message["sources"],
            ["https://docs.agora.io/en/video-calling/token-authentication"],
        )
        self.assertEqual(
            opening_message["citations"][0]["source_url"],
            "https://docs.agora.io/en/video-calling/token-authentication",
        )
        self.assertNotIn("https://docs.agora.io/en/video-calling/token-authentication", opening_message["content"])
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
        detail = self.client.get("/api/engineer/tickets/TK-INV-POSTCHECK-ERR-100-1")
        ticket = detail.json()["ticket"]
        self.assertEqual(ticket["active_investigation"]["trigger_reason"], "rag_post_check_error")
        opening_message = ticket["active_investigation"]["messages"][0]
        self.assertIn("Engineer Request:", opening_message["content"])
        self.assertIn(
            "AI found a tentative docs-backed answer but could not safely send it without engineer review.",
            opening_message["content"],
        )
        self.assertIn("Action Needed:", opening_message["content"])
        self.assertFalse(opening_message.get("citations"))

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
                "/api/engineer/tickets/TK-INV-102-1/investigation/messages",
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
        self.assertEqual(payload["active_investigation"]["messages"][-1]["content"], "I have enough information now. Please confirm this draft before I reply to the customer.")

    def test_investigation_events_include_agent_summary_fields(self) -> None:
        self._seed_ticket(
            ticket_id="TK-INV-EVENT-102",
            status="investigating",
            active_investigation={
                "id": "INV-EVENT-102",
                "state": "active",
                "trigger_reason": "sentiment_alert",
                "trigger_source": "support_query",
                "draft_customer_reply": None,
                "final_confirmation_requested_at": None,
                "opened_at": "2026-03-29T09:00:00+00:00",
                "updated_at": "2026-03-29T09:00:00+00:00",
                "messages": [
                    {
                        "id": "INV-EVENT-102-m1",
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
                "engineer_agent_state": {
                    "phase": "awaiting_confirmation",
                    "issue_understanding": "Android 14 token renewal fails on SDK 4.2.1.",
                    "knowledge_summary": "Client AI found token-authentication guidance but no Android 14-specific fix.",
                    "why_not_solved": "The existing evidence did not prove whether the issue was platform-specific until the engineer confirmed it.",
                    "goal": "Send a safe customer reply that scopes the issue to Android 14 on SDK 4.2.1.",
                    "known_facts": ["Issue reproduces on Android 14 with SDK 4.2.1."],
                    "missing_information": [],
                    "next_request_for_engineer": "Approve the prepared customer reply.",
                    "resolution_hypothesis": "Upgrading to SDK 4.2.2 should resolve the issue.",
                    "ready_to_reply": True,
                    "last_refreshed_at": "2026-03-29T09:04:00+00:00",
                },
            },
        ), patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/engineer/tickets/TK-INV-EVENT-102-1/investigation/messages",
                json={
                    "engineer_id": "eng",
                    "message": "Reproduces on Android 14 with SDK 4.2.1 only.",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        investigation_events = [
            item["payload"]
            for item in self.repository.list_ticket_events("TK-INV-EVENT-102")
            if item["event_type"].startswith("ticket_investigation_")
        ]
        latest_event = investigation_events[0]
        self.assertEqual(latest_event["agent_phase"], "awaiting_confirmation")
        self.assertTrue(latest_event["agent_ready_to_reply"])
        self.assertIn("Android 14", latest_event["agent_goal"])
        self.assertEqual(
            latest_event["agent_next_request_for_engineer"],
            "Approve the prepared customer reply.",
        )

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
            engineer_handoff_packet={
                "source": "worker_async_rag",
                "conversation_summary": "Customer reports token renew callback does not fire.",
                "latest_customer_message": "token renew callback never fires",
                "latest_client_ai_reply": "I've opened an engineer ticket for this issue and we're investigating further. I'll reply here as soon as the engineer review is confirmed.",
                "route_summary": {
                    "answer_route": "rag",
                    "route_reason": "rag_insufficient_evidence",
                },
                "rag_result": {
                    "candidate_answer": "Please upgrade to SDK 4.2.2 and retry token renewal.",
                    "sources": ["https://docs.agora.io/en/video-calling/token-authentication"],
                    "citations": [],
                    "evidence_summary": {},
                },
                "unresolved_reason": "rag_insufficient_evidence",
                "customer_language_hint": "en",
                "created_at": "2026-03-29T09:00:00+00:00",
                "updated_at": "2026-03-29T09:03:00+00:00",
            },
            engineer_agent_state={
                "phase": "awaiting_confirmation",
                "issue_understanding": "Token renew callback does not fire.",
                "knowledge_summary": "Client AI found generic token-renewal guidance.",
                "why_not_solved": "The engineer had to confirm the SDK-specific fix before replying.",
                "goal": "Send the approved SDK upgrade guidance to the customer.",
                "known_facts": ["SDK 4.2.2 is the recommended fix."],
                "missing_information": [],
                "next_request_for_engineer": "Approve the prepared customer reply.",
                "resolution_hypothesis": "Upgrading to SDK 4.2.2 should resolve the callback failure.",
                "ready_to_reply": True,
                "last_refreshed_at": "2026-03-29T09:03:00+00:00",
            },
        )

        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/engineer/tickets/TK-INV-103-1/investigation/confirmation",
                json={
                    "engineer_id": "eng",
                    "decision": "approve",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "communicating")
        self.assertIsNone(payload["active_investigation"])

        detail = self.client.get("/api/engineer/tickets/TK-INV-103-1")
        ticket = detail.json()["ticket"]
        self.assertEqual(ticket["status"], "communicating")
        self.assertIsNone(ticket["active_investigation"])
        self.assertEqual(ticket["messages"][-1]["role"], "assistant")
        self.assertIn("Please upgrade to SDK 4.2.2", ticket["messages"][-1]["content"])
        self.assertEqual(ticket["investigation_history"][0]["state"], "closed")
        self.assertEqual(
            ticket["engineer_handoff_packet"]["rag_result"]["candidate_answer"],
            "Please upgrade to SDK 4.2.2 and retry token renewal.",
        )
        self.assertEqual(ticket["engineer_agent_state"]["phase"], "awaiting_confirmation")
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
                "/api/engineer/tickets/TK-INV-104-1/investigation/confirmation",
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
        self.assertIn("engineer_handoff_packet", sql_source)
        self.assertIn("engineer_agent_state", sql_source)

    def test_ticket_summary_uses_engineer_agent_state_for_investigating_ticket(self) -> None:
        self._seed_ticket(
            ticket_id="TK-INV-SUMMARY-100",
            status="investigating",
            active_investigation={
                "id": "INV-SUMMARY-100",
                "state": "active",
                "trigger_reason": "rag_post_check_insufficient",
                "trigger_source": "support_query",
                "draft_customer_reply": "",
                "final_confirmation_requested_at": None,
                "opened_at": "2026-03-29T09:00:00+00:00",
                "updated_at": "2026-03-29T09:02:00+00:00",
                "messages": [],
            },
            engineer_agent_state={
                "phase": "gather_missing_inputs",
                "issue_understanding": "Android 14 token renewal still fails after the customer upgraded the SDK.",
                "knowledge_summary": "Client AI found generic token-authentication guidance but no Android 14-specific callback evidence.",
                "why_not_solved": "The available evidence does not prove the platform scope or the exact SDK regression boundary.",
                "goal": "Confirm the exact SDK version and whether Android 14 is the only affected platform.",
                "known_facts": ["Customer reports Android 14 token renewal still fails after an upgrade."],
                "missing_information": ["Exact SDK version", "Cross-platform reproduction scope"],
                "next_request_for_engineer": "Please confirm the exact SDK version and whether Android 14 is the only affected platform.",
                "resolution_hypothesis": "The issue may be limited to SDK 4.2.1 on Android 14.",
                "ready_to_reply": False,
                "last_refreshed_at": "2026-03-29T09:02:00+00:00",
            },
        )

        response = self.client.get("/api/engineer/tickets/TK-INV-SUMMARY-100-1/summary")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("Current understanding: Android 14 token renewal still fails", payload["summary"])
        self.assertIn("Why client AI could not solve it", payload["summary"])
        self.assertEqual(
            payload["next_action_needed"],
            "Please confirm the exact SDK version and whether Android 14 is the only affected platform.",
        )

    def test_request_engineer_assistance_marks_ticket_escalated(self) -> None:
        self._seed_ticket(
            ticket_id="TK-INV-105",
            subject="how to join channel",
            status="communicating",
            messages=[
                {
                    "role": "customer",
                    "content": "i got black screen issue",
                    "created_at": "2026-03-29T09:00:00+00:00",
                }
            ],
        )

        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/tickets/TK-INV-105/request-engineer-assistance",
                json={},
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "escalated")
        self.assertNotIn("engineer_mode", payload)

        detail = self.client.get("/api/engineer/tickets/TK-INV-105-1")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["ticket"]["status"], "escalated")
        self.assertEqual(detail.json()["ticket"]["engineer_case_id"], "TK-INV-105-1")
        self.assertEqual(detail.json()["ticket"]["title"], "black screen issue")

    def test_investigate_action_reuses_latest_rag_turn_when_escalated_ticket_enters_investigation(self) -> None:
        self._seed_ticket(
            ticket_id="TK-INV-106",
            status="escalated",
            messages=[
                {
                    "role": "customer",
                    "content": "token renew callback never fires on Android 14",
                    "created_at": "2026-03-29T09:00:00+00:00",
                },
                {
                    "role": "assistant",
                    "content": "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                    "created_at": "2026-03-29T09:01:00+00:00",
                    "answer_route": "rag",
                    "execution_action": "rag",
                    "scope_label": "agora_technical",
                    "route_reason": "grounded_answer",
                    "sources": ["https://docs.agora.io/en/video-calling/token-authentication"],
                    "citations": [
                        {
                            "chunk_id": "chunk-1",
                            "source_path": "official/token-authentication.md",
                            "heading": "Token authentication",
                            "source_url": "https://docs.agora.io/en/video-calling/token-authentication",
                        }
                    ],
                },
            ],
        )

        with patch.object(main, "dispatch_event", AsyncMock()):
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

        detail = self.client.get("/api/engineer/tickets/TK-INV-106-1")
        ticket = detail.json()["ticket"]
        self.assertEqual(ticket["status"], "investigating")
        self.assertIsNotNone(ticket["active_investigation"])
        opening_message = ticket["active_investigation"]["messages"][0]
        self.assertIn("Engineer Request:", opening_message["content"])
        self.assertIn(
            "AI attempted this docs-backed guidance: Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
            opening_message["content"],
        )
        self.assertEqual(
            opening_message["citations"][0]["source_url"],
            "https://docs.agora.io/en/video-calling/token-authentication",
        )

    def test_investigate_action_without_latest_rag_turn_falls_back_to_generic_prompt(self) -> None:
        self._seed_ticket(ticket_id="TK-INV-106B", status="escalated")

        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/tickets/TK-INV-106B/action",
                json={
                    "action": "investigate",
                    "engineer_id": "eng",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        detail = self.client.get("/api/engineer/tickets/TK-INV-106B-1")
        ticket = detail.json()["ticket"]
        opening_message = ticket["active_investigation"]["messages"][0]
        self.assertIn(
            "Please confirm the reproduction scope, SDK version, and whether the issue is limited to a specific platform.",
            opening_message["content"],
        )
        self.assertNotIn("Engineer Request:", opening_message["content"])

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
