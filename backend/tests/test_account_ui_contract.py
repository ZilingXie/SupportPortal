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
        self.assertIn("automation", app_source)
        self.assertIn("No tickets yet", app_source)
        self.assertIn("Ticket detail", app_source)
        self.assertIn("Recent tickets", app_source)
        self.assertIn('"manual"', app_source)
        self.assertIn('"api"', app_source)
        self.assertIn("sourceLabel", app_source)
        self.assertIn("sourceClass", app_source)
        self.assertIn("renderSourceValue", app_source)
        self.assertIn("safeSourceLink", app_source)
        self.assertIn('target="_blank"', app_source)
        self.assertIn('rel="noopener noreferrer"', app_source)
        self.assertIn('parsed.protocol === "http:"', app_source)

    def test_account_app_contains_filter_state_and_reply_composer(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")

        # Filter state and options.
        self.assertIn("statusFilter", app_source)
        self.assertIn("renderFilterControls", app_source)
        self.assertIn("all", app_source)
        self.assertIn("Automation", app_source)
        self.assertIn("Not automated", app_source)
        self.assertIn("matchesFilter", app_source)
        self.assertIn("filter-chip", app_source)

        # Reply composer state and flow.
        self.assertIn("replyMessage", app_source)
        self.assertIn("isSubmittingReply", app_source)
        self.assertIn("replyError", app_source)
        self.assertIn("renderReplyComposer", app_source)
        self.assertIn("submitReply", app_source)
        self.assertIn("renderMessageThread", app_source)
        self.assertIn("msg-bubble", app_source)

        # Reply endpoint references.
        self.assertIn("/api/account/billing-tickets/", app_source)
        self.assertIn("/reply", app_source)
        self.assertIn("/api/tickets/query", app_source)

    def test_account_app_javascript_syntax(self) -> None:
        result = subprocess.run(
            ["node", "--check", "ui/account-ui/app.js"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_account_styles_include_filter_message_reply_classes(self) -> None:
        styles = Path("ui/account-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn(".status-badge--automation", styles)
        self.assertIn(".source-link", styles)
        # Filter chips.
        self.assertIn(".filter-chips", styles)
        self.assertIn(".filter-chip", styles)
        self.assertIn(".filter-chip--active", styles)
        # Message thread.
        self.assertIn(".message-thread", styles)
        self.assertIn(".msg-bubble", styles)
        self.assertIn(".msg-bubble--customer", styles)
        self.assertIn(".msg-bubble--assistant", styles)
        self.assertIn(".msg-row", styles)
        # Reply composer.
        self.assertIn(".reply-composer", styles)
        self.assertIn(".reply-textarea", styles)
        self.assertIn(".reply-actions", styles)

    def test_account_app_source_link_contract(self) -> None:
        app_source = Path("ui/account-ui/app.js").read_text(encoding="utf-8")

        # safeSourceLink supports Link, link, url field variants.
        self.assertIn("source.Link", app_source)
        self.assertIn("source.link", app_source)
        self.assertIn("source.url", app_source)

        # renderSourceValue has zen# label for zendesk.com ticket links.
        self.assertIn("zendesk.com", app_source)
        self.assertIn("zen#", app_source)
        self.assertIn("zendeskTicketLabel", app_source)

        # Keep existing safety markers.
        self.assertIn('target="_blank"', app_source)
        self.assertIn('rel="noopener noreferrer"', app_source)
        self.assertIn('parsed.protocol === "http:"', app_source)
        self.assertIn('parsed.protocol === "https:"', app_source)
