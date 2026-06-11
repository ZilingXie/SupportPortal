from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from backend.services import engineer_multi_agent
from backend.services.engineer_multi_agent import (
    ENGINEER_MULTI_AGENT_PLAN_VERSION,
    build_initial_multi_agent_plan,
    build_multi_agent_conclusion,
    record_multi_agent_task_result,
    review_multi_agent_plan,
)


class EngineerMultiAgentContractTests(unittest.TestCase):
    def test_build_initial_plan_records_ticket_context_and_tasks(self) -> None:
        plan = build_initial_multi_agent_plan(
            ticket={
                "ticket_id": "ticket_camera_1",
                "subject": "Camera fails after SDK upgrade",
                "messages": [{"role": "user", "content": "Camera stopped after upgrading."}],
            },
            handoff_packet={"summary": "Client-side AI escalated because reproduction details are missing."},
            engineer_agent_state={"issue_understanding": "Camera failure after upgrade"},
            revise_note=None,
            available_skills=["context_summary", "internal_rag", "official_rag"],
        )

        self.assertEqual(plan["plan_version"], ENGINEER_MULTI_AGENT_PLAN_VERSION)
        self.assertEqual(plan["created_by"], "plan_agent")
        self.assertIn("Camera", plan["objective"])
        self.assertGreaterEqual(len(plan["tasks"]), 1)
        self.assertEqual(plan["tasks"][0]["status"], "planned")
        self.assertIn("task_id", plan["tasks"][0])
        self.assertEqual(plan["risk_flags"], [])

    def test_initial_plan_preserves_revise_note_for_next_round(self) -> None:
        plan = build_initial_multi_agent_plan(
            ticket={"ticket_id": "ticket_2", "subject": "Webhook failure"},
            handoff_packet={},
            engineer_agent_state={},
            revise_note="Do not assume the SDK is broken; check webhook signature first.",
            available_skills=["context_summary"],
        )

        self.assertEqual(
            plan["revise_note"],
            "Do not assume the SDK is broken; check webhook signature first.",
        )
        self.assertIn("revise", plan["context_summary"].lower())

    def test_review_plan_returns_scheduler_shape_without_memory_access(self) -> None:
        plan = build_initial_multi_agent_plan(
            ticket={"ticket_id": "ticket_3", "subject": "Token expired"},
            handoff_packet={},
            engineer_agent_state={},
            revise_note=None,
            available_skills=["context_summary"],
        )

        reviewed = review_multi_agent_plan(plan, active_memories=[])

        self.assertEqual(reviewed["plan_id"], plan["plan_id"])
        self.assertEqual(reviewed["review_status"], "ready")
        self.assertEqual(reviewed["reviewed_by"], "memory_review_agent")
        self.assertEqual(reviewed["memory_refs"], [])
        self.assertTrue(reviewed["parallel_groups"])

    def test_record_task_result_normalizes_evidence_and_missing_information(self) -> None:
        result = record_multi_agent_task_result(
            task_id="task_context_review",
            status="succeeded",
            summary="Ticket context reviewed.",
            evidence_refs=[{"kind": "ticket", "id": "ticket_4"}],
            missing_information=["SDK version"],
        )

        self.assertEqual(result["task_id"], "task_context_review")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["evidence_refs"][0]["kind"], "ticket")
        self.assertEqual(result["missing_information"], ["SDK version"])

    def test_build_conclusion_requires_engineer_input_when_evidence_is_missing(self) -> None:
        plan = build_initial_multi_agent_plan(
            ticket={"ticket_id": "ticket_5", "subject": "Camera permission issue"},
            handoff_packet={},
            engineer_agent_state={},
            revise_note=None,
            available_skills=["context_summary"],
        )
        task_result = record_multi_agent_task_result(
            task_id="task_context_review",
            status="succeeded",
            summary="Need SDK version before diagnosing.",
            evidence_refs=[],
            missing_information=["SDK version"],
        )

        conclusion = build_multi_agent_conclusion(plan=plan, task_results=[task_result])

        self.assertEqual(conclusion["created_by"], "conclude_agent")
        self.assertEqual(conclusion["conclusion_status"], "needs_engineer_input")
        self.assertEqual(conclusion["confidence"], "low")
        self.assertEqual(conclusion["root_cause_status"], "unknown")
        self.assertIn("SDK version", conclusion["missing_information"])
        self.assertEqual(conclusion["customer_safe_draft"], "")

    def test_first_step_does_not_call_llms_or_repositories(self) -> None:
        with patch("backend.services.llm_factory.invoke_responses_text") as invoke_mock:
            plan = build_initial_multi_agent_plan(
                ticket={"ticket_id": "ticket_6", "subject": "No audio"},
                handoff_packet={},
                engineer_agent_state={},
                revise_note=None,
                available_skills=["context_summary"],
            )
            reviewed = review_multi_agent_plan(plan, active_memories=[])
            result = record_multi_agent_task_result(
                task_id="task_context_review",
                status="succeeded",
                summary="Context only.",
                evidence_refs=[],
                missing_information=[],
            )
            conclusion = build_multi_agent_conclusion(plan=plan, task_results=[result])

        invoke_mock.assert_not_called()
        self.assertEqual(reviewed["review_status"], "ready")
        self.assertIn(conclusion["conclusion_status"], {"ready_for_engineer_review", "needs_engineer_input"})

        module_source = inspect.getsource(engineer_multi_agent)
        self.assertNotIn("ticket_repository", module_source)
        self.assertNotIn("backend.repositories", module_source)
