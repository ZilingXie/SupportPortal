from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


class WorkspaceUiContractTests(unittest.TestCase):
    def run_workspace_app_script(self, script: str) -> None:
        node_script = textwrap.dedent(
            f"""
            (async () => {{
            const fs = require("fs");
            const vm = require("vm");
            const userScript = {script!r};
            const sharedComposerPath = "ui/shared-ui/composer.js";

            const source = fs.readFileSync("ui/workspace-ui/app.js", "utf8");

            function createElementStub(id = "") {{
              const classes = new Set();
              return {{
                id,
                innerHTML: "",
                textContent: "",
                value: "",
                dataset: {{}},
                disabled: false,
                hidden: false,
                classList: {{
                  add(...names) {{
                    names.forEach((name) => classes.add(String(name)));
                  }},
                  remove(...names) {{
                    names.forEach((name) => classes.delete(String(name)));
                  }},
                  toggle(name, force) {{
                    const className = String(name);
                    if (force === true) {{
                      classes.add(className);
                      return true;
                    }}
                    if (force === false) {{
                      classes.delete(className);
                      return false;
                    }}
                    if (classes.has(className)) {{
                      classes.delete(className);
                      return false;
                    }}
                    classes.add(className);
                    return true;
                  }},
                  contains(name) {{
                    return classes.has(String(name));
                  }},
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

            const elements = new Map();
            const storage = new Map();
            const fetchCalls = [];
            const fetchResponses = [];
            const sandbox = {{
              console,
              URL,
              FormData: function FormData(form) {{
                return {{
                  get(name) {{
                    return form && form.__formData ? form.__formData[name] || "" : "";
                  }},
                }};
              }},
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
                __fetchCalls: fetchCalls,
                __fetchResponses: fetchResponses,
              }},
              document: {{
                getElementById(id) {{
                  if (!elements.has(id)) elements.set(id, createElementStub(id));
                  return elements.get(id);
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
              fetch: async (url, options = {{}}) => {{
                fetchCalls.push({{ url: String(url), options }});
                const next = fetchResponses.length ? fetchResponses.shift() : {{ tickets: [] }};
                return {{
                  ok: true,
                  json: async () => next,
                }};
              }},
              WebSocket: function WebSocket() {{
                this.readyState = 1;
                this.close = () => {{}};
                this.send = () => {{}};
              }},
              HTMLTextAreaElement: function HTMLTextAreaElement() {{}},
              setTimeout(callback) {{
                if (typeof callback === "function") callback();
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

    def test_workspace_ui_is_served_as_independent_static_page(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")

        self.assertIn('WORKSPACE_DIR = UI_DIR / "workspace-ui"', main_source)
        self.assertIn(
            'app.mount("/workspace", StaticFiles(directory=WORKSPACE_DIR, html=True), name="workspace-ui")',
            main_source,
        )
        self.assertIn('app.mount("/engineer", StaticFiles(directory=ENGINEER_DIR, html=True), name="engineer-ui")', main_source)
        self.assertNotIn('app.mount("/assignment"', main_source)

        for path in (
            Path("ui/workspace-ui/index.html"),
            Path("ui/workspace-ui/styles.css"),
            Path("ui/workspace-ui/app.js"),
        ):
            self.assertTrue(path.exists(), f"{path} should exist")

    def test_workspace_ui_combines_assignment_entry_with_real_engineer_flow(self) -> None:
        html = Path("ui/workspace-ui/index.html").read_text(encoding="utf-8")
        app_source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("SupportPortal Workspace", html)
        self.assertIn("Choose a demo engineer", html)
        self.assertIn("I'm ready to roll", app_source)
        self.assertIn("supportportal_workspace_selected_engineer", app_source)
        self.assertIn("supportportal_workspace_daily_shift", app_source)
        self.assertIn("supportportal_workspace_active", app_source)
        self.assertNotIn("ASSIGNMENT_AUTH_KEY", app_source)
        self.assertNotIn("ASSIGNMENT_WORKSPACE_KEY", app_source)
        self.assertNotIn('const ENGINEER_ID = "Jack";', app_source)
        self.assertIn("/api/engineer/tickets?status=investigating", app_source)
        self.assertIn("findNextInvestigatingCase", app_source)
        self.assertIn("No investigating cases available", app_source)
        self.assertIn("assignment-sidebar", css)
        self.assertIn("problem-workspace", css)

    def test_workspace_case_shell_keeps_assignment_sidebar_and_case_controls(self) -> None:
        app_source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("supportportal_workspace_break_after_case", app_source)
        self.assertIn("renderWorkspaceAssignmentSidebarHtml", app_source)
        self.assertIn("renderWorkspaceCaseControlsHtml", app_source)
        self.assertIn("Concierge AI", app_source)
        self.assertIn("Assignment Command", app_source)
        self.assertIn("Engineer context", app_source)
        self.assertIn("UTC+8 daily shift", app_source)
        self.assertIn("Break after this case", app_source)
        self.assertIn("data-action=\"toggle-break-after-case\"", app_source)
        self.assertIn("current-ticket-sla", app_source)
        self.assertIn("workspace-assignment-sidebar", css)
        self.assertIn("workspace-case-controls", css)
        self.assertIn("break-after-case-btn", css)

    def test_workspace_home_redesign_has_shift_known_issues_and_service_status(self) -> None:
        html = Path("ui/workspace-ui/index.html").read_text(encoding="utf-8")
        app_source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("workspace-home-readiness-redesign-1", html)
        self.assertIn("workspace-home-layout-tune-1", html)
        self.assertIn("workspace-home-density-1", html)
        self.assertIn("workspace-home-section-blocks-1", html)
        self.assertIn("workspace-ready-action-placement-1", html)
        self.assertIn('const SERVICE_EVENTS_ENDPOINT = "/api/client/service-events";', app_source)
        self.assertIn("WEEKLY_KNOWN_ISSUES", app_source)
        self.assertIn("RTC black screen reports in Chromium 124", app_source)
        self.assertIn("Webhook replay latency for billing exports", app_source)
        self.assertIn("iOS screen share permission prompt confusion", app_source)
        self.assertIn("renderWorkspaceServiceStatusHtml", app_source)
        self.assertIn("handleWorkspaceShiftSubmit", app_source)
        self.assertIn("Welcome back,", app_source)
        self.assertIn("Loading latest Agora service events...", app_source)
        self.assertIn("Open Agora Status Page", app_source)
        self.assertIn("workspace-home-layout", css)
        self.assertIn("workspace-shift-readiness-panel", app_source)
        self.assertIn("workspace-home-status-grid", app_source)
        self.assertIn("workspace-known-issue-list", css)
        self.assertIn("workspace-service-event-list", css)
        self.assertNotIn("workspace-shift-panel", app_source)
        self.assertNotIn("<p class=\"ticket-kicker\">UTC+8 shift</p>", app_source)
        welcome_source = app_source.split("function renderWelcomeViewHtml()", 1)[1].split(
            "function renderWelcome()", 1
        )[0]
        self.assertEqual(welcome_source.count('data-action="ready-to-roll"'), 1)
        self.assertRegex(
            welcome_source,
            r'(?s)workspace-welcome-top.*data-action="ready-to-roll".*workspace-home-intro',
        )
        shift_form_source = welcome_source.split('<form class="workspace-shift-form"', 1)[1].split(
            "</form>", 1
        )[0]
        self.assertIn("Save shift", shift_form_source)
        self.assertNotIn("I'm ready to roll", shift_form_source)
        self.assertNotRegex(css, r"(?s)\.workspace-home-hero\s*\{.*background: transparent;")
        self.assertRegex(
            css,
            r"(?s)\.workspace-home-hero\s*\{.*background: rgba\(255, 255, 255, 0\.74\);.*border: 1px solid var\(--ghost-border\);.*box-shadow: 0 12px 34px rgba\(23, 28, 33, 0\.05\);",
        )
        self.assertNotRegex(css, r"(?s)\.workspace-home-layout \.workspace-info-panel\s*\{.*background: transparent;")
        self.assertRegex(
            css,
            r"(?s)\.workspace-home-layout \.workspace-info-panel\s*\{.*background: rgba\(255, 255, 255, 0\.74\);.*border: 1px solid var\(--ghost-border\);.*box-shadow: 0 12px 34px rgba\(23, 28, 33, 0\.05\);.*padding: 20px;",
        )
        self.assertRegex(
            css,
            r"(?s)\.workspace-home-status-grid\s*\{.*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);",
        )
        self.assertRegex(
            css,
            r"(?s)\.workspace-welcome-view\s*\{.*padding-top: clamp\(18px, 3vw, 34px\);",
        )
        self.assertRegex(
            css,
            r"(?s)\.workspace-welcome-hero\s*\{.*padding: clamp\(12px, 2vw, 20px\) 0;",
        )
        self.assertRegex(
            css,
            r"(?s)\.workspace-home-intro h1\s*\{.*font-size: clamp\(1\.2rem, 2\.5vw, 2\.4rem\);",
        )

    def test_workspace_home_renders_welcome_shift_known_issues_and_fetched_service_status(self) -> None:
        self.run_workspace_app_script(
            """
            localStorage.setItem("supportportal_workspace_selected_engineer", JSON.stringify("Maya"));
            localStorage.setItem("supportportal_workspace_daily_shift", JSON.stringify({ start: "00:00", end: "23:59" }));
            window.__fetchResponses.push({
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
            });

            renderReadinessInsteadOfPool();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();

            const workspace = document.getElementById("workspace-region");
            const sidebar = document.getElementById("workspace-assignment-sidebar");
            if (!workspace.innerHTML.includes("Welcome back, Maya")) {
              throw new Error("workspace home should lead with a welcome back headline");
            }
            if (workspace.innerHTML.includes("Signed in as")) {
              throw new Error("workspace home should not duplicate the full engineer profile in the main area");
            }
            if (!sidebar.innerHTML.includes("Engineer context") || !sidebar.innerHTML.includes("Maya")) {
              throw new Error("engineer details should remain in the left sidebar");
            }
            if (!workspace.innerHTML.includes("Shift readiness")) {
              throw new Error("workspace home should expose the Shift readiness section");
            }
            const shiftPanelIndex = workspace.innerHTML.indexOf("workspace-shift-readiness-panel");
            const readyIndex = workspace.innerHTML.indexOf("data-action=\\"ready-to-roll\\"");
            const statusGridIndex = workspace.innerHTML.indexOf("workspace-home-status-grid");
            const knownIndex = workspace.innerHTML.indexOf("workspace-known-issues-panel");
            const serviceIndex = workspace.innerHTML.indexOf("workspace-service-status-panel");
            if (readyIndex < 0 || shiftPanelIndex < 0 || readyIndex > shiftPanelIndex) {
              throw new Error("ready action should live in the workspace header before the Shift readiness section");
            }
            const shiftPanelHtml = workspace.innerHTML.slice(shiftPanelIndex, statusGridIndex);
            if (!shiftPanelHtml.includes("Save shift") || shiftPanelHtml.includes('data-action="ready-to-roll"')) {
              throw new Error("Shift readiness should keep Save shift without a second ready action");
            }
            if (statusGridIndex < 0 || knownIndex < statusGridIndex || serviceIndex < statusGridIndex) {
              throw new Error("known issues and service status should live in the same side-by-side grid");
            }
            if (!workspace.innerHTML.includes("RTC black screen reports in Chromium 124")) {
              throw new Error("workspace home should render weekly known issue demo data");
            }
            if (!window.__fetchCalls.some((call) => call.url === "/api/client/service-events")) {
              throw new Error("workspace home should fetch service status through the client service events endpoint");
            }
            if (!workspace.innerHTML.includes("RTC black screen issue") || !workspace.innerHTML.includes("Resolved")) {
              throw new Error("workspace home should render fetched service status events");
            }
            """
        )

    def test_workspace_home_shift_form_updates_storage_and_ready_state(self) -> None:
        self.run_workspace_app_script(
            """
            localStorage.setItem("supportportal_workspace_selected_engineer", JSON.stringify("Jack"));
            localStorage.setItem("supportportal_workspace_daily_shift", JSON.stringify({ start: "00:00", end: "23:59" }));
            renderReadinessInsteadOfPool();

            handleWorkspaceShiftSubmit({
              preventDefault() {},
              target: { __formData: { start: "09:30", end: "18:00" } },
            });

            const storedShift = JSON.parse(localStorage.getItem("supportportal_workspace_daily_shift"));
            if (storedShift.start !== "09:30" || storedShift.end !== "18:00") {
              throw new Error(`shift form did not save expected values: ${JSON.stringify(storedShift)}`);
            }
            const workspace = document.getElementById("workspace-region");
            if (!workspace.innerHTML.includes("09:30") || !workspace.innerHTML.includes("18:00")) {
              throw new Error("workspace home should re-render the saved shift");
            }
            if (!workspace.innerHTML.includes("Next shift starts") && !workspace.innerHTML.includes("On shift now")) {
              throw new Error("workspace home should refresh readiness copy after saving shift");
            }
            """
        )

    def test_workspace_service_status_error_does_not_block_ready_flow(self) -> None:
        self.run_workspace_app_script(
            """
            localStorage.setItem("supportportal_workspace_selected_engineer", JSON.stringify("Maya"));
            localStorage.setItem("supportportal_workspace_active", JSON.stringify(false));
            localStorage.setItem("supportportal_workspace_daily_shift", JSON.stringify({ start: "00:00", end: "23:59" }));

            fetch = async (url, options = {}) => {
              window.__fetchCalls.push({ url: String(url), options });
              if (String(url) === "/api/client/service-events") {
                return { ok: false, status: 503, json: async () => ({}) };
              }
              return {
                ok: true,
                json: async () => ({
                  tickets: [
                    { ticket_id: "TK-INV-1", engineer_case_id: "TK-INV-1", status: "investigating" },
                  ],
                }),
              };
            };

            renderReadinessInsteadOfPool();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
            if (!document.getElementById("workspace-region").innerHTML.includes("Service events are temporarily unavailable")) {
              throw new Error("workspace home should render a service status empty/error state");
            }

            await readyToRoll();
            if (window.location.hash !== "#/tickets/TK-INV-1") {
              throw new Error(`service status failure should not block ready flow, got ${window.location.hash}`);
            }
            """
        )

    def test_workspace_detail_header_has_no_back_arrow_and_uses_preparing_loading(self) -> None:
        html = Path("ui/workspace-ui/index.html").read_text(encoding="utf-8")
        app_source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")

        self.assertIn("workspace-continuous-loading-1", html)
        self.assertNotIn("detail-back-icon-btn", app_source)
        self.assertNotIn('aria-label="Back to Pool"', app_source)
        self.assertNotIn(">arrow_back<", app_source)
        self.assertNotIn("Loading ticket workspace...", app_source)
        self.assertRegex(
            app_source,
            r"(?s)function renderWorkspacePreparingLoadingHtml\s*\(.*?\)\s*\{.*Preparing your workspace.*ready-loading-spinner",
        )
        self.assertRegex(
            app_source,
            r"(?s)if \(detailLoading\) \{\s*return renderWorkspacePreparingLoadingHtml",
        )

    def test_workspace_ticket_number_is_hidden_home_button(self) -> None:
        app_source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn('data-detail-action="back-to-workspace-home"', app_source)
        self.assertIn('aria-label="Return to workspace home"', app_source)
        self.assertRegex(
            app_source,
            r"(?s)if \(action === \"back-to-workspace-home\"\) \{\s*closeTicketDetail\(\);",
        )
        self.assertRegex(
            css,
            r"(?s)\.workspace-ticket-id-button\s*\{.*appearance: none;.*border: 0;.*background: transparent;",
        )
        self.run_workspace_app_script(
            """
            selectedTicketId = "TK-231-1";
            routeState.view = "detail";
            routeState.ticketId = "TK-231-1";
            window.location.hash = "#/tickets/TK-231-1";

            await handleDetailClick({
              target: {
                closest(selector) {
                  if (selector === "button[data-detail-action]") {
                    return {
                      dataset: { detailAction: "back-to-workspace-home" },
                      disabled: false,
                    };
                  }
                  return null;
                },
              },
            });

            if (window.location.hash !== "#/tickets") {
              throw new Error(`expected workspace home hash, got ${window.location.hash}`);
            }
            """
        )

    def test_workspace_sidebar_moves_to_tickets_home_and_hides_on_detail(self) -> None:
        app_source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("renderWelcomeViewHtml", app_source)
        self.assertIn(
            '[data-action="ready-to-roll"], [data-action="sign-out"], [data-action="back-to-welcome"]',
            app_source,
        )
        self.assertRegex(
            app_source,
            r"(?s)function renderReadinessInsteadOfPool\s*\(.*?\)\s*\{.*renderWorkspaceAssignmentSidebar\(\);.*workspaceRegionEl\.innerHTML = renderWelcomeViewHtml\(\);",
        )
        self.assertRegex(
            app_source,
            r"(?s)if \(routeState\.view === \"detail\" && routeState\.ticketId\) \{.*setWorkspaceShellMode\(\"detail\"\);",
        )
        self.assertRegex(
            app_source,
            r"(?s)function renderReadyLoading\s*\(.*?\)\s*\{.*showWorkspaceShell\(\"preparing\"\);",
        )
        self.assertRegex(
            css,
            r"(?s)\.screen-engineer\.workspace-detail-mode \.workspace-assignment-sidebar\s*\{.*display: none;",
        )
        self.assertRegex(
            css,
            r"(?s)\.screen-engineer\.workspace-preparing-mode \.workspace-assignment-sidebar\s*\{.*display: none;",
        )
        self.assertRegex(
            css,
            r"(?s)\.screen-engineer\.workspace-detail-mode \.engineer-shell\.problem-workspace\s*\{.*margin-left: 0;",
        )
        self.assertRegex(
            css,
            r"(?s)\.screen-engineer\.workspace-preparing-mode \.engineer-shell\.problem-workspace\s*\{.*margin-left: 0;",
        )
        self.run_workspace_app_script(
            """
            localStorage.setItem("supportportal_workspace_selected_engineer", JSON.stringify("Maya"));
            localStorage.setItem("supportportal_workspace_active", JSON.stringify(false));
            window.location.hash = "#/tickets";

            renderReadinessInsteadOfPool();

            const engineerScreen = document.getElementById("engineer-screen");
            const loginScreen = document.getElementById("login-screen");
            const sidebar = document.getElementById("workspace-assignment-sidebar");
            const workspace = document.getElementById("workspace-region");
            if (engineerScreen.classList.contains("hidden")) {
              throw new Error("tickets home should use the engineer shell so the sidebar can be shown");
            }
            if (!loginScreen.classList.contains("hidden")) {
              throw new Error("tickets home should hide the standalone readiness screen");
            }
            if (!engineerScreen.classList.contains("workspace-home-mode")) {
              throw new Error("tickets home should mark the shell as home mode");
            }
            if (!sidebar.innerHTML.includes("Concierge AI")) {
              throw new Error("tickets home did not render the assignment command sidebar");
            }
            if (!workspace.innerHTML.includes("I'm ready to roll")) {
              throw new Error("tickets home did not render the readiness main view");
            }

            renderReadyLoading();

            if (!engineerScreen.classList.contains("workspace-preparing-mode")) {
              throw new Error("preparing workspace should mark the shell as preparing mode");
            }
            if (!sidebar.classList.contains("hidden")) {
              throw new Error("preparing workspace should hide the assignment command sidebar");
            }
            if (sidebar.innerHTML.includes("Concierge AI")) {
              throw new Error("preparing workspace should clear the assignment command sidebar");
            }
            if (!workspace.innerHTML.includes("Preparing your workspace")) {
              throw new Error("preparing workspace should render the loading view in the workspace region");
            }

            routeState.view = "detail";
            routeState.ticketId = "TK-231-1";
            selectedTicketId = "TK-231-1";
            selectedTicket = { ticket_id: "TK-231-1", status: "investigating" };
            renderWorkspaceChrome();

            if (!engineerScreen.classList.contains("workspace-detail-mode")) {
              throw new Error("detail view should mark the shell as detail mode");
            }
            if (!sidebar.classList.contains("hidden")) {
              throw new Error("detail view should hide the assignment command sidebar");
            }
            """
        )

    def test_workspace_sidebar_footer_keeps_change_engineer_visible(self) -> None:
        app_source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("function ChangeEngineerButton", app_source)
        self.assertIn('title="Change engineer"', app_source)
        self.assertIn('aria-label="Change engineer"', app_source)
        self.assertIn("workspace-original-change-icon-1", Path("ui/workspace-ui/index.html").read_text(encoding="utf-8"))
        self.assertIn('<svg class="logout-icon" viewBox="0 0 24 24"', app_source)
        self.assertIn('d="M14 8L18 12L14 16"', app_source)
        self.assertNotIn(">switch_account</span>", app_source)
        self.assertRegex(
            app_source,
            r"(?s)function handleChangeEngineerClick\s*\(\).*?signOut\(\);",
        )
        self.run_workspace_app_script(
            """
            localStorage.setItem("supportportal_workspace_selected_engineer", JSON.stringify("Jack"));
            localStorage.setItem("supportportal_workspace_active", JSON.stringify(false));
            renderReadinessInsteadOfPool();

            handleChangeEngineerClick();

            if (localStorage.getItem("supportportal_workspace_selected_engineer") !== null) {
              throw new Error("change engineer should clear the selected workspace engineer");
            }
            if (document.getElementById("login-screen").classList.contains("hidden")) {
              throw new Error("change engineer should return to the engineer selector");
            }
            if (!document.getElementById("engineer-screen").classList.contains("hidden")) {
              throw new Error("change engineer should hide the engineer workspace shell");
            }
            if (!document.getElementById("workspace-root").innerHTML.includes("Engineer login")) {
              throw new Error("change engineer should render the workspace engineer selector");
            }
            """
        )

        self.assertRegex(
            css,
            r"(?s)\.workspace-assignment-sidebar \.rail-user-controls,\s*"
            r"\.workspace-assignment-sidebar:hover \.rail-user-controls,\s*"
            r"\.workspace-assignment-sidebar:focus-within \.rail-user-controls\s*\{"
            r".*grid-template-columns: minmax\(0, 1fr\) auto;"
            r".*min-width: 0;"
            r".*max-width: 100%;"
            r".*overflow: hidden;",
        )
        self.assertRegex(
            css,
            r"(?s)\.workspace-assignment-sidebar \.user-profile-chip,\s*"
            r"\.workspace-assignment-sidebar:hover \.user-profile-chip,\s*"
            r"\.workspace-assignment-sidebar:focus-within \.user-profile-chip\s*\{"
            r".*min-width: 0;"
            r".*max-width: 100%;"
            r".*overflow: hidden;",
        )
        self.assertRegex(
            css,
            r"(?s)\.workspace-assignment-sidebar \.logout-icon-btn\s*\{.*flex: 0 0 52px;",
        )
        self.assertRegex(
            css,
            r"(?s)\.workspace-assignment-sidebar \.user-name,\s*"
            r"\.workspace-assignment-sidebar \.user-role,\s*"
            r"\.workspace-assignment-sidebar \.realtime-value\s*\{.*text-overflow: ellipsis;",
        )

    def test_workspace_assignment_sidebar_collapses_until_hover(self) -> None:
        css = Path("ui/workspace-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("--workspace-sidebar-collapsed-width: 96px", css)
        self.assertIn("--workspace-sidebar-expanded-width: 320px", css)
        self.assertIn("width: var(--workspace-sidebar-collapsed-width)", css)
        self.assertIn("margin-left: var(--workspace-sidebar-collapsed-width)", css)
        self.assertRegex(
            css,
            r"(?s)\.workspace-assignment-sidebar \.sidebar-inner\s*\{.*grid-template-columns: minmax\(0, 1fr\);.*min-width: 0;.*max-width: 100%",
        )
        self.assertIn(".workspace-assignment-sidebar .rail-brand-icon", css)
        self.assertIn("flex: 0 0 54px", css)
        self.assertRegex(
            css,
            r"(?s)\.workspace-assignment-sidebar \.rail-brand\s*\{.*min-width: 0;.*max-width: 100%",
        )
        self.assertIn(".workspace-assignment-sidebar:hover,\n.workspace-assignment-sidebar:focus-within", css)
        self.assertIn("width: var(--workspace-sidebar-expanded-width)", css)
        self.assertRegex(
            css,
            r"(?s)\.workspace-assignment-sidebar \.rail-compact-stack\s*\{.*visibility: visible",
        )
        self.assertIn(".workspace-assignment-sidebar .rail-compact-status", css)
        self.assertIn(".workspace-assignment-sidebar .rail-compact-avatar", css)
        self.assertRegex(
            css,
            r"(?s)\.workspace-assignment-sidebar \.sidebar-inner > :not\(\.rail-brand\):not\(\.rail-compact-stack\)\s*\{.*opacity: 0;.*pointer-events: none",
        )
        self.assertRegex(
            css,
            r"(?s)\.workspace-assignment-sidebar:hover \.sidebar-inner > :not\(\.rail-brand\):not\(\.rail-compact-stack\),\s*\.workspace-assignment-sidebar:focus-within \.sidebar-inner > :not\(\.rail-brand\):not\(\.rail-compact-stack\)\s*\{.*opacity: 1;.*pointer-events: auto",
        )

    def test_workspace_ready_opens_only_investigating_case(self) -> None:
        self.run_workspace_app_script(
            """
            localStorage.setItem("supportportal_workspace_selected_engineer", JSON.stringify("Maya"));
            localStorage.setItem("supportportal_workspace_active", JSON.stringify(false));
            window.__fetchResponses.push({
              tickets: [
                { ticket_id: "TK-ESC-1", status: "escalated" },
                { ticket_id: "TK-INV-1", engineer_case_id: "TK-INV-1", status: "investigating" },
              ],
            });
            await readyToRoll();
            if (!window.__fetchCalls.some((call) => call.url === "/api/engineer/tickets?status=investigating")) {
              throw new Error("ready flow did not request investigating tickets");
            }
            if (window.location.hash !== "#/tickets/TK-INV-1") {
              throw new Error(`expected investigating case hash, got ${window.location.hash}`);
            }
            """
        )

    def test_workspace_ready_to_detail_uses_continuous_preparing_loading(self) -> None:
        html = Path("ui/workspace-ui/index.html").read_text(encoding="utf-8")
        app_source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")

        self.assertIn("workspace-continuous-loading-1", html)
        self.assertIn("continuousLoading", app_source)
        self.run_workspace_app_script(
            """
            localStorage.setItem("supportportal_workspace_selected_engineer", JSON.stringify("Maya"));
            localStorage.setItem("supportportal_workspace_active", JSON.stringify(false));
            const workspace = document.getElementById("workspace-region");
            let preparingRenderCount = 0;
            let workspaceHtml = "";
            Object.defineProperty(workspace, "innerHTML", {
              configurable: true,
              get() {
                return workspaceHtml;
              },
              set(value) {
                workspaceHtml = String(value || "");
                if (workspaceHtml.includes("Preparing your workspace")) {
                  preparingRenderCount += 1;
                }
              },
            });

            window.__fetchResponses.push(
              {
                tickets: [
                  {
                    ticket_id: "TK-INV-1",
                    engineer_case_id: "TK-INV-1",
                    status: "investigating",
                  },
                ],
              },
              {
                tickets: [
                  {
                    ticket_id: "TK-INV-1",
                    engineer_case_id: "TK-INV-1",
                    status: "investigating",
                  },
                ],
              },
              {
                ticket: {
                  ticket_id: "TK-INV-1",
                  status: "investigating",
                  title: "Black screen issue",
                  requester: "Client",
                  messages: [],
                },
              }
            );

            await readyToRoll();

            if (window.location.hash !== "#/tickets/TK-INV-1") {
              throw new Error(`expected investigating case hash, got ${window.location.hash}`);
            }
            if (preparingRenderCount !== 1) {
              throw new Error(`expected one continuous preparing render, got ${preparingRenderCount}`);
            }
            if (workspace.innerHTML.includes("Preparing your workspace")) {
              throw new Error("continuous loading view was not replaced after ticket detail fetch");
            }
            """
        )

    def test_workspace_ready_does_not_open_non_investigating_cases(self) -> None:
        self.run_workspace_app_script(
            """
            localStorage.setItem("supportportal_workspace_selected_engineer", JSON.stringify("Maya"));
            localStorage.setItem("supportportal_workspace_active", JSON.stringify(false));
            window.__fetchResponses.push({
              tickets: [
                { ticket_id: "TK-ESC-1", status: "escalated" },
                { ticket_id: "TK-COMM-1", status: "communicating" },
                { ticket_id: "TK-RES-1", status: "resolved" },
              ],
            });
            await readyToRoll();
            if (window.location.hash.includes("TK-")) {
              throw new Error(`non-investigating case should not be opened: ${window.location.hash}`);
            }
            const workspace = document.getElementById("workspace-region");
            const sidebar = document.getElementById("workspace-assignment-sidebar");
            if (!workspace.innerHTML.includes("No investigating cases available")) {
              throw new Error("missing no-investigating empty state");
            }
            if (!sidebar.innerHTML.includes("Concierge AI")) {
              throw new Error("tickets empty state should keep the assignment sidebar visible");
            }
            """
        )

    def test_workspace_root_does_not_fallback_to_engineer_ticket_pool(self) -> None:
        self.run_workspace_app_script(
            """
            localStorage.setItem("supportportal_workspace_selected_engineer", JSON.stringify("Maya"));
            localStorage.setItem("supportportal_workspace_active", JSON.stringify(true));
            window.location.hash = "";

            await enterBoard();

            if (window.__fetchCalls.some((call) => call.url === "/api/engineer/tickets?status=all")) {
              throw new Error("workspace root should not load the engineer ticket pool");
            }
            const workspace = document.getElementById("workspace-region");
            const sidebar = document.getElementById("workspace-assignment-sidebar");
            if (!workspace.innerHTML.includes("I'm ready to roll")) {
              throw new Error("workspace region should return to readiness instead of pool");
            }
            if (!sidebar.innerHTML.includes("Concierge AI")) {
              throw new Error("tickets home should keep the assignment sidebar visible");
            }
            if (workspace.innerHTML.includes("Engineer queue metrics")) {
              throw new Error("workspace root rendered the engineer pool fallback");
            }
            """
        )

    def test_workspace_mutation_payloads_use_selected_engineer_id(self) -> None:
        self.run_workspace_app_script(
            """
            localStorage.setItem("supportportal_workspace_selected_engineer", JSON.stringify("Maya"));
            await updateTicketStatus("TK-INV-1", "investigate");
            await runMultiAgentInvestigation("TK-INV-1");
            await submitInvestigationMessage("TK-INV-1", "Collected SDK logs.");
            await submitInvestigationConfirmation("TK-INV-1", "approve", "Looks grounded.");

            const bodies = window.__fetchCalls
              .filter((call) => call.options && call.options.body)
              .map((call) => JSON.parse(call.options.body));
            if (bodies.length !== 4) {
              throw new Error(`expected 4 mutation bodies, got ${bodies.length}`);
            }
            for (const body of bodies) {
              if (body.engineer_id !== "Maya") {
                throw new Error(`expected Maya engineer_id, got ${body.engineer_id}`);
              }
            }
            """
        )


if __name__ == "__main__":
    unittest.main()
