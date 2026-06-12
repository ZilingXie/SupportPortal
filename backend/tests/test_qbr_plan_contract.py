from __future__ import annotations

from pathlib import Path
import unittest


class QbrPlanContractTests(unittest.TestCase):
    def test_qbr_plan_top_layout_merges_judgment_and_collapses_recent_updates(self) -> None:
        html_source = Path("docs/qbr_plan.html").read_text(encoding="utf-8")
        required_terms = [
            "font-size: clamp(32px, 4vw, 64px);",
            "min-height: 420px;",
            'class="hero-judgment"',
            'aria-label="Current QBR judgment"',
            'class="recent-updates-disclosure"',
            "查看最近合入",
            "默认收起",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)
        self.assertLess(
            html_source.index('class="hero-judgment"'),
            html_source.index('class="hero-meta"'),
        )
        self.assertNotIn('<section class="snapshot"', html_source)
        self.assertNotIn('<details class="recent-updates-disclosure" open', html_source)

    def test_qbr_plan_tracks_multi_agent_lane(self) -> None:
        html_source = Path("docs/qbr_plan.html").read_text(encoding="utf-8")
        required_terms = [
            "Engineer AI multi-agent",
            "Summary Agent",
            "summary packet",
            "Plan Agent",
            "Execute Agent",
            "Review Agent",
            "ready_for_engineer",
            "replan_required",
            "unable_to_resolve",
            "最多 2 次 replan",
            "Guardrail final approve",
            "Summary Agent 第一版（deterministic summary packet）已完成",
            "Plan Agent 第一版已完成",
            "engineer_agent_state.active_plan",
            "engineer_handoff_packet",
            "engineer-summary-packet-v1",
            "class=\"mermaid\"",
            "flowchart TD",
            "Create Engineer Ticket with summary packet",
            "Evidence packet + task results",
            "max 2 retries",
            "Case Memory candidate",
            "mermaid.min.js",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)
        self.assertNotIn("当前下一步：实现 Summary Agent", html_source)
        self.assertNotIn("当前下一步：实现真实 Plan Agent", html_source)
        self.assertNotIn("当前下一步是实现 Plan Agent", html_source)

    def test_qbr_plan_tracks_assignment_lane(self) -> None:
        html_source = Path("docs/qbr_plan.html").read_text(encoding="utf-8")
        required_terms = [
            "Ticket assignment UI demo",
            "/assignment",
            "UTC+8",
            "I'm ready to roll",
            "I'm ready for the next case",
            "read-only",
            "/assignment/admin",
            "Daily shift 卡片已支持直接修改 Start / End 并保存",
            "Save shift",
            "Engineer overview",
            "weekly schedule",
            "on shift now",
            "Online",
            "admin-only shift",
            "weekly schedule grid",
            "engineer/day picker",
            "overnight shift 跨到次日显示",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)

    def test_qbr_plan_tracks_billing_lane_with_canonical_ticket_language(self) -> None:
        html_source = Path("docs/qbr_plan.html").read_text(encoding="utf-8")
        required_terms = [
            "Billing intake / ticket routing",
            "canonical `TK-...` ticket id",
            "account-intake-v2",
            "automation_status = automation",
            "不调用 follow process",
            "不发送内部邮件",
            "不再展示第二个 Support ticket ID",
            "`support_billing_tickets` companion table",
            "`source` 统一为 `manual` / `api`",
            "Recent tickets",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)
        self.assertNotIn("Billing ticket routing", html_source)

    def test_qbr_plan_tracks_rag_vs_kg_lane(self) -> None:
        html_source = Path("docs/qbr_plan.html").read_text(encoding="utf-8")
        required_terms = [
            "RAG vs KG / Case Memory",
            "Case Memory Ledger",
            "Active Retrieval Memory",
            "text-first product-form pipeline",
            "SupportPortal adapter",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)


if __name__ == "__main__":
    unittest.main()
