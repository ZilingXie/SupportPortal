from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from backend.services import engineer_review_agent
from backend.services.engineer_review_agent import (
    ENGINEER_REVIEW_AGENT_VERSION,
    ENGINEER_REVIEW_VERSION,
    review_execution,
)

# Reuse the sample data from test_engineer_execute_agent
SAMPLE_EXECUTION_COMPLETED: dict = {
    "execution_id": "exec_plan_summary_ec_001_r1_r1",
    "execution_version": "engineer-execution-v1",
    "execute_agent_version": "engineer-execute-agent-v1",
    "created_by": "execute_agent",
    "created_at": "2026-06-20T10:02:00Z",
    "plan_id": "plan_summary_ec_001_r1",
    "plan_version": "engineer-plan-v1",
    "status": "completed",
    "scheduler": {
        "mode": "deterministic_allowlist",
        "parallel_groups": [
            ["task_internal_knowledge_search", "task_official_docs_fallback", "task_missing_info_triage"]
        ],
        "serial_steps": ["task_context_review", "task_synthesis"],
        "execution_order": [
            {"stage": 1, "mode": "serial", "task_ids": ["task_context_review"]},
            {"stage": 2, "mode": "parallel", "task_ids": [
                "task_internal_knowledge_search", "task_official_docs_fallback", "task_missing_info_triage"
            ]},
            {"stage": 3, "mode": "serial", "task_ids": ["task_synthesis"]},
        ],
    },
    "task_results": [
        {
            "task_id": "task_context_review",
            "skill": "context_review",
            "subagent": "execute_agent_subagent_context_review",
            "status": "succeeded",
            "summary": "Latest customer message: My camera is black after upgrading to SDK 4.5. Conversation summary: Customer reports camera black screen after SDK upgrade. Escalation reason: rag_insufficient_evidence. Product: RTC.",
            "evidence_refs": [
                {"kind": "customer_message", "text": "My camera is black after upgrading to SDK 4.5."},
                {"kind": "escalation_reason", "value": "rag_insufficient_evidence"},
            ],
            "missing_information": [],
            "started_at": "2026-06-20T10:02:00Z",
            "completed_at": "2026-06-20T10:02:00Z",
        },
        {
            "task_id": "task_internal_knowledge_search",
            "skill": "internal_knowledge_search",
            "subagent": "execute_agent_subagent_internal_knowledge_search",
            "status": "succeeded",
            "summary": "Clue: Check camera permissions and SDK initialization order. Candidate answer: Check camera permissions and SDK initialization order. Sources: 1. Citations: 1.",
            "evidence_refs": [
                {"kind": "clue", "summary": "Check camera permissions and SDK initialization order."},
            ],
            "missing_information": [],
            "started_at": "2026-06-20T10:02:00Z",
            "completed_at": "2026-06-20T10:02:00Z",
        },
        {
            "task_id": "task_official_docs_fallback",
            "skill": "official_docs_fallback",
            "subagent": "execute_agent_subagent_official_docs_fallback",
            "status": "succeeded",
            "summary": "Official documentation may be useful.",
            "evidence_refs": [{"kind": "official_doc_ref", "value": "doc://camera-troubleshooting"}],
            "missing_information": [],
            "started_at": "2026-06-20T10:02:00Z",
            "completed_at": "2026-06-20T10:02:00Z",
        },
        {
            "task_id": "task_missing_info_triage",
            "skill": "missing_info_triage",
            "subagent": "execute_agent_subagent_missing_info_triage",
            "status": "succeeded",
            "summary": "Triaged 4 missing information items.",
            "evidence_refs": [],
            "missing_information": ["SDK version", "Exact error code", "Platform or OS version", "Reproduction steps"],
            "started_at": "2026-06-20T10:02:00Z",
            "completed_at": "2026-06-20T10:02:00Z",
        },
        {
            "task_id": "task_synthesis",
            "skill": "synthesis",
            "subagent": "execute_agent_subagent_synthesis",
            "status": "succeeded",
            "summary": "Synthesized from 4 succeeded and 0 blocked/failed prior tasks.",
            "evidence_refs": [
                {"kind": "customer_message", "text": "My camera is black after upgrading to SDK 4.5."},
                {"kind": "clue", "summary": "Check camera permissions and SDK initialization order."},
                {"kind": "official_doc_ref", "value": "doc://camera-troubleshooting"},
            ],
            "missing_information": ["SDK version", "Exact error code", "Platform or OS version", "Reproduction steps"],
            "started_at": "2026-06-20T10:02:00Z",
            "completed_at": "2026-06-20T10:02:00Z",
        },
    ],
    "evidence_packet": {
        "packet_id": "evidence_exec_plan_summary_ec_001_r1_r1",
        "packet_version": "engineer-evidence-packet-v1",
        "source_execution_id": "exec_plan_summary_ec_001_r1_r1",
        "customer_safe_summary": "Investigation completed. See internal summary for details.",
        "internal_summary": "Internal investigation results.",
        "evidence_refs": [
            {"kind": "customer_message", "text": "My camera is black after upgrading to SDK 4.5."},
            {"kind": "clue", "summary": "Check camera permissions and SDK initialization order."},
            {"kind": "official_doc_ref", "value": "doc://camera-troubleshooting"},
        ],
        "missing_information": ["SDK version", "Exact error code", "Platform or OS version", "Reproduction steps"],
        "redaction_boundary": {},
        "do_not_expose_to_customer": [],
    },
    "blockers": [],
}

SAMPLE_EXECUTION_BLOCKED: dict = {
    "execution_id": "exec_plan_blocked_r1",
    "execution_version": "engineer-execution-v1",
    "execute_agent_version": "engineer-execute-agent-v1",
    "created_by": "execute_agent",
    "created_at": "2026-06-20T10:02:00Z",
    "plan_id": "plan_blocked",
    "plan_version": "engineer-plan-v1",
    "status": "blocked",
    "scheduler": {
        "mode": "deterministic_allowlist",
        "parallel_groups": [],
        "serial_steps": [],
        "execution_order": [],
    },
    "task_results": [],
    "evidence_packet": {
        "packet_id": "evidence_exec_plan_blocked_r1",
        "packet_version": "engineer-evidence-packet-v1",
        "source_execution_id": "exec_plan_blocked_r1",
        "customer_safe_summary": "Investigation completed. See internal summary for details.",
        "internal_summary": "No task results available.",
        "evidence_refs": [],
        "missing_information": [],
        "redaction_boundary": {},
        "do_not_expose_to_customer": [],
    },
    "blockers": [
        {
            "blocker_id": "blocker_exec_plan_blocked_r1_task_a_invalid",
            "type": "dependency_blocked",
            "description": "Cycle detected in task dependencies.",
            "source_task_id": "task_a",
        },
    ],
}

SAMPLE_EXECUTION_PARTIAL: dict = {
    "execution_id": "exec_plan_partial_r1",
    "execution_version": "engineer-execution-v1",
    "execute_agent_version": "engineer-execute-agent-v1",
    "created_by": "execute_agent",
    "created_at": "2026-06-20T10:02:00Z",
    "plan_id": "plan_partial",
    "plan_version": "engineer-plan-v1",
    "status": "partial",
    "scheduler": {
        "mode": "deterministic_allowlist",
        "parallel_groups": [],
        "serial_steps": ["task_context_review", "task_synthesis"],
        "execution_order": [
            {"stage": 1, "mode": "serial", "task_ids": ["task_context_review"]},
            {"stage": 2, "mode": "serial", "task_ids": ["task_synthesis"]},
        ],
    },
    "task_results": [
        {
            "task_id": "task_context_review",
            "skill": "context_review",
            "subagent": "execute_agent_subagent_context_review",
            "status": "succeeded",
            "summary": "Some context was reviewed.",
            "evidence_refs": [],
            "missing_information": [],
            "started_at": "2026-06-20T10:02:00Z",
            "completed_at": "2026-06-20T10:02:00Z",
        },
        {
            "task_id": "task_synthesis",
            "skill": "synthesis",
            "subagent": "execute_agent_subagent_synthesis",
            "status": "blocked",
            "summary": "Not enough prior results to synthesize.",
            "evidence_refs": [],
            "missing_information": ["Missing critical evidence"],
            "started_at": "2026-06-20T10:02:00Z",
            "completed_at": "2026-06-20T10:02:00Z",
        },
    ],
    "evidence_packet": {
        "packet_id": "evidence_exec_plan_partial_r1",
        "packet_version": "engineer-evidence-packet-v1",
        "source_execution_id": "exec_plan_partial_r1",
        "customer_safe_summary": "Investigation completed. See internal summary for details.",
        "internal_summary": "Partial results.",
        "evidence_refs": [],
        "missing_information": ["Missing critical evidence"],
        "redaction_boundary": {},
        "do_not_expose_to_customer": [],
    },
    "blockers": [],
}


class EngineerReviewAgentContractTests(unittest.TestCase):
    """Contract tests for Engineer Review Agent v1.

    The Review Agent must be deterministic, must never call an LLM, and must
    never import repository modules.
    """

    def test_review_produces_required_top_level_fields(self) -> None:
        review = review_execution(
            active_execution=SAMPLE_EXECUTION_COMPLETED,
            engineer_agent_state=None,
            handoff_packet=None,
            ticket=None,
            now_value="2026-06-20T10:03:00Z",
        )

        self.assertIn("review_id", review)
        self.assertTrue(review["review_id"].startswith("review_"))
        self.assertEqual(review["review_version"], ENGINEER_REVIEW_VERSION)
        self.assertEqual(review["review_agent_version"], ENGINEER_REVIEW_AGENT_VERSION)
        self.assertEqual(review["created_by"], "review_agent")
        self.assertIn("created_at", review)
        self.assertEqual(review["plan_id"], SAMPLE_EXECUTION_COMPLETED["plan_id"])
        self.assertEqual(review["execution_id"], SAMPLE_EXECUTION_COMPLETED["execution_id"])
        self.assertIn("review_decision", review)
        self.assertIn(review["review_decision"], {"ready_for_engineer", "replan_required", "unable_to_resolve"})
        self.assertIn("replan_count", review)
        self.assertIn("problem_statement", review)
        self.assertIn("decision_rationale", review)
        self.assertIn("evidence_gaps", review)
        self.assertIn("missing_information", review)
        self.assertIn("recommended_action", review)
        self.assertIn("max_replan_exceeded", review)
        self.assertIn("max_replan_count", review)
        self.assertIn("blockers", review)

    def test_completed_execution_yields_ready_for_engineer(self) -> None:
        review = review_execution(
            active_execution=SAMPLE_EXECUTION_COMPLETED,
            engineer_agent_state=None,
            handoff_packet=None,
            ticket=None,
            now_value="2026-06-20T10:03:00Z",
        )

        self.assertEqual(review["review_decision"], "ready_for_engineer")
        self.assertFalse(review["max_replan_exceeded"])

    def test_blocked_execution_yields_unable_to_resolve(self) -> None:
        review = review_execution(
            active_execution=SAMPLE_EXECUTION_BLOCKED,
            engineer_agent_state=None,
            handoff_packet=None,
            ticket=None,
            now_value="2026-06-20T10:03:00Z",
        )

        self.assertEqual(review["review_decision"], "unable_to_resolve")
        self.assertGreater(len(review["evidence_gaps"]), 0)
        self.assertGreater(len(review["blockers"]), 0)

    def test_partial_execution_yields_replan_required(self) -> None:
        review = review_execution(
            active_execution=SAMPLE_EXECUTION_PARTIAL,
            engineer_agent_state=None,
            handoff_packet=None,
            ticket=None,
            now_value="2026-06-20T10:03:00Z",
        )

        self.assertEqual(review["review_decision"], "replan_required")
        self.assertFalse(review["max_replan_exceeded"])
        self.assertGreater(len(review["evidence_gaps"]), 0)

    def test_replan_count_increments_and_caps(self) -> None:
        # First replan (replan_count=0 → replan_required)
        review_1 = review_execution(
            active_execution=SAMPLE_EXECUTION_PARTIAL,
            engineer_agent_state={"replan_count": 0},
            handoff_packet=None,
            ticket=None,
            now_value="2026-06-20T10:03:00Z",
        )
        self.assertEqual(review_1["review_decision"], "replan_required")
        self.assertEqual(review_1["replan_count"], 0)
        self.assertFalse(review_1["max_replan_exceeded"])

        # Second replan (replan_count=1 → replan_required)
        review_2 = review_execution(
            active_execution=SAMPLE_EXECUTION_PARTIAL,
            engineer_agent_state={"replan_count": 1},
            handoff_packet=None,
            ticket=None,
            now_value="2026-06-20T10:04:00Z",
        )
        self.assertEqual(review_2["review_decision"], "replan_required")
        self.assertEqual(review_2["replan_count"], 1)
        self.assertFalse(review_2["max_replan_exceeded"])

        # Third attempt (replan_count=2 → unable_to_resolve, max exceeded)
        review_3 = review_execution(
            active_execution=SAMPLE_EXECUTION_PARTIAL,
            engineer_agent_state={"replan_count": 2},
            handoff_packet=None,
            ticket=None,
            now_value="2026-06-20T10:05:00Z",
        )
        self.assertEqual(review_3["review_decision"], "unable_to_resolve")
        self.assertEqual(review_3["replan_count"], 2)
        self.assertTrue(review_3["max_replan_exceeded"])

    def test_review_decision_only_has_valid_values(self) -> None:
        # Test with each sample execution
        for execution in [SAMPLE_EXECUTION_COMPLETED, SAMPLE_EXECUTION_BLOCKED, SAMPLE_EXECUTION_PARTIAL]:
            review = review_execution(
                active_execution=execution,
                engineer_agent_state=None,
                handoff_packet=None,
                ticket=None,
                now_value="2026-06-20T10:03:00Z",
            )
            self.assertIn(
                review["review_decision"],
                {"ready_for_engineer", "replan_required", "unable_to_resolve"},
                f"Unexpected review_decision for status={execution['status']}",
            )

    def test_review_never_calls_llm_or_repository(self) -> None:
        with patch("backend.services.llm_factory.invoke_responses_text") as invoke_mock:
            review_execution(
                active_execution=SAMPLE_EXECUTION_COMPLETED,
                engineer_agent_state=None,
                handoff_packet=None,
                ticket=None,
                now_value="2026-06-20T10:03:00Z",
            )

        invoke_mock.assert_not_called()

        module_source = inspect.getsource(engineer_review_agent)
        self.assertNotIn("ticket_repository", module_source)
        self.assertNotIn("backend.repositories", module_source)

    def test_review_with_missing_information_in_handoff_produces_gaps(self) -> None:
        handoff = {
            "missing_information": ["SDK version", "Platform"],
        }
        review = review_execution(
            active_execution=SAMPLE_EXECUTION_PARTIAL,
            engineer_agent_state=None,
            handoff_packet=handoff,
            ticket=None,
            now_value="2026-06-20T10:03:00Z",
        )

        self.assertEqual(review["review_decision"], "replan_required")
        self.assertGreater(len(review["evidence_gaps"]), 0)
        gaps_text = " ".join(review["evidence_gaps"]).lower()
        # Missing info is recorded in decision_rationale, not as gaps
        # since missing info alone does not block engineer review readiness
        self.assertEqual(review["missing_information"], ["Missing critical evidence"])
        self.assertIn("insufficient", review["decision_rationale"].lower())
        self.assertIn("missing critical evidence", review["decision_rationale"].lower())
        self.assertTrue(
            "synthesis" in gaps_text or "evidence" in gaps_text,
            f"Expected gaps to mention synthesis or evidence issues, got: {review['evidence_gaps']}",
        )

    def test_missing_customer_information_only_still_ready_for_engineer(self) -> None:
        execution = dict(SAMPLE_EXECUTION_COMPLETED)
        execution["status"] = "partial"
        execution["task_results"] = [
            dict(result)
            for result in SAMPLE_EXECUTION_COMPLETED["task_results"]
        ]
        execution["task_results"][3] = {
            **execution["task_results"][3],
            "status": "blocked",
            "summary": "Requires customer input: SDK version; Platform or OS version.",
        }
        execution["task_results"][4] = {
            **execution["task_results"][4],
            "status": "succeeded_with_blockers",
        }
        execution["blockers"] = [
            {
                "blocker_id": "blocker_missing_info",
                "type": "dependency_blocked",
                "description": "Requires customer input: SDK version; Platform or OS version.",
                "source_task_id": "task_missing_info_triage",
            }
        ]

        review = review_execution(
            active_execution=execution,
            engineer_agent_state=None,
            handoff_packet=None,
            ticket=None,
            now_value="2026-06-20T10:03:00Z",
        )

        self.assertEqual(review["review_decision"], "ready_for_engineer")
        self.assertEqual(
            review["missing_information"],
            ["SDK version", "Exact error code", "Platform or OS version", "Reproduction steps"],
        )
        self.assertEqual(review["evidence_gaps"], [])

    def test_review_preserves_execution_references(self) -> None:
        review = review_execution(
            active_execution=SAMPLE_EXECUTION_COMPLETED,
            engineer_agent_state=None,
            handoff_packet=None,
            ticket=None,
            now_value="2026-06-20T10:03:00Z",
        )

        self.assertEqual(review["execution_id"], SAMPLE_EXECUTION_COMPLETED["execution_id"])
        self.assertEqual(review["plan_id"], SAMPLE_EXECUTION_COMPLETED["plan_id"])
