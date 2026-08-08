from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from backend.services.graph_mail import send_graph_mail_with_token
from backend.services.internal_email_template import (
    INTERNAL_EMAIL_TEMPLATE_VERSION,
    InternalEmailSection,
    render_internal_handoff_email,
)


class _FakeResponse:
    status = 202

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class InternalEmailTemplateTests(unittest.TestCase):
    def test_surfaces_use_uniform_canvas_colors_for_outlook_dark_mode(self) -> None:
        rendered = render_internal_handoff_email(
            request_type="Enablement",
            title="Media Relay enablement request",
            ticket_id="12688",
            intro="A customer has requested backend feature enablement.",
            summary_fields=(("Ticket ID", "12688"),),
            sections=(
                InternalEmailSection(title="Neutral section", body="Details."),
                InternalEmailSection(title="Warning section", body="Needs review.", tone="warning"),
                InternalEmailSection(title="Success section", body="Verified.", tone="success"),
            ),
            original_message="Please enable the feature.",
            action_text="Please reply directly to this email.",
        )

        html = rendered["body_html"].lower()
        for color in (
            "#f3f8fa",
            "#f5fafc",
            "#f5f8fb",
            "#fafcfd",
            "#edf8fb",
            "#fff8ed",
            "#eef9f4",
        ):
            self.assertNotIn(f"background-color:{color}", html)
            self.assertNotIn(f"background:{color}", html)
        self.assertIn("background-color:#ffffff", html)
        self.assertIn("border-left", html)

    def test_theme_contract_uses_a_light_only_canvas_for_outlook_compatibility(self) -> None:
        rendered = render_internal_handoff_email(
            request_type="Enablement",
            title="Media Relay enablement request",
            ticket_id="12555",
            intro="A customer has requested feature enablement.",
            summary_fields=(("Customer email", "customer@example.com"),),
            action_text="Please reply directly to this email.",
        )

        html = rendered["body_html"]
        self.assertIn('<meta name="color-scheme" content="light">', html)
        self.assertIn('<meta name="supported-color-schemes" content="light">', html)
        self.assertIn("color-scheme: only light;", html)
        self.assertIn('bgcolor="#FFFFFF"', html)
        self.assertNotIn("prefers-color-scheme: dark", html)
        self.assertNotIn("[data-ogsc]", html)
        self.assertNotIn("#0C1C26", html)
        self.assertNotIn("#303941", html)

    def test_rendering_escapes_customer_values_and_preserves_plain_text(self) -> None:
        rendered = render_internal_handoff_email(
            request_type="Enablement",
            title="Media Relay <request>",
            ticket_id="12555",
            intro="A customer has requested feature enablement.",
            summary_fields=(("Customer email", "customer@example.com"),),
            sections=(
                InternalEmailSection(
                    title="Request details",
                    fields=(("App ID", "<img src=x onerror=alert(1)>"),),
                ),
            ),
            original_message="Please enable it.\n<script>alert(1)</script>",
            action_text="Please reply directly to this email.",
        )

        self.assertEqual(rendered["template_version"], INTERNAL_EMAIL_TEMPLATE_VERSION)
        self.assertEqual(rendered["body_content_type"], "HTML")
        self.assertIn("Media Relay <request>", rendered["body"])
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", rendered["body_html"])
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered["body_html"])
        self.assertNotIn("<img src=x", rendered["body_html"])
        self.assertNotIn("<script>", rendered["body_html"])
        self.assertNotIn("javascript:", rendered["body_html"].lower())

    def test_response_link_requires_http_or_https(self) -> None:
        invalid = render_internal_handoff_email(
            request_type="Invoice",
            title="Detailed invoice request",
            ticket_id="12556",
            intro="Review the invoice request.",
            summary_fields=(),
            action_text="Open the handling form.",
            action_url="javascript:alert(1)",
        )
        valid = render_internal_handoff_email(
            request_type="Invoice",
            title="Detailed invoice request",
            ticket_id="12557",
            intro="Review the invoice request.",
            summary_fields=(),
            action_text="Open the handling form.",
            action_url="https://support.example.test/response?token=abc&x=1",
        )

        self.assertNotIn("javascript:", invalid["body_html"].lower())
        self.assertNotIn("href=", invalid["body_html"])
        self.assertIn('href="https://support.example.test/response?token=abc&amp;x=1"', valid["body_html"])

    def test_graph_sendmail_uses_html_content_type_only_when_requested(self) -> None:
        requests = []

        def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
            del timeout
            requests.append(request)
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            send_graph_mail_with_token(
                access_token="access-token",
                to_address="internal@example.com",
                subject="Enablement",
                body="<p>Pretty</p>",
                content_type="HTML",
            )

        payload = json.loads(requests[0].data.decode("utf-8"))
        self.assertEqual(payload["message"]["body"], {"contentType": "HTML", "content": "<p>Pretty</p>"})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            send_graph_mail_with_token(
                access_token="access-token",
                to_address="internal@example.com",
                subject="Invitation",
                body="Plain text",
            )
        plain_payload = json.loads(requests[1].data.decode("utf-8"))
        self.assertEqual(plain_payload["message"]["body"], {"contentType": "Text", "content": "Plain text"})

        with self.assertRaisesRegex(ValueError, "Text or HTML"):
            send_graph_mail_with_token(
                access_token="access-token",
                to_address="internal@example.com",
                subject="Invalid",
                body="body",
                content_type="Markdown",
            )


if __name__ == "__main__":
    unittest.main()
