import base64
import os
import unittest
from unittest.mock import patch

from backend.services.zendesk_comments import ZendeskCommentError, zendesk_basic_auth_header


class ZendeskBasicAuthHeaderTests(unittest.TestCase):
    def _env(self, value):
        return patch.dict(os.environ, {"zendesk_basic_auth": value}, clear=False)

    def test_literal_email_token_is_accepted(self):
        with self._env("agent@example.io/token:secret-token"):
            self.assertEqual(
                zendesk_basic_auth_header(),
                "Basic " + base64.b64encode(b"agent@example.io/token:secret-token").decode("ascii"),
            )

    def test_base64_form_is_accepted(self):
        encoded = base64.b64encode(b"agent@example.io/token:secret-token").decode("ascii")
        with self._env(encoded):
            self.assertEqual(
                zendesk_basic_auth_header(),
                "Basic " + base64.b64encode(b"agent@example.io/token:secret-token").decode("ascii"),
            )

    def test_basic_prefix_is_stripped(self):
        encoded = base64.b64encode(b"user:token").decode("ascii")
        with self._env("Basic " + encoded):
            self.assertEqual(zendesk_basic_auth_header(), "Basic " + encoded)

    def test_missing_value_raises_missing(self):
        with patch.dict(os.environ, {"zendesk_basic_auth": ""}, clear=False):
            with self.assertRaises(ZendeskCommentError) as ctx:
                zendesk_basic_auth_header()
            self.assertEqual(ctx.exception.error_code, "zendesk_basic_auth_missing")

    def test_non_base64_without_colon_raises_invalid(self):
        with self._env("not-a-credential!"):
            with self.assertRaises(ZendeskCommentError) as ctx:
                zendesk_basic_auth_header()
            self.assertEqual(ctx.exception.error_code, "zendesk_basic_auth_invalid")

    def test_empty_username_or_secret_raises_invalid(self):
        for value in (":token", "user:"):
            with self.subTest(value=value):
                with self._env(value):
                    with self.assertRaises(ZendeskCommentError) as ctx:
                        zendesk_basic_auth_header()
                    self.assertEqual(ctx.exception.error_code, "zendesk_basic_auth_invalid")

    def test_base64_without_colon_raises_invalid(self):
        encoded = base64.b64encode(b"no-separator").decode("ascii")
        with self._env(encoded):
            with self.assertRaises(ZendeskCommentError) as ctx:
                zendesk_basic_auth_header()
            self.assertEqual(ctx.exception.error_code, "zendesk_basic_auth_invalid")


if __name__ == "__main__":
    unittest.main()
