"""DSN secret redaction contracts (p2-209, C7).

The 2026-10-21 incident leaked the preproduction runtime DB password because
a ``key=value``-form DSN bypassed a URL-shaped masking pattern. These tests
pin all three shapes (URL, key=value, embedded in exception text) plus
idempotence and structure preservation.
"""

from __future__ import annotations

import unittest

from backend.services.dsn_redaction import redact_dsn_secrets


class DsnRedactionTests(unittest.TestCase):
    def test_url_form_password_is_redacted(self):
        raw = "postgresql://supportportal_preproduction_runtime:S3cret-Pw@n8n-postgres-db.example.com:5432/supportportal_production?sslmode=require"
        out = redact_dsn_secrets(raw)
        self.assertNotIn("S3cret-Pw", out)
        self.assertIn("postgresql://supportportal_preproduction_runtime:***@n8n-postgres-db.example.com:5432/supportportal_production", out)

    def test_key_value_form_password_is_redacted(self):
        raw = "user=supportportal_preproduction_runtime password=Synth3tic-Kv-Pw dbname=supportportal_production host=db.example.com port=5432 sslmode=require"
        out = redact_dsn_secrets(raw)
        self.assertNotIn("Synth3tic-Kv-Pw", out)
        self.assertIn("password=***", out)
        self.assertIn("user=supportportal_preproduction_runtime", out)

    def test_quoted_key_value_password_is_redacted(self):
        raw = "password='p@ss word' host=db"
        out = redact_dsn_secrets(raw)
        self.assertNotIn("p@ss word", out)
        self.assertIn("password=***", out)
        raw2 = 'password="quoted-secret" host=db'
        self.assertNotIn("quoted-secret", redact_dsn_secrets(raw2))

    def test_exception_text_carrying_both_shapes_is_redacted(self):
        raw = (
            "psycopg.OperationalError: connection failed: connection to server at "
            "\"db.example.com\" failed: FATAL: password authentication failed for "
            "user \"u\" (dsn: postgresql://u:hunter2@db.example.com/db, env: "
            "password=hunter2)"
        )
        out = redact_dsn_secrets(raw)
        self.assertNotIn("hunter2", out)
        self.assertIn("u:***@db.example.com", out)
        self.assertIn("password=***", out)
        # Diagnostic structure survives.
        self.assertIn("password authentication failed", out)
        self.assertIn("db.example.com", out)

    def test_already_redacted_text_is_unchanged(self):
        raw = "postgresql://u:***@h/db password=***"
        self.assertEqual(redact_dsn_secrets(raw), raw)

    def test_plain_text_without_secrets_is_unchanged(self):
        raw = "scenario aborted: timed out waiting for reply job"
        self.assertEqual(redact_dsn_secrets(raw), raw)

    def test_none_and_non_string_inputs(self):
        self.assertEqual(redact_dsn_secrets(None), "")
        self.assertEqual(redact_dsn_secrets(42), "42")

    def test_report_field_round_trip(self):
        """The scenario report path must never carry a password out."""
        report = {
            "scenario": "PP-A1",
            "aborted": "preproduction_db_unavailable",
            "aborted_reason": (
                "preproduction_db_unavailable: connection failed "
                "(postgresql://runtime:SuperSecret@db.example.com/prod)"
            ),
        }
        report["aborted_reason"] = redact_dsn_secrets(report["aborted_reason"])
        self.assertNotIn("SuperSecret", str(report))


if __name__ == "__main__":
    unittest.main()
