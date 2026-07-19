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
              function element(id = "") {{
                const classes = new Set();
                return {{
                  id, innerHTML: "", textContent: "", value: "", dataset: {{}}, disabled: false,
                  classList: {{
                    add(...names) {{ names.forEach((name) => classes.add(String(name))); }},
                    remove(...names) {{ names.forEach((name) => classes.delete(String(name))); }},
                    toggle(name, force) {{
                      if (force === true) {{ classes.add(String(name)); return true; }}
                      if (force === false) {{ classes.delete(String(name)); return false; }}
                      return false;
                    }},
                    contains(name) {{ return classes.has(String(name)); }},
                  }},
                  addEventListener() {{}}, removeEventListener() {{}}, querySelector() {{ return null; }},
                  querySelectorAll() {{ return []; }}, closest() {{ return null; }}, focus() {{}},
                  scrollIntoView() {{}}, setSelectionRange() {{}},
                }};
              }}
              const elements = new Map();
              const storage = new Map();
              const fetchCalls = [];
              const fetchResponses = [];
              const sandbox = {{
                console, URL, Headers,
                FormData: function FormData(form) {{
                  return {{ get(name) {{ return form?.__formData?.[name] || ""; }} }};
                }},
                window: {{
                  location: {{ hash: "", protocol: "http:", host: "localhost:8080", assign() {{}}, reload() {{}} }},
                  addEventListener() {{}}, alert(message) {{ throw new Error(message); }},
                  __fetchCalls: fetchCalls, __fetchResponses: fetchResponses,
                }},
                document: {{
                  getElementById(id) {{ if (!elements.has(id)) elements.set(id, element(id)); return elements.get(id); }},
                  addEventListener() {{}}, querySelector() {{ return null; }}, querySelectorAll() {{ return []; }},
                }},
                localStorage: {{
                  getItem(key) {{ return storage.has(key) ? storage.get(key) : null; }},
                  setItem(key, value) {{ storage.set(key, String(value)); }},
                  removeItem(key) {{ storage.delete(key); }},
                }},
                fetch: async (url, options = {{}}) => {{
                  fetchCalls.push({{ url: String(url), options }});
                  const payload = fetchResponses.length ? fetchResponses.shift() : {{ cases: [] }};
                  return {{ ok: true, status: 200, json: async () => payload }};
                }},
                WebSocket: function WebSocket() {{ this.readyState = 1; this.close = () => {{}}; this.send = () => {{}}; }},
                HTMLTextAreaElement: function HTMLTextAreaElement() {{}},
                setTimeout(callback) {{ if (typeof callback === "function") callback(); return 0; }},
                clearTimeout() {{}}, setInterval() {{ return 0; }}, clearInterval() {{}},
              }};
              sandbox.globalThis = sandbox;
              vm.createContext(sandbox);
              vm.runInContext(fs.readFileSync("ui/shared-ui/composer.js", "utf8"), sandbox);
              vm.runInContext(fs.readFileSync("ui/workspace-ui/app.js", "utf8"), sandbox);
              await vm.runInContext(`(async () => {{\\n${{userScript}}\\n}})()`, sandbox);
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

    def test_workspace_ui_is_served_as_independent_static_page(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        self.assertIn('app.mount("/workspace", StaticFiles(directory=WORKSPACE_DIR, html=True)', main_source)
        self.assertIn('app.mount("/engineer", StaticFiles(directory=ENGINEER_DIR, html=True)', main_source)
        self.assertNotIn('app.mount("/assignment"', main_source)

    def test_workspace_uses_real_auth_and_assigned_case_contract(self) -> None:
        source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")
        self.assertIn('fetchJson("/api/workspace/auth/login"', source)
        self.assertIn('fetchJson("/api/workspace/cases?assignment_status=assigned")', source)
        self.assertIn("supportportal_workspace_access_token", source)
        self.assertIn("supportportal_workspace_account", source)
        self.assertIn("assignment_status", source)
        self.assertIn("sla_due_at", source)
        self.assertIn("response.status === 401 && storedToken", source)
        self.assertIn("/ws/workspace?access_token=", source)
        self.assertNotIn("/ws/engineer", source)
        self.assertNotIn("/claim", source)
        self.assertNotIn("/api/engineer", source)
        self.assertNotIn("supportportal_workspace_daily_shift", source)
        self.assertNotIn("Choose a demo engineer", source)

    def test_workspace_login_uses_transactional_entry_contract(self) -> None:
        source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")
        html = Path("ui/workspace-ui/index.html").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/styles.css").read_text(encoding="utf-8")

        for marker in (
            '<strong>Workspace</strong>',
            "Welcome Back",
            "A focused workspace for support engineers to investigate and resolve assigned cases.",
            "workspace-login-card",
            "Secure Engineer Workspace",
            '<span>Email</span>',
            'name="email"',
            'name="password"',
        ):
            self.assertIn(marker, source)
        self.assertNotIn("Account ID", source)
        self.assertIn("20260719-setup-email-identity-1", html)
        self.assertIn(".workspace-login-header", css)
        self.assertIn(".workspace-login-footer", css)
        self.assertIn("@media (max-width: 640px)", css)

    def test_workspace_uses_top_right_account_controls_without_sidebar(self) -> None:
        source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")
        html = Path("ui/workspace-ui/index.html").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/styles.css").read_text(encoding="utf-8")

        for marker in (
            'class="workspace-utility-bar"',
            'id="header-user-controls"',
            'class="header-user-controls workspace-account-controls"',
            'title="Logout"',
            'aria-label="Logout"',
            ".workspace-utility-bar",
            ".workspace-account-controls",
            "justify-content: flex-end",
        ):
            self.assertIn(marker, source + html + css)
        self.assertNotIn("workspace-assignment-sidebar", html)
        self.assertNotIn("renderWorkspaceAssignmentSidebar", source)
        self.assertNotIn("workspaceAssignmentSidebarEl", source)

    def test_workspace_login_stops_at_home_until_engineer_is_ready(self) -> None:
        source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")

        login_start = source.index("async function handleLoginSubmit")
        login_end = source.index("function resetWorkspaceBoardState", login_start)
        login_source = source[login_start:login_end]
        self.assertIn("saveWorkspaceActive(false)", login_source)
        self.assertIn("await loadWorkspaceSchedule()", login_source)
        self.assertNotIn("readyToRoll()", login_source)
        self.assertIn("Ready to roll", source)
        self.assertIn('fetchJson("/api/workspace/cases?assignment_status=assigned")', source)

    def test_workspace_home_renders_personal_weekly_schedule(self) -> None:
        source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/styles.css").read_text(encoding="utf-8")

        for marker in (
            'const WORKSPACE_SCHEDULE_ENDPOINT = "/api/workspace/schedule"',
            "renderPersonalScheduleHtml",
            "Personal weekly schedule",
            "Monday",
            "Ends next day",
            "workspace-personal-schedule",
            "workspace-schedule-week",
            "repeat(7, minmax(112px, 1fr))",
        ):
            self.assertIn(marker, source + css)

    def test_workspace_disables_multi_agent_from_controlled_launch_main_flow(self) -> None:
        source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")
        self.assertIn("const ENGINEER_MULTI_AGENT_ENABLED = false;", source)
        self.assertIn(
            'viewState.clientStatus !== "investigating" || !ENGINEER_MULTI_AGENT_ENABLED',
            source,
        )

    def test_workspace_detail_separates_client_and_assignment_status(self) -> None:
        source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")
        self.assertIn(
            "ticket.client_status || ticket?.client_ticket_ref?.status || \"open\"",
            source,
        )
        self.assertIn('ticket.assignment_status || "pending"', source)
        self.assertIn("Client ${escapeHtml(statusLabel(viewState.clientStatus))}", source)
        self.assertIn("Assignment ${escapeHtml(statusLabel(viewState.assignmentStatus))}", source)

    def test_workspace_selects_only_system_assigned_case_for_current_engineer(self) -> None:
        self.run_workspace_app_script(
            """
            const selected = findNextInvestigatingCase({ cases: [
              { engineer_case_id: "CASE-PENDING", assignment_status: "pending", assigned_engineer_id: null },
              { engineer_case_id: "CASE-OTHER", assignment_status: "assigned", assigned_engineer_id: "Leo" },
              { engineer_case_id: "CASE-MAYA", assignment_status: "assigned", assigned_engineer_id: "Maya" },
            ] }, "Maya");
            if (!selected || selected.engineer_case_id !== "CASE-MAYA") {
              throw new Error("workspace selected an unassigned or another engineer's case");
            }
            """
        )

    def test_workspace_sla_uses_server_due_at(self) -> None:
        self.run_workspace_app_script(
            """
            const dueAt = new Date(Date.now() + 60 * 60 * 1000).toISOString();
            const state = workspaceTicketSlaState({ assigned_at: new Date().toISOString(), sla_due_at: dueAt });
            if (state.overdue || state.remainingMs < 59 * 60 * 1000 || state.remainingMs > 61 * 60 * 1000) {
              throw new Error("workspace did not use server sla_due_at");
            }
            """
        )

    def test_workspace_keeps_guardrail_and_final_approve_controls(self) -> None:
        source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")
        self.assertIn("final_approve", source)
        self.assertIn("run_engineer_guardrail", Path("backend/main.py").read_text(encoding="utf-8"))
        self.assertIn("Engineer Case Thread", source)
        self.assertIn("Break after this case", source)


if __name__ == "__main__":
    unittest.main()
