from __future__ import annotations

import os
import unittest

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

from fastapi.testclient import TestClient

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository


class EngineerHitlFeedbackApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.original_repository = main.ticket_repository
        main.ticket_repository = self.repository
        self.client = TestClient(main.app)
        self._seed_engineer_case()

    def tearDown(self) -> None:
        main.ticket_repository = self.original_repository

    def _seed_engineer_case(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "TK-HITL-001",
                "customer_id": "cust-hitl",
                "requester": "Grace",
                "subject": "Channel disband check",
                "status": "investigating",
                "active_engineer_case_id": "TK-HITL-001-1",
                "engineer_case_count": 1,
                "created_at": "2026-06-10T00:00:00+00:00",
                "updated_at": "2026-06-10T00:00:00+00:00",
                "messages": [],
            },
            new_messages=[],
        )
        self.repository.save_engineer_case(
            {
                "engineer_case_id": "TK-HITL-001-1",
                "client_ticket_id": "TK-HITL-001",
                "case_sequence": 1,
                "title": "Channel disband check",
                "status": "investigating",
                "trigger_source": "customer_request",
                "trigger_reason": "needs_engineer_review",
                "investigation_state": "active",
                "opened_at": "2026-06-10T00:00:00+00:00",
                "updated_at": "2026-06-10T00:00:00+00:00",
                "messages": [],
            },
            new_messages=[],
        )

    def test_engineer_can_record_and_list_structured_hitl_feedback(self) -> None:
        response = self.client.post(
            "/api/engineer/tickets/TK-HITL-001-1/feedback",
            json={
                "engineer_id": "Jack",
                "run_id": "run-123",
                "message_id": "msg-123",
                "evidence_packet_id": "packet-123",
                "feedback_type": "revise",
                "diagnosis_correctness": "partially_correct",
                "root_cause_correctness": "likely",
                "evidence_quality": "partial",
                "citation_quality": "missing",
                "customer_reply_quality": "needs_edit",
                "missing_information": [
                    {"field": "channel_name", "reason": "needed_to_reproduce"}
                ],
                "incorrect_claims": [
                    {"claim": "The API always returns 200.", "correction": "4xx is possible."}
                ],
                "corrected_root_cause": "The rule payload is missing a required field.",
                "corrected_solution": "Validate the request body before retrying.",
                "corrected_customer_reply": "Please share the channel name and request payload.",
                "evidence_refs": [
                    {
                        "kind": "rag_chunk",
                        "source_id": "chunk-123",
                        "access_mode": "official_only",
                        "customer_safe": True,
                    }
                ],
                "memory_candidate": "needs_review",
                "memory_safety": "internal_only",
                "prompt_version": "engineer-investigation-reply-v8",
                "workflow_version": "engineer-hitl-feedback-v1",
                "tool_policy_version": "engineer-evidence-tools-v1",
                "rag_access_policy_version": "rag-access-routing-v1",
                "evidence_packet_version": "engineer-evidence-packet-v1",
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        feedback = response.json()["feedback"]
        self.assertTrue(feedback["feedback_id"].startswith("hitl_"))
        self.assertEqual(feedback["engineer_case_id"], "TK-HITL-001-1")
        self.assertEqual(feedback["client_ticket_id"], "TK-HITL-001")
        self.assertEqual(feedback["created_by"], "Jack")
        self.assertEqual(feedback["memory_candidate"], "needs_review")
        self.assertEqual(feedback["evidence_refs"][0]["source_id"], "chunk-123")

        list_response = self.client.get("/api/engineer/tickets/TK-HITL-001-1/feedback")

        self.assertEqual(list_response.status_code, 200, msg=list_response.text)
        payload = list_response.json()
        self.assertEqual(payload["engineer_case_id"], "TK-HITL-001-1")
        self.assertEqual(payload["client_ticket_id"], "TK-HITL-001")
        self.assertEqual(len(payload["feedback"]), 1)
        self.assertEqual(payload["feedback"][0]["feedback_id"], feedback["feedback_id"])

    def test_engineer_hitl_feedback_rejects_missing_case(self) -> None:
        response = self.client.post(
            "/api/engineer/tickets/UNKNOWN/feedback",
            json={
                "feedback_type": "approve",
                "diagnosis_correctness": "correct",
                "root_cause_correctness": "unknown",
                "evidence_quality": "sufficient",
                "citation_quality": "not_applicable",
                "customer_reply_quality": "sendable",
                "memory_candidate": "no",
                "memory_safety": "do_not_store",
            },
        )

        self.assertEqual(response.status_code, 404)
