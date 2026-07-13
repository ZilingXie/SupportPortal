from __future__ import annotations

import unittest
from pathlib import Path


class WorkspaceAdminUiContractTests(unittest.TestCase):
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
        self.assertIn("./styles.css?v=20260713-workspace-admin-1", html)
        self.assertIn("./app.js?v=20260713-workspace-admin-1", html)
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
            "renderAdminTicketPool",
            "Ticket Pool",
            "Engineer Management",
            "Operations Overview",
            "Shift Schedule",
            "Online Coverage",
            "admin-save-shift",
            "supportportal_assignment_admin_schedule",
        ):
            self.assertIn(marker, app_source)

        for marker in (
            ".admin-shell",
            ".admin-topbar",
            ".admin-metric-grid",
            ".admin-schedule-grid",
            ".admin-edit-panel",
            ".admin-ticket-pool-layout",
        ):
            self.assertIn(marker, css)


if __name__ == "__main__":
    unittest.main()
