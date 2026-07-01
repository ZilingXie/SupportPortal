from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services.billing_automation import (
    BILLING_ACTION_ACCOUNT_VERIFICATION,
    build_billing_automation_result,
    send_billing_internal_email,
)


GRAPH_ENV = {
    "BILLING_AUTOMATION_MAIL_TRANSPORT": "graph",
    "BILLING_AUTOMATION_GRAPH_TENANT_ID": "60275374-3eaa-49c2-83c3-cc189d126981",
    "BILLING_AUTOMATION_GRAPH_CLIENT_ID": "client-id",
    "BILLING_AUTOMATION_GRAPH_CLIENT_SECRET": "client-secret",
    "BILLING_AUTOMATION_GRAPH_USERNAME": "ai-support-agent@agora.io",
}


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, object] | None = None, status: int = 202) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        if self.payload is None:
            return b""
        return json.dumps(self.payload).encode("utf-8")


class BillingAutomationEmailTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patcher = patch.dict(
            os.environ,
            {
                key: ""
                for key in (
                    "BILLING_AUTOMATION_EMAIL_FROM",
                    "BILLING_AUTOMATION_MAIL_TRANSPORT",
                    "BILLING_AUTOMATION_GRAPH_TENANT_ID",
                    "BILLING_AUTOMATION_GRAPH_CLIENT_ID",
                    "BILLING_AUTOMATION_GRAPH_CLIENT_SECRET",
                    "BILLING_AUTOMATION_GRAPH_USERNAME",
                    "BILLING_AUTOMATION_GRAPH_TOKEN_CACHE",
                    "BILLING_AUTOMATION_SMTP_PASSWORD",
                )
            },
        )
        self._env_patcher.start()

    def tearDown(self) -> None:
        self._env_patcher.stop()

    def test_internal_email_defaults_to_company_outlook_sender(self) -> None:
        result = build_billing_automation_result(
            action=BILLING_ACTION_ACCOUNT_VERIFICATION,
            message=(
                "Company: ExampleCorp. Company location: Singapore. "
                "Website: https://example.com. Email: admin@example.com. "
                "Phone: +65-1234-5678. Use Case: internal video calls."
            ),
            ticket_id="TK-1",
            customer_email="customer@example.com",
        )

        self.assertIsNotNone(result.internal_email)
        assert result.internal_email is not None
        self.assertEqual(result.internal_email["from"], "ai-support-agent@agora.io")

    def test_send_billing_internal_email_uses_graph_sendmail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "billing-graph-token.json"
            cache_path.write_text(json.dumps({"refresh_token": "refresh-token"}), encoding="utf-8")
            requests = []

            def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
                requests.append(request)
                url = request.full_url
                if url.endswith("/oauth2/v2.0/token"):
                    return _FakeHttpResponse(
                        {
                            "access_token": "access-token",
                            "refresh_token": "new-refresh-token",
                            "expires_in": 3600,
                        },
                        status=200,
                    )
                if url == "https://graph.microsoft.com/v1.0/me/sendMail":
                    return _FakeHttpResponse(status=202)
                raise AssertionError(f"unexpected URL {url}")

            env = dict(GRAPH_ENV)
            env["BILLING_AUTOMATION_GRAPH_TOKEN_CACHE"] = str(cache_path)
            with patch.dict(os.environ, env), patch("urllib.request.urlopen", side_effect=fake_urlopen):
                result = send_billing_internal_email(
                    {
                        "to": "billing@example.com",
                        "from": "xieziling97@163.com",
                        "subject": "Account verification request",
                        "body": "Hi team,\nPlease review this account.",
                    }
                )

        self.assertEqual(result, {"status": "sent", "reason": ""})
        self.assertEqual(len(requests), 2)
        token_body = requests[0].data.decode("utf-8")
        self.assertIn("refresh_token=refresh-token", token_body)
        self.assertIn("client_secret=client-secret", token_body)
        self.assertEqual(requests[1].headers["Authorization"], "Bearer access-token")
        graph_payload = json.loads(requests[1].data.decode("utf-8"))
        self.assertEqual(graph_payload["message"]["subject"], "Account verification request")
        self.assertEqual(graph_payload["message"]["body"]["content"], "Hi team,\nPlease review this account.")
        self.assertEqual(
            graph_payload["message"]["toRecipients"],
            [{"emailAddress": {"address": "billing@example.com"}}],
        )
        self.assertNotIn("xieziling97@163.com", requests[1].data.decode("utf-8"))

    def test_send_billing_internal_email_never_uses_legacy_smtp(self) -> None:
        with patch.dict(
            os.environ,
            {
                "BILLING_AUTOMATION_MAIL_TRANSPORT": "",
                "BILLING_AUTOMATION_SMTP_HOST": "smtp.163.com",
                "BILLING_AUTOMATION_SMTP_USERNAME": "xieziling97@163.com",
                "BILLING_AUTOMATION_SMTP_PASSWORD": "legacy-password",
            },
        ), patch("smtplib.SMTP_SSL") as smtp_ssl:
            result = send_billing_internal_email(
                {
                    "to": "billing@example.com",
                    "from": "xieziling97@163.com",
                    "subject": "Account verification request",
                    "body": "Hi team",
                }
            )

        self.assertEqual(result["status"], "skipped_config_missing")
        self.assertIn("BILLING_AUTOMATION_GRAPH", result["reason"])
        smtp_ssl.assert_not_called()

    def test_send_billing_internal_email_accepts_msal_token_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "msal-cache.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "AccessToken": {
                            "token-key": {
                                "secret": "cached-access-token",
                                "expires_on": "4102444800",
                                "target": "Mail.Send User.Read",
                            }
                        },
                        "RefreshToken": {
                            "refresh-key": {
                                "secret": "cached-refresh-token",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            requests = []

            def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
                requests.append(request)
                return _FakeHttpResponse(status=202)

            env = dict(GRAPH_ENV)
            env["BILLING_AUTOMATION_GRAPH_TOKEN_CACHE"] = str(cache_path)
            with patch.dict(os.environ, env), patch("urllib.request.urlopen", side_effect=fake_urlopen):
                result = send_billing_internal_email(
                    {
                        "to": "billing@example.com",
                        "subject": "Account verification request",
                        "body": "Hi team",
                    }
                )

        self.assertEqual(result, {"status": "sent", "reason": ""})
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].full_url, "https://graph.microsoft.com/v1.0/me/sendMail")
        self.assertEqual(requests[0].headers["Authorization"], "Bearer cached-access-token")


if __name__ == "__main__":
    unittest.main()
