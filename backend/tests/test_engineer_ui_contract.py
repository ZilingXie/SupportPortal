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
        self.assertIn("./styles.css?v=20260404-icon-font-guard-1", html)
        self.assertIn('./app.js?v=20260402-engineer-case-split-1', html)
        self.assertIn("function parseRoute()", app_source)
        self.assertIn('path.startsWith("/tickets/")', app_source)
        self.assertIn("function renderTicketPoolView()", app_source)
        self.assertIn("function renderTicketDetailView()", app_source)
        self.assertIn("Next Action Needed", app_source)
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
        self.assertIn(".status-surface-escalated", css)
        self.assertIn(".status-surface-investigating", css)
        self.assertIn(".status-surface-resolved", css)
        self.assertIn('font-family: "Material Symbols Outlined";', css)
        self.assertIn("html.material-symbols-pending .material-symbols-outlined", css)
        self.assertIn("visibility: hidden;", css)
        self.assertIn(".ticket-workspace", css)
        self.assertNotIn("priorityLabel(", app_source)
        self.assertNotIn(".priority-badge", css)
        self.assertNotIn('FILTER_KEYS = ["priority"]', app_source)

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
                if (!detailHtml.includes("Ticket #TK-040-1")) {{
                  throw new Error("Engineer detail should label the engineer case id in the header.");
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
            msg="Compact thread panel should not shrink the entire Engineer Ticket card away from the AI Summary bottom edge.",
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
        self.assertIn("overflow-y: auto;", list_block)
        self.assertIn("justify-content: flex-start;", list_block)
        self.assertIn("max-height: none;", list_block)
        self.assertIn(".message-list-compact-thread .message-item", css)

    def test_engineer_detail_prioritizes_internal_investigation_workspace_and_confirmation(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                selectedTicketId = "TK-DETAIL-INV";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-INV",
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
                    {
                      role: "assistant",
                      content: "I've opened an engineer ticket for this issue and we're investigating further. I'll reply here as soon as the engineer review is confirmed.",
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
                        content: "I have enough information now. Please confirm this draft before I reply to the customer.",
                        created_at: "2026-03-24T09:05:00+00:00",
                      },
                    ],
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
                selectedTicketSummary = "Customer-facing answer is drafted and waiting for engineer confirmation.";
                selectedTicketNextAction = "Approve the prepared reply or ask the AI to revise it.";

                const html = renderTicketDetailView();
                const headerTopStart = html.indexOf('class="workspace-header-top"');
                const headerMainStart = html.indexOf('class="workspace-header-main"');
                if (headerTopStart === -1 || headerMainStart === -1 || headerTopStart > headerMainStart) {{
                  throw new Error("Detail header should render a toolbar row ahead of the title block.");
                }}
                const headerTopMarkup = html.slice(headerTopStart, headerMainStart);
                if (!headerTopMarkup.includes("Back to Pool")) {{
                  throw new Error("Toolbar row should keep the back action.");
                }}
                if (!headerTopMarkup.includes("Sync Ticket")) {{
                  throw new Error("Toolbar row should include the sync action.");
                }}
                if (!headerTopMarkup.includes("workspace-eyebrow")) {{
                  throw new Error("Toolbar row should keep the ticket id label.");
                }}
                if (!headerTopMarkup.includes("status-badge")) {{
                  throw new Error("Toolbar row should carry the status badge after the header compaction.");
                }}
                if (headerTopMarkup.includes("priority-badge")) {{
                  throw new Error("Toolbar row should no longer render a priority badge.");
                }}
                if (headerTopMarkup.includes("mode-pill")) {{
                  throw new Error("Toolbar row should not render the removed mode pill.");
                }}
                if (headerTopMarkup.includes("workspace-ticket-title")) {{
                  throw new Error("Ticket title should stay below the compact toolbar row.");
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
                if (!html.includes("Approve Reply")) {{
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
                const customerTimelineSection = html.slice(customerIndex);
                if (customerTimelineSection.includes('message-list message-list-compact-thread')) {{
                  throw new Error("Customer timeline should not inherit the compact engineer thread layout.");
                }}
                const summaryIndex = html.indexOf("AI Summary");
                if (summaryIndex === -1) {{
                  throw new Error("Detail workspace should still render the AI Summary card.");
                }}
                const summarySection = html.slice(summaryIndex);
                if (summarySection.includes("Back to Communicating")) {{
                  throw new Error("AI Summary should no longer render the resume communicating button.");
                }}
                if (summarySection.includes("Resolve Ticket")) {{
                  throw new Error("AI Summary should no longer render the resolve button.");
                }}
                const decisionIndex = engineerThreadSection.indexOf("I have enough information now. Please confirm this draft before I reply to the customer.");
                const inlineActionsIndex = engineerThreadSection.indexOf("detail-investigation-inline-actions");
                if (decisionIndex === -1 || inlineActionsIndex === -1 || inlineActionsIndex < decisionIndex) {{
                  throw new Error("Inline confirmation actions should appear after the final Engineer AI message.");
                }}
                const draftIndex = engineerThreadSection.indexOf("detail-investigation-draft");
                const approveIndex = engineerThreadSection.indexOf("Approve Reply");
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

    def test_engineer_detail_derives_approval_block_from_agent_state_and_shows_placeholder_when_draft_is_missing(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                selectedTicketId = "TK-DETAIL-AGENT-PHASE";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-AGENT-PHASE",
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
                    id: "INV-DETAIL-AGENT-PHASE",
                    state: "active",
                    trigger_reason: "rag_insufficient_evidence",
                    trigger_source: "support_query",
                    draft_customer_reply: "",
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
                    last_refreshed_at: "2026-03-24T09:05:00+00:00",
                  },
                };
                selectedTicketSummary = "Customer-facing answer is ready for approval.";
                selectedTicketNextAction = "Approve the prepared reply.";

                const html = renderTicketDetailView();
                if (!html.includes("Approve Reply")) {{
                  throw new Error("Engineer thread should derive the approval block from engineer_agent_state when the investigation state has not caught up yet.");
                }}
                if (html.includes("Ask AI to Revise")) {{
                  throw new Error("Approval-derived states should not render a separate revise button.");
                }}
                if (!html.includes("Draft reply is not ready yet.")) {{
                  throw new Error("Engineer thread should render a visible placeholder instead of leaving an empty draft area.");
                }}
                if (!html.includes('id="detail-investigation-input"')) {{
                  throw new Error("Approval-derived states should keep the composer visible for direct revision notes.");
                }}
              """
            )
        )

    def test_engineer_local_summary_fallback_prefers_agent_brief_when_available(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                const fallback = buildLocalSummaryFallback({
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

                if (!fallback.summary.includes("Current understanding: Android 14 token renewal still fails after the customer upgraded the SDK.")) {
                  throw new Error("Local summary fallback should surface the agent issue understanding.");
                }
                if (!fallback.summary.includes("Goal: Confirm the exact SDK version and whether Android 14 is the only affected platform.")) {
                  throw new Error("Local summary fallback should surface the current agent goal.");
                }
                if (!fallback.summary.includes("Why client AI could not solve it: The current evidence does not prove the exact SDK regression boundary.")) {
                  throw new Error("Local summary fallback should explain why the client AI is blocked.");
                }
                if (fallback.nextAction !== "Please confirm the exact SDK version and whether Android 14 is the only affected platform.") {
                  throw new Error("Local summary fallback should use the agent next request as the next action.");
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
                if (!html.includes("State: Closed")) {{
                  throw new Error("Closed investigations should label the thread as closed.");
                }}
                if (html.includes('id="detail-investigation-input"')) {{
                  throw new Error("Closed investigations should not render an active composer.");
                }}
                if (html.includes("Approve Reply") || html.includes("Ask AI to Revise")) {{
                  throw new Error("Closed investigations should not keep rendering confirmation actions.");
                }}
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
                  investigation_history: [],
                  engineer_request_records: [],
                };
                selectedTicketSummary = "Customer-facing answer is drafted and waiting for engineer confirmation.";
                selectedTicketNextAction = "Approve the prepared reply or ask the AI to revise it.";

                const approvalHtml = renderTicketDetailView();
                if (!approvalHtml.includes('id="detail-investigation-input"')) {{
                  throw new Error("Approval state should keep the main investigation composer visible.");
                }}
                if (!approvalHtml.includes("If the draft needs changes, tell Engineer AI what to revise before replying to the customer")) {{
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
                  investigation_history: [],
                  engineer_request_records: [],
                };
                selectedTicketSummary = "Customer-facing answer is drafted and waiting for engineer confirmation.";
                selectedTicketNextAction = "Approve the prepared reply or ask the AI to revise it.";

                let capturedUrl = null;
                let capturedOptions = null;
                fetchJson = async (url, options = undefined) => {
                  capturedUrl = url;
                  capturedOptions = options;
                  return {
                    ticket_id: "TK-DETAIL-APPROVE",
                    status: "communicating",
                    active_investigation: null,
                    updated_at: "2026-03-24T09:11:00+00:00",
                  };
                };
                loadTickets = async () => {};
                refreshSelectedTicket = async () => {
                  selectedTicket = {
                    ...selectedTicket,
                    status: "communicating",
                    active_investigation: null,
                    investigation_history: [
                      {
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
                    ],
                  };
                  renderTicketDetail();
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
                  throw new Error("Approve flow should refresh into the closed investigation transcript.");
                }}
                if (!html.includes("State: Closed")) {{
                  throw new Error("Approve flow should render the investigation as closed after refresh.");
                }}
                if (html.includes('id="detail-investigation-input"')) {{
                  throw new Error("Approve flow should hide the composer after the investigation is closed.");
                }}
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
                  if (url === "/api/engineer/tickets/TK-OPEN-DETAIL") {
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


if __name__ == "__main__":
    unittest.main()
