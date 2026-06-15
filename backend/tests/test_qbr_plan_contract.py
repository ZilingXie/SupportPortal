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
            "Execute Agent 第一版已实现 deterministic scheduler",
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
            "当前下一步是实现 revise/approve 链路",
            "Review Agent 第一版已实现 deterministic review decision",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)
        self.assertNotIn("当前下一步：实现 Summary Agent", html_source)
        self.assertNotIn("当前下一步：实现真实 Plan Agent", html_source)
        self.assertNotIn("当前下一步是实现 Plan Agent", html_source)
        self.assertNotIn("当前下一步是实现 Execute Agent", html_source)
        self.assertNotIn("当前下一步是实现 Review Agent", html_source)

    def test_qbr_plan_collapses_engineer_architecture_by_default(self) -> None:
        html_source = Path("docs/qbr_plan.html").read_text(encoding="utf-8")
        required_terms = [
            'class="architecture-disclosure"',
            "查看 Engineer AI Agent 架构",
            "默认收起，点击后展开 Mermaid 生命周期图。",
            'class="architecture-diagram"',
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)
        self.assertLess(
            html_source.index('class="architecture-disclosure"'),
            html_source.index('class="architecture-diagram"'),
        )
        self.assertNotIn('<details class="architecture-disclosure" open', html_source)

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
            "hover 展开模式",
            "Break after this case",
            "Ready loading transition",
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

    def test_qbr_plan_tracks_routing_rules_lane(self) -> None:
        html_source = Path("docs/qbr_plan.html").read_text(encoding="utf-8")
        required_terms = [
            "Routing rules",
            "LLM semantic router",
            "policy gate",
            "Routing Architecture",
            "Semantic Intent",
            "Policy Gate",
            "Route Contract",
            "deterministic fast path",
            "LLM classifier",
            "conservative_agora_technical_fallback",
            "billing_automation",
            "billing_review",
            "account_suspension",
            "detailed_invoice",
            "agora_docs_rag",
            "web_company_info",
            "ticket_resolution",
            "controlled_response",
            "fallback_or_refuse",
            "INTENT_ROUTER_CONFIDENCE_THRESHOLD",
            "TK-ACC-68BAC7",
            "semantic_intent",
            "automation_eligibility",
            "not_automated_reason",
            "risk_flags",
            "evidence_spans",
            "router_source",
            "policy_decision",
            "billing.account_suspension",
            "billing.refund_or_dispute",
            "human_review_required",
            "_apply_policy_gate",
            "support_billing_tickets audit fields",
            "backend/services/support_router.py",
            "backend/services/support_router_prompt.py",
            "backend/services/billing_automation.py",
            "backend/sql/ticket_storage.sql",
            "backend/repositories/ticket_repository.py",
            "test_support_router_semantic_billing.py",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)

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
