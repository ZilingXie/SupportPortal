from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from backend.services import engineer_execute_agent
from backend.services.engineer_execute_agent import (
    ENGINEER_EXECUTE_AGENT_VERSION,
    ENGINEER_EXECUTION_VERSION,
    ENGINEER_EVIDENCE_PACKET_VERSION,
    _EXECUTE_AGENT_ALLOWLIST,
    build_evidence_packet,
    build_execution_schedule,
    execute_engineer_plan,
    validate_execute_plan,
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

SAMPLE_ACTIVE_PLAN: dict = {
    "plan_id": "plan_summary_ec_001_r1",
    "plan_version": "engineer-plan-v1",
    "plan_agent_version": "engineer-plan-agent-v1",
    "created_by": "plan_agent",
    "created_at": "2026-06-20T10:01:00Z",
    "source_summary_packet_id": "summary_ec_001",
    "source_summary_packet_version": "engineer-summary-packet-v1",
    "memory_context": {
        "mode": "fallback_unavailable",
        "memory_refs": [],
        "fallback_reason": "mem0_not_configured",
    },
    "skill_context": {
        "mode": "allowlist_fallback",
        "available_skills": [],
        "selected_skills": [
            "context_review",
            "internal_knowledge_search",
            "official_docs_fallback",
            "missing_info_triage",
            "synthesis",
        ],
        "fallback_reason": "skill_list_not_installed",
    },
    "objective": "Investigate engineer ticket: Camera black screen after upgrade",
    "hypotheses": [
        {
            "hypothesis_id": "hyp_summary_ec_001_missing_context",
            "statement": "The issue may require additional customer context or missing technical details that were not collected during intake.",
            "confidence": "medium",
            "rationale": "Missing information items: SDK version, Exact error code, Platform or OS version, Reproduction steps.",
            "evidence_refs": [{"kind": "missing_information", "items": ["SDK version", "Exact error code", "Platform or OS version", "Reproduction steps"]}],
            "risk_flags": ["delayed_resolution", "back_and_forth_with_customer"],
        },
        {
            "hypothesis_id": "hyp_summary_ec_001_rag_gap",
            "statement": "The existing RAG knowledge base may lack sufficient documentation for this specific issue or platform configuration.",
            "confidence": "medium",
            "rationale": "RAG resolution was: rag_insufficient_evidence.",
            "evidence_refs": [{"kind": "escalation_reason", "value": "rag_insufficient_evidence"}],
            "risk_flags": ["knowledge_gap", "may_need_manual_intervention"],
        },
    ],
    "tasks": [
        {
            "task_id": "task_context_review",
            "title": "Review ticket and handoff context",
            "description": "Read the full ticket history, handoff packet, and escalation context to normalize the issue understanding.",
            "skill": "context_review",
            "depends_on": [],
            "can_parallelize": False,
            "expected_output": "Normalized issue summary with known facts, missing information, and investigation scope.",
            "blockers": [],
            "status": "planned",
        },
        {
            "task_id": "task_internal_knowledge_search",
            "title": "Search internal troubleshooting knowledge",
            "description": "Query the internal knowledge base (non-public docs, past case memory) for relevant troubleshooting patterns.",
            "skill": "internal_knowledge_search",
            "depends_on": ["task_context_review"],
            "can_parallelize": True,
            "expected_output": "Ranked list of relevant internal evidence with source references.",
            "blockers": [],
            "status": "planned",
        },
        {
            "task_id": "task_official_docs_fallback",
            "title": "Check official documentation for customer-safe wording",
            "description": "Search the public official documentation for accurate, customer-safe descriptions of the symptoms and remediation steps.",
            "skill": "official_docs_fallback",
            "depends_on": ["task_context_review"],
            "can_parallelize": True,
            "expected_output": "Official documentation references suitable for customer-facing replies.",
            "blockers": [],
            "status": "planned",
        },
        {
            "task_id": "task_missing_info_triage",
            "title": "Triage missing information and collect if possible",
            "description": "Identify which missing pieces can be inferred or auto-collected, and which must be requested from the customer or engineer.",
            "skill": "missing_info_triage",
            "depends_on": ["task_context_review"],
            "can_parallelize": True,
            "expected_output": "Categorized missing information with collection strategy per item.",
            "blockers": [],
            "status": "planned",
        },
        {
            "task_id": "task_synthesis",
            "title": "Synthesize findings into a conclusion",
            "description": "Combine results from all prior tasks, cross-check evidence, and produce a coherent conclusion with confidence level.",
            "skill": "synthesis",
            "depends_on": [
                "task_internal_knowledge_search",
                "task_official_docs_fallback",
                "task_missing_info_triage",
            ],
            "can_parallelize": False,
            "expected_output": "Structured conclusion with evidence summary, confidence, and next-action recommendation.",
            "blockers": [],
            "status": "planned",
        },
    ],
    "dependencies": [
        {
            "from_task_id": "task_context_review",
            "to_task_id": "task_internal_knowledge_search",
            "reason": "Need normalized issue context before searching internal knowledge.",
        },
        {
            "from_task_id": "task_context_review",
            "to_task_id": "task_official_docs_fallback",
            "reason": "Need normalized issue context before searching official docs.",
        },
        {
            "from_task_id": "task_context_review",
            "to_task_id": "task_missing_info_triage",
            "reason": "Need normalized issue context to assess missing information.",
        },
        {
            "from_task_id": "task_internal_knowledge_search",
            "to_task_id": "task_synthesis",
            "reason": "Need internal evidence before synthesizing conclusion.",
        },
        {
            "from_task_id": "task_official_docs_fallback",
            "to_task_id": "task_synthesis",
            "reason": "Need official documentation references before synthesizing conclusion.",
        },
        {
            "from_task_id": "task_missing_info_triage",
            "to_task_id": "task_synthesis",
            "reason": "Need triaged missing information to assess conclusion completeness.",
        },
    ],
    "blockers": [
        {
            "blocker_id": "blocker_summary_ec_001_sdk_version",
            "type": "missing_customer_info",
            "description": "SDK version",
            "severity": "medium",
            "source": "summary_packet.missing_information",
        },
        {
            "blocker_id": "blocker_summary_ec_001_exact_error_code",
            "type": "missing_customer_info",
            "description": "Exact error code",
            "severity": "medium",
            "source": "summary_packet.missing_information",
        },
        {
            "blocker_id": "blocker_summary_ec_001_platform_or_os_version",
            "type": "missing_customer_info",
            "description": "Platform or OS version",
            "severity": "medium",
            "source": "summary_packet.missing_information",
        },
        {
            "blocker_id": "blocker_summary_ec_001_reproduction_steps",
            "type": "missing_customer_info",
            "description": "Reproduction steps",
            "severity": "medium",
            "source": "summary_packet.missing_information",
        },
    ],
    "scheduler_hints": {
        "parallel_groups": [
            [
                "task_internal_knowledge_search",
                "task_official_docs_fallback",
                "task_missing_info_triage",
            ]
        ],
        "serial_steps": ["task_context_review", "task_synthesis"],
    },
    "redaction_boundary": {
        "customer_safe_summary_fields": ["customer_context.latest_customer_message", "current_clues.customer_safe"],
        "internal_only_fields": ["internal evidence refs", "raw tool traces", "route diagnostics"],
        "do_not_expose_to_customer": [
            "internal source paths",
            "private diagnostics",
            "unverified root cause",
        ],
    },
}


class EngineerExecuteAgentContractTests(unittest.TestCase):
    """Contract tests for Engineer Execute Agent v1.

    The Execute Agent must be deterministic, must never call an LLM, and must
    never import repository modules.
    """

    def test_execute_plan_produces_required_top_level_fields(self) -> None:
        execution = execute_engineer_plan(
            active_plan=SAMPLE_ACTIVE_PLAN,
            summary_packet=SAMPLE_SUMMARY_PACKET,
            engineer_agent_state=None,
            execution_context=None,
            now_value="2026-06-20T10:02:00Z",
        )

        self.assertIn("execution_id", execution)
        self.assertTrue(execution["execution_id"].startswith("exec_"))
        self.assertEqual(execution["execution_version"], ENGINEER_EXECUTION_VERSION)
        self.assertEqual(execution["execute_agent_version"], ENGINEER_EXECUTE_AGENT_VERSION)
        self.assertEqual(execution["created_by"], "execute_agent")
        self.assertIn("created_at", execution)
        self.assertEqual(execution["plan_id"], SAMPLE_ACTIVE_PLAN["plan_id"])
        self.assertEqual(execution["plan_version"], SAMPLE_ACTIVE_PLAN["plan_version"])
        self.assertIn("status", execution)
        self.assertIn(execution["status"], {"completed", "blocked", "partial"})
        self.assertIn("scheduler", execution)
        self.assertIn("task_results", execution)
        self.assertIn("evidence_packet", execution)
        self.assertIn("blockers", execution)

    def test_execute_only_dispatches_allowlisted_skills(self) -> None:
        execution = execute_engineer_plan(
            active_plan=SAMPLE_ACTIVE_PLAN,
            summary_packet=SAMPLE_SUMMARY_PACKET,
            engineer_agent_state=None,
            execution_context=None,
            now_value="2026-06-20T10:02:00Z",
        )

        for result in execution["task_results"]:
            skill = result.get("skill")
            if skill:
                self.assertIn(skill, _EXECUTE_AGENT_ALLOWLIST)

    def test_unknown_skill_becomes_skipped_or_blocked(self) -> None:
        plan_with_unknown = dict(SAMPLE_ACTIVE_PLAN)
        plan_with_unknown["tasks"] = list(SAMPLE_ACTIVE_PLAN["tasks"]) + [
            {
                "task_id": "task_unknown_skill",
                "title": "Unknown skill task",
                "description": "This skill is not in the allowlist.",
                "skill": "unsupported_skill",
                "depends_on": [],
                "can_parallelize": False,
                "expected_output": "Should not execute.",
                "blockers": [],
                "status": "planned",
            },
        ]

        execution = execute_engineer_plan(
            active_plan=plan_with_unknown,
            summary_packet=SAMPLE_SUMMARY_PACKET,
            engineer_agent_state=None,
            execution_context=None,
            now_value="2026-06-20T10:02:00Z",
        )

        unknown_results = [
            r for r in execution["task_results"]
            if r["task_id"] == "task_unknown_skill"
        ]
        self.assertGreaterEqual(len(unknown_results), 1)
        self.assertIn(unknown_results[0]["status"], {"skipped", "blocked"})

        # Verify no subagent was actually run for the unknown skill
        unknown_blockers = [
            b for b in execution["blockers"]
            if b.get("source_task_id") == "task_unknown_skill"
        ]
        self.assertGreaterEqual(len(unknown_blockers), 1)

    def test_scheduler_produces_deterministic_execution_order(self) -> None:
        execution_a = execute_engineer_plan(
            active_plan=SAMPLE_ACTIVE_PLAN,
            summary_packet=SAMPLE_SUMMARY_PACKET,
            engineer_agent_state=None,
            execution_context=None,
            now_value="2026-06-20T10:02:00Z",
        )
        execution_b = execute_engineer_plan(
            active_plan=SAMPLE_ACTIVE_PLAN,
            summary_packet=SAMPLE_SUMMARY_PACKET,
            engineer_agent_state=None,
            execution_context=None,
            now_value="2026-06-20T10:03:00Z",
        )

        self.assertEqual(
            execution_a["scheduler"]["execution_order"],
            execution_b["scheduler"]["execution_order"],
        )

    def test_scheduler_distinguishes_serial_and_parallel_stages(self) -> None:
        execution = execute_engineer_plan(
            active_plan=SAMPLE_ACTIVE_PLAN,
            summary_packet=SAMPLE_SUMMARY_PACKET,
            engineer_agent_state=None,
            execution_context=None,
            now_value="2026-06-20T10:02:00Z",
        )

        execution_order = execution["scheduler"]["execution_order"]
        self.assertIsInstance(execution_order, list)
        self.assertGreaterEqual(len(execution_order), 1)

        modes = {stage["mode"] for stage in execution_order}
        self.assertIn("serial", modes)
        self.assertIn("parallel", modes)

        # First stage should be serial (context_review)
        self.assertEqual(execution_order[0]["mode"], "serial")
        self.assertIn("task_context_review", execution_order[0]["task_ids"])

        # Last stage should be serial (synthesis)
        self.assertEqual(execution_order[-1]["mode"], "serial")
        self.assertIn("task_synthesis", execution_order[-1]["task_ids"])

    def test_task_results_collected_for_every_task(self) -> None:
        execution = execute_engineer_plan(
            active_plan=SAMPLE_ACTIVE_PLAN,
            summary_packet=SAMPLE_SUMMARY_PACKET,
            engineer_agent_state=None,
            execution_context=None,
            now_value="2026-06-20T10:02:00Z",
        )

        result_task_ids = {r["task_id"] for r in execution["task_results"]}
        planned_task_ids = {t["task_id"] for t in SAMPLE_ACTIVE_PLAN["tasks"]}
        for task_id in planned_task_ids:
            self.assertIn(task_id, result_task_ids, f"Task {task_id} missing from task_results")

        for result in execution["task_results"]:
            self.assertIn("task_id", result)
            self.assertIn("skill", result)
            self.assertIn("subagent", result)
            self.assertIn("status", result)
            self.assertIn("summary", result)
            self.assertIn("evidence_refs", result)
            self.assertIn("missing_information", result)
            self.assertIn("started_at", result)
            self.assertIn("completed_at", result)

    def test_evidence_packet_has_correct_structure(self) -> None:
        execution = execute_engineer_plan(
            active_plan=SAMPLE_ACTIVE_PLAN,
            summary_packet=SAMPLE_SUMMARY_PACKET,
            engineer_agent_state=None,
            execution_context=None,
            now_value="2026-06-20T10:02:00Z",
        )

        evidence = execution["evidence_packet"]
        self.assertIn("packet_id", evidence)
        self.assertTrue(evidence["packet_id"].startswith("evidence_"))
        self.assertEqual(evidence["packet_version"], ENGINEER_EVIDENCE_PACKET_VERSION)
        self.assertEqual(evidence["source_execution_id"], execution["execution_id"])
        self.assertIn("customer_safe_summary", evidence)
        self.assertIn("internal_summary", evidence)
        self.assertIn("evidence_refs", evidence)
        self.assertIn("missing_information", evidence)
        self.assertIn("redaction_boundary", evidence)
        self.assertIn("do_not_expose_to_customer", evidence)

    def test_do_not_expose_content_not_in_customer_safe_summary(self) -> None:
        execution = execute_engineer_plan(
            active_plan=SAMPLE_ACTIVE_PLAN,
            summary_packet=SAMPLE_SUMMARY_PACKET,
            engineer_agent_state=None,
            execution_context=None,
            now_value="2026-06-20T10:02:00Z",
        )

        evidence = execution["evidence_packet"]
        customer_safe = evidence["customer_safe_summary"].lower()
        do_not_expose = evidence.get("do_not_expose_to_customer", [])

        for phrase in do_not_expose:
            self.assertNotIn(
                phrase.lower(),
                customer_safe,
                f"do-not-expose phrase '{phrase}' leaked into customer_safe_summary",
            )

    def test_execute_never_calls_llm_or_repository(self) -> None:
        with patch("backend.services.llm_factory.invoke_responses_text") as invoke_mock:
            execute_engineer_plan(
                active_plan=SAMPLE_ACTIVE_PLAN,
                summary_packet=SAMPLE_SUMMARY_PACKET,
                engineer_agent_state=None,
                execution_context=None,
                now_value="2026-06-20T10:02:00Z",
            )

        invoke_mock.assert_not_called()

        module_source = inspect.getsource(engineer_execute_agent)
        self.assertNotIn("ticket_repository", module_source)
        self.assertNotIn("backend.repositories", module_source)

    def test_validate_plan_detects_missing_dependency(self) -> None:
        plan_with_bad_dep = dict(SAMPLE_ACTIVE_PLAN)
        plan_with_bad_dep["tasks"] = [
            {
                "task_id": "task_only",
                "title": "Only task",
                "description": "A task.",
                "skill": "context_review",
                "depends_on": ["task_nonexistent"],
                "can_parallelize": False,
                "expected_output": "Something.",
                "blockers": [],
                "status": "planned",
            },
        ]

        valid, blocked_tasks, warnings = validate_execute_plan(plan_with_bad_dep)
        self.assertFalse(valid)
        self.assertIn("task_only", blocked_tasks)

    def test_validate_plan_detects_cycle(self) -> None:
        plan_with_cycle = dict(SAMPLE_ACTIVE_PLAN)
        plan_with_cycle["tasks"] = [
            {
                "task_id": "task_a",
                "title": "Task A",
                "description": "A.",
                "skill": "context_review",
                "depends_on": ["task_b"],
                "can_parallelize": False,
                "expected_output": "A.",
                "blockers": [],
                "status": "planned",
            },
            {
                "task_id": "task_b",
                "title": "Task B",
                "description": "B.",
                "skill": "internal_knowledge_search",
                "depends_on": ["task_a"],
                "can_parallelize": True,
                "expected_output": "B.",
                "blockers": [],
                "status": "planned",
            },
        ]

        valid, blocked_tasks, warnings = validate_execute_plan(plan_with_cycle)
        self.assertFalse(valid)

    def test_cyclic_dependency_blocks_execution(self) -> None:
        plan_with_cycle = dict(SAMPLE_ACTIVE_PLAN)
        plan_with_cycle["tasks"] = [
            {
                "task_id": "task_a",
                "title": "Task A",
                "description": "A.",
                "skill": "context_review",
                "depends_on": ["task_b"],
                "can_parallelize": False,
                "expected_output": "A.",
                "blockers": [],
                "status": "planned",
            },
            {
                "task_id": "task_b",
                "title": "Task B",
                "description": "B.",
                "skill": "synthesis",
                "depends_on": ["task_a"],
                "can_parallelize": False,
                "expected_output": "B.",
                "blockers": [],
                "status": "planned",
            },
        ]

        execution = execute_engineer_plan(
            active_plan=plan_with_cycle,
            summary_packet=SAMPLE_SUMMARY_PACKET,
            engineer_agent_state=None,
            execution_context=None,
            now_value="2026-06-20T10:02:00Z",
        )

        self.assertEqual(execution["status"], "blocked")
        cycle_blockers = [
            b for b in execution["blockers"]
            if b["type"] == "dependency_blocked"
            and "cycle" in b.get("description", "").lower()
        ]
        self.assertGreaterEqual(len(cycle_blockers), 1)

    def test_build_execution_schedule_respects_hints(self) -> None:
        schedule = build_execution_schedule(SAMPLE_ACTIVE_PLAN)

        self.assertIn("mode", schedule)
        self.assertEqual(schedule["mode"], "deterministic_allowlist")
        self.assertIn("parallel_groups", schedule)
        self.assertIn("serial_steps", schedule)
        self.assertIn("execution_order", schedule)

        # Context review must be first (serial)
        first_stage = schedule["execution_order"][0]
        self.assertEqual(first_stage["mode"], "serial")
        self.assertIn("task_context_review", first_stage["task_ids"])

        # Synthesis must be last (serial)
        last_stage = schedule["execution_order"][-1]
        self.assertEqual(last_stage["mode"], "serial")
        self.assertIn("task_synthesis", last_stage["task_ids"])

    def test_build_evidence_packet_respects_redaction(self) -> None:
        evidence = build_evidence_packet(
            execution_id="exec_test_001",
            plan=SAMPLE_ACTIVE_PLAN,
            task_results=[],
            blockers=[],
        )

        self.assertEqual(evidence["packet_version"], ENGINEER_EVIDENCE_PACKET_VERSION)
        self.assertIn("customer_safe_summary", evidence)
        self.assertIn("internal_summary", evidence)
        # redaction_boundary must be carried forward
        boundary = evidence.get("redaction_boundary", {})
        do_not = boundary.get("do_not_expose_to_customer", [])
        self.assertIn("internal source paths", do_not)

    def test_execution_with_no_tasks_still_produces_packet(self) -> None:
        plan_no_tasks = dict(SAMPLE_ACTIVE_PLAN)
        plan_no_tasks["tasks"] = []

        execution = execute_engineer_plan(
            active_plan=plan_no_tasks,
            summary_packet=SAMPLE_SUMMARY_PACKET,
            engineer_agent_state=None,
            execution_context=None,
            now_value="2026-06-20T10:02:00Z",
        )

        self.assertIn("execution_id", execution)
        self.assertEqual(execution["status"], "blocked")
        self.assertEqual(len(execution["task_results"]), 0)

    def test_execute_captures_missing_dep_blockers(self) -> None:
        plan_bad_dep = dict(SAMPLE_ACTIVE_PLAN)
        plan_bad_dep["tasks"] = list(SAMPLE_ACTIVE_PLAN["tasks"]) + [
            {
                "task_id": "task_orphan",
                "title": "Orphan task",
                "description": "Depends on nonexistent task.",
                "skill": "context_review",
                "depends_on": ["task_does_not_exist"],
                "can_parallelize": False,
                "expected_output": "Should be blocked.",
                "blockers": [],
                "status": "planned",
            },
        ]

        execution = execute_engineer_plan(
            active_plan=plan_bad_dep,
            summary_packet=SAMPLE_SUMMARY_PACKET,
            engineer_agent_state=None,
            execution_context=None,
            now_value="2026-06-20T10:02:00Z",
        )

        dep_blockers = [
            b for b in execution["blockers"]
            if b["type"] == "dependency_blocked"
        ]
        self.assertGreaterEqual(len(dep_blockers), 1)
        orphan_results = [
            r for r in execution["task_results"]
            if r["task_id"] == "task_orphan"
        ]
        self.assertEqual(len(orphan_results), 1)
        self.assertEqual(orphan_results[0]["status"], "blocked")
        self.assertIn("validation failure", orphan_results[0]["summary"])

    def test_cycle_blocks_tasks_with_task_results(self) -> None:
        plan_with_cycle = dict(SAMPLE_ACTIVE_PLAN)
        plan_with_cycle["tasks"] = [
            {
                "task_id": "task_a",
                "title": "Task A",
                "description": "A.",
                "skill": "context_review",
                "depends_on": ["task_b"],
                "can_parallelize": False,
                "expected_output": "A.",
                "blockers": [],
                "status": "planned",
            },
            {
                "task_id": "task_b",
                "title": "Task B",
                "description": "B.",
                "skill": "synthesis",
                "depends_on": ["task_a"],
                "can_parallelize": False,
                "expected_output": "B.",
                "blockers": [],
                "status": "planned",
            },
        ]

        execution = execute_engineer_plan(
            active_plan=plan_with_cycle,
            summary_packet=SAMPLE_SUMMARY_PACKET,
            engineer_agent_state=None,
            execution_context=None,
            now_value="2026-06-20T10:02:00Z",
        )

        self.assertEqual(execution["status"], "blocked")
        self.assertEqual({r["task_id"] for r in execution["task_results"]}, {"task_a", "task_b"})
        for result in execution["task_results"]:
            self.assertEqual(result["status"], "blocked")

    def test_customer_safe_summary_excludes_internal_task_summaries(self) -> None:
        summary_packet = dict(SAMPLE_SUMMARY_PACKET)
        summary_packet["current_clues"] = [
            {
                "kind": "rag_answer",
                "summary": "private diagnostics: internal-only root cause candidate",
                "confidence": 0.4,
                "customer_safe": False,
            }
        ]
        execution = execute_engineer_plan(
            active_plan=SAMPLE_ACTIVE_PLAN,
            summary_packet=summary_packet,
            engineer_agent_state=None,
            execution_context=None,
            now_value="2026-06-20T10:02:00Z",
        )

        customer_safe = execution["evidence_packet"]["customer_safe_summary"].lower()
        self.assertNotIn("private diagnostics", customer_safe)
        self.assertNotIn("internal-only root cause", customer_safe)


if __name__ == "__main__":
    unittest.main()
