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

        self.assertIn("Concierge AI", html)
        self.assertIn("Manrope", html)
        self.assertIn("./styles.css?v=20260331-engineer-single-flow-1", html)
        self.assertIn('./app.js?v=20260331-engineer-single-flow-1', html)
        self.assertIn("function parseRoute()", app_source)
        self.assertIn('path.startsWith("/tickets/")', app_source)
        self.assertIn("function renderTicketPoolView()", app_source)
        self.assertIn("function renderTicketDetailView()", app_source)
        self.assertIn("Next Action Needed", app_source)
        self.assertIn("Investigation Command", html)
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
        self.assertIn(".ticket-workspace", css)

    def test_engineer_ticket_pool_defaults_to_list_rows_and_prioritizes_investigating(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                tickets = [
                  {
                    ticket_id: "TK-OPEN-URGENT",
                    subject: "communicating urgent ticket",
                    requester: "user-1",
                    priority: "urgent",
                    status: "communicating",
                    created_at: "2026-03-24T09:00:00+00:00",
                    updated_at: "2026-03-24T10:00:00+00:00",
                  },
                  {
                    ticket_id: "TK-INVESTIGATING-LOW",
                    subject: "investigating ticket",
                    requester: "user-2",
                    priority: "low",
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
                ];

                filterValues.priority = "all";
                filterValues.status = "all";

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

                const waitingIndex = html.indexOf("TK-INVESTIGATING-LOW");
                const openIndex = html.indexOf("TK-OPEN-URGENT");
                if (waitingIndex === -1 || openIndex === -1) {{
                  throw new Error("Expected both sample tickets in rendered HTML.");
                }}
                if (waitingIndex > openIndex) {{
                  throw new Error("Investigating tickets should render ahead of normal open tickets.");
                }}

                const waitingRowStart = html.indexOf('data-ticket-id="TK-INVESTIGATING-LOW"');
                const waitingRowEnd = html.indexOf("</article>", waitingRowStart);
                const waitingRowMarkup = html.slice(waitingRowStart, waitingRowEnd);
                const badgeIndex = waitingRowMarkup.indexOf("status-badge");
                const secondLineIndex = waitingRowMarkup.indexOf("ticket-row-secondary");
                if (badgeIndex === -1 || secondLineIndex === -1 || badgeIndex > secondLineIndex) {{
                  throw new Error("Status badges should stay in the first row alongside the title.");
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
                filterValues.priority = "all";
                filterValues.status = "all";

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

                tickets = [
                  {
                    ticket_id: "TK-GRID-001",
                    subject: "investigating grid ticket",
                    requester: "user-grid",
                    priority: "high",
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
                    priority: "normal",
                    status: "escalated",
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
                if (!html.includes("Please confirm whether the issue only reproduces on Android 14.")) {{
                  throw new Error("Grid cards should include the latest investigation preview text.");
                }}
                const gridIndex = html.indexOf("TK-GRID-001");
                const openIndex = html.indexOf("TK-GRID-002");
                if (gridIndex === -1 || openIndex === -1 || gridIndex > openIndex) {{
                  throw new Error("Grid mode should preserve the same sorted order as list mode.");
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

    def test_engineer_detail_prioritizes_internal_investigation_workspace_and_confirmation(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                selectedTicketId = "TK-DETAIL-INV";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-INV",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  priority: "high",
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
                      content: "We are investigating this further. Please wait while we continue checking.",
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
                if (!headerTopMarkup.includes("priority-badge") || !headerTopMarkup.includes("status-badge")) {{
                  throw new Error("Toolbar row should carry the priority and status badges after the header compaction.");
                }}
                if (headerTopMarkup.includes("mode-pill")) {{
                  throw new Error("Toolbar row should not render the removed mode pill.");
                }}
                if (headerTopMarkup.includes("workspace-ticket-title")) {{
                  throw new Error("Ticket title should stay below the compact toolbar row.");
                }}
                if (!html.includes("Internal Investigation Thread")) {{
                  throw new Error("Detail workspace should foreground the internal investigation thread.");
                }}
                if (!html.includes("Customer Timeline")) {{
                  throw new Error("Detail workspace should still render the customer timeline in the supporting column.");
                }}
                if (!html.includes("Approve Reply")) {{
                  throw new Error("Awaiting-confirmation investigations should expose the approve action.");
                }}
                if (!html.includes("Ask AI to Revise")) {{
                  throw new Error("Awaiting-confirmation investigations should expose the revise action.");
                }}
                if (!html.includes("detail-investigation-inline-actions")) {{
                  throw new Error("Confirmation actions should render inline inside the investigation chat thread.");
                }}
                if (!html.includes("Back to Communicating")) {{
                  throw new Error("Investigating tickets should surface the resume communicating action.");
                }}
                if (!html.includes("Resolve Ticket")) {{
                  throw new Error("Investigating tickets should surface the resolve action.");
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
                const internalIndex = html.indexOf("Internal Investigation Thread");
                const customerIndex = html.indexOf("Customer Timeline");
                if (internalIndex === -1 || customerIndex === -1 || internalIndex > customerIndex) {{
                  throw new Error("Internal investigation thread should render ahead of the customer timeline.");
                }}
                const decisionIndex = html.indexOf("I have enough information now. Please confirm this draft before I reply to the customer.");
                const inlineActionsIndex = html.indexOf("detail-investigation-inline-actions");
                if (decisionIndex === -1 || inlineActionsIndex === -1 || inlineActionsIndex < decisionIndex) {{
                  throw new Error("Inline confirmation actions should appear after the final Engineer AI message.");
                }}
                if (html.includes('id="detail-investigation-input"')) {{
                  throw new Error("Normal investigation composer should stay hidden while awaiting confirmation.");
                }}
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
                  priority: "high",
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

    def test_engineer_detail_renders_customer_message_sentiment_pill_only_for_customer_messages(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                selectedTicketId = "TK-DETAIL-SENTIMENT";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-SENTIMENT",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  priority: "normal",
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

    def test_engineer_detail_revise_action_reuses_main_composer_and_submit_targets_confirmation_endpoint(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                routeState.view = "detail";
                selectedTicketId = "TK-DETAIL-REV";
                selectedTicket = {
                  ticket_id: "TK-DETAIL-REV",
                  subject: "Android 14 token renew regression",
                  requester: "user-7",
                  priority: "high",
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

                const reviseButton = {
                  dataset: { detailAction: "revise-investigation" },
                  disabled: false,
                };
                const reviseTarget = {
                  closest(selector) {
                    if (selector === "button[data-detail-action]") {
                      return reviseButton;
                    }
                    return null;
                  },
                };

                handleDetailClick({ target: reviseTarget });
                const reviseHtml = renderTicketDetailView();
                if (!reviseHtml.includes('id="detail-investigation-input"')) {{
                  throw new Error("Ask AI to Revise should reopen the main investigation composer.");
                }}
                if (!reviseHtml.includes("Tell Engineer AI what to revise before replying to the customer")) {{
                  throw new Error("Revise mode should update the composer placeholder.");
                }}
                if (!reviseHtml.includes(">Send Revision Note<")) {{
                  throw new Error("Revise mode should relabel the composer submit action.");
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
                  throw new Error("Revise submit should call the investigation confirmation endpoint.");
                }}
                const parsedBody = JSON.parse(capturedOptions.body);
                if (parsedBody.decision !== "revise") {{
                  throw new Error("Revise submit should post decision=revise.");
                }}
                if (parsedBody.note !== "Add a cache-clear step before asking the customer to retry.") {{
                  throw new Error("Revise submit should send the engineer revision note.");
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
                  priority: "high",
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
