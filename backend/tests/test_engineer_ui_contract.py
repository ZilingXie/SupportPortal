from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


class EngineerUiContractTests(unittest.TestCase):
    def run_engineer_app_script(self, script: str) -> None:
        node_script = textwrap.dedent(
            f"""
            (async () => {{
            const fs = require("fs");
            const vm = require("vm");
            const userScript = {script!r};
            const sharedComposerPath = "ui/shared-ui/composer.js";

            let source = fs.readFileSync("ui/engineer-ui/app.js", "utf8");
            source = source.replace(/\\nloginFormEl\\.addEventListener\\([\\s\\S]*$/, "\\n");

            function createElementStub() {{
              return {{
                innerHTML: "",
                textContent: "",
                value: "",
                dataset: {{}},
                disabled: false,
                classList: {{
                  add() {{}},
                  remove() {{}},
                  toggle() {{}},
                  contains() {{ return false; }},
                }},
                addEventListener() {{}},
                removeEventListener() {{}},
                querySelector() {{ return null; }},
                querySelectorAll() {{ return []; }},
                closest() {{ return null; }},
                focus() {{}},
                scrollIntoView() {{}},
                setSelectionRange() {{}},
              }};
            }}

            const storage = new Map();
            const sandbox = {{
              console,
              URL,
              window: {{
                location: {{
                  hash: "",
                  protocol: "http:",
                  host: "localhost:8080",
                  assign() {{}},
                  reload() {{}},
                }},
                addEventListener() {{}},
                alert(message) {{
                  throw new Error(message);
                }},
              }},
              document: {{
                getElementById() {{
                  return createElementStub();
                }},
                addEventListener() {{}},
                querySelector() {{ return null; }},
                querySelectorAll() {{ return []; }},
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
              HTMLTextAreaElement: function HTMLTextAreaElement() {{}},
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

    def test_engineer_ui_uses_stitch_workspace_language(self) -> None:
        html = Path("ui/engineer-ui/index.html").read_text(encoding="utf-8")
        app_source = Path("ui/engineer-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/engineer-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn('<html lang="en" class="material-symbols-pending">', html)
        self.assertIn("Concierge AI", html)
        self.assertIn("Manrope", html)
        self.assertIn(
            'href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&family=Inter:wght@400;500;600;700&display=swap"',
            html,
        )
        self.assertIn(
            'href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght@300;400;500;700&display=block"',
            html,
        )
        self.assertIn('id="material-symbols-font-stylesheet"', html)
        self.assertNotIn(
            'family=Manrope:wght@400;600;700;800&family=Inter:wght@400;500;600;700&family=Material+Symbols+Outlined:wght@300;400;500;700&display=swap',
            html,
        )
        self.assertIn(
            "document.documentElement.classList.remove(\"material-symbols-pending\")",
            html,
        )
        self.assertIn('getElementById("material-symbols-font-stylesheet")', html)
        self.assertIn('addEventListener("load", waitForMaterialSymbols, { once: true })', html)
        self.assertIn('load(\'24px "Material Symbols Outlined"\')', html)
        self.assertIn("if (iconFontStylesheet?.sheet) {", html)
        self.assertIn('/shared-ui/composer.css?v=20260519-json-codeblock-1', html)
        self.assertIn('/shared-ui/composer.js?v=20260519-json-codeblock-1', html)
        self.assertIn("./styles.css?v=20260619-engineer-multi-agent-hitl-gate-1", html)
        self.assertIn('./app.js?v=20260619-engineer-multi-agent-hitl-gate-1', html)
        self.assertIn('const LOGIN_USER = "Jack";', app_source)
        self.assertIn('const LOGIN_PASS = "jack";', app_source)
        self.assertIn('const ENGINEER_ID = "Jack";', app_source)
        self.assertIn('const ENGINEER_DISPLAY_NAME = "jack";', app_source)
        self.assertIn('const ENGINEER_AI_DISPLAY_NAME = "Sid";', app_source)
        self.assertIn('const PUBLIC_ASSISTANT_DISPLAY_NAME = "Sid";', app_source)
        self.assertIn("Jack / jack", html)
        self.assertIn("Sid", app_source)
        self.assertIn("return ENGINEER_DISPLAY_NAME;", app_source)
        self.assertIn("detail-investigation-closing-state", app_source)
        self.assertIn("Approve for Guardrail", app_source)
        self.assertIn(
            "Running final guardrail review before sending to customer...",
            app_source,
        )
        self.assertNotIn('data-detail-action="refresh-ticket"', app_source)
        self.assertNotIn('action === "refresh-ticket"', app_source)
        self.assertNotIn("Sync failed:", app_source)
        self.assertIn(".detail-investigation-closing-state {", css)
        self.assertIn(".case-buddy-request-summary {", css)
        self.assertIn("function parseRoute()", app_source)
        self.assertIn('path.startsWith("/tickets/")', app_source)
        self.assertIn("function renderTicketPoolView()", app_source)
        self.assertIn("function renderTicketDetailView()", app_source)
        self.assertIn("Current issue", app_source)
        self.assertIn("Action needed", app_source)
        self.assertIn("Engineer Ticket Command", html)
        self.assertIn("Start Investigation", app_source)
        self.assertNotIn("Open Workspace", app_source)
        self.assertIn('window.addEventListener("hashchange"', app_source)
        self.assertIn('class="rail-footer"', html)
        self.assertIn('class="rail-status-icon realtime-icon"', html)
        self.assertNotIn('class="engineer-topbar-meta"', html)
        self.assertNotIn('class="engineer-topbar"', html)
        self.assertNotIn("Active Pool", app_source)
        self.assertNotIn("ticket-pool-hero", app_source)
        self.assertNotIn('id="detail-modal"', html)
        self.assertNotIn("detailModalEl", app_source)
        self.assertNotIn("boardScreenEl", app_source)
        self.assertNotIn("ticketTableBodyEl", app_source)
        self.assertNotIn("detailBodyEl", app_source)
        self.assertNotIn("AI Managing", app_source)
        self.assertNotIn("Human Takeover", app_source)
        self.assertNotIn("engineer_mode", app_source)
        self.assertNotIn("Noto Sans SC", css)
        self.assertIn("--surface:", css)
        self.assertIn("--primary:", css)
        self.assertIn(".engineer-shell", css)
        self.assertIn(".rail-footer", css)
        self.assertIn(".rail-footer {\n  display: grid;\n  gap: 12px;\n  justify-items: center;", css)
        self.assertIn(".engineer-rail:hover .rail-footer {\n  justify-items: stretch;", css)
        self.assertIn(".rail-status-card {\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  width: 52px;", css)
        self.assertIn("  height: 52px;", css)
        self.assertIn("  gap: 0;", css)
        self.assertIn("  border-radius: 18px;", css)
        self.assertIn(".engineer-rail:hover .rail-status-card {\n  width: 100%;\n  justify-content: flex-start;\n  gap: 12px;", css)
        self.assertIn(".realtime-icon {\n  width: 20px;\n  height: 20px;", css)
        self.assertIn(".engineer-rail .user-profile-chip {\n  display: none;", css)
        self.assertIn(".engineer-rail:hover .user-profile-chip {\n  display: flex;", css)
        self.assertIn(".ticket-pool-list", css)
        self.assertIn(".ticket-row", css)
        self.assertIn(".ticket-pool-grid", css)
        self.assertIn(".pool-view-toggle", css)
        self.assertIn(".status-open {\n  color: #2f6f44;", css)
        self.assertIn(".status-communicating {\n  color: var(--primary);", css)
        self.assertIn(".status-escalated {\n  color: var(--danger);", css)
        self.assertIn(".status-investigating {\n  color: var(--warning);", css)
        self.assertIn(".status-resolved {\n  color: var(--ink-muted);", css)
        self.assertIn(".status-surface-open", css)
        self.assertIn(".status-surface-communicating", css)
        self.assertNotIn("detail-pane-new-messages", app_source)
        self.assertNotIn("detail-new-messages-btn", app_source)
        self.assertNotIn("jump-detail-thread-latest", app_source)
        self.assertNotIn("jump-detail-timeline-latest", app_source)
        self.assertNotIn(".detail-pane-new-messages", css)
        self.assertNotIn(".detail-new-messages-btn", css)
        self.assertIn(".status-surface-escalated", css)
        self.assertIn(".status-surface-investigating", css)
        self.assertIn(".status-surface-resolved", css)
        self.assertIn(".detail-back-icon-btn", css)
        self.assertIn('font-family: "Material Symbols Outlined";', css)
        self.assertIn("html.material-symbols-pending .material-symbols-outlined", css)
        self.assertIn("visibility: hidden;", css)
        self.assertIn(".ticket-workspace", css)
        self.assertNotIn("priorityLabel(", app_source)
        self.assertNotIn(".priority-badge", css)
        self.assertNotIn('FILTER_KEYS = ["priority"]', app_source)

    def test_engineer_ui_uses_shared_composer_bundle_contract(self) -> None:
        app_source = Path("ui/engineer-ui/app.js").read_text(encoding="utf-8")
        shared_source = Path("ui/shared-ui/composer.js").read_text(encoding="utf-8")

        self.assertIn("globalThis.SupportPortalComposer", app_source)
        self.assertIn("renderMarkdownMessage", shared_source)
        self.assertIn("buildDefaultComposerToolbarState", shared_source)
        self.assertIn("serializeRichComposerHtmlToMarkdown", shared_source)
        self.assertIn("captureComposerPreservationState", shared_source)
        self.assertNotIn("function captureComposerPreservationState(", app_source)
        self.assertNotIn("function restoreComposerPreservationState(", app_source)

    def test_engineer_shared_composer_toolbar_buttons_reset_native_button_chrome(self) -> None:
        shared_css = Path("ui/shared-ui/composer.css").read_text(encoding="utf-8")
        engineer_css = Path("ui/engineer-ui/styles.css").read_text(encoding="utf-8")

        self.assertRegex(
            shared_css,
            r"\.new-ticket-toolbar-button\s*\{[^}]*padding:\s*0;[^}]*border:\s*0;[^}]*appearance:\s*none;[^}]*-webkit-appearance:\s*none;[^}]*background:\s*transparent;[^}]*box-shadow:\s*none;",
        )
        self.assertRegex(
            shared_css,
            r"\.new-ticket-summary-toolbar-btn\s*\{[^}]*padding:\s*0\s+14px;[^}]*border:\s*0;[^}]*appearance:\s*none;[^}]*-webkit-appearance:\s*none;[^}]*box-shadow:\s*none;",
        )
        self.assertNotRegex(engineer_css, r"\.new-ticket-toolbar-button\s*\{")
        self.assertNotRegex(engineer_css, r"\.new-ticket-summary-toolbar-btn\s*\{")

    def test_engineer_ticket_pool_defaults_to_investigating_tab_and_excludes_open(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                tickets = [
                  {
                    ticket_id: "TK-OPEN-LOW",
                    subject: "open ticket",
                    requester: "user-open",
                    status: "open",
                    created_at: "2026-03-24T07:00:00+00:00",
                    updated_at: "2026-03-24T07:10:00+00:00",
                  },
                  {
                    ticket_id: "TK-COMM-URGENT",
                    subject: "communicating urgent ticket",
                    requester: "user-1",
                    status: "communicating",
                    created_at: "2026-03-24T09:00:00+00:00",
                    updated_at: "2026-03-24T10:00:00+00:00",
                  },
                  {
                    ticket_id: "TK-INVESTIGATING-NEW",
                    subject: "investigating newest ticket",
                    requester: "user-2",
                    status: "investigating",
                    created_at: "2026-03-24T08:00:00+00:00",
                    updated_at: "2026-03-24T08:30:00+00:00",
                    active_investigation: {
                      id: "INV-200",
                      state: "active",
                      draft_customer_reply: "",
                      messages: [
                        {
                          id: "INV-200-m1",
                          role: "engineer_ai",
                          content: "Please confirm whether the issue only reproduces on Android 14.",
                          created_at: "2026-03-24T08:25:00+00:00",
                        },
                      ],
                    },
                  },
                  {
                    ticket_id: "TK-INVESTIGATING-OLD",
                    subject: "investigating older ticket",
                    requester: "user-2b",
                    status: "investigating",
                    created_at: "2026-03-24T07:40:00+00:00",
                    updated_at: "2026-03-24T08:10:00+00:00",
                  },
                  {
                    ticket_id: "TK-ESCALATED-NORMAL",
                    subject: "escalated ticket",
                    requester: "user-3",
                    status: "escalated",
                    created_at: "2026-03-24T08:40:00+00:00",
                    updated_at: "2026-03-24T09:20:00+00:00",
                  },
                  {
                    ticket_id: "TK-RESOLVED-NORMAL",
                    subject: "resolved ticket",
                    requester: "user-4",
                    status: "resolved",
                    created_at: "2026-03-24T06:00:00+00:00",
                    updated_at: "2026-03-24T06:20:00+00:00",
                  },
                ];

                if (selectedPoolStatus !== "investigating") {{
                  throw new Error("Engineer workspace should default to the investigating tab.");
                }}

                renderRailNav();
                const railHtml = railNavEl.innerHTML;
                if (railHtml.includes("Ticket Pool")) {{
                  throw new Error("Rail should no longer render the legacy Ticket Pool button.");
                }}
                if (railHtml.includes("Active Ticket")) {{
                  throw new Error("Rail should not render a dedicated detail item anymore.");
                }}
                const investigatingRailIndex = railHtml.indexOf("Investigating");
                const escalatedRailIndex = railHtml.indexOf("Escalated");
                const communicatingRailIndex = railHtml.indexOf("Communicating");
                const resolvedRailIndex = railHtml.indexOf("Resolved");
                if (
                  investigatingRailIndex === -1 ||
                  escalatedRailIndex === -1 ||
                  communicatingRailIndex === -1 ||
                  resolvedRailIndex === -1
                ) {{
                  throw new Error("Rail should render all four engineer status tabs.");
                }}
                if (
                  !(investigatingRailIndex < escalatedRailIndex &&
                    escalatedRailIndex < communicatingRailIndex &&
                    communicatingRailIndex < resolvedRailIndex)
                ) {{
                  throw new Error("Rail tabs should be ordered investigating, escalated, communicating, resolved.");
                }}

                renderFilterControls();
                const controlsHtml = filterControlsEl.innerHTML;
                if (!controlsHtml.includes("pool-view-toggle")) {{
                  throw new Error("Filter row should render the list/grid view toggle.");
                }}
                if (!controlsHtml.includes('data-pool-view-option="list"')) {{
                  throw new Error("Filter row should expose the list view option.");
                }}
                if (!controlsHtml.includes('data-pool-view-option="grid"')) {{
                  throw new Error("Filter row should expose the grid view option.");
                }}
                if (controlsHtml.includes("All Priority")) {{
                  throw new Error("Filter row should no longer render the priority combobox.");
                }}
                if (controlsHtml.includes("All Status")) {{
                  throw new Error("Engineer pool should no longer render the status combobox.");
                }}

                const html = renderTicketPoolView();
                if (html.includes("ticket-card")) {{
                  throw new Error("Ticket pool should no longer render card layout.");
                }}
                if (html.includes("Open Workspace")) {{
                  throw new Error("Ticket pool rows should no longer render an Open Workspace button.");
                }}
                if (!html.includes("ticket-pool-list")) {{
                  throw new Error("Ticket pool should render the list container by default.");
                }}
                if (!html.includes("ticket-row")) {{
                  throw new Error("Ticket pool should render list rows.");
                }}
                if (html.includes("ticket-pool-grid")) {{
                  throw new Error("List mode should not render the grid container.");
                }}
                if (!html.includes("status-surface-investigating")) {{
                  throw new Error("Investigating list cards should use the investigating surface class.");
                }}
                if (!html.includes('data-ticket-row="true"')) {{
                  throw new Error("Ticket pool rows should be marked as directly clickable rows.");
                }}
                if (!html.includes('role="button"')) {{
                  throw new Error("Ticket pool rows should expose button semantics.");
                }}
                if (!html.includes('tabindex="0"')) {{
                  throw new Error("Ticket pool rows should be keyboard focusable.");
                }}
                if (html.includes("mode-pill")) {{
                  throw new Error("Ticket pool rows should not render a mode badge anymore.");
                }}
                if (!html.includes("Investigation Update")) {{
                  throw new Error("Ticket pool rows should render the latest investigation update label when present.");
                }}
                if (!html.includes("Please confirm whether the issue only reproduces on Android 14.")) {{
                  throw new Error("Ticket pool rows should render the latest investigation preview inline.");
                }}
                const requestMatches = html.match(/ticket-row-request/g) || [];
                if (requestMatches.length !== 1) {{
                  throw new Error("Only tickets with investigation preview text should render a request block.");
                }}

                if (!html.includes("Investigating</span>")) {{
                  throw new Error("Metrics should include the investigating card.");
                }}
                if (!html.includes("Escalated</span>")) {{
                  throw new Error("Metrics should include the escalated card.");
                }}
                if (!html.includes("Communicating</span>")) {{
                  throw new Error("Metrics should include the communicating card.");
                }}
                if (!html.includes("Resolved</span>")) {{
                  throw new Error("Metrics should include the resolved card.");
                }}
                const metricMatches = html.match(/class="metric-card"/g) || [];
                if (metricMatches.length !== 4) {{
                  throw new Error("Engineer pool should render exactly four status metric cards.");
                }}
                if (html.includes("Total Tickets")) {{
                  throw new Error("Engineer pool should no longer render the total tickets metric.");
                }}

                if (!html.includes("TK-INVESTIGATING-NEW")) {{
                  throw new Error("Investigating tab should render investigating tickets.");
                }}
                if (!html.includes("TK-INVESTIGATING-OLD")) {{
                  throw new Error("Investigating tab should keep rendering the rest of the investigating queue.");
                }}
                if (html.includes("TK-COMM-URGENT")) {{
                  throw new Error("Investigating tab should not render communicating tickets.");
                }}
                if (html.includes("TK-ESCALATED-NORMAL")) {{
                  throw new Error("Investigating tab should not render escalated tickets.");
                }}
                if (html.includes("TK-RESOLVED-NORMAL")) {{
                  throw new Error("Investigating tab should not render resolved tickets.");
                }}
                if (html.includes("TK-OPEN-LOW")) {{
                  throw new Error("Open tickets should not appear anywhere in the engineer pool.");
                }}

                const waitingRowStart = html.indexOf('data-ticket-id="TK-INVESTIGATING-NEW"');
                const waitingRowEnd = html.indexOf("</article>", waitingRowStart);
                const waitingRowMarkup = html.slice(waitingRowStart, waitingRowEnd);
                const badgeIndex = waitingRowMarkup.indexOf("status-badge");
                const secondLineIndex = waitingRowMarkup.indexOf("ticket-row-secondary");
                if (badgeIndex === -1 || secondLineIndex === -1 || badgeIndex > secondLineIndex) {{
                  throw new Error("Status badges should stay in the first row alongside the title.");
                }}
                if (html.includes("priority-badge")) {{
                  throw new Error("Ticket pool should not render any priority badge.");
                }}
                if (
                  html.indexOf("TK-INVESTIGATING-NEW") >
                  html.indexOf("TK-INVESTIGATING-OLD")
                ) {{
                  throw new Error("Investigating tab should sort tickets by latest update, not priority.");
                }}
                """
            )
        )

    def test_engineer_ticket_pool_shows_loading_state_before_first_queue_snapshot(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                tickets = [];
                boardLoading = true;

                const html = renderTicketPoolView();
                if (!html.includes("pool-loading-state")) {{
                  throw new Error("Ticket pool should render a dedicated loading state before the first queue snapshot arrives.");
                }}
                if (!html.includes("Loading tickets...")) {{
                  throw new Error("Ticket pool loading state should explain that tickets are loading.");
                }}
                if (!html.includes("Fetching the latest engineer queue snapshot.")) {{
                  throw new Error("Ticket pool loading state should render supporting copy.");
                }}
                if (!html.includes("loading-spinner")) {{
                  throw new Error("Ticket pool loading state should include the loading spinner.");
                }}
                if (html.includes("No tickets match the current filters.")) {{
                  throw new Error("Initial queue loading should not render the empty filter state.");
                }}
              """
            )
        )

    def test_engineer_ticket_pool_restores_grid_view_preference_and_renders_cards(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                localStorage.setItem(TICKET_POOL_VIEW_STORAGE_KEY, "grid");
                hydrateTicketPoolViewMode();
                selectedPoolStatus = "escalated";

                tickets = [
                  {
                    ticket_id: "TK-GRID-001",
                    subject: "investigating grid ticket",
                    requester: "user-grid",
                    status: "investigating",
                    created_at: "2026-03-24T08:00:00+00:00",
                    updated_at: "2026-03-24T08:30:00+00:00",
                    active_investigation: {
                      id: "INV-GRID-001",
                      state: "active",
                      draft_customer_reply: "",
                      messages: [
                        {
                          id: "INV-GRID-001-m1",
                          role: "engineer_ai",
                          content: "Please confirm whether the issue only reproduces on Android 14.",
                          created_at: "2026-03-24T08:25:00+00:00",
                        },
                      ],
                    },
                  },
                  {
                    ticket_id: "TK-GRID-002",
                    subject: "escalated fallback ticket",
                    requester: "user-grid-2",
                    status: "escalated",
                    created_at: "2026-03-24T08:10:00+00:00",
                    updated_at: "2026-03-24T08:15:00+00:00",
                    active_investigation: {
                      id: "INV-GRID-002",
                      state: "active",
                      draft_customer_reply: "",
                      messages: [
                        {
                          id: "INV-GRID-002-m1",
                          role: "engineer_ai",
                          content: "Need the exact failing room join URL from the customer.",
                          created_at: "2026-03-24T08:14:00+00:00",
                        },
                      ],
                    },
                  },
                  {
                    ticket_id: "TK-GRID-003",
                    subject: "open client-only ticket",
                    requester: "user-grid-3",
                    status: "open",
                    created_at: "2026-03-24T08:10:00+00:00",
                    updated_at: "2026-03-24T08:15:00+00:00",
                  },
                ];

                const html = renderTicketPoolView();
                if (!html.includes('data-pool-view-mode="grid"')) {{
                  throw new Error("Grid preference should restore the grid view container.");
                }}
                if (!html.includes("ticket-pool-grid")) {{
                  throw new Error("Grid view should render the grid container.");
                }}
                if (html.includes("ticket-pool-list")) {{
                  throw new Error("Grid mode should not render the list container.");
                }}
                if (!html.includes("status-surface-escalated")) {{
                  throw new Error("Escalated grid cards should use the escalated surface class.");
                }}
                if (!html.includes("ticket-pool-card")) {{
                  throw new Error("Grid mode should render compact pool cards.");
                }}
                if (!html.includes('data-ticket-row="true"')) {{
                  throw new Error("Grid cards should stay directly clickable.");
                }}
                if (!html.includes('role="button"')) {{
                  throw new Error("Grid cards should expose button semantics.");
                }}
                if (!html.includes('tabindex="0"')) {{
                  throw new Error("Grid cards should remain keyboard focusable.");
                }}
                if (!html.includes("ticket-pool-card-preview")) {{
                  throw new Error("Grid cards should render an investigation preview block when text exists.");
                }}
                if (html.includes("priority-badge")) {{
                  throw new Error("Grid cards should not render any priority badge.");
                }}
                if (!html.includes("TK-GRID-002")) {{
                  throw new Error("Escalated tab should render escalated tickets in grid mode.");
                }}
                if (!html.includes("Need the exact failing room join URL from the customer.")) {{
                  throw new Error("Grid cards should include the latest investigation preview text.");
                }}
                if (html.includes("TK-GRID-001")) {{
                  throw new Error("Escalated tab should not render investigating tickets in grid mode.");
                }}
                if (html.includes("TK-GRID-003")) {{
                  throw new Error("Open tickets should remain hidden in grid mode.");
                }}
              """
            )
        )

    def test_engineer_ticket_pool_empty_state_is_status_aware(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                tickets = [
                  {
                    ticket_id: "TK-EMPTY-COMM",
                    subject: "communicating ticket",
                    requester: "user-empty",
                    status: "communicating",
                    created_at: "2026-03-24T09:00:00+00:00",
                    updated_at: "2026-03-24T10:00:00+00:00",
                  },
                ];
                selectedPoolStatus = "resolved";

                const html = renderTicketPoolView();
                if (!html.includes("No resolved tickets right now.")) {{
                  throw new Error("Empty state should mention the currently selected engineer status tab.");
                }}
                if (html.includes("No tickets match the current filters.")) {{
                  throw new Error("Engineer empty state should be status-aware instead of generic.");
                }}
              """
            )
        )

    def test_engineer_pool_and_detail_use_engineer_case_identity_with_parent_client_reference(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                tickets = [
                  {
                    ticket_id: "TK-040-1",
                    engineer_case_id: "TK-040-1",
                    title: "black screen issue",
                    subject: "how to join channel",
                    requester: "user-1",
                    customer_id: "user-1",
                    status: "investigating",
                    client_ticket_ref: {
                      ticket_id: "TK-040",
                      subject: "how to join channel",
                    },
                    created_at: "2026-04-02T08:00:00+00:00",
                    updated_at: "2026-04-02T08:10:00+00:00",
                    active_investigation: {
                      id: "TK-040-1",
                      state: "active",
                      messages: [
                        {
                          id: "TK-040-1-m1",
                          role: "engineer_ai",
                          content: "Please confirm whether this black screen issue reproduces on all devices.",
                          created_at: "2026-04-02T08:05:00+00:00",
                        },
                      ],
                    },
                  },
                ];
                selectedPoolStatus = "investigating";

                const poolHtml = renderTicketPoolView();
                if (!poolHtml.includes("TK-040-1")) {{
                  throw new Error("Engineer pool should use the engineer case id as the primary visible id.");
                }}
                if (!poolHtml.includes("black screen issue")) {{
                  throw new Error("Engineer pool should render the engineer case title snapshot.");
                }}
                if (!poolHtml.includes("Client Ticket</strong> TK-040 · how to join channel")) {{
                  throw new Error("Engineer pool should render the parent client ticket reference as secondary metadata.");
                }}

                selectedTicketId = "TK-040-1";
                selectedTicket = tickets[0];
                selectedTicketSummary = "Current understanding: black screen issue blocks the client AI flow.";
                selectedTicketNextAction = "Confirm the affected device scope.";

                const detailHtml = renderTicketDetailView();
                if (!detailHtml.includes('class="workspace-ticket-id">TK-040-1<')) {{
                  throw new Error("Engineer detail should show the engineer case id inline in the primary header row.");
                }}
                if (!detailHtml.includes(">black screen issue<")) {{
                  throw new Error("Engineer detail should use the engineer case title as the primary title.");
                }}
                if (!detailHtml.includes("Client Ticket TK-040 · how to join channel")) {{
                  throw new Error("Engineer detail should show the linked parent client ticket reference.");
                }}
                if (detailHtml.includes(">how to join channel</h2>")) {{
                  throw new Error("Engineer detail title should not fall back to the parent client ticket subject.");
                }}
              """
            )
        )

    def test_engineer_ticket_pool_view_toggle_persists_selected_mode(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                if (ticketPoolViewMode !== "list") {{
                  throw new Error("Ticket pool view should default to list.");
                }}

                applyTicketPoolViewMode("grid");
                if (ticketPoolViewMode !== "grid") {{
                  throw new Error("Applying grid mode should update local state.");
                }}
                if (localStorage.getItem(TICKET_POOL_VIEW_STORAGE_KEY) !== "grid") {{
                  throw new Error("Applying grid mode should persist the preference.");
                }}

                applyTicketPoolViewMode("list");
                if (localStorage.getItem(TICKET_POOL_VIEW_STORAGE_KEY) !== "list") {{
                  throw new Error("Applying list mode should overwrite the saved preference.");
                }}
              """
            )
        )

    def test_engineer_detail_compact_thread_panel_stretches_without_shrinking_card(self) -> None:
        css = Path("ui/engineer-ui/styles.css").read_text(encoding="utf-8")
        marker = ".conversation-panel-compact-thread {"
        start = css.find(marker)
        self.assertNotEqual(start, -1, msg="Engineer detail should keep the compact thread panel variant.")
        end = css.find("}", start)
        self.assertNotEqual(end, -1, msg="Compact thread panel block should be closed.")
        block = css[start:end]

        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto;", block)
        self.assertNotIn(
            "align-self: start;",
            block,
            msg="Compact thread panel should keep the engineer thread stretched even after the standalone summary card is removed.",
        )
        list_marker = ".conversation-panel-compact-thread .message-list-compact-thread {"
        list_start = css.find(list_marker)
        self.assertNotEqual(
            list_start,
            -1,
            msg="Engineer detail should scope its stretch-and-scroll rules to the compact thread message list.",
        )
        list_end = css.find("}", list_start)
        self.assertNotEqual(list_end, -1, msg="Compact thread message-list block should be closed.")
        list_block = css[list_start:list_end]
        self.assertIn("min-height: 0;", list_block)
        self.assertIn("justify-content: flex-start;", list_block)
        self.assertIn("max-height: none;", list_block)
        self.assertIn(".message-list-compact-thread .message-item", css)
        self.assertIn(".message-item-pending-ai", css)
        self.assertIn(".detail-thinking-dots", css)
        self.assertIn(".detail-thinking-label", css)

        static_marker = ".detail-conversation-static {"
        static_start = css.find(static_marker)
        self.assertNotEqual(
            static_start,
            -1,
            msg="Engineer detail should wrap the investigation static region in a dedicated spacing container.",
        )
        static_end = css.find("}", static_start)
        self.assertNotEqual(static_end, -1, msg="Detail conversation static wrapper block should be closed.")
        static_block = css[static_start:static_end]
        self.assertIn("display: grid;", static_block)
        self.assertIn("gap: 16px;", static_block)
        self.assertIn("min-height: 0;", static_block)

        thread_body_marker = ".detail-conversation-thread-body {"
        thread_body_start = css.find(thread_body_marker)
        self.assertNotEqual(
            thread_body_start,
            -1,
            msg="Engineer detail should expose a dedicated scroll body wrapper inside the fixed-height thread card.",
        )
        thread_body_end = css.find("}", thread_body_start)
        self.assertNotEqual(thread_body_end, -1, msg="Detail conversation thread body block should be closed.")
        thread_body_block = css[thread_body_start:thread_body_end]
        self.assertIn("min-height: 0;", thread_body_block)

        timeline_panel_marker = ".detail-timeline-panel {"
        timeline_panel_start = css.find(timeline_panel_marker)
        self.assertNotEqual(
            timeline_panel_start,
            -1,
            msg="Engineer detail should scope the customer timeline card for fixed-height desktop layout.",
        )
        timeline_panel_end = css.find("}", timeline_panel_start)
        self.assertNotEqual(timeline_panel_end, -1, msg="Detail timeline panel block should be closed.")
        timeline_panel_block = css[timeline_panel_start:timeline_panel_end]
        self.assertIn("grid-template-rows: auto minmax(0, 1fr);", timeline_panel_block)

        timeline_body_marker = ".detail-timeline-body {"
        timeline_body_start = css.find(timeline_body_marker)
        self.assertNotEqual(
            timeline_body_start,
            -1,
            msg="Engineer detail should expose a dedicated customer timeline body wrapper for scrolling.",
        )
        timeline_body_end = css.find("}", timeline_body_start)
        self.assertNotEqual(timeline_body_end, -1, msg="Detail timeline body block should be closed.")
        timeline_body_block = css[timeline_body_start:timeline_body_end]
        self.assertIn("min-height: 0;", timeline_body_block)

        readiness_body_marker = ".detail-readiness-body {"
        readiness_body_start = css.find(readiness_body_marker)
        self.assertNotEqual(
            readiness_body_start,
            -1,
            msg="Internal Review should expose a dedicated scroll body wrapper below its fixed header.",
        )
        readiness_body_end = css.find("}", readiness_body_start)
        self.assertNotEqual(readiness_body_end, -1, msg="Detail readiness body block should be closed.")
        readiness_body_block = css[readiness_body_start:readiness_body_end]
        self.assertIn("display: grid;", readiness_body_block)
        self.assertIn("gap: 14px;", readiness_body_block)
        self.assertIn("min-height: 0;", readiness_body_block)

        desktop_media_marker = "@media (min-width: 1181px) {"
        desktop_media_start = css.find(desktop_media_marker)
        self.assertNotEqual(
            desktop_media_start,
            -1,
            msg="Engineer detail fixed right-sidebar sections should be scoped to the desktop two-column layout.",
        )
        mobile_media_start = css.find("@media (max-width: 1180px) {", desktop_media_start)
        self.assertNotEqual(
            mobile_media_start,
            -1,
            msg="Engineer detail should keep the existing single-column mobile/tablet breakpoint after desktop sidebar sizing is added.",
        )
        desktop_block = css[desktop_media_start:mobile_media_start]
        self.assertIn("--engineer-detail-timeline-height: 360px;", desktop_block)
        self.assertIn("--engineer-detail-readiness-review-height: 420px;", desktop_block)
        self.assertIn("--engineer-detail-sidebar-stack-gap: 14px;", desktop_block)
        self.assertIn("--engineer-detail-thread-panel-height: calc(", desktop_block)
        self.assertIn(".conversation-panel-compact-thread {", desktop_block)
        self.assertIn("height: var(--engineer-detail-thread-panel-height);", desktop_block)
        self.assertIn(".detail-timeline-panel {", desktop_block)
        self.assertIn("height: var(--engineer-detail-timeline-height);", desktop_block)
        self.assertIn(".detail-readiness-review {", desktop_block)
        self.assertIn("height: var(--engineer-detail-readiness-review-height);", desktop_block)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr);", desktop_block)
        self.assertIn(".detail-conversation-thread-body {", desktop_block)
        self.assertIn("overflow-y: auto;", desktop_block)
        self.assertIn("scrollbar-gutter: stable;", desktop_block)
        self.assertIn("overscroll-behavior: contain;", desktop_block)
        self.assertIn("align-content: start;", desktop_block)
        self.assertIn(".conversation-panel-compact-thread .message-list-compact-thread {", desktop_block)
        self.assertIn("overflow-y: visible;", desktop_block)
        self.assertIn(".detail-timeline-panel .message-list {", desktop_block)
        self.assertIn(".detail-readiness-body {", desktop_block)

        self.assertNotIn(
            "height: clamp(580px, 68vh, 820px);",
            css,
            msg="Engineer detail should no longer force the entire desktop detail workspace to a viewport-based height.",
        )
        self.assertNotIn(
            "grid-template-rows: minmax(0, 1fr) auto;",
            css[css.find(".insight-panel {"):css.find("}", css.find(".insight-panel {"))],
            msg="Engineer detail sidebar should return to natural vertical stacking instead of a forced two-row split.",
        )
        self.assertNotIn(
            ".detail-timeline-panel,\n  .detail-timeline-body,\n  .detail-timeline-panel .message-list {\n    height: 100%;",
            css,
            msg="Customer timeline should not be forced to consume the full sidebar height on desktop.",
        )

        base_layout_end = css.find("}", css.find(".workspace-layout {"))
        base_layout_block = css[css.find(".workspace-layout {"):base_layout_end]
        self.assertIn("align-items: stretch;", base_layout_block)

    def test_engineer_detail_prioritizes_internal_investigation_workspace_and_confirmation(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                selectedTicketId = "TK-DETAIL-INV";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-INV",
                  subject: "Android 14 token renew regression",
                  requester: "user-1",
                  status: "investigating",
                  created_at: "2026-03-24T09:00:00+08:00",
                  updated_at: "2026-03-24T09:25:00+08:00",
                  client_ticket_ref: {
                    ticket_id: "TK-041",
                    subject: "Token Renew Regression",
                  },
                  messages: [
                    {
                      role: "customer",
                      content: "Token renew callback does not fire on Android 14.",
                      created_at: "2026-03-24T08:00:00+00:00",
                    },
                    {
                      role: "assistant",
                      content: "This issue requires further internal investigation, which may take some time. Thank you for your patience. We expect to reply or update you here within 20 minutes.",
                      created_at: "2026-03-24T08:01:00+00:00",
                    },
                  ],
                  active_investigation: {
                    id: "INV-DETAIL-1",
                    state: "awaiting_confirmation",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                    final_confirmation_requested_at: "2026-03-24T09:05:00+00:00",
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-1-m1",
                        role: "engineer_ai",
                        content: "Please confirm whether the issue only reproduces on Android 14.",
                        created_at: "2026-03-24T08:02:00+00:00",
                      },
                      {
                        id: "INV-DETAIL-1-m2",
                        role: "engineer",
                        content: "Confirmed. Reproduces on Android 14 with SDK 4.2.1 only.",
                        created_at: "2026-03-24T08:20:00+00:00",
                      },
                      {
                        id: "INV-DETAIL-1-m3",
                        role: "engineer_ai",
                        content: "I drafted a customer follow-up asking whether the issue is limited to Android 14. Please confirm whether it is ready to send.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  engineer_agent_state: {
                    phase: "awaiting_confirmation",
                    issue_understanding: "Android 14 token renew callback fails on SDK 4.2.1.",
                    knowledge_summary: "The regression reproduces on Android 14 and the draft upgrade guidance is ready for final review.",
                    why_not_solved: "Sid did not have reproducible platform-scoped evidence before the engineer confirmed Android 14 plus SDK 4.2.1.",
                    known_facts: [
                      "Customer reported token renew callback failures on Android 14.",
                      "The engineer reproduced the issue on Android 14 with SDK 4.2.1 only.",
                    ],
                    missing_information: [
                      "Confirm the customer can upgrade to SDK 4.2.2.",
                    ],
                    next_request_for_engineer: "Confirm the customer can upgrade to SDK 4.2.2 before approving the reply.",
                    resolution_hypothesis: "Upgrading to SDK 4.2.2 should resolve the regression.",
                    ready_to_reply: true,
                    reply_readiness: {
                      has_conclusion: true,
                      has_proof: true,
                      has_solution_or_next_step: true,
                      conclusion_summary: "Android 14 with SDK 4.2.1 reproduces the token renew failure.",
                      proof_summary: "The engineer reproduced the issue on Android 14 with SDK 4.2.1 only.",
                      proof_anchors: ["Android 14", "SDK 4.2.1"],
                      solution_or_next_step: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                      blockers: [],
                      critique: "The current evidence supports the customer-safe SDK upgrade guidance.",
                      ready_for_customer_reply: true,
                    },
                  },
                  investigation_history: [
                    {
                      id: "INV-DETAIL-0",
                      state: "closed",
                      trigger_reason: "legacy_pending_question",
                      trigger_source: "legacy_waiting_for_engineer",
                      draft_customer_reply: "",
                      opened_at: "2026-03-23T10:00:00+00:00",
                      updated_at: "2026-03-23T10:20:00+00:00",
                      closed_at: "2026-03-23T10:20:00+00:00",
                      messages: [],
                    },
                  ],
                  engineer_request_records: [],
                };

                const html = renderTicketDetailView();
                const primaryLineStart = html.indexOf('class="workspace-header-line workspace-header-line-primary"');
                const secondaryLineStart = html.indexOf('class="workspace-header-line workspace-header-line-secondary"');
                if (primaryLineStart === -1 || secondaryLineStart === -1 || primaryLineStart > secondaryLineStart) {{
                  throw new Error("Detail header should render a compact two-line identity banner.");
                }}
                const primaryLineMarkup = html.slice(primaryLineStart, secondaryLineStart);
                const secondaryLineMarkup = html.slice(secondaryLineStart, html.indexOf("</header>", secondaryLineStart));
                if (!primaryLineMarkup.includes('detail-back-icon-btn')) {{
                  throw new Error("Primary banner row should render the icon-only back action.");
                }}
                if (!primaryLineMarkup.includes('aria-label="Back to Pool"')) {{
                  throw new Error("Back action should keep an accessible label.");
                }}
                if (!primaryLineMarkup.includes("arrow_back")) {{
                  throw new Error("Back action should render the arrow_back icon.");
                }}
                if (primaryLineMarkup.includes(">Back to Pool<")) {{
                  throw new Error("Primary banner row should not keep visible Back to Pool text.");
                }}
                if (primaryLineMarkup.includes("Sync Ticket")) {{
                  throw new Error("Primary banner row should not keep the removed sync action.");
                }}
                if (html.includes('class="workspace-header-top"') || html.includes('class="workspace-header-main"')) {{
                  throw new Error("Detail header should no longer render separate toolbar and title rows.");
                }}
                if (!primaryLineMarkup.includes('class="workspace-ticket-id">TK-DETAIL-INV<')) {{
                  throw new Error("Primary banner row should keep the engineer case id inline.");
                }}
                if (!primaryLineMarkup.includes("status-badge") || !primaryLineMarkup.includes("status-badge-compact")) {{
                  throw new Error("Primary banner row should carry the compact status badge.");
                }}
                if (primaryLineMarkup.includes("priority-badge")) {{
                  throw new Error("Primary banner row should no longer render a priority badge.");
                }}
                if (primaryLineMarkup.includes("mode-pill")) {{
                  throw new Error("Primary banner row should not render the removed mode pill.");
                }}
                if (!primaryLineMarkup.includes("workspace-ticket-title")) {{
                  throw new Error("Primary banner row should carry the engineer case title.");
                }}
                if (!secondaryLineMarkup.includes("Client Ticket TK-041 · Token Renew Regression")) {{
                  throw new Error("Secondary banner row should keep the full client ticket reference.");
                }}
                if (!secondaryLineMarkup.includes("Requester user-1")) {{
                  throw new Error("Secondary banner row should keep the requester metadata.");
                }}
                if (!secondaryLineMarkup.includes("Created 03/24 09:00")) {{
                  throw new Error("Secondary banner row should keep the created timestamp.");
                }}
                if (!secondaryLineMarkup.includes("Updated 03/24 09:25")) {{
                  throw new Error("Secondary banner row should keep the updated timestamp.");
                }}
                if (!html.includes("Engineer Ticket Thread")) {{
                  throw new Error("Detail workspace should foreground the engineer ticket thread.");
                }}
                if (html.includes("Internal Investigation Thread")) {{
                  throw new Error("Detail workspace should stop using the old internal investigation label.");
                }}
                if (!html.includes("Customer Timeline")) {{
                  throw new Error("Detail workspace should still render the customer timeline in the supporting column.");
                }}
                if (!html.includes("Approve for Guardrail")) {{
                  throw new Error("Awaiting-confirmation investigations should expose the approve action.");
                }}
                if (!html.includes("detail-investigation-inline-actions")) {{
                  throw new Error("Confirmation actions should render inline inside the investigation chat thread.");
                }}
                if (!html.includes("Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.")) {{
                  throw new Error("Detail workspace should render the draft customer reply for final confirmation.");
                }}
                if (!html.includes("detail-investigation-draft")) {{
                  throw new Error("Final confirmation state should render the draft reply inside the chat thread.");
                }}
                if (html.includes("Mode, Status, and Routing")) {{
                  throw new Error("Detail workspace should no longer render the mode/status/routing section.");
                }}
                if (html.includes("Reply To Engineer AI")) {{
                  throw new Error("Detail workspace should no longer render the engineer AI reply section.");
                }}
                if (html.includes("Investigation History")) {{
                  throw new Error("Detail workspace should no longer render the investigation history section.");
                }}
                if (html.includes("Customer Response Channel")) {{
                  throw new Error("Detail workspace should no longer render the direct customer response section.");
                }}
                if (html.includes("Approve Customer Reply")) {{
                  throw new Error("Awaiting-confirmation state should no longer use a separate confirmation card.");
                }}
                if (html.includes("Engineer Request Records")) {{
                  throw new Error("Detail workspace should use investigation history instead of legacy engineer request records.");
                }}
                const internalIndex = html.indexOf("Engineer Ticket Thread");
                const customerIndex = html.indexOf("Customer Timeline");
                if (internalIndex === -1 || customerIndex === -1 || internalIndex > customerIndex) {{
                  throw new Error("Engineer ticket thread should render ahead of the customer timeline.");
                }}
                const engineerThreadSection = html.slice(internalIndex, customerIndex);
                if (!engineerThreadSection.includes('message-list message-list-compact-thread')) {{
                  throw new Error("Engineer ticket thread should use the compact detail conversation layout.");
                }}
                if (!html.includes('class="detail-conversation-static" data-detail-section="investigation-static"')) {{
                  throw new Error("Engineer ticket thread should wrap its static header and messages in the shared spacing container.");
                }}
                if (!engineerThreadSection.includes('class="detail-conversation-thread-body"')) {{
                  throw new Error("Engineer ticket thread should isolate its scrollable body from the fixed header and composer.");
                }}
                const customerTimelineSection = html.slice(customerIndex);
                if (customerTimelineSection.includes('message-list message-list-compact-thread')) {{
                  throw new Error("Customer timeline should not inherit the compact engineer thread layout.");
                }}
                if (!html.includes('class="panel-card detail-timeline-panel"')) {{
                  throw new Error("Customer timeline should use the dedicated fixed-height timeline card wrapper.");
                }}
                if (!customerTimelineSection.includes('class="detail-timeline-body"')) {{
                  throw new Error("Customer timeline should isolate its scroll body from the fixed-height sidebar stack.");
                }}
                const timelinePanelIndex = html.indexOf('class="panel-card detail-timeline-panel"');
                const readinessReviewIndex = html.indexOf('class="panel-card detail-readiness-review"');
                if (timelinePanelIndex === -1 || readinessReviewIndex === -1 || readinessReviewIndex < timelinePanelIndex) {{
                  throw new Error("Internal Review should remain a natural-flow sibling after the customer timeline card.");
                }}
                if (!customerTimelineSection.includes('class="detail-readiness-body"')) {{
                  throw new Error("Internal Review should expose a dedicated scroll body wrapper below the fixed header.");
                }}
                if (html.includes("AI Summary")) {{
                  throw new Error("Detail workspace should merge the AI summary into the Sid request instead of rendering a separate summary card.");
                }}
                if (!engineerThreadSection.includes("case-buddy-request-sections")) {{
                  throw new Error("The opening Sid request should render the merged structured request layout.");
                }}
                if (!engineerThreadSection.includes("Current issue")) {{
                  throw new Error("The merged Sid request should render the Current issue section.");
                }}
                if (!engineerThreadSection.includes("Action needed")) {{
                  throw new Error("The merged Sid request should render the Action needed section.");
                }}
                if (!/Current issue[\\s\\S]*?<p class="case-buddy-request-summary">Android 14 token renew callback fails on SDK 4\\.2\\.1\\.<\\/p>[\\s\\S]*?<ul[\\s\\S]*?<li>Customer reported token renew callback failures on Android 14\\.<\\/li>[\\s\\S]*?<li>The engineer reproduced the issue on Android 14 with SDK 4\\.2\\.1 only\\.<\\/li>/m.test(engineerThreadSection)) {{
                  throw new Error("Current issue should render a summary paragraph followed by known-fact bullets.");
                }}
                if (!/Action needed[\\s\\S]*?<ul[\\s\\S]*?<li>Confirm the customer can upgrade to SDK 4\\.2\\.2 before approving the reply\\.<\\/li>[\\s\\S]*?<li>Confirm the customer can upgrade to SDK 4\\.2\\.2\\.<\\/li>/m.test(engineerThreadSection)) {{
                  throw new Error("Action needed should render the next request and missing information as bullet points.");
                }}
                if (engineerThreadSection.includes("Sid candidate answer")) {{
                  throw new Error("Current issue should not leak Sid candidate answer text.");
                }}
                if (engineerThreadSection.includes("Why Sid couldn't solve it")) {{
                  throw new Error("The merged Sid request should not render the Why Sid couldn't solve it section anymore.");
                }}
                if (engineerThreadSection.includes("Current understanding:")) {{
                  throw new Error("The merged opening request should no longer show the old summary-style Current understanding label.");
                }}
                const decisionIndex = engineerThreadSection.indexOf("I drafted a customer follow-up asking whether the issue is limited to Android 14. Please confirm whether it is ready to send.");
                const inlineActionsIndex = engineerThreadSection.indexOf("detail-investigation-inline-actions");
                if (decisionIndex === -1 || inlineActionsIndex === -1 || inlineActionsIndex < decisionIndex) {{
                  throw new Error("Inline confirmation actions should appear after the final Engineer AI message.");
                }}
                const mergedRequestIndex = engineerThreadSection.indexOf("case-buddy-request-sections");
                if (mergedRequestIndex === -1 || mergedRequestIndex > decisionIndex) {{
                  throw new Error("Only the opening Sid request should use the merged structured layout.");
                }}
                const draftIndex = engineerThreadSection.indexOf("detail-investigation-draft");
                const approveIndex = engineerThreadSection.indexOf("Approve for Guardrail");
                const composerIndex = engineerThreadSection.indexOf('id="detail-investigation-input"');
                if (draftIndex === -1) {{
                  throw new Error("Engineer thread should render the customer draft directly below the approval request.");
                }}
                if (approveIndex === -1) {{
                  throw new Error("Engineer thread should render the approve action directly under the draft.");
                }}
                if (composerIndex === -1) {{
                  throw new Error("Engineer thread should keep the composer visible so engineers can send draft revisions directly.");
                }}
                if (!(draftIndex < approveIndex && approveIndex < composerIndex)) {{
                  throw new Error("Draft, approve action, and composer should appear in order inside the engineer thread.");
                }}
                if (engineerThreadSection.includes("Ask AI to Revise")) {{
                  throw new Error("Approval state should not render a separate revise button anymore.");
                }}
                if (engineerThreadSection.includes("Back to Communicating")) {{
                  throw new Error("Approval state should not render the resume communicating action.");
                }}
                if (engineerThreadSection.includes("Resolve Ticket")) {{
                  throw new Error("Approval state should not render the resolve action.");
                }}
              """
            )
        )

    def test_engineer_detail_merges_legacy_engineer_request_into_structured_case_buddy_block(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-LEGACY";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-LEGACY",
                  subject: "Black screen happened on March 4th",
                  requester: "user-9",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-LEGACY",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-LEGACY-m1",
                        role: "engineer_ai",
                        content: "Engineer Request:\\nIssue: black screen happened on march 4th at 12pm\\nAction Needed: check backend log regarding black screen issue",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };

                const html = renderTicketDetailView();
                if (!html.includes("Current issue")) {{
                  throw new Error("Legacy engineer request text should still render the Current issue section.");
                }}
                if (!html.includes("Action needed")) {{
                  throw new Error("Legacy engineer request text should still render the Action needed section.");
                }}
                if (!html.includes('<p class="case-buddy-request-summary">black screen happened on march 4th at 12pm</p>')) {{
                  throw new Error("Legacy engineer request issue text should become the Current issue summary paragraph.");
                }}
                if (!html.includes("<li>check backend log regarding black screen issue</li>")) {{
                  throw new Error("Legacy engineer request action text should become a bullet in the Action needed section.");
                }}
                if (html.includes("Why Sid couldn't solve it")) {{
                  throw new Error("Legacy engineer request text should no longer render the Why Sid couldn't solve it section.");
                }}
                if (html.includes("Engineer Request:")) {{
                  throw new Error("Legacy engineer request text should be replaced by the merged structured Sid block.");
                }}
              """
            )
        )

    def test_engineer_detail_refresh_does_not_call_summary_endpoint(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-NO-SUMMARY";
                const requestedUrls = [];
                fetchJson = async (url) => {
                  requestedUrls.push(url);
                  if (url === "/api/engineer/tickets/TK-DETAIL-NO-SUMMARY?include_context=false") {
                    return {
                      ticket: {
                        ticket_id: "TK-DETAIL-NO-SUMMARY",
                        subject: "Black screen after join",
                        requester: "user-10",
                        status: "investigating",
                        created_at: "2026-03-24T08:00:00+00:00",
                        updated_at: "2026-03-24T09:10:00+00:00",
                        messages: [],
                        active_investigation: {
                          id: "INV-DETAIL-NO-SUMMARY",
                          state: "active",
                          trigger_reason: "rag_insufficient_evidence",
                          trigger_source: "support_query",
                          draft_customer_reply: "",
                          final_confirmation_requested_at: null,
                          opened_at: "2026-03-24T08:01:00+00:00",
                          updated_at: "2026-03-24T09:05:00+00:00",
                          messages: [
                            {
                              id: "INV-DETAIL-NO-SUMMARY-m1",
                              role: "engineer_ai",
                              content: "Please share the latest backend logs for the black screen session.",
                              created_at: "2026-03-24T09:05:00+00:00",
                            },
                          ],
                        },
                        engineer_agent_state: {
                          phase: "gather_missing_inputs",
                          issue_understanding: "Black screen happens immediately after join.",
                          known_facts: ["The issue happened after the customer joined the channel."],
                          why_not_solved: "Sid does not yet have backend logs or a reproducible failure trace.",
                          missing_information: ["Backend logs for the affected session"],
                          next_request_for_engineer: "Check the backend logs for the affected session.",
                          ready_to_reply: false,
                        },
                        investigation_history: [],
                      },
                    };
                  }
                  throw new Error(`Unexpected URL: ${url}`);
                };

                await refreshSelectedTicket({ silent: true, showLoading: false });
                await Promise.resolve();
                await Promise.resolve();

                if (!requestedUrls.includes("/api/engineer/tickets/TK-DETAIL-NO-SUMMARY?include_context=false")) {{
                  throw new Error("Engineer detail refresh should request the lightweight detail payload.");
                }}
                if (requestedUrls.includes("/api/engineer/tickets/TK-DETAIL-NO-SUMMARY/summary")) {{
                  throw new Error("Engineer detail refresh should no longer request the summary endpoint.");
                }}
                if (!workspaceRegionEl.innerHTML.includes("Current issue")) {{
                  throw new Error("Engineer detail refresh should still render the merged Sid request from the ticket payload.");
                }}
              """
            )
        )

    def test_engineer_detail_derives_approval_block_from_validated_readiness_when_state_lags(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                selectedTicketId = "TK-DETAIL-READINESS";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-READINESS",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [
                    {
                      role: "customer",
                      content: "Token renew callback does not fire on Android 14.",
                      created_at: "2026-03-24T08:00:00+00:00",
                    },
                  ],
                  active_investigation: {
                    id: "INV-DETAIL-READINESS",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-AGENT-PHASE-m1",
                        role: "engineer_ai",
                        content: "I have enough information now. Please confirm this draft before I reply to the customer.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  engineer_agent_state: {
                    phase: "awaiting_confirmation",
                    ready_to_reply: true,
                    issue_understanding: "Token renew callback fails on Android 14.",
                    knowledge_summary: "Client AI found generic token-renewal guidance.",
                    why_not_solved: "The customer-safe reply has not been approved yet.",
                    goal: "Get engineer approval on the prepared answer.",
                    missing_information: [],
                    next_request_for_engineer: "Approve the prepared customer reply.",
                    reply_readiness: {
                      has_conclusion: true,
                      has_proof: true,
                      has_solution_or_next_step: true,
                      conclusion_summary: "Android 14 with SDK 4.2.1 reproduces the token renew failure.",
                      proof_summary: "The engineer reproduced the issue on Android 14 with SDK 4.2.1 only.",
                      proof_anchors: ["Android 14", "SDK 4.2.1"],
                      solution_or_next_step: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                      blockers: [],
                      critique: "The current evidence supports the customer-safe SDK upgrade guidance.",
                      ready_for_customer_reply: true,
                    },
                    last_refreshed_at: "2026-03-24T09:05:00+00:00",
                  },
                };
                selectedTicketSummary = "Customer-facing answer is ready for approval.";
                selectedTicketNextAction = "Approve the prepared reply.";

                const html = renderTicketDetailView();
                if (!html.includes("Approve for Guardrail")) {{
                  throw new Error("Engineer thread should derive the approval block from backend-validated reply readiness when the investigation state has not caught up yet.");
                }}
                if (html.includes("Ask AI to Revise")) {{
                  throw new Error("Approval-derived states should not render a separate revise button.");
                }}
                if (!html.includes("Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.")) {{
                  throw new Error("Engineer thread should render the backend-validated customer draft.");
                }}
                if (!html.includes('id="detail-investigation-input"')) {{
                  throw new Error("Approval-derived states should keep the composer visible for direct revision notes.");
                }}
              """
            )
        )

    def test_engineer_detail_requires_validated_readiness_before_showing_approve_reply(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                selectedTicketId = "TK-DETAIL-DRAFT-ONLY";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-DRAFT-ONLY",
                  subject: "Black screen after join",
                  requester: "user-9",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [
                    {
                      role: "customer",
                      content: "I got black screen after joining the call.",
                      created_at: "2026-03-24T08:00:00+00:00",
                    },
                  ],
                  active_investigation: {
                    id: "INV-DETAIL-DRAFT-ONLY",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "Could you please share the channel name with us for further investigation?",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-DRAFT-ONLY-m1",
                        role: "engineer_ai",
                        content: "I drafted a customer follow-up asking for the missing channel name.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  engineer_agent_state: {
                    phase: "gather_missing_inputs",
                    ready_to_reply: false,
                    reply_readiness: {
                      has_conclusion: true,
                      has_proof: false,
                      has_solution_or_next_step: true,
                      conclusion_summary: "The current investigation suspects a black-screen decode issue.",
                      proof_summary: "",
                      proof_anchors: [],
                      solution_or_next_step: "Ask the customer to share the channel name for further investigation.",
                      blockers: ["Explicit proof is still missing."],
                      critique: "The draft is not yet backed by explicit proof.",
                      ready_for_customer_reply: false,
                    },
                  },
                };
                selectedTicketSummary = "A customer-safe follow-up draft is prepared.";
                selectedTicketNextAction = "Approve the prepared reply if it is safe.";

                const html = renderTicketDetailView();
                if (html.includes("Approve for Guardrail")) {{
                  throw new Error("Engineer thread should not expose approval actions from draft presence alone when reply readiness is incomplete.");
                }}
                if (!html.includes("Could you please share the channel name with us for further investigation?")) {{
                  throw new Error("Engineer thread should still render the prepared customer draft.");
                }}
              """
            )
        )

    def test_engineer_detail_moves_internal_review_into_sidebar(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                selectedTicketId = "TK-DETAIL-READINESS-BLOCK";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-READINESS-BLOCK",
                  subject: "Black screen after join",
                  requester: "user-9",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [
                    {
                      role: "customer",
                      content: "I got black screen after joining the call.",
                      created_at: "2026-03-24T08:00:00+00:00",
                    },
                  ],
                  active_investigation: {
                    id: "INV-DETAIL-READINESS-BLOCK",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [],
                  },
                  engineer_agent_state: {
                    phase: "gather_missing_inputs",
                    ready_to_reply: false,
                    reply_readiness: {
                      has_conclusion: true,
                      has_proof: false,
                      has_solution_or_next_step: true,
                      conclusion_summary: "The audience may not be able to decode the current video stream.",
                      proof_summary: "",
                      proof_anchors: [],
                      solution_or_next_step: "Ask the engineer to provide the log evidence or reproduction result before replying.",
                      blockers: ["Explicit proof is still missing."],
                      critique: "The current conclusion is plausible but not yet backed by logs, reproduction evidence, or a cited doc path.",
                      ready_for_customer_reply: false,
                    },
                  },
                };
                selectedTicketSummary = "The current draft still needs proof.";
                selectedTicketNextAction = "Collect explicit proof before replying.";

                const html = renderTicketDetailView();
                const engineerIndex = html.indexOf("Engineer Ticket Thread");
                const customerIndex = html.indexOf("Customer Timeline");
                if (engineerIndex === -1 || customerIndex === -1 || engineerIndex > customerIndex) {{
                  throw new Error("Engineer thread should still render before the customer timeline.");
                }}
                const engineerThreadSection = html.slice(engineerIndex, customerIndex);
                const customerTimelineSection = html.slice(customerIndex);

                if (engineerThreadSection.includes('class="detail-readiness-summary"')) {{
                  throw new Error("Engineer thread header should stop rendering the compact readiness summary.");
                }}
                if (html.includes("State:")) {{
                  throw new Error("Engineer detail should stop rendering the thread state line.");
                }}
                if (customerTimelineSection.includes('class="detail-readiness-summary"')) {{
                  throw new Error("Customer timeline sidebar should not render the compact readiness summary.");
                }}
                if (!customerTimelineSection.includes("Internal Review")) {{
                  throw new Error("Engineer detail should render the Internal Review section below the customer timeline.");
                }}
                if (!customerTimelineSection.includes("Readiness Review")) {{
                  throw new Error("Internal Review should render the Readiness Review title.");
                }}
                if (!customerTimelineSection.includes("Needs Follow-up")) {{
                  throw new Error("Internal Review should surface the readiness status pill when reply is not ready.");
                }}
                if (customerTimelineSection.includes("Current Blockers")) {{
                  throw new Error("Internal Review should no longer render blockers in the detail sidebar.");
                }}
                if (customerTimelineSection.includes("Critique")) {{
                  throw new Error("Internal Review should no longer render critique in the detail sidebar.");
                }}
                const readySegments = (customerTimelineSection.match(/detail-readiness-check is-passed/g) || []).length;
                const missingSegments = (customerTimelineSection.match(/detail-readiness-check is-missing/g) || []).length;
                if (readySegments !== 2) {{
                  throw new Error("Internal Review should render two passing checks when conclusion and next step are present.");
                }}
                if (missingSegments !== 1) {{
                  throw new Error("Internal Review should render one missing check when proof is absent.");
                }}
                if ((customerTimelineSection.match(/detail-readiness-check-dot/g) || []).length !== 3) {{
                  throw new Error("Each readiness check should render a status dot.");
                }}
                if (!customerTimelineSection.includes("The audience may not be able to decode the current video stream.")) {{
                  throw new Error("Internal Review should render the conclusion summary.");
                }}
                if (!customerTimelineSection.includes("Proof still missing.")) {{
                  throw new Error("Internal Review should render proof fallback copy when proof summary is missing.");
                }}
                if (!customerTimelineSection.includes("Next step")) {{
                  throw new Error("Internal Review should rename the final readiness label to Next step.");
                }}
                if (customerTimelineSection.includes("Solution / Next Step")) {{
                  throw new Error("Internal Review should stop rendering the old Solution / Next Step label.");
                }}
                if (!customerTimelineSection.includes("Ask the engineer to provide the log evidence or reproduction result before replying.")) {{
                  throw new Error("Internal Review should render the next-step summary.");
                }}
                if (customerTimelineSection.includes("Proof Anchors")) {{
                  throw new Error("Internal Review should not surface proof anchors in the restored sidebar layout.");
                }}
              """
            )
        )

    def test_engineer_detail_internal_review_renders_missing_state_with_fallback_copy(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                selectedTicketId = "TK-DETAIL-READINESS-FALLBACKS";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-READINESS-FALLBACKS",
                  subject: "Unable to summarize engineer response",
                  requester: "user-11",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-READINESS-FALLBACKS",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [],
                  },
                  engineer_agent_state: {
                    phase: "gather_missing_inputs",
                    ready_to_reply: false,
                    reply_readiness: {
                      has_conclusion: false,
                      has_proof: false,
                      has_solution_or_next_step: false,
                      conclusion_summary: "",
                      proof_summary: "",
                      proof_anchors: ["logs://alpha"],
                      solution_or_next_step: "",
                      blockers: ["Conclusion, proof, and next step are all still missing."],
                      critique: "The engineer reply did not provide enough detail.",
                      ready_for_customer_reply: false,
                    },
                  },
                };

                const html = renderTicketDetailView();
                const engineerIndex = html.indexOf("Engineer Ticket Thread");
                const customerIndex = html.indexOf("Customer Timeline");
                const engineerThreadSection = html.slice(engineerIndex, customerIndex);
                const customerTimelineSection = html.slice(customerIndex);

                if (engineerThreadSection.includes('class="detail-readiness-summary"')) {{
                  throw new Error("Engineer thread header should stop rendering readiness summary cards.");
                }}
                if (!customerTimelineSection.includes("Internal Review")) {{
                  throw new Error("Internal Review should still render when all readiness values are missing.");
                }}
                if ((customerTimelineSection.match(/detail-readiness-check is-missing/g) || []).length !== 3) {{
                  throw new Error("All readiness checks should render as missing when every readiness boolean is false.");
                }}
                if (!customerTimelineSection.includes("Conclusion not extracted yet.")) {{
                  throw new Error("Internal Review should render the conclusion fallback copy.");
                }}
                if (!customerTimelineSection.includes("Proof still missing.")) {{
                  throw new Error("Internal Review should render the proof fallback copy.");
                }}
                if (!customerTimelineSection.includes("No actionable next step captured yet.")) {{
                  throw new Error("Internal Review should render the next-step fallback copy.");
                }}
                if (customerTimelineSection.includes("Current Blockers")) {{
                  throw new Error("Internal Review should not render blockers in the trimmed sidebar layout.");
                }}
                if (customerTimelineSection.includes("Critique")) {{
                  throw new Error("Internal Review should not render critique in the trimmed sidebar layout.");
                }}
                if (!customerTimelineSection.includes("Next step")) {{
                  throw new Error("Internal Review should still label the final field as Next step.");
                }}
                if (customerTimelineSection.includes("Solution / Next Step")) {{
                  throw new Error("Internal Review should not render the old Solution / Next Step label.");
                }}
                if (customerTimelineSection.includes("logs://alpha")) {{
                  throw new Error("Internal Review should not surface proof anchors in the restored sidebar layout.");
                }}
              """
            )
        )

    def test_engineer_detail_internal_review_defaults_to_missing_when_readiness_is_absent(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                selectedTicketId = "TK-DETAIL-READINESS-ABSENT";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-READINESS-ABSENT",
                  subject: "Readiness state not evaluated yet",
                  requester: "user-12",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-READINESS-ABSENT",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [],
                  },
                  engineer_agent_state: {
                    phase: "gather_missing_inputs",
                    ready_to_reply: false,
                  },
                };

                const html = renderTicketDetailView();
                const engineerIndex = html.indexOf("Engineer Ticket Thread");
                const customerIndex = html.indexOf("Customer Timeline");
                const engineerThreadSection = html.slice(engineerIndex, customerIndex);
                const customerTimelineSection = html.slice(customerIndex);

                if (engineerThreadSection.includes('class="detail-readiness-summary"')) {{
                  throw new Error("Engineer thread header should not render readiness cards when the review lives in the sidebar.");
                }}
                if (!customerTimelineSection.includes("Internal Review")) {{
                  throw new Error("Internal Review should still render when readiness data is absent.");
                }}
                if ((customerTimelineSection.match(/detail-readiness-check is-missing/g) || []).length !== 3) {{
                  throw new Error("Absent readiness data should default all three readiness checks to missing.");
                }}
                if (!customerTimelineSection.includes("Conclusion not extracted yet.")) {{
                  throw new Error("Absent readiness data should default the conclusion field copy.");
                }}
                if (!customerTimelineSection.includes("Proof still missing.")) {{
                  throw new Error("Absent readiness data should default the proof field copy.");
                }}
                if (!customerTimelineSection.includes("No actionable next step captured yet.")) {{
                  throw new Error("Absent readiness data should default the next-step field copy.");
                }}
                if (!customerTimelineSection.includes("Next step")) {{
                  throw new Error("Absent readiness data should still use the Next step label.");
                }}
                if (customerTimelineSection.includes("Solution / Next Step")) {{
                  throw new Error("Absent readiness data should not render the old Solution / Next Step label.");
                }}
              """
            )
        )

    def test_engineer_detail_shows_read_only_auto_hitl_review(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-HITL-UI-1";
                selectedTicket = {
                  ticket_id: "TK-HITL-UI-1",
                  title: "Android 14 token renew regression",
                  subject: "Parent client ticket subject should stay secondary",
                  client_ticket_ref: {
                    ticket_id: "TK-HITL-CLIENT-1",
                    subject: "Token renewal callback failure",
                  },
                  requester: "user-7",
                  status: "investigating",
                  created_at: "2026-06-10T08:00:00+00:00",
                  updated_at: "2026-06-10T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-HITL-UI-1",
                    state: "awaiting_confirmation",
                    draft_customer_reply: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                    opened_at: "2026-06-10T08:01:00+00:00",
                    updated_at: "2026-06-10T09:05:00+00:00",
                    messages: [
                      {
                        id: "msg-hitl-ui-1",
                        role: "engineer_ai",
                        content: "I have enough evidence. Please confirm the customer reply.",
                        created_at: "2026-06-10T09:05:00+00:00",
                      },
                    ],
                  },
                  engineer_agent_state: {
                    run_id: "run-hitl-ui-1",
                    evidence_packet_id: "packet-hitl-ui-1",
                    prompt_version: "engineer-investigation-reply-v8",
                    workflow_version: "engineer-hitl-feedback-v1",
                    tool_policy_version: "engineer-evidence-tools-v1",
                    rag_access_policy_version: "rag-access-routing-v1",
                    evidence_packet_version: "engineer-evidence-packet-v1",
                    ready_to_reply: true,
                    reply_readiness: {
                      has_conclusion: true,
                      has_proof: true,
                      has_solution_or_next_step: true,
                      ready_for_customer_reply: true,
                      conclusion_summary: "Android 14 with SDK 4.2.1 reproduces the token renew failure.",
                      proof_summary: "The engineer reproduced the issue on Android 14 with SDK 4.2.1 only.",
                      solution_or_next_step: "Upgrade to SDK 4.2.2.",
                    },
                  },
                  engineer_hitl_feedback: [
                    {
                      feedback_id: "hitl_existing",
                      feedback_type: "resolve",
                      diagnosis_correctness: "correct",
                      root_cause_correctness: "confirmed",
                      evidence_quality: "sufficient",
                      citation_quality: "correct",
                      customer_reply_quality: "sendable",
                      corrected_root_cause: "SDK 4.2.1 token renewal callback fails only on Android 14.",
                      corrected_solution: "Upgrade to SDK 4.2.2 and clear the cached token.",
                      corrected_customer_reply: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                      evidence_refs: [{ source_id: "logs://token-renew-android14" }],
                      memory_candidate: "needs_review",
                      memory_safety: "internal_only",
                      memory_notes: "Auto-reviewed after engineer approval; keep internal logs out of customer-safe memory.",
                      created_by: "engineer_ai_auto_review",
                      created_at: "2026-06-10T09:06:00+00:00",
                    },
                  ],
                };

                let html = renderTicketDetailView();
                // The HITL feedback section is gated behind the multi-agent workspace
                // toggle; the default guardrail-only view must not surface it.
                if (html.includes("Feedback for AI Learning")) {
                  throw new Error("Default detail view should hide the HITL feedback section until the multi-agent workspace is toggled on.");
                }

                // Toggling the multi-agent workspace for this investigating ticket
                // surfaces the read-only HITL feedback section alongside the
                // Plan/Execute/Review agent outputs.
                toggleMultiAgentWorkspaceForTicket(selectedTicketId);
                html = renderTicketDetailView();
                if (!html.includes("Feedback for AI Learning")) {
                  throw new Error("Engineer detail should render the AI learning review panel inside the multi-agent workspace.");
                }
                if (!html.includes("hitl_existing")) {
                  throw new Error("Auto review panel should render the latest stored feedback id for audit traceability.");
                }
                if (!html.includes("Auto-reviewed after closure")) {
                  throw new Error("Auto review panel should make the closed-case review source visible.");
                }
                if (!html.includes("needs_review") || !html.includes("internal_only")) {
                  throw new Error("Auto review panel should show memory candidate and safety labels.");
                }
                if (!html.includes("SDK 4.2.1 token renewal callback fails only on Android 14.")) {
                  throw new Error("Auto review panel should show the reviewed root cause.");
                }
                if (html.includes('data-hitl-feedback-field=')) {
                  throw new Error("Auto review panel should not expose editable feedback fields.");
                }
                if (html.includes('data-detail-action="submit-hitl-feedback"')) {
                  throw new Error("Auto review panel should not expose a manual feedback submit action.");
                }

                selectedTicket = {
                  ...selectedTicket,
                  status: "investigating",
                  engineer_hitl_feedback: [],
                };
                // The multi-agent workspace toggle is still on for this ticket id,
                // so the pending-after-close message should render inside the gated view.
                html = renderTicketDetailView();
                if (!html.includes("Learning review will run after this engineer case closes.")) {
                  throw new Error("Active engineer cases should show a pending-after-close learning review message inside the multi-agent workspace.");
                }
                if (html.includes('data-hitl-feedback-field=') || html.includes('data-detail-action="submit-hitl-feedback"')) {
                  throw new Error("Active engineer cases should not expose manual HITL feedback controls.");
                }

                // Turning the multi-agent workspace back off must hide the HITL section again.
                toggleMultiAgentWorkspaceForTicket(selectedTicketId);
                html = renderTicketDetailView();
                if (html.includes("Feedback for AI Learning")) {
                  throw new Error("Turning the multi-agent workspace off should hide the HITL feedback section.");
                }
              """
            )
        )

    def test_case_buddy_opening_request_sections_prefer_agent_state_when_available(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                const sections = buildCaseBuddyOpeningRequestSections({
                  status: "investigating",
                  messages: [
                    {
                      role: "customer",
                      content: "Android 14 token renewal still fails after the upgrade.",
                      created_at: "2026-03-24T08:00:00+00:00",
                    }
                  ],
                  engineer_agent_state: {
                    phase: "gather_missing_inputs",
                    issue_understanding: "Android 14 token renewal still fails after the customer upgraded the SDK.",
                    knowledge_summary: "Client AI found generic token-authentication guidance but no Android 14-specific callback evidence.",
                    why_not_solved: "The current evidence does not prove the exact SDK regression boundary.",
                    goal: "Confirm the exact SDK version and whether Android 14 is the only affected platform.",
                    known_facts: ["Customer already upgraded the SDK."],
                    missing_information: ["Exact SDK version", "Cross-platform reproduction scope"],
                    next_request_for_engineer: "Please confirm the exact SDK version and whether Android 14 is the only affected platform.",
                    resolution_hypothesis: "The issue may be limited to SDK 4.2.1 on Android 14.",
                    ready_to_reply: false,
                    last_refreshed_at: "2026-03-24T09:10:00+00:00",
                  },
                });

                if (!Array.isArray(sections) || sections.length !== 2) {
                  throw new Error("Structured Sid requests should return the two merged sections.");
                }
                if (sections[0].title !== "Current issue") {
                  throw new Error("The first merged section should be Current issue.");
                }
                if (sections[0].summary !== "Android 14 token renewal still fails after the customer upgraded the SDK.") {
                  throw new Error("Current issue should expose the agent issue understanding as the summary paragraph.");
                }
                if (!sections[0].items.includes("Customer already upgraded the SDK.")) {
                  throw new Error("Current issue should include known facts.");
                }
                if (sections[0].items.includes("Android 14 token renewal still fails after the customer upgraded the SDK.")) {
                  throw new Error("Current issue summary should not be duplicated inside the facts bullets.");
                }
                if (sections[1].title !== "Action needed") {
                  throw new Error("The second merged section should be Action needed.");
                }
                if (!sections[1].items.includes("Please confirm the exact SDK version and whether Android 14 is the only affected platform.")) {
                  throw new Error("Action needed should include the agent next request.");
                }
                if (!sections[1].items.includes("Exact SDK version") || !sections[1].items.includes("Cross-platform reproduction scope")) {
                  throw new Error("Action needed should include the remaining missing information bullets.");
                }
              """
            )
        )

    def test_case_buddy_current_issue_filters_redundant_structured_facts(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                const sections = buildCaseBuddyOpeningRequestSections({
                  status: "investigating",
                  engineer_agent_state: {
                    phase: "awaiting_confirmation",
                    issue_understanding: "Camera/video capture failure reported for channel zilingtest, uid 2, around 2026-04-04 12:00 UTC+8.",
                    knowledge_summary: "Web SDK logs point to local capture failure.",
                    why_not_solved: "The exact root cause category is still unconfirmed.",
                    goal: "Confirm the symptom-level draft is safe to send.",
                    known_facts: [
                      "Customer reported channel zilingtest",
                      "Problematic uid is 2",
                      "Issue time is around 2026-04-04 12:00 UTC+8",
                      'Web SDK log for uid 2 says "[websdk] no capture video frame"',
                    ],
                    missing_information: [],
                    next_request_for_engineer: "Approve the prepared symptom-level customer reply if it is safe to send.",
                    resolution_hypothesis: "The local capture path failed.",
                    ready_to_reply: true,
                    last_refreshed_at: "2026-04-14T09:10:00+00:00",
                  },
                });

                if (!Array.isArray(sections) || sections.length !== 2) {
                  throw new Error("Structured Sid requests should still return two sections after dedupe.");
                }
                if (sections[0].summary !== "Camera/video capture failure reported for channel zilingtest, uid 2, around 2026-04-04 12:00 UTC+8.") {
                  throw new Error("Current issue should keep the issue_understanding summary as the summary paragraph.");
                }
                if (sections[0].items.includes("Customer reported channel zilingtest")) {
                  throw new Error("Current issue should hide a redundant channel fact already covered by issue_understanding.");
                }
                if (sections[0].items.includes("Problematic uid is 2")) {
                  throw new Error("Current issue should hide a redundant uid fact already covered by issue_understanding.");
                }
                if (sections[0].items.includes("Issue time is around 2026-04-04 12:00 UTC+8")) {
                  throw new Error("Current issue should hide a redundant timestamp fact already covered by issue_understanding.");
                }
                if (!sections[0].items.includes('Web SDK log for uid 2 says "[websdk] no capture video frame"')) {
                  throw new Error("Current issue should keep non-redundant technical evidence.");
                }
              """
            )
        )

    def test_case_buddy_current_issue_promotes_first_non_candidate_fact_when_issue_summary_missing(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                const sections = buildCaseBuddyOpeningRequestSections({
                  status: "investigating",
                  engineer_agent_state: {
                    phase: "gather_missing_inputs",
                    issue_understanding: "",
                    knowledge_summary: "The handoff still needs the confirmed session details.",
                    why_not_solved: "The current evidence is incomplete.",
                    goal: "Collect the next missing technical detail.",
                    known_facts: [
                      "Sid candidate answer: Please ask the engineer to confirm the workaround.",
                      "Customer reported channel zilingtest",
                      "Problematic uid is 2",
                      "Issue time is around 2026-04-04 12:00 UTC+8",
                    ],
                    missing_information: ["Exact SDK version"],
                    next_request_for_engineer: "Confirm the exact SDK version.",
                    resolution_hypothesis: "",
                    ready_to_reply: false,
                    last_refreshed_at: "2026-04-14T09:12:00+00:00",
                  },
                });

                if (sections[0].summary !== "Customer reported channel zilingtest") {
                  throw new Error("Current issue should promote the first non-candidate known fact into the summary when issue_understanding is missing.");
                }
                if (sections[0].items.includes("Customer reported channel zilingtest")) {
                  throw new Error("The promoted current-issue summary should not remain duplicated inside the fact bullets.");
                }
                if (!sections[0].items.includes("Problematic uid is 2")) {
                  throw new Error("Current issue should keep remaining uid facts when there is no issue_understanding summary.");
                }
                if (!sections[0].items.includes("Issue time is around 2026-04-04 12:00 UTC+8")) {
                  throw new Error("Current issue should keep remaining timestamp facts when there is no issue_understanding summary.");
                }
                if (sections[0].summary.includes("candidate answer") || sections[0].items.some((item) => item.includes("candidate answer"))) {
                  throw new Error("Current issue should filter candidate-answer-like facts before choosing the summary and facts bullets.");
                }
              """
            )
        )

    def test_case_buddy_current_issue_hides_structured_intake_facts_covered_by_symptom_summary(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                const sections = buildCaseBuddyOpeningRequestSections({
                  status: "investigating",
                  engineer_agent_state: {
                    phase: "gather_missing_inputs",
                    issue_understanding: "Black screen issue reported for channel zilingtest, uid 2.",
                    knowledge_summary: "The handoff still needs the reported issue timestamp.",
                    why_not_solved: "The current evidence is incomplete.",
                    goal: "Collect the next missing technical detail.",
                    known_facts: [
                      "Issue symptom is black screen issue.",
                      "Channel name is zilingtest.",
                      "Problematic uid is 2.",
                      'Web SDK log for uid 2 says "[websdk] no capture video frame"',
                    ],
                    missing_information: ["Issue timestamp"],
                    next_request_for_engineer: "Confirm the reported issue timestamp.",
                    resolution_hypothesis: "",
                    ready_to_reply: false,
                    last_refreshed_at: "2026-04-16T03:51:52.897458+00:00",
                  },
                });

                if (sections[0].summary !== "Black screen issue reported for channel zilingtest, uid 2.") {
                  throw new Error("Current issue should surface the symptom-first intake summary.");
                }
                if (sections[0].items.includes("Issue symptom is black screen issue.")) {
                  throw new Error("Current issue should hide a redundant symptom fact already covered by issue_understanding.");
                }
                if (sections[0].items.includes("Channel name is zilingtest.")) {
                  throw new Error("Current issue should hide a redundant channel fact already covered by issue_understanding.");
                }
                if (sections[0].items.includes("Problematic uid is 2.")) {
                  throw new Error("Current issue should hide a redundant uid fact already covered by issue_understanding.");
                }
                if (!sections[0].items.includes('Web SDK log for uid 2 says "[websdk] no capture video frame"')) {
                  throw new Error("Current issue should keep non-redundant technical evidence after hiding intake duplicates.");
                }
              """
            )
        )

    def test_engineer_detail_current_issue_renders_symptom_first_summary_from_payload(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                const sections = buildCaseBuddyOpeningRequestSections({
                  status: "investigating",
                  engineer_agent_state: {
                    phase: "gather_missing_inputs",
                    issue_understanding: "Black screen issue reported for channel zilingtest, uid 2.",
                    knowledge_summary: "The handoff still needs the reported issue timestamp.",
                    why_not_solved: "The current evidence is incomplete.",
                    goal: "Collect the next missing technical detail.",
                    known_facts: [
                      "Issue symptom is black screen issue.",
                      "Channel name is zilingtest.",
                      "Problematic uid is 2.",
                      'Web SDK log for uid 2 says "[websdk] no capture video frame"',
                    ],
                    missing_information: ["Issue timestamp"],
                    next_request_for_engineer: "Confirm the reported issue timestamp.",
                    resolution_hypothesis: "",
                    ready_to_reply: false,
                    last_refreshed_at: "2026-04-16T08:10:00+00:00",
                  },
                });

                const html = renderCaseBuddyRequestSectionsHtml(sections);
                if (!html.includes("Black screen issue reported for channel zilingtest, uid 2.")) {
                  throw new Error("Engineer detail should render the upgraded symptom-first current-issue summary inside the Case Buddy block.");
                }
                if (html.includes("Issue symptom is black screen issue.")) {
                  throw new Error("Engineer detail should not repeat a symptom fact already covered by the current-issue summary.");
                }
                if (html.includes("Channel name is zilingtest.")) {
                  throw new Error("Engineer detail should not repeat a channel fact already covered by the current-issue summary.");
                }
                if (html.includes("Problematic uid is 2.")) {
                  throw new Error("Engineer detail should not repeat a uid fact already covered by the current-issue summary.");
                }
                if (!html.includes('Web SDK log for uid 2 says &quot;[websdk] no capture video frame&quot;')) {
                  throw new Error("Engineer detail should keep non-redundant technical evidence in the current-issue section.");
                }
              """
            )
        )

    def test_engineer_detail_renders_shared_rich_composer_in_active_and_approval_states(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                selectedTicketId = "TK-DETAIL-RICH";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-RICH",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-RICH",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-RICH-m1",
                        role: "engineer_ai",
                        content: "Please share the Android version and latest logcat excerpt.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                  engineer_agent_state: {
                    reply_readiness: {
                      ready_for_customer_reply: false,
                    },
                  },
                };
                selectedTicketSummary = "Sid needs one more technical detail.";
                selectedTicketNextAction = "Share the latest Android logcat excerpt.";

                const activeHtml = renderTicketDetailView();
                if (!activeHtml.includes('id="detail-investigation-input"')) {
                  throw new Error("Active engineer detail should keep the investigation composer visible.");
                }
                if (!activeHtml.includes('data-chat-composer-rich="true"')) {
                  throw new Error("Engineer detail should render the shared rich contenteditable composer.");
                }
                if (!activeHtml.includes('contenteditable="true"')) {
                  throw new Error("Engineer detail rich composer should stay editable while controls are enabled.");
                }
                if ((activeHtml.match(/data-composer-markdown-action=/g) || []).length !== 5) {
                  throw new Error("Engineer detail composer should expose bold, italic, list, code-block, and attach actions.");
                }
                if (activeHtml.includes("new-ticket-summary-toolbar-btn")) {
                  throw new Error("Engineer detail composer should not render the client-only AI Summary control.");
                }
                if (activeHtml.includes("<textarea")) {
                  throw new Error("Engineer detail should no longer render the legacy textarea composer.");
                }

                selectedTicket.active_investigation.draft_customer_reply = "Please retry token renewal after clearing the cached token.";
                selectedTicket.engineer_agent_state.reply_readiness.ready_for_customer_reply = true;
                const approvalHtml = renderTicketDetailView();
                if (!approvalHtml.includes('data-chat-composer-rich="true"')) {
                  throw new Error("Approval state should keep the shared rich composer visible for revision notes.");
                }
                if ((approvalHtml.match(/data-composer-markdown-action=/g) || []).length !== 5) {
                  throw new Error("Approval state should preserve the shared toolbar actions.");
                }
                if (!approvalHtml.includes('aria-label="Send Revision Note"')) {
                  throw new Error("Approval state should keep the revision-note send label.");
                }
              """
            )
        )

    def test_engineer_detail_shows_closed_investigation_thread_without_composer(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                selectedTicketId = "TK-DETAIL-CLOSED";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-CLOSED",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "communicating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [
                    {
                      role: "customer",
                      content: "Token renew callback does not fire on Android 14.",
                      created_at: "2026-03-24T08:00:00+00:00",
                    },
                    {
                      role: "assistant",
                      content: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                      created_at: "2026-03-24T09:06:00+00:00",
                    },
                  ],
                  active_investigation: null,
                  investigation_history: [
                    {
                      id: "INV-DETAIL-1",
                      state: "closed",
                      trigger_reason: "rag_insufficient_evidence",
                      trigger_source: "support_query",
                      draft_customer_reply: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                      final_confirmation_requested_at: null,
                      opened_at: "2026-03-24T08:01:00+00:00",
                      updated_at: "2026-03-24T09:06:00+00:00",
                      closed_at: "2026-03-24T09:06:00+00:00",
                      messages: [
                        {
                          id: "INV-DETAIL-1-m1",
                          role: "engineer_ai",
                          content: "Please confirm whether the issue only reproduces on Android 14.",
                          created_at: "2026-03-24T08:02:00+00:00",
                        },
                        {
                          id: "INV-DETAIL-1-m2",
                          role: "engineer",
                          content: "Confirmed. Reproduces on Android 14 with SDK 4.2.1 only.",
                          created_at: "2026-03-24T08:20:00+00:00",
                        },
                        {
                          id: "INV-DETAIL-1-m3",
                          role: "engineer_ai",
                          content: "I have enough information now. Please confirm this draft before I reply to the customer.",
                          created_at: "2026-03-24T09:05:00+00:00",
                        },
                        {
                          id: "INV-DETAIL-1-m4",
                          role: "engineer",
                          content: "Approved final reply.",
                          created_at: "2026-03-24T09:06:00+00:00",
                        },
                      ],
                    },
                  ],
                  engineer_request_records: [],
                };
                selectedTicketSummary = "Customer-facing answer was approved and sent.";
                selectedTicketNextAction = "Monitor for customer follow-up if they reply again.";

                const html = renderTicketDetailView();
                if (!html.includes("Approved final reply.")) {{
                  throw new Error("Closed investigations should keep rendering the latest internal transcript.");
                }}
                if (html.includes("State:")) {{
                  throw new Error("Closed investigations should no longer render the thread state line.");
                }}
                if (html.includes('id="detail-investigation-input"')) {{
                  throw new Error("Closed investigations should not render an active composer.");
                }}
                if (html.includes("Approve for Guardrail") || html.includes("Ask AI to Revise")) {{
                  throw new Error("Closed investigations should not keep rendering confirmation actions.");
                }}
              """
            )
        )

    def test_engineer_detail_renders_safe_markdown_for_internal_thread_messages(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                selectedTicketId = "TK-DETAIL-MARKDOWN";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-MARKDOWN",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-MARKDOWN",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-MARKDOWN-m1",
                        role: "engineer",
                        content: "Use **token renew** on *Android 14*\\n- clear cache\\n- retry token request\\n```js\\nconst renew = true;\\n```",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };
                selectedTicketSummary = "Render markdown in the engineer thread.";
                selectedTicketNextAction = "Verify internal formatting keeps the safe markdown subset.";

                const html = renderTicketDetailView();
                if (!html.includes("<strong>token renew</strong>")) {
                  throw new Error("Engineer internal thread should render strong markdown.");
                }
                if (!html.includes("<em>Android 14</em>")) {
                  throw new Error("Engineer internal thread should render emphasis markdown.");
                }
                if (!html.includes("<ul><li>clear cache</li><li>retry token request</li></ul>")) {
                  throw new Error("Engineer internal thread should render unordered lists from markdown.");
                }
                if (!html.includes("<pre><code")) {
                  throw new Error("Engineer internal thread should render fenced code blocks.");
                }
                if (html.includes("**token renew**") || html.includes("*Android 14*")) {
                  throw new Error("Engineer internal thread should not leak raw markdown markers after rendering.");
                }
              """
            )
        )

    def test_engineer_detail_renders_references_for_engineer_ai_handoff_messages(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                selectedTicketId = "TK-DETAIL-RAG-HANDOFF";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-RAG-HANDOFF",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [
                    {
                      role: "customer",
                      content: "Token renew callback does not fire on Android 14.",
                      created_at: "2026-03-24T08:00:00+00:00",
                    },
                  ],
                  active_investigation: {
                    id: "INV-DETAIL-RAG-HANDOFF",
                    state: "active",
                    trigger_reason: "rag_post_check_insufficient",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T08:02:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-RAG-HANDOFF-m1",
                        role: "engineer_ai",
                        content: "Engineer Request:\\nIssue: Customer reports token renew callback failing on Android 14. AI attempted this docs-backed guidance: Please upgrade to SDK 4.2.2 and retry token renewal.\\nAction Needed: Review the tentative docs-backed guidance and provide a customer-safe fix.",
                        created_at: "2026-03-24T08:02:00+00:00",
                        citations: [
                          {
                            chunk_id: "chunk-1",
                            source_path: "official/token-authentication.md",
                            heading: "Token authentication",
                            source_url: "https://docs.agora.io/en/video-calling/token-authentication",
                          },
                        ],
                        sources: ["https://docs.agora.io/en/video-calling/token-authentication"],
                      },
                    ],
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };
                selectedTicketSummary = "Engineer should review the tentative docs-backed guidance.";
                selectedTicketNextAction = "Validate the SDK/platform constraints and confirm the customer-safe reply.";

                const html = renderTicketDetailView();
                if (!html.includes("References")) {{
                  throw new Error("Engineer handoff messages should render the references section.");
                }}
                if (!html.includes("Token authentication")) {{
                  throw new Error("Engineer handoff messages should render citation headings.");
                }}
                if (!html.includes('href="https://docs.agora.io/en/video-calling/token-authentication"')) {{
                  throw new Error("Engineer handoff messages should render clickable citation links.");
                }}
              """
            )
        )

    def test_engineer_detail_renders_customer_message_sentiment_pill_only_for_customer_messages(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                selectedTicketId = "TK-DETAIL-SENTIMENT";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-SENTIMENT",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "communicating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [
                    {
                      role: "customer",
                      content: "my service is down, it is so frustrated!",
                      sentiment_label: "bad",
                      created_at: "2026-03-24T08:00:00+00:00",
                    },
                    {
                      role: "assistant",
                      content: "Got it, let me check this for you.",
                      created_at: "2026-03-24T08:00:05+00:00",
                    },
                  ],
                  active_investigation: null,
                  investigation_history: [],
                  engineer_request_records: [],
                };
                selectedTicketSummary = "Check the latest customer report.";
                selectedTicketNextAction = "Inspect the customer timeline.";

                const html = renderTicketDetailView();
                if (!html.includes("message-sentiment-pill")) {
                  throw new Error("Customer timeline should render the inline message sentiment pill.");
                }
                if (!html.includes(">bad<")) {
                  throw new Error("Customer message sentiment pill should keep the lowercase sentiment token.");
                }
                const customerHeaderIndex = html.indexOf("Customer");
                const pillIndex = html.indexOf("message-sentiment-pill");
                const timeIndex = html.indexOf('class="message-time"', pillIndex);
                if (customerHeaderIndex === -1 || pillIndex === -1 || timeIndex === -1) {
                  throw new Error("Expected customer role, sentiment pill, and timestamp in the customer timeline.");
                }
                if (!(customerHeaderIndex < pillIndex && pillIndex < timeIndex)) {
                  throw new Error("Sentiment pill should render after the Customer label and before the timestamp.");
                }
                const assistantHeaderIndex = html.indexOf(">AI<");
                const assistantSection = assistantHeaderIndex === -1 ? "" : html.slice(assistantHeaderIndex, assistantHeaderIndex + 220);
                if (assistantSection.includes("message-sentiment-pill")) {
                  throw new Error("Assistant messages should not render a customer sentiment pill.");
                }
              """
            )
        )

    def test_engineer_detail_approval_state_keeps_composer_and_submit_targets_confirmation_endpoint(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-REV";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-REV",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-REV",
                    state: "awaiting_confirmation",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                    final_confirmation_requested_at: "2026-03-24T09:05:00+00:00",
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-REV-m1",
                        role: "engineer_ai",
                        content: "I have enough information now. Please confirm this draft before I reply to the customer.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  engineer_agent_state: {
                    phase: "awaiting_confirmation",
                    ready_to_reply: true,
                    reply_readiness: {
                      has_conclusion: true,
                      has_proof: true,
                      has_solution_or_next_step: true,
                      conclusion_summary: "Android 14 with SDK 4.2.1 reproduces the token renew failure.",
                      proof_summary: "The engineer reproduced the issue on Android 14 with SDK 4.2.1 only.",
                      proof_anchors: ["Android 14", "SDK 4.2.1"],
                      solution_or_next_step: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                      blockers: [],
                      critique: "The current evidence supports the customer-safe SDK upgrade guidance.",
                      ready_for_customer_reply: true,
                    },
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };
                selectedTicketSummary = "Customer-facing answer is drafted and waiting for engineer confirmation.";
                selectedTicketNextAction = "Approve the prepared reply or ask the AI to revise it.";

                const approvalHtml = renderTicketDetailView();
                if (!approvalHtml.includes('id="detail-investigation-input"')) {{
                  throw new Error("Approval state should keep the main investigation composer visible.");
                }}
                if (!approvalHtml.includes("If the draft needs changes, tell Sid what to revise before replying to the customer")) {{
                  throw new Error("Approval state should explain that the composer sends revision notes directly.");
                }}
                if (!approvalHtml.includes('aria-label="Send Revision Note"')) {{
                  throw new Error("Approval state should preserve the revision-note submit label for the icon button.");
                }}
                if (approvalHtml.includes("Ask AI to Revise")) {{
                  throw new Error("Approval state should no longer render a separate revise button.");
                }}

                let capturedUrl = null;
                let capturedOptions = null;
                fetchJson = async (url, options = undefined) => {
                  capturedUrl = url;
                  capturedOptions = options;
                  return {
                    ticket_id: "TK-DETAIL-REV",
                    status: "investigating",
                    active_investigation: selectedTicket.active_investigation,
                    updated_at: "2026-03-24T09:11:00+00:00",
                  };
                };
                loadTickets = async () => {};
                refreshSelectedTicket = async () => {};

                tellAiDraft = "Add a cache-clear step before asking the customer to retry.";
                const sendButton = {
                  dataset: { detailAction: "send-tell-ai" },
                  disabled: false,
                };
                const sendTarget = {
                  closest(selector) {
                    if (selector === "button[data-detail-action]") {
                      return sendButton;
                    }
                    return null;
                  },
                };

                handleDetailClick({ target: sendTarget });
                if (capturedUrl !== "/api/engineer/tickets/TK-DETAIL-REV/investigation/confirmation") {{
                  throw new Error("Approval-state composer should call the investigation confirmation endpoint.");
                }}
                const parsedBody = JSON.parse(capturedOptions.body);
                if (parsedBody.decision !== "revise") {{
                  throw new Error("Approval-state composer should post decision=revise.");
                }}
                if (parsedBody.note !== "Add a cache-clear step before asking the customer to retry.") {{
                  throw new Error("Approval-state composer should send the engineer revision note.");
                }}
              """
            )
        )

    def test_engineer_detail_send_shows_optimistic_engineer_message_and_ai_placeholder(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-OPT";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-OPT",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-OPT",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-OPT-m1",
                        role: "engineer_ai",
                        content: "Please share the Android version and latest logcat excerpt.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };
                selectedTicketSummary = "Sid needs one more technical detail.";
                selectedTicketNextAction = "Share the latest Android logcat excerpt.";

                let resolveFetch = null;
                let capturedUrl = null;
                let capturedOptions = null;
                fetchJson = async (url, options = undefined) => {
                  capturedUrl = url;
                  capturedOptions = options;
                  return await new Promise((resolve) => {
                    resolveFetch = resolve;
                  });
                };
                loadTickets = async () => {};
                refreshSelectedTicket = async () => {};

                tellAiDraft = "Reproduces on Android 14 with SDK 4.2.1. Logcat shows token expired.";
                const sendButton = {
                  dataset: { detailAction: "send-tell-ai" },
                  disabled: false,
                };
                const sendTarget = {
                  closest(selector) {
                    if (selector === "button[data-detail-action]") {
                      return sendButton;
                    }
                    return null;
                  },
                };

                const sendPromise = handleDetailClick({ target: sendTarget });
                const optimisticHtml = workspaceRegionEl.innerHTML;
                if (!optimisticHtml.includes("Reproduces on Android 14 with SDK 4.2.1. Logcat shows token expired.")) {
                  throw new Error("Engineer thread should immediately show the optimistic engineer message.");
                }
                if (!optimisticHtml.includes(">jack<")) {
                  throw new Error("Optimistic engineer messages should render the jack label in the internal thread.");
                }
                if (!optimisticHtml.includes(">Sid<")) {
                  throw new Error("Internal engineer-thread AI messages should render the Sid label.");
                }
                if (!optimisticHtml.includes("message-item-pending-ai")) {
                  throw new Error("Engineer thread should render an Engineer AI placeholder bubble while waiting.");
                }
                if (!optimisticHtml.includes("detail-thinking-dots")) {
                  throw new Error("Engineer AI placeholder bubble should show the loading-dot animation.");
                }
                if (!optimisticHtml.includes("Sid is reviewing your update")) {
                  throw new Error("Engineer AI placeholder bubble should explain that a reply is pending.");
                }
                if (tellAiDraft !== "") {
                  throw new Error("Composer draft should clear immediately after optimistic send.");
                }
                if (capturedUrl !== "/api/engineer/tickets/TK-DETAIL-OPT/investigation/messages") {
                  throw new Error("Optimistic engineer sends should still call the investigation messages endpoint.");
                }
                if (capturedOptions?.timeoutMs !== 100000) {
                  throw new Error("Send Update should use the extended 100s Sid timeout budget.");
                }

                resolveFetch({
                  ticket_id: "TK-DETAIL-OPT",
                  status: "investigating",
                  updated_at: "2026-03-24T09:11:00+00:00",
                  active_investigation: {
                    id: "INV-DETAIL-OPT",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:11:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-OPT-m1",
                        role: "engineer_ai",
                        content: "Please share the Android version and latest logcat excerpt.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                      {
                        id: "INV-DETAIL-OPT-m2",
                        role: "engineer",
                        content: "Reproduces on Android 14 with SDK 4.2.1. Logcat shows token expired.",
                        created_at: "2026-03-24T09:10:30+00:00",
                      },
                      {
                        id: "INV-DETAIL-OPT-m3",
                        role: "engineer_ai",
                        content: "Thanks. Please also confirm whether clearing the cached token changes the behavior.",
                        created_at: "2026-03-24T09:11:00+00:00",
                      },
                    ],
                  },
                });
                await sendPromise;

                const settledHtml = workspaceRegionEl.innerHTML;
                if (settledHtml.includes("message-item-pending-ai")) {
                  throw new Error("Engineer AI placeholder bubble should disappear once the durable reply arrives.");
                }
                if (!settledHtml.includes("Thanks. Please also confirm whether clearing the cached token changes the behavior.")) {
                  throw new Error("Engineer thread should replace the placeholder with the durable Engineer AI reply.");
                }
                if (!settledHtml.includes(">Sid<")) {
                  throw new Error("Durable internal AI replies should keep the Sid label.");
                }
              """
            )
        )

    def test_engineer_detail_unlocks_approve_reply_before_background_refresh_finishes(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-APPROVE-UNLOCK";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-APPROVE-UNLOCK",
                  subject: "Black screen after join",
                  requester: "user-8",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-APPROVE-UNLOCK",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-APPROVE-UNLOCK-m1",
                        role: "engineer_ai",
                        content: "Please share the latest SDK logs and your reproduction result.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };
                selectedTicketSummary = "Sid still needs technical evidence.";
                selectedTicketNextAction = "Share the latest logs.";

                tellAiSubmitting = true;
                applySuccessfulInvestigationSendResponse("TK-DETAIL-APPROVE-UNLOCK", {
                  ticket_id: "TK-DETAIL-APPROVE-UNLOCK",
                  status: "investigating",
                  updated_at: "2026-03-24T09:11:00+00:00",
                  active_investigation: {
                    id: "INV-DETAIL-APPROVE-UNLOCK",
                    state: "awaiting_confirmation",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "Thanks for waiting. We checked the SDK logs and found that the affected camera was not producing any video frames at that time. Please try another capture device and test again.",
                    final_confirmation_requested_at: "2026-03-24T09:11:00+00:00",
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:11:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-APPROVE-UNLOCK-m1",
                        role: "engineer_ai",
                        content: "Please share the latest SDK logs and your reproduction result.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                      {
                        id: "INV-DETAIL-APPROVE-UNLOCK-m2",
                        role: "engineer",
                        content: "The SDK logs show the camera never produced any video frames, so the black screen is expected.",
                        created_at: "2026-03-24T09:10:30+00:00",
                      },
                      {
                        id: "INV-DETAIL-APPROVE-UNLOCK-m3",
                        role: "engineer_ai",
                        content: "We now have a usable conclusion, proof, and customer-safe next step.",
                        created_at: "2026-03-24T09:11:00+00:00",
                      },
                    ],
                  },
                  engineer_agent_state: {
                    phase: "awaiting_confirmation",
                    ready_to_reply: true,
                    reply_readiness: {
                      has_conclusion: true,
                      has_proof: true,
                      has_solution_or_next_step: true,
                      conclusion_summary: "The affected camera was not producing video frames.",
                      proof_summary: "The SDK logs show no captured video frames for the affected user.",
                      proof_anchors: ["camera", "video frames"],
                      solution_or_next_step: "Ask the customer to switch to another capture device and retest.",
                      blockers: [],
                      critique: "The current evidence supports the customer-safe camera troubleshooting reply.",
                      ready_for_customer_reply: true,
                    },
                  },
                });

                const pendingRefreshHtml = workspaceRegionEl.innerHTML;
                if (!pendingRefreshHtml.includes("Approve for Guardrail")) {
                  throw new Error("The approve action should appear as soon as the investigation reply payload arrives.");
                }
                if (!pendingRefreshHtml.includes("Thanks for waiting. We checked the SDK logs")) {
                  throw new Error("The customer draft should render before the background refresh finishes.");
                }
                const approveButtonIndex = pendingRefreshHtml.indexOf('data-detail-action="approve-investigation"');
                if (approveButtonIndex === -1) {
                  throw new Error("The approve button should be present once the reply payload is applied.");
                }
                const approveButtonMarkup = pendingRefreshHtml.slice(approveButtonIndex, approveButtonIndex + 220);
                if (approveButtonMarkup.includes("disabled")) {
                  throw new Error("The approve button should unlock immediately after the reply payload arrives, even while background refresh is still pending.");
                }
                if (tellAiSubmitting) {
                  throw new Error("The immediate UI lock should clear before the background ticket refresh starts.");
                }
              """
            )
        )

    def test_engineer_detail_refresh_preserves_active_investigation_composer(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-FOCUS";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-FOCUS",
                  client_ticket_ref: {
                    ticket_id: "TK-CLIENT-FOCUS",
                    subject: "Client black screen issue",
                  },
                  subject: "Black screen after join",
                  requester: "user-9",
                  status: "investigating",
                  created_at: "2026-04-14T09:00:00+00:00",
                  updated_at: "2026-04-14T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-FOCUS",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-04-14T09:01:00+00:00",
                    updated_at: "2026-04-14T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-FOCUS-m1",
                        role: "engineer_ai",
                        content: "Please share the latest device log.",
                        created_at: "2026-04-14T09:05:00+00:00",
                      },
                    ],
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };
                tellAiDraft = "第二轮日志补充";

                let activeElement = null;
                let composerInput = null;
                let workspaceRoot = null;
                let headerRegion = { innerHTML: "" };
                let staticRegion = { innerHTML: "" };
                let threadBodyRegion = { innerHTML: "" };
                let insightRegion = { innerHTML: "" };
                const createComposer = () => ({
                  value: tellAiDraft,
                  disabled: false,
                  selectionStart: 0,
                  selectionEnd: 0,
                  selectionDirection: "none",
                  scrollTop: 0,
                  focus() {
                    activeElement = this;
                  },
                  blur() {
                    if (activeElement === this) {
                      activeElement = null;
                    }
                  },
                  setSelectionRange(start, end, direction = "none") {
                    this.selectionStart = start;
                    this.selectionEnd = end;
                    this.selectionDirection = direction;
                  },
                  scrollIntoView() {},
                });
                workspaceRegionEl.querySelector = (selector) => {
                  if (selector === "#detail-investigation-input") {
                    return composerInput;
                  }
                  if (selector === ".ticket-workspace") {
                    return workspaceRoot;
                  }
                  return null;
                };
                const originalGetElementById = document.getElementById;
                document.getElementById = (id) => {
                  if (id === "detail-investigation-input") {
                    return composerInput;
                  }
                  return originalGetElementById(id);
                };
                Object.defineProperty(document, "activeElement", {
                  configurable: true,
                  get() {
                    return activeElement;
                  },
                });
                Object.defineProperty(workspaceRegionEl, "innerHTML", {
                  configurable: true,
                  get() {
                    return this._html || "";
                  },
                  set(value) {
                    this._html = value;
                    headerRegion = { innerHTML: value };
                    staticRegion = { innerHTML: value };
                    threadBodyRegion = { innerHTML: value };
                    insightRegion = { innerHTML: value };
                    if (value.includes('id="detail-investigation-input"')) {
                      workspaceRoot = {
                        dataset: { detailTicketId: selectedTicketId },
                        querySelector(selector) {
                          if (selector === '[data-detail-section="header"]') {
                            return headerRegion;
                          }
                          if (selector === '[data-detail-section="investigation-static"]') {
                            return staticRegion;
                          }
                          if (selector === '[data-detail-section="investigation-thread-body"]') {
                            return threadBodyRegion;
                          }
                          if (selector === '[data-detail-section="insight"]') {
                            return insightRegion;
                          }
                          return null;
                        },
                      };
                      composerInput = createComposer();
                      if (activeElement && activeElement !== composerInput) {
                        activeElement = null;
                      }
                    } else {
                      workspaceRoot = null;
                      composerInput = null;
                      activeElement = null;
                    }
                  },
                });

                renderTicketDetail();
                composerInput.focus();
                composerInput.setSelectionRange(2, 2, "none");
                composerInput.scrollTop = 11;
                const originalComposer = composerInput;

                fetchJson = async (url) => {
                  if (url === "/api/engineer/tickets/TK-DETAIL-FOCUS?include_context=false") {
                    return {
                      ticket: {
                        ...selectedTicket,
                        updated_at: "2026-04-14T09:12:00+00:00",
                      },
                    };
                  }
                  throw new Error(`Unexpected url: ${url}`);
                };

                await refreshSelectedTicket({ silent: true, showLoading: false });

                if (composerInput !== originalComposer) {
                  throw new Error("Detail refresh should not recreate the active investigation composer.");
                }
                if (document.activeElement !== composerInput) {
                  throw new Error("Detail refresh should preserve focus on the active investigation composer.");
                }
                if (composerInput.selectionStart !== 2 || composerInput.selectionEnd !== 2) {
                  throw new Error("Detail refresh should preserve the investigation composer cursor position.");
                }
                if (composerInput.scrollTop !== 11) {
                  throw new Error("Detail refresh should preserve the investigation composer scroll position.");
                }
                if (composerInput.value !== "第二轮日志补充") {
                  throw new Error("Detail refresh should preserve the in-progress engineer draft.");
                }
              """
            )
        )

    def test_engineer_detail_thread_scrolled_up_auto_scrolls_smoothly_for_new_internal_message(self) -> None:
        self.run_engineer_app_script(
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

                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-SCROLL";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-SCROLL",
                  client_ticket_ref: {
                    ticket_id: "TK-CLIENT-SCROLL",
                    subject: "Black screen after join",
                  },
                  subject: "Black screen after join",
                  requester: "user-11",
                  status: "investigating",
                  created_at: "2026-04-15T09:00:00+00:00",
                  updated_at: "2026-04-15T09:10:00+00:00",
                  messages: [
                    {
                      id: "client-msg-1",
                      role: "customer",
                      content: "The remote video is black.",
                      created_at: "2026-04-15T09:00:00+00:00",
                    },
                  ],
                  active_investigation: {
                    id: "INV-SCROLL-1",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    opened_at: "2026-04-15T09:01:00+00:00",
                    updated_at: "2026-04-15T09:05:00+00:00",
                    messages: [
                      {
                        id: "inv-msg-1",
                        role: "engineer_ai",
                        content: "Please collect the latest SDK log.",
                        created_at: "2026-04-15T09:05:00+00:00",
                      },
                    ],
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };

                let workspaceRoot = null;
                let currentThreadBody = null;
                let currentTimelineList = null;
                let insightRegion = { innerHTML: "" };
                let threadScrollCalls = [];
                let timelineScrollCalls = [];
                const renderThreadHeights = [];
                const renderTimelineHeights = [];
                workspaceRegionEl.querySelector = (selector) => {
                  if (selector === ".ticket-workspace") {
                    return workspaceRoot;
                  }
                  if (selector === ".detail-conversation-thread-body") {
                    return currentThreadBody;
                  }
                  if (selector === ".detail-timeline-panel .message-list") {
                    return currentTimelineList;
                  }
                  return null;
                };
                Object.defineProperty(workspaceRegionEl, "innerHTML", {
                  configurable: true,
                  get() {
                    return this._html || "";
                  },
                  set(value) {
                    this._html = value;
                    currentThreadBody = {
                      innerHTML: "",
                      scrollTop: currentThreadBody?.scrollTop || 0,
                      scrollHeight: renderThreadHeights.shift() ?? 0,
                      clientHeight: 180,
                      scrollTo(options) {
                        threadScrollCalls.push(options);
                        this.scrollTop = typeof options?.top === "number" ? options.top : this.scrollTop;
                      },
                    };
                    currentTimelineList = {
                      scrollTop: currentTimelineList?.scrollTop || 0,
                      scrollHeight: renderTimelineHeights.shift() ?? 0,
                      clientHeight: 180,
                      scrollTo(options) {
                        timelineScrollCalls.push(options);
                        this.scrollTop = typeof options?.top === "number" ? options.top : this.scrollTop;
                      },
                    };
                    workspaceRoot = {
                      dataset: { detailTicketId: selectedTicketId },
                      querySelector(selector) {
                        if (selector === '[data-detail-section="header"]') {
                          return { innerHTML: "" };
                        }
                        if (selector === '[data-detail-section="investigation-static"]') {
                          return { innerHTML: "" };
                        }
                        if (selector === '[data-detail-section="investigation-thread-body"]') {
                          return currentThreadBody;
                        }
                        if (selector === '[data-detail-section="insight"]') {
                          return insightRegion;
                        }
                        if (selector === ".detail-conversation-thread-body") {
                          return currentThreadBody;
                        }
                        if (selector === ".detail-timeline-panel .message-list") {
                          return currentTimelineList;
                        }
                        return null;
                      },
                    };
                  },
                });

                renderThreadHeights.push(420);
                renderTimelineHeights.push(240);
                renderTicketDetail();
                flushFrames();

                const initialThreadScrollCalls = threadScrollCalls.length;
                currentThreadBody.scrollTop = 20;
                selectedTicket = {
                  ...selectedTicket,
                  active_investigation: {
                    ...selectedTicket.active_investigation,
                    updated_at: "2026-04-15T09:11:00+00:00",
                    messages: [
                      ...selectedTicket.active_investigation.messages,
                      {
                        id: "inv-msg-2",
                        role: "engineer",
                        content: "Customer confirmed reproduction on iOS 18.",
                        created_at: "2026-04-15T09:11:00+00:00",
                      },
                    ],
                  },
                  updated_at: "2026-04-15T09:11:00+00:00",
                };

                renderThreadHeights.push(520);
                renderTimelineHeights.push(240);
                renderTicketDetail();
                flushFrames();

                const latestThreadCall = threadScrollCalls[threadScrollCalls.length - 1];
                if (!latestThreadCall || latestThreadCall.top !== 520) {
                  throw new Error(`Expected the engineer thread rerender to smooth-scroll to 520, got ${JSON.stringify(latestThreadCall)}.`);
                }
                if (latestThreadCall.behavior !== "smooth") {
                  throw new Error(`Expected the engineer thread rerender to smooth-scroll to bottom, got ${JSON.stringify(latestThreadCall)}.`);
                }
                if (currentThreadBody.scrollTop !== 520) {
                  throw new Error(`Expected thread scrollTop 520 after auto-scroll, got ${currentThreadBody.scrollTop}.`);
                }
                if (currentThreadBody.innerHTML.includes("New messages")) {
                  throw new Error("Engineer thread should not expose a New messages indicator after removing unread CTA logic.");
                }
              """
            )
        )

    def test_engineer_detail_thread_near_bottom_auto_scrolls_smoothly_for_new_internal_message(self) -> None:
        self.run_engineer_app_script(
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

                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-SMOOTH";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-SMOOTH",
                  client_ticket_ref: {
                    ticket_id: "TK-CLIENT-SMOOTH",
                    subject: "Black screen after join",
                  },
                  subject: "Black screen after join",
                  requester: "user-11",
                  status: "investigating",
                  created_at: "2026-04-15T09:00:00+00:00",
                  updated_at: "2026-04-15T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-SMOOTH-1",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    opened_at: "2026-04-15T09:01:00+00:00",
                    updated_at: "2026-04-15T09:05:00+00:00",
                    messages: [
                      {
                        id: "inv-msg-1",
                        role: "engineer_ai",
                        content: "Please collect the latest SDK log.",
                        created_at: "2026-04-15T09:05:00+00:00",
                      },
                    ],
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };

                let workspaceRoot = null;
                let currentThreadBody = null;
                let currentTimelineList = null;
                const threadScrollCalls = [];
                const renderThreadHeights = [];
                const renderTimelineHeights = [];
                workspaceRegionEl.querySelector = (selector) => {
                  if (selector === ".ticket-workspace") {
                    return workspaceRoot;
                  }
                  if (selector === ".detail-conversation-thread-body") {
                    return currentThreadBody;
                  }
                  if (selector === ".detail-timeline-panel .message-list") {
                    return currentTimelineList;
                  }
                  return null;
                };
                Object.defineProperty(workspaceRegionEl, "innerHTML", {
                  configurable: true,
                  get() {
                    return this._html || "";
                  },
                  set(value) {
                    this._html = value;
                    currentThreadBody = {
                      scrollTop: currentThreadBody?.scrollTop || 0,
                      scrollHeight: renderThreadHeights.shift() ?? 0,
                      clientHeight: 160,
                      scrollTo(options) {
                        threadScrollCalls.push(options);
                        this.scrollTop = typeof options?.top === "number" ? options.top : this.scrollTop;
                      },
                    };
                    currentTimelineList = {
                      scrollTop: currentTimelineList?.scrollTop || 0,
                      scrollHeight: renderTimelineHeights.shift() ?? 0,
                      clientHeight: 160,
                      scrollTo() {},
                    };
                    workspaceRoot = {
                      dataset: { detailTicketId: selectedTicketId },
                      querySelector(selector) {
                        if (selector === '[data-detail-section="header"]') {
                          return { innerHTML: "" };
                        }
                        if (selector === '[data-detail-section="investigation-static"]') {
                          return { innerHTML: "" };
                        }
                        if (selector === '[data-detail-section="investigation-thread-body"]') {
                          return currentThreadBody;
                        }
                        if (selector === '[data-detail-section="insight"]') {
                          return { innerHTML: "" };
                        }
                        if (selector === ".detail-conversation-thread-body") {
                          return currentThreadBody;
                        }
                        if (selector === ".detail-timeline-panel .message-list") {
                          return currentTimelineList;
                        }
                        return null;
                      },
                    };
                  },
                });

                renderThreadHeights.push(300);
                renderTimelineHeights.push(240);
                renderTicketDetail();
                flushFrames();

                const initialThreadScrollCalls = threadScrollCalls.length;
                currentThreadBody.scrollTop = 170;
                selectedTicket = {
                  ...selectedTicket,
                  active_investigation: {
                    ...selectedTicket.active_investigation,
                    messages: [
                      ...selectedTicket.active_investigation.messages,
                      {
                        id: "inv-msg-2",
                        role: "engineer_ai",
                        content: "The decoder path looks normal so far.",
                        created_at: "2026-04-15T09:11:00+00:00",
                      },
                    ],
                  },
                };

                renderThreadHeights.push(520);
                renderTimelineHeights.push(240);
                renderTicketDetail();
                flushFrames();

                if (threadScrollCalls.length !== initialThreadScrollCalls + 1) {
                  throw new Error("Near-bottom engineer thread should auto-scroll exactly once for the new internal message.");
                }
                const latestCall = threadScrollCalls[threadScrollCalls.length - 1];
                if (latestCall.behavior !== "smooth") {
                  throw new Error(`Expected smooth auto-scroll in engineer thread, got ${JSON.stringify(latestCall)}.`);
                }
                if (currentThreadBody.scrollTop !== 520) {
                  throw new Error(`Expected thread to scroll to 520, got ${currentThreadBody.scrollTop}.`);
                }
              """
            )
        )

    def test_engineer_detail_send_note_forces_smooth_scroll_to_thread_bottom(self) -> None:
        self.run_engineer_app_script(
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

                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-SEND";
                tellAiDraft = "Please compare the renderer events.";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-SEND",
                  client_ticket_ref: {
                    ticket_id: "TK-CLIENT-SEND",
                    subject: "Black screen after join",
                  },
                  subject: "Black screen after join",
                  requester: "user-11",
                  status: "investigating",
                  created_at: "2026-04-15T09:00:00+00:00",
                  updated_at: "2026-04-15T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-SEND-1",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    opened_at: "2026-04-15T09:01:00+00:00",
                    updated_at: "2026-04-15T09:05:00+00:00",
                    messages: [
                      {
                        id: "inv-msg-1",
                        role: "engineer_ai",
                        content: "Please collect the latest SDK log.",
                        created_at: "2026-04-15T09:05:00+00:00",
                      },
                    ],
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };

                let workspaceRoot = null;
                let currentThreadBody = null;
                let currentTimelineList = null;
                const threadScrollCalls = [];
                const renderThreadHeights = [];
                const renderTimelineHeights = [];
                const sendButton = {
                  dataset: { detailAction: "send-tell-ai" },
                  disabled: false,
                };
                workspaceRegionEl.querySelector = (selector) => {
                  if (selector === ".ticket-workspace") {
                    return workspaceRoot;
                  }
                  if (selector === ".detail-conversation-thread-body") {
                    return currentThreadBody;
                  }
                  if (selector === ".detail-timeline-panel .message-list") {
                    return currentTimelineList;
                  }
                  return null;
                };
                Object.defineProperty(workspaceRegionEl, "innerHTML", {
                  configurable: true,
                  get() {
                    return this._html || "";
                  },
                  set(value) {
                    this._html = value;
                    currentThreadBody = {
                      scrollTop: currentThreadBody?.scrollTop || 0,
                      scrollHeight: renderThreadHeights.shift() ?? 0,
                      clientHeight: 160,
                      scrollTo(options) {
                        threadScrollCalls.push(options);
                        this.scrollTop = typeof options?.top === "number" ? options.top : this.scrollTop;
                      },
                    };
                    currentTimelineList = {
                      scrollTop: currentTimelineList?.scrollTop || 0,
                      scrollHeight: renderTimelineHeights.shift() ?? 0,
                      clientHeight: 160,
                      scrollTo() {},
                    };
                    workspaceRoot = {
                      dataset: { detailTicketId: selectedTicketId },
                      querySelector(selector) {
                        if (selector === '[data-detail-section="header"]') {
                          return { innerHTML: "" };
                        }
                        if (selector === '[data-detail-section="investigation-static"]') {
                          return { innerHTML: "" };
                        }
                        if (selector === '[data-detail-section="investigation-thread-body"]') {
                          return currentThreadBody;
                        }
                        if (selector === '[data-detail-section="insight"]') {
                          return { innerHTML: "" };
                        }
                        if (selector === ".detail-conversation-thread-body") {
                          return currentThreadBody;
                        }
                        if (selector === ".detail-timeline-panel .message-list") {
                          return currentTimelineList;
                        }
                        return null;
                      },
                    };
                  },
                });

                submitInvestigationMessage = async () => ({
                  status: "investigating",
                  updated_at: "2026-04-15T09:12:00+00:00",
                  active_investigation: {
                    ...selectedTicket.active_investigation,
                    messages: [
                      ...selectedTicket.active_investigation.messages,
                      {
                        id: "inv-msg-2",
                        role: "engineer",
                        content: "Please compare the renderer events.",
                        created_at: "2026-04-15T09:11:00+00:00",
                      },
                    ],
                  },
                });
                loadTickets = async () => {};
                refreshSelectedTicket = async () => {};

                renderThreadHeights.push(420);
                renderTimelineHeights.push(240);
                renderTicketDetail();
                flushFrames();

                currentThreadBody.scrollTop = 18;
                renderThreadHeights.push(540);
                renderTimelineHeights.push(240);
                renderThreadHeights.push(540);
                renderTimelineHeights.push(240);
                renderThreadHeights.push(540);
                renderTimelineHeights.push(240);
                await handleDetailClick({
                  target: {
                    closest(selector) {
                      return selector === "button[data-detail-action]" ? sendButton : null;
                    },
                  },
                });
                flushFrames();

                const latestCall = threadScrollCalls[threadScrollCalls.length - 1];
                if (!latestCall) {
                  throw new Error("Expected sending an engineer note to request thread scrolling.");
                }
                if (latestCall.behavior !== "smooth") {
                  throw new Error(`Expected sending an engineer note to use smooth scrolling, got ${JSON.stringify(latestCall)}.`);
                }
                if (currentThreadBody.scrollTop !== 540) {
                  throw new Error(`Expected engineer send to scroll to 540, got ${currentThreadBody.scrollTop}.`);
                }
              """
            )
        )

    def test_engineer_detail_timeline_scrolled_up_auto_scrolls_smoothly_for_new_customer_message(self) -> None:
        self.run_engineer_app_script(
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

                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-TIMELINE";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-TIMELINE",
                  client_ticket_ref: {
                    ticket_id: "TK-CLIENT-TIMELINE",
                    subject: "Black screen after join",
                  },
                  subject: "Black screen after join",
                  requester: "user-11",
                  status: "investigating",
                  created_at: "2026-04-15T09:00:00+00:00",
                  updated_at: "2026-04-15T09:10:00+00:00",
                  messages: [
                    {
                      id: "client-msg-1",
                      role: "customer",
                      content: "The remote video is black.",
                      created_at: "2026-04-15T09:00:00+00:00",
                    },
                  ],
                  active_investigation: {
                    id: "INV-TIMELINE-1",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    opened_at: "2026-04-15T09:01:00+00:00",
                    updated_at: "2026-04-15T09:05:00+00:00",
                    messages: [
                      {
                        id: "inv-msg-1",
                        role: "engineer_ai",
                        content: "Please collect the latest SDK log.",
                        created_at: "2026-04-15T09:05:00+00:00",
                      },
                    ],
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };

                let workspaceRoot = null;
                let currentThreadBody = null;
                let currentTimelineList = null;
                let insightRegion = { innerHTML: "" };
                const timelineScrollCalls = [];
                const renderThreadHeights = [];
                const renderTimelineHeights = [];
                workspaceRegionEl.querySelector = (selector) => {
                  if (selector === ".ticket-workspace") {
                    return workspaceRoot;
                  }
                  if (selector === ".detail-conversation-thread-body") {
                    return currentThreadBody;
                  }
                  if (selector === ".detail-timeline-panel .message-list") {
                    return currentTimelineList;
                  }
                  return null;
                };
                Object.defineProperty(workspaceRegionEl, "innerHTML", {
                  configurable: true,
                  get() {
                    return this._html || "";
                  },
                  set(value) {
                    this._html = value;
                    currentThreadBody = {
                      scrollTop: currentThreadBody?.scrollTop || 0,
                      scrollHeight: renderThreadHeights.shift() ?? 0,
                      clientHeight: 180,
                      scrollTo() {},
                    };
                    currentTimelineList = {
                      innerHTML: "",
                      scrollTop: currentTimelineList?.scrollTop || 0,
                      scrollHeight: renderTimelineHeights.shift() ?? 0,
                      clientHeight: 180,
                      scrollTo(options) {
                        timelineScrollCalls.push(options);
                        this.scrollTop = typeof options?.top === "number" ? options.top : this.scrollTop;
                      },
                    };
                    workspaceRoot = {
                      dataset: { detailTicketId: selectedTicketId },
                      querySelector(selector) {
                        if (selector === '[data-detail-section="header"]') {
                          return { innerHTML: "" };
                        }
                        if (selector === '[data-detail-section="investigation-static"]') {
                          return { innerHTML: "" };
                        }
                        if (selector === '[data-detail-section="investigation-thread-body"]') {
                          return currentThreadBody;
                        }
                        if (selector === '[data-detail-section="insight"]') {
                          return insightRegion;
                        }
                        if (selector === ".detail-conversation-thread-body") {
                          return currentThreadBody;
                        }
                        if (selector === ".detail-timeline-panel .message-list") {
                          return currentTimelineList;
                        }
                        return null;
                      },
                    };
                  },
                });

                renderThreadHeights.push(280);
                renderTimelineHeights.push(420);
                renderTicketDetail();
                flushFrames();

                const initialTimelineScrollCalls = timelineScrollCalls.length;
                currentTimelineList.scrollTop = 12;
                selectedTicket = {
                  ...selectedTicket,
                  messages: [
                    ...selectedTicket.messages,
                    {
                      id: "client-msg-2",
                      role: "assistant",
                      content: "We need the issue timestamp to investigate further.",
                      created_at: "2026-04-15T09:11:00+00:00",
                    },
                  ],
                };

                renderThreadHeights.push(280);
                renderTimelineHeights.push(520);
                renderTicketDetail();
                flushFrames();

                const latestTimelineCall = timelineScrollCalls[timelineScrollCalls.length - 1];
                if (!latestTimelineCall || latestTimelineCall.top !== 520) {
                  throw new Error(`Expected the customer timeline rerender to smooth-scroll to 520, got ${JSON.stringify(latestTimelineCall)}.`);
                }
                if (latestTimelineCall.behavior !== "smooth") {
                  throw new Error(`Expected the customer timeline rerender to smooth-scroll to bottom, got ${JSON.stringify(latestTimelineCall)}.`);
                }
                if (currentTimelineList.scrollTop !== 520) {
                  throw new Error(`Expected timeline scrollTop 520 after auto-scroll, got ${currentTimelineList.scrollTop}.`);
                }
                if (insightRegion.innerHTML.includes("New messages")) {
                  throw new Error("Customer timeline should not expose a New messages indicator after removing unread CTA logic.");
                }
              """
            )
        )

    def test_engineer_socket_ignores_unrelated_ticket_detail_refresh(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                let lastSocket = null;
                WebSocket = function WebSocket() {
                  this.readyState = 1;
                  this.close = () => {};
                  this.send = () => {};
                  lastSocket = this;
                };

                setAuthenticated(true);
                selectedTicketId = "ENG-CASE-001";
                selectedTicket = {
                  ticket_id: "ENG-CASE-001",
                  client_ticket_ref: {
                    ticket_id: "CLIENT-CASE-001",
                    subject: "Joined but black screen",
                  },
                  subject: "Joined but black screen",
                  requester: "user-10",
                  status: "investigating",
                  created_at: "2026-04-14T09:00:00+00:00",
                  updated_at: "2026-04-14T09:10:00+00:00",
                  messages: [],
                  active_investigation: null,
                  investigation_history: [],
                  engineer_request_records: [],
                };

                let loadOptions = null;
                let refreshCalls = 0;
                loadTickets = async (options = {}) => {
                  loadOptions = options;
                };
                refreshSelectedTicket = async () => {
                  refreshCalls += 1;
                };

                setupWebSocket();
                await lastSocket.onmessage({
                  data: JSON.stringify({
                    event: "ticket_updated",
                    ticket_id: "ENG-CASE-999",
                    client_ticket_id: "CLIENT-CASE-999",
                  }),
                });

                if (!loadOptions || loadOptions.refreshDetail !== false) {
                  throw new Error("Engineer websocket should refresh the pool without forcing detail refresh for unrelated tickets.");
                }
                if (refreshCalls !== 0) {
                  throw new Error("Engineer websocket should not refresh the open detail view for unrelated tickets.");
                }
              """
            )
        )

    def test_engineer_socket_refreshes_open_detail_for_matching_ticket(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                let lastSocket = null;
                WebSocket = function WebSocket() {
                  this.readyState = 1;
                  this.close = () => {};
                  this.send = () => {};
                  lastSocket = this;
                };

                setAuthenticated(true);
                selectedTicketId = "ENG-CASE-001";
                selectedTicket = {
                  ticket_id: "ENG-CASE-001",
                  client_ticket_id: "CLIENT-CASE-001",
                  client_ticket_ref: {
                    ticket_id: "CLIENT-CASE-001",
                    subject: "Joined but black screen",
                  },
                  subject: "Joined but black screen",
                  requester: "user-10",
                  status: "investigating",
                  created_at: "2026-04-14T09:00:00+00:00",
                  updated_at: "2026-04-14T09:10:00+00:00",
                  messages: [],
                  active_investigation: null,
                  investigation_history: [],
                  engineer_request_records: [],
                };

                let loadOptions = null;
                let refreshOptions = null;
                let refreshCalls = 0;
                loadTickets = async (options = {}) => {
                  loadOptions = options;
                };
                refreshSelectedTicket = async (options = {}) => {
                  refreshOptions = options;
                  refreshCalls += 1;
                };

                setupWebSocket();
                await lastSocket.onmessage({
                  data: JSON.stringify({
                    event: "ticket_updated",
                    engineer_case_id: "ENG-CASE-001",
                    client_ticket_id: "CLIENT-CASE-001",
                  }),
                });

                if (!loadOptions || loadOptions.refreshDetail !== false) {
                  throw new Error("Engineer websocket should still refresh the pool without forcing detail refresh in the pool call.");
                }
                if (refreshCalls !== 1) {
                  throw new Error("Engineer websocket should refresh the open detail view when the payload matches the current ticket.");
                }
                if (!refreshOptions || refreshOptions.silent !== true) {
                  throw new Error("Engineer websocket should refresh the matching open detail silently.");
                }
              """
            )
        )

    def test_engineer_detail_approval_revision_send_uses_same_optimistic_thread_flow(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-REV-OPT";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-REV-OPT",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-REV-OPT",
                    state: "awaiting_confirmation",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                    final_confirmation_requested_at: "2026-03-24T09:05:00+00:00",
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-REV-OPT-m1",
                        role: "engineer_ai",
                        content: "I have enough information now. Please confirm this draft before I reply to the customer.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  engineer_agent_state: {
                    phase: "awaiting_confirmation",
                    ready_to_reply: true,
                    reply_readiness: {
                      has_conclusion: true,
                      has_proof: true,
                      has_solution_or_next_step: true,
                      conclusion_summary: "Android 14 with SDK 4.2.1 reproduces the token renew failure.",
                      proof_summary: "The engineer reproduced the issue on Android 14 with SDK 4.2.1 only.",
                      proof_anchors: ["Android 14", "SDK 4.2.1"],
                      solution_or_next_step: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                      blockers: [],
                      critique: "The current evidence supports the customer-safe SDK upgrade guidance.",
                      ready_for_customer_reply: true,
                    },
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };
                selectedTicketSummary = "Customer-facing answer is drafted and waiting for engineer confirmation.";
                selectedTicketNextAction = "Approve the prepared reply or send a revision note.";

                let resolveFetch = null;
                let capturedUrl = null;
                let capturedOptions = null;
                fetchJson = async (url, options = undefined) => {
                  capturedUrl = url;
                  capturedOptions = options;
                  return await new Promise((resolve) => {
                    resolveFetch = resolve;
                  });
                };
                loadTickets = async () => {};
                refreshSelectedTicket = async () => {};

                tellAiDraft = "Mention clearing cached auth data before retrying.";
                const sendButton = {
                  dataset: { detailAction: "send-tell-ai" },
                  disabled: false,
                };
                const sendTarget = {
                  closest(selector) {
                    if (selector === "button[data-detail-action]") {
                      return sendButton;
                    }
                    return null;
                  },
                };

                const sendPromise = handleDetailClick({ target: sendTarget });
                const optimisticHtml = workspaceRegionEl.innerHTML;
                if (!optimisticHtml.includes("Mention clearing cached auth data before retrying.")) {
                  throw new Error("Approval-state revision notes should also render optimistically in the thread.");
                }
                if (!optimisticHtml.includes(">jack<")) {
                  throw new Error("Approval-state revision notes should keep the jack label.");
                }
                if (!optimisticHtml.includes("message-item-pending-ai")) {
                  throw new Error("Approval-state revision sends should show the Engineer AI placeholder bubble.");
                }
                if (capturedUrl !== "/api/engineer/tickets/TK-DETAIL-REV-OPT/investigation/confirmation") {
                  throw new Error("Approval-state optimistic sends should still target the confirmation endpoint.");
                }
                if (capturedOptions?.timeoutMs !== 100000) {
                  throw new Error("Revision sends should also use the extended 100s Sid timeout budget.");
                }
                const parsedBody = JSON.parse(capturedOptions.body);
                if (parsedBody.decision !== "revise") {
                  throw new Error("Approval-state optimistic sends should still submit decision=revise.");
                }

                resolveFetch({
                  ticket_id: "TK-DETAIL-REV-OPT",
                  status: "investigating",
                  updated_at: "2026-03-24T09:11:00+00:00",
                  active_investigation: {
                    id: "INV-DETAIL-REV-OPT",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:11:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-REV-OPT-m1",
                        role: "engineer_ai",
                        content: "I have enough information now. Please confirm this draft before I reply to the customer.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                      {
                        id: "INV-DETAIL-REV-OPT-m2",
                        role: "engineer",
                        content: "Mention clearing cached auth data before retrying.",
                        created_at: "2026-03-24T09:10:30+00:00",
                      },
                      {
                        id: "INV-DETAIL-REV-OPT-m3",
                        role: "engineer_ai",
                        content: "Understood. I will revise the customer reply to include the cache-clear step.",
                        created_at: "2026-03-24T09:11:00+00:00",
                      },
                    ],
                  },
                });
                await sendPromise;
              """
            )
        )

    def test_engineer_detail_send_failure_keeps_engineer_message_and_restores_input(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-FAIL";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-FAIL",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-FAIL",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-FAIL-m1",
                        role: "engineer_ai",
                        content: "Please share the Android version and latest logcat excerpt.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };
                selectedTicketSummary = "Sid needs one more technical detail.";
                selectedTicketNextAction = "Share the latest Android logcat excerpt.";

                fetchJson = async () => {
                  throw new Error("Request failed with status 500");
                };
                loadTickets = async () => {};
                refreshSelectedTicket = async () => {};

                tellAiDraft = "Logcat now shows auth timeout before channel join.";
                const sendButton = {
                  dataset: { detailAction: "send-tell-ai" },
                  disabled: false,
                };
                const sendTarget = {
                  closest(selector) {
                    if (selector === "button[data-detail-action]") {
                      return sendButton;
                    }
                    return null;
                  },
                };

                await handleDetailClick({ target: sendTarget });
                const failedHtml = workspaceRegionEl.innerHTML;
                if (!failedHtml.includes("Logcat now shows auth timeout before channel join.")) {
                  throw new Error("Failed sends should keep the optimistic engineer message in the thread.");
                }
                if (!failedHtml.includes(">jack<")) {
                  throw new Error("Failed sends should keep the jack label on the optimistic engineer message.");
                }
                if (failedHtml.includes("message-item-pending-ai")) {
                  throw new Error("Failed sends should remove the pending Engineer AI placeholder bubble.");
                }
                if (!failedHtml.includes("Sid update failed: Request failed with status 500")) {
                  throw new Error("Failed sends should append an inline system error message to the thread.");
                }
                if (tellAiDraft !== "Logcat now shows auth timeout before channel join.") {
                  throw new Error("Failed sends should restore the original draft to the composer.");
                }
                if (!failedHtml.includes(">Logcat now shows auth timeout before channel join.<")) {
                  throw new Error("Failed sends should re-render the composer with the restored draft.");
                }
              """
            )
        )

    def test_engineer_detail_refresh_clears_local_pending_state_when_durable_action_matches(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-RECONCILE";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-RECONCILE",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-RECONCILE",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-RECONCILE-m1",
                        role: "engineer_ai",
                        content: "Please share the Android version and latest logcat excerpt.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };
                selectedTicketSummary = "Sid needs one more technical detail.";
                selectedTicketNextAction = "Share the latest Android logcat excerpt.";

                localInvestigationThreadState = {
                  ticketId: "TK-DETAIL-RECONCILE",
                  pendingAi: true,
                  pendingAction: "investigation_message",
                  pendingNote: "Logcat now shows auth timeout before channel join.",
                  submittedAt: "2026-03-24T09:10:30+00:00",
                  messages: [
                    {
                      id: "local-engineer-1",
                      role: "engineer",
                      content: "Logcat now shows auth timeout before channel join.",
                      created_at: "2026-03-24T09:10:30+00:00",
                      is_optimistic_local: true,
                    },
                    {
                      id: "local-engineer-ai-1",
                      role: "engineer_ai",
                      content: "Sid is reviewing your update...",
                      created_at: "2026-03-24T09:10:30+00:00",
                      is_pending_ai: true,
                    },
                  ],
                };

                fetchJson = async (url) => {
                  if (url === "/api/engineer/tickets/TK-DETAIL-RECONCILE?include_context=false") {
                    return {
                      ticket: {
                        ...selectedTicket,
                        updated_at: "2026-03-24T09:11:00+00:00",
                        last_engineer_action: {
                          action: "investigation_message",
                          note: "Logcat now shows auth timeout before channel join.",
                          created_at: "2026-03-24T09:11:00+00:00",
                          engineer_id: "Jack",
                        },
                        active_investigation: {
                          ...selectedTicket.active_investigation,
                          updated_at: "2026-03-24T09:11:00+00:00",
                          messages: [
                            ...selectedTicket.active_investigation.messages,
                            {
                              id: "INV-DETAIL-RECONCILE-m2",
                              role: "engineer",
                              content: "Logcat now shows auth timeout before channel join.",
                              created_at: "2026-03-24T09:10:30+00:00",
                            },
                            {
                              id: "INV-DETAIL-RECONCILE-m3",
                              role: "engineer_ai",
                              content: "Thanks. Please also confirm whether clearing the cached token changes the behavior.",
                              created_at: "2026-03-24T09:11:00+00:00",
                            },
                          ],
                        },
                      },
                    };
                  }
                  throw new Error(`Unexpected URL: ${url}`);
                };

                await refreshSelectedTicket({ silent: true, showLoading: false });
                const refreshedHtml = workspaceRegionEl.innerHTML;
                if (refreshedHtml.includes("message-item-pending-ai")) {
                  throw new Error("Durable detail refresh should clear the local pending AI placeholder once the backend action matches.");
                }
                if (!refreshedHtml.includes("Thanks. Please also confirm whether clearing the cached token changes the behavior.")) {
                  throw new Error("Durable detail refresh should show the backend Engineer AI reply after reconciliation.");
                }
                if (localInvestigationThreadState !== null) {
                  throw new Error("Durable detail refresh should clear the local pending investigation state after reconciliation.");
                }
              """
            )
        )

    def test_engineer_detail_timeout_recovers_without_inline_failure_after_durable_refresh(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-TIMEOUT-RECOVER";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-TIMEOUT-RECOVER",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-TIMEOUT-RECOVER",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-TIMEOUT-RECOVER-m1",
                        role: "engineer_ai",
                        content: "Please share the Android version and latest logcat excerpt.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };
                selectedTicketSummary = "Sid needs one more technical detail.";
                selectedTicketNextAction = "Share the latest Android logcat excerpt.";
                const durableActionAt = new Date(Date.now() + 1000).toISOString();

                setTimeout = (callback) => {
                  callback();
                  return 1;
                };
                clearTimeout = () => {};

                let loadCalls = 0;
                let detailRefreshCalls = 0;
                let capturedOptions = null;
                loadTickets = async (options = {}) => {
                  loadCalls += 1;
                  if (options.refreshDetail !== false) {
                    throw new Error("Timeout recovery should refresh the pool without forcing detail refresh from loadTickets.");
                  }
                };

                fetchJson = async (url, options = undefined) => {
                  if (url === "/api/engineer/tickets/TK-DETAIL-TIMEOUT-RECOVER/investigation/messages") {
                    capturedOptions = options;
                    throw new Error("Request timed out after 100s");
                  }
                  if (url === "/api/engineer/tickets/TK-DETAIL-TIMEOUT-RECOVER?include_context=false") {
                    detailRefreshCalls += 1;
                    return {
                      ticket: {
                        ...selectedTicket,
                        updated_at: "2026-03-24T09:11:00+00:00",
                        last_engineer_action: {
                          action: "investigation_message",
                          note: "Logcat now shows auth timeout before channel join.",
                          created_at: durableActionAt,
                          engineer_id: "Jack",
                        },
                        active_investigation: {
                          ...selectedTicket.active_investigation,
                          updated_at: durableActionAt,
                          messages: [
                            ...selectedTicket.active_investigation.messages,
                            {
                              id: "INV-DETAIL-TIMEOUT-RECOVER-m2",
                              role: "engineer",
                              content: "Logcat now shows auth timeout before channel join.",
                              created_at: durableActionAt,
                            },
                            {
                              id: "INV-DETAIL-TIMEOUT-RECOVER-m3",
                              role: "engineer_ai",
                              content: "Thanks. Please also confirm whether clearing the cached token changes the behavior.",
                              created_at: durableActionAt,
                            },
                          ],
                        },
                      },
                    };
                  }
                  throw new Error(`Unexpected URL: ${url}`);
                };

                tellAiDraft = "Logcat now shows auth timeout before channel join.";
                const sendButton = {
                  dataset: { detailAction: "send-tell-ai" },
                  disabled: false,
                };
                const sendTarget = {
                  closest(selector) {
                    if (selector === "button[data-detail-action]") {
                      return sendButton;
                    }
                    return null;
                  },
                };

                await handleDetailClick({ target: sendTarget });
                const recoveredHtml = workspaceRegionEl.innerHTML;
                if (capturedOptions?.timeoutMs !== 100000) {
                  throw new Error("Timeout recovery should keep using the extended 100s Sid timeout budget.");
                }
                if (loadCalls < 1) {
                  throw new Error("Timeout recovery should trigger at least one silent pool refresh attempt.");
                }
                if (detailRefreshCalls < 1) {
                  throw new Error("Timeout recovery should re-fetch the selected ticket detail.");
                }
                if (recoveredHtml.includes("Sid update failed:")) {
                  throw new Error("Recovered timeout sends should not append the inline Sid failure message.");
                }
                if (recoveredHtml.includes("message-item-pending-ai")) {
                  throw new Error("Recovered timeout sends should clear the pending AI placeholder once durable success is found.");
                }
                if (!recoveredHtml.includes("Thanks. Please also confirm whether clearing the cached token changes the behavior.")) {
                  throw new Error("Recovered timeout sends should render the durable Engineer AI reply.");
                }
                if (tellAiDraft !== "") {
                  throw new Error("Recovered timeout sends should keep the composer cleared after durable success.");
                }
                if (localInvestigationThreadState !== null) {
                  throw new Error("Recovered timeout sends should clear the local pending state after durable success.");
                }
              """
            )
        )

    def test_engineer_ticket_pool_load_tickets_is_single_flight_with_one_queued_follow_up(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "pool";
                URLSearchParams = globalThis.URLSearchParams = class URLSearchParams {
                  constructor(init = {}) {
                    this._pairs = Object.entries(init);
                  }

                  toString() {
                    return this._pairs
                      .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
                      .join("&");
                  }
                };
                selectedTicketId = "TK-QUEUE-001";
                selectedTicket = {
                  ticket_id: "TK-QUEUE-001",
                  status: "investigating",
                };
                tickets = [];
                boardLoading = false;

                let refreshCalls = 0;
                refreshSelectedTicket = async () => {
                  refreshCalls += 1;
                };

                const resolvers = [];
                const payload = {
                  tickets: [
                    {
                      ticket_id: "TK-QUEUE-001",
                      engineer_case_id: "TK-QUEUE-001",
                      title: "Queue stabilization issue",
                      subject: "Queue stabilization issue",
                      requester: "user-queue",
                      customer_id: "user-queue",
                      status: "investigating",
                      created_at: "2026-03-24T08:00:00+00:00",
                      updated_at: "2026-03-24T08:30:00+00:00",
                      client_ticket_ref: {
                        ticket_id: "TK-QUEUE",
                        subject: "Queue stabilization issue",
                      },
                      messages: [],
                      active_investigation: null,
                      investigation_history: [],
                      engineer_handoff_packet: null,
                      engineer_agent_state: null,
                    },
                  ],
                };
                let fetchCalls = 0;
                fetchJson = async (url) => {
                  if (url !== "/api/engineer/tickets?status=all") {
                    throw new Error(`Unexpected URL: ${url}`);
                  }
                  fetchCalls += 1;
                  return await new Promise((resolve) => {
                    resolvers.push(() => resolve(payload));
                  });
                };

                const first = loadTickets({ refreshDetail: false, showLoading: true });
                const second = loadTickets({ refreshDetail: true, showLoading: false });
                const third = loadTickets({ refreshDetail: false, showLoading: false });
                await Promise.resolve();
                if (fetchCalls !== 1) {
                  throw new Error(`Expected one in-flight pool fetch before resolution, got ${fetchCalls}.`);
                }
                if (!boardLoading) {
                  throw new Error("First empty-board load should keep the loading state visible while in flight.");
                }

                const resolveFirst = resolvers.shift();
                if (!resolveFirst) {
                  throw new Error("Expected the first pool fetch resolver to exist.");
                }
                resolveFirst();
                for (let step = 0; step < 6 && fetchCalls < 2; step += 1) {
                  await Promise.resolve();
                }

                if (fetchCalls !== 2) {
                  throw new Error(`Expected exactly one queued follow-up pool fetch, got ${fetchCalls}.`);
                }

                const resolveSecond = resolvers.shift();
                if (!resolveSecond) {
                  throw new Error("Expected the queued follow-up pool fetch resolver to exist.");
                }
                resolveSecond();
                await Promise.all([first, second, third]);

                if (fetchCalls !== 2) {
                  throw new Error(`Queued loadTickets calls should collapse into exactly two fetches, got ${fetchCalls}.`);
                }
                if (refreshCalls !== 1) {
                  throw new Error(`Queued refreshDetail requests should merge into one detail refresh, got ${refreshCalls}.`);
                }
                if (boardLoading) {
                  throw new Error("Ticket pool loading state should clear after the queued follow-up fetch completes.");
                }
              """
            )
        )

    def test_engineer_detail_blocked_guardrail_keeps_revision_composer(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-GUARDRAIL-BLOCKED";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-GUARDRAIL-BLOCKED",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-GUARDRAIL-BLOCKED",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "This is engineer-only internal use only. Please upgrade.",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-GUARDRAIL-BLOCKED-m1",
                        role: "engineer_ai",
                        content: "Guardrail final review complete. Decision: blocked.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  engineer_agent_state: {
                    phase: "guardrail_blocked",
                    ready_to_reply: true,
                    reply_readiness: {
                      has_conclusion: true,
                      has_proof: true,
                      has_solution_or_next_step: true,
                      conclusion_summary: "Android 14 with SDK 4.2.1 reproduces the token renew failure.",
                      proof_summary: "The engineer reproduced the issue on Android 14 with SDK 4.2.1 only.",
                      proof_anchors: ["Android 14", "SDK 4.2.1"],
                      solution_or_next_step: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                      blockers: [],
                      critique: "The current evidence supports the customer-safe SDK upgrade guidance.",
                      ready_for_customer_reply: true,
                    },
                    active_guardrail_final: {
                      guardrail_id: "GRD-blocked",
                      guardrail_version: "engineer-guardrail-final-v1",
                      decision: "blocked",
                      customer_reply: "Hi there,\\n\\nThis is engineer-only internal use only. Please upgrade.\\n\\nBest Regards,\\nSid",
                      normalized_customer_reply: "Hi there,\\n\\nThis is engineer-only internal use only. Please upgrade.\\n\\nBest Regards,\\nSid",
                      evidence_refs: [],
                      checks: {
                        proof: { passed: true, detail: "Proof check passed." },
                        citation: { passed: true, detail: "No evidence packet provided." },
                        no_internal_leakage: { passed: false, detail: "Customer reply may contain internal-only content." },
                        no_unsupported_claims: { passed: true, detail: "No unsupported claims detected." },
                        style: { passed: true, detail: "Style check passed." },
                      },
                      blockers: ["no_internal_leakage: Customer reply may contain internal-only content."],
                      created_at: "2026-03-24T09:05:00+00:00",
                    },
                    final_approval_required: false,
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };

                const html = renderTicketDetailView();
                if (!html.includes("Guardrail Final Review")) {
                  throw new Error("Blocked guardrail should render the guardrail review card.");
                }
                if (!html.includes("Blocked")) {
                  throw new Error("Blocked guardrail should show the blocked decision.");
                }
                if (!html.includes("Ask AI to Revise")) {
                  throw new Error("Blocked guardrail should expose a revision action.");
                }
                if (!html.includes('id="detail-investigation-input"')) {
                  throw new Error("Blocked guardrail should keep the revision composer available.");
                }
                if (html.includes("Final Approve &amp; Send")) {
                  throw new Error("Blocked guardrail must not expose final approval.");
                }
                if (html.includes("Approve for Guardrail")) {
                  throw new Error("Blocked guardrail should not immediately show the first approve button again.");
                }

                let capturedUrl = null;
                let capturedOptions = null;
                fetchJson = async (url, options = undefined) => {
                  capturedUrl = url;
                  capturedOptions = options;
                  return {
                    ticket_id: "TK-DETAIL-GUARDRAIL-BLOCKED",
                    status: "investigating",
                    active_investigation: selectedTicket.active_investigation,
                    closed_investigation: null,
                    updated_at: "2026-03-24T09:11:00+00:00",
                  };
                };
                loadTickets = async () => {};
                refreshSelectedTicket = async () => {};

                setInvestigationComposerDraftFromMarkdown("Remove the internal-only wording and keep the customer reply safe.");
                const sendButton = {
                  dataset: { detailAction: "send-tell-ai" },
                  disabled: false,
                };
                const sendTarget = {
                  closest(selector) {
                    if (selector === "button[data-detail-action]") {
                      return sendButton;
                    }
                    return null;
                  },
                };
                await handleDetailClick({ target: sendTarget });
                if (capturedUrl !== "/api/engineer/tickets/TK-DETAIL-GUARDRAIL-BLOCKED/investigation/confirmation") {
                  throw new Error("Blocked guardrail revision should use the investigation confirmation endpoint.");
                }
                const parsedBody = JSON.parse(capturedOptions.body);
                if (parsedBody.decision !== "revise") {
                  throw new Error("Blocked guardrail revision should submit decision=revise.");
                }
                if (!parsedBody.note.includes("Remove the internal-only wording")) {
                  throw new Error("Blocked guardrail revision should include engineer feedback.");
                }
              """
            )
        )

    def test_engineer_detail_approve_action_hides_controls_while_request_is_pending(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-APPROVE-PENDING";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-APPROVE-PENDING",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-APPROVE-PENDING",
                    state: "awaiting_confirmation",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                    final_confirmation_requested_at: "2026-03-24T09:05:00+00:00",
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-APPROVE-PENDING-m1",
                        role: "engineer_ai",
                        content: "I have enough information now. Please confirm this draft before I reply to the customer.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  engineer_agent_state: {
                    phase: "awaiting_confirmation",
                    ready_to_reply: true,
                    reply_readiness: {
                      has_conclusion: true,
                      has_proof: true,
                      has_solution_or_next_step: true,
                      conclusion_summary: "Android 14 with SDK 4.2.1 reproduces the token renew failure.",
                      proof_summary: "The engineer reproduced the issue on Android 14 with SDK 4.2.1 only.",
                      proof_anchors: ["Android 14", "SDK 4.2.1"],
                      solution_or_next_step: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                      blockers: [],
                      critique: "The current evidence supports the customer-safe SDK upgrade guidance.",
                      ready_for_customer_reply: true,
                    },
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };
                selectedTicketSummary = "Customer-facing answer is drafted and waiting for engineer confirmation.";
                selectedTicketNextAction = "Approve the prepared reply or ask the AI to revise it.";

                const initialHtml = renderTicketDetailView();
                if (!initialHtml.includes("Approve for Guardrail")) {
                  throw new Error("Awaiting-confirmation state should still render the approve button before submission.");
                }
                if (!initialHtml.includes('id="detail-investigation-input"')) {
                  throw new Error("Awaiting-confirmation state should still render the revision composer before submission.");
                }

                let resolveFetch = null;
                let capturedUrl = null;
                let capturedOptions = null;
                fetchJson = async (url, options = undefined) => {
                  capturedUrl = url;
                  capturedOptions = options;
                  return await new Promise((resolve) => {
                    resolveFetch = resolve;
                  });
                };
                loadTickets = async () => {};
                refreshSelectedTicket = async () => {};

                const approveButton = {
                  dataset: { detailAction: "approve-investigation" },
                  disabled: false,
                };
                const approveTarget = {
                  closest(selector) {
                    if (selector === "button[data-detail-action]") {
                      return approveButton;
                    }
                    return null;
                  },
                };

                const approvePromise = handleDetailClick({ target: approveTarget });
                const pendingHtml = workspaceRegionEl.innerHTML;
                if (capturedUrl !== "/api/engineer/tickets/TK-DETAIL-APPROVE-PENDING/investigation/confirmation") {
                  throw new Error("Approve should call the investigation confirmation endpoint.");
                }
                if (capturedOptions?.timeoutMs !== 25000) {
                  throw new Error("Approve for Guardrail should keep the short timeout budget instead of the 100s AI-turn timeout.");
                }
                const parsedBody = JSON.parse(capturedOptions.body);
                if (parsedBody.decision !== "approve") {
                  throw new Error("Approve should still submit decision=approve while pending.");
                }
                if (pendingHtml.includes("Approve for Guardrail")) {
                  throw new Error("Pending approve should hide the inline approve button immediately.");
                }
                if (pendingHtml.includes('id="detail-investigation-input"')) {
                  throw new Error("Pending approve should hide the composer immediately.");
                }
                if (pendingHtml.includes("Draft Customer Reply")) {
                  throw new Error("Pending approve should not keep the standalone draft preview visible.");
                }
                if (pendingHtml.includes("Readiness Review")) {
                  throw new Error("Pending approve should hide the internal review while the reply is being approved.");
                }
                if (!pendingHtml.includes("detail-investigation-closing-state")) {
                  throw new Error("Pending approve should render the closing-state marker.");
                }
                if (!pendingHtml.includes("Running Guardrail Review")) {
                  throw new Error("Pending approve should show the closing-state title.");
                }
                if (!pendingHtml.includes("Running final guardrail review before sending to customer...")) {
                  throw new Error("Pending approve should show the closing-state explanation.");
                }
                if (!tellAiSubmitting) {
                  throw new Error("Pending approve should still mark the UI as submitting.");
                }

                resolveFetch({
                  ticket_id: "TK-DETAIL-APPROVE-PENDING",
                  status: "investigating",
                  active_investigation: {
                    id: "INV-DETAIL-APPROVE-PENDING",
                    state: "awaiting_final_approval",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                    final_confirmation_requested_at: null,
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:11:00+00:00",
                    closed_at: null,
                    messages: [
                      {
                        id: "INV-DETAIL-APPROVE-PENDING-m1",
                        role: "engineer_ai",
                        content: "I have enough information now. Please confirm this draft before I reply to the customer.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                      {
                        id: "INV-DETAIL-APPROVE-PENDING-m2",
                        role: "engineer_ai",
                        content: "Guardrail final review complete. Decision: approved_for_final_engineer_review.",
                        created_at: "2026-03-24T09:11:00+00:00",
                      },
                    ],
                  },
                  closed_investigation: null,
                  active_guardrail_final: {
                    guardrail_id: "GRD-test123456",
                    guardrail_version: "engineer-guardrail-final-v1",
                    decision: "approved_for_final_engineer_review",
                    customer_reply: "Hi there,\\n\\nPlease upgrade to SDK 4.2.2 and retry token renewal on Android 14.\\n\\nBest Regards,\\nSid",
                    normalized_customer_reply: "Hi there,\\n\\nPlease upgrade to SDK 4.2.2 and retry token renewal on Android 14.\\n\\nBest Regards,\\nSid",
                    evidence_refs: [],
                    checks: {
                      proof: { passed: true, detail: "Proof check passed." },
                      citation: { passed: true, detail: "No evidence packet provided." },
                      no_internal_leakage: { passed: true, detail: "No internal-only leakage detected." },
                      no_unsupported_claims: { passed: true, detail: "No unsupported claims detected." },
                      style: { passed: true, detail: "Style check passed." },
                    },
                    blockers: [],
                    created_at: "2026-03-24T09:11:00+00:00",
                  },
                  updated_at: "2026-03-24T09:11:00+00:00",
                });
                await approvePromise;
              """
            )
        )

    def test_engineer_detail_approve_failure_restores_hidden_controls(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-APPROVE-FAIL";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-APPROVE-FAIL",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-APPROVE-FAIL",
                    state: "awaiting_confirmation",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                    final_confirmation_requested_at: "2026-03-24T09:05:00+00:00",
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-APPROVE-FAIL-m1",
                        role: "engineer_ai",
                        content: "I have enough information now. Please confirm this draft before I reply to the customer.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  engineer_agent_state: {
                    phase: "awaiting_confirmation",
                    ready_to_reply: true,
                    reply_readiness: {
                      has_conclusion: true,
                      has_proof: true,
                      has_solution_or_next_step: true,
                      conclusion_summary: "Android 14 with SDK 4.2.1 reproduces the token renew failure.",
                      proof_summary: "The engineer reproduced the issue on Android 14 with SDK 4.2.1 only.",
                      proof_anchors: ["Android 14", "SDK 4.2.1"],
                      solution_or_next_step: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                      blockers: [],
                      critique: "The current evidence supports the customer-safe SDK upgrade guidance.",
                      ready_for_customer_reply: true,
                    },
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };
                selectedTicketSummary = "Customer-facing answer is drafted and waiting for engineer confirmation.";
                selectedTicketNextAction = "Approve the prepared reply or ask the AI to revise it.";

                let alertMessage = null;
                window.alert = (message) => {
                  alertMessage = message;
                };
                fetchJson = async () => {
                  throw new Error("Request failed with status 500");
                };
                loadTickets = async () => {};
                refreshSelectedTicket = async () => {};

                const approveButton = {
                  dataset: { detailAction: "approve-investigation" },
                  disabled: false,
                };
                const approveTarget = {
                  closest(selector) {
                    if (selector === "button[data-detail-action]") {
                      return approveButton;
                    }
                    return null;
                  },
                };

                await handleDetailClick({ target: approveTarget });
                const failedHtml = workspaceRegionEl.innerHTML;
                if (!failedHtml.includes("Approve for Guardrail")) {
                  throw new Error("Failed approve should restore the approve button.");
                }
                if (!failedHtml.includes('id="detail-investigation-input"')) {
                  throw new Error("Failed approve should restore the composer.");
                }
                if (!failedHtml.includes("Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.")) {
                  throw new Error("Failed approve should keep the draft preview visible.");
                }
                if (!failedHtml.includes("Readiness Review")) {
                  throw new Error("Failed approve should restore the internal review.");
                }
                if (failedHtml.includes("detail-investigation-closing-state")) {
                  throw new Error("Failed approve should remove the temporary closing-state marker.");
                }
                if (alertMessage !== "Approve for guardrail failed: Request failed with status 500") {
                  throw new Error("Failed approve should keep the existing failure alert copy.");
                }
                if (tellAiSubmitting) {
                  throw new Error("Failed approve should clear the submitting lock.");
                }
              """
            )
        )

    def test_engineer_detail_approve_action_refreshes_into_closed_read_only_thread(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-APPROVE";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-APPROVE",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-APPROVE",
                    state: "awaiting_confirmation",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                    final_confirmation_requested_at: "2026-03-24T09:05:00+00:00",
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-APPROVE-m1",
                        role: "engineer_ai",
                        content: "I have enough information now. Please confirm this draft before I reply to the customer.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  engineer_agent_state: {
                    phase: "awaiting_confirmation",
                    ready_to_reply: true,
                    reply_readiness: {
                      has_conclusion: true,
                      has_proof: true,
                      has_solution_or_next_step: true,
                      conclusion_summary: "Android 14 with SDK 4.2.1 reproduces the token renew failure.",
                      proof_summary: "The engineer reproduced the issue on Android 14 with SDK 4.2.1 only.",
                      proof_anchors: ["Android 14", "SDK 4.2.1"],
                      solution_or_next_step: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                      blockers: [],
                      critique: "The current evidence supports the customer-safe SDK upgrade guidance.",
                      ready_for_customer_reply: true,
                    },
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };
                selectedTicketSummary = "Customer-facing answer is drafted and waiting for engineer confirmation.";
                selectedTicketNextAction = "Approve the prepared reply or ask the AI to revise it.";

                let capturedUrl = null;
                let capturedOptions = null;
                let refreshCalls = 0;
                fetchJson = async (url, options = undefined) => {
                  capturedUrl = url;
                  capturedOptions = options;
                  return {
                    ticket_id: "TK-DETAIL-APPROVE",
                    status: "resolved",
                    active_investigation: null,
                    closed_investigation: {
                      id: "INV-DETAIL-APPROVE",
                      state: "closed",
                      trigger_reason: "rag_insufficient_evidence",
                      trigger_source: "support_query",
                      draft_customer_reply: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                      final_confirmation_requested_at: null,
                      opened_at: "2026-03-24T08:01:00+00:00",
                      updated_at: "2026-03-24T09:11:00+00:00",
                      closed_at: "2026-03-24T09:11:00+00:00",
                      messages: [
                        {
                          id: "INV-DETAIL-APPROVE-m1",
                          role: "engineer_ai",
                          content: "I have enough information now. Please confirm this draft before I reply to the customer.",
                          created_at: "2026-03-24T09:05:00+00:00",
                        },
                        {
                          id: "INV-DETAIL-APPROVE-m2",
                          role: "engineer",
                          content: "Approved final reply.",
                          created_at: "2026-03-24T09:11:00+00:00",
                        },
                      ],
                    },
                    updated_at: "2026-03-24T09:11:00+00:00",
                  };
                };
                loadTickets = async () => {};
                refreshSelectedTicket = async () => {
                  refreshCalls += 1;
                };

                const approveButton = {
                  dataset: { detailAction: "approve-investigation" },
                  disabled: false,
                };
                const approveTarget = {
                  closest(selector) {
                    if (selector === "button[data-detail-action]") {
                      return approveButton;
                    }
                    return null;
                  },
                };

                await handleDetailClick({ target: approveTarget });
                if (capturedUrl !== "/api/engineer/tickets/TK-DETAIL-APPROVE/investigation/confirmation") {{
                  throw new Error("Approve should call the investigation confirmation endpoint.");
                }}
                const parsedBody = JSON.parse(capturedOptions.body);
                if (parsedBody.decision !== "approve") {{
                  throw new Error("Approve should post decision=approve.");
                }}
                const html = workspaceRegionEl.innerHTML;
                if (!html.includes("Approved final reply.")) {{
                  throw new Error("Approve flow should immediately render the closed investigation transcript.");
                }}
                if (html.includes("State:")) {{
                  throw new Error("Approve flow should no longer render the thread state line after closing the investigation.");
                }}
                if (html.includes("Approve for Guardrail")) {{
                  throw new Error("Approve flow should remove the approve button after the engineer ticket is closed.");
                }}
                if (html.includes('id="detail-investigation-input"')) {{
                  throw new Error("Approve flow should hide the composer after the investigation is closed.");
                }}
                if (html.includes("detail-investigation-closing-state")) {{
                  throw new Error("Approve flow should not keep the temporary closing-state marker once the ticket is closed.");
                }}
                if (!html.includes("Resolved")) {{
                  throw new Error("Approve flow should move the engineer case into the resolved state.");
                }}
                if (refreshCalls < 1) {{
                  throw new Error("Approve flow should still trigger a background refresh.");
                }}
              """
            )
        )

    def test_engineer_detail_stale_inflight_refresh_started_before_approve_cannot_restore_controls(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-APPROVE-STALE-INFLIGHT";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-APPROVE-STALE-INFLIGHT",
                  client_ticket_ref: {
                    ticket_id: "CLIENT-DETAIL-APPROVE-STALE-INFLIGHT",
                    subject: "Android 14 token renew regression",
                  },
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-APPROVE-STALE-INFLIGHT",
                    state: "awaiting_confirmation",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                    final_confirmation_requested_at: "2026-03-24T09:05:00+00:00",
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-APPROVE-STALE-INFLIGHT-m1",
                        role: "engineer_ai",
                        content: "I have enough information now. Please confirm this draft before I reply to the customer.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  engineer_agent_state: {
                    phase: "awaiting_confirmation",
                    ready_to_reply: true,
                    reply_readiness: {
                      has_conclusion: true,
                      has_proof: true,
                      has_solution_or_next_step: true,
                      conclusion_summary: "Android 14 with SDK 4.2.1 reproduces the token renew failure.",
                      proof_summary: "The engineer reproduced the issue on Android 14 with SDK 4.2.1 only.",
                      proof_anchors: ["Android 14", "SDK 4.2.1"],
                      solution_or_next_step: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                      blockers: [],
                      critique: "The current evidence supports the customer-safe SDK upgrade guidance.",
                      ready_for_customer_reply: true,
                    },
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };

                const staleAwaitingConfirmationTicket = {
                  ...selectedTicket,
                  updated_at: "2026-03-24T09:10:30+00:00",
                };

                const closedInvestigation = {
                  id: "INV-DETAIL-APPROVE-STALE-INFLIGHT",
                  state: "closed",
                  trigger_reason: "rag_insufficient_evidence",
                  trigger_source: "support_query",
                  draft_customer_reply: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                  final_confirmation_requested_at: null,
                  opened_at: "2026-03-24T08:01:00+00:00",
                  updated_at: "2026-03-24T09:11:00+00:00",
                  closed_at: "2026-03-24T09:11:00+00:00",
                  messages: [
                    {
                      id: "INV-DETAIL-APPROVE-STALE-INFLIGHT-m1",
                      role: "engineer_ai",
                      content: "I have enough information now. Please confirm this draft before I reply to the customer.",
                      created_at: "2026-03-24T09:05:00+00:00",
                    },
                    {
                      id: "INV-DETAIL-APPROVE-STALE-INFLIGHT-m2",
                      role: "engineer",
                      content: "Approved final reply.",
                      created_at: "2026-03-24T09:11:00+00:00",
                    },
                  ],
                };

                const closedTicketPayload = {
                  ...selectedTicket,
                  status: "resolved",
                  updated_at: "2026-03-24T09:11:00+00:00",
                  active_investigation: null,
                  investigation_history: [closedInvestigation],
                };

                let detailFetchCount = 0;
                let resolveStaleRefresh = null;
                fetchJson = async (url, options = undefined) => {
                  if (url === "/api/engineer/tickets/TK-DETAIL-APPROVE-STALE-INFLIGHT/investigation/confirmation") {
                    return {
                      ticket_id: "TK-DETAIL-APPROVE-STALE-INFLIGHT",
                      status: "resolved",
                      active_investigation: null,
                      closed_investigation: closedInvestigation,
                      updated_at: "2026-03-24T09:11:00+00:00",
                    };
                  }
                  if (url === "/api/engineer/tickets/TK-DETAIL-APPROVE-STALE-INFLIGHT?include_context=false") {
                    detailFetchCount += 1;
                    if (detailFetchCount === 1) {
                      return await new Promise((resolve) => {
                        resolveStaleRefresh = resolve;
                      });
                    }
                    return {
                      ticket: closedTicketPayload,
                    };
                  }
                  throw new Error(`Unexpected url: ${url}`);
                };
                loadTickets = async () => {};

                const staleRefreshPromise = refreshSelectedTicket({ silent: true, showLoading: false });

                const approveButton = {
                  dataset: { detailAction: "approve-investigation" },
                  disabled: false,
                };
                const approveTarget = {
                  closest(selector) {
                    if (selector === "button[data-detail-action]") {
                      return approveButton;
                    }
                    return null;
                  },
                };

                await handleDetailClick({ target: approveTarget });
                const approvedHtml = workspaceRegionEl.innerHTML;
                if (approvedHtml.includes("Approve for Guardrail")) {
                  throw new Error("Approve should hide the approve button before any stale refresh arrives.");
                }
                if (approvedHtml.includes('id="detail-investigation-input"')) {
                  throw new Error("Approve should hide the composer before any stale refresh arrives.");
                }
                if (!approvedHtml.includes("Approved final reply.")) {
                  throw new Error("Approve should render the closed investigation transcript before stale refresh completion.");
                }

                resolveStaleRefresh({
                  ticket: staleAwaitingConfirmationTicket,
                });
                await staleRefreshPromise;

                const htmlAfterStaleRefresh = workspaceRegionEl.innerHTML;
                if (htmlAfterStaleRefresh.includes("Approve for Guardrail")) {
                  throw new Error("A stale in-flight detail refresh that started before approval must not restore the approve button.");
                }
                if (htmlAfterStaleRefresh.includes('id="detail-investigation-input"')) {
                  throw new Error("A stale in-flight detail refresh that started before approval must not restore the composer.");
                }
                if (!htmlAfterStaleRefresh.includes("Approved final reply.")) {
                  throw new Error("A stale in-flight detail refresh that started before approval must not overwrite the closed transcript.");
                }
              """
            )
        )

    def test_engineer_socket_stale_refresh_after_approve_cannot_restore_controls(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                let lastSocket = null;
                WebSocket = function WebSocket() {
                  this.readyState = 1;
                  this.close = () => {};
                  this.send = () => {};
                  lastSocket = this;
                };

                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-APPROVE-STALE-SOCKET";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-APPROVE-STALE-SOCKET",
                  client_ticket_ref: {
                    ticket_id: "CLIENT-DETAIL-APPROVE-STALE-SOCKET",
                    subject: "Android 14 token renew regression",
                  },
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: {
                    id: "INV-DETAIL-APPROVE-STALE-SOCKET",
                    state: "awaiting_confirmation",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                    final_confirmation_requested_at: "2026-03-24T09:05:00+00:00",
                    opened_at: "2026-03-24T08:01:00+00:00",
                    updated_at: "2026-03-24T09:05:00+00:00",
                    messages: [
                      {
                        id: "INV-DETAIL-APPROVE-STALE-SOCKET-m1",
                        role: "engineer_ai",
                        content: "I have enough information now. Please confirm this draft before I reply to the customer.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
                  },
                  engineer_agent_state: {
                    phase: "awaiting_confirmation",
                    ready_to_reply: true,
                    reply_readiness: {
                      has_conclusion: true,
                      has_proof: true,
                      has_solution_or_next_step: true,
                      conclusion_summary: "Android 14 with SDK 4.2.1 reproduces the token renew failure.",
                      proof_summary: "The engineer reproduced the issue on Android 14 with SDK 4.2.1 only.",
                      proof_anchors: ["Android 14", "SDK 4.2.1"],
                      solution_or_next_step: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                      blockers: [],
                      critique: "The current evidence supports the customer-safe SDK upgrade guidance.",
                      ready_for_customer_reply: true,
                    },
                  },
                  investigation_history: [],
                  engineer_request_records: [],
                };

                const staleAwaitingConfirmationTicket = {
                  ...selectedTicket,
                  updated_at: "2026-03-24T09:10:45+00:00",
                };

                const closedInvestigation = {
                  id: "INV-DETAIL-APPROVE-STALE-SOCKET",
                  state: "closed",
                  trigger_reason: "rag_insufficient_evidence",
                  trigger_source: "support_query",
                  draft_customer_reply: "Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.",
                  final_confirmation_requested_at: null,
                  opened_at: "2026-03-24T08:01:00+00:00",
                  updated_at: "2026-03-24T09:11:00+00:00",
                  closed_at: "2026-03-24T09:11:00+00:00",
                  messages: [
                    {
                      id: "INV-DETAIL-APPROVE-STALE-SOCKET-m1",
                      role: "engineer_ai",
                      content: "I have enough information now. Please confirm this draft before I reply to the customer.",
                      created_at: "2026-03-24T09:05:00+00:00",
                    },
                    {
                      id: "INV-DETAIL-APPROVE-STALE-SOCKET-m2",
                      role: "engineer",
                      content: "Approved final reply.",
                      created_at: "2026-03-24T09:11:00+00:00",
                    },
                  ],
                };

                const closedTicketPayload = {
                  ...selectedTicket,
                  status: "resolved",
                  updated_at: "2026-03-24T09:11:00+00:00",
                  active_investigation: null,
                  investigation_history: [closedInvestigation],
                };

                let detailFetchCount = 0;
                fetchJson = async (url, options = undefined) => {
                  if (url === "/api/engineer/tickets/TK-DETAIL-APPROVE-STALE-SOCKET/investigation/confirmation") {
                    return {
                      ticket_id: "TK-DETAIL-APPROVE-STALE-SOCKET",
                      status: "resolved",
                      active_investigation: null,
                      closed_investigation: closedInvestigation,
                      updated_at: "2026-03-24T09:11:00+00:00",
                    };
                  }
                  if (url === "/api/engineer/tickets/TK-DETAIL-APPROVE-STALE-SOCKET?include_context=false") {
                    detailFetchCount += 1;
                    if (detailFetchCount === 1) {
                      return {
                        ticket: closedTicketPayload,
                      };
                    }
                    return {
                      ticket: staleAwaitingConfirmationTicket,
                    };
                  }
                  throw new Error(`Unexpected url: ${url}`);
                };

                let loadOptions = null;
                loadTickets = async (options = {}) => {
                  loadOptions = options;
                };

                setAuthenticated(true);
                setupWebSocket();

                const approveButton = {
                  dataset: { detailAction: "approve-investigation" },
                  disabled: false,
                };
                const approveTarget = {
                  closest(selector) {
                    if (selector === "button[data-detail-action]") {
                      return approveButton;
                    }
                    return null;
                  },
                };

                await handleDetailClick({ target: approveTarget });
                const approvedHtml = workspaceRegionEl.innerHTML;
                if (approvedHtml.includes("Approve for Guardrail")) {
                  throw new Error("Approve should hide the approve button before websocket refresh.");
                }
                if (!approvedHtml.includes("Approved final reply.")) {
                  throw new Error("Approve should render the closed investigation transcript before websocket refresh.");
                }

                await lastSocket.onmessage({
                  data: JSON.stringify({
                    event: "ticket_guidance_applied",
                    ticket_id: "TK-DETAIL-APPROVE-STALE-SOCKET",
                    client_ticket_id: "CLIENT-DETAIL-APPROVE-STALE-SOCKET",
                  }),
                });

                if (!loadOptions || loadOptions.refreshDetail !== false) {
                  throw new Error("Realtime refresh should still refresh the pool without forcing a separate detail refresh path.");
                }

                const htmlAfterSocketRefresh = workspaceRegionEl.innerHTML;
                if (htmlAfterSocketRefresh.includes("Approve for Guardrail")) {
                  throw new Error("A stale websocket-triggered detail refresh after approval must not restore the approve button.");
                }
                if (htmlAfterSocketRefresh.includes('id="detail-investigation-input"')) {
                  throw new Error("A stale websocket-triggered detail refresh after approval must not restore the composer.");
                }
                if (!htmlAfterSocketRefresh.includes("Approved final reply.")) {
                  throw new Error("A stale websocket-triggered detail refresh after approval must not overwrite the closed transcript.");
                }
              """
            )
        )

    def test_engineer_detail_status_actions_update_selected_pool_tab_and_back_to_pool_preserves_it(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-STATE-ACTION";
                selectedTicket = {
                  ticket_id: "TK-STATE-ACTION",
                  subject: "Ticket state action coverage",
                  requester: "user-state",
                  status: "communicating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T09:10:00+00:00",
                  messages: [],
                  active_investigation: null,
                  investigation_history: [],
                  engineer_request_records: [],
                };

                let navigatedPath = null;
                navigate = (path) => {
                  navigatedPath = path;
                  return true;
                };
                updateTicketStatus = async () => {};
                loadTickets = async () => {};
                refreshSelectedTicket = async () => {};

                selectedPoolStatus = "escalated";
                await handleDetailClick({
                  target: {
                    closest(selector) {
                      if (selector === "button[data-detail-action]") {
                        return { dataset: { detailAction: "start-investigation" }, disabled: false };
                      }
                      return null;
                    },
                  },
                });
                if (selectedPoolStatus !== "investigating") {{
                  throw new Error("Start investigation should retarget the engineer pool to investigating.");
                }}

                selectedTicket = { ...selectedTicket, status: "investigating", active_investigation: { id: "INV-1" } };
                await handleDetailClick({
                  target: {
                    closest(selector) {
                      if (selector === "button[data-detail-action]") {
                        return { dataset: { detailAction: "resume-communicating" }, disabled: false };
                      }
                      return null;
                    },
                  },
                });
                if (selectedPoolStatus !== "communicating") {{
                  throw new Error("Back to communicating should retarget the engineer pool to communicating.");
                }}

                selectedTicket = { ...selectedTicket, status: "communicating", active_investigation: null };
                await handleDetailClick({
                  target: {
                    closest(selector) {
                      if (selector === "button[data-detail-action]") {
                        return { dataset: { detailAction: "resolve-ticket" }, disabled: false };
                      }
                      return null;
                    },
                  },
                });
                if (selectedPoolStatus !== "resolved") {{
                  throw new Error("Resolve should retarget the engineer pool to resolved.");
                }}

                selectedTicket = { ...selectedTicket, status: "resolved" };
                await handleDetailClick({
                  target: {
                    closest(selector) {
                      if (selector === "button[data-detail-action]") {
                        return { dataset: { detailAction: "reopen-ticket" }, disabled: false };
                      }
                      return null;
                    },
                  },
                });
                if (selectedPoolStatus !== "communicating") {{
                  throw new Error("Reopen should retarget the engineer pool back to communicating.");
                }}

                await handleDetailClick({
                  target: {
                    closest(selector) {
                      if (selector === "button[data-detail-action]") {
                        return { dataset: { detailAction: "back-to-pool" }, disabled: false };
                      }
                      return null;
                    },
                  },
                });
                if (selectedPoolStatus !== "communicating") {{
                  throw new Error("Back to pool should preserve the currently selected engineer tab.");
                }}
                if (navigatedPath !== "/tickets") {{
                  throw new Error("Back to pool should still navigate to the pool route.");
                }}
              """
            )
        )

    def test_engineer_open_ticket_detail_redirects_back_to_investigating_pool_with_feedback(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                tickets = [
                  {
                    ticket_id: "TK-INVESTIGATING-RETURN",
                    subject: "investigating fallback target",
                    requester: "user-inv",
                    status: "investigating",
                    created_at: "2026-03-24T08:00:00+00:00",
                    updated_at: "2026-03-24T08:30:00+00:00",
                  },
                ];
                selectedPoolStatus = "resolved";
                window.location.hash = "#/tickets/TK-OPEN-DETAIL";

                fetchJson = async (url) => {
                  if (url === "/api/engineer/tickets/TK-OPEN-DETAIL?include_context=false") {
                    return {
                      ticket: {
                        ticket_id: "TK-OPEN-DETAIL",
                        subject: "client-only open ticket",
                        requester: "user-open",
                        status: "open",
                        created_at: "2026-03-24T08:00:00+00:00",
                        updated_at: "2026-03-24T08:30:00+00:00",
                        messages: [],
                        active_investigation: null,
                        investigation_history: [],
                      },
                    };
                  }
                  throw new Error(`Unexpected url: ${url}`);
                };

                await syncRouteToWorkspace({ silent: true, showLoading: true });

                if (routeState.view !== "pool") {{
                  throw new Error("Open ticket detail should redirect back to the engineer pool.");
                }}
                if (selectedPoolStatus !== "investigating") {{
                  throw new Error("Open ticket detail should return to the investigating tab.");
                }}
                if (!workspaceRegionEl.innerHTML.includes("workspace-feedback")) {{
                  throw new Error("Redirected open detail should render workspace feedback.");
                }}
                if (!workspaceRegionEl.innerHTML.includes("This ticket is only visible in the client workspace.")) {{
                  throw new Error("Redirected open detail should explain that open tickets stay on the client side.");
                }}
              """
            )
        )

    def test_engineer_ticket_pool_row_click_and_keyboard_open_detail(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                let opened = [];
                openTicketDetail = (ticketId) => {
                  opened.push(ticketId);
                };

                const row = {
                  dataset: { ticketId: "TK-CLICK-001" },
                };

                const rowTarget = {
                  closest(selector) {
                    if (selector === "[data-ticket-row]") {
                      return row;
                    }
                    return null;
                  },
                };

                handleTableClick({ target: rowTarget });
                if (opened.length !== 1 || opened[0] !== "TK-CLICK-001") {
                  throw new Error("Clicking a ticket row should open the matching detail workspace.");
                }

                let enterPrevented = false;
                handleTableKeydown({
                  key: "Enter",
                  target: rowTarget,
                  preventDefault() {
                    enterPrevented = true;
                  },
                });
                if (!enterPrevented || opened.length !== 2 || opened[1] !== "TK-CLICK-001") {
                  throw new Error("Pressing Enter on a focused row should open the matching detail workspace.");
                }

                let spacePrevented = false;
                handleTableKeydown({
                  key: " ",
                  target: rowTarget,
                  preventDefault() {
                    spacePrevented = true;
                  },
                });
                if (!spacePrevented || opened.length !== 3 || opened[2] !== "TK-CLICK-001") {
                  throw new Error("Pressing Space on a focused row should open the matching detail workspace.");
                }

                const nestedButtonTarget = {
                  closest(selector) {
                    if (selector === "[data-ticket-row]") {
                      return row;
                    }
                    if (selector.includes("button")) {
                      return {};
                    }
                    return null;
                  },
                };

                handleTableClick({ target: nestedButtonTarget });
                if (opened.length !== 3) {
                  throw new Error("Row click handling should ignore nested interactive controls.");
                }
                """
            )
        )

    def _engineer_multi_agent_ticket_fixture(self) -> str:
        return textwrap.dedent(
            """
            tickets = [
              {
                ticket_id: "TK-MA-1",
                engineer_case_id: "TK-MA-1",
                title: "multi-agent workspace ticket",
                subject: "multi-agent workspace ticket",
                requester: "user-ma",
                customer_id: "user-ma",
                status: "investigating",
                client_ticket_ref: {
                  ticket_id: "TK-MA",
                  subject: "parent client ticket",
                },
                created_at: "2026-06-01T08:00:00+00:00",
                updated_at: "2026-06-01T08:30:00+00:00",
                active_investigation: {
                  id: "TK-MA-1",
                  state: "active",
                  draft_customer_reply: "",
                  messages: [
                    {
                      id: "TK-MA-1-m1",
                      role: "engineer_ai",
                      content: "Reviewing the multi-agent run for this ticket.",
                      created_at: "2026-06-01T08:05:00+00:00",
                    },
                  ],
                },
                engineer_agent_state: {
                  phase: "investigating",
                  issue_understanding: "Token renew callback fails on Android 14.",
                  knowledge_summary: "Regression reproduces on Android 14 with SDK 4.2.1.",
                  why_not_solved: "Platform-scoped evidence was not confirmed yet.",
                  known_facts: ["Customer reported token renew failures on Android 14."],
                  missing_information: ["Confirm the customer can upgrade to SDK 4.2.2."],
                  next_request_for_engineer: "Confirm the upgrade path before approving.",
                  resolution_hypothesis: "Upgrading to SDK 4.2.2 should resolve the regression.",
                  ready_to_reply: false,
                  reply_readiness: {
                    has_conclusion: true,
                    has_proof: false,
                    has_solution_or_next_step: false,
                    conclusion_summary: "Android 14 with SDK 4.2.1 reproduces the token renew failure.",
                    proof_summary: "",
                    solution_or_next_step: "",
                    blockers: [],
                    critique: "",
                    ready_for_customer_reply: false,
                  },
                  active_plan: {
                    plan_id: "plan_TK-MA-1_r1",
                    plan_version: "engineer-plan-v1",
                    plan_agent_version: "engineer-plan-agent-v1",
                    created_by: "plan_agent",
                    created_at: "2026-06-01T08:06:00+00:00",
                    memory_context: {
                      mode: "mem0",
                      memory_refs: [{ memory_record_id: "mem-1", summary: "Prior Android token issue." }],
                      fallback_reason: null,
                    },
                    skill_context: {
                      mode: "installed",
                      available_skills: ["context_review", "rag_lookup", "synthesis"],
                      selected_skills: ["context_review", "rag_lookup", "synthesis"],
                      fallback_reason: null,
                    },
                    objective: "Investigate engineer ticket: multi-agent workspace ticket",
                    hypotheses: [
                      { hypothesis_id: "h1", summary: "SDK 4.2.1 regression on Android 14." },
                    ],
                    tasks: [
                      {
                        task_id: "task_context_review",
                        title: "Review context",
                        description: "Run the context_review skill.",
                        skill: "context_review",
                        depends_on: [],
                        can_parallelize: false,
                        expected_output: "Context summary.",
                        blockers: [],
                        status: "planned",
                      },
                      {
                        task_id: "task_rag_lookup",
                        title: "Look up answers",
                        description: "Run the rag_lookup skill.",
                        skill: "rag_lookup",
                        depends_on: ["task_context_review"],
                        can_parallelize: true,
                        expected_output: "Candidate answers.",
                        blockers: [],
                        status: "planned",
                      },
                    ],
                    dependencies: [
                      { dependency_id: "dep-1", description: "rag_lookup depends on context_review." },
                    ],
                    scheduler_hints: { note: "context_review first, then rag_lookup in parallel." },
                    redaction_boundary: {},
                  },
                  active_execution: {
                    execution_id: "exec_plan_TK-MA-1_r1_r1",
                    execution_version: "engineer-execution-v1",
                    execute_agent_version: "engineer-execute-agent-v1",
                    created_by: "execute_agent",
                    created_at: "2026-06-01T08:07:00+00:00",
                    plan_id: "plan_TK-MA-1_r1",
                    plan_version: "engineer-plan-v1",
                    status: "completed",
                    scheduler: {
                      mode: "deterministic_allowlist",
                      parallel_groups: [],
                      serial_steps: [],
                      execution_order: [
                        { stage: 1, task_ids: ["task_context_review"] },
                        { stage: 2, task_ids: ["task_rag_lookup"] },
                      ],
                    },
                    task_results: [
                      {
                        task_id: "task_context_review",
                        skill: "context_review",
                        subagent: "execute_subagent_context_review",
                        status: "completed",
                        summary: "Confirmed Android 14 reproduction context.",
                        evidence_refs: [],
                        missing_information: [],
                        started_at: "2026-06-01T08:07:00+00:00",
                        completed_at: "2026-06-01T08:07:30+00:00",
                      },
                      {
                        task_id: "task_rag_lookup",
                        skill: "rag_lookup",
                        subagent: "execute_subagent_rag_lookup",
                        status: "completed",
                        summary: "Found candidate answer pointing to SDK 4.2.2 upgrade.",
                        evidence_refs: [],
                        missing_information: ["Confirm the customer can upgrade to SDK 4.2.2."],
                        started_at: "2026-06-01T08:07:30+00:00",
                        completed_at: "2026-06-01T08:08:00+00:00",
                      },
                    ],
                    evidence_packet: {},
                    blockers: [],
                  },
                  active_review: {
                    review_id: "review_exec_plan_TK-MA-1_r1_r1_r1",
                    review_version: "engineer-review-v1",
                    review_agent_version: "engineer-review-agent-v1",
                    created_by: "review_agent",
                    created_at: "2026-06-01T08:08:30+00:00",
                    plan_id: "plan_TK-MA-1_r1",
                    execution_id: "exec_plan_TK-MA-1_r1_r1",
                    review_decision: "replan_required",
                    replan_count: 0,
                    problem_statement: "Evidence is insufficient to confirm the SDK upgrade path.",
                    decision_rationale: "Evidence sufficiency: insufficient. Gaps: proof missing. Missing information: Confirm the customer can upgrade to SDK 4.2.2.",
                    evidence_gaps: ["proof missing"],
                    missing_information: ["Confirm the customer can upgrade to SDK 4.2.2."],
                    recommended_action: "Replan to gather SDK upgrade confirmation.",
                    max_replan_exceeded: false,
                    max_replan_count: 2,
                    blockers: [],
                  },
                },
                messages: [
                  {
                    id: "TK-MA-c1",
                    role: "customer",
                    content: "Token renew callback keeps failing on Android 14.",
                    created_at: "2026-06-01T08:00:00+00:00",
                  },
                ],
              },
            ];
            """
        )

    def test_engineer_detail_default_view_omits_multi_agent_panel(self) -> None:
        self.run_engineer_app_script(
            self._engineer_multi_agent_ticket_fixture()
            + textwrap.dedent(
                """
                selectedPoolStatus = "investigating";
                selectedTicketId = "TK-MA-1";
                selectedTicket = tickets[0];

                const html = renderTicketDetailView();

                // Default guardrail-only view must not surface multi-agent outputs.
                if (html.includes("detail-multi-agent-panel")) {
                  throw new Error("Default detail view should not render the multi-agent workspace panel.");
                }
                if (html.includes("Multi-Agent Run")) {
                  throw new Error("Default detail view should not surface the Multi-Agent Run heading.");
                }
                if (html.includes("Plan Agent")) {
                  throw new Error("Default detail view should not surface the Plan Agent stage.");
                }
                if (html.includes("Execute Agent")) {
                  throw new Error("Default detail view should not surface the Execute Agent stage.");
                }
                if (html.includes("Review Agent")) {
                  throw new Error("Default detail view should not surface the Review Agent stage.");
                }

                // The guardrail-only default path still shows Conclusion / Proof / Next step.
                if (!html.includes("Readiness Review")) {
                  throw new Error("Default detail view should still render the Readiness Review panel.");
                }
                if (!html.includes(">Conclusion<")) {
                  throw new Error("Default detail view should keep the Conclusion field.");
                }
                if (!html.includes(">Proof<")) {
                  throw new Error("Default detail view should keep the Proof field.");
                }
                if (!html.includes(">Next step<")) {
                  throw new Error("Default detail view should keep the Next step field.");
                }
                """
            )
        )

    def test_engineer_detail_investigating_badge_is_multi_agent_dblclick_entry(self) -> None:
        self.run_engineer_app_script(
            self._engineer_multi_agent_ticket_fixture()
            + textwrap.dedent(
                """
                selectedPoolStatus = "investigating";
                selectedTicketId = "TK-MA-1";
                selectedTicket = tickets[0];

                const investigatingHtml = renderTicketDetailView();
                const primaryStart = investigatingHtml.indexOf('class="workspace-header-line workspace-header-line-primary"');
                const primaryLine = investigatingHtml.slice(primaryStart, investigatingHtml.indexOf('class="workspace-header-line workspace-header-line-secondary"'));

                if (!primaryLine.includes('data-detail-action="toggle-multi-agent-workspace"')) {
                  throw new Error("Investigating status badge should carry the multi-agent workspace toggle action.");
                }
                if (!primaryLine.includes("status-badge status-badge-compact")) {
                  throw new Error("Investigating badge should keep the status-badge status-badge-compact styling.");
                }
                if (!primaryLine.includes("status-badge-investigating-toggle")) {
                  throw new Error("Investigating badge should be marked as the multi-agent toggle entry.");
                }
                if (!primaryLine.includes('aria-label="Double-click to toggle multi-agent workspace view"')) {
                  throw new Error("Investigating badge should advertise the double-click affordance.");
                }
                if (!primaryLine.includes('data-multi-agent-toggle="TK-MA-1"')) {
                  throw new Error("Investigating badge should carry the scoped multi-agent toggle target.");
                }

                // A single click on the toggle badge must stay a no-op.
                let clickSideEffect = false;
                const badgeTarget = {
                  closest(selector) {
                    if (selector === 'button[data-detail-action="toggle-multi-agent-workspace"]') {
                      return { dataset: { detailAction: "toggle-multi-agent-workspace" } };
                    }
                    return null;
                  },
                };
                window.alert = () => { clickSideEffect = true; };
                handleDetailClick({ target: badgeTarget }).catch(() => { clickSideEffect = true; });
                if (clickSideEffect) {
                  throw new Error("Single click on the multi-agent toggle badge must not perform any action.");
                }

                // Non-investigating tickets must not expose the toggle entry.
                tickets[0].status = "resolved";
                selectedTicket = tickets[0];
                const resolvedHtml = renderTicketDetailView();
                if (resolvedHtml.includes('data-detail-action="toggle-multi-agent-workspace"')) {
                  throw new Error("Non-investigating tickets should not render the multi-agent toggle badge.");
                }
                if (!resolvedHtml.includes("status-badge status-badge-compact")) {
                  throw new Error("Non-investigating tickets should still render the compact status badge.");
                }
                """
            )
        )

    def test_engineer_detail_multi_agent_workspace_renders_plan_execute_review(self) -> None:
        self.run_engineer_app_script(
            self._engineer_multi_agent_ticket_fixture()
            + textwrap.dedent(
                """
                selectedPoolStatus = "investigating";
                selectedTicketId = "TK-MA-1";
                selectedTicket = tickets[0];

                // Toggle the multi-agent workspace for this ticket within the session.
                if (!toggleMultiAgentWorkspaceForTicket(selectedTicketId)) {
                  throw new Error("Toggling the multi-agent workspace should turn the view on for the current ticket.");
                }
                if (!isMultiAgentWorkspaceActiveForTicket(selectedTicketId)) {
                  throw new Error("Multi-agent workspace should be active for the current ticket after toggling.");
                }

                const html = renderTicketDetailView();

                if (!html.includes("detail-multi-agent-panel")) {
                  throw new Error("Multi-agent workspace view should render the dedicated insight panel.");
                }
                if (!html.includes("Multi-Agent Run")) {
                  throw new Error("Multi-agent workspace view should surface the Multi-Agent Run heading.");
                }

                // Plan Agent stage
                if (!html.includes("Plan Agent")) {
                  throw new Error("Multi-agent workspace should render the Plan Agent stage.");
                }
                if (!html.includes("plan_TK-MA-1_r1")) {
                  throw new Error("Multi-agent workspace should render the active plan_id.");
                }
                if (!html.includes("mem0 / installed")) {
                  throw new Error("Multi-agent workspace should render the fallback/memory/skill mode summary.");
                }
                if (!html.includes("task_context_review")) {
                  throw new Error("Multi-agent workspace should render the planned task list.");
                }

                // Execute Agent stage
                if (!html.includes("Execute Agent")) {
                  throw new Error("Multi-agent workspace should render the Execute Agent stage.");
                }
                if (!html.includes("exec_plan_TK-MA-1_r1_r1")) {
                  throw new Error("Multi-agent workspace should render the active execution_id.");
                }
                if (!html.includes("Stage 1: task_context_review")) {
                  throw new Error("Multi-agent workspace should render the scheduler execution_order.");
                }
                if (!html.includes("Found candidate answer pointing to SDK 4.2.2 upgrade.")) {
                  throw new Error("Multi-agent workspace should render the task result summary.");
                }
                if (!html.includes("missing: Confirm the customer can upgrade to SDK 4.2.2.")) {
                  throw new Error("Multi-agent workspace should render the task result missing_information.");
                }

                // Review Agent stage
                if (!html.includes("Review Agent")) {
                  throw new Error("Multi-agent workspace should render the Review Agent stage.");
                }
                if (!html.includes("replan_required")) {
                  throw new Error("Multi-agent workspace should render the review_decision.");
                }
                if (!html.includes("Evidence is insufficient to confirm the SDK upgrade path.")) {
                  throw new Error("Multi-agent workspace should render the review problem_statement.");
                }
                if (!html.includes("Replan to gather SDK upgrade confirmation.")) {
                  throw new Error("Multi-agent workspace should render the recommended_action.");
                }

                // Toggling off restores the guardrail-only default view.
                if (toggleMultiAgentWorkspaceForTicket(selectedTicketId)) {
                  throw new Error("Toggling the multi-agent workspace a second time should turn the view off.");
                }
                const offHtml = renderTicketDetailView();
                if (offHtml.includes("detail-multi-agent-panel")) {
                  throw new Error("Turning the multi-agent workspace off should remove the insight panel.");
                }
                """
            )
        )

    def test_engineer_detail_multi_agent_workspace_empty_state_keeps_readiness_review(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                tickets = [
                  {
                    ticket_id: "TK-MA-EMPTY-1",
                    engineer_case_id: "TK-MA-EMPTY-1",
                    title: "empty multi-agent workspace ticket",
                    subject: "empty multi-agent workspace ticket",
                    requester: "user-ma-empty",
                    customer_id: "user-ma-empty",
                    status: "investigating",
                    client_ticket_ref: {
                      ticket_id: "TK-MA-EMPTY",
                      subject: "parent client ticket",
                    },
                    created_at: "2026-06-02T08:00:00+00:00",
                    updated_at: "2026-06-02T08:30:00+00:00",
                    active_investigation: {
                      id: "TK-MA-EMPTY-1",
                      state: "active",
                      draft_customer_reply: "",
                      messages: [
                        {
                          id: "TK-MA-EMPTY-1-m1",
                          role: "engineer_ai",
                          content: "No multi-agent run captured yet.",
                          created_at: "2026-06-02T08:05:00+00:00",
                        },
                      ],
                    },
                    engineer_agent_state: {
                      phase: "investigating",
                      issue_understanding: "Issue understanding not captured yet.",
                      knowledge_summary: "",
                      why_not_solved: "",
                      known_facts: [],
                      missing_information: [],
                      next_request_for_engineer: "",
                      resolution_hypothesis: "",
                      ready_to_reply: false,
                      reply_readiness: {
                        has_conclusion: false,
                        has_proof: false,
                        has_solution_or_next_step: false,
                        conclusion_summary: "",
                        proof_summary: "",
                        solution_or_next_step: "",
                        blockers: [],
                        critique: "",
                        ready_for_customer_reply: false,
                      },
                    },
                    messages: [
                      {
                        id: "TK-MA-EMPTY-c1",
                        role: "customer",
                        content: "Just checking in on the issue.",
                        created_at: "2026-06-02T08:00:00+00:00",
                      },
                    ],
                  },
                ];

                selectedPoolStatus = "investigating";
                selectedTicketId = "TK-MA-EMPTY-1";
                selectedTicket = tickets[0];

                toggleMultiAgentWorkspaceForTicket(selectedTicketId);
                const html = renderTicketDetailView();

                if (!html.includes("detail-multi-agent-panel")) {
                  throw new Error("Multi-agent workspace should still render its panel when agent state is missing.");
                }
                if (!html.includes("No multi-agent run captured for this ticket yet.")) {
                  throw new Error("Multi-agent workspace should show the empty state when no agent state is present.");
                }
                if (html.includes("Plan Agent") || html.includes("Execute Agent") || html.includes("Review Agent")) {
                  throw new Error("Empty multi-agent workspace should not render any agent stage.");
                }

                // Readiness Review must remain intact alongside the empty multi-agent panel.
                if (!html.includes("Readiness Review")) {
                  throw new Error("Empty multi-agent workspace should not break the Readiness Review panel.");
                }
                if (!html.includes(">Conclusion<") || !html.includes(">Proof<") || !html.includes(">Next step<")) {
                  throw new Error("Empty multi-agent workspace should keep the Conclusion / Proof / Next step fields.");
                }

                // Switching tickets resets the session-only toggle back to the default view.
                selectedTicketId = null;
                selectedTicket = null;
                resetDetailWorkspaceState();
                if (multiAgentWorkspaceTicketId !== null) {
                  throw new Error("Resetting the detail workspace should clear the multi-agent workspace toggle.");
                }
                """
            )
        )


if __name__ == "__main__":
    unittest.main()
