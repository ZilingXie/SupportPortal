from __future__ import annotations

from pathlib import Path
import unittest


class BillingResponseUiContractTests(unittest.TestCase):
    def test_backend_mounts_response_ui(self) -> None:
        source = Path("backend/main.py").read_text(encoding="utf-8")
        self.assertIn("BILLING_RESPONSE_DIR", source)
        self.assertIn(
            'app.mount("/response", StaticFiles(directory=BILLING_RESPONSE_DIR, html=True), name="billing-response-ui")',
            source,
        )

    def test_response_ui_contains_required_form_fields(self) -> None:
        app = Path("ui/billing-response-ui/app.js").read_text(encoding="utf-8")
        index = Path("ui/billing-response-ui/index.html").read_text(encoding="utf-8")
        styles = Path("ui/billing-response-ui/styles.css").read_text(encoding="utf-8")

        for term in ["completed", "refused", "customer_action_required", "notify_customer", "note"]:
            with self.subTest(term=term):
                self.assertIn(term, app)
        self.assertIn("/api/billing-response", app)
        self.assertIn("/api/billing-response/submit", app)
        self.assertIn("Submit handling result", index)
        self.assertIn("billing_ticket_id", app)
        self.assertIn("response-card", styles)

