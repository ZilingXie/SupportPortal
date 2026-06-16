from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from backend.services import engineer_plan_agent
from backend.services.engineer_plan_agent import (
    ENGINEER_PLAN_AGENT_VERSION,
    ENGINEER_PLAN_VERSION,
    build_engineer_plan,
    build_plan_blockers,
    build_plan_dependencies,
    build_plan_hypotheses,
    build_plan_tasks,
    resolve_plan_memory_context,
    resolve_plan_skill_context,
)

SAMPLE_SUMMARY_PACKET: dict = {
    "packet_id": "summary_ec_001",
    "packet_version": "engineer-summary-packet-v1",
    "summary_agent_version": "engineer-summary-agent-v1",
    "created_by": "summary_agent",
    "created_at": "2026-06-20T10:00:00Z",
    "source": "support_query",
    "product": "RTC",
    "conversation_summary": "Customer reports camera black screen after SDK upgrade.",
    "latest_customer_message": "My camera is black after upgrading to SDK 4.5.",
    "latest_client_ai_reply": "I need more info to help. Can you share your SDK version?",
    "route_summary": {
        "answer_route": "investigate",
        "scope_label": "rtc",
        "route_family": "troubleshooting",
        "execution_action": "escalate_to_engineer",
        "tooling_profile": "rag_first",
        "route_reason": "rag_insufficient_evidence",
        "route_confidence": 0.65,
    },
    "rag_result": {
        "candidate_answer": "Check camera permissions and SDK initialization order.",
        "sources": ["doc://camera-troubleshooting"],
        "citations": [{"chunk_id": "chunk_001", "heading": "Camera Setup"}],
    },
    "client_intake_state": {
        "phase": "collecting",
        "product": "RTC",
        "issue_mode": "troubleshooting",
        "known_information": {
            "issue_symptom": "camera black screen",
            "channel_name": "videoCall",
            "problematic_uid": "uid_12345",
        },
        "missing_information": ["SDK version"],
        "ready_for_engineer_ticket": True,
        "last_updated_at": "2026-06-20T09:55:00Z",
    },
    "unresolved_reason": "rag_insufficient_evidence",
    "customer_language_hint": "en",
    "client_ticket_ref": {
        "ticket_id": "ticket_cam_001",
        "customer_id": "cust_001",
        "requester": "Alice",
        "subject": "Camera black screen after upgrade",
        "product": "RTC",
        "status": "escalated",
    },
    "engineer_case_ref": {
        "engineer_case_id": "ec_001",
        "case_sequence": 1,
        "trigger_source": "support_query",
        "trigger_reason": "rag_insufficient_evidence",
    },
    "escalation": {
        "reason": "rag_insufficient_evidence",
        "route_family": "troubleshooting",
        "execution_action": "escalate_to_engineer",
        "tooling_profile": "rag_first",
        "confidence": 0.65,
        "needs_investigating": True,
    },
    "customer_context": {
        "latest_customer_message": "My camera is black after upgrading to SDK 4.5.",
        "conversation_summary": "Customer reports camera black screen after SDK upgrade.",
        "recent_messages": [
            {
                "role": "customer",
                "content": "My camera is black after upgrading to SDK 4.5.",
                "created_at": "2026-06-20T09:50:00Z",
            },
        ],
    },
    "current_clues": [
        {
            "kind": "rag_answer",
            "summary": "Check camera permissions and SDK initialization order.",
            "confidence": 0.45,
            "sources_count": 1,
            "citations_count": 1,
            "customer_safe": True,
        },
    ],
    "missing_information": ["SDK version", "Exact error code", "Platform or OS version", "Reproduction steps"],
    "redaction_boundary": {
        "customer_safe_summary_fields": ["customer_context.latest_customer_message", "current_clues.customer_safe"],
        "internal_only_fields": ["internal evidence refs", "raw tool traces", "route diagnostics"],
        "do_not_expose_to_customer": [
            "internal source paths",
            "private diagnostics",
            "unverified root cause",
        ],
    },
    "engineer_ticket_input": {
        "title": "[RTC] Camera black screen after upgrade",
        "opening_summary": "Customer message: My camera is black after upgrading to SDK 4.5.\nCustomer reports camera black screen after SDK upgrade.",
        "requested_action": "Investigate the issue. Missing information to collect if needed: SDK version, Exact error code, Platform or OS version, Reproduction steps",
        "initial_internal_note": "Escalated from Client AI. Trigger reason: rag_insufficient_evidence. Route: troubleshooting. Confidence: 0.65",
    },
}


class EngineerPlanAgentContractTests(unittest.TestCase):
    """Contract tests for Engineer Plan Agent v1.

    The Plan Agent must be deterministic, must never call an LLM, and must
    never import repository modules.
    """

    def test_build_plan_produces_required_top_level_fields(self) -> None:
        plan = build_engineer_plan(
            summary_packet=SAMPLE_SUMMARY_PACKET,
            mem0_context=None,
            skill_inventory=None,
            revise_context=None,
            now_value="2026-06-20T10:01:00Z",
        )

        self.assertEqual(plan["plan_version"], ENGINEER_PLAN_VERSION)
        self.assertEqual(plan["plan_agent_version"], ENGINEER_PLAN_AGENT_VERSION)
        self.assertEqual(plan["created_by"], "plan_agent")
        self.assertEqual(
            plan["source_summary_packet_id"],
            SAMPLE_SUMMARY_PACKET["packet_id"],
        )
        self.assertEqual(
            plan["source_summary_packet_version"],
            SAMPLE_SUMMARY_PACKET["packet_version"],
        )
        self.assertTrue(plan["plan_id"].startswith("plan_"))
        self.assertIn("objective", plan)
        self.assertIn("hypotheses", plan)
        self.assertIn("tasks", plan)
        self.assertIn("dependencies", plan)
        self.assertIn("blockers", plan)
        self.assertIn("memory_context", plan)
        self.assertIn("skill_context", plan)
        self.assertIn("scheduler_hints", plan)
        self.assertIn("redaction_boundary", plan)

    def test_plan_id_is_stable_for_same_packet(self) -> None:
        plan_a = build_engineer_plan(
            summary_packet=SAMPLE_SUMMARY_PACKET,
            mem0_context=None,
            skill_inventory=None,
            revise_context=None,
            now_value="2026-06-20T10:01:00Z",
        )
        plan_b = build_engineer_plan(
            summary_packet=SAMPLE_SUMMARY_PACKET,
            mem0_context=None,
            skill_inventory=None,
            revise_context=None,
            now_value="2026-06-20T10:02:00Z",
        )

        self.assertEqual(plan_a["plan_id"], plan_b["plan_id"])

    def test_memory_context_fallback_when_mem0_is_none(self) -> None:
        memory_context = resolve_plan_memory_context(mem0_context=None)

        self.assertEqual(memory_context["mode"], "fallback_unavailable")
        self.assertEqual(memory_context["memory_refs"], [])
        self.assertEqual(memory_context["fallback_reason"], "mem0_not_configured")

    def test_memory_context_accepts_populated_mem0(self) -> None:
        mem0_context = {
            "memories": [
                {"memory_record_id": "mem_001", "customer_safe_summary": "Similar camera issue resolved by SDK downgrade."},
            ],
        }
        memory_context = resolve_plan_memory_context(mem0_context=mem0_context)

        self.assertEqual(memory_context["mode"], "mem0")
        self.assertEqual(len(memory_context["memory_refs"]), 1)
        self.assertEqual(memory_context["memory_refs"][0]["memory_record_id"], "mem_001")

    def test_skill_context_uses_allowlist_fallback_when_none(self) -> None:
        skill_context = resolve_plan_skill_context(skill_inventory=None)

        self.assertEqual(skill_context["mode"], "allowlist_fallback")
        self.assertIn("context_review", skill_context["selected_skills"])
        self.assertIn("synthesis", skill_context["selected_skills"])
        self.assertEqual(skill_context["fallback_reason"], "skill_list_not_installed")

    def test_skill_context_accepts_installed_skills(self) -> None:
        skill_inventory = {"installed": True, "skills": ["internal_rag", "official_rag", "log_parser"]}
        skill_context = resolve_plan_skill_context(skill_inventory=skill_inventory)

        self.assertEqual(skill_context["mode"], "installed")
        self.assertIn("internal_rag", skill_context["available_skills"])
        self.assertGreaterEqual(len(skill_context["selected_skills"]), 1)

    def test_skill_context_keeps_core_tasks_with_partial_installed_skills(self) -> None:
        skill_inventory = {"installed": True, "skills": ["internal_knowledge_search"]}
        skill_context = resolve_plan_skill_context(skill_inventory=skill_inventory)

        self.assertEqual(
            skill_context["selected_skills"],
            ["context_review", "internal_knowledge_search", "synthesis"],
        )

    def test_build_hypotheses_from_summary_packet(self) -> None:
        hypotheses = build_plan_hypotheses(summary_packet=SAMPLE_SUMMARY_PACKET)

        self.assertIsInstance(hypotheses, list)
        self.assertGreaterEqual(len(hypotheses), 1)
        for hyp in hypotheses:
            self.assertIn("hypothesis_id", hyp)
            self.assertIn("statement", hyp)
            self.assertIn("confidence", hyp)
            self.assertIn("rationale", hyp)
            self.assertIn("evidence_refs", hyp)
            self.assertIn("risk_flags", hyp)
            # confidence must be "low" or "medium" — never "high"
            self.assertIn(hyp["confidence"], {"low", "medium"})
            # hypothesis must not claim root cause
            self.assertNotIn("root cause", hyp["statement"].lower())

    def test_build_tasks_from_summary_packet(self) -> None:
        tasks = build_plan_tasks(
            summary_packet=SAMPLE_SUMMARY_PACKET,
            selected_skills=["context_review", "internal_knowledge_search", "official_docs_fallback", "missing_info_triage", "synthesis"],
        )

        self.assertIsInstance(tasks, list)
        self.assertGreaterEqual(len(tasks), 1)
        for task in tasks:
            self.assertIn("task_id", task)
            self.assertIn("title", task)
            self.assertIn("description", task)
            self.assertIn("skill", task)
            self.assertIn("depends_on", task)
            self.assertIn("can_parallelize", task)
            self.assertIn("expected_output", task)
            self.assertIn("blockers", task)
            self.assertEqual(task["status"], "planned")
            # task_id must be snake_case with task_ prefix
            self.assertTrue(task["task_id"].startswith("task_"))

    def test_build_tasks_adds_context_and_synthesis_for_partial_skill_selection(self) -> None:
        tasks = build_plan_tasks(
            summary_packet=SAMPLE_SUMMARY_PACKET,
            selected_skills=["internal_knowledge_search"],
        )

        self.assertEqual(tasks[0]["task_id"], "task_context_review")
        self.assertEqual(tasks[-1]["task_id"], "task_synthesis")
        self.assertIn(
            "task_context_review",
            next(task for task in tasks if task["task_id"] == "task_internal_knowledge_search")["depends_on"],
        )
        self.assertIn("task_internal_knowledge_search", tasks[-1]["depends_on"])

    def test_build_dependencies_from_tasks(self) -> None:
        tasks = build_plan_tasks(
            summary_packet=SAMPLE_SUMMARY_PACKET,
            selected_skills=["context_review", "internal_knowledge_search", "official_docs_fallback", "missing_info_triage", "synthesis"],
        )
        dependencies = build_plan_dependencies(tasks=tasks)

        self.assertIsInstance(dependencies, list)
        for dep in dependencies:
            self.assertIn("from_task_id", dep)
            self.assertIn("to_task_id", dep)
            self.assertIn("reason", dep)
            # dependencies must reference valid tasks
            task_ids = {task["task_id"] for task in tasks}
            self.assertIn(dep["from_task_id"], task_ids)
            self.assertIn(dep["to_task_id"], task_ids)

    def test_build_blockers_from_summary_missing_information(self) -> None:
        blockers = build_plan_blockers(summary_packet=SAMPLE_SUMMARY_PACKET)

        self.assertIsInstance(blockers, list)
        # "SDK version" is explicitly listed as missing
        sdk_blockers = [b for b in blockers if "sdk" in b.get("description", "").lower()]
        self.assertGreaterEqual(len(sdk_blockers), 1)
        for blocker in blockers:
            self.assertIn("blocker_id", blocker)
            self.assertIn("type", blocker)
            self.assertIn("description", blocker)
            self.assertIn("severity", blocker)
            self.assertIn("source", blocker)

    def test_plan_includes_parallel_and_serial_scheduler_hints(self) -> None:
        plan = build_engineer_plan(
            summary_packet=SAMPLE_SUMMARY_PACKET,
            mem0_context=None,
            skill_inventory=None,
            revise_context=None,
            now_value="2026-06-20T10:01:00Z",
        )

        hints = plan["scheduler_hints"]
        self.assertIn("parallel_groups", hints)
        self.assertIn("serial_steps", hints)
        self.assertIsInstance(hints["parallel_groups"], list)
        self.assertIsInstance(hints["serial_steps"], list)

    def test_plan_never_calls_llm_or_repository(self) -> None:
        with patch("backend.services.llm_factory.invoke_responses_text") as invoke_mock:
            build_engineer_plan(
                summary_packet=SAMPLE_SUMMARY_PACKET,
                mem0_context=None,
                skill_inventory=None,
                revise_context=None,
                now_value="2026-06-20T10:01:00Z",
            )

        invoke_mock.assert_not_called()

        module_source = inspect.getsource(engineer_plan_agent)
        self.assertNotIn("ticket_repository", module_source)
        self.assertNotIn("backend.repositories", module_source)

    def test_redaction_boundary_carried_into_plan(self) -> None:
        plan = build_engineer_plan(
            summary_packet=SAMPLE_SUMMARY_PACKET,
            mem0_context=None,
            skill_inventory=None,
            revise_context=None,
            now_value="2026-06-20T10:01:00Z",
        )

        boundary = plan["redaction_boundary"]
        self.assertIn("do_not_expose_to_customer", boundary)
        self.assertIn("internal source paths", boundary["do_not_expose_to_customer"])

    def test_plan_objective_reflects_ticket_subject(self) -> None:
        plan = build_engineer_plan(
            summary_packet=SAMPLE_SUMMARY_PACKET,
            mem0_context=None,
            skill_inventory=None,
            revise_context=None,
            now_value="2026-06-20T10:01:00Z",
        )

        self.assertIn("Camera", plan["objective"])

    def test_build_plan_with_revise_context_captures_revision(self) -> None:
        revise_context = {
            "revise_note": "Focus on SDK initialization order, not permissions.",
            "previous_plan_id": "plan_summary_ec_001_r1",
            "replan_count": 1,
        }
        plan = build_engineer_plan(
            summary_packet=SAMPLE_SUMMARY_PACKET,
            mem0_context=None,
            skill_inventory=None,
            revise_context=revise_context,
            now_value="2026-06-20T10:01:00Z",
        )

        self.assertIn("revise_context", plan)
        self.assertEqual(plan["revise_context"]["revise_note"], revise_context["revise_note"])
        self.assertIn("r2", plan["plan_id"])

    def test_build_plan_with_revise_context_carries_previous_evidence_and_review(self) -> None:
        revise_context = {
            "revise_note": "Check SDK 4.2.1 compatibility with Android 14 specifically.",
            "previous_plan_id": "plan_summary_ec_001_r1",
            "previous_execution_id": "exec_plan_summary_ec_001_r1",
            "previous_review_id": "review_exec_plan_summary_ec_001_r1",
            "previous_review_decision": "replan_required",
            "review_problem_statement": "Evidence does not confirm the exact SDK version boundary.",
            "review_evidence_gaps": ["Exact SDK version not confirmed", "No cross-platform reproduction"],
            "previous_evidence_packet": {
                "packet_id": "evidence_exec_plan_summary_ec_001_r1",
                "packet_version": "engineer-evidence-packet-v1",
                "customer_safe_summary": "Upgrade to SDK 4.2.2 resolves the issue.",
                "internal_summary": "SDK 4.2.1 has a known callback bug on Android 14.",
                "evidence_refs": [
                    {"task_id": "task_context_review", "summary": "Reviewed ticket context"},
                    {"kind": "customer_message", "text": "token renew callback never fires"},
                ],
                "missing_information": ["Exact SDK version", "Device model"],
            },
            "previous_task_results": [
                {
                    "task_id": "task_context_review",
                    "status": "completed",
                    "summary": "Reviewed ticket context and handoff packet.",
                },
            ],
            "engineer_feedback": {
                "note": "Check SDK 4.2.1 compatibility with Android 14 specifically.",
                "engineer_id": "eng_001",
                "created_at": "2026-06-20T10:05:00Z",
            },
            "replan_count": 1,
        }
        plan = build_engineer_plan(
            summary_packet=SAMPLE_SUMMARY_PACKET,
            mem0_context=None,
            skill_inventory=None,
            revise_context=revise_context,
            now_value="2026-06-20T10:01:00Z",
        )

        self.assertIn("revise_context", plan)
        rc = plan["revise_context"]
        self.assertEqual(rc["revise_note"], revise_context["revise_note"])
        self.assertEqual(rc["previous_plan_id"], revise_context["previous_plan_id"])
        # engineer_feedback carried through
        self.assertIn("engineer_feedback", rc)
        self.assertEqual(rc["engineer_feedback"]["note"], revise_context["engineer_feedback"]["note"])
        # previous_evidence_packet carried through
        self.assertIn("previous_evidence_packet", rc)
        self.assertEqual(
            rc["previous_evidence_packet"]["packet_id"],
            revise_context["previous_evidence_packet"]["packet_id"],
        )
        self.assertEqual(
            rc["previous_evidence_packet"]["evidence_refs"][1]["kind"],
            "customer_message",
        )
        self.assertEqual(
            rc["previous_evidence_packet"]["evidence_refs"][1]["text"],
            "token renew callback never fires",
        )
        # review_problem_statement carried through
        self.assertEqual(rc["review_problem_statement"], revise_context["review_problem_statement"])
        # previous_task_results carried through
        self.assertIn("previous_task_results", rc)
        self.assertEqual(len(rc["previous_task_results"]), 1)
        self.assertEqual(rc["previous_task_results"][0]["task_id"], "task_context_review")
        # plan_id uses replan_count suffix _r2 (replan_count=1 -> _r{n+1}=_r2)
        self.assertIn("_r2", plan["plan_id"])
        self.assertNotIn("_r1", plan["plan_id"])


if __name__ == "__main__":
    unittest.main()
