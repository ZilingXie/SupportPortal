from __future__ import annotations

from pathlib import Path
import os
import unittest


os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

ROADMAP_PATH = Path("docs/roadmap.html")
PHASE1_PATH = Path("docs/roadmap/phase1.html")
PHASE1_VIDEO_DIR = Path("docs/roadmap/phase1_video")
PHASE1_VIDEO_SCRIPT_PATH = PHASE1_VIDEO_DIR / "video_script.md"
PHASE1_STORYBOARD_PATH = PHASE1_VIDEO_DIR / "storyboard.md"
PHASE1_VOICEOVER_PATH = PHASE1_VIDEO_DIR / "voiceover-jianying.txt"
QBR_COMPAT_PATH = Path("docs/qbr_plan.html")


class RoadmapContractTests(unittest.TestCase):
    def test_phase1_talk_track_page_is_linked_from_roadmap(self) -> None:
        html_source = ROADMAP_PATH.read_text(encoding="utf-8")
        phase1_source = PHASE1_PATH.read_text(encoding="utf-8")

        self.assertIn('href="./roadmap/phase1.html"', html_source)
        self.assertIn("打开 Phase 1 演示讲稿", html_source)

        required_terms = [
            "SupportPortal Phase 1",
            "From Zendesk replacement to an AI-native Support System",
            "AI guardrail pass rate",
            "Expand full talk track",
            "Collapsed by default",
            "Internally, Zendesk cases are forwarded into SupportPortal",
            "SupportPortal Phase 1 architecture diagram",
            "Customer / Zendesk",
            "SupportPortal Core",
            "Future Agent Network",
            "A2A communication path",
            "R&amp;D Agent investigation loop",
            "requester.closed",
            "Showcase: how AI Guardrail rejects an incomplete reply",
            "customer-safe boundary",
            "Bad case: traditional support quality failure",
            "I encountered black screen during my stream, my audience cannot see my face. What should I do!",
            "Your phone's camera might be broken.",
            "How do you know? It was working 5 minutes ago!",
            "traditional support pain point",
            "we only discover the damage through after-the-fact review",
            'aria-label="Play bad case demo"',
            'data-bad-case-step="customer-issue"',
            'data-bad-case-progress="angry-customer"',
            "Guardrail changes the control point",
            "Playable guardrail demo",
            'aria-label="Play demo"',
            'aria-label="Reset demo"',
            "Showcase progress steps",
            'data-progress-step="issue"',
            'data-progress-step="bad-draft"',
            'data-showcase-step="reject"',
            'data-showcase-step="evidence"',
            'data-showcase-step="draft"',
            "Rejected by AI Guardrail",
            "Ready for approval",
            "Black screen issue reported for channel zilingtest, uid 2",
            "The note &quot;the camera is broken&quot; is not yet usable for a customer reply",
            "[websdk] no input frame received",
            "Draft Customer Reply",
            "Thanks for your patience. We checked the Web SDK logs",
            "Closing: what Phase 1 must prove",
            "If Phase 1 works, support is not merely moving away from Zendesk",
            "agent interaction count",
            "first-contact resolution",
            "limited customization space",
            "feature extension, internal workflow customization, data mining",
            "Open /assignment/admin",
            'href="/assignment/admin"',
            "AgentRelay task network",
            "Why AgentRelay is needed",
            "Why not pure A2A protocol",
            "no public IP",
            "hand unresolved questions to R&D / Data / Log Agents for evidence",
            "R&amp;D Agent example",
            "Question: Please check channel 42175037575290",
            "Investigation complete (390.3s)",
            "Client.unpublish",
            "track-scr-v-754e053e",
            "C4E3D88760726EEEC8668FDE2B825157",
            "not an abnormal interruption",
            "AgentRelay communication foundation is ready, while real SupportPortal domain-agent investigation remains a Phase 2 integration",
            "font-size: clamp(30px, 4.8vw, 60px);",
            "SupportPortal Phase 1 is a POC for an AI-native support operating system",
            "connects Zendesk forwarding, routing, assignment, AI guardrail, final approval, dashboard, case replay, and AgentRelay communication foundation",
            "Self-check + behavior policy + A2A foundation",
            "Assisted investigation + human approval",
            "Autonomous investigation + governed Agent-to-Agent",
            'href="../roadmap.html"',
            "3-minute video kit",
            'href="./phase1_video/video_script.md"',
            'href="./phase1_video/storyboard.md"',
            'href="./phase1_video/voiceover-jianying.txt"',
            'href="./phase1_video/showcase-guardrail-demo.mp4"',
            'href="./phase1_video/"',
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, phase1_source)


        removed_terms = [
            "AgentRelay 先组织拿证据，AI 再拒绝不完整回复",
            "Image needed · AgentRelay task trace",
            "Image needed · Support case demo",
            "建议放截图或拼图：Support Agent create_task",
            'class="annotation-stack"',
            'class="annotation speaker"',
            'class="annotation prep"',
            "批注：演示时这么说",
            "批注：准备建议",
            "批注：图片建议",
            "./assets/rnd-agent-query.png",
            "./assets/rnd-agent-result.png",
            "./assets/rnd-agent-example.png",
            "./assets/engineer-guardrail-bad-draft.png",
            "./assets/engineer-guardrail-reject-reason.png",
            "./assets/engineer-guardrail-safe-draft.png",
            "c4158a3390426fdecc5e9fbbc2e81eb1",
            "Query completed (44.7s)",
        ]
        for term in removed_terms:
            with self.subTest(removed_term=term):
                self.assertNotIn(term, phase1_source)

        self.assertIsNone(
            __import__("re").search(r"[\u4e00-\u9fff]", phase1_source),
            msg="phase1.html should be fully English after the leadership-demo translation.",
        )

    def test_phase1_video_package_contains_script_storyboard_voiceover_and_shots(self) -> None:
        script = PHASE1_VIDEO_SCRIPT_PATH.read_text(encoding="utf-8")
        storyboard = PHASE1_STORYBOARD_PATH.read_text(encoding="utf-8")
        voiceover = PHASE1_VOICEOVER_PATH.read_text(encoding="utf-8")

        for term in [
            "# SupportPortal Phase 1 3-minute video script",
            "00:00-00:15",
            "00:35-01:05",
            "01:05-01:25",
            "01:25-01:55",
            "02:20-02:45",
            "02:45-03:00",
            "Zendesk does not give us enough customization space",
            "/assignment/admin",
            "AgentRelay",
            "no public IP",
            "Client.unpublish",
            "[websdk] no input frame received",
            "AI Guardrail",
            "first-contact resolution",
        ]:
            with self.subTest(script_term=term):
                self.assertIn(term, script)

        for term in [
            "# SupportPortal Phase 1 3-minute video storyboard",
            "phase1_video/1-intro.jpeg",
            "phase1_video/2-why-now.png",
            "phase1_video/3-big-pic.png",
            "phase1_video/4-admin.png",
            "phase1_video/5-agent-relay.png",
            "Client.unpublish",
            "abnormal-disconnection exclusion",
            "Guardrail showcase dialogue",
            "phase1_video/7-show-case.png",
            "phase1_video/11-phase1-closing.png",
            "phase1_video/12-dashboard.png",
        ]:
            with self.subTest(storyboard_term=term):
                self.assertIn(term, storyboard)

        for term in [
            "Zendesk license renewal is about seventy-three thousand dollars a year",
            "customer entry point stays unchanged",
            "customer experience also stays unchanged",
            "Assignment is the Phase 1 dispatch control plane",
            "Agents are not loosely chatting with each other",
            "many personal agents do not have public IP addresses",
            "Client dot unpublish call",
            "not an abnormal disconnection",
            "AI Guardrail rejects an incomplete reply",
            "conservative customer draft",
            "AI guardrail pass rate",
        ]:
            with self.subTest(voiceover_term=term):
                self.assertIn(term, voiceover)

        for shot_name in [
            "1-intro.jpeg",
            "2-why-now.png",
            "3-big-pic.png",
            "4-admin.png",
            "5-agent-relay.png",
            "7-show-case.png",
            "11-phase1-closing.png",
            "12-dashboard.png",
        ]:
            shot = PHASE1_VIDEO_DIR / shot_name
            with self.subTest(shot=str(shot)):
                self.assertTrue(shot.exists())
                self.assertGreater(shot.stat().st_size, 1024)

        animation = PHASE1_VIDEO_DIR / "showcase-guardrail-demo.mp4"
        self.assertTrue(animation.exists())
        self.assertGreater(animation.stat().st_size, 100 * 1024)

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
                ("/roadmap/phase1_video/video_script.md", "SupportPortal Phase 1 3-minute video script"),
                ("/roadmap/phase1_video/storyboard.md", "SupportPortal Phase 1 3-minute video storyboard"),
                ("/roadmap/phase1_video/voiceover-jianying.txt", "Zendesk license renewal is about seventy-three thousand dollars a year"),
                ("/roadmap/phase1_video/2-why-now.png", "PNG"),
            ]
            for path, marker in checks:
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    if path.endswith(".png"):
                        self.assertIn("image/png", response.headers["content-type"])
                        self.assertTrue(response.content.startswith(b"\x89PNG"))
                    else:
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
