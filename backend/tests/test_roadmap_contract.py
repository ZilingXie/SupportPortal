from __future__ import annotations

from pathlib import Path
import os
import unittest


os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

ROADMAP_PATH = Path("docs/roadmap.html")
PHASE1_PATH = Path("docs/roadmap/phase1.html")
QBR_COMPAT_PATH = Path("docs/qbr_plan.html")


class RoadmapContractTests(unittest.TestCase):
    def test_phase1_talk_track_page_is_linked_from_roadmap(self) -> None:
        html_source = ROADMAP_PATH.read_text(encoding="utf-8")
        phase1_source = PHASE1_PATH.read_text(encoding="utf-8")

        self.assertIn('href="./roadmap/phase1.html"', html_source)
        self.assertIn("打开 Phase 1 演示讲稿", html_source)

        required_terms = [
            "SupportPortal Phase 1",
            "从 Zendesk 平替，走向 AI-native Support System",
            "AgentRelay 先组织拿证据，AI 再拒绝不完整回复",
            "AI guardrail pass rate",
            "AgentRelay 让 SupportPortal 从单 Agent 工作台",
            "thread reuse",
            "completion ownership",
            "reply.delivered",
            "Image needed · AgentRelay task trace",
            "批注：演示时这么说",
            "批注：准备建议",
            "批注：图片建议",
            "完整演讲稿初稿（约 3 分钟）",
            "各 section 里的蓝色批注是同一份稿的拆分版",
            "Zendesk license 续费大约 73k 美金一年",
            "AgentRelay communication foundation 已完成，但 SupportPortal 的真实 domain-agent 调查还在 Phase 2",
            "font-size: clamp(34px, 5.8vw, 72px);",
            "自主检测 + 行为规范 + A2A foundation",
            "辅助调查 + 人 approve",
            "自主调查 + governed Agent-to-Agent",
            'href="../roadmap.html"',
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, phase1_source)

    def test_roadmap_static_routes_serve_public_pages(self) -> None:
        from fastapi.testclient import TestClient

        import backend.main as main
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        main.ticket_repository = InMemoryTicketRepository()
        main.ticket_repository.initialize()
        client = TestClient(main.app)
        try:
            checks = [
                ("/roadmap.html", "整体落地优化计划"),
                ("/roadmap/phase1.html", "SupportPortal Phase 1"),
            ]
            for path, marker in checks:
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("text/html", response.headers["content-type"])
                    self.assertIn(marker, response.text)
        finally:
            client.close()

    def test_roadmap_is_new_primary_entry_and_qbr_redirects(self) -> None:
        html_source = ROADMAP_PATH.read_text(encoding="utf-8")
        compat_source = QBR_COMPAT_PATH.read_text(encoding="utf-8")

        for term in [
            "SupportPortal Roadmap",
            "整体落地优化计划",
            "supportportal_roadmap_v1",
            "文件：docs/roadmap.html",
        ]:
            with self.subTest(term=term):
                self.assertIn(term, html_source)

        self.assertIn("roadmap.html", compat_source)
        self.assertIn("Roadmap 是新的维护入口", compat_source)
        self.assertNotIn("const LANES", compat_source)

    def test_roadmap_top_layout_is_compact_and_bullet_based(self) -> None:
        html_source = ROADMAP_PATH.read_text(encoding="utf-8")
        required_terms = [
            "font-size: clamp(30px, 4vw, 48px);",
            "min-height: 320px;",
            'class="hero-bullets"',
            "项目定位",
            "当前阶段",
            "Phase 1/2/3 门禁",
            "近期 POC",
            "总计划追踪",
            "Phase 1：效率提升 + 工单系统雏形",
            "Phase 2：AI 主动处理 + 人 approve",
            "AgentRelay communication foundation 已完成",
            "Phase 3：governed agent-to-agent + 高自动化",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)

        self.assertNotIn('class="hero-meta"', html_source)
        self.assertNotIn('class="meta-pill"', html_source)
        self.assertNotIn("同步点：", html_source)
        self.assertNotIn("来源：docs + tests", html_source)
        self.assertNotIn("四条优化主线，一张可追踪进度板", html_source)

    def test_meeting_minutes_are_a_filterable_lane(self) -> None:
        html_source = ROADMAP_PATH.read_text(encoding="utf-8")
        required_terms = [
            'id: "meeting-minutes"',
            'short: "Meeting"',
            "AI Agent 工单系统落地对齐会",
            "2026-06-18",
            "derek, zac, alex, emma",
            "会议纪要卡片",
            "meeting_minutes",
            "meeting-minutes-grid",
            "meeting-minutes-card",
            "AI review 人",
            "人 review AI",
            "WeCom 不适合作审计 source of truth",
            "billing route 验证",
            "renderMeetingMinutes",
            "renderMeetingLane",
            "下一步计划",
            'title: "下一步计划"',
            "最快不能超过 5 分钟",
            "AI 只检查 conclusion / proof / next step",
            "invoice / account fraud / deactivate",
            "Zendesk webhook / N8n",
            "shadow mode",
            "1% 切流",
            "AgentRelay communication foundation",
            "governed agent-to-agent 自主调查",
            "token 成本",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)

        self.assertNotIn('class="meeting-section"', html_source)
        self.assertNotIn("const MEETING_TRACKS", html_source)
        self.assertLess(html_source.index('id: "meeting-minutes"'), html_source.index('id: "engineer-multi-agent"'))
        meeting_lane_source = html_source[
            html_source.index('id: "meeting-minutes"') : html_source.index('id: "engineer-multi-agent"')
        ]
        for removed_term in [
            "done: [",
            "review: [",
            "sources: [",
            "next: [",
            "lane.next.map(renderTask)",
            "task-board",
            "已完成进展",
            "未完成计划",
            "证据来源",
            "Review 结论",
        ]:
            with self.subTest(removed_term=removed_term):
                self.assertNotIn(removed_term, meeting_lane_source)

    def test_billing_and_route_plans_reflect_meeting_next_steps(self) -> None:
        html_source = ROADMAP_PATH.read_text(encoding="utf-8")
        required_terms = [
            "Phase 1 只接 Zendesk 转发/内部中转，不迁移客户入口",
            "AI account",
            "invoice request",
            "account fraud / deactivate / company verification",
            "邮件回执/邮箱轮询",
            "engineer reply 通过/拒绝",
            "route_accuracy",
            "automation_coverage",
            "not_automated_reason",
            "response_latency",
            "real Zendesk replay set",
            "billing risky negative set",
            "fully_automated",
            "ai_draft_human_approve",
            "unable_to_resolve_handoff",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)

    def test_existing_product_lanes_and_architecture_still_render(self) -> None:
        html_source = ROADMAP_PATH.read_text(encoding="utf-8")
        required_terms = [
            "Engineer AI multi-agent",
            "Ticket assignment UI demo",
            "Billing intake / ticket routing",
            "Routing rules",
            "RAG vs KG / Case Memory",
            "renderMermaidDiagrams",
            "renderStaticMermaidFallback",
            "renderLaneArchitecture(lane.architecture)",
            "replay runner / metrics dashboard / regression gate",
            "billing.account_verification",
            "SupportPortal adapter",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)

    def test_agent_rules_require_roadmap_updates_for_major_changes(self) -> None:
        agents = Path("AGENTS.md").read_text(encoding="utf-8")
        claude = Path("CLAUDE.md").read_text(encoding="utf-8")
        review_skill = Path(".codex/skills/review-implemented-plan/SKILL.md").read_text(encoding="utf-8")
        prompt_log = Path("docs/prompt_change_log.md").read_text(encoding="utf-8")

        for source_name, source in [("AGENTS", agents), ("CLAUDE", claude)]:
            with self.subTest(source=source_name):
                self.assertIn("docs/roadmap.html", source)
                self.assertIn("功能类/重大行为变更", source)
                self.assertIn("整体落地进度", source)

        self.assertIn("docs/roadmap.html", review_skill)
        self.assertIn("功能类/重大行为变更", review_skill)
        self.assertIn("已同步或明确不需要同步", review_skill)

        self.assertIn("roadmap-maintenance-rule-v1", prompt_log)
        self.assertIn("docs/roadmap.html", prompt_log)


if __name__ == "__main__":
    unittest.main()
