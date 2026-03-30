from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


class EngineerUiContractTests(unittest.TestCase):
    def run_engineer_app_script(self, script: str) -> None:
        node_script = textwrap.dedent(
            f"""
            const fs = require("fs");
            const vm = require("vm");

            let source = fs.readFileSync("ui/engineer-ui/app.js", "utf8");
            source = source.replace(/\\nloginFormEl\\.addEventListener\\([\\s\\S]*$/, "\\n");

            function createElementStub() {{
              return {{
                innerHTML: "",
                textContent: "",
                value: "",
                dataset: {{}},
                disabled: false,
                addEventListener() {{}},
                removeEventListener() {{}},
                querySelector() {{ return null; }},
                querySelectorAll() {{ return []; }},
                closest() {{ return null; }},
                focus() {{}},
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
            vm.runInContext({script!r}, sandbox);
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
        self.assertIn("./styles.css?v=20260326-engineer-stitch-7", html)
        self.assertIn('./app.js?v=20260326-engineer-stitch-7', html)
        self.assertIn("function parseRoute()", app_source)
        self.assertIn('path.startsWith("/tickets/")', app_source)
        self.assertIn("function renderTicketPoolView()", app_source)
        self.assertIn("function renderTicketDetailView()", app_source)
        self.assertIn("Next Action Needed", app_source)
        self.assertIn("AI Managing", app_source)
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
        self.assertNotIn(".ticket-pool-grid", css)
        self.assertIn(".ticket-workspace", css)

    def test_engineer_ticket_pool_uses_list_rows_and_prioritizes_investigating(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                tickets = [
                  {
                    ticket_id: "TK-OPEN-URGENT",
                    subject: "open urgent ticket",
                    requester: "user-1",
                    priority: "urgent",
                    status: "open",
                    engineer_mode: "managed",
                    created_at: "2026-03-24T09:00:00+00:00",
                    updated_at: "2026-03-24T10:00:00+00:00",
                    pending_engineer_question: "",
                  },
                  {
                    ticket_id: "TK-INVESTIGATING-LOW",
                    subject: "investigating ticket",
                    requester: "user-2",
                    priority: "low",
                    status: "investigating",
                    engineer_mode: "managed",
                    created_at: "2026-03-24T08:00:00+00:00",
                    updated_at: "2026-03-24T08:30:00+00:00",
                    pending_engineer_question: "",
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
                filterValues.mode = "all";
                filterValues.status = "all";

                const html = renderTicketPoolView();
                if (html.includes("ticket-card")) {{
                  throw new Error("Ticket pool should no longer render card layout.");
                }}
                if (html.includes("Open Workspace")) {{
                  throw new Error("Ticket pool rows should no longer render an Open Workspace button.");
                }}
                if (!html.includes("ticket-pool-list")) {{
                  throw new Error("Ticket pool should render the list container.");
                }}
                if (!html.includes("ticket-row")) {{
                  throw new Error("Ticket pool should render list rows.");
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
                if (!html.includes("mode-pill")) {{
                  throw new Error("Ticket pool rows should render the mode badge in the first line.");
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
                const modeIndex = waitingRowMarkup.indexOf("mode-pill");
                const secondLineIndex = waitingRowMarkup.indexOf("ticket-row-secondary");
                if (modeIndex === -1 || secondLineIndex === -1 || modeIndex > secondLineIndex) {{
                  throw new Error("Mode badge should stay in the first row alongside title and status badges.");
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
                  engineer_mode: "managed",
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
                if (!headerTopMarkup.includes("priority-badge") || !headerTopMarkup.includes("status-badge") || !headerTopMarkup.includes("mode-pill")) {{
                  throw new Error("Toolbar row should carry the ticket badges after the header compaction.");
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
                if (!html.includes("Please upgrade to SDK 4.2.2 and retry token renewal on Android 14.")) {{
                  throw new Error("Detail workspace should render the draft customer reply for final confirmation.");
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
                if (html.includes("Tell AI")) {{
                  throw new Error("Detail workspace should use the reply section instead of the old Tell AI button copy.");
                }}
                if (html.includes("Send to AI")) {{
                  throw new Error("Detail workspace should no longer render the internal reply composer action.");
                }}
                if (html.includes("Engineer Request Records")) {{
                  throw new Error("Detail workspace should use investigation history instead of legacy engineer request records.");
                }}
                const internalIndex = html.indexOf("Internal Investigation Thread");
                const customerIndex = html.indexOf("Customer Timeline");
                if (internalIndex === -1 || customerIndex === -1 || internalIndex > customerIndex) {{
                  throw new Error("Internal investigation thread should render ahead of the customer timeline.");
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
