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
        self.assertIn('SHARED_UI_DIR = UI_DIR / "shared-ui"', main_source)
        self.assertNotIn('CLIENT2_DIR = UI_DIR / "client2-ui"', main_source)
        self.assertNotIn('CLIENTTEST_DIR = UI_DIR / "clienttest-ui"', main_source)
        self.assertNotIn('app.mount("/client2", StaticFiles(directory=CLIENT2_DIR, html=True), name="client2-ui")', main_source)
        self.assertNotIn('app.mount("/clienttest", StaticFiles(directory=CLIENTTEST_DIR, html=True), name="clienttest-ui")', main_source)
        self.assertIn('app.mount("/client", StaticFiles(directory=CLIENT_DIR, html=True), name="client-ui")', main_source)
        self.assertIn('app.mount("/shared-ui", StaticFiles(directory=SHARED_UI_DIR), name="shared-ui")', main_source)

        expected_files = [
            Path("ui/client-ui/index.html"),
            Path("ui/client-ui/styles.css"),
            Path("ui/client-ui/app.js"),
            Path("ui/shared-ui/composer.css"),
            Path("ui/shared-ui/composer.js"),
            Path("ui/archive/client-ui-legacy/index.html"),
            Path("ui/archive/clienttest-ui-legacy/index.html"),
        ]
        for file_path in expected_files:
            self.assertTrue(file_path.exists(), str(file_path))

    def test_client_html_references_promoted_client2_assets(self) -> None:
        html = Path("ui/client-ui/index.html").read_text(encoding="utf-8")

        self.assertIn("<title>Support Portal</title>", html)
        self.assertIn('/shared-ui/composer.css?v=20260423-shared-rich-composer-rollout-1', html)
        self.assertIn('/shared-ui/composer.js?v=20260423-shared-rich-composer-rollout-1', html)
        self.assertIn("./styles.css?v=20260423-shared-rich-composer-rollout-1", html)
        self.assertIn("./app.js?v=20260423-shared-rich-composer-rollout-1", html)


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
            const sharedComposerPath = "ui/shared-ui/composer.js";

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
            if (fs.existsSync(sharedComposerPath)) {{
              const sharedSource = fs.readFileSync(sharedComposerPath, "utf8");
              vm.runInContext(sharedSource, sandbox);
            }}
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
        self.assertNotIn("chat-new-messages", app_source)
        self.assertNotIn("new-messages-btn", app_source)
        self.assertNotIn("jump-chat-latest", app_source)
        self.assertNotIn("chat-new-messages", css)
        self.assertNotIn("new-messages-btn", css)

    def test_client2_uses_shared_composer_bundle_contract(self) -> None:
        app_source = Path("ui/client-ui/app.js").read_text(encoding="utf-8")
        shared_source = Path("ui/shared-ui/composer.js").read_text(encoding="utf-8")

        self.assertIn("globalThis.SupportPortalComposer", app_source)
        self.assertIn("renderMarkdownMessage", shared_source)
        self.assertIn("buildDefaultComposerToolbarState", shared_source)
        self.assertIn("serializeRichComposerHtmlToMarkdown", shared_source)
        self.assertNotIn("function renderMarkdownMessage(", app_source)
        self.assertNotIn("function buildDefaultComposerToolbarState(", app_source)
        self.assertNotIn("function serializeRichComposerHtmlToMarkdown(", app_source)

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

    def test_client2_chat_scrolled_up_auto_scrolls_smoothly_for_new_message(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                const queuedFrames = [];
                globalThis.requestAnimationFrame = (callback) => {
                  queuedFrames.push(callback);
                  return queuedFrames.length;
                };

                const flushFrames = () => {
                  while (queuedFrames.length > 0) {
                    const callback = queuedFrames.shift();
                    callback();
                  }
                };

                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem(
                  "helpdesk_tickets",
                  JSON.stringify([
                    {
                      id: "TK-SCROLL-001",
                      title: "Need help joining a channel",
                      status: "communicating",
                      createdAt: "2026-04-21T09:00:00.000Z",
                      updatedAt: "2026-04-21T09:01:00.000Z",
                      userId: state.user.id,
                      product: "audio_video_calling",
                      messages: [
                        {
                          id: "msg-user-1",
                          role: "user",
                          content: "How do I join a channel?",
                          createdAt: "2026-04-21T09:00:00.000Z",
                        },
                      ],
                    },
                  ])
                );

                window.location.hash = "#/chat/TK-SCROLL-001";
                const inputForm = {
                  addEventListener() {},
                  requestSubmit() {},
                };
                const chatInput = {
                  addEventListener() {},
                  value: "",
                  disabled: false,
                  focus() {},
                };
                const linkInput = {
                  addEventListener() {},
                  value: "",
                  focus() {},
                  select() {},
                };
                document.getElementById = (id) => {
                  if (id === "app") {
                    return appRoot;
                  }
                  if (id === "toast-root") {
                    return toastRoot;
                  }
                  if (id === "chat-input-form") {
                    return inputForm;
                  }
                  if (id === "chat-input") {
                    return chatInput;
                  }
                  if (id === "composer-link-url") {
                    return linkInput;
                  }
                  return null;
                };

                let currentChatMain = null;
                let topbarRegion = { innerHTML: "" };
                let contextRegion = { innerHTML: "" };
                let mainRegion = null;
                let sidebarNavRegion = { innerHTML: "" };
                let sidebarContentRegion = { innerHTML: "" };
                let sidebarFooterRegion = { innerHTML: "" };
                let shellRoot = null;
                const chatScrollCalls = [];
                const renderHeights = [];

                appRoot.querySelector = (selector) => {
                  if (selector === ".chat-main") {
                    return currentChatMain;
                  }
                  if (selector === ".app-shell") {
                    return shellRoot;
                  }
                  return null;
                };

                Object.defineProperty(appRoot, "innerHTML", {
                  configurable: true,
                  get() {
                    return this._html || "";
                  },
                  set(value) {
                    this._html = value;
                    mainRegion = { classList: { toggle() {} } };
                    Object.defineProperty(mainRegion, "innerHTML", {
                      configurable: true,
                      get() {
                        return this._html || "";
                      },
                      set(regionHtml) {
                        this._html = regionHtml;
                        currentChatMain = {
                          scrollTop: currentChatMain?.scrollTop || 0,
                          scrollHeight: renderHeights.shift() ?? 0,
                          clientHeight: 180,
                          scrollTo(options) {
                            chatScrollCalls.push(options);
                            this.scrollTop = typeof options?.top === "number" ? options.top : this.scrollTop;
                          },
                        };
                      },
                    });
                    shellRoot = {
                      classList: { toggle() {} },
                      querySelector(selector) {
                        if (selector === ".clienttest-workspace") {
                          return { classList: { toggle() {} } };
                        }
                        if (selector === '[data-authed-region="sidebar-nav"]') {
                          return sidebarNavRegion;
                        }
                        if (selector === '[data-authed-region="sidebar-content"]') {
                          return sidebarContentRegion;
                        }
                        if (selector === '[data-authed-region="sidebar-footer"]') {
                          return sidebarFooterRegion;
                        }
                        if (selector === '[data-authed-region="topbar"]') {
                          return topbarRegion;
                        }
                        if (selector === '[data-authed-region="context"]') {
                          return contextRegion;
                        }
                        if (selector === '[data-authed-region="main"]') {
                          return mainRegion;
                        }
                        return null;
                      },
                    };
                  },
                });

                renderHeights.push(420);
                render();
                flushFrames();

                currentChatMain.scrollTop = 20;
                saveTicketMessages("TK-SCROLL-001", [
                  {
                    id: "msg-user-1",
                    role: "user",
                    content: "How do I join a channel?",
                    createdAt: "2026-04-21T09:00:00.000Z",
                  },
                  {
                    id: "msg-agent-1",
                    role: "assistant",
                    content: "Use joinChannel with a valid token.",
                    createdAt: "2026-04-21T09:01:00.000Z",
                  },
                ]);

                renderHeights.push(520);
                render();
                flushFrames();

                const latestCall = chatScrollCalls[chatScrollCalls.length - 1];
                if (!latestCall || latestCall.top !== 520) {
                  throw new Error(`Expected the client chat rerender to smooth-scroll to 520, got ${JSON.stringify(latestCall)}.`);
                }
                if (latestCall.behavior !== "smooth") {
                  throw new Error(`Expected the client chat rerender to smooth-scroll to bottom, got ${JSON.stringify(latestCall)}.`);
                }
                if (currentChatMain.scrollTop !== 520) {
                  throw new Error(`Expected chat scrollTop 520 after auto-scroll, got ${currentChatMain.scrollTop}.`);
                }
                if (appRoot.innerHTML.includes("New messages")) {
                  throw new Error("Client chat should not expose a New messages indicator after removing unread CTA logic.");
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

                const setTicketUpdatedAt = (ticketId, updatedAt) => {
                  const tickets = getAllTickets();
                  const target = tickets.find((ticket) => ticket.id === ticketId);
                  if (!target) {
                    throw new Error(`Missing ticket ${ticketId} while preparing workspace test data.`);
                  }
                  target.updatedAt = updatedAt;
                  saveAllTickets(tickets);
                };

                setTicketUpdatedAt(active.id, "2026-04-17T10:00:00.000Z");
                setTicketUpdatedAt(followUp.id, "2026-04-17T10:30:00.000Z");
                setTicketUpdatedAt(escalated.id, "2026-04-17T10:45:00.000Z");
                setTicketUpdatedAt(followUpTwo.id, "2026-04-17T10:50:00.000Z");
                setTicketUpdatedAt(oldestActive.id, "2026-04-17T09:55:00.000Z");
                setTicketUpdatedAt(resolved.id, "2026-04-17T11:00:00.000Z");

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
                if (!readyHtml.includes("view all tickets->")) {
                  throw new Error("Client2 workspace should render the active tickets footer CTA.");
                }
                if (!readyHtml.includes('data-action="go-tickets-top"')) {
                  throw new Error("Client2 workspace should render the dedicated go-tickets-top action for the footer CTA.");
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

    def test_client2_workspace_active_tickets_footer_cta_persists_in_empty_state(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const html = renderChatHome();
                if (!html.includes("No active tickets yet. Start a new one to open the redesigned detail view.")) {
                  throw new Error("Client2 workspace should keep the active tickets empty-state copy.");
                }
                if (!html.includes("view all tickets->")) {
                  throw new Error("Client2 workspace should keep the footer CTA even when there are no active tickets.");
                }
                if (!html.includes('data-action="go-tickets-top"')) {
                  throw new Error("Client2 workspace empty state should keep the footer CTA wired to go-tickets-top.");
                }
              """
            )
        )

    def test_client2_workspace_footer_cta_navigates_to_tickets_and_resets_page_scroll(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                setupClientRealtimeConnection = () => {};
                syncChatScrollPosition = () => {};

                const createClassList = () => {
                  const values = new Set();
                  return {
                    toggle(name, force) {
                      if (force === undefined) {
                        if (values.has(name)) {
                          values.delete(name);
                          return false;
                        }
                        values.add(name);
                        return true;
                      }
                      if (force) {
                        values.add(name);
                        return true;
                      }
                      values.delete(name);
                      return false;
                    },
                    contains(name) {
                      return values.has(name);
                    },
                  };
                };

                const workspace = { classList: createClassList() };
                const topbarRegion = { innerHTML: "" };
                const contextRegion = { innerHTML: "" };
                const sidebarRegion = { innerHTML: "" };
                const sidebarContent = { innerHTML: "" };
                const sidebarFooter = { innerHTML: "" };
                const mainRegion = {
                  classList: createClassList(),
                  scrollTop: 240,
                  _html: "",
                  _ticketsRoot: null,
                  set innerHTML(value) {
                    this._html = String(value || "");
                    this._ticketsRoot = this._html.includes("tickets-root")
                      ? { scrollTop: 180 }
                      : null;
                  },
                  get innerHTML() {
                    return this._html;
                  },
                  querySelector(selector) {
                    if (selector === ".tickets-root") {
                      return this._ticketsRoot;
                    }
                    return null;
                  },
                };
                const shell = {
                  querySelector(selector) {
                    switch (selector) {
                      case ".clienttest-workspace":
                        return workspace;
                      case '[data-authed-region="topbar"]':
                        return topbarRegion;
                      case '[data-authed-region="context"]':
                        return contextRegion;
                      case '[data-authed-region="main"]':
                        return mainRegion;
                      case '[data-authed-region="sidebar-nav"]':
                        return sidebarRegion;
                      case '[data-authed-region="sidebar-content"]':
                        return sidebarContent;
                      case '[data-authed-region="sidebar-footer"]':
                        return sidebarFooter;
                      default:
                        return null;
                    }
                  },
                };
                ensureAuthedShell = () => shell;
                bindAuthedEvents = () => {};

                const scrollCalls = [];
                window.scrollTo = (options) => {
                  scrollCalls.push(options);
                };
                document.documentElement = { scrollTop: 140 };
                document.body = { scrollTop: 80 };

                window.location.hash = "#/chat";
                navigateToTicketsTop();
                if (window.location.hash !== "#/tickets") {
                  throw new Error(`Footer CTA should navigate to #/tickets, got ${window.location.hash}.`);
                }

                render();

                if (!mainRegion.innerHTML.includes("My Tickets")) {
                  throw new Error("Footer CTA should land on the My Tickets page.");
                }
                if (mainRegion.scrollTop !== 0) {
                  throw new Error(`Footer CTA should reset the clienttest-main scroll container, got ${mainRegion.scrollTop}.`);
                }
                const ticketsRoot = mainRegion.querySelector(".tickets-root");
                if (!ticketsRoot || ticketsRoot.scrollTop !== 0) {
                  throw new Error(`Footer CTA should reset the tickets-root scroll container, got ${ticketsRoot?.scrollTop}.`);
                }
                if (scrollCalls.length < 1 || scrollCalls[0]?.top !== 0 || scrollCalls[0]?.behavior !== "auto") {
                  throw new Error("Footer CTA should keep the window scroll fallback available.");
                }
                if (document.documentElement.scrollTop !== 0 || document.body.scrollTop !== 0) {
                  throw new Error("Footer CTA should still reset document fallback scroll positions.");
                }
              """
            )
        )

    def test_client2_workspace_footer_cta_resets_scroll_even_when_already_on_tickets(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                setupClientRealtimeConnection = () => {};
                syncChatScrollPosition = () => {};

                const createClassList = () => {
                  const values = new Set();
                  return {
                    toggle(name, force) {
                      if (force === undefined) {
                        if (values.has(name)) {
                          values.delete(name);
                          return false;
                        }
                        values.add(name);
                        return true;
                      }
                      if (force) {
                        values.add(name);
                        return true;
                      }
                      values.delete(name);
                      return false;
                    },
                    contains(name) {
                      return values.has(name);
                    },
                  };
                };

                const workspace = { classList: createClassList() };
                const topbarRegion = { innerHTML: "" };
                const contextRegion = { innerHTML: "" };
                const sidebarRegion = { innerHTML: "" };
                const sidebarContent = { innerHTML: "" };
                const sidebarFooter = { innerHTML: "" };
                const mainRegion = {
                  classList: createClassList(),
                  scrollTop: 0,
                  _html: "",
                  _ticketsRoot: null,
                  set innerHTML(value) {
                    this._html = String(value || "");
                    this._ticketsRoot = this._html.includes("tickets-root")
                      ? { scrollTop: 0 }
                      : null;
                  },
                  get innerHTML() {
                    return this._html;
                  },
                  querySelector(selector) {
                    if (selector === ".tickets-root") {
                      return this._ticketsRoot;
                    }
                    return null;
                  },
                };
                const shell = {
                  querySelector(selector) {
                    switch (selector) {
                      case ".clienttest-workspace":
                        return workspace;
                      case '[data-authed-region="topbar"]':
                        return topbarRegion;
                      case '[data-authed-region="context"]':
                        return contextRegion;
                      case '[data-authed-region="main"]':
                        return mainRegion;
                      case '[data-authed-region="sidebar-nav"]':
                        return sidebarRegion;
                      case '[data-authed-region="sidebar-content"]':
                        return sidebarContent;
                      case '[data-authed-region="sidebar-footer"]':
                        return sidebarFooter;
                      default:
                        return null;
                    }
                  },
                };
                ensureAuthedShell = () => shell;
                bindAuthedEvents = () => {};

                const scrollCalls = [];
                window.scrollTo = (options) => {
                  scrollCalls.push(options);
                };
                document.documentElement = { scrollTop: 320 };
                document.body = { scrollTop: 220 };

                window.location.hash = "#/tickets";
                render();
                const initialTicketsRoot = mainRegion.querySelector(".tickets-root");
                scrollCalls.length = 0;
                document.documentElement.scrollTop = 320;
                document.body.scrollTop = 220;
                mainRegion.scrollTop = 320;
                if (!initialTicketsRoot) {
                  throw new Error("Same-route footer CTA setup should render the tickets-root container.");
                }
                initialTicketsRoot.scrollTop = 220;

                navigateToTicketsTop();

                if (window.location.hash !== "#/tickets") {
                  throw new Error("Same-route footer CTA should keep the tickets hash.");
                }
                if (mainRegion.scrollTop !== 0) {
                  throw new Error(`Same-route footer CTA should reset the clienttest-main scroll container, got ${mainRegion.scrollTop}.`);
                }
                const nextTicketsRoot = mainRegion.querySelector(".tickets-root");
                if (!nextTicketsRoot || nextTicketsRoot.scrollTop !== 0) {
                  throw new Error(`Same-route footer CTA should reset the tickets-root scroll container, got ${nextTicketsRoot?.scrollTop}.`);
                }
                if (scrollCalls.length < 1 || scrollCalls[0]?.top !== 0 || scrollCalls[0]?.behavior !== "auto") {
                  throw new Error("Same-route footer CTA should still keep the window scroll fallback available.");
                }
                if (document.documentElement.scrollTop !== 0 || document.body.scrollTop !== 0) {
                  throw new Error("Same-route footer CTA should reset document fallback scroll positions.");
                }
                if (!mainRegion.innerHTML.includes("My Tickets")) {
                  throw new Error("Same-route footer CTA should keep the My Tickets page rendered.");
                }
              """
            )
        )

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
                const normalizedLeadingEmptyBlock = normalizeRichComposerHtmlString("<p><br></p><ul><li>Item</li></ul>");
                if (normalizedLeadingEmptyBlock !== "<ul><li>Item</li></ul>") {
                  throw new Error(`Rich composer should drop empty leading paragraph wrappers around lists, got ${normalizedLeadingEmptyBlock}.`);
                }

                const normalizedTrailingEmptyBlock = normalizeRichComposerHtmlString("<ul><li>Item</li></ul><div><br></div>");
                if (normalizedTrailingEmptyBlock !== "<ul><li>Item</li></ul>") {
                  throw new Error(`Rich composer should drop empty trailing block wrappers around lists, got ${normalizedTrailingEmptyBlock}.`);
                }

                const normalizedEmptyList = normalizeRichComposerHtmlString("<ul><li><br></li></ul>");
                if (normalizedEmptyList !== "") {
                  throw new Error(`Rich composer should collapse fully empty lists to an empty draft, got ${JSON.stringify(normalizedEmptyList)}.`);
                }

                const serializedBold = serializeRichComposerHtmlToMarkdown("<strong>Need</strong> help");
                if (serializedBold !== "**Need** help") {
                  throw new Error(`Rich composer should serialize strong tags to markdown, got ${serializedBold}.`);
                }

                const serializedItalicLeadingSpace = serializeRichComposerHtmlToMarkdown("Hello<em> Agora SDK</em>");
                if (serializedItalicLeadingSpace !== "Hello *Agora SDK*") {
                  throw new Error(`Italic serialization should move leading whitespace outside the markdown markers, got ${JSON.stringify(serializedItalicLeadingSpace)}.`);
                }

                const serializedItalicTrailingSpace = serializeRichComposerHtmlToMarkdown("<em>Agora SDK </em>rocks");
                if (serializedItalicTrailingSpace !== "*Agora SDK* rocks") {
                  throw new Error(`Italic serialization should move trailing whitespace outside the markdown markers, got ${JSON.stringify(serializedItalicTrailingSpace)}.`);
                }

                const renderedItalicBoundarySpace = renderMarkdownMessage(serializedItalicLeadingSpace);
                if (!renderedItalicBoundarySpace.includes("Hello <em>Agora SDK</em>")) {
                  throw new Error(`Serialized italic content should still render after send, got ${renderedItalicBoundarySpace}.`);
                }

                const renderedItalicTrailingBoundarySpace = renderMarkdownMessage(serializedItalicTrailingSpace);
                if (!renderedItalicTrailingBoundarySpace.includes("<em>Agora SDK</em> rocks")) {
                  throw new Error(`Serialized italic content with trailing boundary whitespace should still render after send, got ${renderedItalicTrailingBoundarySpace}.`);
                }

                const serializedBoldLeadingSpace = serializeRichComposerHtmlToMarkdown("Hello<strong> Agora SDK</strong>");
                if (serializedBoldLeadingSpace !== "Hello **Agora SDK**") {
                  throw new Error(`Bold serialization should move leading whitespace outside the markdown markers, got ${JSON.stringify(serializedBoldLeadingSpace)}.`);
                }

                const renderedBoldBoundarySpace = renderMarkdownMessage(serializedBoldLeadingSpace);
                if (!renderedBoldBoundarySpace.includes("Hello <strong>Agora SDK</strong>")) {
                  throw new Error(`Serialized bold content with boundary whitespace should still render after send, got ${renderedBoldBoundarySpace}.`);
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

                const nestedRendered = renderMarkdownMessage("**_[Docs](https://example.com)_**");
                if (!nestedRendered.includes('<strong><em><a href="https://example.com/" target="_blank" rel="noopener noreferrer">Docs</a></em></strong>')) {
                  throw new Error(`Nested markdown should preserve bold + italic + link formatting, got ${nestedRendered}.`);
                }
                if (nestedRendered.includes("**<") || nestedRendered.includes(">**") || nestedRendered.includes("_<") || nestedRendered.includes(">_")) {
                  throw new Error(`Nested markdown should not leak raw formatting markers, got ${nestedRendered}.`);
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

                const hydratedStandaloneCode = buildRichComposerHtmlFromMarkdown("```js\\nconst answer = 42;\\n```");
                const standaloneCodeSpacerCount = (hydratedStandaloneCode.match(/data-composer-empty-line=\"true\"/g) || []).length;
                if (standaloneCodeSpacerCount !== 2) {
                  throw new Error(`Standalone fenced code hydration should add editable spacer lines above and below the code block, got ${hydratedStandaloneCode}.`);
                }
                if (!hydratedStandaloneCode.includes('<pre><code class="language-js">const answer = 42;</code></pre>')) {
                  throw new Error(`Standalone fenced code hydration should preserve the code block itself, got ${hydratedStandaloneCode}.`);
                }
                const normalizedStandaloneCode = normalizeRichComposerHtmlString(hydratedStandaloneCode);
                const normalizedStandaloneCodeSpacerCount =
                  (normalizedStandaloneCode.match(/data-composer-empty-line=\"true\"/g) || []).length;
                if (normalizedStandaloneCodeSpacerCount !== 2) {
                  throw new Error(`Rich composer normalization should preserve code-block spacer lines, got ${normalizedStandaloneCode}.`);
                }
                const serializedStandaloneCode = serializeRichComposerHtmlToMarkdown(hydratedStandaloneCode);
                if (serializedStandaloneCode !== "```js\\nconst answer = 42;\\n```") {
                  throw new Error(`Editor-only spacer lines around code blocks must stay out of markdown serialization, got ${JSON.stringify(serializedStandaloneCode)}.`);
                }

                const nestedHydrated = buildRichComposerHtmlFromMarkdown("**_[Docs](https://example.com)_**");
                if (!nestedHydrated.includes('<strong><em><a href="https://example.com/">Docs</a></em></strong>')) {
                  throw new Error(`Hydration should recreate nested bold + italic + link formatting, got ${nestedHydrated}.`);
                }

                const unwrappedListHtml = unwrapRichComposerListHtml("<ul><li>Alpha</li><li><strong>Beta</strong></li></ul>");
                if (unwrappedListHtml !== "Alpha<br><strong>Beta</strong>") {
                  throw new Error(`List toggle should unwrap list items back into composer line breaks, got ${JSON.stringify(unwrappedListHtml)}.`);
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

    def test_client2_rich_composer_inline_toggle_unwraps_existing_markup(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                const unwrappedItalicHtml = unwrapRichComposerInlineTagHtml("<em>Alpha</em>", "em");
                if (unwrappedItalicHtml !== "Alpha") {
                  throw new Error(`Italic toggle should unwrap existing italic markup, got ${JSON.stringify(unwrappedItalicHtml)}.`);
                }

                const unwrappedNestedItalicHtml = unwrapRichComposerInlineTagHtml("<em><strong>Beta</strong></em>", "em");
                if (unwrappedNestedItalicHtml !== "<strong>Beta</strong>") {
                  throw new Error(`Italic toggle should preserve nested bold content while removing italic, got ${JSON.stringify(unwrappedNestedItalicHtml)}.`);
                }

                const unwrappedBoldHtml = unwrapRichComposerInlineTagHtml("<strong><em>Gamma</em></strong>", "strong");
                if (unwrappedBoldHtml !== "<em>Gamma</em>") {
                  throw new Error(`Bold toggle should preserve nested italic content while removing bold, got ${JSON.stringify(unwrappedBoldHtml)}.`);
                }
              """
            )
        )

    def test_client2_rich_composer_inline_format_collapses_selection_to_end(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                const createdElements = [];
                document.createElement = (tag) => {
                  const node = {
                    nodeType: 1,
                    tagName: String(tag || "").toUpperCase(),
                    childNodes: [],
                    appendChild(child) {
                      this.childNodes.push(child);
                      return child;
                    },
                    remove() {},
                  };
                  createdElements.push(node);
                  return node;
                };
                document.createTextNode = (value) => ({ nodeType: 3, textContent: String(value || "") });

                const fakeRange = {
                  collapsed: false,
                  insertedNode: null,
                  extractContents() {
                    return { nodeType: 11, childNodes: [] };
                  },
                  insertNode(node) {
                    this.insertedNode = node;
                  },
                  toString() {
                    return "Alpha";
                  },
                };

                const caretTargets = [];
                getComposerSelectionRange = () => fakeRange;
                findComposerFullySelectedInlineFormatNode = () => null;
                placeComposerCaretAtEnd = (node) => {
                  caretTargets.push(node);
                  return true;
                };
                selectComposerNodeContents = () => {
                  throw new Error("Selected inline formatting should collapse the caret instead of reselecting the wrapped content.");
                };
                syncComposerDraftStateFromElement = () => {};

                const handled = applyComposerInlineFormat("strong", {});
                if (!handled) {
                  throw new Error("Inline format toggle should report success for a selected range.");
                }
                if (!fakeRange.insertedNode || fakeRange.insertedNode.tagName !== "STRONG") {
                  throw new Error("Inline format toggle should insert a strong wrapper around the selected content.");
                }
                if (caretTargets.length !== 1 || caretTargets[0] !== fakeRange.insertedNode) {
                  throw new Error("Inline format toggle should collapse the caret to the end of the formatted wrapper.");
                }
              """
            )
        )

    def test_client2_rich_composer_inline_format_passes_collapsed_bookmark_to_sync(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                document.createElement = (tag) => ({
                  nodeType: 1,
                  tagName: String(tag || "").toUpperCase(),
                  childNodes: [],
                  appendChild(child) {
                    this.childNodes.push(child);
                    return child;
                  },
                });

                const fakeRange = {
                  collapsed: false,
                  insertedNode: null,
                  extractContents() {
                    return { nodeType: 11, childNodes: [] };
                  },
                  insertNode(node) {
                    this.insertedNode = node;
                  },
                  toString() {
                    return "Alpha";
                  },
                };

                const explicitBookmark = {
                  startPath: [0, 0],
                  startOffset: 5,
                  endPath: [0, 0],
                  endOffset: 5,
                };
                const syncCalls = [];

                getComposerSelectionRange = () => fakeRange;
                findComposerFullySelectedInlineFormatNode = () => null;
                placeComposerCaretAtEnd = () => true;
                captureRichComposerSelectionBookmark = () => explicitBookmark;
                syncComposerDraftStateFromElement = (_element, options) => {
                  syncCalls.push(options || null);
                };

                applyComposerInlineFormat("strong", {});
                if (syncCalls.length !== 1) {
                  throw new Error(`Inline format toggle should sync the composer once, got ${syncCalls.length}.`);
                }
                if (!syncCalls[0] || syncCalls[0].selectionBookmark !== explicitBookmark) {
                  throw new Error("Inline format toggle should pass the collapsed caret bookmark into syncComposerDraftStateFromElement.");
                }
              """
            )
        )

    def test_client2_rich_composer_sync_draft_state_restores_explicit_selection_bookmark(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                const explicitBookmark = {
                  startPath: [0, 0],
                  startOffset: 4,
                  endPath: [0, 0],
                  endOffset: 4,
                };
                const restoredBookmarks = [];
                const element = {
                  innerHTML: "<strong>Alpha</strong>",
                  childNodes: [{ nodeType: 1 }],
                  scrollTop: 0,
                };

                isRichTextComposerElement = () => true;
                isTextComposerElement = () => false;
                isComposerElementDisabled = () => false;
                normalizeRichComposerHtmlString = (html) => String(html || "");
                serializeRichComposerHtmlToMarkdown = (html) => String(html || "");
                refreshNewTicketInlineComposerAction = () => {};
                syncComposerToolbarStateFromElement = () => {};
                restoreRichComposerSelectionBookmark = (_element, bookmark) => {
                  restoredBookmarks.push(bookmark);
                  return true;
                };

                syncComposerDraftStateFromElement(element, { selectionBookmark: explicitBookmark });
                if (restoredBookmarks.length !== 1 || restoredBookmarks[0] !== explicitBookmark) {
                  throw new Error("syncComposerDraftStateFromElement should restore the explicit collapsed selection bookmark after normalization.");
                }
              """
            )
        )

    def test_client2_rich_composer_range_context_resolves_root_child_for_collapsed_selection(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                const listItem = { nodeType: 1, tagName: "LI", childNodes: [] };
                const list = { nodeType: 1, tagName: "UL", childNodes: [listItem] };
                listItem.parentNode = list;
                const root = {
                  nodeType: 1,
                  tagName: "DIV",
                  childNodes: [list],
                  contains(candidate) {
                    return candidate === this || candidate === list || candidate === listItem;
                  },
                };
                list.parentNode = root;

                const collapsedAtListStart = {
                  collapsed: true,
                  startContainer: root,
                  startOffset: 0,
                  endContainer: root,
                  endOffset: 0,
                };
                const collapsedAtListEnd = {
                  collapsed: true,
                  startContainer: root,
                  startOffset: 1,
                  endContainer: root,
                  endOffset: 1,
                };

                if (getComposerRangeContextNode(collapsedAtListStart, root) !== list) {
                  throw new Error("Collapsed root selection at the start of a list should resolve to the child list node.");
                }
                if (getComposerRangeContextNode(collapsedAtListEnd, root) !== list) {
                  throw new Error("Collapsed root selection at the end of a list should still resolve to the adjacent list node.");
                }
              """
            )
        )

    def test_client2_rich_composer_list_toggle_exits_current_item_from_root_collapsed_context(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                const listItem = { nodeType: 1, tagName: "LI", childNodes: [] };
                const list = {
                  nodeType: 1,
                  tagName: "UL",
                  childNodes: [listItem],
                  outerHTML: "<ul><li>Alpha</li></ul>",
                };
                listItem.parentNode = list;
                const element = {
                  nodeType: 1,
                  tagName: "DIV",
                  childNodes: [list],
                  contains(candidate) {
                    return candidate === this || candidate === list || candidate === listItem;
                  },
                };
                list.parentNode = element;

                const fakeRange = {
                  collapsed: true,
                  startContainer: element,
                  startOffset: 1,
                  endContainer: element,
                  endOffset: 1,
                  deleted: false,
                  insertedMarker: null,
                  deleteContents() {
                    this.deleted = true;
                  },
                  insertNode(node) {
                    this.insertedMarker = node;
                  },
                };

                const marker = {
                  nodeType: 1,
                  tagName: "SPAN",
                  remove() {},
                  getAttribute(name) {
                    return name === "data-composer-caret-marker" ? "true" : null;
                  },
                };

                const exitInputs = [];
                const replaceCalls = [];
                const wrapCalls = [];
                const unwrapCalls = [];
                const syncCalls = [];

                getComposerSelectionRange = () => fakeRange;
                createComposerCaretMarkerElement = () => marker;
                exitRichComposerCurrentListItemHtml = (html) => {
                  exitInputs.push(html);
                  return "<div>Alpha</div>";
                };
                replaceComposerNodeWithHtml = (node, html) => {
                  replaceCalls.push({ node, html });
                  return [];
                };
                findComposerCaretMarkerInNodes = () => null;
                findComposerCaretMarkerInNode = () => null;
                restoreComposerCaretFromMarker = () => true;
                captureRichComposerSelectionBookmark = () => ({
                  startPath: [0],
                  startOffset: 0,
                  endPath: [0],
                  endOffset: 0,
                });
                syncComposerDraftStateFromElement = (_element, options) => {
                  syncCalls.push(options || null);
                };
                wrapRichComposerBlockHtmlInList = () => {
                  wrapCalls.push(true);
                  return "<ul><li>unexpected</li></ul>";
                };
                unwrapRichComposerListHtml = () => {
                  unwrapCalls.push(true);
                  return "<div>unexpected</div>";
                };

                const handled = applyComposerListFormat(element);
                if (!handled) {
                  throw new Error("Collapsed list toggle should succeed when the caret context is resolved from the root container.");
                }
                if (!fakeRange.deleted || fakeRange.insertedMarker !== marker) {
                  throw new Error("Collapsed list toggle should insert a caret marker before exiting the current item.");
                }
                if (exitInputs.length !== 1 || exitInputs[0] !== list.outerHTML) {
                  throw new Error(`Collapsed list toggle should exit the current list item using the existing list HTML, got ${JSON.stringify(exitInputs)}.`);
                }
                if (replaceCalls.length !== 1 || replaceCalls[0].node !== list || replaceCalls[0].html !== "<div>Alpha</div>") {
                  throw new Error(`Collapsed list toggle should replace the existing list with the exited item HTML, got ${JSON.stringify(replaceCalls)}.`);
                }
                if (wrapCalls.length !== 0) {
                  throw new Error("Collapsed list toggle should not re-wrap the current block into a nested list.");
                }
                if (unwrapCalls.length !== 0) {
                  throw new Error("Collapsed list toggle should not unwrap the whole list when only the current item should exit.");
                }
                if (syncCalls.length !== 1 || !syncCalls[0] || !syncCalls[0].selectionBookmark) {
                  throw new Error("Collapsed list toggle should preserve a collapsed selection bookmark when syncing the composer state.");
                }
              """
            )
        )

    def test_client2_rich_composer_inline_format_source_collapses_instead_of_reselecting(self) -> None:
        app_source = Path("ui/client-ui/app.js").read_text(encoding="utf-8")
        start = app_source.index("function applyComposerInlineFormat(tagName, element) {")
        end = app_source.index("function isRichComposerDomNodeStructurallyEmpty", start)
        inline_toggle_source = app_source[start:end]

        self.assertIn("placeComposerCaretAtEnd(wrapper);", inline_toggle_source)
        self.assertIn("const lastInsertedNode = insertedNodes[insertedNodes.length - 1] || null;", inline_toggle_source)
        self.assertIn("placeComposerCaretAtEnd(lastInsertedNode)", inline_toggle_source)
        self.assertIn("captureRichComposerSelectionBookmark(element)", inline_toggle_source)
        self.assertIn("selectionBookmark", inline_toggle_source)
        self.assertNotIn("selectComposerNodeContents(wrapper);", inline_toggle_source)

    def test_client2_rich_composer_list_helpers_wrap_current_block_and_split_items(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                const marker = '<span data-composer-caret-marker="true"></span>';

                const wrappedBlock = wrapRichComposerBlockHtmlInList(`<div>Alpha${marker}<em>Beta</em></div>`);
                if (wrappedBlock !== `<ul><li>Alpha${marker}<em>Beta</em></li></ul>`) {
                  throw new Error(`Collapsed list toggle should wrap the current block into a single list item, got ${wrappedBlock}.`);
                }

                const wrappedEmptyBlock = wrapRichComposerBlockHtmlInList(`<div>${marker}</div>`);
                if (wrappedEmptyBlock !== `<ul><li>${marker}<br></li></ul>`) {
                  throw new Error(`Collapsed list toggle should keep the empty-list fallback for empty blocks, got ${wrappedEmptyBlock}.`);
                }

                const splitMiddle = splitRichComposerListItemHtmlAtCaret(`<li>Alpha${marker}<em>Beta</em></li>`);
                if (splitMiddle !== `<li>Alpha</li><li>${marker}<em>Beta</em></li>`) {
                  throw new Error(`Shift+Enter inside a list item should move trailing inline markup into the next item, got ${splitMiddle}.`);
                }

                const splitStart = splitRichComposerListItemHtmlAtCaret(`<li>${marker}<strong>Alpha</strong></li>`);
                if (splitStart !== `<li><br></li><li>${marker}<strong>Alpha</strong></li>`) {
                  throw new Error(`Shift+Enter at the start of a list item should leave the current item empty and move content into the next item, got ${splitStart}.`);
                }

                const splitEnd = splitRichComposerListItemHtmlAtCaret(`<li><strong>Alpha</strong>${marker}</li>`);
                if (splitEnd !== `<li><strong>Alpha</strong></li><li>${marker}<br></li>`) {
                  throw new Error(`Shift+Enter at the end of a list item should create an empty following item, got ${splitEnd}.`);
                }

                const splitNested = splitRichComposerListItemHtmlAtCaret(`<li><strong>Al${marker}pha</strong></li>`);
                if (splitNested !== `<li><strong>Al</strong></li><li>${marker}<strong>pha</strong></li>`) {
                  throw new Error(`Shift+Enter should preserve nested inline formatting when splitting list items, got ${splitNested}.`);
                }
              """
            )
        )

    def test_client2_rich_composer_list_cancel_exits_current_item_only(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                const marker = '<span data-composer-caret-marker="true"></span>';

                const singleItemExit = exitRichComposerCurrentListItemHtml(`<ul><li>Alpha${marker}<em>Beta</em></li></ul>`);
                if (singleItemExit !== `<div>Alpha${marker}<em>Beta</em></div>`) {
                  throw new Error(`Single-item list cancel should turn the current item into a plain block, got ${singleItemExit}.`);
                }

                const middleItemExit = exitRichComposerCurrentListItemHtml(`<ul><li>One</li><li>Two${marker}<strong>Three</strong></li><li>Four</li></ul>`);
                if (middleItemExit !== `<ul><li>One</li></ul><div>Two${marker}<strong>Three</strong></div><ul><li>Four</li></ul>`) {
                  throw new Error(`Cancelling list on a middle item should preserve the surrounding items as lists, got ${middleItemExit}.`);
                }

                const firstItemExit = exitRichComposerCurrentListItemHtml(`<ul><li>${marker}<em>First</em></li><li>Second</li></ul>`);
                if (firstItemExit !== `<div>${marker}<em>First</em></div><ul><li>Second</li></ul>`) {
                  throw new Error(`Cancelling list on the first item should keep the trailing items in a list, got ${firstItemExit}.`);
                }

                const lastItemExit = exitRichComposerCurrentListItemHtml(`<ol><li>One</li><li>Last${marker}</li></ol>`);
                if (lastItemExit !== `<ol><li>One</li></ol><div>Last${marker}</div>`) {
                  throw new Error(`Cancelling list on the last item should keep the leading items in the original list type, got ${lastItemExit}.`);
                }
              """
            )
        )

    def test_client2_rich_composer_list_source_uses_block_wrap_and_split_helpers(self) -> None:
        app_source = Path("ui/client-ui/app.js").read_text(encoding="utf-8")
        list_start = app_source.index("function applyComposerListFormat(element) {")
        list_end = app_source.index("function handleRichComposerListDeletion", list_start)
        list_toggle_source = app_source[list_start:list_end]

        shift_enter_start = app_source.index("function handleRichComposerShiftEnter(element) {")
        shift_enter_end = app_source.index("function buildChatTicketViewState(ticket) {", shift_enter_start)
        shift_enter_source = app_source[shift_enter_start:shift_enter_end]

        self.assertIn("const contextNode = getComposerRangeContextNode(range, element);", list_toggle_source)
        self.assertIn("const collapsedListContext = range.collapsed", list_toggle_source)
        self.assertIn("getComposerCollapsedListContext(range, element)", list_toggle_source)
        self.assertIn("if (existingList && currentListItem) {", list_toggle_source)
        self.assertIn("findNearestComposerListConvertibleBlock(", list_toggle_source)
        self.assertIn("wrapRichComposerBlockHtmlInList(", list_toggle_source)
        self.assertIn("exitRichComposerCurrentListItemHtml(", list_toggle_source)
        self.assertIn("splitRichComposerListItemHtmlAtCaret(", shift_enter_source)
        self.assertNotIn('nextItem.appendChild(document.createElement("br"));', shift_enter_source)

    def test_client2_rich_composer_toolbar_context_uses_range_context_node(self) -> None:
        app_source = Path("ui/client-ui/app.js").read_text(encoding="utf-8")
        start = app_source.index("function getRichComposerSelectionContext(element) {")
        end = app_source.index("function applyComposerToolbarStateToDom()", start)
        toolbar_context_source = app_source[start:end]

        self.assertIn("const contextNode = getComposerRangeContextNode(range, element);", toolbar_context_source)
        self.assertIn("const collapsedListContext = range.collapsed", toolbar_context_source)
        self.assertIn("getComposerCollapsedListContext(range, element)", toolbar_context_source)
        self.assertNotIn("const contextNode = range.collapsed", toolbar_context_source)

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
                if ((draftHtml.match(/data-composer-markdown-action=/g) || []).length !== 5) {
                  throw new Error("Draft composer should render bold, italic, list, code-block, and attach only.");
                }
                if (draftHtml.includes('data-composer-markdown-action="link"')) {
                  throw new Error("Draft composer should not render the removed link button.");
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
                if (detailHtml.includes('data-composer-markdown-action="link"')) {
                  throw new Error("Ticket detail shell should not render the removed link button.");
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

    def test_client2_rich_composer_toolbar_state_render_without_link_ui(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.composerToolbarState = {
                  bold: true,
                  italic: false,
                  list: true,
                  codeBlock: true,
                };
                const draftToolbar = renderNewTicketComposerToolbar({ canCompose: true, includeSummary: true });
                if (!draftToolbar.includes('data-composer-markdown-action="bold"') || !draftToolbar.includes("is-active")) {
                  throw new Error(`Toolbar should render the active state for selected formatting, got ${draftToolbar}.`);
                }
                if (!draftToolbar.includes('data-composer-markdown-action="code-block"')) {
                  throw new Error("Toolbar should keep the code block action.");
                }
                if (draftToolbar.includes('data-composer-markdown-action="link"')) {
                  throw new Error("Toolbar should not render the removed link action.");
                }
              """
            )
        )

    def test_client2_rich_composer_no_link_editor_region_and_empty_note_anchor(self) -> None:
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
                if (draftHtml.includes('data-chat-section="composer-link-editor"')) {
                  throw new Error(`Draft composer should not render the removed link-editor region, got ${draftHtml}.`);
                }
                if (!draftHtml.includes('<div data-chat-section="composer-note"></div>')) {
                  throw new Error(`Draft composer should render an empty note anchor without spacer whitespace, got ${draftHtml}.`);
                }

                const detailHtml = renderChatTicketFromState({
                  ticket: {
                    id: "TK-DETAIL-EMPTY-ANCHORS",
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
                if (detailHtml.includes('data-chat-section="composer-link-editor"')) {
                  throw new Error(`Detail composer should not render the removed link-editor region, got ${detailHtml}.`);
                }
                if (!detailHtml.includes('<div data-chat-section="composer-note"></div>')) {
                  throw new Error(`Detail composer should render an empty note anchor without spacer whitespace, got ${detailHtml}.`);
                }
              """
            )
        )

    def test_client2_legacy_resolved_detail_hides_entire_composer_region(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                const detailHtml = renderChatTicketFromState({
                  ticket: {
                    id: "TK-DETAIL-RESOLVED-001",
                    title: "Legacy resolved detail shell",
                    status: "resolved",
                    updatedAt: "2026-04-21T10:00:00.000Z",
                    product: "audio_video_calling",
                    userId: state.user.id,
                  },
                  renderableMessages: [],
                  sending: false,
                  requiresProductSelection: false,
                  canCompose: false,
                  canSubmit: false,
                  usesNewTicketShell: false,
                  showVisibleFooterBand: false,
                  isEditing: false,
                });
                if (detailHtml.includes("ticket-detail-composer")) {
                  throw new Error("Resolved legacy detail tickets should hide the entire composer region.");
                }
                if (detailHtml.includes("ticket-detail-composer-format-toolbar")) {
                  throw new Error("Resolved legacy detail tickets should not render the composer toolbar.");
                }
                if (detailHtml.includes('data-chat-section="composer-form"')) {
                  throw new Error("Resolved legacy detail tickets should not render the composer form.");
                }
                if (detailHtml.includes('id=\"chat-input\"')) {
                  throw new Error("Resolved legacy detail tickets should not render the chat input.");
                }
              """
            )
        )

    def test_client2_rich_composer_code_block_toggle_unwraps_existing_markup(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                const unwrappedSingleLineCodeHtml = unwrapRichComposerCodeBlockHtml("<pre><code>const answer = 42;</code></pre>");
                if (unwrappedSingleLineCodeHtml !== "const answer = 42;") {
                  throw new Error(`Code block toggle should unwrap single-line code blocks, got ${JSON.stringify(unwrappedSingleLineCodeHtml)}.`);
                }

                const unwrappedMultiLineCodeHtml = unwrapRichComposerCodeBlockHtml("<pre><code>const answer = 42;\\nconsole.log(answer);</code></pre>");
                if (unwrappedMultiLineCodeHtml !== "const answer = 42;<br>console.log(answer);") {
                  throw new Error(`Code block toggle should preserve multi-line breaks when unwrapping, got ${JSON.stringify(unwrappedMultiLineCodeHtml)}.`);
                }

                const hydratedStandaloneCode = buildRichComposerHtmlFromMarkdown("```\\nconst answer = 42;\\n```");
                const reserializedStandaloneCode = serializeRichComposerHtmlToMarkdown(hydratedStandaloneCode);
                if (reserializedStandaloneCode !== "```\\nconst answer = 42;\\n```") {
                  throw new Error(`Code-block spacer lines should not leak into markdown when reserializing hydrated editor HTML, got ${JSON.stringify(reserializedStandaloneCode)}.`);
                }
              """
            )
        )

        app_source = Path("ui/client-ui/app.js").read_text(encoding="utf-8")
        start = app_source.index("function applyComposerCodeBlockFormat(element) {")
        end = app_source.index("function handleComposerToolbarAction", start)
        code_block_toggle_source = app_source[start:end]

        self.assertIn("unwrapRichComposerCodeBlockHtml(existingCodeBlock.outerHTML || \"\")", code_block_toggle_source)
        self.assertIn("removeComposerAdjacentCodeBlockSpacerLine(existingCodeBlock.previousSibling);", code_block_toggle_source)
        self.assertIn("removeComposerAdjacentCodeBlockSpacerLine(existingCodeBlock.nextSibling);", code_block_toggle_source)
        self.assertIn("replaceComposerNodeWithHtml(", code_block_toggle_source)
        self.assertIn("removeComposerAdjacentCodeBlockSpacerLine(fullySelectedCodeBlock.previousSibling);", code_block_toggle_source)
        self.assertIn("removeComposerAdjacentCodeBlockSpacerLine(fullySelectedCodeBlock.nextSibling);", code_block_toggle_source)
        self.assertNotIn("ensureComposerCaretInAdjacentTextLine(existingCodeBlock, element, \"after\")", code_block_toggle_source)
        self.assertNotIn("placeComposerCaretAfterNode(existingCodeBlock);", code_block_toggle_source)
        self.assertNotIn('code.appendChild(range.extractContents());', code_block_toggle_source)

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

                const nestedMessageHtml = renderMessageBody({
                  role: "user",
                  content: "**_[Docs](https://example.com)_**",
                  content_format: "markdown",
                });
                if (!nestedMessageHtml.includes('<strong><em><a href="https://example.com/" target="_blank" rel="noopener noreferrer">Docs</a></em></strong>')) {
                  throw new Error(`Customer markdown should render nested bold + italic + link formatting, got ${nestedMessageHtml}.`);
                }
                if (nestedMessageHtml.includes("**<") || nestedMessageHtml.includes(">**") || nestedMessageHtml.includes("_<") || nestedMessageHtml.includes(">_")) {
                  throw new Error(`Customer markdown bubbles should not leak raw formatting markers for nested rich content, got ${nestedMessageHtml}.`);
                }

                const nestedCorrespondenceHtml = renderNewTicketMessageContent({
                  role: "user",
                  content: "**_[Docs](https://example.com)_**",
                  content_format: "markdown",
                });
                if (!nestedCorrespondenceHtml.includes("<strong><em><a href=\\"https://example.com/\\" target=\\"_blank\\" rel=\\"noopener noreferrer\\">Docs</a></em></strong>")) {
                  throw new Error(`Correspondence bubbles should preserve nested markdown formatting, got ${nestedCorrespondenceHtml}.`);
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

    def test_client2_sync_preserves_pending_local_ticket_when_backend_list_temporarily_misses_it(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem(
                  "helpdesk_tickets",
                  JSON.stringify([
                    {
                      id: "TK-171",
                      title: "New ticket",
                      status: "communicating",
                      createdAt: "2026-04-22T09:00:00.000Z",
                      updatedAt: "2026-04-22T09:00:00.000Z",
                      userId: state.user.id,
                      product: null,
                      messages: [
                        {
                          id: "msg-user-1",
                          role: "user",
                          content: "i got black screen, what should i do?",
                          createdAt: "2026-04-22T09:00:00.000Z",
                          contentFormat: "markdown",
                        },
                      ],
                    },
                  ])
                );

                state.view = "chat-ticket";
                state.activeTicketId = "TK-171";
                setPendingSession("TK-171", {
                  phase: "queued",
                  userMessageId: "msg-user-1",
                  persistedMessageCreatedAt: "2026-04-22T09:00:00.000Z",
                  queuedMessageCreatedAt: "2026-04-22T09:00:00.000Z",
                  waitingForDurableReply: true,
                });

                fetch = async (url) => {
                  if (!String(url).startsWith("/api/tickets?")) {
                    throw new Error(`Unexpected fetch call: ${url}`);
                  }
                  return {
                    ok: true,
                    json: async () => ({
                      tickets: [],
                    }),
                  };
                };

                await syncTicketsFromBackend({ silent: true });

                const preserved = getTicketById("TK-171");
                if (!preserved) {
                  throw new Error("Pending local ticket should not disappear when the backend list temporarily omits it.");
                }
                if (preserved.messages.length !== 1 || preserved.messages[0].content !== "i got black screen, what should i do?") {
                  throw new Error(`Pending local ticket should keep its optimistic customer message, got ${JSON.stringify(preserved)}.`);
                }
                const html = renderChatTicket();
                if (html.includes("Session not found.")) {
                  throw new Error(`Pending local ticket sync should not leave the chat route in the Session not found state, got ${html}.`);
                }
              """
            )
        )

    def test_client2_rich_composer_css_resets_block_margins(self) -> None:
        css = Path("ui/client-ui/styles.css").read_text(encoding="utf-8")

        self.assertRegex(
            css,
            r"\.new-ticket-fixed-composer-panel\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;[^}]*min-height:\s*0;",
        )
        self.assertNotRegex(css, r"\.new-ticket-fixed-composer-panel\s*\{[^}]*grid-template-rows:\s*auto\s+auto\s+auto\s+1fr;")
        self.assertRegex(
            css,
            r"\.new-ticket-fixed-composer-panel\s+\.new-ticket-composer-form\s*\{[^}]*min-height:\s*0;[^}]*flex:\s*1\s+1\s+auto;[^}]*display:\s*flex;[^}]*flex-direction:\s*column;[^}]*padding:\s*0\s+18px;",
        )
        self.assertRegex(
            css,
            r"\.new-ticket-fixed-composer-panel\s+\.new-ticket-composer-input-shell\s*\{[^}]*min-height:\s*0;[^}]*height:\s*auto;[^}]*display:\s*flex;[^}]*flex:\s*1\s+1\s+auto;",
        )
        self.assertNotRegex(
            css,
            r"\.new-ticket-fixed-composer-panel\s+\.new-ticket-composer-input-shell\s*\{[^}]*display:\s*grid;",
        )
        self.assertRegex(
            css,
            r"\.new-ticket-fixed-composer-panel\s+\.new-ticket-textarea\s*\{[^}]*flex:\s*1\s+1\s+auto;[^}]*min-height:\s*0;[^}]*height:\s*auto;",
        )
        self.assertRegex(
            css,
            r"\.new-ticket-fixed-composer-panel\s+\.new-ticket-textarea:focus-visible\s*\{[^}]*box-shadow:\s*none;[^}]*border-color:\s*transparent;",
        )
        self.assertNotRegex(css, r"\[data-chat-section=\"composer-link-editor\"\]:empty\s*\{")
        self.assertRegex(css, r"\[data-chat-section=\"composer-note\"\]:empty\s*\{\s*display:\s*none;")
        self.assertNotRegex(css, r"\.composer-link-editor")
        self.assertRegex(css, r"\.composer-rich-input p\s*\{\s*margin:\s*0;")
        self.assertRegex(css, r"\.composer-rich-input li\s*\{\s*margin:\s*0;")

    def test_client2_send_button_css_matches_engineer_visual_contract(self) -> None:
        css = Path("ui/client-ui/styles.css").read_text(encoding="utf-8")

        self.assertRegex(
            css,
            r"\.composer-icon-button,\s*\.new-ticket-inline-send-btn\s*\{[^}]*width:\s*48px;[^}]*height:\s*48px;[^}]*border:\s*1px solid rgba\(110,\s*120,\s*130,\s*0\.14\);[^}]*border-radius:\s*16px;",
        )
        self.assertRegex(
            css,
            r"\.send-btn,\s*\.new-ticket-inline-send-btn\s*\{[^}]*color:\s*#fff;[^}]*border-color:\s*transparent;[^}]*background:\s*linear-gradient\(135deg,\s*var\(--primary\)\s*0%,\s*var\(--primary-container\)\s*100%\);[^}]*box-shadow:\s*0 18px 34px rgba\(0,\s*100,\s*147,\s*0\.22\);",
        )
        self.assertRegex(
            css,
            r"\.composer-icon-button:focus-visible,\s*\.send-btn:focus-visible,\s*\.new-ticket-inline-send-btn:focus-visible\s*\{[^}]*outline:\s*none;[^}]*box-shadow:\s*0 0 0 4px rgba\(0,\s*100,\s*147,\s*0\.14\),\s*0 18px 32px rgba\(52,\s*61,\s*150,\s*0\.14\);",
        )
        self.assertRegex(
            css,
            r"\.btn:disabled,[^}]*\.send-btn:disabled,[^}]*\.new-ticket-inline-send-btn:disabled\s*\{[^}]*cursor:\s*not-allowed;[^}]*opacity:\s*0\.56;",
        )
        self.assertRegex(
            css,
            r"\.composer-icon-button:hover:not\(:disabled\),\s*\.new-ticket-inline-send-btn:hover:not\(:disabled\)\s*\{[^}]*transform:\s*translateY\(-1px\);[^}]*box-shadow:\s*0 18px 32px rgba\(52,\s*61,\s*150,\s*0\.14\);",
        )
        self.assertRegex(
            css,
            r"(?s)@media[^{]*\{.*?\.new-ticket-inline-send-btn\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px;[^}]*border-radius:\s*14px;",
        )
        self.assertNotRegex(
            css,
            r"\.new-ticket-inline-send-btn\s*\{[^}]*background:\s*linear-gradient\(135deg,\s*#97cff6\s*0%,\s*#74bdec\s*100%\);",
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

    def test_client2_resolved_postsend_ticket_hides_entire_composer_region(self) -> None:
        self.run_client2_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                updateTicketTitle(ticket.id, "Resolved channel join question");
                updateTicketStatus(ticket.id, "resolved");
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
                  },
                ]);

                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;
                state.newTicketPreviewTicketId = null;

                const html = renderChatTicket();
                if (!html.includes("new-ticket-postsend-shell")) {
                  throw new Error("Resolved tickets should continue using the postsend shell.");
                }
                if (html.includes("new-ticket-thread-footer-composer")) {
                  throw new Error("Resolved postsend tickets should hide the entire composer region.");
                }
                if (html.includes("new-ticket-postsend-composer")) {
                  throw new Error("Resolved postsend tickets should not render the postsend composer shell.");
                }
                if (html.includes("new-ticket-inline-send-btn")) {
                  throw new Error("Resolved postsend tickets should not render the inline send button.");
                }
                if (html.includes('id=\"chat-input\"')) {
                  throw new Error("Resolved postsend tickets should not render the chat input.");
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

    def test_client2_postsend_header_keeps_desktop_two_column_layout_and_mobile_stack(self) -> None:
        css = Path("ui/client-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn(".new-ticket-postsend-header-row {", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto;", css)
        self.assertNotIn(
            ".new-ticket-postsend-header-row {\n  display: flex;\n  align-items: flex-start;\n  justify-content: space-between;\n  gap: 20px;\n  flex-wrap: wrap;\n}",
            css,
        )
        self.assertIn(".new-ticket-postsend-actions {", css)
        self.assertIn("justify-self: end;", css)
        self.assertIn("align-self: start;", css)
        self.assertIn("flex-wrap: nowrap;", css)
        self.assertRegex(
            css,
            re.compile(
                r"@media \(max-width: 720px\) \{[\s\S]*?\.new-ticket-postsend-header-row \{\n\s+grid-template-columns: 1fr;\n\s+\}",
            ),
        )
        self.assertRegex(
            css,
            re.compile(
                r"@media \(max-width: 720px\) \{[\s\S]*?\.new-ticket-postsend-actions \{\n\s+justify-self: start;\n\s+flex-wrap: wrap;\n\s+\}",
            ),
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
