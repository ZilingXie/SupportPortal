from __future__ import annotations

from pathlib import Path
import unittest


class EngineerAiEvolutionPlanContractTests(unittest.TestCase):
    def test_phase1a_hitl_feedback_schema_spec_exists_and_defines_core_boundaries(self) -> None:
        spec_path = Path(
            "docs/superpowers/specs/2026-06-10-engineer-hitl-feedback-schema-design.md"
        )
        self.assertTrue(spec_path.exists())

        spec_source = spec_path.read_text(encoding="utf-8")
        required_terms = [
            "阶段 1A",
            "support_engineer_hitl_feedback",
            "approve 不等于 confirmed case",
            "memory_candidate",
            "memory_safety",
            "evidence_refs",
            "diagnosis_correctness",
            "root_cause_correctness",
            "prompt_version",
            "workflow_version",
            "tool_policy_version",
            "rag_access_policy_version",
            "evidence_packet_version",
            "Case Memory Layer",
            "Optimization Lab",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, spec_source)

    def test_engineer_ai_evolution_html_tracks_phase1a_schema_step(self) -> None:
        html_source = Path("docs/engineer_ai_evolution_plan.html").read_text(encoding="utf-8")
        required_terms = [
            "阶段 1A：反馈 schema 固化",
            "support_engineer_hitl_feedback",
            "approve 不等于 confirmed case",
            "memory_candidate",
            "docs/superpowers/specs/2026-06-10-engineer-hitl-feedback-schema-design.md",
            'data-task="feedback-schema-design"',
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)

    def test_engineer_ai_evolution_html_tracks_case_memory_ledger_and_active_memory_split(self) -> None:
        html_source = Path("docs/engineer_ai_evolution_plan.html").read_text(encoding="utf-8")
        required_terms = [
            "Case Memory Ledger",
            "Active Retrieval Memory",
            "support_case_memory_ledger",
            "retrieval_enabled",
            "ledger_only",
            "candidate",
            "active",
            "所有 feedback 都进入 Case Memory Ledger",
            "Engineer AI 只自动检索 Active Retrieval Memory",
            "阶段 2A：Case Memory Ledger",
            'data-task="case-memory-ledger"',
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)

    def test_engineer_ai_evolution_html_tracks_online_multi_agent_plan(self) -> None:
        html_source = Path("docs/engineer_ai_evolution_plan.html").read_text(encoding="utf-8")
        required_terms = [
            "EvoAgentX 计划暂停",
            "线上 Engineer Multi-Agent 主链路",
            "Engineer Guardrail",
            "Plan Agent",
            "Memory Review Agent",
            "Implement Agent",
            "Conclude Agent",
            "工程师批准后必须再走现有 guardrail",
            "工程师 revise 后重新进入 Plan Agent",
            'data-task="online-multi-agent-orchestrator"',
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)

    def test_engineer_multi_agent_implementation_plan_tracks_step1_backend_skeleton(self) -> None:
        plan_source = Path(
            "docs/superpowers/plans/2026-06-11-engineer-ai-multi-agent-mainline.md"
        ).read_text(encoding="utf-8")
        required_terms = [
            "Current Next Step",
            "Task 1: Backend Skeleton And Data Contracts",
            "ENGINEER_MULTI_AGENT_PLAN_VERSION",
            "build_initial_multi_agent_plan",
            "review_multi_agent_plan",
            "record_multi_agent_task_result",
            "build_multi_agent_conclusion",
            "test_first_step_does_not_call_llms_or_repositories",
            "python3 -m unittest backend.tests.test_engineer_multi_agent -v",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, plan_source)
