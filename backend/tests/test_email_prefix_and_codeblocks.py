from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.services.billing_automation import (
    BILLING_ACTION_ACCOUNT_SUSPENSION,
    BILLING_ACTION_ACCOUNT_VERIFICATION,
    BILLING_ACTION_DETAILED_INVOICE,
    build_billing_internal_email_payload,
    _REQUEST_TYPE_BY_ACTION,
)
from backend.services.zendesk_comments import _fenced_code_html_body


class EmailRequestTypeTest(unittest.TestCase):
    """Suspension/verification/invoice emails must not say 'Billing:' in the body."""

    def test_request_type_mapping(self) -> None:
        self.assertEqual(
            _REQUEST_TYPE_BY_ACTION.get(BILLING_ACTION_ACCOUNT_SUSPENSION),
            "Account Suspension",
        )
        self.assertEqual(
            _REQUEST_TYPE_BY_ACTION.get(BILLING_ACTION_ACCOUNT_VERIFICATION),
            "Account Verification",
        )
        self.assertEqual(
            _REQUEST_TYPE_BY_ACTION.get(BILLING_ACTION_DETAILED_INVOICE),
            "Detailed Invoice",
        )
        self.assertIsNone(_REQUEST_TYPE_BY_ACTION.get("unknown_action"))

    def test_suspension_email_body_uses_suspension_prefix(self) -> None:
        payload = build_billing_internal_email_payload(
            action=BILLING_ACTION_ACCOUNT_SUSPENSION,
            collected_fields={},
            ticket_id="12953",
            customer_email="test@example.com",
            customer_message="Please review my account",
            billing_ticket_id="AC-12953",
        )
        body = payload.get("body") or ""
        self.assertIn("Account Suspension:", body)
        self.assertNotIn("Billing: Account suspension", body)


class FencedCodeHtmlBodyTest(unittest.TestCase):
    """Fenced Markdown code blocks must render as <pre><code> in html_body."""

    def test_no_code_block_returns_none(self) -> None:
        self.assertIsNone(_fenced_code_html_body("Plain text only\nsecond line"))
        self.assertIsNone(_fenced_code_html_body(""))
        self.assertIsNone(_fenced_code_html_body("Just backticks: ``` but no fence"))

    def test_simple_code_block(self) -> None:
        body = "Here is the code:\n```python\nprint('hello')\n```\nDone."
        result = _fenced_code_html_body(body)
        self.assertIsNotNone(result)
        self.assertIn("<pre><code>print(&#x27;hello&#x27;)</code></pre>", result)
        self.assertIn("<p>Here is the code:</p>", result)
        self.assertIn("<p>Done.</p>", result)
        # Plain-text markers must not appear in HTML.
        self.assertNotIn("```", result)

    def test_html_escaping_inside_code(self) -> None:
        body = "```html\n<div class=\"test\">&amp;</div>\n```"
        result = _fenced_code_html_body(body)
        self.assertIsNotNone(result)
        self.assertIn("&lt;div class=&quot;test&quot;&gt;&amp;amp;&lt;/div&gt;", result)

    def test_multiple_code_blocks(self) -> None:
        body = (
            "First block:\n```js\nconst x = 1;\n```\n"
            "Between blocks.\n"
            "```python\ny = 2\n```\nEnd."
        )
        result = _fenced_code_html_body(body)
        self.assertIsNotNone(result)
        self.assertIn("<pre><code>const x = 1;</code></pre>", result)
        self.assertIn("<pre><code>y = 2</code></pre>", result)
        self.assertIn("<p>Between blocks.</p>", result)
        self.assertIn("<p>End.</p>", result)

    def test_code_only_body(self) -> None:
        body = "```python\nx = 1\n```"
        result = _fenced_code_html_body(body)
        self.assertIsNotNone(result)
        self.assertEqual(result, "<pre><code>x = 1</code></pre>")


if __name__ == "__main__":
    unittest.main()
