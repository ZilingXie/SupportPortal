from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


class AccountUiContractTests(unittest.TestCase):
    def test_account_mount_and_assets_exist(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")

        self.assertIn('ACCOUNT_DIR = UI_DIR / "account-ui"', main_source)
        self.assertIn('app.mount("/account", StaticFiles(directory=ACCOUNT_DIR, html=True), name="account-ui")', main_source)

        expected_files = [
            Path("ui/account-ui/index.html"),
            Path("ui/account-ui/styles.css"),
            Path("ui/account-ui/app.js"),
        ]
        for file_path in expected_files:
            self.assertTrue(file_path.exists(), str(file_path))

    def test_account_html_uses_client_shared_assets(self) -> None:
        html = Path("ui/account-ui/index.html").read_text(encoding="utf-8")

        self.assertIn("<title>Account Intake</title>", html)
        self.assertIn('/shared-ui/composer.css', html)
        self.assertIn('/shared-ui/composer.js', html)
        self.assertIn("./styles.css", html)
        self.assertIn("./app.js", html)

    def test_account_app_posts_title_and_question_to_account_endpoint(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")

        self.assertIn('fetch("/account"', app_source)
        self.assertIn('fetch("/api/account/billing-tickets', app_source)
        self.assertIn("title", app_source)
        self.assertIn("question", app_source)
        self.assertIn("billing_ticket_id", app_source)
        self.assertIn("history", app_source)
        self.assertIn("renderHistorySidebar", app_source)
        self.assertIn("renderDetailView", app_source)
        self.assertIn("not_automated", app_source)
        self.assertIn("needs_more_info", app_source)

    def test_account_app_javascript_syntax(self) -> None:
        result = subprocess.run(
            ["node", "--check", "ui/account-ui/app.js"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
