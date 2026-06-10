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
