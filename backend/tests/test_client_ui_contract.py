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
        self.assertIn("./styles.css?v=20260405-client-pending-cleanup-1", html)
        self.assertIn('./app.js?v=20260405-client-pending-cleanup-1', html)
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
        self.assertIn('font-family: "Material Symbols Outlined";', css)
        self.assertIn("html.material-symbols-pending .material-symbols-outlined", css)
        self.assertIn("visibility: hidden;", css)

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
                              content: "I've opened an engineer ticket for this issue and we're investigating further. I'll reply here as soon as the engineer review is confirmed.",
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

    def test_client_new_session_requires_product_selection_and_locks_after_first_message(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const draft = getOrCreateDraftTicket(state.user.id);
                state.view = "chat-ticket";
                state.activeTicketId = draft.id;

                const initialHtml = renderChatTicket();
                if (!initialHtml.includes("Select Product")) {
                  throw new Error("Empty draft session should prompt for product selection.");
                }
                if (!initialHtml.includes("Audio/Video Calling")) {
                  throw new Error("Product selector should offer Audio/Video Calling.");
                }
                if (!initialHtml.includes("Cloud Recording")) {
                  throw new Error("Product selector should offer Cloud Recording.");
                }
                if (!/id="chat-input"[^>]*disabled/.test(initialHtml)) {
                  throw new Error("Composer should stay disabled until a product is selected.");
                }

                updateTicketProduct(draft.id, "audio_video_calling");
                const selectedDraft = getTicketById(draft.id);
                if (!selectedDraft || selectedDraft.product !== "audio_video_calling") {
                  throw new Error("Selecting a product should persist on the local draft.");
                }

                const selectedHtml = renderChatTicket();
                if (/id="chat-input"[^>]*disabled/.test(selectedHtml)) {
                  throw new Error("Composer should unlock once a product is selected.");
                }
                if (!renderContextBar().includes("Audio/Video Calling")) {
                  throw new Error("Context bar should surface the selected product.");
                }

                saveTicketMessages(draft.id, [
                  {
                    id: "msg-1",
                    role: "user",
                    content: "How do I join a channel?",
                    createdAt: new Date().toISOString(),
                  },
                ]);
                updateTicketStatus(draft.id, "communicating");
                updateTicketProduct(draft.id, "cloud_recording");

                const lockedDraft = getTicketById(draft.id);
                if (!lockedDraft || lockedDraft.product !== "audio_video_calling") {
                  throw new Error("Session product should lock after the first customer message.");
                }
              """
            )
        )

    def test_client_sync_preserves_local_empty_draft_product_selection_until_first_send(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));

                const draft = getOrCreateDraftTicket(state.user.id);
                updateTicketProduct(draft.id, "cloud_recording");

                fetch = async () => ({
                  ok: true,
                  json: async () => ({
                    tickets: [],
                  }),
                });

                await syncTicketsFromBackend({ silent: true });

                const syncedDraft = getTicketById(draft.id);
                if (!syncedDraft) {
                  throw new Error("Local empty draft should survive backend sync before first send.");
                }
                if (syncedDraft.product !== "cloud_recording") {
                  throw new Error("Backend sync should preserve local draft product selection.");
                }

                const historyHtml = renderHistoryRow(syncedDraft, { compact: false });
                if (!historyHtml.includes("Cloud Recording")) {
                  throw new Error("Session history metadata should display the selected product.");
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

    def test_client_send_message_waits_for_model_ack_before_showing_transient_ack(self) -> None:
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
                const scheduledTimeouts = [];
                setTimeout = (fn, delay) => {
                  const id = `timeout-${scheduledTimeouts.length + 1}`;
                  scheduledTimeouts.push({ id, fn, delay });
                  return id;
                };
                clearTimeout = (id) => {
                  const match = scheduledTimeouts.find((entry) => entry.id === id);
                  if (match) {
                    match.cleared = true;
                  }
                };
                let resolveAck = null;
                let ackSignal = null;
                fetch = (url, options = undefined) => {
                  calls.push({ url, options });
                  if (url === "/api/client/ack") {
                    ackSignal = options?.signal || null;
                    return new Promise((resolve) => {
                      resolveAck = resolve;
                    });
                  }
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
                if (!calls.some((entry) => entry.url === "/api/client/ack")) {
                  throw new Error("Expected client ack endpoint to be called.");
                }
                if (!calls.some((entry) => entry.url === "/api/tickets/query")) {
                  throw new Error("Expected ticket query endpoint to be called.");
                }
                if (!scheduledTimeouts.some((entry) => entry.delay === 5000)) {
                  throw new Error("Expected client ack fallback timeout to be scheduled.");
                }

                const pendingHtml = renderChatTicket();
                if (pendingHtml.includes("Got it, let me check this for you.")) {
                  throw new Error("Static fallback ack should not render before the timeout fires.");
                }
                if (!state.isSending || state.pendingAsyncTicketId !== ticket.id) {
                  throw new Error("Queued async ticket should keep the client waiting state active.");
                }
                if (!resolveAck) {
                  throw new Error("Expected ack request promise to remain pending.");
                }
                if (ackSignal?.aborted) {
                  throw new Error("Ack request should stay active before the fallback timer fires.");
                }

                resolveAck({
                  ok: true,
                  json: async () => ({
                    ack_text: "I got your message and I am checking it now.",
                    source: "client_model",
                    model: "gpt-5.4-nano",
                    reasoning_effort: "none",
                    latency_ms: 321,
                    error: null,
                  }),
                });
                await Promise.resolve();
                await Promise.resolve();
                await Promise.resolve();
                await Promise.resolve();

                const html = renderChatTicket();
                if (!html.includes("I got your message and I am checking it now.")) {
                  throw new Error("Expected transient client model ack to render after the ack request resolves.");
                }
                if (html.includes("Got it, let me check this for you.")) {
                  throw new Error("Static fallback text should not render when model ack arrives first.");
                }
              """
            )
        )

    def test_client_send_message_falls_back_after_ack_timeout_and_overwrites_with_late_model_ack(self) -> None:
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

                const scheduledTimeouts = [];
                setTimeout = (fn, delay) => {
                  const id = `timeout-${scheduledTimeouts.length + 1}`;
                  scheduledTimeouts.push({ id, fn, delay });
                  return id;
                };
                clearTimeout = (id) => {
                  const match = scheduledTimeouts.find((entry) => entry.id === id);
                  if (match) {
                    match.cleared = true;
                  }
                };
                let resolveAck = null;
                let ackSignal = null;
                fetch = (url, options = undefined) => {
                  if (url === "/api/client/ack") {
                    ackSignal = options?.signal || null;
                    return new Promise((resolve) => {
                      resolveAck = resolve;
                    });
                  }
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

                const beforeTimeoutHtml = renderChatTicket();
                if (beforeTimeoutHtml.includes("收到，我先帮你看一下。")) {
                  throw new Error("Localized fallback should not render before the timeout fires.");
                }

                const fallbackTimer = scheduledTimeouts.find((entry) => entry.delay === 5000);
                if (!fallbackTimer) {
                  throw new Error("Expected fallback timer to be scheduled.");
                }
                fallbackTimer.fn();
                await Promise.resolve();
                await Promise.resolve();

                if (ackSignal?.aborted) {
                  throw new Error("Fallback timer should keep the in-flight ack request alive for late model overwrite.");
                }
                const fallbackHtml = renderChatTicket();
                if (!fallbackHtml.includes("收到，我先帮你看一下。")) {
                  throw new Error("Expected localized fallback text after ack timeout.");
                }

                resolveAck({
                  ok: true,
                  json: async () => ({
                    ack_text: "收到，我来查看。",
                    source: "client_model",
                    model: "gpt-5.4-nano",
                    reasoning_effort: "none",
                    latency_ms: 4200,
                    error: null,
                  }),
                });
                await Promise.resolve();
                await Promise.resolve();
                await Promise.resolve();
                await Promise.resolve();

                const lateAckHtml = renderChatTicket();
                if (!lateAckHtml.includes("收到，我来查看。")) {
                  throw new Error("Late model ack should overwrite the already rendered fallback text when it eventually arrives.");
                }
                if (lateAckHtml.includes("收到，我先帮你看一下。")) {
                  throw new Error("Static fallback text should disappear after the late model ack overwrites it.");
                }
              """
            )
        )

    def test_client_send_message_does_not_render_server_ack_template_before_client_ack_timeout(self) -> None:
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

                const scheduledTimeouts = [];
                setTimeout = (fn, delay) => {
                  const id = `timeout-${scheduledTimeouts.length + 1}`;
                  scheduledTimeouts.push({ id, fn, delay });
                  return id;
                };
                clearTimeout = (id) => {
                  const match = scheduledTimeouts.find((entry) => entry.id === id);
                  if (match) {
                    match.cleared = true;
                  }
                };
                let resolveAck = null;
                fetch = (url, options = undefined) => {
                  if (url === "/api/client/ack") {
                    return new Promise((resolve) => {
                      resolveAck = resolve;
                    });
                  }
                  if (url === "/api/tickets/query") {
                    return Promise.resolve({
                      ok: true,
                      json: async () => ({
                        ticket_id: ticket.id,
                        answer: "Got it, let me check this for you.",
                        ai_replied: true,
                        queued_for_ai: false,
                        ack_source: "server_ack",
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
                if (updated.messages.length !== 1) {
                  throw new Error(`Expected only the durable user message before fallback, got ${updated.messages.length}.`);
                }
                const pendingHtml = renderChatTicket();
                if (pendingHtml.includes("Got it, let me check this for you.")) {
                  throw new Error("Server-side template ack should not render before the client ack timeout fires.");
                }
                if (!scheduledTimeouts.some((entry) => entry.delay === 5000)) {
                  throw new Error("Expected client ack fallback timeout to be scheduled.");
                }

                resolveAck({
                  ok: true,
                  json: async () => ({
                    ack_text: "I got your message and I am checking it now.",
                    source: "client_model",
                    model: "gpt-5.4-nano",
                    reasoning_effort: "none",
                    latency_ms: 812,
                    error: null,
                  }),
                });
                await Promise.resolve();
                await Promise.resolve();
                await Promise.resolve();
                await Promise.resolve();

                const resolvedHtml = renderChatTicket();
                if (!resolvedHtml.includes("I got your message and I am checking it now.")) {
                  throw new Error("Expected the model ack to render once it resolves.");
                }
                if (resolvedHtml.includes("Got it, let me check this for you.")) {
                  throw new Error("Server-side template ack should stay hidden when the model ack arrives before timeout.");
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

                state.isSending = true;
                state.pendingTicketId = ticket.id;
                currentChatMain.scrollTop = 41;
                renderHeights.push(220);
                render();
                flushFrames();
                if (!mainRegion.innerHTML.includes("thinking-line")) {
                  throw new Error("Expected waiting UI to render the thinking line.");
                }
                if (!mainRegion.innerHTML.includes("composer-note")) {
                  throw new Error("Expected waiting UI to render the composer note.");
                }
                if (currentChatMain.scrollTop !== 41) {
                  throw new Error(
                    `Expected waiting-state rerender to preserve scrollTop 41, got ${currentChatMain.scrollTop}.`
                  );
                }

                state.isSending = false;
                state.pendingTicketId = null;
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

    def test_client_chat_hides_transient_ack_and_waiting_ui_once_durable_answer_exists(self) -> None:
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
                setTransientClientAck(ticket.id, "I got your message and I am checking it now.", {
                  source: "client_model",
                });

                const html = renderChatTicket();
                if (html.includes("I got your message and I am checking it now.")) {
                  throw new Error("Transient client ack should disappear once a durable assistant answer exists.");
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

    def test_client_late_ack_is_ignored_after_durable_answer_already_exists(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));
                render = () => {};

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
                state.pendingAsyncTicketId = ticket.id;
                state.pendingAsyncMessageCreatedAt = "2026-04-05T04:00:00.000Z";

                let resolveAck = null;
                fetch = (url) => {
                  if (url === "/api/client/ack") {
                    return new Promise((resolve) => {
                      resolveAck = resolve;
                    });
                  }
                  throw new Error(`Unexpected fetch call to ${url}`);
                };

                const ackPromise = startClientAck(ticket.id, "how to join channel");
                await Promise.resolve();

                saveTicketMessages(ticket.id, [
                  ...getTicketById(ticket.id).messages,
                  {
                    id: "msg-assistant-1",
                    role: "assistant",
                    content: "Use the joinChannel method after you obtain a token.",
                    createdAt: "2026-04-05T04:00:05.000Z",
                  },
                ]);

                resolveAck({
                  ok: true,
                  json: async () => ({
                    ack_text: "I got your message and I am checking it now.",
                    source: "client_model",
                    model: "gpt-5.4-nano",
                    reasoning_effort: "none",
                    latency_ms: 4210,
                    error: null,
                  }),
                });
                await ackPromise;
                await Promise.resolve();
                await Promise.resolve();

                if (getTransientClientAck(ticket.id)) {
                  throw new Error("Late client ack should be ignored after a durable assistant answer exists.");
                }
                const html = renderChatTicket();
                if (html.includes("I got your message and I am checking it now.")) {
                  throw new Error("Late client ack should not render after the durable answer is already visible.");
                }
                if (!html.includes("Use the joinChannel method after you obtain a token.")) {
                  throw new Error("Durable assistant answer should remain visible.");
                }
                """
            )
        )

    def test_client_fallback_ack_is_ignored_after_durable_answer_already_exists(self) -> None:
        self.run_client_app_script(
            textwrap.dedent(
                """
                state.user = { id: "user-1", name: "Admin", email: "admin@example.com" };
                localStorage.setItem("helpdesk_tickets", JSON.stringify([]));
                render = () => {};

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
                state.pendingAsyncTicketId = ticket.id;
                state.pendingAsyncMessageCreatedAt = "2026-04-05T04:00:00.000Z";

                const scheduledTimeouts = [];
                setTimeout = (fn, delay) => {
                  const id = `timeout-${scheduledTimeouts.length + 1}`;
                  scheduledTimeouts.push({ id, fn, delay });
                  return id;
                };
                clearTimeout = () => {};

                fetch = (url) => {
                  if (url === "/api/client/ack") {
                    return new Promise(() => {});
                  }
                  throw new Error(`Unexpected fetch call to ${url}`);
                };

                startClientAck(ticket.id, "how to join channel");
                await Promise.resolve();

                saveTicketMessages(ticket.id, [
                  ...getTicketById(ticket.id).messages,
                  {
                    id: "msg-assistant-1",
                    role: "assistant",
                    content: "Use the joinChannel method after you obtain a token.",
                    createdAt: "2026-04-05T04:00:05.000Z",
                  },
                ]);

                const fallbackTimer = scheduledTimeouts.find((entry) => entry.delay === 5000);
                if (!fallbackTimer) {
                  throw new Error("Expected client ack fallback timer to be scheduled.");
                }
                fallbackTimer.fn();

                if (getTransientClientAck(ticket.id)) {
                  throw new Error("Fallback ack should be ignored after a durable assistant answer already exists.");
                }
                const html = renderChatTicket();
                if (html.includes("Got it, let me check this for you.")) {
                  throw new Error("Fallback ack should not render after the durable answer is already visible.");
                }
                if (!html.includes("Use the joinChannel method after you obtain a token.")) {
                  throw new Error("Durable assistant answer should remain visible.");
                }
                """
            )
        )


if __name__ == "__main__":
    unittest.main()
