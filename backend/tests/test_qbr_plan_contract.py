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
            "revise 携带 engineer feedback、上一轮 evidence 和 review problem statement 回到 Plan Agent",
            "revise replan 链路已完成接入",
            "Review Agent 第一版已实现 deterministic review decision",
            "Guardrail final approve 已通过两段 approve 机制接管 runtime",
            "第二次 final approve 才发送客户回复并关闭工单",
            "blocked 时保留 revise 出口",
            "close-memory 已完成",
            "不自动晋升 active memory",
            "engineer_case_closed_after_customer_reply",
            "replay eval dataset",
            "replay eval dataset 已完成",
            "replay runner / metrics dashboard / regression gate",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)
        self.assertNotIn("当前下一步：实现 Summary Agent", html_source)
        self.assertNotIn("当前下一步：实现真实 Plan Agent", html_source)
        self.assertNotIn("当前下一步是实现 Plan Agent", html_source)
        self.assertNotIn("当前下一步是实现 Execute Agent", html_source)
        self.assertNotIn("当前下一步是实现 Review Agent", html_source)
        self.assertNotIn("当前下一步是实现 revise/approve 链路", html_source)
        self.assertNotIn("Summary/Plan/Execute/Review 真实链路仍未接管 runtime", html_source)
        self.assertNotIn("Execute/Review 尚未接管", html_source)
        self.assertNotIn("不是 Engineer AI 已经完成 guardrail final approve", html_source)
        self.assertNotIn("当前下一步是推进 close-memory", html_source)
        self.assertNotIn("当前下一步：实现 close-memory", html_source)
        self.assertNotIn("下一步是实现 close-memory", html_source)
        self.assertNotIn("当前下一步是推进 eval/metrics", html_source)

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
            "semantic-first routing + full automation execution",
            "字段追问、客户回复和内部邮件",
            "automation 会追问缺失字段、生成客户回复",
            "字段完整时尝试内部邮件流转",
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
            "TK-ACC-C31612",
            "billing.account_verification",
            "account_verification",
            "semantic-first",
            "weak gratitude",
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

    def test_qbr_plan_balances_lane_columns_and_removes_suggested_route(self) -> None:
        html_source = Path("docs/qbr_plan.html").read_text(encoding="utf-8")
        required_terms = [
            "grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);",
            ".panel {",
            "min-width: 0;",
            "overflow-wrap: anywhere;",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)
        self.assertNotIn("建议 QBR 路线", html_source)
        self.assertNotIn('aria-label="Suggested QBR route"', html_source)

    def test_qbr_plan_renders_dynamic_mermaid_diagrams_with_fallback(self) -> None:
        html_source = Path("docs/qbr_plan.html").read_text(encoding="utf-8")
        required_terms = [
            "renderMermaidDiagrams",
            "renderStaticMermaidFallback",
            "window.mermaid.run",
            'onload="renderMermaidDiagrams()"',
            'onerror="renderStaticMermaidFallback()"',
            'class="flow-diagram"',
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)

    def test_qbr_plan_renders_architecture_full_width_below_lane_columns(self) -> None:
        html_source = Path("docs/qbr_plan.html").read_text(encoding="utf-8")
        render_lane_start = html_source.index("function renderLane(lane, index)")
        render_lane_end = html_source.index("function renderTask(task)", render_lane_start)
        render_lane_source = html_source[render_lane_start:render_lane_end]

        self.assertIn('class="lane-followup"', render_lane_source)
        self.assertLess(
            render_lane_source.index('class="lane-body"'),
            render_lane_source.index('class="lane-followup"'),
        )

        first_panel_source = render_lane_source[
            render_lane_source.index('<section class="panel">') : render_lane_source.index('<section class="panel">', render_lane_source.index('<section class="panel">') + 1)
        ]
        self.assertNotIn("renderLaneArchitecture(lane.architecture)", first_panel_source)
        self.assertIn("renderLaneArchitecture(lane.architecture)", render_lane_source)
        self.assertLess(
            render_lane_source.index("renderLaneArchitecture(lane.architecture)"),
            render_lane_source.index('class="notes"'),
        )


if __name__ == "__main__":
    unittest.main()
