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
            "/api/workspace/cases?assignment_status=all",
            "data-create-account-form",
            "data-availability-form",
            "data-assignment-form",
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

    def test_workspace_admin_login_uses_unified_admin_entry_contract(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        html = Path("ui/workspace-ui/admin/index.html").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        for marker in (
            "Agora internal platform",
            "Controlled workspace operations for technical support teams.",
            "Sign in to Admin Workspace",
            "admin-login-highlights",
            'name="account_id"',
            'name="password"',
        ):
            self.assertIn(marker, source)
        self.assertIn("20260719-workspace-login-refresh-1", html)
        self.assertIn("grid-template-columns: minmax(0, 1.35fr) minmax(400px, 1fr)", css)
        self.assertIn("@media (max-width: 560px)", css)


if __name__ == "__main__":
    unittest.main()
