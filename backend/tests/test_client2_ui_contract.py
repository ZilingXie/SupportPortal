from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


class Client2RouteSmokeTests(unittest.TestCase):
    def test_client2_static_mount_and_entrypoints_exist(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        self.assertIn('CLIENT2_DIR = UI_DIR / "client2-ui"', main_source)
        self.assertIn(
            'app.mount("/client2", StaticFiles(directory=CLIENT2_DIR, html=True), name="client2-ui")',
            main_source,
        )

        expected_files = [
            Path("ui/client2-ui/index.html"),
            Path("ui/client2-ui/styles.css"),
            Path("ui/client2-ui/app.js"),
        ]
        for file_path in expected_files:
            self.assertTrue(file_path.exists(), str(file_path))

    def test_client2_html_references_local_assets(self) -> None:
        html = Path("ui/client2-ui/index.html").read_text(encoding="utf-8")

        self.assertIn("./styles.css?v=20260417-client2-visible-footer-band-1", html)
        self.assertIn("./app.js?v=20260417-client2-visible-footer-band-1", html)


class Client2UiContractTests(unittest.TestCase):
    def run_client2_app_script(self, script: str) -> None:
        node_script = textwrap.dedent(
            f"""
            (async () => {{
            const fs = require("fs");
            const vm = require("vm");
            const userScript = {script!r};

            let source = fs.readFileSync("ui/client2-ui/app.js", "utf8");
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
                activeElement: null,
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
              Node: function Node() {{}},
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

    def test_client2_shell_uses_merged_ui_without_preview_copy(self) -> None:
        html = Path("ui/client2-ui/index.html").read_text(encoding="utf-8")
        app_source = Path("ui/client2-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/client2-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("<title>Support Portal</title>", html)
        self.assertIn("Support Portal", app_source)
        self.assertIn('<span class="sidebar-nav-label">New Ticket</span>', app_source)
        self.assertIn('<span class="sidebar-nav-label">Workspace</span>', app_source)
        self.assertIn('<span class="sidebar-nav-label">My Tickets</span>', app_source)
        self.assertIn("client2-route-shell", app_source)
        self.assertIn("new-ticket-inline-send-btn", app_source)
        self.assertIn("client2-route-shell", css)
        self.assertNotIn("Continue This Ticket", app_source)
        self.assertNotIn("new-ticket-postsend-send-btn", app_source)

        self.assertNotIn("Clienttest Preview", html)
        self.assertNotIn("Clienttest Preview", app_source)
        self.assertNotIn("Preview Route", app_source)
        self.assertNotIn("/client remains unchanged", app_source)
        self.assertNotIn("CLIENTTEST PREVIEW", app_source)

    def test_client2_source_keeps_client_runtime_contracts(self) -> None:
        app_source = Path("ui/client2-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/client2-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("pendingByTicket", app_source)
        self.assertIn("supersededTurnsByTicket", app_source)
        self.assertIn("requester: state.user.name", app_source)
        self.assertIn("if (normalizedProduct) {", app_source)
        self.assertIn("requestBody.product = normalizedProduct;", app_source)
        self.assertIn("cancel-pending", app_source)
        self.assertIn("clienttest-home-shell", app_source)
        self.assertIn("clienttest-home-intro", app_source)
        self.assertIn("clienttest-home-content-grid", app_source)
        self.assertIn("clienttest-route-page", app_source)
        self.assertIn("clienttest-route-page-footer-band", app_source)
        self.assertIn("clienttest-route-footer-band", app_source)
        self.assertIn("clienttest-route-scroll-region", app_source)
        self.assertIn("--client2-route-max-width", css)
        self.assertIn("--client2-route-top-space", css)
        self.assertIn("--client2-route-bottom-space", css)
        self.assertIn("--client2-visible-footer-band-space", css)
        self.assertIn(".clienttest-route-page", css)
        self.assertIn(".clienttest-route-page-footer-band", css)
        self.assertIn(".clienttest-route-footer-band", css)
        self.assertIn(".clienttest-route-scroll-region", css)
        self.assertNotIn("clienttest-route-page-fixed-footer", app_source)
        self.assertNotIn("--client2-fixed-footer-reserved-space", css)
        self.assertNotIn(".clienttest-route-page-fixed-footer", css)
        self.assertNotIn("/api/client/ack", app_source)
        self.assertNotIn("Got it, let me check this for you.", app_source)
        self.assertNotIn("I got your message and I am checking it now.", app_source)
        self.assertNotIn("AI is cross-referencing system health logs", app_source)
        self.assertNotIn("checking the knowledge base... click stop to interrupt.", app_source)

    def test_client2_workspace_home_uses_compact_intro_shell(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const active = createTicket(state.user.id);
                updateTicketTitle(active.id, "Black Screen Troubleshooting Steps");
                updateTicketStatus(active.id, "communicating");
                saveTicketMessages(active.id, [
                  {
                    id: "msg-1",
                    role: "user",
                    content: "I got black screen, what should I do?",
                    createdAt: "2026-04-17T10:00:00.000Z",
                  },
                ]);

                const resolved = createTicket(state.user.id);
                updateTicketTitle(resolved.id, "Join Channel Question");
                updateTicketStatus(resolved.id, "resolved");
                saveTicketMessages(resolved.id, [
                  {
                    id: "msg-2",
                    role: "assistant",
                    content: "Use joinChannel with a valid token.",
                    createdAt: "2026-04-17T11:00:00.000Z",
                  },
                ]);

                state.view = "workspace";
                const html = renderChatHome();
                if (!html.includes("clienttest-home-shell")) {
                  throw new Error("Client2 workspace should render the shared home shell.");
                }
                if (!html.includes("clienttest-home-intro")) {
                  throw new Error("Client2 workspace should render the compact intro block.");
                }
                if (!html.includes("clienttest-home-content-grid")) {
                  throw new Error("Client2 workspace should render the ticket-first content grid.");
                }
                if (!html.includes("clienttest-route-page-footer-band")) {
                  throw new Error("Client2 workspace should render the visible footer-band shell.");
                }
                if (!html.includes("clienttest-route-footer-band")) {
                  throw new Error("Client2 workspace should render a real in-flow footer band element.");
                }
                if (html.includes("welcome-inner")) {
                  throw new Error("Client2 workspace should no longer use the oversized legacy hero wrapper.");
                }
                if (!html.includes("Continue what needs attention")) {
                  throw new Error("Client2 workspace should keep the active tickets panel.");
                }
                if (!html.includes("Latest ticket movement")) {
                  throw new Error("Client2 workspace should keep the recent activity panel.");
                }
              """
            )
        )

    def test_client2_new_ticket_draft_uses_merged_ui_without_product_selector(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Alex Rivera", email: "alex.rivera@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const draft = getOrCreateDraftTicket(state.user.id);
                state.view = "chat-ticket";
                state.activeTicketId = draft.id;
                state.newTicketPreviewTicketId = draft.id;

                const html = renderChatTicket();
                if (!html.includes("Start a new support ticket")) {
                  throw new Error("Client2 draft should render the approved new-ticket title.");
                }
                if (!html.includes("clienttest-route-page-footer-band")) {
                  throw new Error("Client2 draft should render the visible footer-band shell.");
                }
                if (!html.includes("clienttest-route-footer-band")) {
                  throw new Error("Client2 draft should render a real in-flow footer band element.");
                }
                if (!html.includes("Knowledge Base Articles")) {
                  throw new Error("Client2 draft should keep the reference-links sidebar.");
                }
                if (!html.includes("All reference links provided by agent will show up here.")) {
                  throw new Error("Client2 draft should show the empty reference-links placeholder.");
                }
                if (!html.includes("new-ticket-knowledge-placeholder new-ticket-info-value")) {
                  throw new Error("Client2 draft placeholder should reuse the info-value typography treatment.");
                }
                if (html.includes("Select Product") || html.includes("Support Product")) {
                  throw new Error("Client2 draft should not render any product selector.");
                }
                if (html.includes("Clienttest Preview") || html.includes("Preview Route")) {
                  throw new Error("Client2 draft should not leak preview-only copy.");
                }
                if (/id="chat-input"[^>]*disabled/.test(html)) {
                  throw new Error("Client2 draft composer should stay enabled without a preselected product.");
                }
                if (!html.includes("new-ticket-summary-toolbar-btn")) {
                  throw new Error("Client2 draft should keep the AI Summary toolbar button.");
                }
                """
            )
        )

    def test_client2_first_send_uses_client_runtime_and_switches_to_correspondence_shell(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));
                render = () => {};
                syncTicketsFromBackend = async () => {};
                requestChatScrollToBottom = () => {};
                ensurePendingStatusPolling = () => {};
                AbortController = class AbortController {
                  constructor() {
                    this.signal = { aborted: false };
                  }
                  abort() {
                    this.signal.aborted = true;
                  }
                };

                const ticket = getOrCreateDraftTicket(state.user.id);
                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;
                state.newTicketPreviewTicketId = ticket.id;

                const calls = [];
                fetch = (url, options = undefined) => {
                  calls.push({ url, options });
                  if (url === "/api/tickets/query") {
                    return Promise.resolve({
                      ok: true,
                      json: async () => ({
                        ticket_id: ticket.id,
                        answer: "",
                        ai_replied: false,
                        queued_for_ai: true,
                        queued_message_created_at: "2026-04-17T09:00:00.000Z",
                        status: "communicating",
                      }),
                    });
                  }
                  throw new Error(`Unexpected fetch call to ${url}`);
                };

                await handleSendMessage("Need help with RTC join flow");

                if (calls.some((entry) => entry.url === "/api/client/ack")) {
                  throw new Error("Client2 send flow should not call the transient ack endpoint.");
                }
                const queryCall = calls.find((entry) => entry.url === "/api/tickets/query");
                if (!queryCall) {
                  throw new Error("Client2 send flow should call the ticket query endpoint.");
                }
                const requestBody = JSON.parse(queryCall.options.body);
                if (requestBody.requester !== state.user.name) {
                  throw new Error(`Client2 query should include requester name, got ${JSON.stringify(requestBody)}.`);
                }
                if (Object.prototype.hasOwnProperty.call(requestBody, "product")) {
                  throw new Error("Client2 draft first send should omit product when none is known.");
                }
                const pending = state.pendingByTicket[ticket.id];
                if (!pending || pending.phase !== "queued") {
                  throw new Error("Client2 first send should keep per-ticket queued pending state.");
                }

                const html = renderChatTicket();
                if (!html.includes("new-ticket-postsend-shell")) {
                  throw new Error("Client2 first send should switch into the correspondence shell.");
                }
                if (!html.includes("clienttest-route-page")) {
                  throw new Error("Client2 post-send shell should use the shared route page shell.");
                }
                if (!html.includes("clienttest-route-page-footer-band")) {
                  throw new Error("Client2 first-send postsend shell should render the visible footer-band shell.");
                }
                if (!html.includes("clienttest-route-footer-band")) {
                  throw new Error("Client2 first-send postsend shell should render a real in-flow footer band element.");
                }
                if (html.includes("clienttest-route-page-fixed-footer")) {
                  throw new Error("Client2 post-send shell should not use the removed fixed-footer reserve contract.");
                }
                if (!html.includes(`My Tickets / Ticket #${ticket.id}`)) {
                  throw new Error("Client2 post-send shell should restore the breadcrumb header.");
                }
                if (!html.includes("new-ticket-composer-input-shell")) {
                  throw new Error("Client2 post-send shell should reuse the draft composer input shell.");
                }
                if (!html.includes("new-ticket-inline-send-btn")) {
                  throw new Error("Client2 post-send shell should keep the inline send button.");
                }
                if (!html.includes("Add more context or follow-up details...")) {
                  throw new Error("Client2 post-send shell should keep the draft-style follow-up placeholder.");
                }
                if (html.includes("Continue This Ticket")) {
                  throw new Error("Client2 post-send shell should not render the old correspondence composer header.");
                }
                if (html.includes("new-ticket-postsend-composer-header") || html.includes("new-ticket-postsend-composer-footer")) {
                  throw new Error("Client2 post-send shell should not render the old correspondence composer chrome.");
                }
                if (html.includes("new-ticket-postsend-send-btn")) {
                  throw new Error("Client2 post-send shell should not render the old footer send button.");
                }
                if (html.includes("Got it, let me check this for you.") || html.includes("I got your message and I am checking it now.")) {
                  throw new Error("Client2 should not render reassurance copy after the first send.");
                }
                if (html.includes("AI is cross-referencing system health logs") || html.includes("checking the knowledge base... click stop to interrupt.")) {
                  throw new Error("Client2 should not render preview waiting copy after the first send.");
                }
                if (html.includes("Sid is preparing the next support response.")) {
                  throw new Error("Client2 should not render the post-send waiting line after the first send.");
                }
                if (html.includes("new-ticket-postsend-waiting") || html.includes("new-ticket-thread-waiting")) {
                  throw new Error("Client2 should not render waiting markers after the first send.");
                }
              """
            )
        )

    def test_client2_request_engineer_keeps_client_runtime_behavior(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                state.newTicketPreviewTicketId = ticket.id;
                updateTicketTitle(ticket.id, "Need direct engineer review");
                updateTicketStatus(ticket.id, "communicating");
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-1",
                    role: "user",
                    content: "Can an engineer check my routing issue?",
                    createdAt: new Date().toISOString(),
                  },
                ]);
                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;

                let capturedUrl = null;
                let capturedOptions = null;
                fetch = async (url, options = undefined) => {
                  capturedUrl = url;
                  capturedOptions = options;
                  return {
                    ok: true,
                    json: async () => ({
                      ticket_id: ticket.id,
                      status: "escalated",
                      updated_at: "2026-04-17T10:00:00.000Z",
                    }),
                  };
                };

                const changed = await requestEngineerAssistance(ticket.id);
                if (!changed) {
                  throw new Error("Client2 should keep the request engineer action behavior.");
                }
                if (capturedUrl !== `/api/tickets/${ticket.id}/request-engineer-assistance`) {
                  throw new Error(`Expected engineer-assistance endpoint call, got ${capturedUrl}.`);
                }
                if (String(capturedOptions?.method || "").toUpperCase() !== "POST") {
                  throw new Error("Client2 engineer assistance should use POST.");
                }
                const updated = getTicketById(ticket.id);
                if (updated.status !== "escalated") {
                  throw new Error(`Client2 engineer assistance should keep escalated state, got ${updated.status}.`);
                }
                if (updated.messages.length !== 1) {
                  throw new Error("Client2 engineer assistance should not append a fake transcript message.");
                }
              """
            )
        )

    def test_client2_existing_ticket_uses_new_ticket_postsend_shell(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                updateTicketTitle(ticket.id, "Channel join question");
                updateTicketStatus(ticket.id, "communicating");
                updateTicketProduct(ticket.id, "audio_video_calling");
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-1",
                    role: "user",
                    content: "How do I join a channel?",
                    createdAt: "2026-04-17T10:39:00.000Z",
                  },
                  {
                    id: "msg-2",
                    role: "assistant",
                    content: "Use `joinChannel` with a valid token and channel name.",
                    createdAt: "2026-04-17T10:40:00.000Z",
                    citations: [
                      {
                        heading: "Join a channel",
                        sourceUrl: "https://docs.example.com/join-channel",
                      },
                    ],
                  },
                ]);

                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;
                state.newTicketPreviewTicketId = null;

                const html = renderChatTicket();
                if (!html.includes("new-ticket-postsend-shell")) {
                  throw new Error("Existing client2 tickets should render the new-ticket postsend shell.");
                }
                if (!html.includes("clienttest-route-page")) {
                  throw new Error("Existing client2 tickets should use the shared route page shell.");
                }
                if (html.includes("clienttest-route-page-footer-band")) {
                  throw new Error("Existing client2 tickets should keep the current detail footer depth without the visible footer-band shell.");
                }
                if (html.includes("clienttest-route-footer-band")) {
                  throw new Error("Existing client2 tickets should not render the new in-flow footer band element.");
                }
                if (html.includes("clienttest-route-page-fixed-footer")) {
                  throw new Error("Existing client2 tickets should not use the removed fixed-footer reserve contract.");
                }
                if (html.includes("ticket-detail-layout")) {
                  throw new Error("Existing client2 tickets should not fall back to the legacy detail layout.");
                }
                if (!html.includes(`My Tickets / Ticket #${ticket.id}`)) {
                  throw new Error("Existing client2 tickets should keep the postsend breadcrumb.");
                }
                if (!html.includes("Request Engineer")) {
                  throw new Error("Existing client2 tickets should keep the Request Engineer action.");
                }
                if (!html.includes("Resolve")) {
                  throw new Error("Existing client2 tickets should keep the Resolve action.");
                }
                if (!html.includes("Knowledge Base Articles")) {
                  throw new Error("Existing client2 tickets should keep the new-ticket knowledge sidebar.");
                }
                if (!html.includes("Join a channel")) {
                  throw new Error("Existing client2 tickets should keep source chips in the postsend shell.");
                }
                if (!html.includes("new-ticket-fixed-knowledge-card")) {
                  throw new Error("Existing client2 tickets should keep the knowledge card on the draft fixed-height rail.");
                }
                if (!html.includes("new-ticket-correspondence-source-chip")) {
                  throw new Error("Existing client2 tickets should render knowledge references with correspondence chips.");
                }
                if (html.includes("Agent reference")) {
                  throw new Error("Existing client2 tickets should not render legacy knowledge item meta copy.");
                }
                if (!html.includes("new-ticket-inline-send-btn")) {
                  throw new Error("Existing client2 tickets should keep the inline composer action.");
                }
                if (html.includes("ticket-summary-card")) {
                  throw new Error("Existing client2 tickets should not render the legacy AI Summary panel.");
                }
                if (html.includes("Continue the same ticket with Sid handling the assistant turn")) {
                  throw new Error("Existing client2 tickets should not render the legacy composer header copy.");
                }
              """
            )
        )

    def test_client2_my_tickets_uses_visible_footer_band_shell(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                updateTicketTitle(ticket.id, "Channel join question");
                updateTicketStatus(ticket.id, "communicating");

                state.view = "tickets";
                const html = renderTicketsPage();
                if (!html.includes("clienttest-route-page")) {
                  throw new Error("Client2 tickets page should keep the shared route shell.");
                }
                if (!html.includes("clienttest-route-page-footer-band")) {
                  throw new Error("Client2 tickets page should render the visible footer-band shell.");
                }
                if (!html.includes("clienttest-route-scroll-region")) {
                  throw new Error("Client2 tickets page should render the scoped internal scroll region.");
                }
                if (!html.includes("clienttest-route-footer-band")) {
                  throw new Error("Client2 tickets page should render a real in-flow footer band element.");
                }
                if (!html.includes("My Tickets")) {
                  throw new Error("Client2 tickets page should keep the tickets heading.");
                }
              """
            )
        )
