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
              const userScript = {script!r};
              const root = {{
                innerHTML: "",
                addEventListener() {{}},
                querySelector() {{ return null; }},
              }};
              const sandbox = {{
                console,
                window: {{
                  location: {{ pathname: "/workspace/admin/" }},
                  setInterval() {{ return 0; }},
                }},
                document: {{ getElementById() {{ return root; }} }},
                localStorage: {{
                  getItem() {{ return null; }},
                  setItem() {{}},
                  removeItem() {{}},
                }},
                fetch: async () => ({{ ok: true, json: async () => ({{ tickets: [] }}) }}),
                setInterval() {{ return 0; }},
                clearInterval() {{}},
              }};
              sandbox.globalThis = sandbox;
              vm.createContext(sandbox);
              vm.runInContext(fs.readFileSync("ui/workspace-ui/admin/app.js", "utf8"), sandbox);
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

    def test_workspace_admin_replaces_assignment_route(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")

        self.assertIn('WORKSPACE_DIR = UI_DIR / "workspace-ui"', main_source)
        self.assertIn(
            'app.mount("/workspace", StaticFiles(directory=WORKSPACE_DIR, html=True), name="workspace-ui")',
            main_source,
        )
        self.assertNotIn("ASSIGNMENT_DIR", main_source)
        self.assertNotIn('app.mount("/assignment"', main_source)
        self.assertFalse(Path("ui/assignment-ui").exists())

        for path in (
            Path("ui/workspace-ui/admin/index.html"),
            Path("ui/workspace-ui/admin/styles.css"),
            Path("ui/workspace-ui/admin/app.js"),
        ):
            self.assertTrue(path.exists(), f"{path} should exist")

    def test_workspace_admin_assets_and_navigation_are_self_contained(self) -> None:
        html = Path("ui/workspace-ui/admin/index.html").read_text(encoding="utf-8")
        app_source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")

        self.assertIn("System Admin", html)
        self.assertIn('id="workspace-admin-root"', html)
        self.assertIn("./styles.css?v=20260713-workspace-admin-3", html)
        self.assertIn("./app.js?v=20260713-workspace-admin-4", html)
        self.assertIn('document.getElementById("workspace-admin-root")', app_source)
        self.assertIn('href="/workspace"', app_source)
        self.assertNotIn('href="/assignment"', app_source)

    def test_workspace_admin_retains_management_views(self) -> None:
        app_source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        for marker in (
            "renderAdmin",
            "renderAdminShell",
            "renderAdminEngineerManagement",
            "renderAdminTicketBoard",
            "Engineer Management",
            "Active Ticket",
            "Resolved Ticket",
            "Operations Overview",
            "Shift Schedule",
            "Online Coverage",
            "admin-save-shift",
            "supportportal_assignment_admin_schedule",
            'fetch("/api/engineer/tickets?status=all")',
            "normalizeAdminTicket",
            "adminTicketsLoading",
            "adminTicketsError",
        ):
            self.assertIn(marker, app_source)

        self.assertIn('ticket.viewStatus === "resolved"', app_source)
        self.assertIn('ticket.viewStatus === "investigating"', app_source)
        self.assertIn("Assigned to ${ticket.assignedEngineerId}", app_source)
        self.assertIn("updates automatically", app_source)
        self.assertNotIn("Escalation Context", app_source)
        self.assertNotIn("admin-refresh-tickets", app_source)
        self.assertNotIn("Ticket Pool", app_source)
        self.assertNotIn("Sorted by Urgency", app_source)
        self.assertNotIn("US-East", app_source)

        for marker in (
            ".admin-shell",
            ".admin-topbar",
            ".admin-metric-grid",
            ".admin-schedule-grid",
            ".admin-edit-panel",
            ".admin-ticket-pool-layout",
        ):
            self.assertIn(marker, css)

    def test_workspace_admin_splits_pending_assigned_and_resolved_tickets(self) -> None:
        self.run_admin_app_script(
            """
            const pending = normalizeAdminTicket({
              engineer_case_id: "TK-PENDING-1",
              title: "Pending case",
              status: "investigating",
            });
            const assigned = normalizeAdminTicket({
              engineer_case_id: "TK-ACTIVE-1",
              title: "Assigned case",
              status: "investigating",
              assigned_engineer_id: "Maya",
            });
            const resolved = normalizeAdminTicket({
              engineer_case_id: "TK-RESOLVED-1",
              title: "Resolved case",
              status: "resolved",
              assigned_engineer_id: "Maya",
            });
            if (pending.viewStatus !== "pending" || assigned.viewStatus !== "investigating" || resolved.viewStatus !== "resolved") {
              throw new Error("admin status mapping is incorrect");
            }
            adminTickets = [pending, assigned, resolved];
            const activeHtml = renderAdminTicketBoard("active-tickets");
            if (!activeHtml.includes("TK-PENDING-1") || !activeHtml.includes("TK-ACTIVE-1")) {
              throw new Error("active board should include pending and assigned tickets");
            }
            if (!activeHtml.includes("Assigned to Maya") || activeHtml.includes("TK-RESOLVED-1")) {
              throw new Error("active board assignment or filtering is incorrect");
            }
            if (!activeHtml.includes("Tickets in queue</span><strong>2") ||
                !activeHtml.includes("Assigned</span><strong>1") ||
                !activeHtml.includes("Pending assignment</span><strong>1")) {
              throw new Error("active board ticket counts are incorrect");
            }
            if (activeHtml.includes("Awaiting assignment")) {
              throw new Error("pending ticket card should not render assignment helper text");
            }
            const resolvedHtml = renderAdminTicketBoard("resolved-tickets");
            if (!resolvedHtml.includes("TK-RESOLVED-1") || resolvedHtml.includes("TK-ACTIVE-1")) {
              throw new Error("resolved board filtering is incorrect");
            }
            if (activeHtml.includes("Escalation Context") || activeHtml.includes("Refresh Tickets")) {
              throw new Error("simplified ticket card rendered removed controls");
            }
            """
        )


if __name__ == "__main__":
    unittest.main()
