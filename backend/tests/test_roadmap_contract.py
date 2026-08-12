from __future__ import annotations

from pathlib import Path
import os
import re
import unittest


os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

ROADMAP_PATH = Path("docs/roadmap.html")
MEETINGS_PATH = Path("docs/roadmap/meetings.html")
PHASE1_PATH = Path("docs/roadmap/phase1.html")
PHASE2_PATH = Path("docs/roadmap/phase2.html")
PHASE3_PATH = Path("docs/roadmap/phase3.html")
PHASE1_VIDEO_DIR = Path("docs/roadmap/phase1_video")
PHASE1_VIDEO_SCRIPT_PATH = PHASE1_VIDEO_DIR / "video_script.md"
PHASE1_VIDEO_SCRIPT_CN_PATH = PHASE1_VIDEO_DIR / "video_script_cn.md"
PHASE1_STORYBOARD_PATH = PHASE1_VIDEO_DIR / "storyboard.md"
PHASE1_VOICEOVER_PATH = PHASE1_VIDEO_DIR / "voiceover-jianying.txt"
QBR_COMPAT_PATH = Path("docs/qbr_plan.html")


class RoadmapContractTests(unittest.TestCase):
    def test_phase2_delivery_page_is_linked_and_categorizes_endpoints(self) -> None:
        roadmap_source = ROADMAP_PATH.read_text(encoding="utf-8")
        phase2_source = PHASE2_PATH.read_text(encoding="utf-8")

        self.assertIn('href="./roadmap/phase2.html"', roadmap_source)
        self.assertIn("打开 Phase 2 交付记录", roadmap_source)
        for term in (
            "Phase 2 Delivery Record",
            "Phase 2 改动",
            "功能与 endpoint 分类",
            "Access &amp; RBAC",
            "Assignment",
            "Billing",
            "Reliability",
            "Automation Scope",
            "Reply Quality",
            "Validation &amp; Metrics",
            "Fraud 字段规则",
            "Suhird",
            "限制重复追问",
            "随机延迟 6–10 分钟",
            "不同人设 Prompt 模板",
            "Account &amp; Billing 与 Backend Operation",
            "自动化覆盖率",
            "Automated Cases Dashboard",
            "Route execution + Prompt snapshot",
            "Persona Prompt registry 与版本管理",
            "Environment Config names-only inventory",
            "Zendesk ticket ID 作为 canonical Ticket ID",
            "Zendesk source URL 中的 ticket number",
            "列表与详情统一展示来源 ticket #",
            "Agent Config 与 Automation Workflow 管理面",
            "21 项",
            'class="delivery-group" data-status-group="incomplete" open',
            'class="delivery-group" data-status-group="complete"',
            "Active UI",
            "Workspace auth",
            "Engineer backend",
            "Manual claim",
            "Legacy UI",
            "/workspace/admin",
            "/api/workspace/admin/metrics",
            "/api/engineer/*",
            "/api/engineer/tickets/{id}/claim",
            "PR #609",
            "PR #610",
            "PR #611",
            "PR #612",
            "PR #649",
            "PR #651",
            "./phase2/workspace-admin.jpg",
        ):
            with self.subTest(term=term):
                self.assertIn(term, phase2_source)

        delivery_rows = re.findall(
            r'<article class="delivery-row".*?</article>', phase2_source, flags=re.DOTALL
        )
        endpoint_body = re.search(
            r'<tbody id="endpointRows">(.*?)</tbody>', phase2_source, flags=re.DOTALL
        )
        self.assertGreaterEqual(len(delivery_rows), 10)
        self.assertTrue(all('data-status="' in row for row in delivery_rows))
        complete_rows = [row for row in delivery_rows if 'data-status="done"' in row]
        incomplete_rows = [row for row in delivery_rows if 'data-status="done"' not in row]
        self.assertTrue(complete_rows)
        self.assertTrue(incomplete_rows)
        self.assertTrue(all('class="pr-link"' in row for row in complete_rows))
        self.assertTrue(all('status-chip' in row for row in incomplete_rows))
        self.assertIsNotNone(endpoint_body)
        assert endpoint_body is not None
        endpoint_rows = re.findall(r"<tr>.*?</tr>", endpoint_body.group(1), flags=re.DOTALL)
        self.assertGreaterEqual(len(endpoint_rows), 15)
        self.assertTrue(all('class="pr-link"' in row for row in endpoint_rows))

    def test_phase3_plan_page_is_linked_and_tracks_slack_workflow(self) -> None:
        roadmap_source = ROADMAP_PATH.read_text(encoding="utf-8")
        phase3_source = PHASE3_PATH.read_text(encoding="utf-8")

        self.assertIn('href="./roadmap/phase3.html"', roadmap_source)
        for term in (
            "Phase 3 Plan",
            "AI First Response + Slack Engineer Workflow",
            "Zendesk Intake &amp; Eligibility",
            "AI First Response",
            "Slack Engineer Workflow",
            "Assignment &amp; Admin",
            "客户从 Zendesk 创建 ticket",
            "大客户、明显生气或高风险客户",
            "AI 只负责首次有效回复",
            "之后所有客户回复经过 Guardrail",
            "在 Slack 中分配 case",
            "只有 Admin 可以 reassign case",
            "全局 Round Robin 平均分配",
            "Admin Dashboard",
            "Automated Cases、Route &amp; Prompt、Persona Prompt Template 和 Environment Config",
            "Slack bot 权限与承载模型",
            'class="delivery-group" data-status-group="incomplete" open',
            'class="delivery-group" data-status-group="complete"',
        ):
            with self.subTest(term=term):
                self.assertIn(term, phase3_source)

        plan_rows = re.findall(
            r'<article class="delivery-row".*?</article>', phase3_source, flags=re.DOTALL
        )
        self.assertGreaterEqual(len(plan_rows), 8)
        self.assertTrue(all('data-status="' in row for row in plan_rows))
        self.assertTrue(any('data-status="done"' in row for row in plan_rows))
        self.assertTrue(any('data-status="doing"' in row for row in plan_rows))
        self.assertTrue(any('data-status="todo"' in row for row in plan_rows))

    def test_phase_tracker_uses_new_phase_boundaries(self) -> None:
        html_source = ROADMAP_PATH.read_text(encoding="utf-8")

        for term in (
            "Phase 2：确定性 Automation + Controlled Validation",
            "Phase 3：AI First Response + Slack Engineer Workflow",
            "后续长期计划：Engineer multi-agent + governed agent-to-agent",
            'href="./roadmap/phase3.html"',
        ):
            with self.subTest(term=term):
                self.assertIn(term, html_source)

        self.assertNotIn("Phase 3：Engineer multi-agent + governed agent-to-agent", html_source)

    def test_phase1_talk_track_page_is_linked_from_roadmap(self) -> None:
        html_source = ROADMAP_PATH.read_text(encoding="utf-8")
        phase1_source = PHASE1_PATH.read_text(encoding="utf-8")

        self.assertIn('href="./roadmap/phase1.html"', html_source)
        self.assertIn("打开 Phase 1 演示讲稿", html_source)

        required_terms = [
            "SupportPortal Phase 1",
            "From Zendesk replacement to an AI-native Support System",
            "How do we ensure support reply quality before the customer sees the answer?",
            "AI guardrail pass rate",
            "00 · Script + video kit",
            "Expand script and video kit",
            "Collapse script and video kit",
            "Collapsed by default",
            "Full advertising-style video script, about 3 minutes",
            "customer UI stays the same",
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
            "bad-case-support-failure-demo.mp4",
            "I encountered black screen during my stream, my audience cannot seem my face. What should I do!",
            "Your phone's camera might be broken.",
            "How do you know? It was working 5 minutes ago!",
            "We will further investigate the issue.",
            "Please give me a workaround as soon as possible as the stream is ongoing!",
            "traditional support pain point",
            "we only discover the damage through after-the-fact review",
            'aria-label="Play bad case demo"',
            'data-bad-case-step="customer-issue"',
            'data-bad-case-step="delayed-investigation"',
            'data-bad-case-progress="urgent-workaround"',
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
            "Open /workspace",
            'href="/workspace"',
            "AI account automation",
            "Open /account",
            'href="/account"',
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
            "Productize and protocolize AgentRelay",
            "Each agent can represent a person or a team",
            "cross-environment agent collaboration",
            "minimal configuration",
            "font-size: clamp(15px, 2.4vw, 30px);",
            "font-size: clamp(14px, 2.1vw, 27px);",
            "SupportPortal Phase 1 is a POC for an AI-native support operating system",
            "connects Zendesk forwarding, routing, assignment, AI guardrail, final approval, dashboard, case replay, and AgentRelay communication foundation",
            "Self-check + behavior policy + A2A foundation",
            "Assisted investigation + human approval",
            "Autonomous investigation + governed Agent-to-Agent",
            'href="../roadmap.html"',
            "3-minute video kit",
            'href="./phase1_video/video_script.md"',
            'href="./phase1_video/video_script_cn.md"',
            'href="./phase1_video/storyboard.md"',
            'href="./phase1_video/voiceover-jianying.txt"',
            'href="./phase1_video/bad-case-support-failure-demo.mp4"',
            'href="./phase1_video/showcase-guardrail-demo.mp4"',
            'href="./phase1_video/"',
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, phase1_source)

        ordered_terms = [
            "01 · Reply quality failure",
            "02 · Why now",
            "03 · AI-native system",
            "04 · Guardrail showcase",
            "05 · Dashboard",
            "06 · Account automation",
            "07 · R&amp;D Agent investigation",
            "08 · AgentRelay task network",
            "09 · Roadmap",
        ]
        ordered_positions = [phase1_source.index(term) for term in ordered_terms]
        self.assertEqual(ordered_positions, sorted(ordered_positions))

        removed_terms = [
            "00B · Video kit",
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
        script_cn = PHASE1_VIDEO_SCRIPT_CN_PATH.read_text(encoding="utf-8")
        storyboard = PHASE1_STORYBOARD_PATH.read_text(encoding="utf-8")
        voiceover = PHASE1_VOICEOVER_PATH.read_text(encoding="utf-8")

        for term in [
            "# SupportPortal Phase 1 3-minute video script",
            "Paste this script directly into Jianying",
            "How do we ensure support reply quality before the customer sees the answer?",
            "reply quality",
            "manager visibility",
            "late replies",
            "limited customization space",
            "AI-native ticket system",
            "The customer entry point stays unchanged",
            "The customer UI stays unchanged",
            "assignment workspace",
            "assignment admin page",
            "AI Guardrail rejects",
            "AI account automation",
            "AgentRelay",
            "productize and protocolize AgentRelay",
            "cross-environment agent collaboration",
            "minimal configuration",
            "Client.unpublish",
            "AI Guardrail",
            "first-contact resolution",
        ]:
            with self.subTest(script_term=term):
                self.assertIn(term, script)

        self.assertLess(len(script), 3000)
        self.assertIsNone(
            __import__("re").search(r"[\u4e00-\u9fff]", script),
            msg="video_script.md should be English-only and pasteable into Jianying.",
        )
        for non_script_term in [
            "00:00-00:10",
            "phase1_video/",
            "|",
            "剪映",
            "素材",
        ]:
            with self.subTest(non_script_term=non_script_term):
                self.assertNotIn(non_script_term, script)

        for term in [
            "# SupportPortal Phase 1 视频分镜说明",
            "用于你自己看，不建议直接粘到剪映",
            "第 01 幕",
            "第 19 幕",
            "00:00-00:05",
            "02:55-03:00",
            "时长",
            "素材",
            "剪映操作",
            "phase1_video/1-intro.jpeg",
            "phase1_video/bad-case-support-failure-demo.mp4",
            "phase1_video/showcase-guardrail-demo.mp4",
            "phase1_video/12-dashboard.png",
            "support.stellarix.space/workspace",
            "support.stellarix.space/workspace/admin",
            "support.stellarix.space/account",
            "/workspace/admin",
            "/account",
            "[websdk] no input frame received",
            "Client.unpublish",
        ]:
            with self.subTest(script_cn_term=term):
                self.assertIn(term, script_cn)

        for term in [
            "# SupportPortal Phase 1 3-minute video storyboard",
            "phase1_video/1-intro.jpeg",
            "phase1_video/bad-case-support-failure-demo.mp4",
            "phase1_video/2-why-now.png",
            "phase1_video/3-big-pic.png",
            "phase1_video/4-admin.png",
            "phase1_video/5-agent-relay.png",
            "Client.unpublish",
            "abnormal-disconnection exclusion",
            "Guardrail showcase dialogue",
            "phase1_video/7-show-case.png",
            "/account",
            "AI account automation",
            "phase1_video/11-phase1-closing.png",
            "phase1_video/12-dashboard.png",
        ]:
            with self.subTest(storyboard_term=term):
                self.assertIn(term, storyboard)

        for term in [
            "How do we ensure support reply quality before the customer sees the answer?",
            "That is the traditional support trap",
            "reply quality",
            "manager visibility",
            "after-the-fact review",
            "late replies",
            "Zendesk license renewal is about seventy-three thousand dollars a year",
            "customer entry point stays unchanged",
            "customer experience also stays unchanged",
            "SupportPortal is our AI-native ticket system",
            "The customer UI stays unchanged",
            "Assignment is the Phase 1 dispatch control plane",
            "Open the assignment workspace",
            "Agents are not loosely chatting with each other",
            "many personal agents do not have public IP addresses",
            "AI account automation",
            "Client dot unpublish call",
            "not an abnormal disconnection",
            "AI Guardrail rejects an incomplete reply",
            "conservative customer draft",
            "AI guardrail pass rate",
            "productize and protocolize AgentRelay",
            "Each agent can represent a person or a team",
            "cross-environment agent collaboration",
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
        bad_case_animation = PHASE1_VIDEO_DIR / "bad-case-support-failure-demo.mp4"
        self.assertTrue(bad_case_animation.exists())
        self.assertGreater(bad_case_animation.stat().st_size, 100 * 1024)

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
                ("/roadmap/meetings.html", "SupportPortal Meetings"),
                ("/roadmap/phase1.html", "SupportPortal Phase 1"),
                ("/roadmap/phase2.html", "Phase 2 Delivery Record"),
                ("/roadmap/phase3.html", "Phase 3 Plan"),
                ("/roadmap/phase2/workspace-admin.jpg", "JPEG"),
                ("/roadmap/phase1_video/video_script.md", "SupportPortal Phase 1 3-minute video script"),
                ("/roadmap/phase1_video/video_script_cn.md", "SupportPortal Phase 1 视频分镜说明"),
                ("/roadmap/phase1_video/storyboard.md", "SupportPortal Phase 1 3-minute video storyboard"),
                ("/roadmap/phase1_video/voiceover-jianying.txt", "How do we ensure support reply quality before the customer sees the answer?"),
                ("/roadmap/phase1_video/bad-case-support-failure-demo.mp4", ""),
                ("/roadmap/phase1_video/2-why-now.png", "PNG"),
            ]
            for path, marker in checks:
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    if path.endswith(".png"):
                        self.assertIn("image/png", response.headers["content-type"])
                        self.assertTrue(response.content.startswith(b"\x89PNG"))
                    elif path.endswith(".jpg"):
                        self.assertIn("image/jpeg", response.headers["content-type"])
                        self.assertTrue(response.content.startswith(b"\xff\xd8\xff"))
                    else:
                        self.assertIn(marker, response.text)
        finally:
            client.close()

    def test_meetings_page_static_route_serves_new_archive(self) -> None:
        from fastapi.testclient import TestClient

        import backend.main as main
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        main.ticket_repository = InMemoryTicketRepository()
        main.ticket_repository.initialize()
        client = TestClient(main.app)
        try:
            response = client.get("/roadmap/meetings.html")
            self.assertEqual(response.status_code, 200)
            self.assertIn("SupportPortal Meetings", response.text)
            self.assertIn("ticketing-system-2026-08-10", response.text)
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
            "Controlled Validation",
            "Phase 3 建立 Slack 工程师流程",
            "总计划追踪",
            "Phase 1：效率提升 + 工单系统雏形",
            "Phase 2：确定性 Automation + Controlled Validation",
            "AgentRelay communication foundation 已完成",
            "Phase 3：AI First Response + Slack Engineer Workflow",
            "后续长期计划：Engineer multi-agent + governed agent-to-agent",
            "会议第一阶段对应实际 Phase 2",
            "全局 Round Robin 平均分配",
            "AI 只做首次有效回复",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)

        self.assertNotIn('class="hero-meta"', html_source)
        self.assertNotIn('class="meta-pill"', html_source)
        self.assertNotIn("同步点：", html_source)
        self.assertNotIn("来源：docs + tests", html_source)
        self.assertNotIn("四条优化主线，一张可追踪进度板", html_source)

    def test_meetings_page_contains_migrated_and_new_records(self) -> None:
        roadmap_source = ROADMAP_PATH.read_text(encoding="utf-8")
        meetings_source = MEETINGS_PATH.read_text(encoding="utf-8")
        required_terms = [
            "SupportPortal Meetings",
            "const MEETINGS",
            "ticketing-system-2026-08-10",
            "agent-system-2026-06-18",
            "2026-08-10",
            "2026-06-18",
            "重点 Topics",
            "Work Items",
            "Ticketing System 第一阶段对齐会",
            "AI Agent 工单系统落地对齐会",
            '"zac", "jojo", "suhird", "bdr", "emma", "derek"',
            '"derek", "zac", "alex", "emma"',
            "AI 回复仍保存在内部系统中",
            "尚未真实写回 Zendesk",
            "Support Package、大客户和指定 CID",
            "route_accuracy",
            "fully_automated",
            "ai_draft_human_approve",
            "unable_to_resolve_handoff",
            "AI review 人",
            "人 review AI",
            "第三方聊天工具不作为审计 source of truth",
            "billing route 验证",
            "下一步计划",
            "最快不能超过 5 分钟",
            "AI 只检查 conclusion / proof / next step",
            "invoice / account suspension / company verification",
            "Zendesk 转发和内部中转",
            "每第 10 单创建 Engineer Case",
            "AgentRelay communication foundation",
            "governed agent-to-agent 自主调查",
            "token 成本",
            'id: "TS-01"',
            'id: "TS-11"',
            'id: "AG-01"',
            'id: "AG-06"',
            'prs: [675, 676, 680, 683, 685, 686, 687, 702, 709, 719, 731]',
            'prs: [729, 731]',
            "https://developers.openai.com/api/docs/guides/your-data",
            "https://developers.openai.com/api/docs/guides/your-data#zero-data-retention",
            "OpenAI docs",
            'const DEFAULT_MEETING_ID = "ticketing-system-2026-08-10"',
            "hashchange",
            "popstate",
            "window.history.pushState",
            'class="work-prs"',
            '<th scope="col">PR / Docs</th>',
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, meetings_source)

        self.assertIn('href="./roadmap/meetings.html"', roadmap_source)
        self.assertNotIn('id: "meeting-minutes"', roadmap_source)
        work_item_ids = re.findall(r'\{ id: "((?:TS|AG)-\d+)"', meetings_source)
        self.assertEqual(len(work_item_ids), len(set(work_item_ids)))
        self.assertEqual(work_item_ids[:11], [f"TS-{index:02d}" for index in range(1, 12)])
        self.assertEqual(work_item_ids[11:], [f"AG-{index:02d}" for index in range(1, 7)])
        completed_lines = [line for line in meetings_source.splitlines() if 'status: "done"' in line]
        self.assertEqual(len(completed_lines), 2)
        self.assertTrue(all(re.search(r'prs: \[[0-9, ]+\]', line) for line in completed_lines))
        self.assertNotIn('status: "done", prs: []', meetings_source)
        for removed_term in [
            "meeting_minutes",
            "renderMeetingMinutes",
            "renderMeetingLane",
            "meeting-minutes-grid",
            "meeting-minutes-card",
            "meeting-lane-body",
        ]:
            with self.subTest(removed_term=removed_term):
                self.assertNotIn(removed_term, roadmap_source)

    def test_billing_and_route_plans_reflect_meeting_next_steps(self) -> None:
        html_source = ROADMAP_PATH.read_text(encoding="utf-8")
        meetings_source = MEETINGS_PATH.read_text(encoding="utf-8")
        combined_source = html_source + meetings_source
        required_terms = [
            "Phase 1 只接 Zendesk 转发/内部中转，不迁移客户入口",
            "Not automated case",
            "invoice request",
            "account suspension / company verification",
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
                self.assertIn(term, combined_source)

    def test_account_roadmap_uses_v7_domain_ownership_and_persona_rerun_contract(self) -> None:
        roadmap_source = ROADMAP_PATH.read_text(encoding="utf-8")
        phase2_source = PHASE2_PATH.read_text(encoding="utf-8")
        feature_source = Path("docs/feature_list.md").read_text(encoding="utf-8")

        for term in (
            "Account & Billing 拥有 account_suspension、fraud_account、detailed_invoice、other",
            "Backend Operation 拥有 enablement、quota、unregistered",
            "registered automated outcome",
            "clear Persona assignment; route again",
            "一次性 live data operation 仍需部署后由操作员明确启动",
        ):
            with self.subTest(term=term):
                self.assertIn(term, roadmap_source)
        self.assertIn(
            "Intent Classifier、Agora Router、Account &amp; Billing Router、Backend Operation Router",
            phase2_source,
        )
        self.assertNotIn("最新三层分类", feature_source)
        self.assertNotIn("自动化结果统一输出 Automation category", roadmap_source)
        self.assertNotIn("由 Automation subcategory 选择 Billing handler", roadmap_source)

    def test_existing_product_lanes_and_architecture_still_render(self) -> None:
        html_source = ROADMAP_PATH.read_text(encoding="utf-8")
        required_terms = [
            "Engineer AI multi-agent",
            "Engineer Case assignment + Workspace",
            "Automated Case intake / routing",
            "Routing rules",
            "RAG vs KG / Case Memory",
            "renderMermaidDiagrams",
            "renderStaticMermaidFallback",
            "renderLaneArchitecture(lane.architecture)",
            "replay runner / metrics dashboard / regression gate",
            "billing.account_verification",
            "SupportPortal adapter",
            "`/engineer` UI 是 legacy",
            "`/api/engineer/*` 仍是 active backend contract",
            "pending/assigned/resolved",
            "Outlook reply",
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
