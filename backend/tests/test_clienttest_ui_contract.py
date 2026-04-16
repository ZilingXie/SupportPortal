from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


class ClientTestRouteSmokeTests(unittest.TestCase):
    def test_clienttest_static_mount_and_entrypoints_exist(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        self.assertIn('CLIENTTEST_DIR = UI_DIR / "clienttest-ui"', main_source)
        self.assertIn(
            'app.mount("/clienttest", StaticFiles(directory=CLIENTTEST_DIR, html=True), name="clienttest-ui")',
            main_source,
        )

        expected_files = [
            Path("ui/clienttest-ui/index.html"),
            Path("ui/clienttest-ui/styles.css"),
            Path("ui/clienttest-ui/app.js"),
        ]
        for file_path in expected_files:
            self.assertTrue(file_path.exists(), str(file_path))

    def test_clienttest_html_references_local_assets(self) -> None:
        html = Path("ui/clienttest-ui/index.html").read_text(encoding="utf-8")

        self.assertIn("./styles.css?v=20260416-clienttest-reference-links-panel-1", html)
        self.assertIn("./app.js?v=20260416-clienttest-reference-links-panel-1", html)


class ClientTestUiContractTests(unittest.TestCase):
    def run_clienttest_app_script(self, script: str) -> None:
        node_script = textwrap.dedent(
            f"""
            (async () => {{
            const fs = require("fs");
            const vm = require("vm");
            const userScript = {script!r};

            let source = fs.readFileSync("ui/clienttest-ui/app.js", "utf8");
            source = source.replace(/\\nbootstrap\\(\\);\\s*$/, "\\n");

            const appRoot = {{
              innerHTML: "",
              querySelectorAll() {{ return []; }},
              querySelector() {{ return null; }},
            }};
            const toastRoot = {{
              appendChild() {{}},
            }};
            const storage = new Map();
            const sandbox = {{
              console,
              URL,
              crypto: {{
                _counter: 0,
                randomUUID() {{
                  this._counter += 1;
                  return `uuid-${{this._counter}}`;
                }},
              }},
              document: {{
                getElementById(id) {{
                  return id === "app" ? appRoot : toastRoot;
                }},
                createElement() {{
                  return {{
                    className: "",
                    textContent: "",
                    remove() {{}},
                  }};
                }},
              }},
              window: {{
                location: {{
                  hash: "",
                  protocol: "http:",
                  host: "localhost:8080",
                }},
                addEventListener() {{}},
              }},
              localStorage: {{
                getItem(key) {{
                  return storage.has(key) ? storage.get(key) : null;
                }},
                setItem(key, value) {{
                  storage.set(key, String(value));
                }},
                removeItem(key) {{
                  storage.delete(key);
                }},
              }},
              fetch: async () => ({{
                ok: true,
                json: async () => ({{ tickets: [] }}),
              }}),
              WebSocket: function WebSocket() {{
                this.readyState = 1;
                this.close = () => {{}};
                this.send = () => {{}};
              }},
              setTimeout() {{
                return 0;
              }},
              clearTimeout() {{}},
              setInterval() {{
                return 0;
              }},
              clearInterval() {{}},
            }};

            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(source, sandbox);
            await vm.runInContext(`(async () => {{\\n${{userScript}}\\n}})()`, sandbox);
            }})().catch((error) => {{
              console.error(error);
              process.exit(1);
            }});
            """
        )
        result = subprocess.run(
            ["node", "-e", node_script],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_clienttest_shell_uses_preview_branding_and_rail_labels(self) -> None:
        html = Path("ui/clienttest-ui/index.html").read_text(encoding="utf-8")
        app_source = Path("ui/clienttest-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/clienttest-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("<title>Support Portal - Client Preview</title>", html)
        self.assertIn("Support Portal", app_source)
        self.assertIn("Sid", app_source)
        self.assertIn('<span class="sidebar-nav-label">New Ticket</span>', app_source)
        self.assertIn('<span class="sidebar-nav-label">Workspace</span>', app_source)
        self.assertIn('<span class="sidebar-nav-label">My Tickets</span>', app_source)

        new_ticket_pos = app_source.index('<span class="sidebar-nav-label">New Ticket</span>')
        workspace_pos = app_source.index('<span class="sidebar-nav-label">Workspace</span>')
        my_tickets_pos = app_source.index('<span class="sidebar-nav-label">My Tickets</span>')
        self.assertLess(new_ticket_pos, workspace_pos)
        self.assertLess(workspace_pos, my_tickets_pos)

        self.assertIn("clienttest-shell", css)
        self.assertIn("clienttest-sidebar", css)
        self.assertIn("clienttest-main", css)

    def test_clienttest_ticket_detail_exposes_right_sidebar_and_enhanced_composer(self) -> None:
        app_source = Path("ui/clienttest-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/clienttest-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("Ticket Information", app_source)
        self.assertIn("AI Summary", app_source)
        self.assertIn("Related Knowledge", app_source)
        self.assertLess(app_source.index("Ticket Information"), app_source.index("AI Summary"))
        self.assertLess(app_source.index("AI Summary"), app_source.index("Related Knowledge"))

        self.assertIn("ticket-detail-layout", css)
        self.assertIn("ticket-detail-sidebar", css)
        self.assertIn("ticket-detail-composer", css)
        self.assertIn("ticket-detail-toolbar", css)

    def test_clienttest_new_ticket_contract_uses_reference_content_shell(self) -> None:
        app_source = Path("ui/clienttest-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/clienttest-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("Start a new support ticket", app_source)
        self.assertIn("Knowledge Base Articles", app_source)
        self.assertIn("All reference links provided by agent will show up here.", app_source)
        self.assertIn("Draft", app_source)
        self.assertIn("Pending", app_source)
        self.assertIn("Not submitted", app_source)
        self.assertIn("Unassigned", app_source)

        self.assertNotIn("Reply to Ticket", app_source)
        self.assertNotIn("Send Reply", app_source)
        self.assertNotIn("Contact Us", app_source)
        self.assertNotIn("Support Product", app_source)
        self.assertNotIn("Choose the affected product", app_source)
        self.assertNotIn("Smart intake enabled", app_source)
        self.assertNotIn("Write a sharp issue summary", app_source)
        self.assertNotIn("Prepare logs or call IDs", app_source)
        self.assertNotIn("Explain expected vs. actual behavior", app_source)

        self.assertIn("clienttest-new-ticket-shell", css)
        self.assertIn("new-ticket-body-layout", css)
        self.assertIn("new-ticket-thread-card", css)
        self.assertIn("new-ticket-composer-panel", css)
        self.assertIn("new-ticket-info-card", css)
        self.assertIn("new-ticket-inline-send-btn", css)
        self.assertIn("new-ticket-summary-toolbar-btn", css)
        self.assertIn("new-ticket-fixed-thread-panel", css)
        self.assertIn("new-ticket-fixed-info-card", css)
        self.assertIn("new-ticket-fixed-composer-panel", css)
        self.assertIn("new-ticket-fixed-knowledge-card", css)
        self.assertIn(".new-ticket-fixed-knowledge-card {\n  height: var(--new-ticket-info-card-height);", css)
        self.assertIn(".clienttest-main-new-ticket {\n  padding: 28px 36px 0;\n}", css)
        self.assertIn(".new-ticket-footer-spacer {\n  height: 40px;\n}", css)
        self.assertIn(
            '.new-ticket-page-title {\n  margin: 0;\n  font-family: "Manrope", "Inter", sans-serif;\n  font-size: clamp(32px, calc(4.2vw - 2px), 46px);',
            css,
        )
        self.assertIn(
            ".new-ticket-thread-scroll {\n  padding: 16px;\n  height: 100%;\n  max-height: none;\n  overflow: auto;\n  flex: 1 1 auto;\n  display: flex;\n}",
            css,
        )
        self.assertIn(
            ".new-ticket-thread-list {\n  width: 100%;\n  margin: 0;\n  gap: 16px;\n  flex: 1 1 auto;\n  min-height: 0;\n}",
            css,
        )
        self.assertIn(
            ".new-ticket-thread-empty {\n  flex: 1 1 auto;\n  min-height: 100%;\n  height: 100%;",
            css,
        )
        self.assertIn("--new-ticket-thread-panel-height: 480px;", css)
        self.assertIn("--new-ticket-composer-panel-height: 296px;", css)
        self.assertIn("--new-ticket-thread-panel-height: 376px;", css)
        self.assertIn("--new-ticket-composer-panel-height: 284px;", css)
        self.assertIn("--new-ticket-thread-panel-height: 344px;", css)
        self.assertIn("--new-ticket-composer-panel-height: 264px;", css)
        self.assertIn(
            ".new-ticket-textarea {\n  width: 100%;\n  min-height: 124px;\n  height: min(176px, 100%);",
            css,
        )
        self.assertIn("  resize: none;\n}", css)
        self.assertIn(".new-ticket-textarea {\n    min-height: 116px;\n    height: min(168px, 100%);", css)
        self.assertIn(".clienttest-main-new-ticket {\n    padding: 18px 16px 0;\n  }", css)
        self.assertIn(".new-ticket-footer-spacer {\n    height: 24px;\n  }", css)
        self.assertIn(".new-ticket-page-title {\n    font-size: 32px;\n  }", css)
        self.assertIn(".new-ticket-textarea {\n    min-height: 104px;\n    height: min(152px, 100%);", css)
        self.assertNotIn("resize: vertical;", css)
        self.assertNotIn("new-ticket-summary-card", css)
        self.assertNotIn("new-ticket-composer-footer", css)
        self.assertNotIn("new-ticket-product-group", css)
        self.assertNotIn("new-ticket-composer-top", css)
        self.assertNotIn("new-ticket-composer-heading", css)
        self.assertNotIn("new-ticket-composer-mode", css)

    def test_clienttest_new_ticket_initial_state_renders_empty_high_fidelity_draft(self) -> None:
        self.run_clienttest_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Alex Rivera", email: "alex.rivera@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const draft = getOrCreateDraftTicket(state.user.id);
                state.view = "chat-ticket";
                state.activeTicketId = draft.id;
                state.newTicketPreviewTicketId = draft.id;

                const html = renderChatTicket();
                const sidebarStart = html.indexOf('<aside class="new-ticket-sidebar">');
                const sidebarEnd = html.indexOf('</aside>', sidebarStart);
                const sidebarHtml = html.slice(sidebarStart, sidebarEnd);
                const toolbarStart = html.indexOf('<div class="new-ticket-composer-toolbar">');
                const toolbarEnd = html.indexOf('</div>', toolbarStart);
                const toolbarHtml = html.slice(toolbarStart, toolbarEnd);
                if (!html.includes("Start a new support ticket")) {
                  throw new Error("New Ticket draft should render the high-fidelity title.");
                }
                if (!html.includes('class="new-ticket-body-layout"')) {
                  throw new Error("New Ticket draft should render the shared body layout wrapper.");
                }
                if (!(html.indexOf('class="new-ticket-hero"') < html.indexOf('class="new-ticket-body-layout"'))) {
                  throw new Error("New Ticket draft hero should render before the shared body layout.");
                }
                if (!(html.indexOf('class="new-ticket-body-layout"') < html.indexOf('class="new-ticket-sidebar"'))) {
                  throw new Error("New Ticket draft sidebar should render inside the shared body layout.");
                }
                if (!html.includes('class="new-ticket-footer-spacer"')) {
                  throw new Error("New Ticket draft should render the restored footer spacer.");
                }
                if (!(html.indexOf('class="new-ticket-body-layout"') < html.indexOf('class="new-ticket-footer-spacer"'))) {
                  throw new Error("New Ticket draft footer spacer should render after the shared body layout.");
                }
                if (html.includes("new-ticket-breadcrumb") || html.includes("chevron_right")) {
                  throw new Error("New Ticket draft should remove the breadcrumb row.");
                }
                if (!html.includes("Ticket Information") || !html.includes("Knowledge Base Articles")) {
                  throw new Error("New Ticket draft should render the remaining right sidebar cards.");
                }
                if (!html.includes("All reference links provided by agent will show up here.")) {
                  throw new Error("New Ticket draft should show the reference-links placeholder before any agent links exist.");
                }
                if (html.includes("Write a sharp issue summary") || html.includes("Prepare logs or call IDs") || html.includes("Explain expected vs. actual behavior")) {
                  throw new Error("New Ticket draft should remove the static starter items from the knowledge sidebar.");
                }
                if (sidebarHtml.includes("AI Summary")) {
                  throw new Error("New Ticket draft should remove the AI Summary sidebar card.");
                }
                if (!toolbarHtml.includes("AI Summary")) {
                  throw new Error("New Ticket draft should render the AI Summary toolbar button.");
                }
                if (!/new-ticket-summary-toolbar-btn[^>]*type="button"/.test(toolbarHtml)) {
                  throw new Error("New Ticket draft should render AI Summary as a non-submitting toolbar button.");
                }
                if (
                  !html.includes("new-ticket-fixed-thread-panel") ||
                  !html.includes("new-ticket-fixed-info-card") ||
                  !html.includes("new-ticket-fixed-composer-panel") ||
                  !html.includes("new-ticket-fixed-knowledge-card")
                ) {
                  throw new Error("New Ticket draft should mark the major sections as fixed-size blocks.");
                }
                if (!html.includes("Draft") || !html.includes("Pending") || !html.includes("Not submitted") || !html.includes("Unassigned")) {
                  throw new Error("New Ticket draft should render draft-safe placeholder metadata.");
                }
                if (!html.includes("new-ticket-inline-send-btn")) {
                  throw new Error("New Ticket draft should render the inline send action inside the textarea shell.");
                }
                if (!html.includes("new-ticket-summary-toolbar-btn")) {
                  throw new Error("New Ticket draft should keep the AI Summary action inside the composer toolbar.");
                }
                if (html.includes("Describe your issue") || html.includes("Smart intake enabled")) {
                  throw new Error("New Ticket draft should remove the composer topline row.");
                }
                if (html.includes("Your first message creates the ticket and starts Sid's intake workflow.")) {
                  throw new Error("New Ticket draft should remove the intake workflow note.");
                }
                if (!/new-ticket-inline-send-btn[^>]*disabled/.test(html)) {
                  throw new Error("New Ticket draft should keep the inline send action disabled until there is input.");
                }
                if (html.includes("Support Product")) {
                  throw new Error("New Ticket draft should remove the product selector from the intake surface.");
                }
                if (html.includes("Choose the affected product")) {
                  throw new Error("New Ticket draft should remove product-gated helper copy.");
                }
                if (/id="chat-input"[^>]*disabled/.test(html)) {
                  throw new Error("New Ticket draft textarea should remain available before the first submission.");
                }
                if (/>\\s*Submit Ticket\\s*</.test(html) || />\\s*Send Message\\s*</.test(html)) {
                  throw new Error("New Ticket draft should use an icon-only send action, not visible action text.");
                }
                if (html.includes("Hi Alex Rivera, I'm Sid")) {
                  throw new Error("New Ticket draft should not reuse the legacy welcome bubble.");
                }
                if (html.includes("Reply to Ticket") || html.includes("Send Reply")) {
                  throw new Error("New Ticket draft should not reuse email reply semantics.");
                }
                if (html.includes("Dashboard") || html.includes("Contact Us")) {
                  throw new Error("New Ticket draft should not inject the screenshot top navigation.");
                }

                state.inputDraft = "The web SDK disconnects after 30 seconds.";
                const readyHtml = renderChatTicket();
                if (/new-ticket-inline-send-btn[^>]*disabled/.test(readyHtml)) {
                  throw new Error("New Ticket draft should become sendable with input even when no product is selected.");
                }
                """
            )
        )

    def test_clienttest_new_ticket_layout_persists_after_first_message(self) -> None:
        self.run_clienttest_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Alex Rivera", email: "alex.rivera@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const draft = getOrCreateDraftTicket(state.user.id);
                state.newTicketPreviewTicketId = draft.id;
                saveTicketMessages(draft.id, [
                  {
                    id: "msg-1",
                    role: "user",
                    content: "The video freezes after 10 minutes on iOS.",
                    createdAt: "2026-04-16T04:00:00.000Z",
                  },
                  {
                    id: "msg-2",
                    role: "assistant",
                    content: "I checked the latest call path and can see a recurring freeze after the tenth minute.",
                    createdAt: "2026-04-16T04:01:00.000Z",
                    citations: [{ heading: "iOS freeze checklist", source_url: "https://example.com/ios-freeze" }],
                  },
                ]);
                updateTicketTitle(draft.id, "The video freezes after 10 minutes");
                updateTicketStatus(draft.id, "communicating");

                state.view = "chat-ticket";
                state.activeTicketId = draft.id;
                state.inputDraft = "I can add another reproduction detail.";

                const html = renderChatTicket();
                const sidebarStart = html.indexOf('<aside class="new-ticket-sidebar">');
                const sidebarEnd = html.indexOf('</aside>', sidebarStart);
                const sidebarHtml = html.slice(sidebarStart, sidebarEnd);
                const toolbarStart = html.indexOf('<div class="new-ticket-composer-toolbar">');
                const toolbarEnd = html.indexOf('</div>', toolbarStart);
                const toolbarHtml = html.slice(toolbarStart, toolbarEnd);
                if (!html.includes("The video freezes after 10 minutes")) {
                  throw new Error("New Ticket detail should keep the updated ticket title after the first message.");
                }
                if (!html.includes('class="new-ticket-body-layout"')) {
                  throw new Error("Existing New Ticket threads should keep the shared body layout wrapper.");
                }
                if (!(html.indexOf('class="new-ticket-hero"') < html.indexOf('class="new-ticket-body-layout"'))) {
                  throw new Error("Existing New Ticket threads should keep the title above the shared body layout.");
                }
                if (!html.includes('class="new-ticket-footer-spacer"')) {
                  throw new Error("Existing New Ticket threads should render the restored footer spacer.");
                }
                if (!(html.indexOf('class="new-ticket-body-layout"') < html.indexOf('class="new-ticket-footer-spacer"'))) {
                  throw new Error("Existing New Ticket threads should keep the footer spacer after the shared body layout.");
                }
                if (!html.includes("The video freezes after 10 minutes on iOS.")) {
                  throw new Error("New Ticket detail should render the submitted customer message.");
                }
                if (!html.includes("I checked the latest call path")) {
                  throw new Error("New Ticket detail should render the assistant thread card.");
                }
                if (!sidebarHtml.includes("https://example.com/ios-freeze")) {
                  throw new Error("Existing New Ticket threads should show assistant reference links in the knowledge sidebar.");
                }
                if (sidebarHtml.includes("All reference links provided by agent will show up here.")) {
                  throw new Error("Existing New Ticket threads should hide the empty reference-links placeholder once links exist.");
                }
                if (!html.includes("Ticket Information") || !html.includes("Knowledge Base Articles")) {
                  throw new Error("New Ticket detail should keep the remaining right-column layout after the first message.");
                }
                if (sidebarHtml.includes("AI Summary")) {
                  throw new Error("Existing New Ticket threads should not restore the AI Summary sidebar card.");
                }
                if (!toolbarHtml.includes("AI Summary")) {
                  throw new Error("Existing New Ticket threads should keep the AI Summary toolbar button.");
                }
                if (!html.includes("new-ticket-inline-send-btn")) {
                  throw new Error("Existing New Ticket threads should keep the inline send action.");
                }
                if (
                  !html.includes("new-ticket-fixed-thread-panel") ||
                  !html.includes("new-ticket-fixed-info-card") ||
                  !html.includes("new-ticket-fixed-composer-panel") ||
                  !html.includes("new-ticket-fixed-knowledge-card")
                ) {
                  throw new Error("Existing New Ticket threads should keep the fixed-size section markers.");
                }
                if (html.includes("Continue the conversation") || html.includes("Smart intake enabled")) {
                  throw new Error("Existing New Ticket threads should remove the composer topline row.");
                }
                if (html.includes("Support Product")) {
                  throw new Error("Existing New Ticket threads should not restore the product selector.");
                }
                if (/>\\s*Send Message\\s*</.test(html) || />\\s*Submit Ticket\\s*</.test(html)) {
                  throw new Error("Existing New Ticket threads should keep the icon-only send action.");
                }
                if (html.includes("Reply to Ticket") || html.includes("Send Reply")) {
                  throw new Error("Existing New Ticket threads should not regress to reply/email wording.");
                }
                if (!html.includes("new-ticket-thread-card")) {
                  throw new Error("Existing New Ticket threads should render the dedicated high-fidelity message cards.");
                }
                """
            )
        )

    def test_clienttest_new_ticket_reference_sidebar_aggregates_all_agent_links(self) -> None:
        self.run_clienttest_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Alex Rivera", email: "alex.rivera@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const draft = getOrCreateDraftTicket(state.user.id);
                state.newTicketPreviewTicketId = draft.id;
                saveTicketMessages(draft.id, [
                  {
                    id: "msg-1",
                    role: "user",
                    content: "Please help investigate the recurring disconnect.",
                    createdAt: "2026-04-16T04:00:00.000Z",
                    citations: [{ heading: "Customer link should be ignored", source_url: "https://example.com/customer-ignore" }],
                  },
                  {
                    id: "msg-2",
                    role: "assistant",
                    content: "I found an iOS checklist and a retry guide.",
                    createdAt: "2026-04-16T04:01:00.000Z",
                    citations: [
                      { heading: "iOS freeze checklist", source_url: "https://example.com/ios-freeze" },
                      { heading: "No link reference" },
                    ],
                  },
                  {
                    id: "msg-3",
                    role: "engineer",
                    content: "Internal case note with a link that should not surface here.",
                    createdAt: "2026-04-16T04:02:00.000Z",
                    citations: [{ heading: "Engineer only", source_url: "https://example.com/engineer-ignore" }],
                  },
                  {
                    id: "msg-4",
                    role: "assistant",
                    content: "Here is another source plus the same checklist again.",
                    createdAt: "2026-04-16T04:03:00.000Z",
                    citations: [{ heading: "Checklist duplicate", source_url: "https://example.com/ios-freeze" }],
                    sources: ["https://example.com/reconnect-guide"],
                  },
                ]);
                updateTicketTitle(draft.id, "Recurring disconnect during support call");
                updateTicketStatus(draft.id, "communicating");

                state.view = "chat-ticket";
                state.activeTicketId = draft.id;

                const html = renderChatTicket();
                const sidebarStart = html.indexOf('<aside class="new-ticket-sidebar">');
                const sidebarEnd = html.indexOf('</aside>', sidebarStart);
                const sidebarHtml = html.slice(sidebarStart, sidebarEnd);
                const iosMatches = sidebarHtml.match(/https:\\/\\/example.com\\/ios-freeze/g) || [];
                const reconnectMatches = sidebarHtml.match(/https:\\/\\/example.com\\/reconnect-guide/g) || [];

                if (iosMatches.length !== 1) {
                  throw new Error("New Ticket reference sidebar should dedupe repeated assistant links by URL.");
                }
                if (reconnectMatches.length !== 1) {
                  throw new Error("New Ticket reference sidebar should include assistant source URLs from the full conversation.");
                }
                if (sidebarHtml.includes("https://example.com/customer-ignore") || sidebarHtml.includes("https://example.com/engineer-ignore")) {
                  throw new Error("New Ticket reference sidebar should ignore non-assistant links.");
                }
                if (sidebarHtml.includes("No link reference")) {
                  throw new Error("New Ticket reference sidebar should ignore citations that have no source URL.");
                }
                if (sidebarHtml.includes("All reference links provided by agent will show up here.")) {
                  throw new Error("New Ticket reference sidebar should remove the placeholder after assistant links exist.");
                }
              """
            )
        )

    def test_clienttest_reuses_existing_client_runtime_contracts(self) -> None:
        app_source = Path("ui/clienttest-ui/app.js").read_text(encoding="utf-8")

        self.assertIn('const AUTH_KEY = "helpdesk_auth_user";', app_source)
        self.assertIn('const TICKETS_KEY = "helpdesk_tickets";', app_source)
        self.assertIn('const COUNTER_KEY = "helpdesk_ticket_counter";', app_source)
        self.assertIn('/api/client/ack', app_source)
        self.assertIn('/ws/client', app_source)


if __name__ == "__main__":
    unittest.main()
