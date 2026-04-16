from __future__ import annotations

import unittest
from pathlib import Path


class ClientTestRouteSmokeTests(unittest.TestCase):
    def test_clienttest_static_mount_and_entrypoints_exist(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        self.assertIn('CLIENTTEST_DIR = UI_DIR / "clienttest-ui"', main_source)
        self.assertIn(
            'app.mount("/clienttest", StaticFiles(directory=CLIENTTEST_DIR, html=True), name="clienttest-ui")',
            main_source,
        )

        expected_files = [
            Path("ui/clienttest-ui/index.html"),
            Path("ui/clienttest-ui/styles.css"),
            Path("ui/clienttest-ui/app.js"),
        ]
        for file_path in expected_files:
            self.assertTrue(file_path.exists(), str(file_path))

    def test_clienttest_html_references_local_assets(self) -> None:
        html = Path("ui/clienttest-ui/index.html").read_text(encoding="utf-8")

        self.assertIn("./styles.css?v=20260416-clienttest-preview-shell-1", html)
        self.assertIn("./app.js?v=20260416-clienttest-preview-shell-1", html)


class ClientTestUiContractTests(unittest.TestCase):
    def test_clienttest_shell_uses_preview_branding_and_rail_labels(self) -> None:
        html = Path("ui/clienttest-ui/index.html").read_text(encoding="utf-8")
        app_source = Path("ui/clienttest-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/clienttest-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("<title>Support Portal - Client Preview</title>", html)
        self.assertIn("Support Portal", app_source)
        self.assertIn("Sid", app_source)
        self.assertIn('<span class="sidebar-nav-label">New Ticket</span>', app_source)
        self.assertIn('<span class="sidebar-nav-label">Workspace</span>', app_source)
        self.assertIn('<span class="sidebar-nav-label">My Tickets</span>', app_source)

        new_ticket_pos = app_source.index('<span class="sidebar-nav-label">New Ticket</span>')
        workspace_pos = app_source.index('<span class="sidebar-nav-label">Workspace</span>')
        my_tickets_pos = app_source.index('<span class="sidebar-nav-label">My Tickets</span>')
        self.assertLess(new_ticket_pos, workspace_pos)
        self.assertLess(workspace_pos, my_tickets_pos)

        self.assertIn("clienttest-shell", css)
        self.assertIn("clienttest-sidebar", css)
        self.assertIn("clienttest-main", css)

    def test_clienttest_ticket_detail_exposes_right_sidebar_and_enhanced_composer(self) -> None:
        app_source = Path("ui/clienttest-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/clienttest-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("Ticket Information", app_source)
        self.assertIn("AI Summary", app_source)
        self.assertIn("Related Knowledge", app_source)
        self.assertLess(app_source.index("Ticket Information"), app_source.index("AI Summary"))
        self.assertLess(app_source.index("AI Summary"), app_source.index("Related Knowledge"))

        self.assertIn("ticket-detail-layout", css)
        self.assertIn("ticket-detail-sidebar", css)
        self.assertIn("ticket-detail-composer", css)
        self.assertIn("ticket-detail-toolbar", css)

    def test_clienttest_reuses_existing_client_runtime_contracts(self) -> None:
        app_source = Path("ui/clienttest-ui/app.js").read_text(encoding="utf-8")

        self.assertIn('const AUTH_KEY = "helpdesk_auth_user";', app_source)
        self.assertIn('const TICKETS_KEY = "helpdesk_tickets";', app_source)
        self.assertIn('const COUNTER_KEY = "helpdesk_ticket_counter";', app_source)
        self.assertIn('/api/client/ack', app_source)
        self.assertIn('/ws/client', app_source)


if __name__ == "__main__":
    unittest.main()
