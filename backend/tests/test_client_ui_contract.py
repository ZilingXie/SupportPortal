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

        self.assertIn('<html lang="en" class="material-symbols-pending">', html)
        self.assertIn("Sid", html)
        self.assertNotIn("Concierge AI", html)
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
        self.assertIn("<title>Sid - AI Technical Support</title>", html)

        self.assertIn("./styles.css?v=20260416-client-ai-product-selection-1", html)
        self.assertIn('./app.js?v=20260416-client-ai-product-selection-1', html)
        self.assertNotIn("AI-SOLVING", app_source)
        self.assertIn("AI Technical Support", app_source)
        self.assertNotIn(">Technical Support<", app_source)
        self.assertIn("Session History", app_source)
        self.assertIn("Sid", app_source)
        self.assertIn("zac@example.com", app_source)
        self.assertIn("pendingByTicket", app_source)
        self.assertIn("supersededTurnsByTicket", app_source)
        self.assertNotIn('data-action="stop-generation"', app_source)
        self.assertNotIn("Concierge AI", app_source)
        self.assertIn('navigate("/chat");', app_source)
        self.assertIn('<span class="sidebar-nav-label">New Session</span>', app_source)
        self.assertNotIn('aria-label="New session"', app_source)
        self.assertNotIn("workspace-toolbar", app_source)
        self.assertNotIn("CONCIERGE READY", app_source)
        self.assertNotIn("Create a new support session or reopen a recent ticket.", app_source)
        self.assertIn("function ensureAuthedShell()", app_source)
        self.assertIn('data-authed-region="sidebar-nav"', app_source)
        self.assertIn("context-bar context-bar-ticket", app_source)
        self.assertIn('class="context-ticket-meta"', app_source)
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
        self.assertIn(".context-bar-ticket {", css)
        self.assertIn("flex-wrap: nowrap;", css)
        self.assertIn(".context-bar-ticket .context-ticket-meta {", css)
        self.assertIn(".context-bar-ticket .context-actions {", css)
        self.assertIn('font-family: "Material Symbols Outlined";', css)
        self.assertIn("html.material-symbols-pending .material-symbols-outlined", css)
        self.assertIn("visibility: hidden;", css)

    def test_client_query_payload_includes_requester_name(self) -> None:
        app_source = Path("ui/client-ui/app.js").read_text(encoding="utf-8")

        self.assertIn("requester: state.user.name", app_source)

    def test_client_query_payload_only_includes_product_when_known(self) -> None:
        app_source = Path("ui/client-ui/app.js").read_text(encoding="utf-8")

        self.assertIn("if (normalizedProduct) {", app_source)
        self.assertIn("requestBody.product = normalizedProduct;", app_source)

    def test_client_login_uses_zac_credentials_and_identity(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                const success = login("Zac", "Zac");
                if (!success) {
                  throw new Error("Expected Zac / Zac to authenticate.");
                }
                if (success.name !== "Zac") {
                  throw new Error(`Expected Zac display name, got ${success.name}.`);
                }
                if (success.email !== "zac@example.com") {
                  throw new Error(`Expected zac@example.com identity, got ${success.email}.`);
                }
                if (login("admin", "admin") !== null) {
                  throw new Error("Legacy admin / admin credentials should no longer authenticate.");
                }
                const stored = JSON.parse(localStorage.getItem("helpdesk_auth_user") || "null");
                if (!stored || stored.name !== "Zac" || stored.email !== "zac@example.com") {
                  throw new Error(`Expected Zac identity to persist, got ${JSON.stringify(stored)}.`);
                }
                """
            )
        )

    def test_client_restore_clears_legacy_admin_session(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                const legacyUsers = [
                  { id: "user-1", name: "Admin", email: "admin" },
                  { id: "user-1", name: "Admin", email: "admin@example.com" },
                ];

                for (const legacyUser of legacyUsers) {
                  localStorage.setItem("helpdesk_auth_user", JSON.stringify(legacyUser));
                  const restored = getCurrentUser();
                  if (restored !== null) {
                    throw new Error(`Legacy Admin session should be rejected, got ${JSON.stringify(restored)}.`);
                  }
                  if (localStorage.getItem("helpdesk_auth_user") !== null) {
                    throw new Error("Legacy Admin session should be cleared from localStorage.");
                  }
                }
                """
            )
        )

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
                      content: "This issue requires further internal investigation, which may take some time. Thank you for your patience. We expect to reply here within 24 hours.",
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

    def test_client_sync_uses_client_ticket_endpoint_and_hides_engineer_case_identity(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin" };
                let capturedUrl = null;
                fetch = async (url) => {
                  capturedUrl = url;
                  return {
                    ok: true,
                    json: async () => ({
                      tickets: [
                        {
                          ticket_id: "TK-040",
                          customer_id: "user-1",
                          subject: "how to join channel",
                          status: "investigating",
                          active_engineer_case_id: "TK-040-1",
                          engineer_case_count: 1,
                          created_at: "2026-04-02T08:00:00+00:00",
                          updated_at: "2026-04-02T08:10:00+00:00",
                          messages: [
                            {
                              role: "customer",
                              content: "i got black screen issue",
                              created_at: "2026-04-02T08:00:00+00:00",
                            },
                            {
                              role: "assistant",
                              content: "This issue requires further internal investigation, which may take some time. Thank you for your patience. We expect to reply here within 24 hours.",
                              created_at: "2026-04-02T08:01:00+00:00",
                            },
                          ],
                        },
                      ],
                    }),
                  };
                };

                await syncTicketsFromBackend({ silent: true });
                if (capturedUrl !== "/api/tickets?customer_id=user-1&status=all") {
                  throw new Error(`Client sync should use the client ticket endpoint, got ${capturedUrl}.`);
                }

                state.view = "tickets";
                state.statusFilter = "all";
                const html = renderTicketsPage();
                if (!html.includes("TK-040")) {
                  throw new Error("Client session history should keep the client ticket id.");
                }
                if (html.includes("TK-040-1")) {
                  throw new Error("Client session history must not render linked engineer case ids.");
                }
                if (html.includes("Client Ticket")) {
                  throw new Error("Client UI should not expose engineer-side parent ticket metadata.");
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
                if (!filledContextBar.includes("Resolve")) {
                  throw new Error("Non-empty session should show Resolve in chat view.");
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

    def test_client_new_session_renders_light_hint_and_keeps_composer_enabled(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const draft = getOrCreateDraftTicket(state.user.id);
                state.view = "chat-ticket";
                state.activeTicketId = draft.id;

                const html = renderChatTicket();
                if (!html.includes("Describe your issue. Sid will identify the product if needed.")) {
                  throw new Error("Empty draft session should render the lightweight AI product-identification hint.");
                }
                if (html.includes("Select Product")) {
                  throw new Error("Empty draft session should no longer render the product selector.");
                }
                if (html.includes("Hi Zac")) {
                  throw new Error("Empty draft session should no longer render the welcome email bubble.");
                }
                if (/id="chat-input"[^>]*disabled/.test(html)) {
                  throw new Error("Composer should stay enabled without a preselected product.");
                }

                const draftAfterRender = getTicketById(draft.id);
                if (!draftAfterRender || (draftAfterRender.messages || []).length !== 0) {
                  throw new Error("Rendering the lightweight hint must not append durable ticket messages.");
                }
              """
            )
        )

    def test_client_empty_draft_hint_survives_backend_sync_without_creating_messages(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const draft = getOrCreateDraftTicket(state.user.id);
                state.view = "chat-ticket";
                state.activeTicketId = draft.id;

                fetch = async () => ({
                  ok: true,
                  json: async () => ({
                    tickets: [],
                  }),
                });

                await syncTicketsFromBackend({ silent: true });

                const html = renderChatTicket();
                if (!html.includes("Describe your issue. Sid will identify the product if needed.")) {
                  throw new Error("Backend sync should preserve the lightweight empty-session hint.");
                }
                if (html.includes('class="message-author">Sid</span>')) {
                  throw new Error("Empty-session hint should not render as a durable assistant message.");
                }
                const syncedDraft = getTicketById(draft.id);
                if (!syncedDraft || (syncedDraft.messages || []).length !== 0) {
                  throw new Error("Backend sync should not turn the empty-session hint into a durable message.");
                }
              """
            )
        )

    def test_client_empty_state_css_uses_hint_card_not_product_overlay(self) -> None:
        css = Path("ui/client-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn(".empty-chat-hint {", css)
        self.assertIn(".empty-chat-hint-eyebrow {", css)
        self.assertIn(".empty-chat-hint-copy {", css)
        self.assertNotIn(".chat-root.has-open-product-select {", css)
        self.assertNotIn(".product-select {", css)

    def test_client_public_assistant_messages_render_sid_identity(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                updateTicketProduct(ticket.id, "audio_video_calling");
                updateTicketStatus(ticket.id, "communicating");
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-1",
                    role: "user",
                    content: "How do I join a channel?",
                    createdAt: "2026-04-10T02:00:00.000Z",
                  },
                  {
                    id: "msg-2",
                    role: "assistant",
                    content: "Use the same channel name on both clients to join the same session.",
                    createdAt: "2026-04-10T02:01:00.000Z",
                  },
                ]);

                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;

                const html = renderChatTicket();
                if (!html.includes('class="message-author">Sid</span>')) {
                  throw new Error("Public assistant replies should render the Sid identity.");
                }
                if (html.includes('class="message-author">Concierge AI</span>')) {
                  throw new Error("Public assistant replies should no longer render Concierge AI as the message author.");
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
                if (!initialBar.includes('class="context-bar context-bar-ticket"')) {
                  throw new Error("Active ticket should render the dedicated single-line ticket context bar.");
                }
                if (!initialBar.includes(`${ticket.id}: Need direct engineer review`)) {
                  throw new Error("Active ticket context bar should render the bare ticket id prefix.");
                }
                if (initialBar.includes(`Ticket ${ticket.id}: Need direct engineer review`)) {
                  throw new Error("Active ticket context bar should not render the legacy Ticket prefix.");
                }
                if (!initialBar.includes('class="context-ticket-meta"')) {
                  throw new Error("Active ticket context bar should render a dedicated left-side badge group.");
                }
                if (initialBar.includes("AI-SOLVING")) {
                  throw new Error("Active ticket should no longer display AI-SOLVING.");
                }
                if (
                  initialBar.indexOf(`${ticket.id}: Need direct engineer review`) >
                  initialBar.indexOf('class="context-ticket-meta"')
                ) {
                  throw new Error("Badge group should render after the ticket title inside the left-side ticket block.");
                }
                if (
                  initialBar.indexOf("Communicating") >
                  initialBar.indexOf("Request Engineer")
                ) {
                  throw new Error("Status badge should remain on the left side before the action buttons.");
                }
                if (
                  initialBar.indexOf("Audio/Video Calling") >
                  initialBar.indexOf("Request Engineer")
                ) {
                  throw new Error("Product badge should remain on the left side before the action buttons.");
                }
                if (!initialBar.includes('data-action="request-engineer-assistance"')) {
                  throw new Error("Active non-empty ticket should render the request engineer assistance button.");
                }
                if (!initialBar.includes("Request Engineer")) {
                  throw new Error("Engineer assistance button should render its shortened copy.");
                }
                if (!initialBar.includes("Resolve")) {
                  throw new Error("Active non-empty ticket should still render Resolve.");
                }
                if (
                  initialBar.indexOf("Request Engineer") >
                  initialBar.indexOf("Resolve")
                ) {
                  throw new Error("Engineer assistance button should render to the left of Resolve.");
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
                if (requestedBar.includes("Request Engineer")) {
                  throw new Error("After requesting assistance, the button should be replaced by the waiting estimate.");
                }
                if (!requestedBar.includes("status-escalated")) {
                  throw new Error("Escalated ticket should retain the escalated status badge styling.");
                }
                if (!requestedBar.includes("Waiting for Engineer")) {
                  throw new Error("Escalated ticket should show Waiting for Engineer as the ticket status badge.");
                }
                if (!requestedBar.includes("Resolve")) {
                  throw new Error("Resolve should remain available after requesting assistance.");
                }
                if (
                  requestedBar.indexOf("Estimate waiting time: 3 hours") >
                  requestedBar.indexOf("Resolve")
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

    def test_client_send_message_async_omits_client_ack_and_waiting_copy(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin" };
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

                const ticket = createTicket(state.user.id);
                updateTicketProduct(ticket.id, "audio_video_calling");
                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;

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
                        queued_message_created_at: "2026-04-04T09:00:00.000Z",
                        ack_source: "client_model",
                        processing_mode: "main_agent_async",
                        status: "communicating",
                      }),
                    });
                  }
                  throw new Error(`Unexpected fetch call to ${url}`);
                };

                await handleSendMessage("how to join channel");

                const updated = getTicketById(ticket.id);
                if (!updated) {
                  throw new Error("Expected ticket to remain available after sending a message.");
                }
                if (updated.messages.length !== 1) {
                  throw new Error(`Expected only the durable user message to be saved, got ${updated.messages.length}.`);
                }
                if (updated.messages[0].role !== "user") {
                  throw new Error("Expected the durable transcript to contain only the user message.");
                }
                if (!calls.some((entry) => entry.url === "/api/tickets/query")) {
                  throw new Error("Expected ticket query endpoint to be called.");
                }
                if (calls.some((entry) => entry.url === "/api/client/ack")) {
                  throw new Error("Client send flow should no longer call the transient ack endpoint.");
                }

                const pendingHtml = renderChatTicket();
                if (pendingHtml.includes("Got it, let me check this for you.")) {
                  throw new Error("Client send flow should not render the legacy fallback reassurance.");
                }
                if (pendingHtml.includes("I got your message and I am checking it now.")) {
                  throw new Error("Client send flow should not render the model reassurance bubble.");
                }
                if (pendingHtml.includes("AI is cross-referencing system health logs")) {
                  throw new Error("Async waiting state should not render the thinking line.");
                }
                if (pendingHtml.includes("checking the knowledge base... click stop to interrupt.")) {
                  throw new Error("Async waiting state should not render the composer waiting note.");
                }
                if (!state.isSending || state.pendingAsyncTicketId !== ticket.id) {
                  throw new Error("Queued async ticket should keep the client waiting state active.");
                }
                if (!state.pendingByTicket[ticket.id] || state.pendingByTicket[ticket.id].phase !== "queued") {
                  throw new Error("Queued async ticket should be tracked in the per-ticket pending map.");
                }
                if (!pendingHtml.includes('class="composer-icon-button send-btn"')) {
                  throw new Error("Async waiting state should keep the inline send button available.");
                }
                if (pendingHtml.includes('class="composer-icon-button composer-stop-btn"')) {
                  throw new Error("Async waiting state should no longer render the inline stop button.");
                }
              """
            )
        )

    def test_client_send_message_cjk_async_omits_localized_reassurance(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin" };
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

                const ticket = createTicket(state.user.id);
                updateTicketProduct(ticket.id, "audio_video_calling");
                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;

                fetch = (url, options = undefined) => {
                  if (url === "/api/tickets/query") {
                    return Promise.resolve({
                      ok: true,
                      json: async () => ({
                        ticket_id: ticket.id,
                        answer: "",
                        ai_replied: false,
                        queued_for_ai: true,
                        queued_message_created_at: "2026-04-04T09:00:00.000Z",
                        ack_source: "client_model",
                        processing_mode: "main_agent_async",
                        status: "communicating",
                      }),
                    });
                  }
                  throw new Error(`Unexpected fetch call to ${url}`);
                };

                await handleSendMessage("这个问题怎么加入频道？");

                const html = renderChatTicket();
                if (html.includes("收到，我先帮你看一下。")) {
                  throw new Error("Async waiting state should not render the localized fallback reassurance.");
                }
                if (html.includes("收到，我来查看。")) {
                  throw new Error("Async waiting state should not render any localized model reassurance.");
                }
                if (html.includes("AI is cross-referencing system health logs")) {
                  throw new Error("Async waiting state should not render the thinking line for CJK messages either.");
                }
                if (!html.includes('class="composer-icon-button send-btn"')) {
                  throw new Error("Async waiting state should keep the inline send button for CJK messages.");
                }
                if (html.includes('class="composer-icon-button composer-stop-btn"')) {
                  throw new Error("Async waiting state should not render the old stop button for CJK messages.");
                }
              """
            )
        )

    def test_client_send_message_sync_durable_reply_omits_client_ack_flow(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin" };
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

                const ticket = createTicket(state.user.id);
                updateTicketProduct(ticket.id, "audio_video_calling");
                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;

                const calls = [];
                fetch = (url, options = undefined) => {
                  calls.push({ url, options });
                  if (url === "/api/tickets/query") {
                    return Promise.resolve({
                      ok: true,
                      json: async () => ({
                        ticket_id: ticket.id,
                        answer: "Use joinChannel with the same channel name and token.",
                        ai_replied: true,
                        queued_for_ai: false,
                        ack_source: "rule",
                        processing_mode: "main_agent_sync",
                        status: "communicating",
                      }),
                    });
                  }
                  throw new Error(`Unexpected fetch call to ${url}`);
                };

                await handleSendMessage("how to join channel");

                const updated = getTicketById(ticket.id);
                if (!updated) {
                  throw new Error("Expected ticket to remain available after sending a message.");
                }
                if (updated.messages.length !== 2) {
                  throw new Error(`Expected the durable user message and durable assistant reply, got ${updated.messages.length}.`);
                }
                if (updated.messages[1].content !== "Use joinChannel with the same channel name and token.") {
                  throw new Error("Expected the durable assistant reply to be persisted immediately.");
                }
                const resolvedHtml = renderChatTicket();
                if (!resolvedHtml.includes("Use joinChannel with the same channel name and token.")) {
                  throw new Error("Expected the durable assistant reply to render immediately.");
                }
                if (resolvedHtml.includes("Got it, let me check this for you.")) {
                  throw new Error("Server-side reassurance template should not render as the sync durable reply.");
                }
                if (resolvedHtml.includes("AI is cross-referencing system health logs")) {
                  throw new Error("Sync durable reply should not render the thinking line.");
                }
                if (resolvedHtml.includes("checking the knowledge base... click stop to interrupt.")) {
                  throw new Error("Sync durable reply should not render the composer waiting note.");
                }
                if (calls.some((entry) => entry.url === "/api/client/ack")) {
                  throw new Error("Sync durable reply should not call the transient client ack endpoint.");
                }
              """
            )
        )

    def test_client_send_message_sync_followup_sync_replaces_optimistic_customer_turn_without_duplication(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));
                render = () => {};
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

                const ticket = createTicket(state.user.id);
                updateTicketProduct(ticket.id, "audio_video_calling");
                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;

                const persistedCustomerCreatedAt = "2026-04-15T03:43:59.738250+00:00";
                const calls = [];
                fetch = (url, options = undefined) => {
                  calls.push({ url, options });
                  if (url === "/api/tickets/query") {
                    return Promise.resolve({
                      ok: true,
                      json: async () => ({
                        ticket_id: ticket.id,
                        answer: "Thanks for your response. I'm glad to hear the information provided was helpful. I'll mark this case as resolved. If you have any further questions, please create a new ticket.",
                        ai_replied: true,
                        queued_for_ai: false,
                        message_created_at: persistedCustomerCreatedAt,
                        ack_source: "workflow",
                        processing_mode: "main_agent_sync",
                        status: "resolved",
                      }),
                    });
                  }
                  if (url.startsWith("/api/tickets?customer_id=user-1&status=all")) {
                    return Promise.resolve({
                      ok: true,
                      json: async () => ({
                        tickets: [
                          {
                            ticket_id: ticket.id,
                            customer_id: "user-1",
                            subject: "Black Screen Troubleshooting",
                            status: "resolved",
                            product: "audio_video_calling",
                            created_at: "2026-04-15T03:30:00.000000+00:00",
                            updated_at: "2026-04-15T03:44:00.000000+00:00",
                            messages: [
                              {
                                role: "customer",
                                content: "it worked, thanks!",
                                created_at: persistedCustomerCreatedAt,
                              },
                              {
                                role: "assistant",
                                content: "Thanks for your response. I'm glad to hear the information provided was helpful. I'll mark this case as resolved. If you have any further questions, please create a new ticket.",
                                created_at: "2026-04-15T03:44:00.000000+00:00",
                              },
                            ],
                          },
                        ],
                      }),
                    });
                  }
                  throw new Error(`Unexpected fetch call to ${url}`);
                };

                await handleSendMessage("it worked, thanks!");

                const updated = getTicketById(ticket.id);
                if (!updated) {
                  throw new Error("Expected ticket to remain available after the sync response and catch-up sync.");
                }
                const followUps = updated.messages.filter(
                  (message) => message.role === "user" && message.content === "it worked, thanks!"
                );
                if (followUps.length !== 1) {
                  throw new Error(`Expected exactly one persisted user follow-up after sync, got ${followUps.length}.`);
                }
                if (followUps[0].createdAt !== persistedCustomerCreatedAt) {
                  throw new Error("Expected the backend persisted customer timestamp to replace the optimistic local timestamp.");
                }
                if (updated.messages.length !== 2) {
                  throw new Error(`Expected one user message and one assistant reply after sync, got ${updated.messages.length}.`);
                }
                const resolvedHtml = renderChatTicket();
                const renderedFollowUps = (resolvedHtml.match(/it worked, thanks!/g) || []).length;
                if (renderedFollowUps !== 1) {
                  throw new Error(`Expected the rendered transcript to show one customer follow-up, got ${renderedFollowUps}.`);
                }
                if (calls.some((entry) => entry.url === "/api/client/ack")) {
                  throw new Error("Sync follow-up flow should not call the transient client ack endpoint.");
                }
              """
            )
        )

    def test_client_same_ticket_queued_resend_supersedes_old_turn_and_requeues_latest_turn(self) -> None:
        self.run_client_app_script(
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

                const ticket = createTicket(state.user.id);
                updateTicketProduct(ticket.id, "audio_video_calling");
                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;

                let queryCount = 0;
                const cancelBodies = [];
                fetch = (url, options = undefined) => {
                  if (url === "/api/tickets/query") {
                    queryCount += 1;
                    return Promise.resolve({
                      ok: true,
                      json: async () => ({
                        ticket_id: ticket.id,
                        answer: "",
                        ai_replied: false,
                        queued_for_ai: true,
                        message_created_at: `2026-04-16T08:0${queryCount}:00.000Z`,
                        queued_message_created_at: `2026-04-16T08:0${queryCount}:00.000Z`,
                        status: "communicating",
                      }),
                    });
                  }
                  if (url === `/api/tickets/${encodeURIComponent(ticket.id)}/cancel-pending`) {
                    cancelBodies.push(JSON.parse(options.body));
                    return Promise.resolve({
                      ok: true,
                      json: async () => ({ ticket_id: ticket.id, canceled: true }),
                    });
                  }
                  throw new Error(`Unexpected fetch call to ${url}`);
                };

                await handleSendMessage("First question");
                const firstPending = getPendingSession(ticket.id);
                if (!firstPending || firstPending.phase !== "queued") {
                  throw new Error("First send should leave the ticket in queued pending state.");
                }

                await handleSendMessage("Second question");

                const updated = getTicketById(ticket.id);
                const userContents = updated.messages.filter((message) => message.role === "user").map((message) => message.content);
                if (userContents.length !== 2 || userContents[0] !== "First question" || userContents[1] !== "Second question") {
                  throw new Error(`Expected both customer turns to remain in transcript order, got ${JSON.stringify(userContents)}.`);
                }
                if (cancelBodies.length !== 1) {
                  throw new Error(`Expected exactly one silent cancel for the superseded turn, got ${cancelBodies.length}.`);
                }
                if (cancelBodies[0].message_created_at !== "2026-04-16T08:01:00.000Z") {
                  throw new Error(`Expected cancel-pending to target the old queued turn, got ${JSON.stringify(cancelBodies[0])}.`);
                }
                const supersededTurns = getSupersededTurnsForTicket(ticket.id);
                if (!supersededTurns.some((entry) => entry.createdAt === "2026-04-16T08:01:00.000Z")) {
                  throw new Error("Expected the old queued customer turn to be recorded as superseded.");
                }
                const currentPending = getPendingSession(ticket.id);
                if (!currentPending || currentPending.phase !== "queued") {
                  throw new Error("The latest turn should become the only active queued pending turn.");
                }
                if (currentPending.userMessageId === firstPending.userMessageId) {
                  throw new Error("The latest queued turn should replace the old pending anchor.");
                }
                const html = renderChatTicket();
                if (!html.includes("Second question")) {
                  throw new Error("Latest customer turn should remain visible in the transcript.");
                }
                if (!html.includes('class="composer-icon-button send-btn"')) {
                  throw new Error("Queued resend flow should keep the send button available.");
                }
                if (html.includes("composer-stop-btn")) {
                  throw new Error("Queued resend flow should not render the old stop button.");
                }
              """
            )
        )

    def test_client_late_old_reply_is_hidden_when_newer_turn_is_pending(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));
                render = () => {};

                let lastSocket = null;
                WebSocket = function WebSocket() {
                  lastSocket = this;
                  this.readyState = 1;
                  this.close = () => {};
                  this.send = () => {};
                };
                WebSocket.OPEN = 1;

                const ticket = createTicket(state.user.id);
                updateTicketProduct(ticket.id, "audio_video_calling");
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-user-1",
                    role: "user",
                    content: "First question",
                    createdAt: "2026-04-16T08:01:00.000Z",
                  },
                  {
                    id: "msg-user-2",
                    role: "user",
                    content: "Second question",
                    createdAt: "2026-04-16T08:02:00.000Z",
                  },
                ]);
                setSupersededTurnsForTicket(ticket.id, [
                  { messageId: "msg-user-1", createdAt: "2026-04-16T08:01:00.000Z" },
                ]);
                state.pendingByTicket = {
                  [ticket.id]: {
                    phase: "queued",
                    userMessageId: "msg-user-2",
                    persistedMessageCreatedAt: "2026-04-16T08:02:00.000Z",
                    queuedMessageCreatedAt: "2026-04-16T08:02:00.000Z",
                  },
                };
                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;

                let syncCallCount = 0;
                syncTicketsFromBackend = async () => {
                  syncCallCount += 1;
                  if (syncCallCount === 1) {
                    saveTicketMessages(ticket.id, [
                      {
                        id: "msg-user-1",
                        role: "user",
                        content: "First question",
                        createdAt: "2026-04-16T08:01:00.000Z",
                      },
                      {
                        id: "msg-assistant-old",
                        role: "assistant",
                        content: "Old answer that should stay hidden",
                        createdAt: "2026-04-16T08:01:05.000Z",
                      },
                      {
                        id: "msg-user-2",
                        role: "user",
                        content: "Second question",
                        createdAt: "2026-04-16T08:02:00.000Z",
                      },
                    ]);
                    return;
                  }
                  saveTicketMessages(ticket.id, [
                    {
                      id: "msg-user-1",
                      role: "user",
                      content: "First question",
                      createdAt: "2026-04-16T08:01:00.000Z",
                    },
                    {
                      id: "msg-assistant-old",
                      role: "assistant",
                      content: "Old answer that should stay hidden",
                      createdAt: "2026-04-16T08:01:05.000Z",
                    },
                    {
                      id: "msg-user-2",
                      role: "user",
                      content: "Second question",
                      createdAt: "2026-04-16T08:02:00.000Z",
                    },
                    {
                      id: "msg-assistant-new",
                      role: "assistant",
                      content: "Latest answer only",
                      createdAt: "2026-04-16T08:02:05.000Z",
                    },
                  ]);
                };

                setupClientRealtimeConnection();
                await lastSocket.onmessage({
                  data: JSON.stringify({
                    event: "ticket_ai_response_ready",
                    ticket_id: ticket.id,
                    customer_id: state.user.id,
                  }),
                });

                const staleHtml = renderChatTicket();
                if (staleHtml.includes("Old answer that should stay hidden")) {
                  throw new Error("Superseded-turn reply should not render while the latest turn is still pending.");
                }
                if (!getPendingSession(ticket.id)) {
                  throw new Error("A hidden late old reply should not clear the latest pending turn.");
                }

                await lastSocket.onmessage({
                  data: JSON.stringify({
                    event: "ticket_ai_response_ready",
                    ticket_id: ticket.id,
                    customer_id: state.user.id,
                  }),
                });

                const finalHtml = renderChatTicket();
                if (finalHtml.includes("Old answer that should stay hidden")) {
                  throw new Error("Superseded-turn reply should remain hidden after the latest durable answer arrives.");
                }
                if (!finalHtml.includes("Latest answer only")) {
                  throw new Error("The latest durable assistant answer should render.");
                }
                if (getPendingSession(ticket.id)) {
                  throw new Error("Latest durable assistant answer should clear the active pending turn.");
                }
              """
            )
        )

    def test_client_different_tickets_can_queue_concurrently(self) -> None:
        self.run_client_app_script(
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

                const ticketA = createTicket(state.user.id);
                const ticketB = createTicket(state.user.id);
                updateTicketProduct(ticketA.id, "audio_video_calling");
                updateTicketProduct(ticketB.id, "audio_video_calling");

                fetch = (url, options = undefined) => {
                  if (url === "/api/tickets/query") {
                    const body = JSON.parse(options.body);
                    return Promise.resolve({
                      ok: true,
                      json: async () => ({
                        ticket_id: body.ticket_id,
                        answer: "",
                        ai_replied: false,
                        queued_for_ai: true,
                        message_created_at: `2026-04-16T09:${body.ticket_id === ticketA.id ? "00" : "01"}:00.000Z`,
                        queued_message_created_at: `2026-04-16T09:${body.ticket_id === ticketA.id ? "00" : "01"}:00.000Z`,
                        status: "communicating",
                      }),
                    });
                  }
                  throw new Error(`Unexpected fetch call to ${url}`);
                };

                state.view = "chat-ticket";
                state.activeTicketId = ticketA.id;
                await handleSendMessage("Question on ticket A");

                state.activeTicketId = ticketB.id;
                await handleSendMessage("Question on ticket B");

                if (!state.pendingByTicket[ticketA.id] || !state.pendingByTicket[ticketB.id]) {
                  throw new Error("Both tickets should keep independent queued pending sessions.");
                }
                if (Object.keys(state.pendingByTicket).length !== 2) {
                  throw new Error(`Expected two concurrent pending tickets, got ${Object.keys(state.pendingByTicket).length}.`);
                }
              """
            )
        )

    def test_client_same_ticket_submitting_window_blocks_second_send_until_query_returns(self) -> None:
        self.run_client_app_script(
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

                const ticket = createTicket(state.user.id);
                updateTicketProduct(ticket.id, "audio_video_calling");
                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;

                let resolveQuery = null;
                fetch = (url) => {
                  if (url !== "/api/tickets/query") {
                    throw new Error(`Unexpected fetch call to ${url}`);
                  }
                  return new Promise((resolve) => {
                    resolveQuery = resolve;
                  });
                };

                const firstSend = handleSendMessage("First question");
                await Promise.resolve();

                const duringSubmitView = buildChatTicketViewState(getTicketById(ticket.id));
                if (!isTicketSubmitting(ticket.id)) {
                  throw new Error("Ticket should stay in submitting phase until the query API returns.");
                }
                if (duringSubmitView.canCompose) {
                  throw new Error("Composer should stay disabled during the initial submitting window.");
                }

                await handleSendMessage("Second question");

                const userMessages = getTicketById(ticket.id).messages.filter((message) => message.role === "user");
                if (userMessages.length !== 1 || userMessages[0].content !== "First question") {
                  throw new Error("Second send should be ignored while the initial query is still submitting.");
                }

                resolveQuery({
                  ok: true,
                  json: async () => ({
                    ticket_id: ticket.id,
                    answer: "",
                    ai_replied: false,
                    queued_for_ai: true,
                    message_created_at: "2026-04-16T10:00:00.000Z",
                    queued_message_created_at: "2026-04-16T10:00:00.000Z",
                    status: "communicating",
                  }),
                });
                await firstSend;
              """
            )
        )

    def test_client_realtime_ready_keeps_async_pending_state_without_waiting_copy_until_durable_reply_syncs(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));
                render = () => {};

                let lastSocket = null;
                WebSocket = function WebSocket() {
                  lastSocket = this;
                  this.readyState = 1;
                  this.close = () => {};
                  this.send = () => {};
                };
                WebSocket.OPEN = 1;

                const ticket = createTicket(state.user.id);
                updateTicketProduct(ticket.id, "audio_video_calling");
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-user-1",
                    role: "user",
                    content: "how to join channel",
                    createdAt: "2026-04-05T04:00:00.000Z",
                  },
                ]);

                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;
                state.isSending = true;
                state.pendingTicketId = ticket.id;
                state.pendingUserMessageId = "msg-user-1";
                state.pendingAsyncTicketId = ticket.id;
                state.pendingAsyncMessageCreatedAt = "2026-04-05T04:00:00.000Z";

                let syncCalls = 0;
                syncTicketsFromBackend = async () => {
                  syncCalls += 1;
                  if (syncCalls === 1) {
                    saveTicketMessages(ticket.id, [
                      {
                        id: "msg-user-1",
                        role: "user",
                        content: "how to join channel",
                        createdAt: "2026-04-05T04:00:00.000Z",
                      },
                    ]);
                    return;
                  }
                  saveTicketMessages(ticket.id, [
                    {
                      id: "msg-user-1",
                      role: "user",
                      content: "how to join channel",
                      createdAt: "2026-04-05T04:00:00.000Z",
                    },
                    {
                      id: "msg-assistant-1",
                      role: "assistant",
                      content: "Use joinChannel with the same channel name and token.",
                      createdAt: "2026-04-05T04:00:05.000Z",
                    },
                  ]);
                };

                const initialHtml = renderChatTicket();
                if (initialHtml.includes("I got your message and I am checking it now.")) {
                  throw new Error("Async waiting state should not render the transient reassurance bubble.");
                }
                if (initialHtml.includes("AI is cross-referencing system health logs")) {
                  throw new Error("Async waiting state should not render the thinking line before realtime updates.");
                }
                if (initialHtml.includes("checking the knowledge base... click stop to interrupt.")) {
                  throw new Error("Async waiting state should not render the composer waiting note before realtime updates.");
                }
                if (!initialHtml.includes('class="composer-icon-button send-btn"')) {
                  throw new Error("Async waiting state should keep the inline send button available.");
                }
                if (initialHtml.includes('class="composer-icon-button composer-stop-btn"')) {
                  throw new Error("Async waiting state should no longer render the stop button.");
                }

                setupClientRealtimeConnection();
                if (!lastSocket || typeof lastSocket.onmessage !== "function") {
                  throw new Error("Expected realtime connection setup to register an onmessage handler.");
                }

                await lastSocket.onmessage({
                  data: JSON.stringify({
                    event: "ticket_ai_response_ready",
                    ticket_id: ticket.id,
                    customer_id: state.user.id,
                  }),
                });

                if (!state.isSending || state.pendingAsyncTicketId !== ticket.id) {
                  throw new Error("Ready event without a durable reply should keep the async waiting state active.");
                }
                const waitingHtml = renderChatTicket();
                if (waitingHtml.includes("I got your message and I am checking it now.")) {
                  throw new Error("Realtime ready should not inject the transient reassurance bubble while waiting for sync.");
                }
                if (waitingHtml.includes("AI is cross-referencing system health logs")) {
                  throw new Error("Realtime ready should not reintroduce the thinking line while waiting for sync.");
                }
                if (waitingHtml.includes("checking the knowledge base... click stop to interrupt.")) {
                  throw new Error("Realtime ready should not reintroduce the composer waiting note while waiting for sync.");
                }
                if (!waitingHtml.includes('class="composer-icon-button send-btn"')) {
                  throw new Error("Realtime ready should keep the send button available until the durable reply arrives.");
                }
                if (waitingHtml.includes('class="composer-icon-button composer-stop-btn"')) {
                  throw new Error("Realtime ready should not reintroduce the old stop button.");
                }
                if (waitingHtml.includes("Use joinChannel with the same channel name and token.")) {
                  throw new Error("First ready event should not invent a durable reply before backend sync has it.");
                }

                await lastSocket.onmessage({
                  data: JSON.stringify({
                    event: "ticket_ai_response_ready",
                    ticket_id: ticket.id,
                    customer_id: state.user.id,
                  }),
                });

                if (state.isSending || state.pendingAsyncTicketId) {
                  throw new Error("Pending async state should clear once the durable reply is actually available.");
                }
                const resolvedHtml = renderChatTicket();
                if (!resolvedHtml.includes("Use joinChannel with the same channel name and token.")) {
                  throw new Error("Durable reply should render after the sync that finally includes it.");
                }
                if (resolvedHtml.includes("I got your message and I am checking it now.")) {
                  throw new Error("Durable reply should not coexist with a transient reassurance bubble.");
                }
                if (resolvedHtml.includes('class="composer-icon-button composer-stop-btn"')) {
                  throw new Error("Stop button should remain absent once the durable reply is available.");
                }
              """
            )
        )

    def test_client_realtime_generation_stopped_clears_pending_state_without_waiting_copy(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));
                render = () => {};

                let lastSocket = null;
                WebSocket = function WebSocket() {
                  lastSocket = this;
                  this.readyState = 1;
                  this.close = () => {};
                  this.send = () => {};
                };
                WebSocket.OPEN = 1;

                const ticket = createTicket(state.user.id);
                updateTicketProduct(ticket.id, "audio_video_calling");
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-user-1",
                    role: "user",
                    content: "how to join channel",
                    createdAt: "2026-04-05T04:00:00.000Z",
                  },
                ]);

                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;
                state.isSending = true;
                state.pendingTicketId = ticket.id;
                state.pendingUserMessageId = "msg-user-1";
                state.pendingAsyncTicketId = ticket.id;
                state.pendingAsyncMessageCreatedAt = "2026-04-05T04:00:00.000Z";

                syncTicketsFromBackend = async () => {};

                setupClientRealtimeConnection();
                if (!lastSocket || typeof lastSocket.onmessage !== "function") {
                  throw new Error("Expected realtime connection setup to register an onmessage handler.");
                }

                await lastSocket.onmessage({
                  data: JSON.stringify({
                    event: "ticket_ai_generation_stopped",
                    ticket_id: ticket.id,
                    customer_id: state.user.id,
                  }),
                });

                if (state.isSending || state.pendingAsyncTicketId || state.pendingTicketId) {
                  throw new Error("Generation stopped should clear the pending async request state.");
                }
                const html = renderChatTicket();
                if (html.includes("I got your message and I am checking it now.")) {
                  throw new Error("Generation stopped should not leave the reassurance bubble stuck in the transcript.");
                }
                if (html.includes("AI is cross-referencing system health logs")) {
                  throw new Error("Generation stopped should remove the waiting indicator.");
                }
                if (html.includes("checking the knowledge base... click stop to interrupt.")) {
                  throw new Error("Generation stopped should remove the composer waiting note.");
                }
              """
            )
        )

    def test_client_sync_preserves_pending_follow_up_customer_message_until_backend_catches_up(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));
                render = () => {};
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

                const ticket = createTicket(state.user.id);
                updateTicketProduct(ticket.id, "audio_video_calling");
                updateTicketTitle(ticket.id, "how to join channel");
                updateTicketStatus(ticket.id, "communicating");
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-user-1",
                    role: "user",
                    content: "how to join channel",
                    createdAt: "2026-04-05T04:00:00.000Z",
                  },
                  {
                    id: "msg-assistant-1",
                    role: "assistant",
                    content: "Use joinChannel with the same channel name and token.",
                    createdAt: "2026-04-05T04:00:05.000Z",
                  },
                ]);

                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;

                let ticketSyncCallCount = 0;
                fetch = (url, options = undefined) => {
                  if (url === "/api/client/ack") {
                    return new Promise(() => {});
                  }
                  if (url === "/api/tickets/query") {
                    return Promise.resolve({
                      ok: true,
                      json: async () => ({
                        ticket_id: ticket.id,
                        answer: "",
                        ai_replied: false,
                        queued_for_ai: true,
                        queued_message_created_at: "2026-04-05T04:01:00.000Z",
                        ack_source: "client_model",
                        processing_mode: "main_agent_async",
                        status: "communicating",
                      }),
                    });
                  }
                  if (url === `/api/tickets?customer_id=${encodeURIComponent(state.user.id)}&status=all`) {
                    ticketSyncCallCount += 1;
                    if (ticketSyncCallCount === 1) {
                      return Promise.resolve({
                        ok: true,
                        json: async () => ({
                          tickets: [
                            {
                              ticket_id: ticket.id,
                              customer_id: state.user.id,
                              subject: "how to join channel",
                              status: "communicating",
                              product: "audio_video_calling",
                              created_at: "2026-04-05T04:00:00.000Z",
                              updated_at: "2026-04-05T04:00:05.000Z",
                              messages: [
                                {
                                  role: "customer",
                                  content: "how to join channel",
                                  created_at: "2026-04-05T04:00:00.000Z",
                                },
                                {
                                  role: "assistant",
                                  content: "Use joinChannel with the same channel name and token.",
                                  created_at: "2026-04-05T04:00:05.000Z",
                                },
                              ],
                            },
                          ],
                        }),
                      });
                    }
                    if (ticketSyncCallCount === 2) {
                      return Promise.resolve({
                        ok: true,
                        json: async () => ({
                          tickets: [
                            {
                              ticket_id: ticket.id,
                              customer_id: state.user.id,
                              subject: "how to join channel",
                              status: "communicating",
                              product: "audio_video_calling",
                              created_at: "2026-04-05T04:00:00.000Z",
                              updated_at: "2026-04-05T04:01:00.000Z",
                              messages: [
                                {
                                  role: "customer",
                                  content: "how to join channel",
                                  created_at: "2026-04-05T04:00:00.000Z",
                                },
                                {
                                  role: "assistant",
                                  content: "Use joinChannel with the same channel name and token.",
                                  created_at: "2026-04-05T04:00:05.000Z",
                                },
                                {
                                  role: "customer",
                                  content: "what about token renewal",
                                  created_at: "2026-04-05T04:01:00.000Z",
                                },
                              ],
                            },
                          ],
                        }),
                      });
                    }
                    return Promise.resolve({
                      ok: true,
                      json: async () => ({
                        tickets: [
                          {
                            ticket_id: ticket.id,
                            customer_id: state.user.id,
                            subject: "how to join channel",
                            status: "communicating",
                            product: "audio_video_calling",
                            created_at: "2026-04-05T04:00:00.000Z",
                            updated_at: "2026-04-05T04:01:05.000Z",
                            messages: [
                              {
                                role: "customer",
                                content: "how to join channel",
                                created_at: "2026-04-05T04:00:00.000Z",
                              },
                              {
                                role: "assistant",
                                content: "Use joinChannel with the same channel name and token.",
                                created_at: "2026-04-05T04:00:05.000Z",
                              },
                              {
                                role: "customer",
                                content: "what about token renewal",
                                created_at: "2026-04-05T04:01:00.000Z",
                              },
                              {
                                role: "assistant",
                                content: "Check the token expiry callback and renewal timing.",
                                created_at: "2026-04-05T04:01:05.000Z",
                              },
                            ],
                          },
                        ],
                      }),
                    });
                  }
                  throw new Error(`Unexpected fetch call to ${url}`);
                };

                await handleSendMessage("what about token renewal");

                const afterStaleSync = getTicketById(ticket.id);
                if (!afterStaleSync) {
                  throw new Error("Expected ticket to remain available after the stale sync.");
                }
                const staleFollowUps = afterStaleSync.messages.filter(
                  (message) => message.role === "user" && message.content === "what about token renewal"
                );
                if (staleFollowUps.length !== 1) {
                  throw new Error("Stale sync should preserve the just-sent follow-up customer message.");
                }
                if (ticketHasAssistantReply(afterStaleSync)) {
                  throw new Error("Stale sync should still treat the newest follow-up customer turn as pending.");
                }
                const staleHtml = renderChatTicket();
                if (!staleHtml.includes("what about token renewal")) {
                  throw new Error("The just-sent follow-up should stay visible after a stale sync response.");
                }
                if (staleHtml.includes("Check the token expiry callback and renewal timing.")) {
                  throw new Error("Stale sync should not invent the final assistant reply.");
                }

                await syncTicketsFromBackend({ silent: true });

                const afterCatchUpSync = getTicketById(ticket.id);
                const catchUpFollowUps = afterCatchUpSync.messages.filter(
                  (message) => message.role === "user" && message.content === "what about token renewal"
                );
                if (catchUpFollowUps.length !== 1) {
                  throw new Error("Backend catch-up sync should replace the optimistic customer turn without duplicating it.");
                }
                if (catchUpFollowUps[0].createdAt !== "2026-04-05T04:01:00.000Z") {
                  throw new Error("Once backend catches up, the persisted customer timestamp should become canonical.");
                }
                const catchUpHtml = renderChatTicket();
                if (!catchUpHtml.includes("what about token renewal")) {
                  throw new Error("Backend catch-up sync should keep the follow-up customer message visible.");
                }

                await syncTicketsFromBackend({ silent: true });

                const afterFinalSync = getTicketById(ticket.id);
                const finalFollowUps = afterFinalSync.messages.filter(
                  (message) => message.role === "user" && message.content === "what about token renewal"
                );
                if (finalFollowUps.length !== 1) {
                  throw new Error("Final sync should still show only one copy of the follow-up customer message.");
                }
                if (
                  !afterFinalSync.messages.some(
                    (message) =>
                      message.role === "assistant" &&
                      message.content === "Check the token expiry callback and renewal timing."
                  )
                ) {
                  throw new Error("Final sync should render the durable assistant reply.");
                }
                const finalHtml = renderChatTicket();
                if (!finalHtml.includes("what about token renewal")) {
                  throw new Error("The follow-up customer message should remain visible alongside the final reply.");
                }
                if (!finalHtml.includes("Check the token expiry callback and renewal timing.")) {
                  throw new Error("The final durable assistant reply should render after backend catch-up.");
                }
              """
            )
        )

    def test_client_chat_hides_legacy_assistant_reassurance_when_a_later_reply_exists(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;
                updateTicketStatus(ticket.id, "investigating");
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-user-1",
                    role: "user",
                    content: "how to join channel",
                    createdAt: "2026-04-04T04:45:46.947235Z",
                  },
                  {
                    id: "msg-assistant-1",
                    role: "assistant",
                    content: "Got it, let me check this for you.",
                    createdAt: "2026-04-04T04:45:46.947758Z",
                  },
                  {
                    id: "msg-assistant-2",
                    role: "assistant",
                    content: "This issue requires further internal investigation, which may take some time. Thank you for your patience. We expect to reply here within 24 hours.",
                    createdAt: "2026-04-04T04:46:28.952143Z",
                  },
                ]);

                const html = renderChatTicket();
                if (html.includes("Got it, let me check this for you.")) {
                  throw new Error("Legacy assistant reassurance should be hidden once a later assistant reply exists in history.");
                }
                if (!html.includes("requires further internal investigation")) {
                  throw new Error("Later durable assistant reply should remain visible.");
                }
                """
            )
        )

    def test_client_chat_renders_markdown_code_blocks_and_references_for_grounded_answer(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;
                updateTicketStatus(ticket.id, "communicating");
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-user-1",
                    role: "user",
                    content: "how to join channel",
                    createdAt: "2026-04-13T06:00:51.182857Z",
                  },
                  {
                    id: "msg-assistant-1",
                    role: "assistant",
                    content: [
                      "To join a channel, call the SDK join method with your channel name, authentication token, user ID, and channel/media options.",
                      "",
                      "Key Steps:",
                      "1. Provide the channel name you want the client to join.",
                      "2. Pass a valid authentication token from your token server.",
                      "3. Set the local user ID.",
                      "",
                      "```kotlin",
                      "val channelId = \\\"demo-room\\\"",
                      "val uid = 0",
                      "engine.joinChannel(token, channelId, uid, option)",
                      "```",
                    ].join("\\n"),
                    createdAt: "2026-04-13T06:01:56.666315Z",
                    citations: [
                      {
                        chunk_id: "chunk-join-auth",
                        source_url: "https://docs.agora.io/en/video-calling/token-authentication/authentication-workflow?platform=android",
                        source_path: "official/authentication-workflow_android.md",
                        heading: "Use a token to join a channel",
                      },
                    ],
                    sources: [
                      "https://docs.agora.io/en/video-calling/token-authentication/authentication-workflow?platform=android",
                    ],
                  },
                ]);

                const html = renderChatTicket();
                if (!html.includes("<ol>")) {
                  throw new Error("Grounded assistant markdown should render ordered lists.");
                }
                if (!html.includes("<pre><code")) {
                  throw new Error("Grounded assistant markdown should render fenced code blocks.");
                }
                if (!html.includes("References")) {
                  throw new Error("Grounded assistant answers should render a References section.");
                }
                if (!html.includes('href="https://docs.agora.io/en/video-calling/token-authentication/authentication-workflow?platform=android"')) {
                  throw new Error("Grounded assistant answers should render clickable reference links.");
                }
              """
            )
        )

    def test_client_chat_keeps_customer_message_even_if_it_matches_legacy_reassurance_text(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;
                updateTicketStatus(ticket.id, "communicating");
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-user-1",
                    role: "user",
                    content: "Got it, let me check this for you.",
                    createdAt: "2026-04-05T04:45:46.947235Z",
                  },
                  {
                    id: "msg-assistant-1",
                    role: "assistant",
                    content: "Use the joinChannel method after you obtain a token.",
                    createdAt: "2026-04-05T04:46:28.952143Z",
                  },
                ]);

                const html = renderChatTicket();
                if (!html.includes("Got it, let me check this for you.")) {
                  throw new Error("Customer-authored messages should not be hidden just because they match a legacy reassurance template.");
                }
                if (!html.includes("Use the joinChannel method after you obtain a token.")) {
                  throw new Error("Assistant reply should remain visible alongside the matching customer message.");
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

                state.pendingByTicket = {
                  [ticket.id]: {
                    phase: "queued",
                    userMessageId: "msg-user-1",
                    queuedMessageCreatedAt: "2026-04-15T09:00:00.000Z",
                  },
                };

                const sendingHtml = renderChatTicket();
                if (!sendingHtml.includes('class="composer-icon-button send-btn"')) {
                  throw new Error("Queued state should still render an inline send icon button.");
                }
                if (sendingHtml.includes('data-action="stop-generation"')) {
                  throw new Error("Queued state should not render the legacy stop-generation action.");
                }
                if (sendingHtml.includes('class="composer-icon-button composer-stop-btn"')) {
                  throw new Error("Queued state should not render the legacy stop icon button.");
                }
              """
            )
        )

    def test_client_chat_preserves_scroll_during_same_ticket_rerenders(self) -> None:
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

                const shellRegions = {
                  '[data-authed-region="sidebar-nav"]': { innerHTML: "" },
                  '[data-authed-region="sidebar-content"]': { innerHTML: "" },
                  '[data-authed-region="sidebar-footer"]': { innerHTML: "" },
                  '[data-authed-region="topbar"]': { innerHTML: "" },
                  '[data-authed-region="context"]': { innerHTML: "" },
                };
                let currentChatMain = null;
                const renderHeights = [];
                const mainRegion = {};
                Object.defineProperty(mainRegion, "innerHTML", {
                  get() {
                    return this._html || "";
                  },
                  set(value) {
                    this._html = value;
                    currentChatMain = {
                      scrollTop: 0,
                      scrollHeight: renderHeights.shift() ?? 0,
                    };
                  },
                });
                shellRegions['[data-authed-region="main"]'] = mainRegion;

                const shell = {
                  querySelector(selector) {
                    return shellRegions[selector] || null;
                  },
                };
                const fakeForm = {
                  addEventListener() {},
                  requestSubmit() {},
                };
                const fakeInput = {
                  addEventListener() {},
                  value: "",
                };

                document.getElementById = (id) => {
                  if (id === "app") {
                    return appRoot;
                  }
                  if (id === "chat-input-form") {
                    return fakeForm;
                  }
                  if (id === "chat-input") {
                    return fakeInput;
                  }
                  return null;
                };
                appRoot.querySelectorAll = () => [];
                appRoot.querySelector = (selector) => {
                  if (selector === ".app-shell") {
                    return shell;
                  }
                  if (selector === ".chat-main") {
                    return currentChatMain;
                  }
                  return null;
                };

                window.location.hash = `#/chat/${ticket.id}`;

                renderHeights.push(180);
                render();
                flushFrames();
                if (currentChatMain.scrollTop !== 180) {
                  throw new Error(`Expected opening a ticket to scroll to bottom, got ${currentChatMain.scrollTop}.`);
                }

                currentChatMain.scrollTop = 72;
                updateTicketStatus(ticket.id, "communicating");
                renderHeights.push(180);
                render();
                flushFrames();
                if (currentChatMain.scrollTop !== 72) {
                  throw new Error(
                    `Expected same-ticket rerender to preserve scrollTop 72, got ${currentChatMain.scrollTop}.`
                  );
                }

                state.pendingByTicket = {
                  [ticket.id]: {
                    phase: "queued",
                    userMessageId: "msg-pending-1",
                    queuedMessageCreatedAt: "2026-03-31T15:25:00.000Z",
                  },
                };
                currentChatMain.scrollTop = 41;
                renderHeights.push(220);
                render();
                flushFrames();
                if (mainRegion.innerHTML.includes("thinking-line")) {
                  throw new Error("Waiting-state rerender should no longer render the thinking line.");
                }
                if (mainRegion.innerHTML.includes("checking the knowledge base... click stop to interrupt.")) {
                  throw new Error("Waiting-state rerender should no longer render the composer waiting note.");
                }
                if (!mainRegion.innerHTML.includes("send-btn")) {
                  throw new Error("Waiting-state rerender should keep the inline send button available.");
                }
                if (mainRegion.innerHTML.includes("composer-stop-btn")) {
                  throw new Error("Waiting-state rerender should not render the legacy stop button.");
                }
                if (currentChatMain.scrollTop !== 41) {
                  throw new Error(
                    `Expected waiting-state rerender to preserve scrollTop 41, got ${currentChatMain.scrollTop}.`
                  );
                }

                state.pendingByTicket = {};
                currentChatMain.scrollTop = 38;
                saveTicketMessages(ticket.id, [
                  ...getTicketById(ticket.id).messages,
                  {
                    id: "msg-2",
                    role: "assistant",
                    content: "Here is the final answer",
                    createdAt: "2026-03-31T15:25:00.000Z",
                  },
                ]);
                renderHeights.push(420);
                render();
                flushFrames();
                if (currentChatMain.scrollTop !== 38) {
                  throw new Error(
                    `Expected assistant reply rerender to preserve scrollTop 38, got ${currentChatMain.scrollTop}.`
                  );
                }
              """
            )
        )

    def test_client_chat_scrolls_to_latest_when_requested(self) -> None:
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

                const shellRegions = {
                  '[data-authed-region="sidebar-nav"]': { innerHTML: "" },
                  '[data-authed-region="sidebar-content"]': { innerHTML: "" },
                  '[data-authed-region="sidebar-footer"]': { innerHTML: "" },
                  '[data-authed-region="topbar"]': { innerHTML: "" },
                  '[data-authed-region="context"]': { innerHTML: "" },
                };
                let currentChatMain = null;
                const renderHeights = [];
                const mainRegion = {};
                Object.defineProperty(mainRegion, "innerHTML", {
                  get() {
                    return this._html || "";
                  },
                  set(value) {
                    this._html = value;
                    currentChatMain = {
                      scrollTop: 0,
                      scrollHeight: renderHeights.shift() ?? 0,
                    };
                  },
                });
                shellRegions['[data-authed-region="main"]'] = mainRegion;

                const shell = {
                  querySelector(selector) {
                    return shellRegions[selector] || null;
                  },
                };
                const fakeForm = {
                  addEventListener() {},
                  requestSubmit() {},
                };
                const fakeInput = {
                  addEventListener() {},
                  value: "",
                };

                document.getElementById = (id) => {
                  if (id === "app") {
                    return appRoot;
                  }
                  if (id === "chat-input-form") {
                    return fakeForm;
                  }
                  if (id === "chat-input") {
                    return fakeInput;
                  }
                  return null;
                };
                appRoot.querySelectorAll = () => [];
                appRoot.querySelector = (selector) => {
                  if (selector === ".app-shell") {
                    return shell;
                  }
                  if (selector === ".chat-main") {
                    return currentChatMain;
                  }
                  return null;
                };

                window.location.hash = `#/chat/${ticket.id}`;

                renderHeights.push(180);
                render();
                flushFrames();
                if (currentChatMain.scrollTop !== 180) {
                  throw new Error(`Expected opening a ticket to scroll to bottom, got ${currentChatMain.scrollTop}.`);
                }

                currentChatMain.scrollTop = 64;
                saveTicketMessages(ticket.id, [
                  ...getTicketById(ticket.id).messages,
                  {
                    id: "msg-2",
                    role: "user",
                    content: "Follow-up question",
                    createdAt: "2026-03-31T15:25:00.000Z",
                  },
                ]);
                requestChatScrollToBottom(ticket.id);
                renderHeights.push(420);
                render();
                flushFrames();
                if (currentChatMain.scrollTop !== 420) {
                  throw new Error(`Expected requested scroll-to-latest to land at 420, got ${currentChatMain.scrollTop}.`);
                }
              """
            )
        )

    def test_client_chat_remote_reply_shows_new_messages_when_scrolled_up(self) -> None:
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

                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-1",
                    role: "user",
                    content: "How do I enable dual stream?",
                    createdAt: "2026-04-15T09:00:00.000Z",
                  },
                ]);

                const shellRegions = {
                  '[data-authed-region="sidebar-nav"]': { innerHTML: "" },
                  '[data-authed-region="sidebar-content"]': { innerHTML: "" },
                  '[data-authed-region="sidebar-footer"]': { innerHTML: "" },
                  '[data-authed-region="topbar"]': { innerHTML: "" },
                  '[data-authed-region="context"]': { innerHTML: "" },
                };
                let currentChatMain = null;
                let scrollCalls = [];
                const renderHeights = [];
                const mainRegion = {};
                Object.defineProperty(mainRegion, "innerHTML", {
                  get() {
                    return this._html || "";
                  },
                  set(value) {
                    this._html = value;
                    const previousScrollTop = currentChatMain?.scrollTop || 0;
                    currentChatMain = {
                      scrollTop: previousScrollTop,
                      scrollHeight: renderHeights.shift() ?? 0,
                      clientHeight: 160,
                      scrollTo(options) {
                        scrollCalls.push(options);
                        this.scrollTop = typeof options?.top === "number" ? options.top : this.scrollTop;
                      },
                    };
                  },
                });
                shellRegions['[data-authed-region="main"]'] = mainRegion;

                const shell = {
                  querySelector(selector) {
                    return shellRegions[selector] || null;
                  },
                };
                const fakeForm = {
                  addEventListener() {},
                  requestSubmit() {},
                };
                const fakeInput = {
                  addEventListener() {},
                  value: "",
                };

                document.getElementById = (id) => {
                  if (id === "app") {
                    return appRoot;
                  }
                  if (id === "chat-input-form") {
                    return fakeForm;
                  }
                  if (id === "chat-input") {
                    return fakeInput;
                  }
                  return null;
                };
                appRoot.querySelectorAll = () => [];
                appRoot.querySelector = (selector) => {
                  if (selector === ".app-shell") {
                    return shell;
                  }
                  if (selector === ".chat-main") {
                    return currentChatMain;
                  }
                  return null;
                };

                window.location.hash = `#/chat/${ticket.id}`;

                renderHeights.push(360);
                render();
                flushFrames();

                currentChatMain.scrollTop = 8;
                saveTicketMessages(ticket.id, [
                  ...getTicketById(ticket.id).messages,
                  {
                    id: "msg-2",
                    role: "assistant",
                    content: "Enable dual stream from the SDK config.",
                    createdAt: "2026-04-15T09:01:00.000Z",
                  },
                ]);
                renderHeights.push(420);
                render();
                flushFrames();

                if (currentChatMain.scrollTop !== 8) {
                  throw new Error(`Expected stale-up view to preserve scrollTop 8, got ${currentChatMain.scrollTop}.`);
                }
                const latestCall = scrollCalls[scrollCalls.length - 1];
                if (!latestCall || latestCall.top !== 8) {
                  throw new Error(`Expected the follow-up rerender to restore scrollTop 8, got ${JSON.stringify(latestCall)}.`);
                }
                if (latestCall.behavior === "smooth") {
                  throw new Error("Scrolled-up remote replies should restore position, not smooth-scroll to bottom.");
                }
                if (!mainRegion.innerHTML.includes("New messages")) {
                  throw new Error("Expected a New messages indicator when a remote reply arrives while scrolled up.");
                }
              """
            )
        )

    def test_client_chat_clicking_new_messages_smooth_scrolls_to_latest(self) -> None:
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

                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-1",
                    role: "user",
                    content: "Need help with a follow-up",
                    createdAt: "2026-04-15T09:00:00.000Z",
                  },
                ]);

                const shellRegions = {
                  '[data-authed-region="sidebar-nav"]': { innerHTML: "" },
                  '[data-authed-region="sidebar-content"]': { innerHTML: "" },
                  '[data-authed-region="sidebar-footer"]': { innerHTML: "" },
                  '[data-authed-region="topbar"]': { innerHTML: "" },
                  '[data-authed-region="context"]': { innerHTML: "" },
                };
                let currentChatMain = null;
                const scrollCalls = [];
                const renderHeights = [];
                let jumpButton = null;
                const mainRegion = {};
                Object.defineProperty(mainRegion, "innerHTML", {
                  get() {
                    return this._html || "";
                  },
                  set(value) {
                    this._html = value;
                    const previousScrollTop = currentChatMain?.scrollTop || 0;
                    currentChatMain = {
                      scrollTop: previousScrollTop,
                      scrollHeight: renderHeights.shift() ?? 0,
                      clientHeight: 160,
                      scrollTo(options) {
                        scrollCalls.push(options);
                        this.scrollTop = typeof options?.top === "number" ? options.top : this.scrollTop;
                      },
                    };
                    jumpButton = value.includes('data-action="jump-chat-latest"')
                      ? {
                          addEventListener(type, handler) {
                            if (type === "click") {
                              this.click = handler;
                            }
                          },
                        }
                      : null;
                  },
                });
                shellRegions['[data-authed-region="main"]'] = mainRegion;

                const shell = {
                  querySelector(selector) {
                    return shellRegions[selector] || null;
                  },
                };
                const fakeForm = {
                  addEventListener() {},
                  requestSubmit() {},
                };
                const fakeInput = {
                  addEventListener() {},
                  value: "",
                };

                document.getElementById = (id) => {
                  if (id === "app") {
                    return appRoot;
                  }
                  if (id === "chat-input-form") {
                    return fakeForm;
                  }
                  if (id === "chat-input") {
                    return fakeInput;
                  }
                  return null;
                };
                appRoot.querySelectorAll = (selector) => {
                  if (selector === "[data-action='jump-chat-latest']") {
                    return jumpButton ? [jumpButton] : [];
                  }
                  return [];
                };
                appRoot.querySelector = (selector) => {
                  if (selector === ".app-shell") {
                    return shell;
                  }
                  if (selector === ".chat-main") {
                    return currentChatMain;
                  }
                  if (selector === "[data-action='jump-chat-latest']") {
                    return jumpButton;
                  }
                  return null;
                };

                window.location.hash = `#/chat/${ticket.id}`;

                renderHeights.push(360);
                render();
                flushFrames();

                currentChatMain.scrollTop = 4;
                saveTicketMessages(ticket.id, [
                  ...getTicketById(ticket.id).messages,
                  {
                    id: "msg-2",
                    role: "assistant",
                    content: "Here is the latest assistant reply.",
                    createdAt: "2026-04-15T09:01:00.000Z",
                  },
                ]);
                renderHeights.push(430);
                render();
                flushFrames();

                if (!jumpButton || typeof jumpButton.click !== "function") {
                  throw new Error("Expected the New messages button to bind a click handler.");
                }

                renderHeights.push(430);
                jumpButton.click();
                flushFrames();

                if (scrollCalls.length < 2) {
                  throw new Error(`Expected a smooth scroll after clicking New messages, got ${scrollCalls.length} calls.`);
                }
                const latestCall = scrollCalls[scrollCalls.length - 1];
                if (latestCall.behavior !== "smooth") {
                  throw new Error(`Expected smooth scroll behavior, got ${JSON.stringify(latestCall)}.`);
                }
                if (currentChatMain.scrollTop !== 430) {
                  throw new Error(`Expected scroll-to-latest to land at 430, got ${currentChatMain.scrollTop}.`);
                }
                if (mainRegion.innerHTML.includes("New messages")) {
                  throw new Error("Expected the New messages indicator to clear after jumping to latest.");
                }
              """
            )
        )

    def test_client_chat_user_send_forces_smooth_scroll_even_when_scrolled_up(self) -> None:
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

                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-1",
                    role: "assistant",
                    content: "Earlier assistant answer",
                    createdAt: "2026-04-15T09:00:00.000Z",
                  },
                ]);

                const shellRegions = {
                  '[data-authed-region="sidebar-nav"]': { innerHTML: "" },
                  '[data-authed-region="sidebar-content"]': { innerHTML: "" },
                  '[data-authed-region="sidebar-footer"]': { innerHTML: "" },
                  '[data-authed-region="topbar"]': { innerHTML: "" },
                  '[data-authed-region="context"]': { innerHTML: "" },
                };
                let currentChatMain = null;
                const scrollCalls = [];
                const renderHeights = [];
                const mainRegion = {};
                Object.defineProperty(mainRegion, "innerHTML", {
                  get() {
                    return this._html || "";
                  },
                  set(value) {
                    this._html = value;
                    const previousScrollTop = currentChatMain?.scrollTop || 0;
                    currentChatMain = {
                      scrollTop: previousScrollTop,
                      scrollHeight: renderHeights.shift() ?? 0,
                      clientHeight: 160,
                      scrollTo(options) {
                        scrollCalls.push(options);
                        this.scrollTop = typeof options?.top === "number" ? options.top : this.scrollTop;
                      },
                    };
                  },
                });
                shellRegions['[data-authed-region="main"]'] = mainRegion;

                const shell = {
                  querySelector(selector) {
                    return shellRegions[selector] || null;
                  },
                };
                const fakeForm = {
                  addEventListener() {},
                  requestSubmit() {},
                };
                const fakeInput = {
                  addEventListener() {},
                  value: "",
                };

                fetch = async () => ({
                  ok: true,
                  json: async () => ({ queued_for_ai: true, queued_message_created_at: "2026-04-15T09:01:00.000Z" }),
                });
                syncTicketsFromBackend = async () => {};
                startClientAck = async () => {};
                ensurePendingStatusPolling = () => {};

                document.getElementById = (id) => {
                  if (id === "app") {
                    return appRoot;
                  }
                  if (id === "chat-input-form") {
                    return fakeForm;
                  }
                  if (id === "chat-input") {
                    return fakeInput;
                  }
                  return null;
                };
                appRoot.querySelectorAll = () => [];
                appRoot.querySelector = (selector) => {
                  if (selector === ".app-shell") {
                    return shell;
                  }
                  if (selector === ".chat-main") {
                    return currentChatMain;
                  }
                  return null;
                };

                window.location.hash = `#/chat/${ticket.id}`;

                renderHeights.push(360);
                render();
                flushFrames();

                currentChatMain.scrollTop = 12;
                renderHeights.push(420);
                renderHeights.push(420);
                await handleSendMessage("Second-round follow-up", {});
                flushFrames();

                const latestCall = scrollCalls[scrollCalls.length - 1];
                if (!latestCall) {
                  throw new Error("Expected sending to request a scroll-to-latest call.");
                }
                if (latestCall.behavior !== "smooth") {
                  throw new Error(`Expected sending to use smooth scrolling, got ${JSON.stringify(latestCall)}.`);
                }
                if (currentChatMain.scrollTop !== 420) {
                  throw new Error(`Expected sending to scroll to 420, got ${currentChatMain.scrollTop}.`);
                }
              """
            )
        )

    def test_client_chat_same_ticket_refresh_preserves_active_second_round_composer(self) -> None:
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

                state.user = { id: "user-1", name: "Zac", email: "zac@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-1",
                    role: "assistant",
                    content: "First answer",
                    createdAt: "2026-04-14T10:00:00.000Z",
                  },
                ]);

                const shellRegions = {
                  '[data-authed-region="sidebar-nav"]': { innerHTML: "" },
                  '[data-authed-region="sidebar-content"]': { innerHTML: "" },
                  '[data-authed-region="sidebar-footer"]': { innerHTML: "" },
                  '[data-authed-region="topbar"]': { innerHTML: "" },
                  '[data-authed-region="context"]': { innerHTML: "" },
                };
                let currentChatMain = null;
                let currentChatInput = null;
                let currentChatForm = null;
                let activeElement = null;
                let messagesRegion = { innerHTML: "" };
                let noteRegion = { innerHTML: "" };
                let actionRegion = { innerHTML: "" };
                let chatRoot = null;
                const renderHeights = [];
                const makeTextarea = () => ({
                  value: "",
                  disabled: false,
                  selectionStart: 0,
                  selectionEnd: 0,
                  selectionDirection: "none",
                  scrollTop: 0,
                  addEventListener() {},
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
                });
                const mainRegion = {
                  querySelector(selector) {
                    if (selector === ".chat-root") {
                      return chatRoot;
                    }
                    if (selector === ".chat-main") {
                      return currentChatMain;
                    }
                    if (selector === "#chat-input") {
                      return currentChatInput;
                    }
                    if (selector === "#chat-input-form") {
                      return currentChatForm;
                    }
                    return null;
                  },
                };
                Object.defineProperty(mainRegion, "innerHTML", {
                  get() {
                    return this._html || "";
                  },
                  set(value) {
                    this._html = value;
                    currentChatMain = {
                      scrollTop: currentChatMain?.scrollTop || 0,
                      scrollHeight: renderHeights.shift() ?? 0,
                    };
                    messagesRegion = { innerHTML: value };
                    noteRegion = { innerHTML: "" };
                    actionRegion = { innerHTML: "" };
                    currentChatForm = {
                      addEventListener() {},
                      requestSubmit() {},
                    };
                    if (value.includes('id="chat-input"')) {
                      chatRoot = {
                        dataset: { chatTicketId: ticket.id },
                        querySelector(selector) {
                          if (selector === '[data-chat-section="messages"]') {
                            return messagesRegion;
                          }
                          if (selector === '[data-chat-section="composer-note"]') {
                            return noteRegion;
                          }
                          if (selector === '[data-chat-section="composer-action"]') {
                            return actionRegion;
                          }
                          return null;
                        },
                      };
                      currentChatInput = makeTextarea();
                      currentChatInput.value = state.inputDraft || "";
                      if (activeElement && activeElement !== currentChatInput) {
                        activeElement = null;
                      }
                    } else {
                      chatRoot = null;
                      currentChatInput = null;
                      activeElement = null;
                    }
                  },
                });
                shellRegions['[data-authed-region="main"]'] = mainRegion;

                const shell = {
                  querySelector(selector) {
                    return shellRegions[selector] || null;
                  },
                };

                document.getElementById = (id) => {
                  if (id === "app") {
                    return appRoot;
                  }
                  if (id === "chat-input-form") {
                    return currentChatForm;
                  }
                  if (id === "chat-input") {
                    return currentChatInput;
                  }
                  return null;
                };
                Object.defineProperty(document, "activeElement", {
                  configurable: true,
                  get() {
                    return activeElement;
                  },
                });
                appRoot.querySelectorAll = () => [];
                appRoot.querySelector = (selector) => {
                  if (selector === ".app-shell") {
                    return shell;
                  }
                  if (selector === ".chat-main") {
                    return currentChatMain;
                  }
                  return null;
                };

                window.location.hash = `#/chat/${ticket.id}`;

                renderHeights.push(180);
                render();
                flushFrames();

                state.inputDraft = "第二轮 follow-up message";
                currentChatInput.value = state.inputDraft;
                currentChatInput.focus();
                currentChatInput.setSelectionRange(5, 5, "none");
                currentChatInput.scrollTop = 14;
                const originalInput = currentChatInput;

                saveTicketMessages(ticket.id, [
                  ...getTicketById(ticket.id).messages,
                  {
                    id: "msg-2",
                    role: "assistant",
                    content: "Background refresh reply",
                    createdAt: "2026-04-14T10:01:00.000Z",
                  },
                ]);
                renderHeights.push(240);
                render();
                flushFrames();

                if (currentChatInput !== originalInput) {
                  throw new Error("Same-ticket refresh should preserve the active chat composer instance.");
                }
                if (document.activeElement !== currentChatInput) {
                  throw new Error("Same-ticket refresh should keep focus on the active chat composer.");
                }
                if (currentChatInput.selectionStart !== 5 || currentChatInput.selectionEnd !== 5) {
                  throw new Error("Same-ticket refresh should preserve the chat composer cursor position.");
                }
                if (currentChatInput.scrollTop !== 14) {
                  throw new Error("Same-ticket refresh should preserve the chat composer scroll position.");
                }
                if (currentChatInput.value !== "第二轮 follow-up message") {
                  throw new Error("Same-ticket refresh should preserve the in-progress second-round draft.");
                }
              """
            )
        )

    def test_client_chat_preserves_send_scroll_when_reply_rerenders_before_next_frame(self) -> None:
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

                const shellRegions = {
                  '[data-authed-region="sidebar-nav"]': { innerHTML: "" },
                  '[data-authed-region="sidebar-content"]': { innerHTML: "" },
                  '[data-authed-region="sidebar-footer"]': { innerHTML: "" },
                  '[data-authed-region="topbar"]': { innerHTML: "" },
                  '[data-authed-region="context"]': { innerHTML: "" },
                };
                let currentChatMain = null;
                const renderHeights = [];
                const mainRegion = {};
                Object.defineProperty(mainRegion, "innerHTML", {
                  get() {
                    return this._html || "";
                  },
                  set(value) {
                    this._html = value;
                    currentChatMain = {
                      scrollTop: 0,
                      scrollHeight: renderHeights.shift() ?? 0,
                    };
                  },
                });
                shellRegions['[data-authed-region="main"]'] = mainRegion;

                const shell = {
                  querySelector(selector) {
                    return shellRegions[selector] || null;
                  },
                };
                const fakeForm = {
                  addEventListener() {},
                  requestSubmit() {},
                };
                const fakeInput = {
                  addEventListener() {},
                  value: "",
                };

                document.getElementById = (id) => {
                  if (id === "app") {
                    return appRoot;
                  }
                  if (id === "chat-input-form") {
                    return fakeForm;
                  }
                  if (id === "chat-input") {
                    return fakeInput;
                  }
                  return null;
                };
                appRoot.querySelectorAll = () => [];
                appRoot.querySelector = (selector) => {
                  if (selector === ".app-shell") {
                    return shell;
                  }
                  if (selector === ".chat-main") {
                    return currentChatMain;
                  }
                  return null;
                };

                window.location.hash = `#/chat/${ticket.id}`;

                renderHeights.push(180);
                render();
                flushFrames();
                if (currentChatMain.scrollTop !== 180) {
                  throw new Error(`Expected opening a ticket to scroll to bottom, got ${currentChatMain.scrollTop}.`);
                }

                currentChatMain.scrollTop = 64;
                saveTicketMessages(ticket.id, [
                  ...getTicketById(ticket.id).messages,
                  {
                    id: "msg-2",
                    role: "user",
                    content: "Follow-up question",
                    createdAt: "2026-03-31T15:25:00.000Z",
                  },
                ]);
                requestChatScrollToBottom(ticket.id);
                renderHeights.push(240);
                render();

                saveTicketMessages(ticket.id, [
                  ...getTicketById(ticket.id).messages,
                  {
                    id: "msg-3",
                    role: "assistant",
                    content: "Answer arrived quickly",
                    createdAt: "2026-03-31T15:25:01.000Z",
                  },
                ]);
                renderHeights.push(420);
                render();

                flushFrames();
                if (currentChatMain.scrollTop !== 240) {
                  throw new Error(
                    `Expected fast reply rerender to preserve the send-time bottom scroll at 240, got ${currentChatMain.scrollTop}.`
                  );
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

    def test_client_async_completion_detects_single_durable_answer_after_pending_message(self) -> None:
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
                  ],
                };

                if (ticketHasAssistantReply(ticket)) {
                  throw new Error("Ticket without a durable assistant answer should not be considered complete.");
                }

                ticket.messages.push({
                  id: "TK-015-m-2026-03-22T12:39:03.540492+00:00-1",
                  role: "assistant",
                  content: "To join a channel in the Agora Video Calling SDK for Android, call the joinChannel method.",
                  createdAt: "2026-03-22T12:39:03.540492+00:00",
                });

                if (!ticketHasAssistantReply(ticket)) {
                  throw new Error("A single durable assistant answer after the pending user message should stop async polling.");
                }
                """
            )
        )

    def test_client_chat_hides_waiting_ui_once_durable_answer_exists(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                updateTicketProduct(ticket.id, "audio_video_calling");
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-user-1",
                    role: "user",
                    content: "how to join channel",
                    createdAt: "2026-04-05T04:00:00.000Z",
                  },
                  {
                    id: "msg-assistant-1",
                    role: "assistant",
                    content: "Use the joinChannel method after you obtain a token.",
                    createdAt: "2026-04-05T04:00:05.000Z",
                  },
                ]);

                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;
                state.isSending = true;
                state.pendingTicketId = ticket.id;
                state.pendingUserMessageId = "msg-user-1";
                state.pendingAsyncTicketId = ticket.id;
                state.pendingAsyncMessageCreatedAt = "2026-04-05T04:00:00.000Z";

                const html = renderChatTicket();
                if (html.includes("I got your message and I am checking it now.")) {
                  throw new Error("Durable assistant answer should not coexist with a transient reassurance bubble.");
                }
                if (html.includes("AI is cross-referencing system health logs")) {
                  throw new Error("Thinking line should disappear once a durable assistant answer exists.");
                }
                if (html.includes("checking the knowledge base... click stop to interrupt.")) {
                  throw new Error("Composer waiting note should disappear once a durable assistant answer exists.");
                }
                if (html.includes("composer-stop-btn")) {
                  throw new Error("Stop button should disappear once a durable assistant answer exists.");
                }
              """
            )
        )

    def test_client_async_waiting_state_keeps_send_without_waiting_copy(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                updateTicketProduct(ticket.id, "audio_video_calling");
                saveTicketMessages(ticket.id, [
                  {
                    id: "msg-user-1",
                    role: "user",
                    content: "how to join channel",
                    createdAt: "2026-04-05T04:00:00.000Z",
                  },
                ]);
                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;
                state.pendingUserMessageId = "msg-user-1";
                state.pendingTicketId = ticket.id;
                state.isSending = true;
                state.pendingAsyncTicketId = ticket.id;
                state.pendingAsyncMessageCreatedAt = "2026-04-05T04:00:00.000Z";

                const html = renderChatTicket();
                if (html.includes("I got your message and I am checking it now.")) {
                  throw new Error("Async waiting state should not render a transient reassurance bubble.");
                }
                if (html.includes("AI is cross-referencing system health logs")) {
                  throw new Error("Async waiting state should not render the thinking line.");
                }
                if (html.includes("checking the knowledge base... click stop to interrupt.")) {
                  throw new Error("Async waiting state should not render the composer waiting note.");
                }
                if (!html.includes("send-btn")) {
                  throw new Error("Async waiting state should keep the inline send button.");
                }
                if (html.includes("composer-stop-btn")) {
                  throw new Error("Async waiting state should no longer render the old stop button.");
                }
              """
            )
        )

    def test_client_logout_clears_pending_request_state_without_client_ack_state(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const ticket = createTicket(state.user.id);
                updateTicketProduct(ticket.id, "audio_video_calling");
                state.view = "chat-ticket";
                state.activeTicketId = ticket.id;
                state.isSending = true;
                state.pendingTicketId = ticket.id;
                state.pendingUserMessageId = "msg-user-1";
                state.pendingPersistedUserMessageCreatedAt = "2026-04-05T04:00:00.000Z";
                state.pendingAsyncTicketId = ticket.id;
                state.pendingAsyncMessageCreatedAt = "2026-04-05T04:00:00.000Z";
                state.pendingAbortController = { signal: { aborted: false } };

                logout();

                if (state.isSending || state.pendingTicketId || state.pendingAsyncTicketId) {
                  throw new Error("Logout should clear pending request identifiers.");
                }
                if (state.pendingAbortController || state.pendingUserMessageId || state.pendingPersistedUserMessageCreatedAt) {
                  throw new Error("Logout should clear the remaining pending request state.");
                }
              """
            )
        )
if __name__ == "__main__":
    unittest.main()
