from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


class WorkspaceAdminUiContractTests(unittest.TestCase):
    def run_admin_app_script(self, script: str) -> None:
        node_script = textwrap.dedent(
            f"""
            (async () => {{
              const fs = require("fs");
              const vm = require("vm");
              const root = {{ innerHTML: "", addEventListener() {{}}, querySelector() {{ return null; }} }};
              const storage = new Map();
              const sandbox = {{
                console, Headers,
                window: {{ location: {{ pathname: "/workspace/admin/" }} }},
                document: {{ getElementById() {{ return root; }} }},
                localStorage: {{
                  getItem(key) {{ return storage.has(key) ? storage.get(key) : null; }},
                  setItem(key, value) {{ storage.set(key, String(value)); }},
                  removeItem(key) {{ storage.delete(key); }},
                }},
                fetch: async () => ({{ ok: true, status: 200, json: async () => ({{ cases: [] }}) }}),
                FormData: function FormData() {{ return {{ get() {{ return ""; }}, entries() {{ return []; }} }}; }},
              }};
              sandbox.globalThis = sandbox;
              vm.createContext(sandbox);
              vm.runInContext(fs.readFileSync("ui/workspace-ui/admin/app.js", "utf8"), sandbox);
              await vm.runInContext(`(async () => {{\\n${{{script!r}}}\\n}})()`, sandbox);
            }})().catch((error) => {{ console.error(error); process.exit(1); }});
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

    def test_workspace_admin_replaces_assignment_route(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        self.assertIn('app.mount("/workspace", StaticFiles(directory=WORKSPACE_DIR, html=True)', main_source)
        self.assertNotIn('app.mount("/assignment"', main_source)
        self.assertFalse(Path("ui/assignment-ui").exists())

    def test_workspace_admin_uses_protected_production_apis(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        for marker in (
            "/api/workspace/auth/login",
            "/api/workspace/admin/accounts",
            "/api/workspace/admin/metrics",
            "/api/workspace/admin/audit",
            "/api/workspace/admin/dispatch",
            "/api/workspace/admin/reassign-due",
            "/api/workspace/admin/invitations",
            "/api/workspace/admin/engineer-schedules",
            "/api/workspace/cases?assignment_status=all",
            "data-invitation-form",
            "data-schedule-form",
            "On Schedule Now",
            "Weekly Schedule",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("supportportal_assignment_admin_schedule", source)
        self.assertNotIn('/api/engineer/tickets?status=all', source)
        self.assertNotIn("/availability", source)

    def test_account_automation_routing_persona_and_environment_tabs_are_operational(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")
        for marker in (
            '"automated-cases"', '"route-prompt"', '"persona-prompts"', '"environment-config"',
            "/api/workspace/admin/account-automation",
            "/api/workspace/admin/account-routing/config",
            "/api/workspace/admin/account-personas",
            "/api/workspace/admin/environment-config",
            "Automation share", "Route category", "Current route", "Version history", "Configuration names",
            "data-persona-draft-form", "data-env-search", "data-action=\"publish-persona\"",
            "environmentLoadError", "loadEnvironmentConfig",
            "data-action=\"retry-environment-config\"",
            "description.toLowerCase()", "admin-config-description", "admin-config-copy",
        ):
            self.assertIn(marker, source)
        for marker in (".admin-metric-strip", ".admin-route-timeline", ".admin-prompt-editor", ".admin-config-list", ".admin-config-description", ".admin-config-copy"):
            self.assertIn(marker, css)

        self.run_admin_app_script(
            """
            automationData = { metrics: { total_account_cases: 4, automated_cases: 1, not_automated_cases: 3, automation_rate: .25 }, cases: [{ client_ticket_id: 'TK-1', title: 'Invoice', automation_status: 'automation' }] };
            routingData = { router_prompt_version: 'account-router-v1', stages: [{ name: 'semantic_intent', description: 'Classifies the request.' }, { name: 'policy_gate', description: 'Applies policy.' }], route_categories: [{ name: 'billing', display_name: 'Billing', description: 'Billing requests.', execution_actions: ['detailed_invoice'] }], system_prompt: 'actual prompt' };
            personaData = { personas: [{ persona_key: 'default-support', display_name: 'Default Support', enabled: true, published_version: 1, versions: [{ version: 1, status: 'published', content: { instruction: 'Warm', signoff_name: 'Sid' }, change_note: 'Initial' }] }] };
            environmentData = { names: ['OPENAI_API_KEY', 'TICKET_DB_DSN'], items: [
              { name: 'OPENAI_API_KEY', description: 'Credential used by OpenAI.' },
              { name: 'TICKET_DB_DSN', description: 'PostgreSQL connection string for ticket storage.' }
            ] };
            if (!renderAutomatedCases().includes('25.0%')) throw new Error('automation ratio missing');
            if (!renderRoutePrompt().includes('account-router-v1')) throw new Error('route version missing');
            if (!renderRoutePrompt().includes('Classifies the request.')) throw new Error('route stage description missing');
            if (!renderRoutePrompt().includes('Billing requests.')) throw new Error('route category missing');
            if (!renderPersonaPrompts().includes('Version history')) throw new Error('persona history missing');
            if (!renderEnvironmentConfig().includes('OPENAI_API_KEY')) throw new Error('config name missing');
            if (!renderEnvironmentConfig().includes('Credential used by OpenAI.')) throw new Error('config description missing');
            environmentQuery = 'ticket storage';
            const filteredEnvironment = renderEnvironmentConfig();
            if (!filteredEnvironment.includes('TICKET_DB_DSN') || filteredEnvironment.includes('OPENAI_API_KEY')) throw new Error('description search missing');
            environmentQuery = '';
            environmentData = { names: ['LEGACY_ONLY_KEY'] };
            const legacyEnvironment = renderEnvironmentConfig();
            if (!legacyEnvironment.includes('LEGACY_ONLY_KEY') || !legacyEnvironment.includes('Description unavailable until the API is updated.')) throw new Error('names-only compatibility missing');
            """
        )
        self.assertNotIn("Route execution", source)
        self.assertNotIn("inspect-route", source)

        core_load = source[source.index("async function loadAdminData"):source.index("function signOut")]
        promise_all_start = core_load.index("Promise.all")
        promise_all_end = core_load.index("]);", promise_all_start)
        self.assertNotIn(
            "/api/workspace/admin/environment-config",
            core_load[promise_all_start:promise_all_end],
        )

    def test_admin_session_is_role_gated_and_preserves_engineer_storage(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        for marker in (
            "supportportal_admin_workspace_access_token",
            "supportportal_admin_workspace_account",
            "supportportal_admin_workspace_account_id",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("supportportal_engineer_workspace_", source)
        self.assertNotIn('"supportportal_workspace_access_token"', source)

        self.run_admin_app_script(
            """
            accessToken = "engineer-token";
            currentAccount = { account_id: "Zac", display_name: "Zac", role: "engineer" };
            if (isAdminAuthenticated()) {
              throw new Error("admin accepted an engineer session");
            }
            renderAdmin();
            if (!root.innerHTML.includes("admin-login-page")) {
              throw new Error("admin did not show its login page for an engineer session");
            }

            accessToken = "admin-token";
            currentAccount = { account_id: "Admin", display_name: "Admin", role: "admin" };
            if (!isAdminAuthenticated()) {
              throw new Error("admin rejected a valid admin session");
            }

            localStorage.setItem(WORKSPACE_ACCESS_TOKEN_KEY, JSON.stringify("admin-token"));
            localStorage.setItem(WORKSPACE_ACCOUNT_KEY, JSON.stringify({
              account_id: "Admin", display_name: "Admin", role: "admin"
            }));
            localStorage.setItem("supportportal_engineer_workspace_access_token", JSON.stringify("engineer-token"));
            signOut({ render: false });
            if (localStorage.getItem(WORKSPACE_ACCESS_TOKEN_KEY) !== null) {
              throw new Error("admin logout did not clear the admin session");
            }
            if (localStorage.getItem("supportportal_engineer_workspace_access_token") === null) {
              throw new Error("admin logout cleared the engineer session");
            }
            """
        )

    def test_admin_status_mapping_uses_assignment_status_not_client_status_or_assignee(self) -> None:
        self.run_admin_app_script(
            """
            const pending = normalizeAdminTicket({
              engineer_case_id: "CASE-1", status: "resolved", client_status: "investigating", assignment_status: "pending",
              assigned_engineer_id: "legacy-value"
            });
            const assigned = normalizeAdminTicket({
              engineer_case_id: "CASE-2", status: "open", assignment_status: "assigned",
              assigned_engineer_id: "Maya"
            });
            const resolved = normalizeAdminTicket({
              engineer_case_id: "CASE-3", status: "communicating", assignment_status: "resolved"
            });
            if (pending.assignmentStatus !== "pending" || assigned.assignmentStatus !== "assigned" || resolved.assignmentStatus !== "resolved") {
              throw new Error("assignment status is not independent from client status and assignee");
            }
            if (pending.clientStatus !== "investigating") {
              throw new Error("admin used legacy Engineer Case status instead of Client Ticket status");
            }
            """
        )

    def test_admin_case_tabs_use_assignment_status_and_exact_columns(self) -> None:
        self.run_admin_app_script(
            """
            adminTickets = [
              normalizeAdminTicket({ engineer_case_id: "CASE-P", title: "Pending subject", client_status: "open", assignment_status: "pending", requester: "Pat" }),
              normalizeAdminTicket({ engineer_case_id: "CASE-A", title: "Assigned subject", client_status: "investigating", assignment_status: "assigned", requester: "Ari", assigned_engineer_id: "Maya" }),
              normalizeAdminTicket({ engineer_case_id: "CASE-R", title: "Resolved subject", client_status: "resolved", assignment_status: "resolved", requester: "Ren", assigned_engineer_id: "Leo" }),
            ];

            const pendingHtml = renderAdminTicketBoard("pending-assignment");
            const pendingHead = pendingHtml.match(/<thead><tr>(.*?)<\\/tr><\\/thead>/s)?.[1] || "";
            if (!pendingHtml.includes("CASE-P") || pendingHtml.includes("CASE-A") || pendingHtml.includes("CASE-R")) {
              throw new Error("Pending Assignment tab did not filter by assignment_status");
            }
            if (pendingHead !== "<th>ID</th><th>Subject</th><th>Status</th><th>Requester</th><th>Priority</th>") {
              throw new Error(`Pending Assignment columns are incorrect: ${pendingHead}`);
            }

            for (const [section, expectedId, unexpectedId] of [["assigned", "CASE-A", "CASE-R"], ["resolved", "CASE-R", "CASE-A"]]) {
              const html = renderAdminTicketBoard(section);
              const head = html.match(/<thead><tr>(.*?)<\\/tr><\\/thead>/s)?.[1] || "";
              if (!html.includes(expectedId) || html.includes(unexpectedId) || html.includes("CASE-P")) {
                throw new Error(`${section} tab did not filter by assignment_status`);
              }
              if (head !== "<th>ID</th><th>Subject</th><th>Status</th><th>Requester</th><th>Priority</th><th>Assignee</th>") {
                throw new Error(`${section} columns are incorrect: ${head}`);
              }
            }
            """
        )

        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        self.assertNotIn("data-assignment-form", source)
        self.assertNotIn("Admin adjustment", source)

    def test_workspace_admin_assets_are_self_contained(self) -> None:
        html = Path("ui/workspace-ui/admin/index.html").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")
        self.assertIn('id="workspace-admin-root"', html)
        self.assertIn("./styles.css", html)
        self.assertIn("./app.js", html)
        self.assertIn(".admin-shell", css)
        self.assertIn(".admin-case-tabs", css)
        self.assertIn(".admin-case-table", css)
        self.assertIn(".admin-case-table th {\n  padding-block: 12px;", css)

    def test_workspace_admin_login_uses_transactional_entry_contract(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        html = Path("ui/workspace-ui/admin/index.html").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        for marker in (
            '<strong>Admin</strong>',
            "Welcome Back",
            "An administrative workspace for managing engineer access, schedules, assignments, and SLA health.",
            "admin-login-card",
            "Secure Admin Workspace",
            '<span>Email</span>',
            'name="email"',
            'name="password"',
        ):
            self.assertIn(marker, source)
        self.assertNotIn("Account ID", source)
        self.assertIn("20260721-account-automation-admin-1", html)
        self.assertIn(".admin-login-header", css)
        self.assertIn(".admin-login-footer", css)
        self.assertIn("@media (max-width: 640px)", css)

    def test_admin_shell_uses_collapsed_rail_and_footer_account_controls(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        html = Path("ui/workspace-ui/admin/index.html").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        self.assertIn("admin-rail-footer", source)
        self.assertIn("admin-user-chip", source)
        self.assertIn("admin-logout-btn", source)
        self.assertNotIn("admin-topbar-btn", source)
        self.assertIn("grid-template-columns: 96px minmax(0, 1fr)", css)
        self.assertIn("width: 264px", css)
        self.assertIn('class="admin-rail-fallback">AD</span>', source)
        self.assertIn('class="admin-rail-fallback">LO</span>', source)
        self.assertIn("material-symbols-failed", html)
        self.assertIn("material-symbols-ready", html)
        self.assertIn("document.fonts.check(fontSpec, railGlyphs)", html)
        self.assertIn('html:not(.material-symbols-ready) .admin-sidebar .admin-rail-glyph', css)
        self.assertIn('html.material-symbols-ready .admin-sidebar .admin-rail-fallback', css)
        self.assertIn("syncAdminRailScrollPosition", source)
        self.assertNotIn('scrollIntoView({ block: "nearest", inline: "center" })', source)

        self.run_admin_app_script(
            """
            const sidebarBody = { scrollLeft: 73, clientWidth: 68, scrollWidth: 236 };
            const activeLink = { offsetLeft: 104, offsetWidth: 44 };
            root.querySelector = (selector) => selector === ".admin-sidebar-body" ? sidebarBody : activeLink;
            globalThis.matchMedia = () => ({ matches: false });
            syncAdminRailScrollPosition();
            if (sidebarBody.scrollLeft !== 0) throw new Error("desktop rail retained a hidden horizontal offset");

            sidebarBody.clientWidth = 300;
            sidebarBody.scrollWidth = 720;
            activeLink.offsetLeft = 430;
            activeLink.offsetWidth = 120;
            globalThis.matchMedia = () => ({ matches: true });
            syncAdminRailScrollPosition();
            if (sidebarBody.scrollLeft !== 340) throw new Error("mobile active navigation was not centered within its own scroller");
            """
        )

    def test_admin_account_entry_uses_simple_blue_button_and_plain_back_link(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        self.assertIn('class="btn btn-primary admin-new-account-btn"', source)
        self.assertIn('aria-hidden="true">add</span><span>New Account</span>', source)
        self.assertIn(".admin-new-account-btn", css)
        self.assertIn("background: var(--primary)", css)
        self.assertIn(".admin-back-link:visited", css)
        self.assertIn("text-decoration: none", css)

    def test_admin_invitation_submit_has_immediate_feedback_and_deduplicates_requests(self) -> None:
        self.run_admin_app_script(
            """
            let requestCount = 0;
            let resolveRequest;
            fetchJson = () => {
              requestCount += 1;
              return new Promise((resolve) => { resolveRequest = resolve; });
            };
            const attributes = new Map();
            const submit = { disabled: false, innerHTML: "original markup" };
            const errorNode = { textContent: "old error" };
            const form = {
              dataset: {},
              querySelector(selector) { return selector.includes("submit") ? submit : errorNode; },
              setAttribute(name, value) { attributes.set(name, value); },
              removeAttribute(name) { attributes.delete(name); },
            };
            const pending = handleInvitation(form);
            if (!submit.disabled || !submit.innerHTML.includes("Sending invitation...") || form.dataset.submitting !== "true") {
              throw new Error("first click did not enter the sending state immediately");
            }
            await handleInvitation(form);
            if (requestCount !== 1) throw new Error("duplicate click created another invitation request");
            resolveRequest({ invitation: { email: "test@example.com", expires_at: "2026-07-20T00:00:00Z" } });
            await pending;
            if (requestCount !== 1) throw new Error("invitation request count changed after completion");
            """
        )

    def test_admin_invitation_submit_restores_retry_state_after_failure(self) -> None:
        self.run_admin_app_script(
            """
            fetchJson = async () => { throw new Error("mail service unavailable"); };
            const attributes = new Map();
            const submit = { disabled: false, innerHTML: "original markup" };
            const errorNode = { textContent: "" };
            const form = {
              dataset: {},
              querySelector(selector) { return selector.includes("submit") ? submit : errorNode; },
              setAttribute(name, value) { attributes.set(name, value); },
              removeAttribute(name) { attributes.delete(name); },
            };
            await handleInvitation(form);
            if (submit.disabled || submit.innerHTML !== "original markup" || form.dataset.submitting || attributes.has("aria-busy")) {
              throw new Error("failed invitation did not restore the retry state");
            }
            if (errorNode.textContent !== "mail service unavailable") throw new Error("invitation error was not displayed");
            """
        )

    def test_admin_weekly_schedule_uses_blue_half_hour_name_slots(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        for marker in (
            "timeStringToMinutes",
            "buildScheduleSegments",
            "assignScheduleLanes",
            "buildScheduleSlots",
            "renderWeeklyTimeGrid",
            "admin-week-grid",
            "admin-week-time-column",
            "admin-week-slot",
            "repeat(48, 26px)",
            "#cae6ff",
            "#00344e",
        ):
            self.assertIn(marker, source + css)
        self.assertNotIn("admin-week-shift", source + css)
        self.assertNotIn("repeat(96, 12px)", css)
        self.assertNotIn("admin-roster-table", source + css)
        self.assertNotIn("#16262d", css)
        self.assertNotIn("#dff3f5", css)
        self.assertIn("width: max-content", css)
        self.assertIn("max-width: calc((100% / var(--lane-count)) - 8px)", css)
        self.assertIn("border-radius: 999px", css)
        self.assertNotIn("\n  width: calc((100% / var(--lane-count)) - 8px)", css)

    def test_admin_schedule_is_a_separate_tab_with_engineer_edit_entry(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")

        for marker in (
            '["schedule", "calendar_month", "Schedule", "SC"]',
            'adminSection === "schedule"',
            "renderAdminSchedule()",
            "Engineer Schedules",
            "admin-roster-statuses",
            'adminSection = "schedule"',
            'globalThis.location.hash = "schedule"',
        ):
            self.assertIn(marker, source)

        self.run_admin_app_script(
            """
            scheduleData = { timezone: "Asia/Shanghai", engineers: [
              { account_id: "zac", email: "zac@example.com", display_name: "Zac", is_on_schedule_now: false, shifts: [] },
            ] };
            const management = renderAdminEngineerManagement();
            const schedule = renderAdminSchedule();
            if (management.includes("admin-week-grid")) throw new Error("weekly grid remained in Engineer Management");
            if (!management.includes("Engineer Schedules") || !management.includes("off schedule") || !management.includes("Modify Zac schedule")) {
              throw new Error("Engineer Management is missing schedule management access");
            }
            if (!schedule.includes("admin-week-grid") || !schedule.includes("Schedule Grid")) {
              throw new Error("Schedule tab is missing the weekly grid");
            }
            selectedEngineerId = "zac";
            if (!renderAdminSchedule().includes('aria-label="Modify shifts"')) {
              throw new Error("Schedule tab did not open the selected engineer editor");
            }
            """
        )

    def test_admin_time_labels_share_the_first_half_hour_slot_center(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        self.assertIn('class="admin-week-time" data-hour="${hour}" style="grid-row:${row}"', source)
        self.assertIn('style="grid-column:${slot.weekday + 2};grid-row:${row};', source)
        self.assertIn("const row = 2 + hour * 2", source)
        self.assertIn("const row = 2 + Math.floor(slot.slotStart / 30)", source)
        self.assertIn("align-self: center", css)
        self.assertNotIn("transform: translateY(-50%)", css)

    def test_admin_schedule_editor_uses_finite_half_hour_selects(self) -> None:
        self.run_admin_app_script(
            """
            scheduleData = { timezone: "Asia/Shanghai", engineers: [] };
            const html = renderScheduleEditor({
              account_id: "zac", display_name: "Zac", shifts: [{ weekday: 0, start: "00:00", end: "24:00" }],
            }, ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]);
            if (html.includes('type="time"') || html.includes('name="availability"') || html.includes('name="reason"')) {
              throw new Error("schedule editor still contains removed controls");
            }
            if (!html.includes('name="start_hour_0"') || !html.includes('name="end_hour_0"') || !html.includes('value="24" selected')) {
              throw new Error("finite hour controls or 24:00 selection are missing");
            }
            if (!html.includes('name="start_minute_0"') || !html.includes('value="30"')) {
              throw new Error("half-hour minute controls are missing");
            }
            if (!html.includes('data-end-minute="0"') || !html.includes('disabled')) {
              throw new Error("24:00 did not lock its minute to 00");
            }
            """
        )

    def test_admin_schedule_save_is_immediate_deduplicated_and_schedule_only(self) -> None:
        self.run_admin_app_script(
            """
            let requestCount = 0;
            let requestUrl = "";
            let requestBody = null;
            let resolveRequest;
            FormData = function FormData() { return { get(name) {
              const values = { day_0: "on", start_hour_0: "09", start_minute_0: "00", end_hour_0: "17", end_minute_0: "30" };
              return values[name] || "";
            } }; };
            fetchJson = (url, options) => {
              requestCount += 1;
              requestUrl = url;
              requestBody = JSON.parse(options.body);
              return new Promise((resolve) => { resolveRequest = resolve; });
            };
            const attributes = new Map();
            const submit = { disabled: false, innerHTML: "save" };
            const errorNode = { textContent: "old" };
            const form = {
              dataset: { engineerId: "zac" },
              querySelector(selector) { return selector.includes("submit") ? submit : errorNode; },
              setAttribute(name, value) { attributes.set(name, value); },
              removeAttribute(name) { attributes.delete(name); },
            };
            const pending = handleScheduleUpdate(form);
            if (!submit.disabled || !submit.innerHTML.includes("Saving schedule...") || form.dataset.submitting !== "true") {
              throw new Error("first save did not enter loading state immediately");
            }
            await handleScheduleUpdate(form);
            if (requestCount !== 1 || !requestUrl.endsWith("/schedule") || requestUrl.includes("availability")) {
              throw new Error("save did not issue exactly one schedule request");
            }
            if (JSON.stringify(requestBody.shifts) !== JSON.stringify([{ weekday: 0, start: "09:00", end: "17:30" }])) {
              throw new Error("save payload did not preserve half-hour values");
            }
            resolveRequest({ timezone: "Asia/Shanghai", engineers: [] });
            await pending;
            if (scheduleNotice !== "Schedule saved" || requestCount !== 1) throw new Error("save success was not retained");
            """
        )

    def test_admin_schedule_save_restores_editor_after_failure(self) -> None:
        self.run_admin_app_script(
            """
            FormData = function FormData() { return { get() { return ""; } }; };
            fetchJson = async () => { throw new Error("schedule unavailable"); };
            const attributes = new Map();
            const submit = { disabled: false, innerHTML: "save" };
            const errorNode = { textContent: "" };
            const form = {
              dataset: { engineerId: "zac" },
              querySelector(selector) { return selector.includes("submit") ? submit : errorNode; },
              setAttribute(name, value) { attributes.set(name, value); },
              removeAttribute(name) { attributes.delete(name); },
            };
            await handleScheduleUpdate(form);
            if (submit.disabled || submit.innerHTML !== "save" || form.dataset.submitting || attributes.has("aria-busy")) {
              throw new Error("failed save did not restore the editor");
            }
            if (errorNode.textContent !== "schedule unavailable") throw new Error("save error was not visible");
            """
        )

    def test_admin_uses_schedule_as_the_only_engineer_availability_state(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        self.assertNotIn("handleAvailabilityToggle", source)
        self.assertNotIn("toggle-availability", source)
        self.assertNotIn("availability_reason", source)
        self.assertNotIn("admin-availability-toggle", source + css)
        self.assertNotIn("availability_reassigned", source)

    def test_admin_schedule_uses_page_scroll_and_sidebar_scrollbar_is_hidden(self) -> None:
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        self.assertIn("max-height: none", css)
        self.assertIn("overflow: visible", css)
        self.assertIn("overflow-x: auto", css)
        self.assertIn("overflow-y: visible", css)
        self.assertNotIn("height: min(70vh, 720px)", css)
        self.assertIn(".admin-sidebar-body::-webkit-scrollbar", css)
        self.assertIn("scrollbar-width: none", css)

    def test_admin_weekly_schedule_splits_overnight_and_assigns_overlap_lanes(self) -> None:
        self.run_admin_app_script(
            """
            const engineers = [
              { account_id: "zac", display_name: "Zac", shifts: [
                { weekday: 6, start: "22:00", end: "06:00" },
                { weekday: 0, start: "09:00", end: "17:00" },
              ] },
              { account_id: "maya", display_name: "Maya", shifts: [
                { weekday: 0, start: "10:00", end: "14:00" },
              ] },
            ];
            const segments = assignScheduleLanes(buildScheduleSegments(engineers));
            const sunday = segments.find((segment) => segment.weekday === 6 && segment.startMinute === 1320 && segment.endMinute === 1440);
            const mondayOvernight = segments.find((segment) => segment.weekday === 0 && segment.startMinute === 0 && segment.endMinute === 360);
            const zacMonday = segments.find((segment) => segment.weekday === 0 && segment.startMinute === 540);
            const mayaMonday = segments.find((segment) => segment.weekday === 0 && segment.startMinute === 600);
            if (!sunday || !mondayOvernight) throw new Error("Sunday overnight shift was not split across the week boundary");
            if (sunday.label !== "22:00-24:00" || mondayOvernight.label !== "00:00-06:00") {
              throw new Error("overnight shift segments do not display their actual day-local time range");
            }
            if (sunday.laneCount !== 1 || mondayOvernight.laneCount !== 1) {
              throw new Error("non-overlapping overnight segments should occupy the full day column");
            }
            if (!zacMonday || !mayaMonday || zacMonday.lane === mayaMonday.lane || zacMonday.laneCount < 2 || mayaMonday.laneCount < 2) {
              throw new Error("overlapping Monday shifts were not assigned separate lanes");
            }
            const slots = buildScheduleSlots(engineers);
            const mondayTen = slots.filter((slot) => slot.weekday === 0 && slot.slotStart === 600);
            if (mondayTen.length !== 2 || mondayTen[0].lane === mondayTen[1].lane || mondayTen.some((slot) => slot.laneCount !== 2)) {
              throw new Error("overlapping engineers were not retained side by side in the same half-hour slot");
            }
            const zacMondaySlots = slots.filter((slot) => slot.engineer.account_id === "zac" && slot.weekday === 0 && slot.slotStart >= 540);
            if (zacMondaySlots.length !== 16) throw new Error("09:00-17:00 did not produce sixteen half-hour slots");
            const grid = renderWeeklyTimeGrid(engineers, ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]);
            if ((grid.match(/class="admin-week-slot"/g) || []).length !== slots.length) {
              throw new Error("the grid did not render one name block per schedule slot");
            }
            if (!grid.includes("<span>Zac</span>") || !grid.includes("<span>Maya</span>")) {
              throw new Error("schedule slots did not render engineer names");
            }
            """
        )

    def test_workspace_setup_page_uses_one_time_invitation_api(self) -> None:
        source = Path("ui/workspace-ui/setup/app.js").read_text(encoding="utf-8")
        html = Path("ui/workspace-ui/setup/index.html").read_text(encoding="utf-8")

        self.assertIn("/api/workspace/invitations/complete", source)
        self.assertIn("/api/workspace/invitations/${encodeURIComponent(token)}", source)
        self.assertIn('<span>Email</span>', source)
        self.assertIn('name="email"', source)
        self.assertIn('readonly aria-readonly="true"', source)
        self.assertNotIn('name="account_id"', source)
        self.assertNotIn("account_id:", source)
        self.assertIn('name="confirm_password"', source)
        self.assertIn("20260719-setup-email-identity-1", html)
        self.assertIn('id="workspace-setup-root"', html)


if __name__ == "__main__":
    unittest.main()
