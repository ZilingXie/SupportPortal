from __future__ import annotations

import re
import subprocess
import textwrap
import unittest
from pathlib import Path


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

    def test_client_ui_uses_stitch_brand_language(self) -> None:
        html = Path("ui/client-ui/index.html").read_text(encoding="utf-8")
        app_source = Path("ui/client-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/client-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("Concierge AI", html)
        self.assertIn("Manrope", html)
        self.assertIn("./styles.css?v=20260401-client-status-surfaces-1", html)
        self.assertIn('./app.js?v=20260401-client-status-surfaces-1', html)
        self.assertIn("AI-SOLVING", app_source)
        self.assertIn("Session History", app_source)
        self.assertIn('navigate("/chat");', app_source)
        self.assertIn('<span class="sidebar-nav-label">New Session</span>', app_source)
        self.assertNotIn('aria-label="New session"', app_source)
        self.assertNotIn("workspace-toolbar", app_source)
        self.assertNotIn("CONCIERGE READY", app_source)
        self.assertNotIn("Create a new support session or reopen a recent ticket.", app_source)
        self.assertIn("function ensureAuthedShell()", app_source)
        self.assertIn('data-authed-region="sidebar-nav"', app_source)
        self.assertNotIn('appRoot.innerHTML = `\n    <div class="app-shell">', app_source)

        new_session_pos = app_source.index('<span class="sidebar-nav-label">New Session</span>')
        workspace_pos = app_source.index('<span class="sidebar-nav-label">Workspace</span>')
        history_pos = app_source.index('<span class="sidebar-nav-label">Session History</span>')
        self.assertLess(new_session_pos, workspace_pos)
        self.assertLess(workspace_pos, history_pos)

        self.assertIn(".sidebar:not(:hover):not(:focus-within) .user-row", css)
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

    def test_client_shows_investigating_status_without_leaking_internal_thread(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                const normalized = normalizeBackendTicket({
                  ticket_id: "TK-CLIENT-INV",
                  customer_id: "user-1",
                  subject: "Android token renew issue",
                  status: "investigating",
                  created_at: "2026-03-24T08:00:00+00:00",
                  updated_at: "2026-03-24T08:10:00+00:00",
                  messages: [
                    {
                      role: "customer",
                      content: "Token renew callback does not fire.",
                      created_at: "2026-03-24T08:00:00+00:00",
                    },
                    {
                      role: "assistant",
                      content: "I've opened an engineer ticket for this issue and we're investigating further. I'll reply here as soon as the engineer review is confirmed.",
                      created_at: "2026-03-24T08:01:00+00:00",
                    },
                  ],
                  active_investigation: {
                    id: "INV-CLIENT-1",
                    state: "active",
                    messages: [
                      {
                        id: "INV-CLIENT-1-m1",
                        role: "engineer_ai",
                        content: "Please confirm the SDK version first.",
                        created_at: "2026-03-24T08:02:00+00:00",
                      },
                    ],
                  },
                });

                if (!normalized) {
                  throw new Error("Expected backend ticket normalization to return a ticket object.");
                }
                if (normalized.status !== "investigating") {
                  throw new Error(`Expected investigating status, got ${normalized.status}.`);
                }
                if (!statusBadge("investigating").includes("Investigating")) {
                  throw new Error("Client badge rendering should expose Investigating as a first-class state.");
                }
                if (normalized.messages.some((message) => message.role === "engineer_ai")) {
                  throw new Error("Client normalization must never leak internal investigation thread messages.");
                }
                if (mapBackendStatusToClientStatus({ status: "waiting_for_engineer" }) !== "investigating") {
                  throw new Error("Legacy waiting_for_engineer status should normalize to investigating on the client.");
                }
              """
            )
        )

    def test_client_new_session_reuses_existing_empty_ticket_and_hides_close_actions(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const firstDraft = getOrCreateDraftTicket(state.user.id);
                const secondDraft = getOrCreateDraftTicket(state.user.id);
                if (firstDraft.id !== secondDraft.id) {
                  throw new Error("Expected existing empty draft session to be reused.");
                }
                if (getAllTickets().length !== 1) {
                  throw new Error("Expected only one empty draft session after repeated new-session clicks.");
                }

                state.view = "chat-ticket";
                state.activeTicketId = firstDraft.id;
                const emptyContextBar = renderContextBar();
                if (emptyContextBar.includes("Close Ticket")) {
                  throw new Error("Empty draft session should not show Close Ticket in chat view.");
                }

                saveTicketMessages(firstDraft.id, [
                  {
                    id: "msg-1",
                    role: "user",
                    content: "Need help with VPN access",
                    createdAt: new Date().toISOString(),
                  },
                ]);
                updateTicketStatus(firstDraft.id, "communicating");
                const filledContextBar = renderContextBar();
                if (!filledContextBar.includes("Close Ticket")) {
                  throw new Error("Non-empty session should show Close Ticket in chat view.");
                }

                const emptyDraftInHistory = createTicket(state.user.id);
                state.view = "tickets";
                state.statusFilter = "all";
                const ticketsHtml = renderTicketsPage();
                if (ticketsHtml.includes(`data-action="resolve-ticket" data-ticket-id="${emptyDraftInHistory.id}"`)) {
                  throw new Error("Empty draft session should not show Resolve in session history.");
                }
                """
            )
        )

    def test_client_new_session_does_not_renavigate_when_same_empty_ticket_is_open(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const draft = getOrCreateDraftTicket(state.user.id);
                state.view = "chat-ticket";
                state.activeTicketId = draft.id;
                window.location.hash = `#/chat/${draft.id}`;

                let navigateCalls = 0;
                let lastPath = null;
                navigate = (path) => {
                  navigateCalls += 1;
                  lastPath = path;
                };

                openDraftTicket(state.user.id);
                if (navigateCalls !== 0) {
                  throw new Error(`Expected no navigation when the same empty draft is already open, got ${navigateCalls} call(s) to ${lastPath}.`);
                }

                state.view = "chat-home";
                state.activeTicketId = null;
                window.location.hash = "#/chat";
                openDraftTicket(state.user.id);
                if (navigateCalls !== 1) {
                  throw new Error("Expected one navigation when opening the draft from another view.");
                }
                if (lastPath !== `/chat/${draft.id}`) {
                  throw new Error(`Expected navigation to /chat/${draft.id}, got ${lastPath}.`);
                }
                """
            )
        )

    def test_client_session_history_does_not_render_duplicate_start_new_session_button(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin" };
                state.view = "tickets";
                state.statusFilter = "all";

                const ticketsHtml = renderTicketsPage();
                if (ticketsHtml.includes("Start New Session")) {
                  throw new Error("Session History page should not render a duplicate Start New Session button.");
                }
                """
            )
        )

    def test_client_context_bar_requests_engineer_assistance_via_backend_without_fake_chat_message(self) -> None:
        css = Path("ui/client-ui/styles.css").read_text(encoding="utf-8")
        self.assertIn(".context-assistance-note", css)
        self.assertIn(".context-chip.is-escalated {\n  color: var(--danger);", css)
        self.assertIn(".status-escalated {\n  color: var(--danger);", css)

        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                updateTicketTitle(ticket.id, "Need direct engineer review");
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-1",
                    role: "user",
                    content: "Can an engineer check my routing issue?",
                    createdAt: new Date().toISOString(),
                  },
                ]);
                updateTicketStatus(ticket.id, "communicating");

                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;

                const initialBar = renderContextBar();
                if (!initialBar.includes("AI-SOLVING")) {
                  throw new Error("Active ticket should start in AI-SOLVING mode before escalation.");
                }
                if (!initialBar.includes('data-action="request-engineer-assistance"')) {
                  throw new Error("Active non-empty ticket should render the request engineer assistance button.");
                }
                if (!initialBar.includes("Request Engineer Assistance")) {
                  throw new Error("Engineer assistance button should render its copy.");
                }
                if (!initialBar.includes("Close Ticket")) {
                  throw new Error("Active non-empty ticket should still render Close Ticket.");
                }
                if (
                  initialBar.indexOf("Request Engineer Assistance") >
                  initialBar.indexOf("Close Ticket")
                ) {
                  throw new Error("Engineer assistance button should render to the left of Close Ticket.");
                }
                if (initialBar.includes("Estimate waiting time: 3 hours")) {
                  throw new Error("Waiting time note should not render before the user requests assistance.");
                }

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
                      updated_at: "2026-03-31T16:00:00.000Z",
                    }),
                  };
                };

                const changed = await requestEngineerAssistance(ticket.id);
                if (!changed) {
                  throw new Error("Requesting engineer assistance should update the local view state from backend confirmation.");
                }
                if (capturedUrl !== `/api/tickets/${ticket.id}/request-engineer-assistance`) {
                  throw new Error(`Expected request engineer assistance endpoint call, got ${capturedUrl}.`);
                }
                if (String(capturedOptions?.method || "").toUpperCase() !== "POST") {
                  throw new Error("Engineer assistance should use POST.");
                }

                const escalatedTicket = getTicketById(ticket.id);
                if (escalatedTicket.status !== "escalated") {
                  throw new Error(`Engineer assistance should mark the ticket as escalated, got ${escalatedTicket.status}.`);
                }
                if (escalatedTicket.messages.length !== 1) {
                  throw new Error("Engineer assistance should not append a fake escalation message into the transcript.");
                }

                const requestedBar = renderContextBar();
                if (!requestedBar.includes("Estimate waiting time: 3 hours")) {
                  throw new Error("Requested engineer assistance should render the inline waiting estimate.");
                }
                if (requestedBar.includes("Request Engineer Assistance")) {
                  throw new Error("After requesting assistance, the button should be replaced by the waiting estimate.");
                }
                if (requestedBar.includes("AI-SOLVING")) {
                  throw new Error("Escalated ticket should no longer display the AI-SOLVING label.");
                }
                if (!requestedBar.includes("Escalated")) {
                  throw new Error("Escalated ticket should display the Escalated status label.");
                }
                if (!requestedBar.includes("Waiting for Engineer")) {
                  throw new Error("Escalated ticket should show Waiting for Engineer as the ticket status badge.");
                }
                const escalatedMatches = requestedBar.match(/Escalated/g) || [];
                if (escalatedMatches.length !== 1) {
                  throw new Error(`Escalated label should render only once in the context bar, got ${escalatedMatches.length}.`);
                }
                if (!requestedBar.includes("Close Ticket")) {
                  throw new Error("Close Ticket should remain available after requesting assistance.");
                }
                if (
                  requestedBar.indexOf("Estimate waiting time: 3 hours") >
                  requestedBar.indexOf("Close Ticket")
                ) {
                  throw new Error("The waiting estimate should render in the original assistance button position.");
                }

                const chatHtml = renderChatTicket();
                if (
                  chatHtml.includes(
                    "your request has been escalated to an engineer, and he/she will contact you at earlist possible. Estimated waiting time: 3 hours."
                  )
                ) {
                  throw new Error("Escalation request should not append a fake escalation notice into the chat transcript.");
                }
              """
            )
        )

    def test_client_session_history_uses_shared_history_rows_for_page_and_sidebar(self) -> None:
        css = Path("ui/client-ui/styles.css").read_text(encoding="utf-8")
        self.assertIn(".history-list", css)
        self.assertIn(".history-row", css)
        self.assertIn(".history-row-compact", css)
        self.assertIn(".history-row-actions", css)

        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const activeTicket = createTicket(state.user.id);
                updateTicketTitle(activeTicket.id, "VPN latency investigation");
                saveTicketMessages(activeTicket.id, [
                  {
                    id: "msg-1",
                    role: "user",
                    content: "Need help with VPN latency",
                    createdAt: new Date().toISOString(),
                  },
                ]);
                updateTicketStatus(activeTicket.id, "communicating");

                const openTicket = createTicket(state.user.id);
                updateTicketTitle(openTicket.id, "Account setup question");
                saveTicketMessages(openTicket.id, [
                  {
                    id: "msg-open-1",
                    role: "user",
                    content: "I need help setting up my account",
                    createdAt: new Date().toISOString(),
                  },
                ]);
                updateTicketStatus(openTicket.id, "open");

                const escalatedTicket = createTicket(state.user.id);
                updateTicketTitle(escalatedTicket.id, "Need engineer help");
                saveTicketMessages(escalatedTicket.id, [
                  {
                    id: "msg-escalated-1",
                    role: "user",
                    content: "Please involve an engineer",
                    createdAt: new Date().toISOString(),
                  },
                ]);
                updateTicketStatus(escalatedTicket.id, "escalated");

                const investigatingTicket = createTicket(state.user.id);
                updateTicketTitle(investigatingTicket.id, "Token investigation");
                saveTicketMessages(investigatingTicket.id, [
                  {
                    id: "msg-investigating-1",
                    role: "user",
                    content: "Token callback is stuck",
                    createdAt: new Date().toISOString(),
                  },
                ]);
                updateTicketStatus(investigatingTicket.id, "investigating");

                const resolvedTicket = createTicket(state.user.id);
                updateTicketTitle(resolvedTicket.id, "Database restore follow-up");
                saveTicketMessages(resolvedTicket.id, [
                  {
                    id: "msg-2",
                    role: "user",
                    content: "Issue resolved, thanks",
                    createdAt: new Date().toISOString(),
                  },
                ]);
                updateTicketStatus(resolvedTicket.id, "resolved");

                state.activeTicketId = activeTicket.id;
                state.view = "tickets";
                state.statusFilter = "all";

                const sidebarHtml = renderSidebarContent();
                if (sidebarHtml.includes("session-btn")) {
                  throw new Error("Sidebar session history should no longer use the legacy session button card.");
                }
                if (!sidebarHtml.includes("history-row history-row-compact")) {
                  throw new Error("Sidebar session history should render compact history rows.");
                }
                if (!sidebarHtml.includes('data-history-ticket-row="true"')) {
                  throw new Error("Compact history rows should expose row semantics.");
                }
                if (!sidebarHtml.includes("history-row-kicker")) {
                  throw new Error("Compact history rows should render the shared session kicker.");
                }
                if (!sidebarHtml.includes("history-row-meta")) {
                  throw new Error("Compact history rows should render shared session meta.");
                }
                if (sidebarHtml.includes("status-surface-open")) {
                  throw new Error("Sidebar compact rows should not use the open surface class.");
                }
                if (sidebarHtml.includes("status-surface-communicating")) {
                  throw new Error("Sidebar compact rows should not use the communicating surface class.");
                }
                if (sidebarHtml.includes("status-surface-escalated")) {
                  throw new Error("Sidebar compact rows should not use the escalated surface class.");
                }
                if (sidebarHtml.includes("status-surface-investigating")) {
                  throw new Error("Sidebar compact rows should not use the investigating surface class.");
                }
                if (sidebarHtml.includes("status-surface-resolved")) {
                  throw new Error("Sidebar compact rows should not use the resolved surface class.");
                }

                const ticketsHtml = renderTicketsPage();
                if (ticketsHtml.includes("tickets-grid")) {
                  throw new Error("Session History page should no longer render the old grid layout.");
                }
                if (ticketsHtml.includes("ticket-card")) {
                  throw new Error("Session History page should no longer render legacy ticket cards.");
                }
                if (!ticketsHtml.includes('class="history-list"')) {
                  throw new Error("Session History page should render the shared history list container.");
                }
                if (!ticketsHtml.includes("history-row")) {
                  throw new Error("Session History page should render shared history rows.");
                }
                if (!ticketsHtml.includes('role="button"')) {
                  throw new Error("History rows should expose button semantics.");
                }
                if (!ticketsHtml.includes('tabindex="0"')) {
                  throw new Error("History rows should be keyboard focusable.");
                }
                if (!ticketsHtml.includes("history-row-actions")) {
                  throw new Error("History rows should keep the explicit action area.");
                }
                if (!ticketsHtml.includes("status-surface-open")) {
                  throw new Error("Session History page should render open status surfaces.");
                }
                if (!ticketsHtml.includes("status-surface-communicating")) {
                  throw new Error("Session History page should render communicating status surfaces.");
                }
                if (!ticketsHtml.includes("status-surface-escalated")) {
                  throw new Error("Session History page should render escalated status surfaces.");
                }
                if (!ticketsHtml.includes("status-surface-investigating")) {
                  throw new Error("Session History page should render investigating status surfaces.");
                }
                if (!ticketsHtml.includes("status-surface-resolved")) {
                  throw new Error("Session History page should render resolved status surfaces.");
                }
                if (!ticketsHtml.includes("Created")) {
                  throw new Error("History rows should render created metadata.");
                }
                if (!ticketsHtml.includes("Updated")) {
                  throw new Error("History rows should render updated metadata.");
                }
              """
            )
        )

    def test_client_active_history_row_keeps_status_surface(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                const rowHtml = renderHistoryRow(
                  {
                    id: "TK-ACTIVE",
                    title: "Open onboarding task",
                    status: "open",
                    createdAt: "2026-03-24T08:00:00+00:00",
                    updatedAt: "2026-03-24T08:10:00+00:00",
                  },
                  { active: true }
                );

                if (!rowHtml.includes("status-surface-open")) {
                  throw new Error("Active history rows should retain their status surface class.");
                }
                if (!rowHtml.includes("is-active")) {
                  throw new Error("Active history rows should keep the active class.");
                }

                const compactHtml = renderHistoryRow(
                  {
                    id: "TK-COMPACT",
                    title: "Compact sidebar row",
                    status: "resolved",
                    createdAt: "2026-03-24T08:00:00+00:00",
                    updatedAt: "2026-03-24T08:10:00+00:00",
                  },
                  { compact: true, active: true }
                );
                if (compactHtml.includes("status-surface-resolved")) {
                  throw new Error("Compact sidebar rows should not receive the shared light surface classes.");
                }
              """
            )
        )

    def test_client_history_row_interactions_ignore_nested_buttons_and_support_keyboard(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                const row = {
                  dataset: { ticketId: "TK-321" },
                };

                const buttonTarget = {
                  closest(selector) {
                    if (selector === "[data-history-ticket-row]") {
                      return row;
                    }
                    if (selector.includes("button")) {
                      return { tagName: "BUTTON" };
                    }
                    return null;
                  },
                };

                const plainTarget = {
                  closest(selector) {
                    if (selector === "[data-history-ticket-row]") {
                      return row;
                    }
                    if (selector.includes("button")) {
                      return null;
                    }
                    return null;
                  },
                };

                if (getHistoryRowTarget(buttonTarget) !== null) {
                  throw new Error("Nested interactive controls should not activate the history row.");
                }
                if (getHistoryRowTarget(plainTarget) !== row) {
                  throw new Error("Plain row content should resolve to the history row target.");
                }

                let lastPath = null;
                navigate = (path) => {
                  lastPath = path;
                };

                handleHistoryRowClick({ target: plainTarget });
                if (lastPath !== "/chat/TK-321") {
                  throw new Error(`Row click should navigate to the chat ticket, got ${lastPath}.`);
                }

                lastPath = null;
                handleHistoryRowClick({ target: buttonTarget });
                if (lastPath !== null) {
                  throw new Error("Nested button clicks should not trigger row navigation.");
                }

                let prevented = false;
                handleHistoryRowKeydown({
                  key: "Enter",
                  target: plainTarget,
                  preventDefault() {
                    prevented = true;
                  },
                });
                if (!prevented) {
                  throw new Error("Keyboard row activation should prevent the default event.");
                }
                if (lastPath !== "/chat/TK-321") {
                  throw new Error(`Keyboard row activation should navigate to the chat ticket, got ${lastPath}.`);
                }
              """
            )
        )

    def test_client_session_history_uses_custom_status_filter_dropdown(self) -> None:
        app_source = Path("ui/client-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/client-ui/styles.css").read_text(encoding="utf-8")

        self.assertNotIn('<select class="select" id="status-filter">', app_source)
        self.assertIn('class="filter-select"', app_source)
        self.assertIn('function renderStatusFilter()', app_source)
        self.assertIn("function bindStatusFilter()", app_source)
        self.assertIn("role=\"combobox\"", app_source)
        self.assertIn("role=\"listbox\"", app_source)
        self.assertIn("role=\"option\"", app_source)
        self.assertIn(".filter-select", css)
        self.assertIn(".filter-select-panel", css)
        self.assertIn(".filter-select-option.is-selected", css)

    def test_client_chat_composer_uses_inline_icon_actions_without_toolbar(self) -> None:
        css = Path("ui/client-ui/styles.css").read_text(encoding="utf-8")
        self.assertIn(".composer-icon-button", css)
        self.assertNotIn(".composer-toolbar", css)

        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = getOrCreateDraftTicket(state.user.id);
                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;

                const idleHtml = renderChatTicket();
                if (idleHtml.includes("composer-toolbar")) {
                  throw new Error("Chat composer should not render the old toolbar block.");
                }
                if (idleHtml.includes('data-action="go-tickets"')) {
                  throw new Error("Chat composer should not render Session History inside the input area.");
                }
                if (!idleHtml.includes('class="composer-icon-button send-btn"')) {
                  throw new Error("Chat composer should render an inline send icon button.");
                }
                if (!idleHtml.includes('aria-label="Send Request"')) {
                  throw new Error("Send icon button should expose an accessible label.");
                }

                state.isSending = true;
                state.pendingTicketId = ticket.id;

                const sendingHtml = renderChatTicket();
                if (!sendingHtml.includes('class="composer-icon-button composer-stop-btn"')) {
                  throw new Error("Sending state should render an inline stop icon button.");
                }
                if (!sendingHtml.includes('data-action="stop-generation"')) {
                  throw new Error("Stop icon button should keep the existing stop-generation action.");
                }
                if (!sendingHtml.includes('aria-label="Stop Generation"')) {
                  throw new Error("Stop icon button should expose an accessible label.");
                }
                """
            )
        )

    def test_client_chat_auto_scrolls_only_when_transcript_changes(self) -> None:
        self.run_client_app_script(
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

                state.user = { id: "user-1", name: "Admin", email: "admin@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-1",
                    role: "assistant",
                    content: "Initial reply",
                    createdAt: "2026-03-31T15:24:00.000Z",
                  },
                ]);

                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;

                const chatMain = {
                  scrollTop: 0,
                  scrollHeight: 180,
                };
                appRoot.querySelector = (selector) => (selector === ".chat-main" ? chatMain : null);

                syncChatScrollToBottom();
                flushFrames();
                if (chatMain.scrollTop !== 180) {
                  throw new Error(`Expected initial chat render to scroll to bottom, got ${chatMain.scrollTop}.`);
                }

                chatMain.scrollTop = 0;
                updateTicketStatus(ticket.id, "communicating");
                syncChatScrollToBottom();
                flushFrames();
                if (chatMain.scrollTop !== 0) {
                  throw new Error("Chat should not force-scroll when the transcript is unchanged.");
                }

                chatMain.scrollHeight = 420;
                saveTicketMessages(ticket.id, [
                  ...getTicketById(ticket.id).messages,
                  {
                    id: "msg-2",
                    role: "user",
                    content: "Follow-up question",
                    createdAt: "2026-03-31T15:25:00.000Z",
                  },
                ]);
                syncChatScrollToBottom();
                flushFrames();
                if (chatMain.scrollTop !== 420) {
                  throw new Error(`Expected new transcript entries to scroll to bottom, got ${chatMain.scrollTop}.`);
                }
              """
            )
        )

    def test_client_chat_layout_uses_viewport_locked_internal_scroll(self) -> None:
        css = Path("ui/client-ui/styles.css").read_text(encoding="utf-8")

        def rule_block(selector_pattern: str) -> str:
            match = re.search(rf"{selector_pattern}\s*\{{([^}}]+)\}}", css, re.S)
            self.assertIsNotNone(match, msg=f"Expected CSS rule for {selector_pattern}.")
            return match.group(1)

        app_shell = rule_block(r"\.app-shell")
        self.assertIn("display: flex;", app_shell)
        self.assertIn("height: 100vh;", app_shell)
        self.assertIn("overflow: hidden;", app_shell)

        workspace_shell = rule_block(r"\.workspace-shell")
        self.assertIn("height: 100vh;", workspace_shell)
        self.assertIn("min-height: 0;", workspace_shell)
        self.assertIn("overflow: hidden;", workspace_shell)

        main = rule_block(r"\.main")
        self.assertIn("overflow: hidden;", main)

        page_panels = rule_block(r"\.welcome,\s*\.tickets-root")
        self.assertIn("overflow: auto;", page_panels)

    def test_client_async_polling_ignores_placeholder_reply_until_final_answer_arrives(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin" };
                state.pendingUserMessageId = "local-user-message";
                state.pendingAsyncTicketId = "TK-015";
                state.pendingAsyncMessageCreatedAt = "2026-03-22T12:38:23.235109+00:00";

                const ticket = {
                  id: "TK-015",
                  userId: "user-1",
                  title: "how to join channel",
                  status: "communicating",
                  createdAt: "2026-03-22T12:38:23.235078+00:00",
                  updatedAt: "2026-03-22T12:38:25.168026+00:00",
                  messages: [
                    {
                      id: "TK-015-m-2026-03-22T12:38:23.235109+00:00-0",
                      role: "user",
                      content: "how to join channel",
                      createdAt: "2026-03-22T12:38:23.235109+00:00",
                    },
                    {
                      id: "TK-015-m-2026-03-22T12:38:25.168026+00:00-1",
                      role: "assistant",
                      content: "Thank you for your message. I will check the issue and get back to you shortly.",
                      createdAt: "2026-03-22T12:38:25.168026+00:00",
                    },
                  ],
                };

                if (ticketHasAssistantReply(ticket)) {
                  throw new Error("Placeholder reply should not stop async polling.");
                }

                ticket.messages.push({
                  id: "TK-015-m-2026-03-22T12:39:03.540492+00:00-2",
                  role: "assistant",
                  content: "To join a channel in the Agora Video Calling SDK for Android, call the joinChannel method.",
                  createdAt: "2026-03-22T12:39:03.540492+00:00",
                });

                if (!ticketHasAssistantReply(ticket)) {
                  throw new Error("Final assistant reply should stop async polling.");
                }
                """
            )
        )


if __name__ == "__main__":
    unittest.main()
