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
              const sandbox = {{
                console, Headers,
                window: {{ location: {{ pathname: "/workspace/admin/" }} }},
                document: {{ getElementById() {{ return root; }} }},
                localStorage: {{ getItem() {{ return null; }}, setItem() {{}}, removeItem() {{}} }},
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
            "data-assignment-form",
            "On Schedule Now",
            "Weekly Schedule",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("supportportal_assignment_admin_schedule", source)
        self.assertNotIn('/api/engineer/tickets?status=all', source)

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

    def test_admin_board_displays_client_and_assignment_status_separately(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        self.assertIn("Client status", source)
        self.assertIn("Assignment", source)
        self.assertIn("assignmentVersion", source)
        self.assertIn("expected_version", source)
        self.assertIn("SLA", source)

    def test_workspace_admin_assets_are_self_contained(self) -> None:
        html = Path("ui/workspace-ui/admin/index.html").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")
        self.assertIn('id="workspace-admin-root"', html)
        self.assertIn("./styles.css", html)
        self.assertIn("./app.js", html)
        self.assertIn(".admin-shell", css)
        self.assertIn(".admin-assignment-form", css)

    def test_workspace_admin_login_uses_transactional_entry_contract(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        html = Path("ui/workspace-ui/admin/index.html").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        for marker in (
            '<strong>Admin</strong>',
            "Welcome Back",
            "An administrative workspace for managing engineer access, assignments, availability, and SLA health.",
            "admin-login-card",
            "Secure Admin Workspace",
            '<span>Email</span>',
            'name="email"',
            'name="password"',
        ):
            self.assertIn(marker, source)
        self.assertNotIn("Account ID", source)
        self.assertIn("20260719-setup-email-identity-1", html)
        self.assertIn(".admin-login-header", css)
        self.assertIn(".admin-login-footer", css)
        self.assertIn("@media (max-width: 640px)", css)

    def test_admin_shell_uses_collapsed_rail_and_footer_account_controls(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        self.assertIn("admin-rail-footer", source)
        self.assertIn("admin-user-chip", source)
        self.assertIn("admin-logout-btn", source)
        self.assertNotIn("admin-topbar-btn", source)
        self.assertIn("grid-template-columns: 96px minmax(0, 1fr)", css)
        self.assertIn("width: 264px", css)

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

    def test_admin_weekly_schedule_uses_blue_time_grid(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        for marker in (
            "timeStringToMinutes",
            "buildScheduleSegments",
            "assignScheduleLanes",
            "renderWeeklyTimeGrid",
            "admin-week-grid",
            "admin-week-shift",
            "repeat(96, 12px)",
            "#cae6ff",
            "#101a44",
        ):
            self.assertIn(marker, source + css)
        self.assertNotIn("admin-roster-table", source + css)
        self.assertNotIn("#16262d", css)
        self.assertNotIn("#dff3f5", css)

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
              { account_id: "zac", display_name: "Zac", availability: "available", shifts: [
                { weekday: 6, start: "22:00", end: "06:00" },
                { weekday: 0, start: "09:00", end: "17:00" },
              ] },
              { account_id: "maya", display_name: "Maya", availability: "unavailable", shifts: [
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
