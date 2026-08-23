from __future__ import annotations

import unittest
from pathlib import Path


class AutomationTestUiContractTests(unittest.TestCase):
    def test_mount_and_assets_exist(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")

        self.assertIn('AUTOMATION_TEST_DIR = UI_DIR / "automation-test"', main_source)
        self.assertIn(
            'app.mount(\n        "/automation/test",\n        StaticFiles(directory=AUTOMATION_TEST_DIR, html=True),\n        name="automation-test-ui",\n    )',
            main_source,
        )

        expected_files = [
            Path("ui/automation-test/index.html"),
            Path("ui/automation-test/styles.css"),
            Path("ui/automation-test/app.js"),
        ]
        for file_path in expected_files:
            self.assertTrue(file_path.exists(), str(file_path))

    def test_no_cache_prefix_is_registered(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        self.assertIn('"/automation/test",', main_source)

    def test_api_endpoints_are_registered_with_admin_guard(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        for marker in (
            '"/api/automation-test/templates"',
            '"/api/automation-test/tickets"',
            '"/api/automation-test/tickets/{ticket_id}/refresh"',
            "dependencies=[Depends(require_workspace_admin)]",
        ):
            self.assertIn(marker, main_source)

    def test_nginx_routes_console_to_production_api(self) -> None:
        nginx = Path("deployment/nginx/supportportal.conf").read_text(encoding="utf-8")
        self.assertIn("location = /automation/test {", nginx)
        self.assertIn("location /automation/test/ {", nginx)
        automation_block = nginx[nginx.index("location /automation/test/ {") :]
        automation_block = automation_block[: automation_block.index("}")]
        self.assertIn("proxy_pass http://$production_api;", automation_block)

    def test_html_uses_local_assets_with_version_stamp(self) -> None:
        html = Path("ui/automation-test/index.html").read_text(encoding="utf-8")
        self.assertIn("<title>Automation Test</title>", html)
        self.assertIn("./styles.css?v=20260823-automation-test-1", html)
        self.assertIn("./app.js?v=20260823-automation-test-1", html)

    def test_app_uses_workspace_login_via_production_api_base(self) -> None:
        app_source = Path("ui/automation-test/app.js").read_text(encoding="utf-8")
        self.assertIn('const API_BASE = "/production";', app_source)
        self.assertIn('`${API_BASE}/api/workspace/auth/login`', app_source)
        self.assertIn('`${API_BASE}/api/workspace/me`', app_source)
        self.assertIn('"/api/automation-test/templates"', app_source)
        self.assertIn('"/api/automation-test/tickets?limit=100"', app_source)
        self.assertIn("/api/automation-test/tickets/", app_source)
        # The console must warn about real Zendesk side effects before sending.
        self.assertIn("real Zendesk ticket", app_source)
        self.assertIn("window.confirm(", app_source)
        # Subject/body are editable before sending.
        self.assertIn("data-at-subject", app_source)
        self.assertIn("data-at-body", app_source)


if __name__ == "__main__":
    unittest.main()
