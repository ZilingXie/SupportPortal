from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.services.graph_mail import (
    DEFAULT_AUTOMATION_INTERNAL_EMAIL_CC,
    automation_internal_email_cc,
    send_graph_mail_with_token,
)


class AutomationEmailCcTest(unittest.TestCase):
    """All automation internal emails must cc the automation owner."""

    def test_default_cc(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUTOMATION_INTERNAL_EMAIL_CC", None)
            self.assertEqual(automation_internal_email_cc(), [DEFAULT_AUTOMATION_INTERNAL_EMAIL_CC])
            self.assertEqual(DEFAULT_AUTOMATION_INTERNAL_EMAIL_CC, "xieziling@agora.io")

    def test_cc_env_override(self) -> None:
        with patch.dict(os.environ, {"AUTOMATION_INTERNAL_EMAIL_CC": "other@agora.io"}):
            self.assertEqual(automation_internal_email_cc(), ["other@agora.io"])

    def test_graph_payload_includes_cc_recipients(self) -> None:
        import json
        import urllib.request
        from unittest.mock import MagicMock

        with patch.object(urllib.request, "urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_response.status = 202
            mock_urlopen.return_value = mock_response

            send_graph_mail_with_token(
                access_token="fake-token",
                to_address="suhrid.das@agora.io",
                subject="Test",
                body="Hello",
                cc_addresses=["xieziling@agora.io"],
            )

            request = mock_urlopen.call_args.args[0]
            payload = json.loads(request.data.decode("utf-8"))
            message = payload["message"]
            self.assertEqual(
                message["toRecipients"],
                [{"emailAddress": {"address": "suhrid.das@agora.io"}}],
            )
            self.assertEqual(
                message["ccRecipients"],
                [{"emailAddress": {"address": "xieziling@agora.io"}}],
            )

    def test_graph_payload_no_cc_when_empty(self) -> None:
        import json
        import urllib.request
        from unittest.mock import MagicMock

        with patch.object(urllib.request, "urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_response.status = 202
            mock_urlopen.return_value = mock_response

            send_graph_mail_with_token(
                access_token="fake-token",
                to_address="someone@example.com",
                subject="Test",
                body="Hello",
                cc_addresses=None,
            )

            request = mock_urlopen.call_args.args[0]
            payload = json.loads(request.data.decode("utf-8"))
            self.assertNotIn("ccRecipients", payload["message"])


if __name__ == "__main__":
    unittest.main()
