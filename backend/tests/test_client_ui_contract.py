from __future__ import annotations

import re
import subprocess
import textwrap
import unittest
from pathlib import Path


class ClientRouteSmokeTests(unittest.TestCase):
    def test_client_mount_and_archived_legacy_ui_directories_exist(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        self.assertIn('CLIENT_DIR = UI_DIR / "client-ui"', main_source)
        self.assertNotIn('CLIENT2_DIR = UI_DIR / "client2-ui"', main_source)
        self.assertNotIn('CLIENTTEST_DIR = UI_DIR / "clienttest-ui"', main_source)
        self.assertNotIn('app.mount("/client2", StaticFiles(directory=CLIENT2_DIR, html=True), name="client2-ui")', main_source)
        self.assertNotIn('app.mount("/clienttest", StaticFiles(directory=CLIENTTEST_DIR, html=True), name="clienttest-ui")', main_source)
        self.assertIn('app.mount("/client", StaticFiles(directory=CLIENT_DIR, html=True), name="client-ui")', main_source)

        expected_files = [
            Path("ui/client-ui/index.html"),
            Path("ui/client-ui/styles.css"),
            Path("ui/client-ui/app.js"),
            Path("ui/archive/client-ui-legacy/index.html"),
            Path("ui/archive/clienttest-ui-legacy/index.html"),
        ]
        for file_path in expected_files:
            self.assertTrue(file_path.exists(), str(file_path))

    def test_client_html_references_promoted_client2_assets(self) -> None:
        html = Path("ui/client-ui/index.html").read_text(encoding="utf-8")

        self.assertIn("<title>Support Portal</title>", html)
        self.assertIn("./styles.css?v=20260421-client-composer-wysiwyg-before-send-2", html)
        self.assertIn("./app.js?v=20260421-client-composer-wysiwyg-before-send-2", html)


class ClientRouteRedirectContractTests(unittest.TestCase):
    def test_client2_and_clienttest_routes_use_redirect_contract(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")

        self.assertIn('@app.get("/client2")', main_source)
        self.assertIn('@app.get("/client2/{legacy_path:path}")', main_source)
        self.assertIn('@app.get("/clienttest")', main_source)
        self.assertIn('@app.get("/clienttest/{legacy_path:path}")', main_source)
        self.assertIn("request.url.query", main_source)
        self.assertIn('legacy_path or ""', main_source)
        self.assertIn('target = "/client/"', main_source)
        self.assertIn('target = f"/client/{legacy_path}"', main_source)
        self.assertIn('return RedirectResponse(url=target)', main_source)


class ClientUiContractTests(unittest.TestCase):
    def run_client_app_script(self, script: str) -> None:
        node_script = textwrap.dedent(
            f"""
            (async () => {{
            const fs = require("fs");
            const vm = require("vm");
            const userScript = {script!r};

            let source = fs.readFileSync("ui/client-ui/app.js", "utf8");
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

    run_client2_app_script = run_client_app_script

    def test_client2_shell_uses_merged_ui_without_preview_copy(self) -> None:
        html = Path("ui/client-ui/index.html").read_text(encoding="utf-8")
        app_source = Path("ui/client-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/client-ui/styles.css").read_text(encoding="utf-8")

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
        self.assertNotIn(
            "Support Portal keeps your ticket queue, active work, and latest correspondence in one left-rail workspace.",
            app_source,
        )
        self.assertNotIn(
            "Scan active, waiting, and resolved tickets through the card-based My Tickets surface.",
            app_source,
        )
        self.assertIn("Manage your support tickets in one place", app_source)
        self.assertIn(
            "Track open tickets, return to recent conversations, and keep your support work moving.",
            app_source,
        )
        self.assertNotIn(
            "A calmer client workspace with a stronger ticket-detail reading surface.",
            app_source,
        )
        self.assertNotIn(
            "Track open work, return to recent tickets, and continue the same client support flows inside the redesigned left-rail shell.",
            app_source,
        )
        self.assertNotIn("<span>Ticket Board</span>", app_source)

        self.assertNotIn("Clienttest Preview", html)
        self.assertNotIn("Clienttest Preview", app_source)
        self.assertNotIn("Preview Route", app_source)
        self.assertNotIn("/client remains unchanged", app_source)
        self.assertNotIn("CLIENTTEST PREVIEW", app_source)

    def test_client2_context_bar_only_renders_for_chat_ticket(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.view = "workspace";
                const workspaceBar = String(renderContextBar() || "").trim();
                if (workspaceBar !== "") {
                  throw new Error("Client2 workspace should not render the removed context row.");
                }

                state.view = "tickets";
                const ticketsBar = String(renderContextBar() || "").trim();
                if (ticketsBar !== "") {
                  throw new Error("Client2 tickets page should not render the removed context row.");
                }

                state.view = "chat-ticket";
                const chatBar = String(renderContextBar() || "");
                if (!chatBar.includes("context-bar")) {
                  throw new Error("Client2 chat-ticket should keep its ticket context bar.");
                }
              """
            )
        )

    def test_client2_source_keeps_client_runtime_contracts(self) -> None:
        app_source = Path("ui/client-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/client-ui/styles.css").read_text(encoding="utf-8")

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
        self.assertIn("clienttest-route-footer-shell", app_source)
        self.assertIn("clienttest-route-footer-shell-communicating", app_source)
        self.assertIn("new-ticket-tail-route", app_source)
        self.assertIn("new-ticket-draft-inline-route", app_source)
        self.assertIn("new-ticket-thread-footer-composer", app_source)
        self.assertIn("tickets-status-filter", app_source)
        self.assertIn("--client2-tail-footer-block-height", css)
        self.assertIn("--new-ticket-draft-left-stack-height", css)
        self.assertIn("--new-ticket-draft-thread-height", css)
        self.assertIn("--new-ticket-draft-page-tail-blank", css)
        self.assertIn("--new-ticket-draft-knowledge-card-height", css)
        self.assertIn("--tickets-status-filter-width: 188px;", css)
        self.assertRegex(
            css,
            re.compile(
                r"\.new-ticket-draft-inline-route \.new-ticket-fixed-thread-panel \{\s*height: var\(--new-ticket-draft-thread-height\);",
                re.MULTILINE,
            ),
        )
        self.assertRegex(
            css,
            re.compile(
                r"\.new-ticket-draft-inline-route \.new-ticket-main-column \{[^}]*padding-bottom: var\(--new-ticket-draft-page-tail-blank\);",
                re.DOTALL,
            ),
        )
        self.assertRegex(
            css,
            re.compile(
                r"--new-ticket-draft-thread-height: calc\([^;]*var\(--new-ticket-draft-page-tail-blank\)",
                re.DOTALL,
            ),
        )
        self.assertRegex(
            css,
            re.compile(
                r"--new-ticket-draft-knowledge-card-height:\s*calc\(var\(--new-ticket-info-card-height\) - var\(--new-ticket-draft-page-tail-blank\)\);",
                re.MULTILINE,
            ),
        )
        self.assertRegex(
            css,
            re.compile(
                r"\.new-ticket-draft-inline-route \.new-ticket-fixed-knowledge-card \{[^}]*height:\s*var\(--new-ticket-draft-knowledge-card-height\);",
                re.DOTALL,
            ),
        )
        self.assertRegex(
            css,
            re.compile(
                r"\.tickets-status-filter \{[^}]*width:\s*var\(--tickets-status-filter-width\);[^}]*min-width:\s*var\(--tickets-status-filter-width\);[^}]*max-width:\s*var\(--tickets-status-filter-width\);",
                re.DOTALL,
            ),
        )
        self.assertRegex(
            css,
            re.compile(
                r"@media \(max-width: 960px\) \{[\s\S]*?\.tickets-status-filter \{[^}]*width:\s*var\(--tickets-status-filter-width\);[^}]*min-width:\s*var\(--tickets-status-filter-width\);[^}]*max-width:\s*var\(--tickets-status-filter-width\);",
                re.MULTILINE,
            ),
        )
        self.assertNotIn("min-height: calc(100% + var(--new-ticket-composer-panel-height));", css)
        self.assertNotRegex(
            css,
            re.compile(
                r"\.new-ticket-draft-inline-route \.new-ticket-main-column \{[^}]*display: flex;[^}]*flex-direction: column;",
                re.DOTALL,
            ),
        )
        self.assertNotRegex(
            css,
            re.compile(
                r"\.new-ticket-draft-inline-route \.new-ticket-fixed-thread-panel \{[^}]*flex: 1 1 auto;",
                re.DOTALL,
            ),
        )
        self.assertRegex(
            css,
            re.compile(
                r"\.new-ticket-draft-inline-route \.new-ticket-thread-empty \{[^}]*padding: 40px 24px;",
                re.DOTALL,
            ),
        )
        self.assertRegex(
            css,
            re.compile(
                r"\.new-ticket-draft-inline-route \.new-ticket-thread-empty h2 \{[^}]*font-size: 24px;",
                re.DOTALL,
            ),
        )
        self.assertRegex(
            css,
            re.compile(
                r"\.new-ticket-draft-inline-route \.new-ticket-thread-empty p \{[^}]*max-width: 360px;",
                re.DOTALL,
            ),
        )
        self.assertIn("--client2-route-max-width", css)
        self.assertIn("--client2-route-top-space", css)
        self.assertIn("--client2-route-bottom-space", css)
        self.assertIn(".clienttest-route-footer-shell", css)
        self.assertIn(".clienttest-route-footer-shell-communicating", css)
        self.assertIn(".new-ticket-thread-footer-composer", css)
        self.assertIn(".clienttest-route-page", css)
        self.assertIn(".clienttest-route-page-footer-band", css)
        self.assertIn(".clienttest-route-footer-band", css)
        self.assertRegex(
            css,
            re.compile(
                r"\.clienttest-route-page-footer-band \{[^}]*min-height:\s*calc\(100% \+ var\(--client2-route-bottom-space\)\);[^}]*padding-bottom:\s*0;",
                re.DOTALL,
            ),
        )
        self.assertRegex(
            css,
            re.compile(
                r"\.clienttest-route-footer-band \{[^}]*height:\s*var\(--client2-route-bottom-space\);[^}]*min-height:\s*var\(--client2-route-bottom-space\);",
                re.DOTALL,
            ),
        )
        self.assertRegex(
            css,
            re.compile(
                r"\.clienttest-route-footer-shell \{[^}]*height:\s*var\(--client2-tail-footer-block-height\);",
                re.DOTALL,
            ),
        )
        self.assertNotRegex(
            css,
            re.compile(
                r"\.clienttest-home \{[^}]*padding-top:\s*0;",
                re.DOTALL,
            ),
        )
        self.assertRegex(
            css,
            re.compile(
                r"\.clienttest-home \{[^}]*padding-top:\s*12px;",
                re.DOTALL,
            ),
        )
        self.assertIn(".new-ticket-tail-route", css)
        self.assertIn(".new-ticket-draft-inline-route", css)
        self.assertNotIn("clienttest-route-scroll-region", app_source)
        self.assertNotIn(".clienttest-route-scroll-region", css)
        self.assertNotIn("clienttest-route-page-fixed-footer", app_source)
        self.assertNotIn("--client2-fixed-footer-reserved-space", css)
        self.assertNotIn(".clienttest-route-page-fixed-footer", css)
        self.assertNotIn("/api/client/ack", app_source)
        self.assertNotIn("Got it, let me check this for you.", app_source)
        self.assertNotIn("I got your message and I am checking it now.", app_source)
        self.assertNotIn("AI is cross-referencing system health logs", app_source)
        self.assertNotIn("checking the knowledge base... click stop to interrupt.", app_source)

    def test_client2_source_uses_fixed_new_ticket_default_title_contract(self) -> None:
        app_source = Path("ui/client-ui/app.js").read_text(encoding="utf-8")

        self.assertIn('"New ticket"', app_source)
        self.assertNotIn("function generateTitle(", app_source)
        self.assertNotIn("updateTicketTitle(ticketId, generateTitle(text));", app_source)

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

                const followUp = createTicket(state.user.id);
                updateTicketTitle(followUp.id, "Token Expired During Reconnect");
                updateTicketStatus(followUp.id, "investigating");
                saveTicketMessages(followUp.id, [
                  {
                    id: "msg-1b",
                    role: "user",
                    content: "The reconnect flow fails after token expiry.",
                    createdAt: "2026-04-17T10:30:00.000Z",
                  },
                ]);

                const escalated = createTicket(state.user.id);
                updateTicketTitle(escalated.id, "Cloud Proxy Route Check");
                updateTicketStatus(escalated.id, "escalated");
                saveTicketMessages(escalated.id, [
                  {
                    id: "msg-1c",
                    role: "user",
                    content: "Please verify the upstream route behavior.",
                    createdAt: "2026-04-17T10:45:00.000Z",
                  },
                ]);

                const followUpTwo = createTicket(state.user.id);
                updateTicketTitle(followUpTwo.id, "UID Publish State Regression");
                updateTicketStatus(followUpTwo.id, "communicating");
                saveTicketMessages(followUpTwo.id, [
                  {
                    id: "msg-1d",
                    role: "user",
                    content: "The publish state changes unexpectedly after reconnect.",
                    createdAt: "2026-04-17T10:50:00.000Z",
                  },
                ]);

                const oldestActive = createTicket(state.user.id);
                updateTicketTitle(oldestActive.id, "Legacy Active Ticket Hidden");
                updateTicketStatus(oldestActive.id, "investigating");
                saveTicketMessages(oldestActive.id, [
                  {
                    id: "msg-1e",
                    role: "user",
                    content: "This is the oldest active ticket and should not be visible in the top four.",
                    createdAt: "2026-04-17T10:55:00.000Z",
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
                if (html.includes("clienttest-route-footer-shell-communicating")) {
                  throw new Error("Client2 workspace should remove the communicating-style footer shell and keep only the bottom blank.");
                }
                if (html.includes("new-ticket-composer-toolbar") || html.includes("new-ticket-inline-send-btn") || html.includes("id=\\"chat-input\\"")) {
                  throw new Error("Client2 workspace footer band should stay non-interactive.");
                }
                if (html.includes("welcome-inner")) {
                  throw new Error("Client2 workspace should no longer use the oversized legacy hero wrapper.");
                }
                if (!html.includes("Continue what needs attention")) {
                  throw new Error("Client2 workspace should keep the active tickets panel.");
                }
                state.serviceEvents = {
                  loadState: "ready",
                  items: [
                    {
                      title: "RTC black screen issue",
                      summary: "A limited number of users experienced black screen behavior.",
                      link: "https://status.agora.io/events/44",
                      statusLabel: "Resolved",
                      postedAtLabel: "Posted Feb 24, 2026 - 01:04 PM UTC",
                    },
                  ],
                  statusPageUrl: "https://status.agora.io/",
                  fetchedAt: "2026-04-21T02:00:00.000Z",
                  lastRequestedAtMs: 0,
                };

                const readyHtml = renderChatHome();
                if (!readyHtml.includes("Service Events")) {
                  throw new Error("Client2 workspace should render the service events panel.");
                }
                if (!readyHtml.includes("Latest Agora platform events")) {
                  throw new Error("Client2 workspace should render the updated service events title.");
                }
                if (!readyHtml.includes("Open Agora Status Page ->")) {
                  throw new Error("Client2 workspace should render the status page external link.");
                }
                if (!readyHtml.includes('href="https://status.agora.io/"') || !readyHtml.includes('target="_blank"') || !readyHtml.includes('rel="noopener noreferrer"')) {
                  throw new Error("Client2 workspace should render the status page action as a safe external link.");
                }
                if (!readyHtml.includes("RTC black screen issue")) {
                  throw new Error("Client2 workspace should render event titles from the service events payload.");
                }
                if (!readyHtml.includes("View incident")) {
                  throw new Error("Client2 workspace should render incident detail links.");
                }
                if (
                  !readyHtml.includes("Black Screen Troubleshooting Steps") ||
                  !readyHtml.includes("Token Expired During Reconnect") ||
                  !readyHtml.includes("Cloud Proxy Route Check") ||
                  !readyHtml.includes("UID Publish State Regression")
                ) {
                  throw new Error("Client2 workspace should keep the four most recent active tickets visible.");
                }
                if (readyHtml.includes("Legacy Active Ticket Hidden")) {
                  throw new Error("Client2 workspace should truncate the active tickets panel to four visible rows.");
                }
                if (readyHtml.includes("Join Channel Question")) {
                  throw new Error("Client2 workspace should keep resolved tickets out of the active tickets panel.");
                }
                const renderedTicketRows = (readyHtml.match(/data-history-ticket-row=\\"true\\"/g) || []).length;
                if (renderedTicketRows !== 4) {
                  throw new Error(`Client2 workspace should render exactly four active ticket rows, got ${renderedTicketRows}.`);
                }
                if (readyHtml.includes("Recent Activity") || readyHtml.includes("Latest ticket movement")) {
                  throw new Error("Client2 workspace should replace the recent activity panel with service events.");
                }
              """
            )
        )

    def test_client2_workspace_service_events_loading_and_unavailable_states(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };

                state.serviceEvents = {
                  loadState: "loading",
                  items: [],
                  statusPageUrl: "https://status.agora.io/",
                  fetchedAt: null,
                  lastRequestedAtMs: 0,
                };

                const loadingHtml = renderChatHome();
                if (!loadingHtml.includes("Loading latest Agora service events...")) {
                  throw new Error("Client2 workspace should render the service events loading copy.");
                }

                state.serviceEvents = {
                  loadState: "error",
                  items: [],
                  statusPageUrl: "https://status.agora.io/",
                  fetchedAt: null,
                  lastRequestedAtMs: 0,
                };

                const unavailableHtml = renderChatHome();
                if (!unavailableHtml.includes("Service events are temporarily unavailable. Open Agora Status Page -> for the latest updates.")) {
                  throw new Error("Client2 workspace should render the service events fallback copy.");
                }
                if (!unavailableHtml.includes("Open Agora Status Page ->")) {
                  throw new Error("Client2 workspace should keep the status page action visible when events are unavailable.");
                }
              """
            )
        )

    def test_client2_workspace_service_events_link_uses_sentence_case_light_blue_style(self) -> None:
        css = Path("ui/client-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn(".clienttest-home-panel-link", css)
        self.assertIn("text-transform: none;", css)
        self.assertIn("font-weight: 700;", css)
        self.assertIn("color: #7faee6;", css)

    def test_client2_workspace_fetches_service_events_when_entering_workspace(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                window.location.hash = "#/chat";
                setupClientRealtimeConnection = () => {};
                renderAuthed = () => {
                  appRoot.innerHTML = renderMainContent();
                };
                syncChatScrollPosition = () => {};

                const calls = [];
                fetch = (url) => {
                  calls.push(url);
                  if (url === "/api/client/service-events") {
                    return Promise.resolve({
                      ok: true,
                      json: async () => ({
                        items: [
                          {
                            title: "RTC black screen issue",
                            summary: "A limited number of users experienced black screen behavior.",
                            link: "https://status.agora.io/events/44",
                            status_label: "Resolved",
                            posted_at_label: "Posted Feb 24, 2026 - 01:04 PM UTC",
                          },
                        ],
                        status_page_url: "https://status.agora.io/",
                        fetched_at: "2026-04-21T02:00:00.000Z",
                      }),
                    });
                  }
                  return Promise.resolve({
                    ok: true,
                    json: async () => ({ tickets: [] }),
                  });
                };

                render();
                await Promise.resolve();
                await Promise.resolve();

                if (!calls.includes("/api/client/service-events")) {
                  throw new Error("Client2 workspace should fetch service events through the backend proxy.");
                }
                if (state.serviceEvents.loadState !== "ready") {
                  throw new Error(`Client2 workspace should store the loaded service events payload, got ${state.serviceEvents.loadState}.`);
                }
                if (!appRoot.innerHTML.includes("RTC black screen issue")) {
                  throw new Error("Client2 workspace should render fetched service events once the backend payload resolves.");
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
                if (!html.includes("New ticket")) {
                  throw new Error("Client2 draft should render the fixed default ticket title.");
                }
                if (!html.includes("new-ticket-draft-inline-route")) {
                  throw new Error("Client2 draft should render the explicit inline-draft route marker.");
                }
                if (html.includes("clienttest-route-page-footer-band")) {
                  throw new Error("Client2 draft should not render the visible footer-band shell.");
                }
                if (html.includes("new-ticket-tail-route")) {
                  throw new Error("Client2 draft should not opt into the tail-composer route.");
                }
                if (html.includes("clienttest-route-footer-band")) {
                  throw new Error("Client2 draft should not render a tail footer band element.");
                }
                if (html.includes("new-ticket-tail-composer")) {
                  throw new Error("Client2 draft should not render the tail composer.");
                }
                if (!html.includes("new-ticket-draft-inline-composer")) {
                  throw new Error("Client2 draft should render an inline composer inside the left column.");
                }
                if (!html.includes("new-ticket-thread-footer-composer")) {
                  throw new Error("Client2 draft should reuse the shared communicating-case footer shell.");
                }
                if (!html.includes("new-ticket-postsend-composer") || !html.includes("new-ticket-postsend-inline-composer")) {
                  throw new Error("Client2 draft should reuse the full communicating-case footer class stack.");
                }
                if (html.indexOf("new-ticket-thread-panel") >= html.indexOf("new-ticket-draft-inline-composer")) {
                  throw new Error("Client2 draft inline composer should render after the draft thread panel.");
                }
                if (html.indexOf("new-ticket-main-column") >= html.indexOf("new-ticket-draft-inline-composer")) {
                  throw new Error("Client2 draft inline composer should stay inside the draft main column.");
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
                if (!html.includes("New ticket")) {
                  throw new Error("Client2 draft should expose the fixed New ticket default title copy.");
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
                if (html.includes("✅ Delivered")) {
                  throw new Error("Client2 draft should not render the delivered status without a persisted customer message.");
                }
                if (!html.includes(`<div class="new-ticket-info-value mono">${draft.id}</div>`)) {
                  throw new Error("Client2 draft should show the real ticket id in Ticket Information when the draft already has one.");
                }
                if (html.includes('<div class="new-ticket-info-value mono">Pending</div>')) {
                  throw new Error("Client2 draft should not show Pending when the draft ticket already has an id.");
                }
              """
            )
        )

    def test_client2_new_ticket_information_panel_falls_back_to_pending_without_ticket_id(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                const html = renderNewTicketInformationPanel({
                  id: "   ",
                  status: "draft",
                  messages: [],
                  createdAt: null,
                  updatedAt: null,
                });

                if (!html.includes('<div class="new-ticket-info-value mono">Pending</div>')) {
                  throw new Error("Client2 draft ticket information should fall back to Pending when no ticket id exists.");
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

                setComposerDraftFromRichHtml("<strong>Need</strong> help with RTC join flow");
                await handleSendMessage(state.inputDraft);

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
                if (requestBody.message !== "**Need** help with RTC join flow") {
                  throw new Error(`Client2 send flow should serialize the rich composer draft to markdown, got ${JSON.stringify(requestBody)}.`);
                }
                if (requestBody.content_format !== "markdown") {
                  throw new Error(`Client2 query should default customer composer submissions to markdown, got ${JSON.stringify(requestBody)}.`);
                }
                if (Object.prototype.hasOwnProperty.call(requestBody, "product")) {
                  throw new Error("Client2 draft first send should omit product when none is known.");
                }
                const pending = state.pendingByTicket[ticket.id];
                if (!pending || pending.phase !== "queued") {
                  throw new Error("Client2 first send should keep per-ticket queued pending state.");
                }
                const localTicket = getTicketById(ticket.id);
                if (!localTicket || localTicket.title !== "New ticket") {
                  throw new Error(`Client2 first send should keep the fixed New ticket title until backend sync, got ${localTicket && localTicket.title}.`);
                }
                if (localTicket.messages.length !== 1 || localTicket.messages[0].contentFormat !== "markdown") {
                  throw new Error("Client2 first send should keep the optimistic customer message marked as markdown.");
                }

                const html = renderChatTicket();
                if (!html.includes("new-ticket-postsend-shell")) {
                  throw new Error("Client2 first send should switch into the correspondence shell.");
                }
                if (!html.includes("clienttest-route-page")) {
                  throw new Error("Client2 post-send shell should use the shared route page shell.");
                }
                if (html.includes("clienttest-route-page-footer-band")) {
                  throw new Error("Client2 first-send postsend shell should exit preview mode as soon as the ticket has messages.");
                }
                if (html.includes("new-ticket-tail-route")) {
                  throw new Error("Client2 first-send postsend shell should not keep the tail-composer route after the first persisted message.");
                }
                if (html.includes("clienttest-route-footer-band")) {
                  throw new Error("Client2 first-send postsend shell should not keep the in-flow footer band after leaving preview mode.");
                }
                if (html.includes("new-ticket-tail-composer")) {
                  throw new Error("Client2 first-send postsend shell should not keep the tail composer after the first persisted message.");
                }
                if (!html.includes("new-ticket-thread-footer-composer")) {
                  throw new Error("Client2 first-send postsend shell should switch to the communicating inline footer.");
                }
                if (html.includes("clienttest-route-page-fixed-footer")) {
                  throw new Error("Client2 post-send shell should not use the removed fixed-footer reserve contract.");
                }
                if (!html.includes(`My Tickets / Ticket #${ticket.id}`)) {
                  throw new Error("Client2 post-send shell should restore the breadcrumb header.");
                }
                if (!html.includes("New ticket")) {
                  throw new Error("Client2 first-send postsend shell should still show the fixed New ticket title before backend sync.");
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
                const deliveredMatches = html.match(/✅ Delivered/g) || [];
                if (deliveredMatches.length !== 0) {
                  throw new Error(`Client2 first-send postsend shell should delay the delivered label for a fresh customer message, got ${deliveredMatches.length}.`);
                }
              """
            )
        )

    def test_client2_rich_composer_serializes_supported_subset_and_escapes_literal_markdown(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                const serializedBold = serializeRichComposerHtmlToMarkdown("<strong>Need</strong> help");
                if (serializedBold !== "**Need** help") {
                  throw new Error(`Rich composer should serialize strong tags to markdown, got ${serializedBold}.`);
                }

                const serializedLiteral = serializeRichComposerHtmlToMarkdown("**literal** [Docs](https://example.com)");
                if (serializedLiteral !== "\\\\*\\\\*literal\\\\*\\\\* \\\\[Docs\\\\](https://example.com)") {
                  throw new Error(`Literal markdown characters should stay escaped when typed as plain text, got ${serializedLiteral}.`);
                }

                const serializedList = serializeRichComposerHtmlToMarkdown("<ul><li>Alpha</li><li>Beta</li></ul>");
                if (serializedList !== "- Alpha\\n- Beta") {
                  throw new Error(`Rich composer should serialize unordered lists, got ${JSON.stringify(serializedList)}.`);
                }

                const serializedCode = serializeRichComposerHtmlToMarkdown('<pre><code class="language-js">const answer = 42;</code></pre>');
                if (serializedCode !== "```js\\nconst answer = 42;\\n```") {
                  throw new Error(`Rich composer should serialize fenced code blocks, got ${JSON.stringify(serializedCode)}.`);
                }

                const hydrated = buildRichComposerHtmlFromMarkdown(
                  "**Bold** _italic_ [Docs](https://example.com)\\n- Item\\n```js\\nconst answer = 42;\\n```"
                );
                if (!hydrated.includes("<strong>Bold</strong>")) {
                  throw new Error(`Hydration should recreate bold formatting, got ${hydrated}.`);
                }
                if (!hydrated.includes("<em>italic</em>")) {
                  throw new Error("Hydration should recreate italic formatting.");
                }
                if (!hydrated.includes('href="https://example.com/"')) {
                  throw new Error("Hydration should recreate safe links.");
                }
                if (!hydrated.includes("<ul><li>Item</li></ul>")) {
                  throw new Error("Hydration should recreate unordered lists.");
                }
                if (!hydrated.includes('<pre><code class="language-js">const answer = 42;</code></pre>')) {
                  throw new Error("Hydration should recreate fenced code blocks.");
                }
              """
            )
        )

    def test_client2_markdown_toolbar_attach_shows_placeholder_toast(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                const notices = [];
                toast = (message) => notices.push(message);
                const handled = handleComposerToolbarAction("attach", null);
                if (handled !== false) {
                  throw new Error("Attach placeholder should not claim it edited the composer text.");
                }
                if (notices.length !== 1 || notices[0] !== "Attachments are not available yet.") {
                  throw new Error(`Attach placeholder should show the not-yet-available toast, got ${JSON.stringify(notices)}.`);
                }
              """
            )
        )

    def test_client2_rich_composer_renders_on_both_composer_surfaces(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const draftTicket = getOrCreateDraftTicket(state.user.id);
                state.view = "chat-ticket";
                state.activeTicketId = draftTicket.id;
                state.newTicketPreviewTicketId = draftTicket.id;
                const draftHtml = renderChatTicket();
                if ((draftHtml.match(/data-composer-markdown-action=/g) || []).length < 6) {
                  throw new Error("Draft composer should render the full markdown toolbar.");
                }
                if (!draftHtml.includes('data-composer-markdown-action="code-block"')) {
                  throw new Error("Draft composer should expose the code block button.");
                }
                if (!draftHtml.includes('contenteditable="true"')) {
                  throw new Error("Draft composer should render a rich contenteditable editor.");
                }
                if (draftHtml.includes("<textarea")) {
                  throw new Error("Draft composer should no longer render the legacy textarea.");
                }
                if (!draftHtml.includes("new-ticket-summary-toolbar-btn")) {
                  throw new Error("Draft composer should preserve the AI Summary button.");
                }

                const detailHtml = renderChatTicketFromState({
                  ticket: {
                    id: "TK-DETAIL-001",
                    title: "Legacy detail shell",
                    status: "communicating",
                    updatedAt: "2026-04-21T10:00:00.000Z",
                    product: "audio_video_calling",
                  },
                  renderableMessages: [],
                  sending: false,
                  requiresProductSelection: false,
                  canCompose: true,
                  canSubmit: false,
                  usesNewTicketShell: false,
                  showVisibleFooterBand: false,
                  isEditing: false,
                });
                if (!detailHtml.includes("ticket-detail-toolbar")) {
                  throw new Error("Ticket detail shell should keep the existing info chips.");
                }
                if (!detailHtml.includes("ticket-detail-composer-format-toolbar")) {
                  throw new Error("Ticket detail shell should add the markdown formatting toolbar.");
                }
                if (!detailHtml.includes('data-composer-markdown-action="code-block"')) {
                  throw new Error("Ticket detail shell should expose the code block button.");
                }
                if (!detailHtml.includes('contenteditable="true"')) {
                  throw new Error("Ticket detail shell should render a rich contenteditable editor.");
                }
                if (detailHtml.includes("<textarea")) {
                  throw new Error("Ticket detail shell should no longer render the legacy textarea.");
                }
                if (detailHtml.includes("new-ticket-summary-toolbar-btn")) {
                  throw new Error("Ticket detail shell should not add the AI Summary pill to the formatting toolbar.");
                }
              """
            )
        )

    def test_client2_rich_composer_toolbar_state_and_link_editor_render(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.composerToolbarState = {
                  bold: true,
                  italic: false,
                  list: true,
                  link: false,
                  codeBlock: false,
                };
                state.composerLinkEditor = {
                  open: true,
                  url: "https://example.com",
                  selectedText: "Docs",
                  selectionBookmark: null,
                };
                const draftToolbar = renderNewTicketComposerToolbar({ canCompose: true, includeSummary: true });
                if (!draftToolbar.includes('data-composer-markdown-action="bold"') || !draftToolbar.includes("is-active")) {
                  throw new Error(`Toolbar should render the active state for selected formatting, got ${draftToolbar}.`);
                }
                const linkEditor = renderComposerLinkEditor({ canCompose: true });
                if (!linkEditor.includes("composer-link-editor")) {
                  throw new Error("Rich composer should render the inline link editor when requested.");
                }
                if (!linkEditor.includes('value="https://example.com"')) {
                  throw new Error("Inline link editor should keep the current URL draft.");
                }
              """
            )
        )

    def test_client2_customer_markdown_messages_render_safe_subset_only_when_marked(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                const markdownMessage = {
                  role: "user",
                  content:
                    "**Bold** _italic_ [Docs](https://example.com)\\n- First item\\n```js\\nconst answer = 42;\\n```",
                  contentFormat: "markdown",
                };
                const markdownHtml = renderMessageBody(markdownMessage);
                if (!markdownHtml.includes("<strong>Bold</strong>")) {
                  throw new Error(`Customer markdown should render bold text, got ${markdownHtml}.`);
                }
                if (!markdownHtml.includes("<em>italic</em>")) {
                  throw new Error("Customer markdown should render italic text.");
                }
                if (!markdownHtml.includes('<a href="https://example.com/" target="_blank" rel="noopener noreferrer">Docs</a>')) {
                  throw new Error(`Customer markdown should render safe links, got ${markdownHtml}.`);
                }
                if (!markdownHtml.includes("<ul><li>First item</li></ul>")) {
                  throw new Error("Customer markdown should render unordered lists.");
                }
                if (!markdownHtml.includes('<pre><code class="language-js">const answer = 42;</code></pre>')) {
                  throw new Error("Customer markdown should render fenced code blocks.");
                }

                const correspondenceHtml = renderNewTicketMessageContent(markdownMessage);
                if (!correspondenceHtml.includes("<strong>Bold</strong>") || !correspondenceHtml.includes("<pre><code class=\\"language-js\\">const answer = 42;</code></pre>")) {
                  throw new Error("New-ticket correspondence bubbles should render customer markdown.");
                }

                const unsafeLinkHtml = renderMessageBody({
                  role: "user",
                  content: "[Bad](javascript:alert(1))",
                  content_format: "markdown",
                });
                if (unsafeLinkHtml.includes("<a ")) {
                  throw new Error("Unsafe markdown links should stay as plain text.");
                }
                if (!unsafeLinkHtml.includes("[Bad](javascript:alert(1))")) {
                  throw new Error("Unsafe markdown links should preserve the original text.");
                }

                const escapedLiteralMarkdownHtml = renderMessageBody({
                  role: "user",
                  content: "\\\\*\\\\*literal\\\\*\\\\* \\\\[Docs\\\\](https://example.com) \\\\- item",
                  content_format: "markdown",
                });
                if (escapedLiteralMarkdownHtml.includes("<strong>") || escapedLiteralMarkdownHtml.includes("<a ")) {
                  throw new Error("Escaped markdown syntax should stay literal in customer markdown messages.");
                }
                if (!escapedLiteralMarkdownHtml.includes("**literal** [Docs](https://example.com) - item")) {
                  throw new Error(`Escaped markdown syntax should render without the escape backslashes, got ${escapedLiteralMarkdownHtml}.`);
                }

                const plaintextHtml = renderMessageBody({
                  role: "user",
                  content: "**Still plain**",
                });
                if (plaintextHtml.includes("<strong>")) {
                  throw new Error("Historical customer plaintext messages must not be retroactively parsed as markdown.");
                }
                if (!plaintextHtml.includes("**Still plain**")) {
                  throw new Error("Historical customer plaintext messages should preserve their literal markdown characters.");
                }

                const normalized = normalizeBackendTicket({
                  ticket_id: "TK-MD-001",
                  customer_id: "user-1",
                  status: "communicating",
                  created_at: "2026-04-21T09:00:00.000Z",
                  updated_at: "2026-04-21T09:00:00.000Z",
                  messages: [
                    {
                      role: "customer",
                      content: "**Backend markdown**",
                      created_at: "2026-04-21T09:00:00.000Z",
                      content_format: "markdown",
                    },
                  ],
                });
                if (normalized.messages[0].contentFormat !== "markdown") {
                  throw new Error("Backend ticket normalization should preserve customer message content_format.");
                }
              """
            )
        )

    def test_client2_fresh_customer_message_schedules_delivered_label_after_5_seconds(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                const baseNow = Date.parse("2026-04-20T09:00:00.000Z");
                Date.now = () => baseNow;
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                const timers = [];
                setTimeout = (fn, delay) => {
                  timers.push({ fn, delay });
                  return timers.length;
                };

                localStorage.setItem(
                  "helpdesk_tickets",
                  JSON.stringify([
                    {
                      id: "TK-DELIVERED-001",
                      title: "Need help joining a channel",
                      status: "communicating",
                      createdAt: "2026-04-20T08:59:58.000Z",
                      updatedAt: "2026-04-20T09:00:00.000Z",
                      userId: state.user.id,
                      product: "audio_video_calling",
                      messages: [
                        {
                          id: "msg-user-1",
                          role: "user",
                          content: "How do I join a channel?",
                          createdAt: "2026-04-20T09:00:00.000Z",
                        },
                      ],
                    },
                  ])
                );

                state.view = "chat-ticket";
                state.activeTicketId = "TK-DELIVERED-001";
                const html = renderChatTicket();
                if (html.includes("✅ Delivered")) {
                  throw new Error("Client2 should not render delivered for a message that is less than five seconds old.");
                }
                if (timers.length !== 1) {
                  throw new Error(`Client2 should schedule one delivered refresh timer, got ${timers.length}.`);
                }
                if (timers[0].delay !== 5000) {
                  throw new Error(`Client2 should wait 5000ms before showing delivered, got ${timers[0].delay}.`);
                }
              """
            )
        )

    def test_client2_customer_message_shows_delivered_after_5_seconds(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                const baseNow = Date.parse("2026-04-20T09:00:05.000Z");
                Date.now = () => baseNow;
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };

                localStorage.setItem(
                  "helpdesk_tickets",
                  JSON.stringify([
                    {
                      id: "TK-DELIVERED-002",
                      title: "Need help joining a channel",
                      status: "communicating",
                      createdAt: "2026-04-20T08:59:58.000Z",
                      updatedAt: "2026-04-20T09:00:05.000Z",
                      userId: state.user.id,
                      product: "audio_video_calling",
                      messages: [
                        {
                          id: "msg-user-1",
                          role: "user",
                          content: "How do I join a channel?",
                          createdAt: "2026-04-20T09:00:00.000Z",
                        },
                      ],
                    },
                  ])
                );

                state.view = "chat-ticket";
                state.activeTicketId = "TK-DELIVERED-002";
                const html = renderChatTicket();
                const deliveredMatches = html.match(/✅ Delivered/g) || [];
                if (deliveredMatches.length !== 1) {
                  throw new Error(`Client2 should show delivered once the customer message is five seconds old, got ${deliveredMatches.length}.`);
                }
              """
            )
        )

    def test_client2_legacy_new_session_draft_is_still_reusable(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem(
                  "helpdesk_tickets",
                  JSON.stringify([
                    {
                      id: "TK-LEGACY-001",
                      title: "New Session",
                      status: "open",
                      createdAt: "2026-04-17T08:00:00.000Z",
                      updatedAt: "2026-04-17T08:00:00.000Z",
                      userId: state.user.id,
                      product: null,
                      messages: [],
                    },
                  ])
                );

                const draft = getOrCreateDraftTicket(state.user.id);
                if (draft.id !== "TK-LEGACY-001") {
                  throw new Error(`Client2 should still reuse legacy New Session drafts, got ${draft.id}.`);
                }
                if (!isReusableDraftTicket(draft, state.user.id)) {
                  throw new Error("Client2 should still treat legacy New Session drafts as reusable defaults.");
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
                if (html.includes("new-ticket-tail-route")) {
                  throw new Error("Existing client2 tickets should not opt into the draft/preview tail-composer route.");
                }
                if (html.includes("clienttest-route-footer-band")) {
                  throw new Error("Existing client2 tickets should not render the new in-flow footer band element.");
                }
                if (html.includes("new-ticket-tail-composer")) {
                  throw new Error("Existing client2 tickets should keep the current detail-style composer placement.");
                }
                if (!html.includes("new-ticket-thread-footer-composer")) {
                  throw new Error("Existing client2 tickets should continue exposing the shared communicating-case footer shell.");
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
                const deliveredMatches = html.match(/✅ Delivered/g) || [];
                if (deliveredMatches.length !== 1) {
                  throw new Error(`Existing client2 tickets should render one delivered label for the customer message only, got ${deliveredMatches.length}.`);
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

    def test_client2_postsend_header_reply_countdown_uses_waiting_status_durations(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                const baseNow = Date.parse("2026-04-20T09:00:00.000Z");
                Date.now = () => baseNow;
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                function renderWaitingTicket(status, messageCreatedAt) {
                  const ticket = createTicket(state.user.id);
                  updateTicketTitle(ticket.id, `${status} case`);
                  updateTicketStatus(ticket.id, status);
                  saveTicketMessages(ticket.id, [
                    {
                      id: `${ticket.id}-msg-user`,
                      role: "user",
                      content: "Please check my issue.",
                      createdAt: messageCreatedAt,
                    },
                  ]);
                  state.view = "chat-ticket";
                  state.activeTicketId = ticket.id;
                  return renderChatTicket();
                }

                const communicatingHtml = renderWaitingTicket("communicating", "2026-04-20T09:00:00.000Z");
                if (!communicatingHtml.includes("new-ticket-postsend-countdown")) {
                  throw new Error("Client2 communicating tickets should render the reply countdown chip.");
                }
                if (!communicatingHtml.includes(">Next update in 10:00<")) {
                  throw new Error("Client2 communicating tickets should start the reply countdown at Next update in 10:00.");
                }

                const investigatingHtml = renderWaitingTicket("investigating", "2026-04-20T09:00:00.000Z");
                if (!investigatingHtml.includes(">Next update in 1h 00m<")) {
                  throw new Error("Client2 investigating tickets should start the reply countdown at Next update in 1h 00m.");
                }

                const escalatedHtml = renderWaitingTicket("escalated", "2026-04-20T09:00:00.000Z");
                if (!escalatedHtml.includes(">Next update in 3h 00m<")) {
                  throw new Error("Client2 escalated tickets should start the reply countdown at Next update in 3h 00m.");
                }
                if (escalatedHtml.includes("Estimate waiting time: 3 hours")) {
                  throw new Error("Client2 escalated tickets should remove the legacy header wait note when the countdown chip is present.");
                }
              """
            )
        )

    def test_client2_postsend_header_reply_countdown_hides_when_latest_message_is_assistant(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                const baseNow = Date.parse("2026-04-20T09:00:00.000Z");
                Date.now = () => baseNow;
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                updateTicketTitle(ticket.id, "Assistant already replied");
                updateTicketStatus(ticket.id, "communicating");
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-user-1",
                    role: "user",
                    content: "How do I join a channel?",
                    createdAt: "2026-04-20T08:58:00.000Z",
                  },
                  {
                    id: "msg-agent-1",
                    role: "assistant",
                    content: "Use joinChannel with a valid token and channel name.",
                    createdAt: "2026-04-20T08:59:00.000Z",
                  },
                ]);

                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;
                const html = renderChatTicket();
                if (html.includes("new-ticket-postsend-countdown")) {
                  throw new Error("Client2 should hide the reply countdown after the latest visible assistant message.");
                }
              """
            )
        )

    def test_client2_postsend_header_reply_countdown_refreshes_on_minute_boundaries(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                let now = Date.parse("2026-04-20T09:00:00.000Z");
                Date.now = () => now;
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                const intervals = [];
                setInterval = (fn, delay) => {
                  intervals.push({ fn, delay });
                  return intervals.length;
                };
                clearInterval = () => {};

                localStorage.setItem(
                  "helpdesk_tickets",
                  JSON.stringify([
                    {
                      id: "TK-COUNTDOWN-001",
                      title: "Waiting on support reply",
                      status: "communicating",
                      createdAt: "2026-04-20T08:59:30.000Z",
                      updatedAt: "2026-04-20T09:00:00.000Z",
                      userId: state.user.id,
                      product: "audio_video_calling",
                      messages: [
                        {
                          id: "msg-user-1",
                          role: "user",
                          content: "I got black screen, what should I do?",
                          createdAt: "2026-04-20T09:00:00.000Z",
                        },
                      ],
                    },
                  ])
                );

                state.view = "chat-ticket";
                state.activeTicketId = "TK-COUNTDOWN-001";
                render = () => {
                  appRoot.innerHTML = renderChatTicket();
                };
                render();
                if (!appRoot.innerHTML.includes(">Next update in 10:00<")) {
                  throw new Error("Client2 should render the full initial reply countdown before any minute passes.");
                }
                if (intervals.length !== 1) {
                  throw new Error(`Client2 should schedule one countdown refresh interval, got ${intervals.length}.`);
                }
                if (intervals[0].delay !== 60000) {
                  throw new Error(`Client2 should refresh the reply countdown every minute, got ${intervals[0].delay}.`);
                }

                now = Date.parse("2026-04-20T09:01:00.000Z");
                intervals[0].fn();
                if (!appRoot.innerHTML.includes(">Next update in 09:00<")) {
                  throw new Error("Client2 should decrement the reply countdown after one minute.");
                }
              """
            )
        )

    def test_client2_non_detail_shells_do_not_render_reply_countdown(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };

                const draftTicket = createTicket(state.user.id);
                state.view = "chat-ticket";
                state.activeTicketId = draftTicket.id;
                const draftHtml = renderChatTicket();
                if (draftHtml.includes("new-ticket-postsend-countdown")) {
                  throw new Error("Client2 draft shells should not render the reply countdown.");
                }

                state.view = "tickets";
                const ticketsHtml = renderTicketsPage();
                if (ticketsHtml.includes("new-ticket-postsend-countdown")) {
                  throw new Error("Client2 tickets page should not render the reply countdown.");
                }
              """
            )
        )

    def test_client2_non_empty_ticket_exits_preview_mode_even_if_preview_id_lingers(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                updateTicketTitle(ticket.id, "Ban User Privileges API behavior mismatch");
                updateTicketStatus(ticket.id, "communicating");
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-1",
                    role: "user",
                    content: "channel name: zilingtest, uid 2",
                    createdAt: "2026-04-17T10:39:00.000Z",
                  },
                  {
                    id: "msg-2",
                    role: "assistant",
                    content: "Please share the issue timestamp.",
                    createdAt: "2026-04-17T10:40:00.000Z",
                  },
                ]);

                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;
                state.newTicketPreviewTicketId = ticket.id;

                const html = renderChatTicket();
                if (html.includes("new-ticket-tail-route")) {
                  throw new Error("Non-empty tickets must exit preview mode even if preview id lingers.");
                }
                if (html.includes("clienttest-route-page-footer-band")) {
                  throw new Error("Non-empty tickets should not keep the preview footer-band shell.");
                }
                if (html.includes("new-ticket-tail-composer")) {
                  throw new Error("Non-empty tickets should not render the tail composer once real messages exist.");
                }
                if (!html.includes("new-ticket-thread-footer-composer")) {
                  throw new Error("Non-empty tickets should fall back to the communicating inline composer.");
                }
                if (String(state.newTicketPreviewTicketId || "").trim() !== "") {
                  throw new Error("Non-empty tickets should clear stale preview ids while building the chat view.");
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
                if (!html.includes("clienttest-route-footer-band")) {
                  throw new Error("Client2 tickets page should render a real in-flow footer band element.");
                }
                if (!html.includes("tickets-status-filter")) {
                  throw new Error("Client2 tickets page should render the My Tickets specific status filter marker.");
                }
                if (html.includes("clienttest-route-footer-shell-communicating")) {
                  throw new Error("Client2 tickets page should remove the communicating-style footer shell and keep only the bottom blank.");
                }
                if (html.includes("new-ticket-composer-toolbar") || html.includes("new-ticket-inline-send-btn") || html.includes("id=\\"chat-input\\"")) {
                  throw new Error("Client2 tickets footer band should stay non-interactive.");
                }
                if (html.includes("clienttest-route-scroll-region")) {
                  throw new Error("Client2 tickets page should no longer use the scoped internal scroll region.");
                }
                if (!html.includes("My Tickets")) {
                  throw new Error("Client2 tickets page should keep the tickets heading.");
                }
              """
            )
        )
