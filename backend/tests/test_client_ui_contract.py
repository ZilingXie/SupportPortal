from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


class ClientUiContractTests(unittest.TestCase):
    def run_client_app_script(self, script: str) -> None:
        node_script = textwrap.dedent(
            f"""
            const fs = require("fs");
            const vm = require("vm");

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

    def test_client_ui_uses_stitch_brand_language(self) -> None:
        html = Path("ui/client-ui/index.html").read_text(encoding="utf-8")
        app_source = Path("ui/client-ui/app.js").read_text(encoding="utf-8")

        self.assertIn("Concierge AI", html)
        self.assertIn("Manrope", html)
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

        css = Path("ui/client-ui/styles.css").read_text(encoding="utf-8")
        self.assertIn(".sidebar:not(:hover):not(:focus-within) .user-row", css)

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
                updateTicketStatus(firstDraft.id, "waiting_for_support");
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
                  status: "waiting_for_support",
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
